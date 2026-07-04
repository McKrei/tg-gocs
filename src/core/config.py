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
    """Настройки моделей LLM и Embeddings."""

    provider: str = Field("minimax", alias="LLM_PROVIDER")
    
    # Ключи API
    openrouter_api_key: str = Field("", alias="OPENROUTER_API_KEY")
    minimax_api_key: str = Field("", alias="MINIMAX_API_KEY")

    # Базовые URL
    openrouter_base_url: str = Field("https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL")
    minimax_base_url: str = Field("https://api.minimax.io/v1", alias="MINIMAX_BASE_URL")

    # Модели (если не заданы, подставляются по умолчанию для провайдера)
    model_name: str = Field("", alias="LLM_MODEL")
    embedding_model_name: str = Field("google/gemini-embedding-2", alias="EMBEDDING_MODEL")
    embedding_dim: int = Field(768, alias="EMBEDDING_DIM")
    
    search_limit: int = Field(10, alias="SEARCH_LIMIT")
    search_threshold: float = Field(0.80, alias="SEARCH_THRESHOLD")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def api_key(self) -> str:
        """Возвращает ключ API для текущего LLM провайдера."""
        if self.provider == "minimax":
            return self.minimax_api_key
        return self.openrouter_api_key

    @property
    def base_url(self) -> str:
        """Возвращает базовый URL для текущего LLM провайдера."""
        if self.provider == "minimax":
            return self.minimax_base_url
        return self.openrouter_base_url

    @field_validator("model_name", mode="after")
    @classmethod
    def set_default_model(cls, v: str, info: Any) -> str:
        """Устанавливает модель по умолчанию, если она не задана явно."""
        if v:
            return v
        # info.data содержит уже провалидированные поля до текущего
        provider = info.data.get("provider", "minimax")
        if provider == "minimax":
            return "minimax-m3"
        return "google/gemini-3.5-flash"


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

    local_storage_dir: str = Field("", alias="LOCAL_STORAGE_DIR")
    temp_dir: str = Field("data/temp", alias="TEMP_DIR")
    gdrive_credentials_path: str = Field("data/credentials.json", alias="GDRIVE_CREDENTIALS_PATH")
    gdrive_token_path: str = Field("data/token.json", alias="GDRIVE_TOKEN_PATH")
    gdrive_root_folder_id: str = Field(..., alias="GDRIVE_ROOT_FOLDER_ID")
    gdrive_inbox_folder_id: str = Field("", alias="GDRIVE_INBOX_FOLDER_ID")
    max_file_size_mb: int = Field(50, alias="MAX_FILE_SIZE_MB")
    rate_limit_per_minute: int = Field(10, alias="RATE_LIMIT_PER_MINUTE")
    session_ttl_seconds: int = Field(1800, alias="SESSION_TTL_SECONDS")

    @property
    def local_storage_enabled(self) -> bool:
        """Включено ли локальное хранилище."""
        return bool(self.local_storage_dir)

    @property
    def inbox_enabled(self) -> bool:
        """Включена ли папка inbox в Google Drive."""
        return bool(self.gdrive_inbox_folder_id)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


class Settings:
    """Общий класс конфигурации приложения."""

    def __init__(self) -> None:
        self.bot = BotConfig()  # type: ignore[call-arg]
        self.llm = LLMConfig()  # type: ignore[call-arg]
        self.db = DBConfig()  # type: ignore[call-arg]
        self.storage = StorageConfig()  # type: ignore[call-arg]


settings = Settings()
