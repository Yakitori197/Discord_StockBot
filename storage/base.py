"""Storage contract shared by the SQLite and PostgreSQL backends."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple


def calculate_level(xp: int) -> int:
    """Return the level for an XP total."""
    if xp < 0:
        return 1
    return 1 + int(math.floor(math.sqrt(xp / 100)))


def xp_for_level(level: int) -> int:
    """Return the minimum XP required for a level."""
    if level <= 1:
        return 0
    return (level - 1) ** 2 * 100


class Storage(ABC):
    """Async persistence interface used by the Discord cogs."""

    backend_name = "unknown"

    @abstractmethod
    async def initialize(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def get_user_level(self, guild_id: str, user_id: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    async def add_xp(
        self, guild_id: str, user_id: str, username: str, xp_amount: int
    ) -> Tuple[int, int, bool]: ...

    @abstractmethod
    async def get_leaderboard(self, guild_id: str, limit: int = 10) -> List[Dict[str, Any]]: ...

    @abstractmethod
    async def get_user_rank(self, guild_id: str, user_id: str) -> Optional[int]: ...

    @abstractmethod
    async def add_level_reward(
        self, guild_id: str, level: int, role_id: str, role_name: str
    ) -> None: ...

    @abstractmethod
    async def get_level_reward(self, guild_id: str, level: int) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    async def get_all_level_rewards(self, guild_id: str) -> List[Dict[str, Any]]: ...

    @abstractmethod
    async def remove_level_reward(self, guild_id: str, level: int) -> bool: ...

    @abstractmethod
    async def get_guild_settings(self, guild_id: str) -> Dict[str, Any]: ...

    @abstractmethod
    async def update_guild_settings(self, guild_id: str, **kwargs: Any) -> None: ...

    @abstractmethod
    async def log_welcome(self, guild_id: str, user_id: str, username: str) -> None: ...
