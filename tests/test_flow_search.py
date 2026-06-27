from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import types

from src.modules.documents.handlers.search import _do_search, _rerank_documents, handle_search_query


@pytest.mark.asyncio
async def test_rerank_documents_no_docs() -> None:
    """Проверяет реранкинг, если список документов пуст."""
    res = await _rerank_documents("запрос", [])
    assert res["best_match_id"] is None
    assert "не найдены" in res["explanation"].lower()


@pytest.mark.asyncio
async def test_rerank_documents_with_docs() -> None:
    """Проверяет реранкинг с использованием замоканного LLM клиента."""
    mock_docs = [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "saved_filename": "polis.pdf",
            "category": "Медицина",
            "owner": "Жена",
            "summary": "Медицинский полис жены",
        }
    ]

    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content='{"best_match_id": "11111111-1111-1111-1111-111111111111", "explanation": "Документ найден."}'
            )
        )
    ]

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch("src.modules.documents.handlers.search.get_llm_client", return_value=mock_client):
        res = await _rerank_documents("найди полис", mock_docs)
        assert res["best_match_id"] == "11111111-1111-1111-1111-111111111111"
        assert res["explanation"] == "Документ найден."


@pytest.mark.asyncio
async def test_handle_search_found(tmp_path) -> None:
    """Проверяет обработку поиска, когда документ успешно найден."""
    message = AsyncMock()
    message.text = "найди полис"
    message.chat.id = 123
    bot = AsyncMock()

    # Создаем временный файл, чтобы проверить, что он отправляется
    test_file = tmp_path / "polis.pdf"
    test_file.write_text("dummy pdf content")

    mock_search_results = [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "saved_filename": "polis.pdf",
            "local_path": str(test_file),
            "gdrive_link": "https://drive.google.com/test",
            "category": "Медицина",
            "owner": "Жена",
            "summary": "Медицинский полис жены",
        }
    ]

    mock_rerank = {
        "best_match_id": "11111111-1111-1111-1111-111111111111",
        "explanation": "Этот документ подходит.",
    }

    with (
        patch("src.modules.documents.handlers.search.vector_search", AsyncMock(return_value=mock_search_results)),
        patch("src.modules.documents.handlers.search._rerank_documents", AsyncMock(return_value=mock_rerank)),
    ):
        message.bot = bot
        await _do_search(message.text, message)

        # Проверяем, что отправлен документ
        message.answer_document.assert_called_once()
        args, kwargs = message.answer_document.call_args
        assert isinstance(args[0], types.FSInputFile)
        assert args[0].filename == "polis.pdf"
        assert "Медицина" in kwargs["caption"]
        assert "https://drive.google.com" in kwargs["caption"]


@pytest.mark.asyncio
async def test_handle_search_not_found() -> None:
    """Проверяет поведение поиска, если ничего не найдено."""
    message = AsyncMock()
    message.text = "привет"
    message.chat.id = 123
    bot = AsyncMock()

    mock_search_results = []
    mock_rerank = {
        "best_match_id": None,
        "explanation": "Ничего не найдено.",
    }

    with (
        patch("src.modules.documents.handlers.search.vector_search", AsyncMock(return_value=mock_search_results)),
        patch("src.modules.documents.handlers.search._rerank_documents", AsyncMock(return_value=mock_rerank)),
    ):
        message.bot = bot
        await _do_search(message.text, message)
        message.answer.assert_called_once_with("Ничего не найдено.")


@pytest.mark.asyncio
async def test_handle_search_query_clears_state_and_runs_search() -> None:
    """Проверяет поиск текстом после команды /search."""
    message = AsyncMock()
    message.text = "паспорт"
    state = AsyncMock()

    with patch("src.modules.documents.handlers.search._do_search", AsyncMock()) as mock_search:
        await handle_search_query(message, state)

    state.clear.assert_called_once()
    mock_search.assert_awaited_once_with("паспорт", message)


@pytest.mark.asyncio
async def test_handle_search_query_rejects_non_text() -> None:
    """Проверяет ответ, если после /search пришёл не текстовый запрос."""
    message = AsyncMock()
    message.text = None
    state = AsyncMock()

    await handle_search_query(message, state)

    state.clear.assert_not_called()
    message.answer.assert_called_once_with("Пожалуйста, напишите текстовый запрос для поиска.")
