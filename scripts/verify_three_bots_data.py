"""Verification live : les 3 bots recoivent bien des donnees (KuCoin prioritaires)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import get_config
from scanner.market_data import MarketDataService
from trading.clean_sticky_engine import SYMBOL as CS_SYM
from trading.clean_sticky_engine import _tf_str
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
        refreshed = await market.refresh_if_due(symbol, tf)
        print(f"[{label}] refresh_if_due={refreshed} provider={market.exchange_id}")
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
            f"[TRADER_WARMUP] OK bars={len(hist)} "
            f"tf={cfg.trading.signal_tf_min}m "
            f"last={hist.index[-1]} close={float(hist['close'].iloc[-1]):.4f}"
        )
        print(
            "[TRADER_WARMUP] NOTE: warmup vient de Binance Vision/MEXC "
            "(pas KuCoin) — live 5m est KuCoin"
        )
        return True, "binance_vision/mexc"
    except Exception as exc:
        print(f"[TRADER_WARMUP] FAIL: {type(exc).__name__}: {exc}")
        return False, str(exc)


async def check_shorts() -> tuple[bool, str]:
    cfg = get_config()
    eng = ShortHunterEngine(cfg, telegram=None)
    try:
        btc = await eng._fetch_btc_long()
        if btc is None or btc.empty:
            print("[SHORTS] FAIL BTC vide")
            return False, "btc empty"
        print(
            f"[SHORTS] BTC OK bars={len(btc)} last={btc.index[-1]} "
            f"close={float(btc['close'].iloc[-1]):.2f}"
        )
        ok_n = 0
        for sym in ("ETHUSDT", "AAVEUSDT", "SOLUSDT"):
            df = await eng._fetch_klines(sym, 50)
            if df is None or df.empty:
                print(f"[SHORTS] FAIL {sym}")
            else:
                ok_n += 1
                print(
                    f"[SHORTS] OK {sym} bars={len(df)} last={df.index[-1]} "
                    f"close={float(df['close'].iloc[-1]):.4f}"
                )
            await asyncio.sleep(0.15)
        print(
            "[SHORTS] NOTE: Short Hunter utilise Binance Vision/MEXC 2h "
            "(pas MarketDataService / pas KuCoin)"
        )
        return ok_n == 3, "binance_vision/mexc"
    finally:
        await eng.close()


async def check_dual_poll_contention() -> None:
    """Simule 2 MarketDataService (Clean Sticky + Trader) sur AAVE 5m."""
    cfg = get_config()
    a = MarketDataService(cfg)
    b = MarketDataService(cfg)
    await a.start()
    await b.start()
    try:
        await asyncio.gather(
            a.load_history(TR_SYM, TF_5M),
            b.load_history(CS_SYM, _tf_str(cfg.clean_sticky.signal_tf_min)),
        )
        print(
            f"[DUAL] OK deux instances paralleles — "
            f"A={a.data_source_label()} B={b.data_source_label()}"
        )
        if a.exchange_id != "kucoin" or b.exchange_id != "kucoin":
            print(
                f"[DUAL] WARN providers A={a.exchange_id} B={b.exchange_id} "
                f"(risque fallback / budget API)"
            )
    except Exception as exc:
        print(f"[DUAL] FAIL: {type(exc).__name__}: {exc}")
    finally:
        await a.close()
        await b.close()


async def main() -> None:
    print("=== Verif donnees / scans 3 bots ===\n")
    cfg = get_config()
    print(f"EXCHANGE={cfg.exchange.id} fallback={cfg.exchange.fallback}")
    print(f"Clean Sticky TF={cfg.clean_sticky.signal_tf_min}m")
    print(f"Trader live TF=5m signal={cfg.trading.signal_tf_min}m")
    print(f"Shorts TF=2h universe={len(cfg.shorts.universe)} alts\n")

    r1 = await check_market(
        "CLEAN_STICKY", CS_SYM, _tf_str(cfg.clean_sticky.signal_tf_min)
    )
    print()
    r2 = await check_market("TRADER", TR_SYM, TF_5M)
    print()
    r_w = await check_trader_warmup()
    print()
    r3 = await check_shorts()
    print()
    await check_dual_poll_contention()

    print("\n=== RESULTAT ===")
    rows = [
        ("Clean Sticky (live)", r1),
        ("Paper Trader (live 5m)", r2),
        ("Paper Trader (warmup)", r_w),
        ("Short Hunter", r3),
    ]
    all_ok = True
    for name, (ok, detail) in rows:
        status = "OK" if ok else "FAIL"
        kucoin = "KUCOIN" if "kucoin" in detail.lower() or detail.upper() == "KUCOIN" else detail
        print(f"  {status:4} {name}: {kucoin}")
        if not ok:
            all_ok = False

    # Verdict KuCoin pour les bots AAVE live
    live_kucoin = r1[0] and r2[0]
    if live_kucoin and r3[0]:
        print(
            "\nVerdict: Clean Sticky + Trader live = KuCoin OK "
            "(both_main partage 1 seul scan 5m)."
        )
        print(
            "Short Hunter = Binance Vision/MEXC 2h (necessaire: "
            "34 alts + ~3000 bougies BTC — KuCoin limite a 150/req)."
        )
        print(
            "Trader warmup 30m = Binance Vision (profondeur EMA 4h), "
            "puis fusion avec KuCoin live."
        )
    elif live_kucoin:
        print("\nVerdict: bots AAVE KuCoin OK, Short Hunter en erreur.")
    else:
        print("\nVerdict: probleme sur le flux KuCoin AAVE.")

    sys.exit(0 if (r1[0] and r2[0] and r3[0]) else 1)


if __name__ == "__main__":
    asyncio.run(main())
