"""Candidate trips: every (start, end, where, host-leave) combination with its value, cost and leave use."""

from __future__ import annotations

import calendar as _cal
from dataclasses import dataclass
from datetime import date

from .calendar import YearCalendar
from .config import Config, FixedTrip
from .fares import city_fare, home_fare

HOME = ("me", "her")


@dataclass(frozen=True)
class Trip:
    s: int                 # first day index (travel day)
    e: int                 # last day index (travel day)
    where: str             # "me" (you fly to CDMX), "her" (she flies to Paris) or "meet:<city id>"
    host_leave: bool       # home trips: host also takes leave on their working days
    leave_me: int
    leave_her: int
    value: float           # € value of the days together
    fare_me: float
    fare_her: float
    lodging: float
    net: float             # value + trip bonus − cost weight × (fares + lodging + overhead)
    fixed: int = -1        # index of the fixed trip it satisfies, or -1 for a free trip

    @property
    def days(self) -> int:
        return self.e - self.s + 1

    @property
    def city(self) -> str | None:
        return self.where[5:] if self.where.startswith("meet:") else None

    @property
    def fares(self) -> float:
        return self.fare_me + self.fare_her


def evaluate_trip(cal: YearCalendar, cfg: Config, s: int, e: int, where: str, host_leave: bool, fixed: int = -1) -> Trip | None:
    """Return the trip, or None if it needs her leave on a day she can't take off."""
    la = lg = 0
    value = 0.0
    V, beta, edge_f = cfg.day_value, cfg.host_working_factor, cfg.travel_day_factor
    if where.startswith("meet:"):
        city = cfg.city_map[where[5:]]
        for i in range(s, e + 1):
            edge = edge_f if i in (s, e) else 1.0
            if cal.work_me[i]:
                la += 1
            if cal.work_her[i]:
                lg += 1
                if cal.her_blocked[i]:
                    return None
            value += (V + city.bonus) * edge
        fm, fh = city_fare(cal, cfg, s, e, city, "me"), city_fare(cal, cfg, s, e, city, "her")
        lodging = city.lodging * (e - s)
        # Meeting halfway is an effort we choose to make: its cost counts at meet_cost_factor.
        wc = cfg.cost_weight * cfg.meet_cost_factor
        net = value + cfg.trip_bonus - wc * (fm + fh + lodging + cfg.trip_overhead)
        return Trip(s, e, where, False, la, lg, value, fm, fh, lodging, net, fixed)

    for i in range(s, e + 1):
        edge = edge_f if i in (s, e) else 1.0
        if where == "me":  # you travel, she hosts
            traveller_works, host_works = cal.work_me[i], cal.work_her[i]
            if traveller_works:
                la += 1
            if host_works and host_leave:
                lg += 1
                if cal.her_blocked[i]:
                    return None
        else:              # she travels, you host
            traveller_works, host_works = cal.work_her[i], cal.work_me[i]
            if traveller_works:
                lg += 1
                if cal.her_blocked[i]:
                    return None
            if host_works and host_leave:
                la += 1
        host_at_work = host_works and not host_leave
        value += V * edge * (beta if host_at_work else 1.0)
    fare = home_fare(cal, cfg, s, e, where)
    fm, fh = (fare, 0.0) if where == "me" else (0.0, fare)
    net = value + cfg.trip_bonus - cfg.cost_weight * (fare + cfg.trip_overhead)
    return Trip(s, e, where, host_leave, la, lg, value, fm, fh, 0.0, net, fixed)


def expand_where(cfg: Config, where: str) -> list[str]:
    cities = [f"meet:{c.id}" for c in cfg.cities if c.enabled]
    return {
        "home": list(HOME),
        "any": list(HOME),
        "meet:any": cities,
        "anywhere": list(HOME) + cities,
    }.get(where, [where])


def free_wheres(cfg: Config) -> list[str]:
    return expand_where(cfg, {"home": "home", "meet": "meet:any", "both": "anywhere"}[cfg.mode])


def generate_trips(cal: YearCalendar, cfg: Config) -> list[list[Trip]]:
    """All feasible candidate trips, grouped by start day."""
    by_start: list[list[Trip]] = [[] for _ in range(cal.n)]

    def add(s, e, where, fixed):
        for hl in ((False,) if where.startswith("meet:") else (False, True)):
            t = evaluate_trip(cal, cfg, s, e, where, hl, fixed)
            if t is not None:
                by_start[s].append(t)

    wheres = free_wheres(cfg)
    for s in range(cal.n):
        for length in range(cfg.min_days, cfg.max_days + 1):
            e = s + length - 1
            if e >= cal.n:
                break
            for w in wheres:
                add(s, e, w, -1)
    for k, ft in enumerate(sorted_fixed(cfg)):
        for s in range(cal.index(ft.start_min), cal.index(ft.start_max) + 1):
            for e in range(max(cal.index(ft.end_min), s), cal.index(ft.end_max) + 1):
                length = e - s + 1
                if (ft.min_days and length < ft.min_days) or (ft.max_days and length > ft.max_days):
                    continue
                if ft.focus_month and 2 * sum(cal.dates[i].month == ft.focus_month for i in range(s, e + 1)) < length:
                    continue
                for w in expand_where(cfg, ft.where):
                    add(s, e, w, k)
    return by_start


def focus_trip(cfg: Config) -> FixedTrip | None:
    """'Make sure we meet in month M': a trip starting in M with at least half of its days in M."""
    m = cfg.focus_month
    if not m:
        return None
    last = _cal.monthrange(cfg.year, m)[1]
    nxt = date(cfg.year, m + 1, 7) if m < 12 else date(cfg.year, 12, 31)
    where = cfg.focus_where or {"home": "any", "meet": "meet:any", "both": "anywhere"}[cfg.mode]
    return FixedTrip(f"{_cal.month_name[m]} together", date(cfg.year, m, 1), date(cfg.year, m, last - 6),
                     date(cfg.year, m, 7), nxt, where, cfg.min_days, cfg.max_days, m)


def sorted_fixed(cfg: Config) -> list[FixedTrip]:
    trips = list(cfg.fixed_trips)
    if (f := focus_trip(cfg)) is not None:
        trips.append(f)
    return sorted(trips, key=lambda t: t.start_min)
