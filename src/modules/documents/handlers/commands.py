from aiogram import Router, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from sqlalchemy import func, select

from src.core.db.engine import async_session
from src.modules.documents.models import Document
from src.modules.documents.repository import DocumentRepository
from src.modules.documents.states import BatchStates, DocumentProcessingStates

router = Router()


@router.message(CommandStart())
async def cmd_start(message: types.Message) -> None:
    await message.answer(
        "👋 Привет! Я бот для управления семейными документами.\n\n"
        "📎 /add — добавить документ (с автосклеиванием)\n"
        "📦 /batch — пакетная загрузка нескольких документов поштучно\n"
        "🔍 /search — найти документ\n"
        "📋 /list — последние 10 документов\n"
        "📊 /stats — статистика по категориям\n"
        "📥 /inbox — обработать новые файлы из inbox\n"
        "🔄 /sync — синхронизировать Drive с базой данных\n"
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


@router.message(Command("batch"))
async def cmd_batch(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(BatchStates.collecting)
    await message.answer(
        "📦 Пакетная загрузка документов.\n\n"
        "Отправьте по очереди несколько файлов (каждый файл будет сохранен как отдельный документ).\n"
        "Затем нажмите кнопку «🚀 Начать обработку».\n\n"
        "Для отмены — /cancel"
    )


@router.message(Command("search"))
async def cmd_search(message: types.Message, state: FSMContext) -> None:
    from src.modules.documents.handlers.search import _do_search

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
        "📎 *Добавление документа (со склейкой):*\n"
        "Введите /add — бот перейдёт в режим ожидания файлов.\n"
        "Отправьте одно или несколько фото/PDF. Они будут склеены в один PDF-документ.\n\n"
        "📦 *Пакетная загрузка (поштучно):*\n"
        "Введите /batch — бот перейдёт в режим сбора файлов.\n"
        "Отправьте несколько файлов по очереди. Каждый файл будет сохранен как отдельный документ.\n\n"
        "🔍 *Поиск документа:*\n"
        "/search — бот попросит написать запрос.\n"
        "/search паспорт мужа — поиск сразу с запросом.\n\n"
        "📥 *Inbox:*\n"
        "/inbox — обработать файлы из inbox-папки Google Drive по одному.\n\n"
        "🔄 *Синхронизация:*\n"
        "/sync — рекурсивно обойти Google Drive и проиндексировать\n"
        "все новые файлы, которых ещё нет в базе данных.\n\n"
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

    from src.core.drive.uploader import find_folder_by_path

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


@router.message(Command("sync"))
async def cmd_sync(message: types.Message) -> None:
    """Синхронизирует Google Drive с базой данных."""
    from src.modules.documents.services.sync import sync_drive_to_db

    status = await message.answer("🔄 Запускаю синхронизацию Google Drive → БД...\nЭто может занять несколько минут.")
    try:
        result = await sync_drive_to_db()
        parts = ["✅ Синхронизация завершена!\n"]
        parts.append(f"📥 Добавлено: *{result.added}*")
        parts.append(f"⏩ Пропущено (уже в БД): *{result.skipped}*")
        parts.append(f"🗑️ Удалено неактуальных: *{result.removed}*")
        if result.errors:
            parts.append(f"❌ Ошибок: *{result.errors}*")
            if result.error_details:
                details = "\n".join(f"  • {d}" for d in result.error_details[:5])
                parts.append(f"Детали ошибок:\n{details}")
        await status.edit_text("\n".join(parts), parse_mode="Markdown")
    except Exception as e:
        await status.edit_text(f"❌ Ошибка синхронизации: {e}")


@router.message(Command("inbox"))
async def cmd_inbox(message: types.Message, state: FSMContext) -> None:
    """Запускает обработку файлов из inbox-папки."""
    from src.modules.documents.handlers.inbox import start_inbox_processing

    await state.clear()
    await start_inbox_processing(message, state)
