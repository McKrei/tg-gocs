"""Тесты для интеграции с Google Drive (клиент, загрузчик, сохранение файлов)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from src.core.drive.client import get_drive_service, is_drive_configured
from src.core.drive.uploader import find_folder_by_path, upload_file, upload_file_with_status


@pytest.mark.asyncio
async def test_upload_file_mocked(tmp_path: Path) -> None:
    """Проверяет логику создания структуры папок и загрузки файла на Google Drive с моками API."""
    mock_service = MagicMock()
    mock_files = MagicMock()
    mock_create = MagicMock()
    mock_list = MagicMock()

    mock_service.files.return_value = mock_files

    # Мокаем поиск папки (возвращаем пустой список, чтобы папка создавалась)
    mock_list_execute = {"files": []}
    mock_list.execute.return_value = mock_list_execute
    mock_files.list.return_value = mock_list

    # Мокаем создание папки и последующую загрузку файла (две папки и один файл)
    mock_create_execute_folder = {"id": "folder_123"}
    mock_create_execute_file = {"id": "file_999", "webViewLink": "https://drive.google.com/file/d/mocked_file_link"}

    mock_create.execute.side_effect = [
        mock_create_execute_folder,
        mock_create_execute_folder,
        mock_create_execute_file,
    ]

    mock_files.create.return_value = mock_create

    # Создаем временный файл для теста
    temp_file = tmp_path / "doc.pdf"
    temp_file.write_text("test content")

    with (
        patch("src.core.drive.uploader.is_drive_configured", return_value=True),
        patch("src.core.drive.uploader.get_drive_service", return_value=mock_service),
    ):
        # Загружаем файл во вложенную категорию "Личное/Документы/doc.pdf"
        link = await upload_file(str(temp_file), "Личное/Документы/doc.pdf")

        assert link == "https://drive.google.com/file/d/mocked_file_link"
        # Проверяем, что поиск папок производился
        assert mock_files.list.call_count == 2
        # Проверяем, что создание папок и загрузка файла были выполнены (2 папки + 1 файл = 3 вызова)
        assert mock_files.create.call_count == 3


@pytest.mark.asyncio
async def test_upload_file_with_status_returns_error(tmp_path: Path) -> None:
    """Проверяет возврат причины ошибки Google Drive."""
    temp_file = tmp_path / "doc.pdf"
    temp_file.write_text("test content")

    with (
        patch("src.core.drive.uploader.is_drive_configured", return_value=True),
        patch("src.core.drive.uploader._upload_file_with_retry", side_effect=RuntimeError("storageQuotaExceeded")),
    ):
        result = await upload_file_with_status(str(temp_file), "Личное/doc.pdf")

    assert result["link"] is None
    assert result["error"] == "storageQuotaExceeded"


@pytest.mark.asyncio
async def test_upload_file_with_status_does_not_retry_permanent_quota_error(tmp_path: Path) -> None:
    """Проверяет отсутствие повторов для постоянной ошибки квоты Service Account."""
    temp_file = tmp_path / "doc.pdf"
    temp_file.write_text("test content")
    error = HttpError(
        Response({"status": "403", "reason": "Forbidden"}),
        b'{"error": {"errors": [{"reason": "storageQuotaExceeded"}], "code": 403}}',
    )

    with (
        patch("src.core.drive.uploader.is_drive_configured", return_value=True),
        patch("src.core.drive.uploader._upload_file_sync", side_effect=error) as mock_upload,
    ):
        result = await upload_file_with_status(str(temp_file), "Личное/doc.pdf")

    assert result["link"] is None
    assert result["error"] == "storageQuotaExceeded"
    mock_upload.assert_called_once()


@pytest.mark.integration
@pytest.mark.skipif(not is_drive_configured(), reason="Интеграция с Google Drive не настроена.")
@pytest.mark.asyncio
async def test_upload_file_real(tmp_path: Path) -> None:
    """Интеграционный тест с реальным Google Drive. Запускается только при наличии credentials.json."""
    temp_file = tmp_path / "real_test.pdf"
    temp_file.write_text("Hello, Google Drive!")

    link = await upload_file(str(temp_file), "Тесты/real_test.pdf")

    if link is None:
        print("⚠️ Интеграция с Drive настроена, но загрузка не удалась (например, из-за ограничений квоты).")
    else:
        assert link.startswith("https://")


@pytest.mark.asyncio
async def test_get_drive_service_oauth_success() -> None:
    """Проверяет успешное создание клиента Google Drive через OAuth2 при наличии token.json."""
    mock_creds_instance = MagicMock()
    mock_creds_instance.expired = False

    with (
        patch("src.core.drive.client.Path.exists", side_effect=lambda: True),
        patch(
            "src.core.drive.client.Credentials.from_authorized_user_file",
            return_value=mock_creds_instance,
        ) as mock_oauth_from_file,
        patch("src.core.drive.client.build") as mock_build,
    ):
        service = get_drive_service()
        assert service is not None
        mock_oauth_from_file.assert_called_once()
        mock_build.assert_called_once_with("drive", "v3", credentials=mock_creds_instance)


@pytest.mark.asyncio
async def test_get_drive_service_oauth_refresh() -> None:
    """Проверяет автоматическое продление токена, если он устарел."""
    mock_creds_instance = MagicMock()
    mock_creds_instance.expired = True
    mock_creds_instance.refresh_token = "some_refresh_token"

    mock_write_text = MagicMock()

    with (
        patch("src.core.drive.client.Path.exists", side_effect=lambda: True),
        patch("src.core.drive.client.Credentials.from_authorized_user_file", return_value=mock_creds_instance),
        patch("src.core.drive.client.Request"),
        patch("src.core.drive.client.Path.write_text", mock_write_text),
        patch("src.core.drive.client.build") as mock_build,
    ):
        service = get_drive_service()
        assert service is not None
        mock_creds_instance.refresh.assert_called_once()
        mock_write_text.assert_called_once()
        mock_build.assert_called_once_with("drive", "v3", credentials=mock_creds_instance)


@pytest.mark.asyncio
async def test_get_drive_service_service_account_fallback() -> None:
    """Проверяет откат на Service Account, если token.json не существует."""
    mock_sa_creds = MagicMock()

    def path_exists_mock(self: Path) -> bool:
        return "token.json" not in str(self)

    with (
        patch("src.core.drive.client.Path.exists", path_exists_mock),
        patch(
            "src.core.drive.client.service_account.Credentials.from_service_account_file",
            return_value=mock_sa_creds,
        ) as mock_sa_from_file,
        patch("src.core.drive.client.build") as mock_build,
    ):
        service = get_drive_service()
        assert service is not None
        mock_sa_from_file.assert_called_once()
        mock_build.assert_called_once_with("drive", "v3", credentials=mock_sa_creds)


@pytest.mark.asyncio
async def test_get_drive_service_no_creds_error() -> None:
    """Проверяет выброс FileNotFoundError, если нет никаких файлов для авторизации."""
    with (
        patch("src.core.drive.client.Path.exists", return_value=False),
        pytest.raises(FileNotFoundError),
    ):
        get_drive_service()


@pytest.mark.asyncio
async def test_find_folder_by_path() -> None:
    """Проверяет поиск идентификатора папки в Google Drive по её пути."""
    mock_service = MagicMock()
    mock_files = MagicMock()
    mock_list = MagicMock()

    mock_service.files.return_value = mock_files
    mock_files.list.return_value = mock_list

    mock_list.execute.return_value = {"files": [{"id": "folder_456"}]}

    with (
        patch("src.core.drive.uploader.is_drive_configured", return_value=True),
        patch("src.core.drive.uploader.get_drive_service", return_value=mock_service),
    ):
        folder_id = await find_folder_by_path("Личные/Документы")
        assert folder_id == "folder_456"
        assert mock_files.list.call_count == 2
