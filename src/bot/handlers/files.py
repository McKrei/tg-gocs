import contextlib
import time
from pathlib import Path
from typing import Any

from aiogram import Bot, F, Router, types
from aiogram.fsm.context import FSMContext

from src.agent.agent import classify_document
from src.bot.keyboards import get_confirmation_keyboard
from src.bot.states import DocumentProcessingStates
from src.config import settings
from src.utils.logger import get_logger

router = Router()
logger = get_logger(__name__)


def _cleanup_files(files: list[str]) -> None:
    """Удаляет временные файлы с диска."""
    for f in files:
        try:
            p = Path(f)
            if p.exists():
                p.unlink()
        except Exception as e:
            logger.error(f"Не удалось удалить временный файл {f}: {e}")


def format_draft_message(draft: dict[str, Any], file_count: int) -> str:
    """Форматирует сообщение с черновиком метаданных."""
    header = "📄 Получен первый файл пакета." if file_count == 1 else f"📄 Получен пакет из {file_count} файлов."
    return (
        f"{header}\n\n"
        f"Предлагаю следующие метаданные:\n"
        f"📁 Категория: {draft.get('category', 'Не определено')}\n"
        f"👤 Владелец: {draft.get('owner', 'Не определено')}\n"
        f"📝 Имя файла: {draft.get('suggested_filename', 'document.pdf')}\n"
        f"ℹ️ Описание: {draft.get('summary', 'Нет описания')}"
    )


async def process_incoming_file(
    message: types.Message,
    bot: Bot,
    state: FSMContext,
    file_id: str,
    file_size: int,
    original_name: str,
    file_ext: str,
) -> None:
    """Общая логика сохранения файла и обновления FSM сессии."""
    temp_dir = Path(settings.storage.temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"{file_id}{file_ext}"

    # Скачиваем файл во временную директорию
    await bot.download(file_id, destination=temp_path)
    logger.info(f"Сохранен временный файл: {temp_path.name} ({file_size} B)")

    data = await state.get_data()
    last_activity = data.get("last_activity")
    current_time = time.time()

    # Проверка таймаута TTL (30 минут = 1800 секунд)
    if last_activity and (current_time - last_activity > 1800):
        logger.info("Сессия устарела по таймауту. Очистка старых файлов.")
        _cleanup_files(data.get("files", []))
        await state.clear()
        data = {}

    files = data.get("files", [])
    files.append(str(temp_path))

    # Если это первый файл в сессии
    if not data.get("draft"):
        await state.set_state(DocumentProcessingStates.confirming)

        status_msg = await message.answer("Анализирую документ, пожалуйста, подождите...")

        try:
            draft = await classify_document(str(temp_path))
        except Exception as e:
            logger.error(f"Ошибка классификации: {e}")
            draft = {
                "category": "Нераспознано",
                "suggested_filename": original_name,
                "summary": "Не удалось проанализировать документ.",
                "owner": "Неизвестно",
            }

        # Обновляем имя файла, сохраняя исходное расширение
        suggested_name = draft.get("suggested_filename", "document.pdf")
        if not suggested_name.endswith(file_ext):
            draft["suggested_filename"] = f"{Path(suggested_name).stem}{file_ext}"

        await state.update_data(
            files=files,
            draft=draft,
            msg_id=status_msg.message_id,
            last_activity=current_time,
        )

        text = format_draft_message(draft, len(files))
        await status_msg.edit_text(text, reply_markup=get_confirmation_keyboard(multi_file=False))

    # Если сессия уже существует (пакетный режим)
    else:
        draft = data["draft"]
        msg_id = data["msg_id"]

        await state.update_data(
            files=files,
            last_activity=current_time,
        )

        text = format_draft_message(draft, len(files))
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=msg_id,
                text=text,
                reply_markup=get_confirmation_keyboard(multi_file=True),
            )
        except Exception as e:
            logger.warning(f"Не удалось обновить драфт-сообщение: {e}")

        # Удаляем сообщение пользователя, приславшего новый файл пакета, чтобы не спамить в чате
        with contextlib.suppress(Exception):
            await message.delete()


@router.message(F.photo)
async def handle_photo(message: types.Message, bot: Bot, state: FSMContext) -> None:
    """Обрабатывает входящие фотографии."""
    if not message.photo:
        return
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    file_ext = Path(file_info.file_path or "photo.jpg").suffix or ".jpg"

    await process_incoming_file(
        message=message,
        bot=bot,
        state=state,
        file_id=photo.file_id,
        file_size=photo.file_size or 0,
        original_name="photo.jpg",
        file_ext=file_ext,
    )


@router.message(F.document)
async def handle_document(message: types.Message, bot: Bot, state: FSMContext) -> None:
    """Обрабатывает входящие документы (PDF и изображения)."""
    doc = message.document
    if not doc:
        return

    mime = doc.mime_type or ""
    file_ext = Path(doc.file_name or "").suffix.lower()

    is_valid = (
        mime.startswith("image/")
        or mime == "application/pdf"
        or file_ext in [".pdf", ".jpg", ".jpeg", ".png"]
    )

    if not is_valid:
        await message.answer("Пожалуйста, отправьте документ в формате PDF или изображение (JPEG, PNG).")
        return

    await process_incoming_file(
        message=message,
        bot=bot,
        state=state,
        file_id=doc.file_id,
        file_size=doc.file_size or 0,
        original_name=doc.file_name or "document.pdf",
        file_ext=file_ext,
    )
