import json
from typing import Any

from src.core.config import settings
from src.core.llm.client import get_llm_client
from src.core.utils.logger import get_logger
from src.core.utils.retry import with_retry
from src.modules.documents.agent import parse_json_content

logger = get_logger(__name__)


@with_retry(attempts=3, initial_delay=1.0)
async def refine_draft(old_draft: dict[str, Any], user_feedback: str) -> dict[str, Any]:
    """Корректирует черновик классификации документа на основе фидбека пользователя."""
    client = get_llm_client()

    from src.modules.documents.tools import get_existing_structure

    structure_info = await get_existing_structure()

    prompt = (
        "Ты — помощник, который корректирует черновик метаданных документа на основе фидбека пользователя.\n\n"
        "ВАЖНОЕ ПРАВИЛО: Фидбек (замечание) пользователя имеет наивысший приоритет и является "
        "абсолютным указанием. Если пользователь просит изменить категорию, владельца, "
        "имя файла или описание — ты обязан сделать ровно то, о чем он просит.\n"
        "Если пользователь просит перенести файл в папку, которой нет в списке существующих "
        "(например, 'Личные документы/Виктория'), ты должен прописать именно этот путь "
        "в поле 'category'. Не пытайся заменить её на существующую папку.\n\n"
        f"Текущий черновик:\n{json.dumps(old_draft, ensure_ascii=False)}\n\n"
        f'Замечание пользователя:\n"{user_feedback}"\n\n'
        f"Существующая структура для справки:\n{structure_info}\n\n"
        "Обнови черновик на основе замечания пользователя. Верни строго JSON-объект "
        "со следующими ключами:\n"
        "- category: строка, относительный путь (категория/папка для сохранения)\n"
        "- suggested_filename: строка, имя файла с расширением\n"
        "- summary: строка, подробное описание содержания документа (если пользователь просит "
        "исправить описание или реквизиты, обнови его)\n"
        "- owner: строка, владелец документа (например, имя члена семьи, которому "
        "принадлежит документ, или 'Общее')\n"
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
