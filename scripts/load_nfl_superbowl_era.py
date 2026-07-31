"""Load Super Bowl-era NFL roster data into the cross-sport Postgres tables.

The source is nflverse. Weekly rosters (2002 onward) retain a player's teams
during a season; annual rosters are used for 1966-2001 because weekly archives
begin in 2002. A qualifying appearance means the player appeared on a source
roster for at least one week, not necessarily in a game.

Run from the repository root:
    python scripts/load_nfl_superbowl_era.py

The default range is 1966 through 2025. The script caches source CSVs under
raw/nfl/, which is intentionally not committed to Git.
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import re
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

import psycopg
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


ROOT = Path(__file__).resolve().parent.parent
WEEKLY_START = 2002
NFLVERSE_RELEASE = "https://github.com/nflverse/nflverse-data/releases/download"

TEAM_DEFAULTS = {
    "ARI": ("ARI", "Arizona Cardinals"),
    "ATL": ("ATL", "Atlanta Falcons"),
    "BUF": ("BUF", "Buffalo Bills"),
    "CAR": ("CAR", "Carolina Panthers"),
    "CHI": ("CHI", "Chicago Bears"),
    "CIN": ("CIN", "Cincinnati Bengals"),
    "CLE": ("CLE", "Cleveland Browns"),
    "DAL": ("DAL", "Dallas Cowboys"),
    "DEN": ("DEN", "Denver Broncos"),
    "DET": ("DET", "Detroit Lions"),
    "GB": ("GB", "Green Bay Packers"),
    "HOU": ("HOU", "Houston Texans"),
    "IND": ("IND", "Indianapolis Colts"),
    "JAC": ("JAX", "Jacksonville Jaguars"),
    "JAX": ("JAX", "Jacksonville Jaguars"),
    "KC": ("KC", "Kansas City Chiefs"),
    "LAC": ("LAC", "Los Angeles Chargers"),
    "LAR": ("LAR", "Los Angeles Rams"),
    "LV": ("LV", "Las Vegas Raiders"),
    "MIA": ("MIA", "Miami Dolphins"),
    "MIN": ("MIN", "Minnesota Vikings"),
    "NE": ("NE", "New England Patriots"),
    "NO": ("NO", "New Orleans Saints"),
    "NYG": ("NYG", "New York Giants"),
    "NYJ": ("NYJ", "New York Jets"),
    "PHI": ("PHI", "Philadelphia Eagles"),
    "PIT": ("PIT", "Pittsburgh Steelers"),
    "SEA": ("SEA", "Seattle Seahawks"),
    "SF": ("SF", "San Francisco 49ers"),
    "TB": ("TB", "Tampa Bay Buccaneers"),
    "TEN": ("TEN", "Tennessee Titans"),
    "WAS": ("WAS", "Washington Commanders"),
}


def team_info(code: str, season: int) -> tuple[str, str]:
    """Return canonical franchise and era-appropriate team display name."""
    code = code.upper().strip()
    if code == "BOS":
        return "NE", "Boston Patriots"
    if code == "BAL":
        return ("IND", "Baltimore Colts") if season <= 1983 else ("BAL", "Baltimore Ravens")
    if code == "HOU":
        return ("TEN", "Houston Oilers") if season <= 1996 else ("HOU", "Houston Texans")
    if code in {"LA", "RAM"}:
        return "LAR", "Los Angeles Rams"
    if code == "STL":
        return ("ARI", "St. Louis Cardinals") if season <= 1987 else ("LAR", "St. Louis Rams")
    if code in {"OAK", "RAI"}:
        return "LV", "Oakland Raiders"
    if code == "SD":
        return "LAC", "San Diego Chargers"
    if code == "PHO":
        return "ARI", "Phoenix Cardinals"
    if code == "WAS":
        if season >= 2022:
            return "WAS", "Washington Commanders"
        if season >= 2020:
            return "WAS", "Washington Football Team"
        return "WAS", "Washington Redskins"
    return TEAM_DEFAULTS.get(code, (code, code))


def clean_key(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", value.lower())


def player_id(row: dict) -> str | None:
    for column in ("gsis_id", "pfr_id", "espn_id", "sportradar_id"):
        value = (row.get(column) or "").strip()
        if value:
            return f"nfl:{value}"
    name = (row.get("full_name") or "").strip()
    birth = (row.get("birth_date") or "").strip()
    return f"nfl:{clean_key(name)}:{birth}" if name else None


def source_for_season(season: int) -> tuple[str, str]:
    if season >= WEEKLY_START:
        filename = f"roster_weekly_{season}.csv"
        return "weekly_rosters", f"{NFLVERSE_RELEASE}/weekly_rosters/{filename}"
    filename = f"roster_{season}.csv"
    return "rosters", f"{NFLVERSE_RELEASE}/rosters/{filename}"


def load_csv(season: int, cache_dir: Path) -> tuple[str, list[dict], str]:
    source, url = source_for_season(season)
    cache_path = cache_dir / source / Path(url).name
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists():
        text = cache_path.read_text(encoding="utf-8")
    else:
        response = requests.get(url, timeout=90)
        response.raise_for_status()
        text = response.content.decode("utf-8-sig")
        cache_path.write_text(text, encoding="utf-8", newline="")
        time.sleep(0.15)
    return source, list(csv.DictReader(io.StringIO(text))), url


def ensure_schema(conn) -> None:
    schema = (ROOT / "db" / "cross_sport_schema_postgres.sql").read_text(encoding="utf-8")
    conn.execute(schema)


def upsert_season(conn, season: int, rows: list[dict], source: str, url: str) -> tuple[int, int]:
    players: dict[str, tuple] = {}
    appearances: dict[tuple[str, str], int] = defaultdict(int)
    teams: dict[str, tuple[str, str]] = {}

    for row in rows:
        raw_team = (row.get("team") or "").strip().upper()
        pid = player_id(row)
        name = (row.get("full_name") or "").strip()
        if not raw_team or not pid or not name:
            continue
        franchise_id, team_name = team_info(raw_team, season)
        if team_name == raw_team:
            continue
        teams[raw_team] = (franchise_id, team_name)
        appearances[(pid, raw_team)] += 1
        players[pid] = (
            "football", pid, pid,
            name, (row.get("first_name") or "").strip() or None,
            (row.get("last_name") or "").strip() or None,
            int((row.get("birth_date") or "")[:4]) if (row.get("birth_date") or "")[:4].isdigit() else None,
            season, season, (row.get("position") or "").strip() or None,
        )

    franchises = {(franchise, team_name) for franchise, team_name in teams.values()}
    conn.cursor().executemany(
        """INSERT INTO sport_franchises (sport_id, franchise_id, name)
           VALUES ('football', %s, %s)
           ON CONFLICT (sport_id, franchise_id) DO NOTHING""",
        list(franchises),
    )
    conn.cursor().executemany(
        """INSERT INTO sport_teams (sport_id, team_id, season, franchise_id, name)
           VALUES ('football', %s, %s, %s, %s)
           ON CONFLICT (sport_id, team_id, season) DO UPDATE
           SET franchise_id = EXCLUDED.franchise_id, name = EXCLUDED.name""",
        [(team, season, franchise, name) for team, (franchise, name) in teams.items()],
    )
    conn.cursor().executemany(
        """INSERT INTO sport_players
           (sport_id, player_id, external_id, display_name, first_name, last_name,
            birth_year, debut_year, final_year, primary_pos)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (sport_id, player_id) DO UPDATE
           SET display_name = EXCLUDED.display_name,
               first_name = COALESCE(EXCLUDED.first_name, sport_players.first_name),
               last_name = COALESCE(EXCLUDED.last_name, sport_players.last_name),
               birth_year = COALESCE(sport_players.birth_year, EXCLUDED.birth_year),
               debut_year = LEAST(sport_players.debut_year, EXCLUDED.debut_year),
               final_year = GREATEST(sport_players.final_year, EXCLUDED.final_year),
               primary_pos = COALESCE(EXCLUDED.primary_pos, sport_players.primary_pos)""",
        list(players.values()),
    )
    conn.cursor().executemany(
        """INSERT INTO sport_appearances
           (sport_id, player_id, team_id, season, games_total)
           VALUES ('football', %s, %s, %s, %s)
           ON CONFLICT (sport_id, player_id, team_id, season) DO UPDATE
           SET games_total = EXCLUDED.games_total""",
        [(pid, team, season, weeks) for (pid, team), weeks in appearances.items()],
    )
    conn.execute(
        """INSERT INTO sport_data_provenance
           (sport_id, source, season, source_url, license_note, row_count)
           VALUES ('football', %s, %s, %s, %s, %s)
           ON CONFLICT (sport_id, source, season) DO UPDATE
           SET source_url = EXCLUDED.source_url, row_count = EXCLUDED.row_count,
               fetched_at = now()""",
        (source, season, url, "nflverse release data", len(rows)),
    )
    return len(players), len(appearances)


def rebuild_search(conn, materialize_graph: bool) -> tuple[int, int]:
    graph_count = 0
    if materialize_graph:
        conn.execute("DELETE FROM sport_teammates WHERE sport_id = 'football'")
        graph_count = conn.execute(
            """WITH inserted AS (
                   INSERT INTO sport_teammates
                       (sport_id, player_a_id, player_b_id, team_id, season)
                   SELECT 'football', a.player_id, b.player_id, a.team_id, a.season
                     FROM sport_appearances a
                     JOIN sport_appearances b
                       ON b.sport_id = a.sport_id
                      AND b.team_id = a.team_id
                      AND b.season = a.season
                      AND a.player_id < b.player_id
                    WHERE a.sport_id = 'football'
                   RETURNING 1
               ) SELECT COUNT(*) FROM inserted"""
        ).fetchone()[0]
    rows = conn.execute(
        """SELECT p.player_id, p.display_name, p.debut_year, p.final_year,
                  COALESCE(SUM(a.games_total), 0) AS roster_weeks,
                  COALESCE(d.degree, 0) AS degree
             FROM sport_players p
             LEFT JOIN sport_appearances a
               ON a.sport_id = p.sport_id AND a.player_id = p.player_id
             LEFT JOIN (
                 SELECT player_id, COUNT(DISTINCT teammate_id) AS degree
                 FROM (
                     SELECT player_a_id AS player_id, player_b_id AS teammate_id
                       FROM sport_teammates WHERE sport_id = 'football'
                     UNION ALL
                     SELECT player_b_id, player_a_id
                       FROM sport_teammates WHERE sport_id = 'football'
                 ) pairs
                 GROUP BY player_id
             ) d ON d.player_id = p.player_id
            WHERE p.sport_id = 'football'
            GROUP BY p.player_id, p.display_name, p.debut_year, p.final_year, d.degree"""
    ).fetchall()
    conn.execute("DELETE FROM sport_players_searchable WHERE sport_id = 'football'")
    conn.cursor().executemany(
        """INSERT INTO sport_players_searchable
           (sport_id, player_id, display_name, disambiguation, search_key, last_key,
            career_games, teammate_count)
           VALUES ('football', %s, %s, %s, %s, %s, %s, %s)""",
        [
            (
                pid, name,
                f"NFL, {debut or '?'}-{final or '?'}",
                clean_key(name), clean_key(name.split()[-1]), int(weeks), int(degree),
            )
            for pid, name, debut, final, weeks, degree in rows
        ],
    )
    return graph_count, len(rows)


def clear_football_data(conn) -> None:
    """Remove only derived/source NFL records so interrupted loads can restart."""
    conn.execute("DELETE FROM sport_players_searchable WHERE sport_id = 'football'")
    conn.execute("DELETE FROM sport_player_aliases WHERE sport_id = 'football'")
    conn.execute("DELETE FROM sport_teammates WHERE sport_id = 'football'")
    conn.execute("DELETE FROM sport_appearances WHERE sport_id = 'football'")
    conn.execute("DELETE FROM sport_data_provenance WHERE sport_id = 'football'")
    conn.execute("DELETE FROM sport_players WHERE sport_id = 'football'")
    conn.execute("DELETE FROM sport_teams WHERE sport_id = 'football'")
    conn.execute("DELETE FROM sport_franchises WHERE sport_id = 'football'")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-season", type=int, default=1966)
    parser.add_argument("--end-season", type=int, default=2025)
    parser.add_argument("--cache-dir", default=str(ROOT / "raw" / "nfl"))
    parser.add_argument("--resume", action="store_true",
                        help="keep existing NFL rows instead of rebuilding from scratch")
    parser.add_argument("--finalize-only", action="store_true",
                        help="rebuild search from already-loaded NFL roster data")
    parser.add_argument("--materialize-graph", action="store_true",
                        help="build redundant pair edges; requires substantially more database storage")
    args = parser.parse_args()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required. Set it in .env first.")

    cache_dir = Path(args.cache_dir)
    with psycopg.connect(database_url, autocommit=True, prepare_threshold=None) as conn:
        conn.execute("SET default_transaction_read_only = off")
        ensure_schema(conn)
        if not args.resume and not args.finalize_only:
            clear_football_data(conn)
        total_players = total_appearances = 0
        if not args.finalize_only:
            for season in range(args.start_season, args.end_season + 1):
                source, rows, url = load_csv(season, cache_dir)
                player_count, appearance_count = upsert_season(conn, season, rows, source, url)
                total_players += player_count
                total_appearances += appearance_count
                print(f"{season}: {player_count:,} players, {appearance_count:,} player-team-seasons")
        edges, searchable = rebuild_search(conn, args.materialize_graph)
    print(f"Loaded NFL {args.start_season}-{args.end_season}: "
          f"{total_players:,} source player records, {total_appearances:,} appearances, "
          f"{edges:,} teammate edges, {searchable:,} searchable players.")


if __name__ == "__main__":
    main()
