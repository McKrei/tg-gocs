import uuid
from pathlib import Path
from typing import Any

from aiogram import Bot, F, Router, types
from aiogram.fsm.context import FSMContext

from src.agent.tools import (
    convert_to_pdf,
    delete_file_from_drive,
    extract_gdrive_file_id,
    get_unique_filename,
    save_to_local_and_drive,
)
from src.bot.handlers.files import _cleanup_files
from src.db.engine import async_session
from src.db.repository import DocumentRepository
from src.llm.embeddings import get_embedding
from src.utils.logger import get_logger

router = Router()
logger = get_logger(__name__)


def _format_gdrive_result(save_result: dict[str, Any], target_path: str) -> str:
    """Форматирует строку результата для отображения пользователю."""
    if save_result["gdrive_link"]:
        folder_link = save_result.get("gdrive_folder_link")
        folder_md = f" | [открыть папку]({folder_link})" if folder_link else ""
        return f"☁️ Google Drive: `{target_path}`\n[открыть файл]({save_result['gdrive_link']}){folder_md}"
    if save_result.get("gdrive_error"):
        return f"☁️ Google Drive: ошибка загрузки — {save_result['gdrive_error']}"
    return "☁️ Google Drive: не настроен"


async def _prepare_file_for_save(files: list[str], suggested_filename: str) -> str:
    """Подготавливает финальный файл: склеивает мульти-файлы в PDF или возвращает единственный."""
    if len(files) > 1:
        file_ids = [Path(f).name for f in files]
        return await convert_to_pdf(file_ids, suggested_filename)
    return files[0]


async def _persist_document(
    draft: dict[str, Any],
    save_result: dict[str, Any],
    suggested_filename: str,
) -> None:
    """Сохраняет документ и эмбеддинг в БД."""
    embedding = await get_embedding(draft["summary"])
    async with async_session() as session:
        repo = DocumentRepository(session)
        await repo.add_document(
            saved_filename=suggested_filename,
            local_path=save_result["local_path"] or f"{draft['category']}/{suggested_filename}",
            category=draft["category"],
            owner=draft["owner"],
            summary=draft["summary"],
            embedding=embedding,
            gdrive_link=save_result["gdrive_link"],
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
        save_result = await save_to_local_and_drive(final_temp_path, target_path)
        await _persist_document(draft, save_result, suggested_filename)

        gdrive_text = _format_gdrive_result(save_result, target_path)
        if isinstance(callback.message, types.Message):
            await callback.message.edit_text(f"✅ Документ успешно сохранен!\n\n{gdrive_text}", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка при сохранении документа: {e}")
        await callback.answer("Произошла ошибка при сохранении файла.", show_alert=True)
    finally:
        _cleanup_files(files)
        if final_temp_path and len(files) > 1:
            _cleanup_files([final_temp_path])
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
        save_result = await save_to_local_and_drive(final_temp_path, target_path)
        await _persist_document(draft, save_result, suggested_filename)

        gdrive_text = _format_gdrive_result(save_result, target_path)
        if isinstance(callback.message, types.Message):
            await callback.message.edit_text(f"✅ Документ успешно заменен!\n\n{gdrive_text}", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка при сохранении нового документа взамен старого: {e}")
        await callback.answer("Произошла ошибка при сохранении нового файла.", show_alert=True)
    finally:
        _cleanup_files(files)
        if final_temp_path and len(files) > 1:
            _cleanup_files([final_temp_path])
        await state.clear()


@router.callback_query(F.data == "cancel_save")
async def handle_cancel_save(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Обрабатывает отмену сохранения с очисткой временных файлов."""
    data = await state.get_data()
    files = data.get("files", [])

    _cleanup_files(files)
    await state.clear()

    await callback.answer("Сохранение отменено.")
    if isinstance(callback.message, types.Message):
        await callback.message.edit_text("Действие отменено, временные файлы удалены.")
