import logging
import sys

# Настраиваем базовое логирование для всего приложения
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)


def get_logger(name: str) -> logging.Logger:
    """Возвращает настроенный объект логгера для модуля."""
    return logging.getLogger(name)
