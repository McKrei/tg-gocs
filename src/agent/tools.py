"""Инструменты, доступные агенту для выполнения операций над файлами и БД."""

import asyncio
import re
import shutil
from pathlib import Path
from typing import Any

from PIL import Image

from src.config import settings
from src.db.engine import async_session
from src.db.repository import DocumentRepository
from src.drive.uploader import upload_file_with_status
from src.llm.embeddings import get_embedding
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def get_directory_tree(path_prefix: str = "") -> str:
    """Возвращает дерево директорий в локальном хранилище документов в виде текста."""
    if not settings.storage.local_storage_enabled:
        return ""

    storage_root = Path(settings.storage.local_storage_dir).resolve()
    safe_prefix = path_prefix.lstrip("/\\").strip()
    base_dir = (storage_root / safe_prefix).resolve()

    if not str(base_dir).startswith(str(storage_root)):
        logger.warning(f"Попытка выхода за пределы хранилища: {path_prefix!r} → {base_dir}")
        base_dir = storage_root

    if not base_dir.exists():
        return ""

    lines: list[str] = []

    def _walk(path: Path, prefix: str = "") -> None:
        dirs = sorted([x for x in path.iterdir() if x.is_dir()])
        for i, d in enumerate(dirs):
            is_last = i == len(dirs) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{d.name}")
            new_prefix = prefix + ("    " if is_last else "│   ")
            _walk(d, new_prefix)

    lines.append(safe_prefix or ".")
    _walk(base_dir)
    return "\n".join(lines)


async def get_existing_structure() -> str:
    """Возвращает описание существующей структуры папок и категорий."""
    from sqlalchemy import select

    from src.db.models import Document
    from src.db.repository import DocumentRepository
    from src.utils.logger import get_logger

    tree = await get_directory_tree()
    categories = []
    owners = []

    try:
        async with async_session() as session:
            repo = DocumentRepository(session)
            stats = await repo.get_stats_by_category()
            categories = [cat for cat, _ in stats]
            stmt = select(Document.owner).distinct()
            result = await session.execute(stmt)
            owners = [str(row[0]) for row in result.all() if row[0]]
    except Exception as e:
        get_logger(__name__).error(f"Ошибка получения структуры из БД: {e}")

    lines = []
    if tree and tree != ".":
        lines.append("Существующая структура папок на диске:")
        lines.append(tree)
        lines.append("")

    if categories:
        lines.append("Используемые категории в базе данных:")
        for cat in categories:
            lines.append(f"- {cat}")
        lines.append("")

    if owners:
        lines.append("Существующие владельцы документов:")
        for owner in owners:
            lines.append(f"- {owner}")
        lines.append("")

    return "\n".join(lines).strip()


def extract_gdrive_file_id(link: str) -> str | None:
    """Извлекает идентификатор файла из ссылки Google Drive."""
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", link)
    return match.group(1) if match else None


async def delete_file_from_drive(file_id: str) -> None:
    """Удаляет файл из Google Drive по его идентификатору."""
    from src.drive.client import delete_drive_file, is_drive_configured

    if not is_drive_configured():
        return
    try:
        await asyncio.to_thread(delete_drive_file, file_id)
    except Exception as e:
        logger.error(f"Ошибка удаления файла {file_id} из Drive: {e}")


def get_unique_filename(category: str, filename: str, existing_names: list[str] | None = None) -> str:
    """Возвращает уникальное имя файла.

    Проверяет по локальному FS (если включён) и по переданному списку existing_names.
    """
    used: set[str] = set(existing_names or [])

    if settings.storage.local_storage_enabled:
        base_dir = Path(settings.storage.local_storage_dir) / category
        if (base_dir / filename).exists():
            used.add(filename)

    if filename not in used:
        return filename

    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 1
    while True:
        new_filename = f"{stem}_{counter}{suffix}"
        in_fs = (
            settings.storage.local_storage_enabled
            and (Path(settings.storage.local_storage_dir) / category / new_filename).exists()
        )
        if new_filename not in used and not in_fs:
            return new_filename
        counter += 1


async def find_similar_document(category: str, filename: str, summary: str) -> dict[str, Any] | None:
    """Ищет похожий документ по пути или векторной близости."""
    from sqlalchemy import select

    from src.db.models import Document
    from src.db.repository import DocumentRepository
    from src.utils.logger import get_logger

    async with async_session() as session:
        repo = DocumentRepository(session)
        stmt = select(Document).where(Document.category == category, Document.saved_filename == filename)
        result = await session.execute(stmt)
        exact_doc = result.scalar_one_or_none()
        if exact_doc:
            return {
                "id": exact_doc.id,
                "saved_filename": exact_doc.saved_filename,
                "category": exact_doc.category,
                "summary": exact_doc.summary,
                "gdrive_link": exact_doc.gdrive_link,
                "local_path": exact_doc.local_path,
                "reason": "exact_path",
                "similarity_percent": 100,
            }

        try:
            emb = await get_embedding(summary)
            similar = await repo.search_documents(emb, limit=1)
            if similar:
                doc, distance = similar[0]
                if distance < 0.15:
                    from src.llm.duplicate_verifier import check_is_duplicate

                    new_doc = {
                        "category": category,
                        "suggested_filename": filename,
                        "summary": summary,
                    }
                    existing_doc = {
                        "category": doc.category,
                        "saved_filename": doc.saved_filename,
                        "summary": doc.summary,
                    }

                    if await check_is_duplicate(new_doc, existing_doc):
                        return {
                            "id": doc.id,
                            "saved_filename": doc.saved_filename,
                            "category": doc.category,
                            "summary": doc.summary,
                            "gdrive_link": doc.gdrive_link,
                            "local_path": doc.local_path,
                            "reason": "semantic",
                            "similarity_percent": round((1.0 - distance) * 100),
                        }
        except Exception as e:
            get_logger(__name__).error(f"Ошибка поиска дубликатов: {e}")

    return None


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
    """Выгружает файл на Google Drive. Локально сохраняет только если включён LOCAL_STORAGE_DIR."""
    temp_path = Path(temp_filepath)
    if not temp_path.exists():
        raise FileNotFoundError(f"Временный файл не найден: {temp_filepath}")

    local_path: str | None = None
    source_for_upload = str(temp_path)

    if settings.storage.local_storage_enabled:
        dest_path = Path(settings.storage.local_storage_dir) / target_path
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(temp_path, dest_path)
        local_path = str(dest_path)
        source_for_upload = str(dest_path)

    upload_result = await upload_file_with_status(source_for_upload, target_path)

    return {
        "local_path": local_path or target_path,
        "gdrive_link": upload_result["link"],
        "gdrive_folder_link": upload_result.get("folder_link"),
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

# Только read-only инструменты — для режима классификации без сохранения (sync, inbox)
CLASSIFY_TOOLS_MAP: dict[str, Any] = {
    "get_directory_tree": get_directory_tree,
    "vector_search": vector_search,
}

CLASSIFY_TOOLS_SCHEMA: Any = [tool for tool in TOOLS_SCHEMA if tool["function"]["name"] in CLASSIFY_TOOLS_MAP]
