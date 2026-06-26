import asyncio
import functools
from collections.abc import Callable
from typing import Any

from src.utils.logger import get_logger

logger = get_logger(__name__)


def with_retry(
    attempts: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Декоратор для повторного выполнения асинхронных функций при ошибках."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = initial_delay
            for attempt in range(1, attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    if attempt == attempts:
                        logger.error(f"Все попытки ({attempts}) вызова {func.__name__} провалились: {e}")
                        raise e
                    logger.warning(
                        f"Попытка {attempt}/{attempts} вызова {func.__name__} завершилась ошибкой: {e}. "
                        f"Повтор через {delay:.2f} сек..."
                    )
                    await asyncio.sleep(delay)
                    delay *= backoff_factor
            return None
        return wrapper
    return decorator
