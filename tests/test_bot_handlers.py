from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.handlers.commands import cmd_cancel, cmd_help, cmd_list, cmd_start, cmd_stats
from src.bot.handlers.files import handle_document, handle_photo
from src.bot.middleware.auth import AuthMiddleware
from src.config import settings


@pytest.mark.asyncio
async def test_cmd_start() -> None:
    """Проверяет отправку стартового сообщения."""
    message = AsyncMock()
    await cmd_start(message)
    message.answer.assert_called_once_with(
        "Привет! Я бот для управления семейными документами.\nОтправь мне файл (изображение или PDF) для классификации."
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
    """Проверяет пропуск авторизованного чата."""
    middleware = AuthMiddleware()
    handler = AsyncMock()
    event = AsyncMock()

    settings.bot.allowed_chat_ids = [-100123456789]
    chat = MagicMock(id=-100123456789)
    data = {"event_chat": chat, "event_from_user": MagicMock(id=111)}

    await middleware(handler, event, data)
    handler.assert_called_once_with(event, data)


@pytest.mark.asyncio
async def test_auth_middleware_denied() -> None:
    """Проверяет блокировку сообщения из неразрешённого чата."""
    middleware = AuthMiddleware()
    handler = AsyncMock()
    event = AsyncMock()

    settings.bot.allowed_chat_ids = [-100123456789]
    chat = MagicMock(id=-100999999999)
    data = {"event_chat": chat, "event_from_user": MagicMock(id=222)}

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
        mock_settings.storage.max_file_size_mb = 50
        mock_settings.storage.rate_limit_per_minute = 10
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
        mock_settings.storage.max_file_size_mb = 50
        mock_settings.storage.rate_limit_per_minute = 10
        await handle_document(message, bot, state)

        bot.download.assert_called_once()
        message.answer.assert_called_once()


@pytest.mark.asyncio
async def test_cmd_help() -> None:
    """Проверяет команду /help."""
    message = AsyncMock()
    await cmd_help(message)
    message.answer.assert_called_once()
    assert "Справка по командам" in message.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_cmd_list_empty() -> None:
    """Проверяет команду /list, когда документов нет."""
    message = AsyncMock()
    with patch("src.bot.handlers.commands.async_session") as mock_session_maker:
        mock_session = AsyncMock()
        mock_session_maker.return_value.__aenter__.return_value = mock_session

        with patch("src.bot.handlers.commands.DocumentRepository") as mock_repo_class:
            mock_repo = MagicMock()
            mock_repo.get_recent_documents = AsyncMock(return_value=[])
            mock_repo_class.return_value = mock_repo

            await cmd_list(message)
            message.answer.assert_called_once_with("В базе данных пока нет сохраненных документов.")


@pytest.mark.asyncio
async def test_cmd_list_with_items() -> None:
    """Проверяет команду /list, когда в БД есть документы."""
    message = AsyncMock()

    mock_doc = MagicMock()
    mock_doc.saved_filename = "file.pdf"
    mock_doc.category = "Медицина"
    mock_doc.created_at = MagicMock()
    mock_doc.created_at.strftime = MagicMock(return_value="26.06.2026")
    mock_doc.gdrive_link = "https://drive.google.com/doc"

    with patch("src.bot.handlers.commands.async_session") as mock_session_maker:
        mock_session = AsyncMock()
        mock_session_maker.return_value.__aenter__.return_value = mock_session

        with patch("src.bot.handlers.commands.DocumentRepository") as mock_repo_class:
            mock_repo = MagicMock()
            mock_repo.get_recent_documents = AsyncMock(return_value=[mock_doc])
            mock_repo_class.return_value = mock_repo

            await cmd_list(message)
            message.answer.assert_called_once()
            assert "file.pdf" in message.answer.call_args[0][0]
            assert "Медицина" in message.answer.call_args[0][0]
            assert "https://drive.google.com" in message.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_cmd_stats() -> None:
    """Проверяет команду /stats."""
    message = AsyncMock()

    with patch("src.bot.handlers.commands.async_session") as mock_session_maker:
        mock_session = AsyncMock()
        mock_session_maker.return_value.__aenter__.return_value = mock_session

        # Мокаем общее количество документов
        mock_result = MagicMock()
        mock_result.scalar_one = MagicMock(return_value=5)
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("src.bot.handlers.commands.DocumentRepository") as mock_repo_class:
            mock_repo = MagicMock()
            mock_repo.get_stats_by_category = AsyncMock(return_value=[("Медицина", 3), ("Документы", 2)])
            mock_repo_class.return_value = mock_repo

            await cmd_stats(message)
            message.answer.assert_called_once()
            assert "Всего документов: *5*" in message.answer.call_args[0][0]
            assert "Медицина: *3*" in message.answer.call_args[0][0]
            assert "Документы: *2*" in message.answer.call_args[0][0]
