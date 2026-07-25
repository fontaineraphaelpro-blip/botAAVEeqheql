"""Portefeuille paper multi-positions pour le chasseur de shorts."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import ShortsConfig
from utils.logger import setup_logger

logger = setup_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _from_dict(cls, raw: dict[str, Any] | None):
    if not raw:
        return None
    allowed = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in raw.items() if k in allowed})


@dataclass
class ShortPosition:
    symbol: str
    qty: float
    entry: float
    stop: float
    initial_stop: float
    notional: float
    opened_at: str

    def unrealized(self, price: float) -> float:
        return self.qty * (self.entry - price)


@dataclass
class ShortTrade:
    symbol: str
    qty: float
    entry: float
    exit: float
    pnl: float
    pnl_pct: float
    reason: str
    opened_at: str
    closed_at: str


class ShortPortfolio:
    def __init__(self, cfg: ShortsConfig) -> None:
        self.cfg = cfg
        self.state_path = Path(cfg.state_file)
        self.balance: float = cfg.start_balance
        self.start_balance: float = cfg.start_balance
        self.positions: dict[str, ShortPosition] = {}
        self.trades: list[ShortTrade] = []
        self._load()

    # ------------------------------------------------------------ persistance
    def _load(self) -> None:
        if not self.state_path.exists():
            logger.warning(
                "Chasseur: aucun fichier %s — nouvel état. "
                "Volume Railway /app/data requis pour garder l'historique.",
                self.state_path,
            )
            return
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.balance = float(raw["balance"])
            self.start_balance = float(raw.get("start_balance", self.cfg.start_balance))
            self.positions = {}
            for s, p in raw.get("positions", {}).items():
                pos = _from_dict(ShortPosition, p)
                if pos is not None:
                    self.positions[s] = pos
            self.trades = [
                t
                for t in (_from_dict(ShortTrade, x) for x in raw.get("trades", []))
                if t is not None
            ]
            logger.info(
                "Chasseur état RECHARGÉ depuis %s — %.2f USDT, %d pos, %d trades",
                self.state_path,
                self.balance,
                len(self.positions),
                len(self.trades),
            )
        except Exception as exc:
            logger.error("État shorts illisible (%s) — conserve le fichier, pas de wipe", exc)

    def save(self) -> None:
        data = {
            "balance": self.balance,
            "start_balance": self.start_balance,
            "positions": {s: asdict(p) for s, p in self.positions.items()},
            "trades": [asdict(t) for t in self.trades],
            "updated_at": _now_iso(),
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    # ------------------------------------------------------------ trading
    def _cost_rate(self) -> float:
        return (self.cfg.fee_pct + self.cfg.slippage_pct) / 100.0

    def can_open(self) -> bool:
        return len(self.positions) < self.cfg.max_positions

    def open_short(self, symbol: str, price: float, stop: float) -> ShortPosition:
        if symbol in self.positions:
            raise RuntimeError(f"Short déjà ouvert sur {symbol}")
        notional = self.balance / self.cfg.max_positions
        qty = notional / price
        self.balance -= notional * self._cost_rate()
        pos = ShortPosition(
            symbol=symbol, qty=qty, entry=price, stop=stop,
            initial_stop=stop, notional=notional, opened_at=_now_iso(),
        )
        self.positions[symbol] = pos
        self.save()
        logger.info("SHORT %s %.4f @ %.4f | stop %.4f", symbol, qty, price, stop)
        return pos

    def close_short(self, symbol: str, price: float, reason: str) -> ShortTrade:
        pos = self.positions.pop(symbol)
        gross = pos.qty * (pos.entry - price)
        exit_fee = pos.qty * price * self._cost_rate()
        pnl = gross - exit_fee
        self.balance += pnl
        trade = ShortTrade(
            symbol=symbol, qty=pos.qty, entry=pos.entry, exit=price,
            pnl=pnl, pnl_pct=pnl / pos.notional * 100.0,
            reason=reason, opened_at=pos.opened_at, closed_at=_now_iso(),
        )
        self.trades.append(trade)
        self.save()
        logger.info(
            "COVER %s @ %.4f | PnL %+.2f USDT (%+.2f%%) | solde %.2f",
            symbol, price, pnl, trade.pnl_pct, self.balance,
        )
        return trade

    def apply_funding(self) -> None:
        """Funding périodique sur le notionnel des shorts ouverts (par cycle 2h)."""
        rate = self.cfg.funding_pct_8h / 100.0 / 4.0
        cost = sum(p.notional for p in self.positions.values()) * rate
        if cost:
            self.balance -= cost
            self.save()

    # ------------------------------------------------------------ stats
    def equity(self, prices: dict[str, float]) -> float:
        eq = self.balance
        for sym, pos in self.positions.items():
            px = prices.get(sym)
            if px:
                eq += pos.unrealized(px)
        return eq

    def stats(self) -> dict:
        wins = [t for t in self.trades if t.pnl > 0]
        return {
            "n": len(self.trades),
            "winrate": len(wins) / len(self.trades) * 100 if self.trades else 0.0,
            "pnl_total": self.balance - self.start_balance,
            "pnl_pct": (self.balance / self.start_balance - 1) * 100 if self.start_balance else 0.0,
            "balance": self.balance,
            "open": len(self.positions),
        }
