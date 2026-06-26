"""Инициализация базы данных и настройка асинхронного движка SQLAlchemy."""

from pathlib import Path
from typing import Any

import sqlite_vec
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.db.models import Base

# Создаем асинхронный движок SQLAlchemy
engine = create_async_engine(settings.db.database_url)


@event.listens_for(engine.sync_engine, "connect")
def load_sqlite_vec(dbapi_connection: Any, connection_record: Any) -> None:
    """Динамически загружает расширение sqlite-vec в соединение SQLite."""
    dbapi_connection.await_(dbapi_connection.driver_connection.enable_load_extension(True))
    dbapi_connection.await_(dbapi_connection.driver_connection.load_extension(sqlite_vec.loadable_path()))
    dbapi_connection.await_(dbapi_connection.driver_connection.enable_load_extension(False))


# Фабрика асинхронных сессий базы данных
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    """Инициализирует базу данных, создавая все необходимые таблицы."""
    # Создаем директорию для файла базы данных, если она отсутствует
    db_path = Path(settings.db.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    async with engine.begin() as conn:
        # Создаем обычные реляционные таблицы
        await conn.run_sync(Base.metadata.create_all)

        # Создаем виртуальную таблицу для векторного поиска
        # По умолчанию размерность векторов 768, метрика сходства — косинусное расстояние
        await conn.execute(
            text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_documents USING vec0(
                document_id TEXT UNIQUE,
                embedding float[768] distance_metric=cosine
            );
        """)
        )
