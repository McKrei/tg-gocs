"""Обработчик команды /batch: пакетная загрузка нескольких документов поштучно с prefetch-оптимизацией."""

import asyncio
import html
import time
from pathlib import Path
from typing import Any

from aiogram import Bot, F, Router, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from src.core.config import settings
from src.core.db.engine import async_session
from src.core.llm.embeddings import get_embedding
from src.core.utils.logger import get_logger
from src.modules.documents.agent import classify_document, normalize_draft_metadata
from src.modules.documents.handlers.callbacks import _delete_old_document, _format_gdrive_result
from src.modules.documents.handlers.files import _cleanup_files, rate_limits
from src.modules.documents.keyboards import (
    get_batch_confirmation_keyboard,
    get_batch_duplicate_keyboard,
    get_batch_start_keyboard,
)
from src.modules.documents.repository import DocumentRepository
from src.modules.documents.states import BatchStates
from src.modules.documents.tools import (
    find_similar_document,
    get_existing_structure,
    get_flat_directory_list,
    get_unique_filename,
    save_to_local_and_drive,
)

router = Router()
logger = get_logger(__name__)

# Словарь для хранения фоновых задач префетча: user_id -> (file_path, task)
prefetch_tasks: dict[int, tuple[str, asyncio.Task[tuple[dict[str, Any], dict[str, Any] | None]]]] = {}


def _h(text: Any) -> str:
    """Экранирует спецсимволы HTML."""
    return html.escape(str(text))


async def _analyze_file(
    file_path: str, suffix: str, existing_paths: list[str]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Выполняет классификацию документа и поиск дубликатов."""
    try:
        structure_info = await get_existing_structure()
        draft = await classify_document(file_path, classify_only=True, structure_info=structure_info)
        draft = normalize_draft_metadata(draft, suffix, existing_paths=existing_paths)
        similar_doc = await find_similar_document(draft["category"], draft["suggested_filename"], draft["summary"])
        return draft, similar_doc
    except Exception as e:
        logger.error(f"Ошибка фонового анализа файла {file_path}: {e}")
        return {
            "category": "Нераспознано",
            "suggested_filename": Path(file_path).name,
            "summary": f"Не удалось проанализировать документ. Ошибка: {e}",
            "owner": "Неизвестно",
        }, None


async def _get_existing_paths() -> list[str]:
    """Возвращает все существующие пути категорий."""
    flat_dirs = get_flat_directory_list()
    categories = []
    try:
        async with async_session() as session:
            repo = DocumentRepository(session)
            stats = await repo.get_stats_by_category()
            categories = [cat for cat, _ in stats]
    except Exception:
        pass
    return list(set(flat_dirs + categories))


async def _persist_batch_document(
    draft: dict[str, Any],
    save_result: dict[str, Any],
    suggested_filename: str,
    embedding: Any,
) -> None:
    """Сохраняет документ и эмбеддинг в БД, создает PendingUpload при ошибке."""
    async with async_session() as session:
        repo = DocumentRepository(session)
        doc = await repo.add_document(
            saved_filename=suggested_filename,
            local_path=save_result["local_path"] or f"{draft['category']}/{suggested_filename}",
            category=draft["category"],
            owner=draft["owner"],
            summary=draft["summary"],
            embedding=embedding,
            gdrive_link=save_result["gdrive_link"],
        )

        if save_result.get("gdrive_error"):
            await repo.add_pending_upload(
                local_path=save_result["local_path"] or f"{draft['category']}/{suggested_filename}",
                target_path=f"{draft['category']}/{suggested_filename}",
                document_id=doc.id,
            )

        await session.commit()


def _format_batch_draft_message(
    draft: dict[str, Any], file_path: str, similar_doc: dict[str, Any] | None, remaining: int
) -> tuple[str, types.InlineKeyboardMarkup]:
    """Форматирует сообщение с черновиком для пакетной обработки."""
    filename = Path(file_path).name
    counter_line = f"📦 Осталось обработать файлов: <b>{remaining}</b>\n\n"
    file_line = f"📄 Файл: <b>{_h(filename)}</b>\n\n"
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
        keyboard = get_batch_duplicate_keyboard()
    else:
        text = counter_line + file_line + meta
        keyboard = get_batch_confirmation_keyboard()

    return text, keyboard


async def _show_next_batch_file(message: types.Message, state: FSMContext) -> None:
    """Показывает следующий файл в очереди, используя prefetch-результат при наличии."""
    data = await state.get_data()
    queue: list[str] = data.get("batch_queue", [])
    user_id = message.chat.id

    if not queue:
        # Убираем возможную задачу из prefetch
        prefetch_tasks.pop(user_id, None)
        await state.clear()
        await message.answer("✅ Все файлы успешно обработаны!")
        return

    current_file_path = queue[0]
    remaining = len(queue)

    status_msg = await message.answer(f"⏳ Анализирую файл {Path(current_file_path).name}...")

    draft: dict[str, Any]
    similar_doc: dict[str, Any] | None = None

    existing_paths = await _get_existing_paths()

    # Проверяем, есть ли уже готовый префетч для этого файла
    if user_id in prefetch_tasks and prefetch_tasks[user_id][0] == current_file_path:
        logger.info(f"Используем prefetch-результат для файла: {current_file_path}")
        _, task = prefetch_tasks.pop(user_id)
        try:
            draft, similar_doc = await task
        except Exception as e:
            logger.error(f"Ошибка при получении prefetch-результата: {e}")
            draft, similar_doc = await _analyze_file(current_file_path, Path(current_file_path).suffix, existing_paths)
    else:
        logger.info(f"Синхронный анализ файла (prefetch отсутствует): {current_file_path}")
        draft, similar_doc = await _analyze_file(current_file_path, Path(current_file_path).suffix, existing_paths)

    # Запускаем prefetch для СЛЕДУЮЩЕГО файла в очереди, если он есть
    if len(queue) > 1:
        next_file_path = queue[1]
        logger.info(f"Запуск фонового prefetch для следующего файла: {next_file_path}")
        task = asyncio.create_task(
            _analyze_file(next_file_path, Path(next_file_path).suffix, existing_paths)
        )
        prefetch_tasks[user_id] = (next_file_path, task)

    duplicate_id = str(similar_doc["id"]) if similar_doc else None

    await state.set_state(BatchStates.confirming)
    await state.update_data(
        batch_queue=queue,
        current_draft=draft,
        current_file_path=current_file_path,
        duplicate_id=duplicate_id,
        current_msg_id=status_msg.message_id,
    )

    text, keyboard = _format_batch_draft_message(draft, current_file_path, similar_doc, remaining)
    await status_msg.edit_text(text, reply_markup=keyboard, parse_mode="HTML")


async def process_batch_incoming_file(
    message: types.Message,
    bot: Bot,
    state: FSMContext,
    file_id: str,
    file_size: int,
    original_name: str,
    file_ext: str,
) -> None:
    """Сохраняет файл для пакетного режима."""
    max_bytes = settings.storage.max_file_size_mb * 1024 * 1024
    if file_size > max_bytes:
        await message.answer(f"Файл слишком большой. Максимальный размер: {settings.storage.max_file_size_mb} МБ.")
        return

    user_id = message.from_user.id if message.from_user else 0
    if user_id:
        current_time = time.time()
        user_requests = rate_limits.setdefault(user_id, [])
        rate_limits[user_id] = [t for t in user_requests if current_time - t < 60]
        if len(rate_limits[user_id]) >= settings.storage.rate_limit_per_minute:
            await message.answer("Вы отправляете файлы слишком часто. Пожалуйста, подождите немного.")
            return
        rate_limits[user_id].append(current_time)

    temp_dir = Path(settings.storage.temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"{file_id}{file_ext}"

    await bot.download(file_id, destination=temp_path)
    logger.info(f"Сохранен временный файл для batch: {temp_path.name} ({file_size} B)")

    if file_ext.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        from src.core.utils.image import compress_image
        compressed_temp_path = temp_path.with_suffix(".jpg")
        ok = await compress_image(temp_path, compressed_temp_path)
        if ok:
            if temp_path != compressed_temp_path:
                temp_path.unlink(missing_ok=True)
            temp_path = compressed_temp_path
            file_ext = ".jpg"

    data = await state.get_data()
    files = data.get("batch_queue", [])

    # Ограничение на 20 файлов в пакете
    if len(files) >= 20:
        await message.answer("Достигнут лимит пакета (максимум 20 файлов).")
        temp_path.unlink(missing_ok=True)
        return

    files.append(str(temp_path))

    await state.update_data(
        batch_queue=files,
        last_activity=time.time(),
    )

    msg_id = data.get("msg_id")
    reply_markup = get_batch_start_keyboard()

    n = len(files)
    if n % 10 == 1 and n % 100 != 11:
        file_word = "файл"
    elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        file_word = "файла"
    else:
        file_word = "файлов"

    text = (
        f"📦 Пакетная загрузка: получено {n} {file_word}.\n\n"
        f"Каждый файл будет обработан отдельно (без склейки в PDF).\n\n"
        f"Отправьте ещё файлы или нажмите кнопку ниже, чтобы начать."
    )

    if not msg_id:
        status_msg = await message.answer(text, reply_markup=reply_markup)
        await state.update_data(msg_id=status_msg.message_id)
    else:
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=msg_id,
                text=text,
                reply_markup=reply_markup,
            )
        except Exception as e:
            logger.warning(f"Не удалось обновить сообщение о получении batch файлов: {e}")
            status_msg = await message.answer(text, reply_markup=reply_markup)
            await state.update_data(msg_id=status_msg.message_id)


# Хэндлеры для приёма файлов в состоянии BatchStates.collecting
@router.message(StateFilter(BatchStates.collecting), F.photo)
async def handle_batch_photo(message: types.Message, bot: Bot, state: FSMContext) -> None:
    """Обрабатывает входящие фотографии для пакета."""
    if not message.photo:
        return
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    file_ext = Path(file_info.file_path or "photo.jpg").suffix or ".jpg"

    await process_batch_incoming_file(
        message=message,
        bot=bot,
        state=state,
        file_id=photo.file_id,
        file_size=photo.file_size or 0,
        original_name="photo.jpg",
        file_ext=file_ext,
    )


@router.message(StateFilter(BatchStates.collecting), F.document)
async def handle_batch_document(message: types.Message, bot: Bot, state: FSMContext) -> None:
    """Обрабатывает входящие файлы для пакета."""
    doc = message.document
    if not doc:
        return

    mime = doc.mime_type or ""
    file_ext = Path(doc.file_name or "").suffix.lower()

    is_valid = mime.startswith("image/") or mime == "application/pdf" or file_ext in [".pdf", ".jpg", ".jpeg", ".png"]
    if not is_valid:
        await message.answer("Пожалуйста, отправьте документ в формате PDF или изображение (JPEG, PNG).")
        return

    await process_batch_incoming_file(
        message=message,
        bot=bot,
        state=state,
        file_id=doc.file_id,
        file_size=doc.file_size or 0,
        original_name=doc.file_name or "document.pdf",
        file_ext=file_ext,
    )


# Callbacks для управления конвейером
@router.callback_query(F.data == "batch_start", BatchStates.collecting)
async def handle_batch_start(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Запускает конвейер обработки очереди."""
    data = await state.get_data()
    queue = data.get("batch_queue", [])

    if not queue:
        await callback.answer("Ошибка: файлы не найдены.", show_alert=True)
        await state.clear()
        return

    await callback.answer("Начинаю обработку пакета...")
    if isinstance(callback.message, types.Message):
        await callback.message.edit_reply_markup(reply_markup=None)
        await _show_next_batch_file(callback.message, state)
    else:
        await callback.answer("Ошибка: сообщение недоступно.", show_alert=True)


@router.callback_query(F.data == "batch_save", BatchStates.confirming)
async def handle_batch_save(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Сохраняет текущий документ из пакета."""
    data = await state.get_data()
    draft = data.get("current_draft", {})
    file_path = data.get("current_file_path")
    queue = data.get("batch_queue", [])

    if not file_path or not draft:
        await callback.answer("Ошибка сохранения: сессия устарела.", show_alert=True)
        await state.clear()
        return

    if isinstance(callback.message, types.Message):
        await callback.message.edit_text("⏳ Сохраняю...", reply_markup=None)

    await callback.answer("Сохраняю...")

    category = draft["category"]
    suggested_filename = get_unique_filename(category, draft["suggested_filename"])
    target_path = f"{category}/{suggested_filename}"

    try:
        # Запускаем сохранение в Drive и получение эмбеддинга параллельно
        save_result, embedding = await asyncio.gather(
            save_to_local_and_drive(file_path, target_path),
            get_embedding(draft["summary"])
        )
        await _persist_batch_document(draft, save_result, suggested_filename, embedding)

        gdrive_text = _format_gdrive_result(save_result, target_path)
        if isinstance(callback.message, types.Message):
            await callback.message.edit_text(f"✅ Документ сохранен!\n\n{gdrive_text}", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка сохранения файла в batch {file_path}: {e}")
        if isinstance(callback.message, types.Message):
            await callback.message.edit_text(f"❌ Ошибка при сохранении: {e}")
    finally:
        # Удаляем временный файл
        Path(file_path).unlink(missing_ok=True)

    new_queue = queue[1:]
    await state.update_data(batch_queue=new_queue)
    if isinstance(callback.message, types.Message):
        await _show_next_batch_file(callback.message, state)


@router.callback_query(F.data == "batch_replace", BatchStates.confirming)
async def handle_batch_replace(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Заменяет существующий документ новым в пакете."""
    data = await state.get_data()
    draft = data.get("current_draft", {})
    file_path = data.get("current_file_path")
    duplicate_id = data.get("duplicate_id")
    queue = data.get("batch_queue", [])

    if not file_path or not draft or not duplicate_id:
        await callback.answer("Ошибка замены: сессия устарела.", show_alert=True)
        await state.clear()
        return

    if isinstance(callback.message, types.Message):
        await callback.message.edit_text("⏳ Заменяю...", reply_markup=None)

    await callback.answer("Заменяю...")

    # Удаляем старый документ
    await _delete_old_document(duplicate_id)

    category = draft["category"]
    suggested_filename = draft["suggested_filename"]
    target_path = f"{category}/{suggested_filename}"

    try:
        save_result, embedding = await asyncio.gather(
            save_to_local_and_drive(file_path, target_path),
            get_embedding(draft["summary"])
        )
        await _persist_batch_document(draft, save_result, suggested_filename, embedding)

        gdrive_text = _format_gdrive_result(save_result, target_path)
        if isinstance(callback.message, types.Message):
            await callback.message.edit_text(f"✅ Заменено!\n\n{gdrive_text}", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка замены файла в batch {file_path}: {e}")
        if isinstance(callback.message, types.Message):
            await callback.message.edit_text(f"❌ Ошибка при замене: {e}")
    finally:
        Path(file_path).unlink(missing_ok=True)

    new_queue = queue[1:]
    await state.update_data(batch_queue=new_queue)
    if isinstance(callback.message, types.Message):
        await _show_next_batch_file(callback.message, state)


@router.callback_query(F.data == "batch_skip", BatchStates.confirming)
async def handle_batch_skip(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Пропускает текущий файл в пакете."""
    data = await state.get_data()
    file_path = data.get("current_file_path")
    queue = data.get("batch_queue", [])

    await callback.answer("Пропущено.")

    if file_path:
        filename = Path(file_path).name
        if isinstance(callback.message, types.Message):
            await callback.message.edit_text(
                f"Пропущен файл: <b>{_h(filename)}</b>",
                parse_mode="HTML",
                reply_markup=None,
            )
        Path(file_path).unlink(missing_ok=True)

    new_queue = queue[1:]
    await state.update_data(batch_queue=new_queue)
    if isinstance(callback.message, types.Message):
        await _show_next_batch_file(callback.message, state)


@router.callback_query(F.data == "batch_stop")
async def handle_batch_stop(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Останавливает обработку пакета и чистит все временные файлы."""
    data = await state.get_data()
    queue = data.get("batch_queue", [])
    user_id = callback.message.chat.id if callback.message else 0

    # Отменяем фоновую задачу префетча
    if user_id in prefetch_tasks:
        _, task = prefetch_tasks.pop(user_id)
        task.cancel()

    _cleanup_files(queue)
    await state.clear()
    await callback.answer("Пакетная обработка остановлена.")
    if isinstance(callback.message, types.Message):
        await callback.message.edit_text("🛑 Пакетная обработка остановлена. Временные файлы удалены.")


# Поддержка текстового редактирования черновика
@router.message(StateFilter(BatchStates.confirming))
async def handle_batch_text_refine(message: types.Message, state: FSMContext) -> None:
    """Корректирует черновик по текстовому описанию пользователя."""
    data = await state.get_data()
    draft = data.get("current_draft")
    file_path = data.get("current_file_path")
    queue = data.get("batch_queue", [])
    msg_id = data.get("current_msg_id")

    if not draft or not file_path:
        return

    status_msg = await message.answer("🔄 Обновляю черновик...")

    from src.modules.documents.services.refiner import refine_draft
    try:
        feedback = message.text or ""
        updated_draft = await refine_draft(draft, feedback)
        
        # Пересчитываем дубликат с новыми параметрами
        similar_doc = await find_similar_document(
            updated_draft["category"], updated_draft["suggested_filename"], updated_draft["summary"]
        )
        duplicate_id = str(similar_doc["id"]) if similar_doc else None

        await state.update_data(
            current_draft=updated_draft,
            duplicate_id=duplicate_id,
        )

        text, keyboard = _format_batch_draft_message(updated_draft, file_path, similar_doc, len(queue))
        
        # Обновляем старое сообщение с черновиком
        if msg_id and message.bot:
            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=msg_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )
            except Exception:
                # Если сообщение не редактируется, отправим новое
                new_msg = await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
                await state.update_data(current_msg_id=new_msg.message_id)
        else:
            new_msg = await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
            await state.update_data(current_msg_id=new_msg.message_id)

    except Exception as e:
        logger.error(f"Ошибка при уточнении черновика в batch: {e}")
        await message.answer("❌ Не удалось обновить черновик.")
    finally:
        await status_msg.delete()
