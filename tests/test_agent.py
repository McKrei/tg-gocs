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
    extract_gdrive_file_id,
    find_similar_document,
    get_directory_tree,
    get_existing_structure,
    get_unique_filename,
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

    with patch(
        "src.agent.tools.upload_file_with_status", AsyncMock(return_value={"link": None, "error": "mock_error"})
    ):
        res = await save_to_local_and_drive(str(temp_file), "Личное/passport.jpg")

        assert res["gdrive_link"] is None
        assert res["gdrive_error"] == "mock_error"
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


@pytest.mark.asyncio
async def test_get_existing_structure(
    db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проверяет получение структуры папок и данных из БД."""
    monkeypatch.setattr("src.config.settings.storage.local_storage_dir", str(tmp_path))
    monkeypatch.setattr("src.agent.tools.async_session", lambda: db_session)

    (tmp_path / "Медицина").mkdir()
    (tmp_path / "Медицина" / "Жена").mkdir()

    from src.db.repository import DocumentRepository

    repo = DocumentRepository(db_session)
    await repo.add_document(
        saved_filename="pass.pdf",
        local_path="/docs/pass.pdf",
        category="Личные документы/Евгений",
        owner="Евгений",
        summary="Паспорт",
        embedding=[0.1] * 768,
        doc_id=uuid.uuid4(),
    )
    await db_session.commit()

    structure = await get_existing_structure()

    assert "Медицина" in structure
    assert "Жена" in structure
    assert "Личные документы/Евгений" in structure
    assert "Евгений" in structure


def test_extract_gdrive_file_id() -> None:
    """Проверяет извлечение Google Drive ID из ссылки."""
    link = "https://drive.google.com/file/d/1vG58XAG3ajQR11B8EeCoNT6YQJyedbKY/view?usp=drivesdk"
    assert extract_gdrive_file_id(link) == "1vG58XAG3ajQR11B8EeCoNT6YQJyedbKY"

    assert extract_gdrive_file_id("invalid-link") is None


def test_get_unique_filename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет генерацию уникального имени файла."""
    monkeypatch.setattr("src.config.settings.storage.local_storage_dir", str(tmp_path))
    category = "Personal"
    (tmp_path / category).mkdir(parents=True, exist_ok=True)

    filename = "doc.pdf"
    assert get_unique_filename(category, filename) == "doc.pdf"

    (tmp_path / category / filename).write_text("some content")
    assert get_unique_filename(category, filename) == "doc_1.pdf"

    (tmp_path / category / "doc_1.pdf").write_text("some content")
    assert get_unique_filename(category, filename) == "doc_2.pdf"


@pytest.mark.asyncio
async def test_find_similar_document(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет поиск похожих документов по пути и вектору."""
    monkeypatch.setattr("src.agent.tools.async_session", lambda: db_session)

    from src.db.repository import DocumentRepository

    repo = DocumentRepository(db_session)
    await repo.add_document(
        saved_filename="pass.pdf",
        local_path="/docs/pass.pdf",
        category="Personal",
        owner="Ivan",
        summary="Паспорт Ивана",
        embedding=[0.1] * 768,
        doc_id=uuid.uuid4(),
    )
    await db_session.commit()

    res_exact = await find_similar_document("Personal", "pass.pdf", "Другое описание")
    assert res_exact is not None
    assert res_exact["reason"] == "exact_path"
    assert res_exact["similarity_percent"] == 100

    with patch("src.agent.tools.get_embedding", AsyncMock(return_value=[0.101] * 768)), \
         patch("src.llm.duplicate_verifier.check_is_duplicate", AsyncMock(return_value=True)):
        res_semantic = await find_similar_document("Other", "passport.pdf", "Похожее описание")
        assert res_semantic is not None
        assert res_semantic["reason"] == "semantic"
        assert res_semantic["similarity_percent"] > 90

    dummy_emb = [0.1 if i % 2 == 0 else -0.1 for i in range(768)]
    with patch("src.agent.tools.get_embedding", AsyncMock(return_value=dummy_emb)):
        res_diff = await find_similar_document("Other", "passport.pdf", "Совсем другой документ")
        assert res_diff is None


@pytest.mark.asyncio
async def test_find_similar_document_not_duplicate(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяет, что если LLM считает документы разными, похожий документ не возвращается."""
    monkeypatch.setattr("src.agent.tools.async_session", lambda: db_session)

    from src.db.repository import DocumentRepository

    repo = DocumentRepository(db_session)
    await repo.add_document(
        saved_filename="pass.pdf",
        local_path="/docs/pass.pdf",
        category="Personal",
        owner="Ivan",
        summary="Паспорт Ивана",
        embedding=[0.1] * 768,
        doc_id=uuid.uuid4(),
    )
    await db_session.commit()

    with patch("src.agent.tools.get_embedding", AsyncMock(return_value=[0.101] * 768)), \
         patch("src.llm.duplicate_verifier.check_is_duplicate", AsyncMock(return_value=False)):
        res_semantic = await find_similar_document("Other", "passport.pdf", "Похожее описание")
        assert res_semantic is None


@pytest.mark.asyncio
async def test_check_is_duplicate() -> None:
    """Проверяет функцию check_is_duplicate с мокированием ответа LLM."""
    from src.llm.duplicate_verifier import check_is_duplicate

    mock_resp = MagicMock()
    mock_resp.choices = [
        MagicMock(message=MagicMock(content='{"is_duplicate": true, "reason": "Совпадают все данные"}'))
    ]

    with patch("src.llm.duplicate_verifier.get_llm_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
        mock_get_client.return_value = mock_client

        res = await check_is_duplicate(
            {"category": "Personal", "suggested_filename": "pass.pdf", "summary": "Паспорт"},
            {"category": "Personal", "saved_filename": "pass.pdf", "summary": "Паспорт"}
        )
        assert res is True

    mock_resp_false = MagicMock()
    mock_resp_false.choices = [
        MagicMock(message=MagicMock(content='{"is_duplicate": false, "reason": "Разные владельцы"}'))
    ]
    with patch("src.llm.duplicate_verifier.get_llm_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp_false)
        mock_get_client.return_value = mock_client

        res = await check_is_duplicate(
            {"category": "Personal", "suggested_filename": "pass.pdf", "summary": "Паспорт Ивана"},
            {"category": "Personal", "saved_filename": "pass.pdf", "summary": "Паспорт Марии"}
        )
        assert res is False
