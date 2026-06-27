from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import types
from aiogram.fsm.context import FSMContext

from src.modules.documents.handlers.callbacks import handle_confirm_save
from src.modules.documents.handlers.files import handle_photo, rate_limits


@pytest.mark.asyncio
async def test_file_too_large() -> None:
    """Проверяет отклонение слишком больших файлов."""
    message = AsyncMock()
    bot = AsyncMock()
    state = AsyncMock()

    # Файл размером 60 МБ при лимите 50 МБ
    photo = MagicMock(file_id="large_photo", file_size=60 * 1024 * 1024)
    message.photo = [photo]

    with patch("src.modules.documents.handlers.files.settings") as mock_settings:
        mock_settings.storage.max_file_size_mb = 50
        await handle_photo(message, bot, state)

        message.answer.assert_called_once_with("Файл слишком большой. Максимальный размер: 50 МБ.")
        # Убеждаемся, что бот не пытался скачать файл
        bot.download.assert_not_called()


@pytest.mark.asyncio
async def test_rate_limiting() -> None:
    """Проверяет срабатывание rate limit."""
    message = AsyncMock()
    message.from_user.id = 9999
    bot = AsyncMock()
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={})

    photo = MagicMock(file_id="photo", file_size=100)
    message.photo = [photo]

    rate_limits.clear()

    with patch("src.modules.documents.handlers.files.settings") as mock_settings:
        mock_settings.storage.max_file_size_mb = 50
        mock_settings.storage.rate_limit_per_minute = 2

        # Первые 2 запроса должны пройти
        await handle_photo(message, bot, state)
        await handle_photo(message, bot, state)
        assert message.answer.call_count == 2

        # 3-й запрос должен быть заблокирован
        await handle_photo(message, bot, state)
        message.answer.assert_any_call("Вы отправляете файлы слишком часто. Пожалуйста, подождите немного.")


@pytest.mark.asyncio
async def test_drive_unavailable_fallback() -> None:
    """Проверяет сохранение только локально при недоступности Google Drive."""
    callback = AsyncMock()
    callback.message = AsyncMock(spec=types.Message)
    callback.message.edit_text = AsyncMock()
    state = AsyncMock(spec=FSMContext)
    bot = AsyncMock()

    fsm_data = {
        "files": ["file1.jpg"],
        "draft": {
            "category": "Медицина",
            "suggested_filename": "polis.jpg",
            "summary": "Медицинский полис",
            "owner": "Муж",
        },
    }
    state.get_data = AsyncMock(return_value=fsm_data)

    save_result = {
        "local_path": "data/documents/Медицина/polis.jpg",
        "gdrive_link": None,
        "gdrive_error": "Ошибка Google Drive: storageQuotaExceeded",
    }

    with (
        patch("src.modules.documents.handlers.callbacks.save_to_local_and_drive", AsyncMock(return_value=save_result)),
        patch("src.modules.documents.handlers.callbacks.get_embedding", AsyncMock(return_value=[0.1] * 768)),
        patch("src.modules.documents.handlers.callbacks.async_session") as mock_session_maker,
        patch("src.modules.documents.handlers.callbacks._cleanup_files"),
    ):
        mock_session = AsyncMock()
        mock_session_maker.return_value.__aenter__.return_value = mock_session

        with patch("src.modules.documents.handlers.callbacks.DocumentRepository") as mock_repo_class:
            mock_repo = MagicMock()
            mock_repo.add_document = AsyncMock()
            mock_repo.add_pending_upload = AsyncMock()
            mock_repo_class.return_value = mock_repo

            await handle_confirm_save(callback, bot, state)

            mock_repo.add_document.assert_called_once()
            # Проверяем, что в БД записался None для gdrive_link
            _, kwargs = mock_repo.add_document.call_args
            assert kwargs["gdrive_link"] is None

            final_text = callback.message.edit_text.call_args_list[-1].args[0]
            assert "Google Drive: ошибка загрузки" in final_text
            assert "storageQuotaExceeded" in final_text


@pytest.mark.asyncio
async def test_confirm_save_removes_buttons_before_long_operations() -> None:
    """Проверяет, что кнопки исчезают сразу после подтверждения."""
    callback = AsyncMock()
    callback.message = AsyncMock(spec=types.Message)
    callback.message.edit_text = AsyncMock()
    state = AsyncMock(spec=FSMContext)
    bot = AsyncMock()

    state.get_data = AsyncMock(
        return_value={
            "files": ["file1.jpg"],
            "draft": {
                "category": "Медицина",
                "suggested_filename": "polis.jpg",
                "summary": "Медицинский полис",
                "owner": "Муж",
            },
        }
    )

    save_result = {
        "local_path": "data/documents/Медицина/polis.jpg",
        "gdrive_link": "https://drive.google.com/file",
        "gdrive_error": None,
    }

    with (
        patch("src.modules.documents.handlers.callbacks.save_to_local_and_drive", AsyncMock(return_value=save_result)),
        patch("src.modules.documents.handlers.callbacks.get_embedding", AsyncMock(return_value=[0.1] * 768)),
        patch("src.modules.documents.handlers.callbacks.async_session") as mock_session_maker,
        patch("src.modules.documents.handlers.callbacks._cleanup_files"),
    ):
        mock_session = AsyncMock()
        mock_session_maker.return_value.__aenter__.return_value = mock_session

        with patch("src.modules.documents.handlers.callbacks.DocumentRepository") as mock_repo_class:
            mock_repo = MagicMock()
            mock_repo.add_document = AsyncMock()
            mock_repo_class.return_value = mock_repo

            await handle_confirm_save(callback, bot, state)

    first_call = callback.message.edit_text.call_args_list[0]
    assert first_call.args[0] == "⏳ Сохраняю документ..."
    assert first_call.kwargs["reply_markup"] is None
