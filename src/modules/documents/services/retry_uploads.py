import asyncio
import traceback
from pathlib import Path

from src.core.db.engine import async_session
from src.core.drive.uploader import upload_file_with_status
from src.core.utils.logger import get_logger
from src.modules.documents.repository import DocumentRepository

logger = get_logger(__name__)


async def retry_pending_uploads_once() -> None:
    """Выполняет одну итерацию попытки выгрузки файлов из очереди pending_uploads."""
    async with async_session() as session:
        repo = DocumentRepository(session)
        pending_list = await repo.get_pending_uploads()

        if not pending_list:
            return

        logger.info(f"Обнаружено {len(pending_list)} отложенных файлов для загрузки в Google Drive.")

        for item in pending_list:
            local_file = Path(item.local_path)
            if not local_file.exists():
                logger.error(
                    f"Локальный файл {item.local_path} для задачи {item.id} не найден. Отмена задачи."
                )
                await repo.update_pending_upload(
                    upload_id=item.id,
                    attempts=item.attempts + 1,
                    last_error="Локальный файл удален или отсутствует",
                    status="failed",
                )
                continue

            attempts = item.attempts + 1
            logger.info(
                f"Попытка выгрузки {attempts} для {item.target_path} (локальный файл: {item.local_path})"
            )

            try:
                res = await upload_file_with_status(str(local_file), item.target_path)
                if res.get("link"):
                    # Успешно выгружено! Обновляем ссылку у документа, если он привязан
                    if item.document_id:
                        doc = await repo.get_document(item.document_id)
                        if doc:
                            doc.gdrive_link = res["link"]
                    
                    await repo.update_pending_upload(
                        upload_id=item.id,
                        attempts=attempts,
                        last_error=None,
                        status="completed",
                    )
                    logger.info(f"Успешная выгрузка отложенного файла {item.target_path}!")
                else:
                    err_msg = res.get("error") or "Неизвестная ошибка загрузки"
                    status = "pending" if attempts < 5 else "failed"
                    await repo.update_pending_upload(
                        upload_id=item.id,
                        attempts=attempts,
                        last_error=err_msg,
                        status=status,
                    )
                    logger.warning(
                        f"Не удалось выгрузить отложенный файл {item.target_path}. Причина: {err_msg}."
                    )
            except Exception as e:
                err_msg = f"{e}\n{traceback.format_exc()}"
                status = "pending" if attempts < 5 else "failed"
                await repo.update_pending_upload(
                    upload_id=item.id,
                    attempts=attempts,
                    last_error=err_msg,
                    status=status,
                )
                logger.error(f"Исключение при выгрузке отложенного файла {item.target_path}: {e}")

        await session.commit()


async def start_retry_uploads_loop(interval_seconds: float = 900.0) -> None:
    """Запускает бесконечный цикл фоновой выгрузки отложенных файлов."""
    logger.info("Запуск фонового процесса ретраев загрузки Google Drive...")
    # Делаем первую попытку сразу при старте, чтобы обработать накопившееся
    try:
        await retry_pending_uploads_once()
    except Exception as e:
        logger.error(f"Ошибка при стартовой попытке ретрая: {e}")

    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await retry_pending_uploads_once()
        except Exception as e:
            logger.error(f"Ошибка в фоновом цикле ретраев: {e}")
