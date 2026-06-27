import importlib
import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.core.utils.logger import get_logger

logger = get_logger(__name__)


class ToolRegistry:
    """Реестр инструментов ИИ-агента с поддержкой динамического импорта и генерации схем."""

    def __init__(self) -> None:
        # Карты инструментов: name -> func
        self._tools: dict[str, Callable[..., Any]] = {}
        # Схемы инструментов: name -> schema_dict
        self._schemas: dict[str, dict[str, Any]] = {}
        # Режимы инструментов: name -> mode ("full" | "classify")
        self._modes: dict[str, str] = {}

    def register(
        self,
        func: Callable[..., Any],
        name: str | None = None,
        description: str | None = None,
        mode: str = "full",
    ) -> Callable[..., Any]:
        """Регистрирует функцию как инструмент агента."""
        tool_name = name or func.__name__
        tool_desc = description or func.__doc__ or ""
        tool_desc = tool_desc.strip().split("\n")[0]  # Берем первую строчку описания

        # Генерация JSON-схемы параметров
        parameters_schema = self._generate_parameters_schema(func)

        schema = {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": tool_desc,
                "parameters": parameters_schema,
            },
        }

        self._tools[tool_name] = func
        self._schemas[tool_name] = schema
        self._modes[tool_name] = mode

        logger.info(f"Зарегистрирован инструмент '{tool_name}' (режим: {mode})")
        return func

    def get_tools_map(self, mode: str = "full") -> dict[str, Callable[..., Any]]:
        """Возвращает словарь зарегистрированных функций-инструментов для указанного режима."""
        if mode == "classify":
            return {
                name: func for name, func in self._tools.items() if self._modes[name] == "classify"
            }
        return self._tools

    def get_tools_schema(self, mode: str = "full") -> list[dict[str, Any]]:
        """Возвращает JSON-схемы инструментов для указанного режима."""
        if mode == "classify":
            return [
                schema
                for name, schema in self._schemas.items()
                if self._modes[name] == "classify"
            ]
        return list(self._schemas.values())

    def discover_tools(self) -> None:
        """Динамически сканирует директорию модулей и импортирует tools.py для авторегистрации."""
        modules_dir = Path(__file__).resolve().parents[2] / "modules"
        if not modules_dir.exists():
            logger.warning(f"Директория модулей не найдена по пути: {modules_dir}")
            return

        for path in modules_dir.iterdir():
            if path.is_dir() and (path / "tools.py").exists():
                module_name = f"src.modules.{path.name}.tools"
                try:
                    importlib.import_module(module_name)
                    logger.info(f"Успешно импортирован модуль инструментов: {module_name}")
                except Exception as e:
                    logger.error(f"Ошибка при динамическом импорте {module_name}: {e}")

    def _generate_parameters_schema(self, func: Callable[..., Any]) -> dict[str, Any]:
        """Автоматически генерирует JSON-схему параметров из аннотаций типов и docstring."""
        sig = inspect.signature(func)
        properties = {}
        required = []

        # Базовый парсинг описания параметров из docstring
        doc = func.__doc__ or ""
        param_descriptions = {}
        for line in doc.split("\n"):
            line = line.strip()
            if ":" in line and not line.startswith("http"):
                parts = line.split(":", 1)
                name_part = parts[0].strip()
                desc_part = parts[1].strip()
                if "(" in name_part:
                    name_part = name_part.split("(")[0].strip()
                # Если имя совпадает с параметром функции
                if name_part in sig.parameters:
                    param_descriptions[name_part] = desc_part

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue

            # Соответствие типов Python и JSON Schema
            param_type = "string"
            annotation = param.annotation

            if annotation is int:
                param_type = "integer"
            elif annotation is float:
                param_type = "number"
            elif annotation is bool:
                param_type = "boolean"
            elif annotation in (list[str], list):
                param_type = "array"

            param_schema: dict[str, Any] = {"type": param_type}
            if param_type == "array":
                param_schema["items"] = {"type": "string"}

            # Пробуем взять описание из docstring или задаем дефолтное
            desc = param_descriptions.get(param_name)
            if desc:
                param_schema["description"] = desc
            else:
                # Предопределенные описания для стандартных параметров
                defaults = {
                    "query": "Текст поискового запроса",
                    "limit": "Максимальное количество возвращаемых результатов",
                    "path_prefix": "Префикс пути для обхода дерева директорий",
                    "path": "Относительный путь к новой директории (например: 'Медицина/Жена')",
                    "temp_file_ids": "Список имен файлов во временной папке (например: ['page1.jpg', 'page2.png'])",
                    "output_filename": "Имя итогового PDF-файла (например: 'document.pdf')",
                    "temp_filepath": "Путь к временному файлу (источник)",
                    "target_path": "Относительный путь для сохранения (категория и имя файла)",
                }
                if param_name in defaults:
                    param_schema["description"] = defaults[param_name]

            properties[param_name] = param_schema

            if param.default == inspect.Parameter.empty:
                required.append(param_name)

        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            schema["required"] = required
        return schema


# Глобальный реестр инструментов
registry = ToolRegistry()


def register_tool(
    name: str | None = None,
    description: str | None = None,
    mode: str = "full",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Декоратор для автоматической регистрации инструмента в глобальном реестре."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        registry.register(func, name=name, description=description, mode=mode)
        return func

    return decorator
