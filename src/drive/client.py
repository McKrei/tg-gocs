"""Клиент для авторизации и взаимодействия с Google Drive API."""

from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from src.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


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
                logger.warning(
                    "Используется Service Account. Загрузка может завершиться ошибкой storageQuotaExceeded."
                )
            except Exception as e:
                raise ValueError(f"Ошибка инициализации Service Account из {creds_path}: {e}") from e
        else:
            raise FileNotFoundError(
                f"Не найден OAuth токен {token_path} и отсутствует файл Service Account {creds_path}."
            )

    return build("drive", "v3", credentials=creds)
