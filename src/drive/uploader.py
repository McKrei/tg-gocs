"""Модуль для асинхронной загрузки файлов в Google Drive с сохранением структуры папок."""

import asyncio
from pathlib import Path
from typing import Any

from googleapiclient.http import MediaFileUpload

from src.config import settings
from src.drive.client import get_drive_service, is_drive_configured
from src.utils.logger import get_logger
from src.utils.retry import with_retry

logger = get_logger(__name__)


def _find_or_create_folder_sync(service: Any, folder_name: str, parent_id: str) -> str:
    """Ищет папку по имени внутри родительской. Создает ее, если не найдена."""
    query = (
        f"name = '{folder_name}' "
        f"and '{parent_id}' in parents "
        f"and mimeType = 'application/vnd.google-apps.folder' "
        f"and trashed = false"
    )
    results = service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get("files", [])

    if files:
        return str(files[0]["id"])

    # Создаем папку, так как она не найдена
    file_metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = service.files().create(body=file_metadata, fields="id").execute()
    return str(folder["id"])


def _upload_file_sync(local_filepath: str, target_path: str) -> str:
    """Выполняет синхронную цепочку вызовов API для создания структуры папок и загрузки файла."""
    service = get_drive_service()
    local_path = Path(local_filepath)
    target_parts = Path(target_path).parts

    parent_id = settings.storage.gdrive_root_folder_id

    # Создаем/проверяем промежуточные папки (все части пути, кроме имени файла на конце)
    folder_parts = target_parts[:-1]
    for folder_name in folder_parts:
        parent_id = _find_or_create_folder_sync(service, folder_name, parent_id)

    # Имя файла для сохранения
    file_name = target_parts[-1]

    file_metadata = {"name": file_name, "parents": [parent_id]}
    media = MediaFileUpload(str(local_path), resumable=True)

    uploaded_file = service.files().create(body=file_metadata, media_body=media, fields="id, webViewLink").execute()

    return str(uploaded_file["webViewLink"])



@with_retry(attempts=3, initial_delay=1.0)
async def _upload_file_with_retry(local_filepath: str, target_path: str) -> str:
    """Выполняет выгрузку файла с повторными попытками."""
    result: str = await asyncio.to_thread(_upload_file_sync, local_filepath, target_path)
    return result


async def upload_file(local_filepath: str, target_path: str) -> str | None:
    """Запускает загрузку файла на Google Drive в отдельном потоке с повторными попытками."""
    if not is_drive_configured():
        logger.warning(f"Интеграция с Google Drive отключена. Файл {local_filepath} сохранен только локально.")
        return None

    try:
        link: str = await _upload_file_with_retry(local_filepath, target_path)
        logger.info(f"Файл {local_filepath} успешно выгружен на Google Drive: {link}")
        return link
    except Exception as e:
        logger.error(f"Ошибка после всех попыток выгрузки файла {local_filepath} на Google Drive: {e}")
        return None
