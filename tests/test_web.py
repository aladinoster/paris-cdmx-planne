"""The web page and the Python package must give identical plans, whatever the data says.

Runs the page's own solver (web/solver.js with the generated data injected) in Node and compares it with
meetplan.solve on several scenarios. Skipped when Node is not installed.
"""

import json
import shutil
import subprocess

import pytest

from meetplan import load_config, solve
from meetplan.prices import load_quotes
from meetplan.web import build_page, js_params, solver_js, web_data

CONFIG = "config/2027.toml"
SCENARIOS = [
    {},
    {"mode": "meet"},
    {"mode": "home"},
    {"fare_basis": "low"},
    {"fare_basis": "cautious"},
    {"focus_month": None},
    {"focus_where": "meet:bcn"},
    {"cost_weight": 1.0, "trip_bonus": 0.0, "meet_cost_factor": 1.0},
]

RUNNER = """
global.window = {};
const P = require(process.argv[2]);
const runs = JSON.parse(require('fs').readFileSync(process.argv[3], 'utf8'));
const out = runs.map(p => { const r = P.solve(p), e = r.best.ev;
  return { days: e.days, la: e.la, lg: e.lg, fares: e.payMe + e.payHer, lodging: e.lodging, objective: e.objective,
           trips: r.best.trips.map(o => [r.cal.days[o.s].iso, r.cal.days[o.e].iso, o.dir]) }; });
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_web_and_python_agree(tmp_path):
    base = load_config(CONFIG)
    data = web_data(base, load_quotes())
    (tmp_path / "solver.js").write_text(solver_js(data), encoding="utf-8")
    (tmp_path / "run.js").write_text(RUNNER, encoding="utf-8")
    cfgs = [base.with_(**kw) for kw in SCENARIOS]
    (tmp_path / "runs.json").write_text(json.dumps([js_params(c) for c in cfgs]), encoding="utf-8")
    res = subprocess.run(["node", str(tmp_path / "run.js"), str(tmp_path / "solver.js"), str(tmp_path / "runs.json")],
                         capture_output=True, text=True, check=True)
    for kw, cfg, js in zip(SCENARIOS, cfgs, json.loads(res.stdout)):
        r = solve(cfg)
        p = r.plan
        py_trips = [[r.cal.dates[t.s].isoformat(), r.cal.dates[t.e].isoformat(), t.where] for t in p.trips]
        assert (p.days_together, p.leave_me, p.leave_her) == (js["days"], js["la"], js["lg"]), kw
        assert p.fares_me + p.fares_her == pytest.approx(js["fares"], abs=0.01), kw
        assert p.lodging == pytest.approx(js["lodging"], abs=0.01), kw
        assert p.objective == pytest.approx(js["objective"], abs=1e-3), kw
        assert py_trips == js["trips"], kw


def test_page_builds_with_data():
    cfg = load_config(CONFIG)
    html = build_page(web_data(cfg, load_quotes()))
    assert "/*__SOLVER__*/" not in html and "/*__DATA__*/null" not in html
    assert cfg.fares_checked and f'"checked": "{cfg.fares_checked}"' in html
