"""Клиент для авторизации и взаимодействия с Google Drive API."""

import io
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from src.core.config import settings
from src.core.utils.logger import get_logger

logger = get_logger(__name__)

SUPPORTED_MIME_TYPES = {"image/jpeg", "image/png", "application/pdf"}


def is_drive_configured() -> bool:
    """Проверяет наличие файлов конфигурации для Google Drive."""
    creds_path = Path(settings.storage.gdrive_credentials_path)
    token_path = Path(settings.storage.gdrive_token_path)
    if settings.storage.gdrive_root_folder_id == "dummy-gdrive-folder-id":
        return False
    return token_path.exists() or creds_path.exists()


def get_drive_service() -> Any:
    """Возвращает авторизованный клиент Google Drive API."""
    if not is_drive_configured():
        raise FileNotFoundError(
            f"Не найден файл токена {settings.storage.gdrive_token_path} "
            f"или файл credentials {settings.storage.gdrive_credentials_path}."
        )

    creds = None
    token_path = Path(settings.storage.gdrive_token_path)
    creds_path = Path(settings.storage.gdrive_credentials_path)

    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(  # type: ignore[no-untyped-call]
                str(token_path),
                scopes=["https://www.googleapis.com/auth/drive"],
            )
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                token_path.write_text(creds.to_json(), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Ошибка чтения OAuth токена из {token_path}: {e}")
            creds = None

    if not creds:
        if creds_path.exists():
            try:
                creds = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
                    str(creds_path),
                    scopes=["https://www.googleapis.com/auth/drive"],
                )
                logger.warning("Используется Service Account. Загрузка может завершиться ошибкой storageQuotaExceeded.")
            except Exception as e:
                raise ValueError(f"Ошибка инициализации Service Account из {creds_path}: {e}") from e
        else:
            raise FileNotFoundError(
                f"Не найден OAuth токен {token_path} и отсутствует файл Service Account {creds_path}."
            )

    return build("drive", "v3", credentials=creds)


def list_files_recursive(
    folder_id: str,
    service: Any | None = None,
    _visited: set[str] | None = None,
) -> list[dict[str, str]]:
    """Рекурсивно возвращает все файлы (не папки) поддерживаемых форматов из указанной папки Drive."""
    if service is None:
        service = get_drive_service()
    if _visited is None:
        _visited = set()
    if folder_id in _visited:
        return []
    _visited.add(folder_id)

    results: list[dict[str, str]] = []
    page_token = None

    while True:
        query = f"'{folder_id}' in parents and trashed = false"
        params: dict[str, Any] = {
            "q": query,
            "fields": "nextPageToken, files(id, name, mimeType, webViewLink)",
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
            "pageSize": 200,
        }
        if page_token:
            params["pageToken"] = page_token

        response = service.files().list(**params).execute()
        items = response.get("files", [])

        for item in items:
            mime = item.get("mimeType", "")
            if mime == "application/vnd.google-apps.folder":
                results.extend(list_files_recursive(item["id"], service, _visited))
            elif mime in SUPPORTED_MIME_TYPES:
                results.append(
                    {
                        "id": item["id"],
                        "name": item["name"],
                        "mimeType": mime,
                        "webViewLink": item.get("webViewLink", ""),
                    }
                )

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return results


def list_files_in_folder(folder_id: str) -> list[dict[str, str]]:
    """Возвращает файлы поддерживаемых форматов только из указанной папки (без рекурсии)."""
    service = get_drive_service()
    results: list[dict[str, str]] = []
    page_token = None

    while True:
        query = f"'{folder_id}' in parents and trashed = false and mimeType != 'application/vnd.google-apps.folder'"
        params: dict[str, Any] = {
            "q": query,
            "fields": "nextPageToken, files(id, name, mimeType, webViewLink)",
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
            "pageSize": 100,
        }
        if page_token:
            params["pageToken"] = page_token

        response = service.files().list(**params).execute()
        for item in response.get("files", []):
            mime = item.get("mimeType", "")
            if mime in SUPPORTED_MIME_TYPES:
                results.append(
                    {
                        "id": item["id"],
                        "name": item["name"],
                        "mimeType": mime,
                        "webViewLink": item.get("webViewLink", ""),
                    }
                )

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return results


def download_file(file_id: str, dest_path: Path) -> None:
    """Скачивает файл из Google Drive по file_id в указанный путь."""
    service = get_drive_service()
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with io.FileIO(str(dest_path), "wb") as fio:
        downloader = MediaIoBaseDownload(fio, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def delete_drive_file(file_id: str) -> None:
    """Удаляет файл из Google Drive по его идентификатору."""
    service = get_drive_service()
    service.files().delete(fileId=file_id, supportsAllDrives=True).execute()
    logger.info(f"Файл {file_id} удалён из Google Drive.")
