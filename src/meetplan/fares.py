"""Fare model: estimated round-trip prices for home trips and third-city meetups."""

from __future__ import annotations

from .calendar import YearCalendar
from .config import City, Config
from .prices import basis_factor


def _blend(curve, s: int, e: int) -> float:
    # outbound date drives most of a return fare
    return 0.6 * curve[s] + 0.4 * curve[e]


def home_fare(cal: YearCalendar, cfg: Config, s: int, e: int, who: str) -> float:
    """Round trip for the traveller: who='me' flies Paris→CDMX, who='her' flies CDMX→Paris."""
    curve = cal.curve_me if who == "me" else cal.curve_her
    f = 1.0
    if cal.dates[s].weekday() in (4, 5):  # Fri / Sat departure
        f += cfg.weekend_surcharge
    if cal.dates[e].weekday() == 6:       # Sunday return
        f += cfg.sunday_return_surcharge
    return cfg.fare_scale * basis_factor(cfg) * _blend(curve, s, e) * f


def city_fare(cal: YearCalendar, cfg: Config, s: int, e: int, city: City, who: str) -> float:
    """Round trip from who's home to a third city, scaled by that origin's seasonal curve."""
    curve, ref, base = (cal.curve_me, cal.ref_me, city.ref_me) if who == "me" else (cal.curve_her, cal.ref_her, city.ref_her)
    f = 1.0 + (cfg.weekend_surcharge if cal.dates[s].weekday() in (4, 5) else 0.0)
    return cfg.fare_scale * basis_factor(cfg) * base * (_blend(curve, s, e) / ref) * f


def reference_fare(cal: YearCalendar, cfg: Config, s: int, who: str, nights: int = 10) -> float:
    """Smooth midweek reference (no weekday surcharge), as plotted on the web page."""
    e = min(cal.n - 1, s + nights)
    curve = cal.curve_me if who == "me" else cal.curve_her
    return cfg.fare_scale * basis_factor(cfg) * _blend(curve, s, e)
