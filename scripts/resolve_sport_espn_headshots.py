"""Promote validated ESPN portraits for flagged MLB, NBA, and NHL players."""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "scripts"))
import server  # noqa: E402
from audit_runtime_headshots import KNOWN_PLACEHOLDER_URLS, fetch, hamming  # noqa: E402
from name_normalize import normalize  # noqa: E402

CONFIG = {"baseball": "mlb", "basketball": "nba", "hockey": "nhl"}


def identities(league: str) -> tuple[dict[tuple[str, int], list[str]], dict[tuple[str, int], list[str]]]:
    values: dict[tuple[str, int], list[str]] = {}
    by_debut: dict[tuple[str, int], list[str]] = {}
    for path in (ROOT / "raw" / f"espn_{league}_athlete_pages").glob("page_*.csv"):
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                birth = row.get("birth_date") or ""
                athlete_id = row.get("espn_id") or ""
                if athlete_id.isdigit() and len(birth) >= 4:
                    values.setdefault((normalize(row.get("display_name") or ""), int(birth[:4])), []).append(athlete_id)
                debut = row.get("debut_year")
                if athlete_id.isdigit() and str(debut or "").isdigit():
                    by_debut.setdefault((normalize(row.get("display_name") or ""), int(debut)), []).append(athlete_id)
    return ({key: ids for key, ids in values.items() if len(set(ids)) == 1},
            {key: ids for key, ids in by_debut.items() if len(set(ids)) == 1})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", required=True, choices=tuple(CONFIG))
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--workers", type=int, default=20)
    args = parser.parse_args()
    league = CONFIG[args.sport]
    index, debut_index = identities(league)
    if not index and not debut_index:
        raise RuntimeError(f"No completed ESPN {league.upper()} identity index found.")
    server.ensure_runtime_schema()
    with server.db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS player_headshot_source_attempts (
            sport_id TEXT NOT NULL, player_id TEXT NOT NULL, provider TEXT NOT NULL,
            status TEXT NOT NULL, source_url TEXT, checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (sport_id, player_id, provider))""")
        if args.sport == "baseball":
            rows = conn.execute("""SELECT p.player_id, concat_ws(' ',p.name_first,p.name_last), p.birth_year, p.debut_year
                FROM players p JOIN player_headshots h ON h.sport_id='baseball' AND h.player_id=p.player_id
                WHERE h.status IN ('placeholder','missing')""").fetchall()
        else:
            rows = conn.execute("""SELECT p.player_id,p.display_name,p.birth_year,p.debut_year FROM sport_players p
                JOIN player_headshots h ON h.sport_id=p.sport_id AND h.player_id=p.player_id
                WHERE p.sport_id=%s AND h.status IN ('placeholder','missing')""", (args.sport,)).fetchall()
        attempted = {player_id for player_id, in conn.execute("""SELECT player_id FROM player_headshot_source_attempts
            WHERE sport_id=%s AND provider='ESPN'""", (args.sport,)).fetchall()}
        known_hashes = {digest for digest, in conn.execute("""SELECT DISTINCT content_sha256 FROM player_headshots
            WHERE sport_id=%s AND status='placeholder' AND content_sha256 IS NOT NULL""", (args.sport,)).fetchall()}
    jobs = []
    for player_id, name, birth_year, debut_year in rows:
        ids = index.get((normalize(name), birth_year)) if birth_year else None
        if not ids and debut_year:
            ids = debut_index.get((normalize(name), debut_year))
        if ids and player_id not in attempted:
            url = f"https://a.espncdn.com/i/headshots/{league}/players/full/{ids[0]}.png"
            jobs.append((player_id, url))
    jobs = jobs[:args.limit] if args.limit else jobs
    print(f"Checking {len(jobs):,} ESPN {league.upper()} portraits.", flush=True)
    known_perceptual = set()
    for url in KNOWN_PLACEHOLDER_URLS[args.sport]:
        image = fetch(url)
        if image["status"] == "ok":
            known_hashes.add(image["sha256"]); known_perceptual.add(image["perceptual_hash"])
    results = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(fetch, url): (player_id, url) for player_id, url in jobs}
        for number, future in enumerate(as_completed(futures), 1):
            player_id, url = futures[future]; results[player_id] = (url, future.result())
            if number % 250 == 0 or number == len(jobs): print(f"  checked {number}/{len(jobs)}", flush=True)
    duplicate = {digest for digest, count in Counter(result.get("sha256") for _, result in results.values()
                 if result.get("status") == "ok").items() if count > 1}
    attempts=[]; promoted=[]
    for player_id, (url, image) in results.items():
        if image["status"] != "ok": attempts.append((player_id,"unavailable",url)); continue
        if image["sha256"] in known_hashes or any(hamming(image["perceptual_hash"], value) <= 4 for value in known_perceptual):
            attempts.append((player_id,"placeholder",url)); continue
        if image["sha256"] in duplicate:
            attempts.append((player_id,"shared_image",url)); continue
        attempts.append((player_id,"candidate",url)); promoted.append((url,image,player_id))
    with server.db() as conn, conn.cursor() as cur:
        cur.executemany("""INSERT INTO player_headshot_source_attempts (sport_id,player_id,provider,status,source_url)
            VALUES (%s,%s,'ESPN',%s,%s) ON CONFLICT (sport_id,player_id,provider) DO UPDATE SET
            status=EXCLUDED.status,source_url=EXCLUDED.source_url,checked_at=now()""",
            [(args.sport,*attempt) for attempt in attempts])
        cur.executemany("""UPDATE player_headshots SET source_url=%s,fallback_url=NULL,provider='ESPN',status='verified',
            content_sha256=%s,perceptual_hash=%s,width=%s,height=%s,review_note='ESPN identity-matched portrait.'
            WHERE sport_id=%s AND player_id=%s""",
            [(url,image['sha256'],image['perceptual_hash'],image['width'],image['height'],args.sport,player_id)
             for url,image,player_id in promoted])
        if args.sport != 'baseball':
            cur.executemany("""INSERT INTO sport_player_images (sport_id,player_id,source_url) VALUES (%s,%s,%s)
                ON CONFLICT (sport_id,player_id) DO UPDATE SET source_url=EXCLUDED.source_url""",
                [(args.sport,player_id,url) for url,_,player_id in promoted])
    print(f"Promoted {len(promoted)} ESPN portraits; recorded {len(attempts)} attempts.")


if __name__ == '__main__':
    main()
