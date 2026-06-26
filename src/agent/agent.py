"""Оркестратор агента, управляющий процессом классификации документов и вызовом инструментов."""

import base64
import json
import re
from pathlib import Path
from typing import Any, cast

from src.agent.tools import TOOLS_MAP, TOOLS_SCHEMA
from src.config import settings
from src.llm.client import get_llm_client
from src.utils.logger import get_logger

logger = get_logger(__name__)



def encode_image(image_path: str) -> str:
    """Кодирует файл изображения в строку формата base64."""
    with Path(image_path).open("rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


def parse_json_content(content: str) -> dict[str, Any]:
    """Пытается безопасно распарсить JSON-объект из текстового ответа модели."""
    cleaned = content.strip()
    # Убираем возможные markdown обертки для кода (```json ... ```)
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\n", "", cleaned)
        cleaned = re.sub(r"\n```$", "", cleaned)
        cleaned = cleaned.strip()

    try:
        return cast(dict[str, Any], json.loads(cleaned))
    except json.JSONDecodeError as e:
        # Пробуем найти первую '{' и последнюю '}'
        match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
        if match:
            try:
                return cast(dict[str, Any], json.loads(match.group(1)))
            except json.JSONDecodeError as e_inner:
                raise ValueError(f"Не удалось распарсить извлеченный JSON: {match.group(1)}") from e_inner
        raise ValueError(f"Ответ модели не содержит валидного JSON: {content}") from e


async def classify_document(temp_filepath: str) -> dict[str, Any]:
    """Проводит мультимодальный анализ документа, при необходимости используя инструменты."""
    base64_image = encode_image(temp_filepath)
    client = get_llm_client()

    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "Ты — умный агент для классификации семейных документов. "
                "Твоя задача — проанализировать изображение документа, при необходимости посмотреть "
                "существующее дерево категорий с помощью get_directory_tree, и выдать метаданные. "
                "Ты ДОЛЖЕН вернуть результат строго в формате JSON-объекта со следующими ключами:\n"
                "- category: строка, относительный путь (например, 'Медицина/Жена')\n"
                "- suggested_filename: строка, имя файла с расширением (например, 'Polis.pdf')\n"
                "- summary: строка, краткое описание содержания документа\n"
                "- owner: строка, владелец документа (например, 'Жена', 'Муж', 'Сын', 'Общее')\n"
                "Отвечай строго в формате JSON, без лишнего текста вокруг."
            ),
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Классифицируй этот документ:"},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                },
            ],
        },
    ]

    # Максимальное количество итераций агента для предотвращения бесконечного цикла
    max_steps = 5

    for step in range(max_steps):
        logger.info(f"Итерация агента {step + 1}...")
        response = await client.chat.completions.create(
            model=settings.llm.model_name,
            messages=cast(Any, messages),
            tools=cast(Any, TOOLS_SCHEMA),
            tool_choice="auto",
        )

        message = response.choices[0].message

        # Если модель вызывает инструменты
        if message.tool_calls:
            logger.info(f"Модель запросила вызов {len(message.tool_calls)} инструментов...")
            # Добавляем ответ модели в историю диалога
            messages.append(cast(Any, message))

            for tool_call in message.tool_calls:
                func_name = cast(Any, tool_call).function.name
                func_args = json.loads(cast(Any, tool_call).function.arguments)

                logger.info(f"Запуск инструмента {func_name} с аргументами {func_args}...")
                if func_name in TOOLS_MAP:
                    try:
                        tool_result = await TOOLS_MAP[func_name](**func_args)
                    except Exception as e:
                        logger.error(f"Ошибка при вызове {func_name}: {e}")
                        tool_result = {"error": str(e)}
                else:
                    tool_result = {"error": f"Функция {func_name} не зарегистрирована."}

                # Добавляем результат выполнения инструмента в историю диалога
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": func_name,
                        "content": json.dumps(tool_result, ensure_ascii=False),
                    }
                )
            # Переходим на следующую итерацию
            continue

        # Если модель вернула текстовый ответ (финальный шаг классификации)
        content = message.content or ""
        logger.info(f"Агент завершил рассуждения. Финальный ответ: {content}")
        try:
            return parse_json_content(content)
        except Exception as e:
            logger.error(f"Ошибка парсинга финального ответа: {e}")
            return {
                "category": "Нераспознано",
                "suggested_filename": Path(temp_filepath).name,
                "summary": f"Ошибка парсинга JSON. Оригинальный текст: {content}",
                "owner": "Неизвестно",
            }

    logger.error("Превышено максимальное число шагов агента без получения финального ответа.")
    return {
        "category": "Нераспознано",
        "suggested_filename": Path(temp_filepath).name,
        "summary": "Агент превысил количество лимитных шагов.",
        "owner": "Неизвестно",
    }
