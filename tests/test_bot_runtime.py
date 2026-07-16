import asyncio
import importlib

import reliability


def test_health_endpoints_reflect_liveness_and_readiness():
    appmod = importlib.import_module("bot")
    client = appmod.app.test_client()

    appmod.readiness.set_not_ready()
    assert client.get("/live").status_code == 200
    assert client.get("/health").status_code == 503

    appmod.readiness.set_ready()
    assert client.get("/health").status_code == 200
    appmod.readiness.set_not_ready()


def test_startup_retries_inside_one_process(monkeypatch):
    appmod = importlib.import_module("bot")

    class FakeHTTPError(Exception):
        def __init__(self):
            self.status = 429
            self.retry_after = 0

    class FakeBot:
        def __init__(self):
            self.attempts = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def start(self, token, reconnect=True):
            assert token == "dummy"
            assert reconnect is True
            self.attempts += 1
            if self.attempts < 3:
                raise FakeHTTPError()

        async def close(self):
            return None

    async def scenario():
        fake = FakeBot()
        monkeypatch.setattr(appmod, "bot", fake)
        monkeypatch.setattr(appmod.discord, "HTTPException", FakeHTTPError)
        policy = reliability.StartupBackoff(
            base=0,
            factor=1,
            cap=0,
            max_retries=3,
            jitter=0,
            cooldown=0,
        )
        await appmod._run_bot("dummy", backoff=policy)
        assert fake.attempts == 3

    asyncio.run(scenario())


def test_signal_handler_marks_not_ready_and_closes(monkeypatch):
    appmod = importlib.import_module("bot")

    class FakeBot:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    class FakeLoop:
        def __init__(self):
            self.handlers = []
            self.tasks = []

        def add_signal_handler(self, _signal, callback):
            self.handlers.append(callback)

        def create_task(self, coro):
            task = asyncio.create_task(coro)
            self.tasks.append(task)
            return task

    async def scenario():
        fake_bot = FakeBot()
        fake_loop = FakeLoop()
        shutdown = asyncio.Event()
        monkeypatch.setattr(appmod, "bot", fake_bot)
        appmod.readiness.set_ready()

        appmod._install_signal_handlers(fake_loop, shutdown)
        assert fake_loop.handlers
        fake_loop.handlers[0]()
        await asyncio.gather(*fake_loop.tasks)

        assert shutdown.is_set()
        assert fake_bot.closed
        assert not appmod.readiness.is_ready()

    asyncio.run(scenario())
