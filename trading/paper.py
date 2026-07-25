"""Moteur de paper trading — portefeuille virtuel, PnL, persistance JSON."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import TradingConfig
from utils.logger import setup_logger

logger = setup_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _from_dict(cls, raw: dict[str, Any] | None):
    """Charge un dataclass en ignorant les clés inconnues (évite reset au redeploy)."""
    if not raw:
        return None
    allowed = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in raw.items() if k in allowed})


@dataclass
class Position:
    side: int              # +1 long, -1 short
    qty: float
    entry: float
    stop: float
    initial_stop: float
    entry_fee: float
    opened_at: str
    entry_atr: float

    @property
    def side_label(self) -> str:
        return "LONG" if self.side == 1 else "SHORT"

    def unrealized(self, price: float) -> float:
        return self.side * self.qty * (price - self.entry)


@dataclass
class ClosedTrade:
    side: str
    qty: float
    entry: float
    exit: float
    pnl: float             # net, frais inclus
    pnl_pct: float         # % du solde engagé
    reason: str
    opened_at: str
    closed_at: str


@dataclass
class PaperState:
    balance: float
    start_balance: float
    position: Position | None = None
    trades: list[ClosedTrade] = field(default_factory=list)

    @property
    def total_pnl(self) -> float:
        return self.balance - self.start_balance

    def equity(self, price: float) -> float:
        if self.position is None:
            return self.balance
        return self.balance + self.position.unrealized(price)


class PaperTrader:
    def __init__(self, cfg: TradingConfig) -> None:
        self.cfg = cfg
        self.state_path = Path(cfg.state_file)
        self.state = self._load()

    def _load(self) -> PaperState:
        if self.state_path.exists():
            try:
                raw = json.loads(self.state_path.read_text(encoding="utf-8"))
                pos = _from_dict(Position, raw.get("position"))
                trades = [
                    t for t in (_from_dict(ClosedTrade, x) for x in raw.get("trades", []))
                    if t is not None
                ]
                state = PaperState(
                    balance=float(raw["balance"]),
                    start_balance=float(raw.get("start_balance", self.cfg.start_balance)),
                    position=pos,
                    trades=trades,
                )
                logger.info(
                    "Tendance état RECHARGÉ depuis %s — solde %.2f USDT, %d trades, pos=%s",
                    self.state_path,
                    state.balance,
                    len(trades),
                    pos.side_label if pos else "flat",
                )
                return state
            except Exception as exc:
                # Ne pas écraser le fichier corrompu : backup puis fail soft
                bak = self.state_path.with_suffix(".bak")
                try:
                    bak.write_bytes(self.state_path.read_bytes())
                    logger.error(
                        "Tendance état illisible (%s) — backup %s, conserve le fichier",
                        exc,
                        bak,
                    )
                except Exception:
                    logger.error("Tendance état illisible (%s) — reset forcé", exc)
                    return PaperState(
                        balance=self.cfg.start_balance,
                        start_balance=self.cfg.start_balance,
                    )
                # tente de garder au moins balance si possible
                try:
                    raw = json.loads(self.state_path.read_text(encoding="utf-8"))
                    return PaperState(
                        balance=float(raw.get("balance", self.cfg.start_balance)),
                        start_balance=float(
                            raw.get("start_balance", self.cfg.start_balance)
                        ),
                        position=None,
                        trades=[],
                    )
                except Exception:
                    return PaperState(
                        balance=self.cfg.start_balance,
                        start_balance=self.cfg.start_balance,
                    )
        logger.warning(
            "Tendance: aucun fichier %s — NOUVEL état (0 trade). "
            "Sur Railway: monte un volume sur /app/data sinon l'historique est perdu à chaque deploy.",
            self.state_path,
        )
        return PaperState(balance=self.cfg.start_balance, start_balance=self.cfg.start_balance)

    def save(self) -> None:
        data = {
            "balance": self.state.balance,
            "start_balance": self.state.start_balance,
            "position": asdict(self.state.position) if self.state.position else None,
            "trades": [asdict(t) for t in self.state.trades],
            "bot": "AAVE Tendance",
            "updated_at": _now_iso(),
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    @property
    def in_position(self) -> bool:
        return self.state.position is not None

    def _cost_rate(self) -> float:
        return (self.cfg.fee_pct + self.cfg.slippage_pct) / 100.0

    def open(self, side: int, price: float, atr: float, stop: float) -> Position:
        if self.state.position is not None:
            raise RuntimeError("Position déjà ouverte")
        notional = self.state.balance * self.cfg.position_pct / 100.0
        qty = notional / price
        fee = notional * self._cost_rate()
        pos = Position(
            side=side,
            qty=qty,
            entry=price,
            stop=stop,
            initial_stop=stop,
            entry_fee=fee,
            opened_at=_now_iso(),
            entry_atr=atr,
        )
        self.state.position = pos
        self.save()
        logger.info("OPEN %s %.4f AAVE @ %.3f | stop %.3f", pos.side_label, qty, price, stop)
        return pos

    def close(self, price: float, reason: str) -> ClosedTrade:
        pos = self.state.position
        if pos is None:
            raise RuntimeError("Aucune position à fermer")
        exit_fee = pos.qty * price * self._cost_rate()
        gross = pos.side * pos.qty * (price - pos.entry)
        pnl = gross - pos.entry_fee - exit_fee
        engaged = pos.qty * pos.entry
        trade = ClosedTrade(
            side=pos.side_label,
            qty=pos.qty,
            entry=pos.entry,
            exit=price,
            pnl=pnl,
            pnl_pct=pnl / engaged * 100.0 if engaged else 0.0,
            reason=reason,
            opened_at=pos.opened_at,
            closed_at=_now_iso(),
        )
        self.state.balance += pnl
        self.state.trades.append(trade)
        self.state.position = None
        self.save()
        logger.info(
            "CLOSE %s @ %.3f | PnL %+.2f USDT (%+.2f%%) | solde %.2f",
            trade.side, price, pnl, trade.pnl_pct, self.state.balance,
        )
        return trade

    def update_stop(self, new_stop: float) -> bool:
        pos = self.state.position
        if pos is None:
            return False
        moved = (pos.side == 1 and new_stop > pos.stop) or (pos.side == -1 and new_stop < pos.stop)
        if moved:
            pos.stop = new_stop
            self.save()
        return moved

    def stop_hit(self, bar_low: float, bar_high: float) -> bool:
        pos = self.state.position
        if pos is None:
            return False
        return bar_low <= pos.stop if pos.side == 1 else bar_high >= pos.stop

    def stats(self) -> dict:
        trades = self.state.trades
        wins = [t for t in trades if t.pnl > 0]
        return {
            "n": len(trades),
            "wins": len(wins),
            "winrate": len(wins) / len(trades) * 100 if trades else 0.0,
            "pnl_total": self.state.total_pnl,
            "pnl_pct": self.state.total_pnl / self.state.start_balance * 100
            if self.state.start_balance else 0.0,
            "balance": self.state.balance,
        }
