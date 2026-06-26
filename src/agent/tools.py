"""Инструменты, доступные агенту для выполнения операций над файлами и БД."""

import shutil
from pathlib import Path
from typing import Any

from PIL import Image

from src.config import settings
from src.db.engine import async_session
from src.db.repository import DocumentRepository
from src.drive.uploader import upload_file_with_status
from src.llm.embeddings import get_embedding


async def get_directory_tree(path_prefix: str = "") -> str:
    """Возвращает дерево директорий в локальном хранилище документов в виде текста."""
    base_dir = Path(settings.storage.local_storage_dir) / path_prefix
    if not base_dir.exists():
        return f"Директория {path_prefix} не существует."

    lines = []

    def _walk(path: Path, prefix: str = "") -> None:
        dirs = sorted([x for x in path.iterdir() if x.is_dir()])
        for i, d in enumerate(dirs):
            is_last = i == len(dirs) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{d.name}")
            new_prefix = prefix + ("    " if is_last else "│   ")
            _walk(d, new_prefix)

    lines.append(path_prefix or ".")
    _walk(base_dir)
    return "\n".join(lines)


async def create_directory(path: str) -> bool:
    """Создает директорию локально. Возвращает True в случае успеха."""
    try:
        full_path = Path(settings.storage.local_storage_dir) / path
        full_path.mkdir(parents=True, exist_ok=True)
        return True
    except Exception:
        return False


async def convert_to_pdf(temp_file_ids: list[str], output_filename: str) -> str:
    """Конвертирует список временных файлов в один PDF-файл. Возвращает путь к нему."""
    temp_dir = Path(settings.storage.temp_dir)
    images = []

    for file_id in temp_file_ids:
        file_path = temp_dir / file_id
        if file_path.exists():
            img = Image.open(file_path).convert("RGB")
            images.append(img)

    if not images:
        raise ValueError("Нет доступных изображений для конвертации.")

    temp_dir.mkdir(parents=True, exist_ok=True)
    output_path = temp_dir / output_filename
    images[0].save(output_path, save_all=True, append_images=images[1:])
    return str(output_path)


async def save_to_local_and_drive(temp_filepath: str, target_path: str) -> dict[str, str | None]:
    """Сохраняет временный файл локально и выгружает его на Google Drive."""
    temp_path = Path(temp_filepath)
    if not temp_path.exists():
        raise FileNotFoundError(f"Временный файл не найден: {temp_filepath}")

    dest_path = Path(settings.storage.local_storage_dir) / target_path
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    shutil.copy2(temp_path, dest_path)

    upload_result = await upload_file_with_status(str(dest_path), target_path)

    return {
        "local_path": str(dest_path),
        "gdrive_link": upload_result["link"],
        "gdrive_error": upload_result["error"],
    }


async def vector_search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Выполняет векторный поиск KNN по базе данных документов на основе эмбеддинга запроса."""
    query_emb = await get_embedding(query)

    async with async_session() as session:
        repo = DocumentRepository(session)
        results = await repo.search_documents(query_emb, limit=limit)

        output = []
        for doc, distance in results:
            output.append(
                {
                    "id": str(doc.id),
                    "saved_filename": doc.saved_filename,
                    "local_path": doc.local_path,
                    "gdrive_link": doc.gdrive_link,
                    "category": doc.category,
                    "owner": doc.owner,
                    "summary": doc.summary,
                    "distance": distance,
                }
            )
        return output


# Карта доступных функций-инструментов для вызова
TOOLS_MAP: dict[str, Any] = {
    "get_directory_tree": get_directory_tree,
    "create_directory": create_directory,
    "convert_to_pdf": convert_to_pdf,
    "save_to_local_and_drive": save_to_local_and_drive,
    "vector_search": vector_search,
}

# Описание инструментов для передачи в OpenAI/OpenRouter API
TOOLS_SCHEMA: Any = [
    {
        "type": "function",
        "function": {
            "name": "get_directory_tree",
            "description": "Возвращает дерево папок документов для определения существующих категорий.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path_prefix": {
                        "type": "string",
                        "description": "Префикс пути для обхода дерева директорий (по умолчанию корень).",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_directory",
            "description": "Создает новую папку в хранилище документов (локально).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Относительный путь к новой директории (например: 'Медицина/Жена').",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "convert_to_pdf",
            "description": "Склеивает и конвертирует список временных файлов изображений в один PDF-документ.",
            "parameters": {
                "type": "object",
                "properties": {
                    "temp_file_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Список имен файлов во временной папке (например: ['page1.jpg', 'page2.png']).",
                    },
                    "output_filename": {
                        "type": "string",
                        "description": "Имя итогового PDF-файла (например: 'document.pdf').",
                    },
                },
                "required": ["temp_file_ids", "output_filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_to_local_and_drive",
            "description": "Сохраняет временный файл в конечную директорию документов (локально и в Google Drive).",
            "parameters": {
                "type": "object",
                "properties": {
                    "temp_filepath": {
                        "type": "string",
                        "description": "Абсолютный или относительный путь к временному файлу (источник).",
                    },
                    "target_path": {
                        "type": "string",
                        "description": "Относительный путь для сохранения, включая категорию и имя файла (назначение).",
                    },
                },
                "required": ["temp_filepath", "target_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vector_search",
            "description": "Выполняет семантический поиск по ранее сохраненным документам по текстовому запросу.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Текст поискового запроса (например: 'паспорт жены').",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Максимальное количество возвращаемых результатов.",
                    },
                },
                "required": ["query"],
            },
        },
    },
]
