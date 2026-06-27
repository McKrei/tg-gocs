"""Модуль для асинхронной загрузки файлов в Google Drive с сохранением структуры папок."""

import asyncio
import json
from pathlib import Path
from typing import Any

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from src.config import settings
from src.drive.cache import folder_cache
from src.drive.client import get_drive_service, is_drive_configured
from src.utils.logger import get_logger

logger = get_logger(__name__)

NON_RETRYABLE_REASONS = {
    "storageQuotaExceeded",
    "insufficientFilePermissions",
    "notFound",
}


def _extract_http_error_reason(error: HttpError) -> str:
    try:
        details = error.error_details
        if details:
            return str(details[0].get("reason") or details[0].get("message") or "")
    except Exception:
        pass

    try:
        payload = json.loads(error.content.decode("utf-8"))
        errors = payload.get("error", {}).get("errors", [])
        if errors:
            return str(errors[0].get("reason") or errors[0].get("message") or "")
        return str(payload.get("error", {}).get("message") or "")
    except Exception:
        return ""


def _format_upload_error(error: Exception) -> str:
    """Формирует короткое описание ошибки Google Drive."""
    if isinstance(error, HttpError):
        reason = _extract_http_error_reason(error)
        return reason or str(error)
    return str(error)


def _is_non_retryable_upload_error(error: Exception) -> bool:
    if not isinstance(error, HttpError):
        return False
    return _extract_http_error_reason(error) in NON_RETRYABLE_REASONS


def _find_or_create_folder_sync(service: Any, folder_name: str, parent_id: str) -> str:
    """Ищет папку по имени внутри родительской. Создает ее, если не найдена."""
    query = (
        f"name = '{folder_name}' "
        f"and '{parent_id}' in parents "
        f"and mimeType = 'application/vnd.google-apps.folder' "
        f"and trashed = false"
    )
    results = (
        service.files()
        .list(q=query, fields="files(id, name)", supportsAllDrives=True, includeItemsFromAllDrives=True)
        .execute()
    )
    files = results.get("files", [])

    if files:
        return str(files[0]["id"])

    # Создаем папку, так как она не найдена
    file_metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = service.files().create(body=file_metadata, fields="id", supportsAllDrives=True).execute()
    return str(folder["id"])


def _upload_file_sync(local_filepath: str, target_path: str) -> dict[str, str]:
    """Выполняет синхронную цепочку вызовов API для создания структуры папок и загрузки файла."""
    service = get_drive_service()
    local_path = Path(local_filepath)
    target_parts = Path(target_path).parts

    parent_id = settings.storage.gdrive_root_folder_id

    # Проверяем промежуточные папки
    folder_parts = target_parts[:-1]
    full_folder_path = "/".join(folder_parts)
    cached_id = folder_cache.get(full_folder_path)

    if cached_id:
        parent_id = cached_id
    else:
        for i, folder_name in enumerate(folder_parts):
            current_path = "/".join(folder_parts[:i+1])
            cached_part_id = folder_cache.get(current_path)
            if cached_part_id:
                parent_id = cached_part_id
            else:
                parent_id = _find_or_create_folder_sync(service, folder_name, parent_id)
                folder_cache.set(current_path, parent_id)

    # Имя файла для сохранения
    file_name = target_parts[-1]

    file_metadata = {"name": file_name, "parents": [parent_id]}
    media = MediaFileUpload(str(local_path), resumable=True)

    uploaded_file = (
        service.files()
        .create(body=file_metadata, media_body=media, fields="id, webViewLink", supportsAllDrives=True)
        .execute()
    )

    return {
        "webViewLink": str(uploaded_file["webViewLink"]),
        "folderId": parent_id,
    }


async def _upload_file_with_retry(local_filepath: str, target_path: str) -> dict[str, str]:
    """Выполняет выгрузку файла с повторными попытками."""
    attempts = 3
    delay = 1.0
    for attempt in range(1, attempts + 1):
        try:
            result: dict[str, str] = await asyncio.to_thread(_upload_file_sync, local_filepath, target_path)
            return result
        except Exception as e:
            if _is_non_retryable_upload_error(e):
                raise
            if attempt == attempts:
                logger.error(f"Все попытки ({attempts}) выгрузки файла на Google Drive провалились: {e}")
                raise
            logger.warning(
                f"Попытка {attempt}/{attempts} выгрузки файла на Google Drive завершилась ошибкой: {e}. "
                f"Повтор через {delay:.2f} сек..."
            )
            await asyncio.sleep(delay)
            delay *= 2
    raise RuntimeError("Выгрузка файла на Google Drive завершилась без результата.")


def find_folder_by_path_sync(category: str) -> str | None:
    """Ищет идентификатор папки в Google Drive по её относительному пути."""
    cached_id = folder_cache.get(category)
    if cached_id:
        return cached_id

    service = get_drive_service()
    parent_id = settings.storage.gdrive_root_folder_id
    parts = [p for p in Path(category).parts if p and p != "."]

    for i, folder_name in enumerate(parts):
        current_path = "/".join(parts[:i+1])
        cached_part_id = folder_cache.get(current_path)
        if cached_part_id:
            parent_id = cached_part_id
            continue

        query = (
            f"name = '{folder_name}' "
            f"and '{parent_id}' in parents "
            f"and mimeType = 'application/vnd.google-apps.folder' "
            f"and trashed = false"
        )
        try:
            results = (
                service.files()
                .list(q=query, fields="files(id)", supportsAllDrives=True, includeItemsFromAllDrives=True)
                .execute()
            )
            files = results.get("files", [])
            if not files:
                return None
            parent_id = str(files[0]["id"])
            folder_cache.set(current_path, parent_id)
        except Exception:
            return None

    return parent_id


async def find_folder_by_path(category: str) -> str | None:
    """Ищет идентификатор папки в Google Drive по её относительному пути."""
    return await asyncio.to_thread(find_folder_by_path_sync, category)


async def upload_file(local_filepath: str, target_path: str) -> str | None:
    """Запускает загрузку файла на Google Drive в отдельном потоке с повторными попытками."""
    result = await upload_file_with_status(local_filepath, target_path)
    return result["link"]


async def upload_file_with_status(local_filepath: str, target_path: str) -> dict[str, str | None]:
    """Загружает файл на Google Drive и возвращает ссылку или причину ошибки."""
    if not is_drive_configured():
        logger.warning(f"Интеграция с Google Drive отключена. Файл {local_filepath} сохранен только локально.")
        return {"link": None, "folder_link": None, "error": "Google Drive не настроен"}

    try:
        res: dict[str, str] = await _upload_file_with_retry(local_filepath, target_path)
        folder_link = f"https://drive.google.com/drive/folders/{res['folderId']}"
        logger.info(f"Файл {local_filepath} успешно выгружен на Google Drive: {res['webViewLink']}")
        return {"link": res["webViewLink"], "folder_link": folder_link, "error": None}
    except Exception as e:
        error = _format_upload_error(e)
        logger.error(f"Ошибка после всех попыток выгрузки файла {local_filepath} на Google Drive: {error}")
        return {"link": None, "folder_link": None, "error": error}
