"""Интеграционные тесты для проверки доступов к OpenRouter и Google Drive."""

import asyncio
import math

import pytest

from src.core.config import settings
from src.core.drive.client import get_drive_service, is_drive_configured
from src.core.llm.client import get_llm_client
from src.core.llm.embeddings import get_embedding


@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_openrouter_llm() -> None:
    """Проверяет подключение к OpenRouter и доступность модели Gemini Flash."""
    client = get_llm_client()
    response = await client.chat.completions.create(
        model=settings.llm.model_name,
        messages=[{"role": "user", "content": "Привет! Ответь строго одним словом 'ОК'."}],
        max_tokens=500,
    )
    ans = response.choices[0].message.content
    assert ans is not None
    assert "ОК" in ans.strip()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_openrouter_embeddings() -> None:
    """Проверяет генерацию и L2-нормализацию обрезанных эмбеддингов Gemini."""
    vec = await get_embedding("Тестовый запрос для проверки эмбеддингов")
    assert len(vec) == 768

    # Проверяем, что вектор L2-нормализован (длина равна 1.0)
    l2_norm = math.sqrt(sum(x * x for x in vec))
    assert pytest.approx(l2_norm, rel=1e-3) == 1.0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_google_drive_permissions() -> None:
    """Проверяет права редактора (Editor) для Сервисного Аккаунта на Google Drive."""
    assert is_drive_configured(), "Интеграция с Google Drive не настроена."

    def _run_drive_actions() -> str:
        service = get_drive_service()
        root_id = settings.storage.gdrive_root_folder_id

        # Попытка создать тестовую папку
        test_folder_name = "__INTEGRATION_TEST_FOLDER__"
        file_metadata = {
            "name": test_folder_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [root_id],
        }
        folder = service.files().create(body=file_metadata, fields="id").execute()
        folder_id = folder.get("id")

        # Попытка удалить тестовую папку
        service.files().delete(fileId=folder_id).execute()
        return str(folder_id)

    folder_id = await asyncio.to_thread(_run_drive_actions)
    assert folder_id is not None
