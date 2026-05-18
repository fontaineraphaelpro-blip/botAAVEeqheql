"""
Backtest EQH/EQL AAVE 5m (KuCoin) — entrée LONG/SHORT à chaque alerte.

Hypothèses :
- Entrée au close de la bougie d'alerte
- SL : au-delà de la zone (EQH → au-dessus du top, EQL → sous le bottom)
- TP : R:R 1:2
- Max hold : 48 bougies (4h en 5m)
- 1 position à la fois
- Frais : 0.06 % par côté (0.12 % aller-retour)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Literal, Optional

import aiohttp
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import get_config
from models.liquidity_zone import ZoneType
from scanner.liquidity_detector import LiquidityDetector
from utils.trade_bias import get_bias

CACHE_DIR = ROOT / "data"
SYMBOL = "AAVE/USDT"
TF = "5m"
TF_SEC = 5 * 60
CHUNK_CANDLES = 1500


@dataclass
class Trade:
    side: Literal["long", "short"]
    alert_type: str
    entry_bar: int
    entry_price: float
    sl: float
    tp: float
    exit_bar: int = -1
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl_pct: float = 0.0


@dataclass
class BacktestResult:
    trades: List[Trade] = field(default_factory=list)
    months: float = 0.0
    mode: str = ""

    def summary(self) -> str:
        if not self.trades:
            return "Aucun trade genere sur la periode."

        closed = [t for t in self.trades if t.exit_bar >= 0]
        if not closed:
            return "Trades ouverts sans sortie (bug)."

        wins = [t for t in closed if t.pnl_pct > 0]
        losses = [t for t in closed if t.pnl_pct <= 0]
        win_rate = len(wins) / len(closed) * 100
        total_pnl = sum(t.pnl_pct for t in closed)
        avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0.0
        avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0.0
        gross_profit = sum(t.pnl_pct for t in wins)
        gross_loss = abs(sum(t.pnl_pct for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        by_type: dict[str, list] = {}
        for t in closed:
            by_type.setdefault(t.alert_type, []).append(t)
        type_lines = []
        for k, ts in sorted(by_type.items()):
            wr = sum(1 for x in ts if x.pnl_pct > 0) / len(ts) * 100
            type_lines.append(f"    {k}: {len(ts)} trades, win rate {wr:.1f}%")

        return (
            f"\n{'='*50}\n"
            f"BACKTEST AAVE/USDT 5m — KuCoin — {self.months:.0f} mois\n"
            f"Mode : {self.mode}\n"
            f"{'='*50}\n"
            f"Trades fermes     : {len(closed)}\n"
            f"Win rate          : {win_rate:.1f}%\n"
            f"PnL total (net %) : {total_pnl:.2f}%\n"
            f"Gain moyen        : {avg_win:.2f}%\n"
            f"Perte moyenne     : {avg_loss:.2f}%\n"
            f"Profit factor     : {profit_factor:.2f}\n"
            f"\nPar type d'alerte :\n" + "\n".join(type_lines) + "\n"
            f"{'='*50}\n"
            f"(!) Resultats passes — pas une garantie future.\n"
        )


async def fetch_kucoin_history(months: int) -> pd.DataFrame:
    cache = CACHE_DIR / f"aave_5m_kucoin_{months}m.csv"
    if cache.exists():
        df = pd.read_csv(cache, parse_dates=["timestamp"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        print(f"[cache] {len(df)} bougies chargees depuis {cache.name}")
        return df

    end_at = int(time.time())
    start_at = end_at - int(months * 30.25 * 24 * 3600)
    all_rows: list[list] = []
    chunk_seconds = CHUNK_CANDLES * TF_SEC
    cursor_start = start_at

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=60),
        headers={"User-Agent": "AAVE-EQH-EQL-Backtest/1.0"},
    ) as session:
        while cursor_start < end_at:
            cursor_end = min(cursor_start + chunk_seconds, end_at)
            params = {
                "type": "5min",
                "symbol": "AAVE-USDT",
                "startAt": cursor_start,
                "endAt": cursor_end,
            }
            url = "https://api.kucoin.com/api/v1/market/candles"
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                body = await resp.json()
            if body.get("code") != "200000":
                raise RuntimeError(body)

            batch = body.get("data") or []
            if not batch:
                cursor_start = cursor_end + 1
                continue

            for r in reversed(batch):
                ts = int(r[0])
                o, c, h, lo, vol = float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])
                all_rows.append([ts * 1000, o, h, lo, c, vol])

            print(
                f"  ... {len(all_rows)} bougies "
                f"({pd.to_datetime(cursor_start, unit='s').date()} -> "
                f"{pd.to_datetime(cursor_end, unit='s').date()})"
            )
            cursor_start = cursor_end + 1
            await asyncio.sleep(0.2)

    if not all_rows:
        raise RuntimeError("Pas de donnees KuCoin")

    df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache, index=False)
    print(f"[save] {len(df)} bougies -> {cache}")
    return df


def _sl_tp(side: str, entry: float, zone_top: float, zone_bottom: float, bar_high: float, bar_low: float) -> tuple[float, float]:
    buffer = 0.001  # 0.1 %
    if side == "short":
        sl = max(zone_top, bar_high) * (1 + buffer)
        risk = sl - entry
        if risk <= 0:
            risk = entry * 0.005
            sl = entry + risk
        tp = entry - 2.0 * risk
        return sl, tp
    sl = min(zone_bottom, bar_low) * (1 - buffer)
    risk = entry - sl
    if risk <= 0:
        risk = entry * 0.005
        sl = entry - risk
    tp = entry + 2.0 * risk
    return sl, tp


def run_backtest(
    df: pd.DataFrame,
    mode: Literal["all_alerts", "sweep_only"],
    rr: float = 2.0,
    max_hold_bars: int = 48,
    fee_pct: float = 0.06,
) -> BacktestResult:
    config = get_config()
    detector = LiquidityDetector(config)
    symbol = SYMBOL
    tf = TF
    min_bars = config.scan.min_bars

    trades: List[Trade] = []
    open_trade: Optional[Trade] = None

    for i in range(min_bars, len(df)):
        slice_df = df.iloc[: i + 1].copy()
        ts = int(slice_df["timestamp"].iloc[-1].timestamp())
        result = detector.process(symbol, tf, slice_df, ts)

        bar = df.iloc[i]
        high, low, close = float(bar["high"]), float(bar["low"]), float(bar["close"])

        # Gestion position ouverte
        if open_trade is not None:
            t = open_trade
            hit_sl = hit_tp = False
            if t.side == "long":
                if low <= t.sl:
                    t.exit_price, t.exit_reason, hit_sl = t.sl, "SL", True
                elif high >= t.tp:
                    t.exit_price, t.exit_reason, hit_tp = t.tp, "TP", True
            else:
                if high >= t.sl:
                    t.exit_price, t.exit_reason, hit_sl = t.sl, "SL", True
                elif low <= t.tp:
                    t.exit_price, t.exit_reason, hit_tp = t.tp, "TP", True

            if not hit_sl and not hit_tp and i - t.entry_bar >= max_hold_bars:
                t.exit_price, t.exit_reason = close, "TIME"

            if t.exit_reason:
                t.exit_bar = i
                if t.side == "long":
                    raw = (t.exit_price - t.entry_price) / t.entry_price * 100
                else:
                    raw = (t.entry_price - t.exit_price) / t.entry_price * 100
                t.pnl_pct = raw - fee_pct * 2
                trades.append(t)
                open_trade = None

        if open_trade is not None:
            continue

        signals: list[tuple[str, str, object]] = []

        for zone in result.new_zones:
            bias = get_bias(zone, is_sweep=False)
            if mode == "all_alerts":
                signals.append((f"{zone.zone_type.value}_detect", bias.direction.lower(), zone))

        for zone, stype in result.sweeps:
            if mode == "sweep_only" or mode == "all_alerts":
                side = "short" if stype == "EQH_SWEEP" else "long"
                signals.append((stype, side, zone))

        for alert_type, side, zone in signals:
            if open_trade is not None:
                break
            entry = close
            sl, tp = _sl_tp(side, entry, zone.top, zone.bottom, high, low)
            if side == "short":
                risk = sl - entry
                tp = entry - rr * risk
            else:
                risk = entry - sl
                tp = entry + rr * risk

            open_trade = Trade(
                side=side,
                alert_type=alert_type,
                entry_bar=i,
                entry_price=entry,
                sl=sl,
                tp=tp,
            )
            break

    result_obj = BacktestResult(trades=trades, mode=mode)
    return result_obj


async def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest EQH/EQL AAVE 5m")
    parser.add_argument("--months", type=int, default=6, help="Mois d'historique (6-12)")
    parser.add_argument(
        "--mode",
        choices=["sweep_only", "all_alerts"],
        default="sweep_only",
        help="sweep_only = signaux SWEEP | all_alerts = toutes alertes",
    )
    args = parser.parse_args()
    months = max(1, min(12, args.months))

    print(f"Telechargement / cache KuCoin AAVE 5m ({months} mois)...")
    df = await fetch_kucoin_history(months)
    print(f"Periode : {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}")
    print(f"Bougies : {len(df)}\n")

    for mode in ([args.mode] if args.mode else ["sweep_only", "all_alerts"]):
        print(f"--- Backtest mode: {mode} ---")
        bt = run_backtest(df, mode=mode)
        bt.months = months
        print(bt.summary())


if __name__ == "__main__":
    asyncio.run(main())
