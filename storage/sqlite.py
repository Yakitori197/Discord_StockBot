"""Local asynchronous SQLite backend used for development and tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

from .base import Storage, calculate_level


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_levels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT,
    xp INTEGER DEFAULT 0,
    level INTEGER DEFAULT 1,
    total_messages INTEGER DEFAULT 0,
    last_xp_time TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS level_rewards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    level INTEGER NOT NULL,
    role_id TEXT NOT NULL,
    role_name TEXT,
    UNIQUE(guild_id, level)
);
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id TEXT PRIMARY KEY,
    welcome_channel_id TEXT,
    welcome_message TEXT,
    rules_channel_id TEXT,
    log_channel_id TEXT,
    level_up_channel_id TEXT,
    xp_per_message INTEGER DEFAULT 15,
    xp_cooldown INTEGER DEFAULT 60
);
CREATE TABLE IF NOT EXISTS welcome_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


ALLOWED_SETTINGS = {
    "welcome_channel_id",
    "welcome_message",
    "rules_channel_id",
    "log_channel_id",
    "level_up_channel_id",
    "xp_per_message",
    "xp_cooldown",
}


class SQLiteStorage(Storage):
    """A single-connection SQLite backend serialized with an async lock."""

    backend_name = "sqlite"

    def __init__(self, path: str):
        self.path = Path(path)
        self._connection: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    def _conn(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("storage is not initialized")
        return self._connection

    async def initialize(self) -> None:
        if self._connection is not None:
            return
        if self.path.parent != Path("."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self.path)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute("PRAGMA journal_mode=WAL")
        await self._connection.execute("PRAGMA foreign_keys=ON")
        await self._connection.executescript(SQLITE_SCHEMA)
        await self._connection.commit()

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def get_user_level(self, guild_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            cursor = await self._conn().execute(
                "SELECT * FROM user_levels WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def add_xp(
        self, guild_id: str, user_id: str, username: str, xp_amount: int
    ) -> Tuple[int, int, bool]:
        async with self._lock:
            conn = self._conn()
            await conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await conn.execute(
                    "SELECT xp, level FROM user_levels WHERE guild_id = ? AND user_id = ?",
                    (guild_id, user_id),
                )
                row = await cursor.fetchone()
                now = datetime.now(timezone.utc).isoformat()
                if row:
                    old_level = int(row["level"])
                    new_xp = int(row["xp"]) + xp_amount
                    new_level = calculate_level(new_xp)
                    await conn.execute(
                        """
                        UPDATE user_levels
                        SET xp = ?, level = ?, username = ?,
                            total_messages = total_messages + 1, last_xp_time = ?
                        WHERE guild_id = ? AND user_id = ?
                        """,
                        (new_xp, new_level, username, now, guild_id, user_id),
                    )
                else:
                    old_level = 1
                    new_xp = xp_amount
                    new_level = calculate_level(new_xp)
                    await conn.execute(
                        """
                        INSERT INTO user_levels
                            (guild_id, user_id, username, xp, level, total_messages, last_xp_time)
                        VALUES (?, ?, ?, ?, ?, 1, ?)
                        """,
                        (guild_id, user_id, username, new_xp, new_level, now),
                    )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
        return new_level, new_xp, new_level > old_level

    async def get_leaderboard(self, guild_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        async with self._lock:
            cursor = await self._conn().execute(
                "SELECT * FROM user_levels WHERE guild_id = ? ORDER BY xp DESC LIMIT ?",
                (guild_id, limit),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_user_rank(self, guild_id: str, user_id: str) -> Optional[int]:
        async with self._lock:
            cursor = await self._conn().execute(
                """
                SELECT COUNT(*) AS rank FROM user_levels
                WHERE guild_id = ? AND xp > (
                    SELECT COALESCE(xp, 0) FROM user_levels
                    WHERE guild_id = ? AND user_id = ?
                )
                """,
                (guild_id, guild_id, user_id),
            )
            row = await cursor.fetchone()
        return int(row["rank"]) + 1 if row else None

    async def add_level_reward(
        self, guild_id: str, level: int, role_id: str, role_name: str
    ) -> None:
        async with self._lock:
            await self._conn().execute(
                """
                INSERT INTO level_rewards (guild_id, level, role_id, role_name)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, level)
                DO UPDATE SET role_id = excluded.role_id, role_name = excluded.role_name
                """,
                (guild_id, level, role_id, role_name),
            )
            await self._conn().commit()

    async def get_level_reward(self, guild_id: str, level: int) -> Optional[Dict[str, Any]]:
        async with self._lock:
            cursor = await self._conn().execute(
                "SELECT * FROM level_rewards WHERE guild_id = ? AND level = ?",
                (guild_id, level),
            )
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_all_level_rewards(self, guild_id: str) -> List[Dict[str, Any]]:
        async with self._lock:
            cursor = await self._conn().execute(
                "SELECT * FROM level_rewards WHERE guild_id = ? ORDER BY level ASC",
                (guild_id,),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def remove_level_reward(self, guild_id: str, level: int) -> bool:
        async with self._lock:
            cursor = await self._conn().execute(
                "DELETE FROM level_rewards WHERE guild_id = ? AND level = ?",
                (guild_id, level),
            )
            await self._conn().commit()
        return cursor.rowcount > 0

    async def get_guild_settings(self, guild_id: str) -> Dict[str, Any]:
        async with self._lock:
            conn = self._conn()
            await conn.execute(
                "INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)",
                (guild_id,),
            )
            await conn.commit()
            cursor = await conn.execute(
                "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)
            )
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("failed to create guild settings")
        return dict(row)

    async def update_guild_settings(self, guild_id: str, **kwargs: Any) -> None:
        values = [(key, value) for key, value in kwargs.items() if key in ALLOWED_SETTINGS]
        if not values:
            return
        async with self._lock:
            conn = self._conn()
            await conn.execute(
                "INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)", (guild_id,)
            )
            assignments = ", ".join(f"{key} = ?" for key, _ in values)
            params = [value for _, value in values] + [guild_id]
            await conn.execute(
                f"UPDATE guild_settings SET {assignments} WHERE guild_id = ?", params
            )
            await conn.commit()

    async def log_welcome(self, guild_id: str, user_id: str, username: str) -> None:
        async with self._lock:
            await self._conn().execute(
                "INSERT INTO welcome_logs (guild_id, user_id, username) VALUES (?, ?, ?)",
                (guild_id, user_id, username),
            )
            await self._conn().commit()
