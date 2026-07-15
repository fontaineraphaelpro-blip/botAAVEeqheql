"""Rejoue le CSV 12 mois à travers la vraie stratégie live + PaperTrader.

Valide que trading/strategy.py + trading/paper.py reproduisent la logique
du backtest (mêmes ordres de grandeur de PnL, trades, winrate).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import get_config
from trading.paper import PaperTrader
from trading.strategy import EmaFlipStrategy

DATA = Path(__file__).resolve().parent.parent / "data" / "aave_5m_kucoin_12m.csv"


def main() -> None:
    cfg = get_config().trading
    # État isolé pour la simulation
    import dataclasses
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    Path(tmp.name).unlink()
    cfg = dataclasses.replace(cfg, state_file=tmp.name)

    strategy = EmaFlipStrategy(cfg)
    trader = PaperTrader(cfg)

    df5 = pd.read_csv(DATA, parse_dates=["timestamp"]).set_index("timestamp").sort_index()
    tf = strategy.resample(df5)
    print(f"{len(tf)} bougies TF{cfg.signal_tf_min}min")

    warmup = max(cfg.ema_len, cfg.er_len, cfg.atr_len) + 2
    be_notified = False
    n_be = 0

    for i in range(warmup, len(tf) - 1):
        window = tf.iloc[: i + 1]
        bar = tf.iloc[i]

        # 1. stop intra-bougie (approximation TF, comme le backtest)
        if trader.in_position and trader.stop_hit(float(bar["low"]), float(bar["high"])):
            pos = trader.state.position
            trader.close(pos.stop, "stop")
            be_notified = False

        sig = strategy.compute(window)
        if sig is None:
            continue

        # 2. trailing
        if trader.in_position:
            pos = trader.state.position
            new_stop = strategy.trail_stop(
                pos.side, pos.stop, float(bar["high"]), float(bar["low"]), sig.atr
            )
            if trader.update_stop(new_stop):
                protected = (pos.side == 1 and pos.stop >= pos.entry) or (
                    pos.side == -1 and pos.stop <= pos.entry
                )
                if protected and not be_notified:
                    be_notified = True
                    n_be += 1

        # 3. entrée
        if not trader.in_position and sig.direction != 0:
            entry = float(tf.iloc[i + 1]["open"])  # open suivant comme le backtest
            stop = strategy.initial_stop(sig.direction, entry, sig.atr)
            trader.open(sig.direction, entry, sig.atr, stop)
            be_notified = False

    s = trader.stats()
    days = (tf.index[-1] - tf.index[0]).days or 1
    print(f"\nRésultat simulation moteur live (12 mois):")
    print(f"  Solde final : {s['balance']:.2f} USDT ({s['pnl_pct']:+.1f}%)")
    print(f"  Trades      : {s['n']} ({s['n']/days:.2f}/jour)")
    print(f"  Winrate     : {s['winrate']:.1f}%")
    print(f"  Break-even  : {n_be} notifications")
    Path(tmp.name).unlink(missing_ok=True)


if __name__ == "__main__":
    import logging
    logging.disable(logging.INFO)
    main()
