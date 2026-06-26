# Архитектура Family Docs Agent Bot

## Обзор

Telegram-бот (агент) для семейного хранения документов. Обрабатывает входящие файлы через multimodal LLM, сохраняет на диск + Google Drive, индексирует в SQLite для семантического поиска.

## Структура компонентов

```
src/
├── config.py          # Pydantic Settings (BotConfig, LLMConfig, DBConfig, StorageConfig)
├── bot/
│   ├── main.py        # Точка входа: Bot + Dispatcher + регистрация роутеров
│   ├── states.py      # FSM состояния (confirming)
│   ├── keyboards.py   # Inline-кнопки
│   ├── middleware/
│   │   └── auth.py    # Whitelist по user_id из .env
│   └── handlers/
│       ├── commands.py  # /start /cancel /reset /help /list /stats
│       ├── files.py     # Приём фото/PDF, rate limiting, file size check
│       ├── callbacks.py # Inline-кнопки: сохранить/отменить
│       ├── text.py      # Текстовые поправки к черновику (в состоянии confirming)
│       └── search.py    # Семантический поиск (в состоянии idle)
├── agent/
│   ├── agent.py       # Оркестратор: Vision → tool calls → JSON метаданные
│   └── tools.py       # 5 инструментов агента (get_dir_tree, create_dir, convert_pdf, save, search)
├── llm/
│   ├── client.py      # AsyncOpenAI клиент для OpenRouter (timeout=30s)
│   ├── embeddings.py  # get_embedding() через /embeddings endpoint
│   └── refiner.py     # refine_draft() — корректировка черновика по фидбеку пользователя
├── db/
│   ├── models.py      # SQLAlchemy модель таблицы documents
│   ├── engine.py      # async_engine, async_session (aiosqlite)
│   └── repository.py  # CRUD + KNN-поиск через sqlite-vec + get_recent + get_stats
├── drive/
│   ├── client.py      # get_drive_service() через Service Account
│   └── uploader.py    # upload_file() с retry и graceful degradation
└── utils/
    ├── logger.py      # get_logger() — структурированное логирование
    └── retry.py       # @with_retry(attempts, backoff) декоратор для async функций
```

## Flow 1: Сохранение документа

```
Пользователь -> [photo/pdf]
  -> files.py: скачать во temp/, проверить размер/rate limit
  -> agent.py: encode_image() -> Gemini Flash (vision + tool_calls)
     -> tools.get_directory_tree() при необходимости
  <- draft JSON: {category, owner, suggested_filename, summary}
  -> Бот: сообщение с черновиком + inline-кнопки

Пользователь -> [текст-поправка]
  -> text.py: refiner.refine_draft(old_draft, feedback)
  <- Обновлённый черновик

Пользователь -> [✅ Сохранить]
  -> callbacks.py:
     -> tools.convert_to_pdf() если > 1 файла
     -> tools.save_to_local_and_drive() -> drive/uploader.py (retry x3)
     -> embeddings.get_embedding(summary) -> sqlite-vec INSERT
     -> db/repository.add_document()
  <- ✅ Сохранено! + Google Drive ссылка
  finally: cleanup temp files + state.clear()
```

## Flow 2: Семантический поиск

```
Пользователь -> [текст в idle]
  -> search.py:
     -> tools.vector_search(query, limit=5)
        -> embeddings.get_embedding(query) -> KNN в sqlite-vec
     -> rerank_documents(query, top5) -> Gemini Flash re-ranking
     <- {best_match_id, explanation}
  -> Если найдено: answer_document(FSInputFile) + caption + Drive link
  -> Если нет: fallback-сообщение
```

## Надёжность

| Компонент | Механизм защиты |
|-----------|-----------------|
| LLM вызовы | `@with_retry(attempts=3, backoff=exponential)`, timeout=30s |
| Google Drive | `@with_retry(attempts=3)`, graceful degradation (только локально) |
| Файл слишком большой | Проверка `file_size > max_file_size_mb * 1024²` перед скачиванием |
| Спам | In-memory rate limiting: ≤ N файлов/60 сек на user_id |
| FSM сброс | `try/finally` в confirm_save — state.clear() всегда |
| Temp-файлы | `try/finally` — cleanup даже при ошибке |

## Хранение данных

```
data/
├── sqlite.db          # Таблица documents + виртуальная таблица vec_documents (sqlite-vec KNN)
├── documents/         # Локальная копия файлов (структура: Категория/Владелец/файл.pdf)
├── temp/              # Временные файлы (удаляются после сохранения)
└── credentials.json   # Google Service Account (в .gitignore)
```

## Конфигурация (.env)

| Переменная | Описание |
|------------|----------|
| `BOT_TOKEN` | Токен Telegram-бота |
| `ALLOWED_USER_IDS` | Список разрешённых user_id через запятую |
| `OPENROUTER_API_KEY` | API-ключ OpenRouter |
| `LLM_MODEL` | Модель LLM (по умолчанию: `google/gemini-3.5-flash`) |
| `EMBEDDING_MODEL` | Модель эмбеддингов (по умолчанию: `google/gemini-embedding-2`) |
| `GDRIVE_ROOT_FOLDER_ID` | ID корневой папки в Google Drive |
| `MAX_FILE_SIZE_MB` | Максимальный размер файла (по умолчанию: 50) |
| `RATE_LIMIT_PER_MINUTE` | Лимит файлов в минуту на пользователя (по умолчанию: 10) |

## Развёртывание

```bash
make run     # dev-режим
make up      # Docker Compose (python:3.13-slim, volumes: ./data:/app/data)
make test    # юнит-тесты (без внешних API)
make lint    # ruff + mypy
```
