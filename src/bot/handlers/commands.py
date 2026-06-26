from aiogram import Router, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from sqlalchemy import func, select

from src.db.engine import async_session
from src.db.models import Document
from src.db.repository import DocumentRepository

router = Router()


@router.message(CommandStart())
async def cmd_start(message: types.Message) -> None:
    """Приветственное сообщение для команды /start."""
    await message.answer(
        "Привет! Я бот для управления семейными документами.\n"
        "Отправь мне файл (изображение или PDF) для классификации."
    )


@router.message(Command("cancel"))
@router.message(Command("reset"))
async def cmd_cancel(message: types.Message, state: FSMContext) -> None:
    """Сбрасывает текущее состояние FSM."""
    await state.clear()
    await message.answer("Действие отменено.")


@router.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    """Отображает справку по использованию бота."""
    help_text = (
        "🤖 *Справка по командам бота:*\n\n"
        "📁 *Сохранение документов:*\n"
        "Просто отправьте мне одну или несколько фотографий/PDF-файлов. "
        "Я проанализирую их, предложу категорию, имя и описание, а затем сохраню.\n\n"
        "🔍 *Поиск документов:*\n"
        "Напишите мне обычным текстом, что вы ищете (например: \"найди паспорт мужа\").\n\n"
        "📋 *Команды управления:*\n"
        "/start — Начать работу с ботом\n"
        "/help — Показать эту справку\n"
        "/cancel, /reset — Отменить текущую сессию добавления файлов\n"
        "/list — Показать список последних 10 документов\n"
        "/stats — Показать статистику по категориям"
    )
    await message.answer(help_text, parse_mode="Markdown")


@router.message(Command("list"))
async def cmd_list(message: types.Message) -> None:
    """Выводит список последних 10 сохраненных документов."""
    async with async_session() as session:
        repo = DocumentRepository(session)
        docs = await repo.get_recent_documents(limit=10)

    if not docs:
        await message.answer("В базе данных пока нет сохраненных документов.")
        return

    text = "📋 *Последние 10 документов:*\n\n"
    for i, doc in enumerate(docs, 1):
        date_str = doc.created_at.strftime("%d.%m.%Y")
        text += f"{i}. *{doc.saved_filename}*\n   📁 {doc.category} | 📅 {date_str}\n"
        if doc.gdrive_link:
            text += f"   ☁️ [Google Drive]({doc.gdrive_link})\n"
        text += "\n"
    await message.answer(text, parse_mode="Markdown", disable_web_page_preview=True)


@router.message(Command("stats"))
async def cmd_stats(message: types.Message) -> None:
    """Выводит статистику документов по категориям."""
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
