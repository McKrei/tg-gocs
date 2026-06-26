from aiogram import Router, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from sqlalchemy import func, select

from src.bot.states import DocumentProcessingStates
from src.db.engine import async_session
from src.db.models import Document
from src.db.repository import DocumentRepository

router = Router()


@router.message(CommandStart())
async def cmd_start(message: types.Message) -> None:
    await message.answer(
        "👋 Привет! Я бот для управления семейными документами.\n\n"
        "📎 /add — добавить документ\n"
        "🔍 /search — найти документ\n"
        "📋 /list — последние 10 документов\n"
        "📊 /stats — статистика по категориям\n"
        "❓ /help — справка"
    )


@router.message(Command("add"))
async def cmd_add(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(DocumentProcessingStates.waiting_file)
    await message.answer(
        "📎 Отправьте фото или файл документа.\n\n"
        "Можно прислать несколько фото — они будут склеены в один PDF.\n"
        "Для отмены — /cancel"
    )


@router.message(Command("search"))
async def cmd_search(message: types.Message, state: FSMContext) -> None:
    from src.bot.handlers.search import _do_search

    parts = message.text.split(maxsplit=1) if message.text else []
    query = parts[1].strip() if len(parts) > 1 else ""
    if query:
        await state.clear()
        await _do_search(query, message)
    else:
        await state.clear()
        await state.set_state(DocumentProcessingStates.waiting_query)
        await message.answer("🔍 Что ищем? Напишите запрос:")


@router.message(Command("cancel"))
@router.message(Command("reset"))
async def cmd_cancel(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("✅ Действие отменено.")


@router.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    help_text = (
        "🤖 *Справка по командам бота:*\n\n"
        "📎 *Добавление документа:*\n"
        "Введите /add — бот перейдёт в режим ожидания файла.\n"
        "Отправьте одно или несколько фото/PDF. Бот проанализирует документ, "
        "предложит категорию, имя и описание. Можно уточнить текстом.\n\n"
        "🔍 *Поиск документа:*\n"
        "/search — бот попросит написать запрос.\n"
        "/search паспорт мужа — поиск сразу с запросом.\n\n"
        "📋 *Управление:*\n"
        "/list — список последних 10 документов\n"
        "/stats — статистика по категориям\n"
        "/cancel — отменить текущее действие"
    )
    await message.answer(help_text, parse_mode="Markdown")


@router.message(Command("list"))
async def cmd_list(message: types.Message) -> None:
    """Выводит список последних 10 документов с группировкой по категориям."""
    from collections import defaultdict

    from src.drive.uploader import find_folder_by_path

    async with async_session() as session:
        repo = DocumentRepository(session)
        docs = await repo.get_recent_documents(limit=10)

    if not docs:
        await message.answer("В базе данных пока нет сохраненных документов.")
        return

    unique_categories = list({doc.category for doc in docs})
    category_folders = {}
    for cat in unique_categories:
        folder_id = await find_folder_by_path(cat)
        if folder_id:
            category_folders[cat] = f"https://drive.google.com/drive/folders/{folder_id}"

    grouped = defaultdict(list)
    for doc in docs:
        grouped[doc.category].append(doc)

    text = "📋 *Последние документы по категориям:*\n\n"
    for category, category_docs in grouped.items():
        folder_link = category_folders.get(category)
        folder_md = f" [📂 открыть папку]({folder_link})" if folder_link else ""
        text += f"📁 *{category}*{folder_md}\n"

        for doc in category_docs:
            date_str = doc.created_at.strftime("%d.%m.%Y")
            gdrive_md = f" | [☁️ файл]({doc.gdrive_link})" if doc.gdrive_link else ""
            text += f"  • *{doc.saved_filename}* (📅 {date_str}){gdrive_md}\n"
        text += "\n"

    await message.answer(text, parse_mode="Markdown", disable_web_page_preview=True)


@router.message(Command("stats"))
async def cmd_stats(message: types.Message) -> None:
    async with async_session() as session:
        repo = DocumentRepository(session)
        stats = await repo.get_stats_by_category()

        total_stmt = select(func.count(Document.id))
        total_result = await session.execute(total_stmt)
        total_count = total_result.scalar_one()

    if total_count == 0:
        await message.answer("Статистика пуста. Документы пока не сохранены.")
        return

    text = "📊 *Статистика документов:*\n\n"
    text += f"Всего документов: *{total_count}*\n\n"
    text += "*По категориям:*\n"
    for category, count in stats:
        text += f"📁 {category}: *{count}*\n"
    await message.answer(text, parse_mode="Markdown")
