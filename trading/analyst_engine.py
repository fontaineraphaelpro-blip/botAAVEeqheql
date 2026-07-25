"""Moteur live AAVE Analyst — analyse 5m, prédictions Telegram, score de précision."""

from __future__ import annotations

import json
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
        self._last_5m_ts: pd.Timestamp | None = None
        self._state_path = Path(self.cfg.state_file)

    def load_memory(self) -> None:
        path = Path(self.cfg.memory_file)
        if not path.exists():
            raise FileNotFoundError(
                f"Mémoire Analyst introuvable: {path}. "
                "Lance scripts/build_analyst_memory.py puis redéploie."
            )
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
            "Analyst mémoire: %d motifs (source %s, %d barres)",
            self.memory.size,
            self.memory.built_from or "?",
            self.memory.n_source_bars,
        )

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

    def stats(self) -> dict[str, Any]:
        r = self.state.resolved
        return {
            "memory_size": self.memory.size if self.memory else 0,
            "hits": self.state.hits,
            "resolved": r,
            "accuracy_pct": (self.state.hits / r * 100.0) if r else 0.0,
            "alerts_today": self.state.alerts_today,
            "last_label": self.state.last_label,
        }

    def startup_message(self, source: str) -> str:
        return notif.msg_startup(
            memory_size=self.memory.size if self.memory else 0,
            lookback=self.params.lookback,
            horizon=self.params.horizon,
            source=source,
            resolved=self.state.resolved,
            hits=self.state.hits,
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
                # Cache a roulé au-delà de cette barre — on abandonne
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
            vec = p.get("vec")
            if vec is not None:
                try:
                    self.memory.append_online(vec, actual, fwd)
                except Exception as exc:
                    logger.debug("append online: %s", exc)
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
                "Analyst resolve dir=%s fwd=%+.2f%% hit=%s (%d/%d)",
                expected,
                fwd,
                hit,
                self.state.hits,
                self.state.resolved,
            )
        self.state.pending = still[-150:]
        return msgs

    def _bars_since_alert(self, closed: pd.DataFrame) -> int | None:
        if not self.state.last_alert_bar:
            return None
        ts_map = self._ts_index(closed)
        i = ts_map.get(self.state.last_alert_bar)
        if i is None:
            return self.cfg.alert_cooldown_bars
        return len(closed) - 1 - i

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

        need = self.params.lookback
        if len(closed) < need:
            logger.debug("Analyst: pas assez de barres (%d < %d)", len(closed), need)
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
        )
        bar_ts = str(last_5m_ts)

        actionable = (
            pred.actionable
            and pred.n_matches >= self.cfg.min_matches
            and pred.confidence >= self.cfg.min_confidence
        )
        if actionable:
            since = self._bars_since_alert(closed)
            if since is None or since >= self.cfg.alert_cooldown_bars:
                await self._send(
                    notif.msg_prediction(
                        pred,
                        price=float(closed["close"].iloc[-1]),
                        bar_ts=bar_ts,
                    )
                )
                self.state.alerts_today += 1
                self.state.last_alert_bar = bar_ts
                self.state.last_label = pred.label
                self.state.last_pred_ts = bar_ts
                if not any(p.get("bar_ts") == bar_ts for p in self.state.pending):
                    self.state.pending.append(
                        {
                            "bar_ts": bar_ts,
                            "direction": int(pred.direction),
                            "price": float(closed["close"].iloc[-1]),
                            "vec": vec.tolist(),
                            "alerted": True,
                        }
                    )
                    if len(self.state.pending) > 200:
                        self.state.pending = self.state.pending[-200:]
                logger.info(
                    "Analyst PRED %s conf=%.0f%% n=%d avg=%+.2f%%",
                    pred.label,
                    pred.confidence * 100,
                    pred.n_matches,
                    pred.avg_fwd_pct,
                )
            else:
                logger.debug("Analyst %s en cooldown (%s barres)", pred.label, since)
        else:
            logger.debug(
                "Analyst skip %s conf=%.2f n=%d dist=%.3f",
                pred.label,
                pred.confidence,
                pred.n_matches,
                pred.distance,
            )

        self.save_state()
