#!/usr/bin/env python3
"""Flight price tracker.

Queries SerpApi's Google Flights engine for one or more routes/dates, records
every check in a price-history file, and decides whether today's price is a
good deal — using both Google's own price insights and the price history this
tool has collected over time.

Usage:
    SERPAPI_KEY=xxxx python tracker.py

Config lives in config.json. History is stored in price_history.json and a
human-readable report is written to report.md (also printed to stdout).
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
HISTORY_PATH = ROOT / "price_history.json"
REPORT_PATH = ROOT / "report.md"

SERPAPI_ENDPOINT = "https://serpapi.com/search.json"


# --------------------------------------------------------------------------- #
# Config / history I/O
# --------------------------------------------------------------------------- #
def load_config() -> dict:
    with CONFIG_PATH.open() as f:
        return json.load(f)


def load_history() -> list[dict]:
    if HISTORY_PATH.exists():
        with HISTORY_PATH.open() as f:
            return json.load(f)
    return []


def save_history(history: list[dict]) -> None:
    with HISTORY_PATH.open("w") as f:
        json.dump(history, f, indent=2)
        f.write("\n")


# --------------------------------------------------------------------------- #
# SerpApi query
# --------------------------------------------------------------------------- #
def fetch_route(api_key: str, cfg: dict, destination: str) -> dict:
    """Return the SerpApi Google Flights response for one route."""
    params = {
        "engine": "google_flights",
        "departure_id": cfg["origin"],
        "arrival_id": destination,
        "outbound_date": cfg["outbound_date"],
        # Google Flights type: 1 = round trip, 2 = one way.
        "type": 2 if cfg.get("trip_type") == "one_way" else 1,
        "adults": cfg.get("travelers", 1),
        "currency": cfg.get("currency", "USD"),
        "hl": "en",
        "api_key": api_key,
    }
    url = f"{SERPAPI_ENDPOINT}?{urlencode(params)}"
    with urlopen(url, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if "error" in data:
        raise RuntimeError(f"SerpApi error for {destination}: {data['error']}")
    return data


def parse_offer(data: dict) -> dict:
    """Extract the cheapest offer and Google's price insights from a response."""
    flights = (data.get("best_flights") or []) + (data.get("other_flights") or [])
    prices = [f["price"] for f in flights if isinstance(f.get("price"), (int, float))]

    insights = data.get("price_insights") or {}
    lowest = min(prices) if prices else insights.get("lowest_price")

    cheapest_flight = None
    if prices:
        cheapest_flight = min(
            (f for f in flights if isinstance(f.get("price"), (int, float))),
            key=lambda f: f["price"],
        )

    return {
        "lowest_price": lowest,
        "price_level": insights.get("price_level"),  # low | typical | high
        "typical_price_range": insights.get("typical_price_range"),  # [low, high]
        "itinerary": summarize_itinerary(cheapest_flight),
    }


def summarize_itinerary(flight: dict | None) -> str | None:
    if not flight:
        return None
    segs = flight.get("flights") or []
    if not segs:
        return None
    airlines = sorted({s.get("airline") for s in segs if s.get("airline")})
    stops = len(segs) - 1
    stop_txt = "nonstop" if stops == 0 else f"{stops} stop{'s' if stops > 1 else ''}"
    dep = segs[0].get("departure_airport", {}).get("time", "")
    arr = segs[-1].get("arrival_airport", {}).get("time", "")
    dur = flight.get("total_duration")
    dur_txt = f", {dur // 60}h{dur % 60:02d}m" if dur else ""
    return f"{', '.join(airlines)} — {stop_txt}{dur_txt} ({dep} → {arr})"


# --------------------------------------------------------------------------- #
# Deal analysis
# --------------------------------------------------------------------------- #
def route_history(history: list[dict], destination: str) -> list[dict]:
    """Successful observations for a destination, oldest first."""
    return [
        e
        for e in history
        if e["destination"] == destination and e.get("lowest_price") is not None
    ]


def analyze(offer: dict, past: list[dict]) -> dict:
    """Combine Google's insight with our own history into a verdict.

    Returns a dict with the numeric context and a short verdict string.
    """
    price = offer["lowest_price"]
    prior_prices = [e["lowest_price"] for e in past]

    stats = {}
    if prior_prices:
        stats = {
            "n": len(prior_prices),
            "min": min(prior_prices),
            "median": round(statistics.median(prior_prices), 2),
            "max": max(prior_prices),
        }
        # How today ranks against everything we've seen (0% = cheapest ever).
        below = sum(1 for p in prior_prices if p > price)
        stats["percentile"] = round(100 * (1 - below / len(prior_prices)))

    # Score the deal. Google's price_level is the strongest single signal;
    # our own history refines it once we have enough samples.
    level = offer.get("price_level")
    signals: list[str] = []

    if level == "low":
        signals.append("Google rates this price **low** for these dates")
    elif level == "high":
        signals.append("Google rates this price **high** for these dates")
    elif level == "typical":
        signals.append("Google rates this price **typical** for these dates")

    trange = offer.get("typical_price_range")
    if trange and len(trange) == 2 and price is not None:
        lo, hi = trange
        if price <= lo:
            signals.append(f"at/below the bottom of Google's usual range (${lo}–${hi})")
        elif price >= hi:
            signals.append(f"at/above the top of Google's usual range (${lo}–${hi})")
        else:
            signals.append(f"inside Google's usual range (${lo}–${hi})")

    if stats:
        if price < stats["min"]:
            signals.append(f"a new low vs the {stats['n']} checks we've logged")
        elif price <= stats["median"]:
            signals.append(f"below our tracked median of ${stats['median']}")
        else:
            signals.append(f"above our tracked median of ${stats['median']}")

    # Verdict: prefer Google's level, fall back to our percentile.
    if level == "low" or (stats.get("percentile") is not None and stats["percentile"] <= 25):
        verdict = "🟢 GOOD DEAL — consider booking"
    elif level == "high" or (stats.get("percentile") is not None and stats["percentile"] >= 75):
        verdict = "🔴 PRICEY — likely worth waiting"
    else:
        verdict = "🟡 AVERAGE — no rush either way"

    return {"stats": stats, "signals": signals, "verdict": verdict}


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def build_report(cfg: dict, results: list[dict], now: datetime) -> str:
    cur = cfg.get("currency", "USD")
    trav = cfg.get("travelers", 1)
    trip = "one-way" if cfg.get("trip_type") == "one_way" else "round-trip"

    lines = [
        f"# ✈️ Flight price update — {now.strftime('%a %b %d, %Y')}",
        "",
        f"**{cfg['origin']} → {' / '.join(cfg['destinations'])}** · "
        f"{trip} · {cfg['outbound_date']} · {trav} traveler(s) · prices in {cur}",
        "",
        "_Price shown is the total returned by Google Flights for the passenger "
        "count above, per direction._",
        "",
    ]

    for r in results:
        dest = r["destination"]
        lines.append(f"## {cfg['origin']} → {dest}")
        if r.get("error"):
            lines.append(f"⚠️ Could not fetch this route: `{r['error']}`")
            lines.append("")
            continue

        price = r["lowest_price"]
        lines.append(f"**Cheapest: ${price} {cur}**  ·  {r['analysis']['verdict']}")
        if r.get("itinerary"):
            lines.append(f"> {r['itinerary']}")
        lines.append("")
        for s in r["analysis"]["signals"]:
            lines.append(f"- {s}")

        st = r["analysis"]["stats"]
        if st:
            lines.append(
                f"- History ({st['n']} checks): "
                f"low ${st['min']} · median ${st['median']} · high ${st['max']} · "
                f"today ≈ {st['percentile']}th percentile"
            )
        else:
            lines.append("- First check for this route — history starts building today.")
        lines.append("")

    lines.append("---")
    lines.append(
        f"_Checked {now.strftime('%Y-%m-%d %H:%M UTC')}. "
        "Deal calls blend Google's price insights with this tool's own history._"
    )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        print("ERROR: SERPAPI_KEY environment variable is not set.", file=sys.stderr)
        return 2

    cfg = load_config()
    history = load_history()
    now = datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")

    results = []
    for dest in cfg["destinations"]:
        past = route_history(history, dest)
        entry = {
            "checked_at": stamp,
            "origin": cfg["origin"],
            "destination": dest,
            "outbound_date": cfg["outbound_date"],
            "currency": cfg.get("currency", "USD"),
            "travelers": cfg.get("travelers", 1),
        }
        try:
            offer = parse_offer(fetch_route(api_key, cfg, dest))
        except Exception as exc:  # noqa: BLE001 - report and keep going
            print(f"WARN: {dest}: {exc}", file=sys.stderr)
            entry.update({"lowest_price": None, "error": str(exc)})
            results.append({"destination": dest, "error": str(exc)})
            history.append(entry)
            continue

        entry.update(
            {
                "lowest_price": offer["lowest_price"],
                "price_level": offer.get("price_level"),
                "typical_price_range": offer.get("typical_price_range"),
            }
        )
        history.append(entry)
        results.append(
            {
                "destination": dest,
                "lowest_price": offer["lowest_price"],
                "itinerary": offer.get("itinerary"),
                "analysis": analyze(offer, past),
            }
        )

    save_history(history)
    report = build_report(cfg, results, now)
    REPORT_PATH.write_text(report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
