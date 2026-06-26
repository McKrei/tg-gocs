# Задача 2 — База данных и векторный поиск

## Статус: ✅ Выполнена

## Предзависимость
✅ Задача 1 выполнена

## Цель
Настроить SQLite + SQLAlchemy 2.0 async + sqlite-vec для хранения документов и их эмбеддингов.

## Что делаем

- [x] `src/db/models.py` — SQLAlchemy модель таблицы `documents`
- [x] `src/db/engine.py` — асинхронный движок и сессия
- [x] `src/db/repository.py` — CRUD операции над документами
- [x] Интеграция `sqlite-vec` — хранение и поиск по эмбеддингу
- [x] Автомиграции через `metadata.create_all` при старте
- [x] `tests/test_db.py` — тесты: создать, найти, удалить документ


## Модель данных (таблица `documents`)

| Поле | Тип | Описание |
|------|-----|---------|
| id | UUID | Первичный ключ |
| saved_filename | String | Итоговое имя файла |
| local_path | String | Путь на диске |
| gdrive_link | String (nullable) | Ссылка на Google Drive |
| category | String | Категория (напр. "Медицина/Жена") |
| owner | String | Владелец документа |
| summary | Text | Текстовое описание от LLM |
| embedding | BLOB | Векторное представление |
| created_at | DateTime | Время создания |

## Критерий готовности

```bash
make test  # тесты test_db.py зелёные
```
