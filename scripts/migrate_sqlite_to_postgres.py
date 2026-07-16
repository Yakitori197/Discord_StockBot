"""One-time, idempotent SQLite-to-PostgreSQL migration.

The script never prints row values or connection details. It validates exact
row counts and deterministic checksums inside one PostgreSQL transaction.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import asyncpg

from storage.postgres import POSTGRES_SCHEMA

TABLE_COLUMNS = {
    "user_levels": (
        "id", "guild_id", "user_id", "username", "xp", "level",
        "total_messages", "last_xp_time", "created_at",
    ),
    "level_rewards": ("id", "guild_id", "level", "role_id", "role_name"),
    "guild_settings": (
        "guild_id", "welcome_channel_id", "welcome_message", "rules_channel_id",
        "log_channel_id", "level_up_channel_id", "xp_per_message", "xp_cooldown",
    ),
    "welcome_logs": ("id", "guild_id", "user_id", "username", "joined_at"),
}

TIMESTAMP_COLUMNS = {
    "user_levels": {"last_xp_time", "created_at"},
    "welcome_logs": {"joined_at"},
}

UPSERTS = {
    "user_levels": """
        INSERT INTO user_levels
            (id, guild_id, user_id, username, xp, level, total_messages, last_xp_time, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET
            username = excluded.username,
            xp = excluded.xp,
            level = excluded.level,
            total_messages = excluded.total_messages,
            last_xp_time = excluded.last_xp_time,
            created_at = excluded.created_at
    """,
    "level_rewards": """
        INSERT INTO level_rewards (id, guild_id, level, role_id, role_name)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT(guild_id, level) DO UPDATE SET
            role_id = excluded.role_id,
            role_name = excluded.role_name
    """,
    "guild_settings": """
        INSERT INTO guild_settings
            (guild_id, welcome_channel_id, welcome_message, rules_channel_id,
             log_channel_id, level_up_channel_id, xp_per_message, xp_cooldown)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT(guild_id) DO UPDATE SET
            welcome_channel_id = excluded.welcome_channel_id,
            welcome_message = excluded.welcome_message,
            rules_channel_id = excluded.rules_channel_id,
            log_channel_id = excluded.log_channel_id,
            level_up_channel_id = excluded.level_up_channel_id,
            xp_per_message = excluded.xp_per_message,
            xp_cooldown = excluded.xp_cooldown
    """,
    "welcome_logs": """
        INSERT INTO welcome_logs (id, guild_id, user_id, username, joined_at)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT(id) DO UPDATE SET
            guild_id = excluded.guild_id,
            user_id = excluded.user_id,
            username = excluded.username,
            joined_at = excluded.joined_at
    """,
}


def _normalize_timestamp(value: Any, source_timezone: ZoneInfo) -> Any:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=source_timezone)
    return parsed.astimezone(timezone.utc)


def _canonical_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value


def _checksum(rows: list[dict[str, Any]]) -> str:
    canonical = [
        {key: _canonical_value(value) for key, value in sorted(row.items())}
        for row in rows
    ]
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_sqlite_snapshot(path: Path, source_timezone_name: str) -> dict[str, list[dict[str, Any]]]:
    """Load a consistent read-only snapshot without logging row values."""
    if not path.is_file():
        raise FileNotFoundError("SQLite source file does not exist")
    source_timezone = ZoneInfo(source_timezone_name)
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        existing = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        missing = set(TABLE_COLUMNS) - existing
        if missing:
            raise RuntimeError("SQLite source is missing required tables")

        snapshot: dict[str, list[dict[str, Any]]] = {}
        for table, columns in TABLE_COLUMNS.items():
            column_list = ", ".join(columns)
            order_by = "id" if "id" in columns else "guild_id"
            rows = [
                dict(row)
                for row in connection.execute(
                    f"SELECT {column_list} FROM {table} ORDER BY {order_by}"
                ).fetchall()
            ]
            for row in rows:
                for column in TIMESTAMP_COLUMNS.get(table, set()):
                    row[column] = _normalize_timestamp(row[column], source_timezone)
            snapshot[table] = rows
        return snapshot
    finally:
        connection.close()


async def _fetch_target_snapshot(
    connection: asyncpg.Connection,
) -> dict[str, list[dict[str, Any]]]:
    snapshot: dict[str, list[dict[str, Any]]] = {}
    for table, columns in TABLE_COLUMNS.items():
        column_list = ", ".join(columns)
        order_by = "id" if "id" in columns else "guild_id"
        rows = await connection.fetch(
            f"SELECT {column_list} FROM {table} ORDER BY {order_by}"
        )
        snapshot[table] = [dict(row) for row in rows]
    return snapshot


async def migrate(snapshot: dict[str, list[dict[str, Any]]], dsn: str) -> None:
    """Apply and validate the migration atomically."""
    connection = await asyncpg.connect(dsn=dsn, command_timeout=60)
    try:
        async with connection.transaction():
            await connection.execute(POSTGRES_SCHEMA)
            for table, columns in TABLE_COLUMNS.items():
                values = [tuple(row[column] for column in columns) for row in snapshot[table]]
                if values:
                    await connection.executemany(UPSERTS[table], values)

            for table in ("user_levels", "level_rewards", "welcome_logs"):
                await connection.execute(
                    f"""
                    SELECT setval(
                        pg_get_serial_sequence('{table}', 'id'),
                        COALESCE((SELECT MAX(id) FROM {table}), 1),
                        EXISTS(SELECT 1 FROM {table})
                    )
                    """
                )

            target = await _fetch_target_snapshot(connection)
            for table in TABLE_COLUMNS:
                if len(target[table]) != len(snapshot[table]):
                    raise RuntimeError(f"row count validation failed for {table}")
                if _checksum(target[table]) != _checksum(snapshot[table]):
                    raise RuntimeError(f"checksum validation failed for {table}")
    finally:
        await connection.close()


def _print_summary(snapshot: dict[str, list[dict[str, Any]]], label: str) -> None:
    print(label)
    for table in TABLE_COLUMNS:
        print(f"  {table}: {len(snapshot[table])} rows")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate SQLite data to PostgreSQL safely")
    parser.add_argument("--source", type=Path, required=True, help="Path to the SQLite snapshot")
    parser.add_argument(
        "--source-timezone",
        default="Asia/Taipei",
        help="Timezone applied to legacy timestamps without an offset",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Read and validate the source only")
    mode.add_argument("--apply", action="store_true", help="Apply one atomic migration")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    snapshot = load_sqlite_snapshot(args.source, args.source_timezone)
    _print_summary(snapshot, "SQLite snapshot validated")
    if args.dry_run:
        return 0

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("target database is not configured")
    asyncio.run(migrate(snapshot, dsn))
    _print_summary(snapshot, "PostgreSQL migration verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
