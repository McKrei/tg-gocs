from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import types
from aiogram.fsm.context import FSMContext

from src.modules.documents.handlers.callbacks import (
    handle_cancel_save,
    handle_confirm_save,
    handle_replace_save,
    handle_start_analysis,
)
from src.modules.documents.handlers.files import handle_photo
from src.modules.documents.handlers.text import handle_refinement
from src.modules.documents.states import DocumentProcessingStates


@pytest.mark.asyncio
async def test_flow_save_end_to_end() -> None:
    """Сквозной тест сценария получения, корректировки и подтверждения сохранения документов."""
    # 1. Симулируем получение первого фото
    message_photo1 = AsyncMock()
    bot = AsyncMock()
    state = MagicMock(spec=FSMContext)

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

    status_message = AsyncMock()
    status_message.message_id = 456
    message_photo1.answer = AsyncMock(return_value=status_message)

    await handle_photo(message_photo1, bot, state)

    # Проверяем, что стейт не переведен при накоплении и сохранены файлы
    assert len(fsm_data["files"]) == 1
    assert fsm_data["files"][0].endswith("photo_1.jpg")
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

    # 3. Симулируем вызов callback start_analysis
    callback_start = AsyncMock()
    callback_start.message = AsyncMock(spec=types.Message)
    callback_start.message.edit_text = AsyncMock()
    callback_start.message.chat = MagicMock()
    callback_start.message.chat.id = 123
    callback_start.message.message_id = 456

    mock_draft = {
        "category": "Медицина",
        "suggested_filename": "polis.pdf",
        "summary": "Медицинский полис",
        "owner": "Муж",
    }

    with (
        patch("src.modules.documents.handlers.callbacks.classify_document", AsyncMock(return_value=mock_draft)),
        patch("src.modules.documents.handlers.callbacks.merge_files_to_pdf", AsyncMock()),
        patch("src.modules.documents.handlers.callbacks.find_similar_document", AsyncMock(return_value=None)),
    ):
        await handle_start_analysis(callback_start, bot, state)

        # Проверяем переход в стейт confirming и то, что в files теперь один склеенный файл
        state.set_state.assert_called_once_with(DocumentProcessingStates.confirming)
        assert len(fsm_data["files"]) == 1
        assert "merged_" in fsm_data["files"][0]
        assert fsm_data["draft"]["category"] == mock_draft["category"]
        assert fsm_data["draft"]["suggested_filename"] == f"{date.today().isoformat()} polis.pdf"

    # 4. Симулируем текстовую корректировку черновика
    message_text = AsyncMock()
    message_text.text = "нет, это полис жены"
    message_text.chat.id = 123

    refined_draft = {
        "category": "Медицина/Жена",
        "suggested_filename": "polis_wife.pdf",
        "summary": "Медицинский полис жены",
        "owner": "Жена",
    }

    bot.edit_message_text.reset_mock()

    with patch("src.modules.documents.handlers.text.refine_draft", AsyncMock(return_value=refined_draft)):
        await handle_refinement(message_text, bot, state)

        assert fsm_data["draft"] == refined_draft
        bot.edit_message_text.assert_called_once()
        message_text.delete.assert_called_once()

    # 5. Симулируем подтверждение сохранения документов (confirm_save)
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
        patch("src.modules.documents.handlers.callbacks.save_to_local_and_drive", AsyncMock(return_value=save_result)),
        patch("src.modules.documents.handlers.callbacks.get_embedding", AsyncMock(return_value=[0.1] * 768)),
        patch("src.modules.documents.handlers.callbacks.async_session") as mock_session_maker,
        patch("src.modules.documents.handlers.callbacks._cleanup_files") as mock_cleanup,
    ):
        mock_session = AsyncMock()
        mock_session_maker.return_value.__aenter__.return_value = mock_session

        with patch("src.modules.documents.handlers.callbacks.DocumentRepository") as mock_repo_class:
            mock_repo = MagicMock()
            mock_repo.add_document = AsyncMock()
            mock_repo_class.return_value = mock_repo

            await handle_confirm_save(callback, bot, state)

            mock_repo.add_document.assert_called_once()
            mock_session.commit.assert_called_once()
            state.clear.assert_called_once()
            assert callback.message.edit_text.call_args_list[0].args[0] == "⏳ Сохраняю документ..."
            assert callback.message.edit_text.call_args_list[0].kwargs["reply_markup"] is None
            assert "Документ успешно сохранен" in callback.message.edit_text.call_args_list[-1].args[0]
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

    with patch("src.modules.documents.handlers.callbacks._cleanup_files") as mock_cleanup:
        await handle_cancel_save(callback, state)

        mock_cleanup.assert_called_once_with(["file1.jpg"])
        state.clear.assert_called_once()
        callback.answer.assert_called_once_with("Сохранение отменено.")
        callback.message.edit_text.assert_called_once()


@pytest.mark.asyncio
async def test_flow_replace_save() -> None:
    """Проверяет сценарий замены существующего документа."""
    callback = AsyncMock()
    callback.message = AsyncMock(spec=types.Message)
    callback.message.edit_text = AsyncMock()
    bot = AsyncMock()
    state = AsyncMock(spec=FSMContext)

    fsm_data = {
        "files": ["file1.jpg"],
        "draft": {
            "category": "Медицина",
            "suggested_filename": "polis.jpg",
            "summary": "Медицинский полис",
            "owner": "Жена",
        },
        "duplicate_id": "00000000-0000-0000-0000-000000000000",
    }

    state.get_data = AsyncMock(return_value=fsm_data)
    state.clear = AsyncMock()

    save_result = {
        "local_path": "data/documents/Медицина/polis.jpg",
        "gdrive_link": "https://drive.google.com/polis",
        "gdrive_folder_link": "https://drive.google.com/folder",
        "gdrive_error": None,
    }

    import uuid

    from src.modules.documents.models import Document

    mock_old_doc = MagicMock(spec=Document)
    mock_old_doc.local_path = "data/documents/Медицина/polis_old.jpg"
    mock_old_doc.gdrive_link = "https://drive.google.com/file/d/old_gdrive_id/view"

    with (
        patch("src.modules.documents.handlers.callbacks.save_to_local_and_drive", AsyncMock(return_value=save_result)),
        patch("src.modules.documents.handlers.callbacks.get_embedding", AsyncMock(return_value=[0.1] * 768)),
        patch("src.modules.documents.handlers.callbacks.async_session") as mock_session_maker,
        patch("src.modules.documents.handlers.callbacks.delete_file_from_drive", AsyncMock()) as mock_del_drive,
        patch("src.modules.documents.handlers.callbacks.extract_gdrive_file_id", return_value="old_gdrive_id"),
        patch("src.modules.documents.handlers.callbacks._cleanup_files") as mock_cleanup,
        patch("pathlib.Path.exists", return_value=True),
        patch("pathlib.Path.unlink", MagicMock()) as mock_unlink,
    ):
        mock_session = AsyncMock()
        mock_session_maker.return_value.__aenter__.return_value = mock_session

        with patch("src.modules.documents.handlers.callbacks.DocumentRepository") as mock_repo_class:
            mock_repo = MagicMock()
            mock_repo.get_document = AsyncMock(return_value=mock_old_doc)
            mock_repo.delete_document = AsyncMock(return_value=True)
            mock_repo.add_document = AsyncMock()
            mock_repo_class.return_value = mock_repo

            await handle_replace_save(callback, bot, state)

            mock_repo.get_document.assert_called_once_with(uuid.UUID("00000000-0000-0000-0000-000000000000"))
            mock_unlink.assert_called_once()
            mock_del_drive.assert_called_once_with("old_gdrive_id")
            mock_repo.delete_document.assert_called_once()
            mock_repo.add_document.assert_called_once()
            mock_session.commit.assert_called()
            state.clear.assert_called_once()
            assert "Документ успешно заменен" in callback.message.edit_text.call_args_list[-1].args[0]
            assert mock_cleanup.call_count > 0
