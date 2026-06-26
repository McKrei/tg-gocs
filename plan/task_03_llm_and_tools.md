# Задача 3 — LLM клиент и инструменты агента

## Статус: ⬜ Не начата

## Предзависимость
✅ Задача 2 выполнена

## Цель
Создать ядро интеллекта: клиент OpenRouter, генерацию эмбеддингов и все 5 инструментов агента.

## Что делаем

- [ ] `src/llm/client.py` — async клиент OpenRouter для Gemini Flash (vision + chat + tools)
- [ ] `src/llm/embeddings.py` — генерация эмбеддингов через gemini-embedding-2
- [ ] `src/agent/tools.py` — 5 инструментов агента:
  - `get_directory_tree(path_prefix)` — структура папок
  - `create_directory(path)` — создать папку
  - `convert_to_pdf(temp_file_ids, output_filename)` — склеить фото/файлы в PDF
  - `save_to_local_and_drive(temp_filepath, target_path)` — сохранить (Drive = заглушка)
  - `vector_search(query, limit)` — поиск по SQLite
- [ ] `src/agent/agent.py` — оркестрация: принять файл → вызвать модель → вернуть JSON с метаданными
- [ ] `tests/test_agent.py` — моки LLM, проверка вызовов инструментов

## Критерий готовности

```bash
# Агент классифицирует документ без Telegram
uv run python -c "
import asyncio
from src.agent.agent import classify_document
result = asyncio.run(classify_document('tests/fixtures/passport.jpg'))
print(result)
"
make test  # test_agent.py зелёные
```

## Заметки для агента

- `convert_to_pdf` использует `Pillow` или `img2pdf`
- Инструменты описываются через JSON Schema для function calling
- `save_to_local_and_drive` на этом этапе — только локальное сохранение, Drive = `None`
