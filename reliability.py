"""
可靠性工具（純邏輯，不相依 discord / flask，方便單元測試）。

- should_sync_commands：本次啟動是否執行全域斜線指令同步（預設否，避免每次重啟都同步 → Discord 429）
- classify_startup_error：把啟動例外分類為 rate_limited / auth_failed / network / other（duck-typing，不 import discord）
- parse_retry_after：盡力從例外取出 Retry-After 秒數
- StartupBackoff：指數退避 + jitter，遵守 Retry-After，設上限與最大重試次數
- ReadinessState：區分 liveness（行程存活）與 readiness（Discord 連線完成）
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Optional


def should_sync_commands(env_value) -> bool:
    """
    是否在本次啟動同步全域斜線指令。

    只有環境變數明確為 true/1/yes/on 才回傳 True。一般重啟一律不同步，
    避免對 Discord 全域指令 API 反覆呼叫而觸發 429。指令有變更時，由管理者
    以一次性部署（設 SYNC_COMMANDS_ON_START=true）或管理指令觸發同步。
    """
    if env_value is None:
        return False
    return str(env_value).strip().lower() in ("1", "true", "yes", "on")


def classify_startup_error(exc) -> str:
    """
    以 duck-typing 分類啟動例外（刻意不 import discord，方便測試）：
      - 'rate_limited'：HTTP 429 / 類名含 ratelimit / toomanyrequests
      - 'auth_failed'：401 / 403 / 類名含 login / unauthorized / forbidden / impropertoken
      - 'network'：ConnectionError / TimeoutError / 類名含 connection / timeout / dns / socket
      - 'other'：其他
    """
    status = getattr(exc, "status", None)
    if status is None:
        status = getattr(exc, "code", None)
    name = type(exc).__name__.lower()

    if status == 429 or "ratelimit" in name or "toomanyrequests" in name:
        return "rate_limited"
    if status in (401, 403) or any(k in name for k in ("login", "unauthorized", "forbidden", "impropertoken")):
        return "auth_failed"
    if isinstance(exc, (ConnectionError, TimeoutError)) or any(
        k in name for k in ("connection", "timeout", "dns", "socket")
    ):
        return "network"
    return "other"


def parse_retry_after(exc, default=None):
    """盡力從例外取出 Retry-After（秒）。取不到回傳 default。"""
    ra = getattr(exc, "retry_after", None)
    if ra is not None:
        try:
            return max(0.0, float(ra))
        except (TypeError, ValueError):
            pass
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if headers is not None:
        try:
            val = headers.get("Retry-After")
            if val is not None:
                return max(0.0, float(val))
        except (TypeError, ValueError, AttributeError):
            pass
    return default


@dataclass
class StartupBackoff:
    """
    啟動重試退避器：指數退避 + jitter、遵守 Retry-After、設上限與最大重試次數。

    next_delay(retry_after) 回傳本次應等待秒數；已達 max_retries 回傳 None（放棄 → 進入冷卻）。
    - 有 retry_after：至少等 Discord 要求的秒數，另加 0..(jitter*base) 抖動分散重啟。
    - 無 retry_after：base * factor^(n-1)，套 cap，再套 full-jitter 下界 [raw*(1-jitter), raw]。
    """

    base: float = 2.0
    factor: float = 2.0
    cap: float = 300.0
    max_retries: int = 6
    jitter: float = 0.5
    cooldown: float = 60.0
    attempt: int = 0
    _rng: Optional[Callable[[], float]] = field(default=None, repr=False)

    def _rand(self) -> float:
        return (self._rng or random.random)()

    def has_retries_left(self) -> bool:
        return self.attempt < self.max_retries

    def next_delay(self, retry_after: Optional[float] = None) -> Optional[float]:
        if self.attempt >= self.max_retries:
            return None
        self.attempt += 1
        r = self._rand()
        if retry_after is not None:
            ra = max(0.0, float(retry_after))
            # 尊重 Discord：至少等 retry_after，再加 0..(jitter*base) 秒抖動
            return min(ra + self.jitter * self.base * r, self.cap + ra)
        raw = min(self.base * (self.factor ** (self.attempt - 1)), self.cap)
        # full jitter：[raw*(1-jitter), raw]，避免多實例同時重試
        return raw * (1.0 - self.jitter * r)

    def reset(self) -> None:
        self.attempt = 0


class ReadinessState:
    """
    區分 liveness 與 readiness：
      - is_live()：行程存活（能回應即 True）
      - is_ready()：Discord 已連線且 on_ready 完成才 True

    Render 的 healthCheckPath 指向 liveness（/live），避免外部 Discord 暫時中斷時
    形成重啟循環；readiness（/health）另供連線狀態監控。
    """

    def __init__(self) -> None:
        self._ready = False

    def set_ready(self) -> None:
        self._ready = True

    def set_not_ready(self) -> None:
        self._ready = False

    def is_ready(self) -> bool:
        return self._ready

    def is_live(self) -> bool:  # noqa: D401 - 行程只要能執行到這裡就是 live
        return True
