import contextlib
import time

from aiogram import Bot, F, Router, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from src.bot.handlers.files import format_draft_message
from src.bot.keyboards import get_confirmation_keyboard
from src.bot.states import DocumentProcessingStates
from src.llm.refiner import refine_draft
from src.utils.logger import get_logger

router = Router()
logger = get_logger(__name__)


@router.message(StateFilter(DocumentProcessingStates.waiting_file), F.text)
async def handle_text_while_waiting_file(message: types.Message) -> None:
    """Подсказывает отправить файл после команды /add."""
    if message.text and message.text.startswith("/"):
        return
    await message.answer("Ожидаю фото или файл документа. Для отмены используйте /cancel.")


@router.message(DocumentProcessingStates.confirming)
async def handle_refinement(message: types.Message, bot: Bot, state: FSMContext) -> None:
    """Обрабатывает текстовые поправки пользователя к черновику документа."""
    # Если пришло не текстовое сообщение, игнорируем
    if not message.text:
        return

    # Если это команда, не перехватываем её (пусть обрабатывается commands.py)
    if message.text.startswith("/"):
        return

    data = await state.get_data()
    old_draft = data.get("draft")
    msg_id = data.get("msg_id")
    files = data.get("files", [])

    if not old_draft or not msg_id:
        return

    # Показываем статус набора текста
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # Корректируем черновик через LLM
    new_draft = await refine_draft(old_draft, message.text)
    current_time = time.time()

    await state.update_data(
        draft=new_draft,
        last_activity=current_time,
    )

    text = format_draft_message(new_draft, len(files))

    try:
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=msg_id,
            text=text,
            reply_markup=get_confirmation_keyboard(multi_file=(len(files) > 1)),
        )
    except Exception as e:
        logger.warning(f"Не удалось обновить драфт-сообщение: {e}")

    # Удаляем текстовое сообщение пользователя, чтобы не засорять чат
    with contextlib.suppress(Exception):
        await message.delete()
