import json
from typing import Any

from aiogram import Bot, Router, types
from aiogram.filters import StateFilter

from src.agent.agent import parse_json_content
from src.agent.tools import vector_search
from src.config import settings
from src.llm.client import get_llm_client
from src.utils.logger import get_logger
from src.utils.retry import with_retry

router = Router()
logger = get_logger(__name__)


@with_retry(attempts=3, initial_delay=1.0)
async def rerank_documents(query: str, documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Выполняет повторное ранжирование документов с помощью LLM."""
    if not documents:
        return {"best_match_id": None, "explanation": "Документы не найдены."}

    client = get_llm_client()

    docs_subset = [
        {
            "id": doc["id"],
            "saved_filename": doc["saved_filename"],
            "category": doc["category"],
            "owner": doc["owner"],
            "summary": doc["summary"],
        }
        for doc in documents
    ]

    prompt = (
        "Ты — интеллектуальный ассистент по поиску семейных документов.\n"
        "Тебе предоставлен поисковый запрос пользователя и список документов, найденных по векторному сходству.\n"
        "Твоя задача:\n"
        "1. Определить, какой из найденных документов наиболее точно соответствует запросу пользователя.\n"
        "2. Если ни один документ не подходит под запрос, или если запрос не является поисковым "
        "(например, это приветствие или вопрос о жизни), верни null в поле 'best_match_id'.\n"
        "3. Сформулировать краткое пояснение или вежливый ответ для пользователя.\n\n"
        "Результат ты должен вернуть СТРОГО в формате JSON-объекта со следующими ключами:\n"
        "- best_match_id: строка (UUID документа) или null\n"
        "- explanation: строка (краткое описание найденного документа и почему он подходит, "
        "либо вежливый отказ/уточнение, если ничего не найдено)\n\n"
        f"Список документов:\n{json.dumps(docs_subset, ensure_ascii=False)}\n\n"
        f'Запрос пользователя:\n"{query}"\n'
    )

    try:
        response = await client.chat.completions.create(
            model=settings.llm.model_name,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or ""
        return parse_json_content(content)
    except Exception as e:
        logger.error(f"Ошибка при ранжировании документов: {e}")
        return {"best_match_id": None, "explanation": "Произошла ошибка при обработке запроса."}


@router.message(StateFilter(None))
async def handle_search(message: types.Message, bot: Bot) -> None:
    """Обрабатывает текстовые запросы для поиска документов."""
    if not message.text or message.text.startswith("/"):
        return

    query = message.text
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        search_results = await vector_search(query, limit=5)
        rerank_result = await rerank_documents(query, search_results)

        best_match_id = rerank_result.get("best_match_id")
        explanation = rerank_result.get("explanation", "")

        if not best_match_id:
            await message.answer(explanation or "Ничего не найдено. Попробуйте уточнить запрос.")
            return

        matched_doc = next((d for d in search_results if d["id"] == best_match_id), None)
        if not matched_doc:
            await message.answer("Произошла ошибка при сопоставлении документа.")
            return

        local_path = matched_doc["local_path"]
        gdrive_link = matched_doc["gdrive_link"]
        filename = matched_doc["saved_filename"]

        caption = (
            f"🔍 Найден документ:\n"
            f"📄 {filename}\n"
            f"📁 Категория: {matched_doc['category']}\n"
            f"👤 Владелец: {matched_doc['owner']}\n\n"
            f"{explanation}\n"
        )
        if gdrive_link:
            caption += f"\n☁️ [Открыть в Google Drive]({gdrive_link})"

        from pathlib import Path

        doc_path = Path(local_path)
        if doc_path.exists():
            file_input = types.FSInputFile(str(doc_path), filename=filename)
            await message.answer_document(file_input, caption=caption, parse_mode="Markdown")
        else:
            await message.answer(
                f"{caption}\n\n⚠️ Локальный файл не найден на сервере.",
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )

    except Exception as e:
        logger.error(f"Ошибка при поиске документов: {e}")
        await message.answer("Произошла ошибка при поиске. Пожалуйста, попробуйте позже.")
