from pathlib import Path

from aiogram import Bot, F, Router, types
from aiogram.fsm.context import FSMContext

from src.agent.tools import convert_to_pdf, save_to_local_and_drive
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

    category = draft["category"]
    suggested_filename = draft["suggested_filename"]
    summary = draft["summary"]
    owner = draft["owner"]

    final_temp_path = None
    try:
        # Если прислано несколько файлов, склеиваем их в PDF
        if len(files) > 1:
            file_ids = [Path(f).name for f in files]
            # convert_to_pdf возвращает путь к сохраненному файлу
            final_temp_path = await convert_to_pdf(file_ids, suggested_filename)
        else:
            final_temp_path = files[0]

        target_path = f"{category}/{suggested_filename}"

        # Сохраняем локально и в Google Drive
        save_result = await save_to_local_and_drive(final_temp_path, target_path)

        # Вычисляем эмбеддинг для семантического поиска
        embedding = await get_embedding(summary)

        # Записываем информацию о документе в базу данных
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

        gdrive_text = (
            f"☁️ Google Drive: [открыть файл]({save_result['gdrive_link']})"
            if save_result["gdrive_link"]
            else "☁️ Google Drive: Не настроен"
        )

        text = (
            f"✅ Документ успешно сохранен!\n\n"
            f"📁 Локально: `{save_result['local_path']}`\n"
            f"{gdrive_text}"
        )

        if isinstance(callback.message, types.Message):
            await callback.message.edit_text(text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Ошибка при сохранении документа: {e}")
        await callback.answer("Произошла ошибка при сохранении файла.", show_alert=True)
    finally:
        # Гарантированная очистка временных ресурсов и сброс состояния
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
