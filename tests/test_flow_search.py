from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import types

from src.modules.documents.handlers.search import _do_search, _rerank_documents, handle_search_query


@pytest.mark.asyncio
async def test_rerank_documents_no_docs() -> None:
    """Проверяет реранкинг, если список документов пуст."""
    res = await _rerank_documents("запрос", [])
    assert res["matching_doc_ids"] == []


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
        MagicMock(message=MagicMock(content='{"matching_doc_ids": ["11111111-1111-1111-1111-111111111111"]}'))
    ]

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

    with patch("src.modules.documents.handlers.search.get_llm_client", return_value=mock_client):
        res = await _rerank_documents("найди полис", mock_docs)
        assert res["matching_doc_ids"] == ["11111111-1111-1111-1111-111111111111"]


@pytest.mark.asyncio
async def test_generate_search_explanation() -> None:
    """Проверяет генерацию текстового объяснения на основе найденных документов."""
    from src.modules.documents.handlers.search import _generate_search_explanation

    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="Найдено 1 документ. Это полис."))]
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
    with patch("src.modules.documents.handlers.search.get_llm_client", return_value=mock_client):
        res = await _generate_search_explanation(
            "найди полис",
            [{"saved_filename": "polis.pdf", "category": "Медицина", "owner": "Жена", "summary": "Полис"}],
        )
        assert "полис" in res.lower()


@pytest.mark.asyncio
async def test_handle_search_found_single_doc(tmp_path) -> None:
    """Проверяет обработку поиска с одним документом — отправляется answer_document."""
    message = AsyncMock()
    message.text = "найди полис"
    message.chat.id = 123
    bot = AsyncMock()

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
            "distance": 0.05,
        }
    ]

    mock_rerank = {"matching_doc_ids": ["11111111-1111-1111-1111-111111111111"]}

    with (
        patch("src.modules.documents.handlers.search.vector_search", AsyncMock(return_value=mock_search_results)),
        patch("src.modules.documents.handlers.search._rerank_documents", AsyncMock(return_value=mock_rerank)),
        patch(
            "src.modules.documents.handlers.search._generate_search_explanation",
            AsyncMock(return_value="Это медицинский полис жены."),
        ),
    ):
        message.bot = bot
        await _do_search(message.text, message)

        # Первое сообщение — AI-текст с перечнем названий
        message.answer.assert_called_once()
        text_args, _ = message.answer.call_args
        assert "Запрос:" in text_args[0]
        assert "Найденные документы:" in text_args[0]
        assert "polis.pdf" in text_args[0]
        assert "медицинский полис" in text_args[0].lower()

        # Один документ — отправляем через answer_document (не медиа-группа)
        message.answer_document.assert_called_once()
        args, kwargs = message.answer_document.call_args
        assert isinstance(args[0], types.FSInputFile)
        assert args[0].filename == "polis.pdf"
        assert "https://drive.google.com" in kwargs["caption"]


@pytest.mark.asyncio
async def test_handle_search_found_multiple_docs(tmp_path) -> None:
    """Проверяет обработку поиска с несколькими документами — отправляется медиа-группа."""
    message = AsyncMock()
    message.text = "все консультации кардиолога"
    message.chat.id = 123
    bot = AsyncMock()

    file1 = tmp_path / "2026-06-02 Консультация кардиолога.jpg"
    file1.write_text("img1")
    file2 = tmp_path / "2026-06-07 Консультация кардиолога.jpg"
    file2.write_text("img2")

    mock_search_results = [
        {
            "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "saved_filename": "2026-06-02 Консультация кардиолога.jpg",
            "local_path": str(file1),
            "gdrive_link": "https://drive.google.com/1",
            "category": "Медицина",
            "owner": "Евгений",
            "summary": "Первичная консультация кардиолога",
            "distance": 0.05,
        },
        {
            "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "saved_filename": "2026-06-07 Консультация кардиолога.jpg",
            "local_path": str(file2),
            "gdrive_link": "https://drive.google.com/2",
            "category": "Медицина",
            "owner": "Евгений",
            "summary": "Повторная консультация кардиолога",
            "distance": 0.07,
        },
    ]

    mock_rerank = {
        "matching_doc_ids": [
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        ]
    }

    with (
        patch("src.modules.documents.handlers.search.vector_search", AsyncMock(return_value=mock_search_results)),
        patch("src.modules.documents.handlers.search._rerank_documents", AsyncMock(return_value=mock_rerank)),
        patch(
            "src.modules.documents.handlers.search._generate_search_explanation",
            AsyncMock(return_value="Найдено 2 консультации кардиолога."),
        ),
    ):
        message.bot = bot
        await _do_search(message.text, message)

        # Первое сообщение — AI-текст
        message.answer.assert_called_once()
        text_args, _ = message.answer.call_args
        assert "Найденные документы:" in text_args[0]
        assert "Найдено 2 консультации" in text_args[0]

        # Несколько документов — медиа-группа через bot.send_media_group
        bot.send_media_group.assert_called_once()
        call_args = bot.send_media_group.call_args
        media = call_args.kwargs.get("media") or (call_args.args[1] if len(call_args.args) > 1 else [])
        assert len(media) == 2


@pytest.mark.asyncio
async def test_handle_search_not_found() -> None:
    """Проверяет поведение поиска, если ничего не найдено."""
    message = AsyncMock()
    message.text = "привет"
    message.chat.id = 123
    bot = AsyncMock()

    mock_rerank = {"matching_doc_ids": []}

    with (
        patch("src.modules.documents.handlers.search.vector_search", AsyncMock(return_value=[])),
        patch("src.modules.documents.handlers.search._rerank_documents", AsyncMock(return_value=mock_rerank)),
    ):
        message.bot = bot
        await _do_search(message.text, message)
        message.answer.assert_called_once_with("Ничего не найдено. Попробуйте уточнить запрос.")


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
