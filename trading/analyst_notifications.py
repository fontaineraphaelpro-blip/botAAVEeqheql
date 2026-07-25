"""Messages Telegram — AAVE Analyst (mémoire + paper)."""

from __future__ import annotations

from datetime import datetime, timezone

from trading.analyst_features import DIR_LABEL
from trading.analyst_memory import Prediction
from trading.analyst_paper import AnalystPaper, AnalystPosition, AnalystTrade
from trading.bot_ids import BOT_ANALYST, TAG_ANALYST, header


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M UTC")


def _pct(v: float) -> str:
    return f"{v:+.2f}%"


def msg_startup(
    *,
    memory_size: int,
    lookback: int,
    horizon: int,
    source: str,
    resolved: int,
    hits: int,
    trader: AnalystPaper,
) -> str:
    acc = f"{(hits / resolved * 100):.0f}%" if resolved else "n/a"
    s = trader.stats()
    pos = trader.state.position
    pos_line = (
        f"Pos : <b>{pos.side_label}</b> @ <code>{pos.entry:.3f}</code>"
        if pos
        else "Pos : flat"
    )
    return (
        f"{header(TAG_ANALYST, f'<b>{BOT_ANALYST}</b> démarré')}\n"
        f"Paper continu · mémoire <code>{memory_size}</code> motifs · "
        f"analyse chaque 5m · horizon {horizon}×5m (~{horizon * 5} min)\n"
        f"Solde <code>{s['balance']:.2f}</code> · {pos_line}\n"
        f"Précision live : <code>{acc}</code> ({hits}/{resolved})\n"
        f"<i>Prédit + apprend sur chaque bougie · {source} · {_ts()}</i>"
    )


def msg_prediction(
    pred: Prediction,
    *,
    price: float,
    bar_ts: str,
) -> str:
    emoji = {"UP": "📈", "DOWN": "📉", "FLAT": "➖"}.get(pred.label, "❔")
    return (
        f"{header(TAG_ANALYST, f'{emoji} Prédiction <b>{pred.label}</b>')}\n"
        f"Confiance <code>{pred.confidence * 100:.0f}%</code> · "
        f"<code>{pred.n_matches}</code> cas · dist <code>{pred.distance:.3f}</code>\n"
        f"Fwd hist. <code>{pred.avg_fwd_pct:+.2f}%</code> · AAVE <code>{price:.3f}</code>\n"
        f"{bar_ts} · {_ts()}"
    )


def msg_open(
    pos: AnalystPosition,
    pred: Prediction,
    balance: float,
    *,
    horizon_min: int,
) -> str:
    emoji = "🟢" if pos.side == 1 else "🔴"
    return (
        f"{header(TAG_ANALYST, f'{emoji} Paper <b>{pos.side_label}</b> @ <code>{pos.entry:.3f}</code>')}\n"
        f"Signal {pred.label} · conf <code>{pred.confidence * 100:.0f}%</code> · "
        f"<code>{pred.n_matches}</code> cas · stop <code>{pos.stop:.3f}</code>\n"
        f"Taille <code>{pos.qty:.3f}</code> AAVE · solde <code>{balance:.2f}</code>\n"
        f"Sortie prévue ~{horizon_min} min (horizon) · {_ts()}"
    )


def msg_close(trade: AnalystTrade, trader: AnalystPaper) -> str:
    emoji = "✅" if trade.pnl > 0 else "🛑"
    s = trader.stats()
    return (
        f"{header(TAG_ANALYST, f'{emoji} Close {trade.side} ({trade.reason})')}\n"
        f"<code>{trade.entry:.3f}</code> → <code>{trade.exit:.3f}</code>\n"
        f"PnL : <code>{trade.pnl:+.2f}</code> USDT ({_pct(trade.pnl_pct)})\n"
        f"Solde : <code>{s['balance']:.2f}</code> · WR {s['winrate']:.0f}% "
        f"({s['n']}t) · {_ts()}"
    )


def msg_resolve(
    *,
    expected: int,
    actual_fwd_pct: float,
    hit: bool,
    hits: int,
    resolved: int,
) -> str:
    emoji = "✅" if hit else "❌"
    exp = DIR_LABEL.get(expected, "?")
    acc = f"{(hits / resolved * 100):.0f}%" if resolved else "n/a"
    return (
        f"{header(TAG_ANALYST, f'{emoji} Apprentissage {exp}')}\n"
        f"Mouvement réel : <code>{actual_fwd_pct:+.2f}%</code>\n"
        f"Précision live : <code>{acc}</code> ({hits}/{resolved})\n"
        f"{_ts()}"
    )


def section_daily(engine) -> str:
    s = engine.stats()
    last = s.get("last_label") or "—"
    acc = s["accuracy_pct"]
    acc_txt = f"{acc:.0f}%" if s["resolved"] else "n/a"
    return (
        f"<b>[{TAG_ANALYST}]</b> {BOT_ANALYST}\n"
        f"Équité <code>{s['balance']:.2f}</code> ({_pct(s['pnl_pct'])}) · "
        f"{s['n_trades']}t WR {s['winrate']:.0f}% · "
        f"précision <code>{acc_txt}</code> · "
        f"mémoire <code>{s['memory_size']}</code> · dernier {last}"
    )
