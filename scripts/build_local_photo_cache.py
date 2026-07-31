"""Cache locally usable player headshots with source tracking.

Native league images are attempted first. Wikimedia is deliberately optional:
it is a fallback, not proof of image licensing for commercial production.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import sqlite3
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "db" / "teammatetag_local.sqlite"
CACHE = ROOT / "raw" / "player_headshots"


def source_url(sport: str, external_id: str) -> str | None:
    if not external_id:
        return None
    if sport == "basketball": return f"https://cdn.nba.com/headshots/nba/latest/1040x760/{external_id}.png"
    if sport == "hockey": return f"https://assets.nhle.com/mugs/nhl/latest/{external_id}.png"
    if sport == "football": return external_id if external_id.startswith("http") else f"https://a.espncdn.com/i/headshots/nfl/players/full/{external_id}.png"
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="0 means all players")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE)
    conn.execute("""CREATE TABLE IF NOT EXISTS local_player_images (
        sport_id TEXT NOT NULL, player_id TEXT NOT NULL, source_url TEXT NOT NULL,
        local_path TEXT NOT NULL, content_type TEXT, PRIMARY KEY (sport_id, player_id))""")
    rows = conn.execute("SELECT sport_id, player_id, external_id FROM sport_players WHERE sport_id IN ('basketball','football','hockey')").fetchall()
    conn.close()
    jobs = [(sport, pid, source_url(sport, str(external or ""))) for sport, pid, external in rows]
    jobs = [job for job in jobs if job[2]]
    if args.limit: jobs = jobs[:args.limit]

    def fetch(job):
        sport, pid, url = job
        suffix = ".png"
        path = CACHE / sport / (pid.replace(":", "_") + suffix); path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 256: return sport, pid, url, str(path), "cached"
        try:
            response = requests.get(url, timeout=20)
            if response.status_code == 200 and response.headers.get("content-type", "").startswith("image/") and len(response.content) > 256:
                path.write_bytes(response.content)
                return sport, pid, url, str(path), response.headers.get("content-type")
        except requests.RequestException:
            pass
        return None

    saved = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for result in pool.map(fetch, jobs):
            if result: saved.append(result)
    conn = sqlite3.connect(DATABASE)
    conn.executemany("INSERT OR REPLACE INTO local_player_images VALUES (?, ?, ?, ?, ?)", saved)
    conn.commit(); conn.close()
    print(f"Cached {len(saved):,} of {len(jobs):,} native headshots in {CACHE}")


if __name__ == "__main__":
    main()
