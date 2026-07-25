"""Moteur live AAVE Analyst — paper trade + mémoire persistante + apprentissage."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from config import AppConfig
from scanner.market_data import MarketDataService
from trading import analyst_notifications as notif
from trading.analyst_features import (
    FeatureParams,
    encode_window,
    forward_return_pct,
    label_from_return,
)
from trading.analyst_memory import PatternMemory
from trading.analyst_paper import AnalystPaper
from utils.logger import setup_logger

logger = setup_logger(__name__)

SYMBOL = "AAVE/USDT"
TF_5M = "5m"


@dataclass
class AnalystState:
    hits: int = 0
    resolved: int = 0
    alerts_today: int = 0
    alerts_day: str = ""
    last_alert_bar: str = ""
    last_label: str = ""
    last_pred_ts: str = ""
    learns_since_save: int = 0
    pending: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hits": self.hits,
            "resolved": self.resolved,
            "alerts_today": self.alerts_today,
            "alerts_day": self.alerts_day,
            "last_alert_bar": self.last_alert_bar,
            "last_label": self.last_label,
            "last_pred_ts": self.last_pred_ts,
            "learns_since_save": self.learns_since_save,
            "pending": self.pending,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AnalystState:
        return cls(
            hits=int(d.get("hits", 0)),
            resolved=int(d.get("resolved", 0)),
            alerts_today=int(d.get("alerts_today", 0)),
            alerts_day=str(d.get("alerts_day", "")),
            last_alert_bar=str(d.get("last_alert_bar", "")),
            last_label=str(d.get("last_label", "")),
            last_pred_ts=str(d.get("last_pred_ts", "")),
            learns_since_save=int(d.get("learns_since_save", 0)),
            pending=list(d.get("pending") or []),
        )


class AnalystEngine:
    def __init__(
        self,
        config: AppConfig,
        market: MarketDataService,
        telegram,
    ) -> None:
        self.config = config
        self.cfg = config.analyst
        self.market = market
        self.telegram = telegram
        self.params = FeatureParams(
            lookback=self.cfg.lookback,
            horizon=self.cfg.horizon,
            flat_pct=self.cfg.flat_pct,
        )
        self.memory: PatternMemory | None = None
        self.state = AnalystState()
        self.trader = AnalystPaper(
            state_file=self.cfg.paper_state_file,
            start_balance=self.cfg.start_balance,
            position_pct=self.cfg.position_pct,
            fee_pct=self.cfg.fee_pct,
            slippage_pct=self.cfg.slippage_pct,
        )
        self._last_5m_ts: pd.Timestamp | None = None
        self._state_path = Path(self.cfg.state_file)
        self._runtime_mem = Path(self.cfg.runtime_memory_file)
        self._seed_mem = Path(self.cfg.memory_file)

    # ------------------------------------------------------------------ memory
    def load_memory(self) -> None:
        path = self._runtime_mem if self._runtime_mem.exists() else self._seed_mem
        if not path.exists():
            raise FileNotFoundError(
                f"Mémoire Analyst introuvable: {path}. "
                "Lance scripts/build_analyst_memory.py puis redéploie."
            )
        # bootstrap runtime copy depuis le seed
        if path == self._seed_mem and not self._runtime_mem.exists():
            self._runtime_mem.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self._seed_mem, self._runtime_mem)
            meta = self._seed_mem.with_suffix(".json")
            if meta.exists():
                shutil.copy2(meta, self._runtime_mem.with_suffix(".json"))
            path = self._runtime_mem

        self.memory = PatternMemory.load(path)
        if (
            self.memory.params.lookback != self.params.lookback
            or self.memory.params.horizon != self.params.horizon
        ):
            logger.warning(
                "Params mémoire (lb=%d h=%d) ≠ config — utilise mémoire",
                self.memory.params.lookback,
                self.memory.params.horizon,
            )
            self.params = self.memory.params
        logger.info(
            "Analyst mémoire: %d motifs depuis %s",
            self.memory.size,
            path,
        )

    def save_memory(self, *, force: bool = False) -> None:
        if self.memory is None:
            return
        if not force and self.state.learns_since_save < self.cfg.memory_save_every:
            return
        self.memory.built_from = self.memory.built_from or "live"
        self.memory.save(self._runtime_mem)
        self.state.learns_since_save = 0
        logger.info("Analyst mémoire sauvée (%d motifs) → %s", self.memory.size, self._runtime_mem)

    def load_state(self) -> None:
        if self._state_path.exists():
            try:
                raw = json.loads(self._state_path.read_text(encoding="utf-8"))
                self.state = AnalystState.from_dict(raw)
            except Exception as exc:
                logger.warning("Analyst state illisible: %s", exc)
                self.state = AnalystState()

    def save_state(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(
            json.dumps(self.state.to_dict(), indent=2), encoding="utf-8"
        )

    def effective_min_confidence(self) -> float:
        """S'améliore : plus sélectif si ça perd, un peu plus ouvert si ça gagne."""
        base = self.cfg.min_confidence
        if self.state.resolved < 20:
            return base
        acc = self.state.hits / self.state.resolved
        if acc < 0.48:
            return min(0.72, base + 0.10)
        if acc > 0.56:
            return max(0.50, base - 0.04)
        return base

    def stats(self) -> dict[str, Any]:
        r = self.state.resolved
        ps = self.trader.stats()
        price = 0.0
        try:
            price = float(self.market.get_cached(SYMBOL, TF_5M)["close"].iloc[-1])
        except Exception:
            pass
        eq = self.trader.state.equity(price) if price else self.trader.state.balance
        return {
            "memory_size": self.memory.size if self.memory else 0,
            "hits": self.state.hits,
            "resolved": r,
            "accuracy_pct": (self.state.hits / r * 100.0) if r else 0.0,
            "alerts_today": self.state.alerts_today,
            "last_label": self.state.last_label,
            "balance": eq,
            "pnl_pct": ps["pnl_pct"],
            "n_trades": ps["n"],
            "winrate": ps["winrate"],
        }

    def startup_message(self, source: str) -> str:
        return notif.msg_startup(
            memory_size=self.memory.size if self.memory else 0,
            lookback=self.params.lookback,
            horizon=self.params.horizon,
            source=source,
            resolved=self.state.resolved,
            hits=self.state.hits,
            trader=self.trader,
        )

    async def _send(self, text: str) -> None:
        try:
            await self.telegram.send_raw(text)
        except Exception as exc:
            logger.error("Telegram Analyst: %s", exc)

    def _roll_day(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.state.alerts_day != today:
            self.state.alerts_day = today
            self.state.alerts_today = 0

    def _ts_index(self, closed: pd.DataFrame) -> dict[str, int]:
        return {str(t): i for i, t in enumerate(closed.index)}

    def _learn(
        self,
        vec: list[float] | None,
        actual: int,
        fwd: float,
        *,
        hit: bool,
    ) -> None:
        if self.memory is None or vec is None:
            return
        try:
            # Erreur = double poids pour mieux mémoriser le vrai dénouement
            times = 1 if hit else 2
            for _ in range(times):
                self.memory.append_online(vec, actual, fwd)
            self.state.learns_since_save += times
        except Exception as exc:
            logger.debug("learn: %s", exc)

    def _resolve_pending(self, closed: pd.DataFrame) -> list[str]:
        if self.memory is None or not self.state.pending:
            return []
        ts_map = self._ts_index(closed)
        closes = closed["close"].to_numpy(dtype=float)
        h = self.params.horizon
        still: list[dict] = []
        msgs: list[str] = []

        for p in self.state.pending:
            key = str(p["bar_ts"])
            if key not in ts_map:
                continue
            i0 = ts_map[key]
            if i0 + h >= len(closes):
                still.append(p)
                continue
            fwd = forward_return_pct(closes, i0, h)
            actual = label_from_return(fwd, self.params.flat_pct)
            expected = int(p["direction"])
            hit = actual == expected
            self.state.resolved += 1
            if hit:
                self.state.hits += 1
            self._learn(p.get("vec"), actual, fwd, hit=hit)
            if p.get("alerted") and expected != 0:
                msgs.append(
                    notif.msg_resolve(
                        expected=expected,
                        actual_fwd_pct=fwd,
                        hit=hit,
                        hits=self.state.hits,
                        resolved=self.state.resolved,
                    )
                )
            logger.info(
                "Analyst learn dir=%s fwd=%+.2f%% hit=%s (%d/%d)",
                expected,
                fwd,
                hit,
                self.state.hits,
                self.state.resolved,
            )
        self.state.pending = still[-150:]
        self.save_memory()
        return msgs

    def _bars_since(self, closed: pd.DataFrame, bar_ts: str) -> int | None:
        ts_map = self._ts_index(closed)
        i = ts_map.get(bar_ts)
        if i is None:
            return None
        return len(closed) - 1 - i

    async def _manage_position(self, closed: pd.DataFrame) -> None:
        pos = self.trader.state.position
        if pos is None:
            return
        last = closed.iloc[-1]
        price = float(last["close"])
        lo = float(last["low"])
        hi = float(last["high"])

        if self.trader.stop_hit(lo, hi):
            trade = self.trader.close(pos.stop, "stop")
            await self._send(notif.msg_close(trade, self.trader))
            return

        since = self._bars_since(closed, pos.entry_bar_ts)
        if since is not None and since >= self.params.horizon:
            trade = self.trader.close(price, "horizon")
            await self._send(notif.msg_close(trade, self.trader))

    async def on_new_5m_close(self) -> None:
        if self.memory is None:
            return
        df = self.market.get_cached(SYMBOL, TF_5M)
        closed = self.market.closed_bars(df).set_index("timestamp")
        if closed.empty:
            return
        last_5m_ts = closed.index[-1]
        if self._last_5m_ts is not None and last_5m_ts <= self._last_5m_ts:
            return
        self._last_5m_ts = last_5m_ts
        self._roll_day()

        for msg in self._resolve_pending(closed):
            await self._send(msg)

        await self._manage_position(closed)

        need = self.params.lookback
        if len(closed) < need:
            self.save_state()
            return

        window = closed.iloc[-need:]
        try:
            vec = encode_window(window, self.params)
        except ValueError as exc:
            logger.debug("Analyst encode: %s", exc)
            self.save_state()
            return

        pred = self.memory.query(
            vec,
            top_k=self.cfg.top_k,
            max_distance=self.cfg.max_distance,
            always=self.cfg.continuous,
            prefer_direction=self.cfg.continuous,
        )
        bar_ts = str(last_5m_ts)
        min_conf = self.effective_min_confidence()
        price = float(closed["close"].iloc[-1])

        # Toujours enregistrer la prédiction pour apprendre (chaque bougie)
        self.state.last_label = pred.label
        self.state.last_pred_ts = bar_ts
        if not any(p.get("bar_ts") == bar_ts for p in self.state.pending):
            self.state.pending.append(
                {
                    "bar_ts": bar_ts,
                    "direction": int(pred.direction),
                    "price": price,
                    "vec": vec.tolist(),
                    "alerted": False,
                }
            )
            if len(self.state.pending) > 400:
                self.state.pending = self.state.pending[-400:]

        logger.info(
            "Analyst PRED %s conf=%.0f%% n=%d avg=%+.2f%% dist=%.3f",
            pred.label,
            pred.confidence * 100,
            pred.n_matches,
            pred.avg_fwd_pct,
            pred.distance,
        )

        if self.cfg.telegram_every_pred:
            await self._send(
                notif.msg_prediction(pred, price=price, bar_ts=bar_ts)
            )

        # En continu : trade dès qu'on a une direction (UP/DOWN)
        can_trade = pred.direction != 0 and pred.n_matches >= self.cfg.min_matches
        if not self.cfg.continuous:
            can_trade = (
                can_trade
                and pred.confidence >= min_conf
                and pred.n_matches >= max(self.cfg.min_matches, 1)
            )

        if can_trade:
            since_alert = (
                self._bars_since(closed, self.state.last_alert_bar)
                if self.state.last_alert_bar
                else None
            )
            cooldown_ok = (
                since_alert is None or since_alert >= self.cfg.alert_cooldown_bars
            )

            pos = self.trader.state.position
            if pos is not None and pred.direction == -pos.side:
                trade = self.trader.close(price, "flip")
                await self._send(notif.msg_close(trade, self.trader))
                pos = None

            if cooldown_ok and not self.trader.in_position:
                try:
                    opened = self.trader.open(
                        pred.direction,
                        price,
                        entry_bar_ts=bar_ts,
                        confidence=pred.confidence,
                        pred_label=pred.label,
                        stop_pct=self.cfg.stop_pct,
                    )
                    await self._send(
                        notif.msg_open(
                            opened,
                            pred,
                            self.trader.state.balance,
                            horizon_min=self.params.horizon * 5,
                        )
                    )
                    self.state.alerts_today += 1
                    self.state.last_alert_bar = bar_ts
                    # marque ce pending comme trade pour notif resolve
                    for p in self.state.pending:
                        if p.get("bar_ts") == bar_ts:
                            p["alerted"] = True
                            break
                    logger.info(
                        "Analyst TRADE %s conf=%.0f%% n=%d",
                        pred.label,
                        pred.confidence * 100,
                        pred.n_matches,
                    )
                except Exception as exc:
                    logger.error("Analyst open: %s", exc)

        self.save_state()

    def shutdown(self) -> None:
        self.save_memory(force=True)
        self.save_state()
        self.trader.save()
