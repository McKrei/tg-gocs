"""Интеграционные тесты для базы данных и векторного поиска."""

import contextlib
import uuid
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import sqlite_vec
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db.models import Base
from src.db.repository import DocumentRepository

TEST_DB_PATH = Path("data/test_db.db")


@pytest.fixture(scope="session", autouse=True)
def setup_test_db_dir() -> None:
    """Создает директорию для тестовой БД и очищает старый файл при наличии."""
    TEST_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if TEST_DB_PATH.exists():
        with contextlib.suppress(PermissionError):
            TEST_DB_PATH.unlink()


@pytest_asyncio.fixture
async def test_engine():
    """Создает тестовый асинхронный движок с загруженным расширением sqlite-vec."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{TEST_DB_PATH}")

    @event.listens_for(engine.sync_engine, "connect")
    def load_sqlite_vec(dbapi_connection: Any, connection_record: Any) -> None:
        dbapi_connection.await_(dbapi_connection.driver_connection.enable_load_extension(True))
        dbapi_connection.await_(dbapi_connection.driver_connection.load_extension(sqlite_vec.loadable_path()))
        dbapi_connection.await_(dbapi_connection.driver_connection.enable_load_extension(False))

    # Создаем таблицы в тестовой БД
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

    # Дропаем таблицы после выполнения тестов
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS vec_documents"))

    await engine.dispose()
    if TEST_DB_PATH.exists():
        with contextlib.suppress(PermissionError):
            TEST_DB_PATH.unlink()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncSession:
    """Предоставляет асинхронную сессию для выполнения тестов."""
    async_session = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as session:
        yield session


@pytest.mark.asyncio
async def test_add_and_get_document(db_session: AsyncSession) -> None:
    """Проверяет добавление документа в базу данных и его получение по ID."""
    repo = DocumentRepository(db_session)
    doc_id = uuid.uuid4()
    dummy_embedding = [0.1] * 768

    # Добавляем документ
    new_doc = await repo.add_document(
        saved_filename="pass.pdf",
        local_path="/docs/pass.pdf",
        category="Personal/Passport",
        owner="Ivan",
        summary="Паспорт гражданина РФ",
        embedding=dummy_embedding,
        doc_id=doc_id,
    )
    await db_session.commit()

    assert new_doc.id == doc_id
    assert new_doc.saved_filename == "pass.pdf"

    # Получаем документ по ID
    fetched_doc = await repo.get_document(doc_id)
    assert fetched_doc is not None
    assert fetched_doc.saved_filename == "pass.pdf"
    assert fetched_doc.category == "Personal/Passport"


@pytest.mark.asyncio
async def test_vector_search(db_session: AsyncSession) -> None:
    """Проверяет корректность векторного поиска по сходству эмбеддингов."""
    repo = DocumentRepository(db_session)

    # Эмбеддинг 1 (первый документ)
    embedding1 = [0.0] * 768
    embedding1[0] = 1.0  # сильный сигнал на первой координате

    # Эмбеддинг 2 (второй документ)
    embedding2 = [0.0] * 768
    embedding2[100] = 1.0  # сильный сигнал на другой координате

    # Добавляем оба документа в БД
    doc1_id = uuid.uuid4()
    await repo.add_document(
        saved_filename="doc1.pdf",
        local_path="/docs/doc1.pdf",
        category="Cat1",
        owner="Ivan",
        summary="Документ про котиков",
        embedding=embedding1,
        doc_id=doc1_id,
    )

    doc2_id = uuid.uuid4()
    await repo.add_document(
        saved_filename="doc2.pdf",
        local_path="/docs/doc2.pdf",
        category="Cat2",
        owner="Ivan",
        summary="Документ про собак",
        embedding=embedding2,
        doc_id=doc2_id,
    )
    await db_session.commit()

    # Поисковый запрос, близкий к первому документу
    query_embedding = [0.0] * 768
    query_embedding[0] = 0.9  # вектор очень похож на embedding1

    results = await repo.search_documents(query_embedding, limit=5)

    assert len(results) >= 2
    # Первым в выдаче должен быть doc1, так как он ближе по косинусному расстоянию
    first_match, first_distance = results[0]
    assert first_match.id == doc1_id
    assert first_distance < 0.1  # косинусное расстояние должно быть крайне малым


@pytest.mark.asyncio
async def test_delete_document(db_session: AsyncSession) -> None:
    """Проверяет успешное удаление документа из реляционной и виртуальной таблиц."""
    repo = DocumentRepository(db_session)
    doc_id = uuid.uuid4()
    dummy_embedding = [0.2] * 768

    # Добавляем документ
    await repo.add_document(
        saved_filename="to_delete.pdf",
        local_path="/docs/to_delete.pdf",
        category="Temp",
        owner="Ivan",
        summary="Временный файл",
        embedding=dummy_embedding,
        doc_id=doc_id,
    )
    await db_session.commit()

    # Удаляем документ
    deleted = await repo.delete_document(doc_id)
    await db_session.commit()

    assert deleted is True

    # Проверяем, что документ больше не извлекается по ID
    fetched_doc = await repo.get_document(doc_id)
    assert fetched_doc is None

    # Проверяем, что векторный поиск больше не находит этот документ
    search_results = await repo.search_documents(dummy_embedding, limit=5)
    assert not any(doc.id == doc_id for doc, _ in search_results)
