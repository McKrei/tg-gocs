from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from src.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


class AuthMiddleware(BaseMiddleware):
    """Разрешает доступ только из чатов/групп из списка ALLOWED_CHAT_IDS."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat = data.get("event_chat")
        if not chat:
            return await handler(event, data)

        if settings.bot.allowed_chat_ids and chat.id not in settings.bot.allowed_chat_ids:
            user = data.get("event_from_user")
            logger.warning(f"Доступ заблокирован для chat_id={chat.id}, user_id={user.id if user else 'unknown'}")
            return None

        return await handler(event, data)
