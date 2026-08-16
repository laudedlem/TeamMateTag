"""Export unresolved NFL headshot priority list.

The list is ranked by corrected games played, includes reliable college values
from nflverse roster files when available, and adds search links for manual
photo hunting.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote_plus

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
OUT = ROOT / "raw" / "nfl_headshot_priority_list.csv"


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", value.strip())


def football_db_guesses(name: str) -> str:
    value = norm(name).lower()
    display = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    parts = [re.sub(r"[^a-z]", "", part) for part in value.split()]
    while parts and parts[-1] in {"jr", "sr", "ii", "iii", "iv", "v"}:
        parts.pop()
    if len(parts) < 2:
        return ""
    base = parts[-1][:5] + parts[0][:2]
    return "; ".join(f"https://www.footballdb.com/players/{display}-{base}{i:02d}" for i in range(1, 5))


def college_map() -> dict[str, str]:
    colleges: dict[str, Counter[str]] = defaultdict(Counter)
    for folder in (ROOT / "raw" / "nfl" / "weekly_rosters", ROOT / "raw" / "nfl" / "rosters"):
        if not folder.exists():
            continue
        for path in folder.glob("*.csv"):
            with path.open(encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    college = norm(row.get("college") or "")
                    if not college:
                        continue
                    for key in ("gsis_id", "pfr_id", "espn_id"):
                        source = norm(row.get(key) or "")
                        if source:
                            colleges[f"nfl:{source}"][college] += 1
    return {player_id: counts.most_common(1)[0][0] for player_id, counts in colleges.items()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-games", type=int, default=0)
    parser.add_argument("--output", default=str(OUT))
    args = parser.parse_args()

    colleges = college_map()
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None) as conn:
        rows = conn.execute(
            """
          WITH season_games AS (
            SELECT sport_id, player_id, team_id, season, MAX(COALESCE(games_total,0)) AS games
              FROM sport_appearances
             WHERE sport_id='football'
             GROUP BY sport_id, player_id, team_id, season
          ), player_games AS (
            SELECT player_id, SUM(games) AS games
              FROM season_games
             GROUP BY player_id
          )
          SELECT p.player_id,p.display_name,p.debut_year,p.final_year,p.primary_pos,h.status,
                 COALESCE(pg.games,0) games,
                 string_agg(DISTINCT st.name, ', ' ORDER BY st.name) teams,
                 string_agg(DISTINCT sg.season::text || ':' || st.name, '; ' ORDER BY sg.season::text || ':' || st.name) seasons_teams,
                 string_agg(DISTINCT COALESCE(tried.provider,'') || ':' || COALESCE(tried.status,''), '; ' ORDER BY COALESCE(tried.provider,'') || ':' || COALESCE(tried.status,'')) attempted
            FROM sport_players p
            JOIN player_headshots h ON h.sport_id=p.sport_id AND h.player_id=p.player_id
            LEFT JOIN player_games pg ON pg.player_id=p.player_id
            LEFT JOIN season_games sg ON sg.sport_id=p.sport_id AND sg.player_id=p.player_id
            LEFT JOIN sport_teams st ON st.sport_id=sg.sport_id AND st.team_id=sg.team_id AND st.season=sg.season
            LEFT JOIN player_headshot_source_attempts tried ON tried.sport_id=p.sport_id AND tried.player_id=p.player_id
           WHERE p.sport_id='football'
             AND p.final_year>=2000
             AND h.status IN ('placeholder','missing','wrong_player','bad_crop')
           GROUP BY p.player_id,p.display_name,p.debut_year,p.final_year,p.primary_pos,h.status,pg.games
          HAVING COALESCE(pg.games,0) >= %s
           ORDER BY games DESC, p.final_year DESC NULLS LAST, p.display_name
            """,
            (args.min_games,),
        ).fetchall()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "rank", "player_id", "name", "career_years", "position", "games",
        "college", "headshot_status", "teams", "seasons_teams", "attempted_sources",
        "google_search", "google_image_search", "college_image_search",
        "college_team_search", "footballdb_guesses",
    ]
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for rank, row in enumerate(rows, 1):
            player_id, name, debut, final, position, status, games, teams, seasons_teams, attempted = row
            college = colleges.get(player_id, "")
            first_team = (teams or "").split(", ")[0]
            base_query = f"{name} NFL football player photo {first_team}".strip()
            college_query = f"{name} {college} football photo".strip()
            college_team_query = f"{name} {college} football {first_team} photo".strip()
            writer.writerow(
                {
                    "rank": rank,
                    "player_id": player_id,
                    "name": name,
                    "career_years": f"{debut}-{final}",
                    "position": position or "",
                    "games": int(games or 0),
                    "college": college,
                    "headshot_status": status,
                    "teams": teams or "",
                    "seasons_teams": seasons_teams or "",
                    "attempted_sources": attempted or "",
                    "google_search": "https://www.google.com/search?q=" + quote_plus(base_query),
                    "google_image_search": "https://www.google.com/search?tbm=isch&q=" + quote_plus(base_query),
                    "college_image_search": "https://www.google.com/search?tbm=isch&q=" + quote_plus(college_query),
                    "college_team_search": "https://www.google.com/search?q=" + quote_plus(college_team_query),
                    "footballdb_guesses": football_db_guesses(name),
                }
            )
    print(f"Wrote {len(rows):,} rows to {output}")


if __name__ == "__main__":
    main()
