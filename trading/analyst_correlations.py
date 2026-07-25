"""Livre de corrélations indicateur → direction future (apprentissage live)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _bin(value: float, edges: list[float]) -> int:
    for i, e in enumerate(edges):
        if value < e:
            return i
    return len(edges)


# Bornes empiriques AAVE 5m
BINS: dict[str, list[float]] = {
    "rsi": [30.0, 45.0, 55.0, 70.0],
    "bb_pct": [-0.8, -0.2, 0.2, 0.8],
    "macd_hist": [-0.002, -0.0005, 0.0005, 0.002],
    "er": [0.2, 0.35, 0.5, 0.65],
    "stoch_k": [20.0, 40.0, 60.0, 80.0],
    "ema_spread": [-0.01, -0.003, 0.003, 0.01],
    "corr_pv": [-0.3, 0.0, 0.3, 0.6],
    "autocorr": [-0.2, 0.0, 0.2, 0.4],
}


@dataclass
class BinStats:
    up: int = 0
    down: int = 0
    flat: int = 0

    def observe(self, label: int) -> None:
        if label == 1:
            self.up += 1
        elif label == -1:
            self.down += 1
        else:
            self.flat += 1

    @property
    def n(self) -> int:
        return self.up + self.down + self.flat

    def up_rate(self) -> float:
        d = self.up + self.down
        return self.up / d if d else 0.5

    def to_dict(self) -> dict[str, int]:
        return {"up": self.up, "down": self.down, "flat": self.flat}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BinStats:
        return cls(
            up=int(d.get("up", 0)),
            down=int(d.get("down", 0)),
            flat=int(d.get("flat", 0)),
        )


@dataclass
class CorrelationBook:
    """Compte, pour chaque indicateur×bin, combien de fois UP/DOWN a suivi."""

    tables: dict[str, dict[str, BinStats]] = field(default_factory=dict)
    min_samples: int = 30

    def _cell(self, ind: str, b: int) -> BinStats:
        if ind not in self.tables:
            self.tables[ind] = {}
        key = str(b)
        if key not in self.tables[ind]:
            self.tables[ind][key] = BinStats()
        return self.tables[ind][key]

    def observe(self, snapshot: dict[str, float], label: int) -> None:
        for ind, edges in BINS.items():
            if ind not in snapshot:
                continue
            b = _bin(float(snapshot[ind]), edges)
            self._cell(ind, b).observe(label)

    def direction_bias(self, snapshot: dict[str, float]) -> tuple[int, float, list[str]]:
        """Vote pondéré des corrélations connues → (dir, conf, raisons)."""
        score = 0.0
        weight = 0.0
        reasons: list[str] = []
        for ind, edges in BINS.items():
            if ind not in snapshot:
                continue
            b = _bin(float(snapshot[ind]), edges)
            st = self._cell(ind, b)
            if st.n < self.min_samples:
                continue
            rate = st.up_rate()
            # écart vs 50%
            lift = rate - 0.5
            w = min(st.n / 100.0, 2.0)
            score += lift * w
            weight += w
            if abs(lift) >= 0.08:
                reasons.append(f"{ind}@bin{b}:{rate:.0%}(n={st.n})")
        if weight < 1e-9:
            return 0, 0.0, []
        avg = score / weight
        if avg > 0.05:
            return 1, min(abs(avg) * 2, 1.0), reasons[:4]
        if avg < -0.05:
            return -1, min(abs(avg) * 2, 1.0), reasons[:4]
        return 0, abs(avg), reasons[:4]

    def top_edges(self, limit: int = 5) -> list[str]:
        rows: list[tuple[float, str]] = []
        for ind, bins in self.tables.items():
            for b, st in bins.items():
                if st.n < self.min_samples:
                    continue
                rate = st.up_rate()
                rows.append((abs(rate - 0.5), f"{ind}[{b}] UP={rate:.0%} n={st.n}"))
        rows.sort(reverse=True)
        return [r[1] for r in rows[:limit]]

    def to_dict(self) -> dict[str, Any]:
        return {
            ind: {b: st.to_dict() for b, st in bins.items()}
            for ind, bins in self.tables.items()
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any], min_samples: int = 30) -> CorrelationBook:
        book = cls(min_samples=min_samples)
        for ind, bins in (d or {}).items():
            book.tables[ind] = {
                str(b): BinStats.from_dict(st) for b, st in bins.items()
            }
        return book

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path, min_samples: int = 30) -> CorrelationBook:
        path = Path(path)
        if not path.exists():
            return cls(min_samples=min_samples)
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(raw, min_samples=min_samples)
