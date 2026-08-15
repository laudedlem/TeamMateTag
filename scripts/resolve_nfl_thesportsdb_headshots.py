"""Use birth-date-matched TheSportsDB portraits for unresolved NFL players.

The free API is limited to 30 requests per minute. This script defaults to a
respectful interval and records every attempt, so it can resume safely without
retrying the same player or overloading the provider.
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
import time

import requests

sys.path.insert(0, "web")
import server  # noqa: E402
from audit_runtime_headshots import fetch

PLAYERS_URL = "https://github.com/nflverse/nflverse-data/releases/download/players/players.csv"
API = "https://www.thesportsdb.com/api/v1/json/123/searchplayers.php"
HEADERS = {"User-Agent": "TeamMateTag headshot resolver/0.2.10 (contact: teammatetag.com)"}


def norm(value: str) -> str:
    return "".join(char for char in value.lower() if char.isalnum()).removesuffix("jr").removesuffix("sr")


def birth_dates() -> dict[str, str]:
    response = requests.get(PLAYERS_URL, headers=HEADERS, timeout=90)
    response.raise_for_status()
    return {f"nfl:{row['gsis_id']}": row.get("birth_date", "")
            for row in csv.DictReader(io.StringIO(response.content.decode("utf-8"))) if row.get("gsis_id")}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--delay", type=float, default=2.1)
    parser.add_argument("--player-id", help="Resolve one explicit TeamMateTag player ID.")
    args = parser.parse_args()
    server.ensure_runtime_schema()
    births = birth_dates()
    with server.db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS player_headshot_source_attempts (
            sport_id TEXT NOT NULL, player_id TEXT NOT NULL, provider TEXT NOT NULL,
            status TEXT NOT NULL, source_url TEXT, checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (sport_id, player_id, provider))""")
        rows = conn.execute("""
            SELECT p.player_id, p.display_name FROM sport_players p
            JOIN player_headshots h ON h.sport_id=p.sport_id AND h.player_id=p.player_id
            LEFT JOIN player_headshot_source_attempts tried
              ON tried.sport_id=p.sport_id AND tried.player_id=p.player_id AND tried.provider='TheSportsDB'
            WHERE p.sport_id='football' AND h.status IN ('placeholder','missing') AND tried.player_id IS NULL
              AND (%s::text IS NULL OR p.player_id=%s)
            ORDER BY p.player_id LIMIT %s
        """, (args.player_id, args.player_id, args.limit)).fetchall()
    results = []
    for index, (player_id, name) in enumerate(rows, 1):
        response = requests.get(API, params={"p": name}, headers=HEADERS, timeout=30)
        if response.status_code == 429:
            raise RuntimeError("TheSportsDB rate-limited this run; wait before resuming.")
        players = response.json().get("player") or []
        birth = births.get(player_id, "")
        match = next((item for item in players
                      if item.get("strSport") == "American Football"
                      and norm(item.get("strPlayer") or "") == norm(name)
                      and item.get("dateBorn") == birth and item.get("strThumb")), None)
        if not match:
            results.append((player_id, "no_match", None, None))
        else:
            url = match["strThumb"]
            image = fetch(url)
            if image["status"] != "ok":
                results.append((player_id, "unavailable", None, None))
            else:
                results.append((player_id, "candidate", url, image))
        if index < len(rows):
            time.sleep(args.delay)
        print(f"  checked {index}/{len(rows)}", flush=True)
    promoted = [row for row in results if row[1] == "candidate"]
    with server.db() as conn:
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO player_headshot_source_attempts (sport_id, player_id, provider, status, source_url)
                VALUES ('football', %s, 'TheSportsDB', %s, %s)
                ON CONFLICT (sport_id, player_id, provider) DO UPDATE SET status=EXCLUDED.status,
                  source_url=EXCLUDED.source_url, checked_at=now()
            """, [(player_id, status, url) for player_id, status, url, _ in results])
            cur.executemany("""
                UPDATE player_headshots SET source_url=%s, fallback_url=NULL, provider='TheSportsDB', status='verified',
                  content_sha256=%s, perceptual_hash=%s, width=%s, height=%s,
                  review_note='Birth-date-matched TheSportsDB portrait.'
                WHERE sport_id='football' AND player_id=%s
            """, [(url, image['sha256'], image['perceptual_hash'], image['width'], image['height'], player_id)
                   for player_id, _, url, image in promoted])
            cur.executemany("""
                INSERT INTO sport_player_images (sport_id, player_id, source_url) VALUES ('football', %s, %s)
                ON CONFLICT (sport_id, player_id) DO UPDATE SET source_url=EXCLUDED.source_url
            """, [(player_id, url) for player_id, _, url, _ in promoted])
    print(f"Promoted {len(promoted)} TheSportsDB portraits from {len(results)} attempts.")


if __name__ == "__main__":
    main()
