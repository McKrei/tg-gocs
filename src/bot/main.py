import asyncio
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from src.bot.handlers import callbacks, commands, files, inbox, search, text
from src.bot.middleware.auth import AuthMiddleware
from src.config import settings
from src.db.engine import init_db
from src.utils.logger import get_logger

logger = get_logger(__name__)


background_tasks: set[asyncio.Task[Any]] = set()


async def main() -> None:
    """Точка входа для запуска Telegram-бота."""
    bot = Bot(token=settings.bot.token)
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.outer_middleware(AuthMiddleware())
    dp.callback_query.outer_middleware(AuthMiddleware())

    dp.include_router(commands.router)
    dp.include_router(callbacks.router)
    dp.include_router(inbox.router)
    dp.include_router(text.router)
    dp.include_router(search.router)
    dp.include_router(files.router)

    await init_db()
    
    # Запускаем фоновую задачу ретраев отложенных выгрузок в Drive
    from src.services.retry_uploads import start_retry_uploads_loop
    task = asyncio.create_task(start_retry_uploads_loop())
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)

    logger.info("Запуск Telegram-бота...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
