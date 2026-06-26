import asyncio

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from src.bot.handlers import callbacks, commands, files, text
from src.bot.middleware.auth import AuthMiddleware
from src.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


async def main() -> None:
    """Точка входа для запуска Telegram-бота."""
    bot = Bot(token=settings.bot.token)
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.outer_middleware(AuthMiddleware())
    dp.callback_query.outer_middleware(AuthMiddleware())

    dp.include_router(commands.router)
    dp.include_router(callbacks.router)
    dp.include_router(text.router)
    dp.include_router(files.router)


    logger.info("Запуск Telegram-бота...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
