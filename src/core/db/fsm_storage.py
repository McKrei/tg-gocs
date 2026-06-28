"""SQLite FSM-хранилище для aiogram 3.x, предотвращающее сброс состояний при перезапуске."""

import json
from collections.abc import Mapping
from typing import Any, cast

import aiosqlite
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey


class SQLiteStorage(BaseStorage):
    """Персистентное FSM-хранилище на базе SQLite для сохранения состояний при перезапусках."""

    def __init__(self, db_path: str = "data/fsm.db") -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def _get_conn(self) -> aiosqlite.Connection:
        if self._db is None:
            self._db = await aiosqlite.connect(self.db_path)
            # Включаем WAL-режим для лучшей конкурентности
            await self._db.execute("PRAGMA journal_mode=WAL;")
            await self._db.execute(
                """
                CREATE TABLE IF NOT EXISTS fsm_states (
                    bot_id INTEGER,
                    chat_id INTEGER,
                    user_id INTEGER,
                    destiny TEXT,
                    state TEXT,
                    data TEXT,
                    PRIMARY KEY (bot_id, chat_id, user_id, destiny)
                )
                """
            )
            await self._db.commit()
        return self._db

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        db = await self._get_conn()

        state_str: str | None = None
        if state is not None:
            if isinstance(state, str):
                state_str = state
            elif hasattr(state, "state"):
                state_str = state.state
            else:
                state_str = str(state)

        await db.execute(
            """
            INSERT INTO fsm_states (bot_id, chat_id, user_id, destiny, state, data)
            VALUES (?, ?, ?, ?, ?, '{}')
            ON CONFLICT(bot_id, chat_id, user_id, destiny) DO UPDATE SET state = excluded.state
            """,
            (key.bot_id, key.chat_id, key.user_id, key.destiny, state_str),
        )
        await db.commit()

    async def get_state(self, key: StorageKey) -> str | None:
        db = await self._get_conn()
        async with db.execute(
            """
            SELECT state FROM fsm_states
            WHERE bot_id = ? AND chat_id = ? AND user_id = ? AND destiny = ?
            """,
            (key.bot_id, key.chat_id, key.user_id, key.destiny),
        ) as cursor:
            row = await cursor.fetchone()
            return cast(str | None, row[0]) if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        db = await self._get_conn()
        data_str = json.dumps(data, ensure_ascii=False)

        await db.execute(
            """
            INSERT INTO fsm_states (bot_id, chat_id, user_id, destiny, state, data)
            VALUES (?, ?, ?, ?, NULL, ?)
            ON CONFLICT(bot_id, chat_id, user_id, destiny) DO UPDATE SET data = excluded.data
            """,
            (key.bot_id, key.chat_id, key.user_id, key.destiny, data_str),
        )
        await db.commit()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        db = await self._get_conn()
        async with db.execute(
            """
            SELECT data FROM fsm_states
            WHERE bot_id = ? AND chat_id = ? AND user_id = ? AND destiny = ?
            """,
            (key.bot_id, key.chat_id, key.user_id, key.destiny),
        ) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                try:
                    loaded = json.loads(row[0])
                    if isinstance(loaded, dict):
                        return cast(dict[str, Any], loaded)
                except json.JSONDecodeError:
                    return {}
            return {}

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
