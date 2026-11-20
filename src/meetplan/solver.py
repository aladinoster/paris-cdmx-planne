"""Exact dynamic programme over days.

State (t, r, a, g): free from day t, next fixed trip r, a leave days left for you, g for her.
    V[t, r, a, g] = max( end-of-year option,  max_s  B[s, r, a, g] − pen(s − t) )
    B[s, r, a, g] = max over trips starting on day s of  net + V[e+1, r', a − la, g − lg]
pen(g) = λ · max(0, g − target)², counted for every gap including before the first and after the last trip.
The (a, g) dimensions are handled as numpy arrays, so each update is one vectorised slice operation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .calendar import YearCalendar, build_calendar
from .config import Config
from .model import Trip, generate_trips, sorted_fixed

NEG = -1e30


@dataclass
class Plan:
    trips: list[Trip]
    days_together: int
    leave_me: int
    leave_her: int
    fares_me: float
    fares_her: float
    lodging: float
    longest_gap: int
    gap_penalty: float
    objective: float

    @property
    def travel_cost(self) -> float:
        return self.fares_me + self.fares_her + self.lodging


@dataclass
class Result:
    cfg: Config
    cal: YearCalendar
    plan: Plan | None
    n_candidates: int = 0
    fixed_labels: list[str] = field(default_factory=list)


def _penalty(cfg: Config, gap: int) -> float:
    return cfg.gap_lambda * max(0.0, gap - cfg.gap_target) ** 2


def solve(cfg: Config, cal: YearCalendar | None = None) -> Result:
    cal = cal or build_calendar(cfg)
    N = cal.n
    fixed = sorted_fixed(cfg)
    fixed_idx = [(cal.index(f.start_min), cal.index(f.start_max), cal.index(f.end_min)) for f in fixed]
    R = len(fixed)
    A1, G1 = cfg.leave_me + 1, cfg.leave_her + 1
    by_start = generate_trips(cal, cfg)
    pen = np.array([_penalty(cfg, g) for g in range(N + 1)])

    def r_lo(r):
        return 0 if r == 0 else fixed_idx[r - 1][2] + 1

    def r_hi(r):
        return N if r == R else fixed_idx[r][1]

    V = np.full((N + 1, R + 1, A1, G1), NEG)
    Vch = np.full((N + 1, R + 1, A1, G1), -1, dtype=np.int16)
    B = np.full((N, R + 1, A1, G1), NEG)
    Bch = np.full((N, R + 1, A1, G1), -1, dtype=np.int32)
    flat: list[Trip] = []

    for t in range(N, -1, -1):
        if t < N:
            for trip in by_start[t]:
                k = len(flat)
                flat.append(trip)
                la, lg, nt = trip.leave_me, trip.leave_her, trip.e + 1
                if la >= A1 or lg >= G1:
                    continue
                for r in range(R + 1):
                    if t < r_lo(r) or t > r_hi(r):
                        continue
                    if trip.fixed >= 0:
                        if trip.fixed != r:
                            continue
                        nr = r + 1
                    else:
                        if r < R and trip.e + 1 + cfg.min_gap > fixed_idx[r][1]:
                            continue
                        nr = r
                    cand = trip.net + V[nt, nr, : A1 - la, : G1 - lg]
                    tgt = B[t, r, la:, lg:]
                    better = cand > tgt
                    if better.any():
                        tgt[better] = cand[better]
                        Bch[t, r, la:, lg:][better] = k
        for r in range(R + 1):
            if t < r_lo(r) - 1 or t > r_hi(r):
                continue
            cur = V[t, r]
            cur[:] = -pen[N - t] if r == R else NEG
            ch = Vch[t, r]
            s0 = 0 if t == 0 else t + cfg.min_gap
            s1 = min(N - 1, t + cfg.max_gap)
            for s in range(s0, s1 + 1):
                cand = B[s, r] - pen[s - t]
                better = cand > cur
                if better.any():
                    cur[better] = cand[better]
                    ch[better] = s - t

    plan = None
    if V[0, 0, cfg.leave_me, cfg.leave_her] > NEG / 2:
        trips: list[Trip] = []
        t, r, a, g = 0, 0, cfg.leave_me, cfg.leave_her
        while t < N:
            off = int(Vch[t, r, a, g])
            if off < 0:
                break
            s = t + off
            trip = flat[int(Bch[s, r, a, g])]
            trips.append(trip)
            if trip.fixed >= 0:
                r += 1
            a -= trip.leave_me
            g -= trip.leave_her
            t = trip.e + 1
        plan = summarise(cfg, N, trips)
    return Result(cfg, cal, plan, len(flat), [f.label for f in fixed])


def summarise(cfg: Config, n_days: int, trips: list[Trip]) -> Plan:
    prev, longest, gp = 0, 0, 0.0
    for t in trips:
        gap = t.s - prev
        longest, gp, prev = max(longest, gap), gp + _penalty(cfg, gap), t.e + 1
    longest, gp = max(longest, n_days - prev), gp + _penalty(cfg, n_days - prev)
    return Plan(
        trips=trips,
        days_together=sum(t.days for t in trips),
        leave_me=sum(t.leave_me for t in trips),
        leave_her=sum(t.leave_her for t in trips),
        fares_me=sum(t.fare_me for t in trips),
        fares_her=sum(t.fare_her for t in trips),
        lodging=sum(t.lodging for t in trips),
        longest_gap=longest,
        gap_penalty=gp,
        objective=sum(t.net for t in trips) - gp,
    )
