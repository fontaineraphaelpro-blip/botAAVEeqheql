"""Recherche WR >= 50% — max periode, max combinaisons. Local only."""

from __future__ import annotations

import asyncio
import sys
import time
from itertools import product
from pathlib import Path
from typing import List, Literal, Optional, Set

import aiohttp
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.backtest_optimize import Signal, build_signals, ema

FEE = 0.12
TF_SEC = 300
CHUNK = 1500


async def fetch_kucoin_months(months: int) -> pd.DataFrame:
    cache = ROOT / "data" / f"aave_5m_kucoin_{months}m.csv"
    if cache.exists():
        df = pd.read_csv(cache, parse_dates=["timestamp"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        print(f"[cache] {len(df)} bougies ({months}m)")
        return df

    end_at = int(time.time())
    start_at = end_at - int(months * 30.44 * 24 * 3600)
    rows: list = []
    chunk_sec = CHUNK * TF_SEC
    cur = start_at

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        while cur < end_at:
            ce = min(cur + chunk_sec, end_at)
            params = {"type": "5min", "symbol": "AAVE-USDT", "startAt": cur, "endAt": ce}
            async with session.get(
                "https://api.kucoin.com/api/v1/market/candles", params=params
            ) as resp:
                body = await resp.json()
            batch = body.get("data") or []
            for r in reversed(batch):
                ts = int(r[0])
                rows.append([ts * 1000, float(r[1]), float(r[3]), float(r[4]), float(r[2]), float(r[5])])
            cur = ce + 1
            if len(rows) % 15000 < CHUNK:
                print(f"  ... {len(rows)} bougies")
            await asyncio.sleep(0.15)

    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    cache.parent.mkdir(exist_ok=True)
    df.to_csv(cache, index=False)
    print(f"[saved] {len(df)} bougies")
    return df


def simulate_v2(
    df: pd.DataFrame,
    signals: List[Signal],
    hours: pd.Series,
    *,
    signal_kinds: Set[str],
    entry: str,
    side_filter: str,
    rr: float,
    max_hold: int,
    max_zone_pct: float,
    min_body: float,
    min_score: float,
    trend: str,
    hour_rng: tuple[int, int],
    tp_mode: Literal["rr", "mid", "fixed"],
    fixed_tp_pct: float,
    sl_buffer: float,
    use_detect: bool,
) -> dict:
    kinds = set(signal_kinds)
    if use_detect:
        kinds |= {k.replace("_SWEEP", "_detect") for k in kinds if "SWEEP" in k}
        kinds |= {k for k in kinds if "detect" in k}

    closes = df["close"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    opens = df["open"].to_numpy()
    ema50 = ema(df["close"], 50).to_numpy()
    n = len(df)
    h0, h1 = hour_rng

    by_bar: dict[int, list[Signal]] = {}
    for s in signals:
        if s.kind in kinds and h0 <= hours.iloc[s.bar] < h1:
            by_bar.setdefault(s.bar, []).append(s)

    pnls: list[float] = []
    busy = 0
    i = 0
    while i < n:
        if i < busy:
            i += 1
            continue
        for s in by_bar.get(i, []):
            if s.score < min_score:
                continue
            zp = (s.top - s.bottom) / s.sweep_level * 100
            if zp > max_zone_pct:
                continue
            if side_filter == "short" and s.side != "short":
                continue
            if side_filter == "long" and s.side != "long":
                continue

            eb, ep = i, float(closes[i])
            if entry == "confirm_next" and i + 1 < n:
                nc = float(closes[i + 1])
                if s.side == "short" and nc >= s.sweep_level:
                    continue
                if s.side == "long" and nc <= s.sweep_level:
                    continue
                eb, ep = i + 1, nc
            elif entry == "confirm_same":
                if s.side == "short" and ep >= s.sweep_level:
                    continue
                if s.side == "long" and ep <= s.sweep_level:
                    continue

            if trend == "counter_ema":
                e = ema50[eb]
                if s.side == "short" and ep < e:
                    continue
                if s.side == "long" and ep > e:
                    continue
            elif trend == "with_ema":
                e = ema50[eb]
                if s.side == "long" and ep < e:
                    continue
                if s.side == "short" and ep > e:
                    continue

            if min_body > 0:
                rng = float(highs[eb]) - float(lows[eb])
                body = abs(float(closes[eb]) - float(opens[eb]))
                if rng <= 0 or body / rng < min_body:
                    continue

            if s.side == "short":
                sl = max(s.top, float(highs[eb])) * (1 + sl_buffer)
                risk = max(sl - ep, ep * 0.002)
                if tp_mode == "mid":
                    tp = (s.top + s.bottom) / 2
                elif tp_mode == "fixed":
                    tp = ep * (1 - fixed_tp_pct / 100)
                else:
                    tp = ep - rr * risk
            else:
                sl = min(s.bottom, float(lows[eb])) * (1 - sl_buffer)
                risk = max(ep - sl, ep * 0.002)
                if tp_mode == "mid":
                    tp = (s.top + s.bottom) / 2
                elif tp_mode == "fixed":
                    tp = ep * (1 + fixed_tp_pct / 100)
                else:
                    tp = ep + rr * risk

            pnl = None
            for j in range(eb + 1, min(eb + max_hold + 1, n)):
                hi, lo = float(highs[j]), float(lows[j])
                if s.side == "long":
                    if lo <= sl:
                        pnl = (sl - ep) / ep * 100 - FEE
                        break
                    if hi >= tp:
                        pnl = (tp - ep) / ep * 100 - FEE
                        break
                else:
                    if hi >= sl:
                        pnl = (ep - sl) / ep * 100 - FEE
                        break
                    if lo <= tp:
                        pnl = (ep - tp) / ep * 100 - FEE
                        break
            if pnl is None and eb + max_hold < n:
                cl = float(closes[eb + max_hold])
                pnl = ((cl - ep) / ep if s.side == "long" else (ep - cl) / ep) * 100 - FEE

            if pnl is not None:
                pnls.append(pnl)
                busy = eb + max_hold + 1
                break
        i += 1

    if not pnls:
        return {"n": 0, "wr": 0.0, "pnl": 0.0, "pf": 0.0}

    wins = [p for p in pnls if p > 0]
    wr = len(wins) / len(pnls) * 100
    pnl = sum(pnls)
    gl = abs(sum(p for p in pnls if p <= 0)) or 1e-9
    pf = sum(wins) / gl if wins else 0.0
    return {"n": len(pnls), "wr": wr, "pnl": pnl, "pf": pf}


async def run_search(months: int) -> None:
    df = await fetch_kucoin_months(months)
    hours = df["timestamp"].dt.hour
    print(f"Periode: {df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()}")
    print("Generation signaux (plusieurs minutes)...")
    signals = build_signals(df)
    print(f"Signaux: {len(signals)} | sweeps: {sum(1 for s in signals if 'SWEEP' in s.kind)}\n")

    hits: list[tuple] = []
    total = 0

    grids = []
    for kinds, side, entry, rr, hold, zmax, body, trend, hrs, tpm, ftp, det in product(
        [{"EQH_SWEEP"}, {"EQL_SWEEP"}, {"EQH_SWEEP", "EQL_SWEEP"}],
        ["short", "long", "both"],
        ["immediate", "confirm_next", "confirm_same"],
        [0.4, 0.6, 0.8, 1.0, 1.2, 1.5],
        [4, 6, 8, 12, 18],
        [0.15, 0.35, 0.8, 99.0],
        [0.0, 0.5, 0.65],
        ["none", "counter_ema"],
        [(0, 24), (8, 20), (12, 22)],
        ["rr", "mid", "fixed"],
        [0.25, 0.4, 0.6],
        [False, True],
    ):
        if kinds == {"EQH_SWEEP"} and side == "long":
            continue
        if kinds == {"EQL_SWEEP"} and side == "short":
            continue
        if tpm == "fixed":
            if rr != 1.0:
                continue
        elif ftp != 0.25:
            continue
        grids.append((kinds, side, entry, rr, hold, zmax, body, trend, hrs, tpm, ftp, det))

    print(f"Grille: {len(grids)} combinaisons | objectif WR >= 50% | min 35 trades\n")

    for g in grids:
        kinds, side, entry, rr, hold, zmax, body, trend, hrs, tpm, ftp, det = g
        total += 1
        m = simulate_v2(
            df,
            signals,
            hours,
            signal_kinds=set(kinds),
            entry=entry,
            side_filter=side,
            rr=rr,
            max_hold=hold,
            max_zone_pct=zmax,
            min_body=body,
            min_score=0,
            trend=trend,
            hour_rng=hrs,
            tp_mode=tpm,
            fixed_tp_pct=ftp,
            sl_buffer=0.0005,
            use_detect=det,
        )
        if m["n"] >= 35 and m["wr"] >= 50.0:
            hits.append((m["wr"], m["pnl"], m["pf"], m["n"], g))
        if total % 500 == 0:
            print(f"  {total}/{len(grids)} | trouves WR>=50%: {len(hits)}")

    hits.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)

    print("\n" + "=" * 78)
    print(f"RESULTATS WR >= 50% — {months} mois — min 35 trades")
    print("=" * 78)

    if not hits:
        print("Aucune config WR>=50% avec 35+ trades.")
        print("Relachement: WR>=48%, min 25 trades...\n")
        for g in grids:
            m = simulate_v2(
                df,
                signals,
                hours,
                signal_kinds=set(g[0]),
                entry=g[2],
                side_filter=g[1],
                rr=g[3],
                max_hold=g[4],
                max_zone_pct=g[5],
                min_body=g[6],
                min_score=0,
                trend=g[7],
                hour_rng=g[8],
                tp_mode=g[9],
                fixed_tp_pct=g[10],
                sl_buffer=0.0005,
                use_detect=g[11],
            )
            if m["n"] >= 25 and m["wr"] >= 48.0:
                hits.append((m["wr"], m["pnl"], m["pf"], m["n"], g))
        hits.sort(key=lambda x: (x[0], x[1]), reverse=True)

    for wr, pnl, pf, n, g in hits[:25]:
        kinds, side, entry, rr, hold, zmax, body, trend, hrs, tpm, ftp, det = g
        print(
            f"WR {wr:.1f}% | PnL {pnl:+.1f}% | PF {pf:.2f} | n={n} | "
            f"{kinds} {side} | {entry} | tp={tpm} rr={rr} ftp={ftp}% hold={hold} "
            f"zone<{zmax}% body>={body} {trend} h{hrs[0]}-{hrs[1]} detect={det}"
        )

    if hits and hits[0][0] >= 50:
        print("\n--- Walk-forward 50/50 meilleure config ---")
        best = hits[0][4]
        mid = len(df) // 2
        for label, part in [("1ere moitie", df.iloc[:mid]), ("2eme moitie", df.iloc[mid:])]:
            sig = build_signals(part)
            m = simulate_v2(
                part,
                sig,
                part["timestamp"].dt.hour,
                signal_kinds=set(best[0]),
                entry=best[2],
                side_filter=best[1],
                rr=best[3],
                max_hold=best[4],
                max_zone_pct=best[5],
                min_body=best[6],
                min_score=0,
                trend=best[7],
                hour_rng=best[8],
                tp_mode=best[9],
                fixed_tp_pct=best[10],
                sl_buffer=0.0005,
                use_detect=best[11],
            )
            print(f"  {label}: n={m['n']} WR={m['wr']:.1f}% PnL={m['pnl']:+.1f}% PF={m['pf']:.2f}")


if __name__ == "__main__":
    asyncio.run(run_search(12))
