"""Verification live : Tendance (KuCoin) + Chasseur (Binance/MEXC)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import get_config
from scanner.market_data import MarketDataService
from trading.engine import SYMBOL as TR_SYM
from trading.engine import TF_5M
from trading.short_engine import ShortHunterEngine
from trading.warmup import fetch_history_tf


async def check_market(label: str, symbol: str, tf: str) -> tuple[bool, str]:
    cfg = get_config()
    market = MarketDataService(cfg)
    await market.start()
    try:
        df = await market.load_history(symbol, tf)
        closed = market.closed_bars(df)
        src = market.data_source_label()
        last = closed.iloc[-1]
        print(
            f"[{label}] OK src={src} {symbol} {tf} "
            f"bars={len(df)} closed={len(closed)} "
            f"last={closed['timestamp'].iloc[-1]} close={float(last['close']):.4f}"
        )
        if src.upper() != "KUCOIN":
            print(f"[{label}] WARN: source attendue KUCOIN, obtenu {src}")
            return False, src
        return True, src
    except Exception as exc:
        print(f"[{label}] FAIL {symbol} {tf}: {type(exc).__name__}: {exc}")
        return False, str(exc)
    finally:
        await market.close()


async def check_trader_warmup() -> tuple[bool, str]:
    cfg = get_config()
    try:
        hist = await fetch_history_tf(cfg.trading.signal_tf_min, 200)
        print(
            f"[TENDANCE_WARMUP] OK bars={len(hist)} "
            f"tf={cfg.trading.signal_tf_min}m last={hist.index[-1]}"
        )
        return True, "binance_vision/mexc"
    except Exception as exc:
        print(f"[TENDANCE_WARMUP] FAIL: {type(exc).__name__}: {exc}")
        return False, str(exc)


async def check_shorts() -> tuple[bool, str]:
    cfg = get_config()
    eng = ShortHunterEngine(cfg, telegram=None)
    try:
        btc = await eng._fetch_btc_long()
        if btc is None or btc.empty:
            print("[CHASSEUR] FAIL BTC vide")
            return False, "btc empty"
        print(f"[CHASSEUR] BTC OK bars={len(btc)} last={btc.index[-1]}")
        ok_n = 0
        for sym in ("ETHUSDT", "AAVEUSDT", "SOLUSDT"):
            df = await eng._fetch_klines(sym, 50)
            if df is None or df.empty:
                print(f"[CHASSEUR] FAIL {sym}")
            else:
                ok_n += 1
                print(f"[CHASSEUR] OK {sym} bars={len(df)}")
            await asyncio.sleep(0.15)
        return ok_n == 3, "binance_vision/mexc"
    finally:
        await eng.close()


async def main() -> None:
    print("=== Verif donnees 2 bots ===\n")
    cfg = get_config()
    r1 = await check_market("TENDANCE", TR_SYM, TF_5M)
    print()
    r_w = await check_trader_warmup()
    print()
    r2 = await check_shorts()
    print("\n=== RESULTAT ===")
    print("tendance live", "OK" if r1[0] else "FAIL", r1[1])
    print("tendance warmup", "OK" if r_w[0] else "FAIL", r_w[1])
    print("chasseur", "OK" if r2[0] else "FAIL", r2[1])
    sys.exit(0 if (r1[0] and r2[0]) else 1)


if __name__ == "__main__":
    asyncio.run(main())
