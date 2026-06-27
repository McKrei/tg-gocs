"""Модуль для проверки документов на дубликаты с помощью LLM."""

from typing import Any

from src.core.config import settings
from src.core.llm.client import get_llm_client
from src.core.utils.logger import get_logger
from src.core.utils.retry import with_retry
from src.modules.documents.agent import parse_json_content

logger = get_logger(__name__)


@with_retry(attempts=3, initial_delay=1.0)
async def check_is_duplicate(new_doc: dict[str, Any], existing_doc: dict[str, Any]) -> bool:
    """Проверяет с помощью LLM, являются ли два документа дубликатами."""
    client = get_llm_client()

    prompt = (
        "Ты — ассистент, помогающий определить, являются ли два документа дубликатами "
        "(одним и тем же документом) или это разные документы (например, одного типа, "
        "но для разных людей, с разными датами или номерами).\n\n"
        "Сравни два документа:\n\n"
        "Документ 1 (Новый):\n"
        f"- Имя файла: {new_doc.get('suggested_filename')}\n"
        f"- Категория: {new_doc.get('category')}\n"
        f"- Описание/Содержимое: {new_doc.get('summary')}\n\n"
        "Документ 2 (Существующий):\n"
        f"- Имя файла: {existing_doc.get('saved_filename')}\n"
        f"- Категория: {existing_doc.get('category')}\n"
        f"- Описание/Содержимое: {existing_doc.get('summary')}\n\n"
        "Определи, являются ли они дубликатами одного и того же физического документа. "
        "Обрати внимание:\n"
        "1. Если они принадлежат разным людям (разные имена, фамилии, отчества в описании "
        "или имени файла), они НЕ являются дубликатами.\n"
        "2. Если у них разные даты выдачи, номера документов или другие уникальные реквизиты, "
        "они НЕ являются дубликатами.\n"
        "3. Если это документы одного типа (например, два паспорта РФ или два медицинских полиса), "
        "но для разных членов семьи, они НЕ являются дубликатами.\n"
        "4. Если все ключевые данные (ФИО владельца, тип документа, дата, номера) совпадают, "
        "то это дубликаты.\n\n"
        "Верни строго JSON-объект с ключами:\n"
        "- \"is_duplicate\": true (если это один и тот же документ) или false (если это разные документы)\n"
        "- \"reason\": краткое объяснение решения на русском языке.\n\n"
        "Отвечай строго в формате JSON, без лишнего текста вокруг."
    )

    try:
        response = await client.chat.completions.create(
            model=settings.llm.model_name,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content or ""
        result = parse_json_content(content)
        is_dup = bool(result.get("is_duplicate", False))
        reason = result.get("reason", "Нет объяснения")
        logger.info(f"LLM проверка на дубликат: {is_dup} (Причина: {reason})")
        return is_dup
    except Exception as e:
        logger.error(f"Ошибка при проверке дубликата через LLM: {e}")
        return True
