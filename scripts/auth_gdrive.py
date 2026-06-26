"""Скрипт для получения OAuth 2.0 токена Google Drive."""

import sys
from pathlib import Path

# Добавляем корень проекта в путь поиска модулей, чтобы импортировать из src
sys.path.append(str(Path(__file__).resolve().parent.parent))

from google_auth_oauthlib.flow import InstalledAppFlow
from src.config import settings


def main() -> None:
    """Запускает авторизацию в браузере и сохраняет токен."""
    creds_path = Path(settings.storage.gdrive_credentials_path)
    token_path = Path(settings.storage.gdrive_token_path)

    if not creds_path.exists():
        print(f"Ошибка: Файл {creds_path} не найден.")
        print("Пожалуйста, скачайте OAuth Client ID JSON и сохраните его по этому пути.")
        return

    print("Инициализация авторизации Google Drive...")
    flow = InstalledAppFlow.from_client_secrets_file(
        str(creds_path),
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    creds = flow.run_local_server(port=0)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    print(f"Успешно! Токен сохранен в {token_path}")


if __name__ == "__main__":
    main()
