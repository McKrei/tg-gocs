import asyncio
import importlib
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher

from src.bot.middleware.auth import AuthMiddleware
from src.core.config import settings
from src.core.db.engine import init_db
from src.core.db.fsm_storage import SQLiteStorage
from src.core.utils.logger import get_logger

logger = get_logger(__name__)

# Сохраняем сильные ссылки на фоновые задачи, чтобы Garbage Collector не удалил их
background_tasks: set[asyncio.Task[Any]] = set()


def discover_and_register_modules(dp: Dispatcher) -> list[Callable[..., Coroutine[Any, Any, Any]]]:
    """Динамически сканирует директорию modules, регистрирует каждый найденный модуль."""
    modules_dir = Path(__file__).resolve().parent.parent / "modules"
    all_bg_tasks: list[Callable[..., Coroutine[Any, Any, Any]]] = []

    if not modules_dir.exists():
        logger.warning(f"Директория модулей не найдена по пути: {modules_dir}")
        return all_bg_tasks

    for path in modules_dir.iterdir():
        if path.is_dir() and not path.name.startswith("__"):
            module_name = f"src.modules.{path.name}"
            try:
                mod = importlib.import_module(module_name)
                if hasattr(mod, "register_module"):
                    router, bg_tasks = mod.register_module()
                    dp.include_router(router)
                    all_bg_tasks.extend(bg_tasks)
                    logger.info(f"Успешно зарегистрирован модуль: {path.name}")
                else:
                    logger.warning(f"Модуль {path.name} не содержит функцию register_module")
            except Exception as e:
                logger.error(f"Ошибка при регистрации модуля {path.name}: {e}")

    return all_bg_tasks


async def main() -> None:
    """Точка входа для запуска Telegram-бота."""
    bot = Bot(token=settings.bot.token)
    dp = Dispatcher(storage=SQLiteStorage())

    # Настройка middleware авторизации
    dp.message.outer_middleware(AuthMiddleware())
    dp.callback_query.outer_middleware(AuthMiddleware())

    # Динамическая регистрация модулей
    bg_tasks_funcs = discover_and_register_modules(dp)

    # Инициализация БД
    await init_db()

    # Запуск фоновых задач из модулей
    for task_func in bg_tasks_funcs:
        task = asyncio.create_task(task_func())
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        logger.info(f"Запущена фоновая задача: {task_func.__name__}")

    logger.info("Запуск Telegram-бота...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
