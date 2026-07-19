"""Helpers robustesse — retry, boucle infinie."""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")
logger = logging.getLogger(__name__)


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 4,
    base_delay: float = 2.0,
    label: str = "operation",
) -> T:
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return await fn()
        except Exception as exc:
            last_exc = exc
            # Erreurs permanentes Telegram — inutile de réessayer
            name = type(exc).__name__
            msg = str(exc).lower()
            if name in ("Forbidden", "InvalidToken", "BadRequest") or "blocked" in msg:
                raise
            if i + 1 >= attempts:
                break
            wait = min(60.0, base_delay * (2**i))
            logger.warning(
                "%s echoue (%s) — retry %d/%d dans %.0fs",
                label,
                exc,
                i + 1,
                attempts,
                wait,
            )
            await asyncio.sleep(wait)
    assert last_exc is not None
    raise last_exc
