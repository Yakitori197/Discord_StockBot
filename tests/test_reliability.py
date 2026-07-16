"""reliability.py 單元測試：backoff / 429 / 錯誤分類 / 同步閘門 / readiness / 連續重啟模擬。"""

import pytest

from reliability import (
    StartupBackoff,
    ReadinessState,
    should_sync_commands,
    classify_startup_error,
    parse_retry_after,
)


# --------------------------------------------------------------------- #
# should_sync_commands —— 一般重啟不可重複同步全域指令
# --------------------------------------------------------------------- #

class TestShouldSyncCommands:
    @pytest.mark.parametrize("val", ["true", "True", "1", "yes", "on", " ON "])
    def test_enabled_values(self, val):
        assert should_sync_commands(val) is True

    @pytest.mark.parametrize("val", [None, "", "false", "0", "no", "off", "random"])
    def test_disabled_values(self, val):
        assert should_sync_commands(val) is False

    def test_default_env_missing_is_no_sync(self):
        # 未設環境變數（None）→ 不同步，這是「一般重啟」的預設行為
        assert should_sync_commands(None) is False

    def test_simulated_repeated_restarts_do_not_sync(self):
        """模擬連續 10 次重啟（環境變數未開）→ 一次都不會同步全域指令。"""
        env_on_normal_restart = None
        sync_calls = sum(1 for _ in range(10) if should_sync_commands(env_on_normal_restart))
        assert sync_calls == 0

    def test_explicit_one_off_deploy_syncs_once(self):
        """只有明確設 true 的那一次部署會同步。"""
        assert should_sync_commands("true") is True


# --------------------------------------------------------------------- #
# classify_startup_error —— 日誌要能分辨錯誤類型
# --------------------------------------------------------------------- #

class _Exc(Exception):
    def __init__(self, status=None, name=None):
        super().__init__(name or "")
        if status is not None:
            self.status = status
        if name:
            type(self).__name__ = name


def _make(status=None, clsname="SomeError"):
    e = type(clsname, (Exception,), {})()
    if status is not None:
        e.status = status
    return e


class TestClassifyStartupError:
    def test_429_is_rate_limited(self):
        assert classify_startup_error(_make(status=429)) == "rate_limited"

    def test_ratelimit_by_name(self):
        assert classify_startup_error(_make(clsname="RateLimited")) == "rate_limited"

    def test_401_is_auth_failed(self):
        assert classify_startup_error(_make(status=401)) == "auth_failed"

    def test_login_failure_by_name(self):
        assert classify_startup_error(_make(clsname="LoginFailure")) == "auth_failed"

    def test_connection_error_is_network(self):
        assert classify_startup_error(ConnectionError("boom")) == "network"

    def test_timeout_is_network(self):
        assert classify_startup_error(TimeoutError("slow")) == "network"

    def test_unknown_is_other(self):
        assert classify_startup_error(ValueError("weird")) == "other"


# --------------------------------------------------------------------- #
# parse_retry_after
# --------------------------------------------------------------------- #

class TestParseRetryAfter:
    def test_retry_after_attribute(self):
        e = _make(status=429)
        e.retry_after = 7.5
        assert parse_retry_after(e) == 7.5

    def test_retry_after_header(self):
        e = _make(status=429)
        e.response = type("R", (), {"headers": {"Retry-After": "12"}})()
        assert parse_retry_after(e) == 12.0

    def test_missing_returns_default(self):
        assert parse_retry_after(_make(status=500), default=None) is None

    def test_negative_clamped_to_zero(self):
        e = _make(status=429)
        e.retry_after = -3
        assert parse_retry_after(e) == 0.0


# --------------------------------------------------------------------- #
# StartupBackoff —— 指數退避 + jitter + 遵守 Retry-After + 上限 + 最大次數
# --------------------------------------------------------------------- #

class TestStartupBackoff:
    def test_exponential_growth_no_jitter(self):
        b = StartupBackoff(base=2.0, factor=2.0, jitter=0.0, max_retries=5, _rng=lambda: 0.0)
        # jitter=0 → 回傳 raw：2, 4, 8, 16, 32
        assert [b.next_delay() for _ in range(5)] == [2.0, 4.0, 8.0, 16.0, 32.0]

    def test_cap_is_respected(self):
        b = StartupBackoff(base=100.0, factor=10.0, cap=300.0, jitter=0.0, max_retries=4, _rng=lambda: 0.0)
        delays = [b.next_delay() for _ in range(4)]
        assert all(d <= 300.0 for d in delays)
        assert delays[-1] == 300.0

    def test_full_jitter_lower_bound(self):
        # rng=1.0 → 下界 raw*(1-jitter)
        b = StartupBackoff(base=10.0, factor=1.0, jitter=0.5, max_retries=3, _rng=lambda: 1.0)
        assert b.next_delay() == pytest.approx(5.0)  # 10 * (1 - 0.5*1)

    def test_jitter_within_bounds_random(self):
        seq = iter([0.3, 0.7, 0.1])
        b = StartupBackoff(base=8.0, factor=1.0, jitter=0.5, max_retries=3, _rng=lambda: next(seq))
        for _ in range(3):
            d = b.next_delay()
            assert 4.0 <= d <= 8.0  # [raw*(1-0.5), raw]

    def test_retry_after_is_honored_minimum(self):
        # 有 retry_after 時，延遲至少為 retry_after（不可低於 Discord 要求）
        b = StartupBackoff(base=2.0, jitter=0.5, max_retries=5, _rng=lambda: 0.0)
        assert b.next_delay(retry_after=10.0) == pytest.approx(10.0)

    def test_retry_after_adds_jitter_on_top(self):
        b = StartupBackoff(base=2.0, jitter=0.5, max_retries=5, _rng=lambda: 1.0)
        # 10 + jitter(0.5)*base(2)*rng(1) = 11
        assert b.next_delay(retry_after=10.0) == pytest.approx(11.0)

    def test_max_retries_then_none(self):
        b = StartupBackoff(max_retries=3, jitter=0.0, _rng=lambda: 0.0)
        assert b.next_delay() is not None
        assert b.next_delay() is not None
        assert b.next_delay() is not None
        assert b.next_delay() is None  # 第 4 次超過上限 → None（放棄、進冷卻）
        assert b.has_retries_left() is False

    def test_reset(self):
        b = StartupBackoff(max_retries=2, jitter=0.0, _rng=lambda: 0.0)
        b.next_delay(); b.next_delay()
        assert b.next_delay() is None
        b.reset()
        assert b.has_retries_left() is True
        assert b.next_delay() is not None

    def test_no_fast_exit_loop_minimum_wait(self):
        """退避的第一次延遲應 > 0，確保不會 0 秒快速 exit/restart。"""
        b = StartupBackoff(base=2.0, jitter=0.5, max_retries=3, _rng=lambda: 0.9)
        assert b.next_delay() > 0.0


# --------------------------------------------------------------------- #
# ReadinessState —— liveness vs readiness
# --------------------------------------------------------------------- #

class TestReadinessState:
    def test_starts_not_ready_but_live(self):
        s = ReadinessState()
        assert s.is_live() is True
        assert s.is_ready() is False

    def test_becomes_ready(self):
        s = ReadinessState()
        s.set_ready()
        assert s.is_ready() is True

    def test_disconnect_sets_not_ready(self):
        s = ReadinessState()
        s.set_ready()
        s.set_not_ready()
        assert s.is_ready() is False
        assert s.is_live() is True  # 斷線仍 live（行程還在）

    def test_readiness_reflects_reconnect_cycle(self):
        s = ReadinessState()
        assert not s.is_ready()
        s.set_ready()      # on_ready
        assert s.is_ready()
        s.set_not_ready()  # on_disconnect
        assert not s.is_ready()
        s.set_ready()      # on_resumed
        assert s.is_ready()
