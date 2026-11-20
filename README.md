# Todo contigo · meetplan

Made for Laura (Mexico City) and Andrés (Paris). An exact optimiser for a long-distance couple's year of trips: **when** to meet, **where** (Paris, Mexico City or a
city in between) and **for how long**. It stays within both people's leave and blocked dates, and trades days together
against fares, hotels and long gaps apart.

- **Web app** (no install, runs in the browser): https://aladinoster.github.io/paris-cdmx-planne/
- **Python package** `meetplan`: the same model as a CLI and library, managed with [uv](https://docs.astral.sh/uv/).

Both implementations give identical results; `tests/` checks this.

---

## Repository layout

```
.
├── config/
│   └── 2027.toml          # ← all inputs: leave, holidays, blocked dates, fixed trips, fares, cities
├── docs/
│   └── index.html         # GitHub Pages site (self-contained, JS solver inside)
├── src/meetplan/
│   ├── config.py          # load + validate the TOML input
│   ├── calendar.py        # working days, holidays, blocked days, daily fare curves
│   ├── fares.py           # home fares and third-city fares
│   ├── model.py           # candidate trips: value, cost, leave per trip
│   ├── solver.py          # exact dynamic programme
│   ├── report.py          # text report, JSON / CSV / iCalendar export, "where to meet" ranking
│   └── cli.py             # `meetplan` command
├── tests/                 # pytest: web-parity, constraints, brute-force optimality, exports, CLI
└── pyproject.toml
```

## Quick start

```bash
# 1. install uv once: https://docs.astral.sh/uv/getting-started/installation/
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. clone and install dependencies (creates .venv automatically)
git clone https://github.com/aladinoster/paris-cdmx-planne.git
cd paris-cdmx-planne
uv sync

# 3. solve
uv run meetplan solve
```

Output:

```
Plan for 2027 (mode: both)
  Days together : 70 (19% of the year)
  Trips         : 5
  Travel cost   : €7,426  (Andrés's fares €2,569, Laura's €2,558, lodging €2,300)
  Leave used    : Andrés 45/45, Laura 22/22
  Longest gap   : 75 days
Dates                    Where                    Days     Fares  Lodging     Leave A/L  Host / fixed
2027-02-04 → 2027-02-15  Andrés → Mexico City       12      €900        –      8/0       Laura keeps working · February, Andrés goes
2027-03-18 → 2027-03-28  Both → New York            11    €1,183   €2,300      7/5       both on leave · March, New York together
2027-06-12 → 2027-06-27  Laura → Paris              16    €1,033        –     10/10      Andrés takes leave · June, Laura comes (Marseille)
2027-09-09 → 2027-09-20  Laura → Paris              12    €1,000        –      8/7       Andrés takes leave · suggested
2027-10-22 → 2027-11-09  Andrés → Mexico City       19    €1,010        –     12/0       Laura keeps working · Late Oct to early Nov, Andrés goes
```

## Commands

| Command | What it does |
|---|---|
| `uv run meetplan solve` | Optimal plan for the config's scenario |
| `uv run meetplan solve --mode home` | Override the scenario: `home`, `both` or `meet` |
| `uv run meetplan solve --fare-scale 1.1` | Re-solve with every fare 10% higher |
| `uv run meetplan solve -o plan.ics` | Also export the plan: `.ics` (calendar), `.csv` or `.json` |
| `uv run meetplan compare` | Solve the three scenarios side by side |
| `uv run meetplan where 9` | Rank every place to meet for a 10-night trip from 15 September |
| `uv run meetplan fares` | Who should fly each month (home trips) |
| `uv run meetplan -c my.toml solve` | Use another input file |

Scenarios: **home** = free trips only to Paris or Mexico City; **both** = home or a third city;
**meet** = third cities only. Fixed trips always keep their own `where`.

Import `plan.ics` into Google Calendar, Apple Calendar or Outlook to get one all-day event per trip.

### As a library

```python
from meetplan import load_config, solve

cfg = load_config("config/2027.toml").with_(mode="home", leave_her=25)
plan = solve(cfg).plan
print(plan.days_together, plan.travel_cost)
for t in plan.trips:
    print(t.s, t.e, t.where, t.leave_me, t.leave_her)
```

## Input data (`config/2027.toml`)

Everything the model knows lives in one commented TOML file. Copy it (e.g. `config/2028.toml`), edit it and pass it
with `-c`.

| Section | Meaning |
|---|---|
| `year`, `mode` | Planning year; default scenario (`home` / `both` / `meet`) |
| `[names]` | Display names: `me` (Paris side), `her` (Mexico side) |
| `[leave]` | Annual leave days for each |
| `[trips]` | `min_days` / `max_days` for free trips, `min_gap` between trips, `max_gap` search window |
| `[value]` | `day`: € value of a day together; `host_working_factor`: value when the host works; `travel_day_factor`; `trip_overhead` €; `gap_target` and `gap_lambda` for the gap penalty |
| `[her_constraints]` | `month_end_days` she can't take off, `block_august`, `extra_blackout` dates |
| `[holidays]` | Public holidays per side; these days cost no leave |
| `[fares]` | `scale`, weekend surcharges, the two daily fare `curve`s (Paris→CDMX, CDMX→Paris), the `quotes` they were calibrated on |
| `[[fixed_trips]]` | Trips that must happen. `start` and `end` are `[earliest, latest]`; `where` is `me`, `her`, `home`, `meet:<city>`, `meet:any` or `anywhere` |
| `[[cities]]` | Third-city options: reference fares from each side (`ref_me`, `ref_her`), `lodging` € per night for two, `bonus` € of extra value per holiday day, `enabled` |

### Updating fares

Fares are estimates. The two curves are piecewise-constant from each `MM-DD` and are calibrated on Kiwi.com quotes
from November 2026 (listed under `[[fares.quotes]]`). Third-city fares use quotes for 13–23 May 2027 (and 18–28 March
for New York, Miami and Cancún), moved through the year with each origin's curve. When you look up live prices:

- if everything is off by a similar ratio, set `fares.scale` (or use `--fare-scale`);
- if one season is off, edit that segment of `curve.me` / `curve.her`;
- if one city is off, edit its `ref_me` / `ref_her`.

## The model

A candidate trip is a start day `s`, an end day `e`, a place and, for home trips, whether the host also takes leave.

- **Value:** `V` € per day together; × `β` on days the host works; × `travel_day_factor` on the first and last day;
  plus the city's `bonus` per day for third-city trips.
- **Cost:** the fares (one ticket for home trips, two for a third city), hotel nights (third city only) and a fixed
  `trip_overhead`.
- **Leave:** the traveller spends leave on each of their working days. The host does too if they take leave.
  In a third city, both do. Her leave is never allowed on her blocked days.
- **Gaps:** a gap of `g` days apart (including before the first trip and after the last) costs
  `λ · max(0, g − gap_target)²`, so the plan spreads out.

The objective is total value minus costs minus gap penalties, under both leave budgets, the fixed trips and the minimum
gap. It is solved **exactly** by dynamic programming over days with state
`(day, next fixed trip, your leave left, her leave left)`; the two leave dimensions are numpy arrays, so a full year
solves in under a second. `tests/test_solver.py::test_dp_is_optimal_against_brute_force` checks optimality against
exhaustive enumeration on a small instance.

## Development

```bash
uv sync                    # installs runtime + dev dependencies (pytest, ruff)
uv run pytest              # 12 tests
uv run ruff check src tests
```

### Web app

`docs/index.html` is a single self-contained page: the same solver in JavaScript, plus the interface. GitHub Pages
serves it from the `gh-pages` branch, which holds a copy of `docs/`. After editing `docs/index.html`, copy it to
`gh-pages` and push (or switch Pages to `main` / `/docs` under Settings → Pages). It loads Google Fonts and nothing else. Settings you change on the page are
kept in your browser only.

## Licence

MIT
