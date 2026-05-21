"""Verifie imports, config, detection, guards anti-crash."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []


def ok(msg: str) -> None:
    print(f"  OK  {msg}")


def fail(msg: str) -> None:
    print(f"  FAIL {msg}")
    FAILURES.append(msg)


def check_imports() -> None:
    print("=== Imports ===")
    try:
        from main import AAVEEqhEqlBot
        from config import get_config
        from scanner.market_data import MarketDataService
        from scanner.liquidity_detector import LiquidityDetector

        c = get_config()
        assert c.scan.ohlcv_limit >= 300
        ok(f"config ohlcv_limit={c.scan.ohlcv_limit}")
        ok("modules")
    except Exception as exc:
        fail(f"imports: {exc}")


def check_empty_guards() -> None:
    print("=== Guards donnees vides ===")
    from scanner.market_data import MarketDataService
    from config import get_config
    import pandas as pd

    m = MarketDataService(get_config())
    try:
        m._to_df([])
        fail("_to_df devrait rejeter liste vide")
    except ValueError:
        ok("_to_df liste vide")

    empty = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    try:
        m.last_closed_ts(empty)
        fail("last_closed_ts devrait rejeter df vide")
    except (ValueError, IndexError):
        ok("last_closed_ts df vide")


async def check_detection() -> None:
    print("=== Detection KuCoin ===")
    import os

    os.environ["EXCHANGE"] = "kucoin"
    os.environ["EXCHANGE_FALLBACK"] = "true"
    from config import get_config
    from scanner.market_data import MarketDataService
    from scanner.liquidity_detector import LiquidityDetector

    c = get_config()
    market = MarketDataService(c)
    det = LiquidityDetector(c)
    try:
        await market.start()
        sym, tf = "AAVE/USDT", "5m"
        df = await market.load_history(sym, tf)
        if len(df) < c.scan.min_bars:
            fail(f"pas assez de barres: {len(df)}")
        else:
            ok(f"{len(df)} barres via {market.exchange_id}")
        det.warmup(sym, tf, df)
        closed = market.closed_bars(df)
        ts = market.last_closed_ts(df)
        r = det.scan_live(sym, tf, closed, ts)
        ok(f"scan_live -> {len(r.new_zones)} zones (test)")
    except Exception as exc:
        fail(f"detection: {exc}")
    finally:
        await market.close()


def check_crash_handlers() -> None:
    print("=== Anti-crash code ===")
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    checks = [
        ("while True", "boucle infinie main"),
        ("except Exception", "exceptions captees"),
        ("_ensure_history", "retry historique"),
        ("_safe_telegram", "telegram protege"),
        ("refresh_if_due", "refresh aligne bougie"),
        ("_scan_cached", "scan depuis cache"),
    ]
    for needle, label in checks:
        if needle in src:
            ok(label)
        else:
            fail(f"manquant: {label}")


async def check_rate_budget() -> None:
    print("=== Budget API ===")
    import os
    import time

    from utils.api_budget import acquire_request_slot, _MAX_PER_MINUTE, _MIN_INTERVAL_SEC

    t0 = time.monotonic()
    for _i in range(3):
        await acquire_request_slot()
    elapsed = time.monotonic() - t0
    ok(f"max {_MAX_PER_MINUTE}/min, interval {_MIN_INTERVAL_SEC}s (3 req ~{elapsed:.1f}s)")
    from scanner.ohlcv_providers import KUCOIN_MAX_LIMIT, RAILWAY_CHAIN

    ok(f"KuCoin max {KUCOIN_MAX_LIMIT} barres/req, chain={RAILWAY_CHAIN}")
    from config import get_config
    from scanner.market_data import MarketDataService

    m = MarketDataService(get_config())
    m._provider_chain = ["kucoin", "binance_vision", "mexc"]
    bulk = m._chain_for_history()
    live = m._chain_for_live()
    if "kucoin" in bulk:
        fail("KuCoin ne doit pas etre utilise pour historique")
    else:
        ok(f"historique -> {bulk[0]} en premier")
    if "kucoin" in live and os.getenv("LIVE_USE_KUCOIN", "").lower() not in ("1", "true", "yes"):
        fail("KuCoin ne doit pas etre en live par defaut")
    else:
        ok(f"live refresh -> {live[0]} en premier")


async def main() -> None:
    check_imports()
    check_empty_guards()
    check_crash_handlers()
    await check_rate_budget()
    await check_detection()
    print()
    if FAILURES:
        print(f"ECHEC: {len(FAILURES)} probleme(s)")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("Tous les controles OK")


if __name__ == "__main__":
    asyncio.run(main())
