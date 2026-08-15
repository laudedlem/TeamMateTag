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
OUTPUT_DIR = ROOT / "raw" / "espn_nfl_athlete_pages"
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
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--page", type=int, help="Index exactly one catalog page.")
    parser.add_argument("--limit", type=int, default=0, help="Index only this many athletes for a smoke test.")
    parser.add_argument("--force", action="store_true", help="Rebuild an existing page checkpoint.")
    args = parser.parse_args()
    first = get_json(f"{CATALOG}?limit=1000&page=1&lang=en&region=us")
    if not first:
        raise RuntimeError("Could not load ESPN NFL athlete catalog.")
    page = args.page or 1
    if page < 1 or page > int(first["pageCount"]):
        raise ValueError(f"Page must be 1 through {first['pageCount']}.")
    data = first if page == 1 else get_json(f"{CATALOG}?limit=1000&page={page}&lang=en&region=us")
    if not data:
        raise RuntimeError(f"Could not load ESPN athlete catalog page {page}.")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUTPUT_DIR / f"page_{page:02d}.csv"
    if output.exists() and not args.force:
        print(f"Page {page} already indexed at {output}; use --force to rebuild.")
        return
    refs = list(data["items"])
    if args.limit:
        refs = refs[:args.limit]
    print(f"Indexing ESPN NFL athlete page {page}/{first['pageCount']} ({len(refs):,} athletes).", flush=True)
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
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["espn_id", "display_name", "birth_date", "debut_year", "active", "college_athlete_ref"])
        writer.writerows(sorted(rows, key=lambda row: int(row[0]) if str(row[0]).isdigit() else 0))
    print(f"Wrote {len(rows):,} ESPN athlete identities to {output}")


if __name__ == "__main__":
    main()
