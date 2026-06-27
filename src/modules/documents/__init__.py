from collections.abc import Callable, Coroutine
from typing import Any

from aiogram import Router

from src.core.agent.registry import registry

from .handlers import batch, callbacks, commands, files, inbox, search, text
from .services.retry_uploads import start_retry_uploads_loop


def register_module() -> tuple[Router, list[Callable[..., Coroutine[Any, Any, Any]]]]:
    """Регистрирует модуль управления документами.

    Возвращает:
        Router: Роутер со всеми обработчиками модуля.
        list: Список асинхронных фоновых задач для запуска при старте бота.
    """
    # 1. Сканируем/регистрируем инструменты модуля
    registry.discover_tools()

    # 2. Создаем главный роутер модуля и подключаем дочерние роутеры хэндлеров
    module_router = Router(name="documents")
    module_router.include_router(commands.router)
    module_router.include_router(batch.router)
    module_router.include_router(callbacks.router)
    module_router.include_router(inbox.router)
    module_router.include_router(text.router)
    module_router.include_router(search.router)
    module_router.include_router(files.router)


    # 3. Список фоновых задач
    background_tasks = [start_retry_uploads_loop]

    return module_router, background_tasks
