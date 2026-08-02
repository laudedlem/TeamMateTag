"""Load Lahman appearance counts needed for daily Baseball Film Review decks.

This table is intentionally small: player, position, and cumulative games.
Run after updating raw/Appearances.csv, locally or against DATABASE_URL.
"""
from __future__ import annotations

import csv
import os
from collections import defaultdict
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

POSITION_COLUMNS = {
    "G_c": "C", "G_1b": "1B", "G_2b": "2B", "G_3b": "3B", "G_ss": "SS",
    "G_lf": "LF", "G_cf": "CF", "G_rf": "RF", "G_dh": "DH", "G_p": "SP",
}


def main() -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    totals: dict[tuple[str, str], int] = defaultdict(int)
    with (ROOT / "raw" / "Appearances.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            player_id = row.get("playerID")
            if not player_id:
                continue
            for column, position in POSITION_COLUMNS.items():
                totals[(player_id, position)] += int(row.get(column) or 0)
    # Supabase's transaction pooler cannot retain prepared statements.
    with psycopg.connect(url, autocommit=True, prepare_threshold=None) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS baseball_player_positions (
            player_id TEXT NOT NULL REFERENCES players(player_id) ON DELETE CASCADE,
            position TEXT NOT NULL, games INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (player_id, position))""")
        conn.execute("TRUNCATE baseball_player_positions")
        known_players = {row[0] for row in conn.execute("SELECT player_id FROM players").fetchall()}
        rows = [(pid, position, games) for (pid, position), games in totals.items()
                if games and pid in known_players]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO baseball_player_positions (player_id, position, games) VALUES (%s,%s,%s) ON CONFLICT (player_id,position) DO UPDATE SET games=EXCLUDED.games",
                rows,
            )
    print(f"Loaded {len(rows):,} baseball player-position totals.")


if __name__ == "__main__":
    main()
