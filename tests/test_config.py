"""Тесты для модуля конфигурации проекта."""

from src.config import settings


def test_config_loads() -> None:
    """Проверка загрузки тестовых настроек из .env."""
    assert settings.bot.token == "123456:dummy-token"
    assert settings.bot.allowed_user_ids == [123456789]
    assert settings.llm.api_key == "sk-or-v1-dummy"
    assert settings.db.db_path == "data/sqlite.db"
    assert settings.db.database_url == "sqlite+aiosqlite:///data/sqlite.db"
    assert settings.storage.gdrive_root_folder_id == "dummy-gdrive-folder-id"
