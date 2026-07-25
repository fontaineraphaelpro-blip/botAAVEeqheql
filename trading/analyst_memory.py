"""Mémoire de motifs AAVE — KNN cosine sur historiques déjà analysés."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from trading.analyst_features import (
    DIR_LABEL,
    FeatureParams,
    encode_window,
    feature_dim,
    forward_return_pct,
    label_from_return,
)


@dataclass
class Prediction:
    direction: int  # +1 / -1 / 0
    confidence: float  # fraction des K voisins dans la direction majoritaire
    n_matches: int
    avg_fwd_pct: float
    distance: float  # distance moyenne des voisins retenus
    label: str

    @property
    def actionable(self) -> bool:
        return self.direction != 0 and self.n_matches > 0


class PatternMemory:
    """Banque de vecteurs + labels / forward returns observés."""

    def __init__(
        self,
        vectors: np.ndarray,
        labels: np.ndarray,
        fwd_pct: np.ndarray,
        params: FeatureParams,
        *,
        built_from: str = "",
        n_source_bars: int = 0,
    ) -> None:
        self.vectors = np.asarray(vectors, dtype=np.float32)
        self.labels = np.asarray(labels, dtype=np.int8)
        self.fwd_pct = np.asarray(fwd_pct, dtype=np.float32)
        self.params = params
        self.built_from = built_from
        self.n_source_bars = n_source_bars

    @property
    def size(self) -> int:
        return int(self.vectors.shape[0])

    @classmethod
    def build_from_df(
        cls,
        df: pd.DataFrame,
        params: FeatureParams,
        *,
        stride: int = 3,
        source: str = "",
    ) -> PatternMemory:
        need = params.lookback + params.horizon
        if len(df) < need + 10:
            raise ValueError(f"historique trop court ({len(df)} < {need + 10})")

        closes = df["close"].to_numpy(dtype=np.float64)
        vectors: list[np.ndarray] = []
        labels: list[int] = []
        fwds: list[float] = []

        last_i = len(df) - params.horizon - 1
        for i in range(params.lookback - 1, last_i + 1, max(1, stride)):
            window = df.iloc[i - params.lookback + 1 : i + 1]
            try:
                vec = encode_window(window, params)
            except ValueError:
                continue
            fwd = forward_return_pct(closes, i, params.horizon)
            lab = label_from_return(fwd, params.flat_pct)
            vectors.append(vec)
            labels.append(lab)
            fwds.append(fwd)

        if not vectors:
            raise ValueError("aucun motif extrait")

        return cls(
            np.stack(vectors),
            np.array(labels, dtype=np.int8),
            np.array(fwds, dtype=np.float32),
            params,
            built_from=source,
            n_source_bars=len(df),
        )

    def query(
        self,
        vec: np.ndarray,
        *,
        top_k: int = 40,
        max_distance: float = 0.55,
    ) -> Prediction:
        if self.size == 0:
            return Prediction(0, 0.0, 0, 0.0, 1.0, "FLAT")

        v = np.asarray(vec, dtype=np.float32)
        # cosine distance = 1 - dot (vecteurs L2-normalisés)
        sims = self.vectors @ v
        dists = 1.0 - sims
        k = min(top_k, self.size)
        idx = np.argpartition(dists, k - 1)[:k]
        idx = idx[np.argsort(dists[idx])]

        # garder uniquement voisins assez proches
        mask = dists[idx] <= max_distance
        idx = idx[mask]
        if len(idx) == 0:
            return Prediction(0, 0.0, 0, 0.0, float(dists.min()), "FLAT")

        labs = self.labels[idx]
        fwds = self.fwd_pct[idx]
        # vote majoritaire (hors FLAT si possible)
        counts = {1: int(np.sum(labs == 1)), -1: int(np.sum(labs == -1)), 0: int(np.sum(labs == 0))}
        direction = max(counts, key=counts.get)
        n = len(idx)
        confidence = counts[direction] / n if n else 0.0
        return Prediction(
            direction=int(direction),
            confidence=float(confidence),
            n_matches=int(n),
            avg_fwd_pct=float(np.mean(fwds)),
            distance=float(np.mean(dists[idx])),
            label=DIR_LABEL[direction],
        )

    def append_online(
        self,
        vec: np.ndarray,
        label: int,
        fwd_pct: float,
        *,
        max_size: int = 80_000,
    ) -> None:
        """Ajoute un motif observé en live (cap mémoire)."""
        v = np.asarray(vec, dtype=np.float32).reshape(1, -1)
        self.vectors = np.vstack([self.vectors, v])
        self.labels = np.append(self.labels, np.int8(label))
        self.fwd_pct = np.append(self.fwd_pct, np.float32(fwd_pct))
        if self.size > max_size:
            cut = self.size - max_size
            self.vectors = self.vectors[cut:]
            self.labels = self.labels[cut:]
            self.fwd_pct = self.fwd_pct[cut:]

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path,
            vectors=self.vectors,
            labels=self.labels,
            fwd_pct=self.fwd_pct,
            lookback=np.array([self.params.lookback]),
            horizon=np.array([self.params.horizon]),
            flat_pct=np.array([self.params.flat_pct]),
            n_source_bars=np.array([self.n_source_bars]),
        )
        meta = {
            "size": self.size,
            "dim": int(self.vectors.shape[1]) if self.size else feature_dim(self.params.lookback),
            "lookback": self.params.lookback,
            "horizon": self.params.horizon,
            "flat_pct": self.params.flat_pct,
            "built_from": self.built_from,
            "n_source_bars": self.n_source_bars,
        }
        path.with_suffix(".json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> PatternMemory:
        path = Path(path)
        data = np.load(path)
        params = FeatureParams(
            lookback=int(data["lookback"][0]),
            horizon=int(data["horizon"][0]),
            flat_pct=float(data["flat_pct"][0]),
        )
        meta_path = path.with_suffix(".json")
        built_from = ""
        n_bars = int(data["n_source_bars"][0]) if "n_source_bars" in data.files else 0
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            built_from = str(meta.get("built_from", ""))
            n_bars = int(meta.get("n_source_bars", n_bars))
        return cls(
            data["vectors"],
            data["labels"],
            data["fwd_pct"],
            params,
            built_from=built_from,
            n_source_bars=n_bars,
        )
