"""Тесты для LLM клиента, эмбеддингов, инструментов и оркестратора агента."""

import contextlib
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import sqlite_vec
from PIL import Image
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.agent.agent import classify_document, normalize_draft_metadata
from src.agent.tools import (
    convert_to_pdf,
    create_directory,
    get_directory_tree,
    save_to_local_and_drive,
    vector_search,
)
from src.db.models import Base
from src.llm.embeddings import get_embedding

TEST_DB_PATH = Path("data/test_agent_db.db")


@pytest_asyncio.fixture
async def test_engine():
    """Создает тестовый асинхронный движок с загруженным расширением sqlite-vec."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{TEST_DB_PATH}")

    @event.listens_for(engine.sync_engine, "connect")
    def load_sqlite_vec(dbapi_connection: Any, connection_record: Any) -> None:
        dbapi_connection.await_(dbapi_connection.driver_connection.enable_load_extension(True))
        dbapi_connection.await_(dbapi_connection.driver_connection.load_extension(sqlite_vec.loadable_path()))
        dbapi_connection.await_(dbapi_connection.driver_connection.enable_load_extension(False))

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_documents USING vec0(
                document_id TEXT UNIQUE,
                embedding float[768] distance_metric=cosine
            );
        """)
        )

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS vec_documents"))

    await engine.dispose()
    if TEST_DB_PATH.exists():
        with contextlib.suppress(PermissionError):
            TEST_DB_PATH.unlink()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncSession:
    """Сессия базы данных для тестирования репозитория в инструментах."""
    async_session = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as session:
        yield session


@pytest.mark.asyncio
async def test_get_embedding() -> None:
    """Проверяет генерацию эмбеддингов через мок-клиент OpenRouter."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": [{"embedding": [0.1] * 768}]}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        vec = await get_embedding("тестовый текст")
        assert len(vec) == 768

        # Проверяем, что вектор нормализован
        import math

        l2_norm = math.sqrt(sum(x * x for x in vec))
        assert pytest.approx(l2_norm, rel=1e-3) == 1.0

        mock_post.assert_called_once()


@pytest.mark.asyncio
async def test_get_directory_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет корректность генерации дерева директорий."""
    monkeypatch.setattr("src.config.settings.storage.local_storage_dir", str(tmp_path))

    (tmp_path / "Медицина").mkdir()
    (tmp_path / "Медицина" / "Жена").mkdir()
    (tmp_path / "Личное").mkdir()

    tree = await get_directory_tree()
    assert "Медицина" in tree
    assert "Жена" in tree
    assert "Личное" in tree


@pytest.mark.asyncio
async def test_create_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет создание папок в хранилище."""
    monkeypatch.setattr("src.config.settings.storage.local_storage_dir", str(tmp_path))

    success = await create_directory("Медицина/Муж")
    assert success is True
    assert (tmp_path / "Медицина" / "Муж").exists()


@pytest.mark.asyncio
async def test_convert_to_pdf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет склеивание картинок во временной папке в PDF."""
    monkeypatch.setattr("src.config.settings.storage.temp_dir", str(tmp_path))

    img1 = tmp_path / "page1.jpg"
    img2 = tmp_path / "page2.jpg"

    Image.new("RGB", (50, 50), "red").save(img1)
    Image.new("RGB", (50, 50), "blue").save(img2)

    pdf_path_str = await convert_to_pdf(["page1.jpg", "page2.jpg"], "result.pdf")
    pdf_path = Path(pdf_path_str)

    assert pdf_path.exists()
    assert pdf_path.name == "result.pdf"


@pytest.mark.asyncio
async def test_save_to_local_and_drive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет локальное копирование файлов в конечную категорию."""
    local_dir = tmp_path / "documents"
    temp_dir = tmp_path / "temp"

    local_dir.mkdir()
    temp_dir.mkdir()

    monkeypatch.setattr("src.config.settings.storage.local_storage_dir", str(local_dir))

    temp_file = temp_dir / "passport.jpg"
    temp_file.write_text("some data")

    res = await save_to_local_and_drive(str(temp_file), "Личное/passport.jpg")

    assert res["gdrive_link"] is None
    assert Path(res["local_path"]).exists()
    assert (local_dir / "Личное" / "passport.jpg").exists()


@pytest.mark.asyncio
async def test_vector_search_tool(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет работу поискового инструмента с базой данных."""
    monkeypatch.setattr("src.agent.tools.async_session", lambda: db_session)

    # Мокаем генерацию эмбеддингов
    with patch("src.agent.tools.get_embedding", AsyncMock(return_value=[0.1] * 768)):
        # Предварительно добавим документ в БД через репозиторий
        from src.db.repository import DocumentRepository

        repo = DocumentRepository(db_session)
        await repo.add_document(
            saved_filename="pass.pdf",
            local_path="/docs/pass.pdf",
            category="Personal",
            owner="Ivan",
            summary="Паспорт",
            embedding=[0.1] * 768,
            doc_id=uuid.uuid4(),
        )
        await db_session.commit()

        results = await vector_search(query="паспорт", limit=5)
        assert len(results) == 1
        assert results[0]["saved_filename"] == "pass.pdf"


@pytest.mark.asyncio
async def test_classify_document_orchestrator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет цикл оркестрации агента (Agent Loop) с вызовом инструмента и финальным ответом."""
    test_img = tmp_path / "passport.jpg"
    Image.new("RGB", (50, 50), "blue").save(test_img)

    # 1. Мокаем вызов инструмента
    mock_tool_call = MagicMock()
    mock_tool_call.id = "call_1"
    mock_tool_call.function.name = "get_directory_tree"
    mock_tool_call.function.arguments = '{"path_prefix": ""}'

    # Первый шаг: модель просит вызвать инструмент
    mock_msg_step1 = MagicMock()
    mock_msg_step1.tool_calls = [mock_tool_call]
    mock_msg_step1.content = None

    mock_choice_step1 = MagicMock()
    mock_choice_step1.message = mock_msg_step1

    mock_resp_step1 = MagicMock()
    mock_resp_step1.choices = [mock_choice_step1]

    # Второй шаг: модель возвращает финальный классифицированный JSON
    mock_msg_step2 = MagicMock()
    mock_msg_step2.tool_calls = None
    mock_msg_step2.content = (
        '{"category": "Медицина/Жена", "suggested_filename": "Polis.pdf", '
        '"summary": "Медицинский полис жены", "owner": "Жена"}'
    )

    mock_choice_step2 = MagicMock()
    mock_choice_step2.message = mock_msg_step2

    mock_resp_step2 = MagicMock()
    mock_resp_step2.choices = [mock_choice_step2]

    # Мокаем вызовы к OpenRouter API
    with patch("src.agent.agent.get_llm_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=[mock_resp_step1, mock_resp_step2])
        mock_get_client.return_value = mock_client

        # Мокаем сам инструмент
        with patch(
            "src.agent.agent.TOOLS_MAP",
            {"get_directory_tree": AsyncMock(return_value="Дерево директорий")},
        ):
            result = await classify_document(str(test_img))

            assert result["category"] == "Медицина/Жена"
            assert result["suggested_filename"] == "Polis.pdf"
            assert result["owner"] == "Жена"
            assert mock_client.chat.completions.create.call_count == 2


def test_normalize_draft_metadata_uses_document_date_from_summary() -> None:
    """Проверяет добавление даты документа в имя файла."""
    draft = {
        "category": "Личные документы/Общие",
        "suggested_filename": "Свидетельство о заключении брака Сергуньины.jpg",
        "summary": "Брак зарегистрирован 07.07.2017. Актовая запись № 306. Серия I-ГР № 733248.",
        "owner": "Общее",
    }

    result = normalize_draft_metadata(draft, ".jpg", today="2026-06-26")

    assert result["suggested_filename"] == "2017-07-07 Свидетельство о заключении брака Сергуньины.jpg"


def test_normalize_draft_metadata_uses_today_when_document_date_missing() -> None:
    """Проверяет добавление текущей даты, если дата документа не найдена."""
    draft = {
        "category": "Личные документы/Общие",
        "suggested_filename": "Документ.jpg",
        "summary": "Документ без явной даты.",
        "owner": "Общее",
    }

    result = normalize_draft_metadata(draft, ".jpg", today="2026-06-26")

    assert result["suggested_filename"] == "2026-06-26 Документ.jpg"
