"""PostgreSQL backend for durable Render deployments."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

from .base import Storage, calculate_level
from .sqlite import ALLOWED_SETTINGS


POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_levels (
    id BIGSERIAL PRIMARY KEY,
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT,
    xp BIGINT NOT NULL DEFAULT 0,
    level INTEGER NOT NULL DEFAULT 1,
    total_messages BIGINT NOT NULL DEFAULT 0,
    last_xp_time TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS level_rewards (
    id BIGSERIAL PRIMARY KEY,
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
    xp_per_message INTEGER NOT NULL DEFAULT 15,
    xp_cooldown INTEGER NOT NULL DEFAULT 60
);
CREATE TABLE IF NOT EXISTS welcome_logs (
    id BIGSERIAL PRIMARY KEY,
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class PostgresStorage(Storage):
    """PostgreSQL implementation backed by an asyncpg connection pool."""

    backend_name = "postgres"

    def __init__(self, dsn: str, min_pool_size: int = 1, max_pool_size: int = 5):
        self._dsn = dsn
        self._min_pool_size = min_pool_size
        self._max_pool_size = max_pool_size
        self._pool: Optional[asyncpg.Pool] = None

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("storage is not initialized")
        return self._pool

    async def initialize(self) -> None:
        if self._pool is not None:
            return
        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=self._min_pool_size,
            max_size=self._max_pool_size,
            command_timeout=30,
        )
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(POSTGRES_SCHEMA)
        except Exception:
            await self._pool.close()
            self._pool = None
            raise

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def get_user_level(self, guild_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        row = await self._require_pool().fetchrow(
            "SELECT * FROM user_levels WHERE guild_id = $1 AND user_id = $2",
            guild_id,
            user_id,
        )
        return dict(row) if row else None

    async def add_xp(
        self, guild_id: str, user_id: str, username: str, xp_amount: int
    ) -> Tuple[int, int, bool]:
        async with self._require_pool().acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                    f"{guild_id}:{user_id}",
                )
                row = await conn.fetchrow(
                    "SELECT xp, level FROM user_levels WHERE guild_id = $1 AND user_id = $2",
                    guild_id,
                    user_id,
                )
                now = datetime.now(timezone.utc)
                if row:
                    old_level = int(row["level"])
                    new_xp = int(row["xp"]) + xp_amount
                    new_level = calculate_level(new_xp)
                    await conn.execute(
                        """
                        UPDATE user_levels
                        SET xp = $1, level = $2, username = $3,
                            total_messages = total_messages + 1, last_xp_time = $4
                        WHERE guild_id = $5 AND user_id = $6
                        """,
                        new_xp,
                        new_level,
                        username,
                        now,
                        guild_id,
                        user_id,
                    )
                else:
                    old_level = 1
                    new_xp = xp_amount
                    new_level = calculate_level(new_xp)
                    await conn.execute(
                        """
                        INSERT INTO user_levels
                            (guild_id, user_id, username, xp, level, total_messages, last_xp_time)
                        VALUES ($1, $2, $3, $4, $5, 1, $6)
                        """,
                        guild_id,
                        user_id,
                        username,
                        new_xp,
                        new_level,
                        now,
                    )
        return new_level, new_xp, new_level > old_level

    async def get_leaderboard(self, guild_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        rows = await self._require_pool().fetch(
            "SELECT * FROM user_levels WHERE guild_id = $1 ORDER BY xp DESC LIMIT $2",
            guild_id,
            limit,
        )
        return [dict(row) for row in rows]

    async def get_user_rank(self, guild_id: str, user_id: str) -> Optional[int]:
        row = await self._require_pool().fetchrow(
            """
            SELECT COUNT(*) AS rank FROM user_levels
            WHERE guild_id = $1 AND xp > (
                SELECT COALESCE(xp, 0) FROM user_levels WHERE guild_id = $1 AND user_id = $2
            )
            """,
            guild_id,
            user_id,
        )
        return int(row["rank"]) + 1 if row else None

    async def add_level_reward(
        self, guild_id: str, level: int, role_id: str, role_name: str
    ) -> None:
        await self._require_pool().execute(
            """
            INSERT INTO level_rewards (guild_id, level, role_id, role_name)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT(guild_id, level)
            DO UPDATE SET role_id = excluded.role_id, role_name = excluded.role_name
            """,
            guild_id,
            level,
            role_id,
            role_name,
        )

    async def get_level_reward(self, guild_id: str, level: int) -> Optional[Dict[str, Any]]:
        row = await self._require_pool().fetchrow(
            "SELECT * FROM level_rewards WHERE guild_id = $1 AND level = $2",
            guild_id,
            level,
        )
        return dict(row) if row else None

    async def get_all_level_rewards(self, guild_id: str) -> List[Dict[str, Any]]:
        rows = await self._require_pool().fetch(
            "SELECT * FROM level_rewards WHERE guild_id = $1 ORDER BY level ASC",
            guild_id,
        )
        return [dict(row) for row in rows]

    async def remove_level_reward(self, guild_id: str, level: int) -> bool:
        result = await self._require_pool().execute(
            "DELETE FROM level_rewards WHERE guild_id = $1 AND level = $2",
            guild_id,
            level,
        )
        return result == "DELETE 1"

    async def get_guild_settings(self, guild_id: str) -> Dict[str, Any]:
        row = await self._require_pool().fetchrow(
            """
            INSERT INTO guild_settings (guild_id) VALUES ($1)
            ON CONFLICT(guild_id) DO UPDATE SET guild_id = excluded.guild_id
            RETURNING *
            """,
            guild_id,
        )
        return dict(row)

    async def update_guild_settings(self, guild_id: str, **kwargs: Any) -> None:
        values = [(key, value) for key, value in kwargs.items() if key in ALLOWED_SETTINGS]
        if not values:
            return
        columns = ["guild_id"] + [key for key, _ in values]
        params = [guild_id] + [value for _, value in values]
        placeholders = ", ".join(f"${index}" for index in range(1, len(params) + 1))
        updates = ", ".join(f"{key} = excluded.{key}" for key, _ in values)
        await self._require_pool().execute(
            f"""
            INSERT INTO guild_settings ({', '.join(columns)}) VALUES ({placeholders})
            ON CONFLICT(guild_id) DO UPDATE SET {updates}
            """,
            *params,
        )

    async def log_welcome(self, guild_id: str, user_id: str, username: str) -> None:
        await self._require_pool().execute(
            "INSERT INTO welcome_logs (guild_id, user_id, username) VALUES ($1, $2, $3)",
            guild_id,
            user_id,
            username,
        )
