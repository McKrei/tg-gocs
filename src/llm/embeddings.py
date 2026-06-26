import math

import httpx

from src.config import settings
from src.utils.logger import get_logger
from src.utils.retry import with_retry

logger = get_logger(__name__)


def _normalize_vector(vector: list[float]) -> list[float]:
    """Приводит вектор к единичной L2-норме."""
    l2_norm = math.sqrt(sum(x * x for x in vector))
    if l2_norm == 0:
        return vector
    return [x / l2_norm for x in vector]



@with_retry(attempts=3, initial_delay=1.0)
async def get_embedding(text: str) -> list[float]:
    """Получает L2-нормализованный эмбеддинг текста через OpenRouter."""
    dim = settings.llm.embedding_dim
    url = f"{settings.llm.base_url}/embeddings"
    headers = {
        "Authorization": f"Bearer {settings.llm.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.llm.embedding_model_name,
        "input": text,
        "dimensions": dim,
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=headers, json=payload, timeout=20.0)
            response.raise_for_status()
            data = response.json()
            if "data" not in data or not data["data"]:
                raise ValueError(f"Некорректный формат ответа от OpenRouter: {data}")

            # Защитное обрезание и приведение к единичной длине
            raw_embedding = list(data["data"][0]["embedding"])[:dim]
            return _normalize_vector(raw_embedding)

        except Exception as e:
            logger.error(f"Ошибка при получении эмбеддинга для '{text[:30]}...': {e}")
            raise

