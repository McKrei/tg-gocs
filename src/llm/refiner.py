import json
from typing import Any

from src.agent.agent import parse_json_content
from src.config import settings
from src.llm.client import get_llm_client
from src.utils.logger import get_logger
from src.utils.retry import with_retry

logger = get_logger(__name__)


@with_retry(attempts=3, initial_delay=1.0)
async def refine_draft(old_draft: dict[str, Any], user_feedback: str) -> dict[str, Any]:
    """Корректирует черновик классификации документа на основе фидбека пользователя."""
    client = get_llm_client()

    prompt = (
        "Ты — помощник, который корректирует черновик метаданных документа на основе фидбека пользователя.\n"
        f"Текущий черновик:\n{json.dumps(old_draft, ensure_ascii=False)}\n\n"
        f"Замечание пользователя:\n\"{user_feedback}\"\n\n"
        "Обнови черновик. Верни строго JSON-объект со следующими ключами:\n"
        "- category: строка, относительный путь (например, 'Медицина/Жена')\n"
        "- suggested_filename: строка, имя файла с расширением (например, 'Polis.pdf')\n"
        "- summary: строка, краткое описание содержания документа\n"
        "- owner: строка, владелец документа (например, 'Жена', 'Муж', 'Сын', 'Общее')\n"
        "Отвечай строго в формате JSON, без лишнего текста вокруг."
    )

    try:
        response = await client.chat.completions.create(
            model=settings.llm.model_name,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content or ""
        return parse_json_content(content)
    except Exception as e:
        logger.error(f"Ошибка при уточнении драфта: {e}")
        return old_draft
