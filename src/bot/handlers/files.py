from pathlib import Path

from aiogram import Bot, F, Router, types
from aiogram.fsm.context import FSMContext

from src.config import settings
from src.utils.logger import get_logger

router = Router()
logger = get_logger(__name__)


@router.message(F.photo)
async def handle_photo(message: types.Message, bot: Bot, state: FSMContext) -> None:
    """Загружает полученное фото во временную папку."""
    if not message.photo:
        return
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)

    temp_dir = Path(settings.storage.temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    file_ext = Path(file_info.file_path or "photo.jpg").suffix or ".jpg"
    temp_path = temp_dir / f"{photo.file_id}{file_ext}"

    logger.info(f"Получено фото: file_id={photo.file_id}, size={photo.file_size} B")
    await bot.download(photo, destination=temp_path)

    await message.answer(
        f"Файл успешно получен и сохранен.\n"
        f"Имя: {temp_path.name}\n"
        f"(В дальнейшем здесь будет запускаться классификация)."
    )


@router.message(F.document)
async def handle_document(message: types.Message, bot: Bot, state: FSMContext) -> None:
    """Загружает полученный документ во временную папку."""
    doc = message.document
    if not doc:
        return

    mime = doc.mime_type or ""
    file_ext = Path(doc.file_name or "").suffix.lower()

    is_valid = (
        mime.startswith("image/")
        or mime == "application/pdf"
        or file_ext in [".pdf", ".jpg", ".jpeg", ".png"]
    )

    if not is_valid:
        await message.answer("Пожалуйста, отправьте документ в формате PDF или изображение (JPEG, PNG).")
        return

    temp_dir = Path(settings.storage.temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    safe_name = doc.file_name or f"{doc.file_id}{file_ext}"
    temp_path = temp_dir / f"{doc.file_id}_{safe_name}"

    logger.info(f"Получен документ: name={doc.file_name}, size={doc.file_size} B, mime={mime}")
    await bot.download(doc, destination=temp_path)

    await message.answer(
        f"Файл успешно получен и сохранен.\n"
        f"Имя: {temp_path.name}\n"
        f"(В дальнейшем здесь будет запускаться классификация)."
    )
