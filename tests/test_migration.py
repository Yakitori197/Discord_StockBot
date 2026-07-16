import asyncio

from scripts.migrate_sqlite_to_postgres import (
    TABLE_COLUMNS,
    _checksum,
    _print_summary,
    load_sqlite_snapshot,
)
from storage.sqlite import SQLiteStorage


def test_sqlite_snapshot_is_complete_normalized_and_quiet(tmp_path, capsys):
    async def build_source(path):
        storage = SQLiteStorage(str(path))
        await storage.initialize()
        try:
            await storage.add_xp("guild", "user", "Private Marker", 100)
            await storage.add_level_reward("guild", 2, "role", "Example Role")
            await storage.update_guild_settings("guild", xp_per_message=25)
            await storage.log_welcome("guild", "new-user", "New Member")
        finally:
            await storage.close()

    source = tmp_path / "source.db"
    asyncio.run(build_source(source))
    snapshot = load_sqlite_snapshot(source, "Asia/Taipei")

    assert set(snapshot) == set(TABLE_COLUMNS)
    assert {table: len(rows) for table, rows in snapshot.items()} == {
        "user_levels": 1,
        "level_rewards": 1,
        "guild_settings": 1,
        "welcome_logs": 1,
    }
    assert snapshot["user_levels"][0]["last_xp_time"].utcoffset().total_seconds() == 0

    _print_summary(snapshot, "validated")
    output = capsys.readouterr().out
    assert "Private Marker" not in output
    assert "New Member" not in output
    assert "user_levels: 1 rows" in output


def test_snapshot_checksum_is_deterministic():
    first = [{"id": 1, "guild_id": "g", "xp": 10}]
    second = [{"xp": 10, "guild_id": "g", "id": 1}]
    assert _checksum(first) == _checksum(second)
