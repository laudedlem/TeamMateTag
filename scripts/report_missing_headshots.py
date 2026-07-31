"""Write a reviewable CSV of local players without a cached native headshot."""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "db" / "teammatetag_local.sqlite"
REPORT = ROOT / "raw" / "missing_headshots.csv"


def main() -> None:
    conn = sqlite3.connect(DATABASE)
    rows = conn.execute("""
        SELECT p.sport_id, p.player_id, p.display_name, p.external_id, p.debut_year, p.final_year
          FROM sport_players p
          LEFT JOIN local_player_images i ON i.sport_id=p.sport_id AND i.player_id=p.player_id
         WHERE p.sport_id IN ('basketball','football','hockey') AND i.player_id IS NULL
         ORDER BY p.sport_id, p.debut_year, p.display_name
    """).fetchall()
    conn.close()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with REPORT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sport", "player_id", "player_name", "external_id", "debut_year", "final_year"])
        writer.writerows(rows)
    counts = {}
    for sport, *_ in rows: counts[sport] = counts.get(sport, 0) + 1
    print(f"Wrote {len(rows):,} unresolved players to {REPORT}")
    print(counts)


if __name__ == "__main__":
    main()
