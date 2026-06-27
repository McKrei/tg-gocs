import asyncio
import html
import time
import uuid
from pathlib import Path
from typing import Any

from aiogram import Bot, F, Router, types
from aiogram.fsm.context import FSMContext

from src.agent.agent import classify_document, normalize_draft_metadata
from src.agent.tools import (
    delete_file_from_drive,
    extract_gdrive_file_id,
    find_similar_document,
    get_unique_filename,
    merge_files_to_pdf,
    save_to_local_and_drive,
)
from src.bot.handlers.files import (
    _cleanup_files,
    format_draft_message,
    format_draft_message_with_warning,
)
from src.bot.keyboards import get_confirmation_keyboard, get_duplicate_confirmation_keyboard
from src.bot.states import DocumentProcessingStates
from src.config import settings
from src.db.engine import async_session
from src.db.repository import DocumentRepository
from src.llm.embeddings import get_embedding
from src.utils.logger import get_logger

router = Router()
logger = get_logger(__name__)


def _format_gdrive_result(save_result: dict[str, Any], target_path: str) -> str:
    """Форматирует строку результата для отображения пользователю."""
    escaped_path = html.escape(target_path)
    if save_result["gdrive_link"]:
        folder_link = save_result.get("gdrive_folder_link")
        folder_md = f" | <a href=\"{html.escape(folder_link)}\">открыть папку</a>" if folder_link else ""
        file_link = html.escape(save_result["gdrive_link"])
        return f"☁️ Google Drive: <code>{escaped_path}</code>\n<a href=\"{file_link}\">открыть файл</a>{folder_md}"
    if save_result.get("gdrive_error"):
        return f"☁️ Google Drive: ошибка загрузки — {html.escape(str(save_result['gdrive_error']))}"
    return "☁️ Google Drive: не настроен"


async def _prepare_file_for_save(files: list[str], suggested_filename: str) -> str:
    """Возвращает единственный подготовленный файл."""
    return files[0]


async def _persist_document(
    draft: dict[str, Any],
    save_result: dict[str, Any],
    suggested_filename: str,
    embedding: Any,
) -> None:
    """Сохраняет документ и эмбеддинг в БД."""
    async with async_session() as session:
        repo = DocumentRepository(session)
        doc = await repo.add_document(
            saved_filename=suggested_filename,
            local_path=save_result["local_path"] or f"{draft['category']}/{suggested_filename}",
            category=draft["category"],
            owner=draft["owner"],
            summary=draft["summary"],
            embedding=embedding,
            gdrive_link=save_result["gdrive_link"],
        )

        # Если при сохранении произошла ошибка Drive — создаем PendingUpload для отложенной синхронизации
        if save_result.get("gdrive_error"):
            await repo.add_pending_upload(
                local_path=save_result["local_path"] or f"{draft['category']}/{suggested_filename}",
                target_path=f"{draft['category']}/{suggested_filename}",
                document_id=doc.id,
            )

        await session.commit()


async def _delete_old_document(duplicate_id: str) -> None:
    """Удаляет старый документ из БД, локального FS и Google Drive."""
    try:
        dup_uuid = uuid.UUID(duplicate_id)
        async with async_session() as session:
            repo = DocumentRepository(session)
            old_doc = await repo.get_document(dup_uuid)
            if old_doc:
                old_local = Path(old_doc.local_path)
                if old_local.exists():
                    try:
                        old_local.unlink()
                    except Exception as e:
                        logger.error(f"Не удалось удалить локальный файл {old_doc.local_path}: {e}")

                if old_doc.gdrive_link:
                    file_id = extract_gdrive_file_id(old_doc.gdrive_link)
                    if file_id:
                        await delete_file_from_drive(file_id)

                await repo.delete_document(dup_uuid)
                await session.commit()
    except Exception as e:
        logger.error(f"Ошибка при удалении старого документа {duplicate_id}: {e}")


@router.callback_query(F.data == "start_analysis")
async def handle_start_analysis(callback: types.CallbackQuery, bot: Bot, state: FSMContext) -> None:
    """Запускает процесс слияния файлов и их классификацию."""
    data = await state.get_data()
    files = data.get("files", [])

    if not files:
        await callback.answer("Ошибка: файлы не найдены.", show_alert=True)
        await state.clear()
        return

    await callback.answer("Запускаю анализ...")
    if isinstance(callback.message, types.Message):
        await callback.message.edit_text("⏳ Подготавливаю файлы...", reply_markup=None)

    from src.agent.tools import get_existing_structure

    async def _prepare_files() -> Path:
        if len(files) == 1:
            return Path(files[0])
        temp_dir = Path(settings.storage.temp_dir)
        merged_filename = f"merged_{uuid.uuid4()}.pdf"
        merged_path = temp_dir / merged_filename
        await merge_files_to_pdf(files, str(merged_path))
        return merged_path

    try:
        # Параллельно готовим/склеиваем файлы и собираем структуру папок
        merged_path, structure_info = await asyncio.gather(
            _prepare_files(),
            get_existing_structure()
        )
    except Exception as e:
        logger.error(f"Ошибка при обработке файлов: {e}")
        if isinstance(callback.message, types.Message):
            await callback.message.edit_text("❌ Произошла ошибка при обработке файлов.")
        _cleanup_files(files)
        await state.clear()
        return

    if isinstance(callback.message, types.Message):
        await callback.message.edit_text("🔍 Анализирую документ...", reply_markup=None)

    try:
        draft = await classify_document(str(merged_path), classify_only=True, structure_info=structure_info)
    except Exception as e:
        logger.error(f"Ошибка классификации: {e}")
        draft = {
            "category": "Нераспознано",
            "suggested_filename": "document.pdf",
            "summary": "Не удалось проанализировать документ.",
            "owner": "Неизвестно",
        }

    if isinstance(callback.message, types.Message):
        await callback.message.edit_text("📂 Определяю категорию и проверяю дубликаты...", reply_markup=None)

    from src.agent.tools import get_flat_directory_list
    flat_dirs = get_flat_directory_list()
    categories = []
    try:
        async with async_session() as session:
            repo = DocumentRepository(session)
            stats = await repo.get_stats_by_category()
            categories = [cat for cat, _ in stats]
    except Exception:
        pass
    all_existing_paths = list(set(flat_dirs + categories))

    suffix = merged_path.suffix.lower()
    draft = normalize_draft_metadata(draft, suffix, existing_paths=all_existing_paths)
    similar_doc = await find_similar_document(draft["category"], draft["suggested_filename"], draft["summary"])
    duplicate_id = str(similar_doc["id"]) if similar_doc else None

    await state.update_data(
        files=[str(merged_path)],
        original_files=files,
        draft=draft,
        duplicate_id=duplicate_id,
        last_activity=time.time(),
    )
    await state.set_state(DocumentProcessingStates.confirming)

    if similar_doc:
        text = format_draft_message_with_warning(draft, len(files), similar_doc)
        reply_markup = get_duplicate_confirmation_keyboard(multi_file=False)
    else:
        text = format_draft_message(draft, len(files))
        reply_markup = get_confirmation_keyboard(multi_file=False)

    if isinstance(callback.message, types.Message):
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")


@router.callback_query(F.data == "confirm_save")
async def handle_confirm_save(callback: types.CallbackQuery, bot: Bot, state: FSMContext) -> None:
    """Обрабатывает подтверждение сохранения документа."""
    data = await state.get_data()
    files = data.get("files", [])
    draft = data.get("draft")

    if not files or not draft:
        await callback.answer("Ошибка: сессия не найдена или устарела.", show_alert=True)
        await state.clear()
        return

    await callback.answer("Сохраняю документ...")
    if isinstance(callback.message, types.Message):
        await callback.message.edit_text("⏳ Сохраняю документ...", reply_markup=None)

    category = draft["category"]
    suggested_filename = get_unique_filename(category, draft["suggested_filename"])
    target_path = f"{category}/{suggested_filename}"

    final_temp_path = None
    try:
        final_temp_path = await _prepare_file_for_save(files, suggested_filename)
        # Запускаем сохранение в Drive и получение эмбеддинга параллельно
        save_result, embedding = await asyncio.gather(
            save_to_local_and_drive(final_temp_path, target_path),
            get_embedding(draft["summary"])
        )
        await _persist_document(draft, save_result, suggested_filename, embedding)

        gdrive_text = _format_gdrive_result(save_result, target_path)
        if isinstance(callback.message, types.Message):
            await callback.message.edit_text(f"✅ Документ успешно сохранен!\n\n{gdrive_text}", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка при сохранении документа: {e}")
        await callback.answer("Произошла ошибка при сохранении файла.", show_alert=True)
        if isinstance(callback.message, types.Message):
            await callback.message.edit_text(f"❌ Ошибка при сохранении документа: {html.escape(str(e))}")
    finally:
        all_to_clean = list(set(files + data.get("original_files", [])))
        _cleanup_files(all_to_clean)
        await state.clear()


@router.callback_query(F.data == "replace_save")
async def handle_replace_save(callback: types.CallbackQuery, bot: Bot, state: FSMContext) -> None:
    """Обрабатывает замену существующего документа новым."""
    data = await state.get_data()
    files = data.get("files", [])
    draft = data.get("draft")
    duplicate_id = data.get("duplicate_id")

    if not files or not draft or not duplicate_id:
        await callback.answer("Ошибка: сессия не найдена или дубликат не определен.", show_alert=True)
        await state.clear()
        return

    await callback.answer("Заменяю документ...")
    if isinstance(callback.message, types.Message):
        await callback.message.edit_text("⏳ Заменяю документ...", reply_markup=None)

    await _delete_old_document(duplicate_id)

    category = draft["category"]
    suggested_filename = draft["suggested_filename"]
    target_path = f"{category}/{suggested_filename}"

    final_temp_path = None
    try:
        final_temp_path = await _prepare_file_for_save(files, suggested_filename)
        # Запускаем сохранение в Drive и получение эмбеддинга параллельно
        save_result, embedding = await asyncio.gather(
            save_to_local_and_drive(final_temp_path, target_path),
            get_embedding(draft["summary"])
        )
        await _persist_document(draft, save_result, suggested_filename, embedding)

        gdrive_text = _format_gdrive_result(save_result, target_path)
        if isinstance(callback.message, types.Message):
            await callback.message.edit_text(f"✅ Документ успешно заменен!\n\n{gdrive_text}", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка при сохранении нового документа взамен старого: {e}")
        await callback.answer("Произошла ошибка при сохранении нового файла.", show_alert=True)
        if isinstance(callback.message, types.Message):
            await callback.message.edit_text(f"❌ Ошибка при сохранении нового документа: {html.escape(str(e))}")
    finally:
        all_to_clean = list(set(files + data.get("original_files", [])))
        _cleanup_files(all_to_clean)
        await state.clear()


@router.callback_query(F.data == "cancel_save")
async def handle_cancel_save(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Обрабатывает отмену сохранения с очисткой временных файлов."""
    data = await state.get_data()
    files = data.get("files", [])
    original_files = data.get("original_files", [])

    all_to_clean = list(set(files + original_files))
    _cleanup_files(all_to_clean)
    await state.clear()

    await callback.answer("Сохранение отменено.")
    if isinstance(callback.message, types.Message):
        await callback.message.edit_text("Действие отменено, временные файлы удалены.")
