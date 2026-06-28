"""Декларативные модели SQLAlchemy для базы данных."""

import datetime
import uuid

from sqlalchemy import DateTime, LargeBinary, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db.base import Base


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


class PendingUpload(Base):
    """Модель для хранения задач на отложенную синхронизацию файлов с Google Drive."""

    __tablename__ = "pending_uploads"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, doc="Уникальный идентификатор задачи"
    )
    local_path: Mapped[str] = mapped_column(String(512), nullable=False, doc="Путь к локальному файлу-источнику")
    target_path: Mapped[str] = mapped_column(
        String(512), nullable=False, doc="Относительный путь для сохранения в Drive"
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, nullable=True, doc="Ссылка на ID документа в таблице documents"
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
        nullable=False,
        doc="Время создания задачи",
    )
    attempts: Mapped[int] = mapped_column(default=0, nullable=False, doc="Количество предпринятых попыток загрузки")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True, doc="Текст последней ошибки")
    status: Mapped[str] = mapped_column(
        String(50), default="pending", nullable=False, doc="Статус задачи: pending, completed, failed"
    )
