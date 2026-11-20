"""Price uncertainty: the fare band, the planning basis, the observed-quote database and Monte Carlo."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np

from .config import Config

DATA = Path(__file__).resolve().parents[2] / "data" / "fares.csv"


def tri_quantile(u, a: float, c: float, b: float):
    """Inverse CDF of Triangular(a, c, b); works on floats and numpy arrays."""
    u = np.asarray(u, dtype=float)
    f = (c - a) / (b - a)
    lo = a + np.sqrt(u * (b - a) * (c - a))
    hi = b - np.sqrt((1 - u) * (b - a) * (b - c))
    return np.where(u < f, lo, hi)


def basis_factor(cfg: Config) -> float:
    """Multiplier applied to the lowest-seen curve for planning."""
    a, c, b = cfg.band
    return {"low": 1.0, "typical": (a + b + c) / 3, "cautious": float(tri_quantile(0.9, a, c, b))}[cfg.fare_basis]


@dataclass(frozen=True)
class Quote:
    checked: date
    source: str
    direction: str  # "PAR-MEX" (me flies) or "MEX-PAR" (her flies)
    depart: date
    ret: date
    kind: str
    price: float
    nonstop: float | None
    carriers: str
    notes: str

    @property
    def who(self) -> str:
        return "me" if self.direction == "PAR-MEX" else "her"


def load_quotes(path: str | Path = DATA) -> list[Quote]:
    with open(path, newline="", encoding="utf-8") as f:
        return [Quote(date.fromisoformat(r["checked"]), r["source"], r["direction"], date.fromisoformat(r["depart"]),
                      date.fromisoformat(r["return"]), r["kind"], float(r["price_eur"]),
                      float(r["cheapest_nonstop_eur"]) if r["cheapest_nonstop_eur"] else None, r["carriers"], r["notes"])
                for r in csv.DictReader(f)]


def monthly_summary(quotes: list[Quote]) -> dict[tuple[str, int], dict]:
    """Per (who, month): number of quotes, min, max, sources."""
    out: dict[tuple[str, int], dict] = {}
    for q in quotes:
        k = (q.who, q.depart.month)
        d = out.setdefault(k, {"n": 0, "min": math.inf, "max": 0.0, "sources": set()})
        d["n"] += 1
        d["min"] = min(d["min"], q.price)
        d["max"] = max(d["max"], q.price)
        d["sources"].add(q.source)
    return out


def simulate_cost(cfg: Config, trips, n: int = 4000, seed: int = 12345) -> dict[str, float]:
    """Redraw every ticket of a plan from the band; P10 / P50 / P90 of total travel cost (fares + hotels)."""
    rng = np.random.default_rng(seed)
    a, c, b = cfg.band
    f = basis_factor(cfg)
    base = np.array([v / f for t in trips for v in (t.fare_me, t.fare_her) if v > 0])
    fixed = sum(t.lodging for t in trips)
    if base.size == 0:
        return {"p10": fixed, "p50": fixed, "p90": fixed}
    m = tri_quantile(rng.random((n, base.size)), a, c, b)
    tot = fixed + (m * base).sum(axis=1)
    p10, p50, p90 = np.quantile(tot, [0.1, 0.5, 0.9])
    return {"p10": float(p10), "p50": float(p50), "p90": float(p90)}
