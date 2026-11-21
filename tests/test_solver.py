import itertools
from pathlib import Path

import pytest

from meetplan import build_calendar, load_config, solve
from meetplan.model import generate_trips, sorted_fixed
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
# Keys: (fare basis, mode, focus month); values: (days, leave A, leave L, fares, lodging, objective).
WEB = {
    ("typical", "both", 4): (99, 45, 22, 7415.57, 2300, 3663.1041),
    ("typical", "meet", 4): (70, 45, 22, 5344.52, 3700, 2376.8805),
    ("low", "both", 4): (99, 45, 22, 6581.87, 2300, 4134.9501),
    ("cautious", "both", 4): (99, 45, 22, 8021.72, 2300, 3320.0486),
    ("typical", "both", None): (100, 45, 22, 7333.05, 2300, 3784.6159),
}


@pytest.mark.parametrize("key", list(WEB))
def test_matches_web_solver(cfg, key):
    basis, mode, month = key
    days, la, lg, fares, lodging, obj = WEB[key]
    p = solve(cfg.with_(fare_basis=basis, mode=mode, focus_month=month)).plan
    assert (p.days_together, p.leave_me, p.leave_her) == (days, la, lg)
    assert p.fares_me + p.fares_her == pytest.approx(fares, abs=0.01)
    assert round(p.lodging) == lodging
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
    assert done == list(range(len(sorted_fixed(cfg))))  # the four fixed trips + the April one
    nyc = next(t for t in result.plan.trips if t.fixed == 1)
    assert nyc.city == "nyc" and nyc.fare_me > 0 and nyc.fare_her > 0 and nyc.lodging > 0


def test_infeasible_when_leave_too_small(cfg):
    assert solve(cfg.with_(leave_me=5)).plan is None


def test_dp_is_optimal_against_brute_force(cfg):
    # Small instance: one length, no fixed trips, a gap so long that at most two trips fit.
    small = cfg.with_(mode="home", fixed_trips=(), focus_month=None, min_days=14, max_days=14, min_gap=180, leave_me=12, leave_her=6)
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
    assert '"days_together": 99' in to_json(result)
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


def test_month_turn_blackout(cfg):
    # Her last working day of a month and her first three of the next are blocked; weekends are skipped over.
    cal = build_calendar(cfg)
    blocked = {d.isoformat() for d, b, w in zip(cal.dates, cal.her_blocked, cal.work_her) if b and w and d.month != 8}
    for d in ("2027-03-31", "2027-04-01", "2027-04-02", "2027-04-05", "2027-04-30", "2027-05-03", "2027-05-04",
              "2027-05-05", "2027-02-02", "2027-02-03", "2027-02-04"):  # 1 Feb is a holiday, so 2-4 Feb
        assert d in blocked
    for d in ("2027-04-06", "2027-04-29", "2027-02-05", "2027-03-30"):
        assert d not in blocked


def test_april_trip(cfg, result):
    april = [t for t in result.plan.trips if 2 * sum(result.cal.dates[i].month == 4 for i in range(t.s, t.e + 1)) >= t.days]
    assert len(april) == 1


def test_lower_cost_weight_buys_more_days(cfg):
    full = solve(cfg.with_(cost_weight=1.0, trip_bonus=0.0, meet_cost_factor=1.0, focus_month=None)).plan
    light = solve(cfg.with_(focus_month=None)).plan
    assert light.days_together > full.days_together and len(light.trips) > len(full.trips)


def test_barcelona_april_uses_observed_fares(cfg):
    p = solve(cfg.with_(focus_where="meet:bcn")).plan
    t = next(t for t in p.trips if t.city == "bcn")
    f = basis_factor(cfg)
    assert build_calendar(cfg).dates[t.s].month == 4
    assert t.fare_her == pytest.approx(682 * f, rel=0.05) and t.fare_me == pytest.approx(84 * f, rel=0.05)
    assert p.days_together == 86
