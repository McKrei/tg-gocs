from pathlib import Path

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


@router.callback_query(F.data == "confirm_save")
async def handle_confirm_save(callback: types.CallbackQuery, bot: Bot, state: FSMContext) -> None:
    """Обрабатывает подтверждение сохранения документа (включая склеивание мультифайлов)."""
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
    summary = draft["summary"]
    owner = draft["owner"]

    final_temp_path = None
    try:
        if len(files) > 1:
            file_ids = [Path(f).name for f in files]
            final_temp_path = await convert_to_pdf(file_ids, suggested_filename)
        else:
            final_temp_path = files[0]

        target_path = f"{category}/{suggested_filename}"

        save_result = await save_to_local_and_drive(final_temp_path, target_path)

        embedding = await get_embedding(summary)

        async with async_session() as session:
            repo = DocumentRepository(session)
            await repo.add_document(
                saved_filename=suggested_filename,
                local_path=save_result["local_path"] or target_path,
                category=category,
                owner=owner,
                summary=summary,
                embedding=embedding,
                gdrive_link=save_result["gdrive_link"],
            )
            await session.commit()

        gdrive_error = save_result.get("gdrive_error")
        if save_result["gdrive_link"]:
            folder_link = save_result.get("gdrive_folder_link")
            folder_md = f" | [открыть папку]({folder_link})" if folder_link else ""
            gdrive_text = f"☁️ Google Drive: `{target_path}`\n[открыть файл]({save_result['gdrive_link']}){folder_md}"
        elif gdrive_error:
            gdrive_text = f"☁️ Google Drive: ошибка загрузки — {gdrive_error}"
        else:
            gdrive_text = "☁️ Google Drive: не настроен"

        text = f"✅ Документ успешно сохранен!\n\n{gdrive_text}"

        if isinstance(callback.message, types.Message):
            await callback.message.edit_text(text, parse_mode="Markdown")

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

    import uuid

    try:
        dup_uuid = uuid.UUID(duplicate_id)
        async with async_session() as session:
            repo = DocumentRepository(session)
            old_doc = await repo.get_document(dup_uuid)
            if old_doc:
                try:
                    old_local_path = Path(old_doc.local_path)
                    if old_local_path.exists():
                        old_local_path.unlink()
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

    category = draft["category"]
    suggested_filename = draft["suggested_filename"]
    summary = draft["summary"]
    owner = draft["owner"]

    final_temp_path = None
    try:
        if len(files) > 1:
            file_ids = [Path(f).name for f in files]
            final_temp_path = await convert_to_pdf(file_ids, suggested_filename)
        else:
            final_temp_path = files[0]

        target_path = f"{category}/{suggested_filename}"

        save_result = await save_to_local_and_drive(final_temp_path, target_path)

        embedding = await get_embedding(summary)

        async with async_session() as session:
            repo = DocumentRepository(session)
            await repo.add_document(
                saved_filename=suggested_filename,
                local_path=save_result["local_path"] or target_path,
                category=category,
                owner=owner,
                summary=summary,
                embedding=embedding,
                gdrive_link=save_result["gdrive_link"],
            )
            await session.commit()

        gdrive_error = save_result.get("gdrive_error")
        if save_result["gdrive_link"]:
            folder_link = save_result.get("gdrive_folder_link")
            folder_md = f" | [открыть папку]({folder_link})" if folder_link else ""
            gdrive_text = f"☁️ Google Drive: `{target_path}`\n[открыть файл]({save_result['gdrive_link']}){folder_md}"
        elif gdrive_error:
            gdrive_text = f"☁️ Google Drive: ошибка загрузки — {gdrive_error}"
        else:
            gdrive_text = "☁️ Google Drive: не настроен"

        text = f"✅ Документ успешно заменен!\n\n{gdrive_text}"

        if isinstance(callback.message, types.Message):
            await callback.message.edit_text(text, parse_mode="Markdown")

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
