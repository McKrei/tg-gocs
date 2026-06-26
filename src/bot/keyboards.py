from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_confirmation_keyboard() -> InlineKeyboardMarkup:
    """Возвращает клавиатуру для подтверждения сохранения документа."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Сохранить", callback_data="confirm_save"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_save"),
    )
    return builder.as_markup()
