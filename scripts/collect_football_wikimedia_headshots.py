#!/usr/bin/env python3
"""Fill verified Football headshot gaps from Wikidata/Wikimedia.

This is intentionally narrow: exact player-name match, Wikidata description
must identify an American football player, and the record must have a Commons
image claim. The output is a small verified source CSV consumed by
sync_football_headshots.py.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
import urllib.parse
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parent.parent
MISSING_REPORT = ROOT / "raw" / "headshot_gap_reports" / "football_missing_50plus_after_canonical_sync.csv"
OUTPUT_CSV = ROOT / "raw" / "football_manual_headshots.csv"
USER_AGENT = "TeamMateTag football Wikimedia headshot gap fill/1.0"


FIELDS = [
    "player_id",
    "name",
    "debut",
    "final",
    "position",
    "career_games",
    "result_status",
    "source_url",
    "profile_url",
    "note",
    "width",
    "height",
]


def write_rows(rows_by_id: dict[str, dict[str, str]]) -> None:
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows_by_id[player_id] for player_id in sorted(rows_by_id))


def get_json(
    session: requests.Session,
    url: str,
    params: dict[str, object] | None = None,
    timeout: float = 8.0,
) -> dict | None:
    for attempt in range(4):
        try:
            response = session.get(url, params=params, timeout=timeout)
            if response.status_code in {429, 500, 502, 503, 504}:
                time.sleep(1.5 * (attempt + 1))
                continue
            response.raise_for_status()
            return response.json()
        except Exception:
            if attempt == 3:
                print(f"request failed after retries: {url}", flush=True)
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def commons_original_url(session: requests.Session, filename: str) -> str | None:
    payload = get_json(
        session,
        "https://commons.wikimedia.org/w/api.php",
        {
            "action": "query",
            "titles": f"File:{filename}",
            "prop": "imageinfo",
            "iiprop": "url",
            "format": "json",
        },
    )
    if not payload:
        return None
    for page in payload.get("query", {}).get("pages", {}).values():
        imageinfo = page.get("imageinfo") or []
        if imageinfo and imageinfo[0].get("url"):
            # Fixed-width Commons redirects avoid repeatedly pulling originals
            # and have been more reliable during local batch fetches.
            return (
                "https://commons.wikimedia.org/w/index.php?title=Special:Redirect/file/"
                f"{urllib.parse.quote(filename)}&width=500"
            )
    return None


def find_wikimedia_image(session: requests.Session, player_name: str) -> tuple[str, str, str] | None:
    search = get_json(
        session,
        "https://www.wikidata.org/w/api.php",
        {
            "action": "wbsearchentities",
            "search": player_name,
            "language": "en",
            "format": "json",
            "limit": 5,
        },
    )
    if not search:
        return None
    item_id = None
    for candidate in search.get("search", []):
        label = (candidate.get("label") or "").strip().lower()
        description = (candidate.get("description") or "").lower()
        if label == player_name.strip().lower() and "american football" in description:
            item_id = candidate.get("id")
            break
    if not item_id:
        return None
    entity = get_json(session, f"https://www.wikidata.org/wiki/Special:EntityData/{item_id}.json")
    if not entity:
        return None
    claims = entity.get("entities", {}).get(item_id, {}).get("claims", {})
    images = claims.get("P18") or []
    if not images:
        return None
    filename = images[0].get("mainsnak", {}).get("datavalue", {}).get("value")
    if not filename:
        return None
    url = commons_original_url(session, filename)
    if not url:
        return None
    return item_id, filename, url


def read_existing() -> dict[str, dict[str, str]]:
    if not OUTPUT_CSV.exists():
        return {}
    with OUTPUT_CSV.open(newline="", encoding="utf-8") as handle:
        return {row["player_id"]: row for row in csv.DictReader(handle)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=250)
    parser.add_argument("--sleep", type=float, default=0.25)
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    existing = read_existing()
    with MISSING_REPORT.open(newline="", encoding="utf-8") as handle:
        missing = list(csv.DictReader(handle))
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    added = 0
    checked = 0
    for row in missing:
        if checked >= args.limit:
            break
        player_id = row["player_id"]
        if player_id in existing:
            continue
        checked += 1
        result = find_wikimedia_image(session, row["player_name"])
        if result:
            item_id, filename, url = result
            existing[player_id] = {
                "player_id": player_id,
                "name": row["player_name"],
                "debut": row.get("debut_year") or "",
                "final": row.get("final_year") or "",
                "position": "",
                "career_games": row.get("career_games") or "",
                "result_status": "verified",
                "source_url": url,
                "profile_url": f"https://www.wikidata.org/wiki/{item_id}",
                "note": f"Wikidata exact-name American football player image {item_id}: {filename}",
                "width": "",
                "height": "",
            }
            added += 1
            print(f"added {added}: {row['player_name']} ({player_id})", flush=True)
            write_rows(existing)
        if checked % 25 == 0:
            print(f"checked={checked} added={added} existing={len(existing)}", flush=True)
        time.sleep(args.sleep)

    write_rows(existing)
    print(f"wrote {len(existing)} verified rows to {OUTPUT_CSV}", flush=True)
    print(f"checked={checked} added={added}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
