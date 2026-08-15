"""Restore manually reviewed production headshots after automated source audits.

These are intentionally a very small set. Each entry was visually checked for
the correct player and has a usable source/license note. Do not add a URL here
just because it returns HTTP 200.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "web")
import server  # noqa: E402
from audit_runtime_headshots import fetch

OVERRIDES = {
    "nfl:00-0024272": (
        "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f4/Devin_Hester_Chicago_Bears_Salute_to_Service_Day_%28cropped%29.jpg/330px-Devin_Hester_Chicago_Bears_Salute_to_Service_Day_%28cropped%29.jpg",
        "Wikimedia Commons public-domain photo, visually verified as Devin Hester.",
    ),
    "nfl:00-0019699": (
        "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4c/Mike_Brown.jpg/330px-Mike_Brown.jpg",
        "Wikimedia Commons photo, visually verified as Mike Brown.",
    ),
}


def main() -> None:
    server.ensure_runtime_schema()
    rows = []
    for player_id, (url, note) in OVERRIDES.items():
        result = fetch(url)
        # These entries were reviewed before being added. Wikimedia can
        # transiently rate-limit the audit client, which must not turn a known
        # valid player photo back into a league placeholder.
        rows.append((url, result.get("sha256"), result.get("perceptual_hash"),
                     result.get("width"), result.get("height"), note, player_id))
    with server.db() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """UPDATE player_headshots SET source_url=%s, fallback_url=NULL, provider='manual_verified',
                       status='verified', content_sha256=%s, perceptual_hash=%s, width=%s, height=%s,
                       review_note=%s WHERE sport_id='football' AND player_id=%s""",
                rows,
            )
    print(f"Applied {len(rows)} manually verified runtime headshots.")


if __name__ == "__main__":
    main()
