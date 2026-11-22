# Monthly fare refresh (runbook)

This is what the monthly scheduled task does. It can also be done by hand. The searches are listed in
`config/searches.toml`.

## 1. Set up

```bash
git clone https://github.com/aladinoster/paris-cdmx-planne && cd paris-cdmx-planne
uv sync
uv run meetplan solve        # current plan: note the trip dates, they guide the cross-checks
```

## 2. Search fares and record every result

For each search in `config/searches.toml` whose departures are ≥ 7 days from today and still in the planning
year:

- **Home months (Kiwi.com):** `flyFrom`/`flyTo` as listed, `departureDate` = 1st of the month,
  `departureDateTo` = last day of the month, `nights_in_dst_from/to` = 9/11, `one_for_city = true`.
  Record the cheapest result as `--kind best_in_month`.
- **Cross-checks (lastminute.com):** in `crosscheck_months`, fixed dates taken from the current plan's trip in
  that month (or the 10th → 21st if there is none). Use `--kind fixed_dates`.
- **Third cities (Kiwi.com):** for each code and origin, a departure range over the month with 7–12 nights.
  Use `--kind best_in_window` and direction `PAR-<code>` or `MEX-<code>`.

Record each fare (the command validates it and skips exact duplicates):

```bash
uv run meetplan add-quote --source kiwi --direction PAR-MEX --depart 2027-05-12 --return 2027-05-22 \
    --kind best_in_month --price 838 --carriers "British Airways via London"
```

Use the itinerary's real departure and return dates and the total price in EUR. If a search returns nothing or
errors, skip it. Never invent a price. Add `--notes thin` to results that look wrong, such as one odd itinerary
far below everything else; the rebuild ignores them.

## 3. Rebuild, check, publish

```bash
uv run meetplan refresh      # rebuilds data/fare_model.json and prints what changed
uv run pytest -q             # must pass (includes JS ↔ Python parity, needs node)
uv run meetplan build-web    # writes docs/index.html
uv run meetplan solve        # compare with step 1
git add data docs && git commit -m "Fares: monthly refresh <YYYY-MM-DD>" && git push origin main
```

Pushing to `main` triggers `.github/workflows/pages.yml`, which tests, builds and publishes the page to the
`gh-pages` branch, so the public link updates within a couple of minutes.

## 4. Report

Summarise in a few lines:
- how many quotes were added;
- the curve changes printed by `refresh`;
- whether the plan changed: days together, trips, the April trip and the cost range;
- any fare more than 15% below the curve for a month with a planned trip, which is worth booking.

If the tests fail, don't push. Report the failure instead.
