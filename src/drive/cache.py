import time


class FolderCache:
    """In-memory кэш сопоставления путей категорий и ID папок Google Drive."""

    def __init__(self, ttl_seconds: float = 86400.0) -> None:
        """Инициализирует кэш со временем жизни (TTL) записей."""
        self._cache: dict[str, tuple[str, float]] = {}
        self.ttl = ttl_seconds

    def get(self, path: str) -> str | None:
        """Возвращает ID папки по её относительному пути, если запись существует и не устарела."""
        normalized_path = path.strip().replace("\\", "/").lower()
        if normalized_path not in self._cache:
            return None

        folder_id, timestamp = self._cache[normalized_path]
        if time.monotonic() - timestamp > self.ttl:
            del self._cache[normalized_path]
            return None

        return folder_id

    def set(self, path: str, folder_id: str) -> None:
        """Сохраняет ID папки для указанного относительного пути."""
        normalized_path = path.strip().replace("\\", "/").lower()
        self._cache[normalized_path] = (folder_id, time.monotonic())

    def invalidate(self, path: str) -> None:
        """Инвалидирует запись по конкретному пути."""
        normalized_path = path.strip().replace("\\", "/").lower()
        self._cache.pop(normalized_path, None)

    def clear(self) -> None:
        """Полностью очищает кэш."""
        self._cache.clear()


# Глобальный единственный экземпляр кэша
folder_cache = FolderCache()
