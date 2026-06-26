"""Сервис синхронизации Google Drive с базой данных и обработки inbox."""

import asyncio
import contextlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from src.agent.agent import classify_document, normalize_draft_metadata
from src.config import settings
from src.db.engine import async_session
from src.db.repository import DocumentRepository
from src.drive.client import (
    delete_drive_file,
    download_file,
    is_drive_configured,
    list_files_in_folder,
    list_files_recursive,
)
from src.drive.uploader import upload_file_with_status
from src.llm.embeddings import get_embedding
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SyncResult:
    """Результат синхронизации Drive → БД."""

    added: int = 0
    skipped: int = 0
    errors: int = 0
    error_details: list[str] = field(default_factory=list)


@dataclass
class InboxFile:
    """Файл из inbox-папки Drive."""

    drive_id: str
    name: str
    mime_type: str
    web_view_link: str


def _mime_to_ext(mime_type: str) -> str:
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "application/pdf": ".pdf",
    }
    return mapping.get(mime_type, ".bin")


async def _download_to_temp(drive_id: str, name: str, mime_type: str) -> Path:
    """Скачивает файл из Drive во временную папку, возвращает путь."""
    temp_dir = Path(settings.storage.temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    ext = _mime_to_ext(mime_type)
    dest = temp_dir / f"dl_{drive_id[:8]}{ext}"
    await asyncio.to_thread(download_file, drive_id, dest)
    logger.info(f"Скачан файл из Drive: {name} → {dest.name}")
    return dest


async def _index_file(temp_path: Path, drive_link: str, original_name: str) -> bool:
    """Классифицирует файл и добавляет его в БД. Возвращает True при успехе."""
    try:
        draft = await classify_document(str(temp_path), classify_only=True)
        draft = normalize_draft_metadata(draft, temp_path.suffix)
        embedding = await get_embedding(draft["summary"])

        async with async_session() as session:
            repo = DocumentRepository(session)
            await repo.add_document(
                saved_filename=draft["suggested_filename"],
                local_path=draft["category"] + "/" + draft["suggested_filename"],
                category=draft["category"],
                owner=draft.get("owner", "Неизвестно"),
                summary=draft["summary"],
                embedding=embedding,
                gdrive_link=drive_link,
            )
            await session.commit()

        logger.info(f"Проиндексирован файл: {original_name} → {draft['category']}/{draft['suggested_filename']}")
        return True
    except Exception as e:
        logger.error(f"Ошибка индексации файла {original_name}: {e}")
        return False


async def sync_drive_to_db() -> SyncResult:
    """Рекурсивно обходит GDRIVE_ROOT_FOLDER_ID и добавляет незаиндексированные файлы в БД."""
    result = SyncResult()

    if not is_drive_configured():
        logger.warning("Google Drive не настроен — синхронизация невозможна.")
        return result

    logger.info("Начало синхронизации Drive → БД...")

    drive_files = await asyncio.to_thread(list_files_recursive, settings.storage.gdrive_root_folder_id)
    logger.info(f"Найдено файлов в Drive: {len(drive_files)}")

    async with async_session() as session:
        repo = DocumentRepository(session)
        indexed_links = await repo.get_all_gdrive_links()

    logger.info(f"Уже проиндексировано: {len(indexed_links)} файлов")

    for file_info in drive_files:
        web_link = file_info["webViewLink"]
        name = file_info["name"]

        if web_link in indexed_links:
            result.skipped += 1
            continue

        temp_path: Path | None = None
        try:
            temp_path = await _download_to_temp(file_info["id"], name, file_info["mimeType"])
            ok = await _index_file(temp_path, web_link, name)
            if ok:
                result.added += 1
            else:
                result.errors += 1
                result.error_details.append(name)
        except Exception as e:
            logger.error(f"Ошибка обработки файла {name}: {e}")
            result.errors += 1
            result.error_details.append(f"{name}: {e}")
        finally:
            if temp_path and temp_path.exists():
                with contextlib.suppress(Exception):
                    temp_path.unlink()

    logger.info(
        f"Синхронизация завершена: добавлено={result.added}, пропущено={result.skipped}, ошибок={result.errors}"
    )
    return result


async def get_inbox_files() -> list[InboxFile]:
    """Возвращает необработанные файлы из inbox-папки (которых нет в БД)."""
    if not settings.storage.inbox_enabled:
        return []

    if not is_drive_configured():
        return []

    drive_files = await asyncio.to_thread(list_files_in_folder, settings.storage.gdrive_inbox_folder_id)

    async with async_session() as session:
        repo = DocumentRepository(session)
        indexed_links = await repo.get_all_gdrive_links()

    result = []
    for f in drive_files:
        if f["webViewLink"] not in indexed_links:
            result.append(
                InboxFile(
                    drive_id=f["id"],
                    name=f["name"],
                    mime_type=f["mimeType"],
                    web_view_link=f["webViewLink"],
                )
            )

    return result


async def process_inbox_file(
    inbox_file: InboxFile,
    target_category: str,
    target_filename: str,
    summary: str,
    owner: str,
) -> dict[str, str | None]:
    """Переносит файл из inbox в нужную папку Drive, индексирует в БД. Удаляет из inbox."""
    temp_path: Path | None = None
    try:
        temp_path = await _download_to_temp(inbox_file.drive_id, inbox_file.name, inbox_file.mime_type)

        target_path = f"{target_category}/{target_filename}"
        upload_result = await upload_file_with_status(str(temp_path), target_path)

        embedding = await get_embedding(summary)

        async with async_session() as session:
            repo = DocumentRepository(session)
            await repo.add_document(
                saved_filename=target_filename,
                local_path=target_path,
                category=target_category,
                owner=owner,
                summary=summary,
                embedding=embedding,
                gdrive_link=upload_result.get("link"),
                doc_id=uuid.uuid4(),
            )
            await session.commit()

        await asyncio.to_thread(delete_drive_file, inbox_file.drive_id)
        logger.info(f"Inbox-файл {inbox_file.name} обработан и удалён из inbox.")

        return {
            "gdrive_link": upload_result.get("link"),
            "gdrive_folder_link": upload_result.get("folder_link"),
            "gdrive_error": upload_result.get("error"),
        }
    finally:
        if temp_path and temp_path.exists():
            with contextlib.suppress(Exception):
                temp_path.unlink()
