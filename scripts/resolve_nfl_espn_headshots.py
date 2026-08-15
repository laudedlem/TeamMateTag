"""Replace audited NFL placeholders with a validated ESPN fallback when possible.

nflverse supplies both a preferred NFL image URL and an ESPN identity. The NFL
URL frequently returns league artwork for historical players, while ESPN often
has a real portrait. This script checks the image bytes before promoting the
fallback; a successful HTTP response alone is never enough.
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

sys.path.insert(0, "web")
import server  # noqa: E402
from audit_runtime_headshots import KNOWN_PLACEHOLDER_URLS, fetch, hamming

PLAYERS_URL = "https://github.com/nflverse/nflverse-data/releases/download/players/players.csv"


def espn_urls() -> dict[str, str]:
    response = requests.get(PLAYERS_URL, timeout=90)
    response.raise_for_status()
    urls = {}
    for row in csv.DictReader(io.StringIO(response.content.decode("utf-8"))):
        gsis_id = (row.get("gsis_id") or "").strip()
        espn_id = (row.get("espn_id") or "").strip()
        if gsis_id and espn_id.isdigit():
            urls[f"nfl:{gsis_id}"] = f"https://a.espncdn.com/i/headshots/nfl/players/full/{espn_id}.png"
    return urls


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=500, help="Process only this many flagged players.")
    parser.add_argument("--workers", type=int, default=24)
    args = parser.parse_args()
    server.ensure_runtime_schema()
    urls = espn_urls()
    with server.db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS player_headshot_source_attempts (
            sport_id TEXT NOT NULL, player_id TEXT NOT NULL, provider TEXT NOT NULL,
            status TEXT NOT NULL, source_url TEXT, checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (sport_id, player_id, provider))""")
        rows = conn.execute(
            """SELECT h.player_id, h.content_sha256, COALESCE(SUM(a.games_total), 0) AS career_games
                 FROM player_headshots h
                 LEFT JOIN sport_appearances a ON a.sport_id=h.sport_id AND a.player_id=h.player_id
                WHERE h.sport_id='football' AND h.status IN ('placeholder', 'missing', 'duplicate')
                GROUP BY h.player_id, h.content_sha256
                ORDER BY career_games DESC, h.player_id"""
        ).fetchall()
        attempted = {player_id for (player_id,) in conn.execute(
            """SELECT player_id FROM player_headshot_source_attempts
                 WHERE sport_id='football' AND provider='ESPN'"""
        ).fetchall()}
        known_hashes = {digest for (digest,) in conn.execute(
            "SELECT DISTINCT content_sha256 FROM player_headshots WHERE sport_id='football' AND status='placeholder' AND content_sha256 IS NOT NULL"
        ).fetchall()}
    jobs = [(player_id, urls[player_id]) for player_id, _, _ in rows
            if player_id in urls and player_id not in attempted]
    if args.limit:
        jobs = jobs[:args.limit]
    print(f"Checking ESPN alternatives for {len(jobs):,} audited NFL gaps.", flush=True)

    # Include the known ESPN generic in addition to hashes already discovered.
    known_perceptual = set()
    for url in KNOWN_PLACEHOLDER_URLS["football"] + [
        "https://a.espncdn.com/i/headshots/nfl/players/full/9643.png"
    ]:
        result = fetch(url)
        if result["status"] == "ok":
            known_hashes.add(result["sha256"])
            known_perceptual.add(result["perceptual_hash"])

    results = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(fetch, url): (player_id, url) for player_id, url in jobs}
        for index, future in enumerate(as_completed(futures), 1):
            player_id, url = futures[future]
            results[player_id] = (url, future.result())
            if index % 500 == 0 or index == len(jobs):
                print(f"  checked {index:,}/{len(jobs):,}", flush=True)

    duplicate_hashes = {digest for digest, count in Counter(
        result.get("sha256") for _, result in results.values() if result.get("status") == "ok"
    ).items() if count > 1}
    promoted = []
    attempts = []
    reasons = Counter()
    for player_id, (url, result) in results.items():
        if result["status"] != "ok":
            reasons["unavailable"] += 1
            attempts.append((player_id, "unavailable", url))
            continue
        digest = result["sha256"]
        if digest in known_hashes or any(hamming(result["perceptual_hash"], known) <= 4 for known in known_perceptual):
            reasons["placeholder"] += 1
            attempts.append((player_id, "placeholder", url))
        elif digest in duplicate_hashes:
            reasons["shared_image"] += 1
            attempts.append((player_id, "shared_image", url))
        else:
            promoted.append((url, digest, result["perceptual_hash"], result["width"], result["height"], player_id))
            attempts.append((player_id, "candidate", url))

    with server.db() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO player_headshot_source_attempts (sport_id, player_id, provider, status, source_url)
                   VALUES ('football', %s, 'ESPN', %s, %s)
                   ON CONFLICT (sport_id, player_id, provider) DO UPDATE SET status=EXCLUDED.status,
                     source_url=EXCLUDED.source_url, checked_at=now()""",
                attempts,
            )
            cur.executemany(
                """UPDATE player_headshots SET source_url=%s, fallback_url=NULL, provider='ESPN', status='verified',
                       content_sha256=%s, perceptual_hash=%s, width=%s, height=%s,
                       review_note='Validated ESPN player-specific fallback.'
                   WHERE sport_id='football' AND player_id=%s""",
                promoted,
            )
            cur.executemany(
                """INSERT INTO sport_player_images (sport_id, player_id, source_url) VALUES ('football', %s, %s)
                   ON CONFLICT (sport_id, player_id) DO UPDATE SET source_url=EXCLUDED.source_url""",
                [(player_id, url) for url, _, _, _, _, player_id in promoted],
            )
    print(f"Promoted {len(promoted):,} real ESPN portraits. Skipped: {dict(reasons)}")


if __name__ == "__main__":
    main()
