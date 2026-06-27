"""Инструменты, доступные агенту для выполнения операций над файлами и БД."""

import asyncio
import re
import shutil
from pathlib import Path
from typing import Any

from src.core.agent.registry import register_tool
from src.core.config import settings
from src.core.db.engine import async_session
from src.core.drive.uploader import upload_file_with_status
from src.core.llm.embeddings import get_embedding
from src.core.utils.logger import get_logger
from src.modules.documents.repository import DocumentRepository

logger = get_logger(__name__)


@register_tool(mode="classify")
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


def get_flat_directory_list() -> list[str]:
    """Возвращает список относительных путей всех папок в локальном хранилище."""
    if not settings.storage.local_storage_enabled:
        return []

    storage_root = Path(settings.storage.local_storage_dir).resolve()
    if not storage_root.exists():
        return []

    paths: list[str] = []
    for p in storage_root.rglob("*"):
        if p.is_dir():
            try:
                rel = p.relative_to(storage_root)
                paths.append(str(rel))
            except ValueError:
                continue
    return sorted(paths)


async def get_existing_structure() -> str:
    """Возвращает описание существующей структуры папок и категорий в виде плоского списка относительных путей."""
    from sqlalchemy import select

    from src.core.utils.logger import get_logger
    from src.modules.documents.models import Document
    from src.modules.documents.repository import DocumentRepository

    flat_dirs = get_flat_directory_list()
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
    all_paths = sorted(set(flat_dirs + categories))
    if all_paths:
        lines.append("Существующие папки и категории документов (используй их в точности как образец пути):")
        for path in all_paths:
            lines.append(f"- {path}")
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
    from src.core.drive.client import delete_drive_file, is_drive_configured

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

    from src.core.utils.logger import get_logger
    from src.modules.documents.models import Document
    from src.modules.documents.repository import DocumentRepository

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
                if distance < 0.10:
                    # Почти 100% совпадение векторов — считаем дубликатом автоматически
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
                if distance < 0.20:
                    # Возможный дубликат — помечаем как дубликат для выбора пользователю
                    # без вызова ресурсоемкой LLM-верификации
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


@register_tool(mode="full")
async def create_directory(path: str) -> bool:
    """Создает директорию локально. Возвращает True в случае успеха."""
    try:
        full_path = Path(settings.storage.local_storage_dir) / path
        full_path.mkdir(parents=True, exist_ok=True)
        return True
    except Exception:
        return False


async def merge_files_to_pdf(file_paths: list[str], output_path: str) -> None:
    """Объединяет список файлов (изображений и/или PDF) в один PDF-файл."""
    import asyncio

    def _merge() -> None:
        import pypdf
        from PIL import Image

        writer = pypdf.PdfWriter()
        temp_pdfs: list[Path] = []

        try:
            for path_str in file_paths:
                path = Path(path_str)
                if not path.exists():
                    continue

                if path.suffix.lower() == ".pdf":
                    writer.append(str(path))
                elif path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                    img = Image.open(path).convert("RGB")
                    temp_pdf = path.with_suffix(path.suffix + ".pdf")
                    img.save(temp_pdf, "PDF")
                    temp_pdfs.append(temp_pdf)
                    writer.append(str(temp_pdf))
                else:
                    logger.warning(f"Пропущен неподдерживаемый тип файла при склеивании: {path.name}")

            with Path(output_path).open("wb") as f:
                writer.write(f)
        finally:
            writer.close()
            for tp in temp_pdfs:
                try:
                    tp.unlink(missing_ok=True)
                except Exception as e:
                    logger.error(f"Не удалось удалить временный PDF {tp}: {e}")

    await asyncio.to_thread(_merge)


@register_tool(mode="full")
async def convert_to_pdf(temp_file_ids: list[str], output_filename: str) -> str:
    """Конвертирует список временных файлов в один PDF-файл. Возвращает путь к нему."""
    temp_dir = Path(settings.storage.temp_dir)
    full_paths = [str(temp_dir / fid) for fid in temp_file_ids]
    output_path = temp_dir / output_filename
    await merge_files_to_pdf(full_paths, str(output_path))
    return str(output_path)


@register_tool(mode="full")
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

    # Если выгрузка завершилась с ошибкой, а локальной копии еще нет — создаем принудительно
    if upload_result.get("error") and not local_path:
        default_dir = Path("data/documents")
        dest_path = default_dir / target_path
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(temp_path, dest_path)
        local_path = str(dest_path)

    return {
        "local_path": local_path or target_path,
        "gdrive_link": upload_result["link"],
        "gdrive_folder_link": upload_result.get("folder_link"),
        "gdrive_error": upload_result["error"],
    }


@register_tool(mode="classify")
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

