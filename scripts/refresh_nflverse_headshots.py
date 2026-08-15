"""Refresh NFL headshot candidates from nflverse's GSIS-to-ESPN identity file."""
from __future__ import annotations

import csv
import io
import sys

import requests

sys.path.insert(0, "web")
import server  # noqa: E402

PLAYERS_URL = "https://github.com/nflverse/nflverse-data/releases/download/players/players.csv"


def main() -> None:
    response = requests.get(PLAYERS_URL, timeout=90)
    response.raise_for_status()
    images_by_gsis = {}
    for row in csv.DictReader(io.StringIO(response.content.decode("utf-8"))):
        gsis_id = (row.get("gsis_id") or "").strip()
        headshot, espn_id = (row.get("headshot") or "").strip(), (row.get("espn_id") or "").strip()
        if gsis_id and (headshot.startswith("http") or espn_id.isdigit()):
            images_by_gsis[gsis_id] = headshot if headshot.startswith("http") else (
                "https://a.espncdn.com/i/headshots/nfl/players/full/" + espn_id + ".png"
            )
    with server.db() as conn:
        player_ids = conn.execute("SELECT player_id FROM sport_players WHERE sport_id='football'").fetchall()
        updates = []
        for (player_id,) in player_ids:
            gsis_id = player_id.removeprefix("nfl:")
            image_url = images_by_gsis.get(gsis_id)
            if image_url:
                updates.append((image_url, player_id))
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO sport_player_images (sport_id, player_id, source_url)
                     VALUES ('football', %s, %s)
                     ON CONFLICT (sport_id, player_id) DO UPDATE SET source_url=EXCLUDED.source_url""",
                [(player_id, url) for url, player_id in updates],
            )
    print(f"Updated {len(updates):,} NFL headshot candidates from nflverse.")


if __name__ == "__main__":
    main()
