"""Обработчик inbox-флоу: анализ и сохранение файлов из inbox-папки Google Drive."""

import html
from typing import Any

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext

from src.core.utils.logger import get_logger
from src.modules.documents.agent import classify_document, normalize_draft_metadata
from src.modules.documents.handlers.callbacks import _delete_old_document, _format_gdrive_result
from src.modules.documents.keyboards import get_inbox_confirmation_keyboard, get_inbox_duplicate_keyboard
from src.modules.documents.services.sync import InboxFile, get_inbox_files, process_inbox_file
from src.modules.documents.states import InboxStates
from src.modules.documents.tools import find_similar_document, get_unique_filename

router = Router()
logger = get_logger(__name__)


def _h(text: str) -> str:
    """Экранирует HTML-спецсимволы в динамическом тексте."""
    return html.escape(str(text))


def _format_inbox_draft_message(
    draft: dict[str, Any], file_info: InboxFile, similar_doc: dict[str, Any] | None, remaining: int
) -> tuple[str, Any]:
    """Формирует HTML-сообщение с черновиком для inbox-файла и подходящую клавиатуру."""
    counter_line = f"📥 Файлов в очереди: <b>{remaining}</b>\n\n"
    file_line = f'📄 <b>{_h(file_info.name)}</b> (<a href="{_h(file_info.web_view_link)}">открыть в Drive</a>)\n\n'
    meta = (
        f"Предлагаемые метаданные:\n"
        f"📁 Категория: {_h(draft.get('category', '—'))}\n"
        f"👤 Владелец: {_h(draft.get('owner', '—'))}\n"
        f"📝 Имя файла: {_h(draft.get('suggested_filename', '—'))}\n"
        f"ℹ️ Описание: {_h(draft.get('summary', '—'))}\n"
    )

    if similar_doc:
        reason = (
            "найден файл с таким же именем и категорией"
            if similar_doc["reason"] == "exact_path"
            else f"найден похожий файл (похожесть {similar_doc.get('similarity_percent', '?')}%)"
        )
        dup_path = _h(f"{similar_doc['category']}/{similar_doc['saved_filename']}")
        warning = f"\n⚠️ <b>Внимание: {_h(reason)}!</b>\n📁 <code>{dup_path}</code>\n\n"
        text = counter_line + file_line + warning + meta
        keyboard = get_inbox_duplicate_keyboard()
    else:
        text = counter_line + file_line + meta
        keyboard = get_inbox_confirmation_keyboard()

    return text, keyboard


async def _show_next_inbox_file(message: types.Message, state: FSMContext) -> None:
    """Загружает следующий файл из inbox, классифицирует и показывает пользователю."""
    data = await state.get_data()
    queue: list[dict[str, Any]] = data.get("inbox_queue", [])

    if not queue:
        await state.clear()
        await message.answer("✅ Все файлы из inbox обработаны!")
        return

    file_data = queue[0]
    inbox_file = InboxFile(**file_data)
    remaining = len(queue)

    status_msg = await message.answer(f"⏳ Анализирую файл {inbox_file.name}...")

    try:
        from src.core.db.engine import async_session
        from src.modules.documents.repository import DocumentRepository
        from src.modules.documents.services.sync import _download_to_temp
        from src.modules.documents.tools import get_existing_structure, get_flat_directory_list

        temp_path = await _download_to_temp(inbox_file.drive_id, inbox_file.name, inbox_file.mime_type)
        structure_info = await get_existing_structure()
        draft = await classify_document(str(temp_path), classify_only=True, structure_info=structure_info)
        flat_dirs = get_flat_directory_list()
        categories = []
        try:
            async with async_session() as session:
                repo = DocumentRepository(session)
                stats = await repo.get_stats_by_category()
                categories = [cat for cat, _ in stats]
        except Exception:
            pass
        all_existing_paths = list(set(flat_dirs + categories))

        draft = normalize_draft_metadata(draft, temp_path.suffix, existing_paths=all_existing_paths)
        temp_path.unlink(missing_ok=True)
    except Exception as e:
        logger.error(f"Ошибка анализа inbox-файла {inbox_file.name}: {e}")
        draft = {
            "category": "Нераспознано",
            "suggested_filename": inbox_file.name,
            "summary": "Не удалось проанализировать документ.",
            "owner": "Неизвестно",
        }

    similar_doc = await find_similar_document(draft["category"], draft["suggested_filename"], draft["summary"])
    duplicate_id = str(similar_doc["id"]) if similar_doc else None

    await state.set_state(InboxStates.confirming)
    await state.update_data(
        inbox_queue=queue,
        current_draft=draft,
        current_file=file_data,
        duplicate_id=duplicate_id,
        current_msg_id=status_msg.message_id,
    )

    text, keyboard = _format_inbox_draft_message(draft, inbox_file, similar_doc, remaining)
    await status_msg.edit_text(text, reply_markup=keyboard, parse_mode="HTML")


async def start_inbox_processing(message: types.Message, state: FSMContext) -> None:
    """Запускает обработку inbox. Вызывается из commands.py."""
    from src.core.config import settings

    if not settings.storage.inbox_enabled:
        await message.answer("📭 Inbox-папка не настроена. Укажите GDRIVE_INBOX_FOLDER_ID в конфигурации.")
        return

    status = await message.answer("🔍 Проверяю inbox-папку в Google Drive...")

    try:
        inbox_files = await get_inbox_files()
    except Exception as e:
        logger.error(f"Ошибка получения списка inbox-файлов: {e}")
        await status.edit_text(f"❌ Не удалось получить список файлов: {e}")
        return

    if not inbox_files:
        await status.edit_text("📭 Inbox пуст — новых файлов нет.")
        return

    queue = [
        {
            "drive_id": f.drive_id,
            "name": f.name,
            "mime_type": f.mime_type,
            "web_view_link": f.web_view_link,
        }
        for f in inbox_files
    ]

    await state.set_state(InboxStates.processing)
    await state.update_data(inbox_queue=queue)
    await status.edit_text(
        f"📥 Найдено новых файлов в inbox: <b>{len(inbox_files)}</b>. Начинаю обработку...", parse_mode="HTML"
    )
    await _show_next_inbox_file(message, state)


@router.callback_query(F.data == "inbox_save", InboxStates.confirming)
async def handle_inbox_save(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Сохраняет inbox-файл в правильную категорию."""
    data = await state.get_data()
    draft = data.get("current_draft", {})
    file_data = data.get("current_file", {})
    queue = data.get("inbox_queue", [])

    if isinstance(callback.message, types.Message):
        await callback.message.edit_text("⏳ Сохраняю...", reply_markup=None)

    await callback.answer("Сохраняю файл...")

    inbox_file = InboxFile(**file_data)
    category = draft["category"]
    suggested_filename = get_unique_filename(category, draft["suggested_filename"])

    try:
        result = await process_inbox_file(
            inbox_file=inbox_file,
            target_category=category,
            target_filename=suggested_filename,
            summary=draft["summary"],
            owner=draft.get("owner", "Неизвестно"),
        )
        gdrive_text = _format_gdrive_result(result, f"{category}/{suggested_filename}")
        if isinstance(callback.message, types.Message):
            await callback.message.edit_text(f"✅ Сохранено!\n\n{gdrive_text}", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка сохранения inbox-файла {inbox_file.name}: {e}")
        if isinstance(callback.message, types.Message):
            await callback.message.edit_text(f"❌ Ошибка при сохранении: {e}")

    new_queue = queue[1:]
    await state.update_data(inbox_queue=new_queue)
    if isinstance(callback.message, types.Message):
        await _show_next_inbox_file(callback.message, state)


@router.callback_query(F.data == "inbox_replace", InboxStates.confirming)
async def handle_inbox_replace(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Заменяет существующий документ inbox-файлом."""
    data = await state.get_data()
    draft = data.get("current_draft", {})
    file_data = data.get("current_file", {})
    duplicate_id = data.get("duplicate_id")
    queue = data.get("inbox_queue", [])

    if isinstance(callback.message, types.Message):
        await callback.message.edit_text("⏳ Заменяю...", reply_markup=None)

    await callback.answer("Заменяю документ...")

    if duplicate_id:
        await _delete_old_document(duplicate_id)

    inbox_file = InboxFile(**file_data)
    category = draft["category"]
    suggested_filename = draft["suggested_filename"]

    try:
        result = await process_inbox_file(
            inbox_file=inbox_file,
            target_category=category,
            target_filename=suggested_filename,
            summary=draft["summary"],
            owner=draft.get("owner", "Неизвестно"),
        )
        gdrive_text = _format_gdrive_result(result, f"{category}/{suggested_filename}")
        if isinstance(callback.message, types.Message):
            await callback.message.edit_text(f"✅ Заменено!\n\n{gdrive_text}", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка замены inbox-файла {inbox_file.name}: {e}")
        if isinstance(callback.message, types.Message):
            await callback.message.edit_text(f"❌ Ошибка при замене: {e}")

    new_queue = queue[1:]
    await state.update_data(inbox_queue=new_queue)
    if isinstance(callback.message, types.Message):
        await _show_next_inbox_file(callback.message, state)


@router.callback_query(F.data == "inbox_skip", InboxStates.confirming)
async def handle_inbox_skip(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Пропускает текущий inbox-файл и удаляет его из папки inbox."""
    import asyncio

    from src.core.drive.client import delete_drive_file

    data = await state.get_data()
    queue = data.get("inbox_queue", [])
    current = InboxFile(**data.get("current_file", {})) if data.get("current_file") else None

    await callback.answer("Файл пропущен.")

    if current:
        if isinstance(callback.message, types.Message):
            await callback.message.edit_text(
                f"⏭️ Файл пропущен: <b>{_h(current.name)}</b>", parse_mode="HTML", reply_markup=None
            )
        logger.info(f"Inbox-файл пропущен и удаляется из Google Drive: {current.name} ({current.drive_id})")
        try:
            await asyncio.to_thread(delete_drive_file, current.drive_id)
        except Exception as e:
            logger.error(f"Не удалось удалить пропущенный файл {current.name} из Google Drive: {e}")
    else:
        logger.warning("Попытка пропустить файл, но current_file отсутствует в состоянии FSM.")
        if isinstance(callback.message, types.Message):
            await callback.message.edit_text("⏭️ Файл пропущен.", reply_markup=None)

    new_queue = queue[1:]
    await state.update_data(inbox_queue=new_queue)
    if isinstance(callback.message, types.Message):
        await _show_next_inbox_file(callback.message, state)


@router.callback_query(F.data == "inbox_stop")
async def handle_inbox_stop(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Останавливает обработку inbox."""
    await state.clear()
    await callback.answer("Обработка остановлена.")
    if isinstance(callback.message, types.Message):
        await callback.message.edit_text("🛑 Обработка inbox остановлена. Непросмотренные файлы остались в папке.")
