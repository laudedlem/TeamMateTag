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
            """SELECT p.player_id, concat_ws(' ', p.name_first, p.name_last), p.debut_year, p.final_year, h.status
                 FROM player_headshots h JOIN players p ON p.player_id=h.player_id
                 WHERE h.sport_id='baseball' AND p.final_year>=2000
                   AND h.status IN ('placeholder','missing')
                 ORDER BY p.final_year DESC NULLS LAST, p.name_last, p.name_first"""
        ).fetchall()
    return conn.execute(
        """SELECT p.player_id, p.display_name, p.debut_year, p.final_year, h.status
             FROM player_headshots h JOIN sport_players p ON p.sport_id=h.sport_id AND p.player_id=h.player_id
             WHERE h.sport_id=%s AND p.final_year>=2000
               AND h.status IN ('placeholder','missing')
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
            for player_id, name, debut, final, status in rows_for_sport(conn, sport):
                output_rows.append({
                    "sport": sport,
                    "player_id": player_id,
                    "player_name": name,
                    "career_years": f"{debut or '?'}-{final or '?'}",
                    "current_status": status,
                    "replacement_url": "",
                    "source_note": "",
                })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        fields = ["sport", "player_id", "player_name", "career_years", "current_status", "replacement_url", "source_note"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_rows)
    print(f"Wrote {len(output_rows):,} review rows to {args.output}")


if __name__ == "__main__":
    main()
