from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_confirmation_keyboard(multi_file: bool = False) -> InlineKeyboardMarkup:
    """Возвращает клавиатуру для подтверждения сохранения документа."""
    builder = InlineKeyboardBuilder()
    save_text = "📎 Склеить и сохранить в PDF" if multi_file else "✅ Сохранить так"
    builder.row(
        InlineKeyboardButton(text=save_text, callback_data="confirm_save"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_save"),
    )
    return builder.as_markup()
