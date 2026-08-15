"""Validate and apply user-supplied direct image URLs from a review CSV.

Run after filling ``raw/headshot_submissions.csv``. Only rows with a URL are
processed. A URL that fails to decode or matches a known placeholder is kept
out of the live player-card registry.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web")); sys.path.insert(0, str(ROOT / "scripts"))
import server  # noqa: E402
from audit_runtime_headshots import KNOWN_PLACEHOLDER_URLS, fetch, hamming  # noqa: E402


def placeholder_hashes(sport: str) -> tuple[set[str], set[str]]:
    hashes, perceptual_hashes = set(), set()
    for url in KNOWN_PLACEHOLDER_URLS.get(sport, []):
        result = fetch(url)
        if result.get("status") == "ok":
            hashes.add(result["sha256"])
            perceptual_hashes.add(result["perceptual_hash"])
    return hashes, perceptual_hashes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=ROOT / "raw" / "headshot_submissions.csv")
    args = parser.parse_args()
    if not args.input.exists():
        raise RuntimeError(f"Missing submission sheet: {args.input}")
    with args.input.open(encoding="utf-8", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if (row.get("replacement_url") or "").strip()]
    verified = []
    cached_placeholders = {}
    for row in rows:
        sport = (row.get("sport") or "").strip()
        player_id = (row.get("player_id") or "").strip()
        url = (row.get("replacement_url") or "").strip()
        if sport not in {"baseball", "basketball", "football", "hockey"} or not player_id or not url.startswith(("https://", "http://")):
            print(f"Skipped invalid row: {sport} {player_id}")
            continue
        if sport not in cached_placeholders:
            cached_placeholders[sport] = placeholder_hashes(sport)
        hashes, perceptual_hashes = cached_placeholders[sport]
        image = fetch(url)
        bad = image.get("status") != "ok" or image.get("sha256") in hashes or any(
            hamming(image.get("perceptual_hash", ""), candidate) <= 4 for candidate in perceptual_hashes
        )
        if bad:
            print(f"Rejected {sport} {player_id}: {image.get('status', 'placeholder')}")
            continue
        note = (row.get("source_note") or "User-submitted direct image URL; verified by TeamMateTag importer.").strip()[:1000]
        verified.append((url, image["sha256"], image["perceptual_hash"], image["width"], image["height"], note, sport, player_id))
    with server.db() as conn, conn.cursor() as cur:
        cur.executemany(
            """UPDATE player_headshots SET source_url=%s, fallback_url=NULL, provider='manual_submission', status='verified',
                   content_sha256=%s, perceptual_hash=%s, width=%s, height=%s, reviewed_at=now(), review_note=%s
                 WHERE sport_id=%s AND player_id=%s""",
            verified,
        )
        cur.executemany(
            """INSERT INTO sport_player_images (sport_id,player_id,source_url) VALUES (%s,%s,%s)
                 ON CONFLICT (sport_id,player_id) DO UPDATE SET source_url=EXCLUDED.source_url""",
            [(sport, player_id, url) for url, _, _, _, _, _, sport, player_id in verified if sport != "baseball"],
        )
    print(f"Applied {len(verified):,} validated user-submitted headshots.")


if __name__ == "__main__":
    main()
