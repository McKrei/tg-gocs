"""Функции для работы с векторными представлениями (эмбеддингами) текстов."""

import logging
import math

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

# Размерность векторов для базы данных sqlite-vec
EMBEDDING_DIM = 768


def _normalize_vector(vector: list[float]) -> list[float]:
    """Нормализует вектор по L2 норме (приводит его к единичной длине).

    Это гарантирует, что косинусное сходство может быть корректно вычислено
    через скалярное произведение или через оптимизированные L2/косинусные метрики.
    """
    l2_norm = math.sqrt(sum(x * x for x in vector))
    if l2_norm == 0:
        return vector
    return [x / l2_norm for x in vector]


async def get_embedding(text: str) -> list[float]:
    """Генерирует векторное представление (эмбеддинг) для текста через OpenRouter.

    Для модели google/gemini-embedding-2 (по умолчанию возвращающей 3072 измерений)
    передается параметр "dimensions": 768. Благодаря технологии Matryoshka Representation Learning (MRL),
    модель возвращает усеченный вектор размерности 768 с минимальной потерей качества
    (в пределах 1-2% точности), что значительно экономит дисковое пространство и память.
    """
    url = f"{settings.llm.base_url}/embeddings"
    headers = {
        "Authorization": f"Bearer {settings.llm.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.llm.embedding_model_name,
        "input": text,
        "dimensions": EMBEDDING_DIM,
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=headers, json=payload, timeout=20.0)
            response.raise_for_status()
            data = response.json()
            if "data" not in data or not data["data"]:
                raise ValueError(f"Некорректный формат ответа эмбеддингов от OpenRouter: {data}")

            # Резервное обрезание до 768 элементов на случай, если провайдер проигнорировал параметр "dimensions"
            raw_embedding = list(data["data"][0]["embedding"])[:EMBEDDING_DIM]

            # Обязательная L2-нормализация вектора
            return _normalize_vector(raw_embedding)

        except Exception as e:
            logger.error(f"Ошибка при получении эмбеддинга для текста '{text[:30]}...': {e}")
            raise

