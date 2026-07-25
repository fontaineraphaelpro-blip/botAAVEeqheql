"""Messages Telegram — AAVE Analyst (mémoire de motifs)."""

from __future__ import annotations

from datetime import datetime, timezone

from trading.analyst_features import DIR_LABEL
from trading.analyst_memory import Prediction
from trading.bot_ids import BOT_ANALYST, TAG_ANALYST, header


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M UTC")


def msg_startup(
    *,
    memory_size: int,
    lookback: int,
    horizon: int,
    source: str,
    resolved: int,
    hits: int,
) -> str:
    acc = f"{(hits / resolved * 100):.0f}%" if resolved else "n/a"
    return (
        f"{header(TAG_ANALYST, f'<b>{BOT_ANALYST}</b> démarré')}\n"
        f"Mémoire <code>{memory_size}</code> motifs · fenêtre {lookback}×5m → "
        f"horizon {horizon}×5m (~{horizon * 5} min)\n"
        f"Précision live : <code>{acc}</code> ({hits}/{resolved})\n"
        f"<i>Prédictions seulement si cas historiques similaires · {source} · {_ts()}</i>"
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
        f"<code>{pred.n_matches}</code> cas similaires · "
        f"dist moy <code>{pred.distance:.3f}</code>\n"
        f"Fwd moyen historique : <code>{pred.avg_fwd_pct:+.2f}%</code>\n"
        f"AAVE <code>{price:.3f}</code> · barre {bar_ts}\n"
        f"<i>Basé uniquement sur motifs déjà analysés · {_ts()}</i>"
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
        f"{header(TAG_ANALYST, f'{emoji} Résultat {exp}')}\n"
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
        f"Mémoire <code>{s['memory_size']}</code> · "
        f"précision <code>{acc_txt}</code> ({s['hits']}/{s['resolved']}) · "
        f"alertes j <code>{s['alerts_today']}</code> · dernier {last}"
    )
