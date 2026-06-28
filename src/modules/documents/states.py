from aiogram.fsm.state import State, StatesGroup


class DocumentProcessingStates(StatesGroup):
    """Состояния процесса обработки документов."""

    waiting_file = State()
    confirming = State()
    waiting_query = State()


class InboxStates(StatesGroup):
    """Состояния обработки файлов из inbox-папки."""

    processing = State()
    confirming = State()


class BatchStates(StatesGroup):
    """Состояния пакетной загрузки однофайловых документов."""

    collecting = State()  # Сбор файлов от пользователя
    confirming = State()  # Подтверждение/редактирование текущего черновика
