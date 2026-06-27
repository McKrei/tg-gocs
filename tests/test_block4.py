import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from src.core.drive.cache import FolderCache
from src.core.utils.image import compress_image


def test_folder_cache() -> None:
    """Проверяет корректность работы in-memory кэша папок."""
    cache = FolderCache(ttl_seconds=0.2)

    # Запись и чтение
    cache.set("category1", "id1")
    assert cache.get("category1") == "id1"

    # Регистронезависимость и нормализация слешей
    assert cache.get("CATEGORY1") == "id1"
    cache.set("parent/child", "id2")
    assert cache.get("parent\\child") == "id2"

    # Инвалидация
    cache.invalidate("category1")
    assert cache.get("category1") is None

    # Очистка
    cache.clear()
    assert cache.get("parent/child") is None

    # Проверка TTL (время жизни)
    cache.set("temp", "id_temp")
    assert cache.get("temp") == "id_temp"
    time.sleep(0.25)
    assert cache.get("temp") is None


@pytest.mark.asyncio
async def test_compress_image(tmp_path: Path) -> None:
    """Проверяет сжатие картинок утилитой compress_image."""
    # Создаем тестовое изображение большого размера
    input_img_path = tmp_path / "large.jpg"
    img = Image.new("RGB", (2000, 1500), color="red")
    img.save(input_img_path)

    output_img_path = tmp_path / "compressed.jpg"

    ok = await compress_image(input_img_path, output_img_path, max_width=1000, max_height=1000)
    assert ok is True
    assert output_img_path.exists()

    with Image.open(output_img_path) as res_img:
        width, height = res_img.size
        # Размеры должны пропорционально уменьшиться
        assert width == 1000
        assert height == 750


@pytest.mark.asyncio
async def test_retry_pending_uploads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет фоновую задачу повтора загрузки отложенных файлов."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.modules.documents.repository import DocumentRepository
    from src.modules.documents.services.retry_uploads import retry_pending_uploads_once

    # Создаем фиктивную сессию и репозиторий через моки
    mock_session = AsyncMock(spec=AsyncSession)
    
    mock_pending = AsyncMock()
    mock_pending.id = uuid.uuid4()
    mock_pending.local_path = "tests/fixtures/passport.jpg"  # Файл существует
    mock_pending.target_path = "Test/passport.jpg"
    mock_pending.document_id = uuid.uuid4()
    mock_pending.attempts = 1

    mock_repo = MagicMock(spec=DocumentRepository)
    mock_repo.get_pending_uploads = AsyncMock(return_value=[mock_pending])
    mock_repo.get_document = AsyncMock()
    mock_repo.update_pending_upload = AsyncMock()

    # Мокаем фабрику сессий и репозиторий
    monkeypatch.setattr(
        "src.modules.documents.services.retry_uploads.async_session",
        MagicMock(return_value=mock_session),
    )
    
    with (
        patch("src.modules.documents.services.retry_uploads.DocumentRepository", return_value=mock_repo),
        patch(
            "src.modules.documents.services.retry_uploads.upload_file_with_status",
            AsyncMock(return_value={"link": "https://drive.google.com/file_id"}),
        ),
    ):
        await retry_pending_uploads_once()

        # Проверяем, что была попытка получить документ
        mock_repo.get_document.assert_called_once_with(mock_pending.document_id)
        # Проверяем, что статус обновился на completed
        mock_repo.update_pending_upload.assert_called_once_with(
            upload_id=mock_pending.id,
            attempts=2,
            last_error=None,
            status="completed",
        )
