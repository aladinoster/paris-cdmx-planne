"""Command-line interface: `meetplan solve | compare | risk | where | fares | refresh | build-web`."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .calendar import build_calendar
from .config import MODES, load_config, model_path
from .fares import reference_fare
from .prices import load_quotes, monthly_summary, simulate_cost
from .report import text_report, to_csv, to_ics, to_json, where_to_meet
from .solver import solve

DEFAULT_CONFIG = "config/2027.toml"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _load(args):
    cfg = load_config(args.config)
    if getattr(args, "mode", None):
        cfg = cfg.with_(mode=args.mode)
    if getattr(args, "fare_scale", None):
        cfg = cfg.with_(fare_scale=args.fare_scale)
    if getattr(args, "basis", None):
        cfg = cfg.with_(fare_basis=args.basis)
    return cfg


def cmd_solve(args) -> int:
    cfg = _load(args)
    t0 = time.perf_counter()
    res = solve(cfg)
    dt = time.perf_counter() - t0
    print(text_report(res))
    if res.plan:
        sim = simulate_cost(cfg, res.plan.trips)
        print(f"If prices swing: travel cost €{sim['p10']:,.0f}–€{sim['p90']:,.0f} in 80% of 4,000 simulations "
              f"(median €{sim['p50']:,.0f}; planned with '{cfg.fare_basis}' fares)")
    print(f"(exact optimum over {res.n_candidates:,} candidate trips in {dt:.1f}s)")
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        writer = {".json": to_json, ".csv": to_csv, ".ics": to_ics}.get(out.suffix.lower())
        if writer is None:
            print(f"Unknown output type {out.suffix!r}: use .json, .csv or .ics", file=sys.stderr)
            return 2
        out.write_text(writer(res), encoding="utf-8")
        print(f"Wrote {out}")
    return 0 if res.plan else 1


def cmd_compare(args) -> int:
    base = load_config(args.config)
    labels = {"home": "Only Paris or Mexico City", "both": "Home or a third city", "meet": "Only third cities"}
    print(f"{'Scenario':<28}{'Days':>6}{'Trips':>7}{'Fares':>10}{'Lodging':>10}{'Leave A/L':>14}{'Score':>10}")
    for mode in MODES:
        p = solve(base.with_(mode=mode)).plan
        if p is None:
            print(f"{labels[mode]:<28}  infeasible")
            continue
        print(f"{labels[mode]:<28}{p.days_together:>6}{len(p.trips):>7}{p.fares_me + p.fares_her:>10,.0f}"
              f"{p.lodging:>10,.0f}{p.leave_me:>8}/{p.leave_her:<5}{p.objective:>10,.0f}")
    return 0


def cmd_risk(args) -> int:
    """Re-plan with optimistic / expected / cautious fares and price each plan with Monte Carlo."""
    base = load_config(args.config)
    print(f"{'Plan with fares':<18}{'Days':>6}{'Trips':>7}{'Cost (plan)':>13}{'P10':>9}{'P50':>9}{'P90':>9}")
    for basis in ("low", "typical", "cautious"):
        cfg = base.with_(fare_basis=basis)
        p = solve(cfg).plan
        if p is None:
            print(f"{basis:<18}  infeasible")
            continue
        s = simulate_cost(cfg, p.trips)
        print(f"{basis:<18}{p.days_together:>6}{len(p.trips):>7}{p.travel_cost:>13,.0f}"
              f"{s['p10']:>9,.0f}{s['p50']:>9,.0f}{s['p90']:>9,.0f}")
    print("\nP10–P90: realistic travel cost when every ticket is redrawn from the price band.")
    return 0


def cmd_frontier(args) -> int:
    """Sweep the value of a day together: the days-vs-cost trade-off curve of optimal plans."""
    base = load_config(args.config)
    print(f"{'€/day':>6}{'Days':>6}{'Trips':>7}{'Cost':>9}{'P10–P90':>16}{'Longest gap':>13}")
    for v in (40, 60, 80, 100, 120, 150, 180, 220, 260, 320):
        cfg = base.with_(day_value=float(v))
        p = solve(cfg).plan
        if p is None:
            continue
        s = simulate_cost(cfg, p.trips, n=1500)
        rng = f"{s['p10'] / 1000:.1f}k–{s['p90'] / 1000:.1f}k"
        print(f"{v:>6}{p.days_together:>6}{len(p.trips):>7}{p.travel_cost:>9,.0f}{rng:>16}{p.longest_gap:>10} d")
    return 0


def cmd_data(args) -> int:
    """Summarise the price database (data/fares.csv)."""
    qs = load_quotes(args.data) if args.data else load_quotes()
    srcs = sorted({q.source for q in qs})
    print(f"{len(qs)} quotes from {', '.join(srcs)} (checked {min(q.checked for q in qs)})\n")
    summ = monthly_summary(qs)
    print(f"{'Month':<7}{'Paris→CDMX  n   min   max':>28}{'CDMX→Paris  n   min   max':>30}")
    for m in range(1, 13):
        cells = []
        for who in ("me", "her"):
            d = summ.get((who, m))
            cells.append(f"{d['n']:>13}{d['min']:>6,.0f}{d['max']:>6,.0f}" if d else f"{'–':>13}{'':>12}")
        print(f"{MONTHS[m - 1]:<7}{cells[0]:>28}{cells[1]:>30}")
    ns = [q.nonstop / q.price for q in qs if q.nonstop and q.who != "city"]
    print(f"\nCheapest nonstop vs cheapest overall: ×{min(ns):.2f}–×{max(ns):.2f} (median ×{sorted(ns)[len(ns) // 2]:.2f})")
    return 0


def cmd_where(args) -> int:
    cfg = load_config(args.config)
    cal = build_calendar(cfg)
    rows = where_to_meet(cal, cfg, args.month, args.nights)
    print(f"{args.nights}-night trip from 15 {MONTHS[args.month - 1]} {cfg.year}, cheapest first\n")
    print(f"{'Place':<28}{'Kind':<7}{cfg.name_me[:9]:>11}{cfg.name_her[:9]:>10}{'Lodging':>10}{'Total':>9}")
    for r in rows:
        dash = lambda v: f"{v:,.0f}" if v else "–"
        print(f"{r['place']:<28}{r['kind']:<7}{dash(r['fare_me']):>11}{dash(r['fare_her']):>10}"
              f"{dash(r['lodging']):>10}{r['total']:>9,.0f}")
    return 0


def cmd_fares(args) -> int:
    cfg = load_config(args.config)
    cal = build_calendar(cfg)
    print("Average estimated round-trip fare by departure month (10-night midweek trip)\n")
    print(f"{'Month':<7}{cfg.name_me[:9]:>9}{cfg.name_her[:9]:>11}  Cheaper (who flies)")
    for m in range(1, 13):
        idx = [i for i, d in enumerate(cal.dates) if d.month == m]
        a = sum(reference_fare(cal, cfg, i, "me") for i in idx) / len(idx)
        b = sum(reference_fare(cal, cfg, i, "her") for i in idx) / len(idx)
        who = "about the same" if abs(a - b) < 25 else (f"{cfg.name_me}, by €{b - a:,.0f}" if a < b else f"{cfg.name_her}, by €{a - b:,.0f}")
        print(f"{MONTHS[m - 1]:<7}{a:>9,.0f}{b:>11,.0f}  {who}")
    return 0


def _model_file(config: str) -> Path:
    import tomllib
    rel = tomllib.loads(Path(config).read_text(encoding="utf-8"))["fares"].get("model", "../data/fare_model.json")
    return model_path(config, rel)


def cmd_refresh(args) -> int:
    """Rebuild data/fare_model.json from data/fares.csv (latest survey's lowest fare per segment / city-month)."""
    from .refresh import diff, rebuild, write_model

    seed = load_config(args.config, use_model=False)
    quotes = load_quotes(args.data) if args.data else load_quotes()
    out = _model_file(args.config)
    old = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    model = rebuild(seed, quotes, args.window)
    changes = diff(old, model)
    write_model(model, out)
    print(f"Fare model: {len(quotes)} quotes, newest checked {model['checked']} → {out}")
    obs = sum(s["source"] == "observed" for w in ("me", "her") for s in model["curve_source"][w])
    tot = sum(len(model["curve_source"][w]) for w in ("me", "her"))
    print(f"  {obs}/{tot} curve segments observed, {sum(len(v) for v in model['seen'].values())} city-months")
    print("  Changes:" if changes else "  No changes.")
    for line in changes:
        print("   ", line)
    return 0


def cmd_add_quote(args) -> int:
    """Append one observed fare to data/fares.csv (skips exact duplicates)."""
    import csv
    from datetime import date

    from .prices import DATA

    path = Path(args.data) if args.data else DATA
    for d in (args.depart, args.ret):
        date.fromisoformat(d)
    if args.source not in ("kiwi", "lastminute", "letsfg"):
        raise ValueError("source must be kiwi, lastminute or letsfg")
    if args.kind not in ("best_in_month", "best_in_window", "fixed_dates"):
        raise ValueError("kind must be best_in_month, best_in_window or fixed_dates")
    o, _, d = args.direction.partition("-")
    if not (len(o) == 3 and len(d) == 3 and o.isupper() and d.isupper()) or "PAR" not in (o, d) and "MEX" not in (o, d):
        raise ValueError("direction must look like PAR-MEX, MEX-PAR, PAR-BCN, MEX-ROM…")
    checked = args.checked or date.today().isoformat()
    row = [checked, args.source, args.direction, args.depart, args.ret, args.kind, f"{args.price:g}",
           f"{args.nonstop:g}" if args.nonstop else "", args.carriers, args.notes]
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    key = lambda r: (r[0], r[1], r[2], r[3], r[4], float(r[6]))
    if any(key(r) == key(row) for r in rows[1:]):
        print("duplicate, skipped:", ",".join(row))
        return 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f, lineterminator="\n").writerow(row)
    print("added:", ",".join(row))
    return 0


def cmd_build_web(args) -> int:
    """Write docs/index.html from web/ + config + fare model + quotes."""
    from .web import build_page, web_data

    cfg = load_config(args.config)
    mf = _model_file(args.config)
    model = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else None
    html = build_page(web_data(cfg, load_quotes(), model))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(html, encoding="utf-8")
    print(f"Wrote {args.out} ({len(html) / 1024:.0f} KB, prices checked {cfg.fares_checked})")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="meetplan", description="Plan a long-distance year of trips, exactly.")
    ap.add_argument("-c", "--config", default=DEFAULT_CONFIG, help=f"input TOML (default: {DEFAULT_CONFIG})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("solve", help="find the optimal plan")
    p.add_argument("--mode", choices=MODES, help="override the config's scenario")
    p.add_argument("--fare-scale", type=float, help="multiply every fare, e.g. 1.1 for +10%%")
    p.add_argument("--basis", choices=("low", "typical", "cautious"), help="fares to plan with")
    p.add_argument("-o", "--out", help="also write the plan to .json, .csv or .ics")
    p.set_defaults(func=cmd_solve)

    p = sub.add_parser("compare", help="solve all three scenarios side by side")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("risk", help="re-plan with low / typical / cautious fares + Monte Carlo cost range")
    p.set_defaults(func=cmd_risk)

    p = sub.add_parser("frontier", help="days-vs-cost trade-off of optimal plans")
    p.set_defaults(func=cmd_frontier)

    p = sub.add_parser("data", help="summarise the fare database")
    p.add_argument("--data", help="path to a fares CSV (default: data/fares.csv)")
    p.set_defaults(func=cmd_data)

    p = sub.add_parser("where", help="rank places to meet for a given month")
    p.add_argument("month", type=int, choices=range(1, 13), metavar="MONTH", help="1–12")
    p.add_argument("--nights", type=int, default=10)
    p.set_defaults(func=cmd_where)

    p = sub.add_parser("fares", help="who should fly each month (home trips)")
    p.set_defaults(func=cmd_fares)

    p = sub.add_parser("refresh", help="rebuild the fare model (data/fare_model.json) from data/fares.csv")
    p.add_argument("--data", help="path to a fares CSV (default: data/fares.csv)")
    p.add_argument("--window", type=int, default=45, help="days that count as one survey (default 45)")
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("add-quote", help="append one observed fare to data/fares.csv")
    p.add_argument("--source", required=True, help="kiwi | lastminute | letsfg")
    p.add_argument("--direction", required=True, help="PAR-MEX, MEX-PAR, PAR-BCN, MEX-ROM…")
    p.add_argument("--depart", required=True, help="YYYY-MM-DD")
    p.add_argument("--return", dest="ret", required=True, help="YYYY-MM-DD")
    p.add_argument("--kind", required=True, help="best_in_month | best_in_window | fixed_dates")
    p.add_argument("--price", type=float, required=True, help="cheapest return fare, EUR")
    p.add_argument("--nonstop", type=float, help="cheapest nonstop fare, EUR, if seen")
    p.add_argument("--carriers", default="")
    p.add_argument("--notes", default="")
    p.add_argument("--checked", help="date checked (default: today)")
    p.add_argument("--data", help="path to a fares CSV (default: data/fares.csv)")
    p.set_defaults(func=cmd_add_quote)

    p = sub.add_parser("build-web", help="build the web page from web/ and the data")
    p.add_argument("-o", "--out", default="docs/index.html")
    p.set_defaults(func=cmd_build_web)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, FileNotFoundError) as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
