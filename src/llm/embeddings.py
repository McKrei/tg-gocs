"""Функции для работы с векторными представлениями (эмбеддингами) текстов."""

import logging

from src.config import settings
from src.llm.client import get_llm_client

logger = logging.getLogger(__name__)


async def get_embedding(text: str) -> list[float]:
    """Генерирует векторное представление (эмбеддинг) для переданного текста."""
    client = get_llm_client()
    try:
        response = await client.embeddings.create(
            model=settings.llm.embedding_model_name,
            input=text,
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"Ошибка при получении эмбеддинга для текста '{text[:30]}...': {e}")
        raise
