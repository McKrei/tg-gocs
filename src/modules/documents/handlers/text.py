import contextlib
import time

from aiogram import Bot, F, Router, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from src.core.utils.logger import get_logger
from src.modules.documents.handlers.files import format_draft_message, format_draft_message_with_warning
from src.modules.documents.keyboards import get_confirmation_keyboard, get_duplicate_confirmation_keyboard
from src.modules.documents.services.refiner import refine_draft
from src.modules.documents.states import DocumentProcessingStates, InboxStates
from src.modules.documents.tools import find_similar_document

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
    if not message.text:
        return
    if message.text.startswith("/"):
        return

    data = await state.get_data()
    old_draft = data.get("draft")
    msg_id = data.get("msg_id")
    files = data.get("files", [])

    if not old_draft or not msg_id:
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    new_draft = await refine_draft(old_draft, message.text)
    current_time = time.time()
    similar_doc = await find_similar_document(
        new_draft["category"], new_draft["suggested_filename"], new_draft["summary"]
    )
    duplicate_id = str(similar_doc["id"]) if similar_doc else None

    await state.update_data(
        draft=new_draft,
        duplicate_id=duplicate_id,
        last_activity=current_time,
    )

    if similar_doc:
        text = format_draft_message_with_warning(new_draft, len(files), similar_doc)
        reply_markup = get_duplicate_confirmation_keyboard(multi_file=(len(files) > 1))
    else:
        text = format_draft_message(new_draft, len(files))
        reply_markup = get_confirmation_keyboard(multi_file=(len(files) > 1))

    try:
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=msg_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"Не удалось обновить драфт-сообщение: {e}")

    with contextlib.suppress(Exception):
        await message.delete()


@router.message(InboxStates.confirming)
async def handle_inbox_refinement(message: types.Message, bot: Bot, state: FSMContext) -> None:
    """Обрабатывает текстовые уточнения пользователя к inbox-черновику."""
    if not message.text:
        return
    if message.text.startswith("/"):
        return

    data = await state.get_data()
    old_draft = data.get("current_draft")
    file_data = data.get("current_file")
    queue = data.get("inbox_queue", [])

    if not old_draft or not file_data:
        return

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    new_draft = await refine_draft(old_draft, message.text)
    similar_doc = await find_similar_document(
        new_draft["category"], new_draft["suggested_filename"], new_draft["summary"]
    )
    duplicate_id = str(similar_doc["id"]) if similar_doc else None

    await state.update_data(
        current_draft=new_draft,
        duplicate_id=duplicate_id,
        inbox_queue=queue,
    )

    from src.modules.documents.handlers.inbox import _format_inbox_draft_message
    from src.modules.documents.services.sync import InboxFile

    inbox_file = InboxFile(**file_data)
    remaining = len(queue)
    text, keyboard = _format_inbox_draft_message(new_draft, inbox_file, similar_doc, remaining)

    current_msg_id = data.get("current_msg_id")
    if current_msg_id:
        with contextlib.suppress(Exception):
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=current_msg_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            with contextlib.suppress(Exception):
                await message.delete()
            return

    sent = await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    await state.update_data(current_msg_id=sent.message_id)

    with contextlib.suppress(Exception):
        await message.delete()
