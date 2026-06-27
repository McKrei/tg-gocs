# Архитектура Family Docs Agent Bot

## Обзор

Telegram-бот (агент) для семейного хранения документов. Обрабатывает входящие файлы через мультимодальную LLM (Gemini Flash via OpenRouter), классифицирует их, сохраняет на диск и/или в Google Drive, индексирует в SQLite для последующего семантического поиска.

---

## Структура модулей (`src/`)

```
src/
├── core/
│   ├── config.py              # Pydantic Settings: BotConfig, LLMConfig, DBConfig, StorageConfig
│   ├── db/
│   │   ├── engine.py          # async_engine, async_session (aiosqlite + sqlite-vec)
│   │   └── base.py            # Base для SQLAlchemy-моделей
│   ├── agent/
│   │   └── registry.py        # ToolRegistry & @register_tool декоратор
│   ├── llm/
│   │   ├── client.py          # AsyncOpenAI клиент для OpenRouter (timeout=30s)
│   │   └── embeddings.py      # get_embedding() через /embeddings endpoint
│   ├── drive/
│   │   ├── client.py          # get_drive_service(), OAuth2/Service Account; list/download/delete
│   │   ├── uploader.py        # upload_file_with_status() с retry, кэшем папок и graceful degradation
│   │   └── cache.py           # FolderCache (in-memory кэш ID папок Drive с TTL 24ч)
│   └── utils/
│       ├── logger.py          # get_logger() — структурированное логирование
│       ├── retry.py           # @with_retry(attempts, backoff) декоратор для async функций
│       └── image.py           # compress_image() — сжатие изображений перед анализом
├── modules/
│   └── documents/
│       ├── __init__.py        # register_module() -> Router + фоновые задачи
│       ├── handlers/
│       │   ├── commands.py    # /start /add /search /cancel /reset /help /list /stats /sync /inbox
│       │   ├── files.py       # Приём фото/PDF: rate limiting, size check, накопление батча, сжатие фото
│       │   ├── callbacks.py   # Обработка inline-кнопок: сохранить / отменить / удалить
│       │   ├── text.py        # Текстовые поправки к черновику (в состоянии confirming)
│       │   ├── search.py      # Семантический поиск + LLM re-ranking (в состоянии waiting_query)
│       │   └── inbox.py       # Inbox-флоу: обход очереди файлов из Drive, confirm/skip/replace
│       ├── models.py          # SQLAlchemy-модели: Document, PendingUpload
│       ├── repository.py      # CRUD + KNN-поиск + get_recent + get_stats_by_category
│       ├── tools.py           # Инструменты агента, декорированные @register_tool
│       ├── states.py          # FSM: DocumentProcessingStates, InboxStates
│       ├── keyboards.py       # Inline-клавиатуры (confirm, cancel, inbox, duplicate)
│       ├── agent.py           # Оркестратор: Vision → tool calls → JSON-метаданные
│       └── services/
│           ├── sync.py        # sync_drive_to_db(), get_inbox_files(), process_inbox_file()
│           ├── retry_uploads.py # Фоновая служба повторной загрузки локальных файлов в Drive
│           ├── refiner.py     # refine_draft() — корректировка черновика по фидбеку
│           └── duplicate_verifier.py # check_is_duplicate() — LLM-проверка на дубль
└── bot/
    ├── main.py                # Точка входа: Bot + Dispatcher + авторегистрация модулей
    └── middleware/
        └── auth.py            # Middleware авторизации по Telegram User ID
```

---

## Flow 1: Сохранение документа через бота

```
Пользователь → /add
  → files.py (FSM: waiting_file):
      проверить rate limit, размер файла
      скачать во data/temp/, накопить батч

Пользователь → [ещё фото / сразу готово]
  → agent.py: encode_file() → Gemini Flash (vision + tool_calls)
      tools.get_existing_structure()  ← DB + локальные папки
    ← draft JSON: {category, owner, suggested_filename, summary}
      normalize_draft_metadata()  ← нормализация даты, имени, пути
      find_similar_document()     ← поиск дублей по точному пути или KNN+LLM
  → Бот: черновик + inline-кнопки [✅ Сохранить] / [✏️ Редактировать] / [❌ Отменить]

Пользователь → [текст-поправка] (FSM: confirming)
  → text.py → refiner.refine_draft(old_draft, feedback) → обновлённый черновик

Пользователь → [✅ Сохранить] (FSM: confirming)
  → callbacks.py:
      если > 1 файла → tools.convert_to_pdf()   (PIL + pypdf)
      tools.save_to_local_and_drive()
          ↳ shutil.copy2 в LOCAL_STORAGE_DIR (если включён)
          ↳ drive/uploader.py → Google Drive с retry x3
      get_embedding(summary) → sqlite-vec INSERT
      repository.add_document()
  ← ✅ Сохранено! + ссылка на файл/папку в Google Drive
  finally: cleanup temp/ + state.clear()
```

## Flow 2: Семантический поиск

```
Пользователь → /search [запрос] или текст в waiting_query
  → search.py:
      tools.vector_search(query, limit=5)
          ↳ get_embedding(query) → KNN в sqlite-vec
      rerank_documents(query, top5) → Gemini Flash re-ranking
    ← {best_match_id, explanation}
  → Если найдено: отправить FSInputFile + caption + Drive link
  → Если нет: fallback-сообщение
```

## Flow 3: Inbox-обработка

```
Пользователь → /inbox
  → services/sync.get_inbox_files()  ← список файлов из GDRIVE_INBOX_FOLDER_ID
  → для каждого файла (FSM: InboxStates.processing/confirming):
      скачать во temp/
      agent.classify_document(classify_only=True)   ← только read-only инструменты
      find_similar_document()  ← проверка дублей
      Бот: черновик + inline-кнопки [✅ Сохранить] / [🔄 Заменить] / [⏭ Пропустить]
      При сохранении: upload_file_with_status() + add_document()
      Удалить файл из inbox Drive-папки
```

## Flow 4: Синхронизация Drive → БД (`/sync`)

```
Пользователь → /sync
  → services/sync.sync_drive_to_db():
      list_files_recursive(GDRIVE_ROOT_FOLDER_ID)  ← все файлы Drive
      сравнить с gdrive_link в БД
      новые файлы: скачать, classify_document(), get_embedding(), add_document()
      удалённые из Drive: удалить из БД
  ← отчёт: добавлено / пропущено / удалено / ошибки
```

---

## Инструменты агента (tools.py)

| Инструмент | Режим | Описание |
|---|---|---|
| `get_directory_tree(path_prefix)` | classify + full | Дерево локальных папок в виде текста |
| `create_directory(path)` | full | Создать папку локально |
| `convert_to_pdf(temp_file_ids, output_filename)` | full | Склеить фото/PDF в один PDF (PIL + pypdf) |
| `save_to_local_and_drive(temp_filepath, target_path)` | full | Сохранить локально + в Drive |
| `vector_search(query, limit)` | classify + full | KNN-поиск по sqlite-vec |

Вспомогательные функции (не exposed в схему агента):
- `get_existing_structure()` — плоский список папок + категорий из БД (передаётся в system prompt)
- `find_similar_document()` — точный поиск по пути + KNN + LLM-верификация дубля
- `get_unique_filename()` — разрешение коллизий имён файлов

---

## Надёжность

| Компонент | Механизм |
|---|---|
| LLM-вызовы | `@with_retry(attempts=3, backoff×2)`, timeout=30s |
| Google Drive upload | `@with_retry(attempts=3)`, graceful degradation: сбой → локальная копия + запись в `pending_uploads` |
| Ретраи выгрузки | Фоновая задача проверяет `pending_uploads` каждые 15 минут, повторно загружает файлы (до 5 попыток) |
| Кэширование Drive | `FolderCache` хранит ID созданных папок 24 часа для минимизации сетевых API-запросов |
| Проверка размера файла | `file_size > MAX_FILE_SIZE_MB * 1024²` до скачивания |
| Rate limiting | In-memory: ≤ `RATE_LIMIT_PER_MINUTE` файлов/60с на user_id |
| Сжатие фото | Асинхронное сжатие Pillow до 1600x1200, quality=80% сразу после скачивания |
| FSM cleanup | `try/finally` в confirm_save → `state.clear()` всегда |
| Temp-файлы | `try/finally` → cleanup даже при ошибке |
| Session TTL | `SESSION_TTL_SECONDS` (по умолчанию 1800с) — сброс зависшей сессии |

---

## Модель данных

**Таблица `documents`** (SQLAlchemy + aiosqlite):

| Поле | Тип | Описание |
|---|---|---|
| `id` | UUID | Первичный ключ |
| `saved_filename` | String(255) | Имя файла в хранилище |
| `local_path` | String(512) | Путь локальной копии |
| `gdrive_link` | String(512)? | Ссылка на файл в Drive |
| `category` | String(100) | Путь к папке (напр. `Медицина/Жена`) |
| `owner` | String(100) | Владелец (имя члена семьи или `Общее`) |
| `summary` | Text | Подробное описание и реквизиты от LLM |
| `embedding` | LargeBinary | BLOB float32 (768-мерный вектор) |
| `created_at` | DateTime | Дата добавления в UTC |

**Таблица `pending_uploads`** (SQLAlchemy + aiosqlite):

| Поле | Тип | Описание |
|---|---|---|
| `id` | UUID | Первичный ключ задачи |
| `local_path` | String(512) | Путь к локальной копии файла |
| `target_path` | String(512) | Целевой относительный путь сохранения в Drive |
| `document_id` | UUID? | Ссылка на документ в `documents` (может быть Null) |
| `created_at` | DateTime | Дата создания задачи |
| `attempts` | Integer | Количество попыток выгрузки |
| `last_error` | Text? | Текст последней ошибки |
| `status` | String(50) | Статус (`pending`, `completed`, `failed`) |

**Виртуальная таблица `vec_documents`** (sqlite-vec):
- KNN-поиск по косинусному расстоянию (`distance_metric=cosine`)
- Создаётся автоматически при инициализации `engine.py`

---

## Конфигурация (`.env`)

| Переменная | По умолчанию | Описание |
|---|---|---|
| `BOT_TOKEN` | — | Токен Telegram-бота |
| `ALLOWED_CHAT_IDS` | `[]` | Список разрешённых chat_id через запятую |
| `OPENROUTER_API_KEY` | — | API-ключ OpenRouter |
| `LLM_MODEL` | `google/gemini-3.5-flash` | Модель для Vision/Chat/Agent |
| `EMBEDDING_MODEL` | `google/gemini-embedding-2` | Модель для эмбеддингов |
| `EMBEDDING_DIM` | `768` | Размерность вектора |
| `DB_PATH` | `data/sqlite.db` | Путь к файлу SQLite |
| `LOCAL_STORAGE_DIR` | `""` | Папка для локальных копий (пусто = только Drive) |
| `TEMP_DIR` | `data/temp` | Папка временных файлов |
| `GDRIVE_CREDENTIALS_PATH` | `data/credentials.json` | OAuth client secrets или SA key |
| `GDRIVE_TOKEN_PATH` | `data/token.json` | OAuth токен (не нужен для SA) |
| `GDRIVE_ROOT_FOLDER_ID` | — | ID корневой папки в Drive |
| `GDRIVE_INBOX_FOLDER_ID` | `""` | ID inbox-папки Drive (пусто = /inbox отключён) |
| `MAX_FILE_SIZE_MB` | `50` | Максимальный размер файла |
| `RATE_LIMIT_PER_MINUTE` | `10` | Лимит файлов в минуту на пользователя |
| `SESSION_TTL_SECONDS` | `1800` | Таймаут сессии (30 мин) |

---

## Хранение данных

```
data/
├── sqlite.db          # БД: таблица documents + vec_documents (sqlite-vec)
├── documents/         # Локальные копии (Категория/Владелец/файл.pdf) [если LOCAL_STORAGE_DIR задан]
├── temp/              # Временные файлы (удаляются после сохранения)
├── credentials.json   # OAuth client secrets или Service Account key
└── token.json         # OAuth-токен (создаётся auth_gdrive.py, не нужен для SA)
```

---

## Развёртывание

```bash
make run              # dev-режим: uv run python -m src.bot.main
make test             # юнит-тесты (без внешних API, -m "not integration")
make test-integration # интеграционные тесты с реальными API
make lint             # ruff check + mypy
make format           # ruff format
make up               # docker-compose up --build -d
make down             # docker-compose down
make clean            # очистка кэшей (__pycache__, .ruff_cache и т.д.)
```

---

## Динамический реестр инструментов (Tool Registry)

Для расширения возможностей ИИ-агента без изменения ядра оркестратора используется динамический реестр инструментов:
- **Декоратор `@register_tool`** (в `src/core/agent/registry.py`) регистрирует функции в глобальном реестре ИИ-агента и автоматически генерирует для них JSON-схему на основе аннотаций типов параметров и docstring.
- При запуске бота метод `discover_tools()` сканирует директорию `src/modules/*/tools.py`, автоматически импортирует файлы и регистрирует содержащиеся в них функции как инструменты.
- Поддерживается разделение инструментов по режимам доступа (параметр `mode`):
  - `classify` — только read-only инструменты (доступные при предварительном разборе и обходе inbox).
  - `full` — все инструменты, включая модифицирующие файловую систему и базу данных.

