"""
Run this ONCE, manually, after filling in backfill_history_import.csv with
your past monthly totals from Duke Energy's website (My Account > Billing
& Payment History, which keeps up to 24 months).

This does NOT run on the automated schedule. It just seeds history.json
with monthly totals for months before this tool existed, so the "Year"
view on the dashboard has something to show. Those backfilled months
won't have temp/humidity data (that genuinely doesn't exist for the
past) -- only the $ figure.

Usage:
    python scripts/backfill_history.py
"""

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "scripts" / "backfill_history_import.csv"
HISTORY_PATH = ROOT / "dashboard" / "history.json"


def main():
    history = []
    if HISTORY_PATH.exists():
        history = json.loads(HISTORY_PATH.read_text())

    existing_months = {e["month"] for e in history if e.get("type") == "backfilled_month"}

    with open(CSV_PATH, newline="") as f:
        reader = csv.DictReader(f)
        added = 0
        for row in reader:
            month = row["month"].strip()
            if month in existing_months:
                continue
            history.append(
                {
                    "type": "backfilled_month",
                    "month": month,
                    "total_dollars": float(row["total_dollars"]),
                }
            )
            added += 1

    history.sort(key=lambda e: e.get("month") or e.get("date"))
    HISTORY_PATH.write_text(json.dumps(history, indent=2))
    print(f"Added {added} backfilled month(s). Total history entries: {len(history)}.")
    print("Commit and push dashboard/history.json to see it on the dashboard.")


if __name__ == "__main__":
    main()
