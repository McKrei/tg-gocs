"""Тесты для сервиса синхронизации sync.py."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.modules.documents.models import Document
from src.modules.documents.services.sync import sync_drive_to_db


@pytest.mark.asyncio
async def test_sync_drive_to_db_removes_stale_documents() -> None:
    """Проверяет, что неактуальные документы (удаленные из Drive) вычищаются из БД и диска."""
    drive_files = [
        {
            "id": "drive_id_active",
            "name": "active_file.jpg",
            "mimeType": "image/jpeg",
            "webViewLink": "http://drive/active",
        }
    ]

    mock_list_recursive = MagicMock(return_value=drive_files)
    mock_is_configured = MagicMock(return_value=True)

    doc_active = Document(
        id="11111111-1111-1111-1111-111111111111",
        saved_filename="active_file.jpg",
        local_path="data/documents/Личные документы/active_file.jpg",
        gdrive_link="http://drive/active",
        category="Личные документы",
        owner="Евгений",
        summary="Активный документ",
    )
    doc_stale = Document(
        id="22222222-2222-2222-2222-222222222222",
        saved_filename="stale_file.jpg",
        local_path="data/documents/Личные документы/stale_file.jpg",
        gdrive_link="http://drive/stale",
        category="Личные документы",
        owner="Евгений",
        summary="Удаленный документ",
    )

    mock_repo = MagicMock()
    mock_repo.get_all_gdrive_links = AsyncMock(return_value={"http://drive/active", "http://drive/stale"})
    mock_repo.delete_document = AsyncMock(return_value=True)

    mock_execute_result = MagicMock()
    mock_execute_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[doc_active, doc_stale])))

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_execute_result)
    mock_session.commit = AsyncMock()

    with (
        patch("src.modules.documents.services.sync.is_drive_configured", mock_is_configured),
        patch("src.modules.documents.services.sync.list_files_recursive", mock_list_recursive),
        patch("src.modules.documents.services.sync.async_session") as mock_session_maker,
        patch("src.modules.documents.services.sync.DocumentRepository", return_value=mock_repo),
        patch("src.modules.documents.services.sync.Path.exists", return_value=True),
        patch("src.modules.documents.services.sync.Path.is_file", return_value=True),
        patch("src.modules.documents.services.sync.Path.unlink") as mock_unlink,
    ):
        mock_session_maker.return_value.__aenter__.return_value = mock_session

        result = await sync_drive_to_db()

        assert result.removed == 1
        assert "stale_file.jpg" in result.removed_details
        mock_repo.delete_document.assert_called_once_with(doc_stale.id)
        mock_unlink.assert_called_once()
        mock_session.commit.assert_called()
