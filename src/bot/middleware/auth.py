from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from src.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


class AuthMiddleware(BaseMiddleware):
    """Проверяет права доступа пользователя по списку ALLOWED_USER_IDS."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)

        if user.id not in settings.bot.allowed_user_ids:
            logger.warning(f"Доступ заблокирован для user_id={user.id}, username={user.username}")
            return None

        return await handler(event, data)
