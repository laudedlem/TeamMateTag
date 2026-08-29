#!/usr/bin/env python3
"""Replace Supabase derived runtime data from the compact local SQLite build."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNTIME = ROOT / "raw" / "runtime_compact" / "teammatetag_runtime_minimal.sqlite"
HEADSHOT_BUCKET = "player-headshots"

TRUNCATE_TABLES = [
    "compact_sport_teammates",
    "compact_mlb_teammate_game_proofs",
    "compact_player_keys",
    "compact_team_keys",
    "player_headshots",
    "sport_player_images",
    "sport_players_searchable",
    "sport_player_traits",
    "sport_player_season_traits",
    "sport_player_positions",
    "sport_player_stints",
    "sport_appearances",
    "sport_teammate_stint_coverage",
    "baseball_player_positions",
    "player_powerup_qualifications",
    "player_playoff_traits",
    "players_searchable",
    "player_stints",
    "appearances",
    "teammate_stint_coverage",
]


def db_url() -> str:
    if load_dotenv:
        load_dotenv(ROOT / ".env")
    url = os.environ.get("DIRECT_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("ERROR: DIRECT_URL or DATABASE_URL is required in .env")
    return url


def supabase_url() -> str:
    if load_dotenv:
        load_dotenv(ROOT / ".env")
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or ""
    if not url:
        raise SystemExit("ERROR: SUPABASE_URL is required in .env")
    return url.rstrip("/")


def table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def public_storage_url(base_url: str, sport: str, player_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "__" for ch in player_id)
    object_path = f"{sport}/{safe}.webp"
    return f"{base_url}/storage/v1/object/public/{HEADSHOT_BUCKET}/{quote(object_path, safe='/')}"


def copy_rows(cur: "psycopg.Cursor", table: str, columns: list[str], rows) -> int:
    count = 0
    col_sql = ", ".join(columns)
    with cur.copy(f"COPY {table} ({col_sql}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)
            count += 1
    print(f"loaded {table}: {count:,}", flush=True)
    return count


def upsert_rows(cur: "psycopg.Cursor", sql: str, rows) -> int:
    data = list(rows)
    if data:
        cur.executemany(sql, data)
    print(f"upserted rows: {len(data):,}", flush=True)
    return len(data)


def rows_from_sqlite(conn: sqlite3.Connection, sql: str, params: tuple = ()):
    yield from conn.execute(sql, params)


def transformed_headshot_rows(conn: sqlite3.Connection, base_url: str):
    for row in conn.execute(
        """
        SELECT scope, player_id, provider, status
          FROM runtime_headshots
         WHERE status = 'verified'
         ORDER BY scope, player_id
        """
    ):
        sport, player_id, provider, status = row
        url = public_storage_url(base_url, sport, player_id)
        yield (sport, player_id, url, None, provider, status, None, None, None, None, "Verified local canonical file-storage headshot.")


def transformed_sport_images(conn: sqlite3.Connection, base_url: str):
    for sport, player_id in conn.execute(
        """
        SELECT scope, player_id
          FROM runtime_headshots
         WHERE scope <> 'baseball' AND status = 'verified'
         ORDER BY scope, player_id
        """
    ):
        yield (sport, player_id, public_storage_url(base_url, sport, player_id), "image/webp")


def load_all(pg: "psycopg.Connection", src: sqlite3.Connection, base_url: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    with pg.cursor() as cur:
        cur.execute("SET statement_timeout = '30min'")
        cur.execute("SET lock_timeout = '30s'")
        cur.execute("TRUNCATE TABLE " + ", ".join(TRUNCATE_TABLES))
        cur.execute("DELETE FROM sport_players WHERE sport_id IN ('basketball', 'hockey', 'football')")
        cur.execute("DELETE FROM sport_teams WHERE sport_id IN ('basketball', 'hockey', 'football')")
        pg.commit()
        print("cleared derived runtime tables and cross-sport catalog rows", flush=True)

        counts["sports"] = upsert_rows(
            cur,
            """
            INSERT INTO sports (sport_id, display_name, league_name, active, first_season, last_season)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (sport_id) DO UPDATE SET
                display_name=EXCLUDED.display_name,
                league_name=EXCLUDED.league_name,
                active=EXCLUDED.active,
                first_season=EXCLUDED.first_season,
                last_season=EXCLUDED.last_season
            """,
            ((sport, display, league, True, first, last) for sport, display, league, first, last in src.execute("SELECT * FROM sports ORDER BY sport_id")),
        )
        counts["teams"] = upsert_rows(
            cur,
            """
            INSERT INTO teams (team_id, season, franchise_id, league, name)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (team_id, season) DO UPDATE SET
                franchise_id=EXCLUDED.franchise_id,
                league=EXCLUDED.league,
                name=EXCLUDED.name
            """,
            rows_from_sqlite(src, "SELECT * FROM teams ORDER BY season, team_id"),
        )
        counts["players"] = upsert_rows(
            cur,
            """
            INSERT INTO players (
                player_id, bbref_id, retro_id, mlbam_id, name_first, name_last,
                name_given, birth_year, debut_year, final_year, bats, throws,
                primary_pos, name_nick
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (player_id) DO UPDATE SET
                bbref_id=EXCLUDED.bbref_id,
                retro_id=EXCLUDED.retro_id,
                mlbam_id=EXCLUDED.mlbam_id,
                name_first=EXCLUDED.name_first,
                name_last=EXCLUDED.name_last,
                name_given=EXCLUDED.name_given,
                birth_year=EXCLUDED.birth_year,
                debut_year=EXCLUDED.debut_year,
                final_year=EXCLUDED.final_year,
                bats=EXCLUDED.bats,
                throws=EXCLUDED.throws,
                primary_pos=EXCLUDED.primary_pos,
                name_nick=EXCLUDED.name_nick
            """,
            rows_from_sqlite(src, "SELECT * FROM players ORDER BY player_id"),
        )
        counts["appearances"] = copy_rows(cur, "appearances", ["player_id", "team_id", "season", "games_total", "games_pitched", "games_batted"], rows_from_sqlite(src, "SELECT * FROM appearances ORDER BY season, team_id, player_id"))
        counts["player_stints"] = copy_rows(cur, "player_stints", ["player_id", "team_id", "season", "first_unit", "last_unit", "first_label", "last_label", "source"], rows_from_sqlite(src, "SELECT * FROM player_stints ORDER BY season, team_id, player_id"))
        counts["players_searchable"] = copy_rows(
            cur,
            "players_searchable",
            ["player_id", "display_name", "disambiguation", "search_key", "last_key", "career_games", "teammate_count"],
            rows_from_sqlite(
                src,
                """
                SELECT player_id, display_name, COALESCE(disambiguation, ''),
                       search_key, last_key, career_games, teammate_count
                  FROM players_searchable
                 ORDER BY player_id
                """,
            ),
        )
        counts["player_playoff_traits"] = copy_rows(cur, "player_playoff_traits", ["player_id", "birth_country", "is_japanese", "is_cuban", "is_canadian", "mvp_count", "roty_count", "gold_glove_count", "triple_crown_count", "career_hr", "world_series_rings", "team_count", "franchise_count", "season_count", "hound_dog_eligible", "journeyman_eligible"], rows_from_sqlite(src, "SELECT * FROM player_playoff_traits ORDER BY player_id"))
        counts["player_powerup_qualifications"] = copy_rows(cur, "player_powerup_qualifications", ["powerup_key", "franchise_id", "team_id", "season", "player_id"], rows_from_sqlite(src, "SELECT powerup_key, franchise_id, team_id, season, player_id FROM player_powerup_qualifications ORDER BY powerup_key, franchise_id, team_id, season, player_id"))
        counts["baseball_player_positions"] = copy_rows(
            cur,
            "baseball_player_positions",
            ["player_id", "position", "games"],
            rows_from_sqlite(
                src,
                """
                SELECT b.player_id, b.position, b.games
                  FROM baseball_player_positions b
                  JOIN players p ON p.player_id=b.player_id
                 WHERE b.sport_id='baseball'
                 ORDER BY b.player_id, b.position
                """,
            ),
        )

        counts["sport_teams"] = copy_rows(cur, "sport_teams", ["sport_id", "team_id", "season", "franchise_id", "name"], rows_from_sqlite(src, "SELECT sport_id, team_id, season, franchise_id, name FROM sport_teams ORDER BY sport_id, season, team_id"))
        counts["sport_players"] = copy_rows(cur, "sport_players", ["sport_id", "player_id", "external_id", "display_name", "first_name", "last_name", "birth_year", "debut_year", "final_year", "primary_pos"], rows_from_sqlite(src, "SELECT * FROM sport_players ORDER BY sport_id, player_id"))
        counts["sport_appearances"] = copy_rows(cur, "sport_appearances", ["sport_id", "player_id", "team_id", "season", "games_total"], rows_from_sqlite(src, "SELECT * FROM sport_appearances ORDER BY sport_id, season, team_id, player_id"))
        counts["sport_player_stints"] = copy_rows(cur, "sport_player_stints", ["sport_id", "player_id", "team_id", "season", "first_unit", "last_unit", "first_label", "last_label", "source"], rows_from_sqlite(src, "SELECT * FROM sport_player_stints ORDER BY sport_id, season, team_id, player_id"))
        counts["sport_player_positions"] = copy_rows(
            cur,
            "sport_player_positions",
            ["sport_id", "player_id", "position", "games"],
            rows_from_sqlite(
                src,
                """
                SELECT x.sport_id, x.player_id, x.position, x.games
                  FROM sport_player_positions x
                  JOIN sport_players p ON p.sport_id=x.sport_id AND p.player_id=x.player_id
                 ORDER BY x.sport_id, x.player_id, x.position
                """,
            ),
        )
        counts["sport_player_season_traits"] = copy_rows(
            cur,
            "sport_player_season_traits",
            ["sport_id", "player_id", "season", "games", "points", "goals", "assists", "touchdowns", "passing_touchdowns", "rushing_touchdowns", "receiving_touchdowns", "sacks", "interceptions", "source"],
            rows_from_sqlite(
                src,
                """
                SELECT x.sport_id, x.player_id, x.season, x.games, x.points,
                       x.goals, x.assists, x.touchdowns, x.passing_touchdowns,
                       x.rushing_touchdowns, x.receiving_touchdowns, x.sacks,
                       x.interceptions, x.source
                  FROM sport_player_season_traits x
                  JOIN sport_players p ON p.sport_id=x.sport_id AND p.player_id=x.player_id
                 ORDER BY x.sport_id, x.season, x.player_id
                """,
            ),
        )
        counts["sport_player_traits"] = copy_rows(
            cur,
            "sport_player_traits",
            ["sport_id", "player_id", "career_games", "career_points", "career_goals", "career_assists", "career_touchdowns", "passing_touchdowns", "rushing_touchdowns", "receiving_touchdowns", "career_sacks", "career_interceptions", "all_star_count", "mvp_count", "roty_count", "championship_count", "source", "updated_at"],
            rows_from_sqlite(
                src,
                """
                SELECT x.sport_id, x.player_id, x.career_games,
                       x.career_points, x.career_goals, x.career_assists,
                       x.career_touchdowns, x.passing_touchdowns,
                       x.rushing_touchdowns, x.receiving_touchdowns,
                       x.career_sacks, x.career_interceptions,
                       x.all_star_count, x.mvp_count, x.roty_count,
                       x.championship_count, x.source, x.updated_at
                  FROM sport_player_traits x
                  JOIN sport_players p ON p.sport_id=x.sport_id AND p.player_id=x.player_id
                 ORDER BY x.sport_id, x.player_id
                """,
            ),
        )
        counts["sport_players_searchable"] = copy_rows(
            cur,
            "sport_players_searchable",
            ["sport_id", "player_id", "display_name", "disambiguation", "search_key", "last_key", "career_games", "teammate_count"],
            rows_from_sqlite(
                src,
                """
                SELECT s.sport_id, s.player_id, s.display_name, COALESCE(s.disambiguation, ''),
                       s.search_key, s.last_key,
                       COALESCE(NULLIF(t.career_games, 0), s.career_games),
                       s.teammate_count
                  FROM sport_players_searchable s
                  LEFT JOIN sport_player_traits t
                    ON t.sport_id=s.sport_id AND t.player_id=s.player_id
                 ORDER BY s.sport_id, s.player_id
                """,
            ),
        )
        counts["sport_player_images"] = copy_rows(cur, "sport_player_images", ["sport_id", "player_id", "source_url", "content_type"], transformed_sport_images(src, base_url))
        counts["player_headshots"] = copy_rows(cur, "player_headshots", ["sport_id", "player_id", "source_url", "fallback_url", "provider", "status", "content_sha256", "perceptual_hash", "width", "height", "review_note"], transformed_headshot_rows(src, base_url))
        counts["teammate_stint_coverage"] = copy_rows(cur, "teammate_stint_coverage", ["season", "coverage_type", "strict", "source", "updated_at"], rows_from_sqlite(src, "SELECT season, coverage_type, strict, source, updated_at FROM teammate_stint_coverage ORDER BY season"))
        counts["sport_teammate_stint_coverage"] = copy_rows(cur, "sport_teammate_stint_coverage", ["sport_id", "season", "coverage_type", "strict", "source", "updated_at"], rows_from_sqlite(src, "SELECT sport_id, season, coverage_type, strict, source, updated_at FROM sport_teammate_stint_coverage ORDER BY sport_id, season"))
        counts["compact_player_keys"] = copy_rows(cur, "compact_player_keys", ["player_key", "scope", "player_id"], rows_from_sqlite(src, "SELECT player_key, scope, player_id FROM compact_player_keys ORDER BY player_key"))
        counts["compact_team_keys"] = copy_rows(cur, "compact_team_keys", ["team_key", "scope", "team_id", "season"], rows_from_sqlite(src, "SELECT team_key, scope, team_id, season FROM compact_team_keys ORDER BY team_key"))
        counts["compact_mlb_teammate_game_proofs"] = copy_rows(
            cur,
            "compact_mlb_teammate_game_proofs",
            ["player_a_key", "player_b_key", "team_key", "season", "shared_games", "first_game_pk", "first_game_date"],
            rows_from_sqlite(
                src,
                """
                SELECT t.player_a_key, t.player_b_key, t.team_key, k.season, 1, 0,
                       printf('%04d-01-01', k.season)
                  FROM teammate_team_seasons t
                  JOIN compact_team_keys k ON k.team_key=t.team_key
                 WHERE t.scope='baseball'
                 ORDER BY t.player_a_key, t.player_b_key, t.team_key
                """,
            ),
        )
        counts["compact_sport_teammates"] = copy_rows(
            cur,
            "compact_sport_teammates",
            ["sport_id", "player_a_key", "player_b_key", "team_key", "season"],
            rows_from_sqlite(
                src,
                """
                SELECT t.scope, t.player_a_key, t.player_b_key, t.team_key, k.season
                  FROM teammate_team_seasons t
                  JOIN compact_team_keys k ON k.team_key=t.team_key
                 WHERE t.scope<>'baseball'
                 ORDER BY t.scope, t.player_a_key, t.player_b_key, t.team_key
                """,
            ),
        )
        pg.commit()

        for table in [*TRUNCATE_TABLES, "sport_players", "sport_teams", "players", "teams", "sports"]:
            cur.execute(f"ANALYZE {table}")
        pg.commit()
    return counts


def remote_size(pg: "psycopg.Connection") -> tuple[str, int]:
    with pg.cursor() as cur:
        cur.execute("SELECT pg_size_pretty(pg_database_size(current_database())), pg_database_size(current_database())")
        pretty, bytes_ = cur.fetchone()
    return str(pretty), int(bytes_)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-db", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if psycopg is None:
        raise SystemExit("ERROR: install psycopg first: pip install 'psycopg[binary]'")
    runtime = args.runtime_db.resolve()
    if not runtime.exists():
        raise SystemExit(f"ERROR: missing runtime DB: {runtime}")
    base_url = supabase_url()
    with sqlite3.connect(runtime) as src:
        local_counts = {
            "runtime_players": table_count(src, "runtime_players"),
            "runtime_teams": table_count(src, "runtime_teams"),
            "runtime_player_team_seasons": table_count(src, "runtime_player_team_seasons"),
            "teammate_team_seasons": table_count(src, "teammate_team_seasons"),
            "runtime_headshots": table_count(src, "runtime_headshots"),
        }
        print("local runtime counts:", local_counts)
        if not args.execute:
            print("dry run only; pass --execute to replace Supabase runtime data")
            return 0
        with psycopg.connect(db_url(), autocommit=False, prepare_threshold=None) as pg:
            before = remote_size(pg)
            print(f"Supabase size before: {before[0]}")
            counts = load_all(pg, src, base_url)
            after = remote_size(pg)
            print("loaded counts:", counts)
            print(f"Supabase size after: {after[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
