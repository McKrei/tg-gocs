from aiogram.fsm.state import State, StatesGroup


class DocumentProcessingStates(StatesGroup):
    """Состояния процесса обработки документов."""

    waiting_file = State()
    confirming = State()
    waiting_query = State()
