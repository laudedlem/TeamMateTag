"""Publish verified runtime headshot URLs into the display image table.

The audit registry is `player_headshots`; cross-sport player cards read
`sport_player_images`. Run this after resolving or importing headshots so a
verified registry URL is visible in the app.
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "web")
import server  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", action="append", choices=("basketball", "football", "hockey"))
    args = parser.parse_args()
    sports = args.sport or ["basketball", "football", "hockey"]
    server.ensure_runtime_schema()
    with server.db() as conn:
        count = conn.execute(
            """INSERT INTO sport_player_images (sport_id, player_id, source_url)
               SELECT h.sport_id, h.player_id, h.source_url
                 FROM player_headshots h
                 JOIN sport_players p ON p.sport_id=h.sport_id AND p.player_id=h.player_id
                WHERE h.sport_id = ANY(%s)
                  AND h.status='verified'
                  AND COALESCE(h.source_url,'') <> ''
                  AND p.final_year >= 2000
               ON CONFLICT (sport_id, player_id)
               DO UPDATE SET source_url=EXCLUDED.source_url""",
            (sports,),
        ).rowcount
    print(f"Synced {count:,} verified headshots into sport_player_images.")


if __name__ == "__main__":
    main()
