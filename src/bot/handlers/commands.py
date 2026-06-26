from aiogram import Router, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext

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
