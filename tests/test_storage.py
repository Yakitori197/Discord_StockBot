import asyncio

import pytest

import database
from storage.factory import create_storage
from storage.postgres import PostgresStorage
from storage.sqlite import SQLiteStorage


def run(coro):
    return asyncio.run(coro)


def test_factory_uses_sqlite_without_database_url(tmp_path):
    storage = create_storage({"DB_PATH": str(tmp_path / "local.db")})
    assert isinstance(storage, SQLiteStorage)


def test_factory_uses_postgres_without_exposing_url():
    marker = "postgresql://placeholder.invalid/test"
    storage = create_storage({"DATABASE_URL": marker})
    assert isinstance(storage, PostgresStorage)
    assert marker not in repr(storage)


def test_factory_refuses_ephemeral_fallback_when_durable_is_required():
    with pytest.raises(RuntimeError, match="durable storage"):
        create_storage({"REQUIRE_DURABLE_STORAGE": "true"})


def test_postgres_initialize_closes_pool_when_schema_setup_fails(monkeypatch):
    class BrokenConnection:
        async def execute(self, _schema):
            raise RuntimeError("schema setup failed")

    class AcquireContext:
        async def __aenter__(self):
            return BrokenConnection()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    class FakePool:
        def __init__(self):
            self.closed = False

        def acquire(self):
            return AcquireContext()

        async def close(self):
            self.closed = True

    pool = FakePool()

    async def fake_create_pool(**_kwargs):
        return pool

    monkeypatch.setattr("storage.postgres.asyncpg.create_pool", fake_create_pool)
    storage = PostgresStorage("postgresql://placeholder.invalid/test")

    with pytest.raises(RuntimeError, match="schema setup failed"):
        run(storage.initialize())

    assert pool.closed is True
    assert storage._pool is None

def test_sqlite_contract_and_concurrent_xp(tmp_path):
    async def scenario():
        storage = SQLiteStorage(str(tmp_path / "contract.db"))
        await storage.initialize()
        try:
            settings = await storage.get_guild_settings("guild-1")
            assert settings["xp_per_message"] == 15

            await storage.update_guild_settings(
                "guild-1", xp_per_message=20, xp_cooldown=30, ignored_field="nope"
            )
            settings = await storage.get_guild_settings("guild-1")
            assert settings["xp_per_message"] == 20
            assert settings["xp_cooldown"] == 30
            assert "ignored_field" not in settings

            await asyncio.gather(
                *[
                    storage.add_xp("guild-1", "user-1", "Example User", 5)
                    for _ in range(20)
                ]
            )
            user = await storage.get_user_level("guild-1", "user-1")
            assert user["xp"] == 100
            assert user["level"] == 2
            assert user["total_messages"] == 20
            assert await storage.get_user_rank("guild-1", "user-1") == 1

            leaders = await storage.get_leaderboard("guild-1")
            assert [row["user_id"] for row in leaders] == ["user-1"]

            await storage.add_level_reward("guild-1", 2, "role-1", "Example Role")
            reward = await storage.get_level_reward("guild-1", 2)
            assert reward["role_id"] == "role-1"
            await storage.add_level_reward("guild-1", 2, "role-2", "Updated Role")
            rewards = await storage.get_all_level_rewards("guild-1")
            assert len(rewards) == 1
            assert rewards[0]["role_id"] == "role-2"
            assert await storage.remove_level_reward("guild-1", 2) is True
            assert await storage.remove_level_reward("guild-1", 2) is False

            await storage.log_welcome("guild-1", "user-2", "New Member")
        finally:
            await storage.close()

    run(scenario())


def test_database_facade_lifecycle(tmp_path):
    async def scenario():
        storage = SQLiteStorage(str(tmp_path / "facade.db"))
        await database.initialize(storage)
        try:
            assert database.backend_name() == "sqlite"
            await database.add_xp("guild", "user", "Example", 100)
            assert (await database.get_user_level("guild", "user"))["level"] == 2
        finally:
            await database.close()

    run(scenario())
