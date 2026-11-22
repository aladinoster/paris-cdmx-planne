"""Turn the fare database (data/fares.csv) into the fare model the planner uses (data/fare_model.json).

The rule is the same everywhere: a fare is the LOWEST price found in the most recent survey.

* Home curves (Paris → Mexico City, Mexico City → Paris). The seed curve in the TOML defines the segments
  (``["04-01", 862]`` = from 1 April). For each segment, take the quotes departing inside it, keep only the
  latest survey (quotes checked within ``window_days`` of the newest one) and use their minimum. Segments
  with no quotes keep the seed value, e.g. holiday peaks or months not on sale yet.
* Third cities. Per city, departure month and origin, the minimum of the latest survey becomes a ``seen``
  fare, which the fare model prefers over the seasonal estimate.

Quotes whose notes contain "thin" (a sold-out or odd result) are ignored. The output is deterministic:
the same CSV and TOML always give the same JSON.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from .config import Config
from .prices import Quote

WINDOW_DAYS = 45


def _latest_min(qs: list[Quote], window_days: int) -> tuple[float, date, int]:
    newest = max(q.checked for q in qs)
    recent = [q for q in qs if (newest - q.checked).days <= window_days]
    return min(q.price for q in recent), newest, len(recent)


def _segments(year: int, curve: tuple[tuple[str, float], ...]):
    starts = [date(year, int(k[:2]), int(k[3:])) for k, _ in curve]
    ends = starts[1:] + [date(year + 1, 1, 1)]
    return [(k, v, s, e) for (k, v), s, e in zip(curve, starts, ends)]


def rebuild(seed: Config, quotes: list[Quote], window_days: int = WINDOW_DAYS) -> dict:
    """Compute the fare model from a seed config (curves from the TOML) and the quote database."""
    usable = [q for q in quotes if "thin" not in q.notes.lower() and q.depart.year == seed.year]
    out: dict = {
        "checked": max(q.checked for q in quotes).isoformat() if quotes else None,
        "window_days": window_days,
        "n_quotes": len(quotes),
        "curve": {},
        "curve_source": {},
        "seen": {},
    }
    for who, curve in (("me", seed.curve_me), ("her", seed.curve_her)):
        pts, src = [], []
        for key, val, s, e in _segments(seed.year, curve):
            qs = [q for q in usable if q.who == who and s <= q.depart < e]
            if qs:
                low, newest, n = _latest_min(qs, window_days)
                pts.append([key, low])
                src.append({"from": key, "fare": low, "source": "observed", "checked": newest.isoformat(), "quotes": n})
            else:
                pts.append([key, val])
                src.append({"from": key, "fare": val, "source": "seed"})
        out["curve"][who] = pts
        out["curve_source"][who] = src

    groups: dict[tuple[str, int, str], list[Quote]] = defaultdict(list)
    for c in seed.cities:
        if not c.code:
            continue
        for q in usable:
            origin, _, dest = q.direction.partition("-")
            if dest == c.code and origin in ("PAR", "MEX"):
                groups[(c.id, q.depart.month, "me" if origin == "PAR" else "her")].append(q)
    for (cid, month, who), qs in sorted(groups.items()):
        low, _, _ = _latest_min(qs, window_days)
        out["seen"].setdefault(cid, {}).setdefault(str(month), {})[who] = low
    return out


def write_model(model: dict, path: str | Path) -> None:
    Path(path).write_text(json.dumps(model, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def diff(old: dict | None, new: dict) -> list[str]:
    """Human-readable list of what changed between two fare models."""
    lines = []
    old = old or {"curve": {}, "seen": {}}
    for who, label in (("me", "Paris → CDMX"), ("her", "CDMX → Paris")):
        before = dict(map(tuple, old["curve"].get(who, [])))
        for k, v in new["curve"][who]:
            if k in before and abs(before[k] - v) >= 1:
                lines.append(f"{label} from {k}: €{before[k]:,.0f} → €{v:,.0f} ({(v / before[k] - 1) * 100:+.0f}%)")
    for cid, months in new["seen"].items():
        for m, sides in months.items():
            for who, v in sides.items():
                b = old["seen"].get(cid, {}).get(m, {}).get(who)
                if b is None:
                    lines.append(f"{cid} month {m} {who}: new €{v:,.0f}")
                elif abs(b - v) >= 1:
                    lines.append(f"{cid} month {m} {who}: €{b:,.0f} → €{v:,.0f}")
    return lines
