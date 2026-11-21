"""Load and validate the planner input (a TOML file, see config/2027.toml)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

MODES = ("home", "both", "meet")


@dataclass(frozen=True)
class City:
    id: str
    name: str
    beach: bool
    ref_me: float
    ref_her: float
    lodging: float
    bonus: float = 0.0
    enabled: bool = True
    quotes: str = ""
    seen: tuple[tuple[int, str, float], ...] = ()  # (month, "me"|"her", lowest fare observed that month)


@dataclass(frozen=True)
class FixedTrip:
    label: str
    start_min: date
    start_max: date
    end_min: date
    end_max: date
    where: str  # me | her | home | meet:<id> | meet:any | anywhere
    min_days: int | None = None   # optional length limits (used by the focus-month trip)
    max_days: int | None = None
    focus_month: int | None = None  # if set, at least half of the trip's days fall in this month (1-12)


@dataclass(frozen=True)
class Quote:
    who: str
    start: date
    end: date
    price: float


@dataclass(frozen=True)
class Config:
    year: int
    mode: str
    leave_me: int
    leave_her: int
    min_days: int
    max_days: int
    min_gap: int
    max_gap: int
    day_value: float
    host_working_factor: float
    travel_day_factor: float
    trip_overhead: float
    gap_target: float
    gap_lambda: float
    last_working_days: int
    first_working_days: int
    block_august: bool
    extra_blackout: frozenset[date]
    holidays_me: frozenset[date]
    holidays_her: frozenset[date]
    fare_scale: float
    weekend_surcharge: float
    sunday_return_surcharge: float
    reference_day: str
    curve_me: tuple[tuple[str, float], ...]
    curve_her: tuple[tuple[str, float], ...]
    quotes: tuple[Quote, ...]
    fixed_trips: tuple[FixedTrip, ...]
    cities: tuple[City, ...] = field(default_factory=tuple)
    name_me: str = "Me"
    name_her: str = "Her"
    fare_basis: str = "typical"
    band: tuple[float, float, float] = (1.0, 1.08, 1.30)
    cost_weight: float = 1.0       # how much a euro counts in the objective (1 = fully)
    meet_cost_factor: float = 1.0  # extra multiplier on the cost of third-city trips (effort we choose to make)
    trip_bonus: float = 0.0        # € value of each trip, rewarding frequency
    focus_month: int | None = None  # 1-12: make sure one trip falls in this month
    focus_where: str | None = None  # where that trip goes (default: follows `mode`)

    def with_(self, **kw) -> Config:
        """Return a copy with some fields replaced (used for scenarios)."""
        return replace(self, **kw)

    @property
    def city_map(self) -> dict[str, City]:
        return {c.id: c for c in self.cities}


def _d(s: str) -> date:
    return date.fromisoformat(s)


def load_config(path: str | Path) -> Config:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return parse_config(raw)


def parse_config(raw: dict) -> Config:
    fares = raw["fares"]
    cfg = Config(
        year=int(raw["year"]),
        mode=raw.get("mode", "both"),
        leave_me=int(raw["leave"]["me"]),
        leave_her=int(raw["leave"]["her"]),
        min_days=int(raw["trips"]["min_days"]),
        max_days=int(raw["trips"]["max_days"]),
        min_gap=int(raw["trips"]["min_gap"]),
        max_gap=int(raw["trips"]["max_gap"]),
        day_value=float(raw["value"]["day"]),
        host_working_factor=float(raw["value"]["host_working_factor"]),
        travel_day_factor=float(raw["value"]["travel_day_factor"]),
        trip_overhead=float(raw["value"]["trip_overhead"]),
        gap_target=float(raw["value"]["gap_target"]),
        gap_lambda=float(raw["value"]["gap_lambda"]),
        last_working_days=int(raw["her_constraints"].get("last_working_days", 1)),
        first_working_days=int(raw["her_constraints"].get("first_working_days", 3)),
        block_august=bool(raw["her_constraints"]["block_august"]),
        extra_blackout=frozenset(map(_d, raw["her_constraints"].get("extra_blackout", []))),
        holidays_me=frozenset(map(_d, raw["holidays"]["me"])),
        holidays_her=frozenset(map(_d, raw["holidays"]["her"])),
        fare_scale=float(fares.get("scale", 1.0)),
        weekend_surcharge=float(fares.get("weekend_departure_surcharge", 0.04)),
        sunday_return_surcharge=float(fares.get("sunday_return_surcharge", 0.03)),
        reference_day=fares.get("reference_day", "05-13"),
        curve_me=tuple((k, float(v)) for k, v in fares["curve"]["me"]),
        curve_her=tuple((k, float(v)) for k, v in fares["curve"]["her"]),
        quotes=(),
        fixed_trips=tuple(
            FixedTrip(t["label"], _d(t["start"][0]), _d(t["start"][1]), _d(t["end"][0]), _d(t["end"][1]), t["where"])
            for t in raw.get("fixed_trips", [])
        ),
        cities=tuple(
            City(c["id"], c["name"], bool(c.get("beach", False)), float(c["ref_me"]), float(c["ref_her"]),
                 float(c["lodging"]), float(c.get("bonus", 0)), bool(c.get("enabled", True)), c.get("quotes", ""),
                 tuple((int(m), who, float(v)) for m, sides in c.get("seen", {}).items() for who, v in sides.items()))
            for c in raw.get("cities", [])
        ),
    )
    names = raw.get("names", {})
    val, focus = raw["value"], raw.get("focus", {})
    fm = focus.get("month")
    cfg = replace(cfg, cost_weight=float(val.get("cost_weight", 1.0)),
                  meet_cost_factor=float(val.get("meet_cost_factor", 1.0)), trip_bonus=float(val.get("trip_bonus", 0.0)),
                  focus_month=int(fm) if fm else None, focus_where=focus.get("where") or None)
    cfg = replace(cfg, name_me=names.get("me", "Me"), name_her=names.get("her", "Her"),
                  fare_basis=fares.get("basis", "typical"),
                  band=(float(fares.get("band_low", 1.0)), float(fares.get("band_mode", 1.08)),
                        float(fares.get("band_high", 1.30))))
    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    errors = []
    if cfg.fare_basis not in ("low", "typical", "cautious"):
        errors.append(f"fares.basis must be low, typical or cautious, got {cfg.fare_basis!r}")
    a, c, b = cfg.band
    if not a <= c <= b or a <= 0:
        errors.append("fares band must satisfy 0 < band_low ≤ band_mode ≤ band_high")
    if cfg.mode not in MODES:
        errors.append(f"mode must be one of {MODES}, got {cfg.mode!r}")
    if not 1 <= cfg.min_days <= cfg.max_days:
        errors.append("trips.min_days must be ≥ 1 and ≤ trips.max_days")
    ids = cfg.city_map
    for t in cfg.fixed_trips:
        if not (t.start_min <= t.start_max and t.end_min <= t.end_max and t.start_min <= t.end_max):
            errors.append(f"fixed trip {t.label!r}: dates are in the wrong order")
        for d in (t.start_min, t.start_max, t.end_min, t.end_max):
            if d.year != cfg.year:
                errors.append(f"fixed trip {t.label!r}: {d} is outside {cfg.year}")
        w = t.where
        if not (w in ("me", "her", "home", "meet:any", "anywhere") or (w.startswith("meet:") and w[5:] in ids)):
            errors.append(f"fixed trip {t.label!r}: unknown where={w!r}")
    if cfg.focus_month is not None and not 1 <= cfg.focus_month <= 12:
        errors.append("focus.month must be 1-12")
    if errors:
        raise ValueError("Invalid config:\n  - " + "\n  - ".join(errors))
