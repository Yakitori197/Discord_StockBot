"""Select a durable PostgreSQL backend or the local SQLite fallback."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Optional

from .base import Storage
from .postgres import PostgresStorage
from .sqlite import SQLiteStorage


def _truthy(value: Optional[str]) -> bool:
    return bool(value and value.strip().lower() in {"1", "true", "yes", "on"})


def create_storage(environ: Optional[Mapping[str, str]] = None) -> Storage:
    """Create a backend without logging any connection details."""
    values = os.environ if environ is None else environ
    database_url = values.get("DATABASE_URL")
    if database_url:
        return PostgresStorage(database_url)
    if _truthy(values.get("REQUIRE_DURABLE_STORAGE")):
        raise RuntimeError("durable storage is required but not configured")
    return SQLiteStorage(values.get("DB_PATH", "data/discord_bot.db"))