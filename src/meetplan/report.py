"""Human-readable tables and machine-readable exports (JSON, CSV, iCalendar)."""

from __future__ import annotations

import csv
import io
import json
from datetime import date, timedelta

from .calendar import YearCalendar
from .config import Config
from .fares import city_fare, home_fare
from .model import Trip
from .solver import Result


def where_label(cfg: Config, t: Trip) -> str:
    if t.city:
        return f"Both → {cfg.city_map[t.city].name}"
    return f"{cfg.name_me} → Mexico City" if t.where == "me" else f"{cfg.name_her} → Paris"


def _fmt_eur(v: float) -> str:
    return f"€{v:,.0f}"


def trip_rows(res: Result) -> list[dict]:
    cfg, cal, plan = res.cfg, res.cal, res.plan
    rows = []
    for t in plan.trips if plan else []:
        rows.append({
            "start": cal.dates[t.s].isoformat(),
            "end": cal.dates[t.e].isoformat(),
            "days": t.days,
            "where": where_label(cfg, t),
            "who_flies": "both" if t.city else ("me" if t.where == "me" else "her"),
            "city": cfg.city_map[t.city].name if t.city else ("Mexico City" if t.where == "me" else "Paris"),
            "host": "both on leave" if t.city else
                    f"{cfg.name_her if t.where == 'me' else cfg.name_me} {'takes leave' if t.host_leave else 'keeps working'}",
            "fare_me": round(t.fare_me, 2),
            "fare_her": round(t.fare_her, 2),
            "lodging": round(t.lodging, 2),
            "leave_me": t.leave_me,
            "leave_her": t.leave_her,
            "fixed": res.fixed_labels[t.fixed] if t.fixed >= 0 else "",
        })
    return rows


def text_report(res: Result) -> str:
    cfg, plan = res.cfg, res.plan
    if plan is None:
        return ("No feasible plan: the fixed trips need more leave than you have, or one lands on her "
                "blocked days. Loosen a date range, the blackout, or the leave budgets.")
    out = io.StringIO()
    w = out.write
    w(f"Plan for {cfg.year} (mode: {cfg.mode})\n")
    w(f"  Days together : {plan.days_together} ({plan.days_together / res.cal.n:.0%} of the year)\n")
    w(f"  Trips         : {len(plan.trips)}\n")
    w(f"  Travel cost   : {_fmt_eur(plan.travel_cost)}  ({cfg.name_me}'s fares {_fmt_eur(plan.fares_me)}, "
      f"{cfg.name_her}'s {_fmt_eur(plan.fares_her)}, lodging {_fmt_eur(plan.lodging)})\n")
    w(f"  Leave used    : {cfg.name_me} {plan.leave_me}/{cfg.leave_me}, {cfg.name_her} {plan.leave_her}/{cfg.leave_her}\n")
    w(f"  Longest gap   : {plan.longest_gap} days\n")
    w(f"  Score         : {plan.objective:,.0f}\n\n")
    hdr = f"{'Dates':<25}{'Where':<24}{'Days':>5}{'Fares':>10}{'Lodging':>9}{'Leave A/L':>14}  Host / fixed\n"
    w(hdr)
    w("-" * (len(hdr) + 18) + "\n")
    for row in trip_rows(res):
        dates = f"{row['start']} → {row['end']}"
        fares = row["fare_me"] + row["fare_her"]
        lodg = _fmt_eur(row["lodging"]) if row["lodging"] else "–"
        tag = row["host"] + (f" · {row['fixed']}" if row["fixed"] else " · suggested")
        w(f"{dates:<25}{row['where']:<24}{row['days']:>5}{_fmt_eur(fares):>10}{lodg:>9}"
          f"{row['leave_me']:>7}/{row['leave_her']:<6}  {tag}\n")
    return out.getvalue()


def to_json(res: Result) -> str:
    p = res.plan
    summary = None if p is None else {
        "days_together": p.days_together, "trips": len(p.trips), "leave_me": p.leave_me, "leave_her": p.leave_her,
        "fares_me": round(p.fares_me, 2), "fares_her": round(p.fares_her, 2), "lodging": round(p.lodging, 2),
        "longest_gap": p.longest_gap, "objective": round(p.objective, 2),
    }
    return json.dumps({"year": res.cfg.year, "mode": res.cfg.mode, "summary": summary, "trips": trip_rows(res)},
                      ensure_ascii=False, indent=2)


def to_csv(res: Result) -> str:
    rows = trip_rows(res)
    out = io.StringIO()
    if rows:
        wr = csv.DictWriter(out, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)
    return out.getvalue()


def to_ics(res: Result) -> str:
    """One all-day event per trip, importable into Google Calendar / Apple Calendar / Outlook."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//meetplan//EN", "CALSCALE:GREGORIAN"]
    for i, row in enumerate(trip_rows(res)):
        start = row["start"].replace("-", "")
        end = (date.fromisoformat(row["end"]) + timedelta(days=1)).strftime("%Y%m%d")  # DTEND is exclusive
        lines += [
            "BEGIN:VEVENT",
            f"UID:meetplan-{res.cfg.year}-{i}@local",
            f"DTSTART;VALUE=DATE:{start}",
            f"DTEND;VALUE=DATE:{end}",
            f"SUMMARY:{row['where']} ({row['days']} days)",
            (f"DESCRIPTION:Fares €{row['fare_me'] + row['fare_her']:.0f}, lodging €{row['lodging']:.0f}, "
             f"leave {res.cfg.name_me} {row['leave_me']} / {res.cfg.name_her} {row['leave_her']}. {row['fixed'] or 'Suggested by optimiser'}"),
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def where_to_meet(cal: YearCalendar, cfg: Config, month: int, nights: int = 10) -> list[dict]:
    """Cost of a trip starting on the 15th of `month`, for every place, cheapest first."""
    s = next(i for i, d in enumerate(cal.dates) if d.month == month and d.day == 15)
    e = min(cal.n - 1, s + nights)
    rows = [
        {"place": f"Paris ({cfg.name_her} flies)", "kind": "home", "fare_me": 0.0, "fare_her": home_fare(cal, cfg, s, e, "her"), "lodging": 0.0},
        {"place": f"Mexico City ({cfg.name_me} flies)", "kind": "home", "fare_me": home_fare(cal, cfg, s, e, "me"), "fare_her": 0.0, "lodging": 0.0},
    ]
    for c in cfg.cities:
        rows.append({"place": c.name + ("" if c.enabled else " (off)"), "kind": "beach" if c.beach else "city",
                     "fare_me": city_fare(cal, cfg, s, e, c, "me"), "fare_her": city_fare(cal, cfg, s, e, c, "her"),
                     "lodging": c.lodging * (e - s)})
    for r in rows:
        r["total"] = r["fare_me"] + r["fare_her"] + r["lodging"]
    return sorted(rows, key=lambda r: r["total"])
