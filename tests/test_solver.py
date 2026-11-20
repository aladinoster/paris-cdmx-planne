import itertools
from pathlib import Path

import pytest

from meetplan import build_calendar, load_config, solve
from meetplan.model import generate_trips
from meetplan.prices import basis_factor, load_quotes, simulate_cost
from meetplan.report import to_csv, to_ics, to_json
from meetplan.solver import summarise

CONFIG = Path(__file__).parents[1] / "config" / "2027.toml"


@pytest.fixture(scope="module")
def cfg():
    return load_config(CONFIG)


@pytest.fixture(scope="module")
def result(cfg):
    return solve(cfg)


# Reference numbers from the JavaScript solver on the GitHub Pages site (docs/index.html), per fare basis.
WEB = {
    ("typical", "both"): (70, 45, 22, 5309.59, -1433.5435),
    ("typical", "meet"): (59, 38, 15, 4481.49, -2340.0435),
    ("low", "both"): (87, 45, 22, 5561.82, -810.8170),
    ("cautious", "both"): (70, 45, 22, 5743.60, -1867.5460),
}


@pytest.mark.parametrize("key", list(WEB))
def test_matches_web_solver(cfg, key):
    basis, mode = key
    days, la, lg, fares, obj = WEB[key]
    p = solve(cfg.with_(fare_basis=basis, mode=mode)).plan
    assert (p.days_together, p.leave_me, p.leave_her) == (days, la, lg)
    assert p.fares_me + p.fares_her == pytest.approx(fares, abs=0.01)
    assert round(p.lodging) == 2300
    assert p.objective == pytest.approx(obj, abs=0.01)


def test_constraints_hold(cfg, result):
    cal, p = result.cal, result.plan
    assert p.leave_me <= cfg.leave_me and p.leave_her <= cfg.leave_her
    for a, b in itertools.pairwise(p.trips):
        assert b.s - a.e - 1 >= cfg.min_gap
    for t in p.trips:
        her_travels = t.where == "her" or t.city or (t.where == "me" and t.host_leave)
        if her_travels:
            assert not any(cal.work_her[i] and cal.her_blocked[i] for i in range(t.s, t.e + 1))


def test_every_fixed_trip_is_scheduled(cfg, result):
    done = sorted(t.fixed for t in result.plan.trips if t.fixed >= 0)
    assert done == list(range(len(cfg.fixed_trips)))
    nyc = next(t for t in result.plan.trips if t.fixed == 1)
    assert nyc.city == "nyc" and nyc.fare_me > 0 and nyc.fare_her > 0 and nyc.lodging > 0


def test_infeasible_when_leave_too_small(cfg):
    assert solve(cfg.with_(leave_me=5)).plan is None


def test_dp_is_optimal_against_brute_force(cfg):
    # Small instance: one length, no fixed trips, a gap so long that at most two trips fit.
    small = cfg.with_(mode="home", fixed_trips=(), min_days=14, max_days=14, min_gap=180, leave_me=12, leave_her=6)
    cal = build_calendar(small)
    cands = [t for ts in generate_trips(cal, small) for t in ts]

    def ok(trips):
        return (sum(t.leave_me for t in trips) <= small.leave_me and sum(t.leave_her for t in trips) <= small.leave_her)

    best = summarise(small, cal.n, []).objective
    for a in cands:
        if ok([a]):
            best = max(best, summarise(small, cal.n, [a]).objective)
    for a in cands:
        for b in cands:
            if b.s - a.e - 1 >= small.min_gap and ok([a, b]):
                best = max(best, summarise(small, cal.n, [a, b]).objective)
    assert solve(small, cal).plan.objective == pytest.approx(best, abs=1e-6)


def test_exports(result):
    assert '"days_together": 70' in to_json(result)
    assert to_csv(result).count("\n") == len(result.plan.trips) + 1
    ics = to_ics(result)
    assert ics.count("BEGIN:VEVENT") == len(result.plan.trips)
    assert "DTSTART;VALUE=DATE:20270318" in ics and "DTEND;VALUE=DATE:20270329" in ics


def test_price_database_supports_the_curves(cfg):
    # Every month with real quotes: the curve's base fare is never above the cheapest quote seen that month.
    cal = build_calendar(cfg)
    quotes = load_quotes()
    assert len(quotes) >= 40 and {q.source for q in quotes} == {"kiwi", "lastminute", "letsfg"}
    for who in ("me", "her"):
        curve = cal.curve_me if who == "me" else cal.curve_her
        for m in range(1, 12):
            qs = [q.price for q in quotes if q.who == who and q.depart.month == m and "thin" not in q.notes]
            if qs:
                mid = next(i for i, d in enumerate(cal.dates) if d.month == m and d.day == 15)
                assert curve[mid] <= min(qs) + 1e-6, (who, m, curve[mid], min(qs))


def test_monte_carlo_band(cfg, result):
    s = simulate_cost(cfg, result.plan.trips)
    assert s["p10"] < s["p50"] < s["p90"]
    planned = result.plan.travel_cost
    assert s["p10"] <= planned <= s["p90"] * 1.05   # the 'typical' plan sits inside its own band
    assert basis_factor(cfg.with_(fare_basis="low")) == 1.0
    assert basis_factor(cfg.with_(fare_basis="cautious")) > basis_factor(cfg)
