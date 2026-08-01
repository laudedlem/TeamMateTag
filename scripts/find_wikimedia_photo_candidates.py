"""Find, but do not automatically publish, Commons photo candidates.

This turns native-image misses into explicit review rows: candidate image URL,
license, and source page, or a clear no-candidate result. A candidate is never
served by the game until its license and player match are reviewed.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import sqlite3
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "db" / "teammatetag_local.sqlite"
HEADERS = {"User-Agent": "TeamMateTag photo review/1.0"}


def candidate(row):
    sport, player_id, name = row
    try:
        summary = requests.get(
            "https://en.wikipedia.org/api/rest_v1/page/summary/" + quote(name.replace(" ", "_")),
            headers=HEADERS, timeout=20,
        ).json()
        image = (summary.get("thumbnail") or {}).get("source")
        if not image or "upload.wikimedia.org" not in image:
            return sport, player_id, None, None, None, "no_candidate"
        title = "File:" + image.rsplit("/", 1)[-1].split("px-")[-1].replace("_", " ")
        media = requests.get("https://commons.wikimedia.org/w/api.php", params={
            "action": "query", "format": "json", "prop": "imageinfo", "iiprop": "extmetadata", "titles": title,
        }, headers=HEADERS, timeout=20).json()
        page = next(iter(media.get("query", {}).get("pages", {}).values()), {})
        metadata = (page.get("imageinfo") or [{}])[0].get("extmetadata") or {}
        license_name = (metadata.get("LicenseShortName") or {}).get("value")
        return sport, player_id, image, summary.get("content_urls", {}).get("desktop", {}).get("page"), license_name, "candidate"
    except requests.RequestException:
        return sport, player_id, None, None, None, "request_failed"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    conn = sqlite3.connect(DATABASE)
    conn.execute("""CREATE TABLE IF NOT EXISTS local_player_image_candidates (
        sport_id TEXT NOT NULL, player_id TEXT NOT NULL, image_url TEXT, source_page TEXT,
        license_name TEXT, status TEXT NOT NULL, checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (sport_id, player_id))""")
    rows = conn.execute("""
        SELECT p.sport_id,p.player_id,p.display_name FROM sport_players p
        LEFT JOIN local_player_images i ON i.sport_id=p.sport_id AND i.player_id=p.player_id
        WHERE p.sport_id IN ('football','hockey') AND i.player_id IS NULL
    """).fetchall()
    if args.limit: rows = rows[:args.limit]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(candidate, rows))
    conn.executemany("INSERT OR REPLACE INTO local_player_image_candidates (sport_id,player_id,image_url,source_page,license_name,status) VALUES (?,?,?,?,?,?)", results)
    conn.commit(); conn.close()
    print(f"Reviewed {len(results):,} missing native images.")


if __name__ == "__main__":
    main()
