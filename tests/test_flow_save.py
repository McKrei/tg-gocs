from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import types
from aiogram.fsm.context import FSMContext

from src.bot.handlers.callbacks import handle_cancel_save, handle_confirm_save
from src.bot.handlers.files import handle_photo
from src.bot.handlers.text import handle_refinement
from src.bot.states import DocumentProcessingStates


@pytest.mark.asyncio
async def test_flow_save_end_to_end() -> None:
    """Сквозной тест сценария получения, корректировки и подтверждения сохранения документов."""
    # 1. Симулируем получение первого фото
    message_photo1 = AsyncMock()
    bot = AsyncMock()
    state = AsyncMock(spec=FSMContext)

    photo1 = MagicMock(file_id="photo_1", file_size=500)
    message_photo1.photo = [photo1]

    file_info1 = MagicMock(file_path="photos/file_1.jpg")
    bot.get_file = AsyncMock(return_value=file_info1)
    bot.download = AsyncMock()

    # Мокаем FSM data
    fsm_data: dict = {}

    async def mock_get_data() -> dict:
        return fsm_data

    async def mock_update_data(**kwargs: dict) -> dict:
        fsm_data.update(kwargs)
        return fsm_data

    state.get_data = AsyncMock(side_effect=mock_get_data)
    state.update_data = AsyncMock(side_effect=mock_update_data)
    state.set_state = AsyncMock()

    mock_draft = {
        "category": "Медицина",
        "suggested_filename": "polis.jpg",
        "summary": "Медицинский полис",
        "owner": "Муж",
    }

    status_message = AsyncMock()
    status_message.message_id = 456
    message_photo1.answer = AsyncMock(return_value=status_message)

    with patch("src.bot.handlers.files.classify_document", AsyncMock(return_value=mock_draft)):
        await handle_photo(message_photo1, bot, state)

        # Проверяем, что установлен стейт и сохранены файлы
        state.set_state.assert_called_once_with(DocumentProcessingStates.confirming)
        assert len(fsm_data["files"]) == 1
        assert fsm_data["files"][0].endswith("photo_1.jpg")
        assert fsm_data["draft"] == mock_draft
        assert fsm_data["msg_id"] == 456

    # 2. Симулируем получение второго фото (пакетный режим)
    message_photo2 = AsyncMock()
    message_photo2.chat.id = 123
    photo2 = MagicMock(file_id="photo_2", file_size=600)
    message_photo2.photo = [photo2]

    file_info2 = MagicMock(file_path="photos/file_2.jpg")
    bot.get_file = AsyncMock(return_value=file_info2)

    await handle_photo(message_photo2, bot, state)

    # Проверяем, что добавился второй файл и обновилось сообщение
    assert len(fsm_data["files"]) == 2
    bot.edit_message_text.assert_called_once()

    # 3. Симулируем текстовую корректировку черновика
    message_text = AsyncMock()
    message_text.text = "нет, это полис жены"
    message_text.chat.id = 123

    refined_draft = {
        "category": "Медицина/Жена",
        "suggested_filename": "polis_wife.jpg",
        "summary": "Медицинский полис жены",
        "owner": "Жена",
    }

    bot.edit_message_text.reset_mock()

    with patch("src.bot.handlers.text.refine_draft", AsyncMock(return_value=refined_draft)):
        await handle_refinement(message_text, bot, state)

        assert fsm_data["draft"] == refined_draft
        bot.edit_message_text.assert_called_once()
        message_text.delete.assert_called_once()

    # 4. Симулируем подтверждение сохранения документов (confirm_save)
    callback = AsyncMock()
    callback.message = AsyncMock(spec=types.Message)
    callback.message.edit_text = AsyncMock()
    callback.message.chat = MagicMock()
    callback.message.chat.id = 123
    callback.message.message_id = 456

    save_result = {
        "local_path": "data/documents/Медицина/Жена/polis_wife.pdf",
        "gdrive_link": "https://drive.google.com/polis_wife",
    }

    with (
        patch("src.bot.handlers.callbacks.convert_to_pdf", AsyncMock(return_value="data/temp/merged.pdf")),
        patch("src.bot.handlers.callbacks.save_to_local_and_drive", AsyncMock(return_value=save_result)),
        patch("src.bot.handlers.callbacks.get_embedding", AsyncMock(return_value=[0.1] * 768)),
        patch("src.bot.handlers.callbacks.async_session") as mock_session_maker,
        patch("src.bot.handlers.callbacks._cleanup_files") as mock_cleanup,
    ):
        mock_session = AsyncMock()
        mock_session_maker.return_value.__aenter__.return_value = mock_session

        with patch("src.bot.handlers.callbacks.DocumentRepository") as mock_repo_class:
            mock_repo = MagicMock()
            mock_repo.add_document = AsyncMock()
            mock_repo_class.return_value = mock_repo

            await handle_confirm_save(callback, bot, state)

            # Проверяем склеивание (так как файлов 2), выгрузку и сохранение в БД
            mock_repo.add_document.assert_called_once()
            mock_session.commit.assert_called_once()
            state.clear.assert_called_once()
            callback.message.edit_text.assert_called_once()
            assert mock_cleanup.call_count > 0


@pytest.mark.asyncio
async def test_flow_cancel() -> None:
    """Проверяет сценарий отмены сохранения документов."""
    callback = AsyncMock()
    callback.message = AsyncMock(spec=types.Message)
    callback.message.edit_text = AsyncMock()
    state = AsyncMock(spec=FSMContext)

    fsm_data = {"files": ["file1.jpg"], "draft": {}}
    state.get_data = AsyncMock(return_value=fsm_data)
    state.clear = AsyncMock()

    with patch("src.bot.handlers.callbacks._cleanup_files") as mock_cleanup:
        await handle_cancel_save(callback, state)

        mock_cleanup.assert_called_once_with(["file1.jpg"])
        state.clear.assert_called_once()
        callback.answer.assert_called_once_with("Сохранение отменено.")
        callback.message.edit_text.assert_called_once()
