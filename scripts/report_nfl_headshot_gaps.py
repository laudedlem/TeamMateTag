"""Export the complete unresolved NFL headshot queue for review."""
from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, "web")
import server  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    server.ensure_runtime_schema()
    output = ROOT / "raw" / f"nfl_unresolved_headshots_{date.today().isoformat()}.csv"
    with server.db() as conn:
        rows = conn.execute("""
            SELECT p.player_id, p.display_name, p.debut_year, p.final_year, h.status,
                   COALESCE(SUM(a.games_total), 0) AS career_games,
                   string_agg(DISTINCT tried.provider || ':' || tried.status, '; ' ORDER BY tried.provider || ':' || tried.status) AS attempted_sources
              FROM sport_players p
              JOIN player_headshots h ON h.sport_id=p.sport_id AND h.player_id=p.player_id
              LEFT JOIN sport_appearances a ON a.sport_id=p.sport_id AND a.player_id=p.player_id
              LEFT JOIN player_headshot_source_attempts tried ON tried.sport_id=p.sport_id AND tried.player_id=p.player_id
             WHERE p.sport_id='football' AND h.status IN ('placeholder','missing')
             GROUP BY p.player_id, p.display_name, p.debut_year, p.final_year, h.status
             ORDER BY career_games DESC, p.display_name
        """).fetchall()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["player_id", "display_name", "debut_year", "final_year", "headshot_status", "career_games", "attempted_sources"])
        writer.writerows(rows)
    print(f"Wrote {len(rows):,} unresolved football headshots to {output}")


if __name__ == "__main__":
    main()
