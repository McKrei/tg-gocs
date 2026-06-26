"""Репозиторий для управления документами и их векторными эмбеддингами в базе данных."""

import struct
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Document


class DocumentRepository:
    """Репозиторий для выполнения CRUD операций и векторного поиска над документами."""

    def __init__(self, session: AsyncSession) -> None:
        """Инициализирует репозиторий асинхронной сессией."""
        self.session = session

    @staticmethod
    def _vector_to_bytes(vector: list[float]) -> bytes:
        """Преобразует список float в байтовое представление float32 (BLOB для SQLite)."""
        return struct.pack(f"{len(vector)}f", *vector)

    @staticmethod
    def _bytes_to_vector(data: bytes) -> list[float]:
        """Преобразует байтовое представление float32 обратно в список float."""
        num_floats = len(data) // 4
        return list(struct.unpack(f"{num_floats}f", data))

    async def add_document(
        self,
        saved_filename: str,
        local_path: str,
        category: str,
        owner: str,
        summary: str,
        embedding: list[float],
        gdrive_link: str | None = None,
        doc_id: uuid.UUID | None = None,
    ) -> Document:
        """Добавляет новый документ в реляционную таблицу и виртуальную таблицу векторов."""
        final_id = doc_id or uuid.uuid4()
        embedding_bytes = self._vector_to_bytes(embedding)

        doc = Document(
            id=final_id,
            saved_filename=saved_filename,
            local_path=local_path,
            gdrive_link=gdrive_link,
            category=category,
            owner=owner,
            summary=summary,
            embedding=embedding_bytes,
        )
        self.session.add(doc)

        # Вставляем/обновляем эмбеддинг в виртуальной таблице vec_documents
        await self.session.execute(
            text(
                """
                INSERT OR REPLACE INTO vec_documents(document_id, embedding)
                VALUES (:doc_id, :embedding)
                """
            ),
            {"doc_id": str(final_id), "embedding": embedding_bytes},
        )

        return doc

    async def get_document(self, doc_id: uuid.UUID) -> Document | None:
        """Возвращает документ по его уникальному идентификатору (UUID)."""
        return await self.session.get(Document, doc_id)

    async def delete_document(self, doc_id: uuid.UUID) -> bool:
        """Удаляет документ и его векторный эмбеддинг. Возвращает True при успешном удалении."""
        doc = await self.get_document(doc_id)
        if not doc:
            return False

        await self.session.delete(doc)

        # Удаляем запись о векторе из виртуальной таблицы
        await self.session.execute(
            text("DELETE FROM vec_documents WHERE document_id = :doc_id"),
            {"doc_id": str(doc_id)},
        )
        return True

    async def search_documents(self, query_embedding: list[float], limit: int = 5) -> list[tuple[Document, float]]:
        """Ищет ближайшие документы на основе косинусного сходства их векторов."""
        query_bytes = self._vector_to_bytes(query_embedding)

        # Выполняем KNN поиск в виртуальной таблице vec_documents
        result = await self.session.execute(
            text(
                """
                SELECT document_id, distance
                FROM vec_documents
                WHERE embedding MATCH :query_vector
                  AND k = :limit
                """
            ),
            {"query_vector": query_bytes, "limit": limit},
        )

        rows = result.all()
        if not rows:
            return []

        # Парсим результаты и сохраняем соответствие doc_id -> distance
        matched_ids = [uuid.UUID(row[0]) for row in rows]
        id_to_distance = {uuid.UUID(row[0]): float(row[1]) for row in rows}

        # Достаем полные сущности документов из БД
        stmt = select(Document).where(Document.id.in_(matched_ids))
        db_result = await self.session.execute(stmt)
        documents = db_result.scalars().all()

        # Возвращаем отсортированные по расстоянию результаты поиска
        sorted_results = []
        doc_map = {doc.id: doc for doc in documents}
        for doc_id in matched_ids:
            if doc_id in doc_map:
                sorted_results.append((doc_map[doc_id], id_to_distance[doc_id]))

        return sorted_results

    async def get_recent_documents(self, limit: int = 10) -> list[Document]:
        """Возвращает список последних сохраненных документов."""
        stmt = select(Document).order_by(Document.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_stats_by_category(self) -> list[tuple[str, int]]:
        """Возвращает количество документов по категориям."""
        from sqlalchemy import func

        stmt = (
            select(Document.category, func.count(Document.id))
            .group_by(Document.category)
            .order_by(func.count(Document.id).desc())
        )
        result = await self.session.execute(stmt)
        return [(str(row[0]), int(row[1])) for row in result.all()]

    async def get_by_gdrive_link(self, gdrive_link: str) -> "Document | None":
        """Возвращает документ по ссылке Google Drive."""
        from src.db.models import Document as Doc

        stmt = select(Doc).where(Doc.gdrive_link == gdrive_link)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_gdrive_links(self) -> set[str]:
        """Возвращает множество всех сохранённых ссылок Google Drive."""
        stmt = select(Document.gdrive_link).where(Document.gdrive_link.isnot(None))
        result = await self.session.execute(stmt)
        return {str(row[0]) for row in result.all() if row[0]}
