# ✈️ Flight ticket tracker

Tracks flight prices for specific routes/dates, records a price history over
time, and tells you whether today's fare is a **good deal** — combining
Google's own price insights (via SerpApi's Google Flights engine) with the
history this tool collects.

Configured out of the box for:

> **SEA → SMF** and **SEA → SFO**, one-way, **Aug 17 2026**, 2 travelers,
> afternoon departures only (12:00–17:59).

Every morning a GitHub Action checks both routes, commits the updated price
history, and posts a report as a comment on a tracking issue.

## How the "good deal" call works

Each route gets a 🟢 / 🟡 / 🔴 verdict from two signals:

1. **Google's price insight** — `low` / `typical` / `high` and the typical
   price range Google shows for those dates. This is the strongest signal.
2. **Your own history** — once a few days of checks accumulate, the tool also
   reports where today sits vs. the min / median / max it has logged, and
   today's percentile (0% = cheapest ever seen).

Green = Google says `low` **or** today is in the cheapest 25% of what we've
tracked. Red = `high` or top 25%. Yellow = in between.

## One-time setup

1. **Get a SerpApi key** — sign up at <https://serpapi.com> (free tier is 100
   searches/month; 2 routes/day ≈ 60/month, so it fits).
2. **Add it as a repo secret** — repo **Settings → Secrets and variables →
   Actions → New repository secret**, name it `SERPAPI_KEY`.
3. That's it. The workflow runs daily at **7:00 AM Pacific** (`0 14 * * *`
   UTC). You can also trigger it manually from the **Actions** tab
   (**Daily flight price check → Run workflow**) to test it immediately.

The first run creates a tracking issue titled
`✈️ Flight price tracker — SEA → SMF / SFO` and posts the report there;
every later run adds a comment.

## Change routes, dates, or travelers

Edit [`config.json`](config.json):

```json
{
  "origin": "SEA",
  "destinations": ["SMF", "SFO"],
  "outbound_date": "2026-08-17",
  "trip_type": "one_way",
  "departure_window": [12, 18],
  "currency": "USD",
  "travelers": 2
}
```

Use IATA airport codes. Set `"trip_type": "round_trip"` if you add a return
date (you'd extend `tracker.py` to pass `return_date` for that).

`departure_window` is `[start_hour, end_hour)` — only flights departing in
that range count, both in the SerpApi query and when picking the top tickets.
`[12, 18]` means 12:00–17:59. Remove the key to consider all departure times.
History stats only compare checks made with the same window, so changing it
restarts the min/median/max tracking.

Each route's report shows the **top 3 tickets by price**, with departure and
arrival times.

## Run it locally

```bash
pip install -r requirements.txt
export SERPAPI_KEY=your_key_here
python tracker.py
```

Writes/updates `price_history.json` and `report.md`, and prints the report.

## Files

| File | Purpose |
|------|---------|
| `tracker.py` | Fetches prices, analyzes deals, writes the report |
| `config.json` | Routes, date, travelers, currency |
| `price_history.json` | Append-only log of every check (created on first run) |
| `report.md` | Latest report (regenerated each run) |
| `.github/workflows/daily-flight-check.yml` | Daily schedule + issue posting |
