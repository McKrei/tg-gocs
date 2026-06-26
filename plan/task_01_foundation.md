# Задача 1 — Фундамент проекта

## Статус: ✅ Выполнена

## Цель
Scaffold всего проекта с нуля: структура, конфигурация, инструменты разработки.

## Что делаем

- [x] Структура директорий (`src/`, `data/`, `tests/`)
- [x] `pyproject.toml` — uv, зависимости, настройки ruff и mypy
- [x] `Makefile` — команды `run`, `test`, `lint`, `format`, `up`
- [x] `src/config.py` — Pydantic Settings (BotConfig, LLMConfig, DBConfig, StorageConfig)
- [x] `.env.example` с описанием всех переменных
- [x] `README.md` с инструкцией запуска
- [x] `.gitignore`


## Критерий готовности

```bash
uv run python -c "from src.config import settings; print(settings)"
make lint  # без ошибок
```

## Заметки для агента

- Использовать `uv` — никакого pip
- Все классы конфигурации в одном файле `src/config.py`
- В `.env.example` описать каждую переменную на русском
- `data/` должна быть в `.gitignore`, но с сохранением папки через `.gitkeep`
