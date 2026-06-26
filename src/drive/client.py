"""Клиент для авторизации и взаимодействия с Google Drive API."""

from pathlib import Path
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build

from src.config import settings


def is_drive_configured() -> bool:
    """Проверяет, предоставлены ли файлы конфигурации для работы с Google Drive."""
    creds_path = Path(settings.storage.gdrive_credentials_path)
    return creds_path.exists() and settings.storage.gdrive_root_folder_id != "dummy-gdrive-folder-id"


def get_drive_service() -> Any:
    """Создает и возвращает авторизованный клиент Google Drive API."""
    if not is_drive_configured():
        raise FileNotFoundError(
            f"Файл credentials.json не найден по пути {settings.storage.gdrive_credentials_path} "
            f"или GDRIVE_ROOT_FOLDER_ID не настроен."
        )

    creds = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
        settings.storage.gdrive_credentials_path,
        scopes=["https://www.googleapis.com/auth/drive"],
    )

    return build("drive", "v3", credentials=creds)
