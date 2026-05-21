"""Budget global de requetes HTTP — evite 429 KuCoin/Railway."""

from __future__ import annotations

import asyncio
import os
import time
from collections import deque

_lock = asyncio.Lock()
_timestamps: deque[float] = deque()
# Railway IP partagee : max ~8 req / 60s (marge large)
_MAX_PER_MINUTE = int(os.getenv("API_MAX_REQUESTS_PER_MINUTE", "8"))
_MIN_INTERVAL_SEC = float(os.getenv("API_MIN_INTERVAL_SEC", "8.0"))
_last_request_at = 0.0

# Circuit breaker KuCoin apres 429
_kucoin_banned_until = 0.0
KUCOIN_BAN_SEC = float(os.getenv("KUCOIN_BAN_SECONDS", "600"))


def kucoin_is_banned() -> bool:
    return time.monotonic() < _kucoin_banned_until


def ban_kucoin(seconds: float = KUCOIN_BAN_SEC) -> None:
    global _kucoin_banned_until
    _kucoin_banned_until = time.monotonic() + seconds


async def acquire_request_slot() -> None:
    """Attend si necessaire avant une requete HTTP."""
    global _last_request_at
    async with _lock:
        now = time.monotonic()
        wait_interval = _MIN_INTERVAL_SEC - (now - _last_request_at)
        if wait_interval > 0:
            await asyncio.sleep(wait_interval)
            now = time.monotonic()

        while _timestamps and now - _timestamps[0] > 60.0:
            _timestamps.popleft()

        if len(_timestamps) >= _MAX_PER_MINUTE:
            sleep_for = 60.0 - (now - _timestamps[0]) + 0.5
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            now = time.monotonic()
            while _timestamps and now - _timestamps[0] > 60.0:
                _timestamps.popleft()

        _timestamps.append(time.monotonic())
        _last_request_at = time.monotonic()
