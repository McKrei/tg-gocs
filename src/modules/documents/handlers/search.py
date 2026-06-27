import html
import json
from pathlib import Path
from typing import Any

from aiogram import Bot, Router, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from src.core.config import settings
from src.core.llm.client import get_llm_client
from src.core.utils.logger import get_logger
from src.core.utils.retry import with_retry
from src.modules.documents.agent import parse_json_content
from src.modules.documents.states import DocumentProcessingStates
from src.modules.documents.tools import vector_search

router = Router()
logger = get_logger(__name__)


@with_retry(attempts=3, initial_delay=1.0)
async def _rerank_documents(query: str, documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Выполняет фильтрацию и выбор подходящих документов с помощью LLM (Этап 1)."""
    if not documents:
        return {"matching_doc_ids": []}

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
        "Тебе предоставлен поисковый запрос пользователя и список документов, "
        "найденных по векторному сходству.\n"
        "Твоя задача — определить, какие из найденных документов действительно "
        "соответствуют запросу пользователя.\n\n"
        "ПРАВИЛА ОТБОРА:\n"
        "1. Проанализируй имена файлов и краткие описания (summary).\n"
        "2. Выбери только те документы, которые прямо относятся к запросу. "
        "Если пользователь ищет 'все консультации кардиолога', выбери все консультации кардиолога "
        "независимо от их даты. Если пользователь ищет конкретный документ, выбери только его.\n"
        "3. Верни список ID документов в порядке релевантности.\n\n"
        "Результат — СТРОГО JSON со следующим ключом:\n"
        "- matching_doc_ids: список строк (UUID) или пустой список\n\n"
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
        logger.error(f"Ошибка при фильтрации документов: {e}")
        return {"matching_doc_ids": []}


@with_retry(attempts=3, initial_delay=1.0)
async def _generate_search_explanation(query: str, matched_documents: list[dict[str, Any]]) -> str:
    """Генерирует финальный текстовый ответ пользователю на основе верифицированных документов (Этап 2)."""
    if not matched_documents:
        return "Документы не найдены."

    client = get_llm_client()
    docs_subset = [
        {
            "saved_filename": doc["saved_filename"],
            "category": doc["category"],
            "owner": doc["owner"],
            "summary": doc["summary"],
        }
        for doc in matched_documents
    ]

    prompt = (
        "Ты — интеллектуальный ассистент по поиску семейных документов.\n"
        "Пользователь искал документы по запросу.\n"
        "Мы выполнили поиск и нашли следующие подтвержденные (верифицированные) документы:\n\n"
        f"{json.dumps(docs_subset, ensure_ascii=False)}\n\n"
        f"Запрос пользователя: \"{query}\"\n\n"
        "Твоя задача — сформулировать вежливый и понятный ответ для пользователя на русском языке.\n"
        "В ответе:\n"
        "1. Укажи, сколько документов найдено (например: 'Найдено 3 документа...').\n"
        "2. Перечисли найденные документы (по именам/датам) и кратко расскажи, что представляет каждый из них.\n"
        "3. Если документов несколько, опиши их хронологию или ключевые отличия "
        "(например, разные даты консультаций).\n"
        "Отвечай кратко, информативно и по делу, на русском языке."
    )

    try:
        response = await client.chat.completions.create(
            model=settings.llm.model_name,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.error(f"Ошибка при генерации объяснения поиска: {e}")
        return f"Найдено {len(matched_documents)} док. (ошибка генерации описания)."


async def _do_search(query: str, message: types.Message) -> None:
    """Выполняет векторный поиск и отправляет результат пользователю."""
    bot: Bot = message.bot  # type: ignore[assignment]
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        search_results = await vector_search(query, limit=settings.llm.search_limit)
        
        # Отсекаем по порогу расстояния
        filtered_results = [
            doc for doc in search_results
            if doc.get("distance", 1.0) <= settings.llm.search_threshold
        ]

        rerank_result = await _rerank_documents(query, filtered_results)

        matching_ids = rerank_result.get("matching_doc_ids")
        if matching_ids is None:
            # Обратная совместимость для старого формата
            best_id = rerank_result.get("best_match_id")
            matching_ids = [best_id] if best_id else []

        if not matching_ids:
            await message.answer("Ничего не найдено. Попробуйте уточнить запрос.")
            return

        # Находим подходящие документы и сортируем их хронологически по имени файла
        matched_docs = [d for d in filtered_results if d["id"] in matching_ids]
        matched_docs.sort(key=lambda d: d.get("saved_filename", ""))

        # Получаем финальный ответ от модели по найденным документам
        explanation = await _generate_search_explanation(query, matched_docs)

        # Отправляем сообщение с общим пояснением от ассистента
        escaped_query = html.escape(query)
        escaped_explanation = html.escape(explanation)
        await message.answer(
            f"🔍 <b>Результаты поиска по запросу:</b> \"{escaped_query}\"\n\n{escaped_explanation}",
            parse_mode="HTML",
        )

        for idx, doc in enumerate(matched_docs, 1):
            filename = doc["saved_filename"]
            local_path = doc["local_path"]
            gdrive_link = doc["gdrive_link"]
            category = doc["category"]
            owner = doc["owner"]

            caption = (
                f"📄 <b>Документ {idx} из {len(matched_docs)}:</b>\n"
                f"📝 Имя: <code>{html.escape(filename)}</code>\n"
                f"📁 Категория: <code>{html.escape(category)}</code>\n"
                f"👤 Владелец: <code>{html.escape(owner)}</code>"
            )
            if gdrive_link:
                caption += f"\n☁️ <a href=\"{gdrive_link}\">Открыть в Google Drive</a>"

            doc_path = Path(local_path)
            if doc_path.exists():
                file_input = types.FSInputFile(str(doc_path), filename=filename)
                await message.answer_document(file_input, caption=caption, parse_mode="HTML")
            else:
                await message.answer(
                    f"{caption}\n\n⚠️ Локальный файл не найден на сервере.",
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )

    except Exception as e:
        logger.error(f"Ошибка при поиске документов: {e}")
        await message.answer("Произошла ошибка при поиске. Пожалуйста, попробуйте позже.")


@router.message(StateFilter(DocumentProcessingStates.waiting_query))
async def handle_search_query(message: types.Message, state: FSMContext) -> None:
    """Обрабатывает текстовый запрос в режиме поиска."""
    if not message.text or message.text.startswith("/"):
        await message.answer("Пожалуйста, напишите текстовый запрос для поиска.")
        return

    await state.clear()
    await _do_search(message.text, message)
