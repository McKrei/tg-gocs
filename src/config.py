"""Настройки приложения, загружаемые из .env."""

from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotConfig(BaseSettings):
    """Настройки Telegram-бота."""

    token: str = Field(..., alias="BOT_TOKEN")
    allowed_chat_ids: list[int] = Field(default_factory=list, alias="ALLOWED_CHAT_IDS")

    @field_validator("allowed_chat_ids", mode="before")
    @classmethod
    def parse_allowed_ids(cls, v: Any) -> list[int]:
        """Парсинг списка chat_id из строки, разделенной запятыми."""
        if isinstance(v, str):
            if not v.strip():
                return []
            try:
                return [int(x.strip()) for x in v.split(",") if x.strip()]
            except ValueError as e:
                raise ValueError("ALLOWED_CHAT_IDS должен содержать список чисел через запятую") from e
        if isinstance(v, (int, float)):
            return [int(v)]
        if isinstance(v, (list, tuple, set)):
            return [int(x) for x in v]
        if v is None:
            return []
        raise ValueError("Неверный формат для ALLOWED_CHAT_IDS")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


class LLMConfig(BaseSettings):
    """Настройки моделей OpenRouter (Gemini)."""

    api_key: str = Field(..., alias="OPENROUTER_API_KEY")
    model_name: str = Field("google/gemini-3.5-flash", alias="LLM_MODEL")
    embedding_model_name: str = Field("google/gemini-embedding-2", alias="EMBEDDING_MODEL")
    embedding_dim: int = Field(768, alias="EMBEDDING_DIM")
    base_url: str = Field("https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


class DBConfig(BaseSettings):
    """Настройки базы данных SQLite."""

    db_path: str = Field("data/sqlite.db", alias="DB_PATH")

    @property
    def database_url(self) -> str:
        """DSN для асинхронного подключения к SQLite."""
        return f"sqlite+aiosqlite:///{self.db_path}"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


class StorageConfig(BaseSettings):
    """Настройки локального хранилища и Google Drive."""

    local_storage_dir: str = Field("data/documents", alias="LOCAL_STORAGE_DIR")
    temp_dir: str = Field("data/temp", alias="TEMP_DIR")
    gdrive_credentials_path: str = Field("data/credentials.json", alias="GDRIVE_CREDENTIALS_PATH")
    gdrive_token_path: str = Field("data/token.json", alias="GDRIVE_TOKEN_PATH")
    gdrive_root_folder_id: str = Field(..., alias="GDRIVE_ROOT_FOLDER_ID")
    max_file_size_mb: int = Field(50, alias="MAX_FILE_SIZE_MB")
    rate_limit_per_minute: int = Field(10, alias="RATE_LIMIT_PER_MINUTE")
    session_ttl_seconds: int = Field(1800, alias="SESSION_TTL_SECONDS")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


class Settings:
    """Общий класс конфигурации приложения."""

    def __init__(self) -> None:
        self.bot = BotConfig()  # type: ignore[call-arg]
        self.llm = LLMConfig()  # type: ignore[call-arg]
        self.db = DBConfig()  # type: ignore[call-arg]
        self.storage = StorageConfig()  # type: ignore[call-arg]


settings = Settings()
