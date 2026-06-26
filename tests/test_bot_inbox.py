"""Тесты для inbox-обработчиков Telegram-бота."""

from unittest.mock import AsyncMock, patch

import pytest
from aiogram import types

from src.bot.handlers.inbox import handle_inbox_skip


@pytest.mark.asyncio
async def test_handle_inbox_skip_deletes_file() -> None:
    """Проверяет, что при пропуске файла вызывается удаление из Google Drive."""
    callback = AsyncMock()
    callback.message = AsyncMock(spec=types.Message)
    callback.message.edit_text = AsyncMock()

    state = AsyncMock()
    test_file = {
        "drive_id": "test_drive_id_123",
        "name": "test_file.jpg",
        "mime_type": "image/jpeg",
        "web_view_link": "https://drive.google.com/test",
    }
    state.get_data = AsyncMock(
        return_value={
            "inbox_queue": [test_file],
            "current_file": test_file,
        }
    )

    with (
        patch("src.drive.client.delete_drive_file") as mock_delete,
        patch("src.bot.handlers.inbox._show_next_inbox_file", AsyncMock()) as mock_next,
    ):
        await handle_inbox_skip(callback, state)

        mock_delete.assert_called_once_with("test_drive_id_123")
        callback.answer.assert_called_once_with("Файл пропущен.")
        callback.message.edit_text.assert_called_once_with(
            "⏭️ Файл пропущен: <b>test_file.jpg</b>", parse_mode="HTML", reply_markup=None
        )
        state.update_data.assert_called_once_with(inbox_queue=[])
        mock_next.assert_called_once_with(callback.message, state)
