"""Асинхронный клиент для взаимодействия с API OpenRouter."""

from openai import AsyncOpenAI

from src.config import settings


def get_llm_client() -> AsyncOpenAI:
    """Возвращает настроенный экземпляр асинхронного клиента OpenAI для OpenRouter."""
    return AsyncOpenAI(
        api_key=settings.llm.api_key,
        base_url=settings.llm.base_url,
        timeout=30.0,
    )
