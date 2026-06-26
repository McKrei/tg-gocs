from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.handlers.commands import cmd_cancel, cmd_start
from src.bot.handlers.files import handle_document, handle_photo
from src.bot.middleware.auth import AuthMiddleware
from src.config import settings


@pytest.mark.asyncio
async def test_cmd_start() -> None:
    """Проверяет отправку стартового сообщения."""
    message = AsyncMock()
    await cmd_start(message)
    message.answer.assert_called_once_with(
        "Привет! Я бот для управления семейными документами.\n"
        "Отправь мне файл (изображение или PDF) для классификации."
    )


@pytest.mark.asyncio
async def test_cmd_cancel() -> None:
    """Проверяет сброс состояния FSM при отмене."""
    message = AsyncMock()
    state = AsyncMock()
    await cmd_cancel(message, state)
    state.clear.assert_called_once()
    message.answer.assert_called_once_with("Действие отменено.")


@pytest.mark.asyncio
async def test_auth_middleware_allowed() -> None:
    """Проверяет пропуск авторизованного пользователя."""
    middleware = AuthMiddleware()
    handler = AsyncMock()
    event = AsyncMock()

    settings.bot.allowed_user_ids = [111]
    user = MagicMock(id=111, username="user1")
    data = {"event_from_user": user}

    await middleware(handler, event, data)
    handler.assert_called_once_with(event, data)


@pytest.mark.asyncio
async def test_auth_middleware_denied() -> None:
    """Проверяет блокировку неавторизованного пользователя."""
    middleware = AuthMiddleware()
    handler = AsyncMock()
    event = AsyncMock()

    settings.bot.allowed_user_ids = [111]
    user = MagicMock(id=222, username="user2")
    data = {"event_from_user": user}

    await middleware(handler, event, data)
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_handle_photo() -> None:
    """Проверяет загрузку присланного фото."""
    message = AsyncMock()
    bot = AsyncMock()
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={})
    state.update_data = AsyncMock(return_value={})

    photo = MagicMock(file_id="photo123", file_size=500)
    message.photo = [photo]

    file_info = MagicMock(file_path="photos/file_0.jpg")
    bot.get_file = AsyncMock(return_value=file_info)
    bot.download = AsyncMock()

    with patch("src.bot.handlers.files.settings") as mock_settings:
        mock_settings.storage.temp_dir = "data/temp_test"
        await handle_photo(message, bot, state)

        bot.get_file.assert_called_once_with("photo123")
        bot.download.assert_called_once()
        message.answer.assert_called_once()


@pytest.mark.asyncio
async def test_handle_document() -> None:
    """Проверяет загрузку документа с валидным MIME-типом."""
    message = AsyncMock()
    bot = AsyncMock()
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={})
    state.update_data = AsyncMock(return_value={})

    doc = MagicMock(file_id="doc123", file_name="doc.pdf", mime_type="application/pdf", file_size=1000)
    message.document = doc
    bot.download = AsyncMock()

    with patch("src.bot.handlers.files.settings") as mock_settings:
        mock_settings.storage.temp_dir = "data/temp_test"
        await handle_document(message, bot, state)

        bot.download.assert_called_once()
        message.answer.assert_called_once()
