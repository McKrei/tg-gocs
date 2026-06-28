from src.core.agent.registry import ToolRegistry, register_tool, registry


def test_tool_registry_registration() -> None:
    """Проверяет успешную регистрацию инструмента и генерацию схемы."""
    test_registry = ToolRegistry()

    @test_registry.register
    async def my_test_tool(query: str, limit: int = 10) -> str:
        """Это тестовый инструмент.

        query: текст запроса
        limit: лимит результатов
        """
        return f"{query}_{limit}"

    # Проверка регистрации функции
    tools = test_registry.get_tools_map("full")
    assert "my_test_tool" in tools
    assert tools["my_test_tool"] == my_test_tool

    # Проверка генерации схемы
    schemas = test_registry.get_tools_schema("full")
    assert len(schemas) == 1
    schema = schemas[0]

    assert schema["type"] == "function"
    assert schema["function"]["name"] == "my_test_tool"
    assert schema["function"]["description"] == "Это тестовый инструмент."

    params = schema["function"]["parameters"]
    assert params["type"] == "object"
    assert "query" in params["properties"]
    assert params["properties"]["query"]["type"] == "string"
    assert params["properties"]["query"]["description"] == "текст запроса"

    assert "limit" in params["properties"]
    assert params["properties"]["limit"]["type"] == "integer"
    assert params["properties"]["limit"]["description"] == "лимит результатов"

    assert params["required"] == ["query"]


def test_decorator_registration() -> None:
    """Проверяет регистрацию через глобальный декоратор."""

    @register_tool(name="custom_name", mode="classify")
    def decorated_tool(path: str) -> bool:
        """Кастомный инструмент.

        path: путь к папке
        """
        return True

    # Должен быть в глобальном реестре
    classify_tools = registry.get_tools_map("classify")
    assert "custom_name" in classify_tools

    registry.get_tools_schema("classify")[0]
    # Наш декоратор может зарегистрировать несколько инструментов, поэтому найдем нужный в схеме
    target_schema = next(s for s in registry.get_tools_schema("classify") if s["function"]["name"] == "custom_name")
    assert target_schema["function"]["description"] == "Кастомный инструмент."
