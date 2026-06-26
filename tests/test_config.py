"""Тесты для модуля конфигурации проекта."""

from src.config import settings


def test_config_loads() -> None:
    """Проверка загрузки настроек из .env."""
    assert isinstance(settings.bot.token, str)
    assert len(settings.bot.token) > 0
    assert isinstance(settings.bot.allowed_chat_ids, list)
    assert isinstance(settings.llm.api_key, str)
    assert len(settings.llm.api_key) > 0
    assert settings.db.db_path == "data/sqlite.db"
    assert settings.db.database_url == "sqlite+aiosqlite:///data/sqlite.db"
    assert isinstance(settings.storage.gdrive_root_folder_id, str)
    assert len(settings.storage.gdrive_root_folder_id) > 0
