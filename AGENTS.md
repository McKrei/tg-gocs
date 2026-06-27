# Правила разработки — Family Docs Agent Bot

## Быстрый старт для агента

Перед любой задачей прочитай:
- **Архитектура и потоки данных:** [`docs/architecture.md`](docs/architecture.md)
- **Конфигурация:** [`src/config.py`](src/config.py) — все параметры, классы `BotConfig`, `LLMConfig`, `DBConfig`, `StorageConfig`
- **Инструменты агента:** [`src/agent/tools.py`](src/agent/tools.py) — `TOOLS_MAP`, `CLASSIFY_TOOLS_MAP`
- **Модель данных:** [`src/db/models.py`](src/db/models.py) — таблица `documents`

Ключевые точки входа:
- Бот: [`src/bot/main.py`](src/bot/main.py)
- FSM-состояния: [`src/bot/states.py`](src/bot/states.py) (`DocumentProcessingStates`, `InboxStates`)
- Хэндлеры: `src/bot/handlers/` (`commands.py`, `files.py`, `callbacks.py`, `text.py`, `search.py`, `inbox.py`)
- Оркестратор агента: [`src/agent/agent.py`](src/agent/agent.py)
- Репозиторий БД: [`src/db/repository.py`](src/db/repository.py)
- Синхронизация Drive: [`src/services/sync.py`](src/services/sync.py)

---

## 1. Язык и документирование

- Все комментарии, docstrings и логи — **строго на русском языке**.
- Код (переменные, функции, классы) — на английском.
- Docstrings — только одна строка для ключевых функций; очевидную логику не комментировать.
- Комментарии внутри функций — только для неочевидной/сложной логики.

## 2. Стек и инструменты

- **Python 3.13+**, менеджер пакетов — `uv`.
  - Установка: `uv add <package>` (без жёсткого пиннинга версий).
  - Запуск: `uv run <command>`.
- **Telegram:** `aiogram 3.x`, FSM обязателен для всех сессионных флоу.
- **Конфиг:** `pydantic-settings`. Все параметры — в `src/config.py`, никаких «магических» констант в коде.
- **БД:** SQLite + SQLAlchemy 2.0 async (`aiosqlite`) + `sqlite-vec` для векторного поиска.
- **LLM:** `google/gemini-3.5-flash` via OpenRouter. **Embeddings:** `google/gemini-embedding-2` via OpenRouter.
- **Google Drive:** `google-api-python-client`, OAuth 2.0 (основной) или Service Account.
- **Логирование:** только через `from src.utils.logger import get_logger; logger = get_logger(__name__)`. Прямой `logging.getLogger` — запрещён.
- **Git:** автоматические коммиты запрещены без явного разрешения пользователя.

## 3. Архитектура

- Весь код — в `src/`. БД, файлы, credentials — в `data/`.
- Новые параметры конфигурации — только в `src/config.py` с переопределением через `.env`.
- Retry для внешних вызовов — через `@with_retry` из `src/utils/retry.py`.
- Google Drive upload — всегда через `drive/uploader.py` с graceful degradation (Drive недоступен → только локально).

**Makefile-команды:**
- `make run` — dev-запуск бота.
- `make test` — юнит-тесты (без внешних API, `-m "not integration"`).
- `make test-integration` — тесты с реальными OpenRouter / Google Drive.
- `make test-all` — все тесты.
- `make lint` — `ruff check` + `mypy`.
- `make format` — `ruff format`.
- `make up` / `make down` — Docker Compose.

**Критическое правило:** не запускай `make test-integration` без прямой необходимости — экономь API-квоты.

## 4. Качество кода

- **Линтинг:** `ruff` (настройки в `pyproject.toml`).
- **Типизация:** `mypy`, type-hints обязательны для всех публичных сигнатур. Никогда не использовать `any` — только `Any` из `typing`.
- Для `pydantic-settings`-инициализаторов без аргументов добавлять `# type: ignore[call-arg]`.
- Весь код — async, без блокирующих вызовов в хэндлерах.
- Перед завершением задачи — **обязательно** `make lint` и `make test`.
