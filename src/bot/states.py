from aiogram.fsm.state import State, StatesGroup


class DocumentProcessingStates(StatesGroup):
    """Состояния процесса обработки и подтверждения документа."""

    confirming = State()
