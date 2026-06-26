"""Декларативные модели SQLAlchemy для базы данных."""

import datetime
import uuid

from sqlalchemy import DateTime, LargeBinary, String, Text, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Базовый класс для всех моделей SQLAlchemy."""

    pass


class Document(Base):
    """Модель документа для таблицы documents."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, doc="Уникальный идентификатор документа"
    )
    saved_filename: Mapped[str] = mapped_column(String(255), nullable=False, doc="Имя сохраненного файла на диске")
    local_path: Mapped[str] = mapped_column(String(512), nullable=False, doc="Локальный путь к файлу в хранилище")
    gdrive_link: Mapped[str | None] = mapped_column(String(512), nullable=True, doc="Ссылка на файл в Google Drive")
    category: Mapped[str] = mapped_column(
        String(100), nullable=False, doc="Категория документа (например, Медицина/Жена)"
    )
    owner: Mapped[str] = mapped_column(String(100), nullable=False, doc="Владелец документа (автор)")
    summary: Mapped[str] = mapped_column(Text, nullable=False, doc="Текстовое описание документа от LLM")
    embedding: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False, doc="Бинарное представление вектора эмбеддинга"
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
        doc="Время добавления документа",
    )
