"""Report local multi-sport coverage and suspicious player-season gaps."""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "db" / "teammatetag_local.sqlite"


def main() -> None:
    conn = sqlite3.connect(DATABASE)
    try:
        for sport in ("baseball", "football", "basketball", "hockey"):
            players, first_year, last_year = conn.execute(
                "SELECT COUNT(DISTINCT player_id), MIN(season), MAX(season) FROM sport_appearances WHERE sport_id = ?", (sport,)
            ).fetchone()
            print(f"{sport}: {players:,} players, {first_year}-{last_year}")
        print("\nCareer gaps of two or fewer seasons, for review only:")
        rows = conn.execute("""
            WITH ordered AS (
              SELECT sport_id, player_id, season,
                     LAG(season) OVER (PARTITION BY sport_id, player_id ORDER BY season) AS prior
                FROM (SELECT DISTINCT sport_id, player_id, season FROM sport_appearances)
            )
            SELECT p.sport_id, p.display_name, o.prior, o.season
              FROM ordered o JOIN sport_players p ON p.sport_id=o.sport_id AND p.player_id=o.player_id
             WHERE o.prior IS NOT NULL AND o.season - o.prior BETWEEN 2 AND 3
             ORDER BY p.sport_id, p.display_name LIMIT 100
        """).fetchall()
        for sport, name, prior, season in rows:
            print(f"  {sport}: {name}, {prior} to {season}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
