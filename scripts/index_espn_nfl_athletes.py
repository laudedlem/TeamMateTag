"""Build a local identity index from ESPN's public historical NFL athlete catalog."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "raw" / "espn_nfl_athlete_index.csv"
CATALOG = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/athletes"
HEADERS = {"User-Agent": "TeamMateTag data reconciliation/0.2.10"}


def get_json(url: str) -> dict | None:
    try:
        response = requests.get(url.replace("http://", "https://"), headers=HEADERS, timeout=30)
        return response.json() if response.status_code == 200 else None
    except (requests.RequestException, ValueError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--limit", type=int, default=0, help="Index only this many athletes for a smoke test.")
    args = parser.parse_args()
    first = get_json(f"{CATALOG}?limit=1000&page=1&lang=en&region=us")
    if not first:
        raise RuntimeError("Could not load ESPN NFL athlete catalog.")
    refs = list(first["items"])
    for page in range(2, int(first["pageCount"]) + 1):
        data = get_json(f"{CATALOG}?limit=1000&page={page}&lang=en&region=us")
        if not data:
            raise RuntimeError(f"Could not load ESPN athlete catalog page {page}.")
        refs.extend(data["items"])
    if args.limit:
        refs = refs[:args.limit]
    print(f"Indexing {len(refs):,} ESPN NFL athletes.", flush=True)
    rows = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(get_json, ref["$ref"]) for ref in refs]
        for index, future in enumerate(as_completed(futures), 1):
            athlete = future.result()
            if athlete:
                rows.append((athlete.get("id"), athlete.get("displayName"), athlete.get("dateOfBirth"),
                             athlete.get("debutYear"), athlete.get("active"), athlete.get("collegeAthlete", {}).get("$ref")))
            if index % 500 == 0 or index == len(refs):
                print(f"  indexed {index:,}/{len(refs):,}", flush=True)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["espn_id", "display_name", "birth_date", "debut_year", "active", "college_athlete_ref"])
        writer.writerows(sorted(rows, key=lambda row: int(row[0]) if str(row[0]).isdigit() else 0))
    print(f"Wrote {len(rows):,} ESPN athlete identities to {OUTPUT}")


if __name__ == "__main__":
    main()
