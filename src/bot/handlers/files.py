import contextlib
import time
from pathlib import Path
from typing import Any

from aiogram import Bot, F, Router, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from src.agent.agent import classify_document, normalize_draft_metadata
from src.agent.tools import find_similar_document
from src.bot.keyboards import get_confirmation_keyboard, get_duplicate_confirmation_keyboard
from src.bot.states import DocumentProcessingStates
from src.config import settings
from src.utils.logger import get_logger

router = Router()
logger = get_logger(__name__)

rate_limits: dict[int, list[float]] = {}


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
    header = "📄 Получен файл." if file_count == 1 else f"📄 Получен пакет из {file_count} файлов."
    return (
        f"{header}\n\n"
        f"Предлагаю следующие метаданные:\n"
        f"📁 Категория: {draft.get('category', 'Не определено')}\n"
        f"👤 Владелец: {draft.get('owner', 'Не определено')}\n"
        f"📝 Имя файла: {draft.get('suggested_filename', 'document.pdf')}\n"
        f"ℹ️ Описание: {draft.get('summary', 'Нет описания')}\n\n"
        f"Можете уточнить любое поле текстом или сохранить как есть."
    )


def format_draft_message_with_warning(draft: dict[str, Any], file_count: int, similar_doc: dict[str, Any]) -> str:
    """Форматирует сообщение с предупреждением о дубликате."""
    header = "📄 Получен файл." if file_count == 1 else f"📄 Получен пакет из {file_count} файлов."
    similarity = similar_doc.get("similarity_percent", 100)
    reason = (
        "найден файл с таким же именем и категорией"
        if similar_doc["reason"] == "exact_path"
        else f"найден похожий файл (похожесть {similarity}%)"
    )
    return (
        f"{header}\n\n"
        f"⚠️ **Внимание: {reason}!**\n"
        f"📁 Категория: `{similar_doc['category']}`\n"
        f"📝 Имя файла: `{similar_doc['saved_filename']}`\n"
        f"ℹ️ Описание: {similar_doc['summary']}\n\n"
        f"--- Предлагаемые метаданные нового документа ---\n"
        f"📁 Категория: {draft.get('category', 'Не определено')}\n"
        f"👤 Владелец: {draft.get('owner', 'Не определено')}\n"
        f"📝 Имя файла: {draft.get('suggested_filename', 'document.pdf')}\n"
        f"ℹ️ Описание: {draft.get('summary', 'Нет описания')}\n\n"
        f"Вы можете заменить существующий документ, сохранить его как новый или отменить."
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
    max_bytes = settings.storage.max_file_size_mb * 1024 * 1024
    if file_size > max_bytes:
        await message.answer(f"Файл слишком большой. Максимальный размер: {settings.storage.max_file_size_mb} МБ.")
        return

    user_id = message.from_user.id if message.from_user else 0
    if user_id:
        current_time = time.time()
        user_requests = rate_limits.setdefault(user_id, [])
        rate_limits[user_id] = [t for t in user_requests if current_time - t < 60]
        if len(rate_limits[user_id]) >= settings.storage.rate_limit_per_minute:
            await message.answer("Вы отправляете файлы слишком часто. Пожалуйста, подождите немного.")
            return
        rate_limits[user_id].append(current_time)

    temp_dir = Path(settings.storage.temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"{file_id}{file_ext}"

    await bot.download(file_id, destination=temp_path)
    logger.info(f"Сохранен временный файл: {temp_path.name} ({file_size} B)")

    data = await state.get_data()
    last_activity = data.get("last_activity")
    current_time = time.time()

    if last_activity and (current_time - last_activity > settings.storage.session_ttl_seconds):
        logger.info("Сессия устарела по таймауту. Очистка старых файлов.")
        _cleanup_files(data.get("files", []))
        await state.clear()
        await state.set_state(DocumentProcessingStates.waiting_file)
        data = {}

    files = data.get("files", [])
    files.append(str(temp_path))

    if not data.get("draft"):
        await state.set_state(DocumentProcessingStates.confirming)
        status_msg = await message.answer("⏳ Анализирую документ, пожалуйста, подождите...")

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

        draft = normalize_draft_metadata(draft, file_ext)
        similar_doc = await find_similar_document(draft["category"], draft["suggested_filename"], draft["summary"])
        duplicate_id = str(similar_doc["id"]) if similar_doc else None

        await state.update_data(
            files=files,
            draft=draft,
            duplicate_id=duplicate_id,
            msg_id=status_msg.message_id,
            last_activity=current_time,
        )

        if similar_doc:
            text = format_draft_message_with_warning(draft, len(files), similar_doc)
            reply_markup = get_duplicate_confirmation_keyboard(multi_file=False)
        else:
            text = format_draft_message(draft, len(files))
            reply_markup = get_confirmation_keyboard(multi_file=False)

        await status_msg.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")

    else:
        draft = data["draft"]
        msg_id = data["msg_id"]

        similar_doc = await find_similar_document(draft["category"], draft["suggested_filename"], draft["summary"])
        duplicate_id = str(similar_doc["id"]) if similar_doc else None

        await state.update_data(
            files=files,
            duplicate_id=duplicate_id,
            last_activity=current_time,
        )

        if similar_doc:
            text = format_draft_message_with_warning(draft, len(files), similar_doc)
            reply_markup = get_duplicate_confirmation_keyboard(multi_file=True)
        else:
            text = format_draft_message(draft, len(files))
            reply_markup = get_confirmation_keyboard(multi_file=True)

        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=msg_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning(f"Не удалось обновить драфт-сообщение: {e}")

        with contextlib.suppress(Exception):
            await message.delete()


@router.message(
    StateFilter(DocumentProcessingStates.waiting_file, DocumentProcessingStates.confirming),
    F.photo,
)
async def handle_photo(message: types.Message, bot: Bot, state: FSMContext) -> None:
    """Обрабатывает входящие фотографии в режиме добавления документа."""
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


@router.message(
    StateFilter(DocumentProcessingStates.waiting_file, DocumentProcessingStates.confirming),
    F.document,
)
async def handle_document(message: types.Message, bot: Bot, state: FSMContext) -> None:
    """Обрабатывает входящие файлы в режиме добавления документа."""
    doc = message.document
    if not doc:
        return

    mime = doc.mime_type or ""
    file_ext = Path(doc.file_name or "").suffix.lower()

    is_valid = mime.startswith("image/") or mime == "application/pdf" or file_ext in [".pdf", ".jpg", ".jpeg", ".png"]
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


@router.message(F.photo)
@router.message(F.document)
async def handle_file_without_add(message: types.Message) -> None:
    """Отвечает пользователю, если файл прислан без команды /add."""
    await message.answer("📎 Чтобы добавить документ, используйте команду /add")
