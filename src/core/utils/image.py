import asyncio
from pathlib import Path

from PIL import Image

from src.core.utils.logger import get_logger

logger = get_logger(__name__)


def compress_image_sync(
    input_path: Path,
    output_path: Path,
    max_width: int = 1600,
    max_height: int = 1200,
    quality: int = 80,
) -> bool:
    """Синхронно сжимает изображение до максимальных размеров с заданным качеством."""
    try:
        if not input_path.exists():
            return False

        with Image.open(input_path) as open_img:
            img: Image.Image = open_img
            # Предотвращаем сбои из-за EXIF ориентации
            try:
                from PIL import ImageOps
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass

            original_width, original_height = img.size

            # Вычисляем коэффициент масштабирования, если изображение больше лимита
            ratio = min(max_width / original_width, max_height / original_height)
            if ratio < 1.0:
                new_width = int(original_width * ratio)
                new_height = int(original_height * ratio)
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                logger.info(
                    f"Изображение сжато с размера {original_width}x{original_height} до {new_width}x{new_height}"
                )
            else:
                logger.info(
                    f"Изображение не требует изменения размеров: {original_width}x{original_height}"
                )

            # Конвертируем в RGB если нужно
            if img.mode != "RGB":
                img = img.convert("RGB")

            # Сохраняем сжатую копию
            img.save(output_path, "JPEG", quality=quality)
            return True
    except Exception as e:
        logger.error(f"Не удалось сжать изображение {input_path.name}: {e}")
        return False


async def compress_image(
    input_path: Path,
    output_path: Path,
    max_width: int = 1600,
    max_height: int = 1200,
    quality: int = 80,
) -> bool:
    """Асинхронно сжимает изображение, запуская Pillow в отдельном потоке."""
    return await asyncio.to_thread(
        compress_image_sync,
        input_path,
        output_path,
        max_width,
        max_height,
        quality,
    )
