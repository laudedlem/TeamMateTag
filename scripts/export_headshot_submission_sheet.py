"""Export review sheets for user-supplied player-headshot replacements.

Open the resulting CSV in Excel, paste a direct image URL into
``replacement_url``, and add a brief source note. The matching importer checks
the bytes and blocks known placeholders before changing the live registry.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
import server  # noqa: E402


def rows_for_sport(conn, sport: str):
    if sport == "baseball":
        return conn.execute(
            """SELECT p.player_id, concat_ws(' ', p.name_first, p.name_last), p.debut_year, p.final_year, h.status,
                       p.primary_pos, string_agg(DISTINCT a.team_id, ',' ORDER BY a.team_id)
                 FROM player_headshots h JOIN players p ON p.player_id=h.player_id
                 LEFT JOIN appearances a ON a.player_id=p.player_id
                 WHERE h.sport_id='baseball' AND p.final_year>=2000
                   AND h.status IN ('placeholder','missing')
                 GROUP BY p.player_id,p.name_first,p.name_last,p.debut_year,p.final_year,h.status,p.primary_pos
                 ORDER BY p.final_year DESC NULLS LAST, p.name_last, p.name_first"""
        ).fetchall()
    if sport == "hockey":
        # Hockey Databank and NHL API imports can represent the same person
        # under different IDs. Collapse only exact same-player career signatures.
        return conn.execute(
            """WITH candidates AS (
                  SELECT p.player_id,p.display_name,p.debut_year,p.final_year,h.status,
                         CASE p.primary_pos WHEN 'L' THEN 'LW' WHEN 'R' THEN 'RW' ELSE p.primary_pos END AS primary_pos,
                         string_agg((CASE regexp_replace(a.team_id, '^hdb:', '')
                                      WHEN 'WAS' THEN 'WSH' WHEN 'CBS' THEN 'CBJ'
                                      ELSE regexp_replace(a.team_id, '^hdb:', '') END)
                                    || ':' || a.season::text, ',' ORDER BY a.team_id,a.season) AS team_seasons,
                         row_number() OVER (
                           PARTITION BY p.display_name,p.birth_year,p.debut_year,p.final_year,
                             CASE p.primary_pos WHEN 'L' THEN 'LW' WHEN 'R' THEN 'RW' ELSE p.primary_pos END,
                             string_agg((CASE regexp_replace(a.team_id, '^hdb:', '')
                                          WHEN 'WAS' THEN 'WSH' WHEN 'CBS' THEN 'CBJ'
                                          ELSE regexp_replace(a.team_id, '^hdb:', '') END)
                                        || ':' || a.season::text, ',' ORDER BY a.team_id,a.season)
                           ORDER BY (p.player_id LIKE 'nhl:%') DESC,p.player_id
                         ) AS identity_rank
                    FROM sport_players p JOIN player_headshots h
                      ON h.sport_id=p.sport_id AND h.player_id=p.player_id
                    JOIN sport_appearances a ON a.sport_id=p.sport_id AND a.player_id=p.player_id
                   WHERE p.sport_id='hockey' AND p.final_year>=2000
                     AND h.status IN ('placeholder','missing')
                   GROUP BY p.player_id,p.display_name,p.birth_year,p.debut_year,p.final_year,h.status,p.primary_pos
                ) SELECT player_id,display_name,debut_year,final_year,status,primary_pos,team_seasons
                    FROM candidates WHERE identity_rank=1
                    ORDER BY final_year DESC NULLS LAST,display_name"""
        ).fetchall()
    return conn.execute(
        """SELECT p.player_id, p.display_name, p.debut_year, p.final_year, h.status, p.primary_pos,
                   string_agg(DISTINCT a.team_id, ',' ORDER BY a.team_id)
             FROM player_headshots h JOIN sport_players p ON p.sport_id=h.sport_id AND p.player_id=h.player_id
             LEFT JOIN sport_appearances a ON a.sport_id=p.sport_id AND a.player_id=p.player_id
             WHERE h.sport_id=%s AND p.final_year>=2000
               AND h.status IN ('placeholder','missing')
             GROUP BY p.player_id,p.display_name,p.debut_year,p.final_year,h.status,p.primary_pos
             ORDER BY p.final_year DESC NULLS LAST, p.display_name""",
        (sport,),
    ).fetchall()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", choices=("baseball", "basketball", "football", "hockey", "all"), default="all")
    parser.add_argument("--output", type=Path, default=ROOT / "raw" / "headshot_submissions.csv")
    args = parser.parse_args()
    sports = ("baseball", "basketball", "football", "hockey") if args.sport == "all" else (args.sport,)
    output_rows = []
    with server.db() as conn:
        for sport in sports:
            for player_id, name, debut, final, status, position, teams in rows_for_sport(conn, sport):
                output_rows.append({
                    "sport": sport,
                    "player_id": player_id,
                    "player_name": name,
                    "career_years": f"{debut or '?'}-{final or '?'}",
                    "position": position or "?",
                    "teams": teams or "?",
                    "current_status": status,
                    "replacement_url": "",
                    "source_note": "",
                })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        fields = ["sport", "player_id", "player_name", "career_years", "position", "teams", "current_status", "replacement_url", "source_note"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_rows)
    print(f"Wrote {len(output_rows):,} review rows to {args.output}")


if __name__ == "__main__":
    main()
