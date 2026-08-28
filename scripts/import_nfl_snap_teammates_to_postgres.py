#!/usr/bin/env python3
"""Import strict NFL snap-count appearances into Supabase.

Football covered seasons use compact same-game snap appearances for validation:
two players are teammates when both recorded at least one offensive, defensive,
or special-teams snap for the same team in the same regular-season game.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

import psycopg

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "raw" / "nfl_game_teammates" / "nfl_snap_teammates.sqlite"
SCHEMA = ROOT / "db" / "cross_sport_schema_postgres.sql"
SPORT_ID = "football"
SOURCE_NAME = "nflverse_snap_counts"
SOURCE_URL = "https://github.com/nflverse/nflverse-data/releases/tag/snap_counts"
DEFAULT_GAME_DATE = "2000-01-01"


def nfl_team(code: str, season: int) -> tuple[str, str]:
    code = code.upper()
    if code == "BAL":
        return "BAL", "Baltimore Ravens"
    if code == "HOU":
        return "HOU", "Houston Texans"
    if code in {"LA", "LAR", "STL"}:
        if code == "STL" or season <= 2015:
            return "LAR", "St. Louis Rams"
        return "LAR", "Los Angeles Rams"
    if code == "OAK":
        return "LV", "Oakland Raiders"
    if code == "SD":
        return "LAC", "San Diego Chargers"
    names = {
        "ARI": "Arizona Cardinals",
        "ATL": "Atlanta Falcons",
        "BUF": "Buffalo Bills",
        "CAR": "Carolina Panthers",
        "CHI": "Chicago Bears",
        "CIN": "Cincinnati Bengals",
        "CLE": "Cleveland Browns",
        "DAL": "Dallas Cowboys",
        "DEN": "Denver Broncos",
        "DET": "Detroit Lions",
        "GB": "Green Bay Packers",
        "IND": "Indianapolis Colts",
        "JAX": "Jacksonville Jaguars",
        "KC": "Kansas City Chiefs",
        "LAC": "Los Angeles Chargers",
        "LV": "Las Vegas Raiders",
        "MIA": "Miami Dolphins",
        "MIN": "Minnesota Vikings",
        "NE": "New England Patriots",
        "NO": "New Orleans Saints",
        "NYG": "New York Giants",
        "NYJ": "New York Jets",
        "PHI": "Philadelphia Eagles",
        "PIT": "Pittsburgh Steelers",
        "SEA": "Seattle Seahawks",
        "SF": "San Francisco 49ers",
        "TB": "Tampa Bay Buccaneers",
        "TEN": "Tennessee Titans",
        "WAS": "Washington Commanders",
    }
    return code, names.get(code, code)


def first_name(display_name: str) -> str | None:
    parts = [part for part in display_name.split() if part]
    return parts[0] if parts else None


def last_name(display_name: str) -> str | None:
    parts = [part for part in display_name.replace(".", " ").split() if part]
    if len(parts) > 1 and parts[-1].lower() in {"jr", "sr", "ii", "iii", "iv", "v"}:
        parts = parts[:-1]
    return parts[-1] if parts else None


def normalize(value: str) -> str:
    import re
    import unicodedata

    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", value.lower())


def connect() -> "psycopg.Connection":
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    return psycopg.connect(url, prepare_threshold=None)


def ensure_tables(conn: "psycopg.Connection") -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA.read_text(encoding="utf-8"))
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_sport_live_player_games_pair "
            "ON sport_live_player_games(sport_id, game_id, team_id, player_id)"
        )
    conn.commit()


def source_summary(src: sqlite3.Connection) -> dict[str, int | None]:
    row = src.execute(
        """
        SELECT MIN(season), MAX(season), COUNT(DISTINCT game_id), COUNT(*),
               COUNT(DISTINCT player_id)
          FROM nfl_player_game_snap_appearances
        """
    ).fetchone()
    return {
        "season_start": int(row[0]) if row[0] is not None else None,
        "season_end": int(row[1]) if row[1] is not None else None,
        "games": int(row[2] or 0),
        "appearances": int(row[3] or 0),
        "players": int(row[4] or 0),
    }


def source_has_gamebook_dates(src: sqlite3.Connection) -> bool:
    row = src.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='nfl_gamebook_games'"
    ).fetchone()
    return row is not None


def copy_players(src: sqlite3.Connection, dst: "psycopg.Connection") -> int:
    rows = src.execute(
        """
        SELECT player_id, pfr_player_id, gsis_id, display_name, first_name,
               last_name, birth_year, primary_pos, headshot_url
          FROM nfl_snap_players
        """
    ).fetchall()
    with dst.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO sport_players
                (sport_id, player_id, external_id, display_name, first_name,
                 last_name, birth_year, debut_year, final_year, primary_pos)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, NULL, %s)
            ON CONFLICT (sport_id, player_id) DO UPDATE
            SET external_id = COALESCE(sport_players.external_id, EXCLUDED.external_id),
                display_name = COALESCE(NULLIF(sport_players.display_name, ''), EXCLUDED.display_name),
                first_name = COALESCE(sport_players.first_name, EXCLUDED.first_name),
                last_name = COALESCE(sport_players.last_name, EXCLUDED.last_name),
                birth_year = COALESCE(sport_players.birth_year, EXCLUDED.birth_year),
                primary_pos = COALESCE(sport_players.primary_pos, EXCLUDED.primary_pos)
            """,
            [
                (
                    SPORT_ID,
                    player_id,
                    pfr_id,
                    name,
                    first or first_name(name),
                    last or last_name(name),
                    birth_year,
                    pos,
                )
                for player_id, pfr_id, _gsis, name, first, last, birth_year, pos, _headshot in rows
            ],
        )
        image_rows = [(SPORT_ID, player_id, headshot) for player_id, *_rest, headshot in rows if headshot]
        cur.executemany(
            """
            INSERT INTO sport_player_images (sport_id, player_id, source_url)
            VALUES (%s, %s, %s)
            ON CONFLICT (sport_id, player_id) DO NOTHING
            """,
            image_rows,
        )
        cur.executemany(
            """
            INSERT INTO sport_players_searchable
                (sport_id, player_id, display_name, disambiguation,
                 search_key, last_key, career_games, teammate_count)
            VALUES (%s, %s, %s, %s, %s, %s, 0, 0)
            ON CONFLICT (sport_id, player_id) DO NOTHING
            """,
            [
                (
                    SPORT_ID,
                    player_id,
                    name,
                    f"{pos or 'NFL'}, ?-?",
                    normalize(name),
                    normalize(last or last_name(name) or name),
                )
                for player_id, _pfr_id, _gsis, name, _first, last, _birth_year, pos, _headshot in rows
            ],
        )
    return len(rows)


def copy_teams(src: sqlite3.Connection, dst: "psycopg.Connection", start: int, end: int) -> int:
    snap_teams = src.execute(
        """
        SELECT DISTINCT team_id, season
          FROM nfl_player_game_snap_appearances
         WHERE season BETWEEN ? AND ?
         ORDER BY season, team_id
        """,
        (start, end),
    ).fetchall()
    rows = [(team_id, season, *nfl_team(team_id, season)) for team_id, season in snap_teams]
    franchises = sorted({(franchise_id, name) for _team_id, _season, franchise_id, name in rows})
    with dst.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sports (sport_id, display_name, league_name, active, first_season, last_season)
            VALUES (%s, 'Football', 'NFL', true, %s, %s)
            ON CONFLICT (sport_id) DO UPDATE
            SET display_name = EXCLUDED.display_name,
                league_name = EXCLUDED.league_name,
                first_season = LEAST(COALESCE(sports.first_season, EXCLUDED.first_season), EXCLUDED.first_season),
                last_season = GREATEST(COALESCE(sports.last_season, EXCLUDED.last_season), EXCLUDED.last_season)
            """,
            (SPORT_ID, start, end),
        )
        cur.executemany(
            """
            INSERT INTO sport_franchises (sport_id, franchise_id, name, active)
            VALUES (%s, %s, %s, true)
            ON CONFLICT (sport_id, franchise_id) DO UPDATE
            SET name = EXCLUDED.name,
                active = true
            """,
            [(SPORT_ID, franchise_id, name) for franchise_id, name in franchises],
        )
        cur.executemany(
            """
            INSERT INTO sport_teams (sport_id, team_id, season, franchise_id, name)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (sport_id, team_id, season) DO UPDATE
            SET franchise_id = EXCLUDED.franchise_id,
                name = EXCLUDED.name
            """,
            [(SPORT_ID, team_id, season, franchise_id, name) for team_id, season, franchise_id, name in rows],
        )
    return len(rows)


def copy_games_and_appearances(
    src: sqlite3.Connection,
    dst: "psycopg.Connection",
    start: int,
    end: int,
    source_name: str,
) -> tuple[int, int]:
    if source_has_gamebook_dates(src):
        games = src.execute(
            """
            SELECT a.game_id, a.season, a.week, g.gameday, COUNT(*)
              FROM nfl_player_game_snap_appearances a
              JOIN nfl_gamebook_games g ON g.game_id = a.game_id
             WHERE a.season BETWEEN ? AND ?
             GROUP BY a.game_id, a.season, a.week, g.gameday
             ORDER BY a.season, a.week, a.game_id
            """,
            (start, end),
        ).fetchall()
        appearances = src.execute(
            """
            SELECT a.game_id, a.season, a.week, g.gameday, a.team_id, a.player_id, a.position
              FROM nfl_player_game_snap_appearances a
              JOIN nfl_gamebook_games g ON g.game_id = a.game_id
             WHERE a.season BETWEEN ? AND ?
             ORDER BY a.season, a.week, a.game_id, a.team_id, a.player_id
            """,
            (start, end),
        ).fetchall()
    else:
        games = src.execute(
            """
            SELECT game_id, season, week, NULL, COUNT(*)
              FROM nfl_player_game_snap_appearances
             WHERE season BETWEEN ? AND ?
             GROUP BY game_id, season, week
             ORDER BY season, week, game_id
            """,
            (start, end),
        ).fetchall()
        appearances = src.execute(
            """
            SELECT game_id, season, week, NULL, team_id, player_id, position
              FROM nfl_player_game_snap_appearances
             WHERE season BETWEEN ? AND ?
             ORDER BY season, week, game_id, team_id, player_id
            """,
            (start, end),
        ).fetchall()
    with dst.cursor() as cur:
        cur.execute("DELETE FROM sport_live_player_games WHERE sport_id = %s AND season BETWEEN %s AND %s", (SPORT_ID, start, end))
        cur.execute("DELETE FROM sport_live_game_imports WHERE sport_id = %s AND season BETWEEN %s AND %s", (SPORT_ID, start, end))
        cur.executemany(
            """
            INSERT INTO sport_live_game_imports
                (sport_id, game_id, game_date, season, status, source, row_count, imported_at)
            VALUES (%s, %s, COALESCE(%s::date, %s::date + ((%s - 1) * interval '7 days')), %s, 'Final', %s, %s, now())
            """,
            [
                (SPORT_ID, game_id, gameday, f"{season}-09-01", week, season, source_name, count)
                for game_id, season, week, gameday, count in games
            ],
        )
        cur.executemany(
            """
            INSERT INTO sport_live_player_games
                (sport_id, game_id, game_date, season, player_id, team_id,
                 position, games_total, goals, assists, points)
            VALUES (%s, %s, COALESCE(%s::date, %s::date + ((%s - 1) * interval '7 days')), %s, %s, %s, %s, 1, 0, 0, 0)
            """,
            [
                (SPORT_ID, game_id, gameday, f"{season}-09-01", week, season, player_id, team, pos)
                for game_id, season, week, gameday, team, player_id, pos in appearances
            ],
        )
    return len(games), len(appearances)


def copy_teammate_proofs(src: sqlite3.Connection, dst: "psycopg.Connection", start: int, end: int) -> int:
    query = """
        SELECT a.sport_id, a.player_a_id, a.player_b_id, a.team_id, a.season
          FROM (
                SELECT 'football' AS sport_id,
                       MIN(a.player_id, b.player_id) AS player_a_id,
                       MAX(a.player_id, b.player_id) AS player_b_id,
                       a.team_id,
                       a.season
                  FROM nfl_player_game_snap_appearances a
                  JOIN nfl_player_game_snap_appearances b
                    ON b.game_id = a.game_id
                   AND b.team_id = a.team_id
                   AND b.player_id > a.player_id
                 WHERE a.season BETWEEN ? AND ?
                 GROUP BY a.season, a.team_id,
                          MIN(a.player_id, b.player_id),
                          MAX(a.player_id, b.player_id)
               ) a
         ORDER BY a.season, a.team_id, a.player_a_id, a.player_b_id
    """
    with dst.cursor() as cur:
        cur.execute("DELETE FROM sport_teammates WHERE sport_id = %s AND season BETWEEN %s AND %s", (SPORT_ID, start, end))
        count = 0
        with cur.copy(
            "COPY sport_teammates (sport_id, player_a_id, player_b_id, team_id, season) FROM STDIN"
        ) as copy:
            for row in src.execute(query, (start, end)):
                copy.write_row(row)
                count += 1
    return count


def prune_live_rows(dst: "psycopg.Connection", start: int, end: int) -> tuple[int, int]:
    with dst.cursor() as cur:
        cur.execute(
            """
            DELETE FROM sport_live_player_games live
             WHERE live.sport_id = %s
               AND live.season BETWEEN %s AND %s
               AND EXISTS (
                   SELECT 1
                     FROM sport_teammate_stint_coverage c
                    WHERE c.sport_id = live.sport_id
                      AND c.season = live.season
                      AND c.strict <> 0
                      AND c.coverage_type = 'game_boxscore'
               )
               AND EXISTS (
                   SELECT 1
                     FROM sport_teammates t
                    WHERE t.sport_id = live.sport_id
                      AND t.season = live.season
               )
            """,
            (SPORT_ID, start, end),
        )
        player_rows = cur.rowcount
        cur.execute(
            """
            DELETE FROM sport_live_game_imports game
             WHERE game.sport_id = %s
               AND game.season BETWEEN %s AND %s
               AND NOT EXISTS (
                   SELECT 1
                     FROM sport_live_player_games live
                    WHERE live.sport_id = game.sport_id
                      AND live.game_id = game.game_id
               )
            """,
            (SPORT_ID, start, end),
        )
        game_rows = cur.rowcount
    return int(player_rows), int(game_rows)


def refresh_runtime(dst: "psycopg.Connection", start: int, end: int, source_name: str, source_url: str) -> None:
    with dst.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '20min'")
        cur.execute("DELETE FROM sport_appearances WHERE sport_id = %s AND season BETWEEN %s AND %s", (SPORT_ID, start, end))
        cur.execute(
            """
            INSERT INTO sport_appearances (sport_id, player_id, team_id, season, games_total)
            SELECT sport_id, player_id, team_id, season, COUNT(DISTINCT game_id)::integer
              FROM sport_live_player_games
             WHERE sport_id = %s AND season BETWEEN %s AND %s
             GROUP BY sport_id, player_id, team_id, season
            ON CONFLICT (sport_id, player_id, team_id, season) DO UPDATE
            SET games_total = EXCLUDED.games_total
            """,
            (SPORT_ID, start, end),
        )
        cur.execute("DELETE FROM sport_player_stints WHERE sport_id = %s AND season BETWEEN %s AND %s", (SPORT_ID, start, end))
        cur.execute(
            """
            INSERT INTO sport_player_stints
                (sport_id, player_id, team_id, season, first_unit, last_unit,
                 first_label, last_label, source)
            SELECT sport_id, player_id, team_id, season,
                   MIN(EXTRACT(WEEK FROM game_date)::integer),
                   MAX(EXTRACT(WEEK FROM game_date)::integer),
                   MIN(game_date)::text,
                   MAX(game_date)::text,
                   %s
              FROM sport_live_player_games
             WHERE sport_id = %s AND season BETWEEN %s AND %s
             GROUP BY sport_id, player_id, team_id, season
            ON CONFLICT (sport_id, player_id, team_id, season) DO UPDATE
            SET first_unit = EXCLUDED.first_unit,
                last_unit = EXCLUDED.last_unit,
                first_label = EXCLUDED.first_label,
                last_label = EXCLUDED.last_label,
                source = EXCLUDED.source
            """,
            (source_name, SPORT_ID, start, end),
        )
        cur.execute(
            """
            INSERT INTO sport_player_positions (sport_id, player_id, position, games)
            SELECT sport_id, player_id, COALESCE(position, 'UNK'), COUNT(*)::integer
              FROM sport_live_player_games
             WHERE sport_id = %s AND season BETWEEN %s AND %s
             GROUP BY sport_id, player_id, COALESCE(position, 'UNK')
            ON CONFLICT (sport_id, player_id, position) DO UPDATE
            SET games = EXCLUDED.games
            """,
            (SPORT_ID, start, end),
        )
        cur.execute(
            """
            WITH span AS (
                SELECT player_id, MIN(season) AS debut_year, MAX(season) AS final_year
                  FROM sport_appearances
                 WHERE sport_id = %s
                 GROUP BY player_id
            )
            UPDATE sport_players p
               SET debut_year = COALESCE(span.debut_year, p.debut_year),
                   final_year = COALESCE(span.final_year, p.final_year)
              FROM span
             WHERE p.sport_id = %s AND p.player_id = span.player_id
            """,
            (SPORT_ID, SPORT_ID),
        )
        cur.execute(
            """
            WITH careers AS (
                SELECT player_id, SUM(games_total)::integer AS career_games
                  FROM sport_appearances
                 WHERE sport_id = %s
                 GROUP BY player_id
            )
            UPDATE sport_players_searchable s
               SET career_games = COALESCE(c.career_games, 0),
                   disambiguation = COALESCE(NULLIF(p.primary_pos, ''), 'NFL') || ', '
                                    || COALESCE(p.debut_year::text, '?') || '-'
                                    || COALESCE(p.final_year::text, '?')
              FROM sport_players p
              LEFT JOIN careers c ON c.player_id = p.player_id
             WHERE s.sport_id = %s
               AND p.sport_id = s.sport_id
               AND p.player_id = s.player_id
            """,
            (SPORT_ID, SPORT_ID),
        )
        cur.execute(
            """
            INSERT INTO sport_teammate_stint_coverage
                (sport_id, season, coverage_type, strict, source, updated_at)
            SELECT %s, generate_series(%s::integer, %s::integer), 'game_boxscore', 1, %s, now()
            ON CONFLICT (sport_id, season) DO UPDATE
            SET coverage_type = EXCLUDED.coverage_type,
                strict = EXCLUDED.strict,
                source = EXCLUDED.source,
                updated_at = now()
            """,
            (SPORT_ID, start, end, source_name),
        )
        cur.execute(
            """
            INSERT INTO sport_data_provenance (sport_id, source, season, source_url, row_count)
            SELECT %s, %s, season, %s, COUNT(*)::integer
              FROM sport_live_player_games
             WHERE sport_id = %s AND season BETWEEN %s AND %s
             GROUP BY season
            ON CONFLICT (sport_id, source, season) DO UPDATE
            SET source_url = EXCLUDED.source_url,
                row_count = EXCLUDED.row_count
            """,
            (SPORT_ID, source_name, source_url, SPORT_ID, start, end),
        )


def verify(dst: "psycopg.Connection", start: int, end: int) -> dict[str, int]:
    with dst.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM sport_live_game_imports WHERE sport_id = %s AND season BETWEEN %s AND %s", (SPORT_ID, start, end))
        games = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM sport_live_player_games WHERE sport_id = %s AND season BETWEEN %s AND %s", (SPORT_ID, start, end))
        appearances = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM sport_appearances WHERE sport_id = %s AND season BETWEEN %s AND %s", (SPORT_ID, start, end))
        rollups = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM sport_teammate_stint_coverage WHERE sport_id = %s AND coverage_type='game_boxscore' AND season BETWEEN %s AND %s", (SPORT_ID, start, end))
        coverage = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM sport_teammates WHERE sport_id = %s AND season BETWEEN %s AND %s", (SPORT_ID, start, end))
        teammates = cur.fetchone()[0]
    return {"games": games, "appearances": appearances, "rollups": rollups, "coverage": coverage, "teammates": teammates}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--season-start", type=int)
    parser.add_argument("--season-end", type=int)
    parser.add_argument("--source-name", default=SOURCE_NAME)
    parser.add_argument("--source-url", default=SOURCE_URL)
    parser.add_argument("--proofs-only", action="store_true")
    parser.add_argument(
        "--prune-live-after-refresh",
        action="store_true",
        help="Delete imported live player-game staging rows after compact proofs and rollups are refreshed.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    src = sqlite3.connect(args.source)
    summary = source_summary(src)
    start = args.season_start or int(summary["season_start"] or 0)
    end = args.season_end or int(summary["season_end"] or 0)
    print(
        f"source seasons {summary['season_start']}-{summary['season_end']}: "
        f"{summary['games']:,} games; {summary['appearances']:,} snap appearances; "
        f"{summary['players']:,} players"
    )
    if args.dry_run:
        print(f"dry run: would import {start}-{end}")
        return 0

    with connect() as dst:
        ensure_tables(dst)
        if args.proofs_only:
            teammates = copy_teammate_proofs(src, dst, start, end)
            checks = verify(dst, start, end)
            dst.commit()
            print(f"imported {teammates:,} teammate proofs")
            print(
                f"verified production: {checks['games']:,} games; {checks['appearances']:,} player-games; "
                f"{checks['rollups']:,} player-team-season rollups; {checks['teammates']:,} teammate proofs; "
                f"{checks['coverage']} strict seasons"
            )
            return 0
        team_count = copy_teams(src, dst, start, end)
        player_count = copy_players(src, dst)
        games, appearances = copy_games_and_appearances(src, dst, start, end, args.source_name)
        teammates = copy_teammate_proofs(src, dst, start, end)
        refresh_runtime(dst, start, end, args.source_name, args.source_url)
        pruned_players = pruned_games = 0
        if args.prune_live_after_refresh:
            pruned_players, pruned_games = prune_live_rows(dst, start, end)
        checks = verify(dst, start, end)
        dst.commit()
    print(
        f"imported {team_count:,} team-seasons; {player_count:,} players; {games:,} games; "
        f"{appearances:,} snap appearances; {teammates:,} teammate proofs"
    )
    if args.prune_live_after_refresh:
        print(f"pruned {pruned_players:,} live player-game rows and {pruned_games:,} game-import rows")
    print(
        f"verified production: {checks['games']:,} games; {checks['appearances']:,} player-games; "
        f"{checks['rollups']:,} player-team-season rollups; {checks['teammates']:,} teammate proofs; "
        f"{checks['coverage']} strict seasons"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
