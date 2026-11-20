"""Day-by-day calendar for one year: working days, holidays, her blocked days, fare curves."""

from __future__ import annotations

import calendar as _cal
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from .config import Config


@dataclass(frozen=True)
class YearCalendar:
    year: int
    dates: tuple[date, ...]
    work_me: np.ndarray       # bool[N]: you work that day (weekday, not a French holiday)
    work_her: np.ndarray      # bool[N]: she works that day
    her_blocked: np.ndarray   # bool[N]: she may not take leave that day
    hol_me: np.ndarray
    hol_her: np.ndarray
    curve_me: np.ndarray      # float[N]: round-trip fare Paris→CDMX departing that day
    curve_her: np.ndarray     # float[N]: round-trip fare CDMX→Paris departing that day
    ref_me: float             # curve value at the third-city reference day
    ref_her: float

    @property
    def n(self) -> int:
        return len(self.dates)

    def index(self, d: date) -> int:
        return (d - self.dates[0]).days


def _curve(dates: tuple[date, ...], steps: tuple[tuple[str, float], ...]) -> np.ndarray:
    raw = np.empty(len(dates))
    for i, d in enumerate(dates):
        md = d.strftime("%m-%d")
        v = steps[0][1]
        for k, p in steps:
            if md >= k:
                v = p
        raw[i] = v
    # 5-day centred moving average (window shrinks at the year edges)
    out = np.empty_like(raw)
    for i in range(len(raw)):
        lo, hi = max(0, i - 2), min(len(raw), i + 3)
        out[i] = raw[lo:hi].mean()
    return out


def build_calendar(cfg: Config) -> YearCalendar:
    start = date(cfg.year, 1, 1)
    n = 366 if _cal.isleap(cfg.year) else 365
    dates = tuple(start + timedelta(i) for i in range(n))
    weekend = np.array([d.weekday() >= 5 for d in dates])
    hol_me = np.array([d in cfg.holidays_me for d in dates])
    hol_her = np.array([d in cfg.holidays_her for d in dates])
    blocked = np.array([
        (cfg.block_august and d.month == 8)
        or (_cal.monthrange(d.year, d.month)[1] - d.day < cfg.month_end_days)
        or d in cfg.extra_blackout
        for d in dates
    ])
    cme, cher = _curve(dates, cfg.curve_me), _curve(dates, cfg.curve_her)
    ri = next(i for i, d in enumerate(dates) if d.strftime("%m-%d") == cfg.reference_day)
    return YearCalendar(cfg.year, dates, ~weekend & ~hol_me, ~weekend & ~hol_her, blocked, hol_me, hol_her,
                        cme, cher, float(cme[ri]), float(cher[ri]))
