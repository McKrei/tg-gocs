"""Функции для работы с векторными представлениями (эмбеддингами) текстов."""

import logging

import httpx

from src.config import settings

logger = logging.getLogger(__name__)


async def get_embedding(text: str) -> list[float]:
    """Генерирует векторное представление (эмбеддинг) для переданного текста через httpx."""
    url = f"{settings.llm.base_url}/embeddings"
    headers = {
        "Authorization": f"Bearer {settings.llm.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.llm.embedding_model_name,
        "input": text,
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=headers, json=payload, timeout=20.0)
            response.raise_for_status()
            data = response.json()
            if "data" not in data or not data["data"]:
                raise ValueError(f"Некорректный формат ответа эмбеддингов от OpenRouter: {data}")
            # Обрезаем вектор до первых 768 элементов (MRL - Matryoshka Representation Learning)
            return list(data["data"][0]["embedding"])[:768]

        except Exception as e:
            logger.error(f"Ошибка при получении эмбеддинга для текста '{text[:30]}...': {e}")
            raise
