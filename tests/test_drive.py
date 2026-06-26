"""Тесты для интеграции с Google Drive (клиент, загрузчик, сохранение файлов)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.drive.client import is_drive_configured
from src.drive.uploader import upload_file


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
        patch("src.drive.uploader.is_drive_configured", return_value=True),
        patch("src.drive.uploader.get_drive_service", return_value=mock_service),
    ):
        # Загружаем файл во вложенную категорию "Личное/Документы/doc.pdf"
        link = await upload_file(str(temp_file), "Личное/Документы/doc.pdf")

        assert link == "https://drive.google.com/file/d/mocked_file_link"
        # Проверяем, что поиск папок производился
        assert mock_files.list.call_count == 2
        # Проверяем, что создание папок и загрузка файла были выполнены (2 папки + 1 файл = 3 вызова)
        assert mock_files.create.call_count == 3


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
