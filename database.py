"""Async storage facade used by the Discord cogs."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from storage import Storage, calculate_level, create_storage, xp_for_level

_storage: Optional[Storage] = None
_initialize_lock = asyncio.Lock()


async def initialize(storage: Optional[Storage] = None) -> None:
    global _storage
    async with _initialize_lock:
        if _storage is not None:
            return
        candidate = storage or create_storage()
        await candidate.initialize()
        _storage = candidate


async def close() -> None:
    global _storage
    async with _initialize_lock:
        if _storage is not None:
            await _storage.close()
            _storage = None


def backend_name() -> str:
    return _require_storage().backend_name


def _require_storage() -> Storage:
    if _storage is None:
        raise RuntimeError("database is not initialized")
    return _storage


async def get_user_level(guild_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    return await _require_storage().get_user_level(guild_id, user_id)


async def add_xp(guild_id: str, user_id: str, username: str, xp_amount: int) -> Tuple[int, int, bool]:
    return await _require_storage().add_xp(guild_id, user_id, username, xp_amount)


async def get_leaderboard(guild_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    return await _require_storage().get_leaderboard(guild_id, limit)


async def get_user_rank(guild_id: str, user_id: str) -> Optional[int]:
    return await _require_storage().get_user_rank(guild_id, user_id)


async def add_level_reward(guild_id: str, level: int, role_id: str, role_name: str) -> None:
    await _require_storage().add_level_reward(guild_id, level, role_id, role_name)


async def get_level_reward(guild_id: str, level: int) -> Optional[Dict[str, Any]]:
    return await _require_storage().get_level_reward(guild_id, level)


async def get_all_level_rewards(guild_id: str) -> List[Dict[str, Any]]:
    return await _require_storage().get_all_level_rewards(guild_id)


async def remove_level_reward(guild_id: str, level: int) -> bool:
    return await _require_storage().remove_level_reward(guild_id, level)


async def get_guild_settings(guild_id: str) -> Dict[str, Any]:
    return await _require_storage().get_guild_settings(guild_id)


async def update_guild_settings(guild_id: str, **kwargs: Any) -> None:
    await _require_storage().update_guild_settings(guild_id, **kwargs)


async def log_welcome(guild_id: str, user_id: str, username: str) -> None:
    await _require_storage().log_welcome(guild_id, user_id, username)


__all__ = [
    "initialize", "close", "backend_name", "calculate_level", "xp_for_level",
    "get_user_level", "add_xp", "get_leaderboard", "get_user_rank",
    "add_level_reward", "get_level_reward", "get_all_level_rewards",
    "remove_level_reward", "get_guild_settings", "update_guild_settings", "log_welcome",
]
