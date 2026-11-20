import itertools
from pathlib import Path

import pytest

from meetplan import build_calendar, load_config, solve
from meetplan.model import generate_trips
from meetplan.report import to_csv, to_ics, to_json
from meetplan.solver import summarise

CONFIG = Path(__file__).parents[1] / "config" / "2027.toml"


@pytest.fixture(scope="module")
def cfg():
    return load_config(CONFIG)


@pytest.fixture(scope="module")
def result(cfg):
    return solve(cfg)


def test_reference_plan_matches_web_solver(result):
    # Same numbers as the JavaScript solver on the GitHub Pages site (docs/index.html).
    p = result.plan
    assert p.days_together == 70
    assert (p.leave_me, p.leave_her) == (45, 22)
    assert round(p.fares_me + p.fares_her) == 5126
    assert round(p.lodging) == 2300
    assert p.objective == pytest.approx(-1250.25, abs=0.01)


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
