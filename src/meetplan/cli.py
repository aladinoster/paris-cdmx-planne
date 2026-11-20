"""Command-line interface: `meetplan solve | compare | where | fares`."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .calendar import build_calendar
from .config import MODES, load_config
from .fares import reference_fare
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
    return cfg


def cmd_solve(args) -> int:
    cfg = _load(args)
    t0 = time.perf_counter()
    res = solve(cfg)
    dt = time.perf_counter() - t0
    print(text_report(res))
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="meetplan", description="Plan a long-distance year of trips, exactly.")
    ap.add_argument("-c", "--config", default=DEFAULT_CONFIG, help=f"input TOML (default: {DEFAULT_CONFIG})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("solve", help="find the optimal plan")
    p.add_argument("--mode", choices=MODES, help="override the config's scenario")
    p.add_argument("--fare-scale", type=float, help="multiply every fare, e.g. 1.1 for +10%%")
    p.add_argument("-o", "--out", help="also write the plan to .json, .csv or .ics")
    p.set_defaults(func=cmd_solve)

    p = sub.add_parser("compare", help="solve all three scenarios side by side")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("where", help="rank places to meet for a given month")
    p.add_argument("month", type=int, choices=range(1, 13), metavar="MONTH", help="1–12")
    p.add_argument("--nights", type=int, default=10)
    p.set_defaults(func=cmd_where)

    p = sub.add_parser("fares", help="who should fly each month (home trips)")
    p.set_defaults(func=cmd_fares)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, FileNotFoundError) as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
