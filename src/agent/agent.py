"""Оркестратор агента, управляющий процессом классификации документов и вызовом инструментов."""

import base64
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, cast

from src.agent.tools import CLASSIFY_TOOLS_MAP, CLASSIFY_TOOLS_SCHEMA, TOOLS_MAP, TOOLS_SCHEMA
from src.config import settings
from src.llm.client import get_llm_client
from src.utils.logger import get_logger
from src.utils.retry import with_retry

logger = get_logger(__name__)

DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+")
DATE_PATTERNS = [
    re.compile(r"\b(?P<year>20\d{2}|19\d{2})[-.](?P<month>\d{1,2})[-.](?P<day>\d{1,2})\b"),
    re.compile(r"\b(?P<day>\d{1,2})[./-](?P<month>\d{1,2})[./-](?P<year>20\d{2}|19\d{2})\b"),
]
MONTHS_RU = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
MONTH_DATE_RE = re.compile(
    r"\b(?P<day>\d{1,2})\s+"
    r"(?P<month>января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
    r"\s+(?P<year>20\d{2}|19\d{2})\b",
    re.IGNORECASE,
)


def encode_file(file_path: str) -> str:
    """Кодирует файл в строку base64."""
    with Path(file_path).open("rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


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


def _build_date(year: str, month: str | int, day: str) -> str | None:
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def _extract_document_date(text: str) -> str | None:
    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            result = _build_date(match["year"], match["month"], match["day"])
            if result:
                return result

    month_match = MONTH_DATE_RE.search(text)
    if month_match:
        month = MONTHS_RU[month_match["month"].lower()]
        return _build_date(month_match["year"], month, month_match["day"])

    return None


def normalize_draft_metadata(draft: dict[str, Any], file_ext: str, today: str | None = None) -> dict[str, Any]:
    """Нормализует имя файла черновика по правилам проекта."""
    normalized = dict(draft)
    fallback_date = today or date.today().isoformat()
    suggested_name = str(normalized.get("suggested_filename") or "Документ")
    ext = file_ext or Path(suggested_name).suffix or ".pdf"
    stem = Path(suggested_name).stem.strip() or "Документ"
    stem = DATE_PREFIX_RE.sub("", stem).strip()
    stem = re.sub(r"[\\/]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip() or "Документ"

    date_source = " ".join(
        [
            suggested_name,
            str(normalized.get("summary") or ""),
        ]
    )
    document_date = _extract_document_date(date_source) or fallback_date
    normalized["suggested_filename"] = f"{document_date} {stem}{ext}"
    return normalized


@with_retry(attempts=3, initial_delay=1.0)
async def classify_document(temp_filepath: str, classify_only: bool = False) -> dict[str, Any]:
    """Проводит мультимодальный анализ документа. При classify_only=True использует только read-only инструменты."""
    active_tools_map = CLASSIFY_TOOLS_MAP if classify_only else TOOLS_MAP
    active_tools_schema = CLASSIFY_TOOLS_SCHEMA if classify_only else TOOLS_SCHEMA
    file_path = Path(temp_filepath)
    suffix = file_path.suffix.lower()
    base64_data = encode_file(temp_filepath)
    client = get_llm_client()

    if suffix == ".pdf":
        media_content = {
            "type": "file",
            "file": {
                "filename": file_path.name,
                "file_data": f"data:application/pdf;base64,{base64_data}",
            },
        }
    else:
        mime_type = "image/png" if suffix == ".png" else "image/jpeg"
        media_content = {
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{base64_data}"},
        }

    from src.agent.tools import get_existing_structure

    structure_info = await get_existing_structure()

    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "Ты — умный агент для классификации семейных документов. "
                "Твоя задача — проанализировать изображение документа, использовать существующее дерево категорий "
                "и выдать метаданные. Постарайся сопоставить документ с существующими папками, если они подходят.\n\n"
                f"{structure_info}\n\n"
                "Извлеки максимум полезных реквизитов из документа: серию, номер, дату выдачи или регистрации, "
                "номер актовой записи, орган выдачи, ФИО, даты рождения, адреса, сроки действия, суммы, "
                "идентификаторы и любые другие поля, по которым пользователь может потом искать документ. "
                "Поле summary должно быть подробным, но без выдумок: перечисли только то, что реально видно. "
                "Имя файла пиши на русском языке. Оно должно начинаться с даты в формате YYYY-MM-DD: "
                "если в документе есть дата документа, регистрации или выдачи — используй её; "
                "если даты нет — используй сегодняшнюю дату. После даты добавь короткое понятное название. "
                "Ты ДОЛЖЕН вернуть результат строго в формате JSON-объекта со следующими ключами:\n"
                "- category: строка, относительный путь (например, 'Медицина/Жена')\n"
                "- suggested_filename: строка, имя файла с расширением "
                "(например, '2017-07-07 Свидетельство о браке.jpg')\n"
                "- summary: строка, подробное описание содержания и всех найденных реквизитов\n"
                "- owner: строка, владелец документа (например, 'Жена', 'Муж', 'Сын', 'Общее')\n"
                "Отвечай строго в формате JSON, без лишнего текста вокруг."
            ),
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Классифицируй этот документ:"},
                media_content,
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
            tools=cast(Any, active_tools_schema),
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
                if func_name in active_tools_map:
                    try:
                        tool_result = await active_tools_map[func_name](**func_args)
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
