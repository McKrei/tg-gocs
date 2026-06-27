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
        "Ты — строгий фильтр документов.\n"
        "Тебе предоставлен поисковый запрос пользователя и список документов-кандидатов.\n\n"
        "ПРАВИЛА ОТБОРА — СТРОГО:\n"
        "1. Выбирай ТОЛЬКО документы, которые ПРЯМО и ТОЧНО соответствуют запросу.\n"
        "2. НЕ включай документы, которые лишь косвенно связаны с темой запроса.\n"
        "   Пример: если пользователь просит 'консультации кардиолога', НЕ включай\n"
        "   результаты анализов, мониторирования ЭКГ или других процедур — только сами консультации.\n"
        "3. Если пользователь просит 'все [тип документа]', включай ВСЕ документы этого типа.\n"
        "4. Верни список ID в порядке хронологии (по имени файла).\n\n"
        "Результат — СТРОГО JSON с единственным ключом:\n"
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
        "Мы нашли следующие подтверждённые документы:\n\n"
        f"{json.dumps(docs_subset, ensure_ascii=False)}\n\n"
        f'Запрос пользователя: "{query}"\n\n'
        "Сформулируй короткий и понятный ответ на русском языке:\n"
        "1. Скажи, сколько документов найдено.\n"
        "2. Для каждого документа — одна строка: дата и краткая суть.\n"
        "3. Если несколько — выдели ключевые отличия между ними.\n"
        "Пиши лаконично, без воды."
    )

    try:
        response = await client.chat.completions.create(
            model=settings.llm.model_name,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.error(f"Ошибка при генерации объяснения поиска: {e}")
        return f"Найдено {len(matched_documents)} документ(ов)."


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

        # Находим подходящие документы и сортируем хронологически по имени файла
        matched_docs = [d for d in filtered_results if d["id"] in matching_ids]
        matched_docs.sort(key=lambda d: d.get("saved_filename", ""))

        # Этап 2: получаем AI-объяснение по верифицированным документам
        explanation = await _generate_search_explanation(query, matched_docs)

        # Формируем список названий документов
        names_list = "\n".join(
            f"  {idx}. {html.escape(doc['saved_filename'])}"
            for idx, doc in enumerate(matched_docs, 1)
        )
        escaped_query = html.escape(query)
        escaped_explanation = html.escape(explanation)

        # Первое сообщение: AI-текст + список названий
        await message.answer(
            f"🔍 <b>Запрос:</b> \"{escaped_query}\"\n\n"
            f"📋 <b>Найденные документы:</b>\n{names_list}\n\n"
            f"{escaped_explanation}",
            parse_mode="HTML",
        )

        # Второе: все файлы как медиа-группа (один альбом) или одиночный документ
        existing_docs = [d for d in matched_docs if Path(d["local_path"]).exists()]
        missing_docs = [d for d in matched_docs if not Path(d["local_path"]).exists()]

        if existing_docs:
            if len(existing_docs) == 1:
                # Один документ — отправляем напрямую
                doc = existing_docs[0]
                gdrive_link = doc["gdrive_link"]
                caption = f"📄 <code>{html.escape(doc['saved_filename'])}</code>"
                if gdrive_link:
                    caption += f"\n☁️ <a href=\"{gdrive_link}\">Google Drive</a>"
                file_input = types.FSInputFile(str(doc["local_path"]), filename=doc["saved_filename"])
                await message.answer_document(file_input, caption=caption, parse_mode="HTML")
            else:
                # Несколько документов — медиа-группа (один альбом)
                media_items: list[
                    types.InputMediaAudio
                    | types.InputMediaDocument
                    | types.InputMediaLivePhoto
                    | types.InputMediaPhoto
                    | types.InputMediaVideo
                ] = []
                for idx, doc in enumerate(existing_docs):
                    gdrive_link = doc["gdrive_link"]
                    if idx == 0:
                        # Подпись только у первого элемента группы
                        first_gdrive = gdrive_link
                        cap_lines = "\n".join(
                            f"📄 {html.escape(d['saved_filename'])}"
                            for d in existing_docs
                        )
                        cap = cap_lines
                        if first_gdrive:
                            cap += f"\n\n☁️ <a href=\"{first_gdrive}\">Google Drive</a>"
                        media_items.append(
                            types.InputMediaDocument(
                                media=types.FSInputFile(
                                    str(doc["local_path"]),
                                    filename=doc["saved_filename"],
                                ),
                                caption=cap,
                                parse_mode="HTML",
                            )
                        )
                    else:
                        media_items.append(
                            types.InputMediaDocument(
                                media=types.FSInputFile(
                                    str(doc["local_path"]),
                                    filename=doc["saved_filename"],
                                ),
                            )
                        )
                await bot.send_media_group(chat_id=message.chat.id, media=media_items)

        # Сообщаем о файлах, которых нет локально
        for doc in missing_docs:
            gdrive_link = doc["gdrive_link"]
            text = f"⚠️ Файл <code>{html.escape(doc['saved_filename'])}</code> не найден локально."
            if gdrive_link:
                text += f"\n☁️ <a href=\"{gdrive_link}\">Открыть в Google Drive</a>"
            await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)

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
