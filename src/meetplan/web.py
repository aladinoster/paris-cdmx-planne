"""Build the web page (docs/index.html) from web/page.html + web/solver.js and the repo's data.

Everything the page knows about fares and defaults is generated here from config/2027.toml, the fare model
(data/fare_model.json) and the quote database (data/fares.csv), so the TOML and CSV are the single source
of truth. The same JS solver code is used by the parity tests (tests/test_web.py), which check that the page and
the Python package give identical plans.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import Config
from .prices import Quote

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
SRC = {"kiwi": "Kiwi", "lastminute": "lastminute", "letsfg": "LetsFG"}
KIND = {"best_in_month": "month", "best_in_window": "window", "fixed_dates": "date", "date": "date"}


def js_params(cfg: Config) -> dict:
    """The page's default settings (its `P` object) for a config."""
    iso = lambda ds: sorted(d.isoformat() for d in ds)
    return {
        "nameMe": cfg.name_me, "nameHer": cfg.name_her, "year": cfg.year,
        "leaveMe": cfg.leave_me, "leaveHer": cfg.leave_her, "Lmin": cfg.min_days, "Lmax": cfg.max_days,
        "minGap": cfg.min_gap, "maxGap": cfg.max_gap, "V": cfg.day_value, "beta": cfg.host_working_factor,
        "travelDayValue": cfg.travel_day_factor, "C": cfg.trip_overhead, "priceScale": cfg.fare_scale,
        "gapTarget": cfg.gap_target, "lambda": cfg.gap_lambda,
        "lastWD": cfg.last_working_days, "firstWD": cfg.first_working_days, "blockAugust": cfg.block_august,
        "costWeight": cfg.cost_weight, "meetCostFactor": cfg.meet_cost_factor, "tripBonus": cfg.trip_bonus,
        "focusMonth": cfg.focus_month - 1 if cfg.focus_month else -1, "focusWhere": cfg.focus_where,
        "holidaysMe": iso(cfg.holidays_me), "holidaysHer": iso(cfg.holidays_her),
        "blackoutHerExtra": iso(cfg.extra_blackout),
        "mode": cfg.mode, "fareBasis": cfg.fare_basis,
        "citiesOff": [c.id for c in cfg.cities if not c.enabled],
        "cityBonus": {c.id: c.bonus for c in cfg.cities},
        "lodging": {c.id: c.lodging for c in cfg.cities},
        "required": [
            {"label": t.label, "label_es": t.label_es or t.label, "sMin": t.start_min.isoformat(),
             "sMax": t.start_max.isoformat(), "eMin": t.end_min.isoformat(), "eMax": t.end_max.isoformat(),
             "dir": t.where}
            for t in cfg.fixed_trips
        ],
    }


def web_data(cfg: Config, quotes: list[Quote], model: dict | None = None) -> dict:
    """Everything the JS solver and the page read from `Planner.DATA`."""
    a, c, b = cfg.band
    src = (model or {}).get("curve_source") or {
        w: [{"from": k, "fare": v, "source": "seed"} for k, v in curve]
        for w, curve in (("me", cfg.curve_me), ("her", cfg.curve_her))
    }
    return {
        "checked": cfg.fares_checked,
        "curve": {"me": [list(p) for p in cfg.curve_me], "her": [list(p) for p in cfg.curve_her]},
        "curveSource": src,
        "band": {"a": a, "c": c, "b": b},
        "weekend": cfg.weekend_surcharge, "sunday": cfg.sunday_return_surcharge,
        "refDay": cfg.reference_day,
        "quotes": [
            {"dir": q.who, "src": SRC.get(q.source, q.source), "kind": KIND.get(q.kind, q.kind),
             "start": q.depart.isoformat(), "end": q.ret.isoformat(), "price": q.price, "via": q.carriers,
             "checked": q.checked.isoformat()}
            for q in sorted(quotes, key=lambda q: (q.direction, q.depart, q.source)) if q.who in ("me", "her")
        ],
        "cities": [
            {"id": c.id, "name": c.name, "beach": c.beach, "ref": {"me": c.ref_me, "her": c.ref_her},
             "lodging": c.lodging, "q": c.quotes,
             **({"seen": {str(m): {w: v for mm, w, v in c.seen if mm == m} for m in sorted({m for m, _, _ in c.seen})}}
                if c.seen else {})}
            for c in cfg.cities
        ],
        "defaults": js_params(cfg),
    }


def solver_js(data: dict) -> str:
    code = (WEB / "solver.js").read_text(encoding="utf-8")
    assert "/*__DATA__*/null" in code
    return code.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False))


def build_page(data: dict) -> str:
    page = (WEB / "page.html").read_text(encoding="utf-8")
    assert "/*__SOLVER__*/" in page
    return page.replace("/*__SOLVER__*/", solver_js(data))
