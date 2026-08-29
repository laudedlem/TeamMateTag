#!/usr/bin/env python3
"""Build and publish compact NFL live-season runtime data.

The nflverse snap CSV is downloaded into raw/ by build_nfl_snap_teammates.py.
This script then compiles a season-scoped compact SQLite runtime and uploads
only rollups plus compact same-game teammate proofs to Supabase.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from pathlib import Path
from urllib.request import urlopen

try:
    import psycopg
except ImportError:
    psycopg = None

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from build_nfl_compact_runtime import first_name, last_name, nfl_team, normalize  # noqa: E402
from build_nfl_snap_teammates import main as build_snap_source  # noqa: E402


SPORT_ID = "football"
SOURCE_NAME = "nflverse_snap_counts"
RELEASE_API = "https://api.github.com/repos/nflverse/nflverse-data/releases/tags/snap_counts"
LIVE_SOURCE = ROOT / "raw" / "nfl_game_teammates" / "nfl_snap_teammates_live.sqlite"
LIVE_RUNTIME_DIR = ROOT / "raw" / "football_live_runtime"


def default_nfl_season(today: dt.date | None = None) -> int:
    today = today or dt.date.today()
    return today.year if today.month >= 8 else today.year - 1


def snap_asset_available(season: int) -> tuple[bool, int]:
    with urlopen(RELEASE_API, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))
    asset_name = f"snap_counts_{season}.csv"
    for asset in data.get("assets", []):
        if asset.get("name") == asset_name:
            size = int(asset.get("size") or 0)
            return size > 1000, size
    return False, 0


def runtime_path(season: int) -> Path:
    return LIVE_RUNTIME_DIR / f"football_live_{season}.sqlite"


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA synchronous = OFF;
        PRAGMA temp_store = MEMORY;

        CREATE TABLE sport_teams (
            sport_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            franchise_id TEXT NOT NULL,
            name TEXT NOT NULL,
            PRIMARY KEY (sport_id, team_id, season)
        ) WITHOUT ROWID;
        CREATE TABLE sport_players (
            sport_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            external_id TEXT,
            display_name TEXT NOT NULL,
            first_name TEXT,
            last_name TEXT,
            birth_year INTEGER,
            debut_year INTEGER,
            final_year INTEGER,
            primary_pos TEXT,
            PRIMARY KEY (sport_id, player_id)
        ) WITHOUT ROWID;
        CREATE TABLE sport_appearances (
            sport_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            games_total INTEGER NOT NULL,
            PRIMARY KEY (sport_id, player_id, team_id, season)
        ) WITHOUT ROWID;
        CREATE TABLE sport_player_stints (
            sport_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            first_unit INTEGER NOT NULL,
            last_unit INTEGER NOT NULL,
            first_label TEXT,
            last_label TEXT,
            source TEXT,
            PRIMARY KEY (sport_id, player_id, team_id, season)
        ) WITHOUT ROWID;
        CREATE TABLE sport_player_positions (
            sport_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            position TEXT NOT NULL,
            games INTEGER NOT NULL,
            PRIMARY KEY (sport_id, player_id, position)
        ) WITHOUT ROWID;
        CREATE TABLE sport_teammate_stint_coverage (
            sport_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            coverage_type TEXT NOT NULL,
            strict INTEGER NOT NULL DEFAULT 1,
            source TEXT,
            PRIMARY KEY (sport_id, season)
        ) WITHOUT ROWID;
        CREATE TABLE sport_players_searchable (
            sport_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            disambiguation TEXT NOT NULL,
            search_key TEXT NOT NULL,
            last_key TEXT NOT NULL,
            career_games INTEGER NOT NULL DEFAULT 0,
            teammate_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (sport_id, player_id)
        ) WITHOUT ROWID;
        CREATE TABLE sport_player_images (
            sport_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            source_url TEXT NOT NULL,
            content_type TEXT,
            PRIMARY KEY (sport_id, player_id)
        ) WITHOUT ROWID;
        CREATE TABLE compact_player_keys (
            player_key INTEGER PRIMARY KEY,
            player_id TEXT NOT NULL UNIQUE
        );
        CREATE TABLE compact_team_keys (
            team_key INTEGER PRIMARY KEY,
            team_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            UNIQUE (team_id, season)
        );
        CREATE TABLE compact_sport_teammates (
            player_a_key INTEGER NOT NULL,
            player_b_key INTEGER NOT NULL,
            team_key INTEGER NOT NULL,
            season INTEGER NOT NULL,
            PRIMARY KEY (player_a_key, player_b_key, team_key, season)
        ) WITHOUT ROWID;
        CREATE VIEW sport_teammates AS
        SELECT 'football' AS sport_id,
               pa.player_id AS player_a_id,
               pb.player_id AS player_b_id,
               tk.team_id,
               c.season
          FROM compact_sport_teammates c
          JOIN compact_player_keys pa ON pa.player_key = c.player_a_key
          JOIN compact_player_keys pb ON pb.player_key = c.player_b_key
          JOIN compact_team_keys tk ON tk.team_key = c.team_key;
        """
    )


def build_local_runtime(season: int, output: Path) -> dict[str, int]:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    with sqlite3.connect(output) as conn:
        conn.execute("ATTACH DATABASE ? AS src", (str(LIVE_SOURCE),))
        create_schema(conn)
        teams = {
            (team_id, season, *nfl_team(team_id, int(season)))
            for team_id, season in conn.execute(
                "SELECT DISTINCT team_id, season FROM src.nfl_player_game_snap_appearances"
            )
        }
        conn.executemany(
            "INSERT OR IGNORE INTO sport_teams VALUES (?, ?, ?, ?, ?)",
            [(SPORT_ID, team, yr, franchise, name) for team, yr, franchise, name in sorted(teams)],
        )
        conn.executemany(
            "INSERT OR IGNORE INTO sport_players VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    SPORT_ID,
                    player_id,
                    pfr_id or None,
                    name,
                    first or first_name(name),
                    last or last_name(name),
                    birth,
                    season,
                    season,
                    pos,
                )
                for player_id, pfr_id, name, first, last, birth, pos in conn.execute(
                    """
                    SELECT player_id, pfr_player_id, display_name, first_name,
                           last_name, birth_year, primary_pos
                      FROM src.nfl_snap_players
                    """
                )
            ],
        )
        conn.execute(
            """
            INSERT INTO sport_appearances
            SELECT ?, player_id, team_id, season, COUNT(DISTINCT game_id)
              FROM src.nfl_player_game_snap_appearances
             GROUP BY player_id, team_id, season
            """,
            (SPORT_ID,),
        )
        conn.execute(
            """
            INSERT INTO sport_player_stints
            SELECT ?, player_id, team_id, season, MIN(week), MAX(week),
                   'Week ' || MIN(week), 'Week ' || MAX(week), ?
              FROM src.nfl_player_game_snap_appearances
             GROUP BY player_id, team_id, season
            """,
            (SPORT_ID, SOURCE_NAME),
        )
        conn.execute(
            """
            INSERT INTO sport_player_positions
            SELECT ?, player_id, COALESCE(NULLIF(position, ''), 'NFL'),
                   COUNT(DISTINCT game_id)
              FROM src.nfl_player_game_snap_appearances
             GROUP BY player_id, COALESCE(NULLIF(position, ''), 'NFL')
            """,
            (SPORT_ID,),
        )
        conn.execute("INSERT INTO sport_teammate_stint_coverage VALUES (?, ?, 'game_boxscore', 1, ?)", (SPORT_ID, season, SOURCE_NAME))
        conn.execute(
            """
            INSERT INTO compact_player_keys (player_id)
            SELECT DISTINCT player_id
              FROM src.nfl_player_game_snap_appearances
             ORDER BY player_id
            """
        )
        conn.execute(
            """
            INSERT INTO compact_team_keys (team_id, season)
            SELECT DISTINCT team_id, season
              FROM src.nfl_player_game_snap_appearances
             ORDER BY season, team_id
            """
        )
        conn.execute(
            """
            INSERT INTO compact_sport_teammates
            SELECT pa.player_key, pb.player_key, tk.team_key, a.season
              FROM src.nfl_player_game_snap_appearances a
              JOIN src.nfl_player_game_snap_appearances b
                ON b.game_id = a.game_id
               AND b.team_id = a.team_id
               AND b.player_id > a.player_id
              JOIN compact_player_keys pa ON pa.player_id = a.player_id
              JOIN compact_player_keys pb ON pb.player_id = b.player_id
              JOIN compact_team_keys tk ON tk.team_id = a.team_id AND tk.season = a.season
             GROUP BY pa.player_key, pb.player_key, tk.team_key, a.season
            """
        )
        teammate_counts: dict[str, int] = defaultdict(int)
        for a, b in conn.execute("SELECT player_a_id, player_b_id FROM sport_teammates"):
            teammate_counts[a] += 1
            teammate_counts[b] += 1
        rows = []
        for player_id, name, last, pos, games in conn.execute(
            """
            SELECT p.player_id, p.display_name, p.last_name, p.primary_pos,
                   COALESCE(SUM(a.games_total), 0)
              FROM sport_players p
              LEFT JOIN sport_appearances a ON a.sport_id=p.sport_id AND a.player_id=p.player_id
             GROUP BY p.player_id, p.display_name, p.last_name, p.primary_pos
            """
        ):
            rows.append((SPORT_ID, player_id, name, f"{pos or 'NFL'}, {season}-{season}", normalize(name), normalize(last or name), int(games or 0), teammate_counts.get(player_id, 0)))
        conn.executemany("INSERT INTO sport_players_searchable VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
        conn.executemany(
            "INSERT OR IGNORE INTO sport_player_images VALUES (?, ?, ?, NULL)",
            [
                (SPORT_ID, player_id, url)
                for player_id, url in conn.execute("SELECT player_id, headshot_url FROM src.nfl_snap_players WHERE COALESCE(headshot_url, '') <> ''")
            ],
        )
        conn.commit()
        checks = {
            "players": conn.execute("SELECT COUNT(*) FROM sport_players").fetchone()[0],
            "teams": conn.execute("SELECT COUNT(*) FROM sport_teams").fetchone()[0],
            "appearances": conn.execute("SELECT COUNT(*) FROM sport_appearances").fetchone()[0],
            "proofs": conn.execute("SELECT COUNT(*) FROM compact_sport_teammates").fetchone()[0],
        }
        conn.execute("VACUUM")
        return {key: int(value) for key, value in checks.items()}


def copy_rows(cur: "psycopg.Cursor", sql: str, rows: list[tuple], page_size: int = 5000) -> None:
    for start in range(0, len(rows), page_size):
        cur.executemany(sql, rows[start:start + page_size])


def upload_compact(path: Path, season: int, prune_live_staging: bool) -> dict[str, int | str]:
    if psycopg is None:
        raise SystemExit("ERROR: install psycopg first: pip install 'psycopg[binary]'")
    database_url = os.environ.get("DIRECT_URL") or os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("ERROR: DIRECT_URL or DATABASE_URL is required for --upload")
    src = sqlite3.connect(path)
    try:
        tables = {}
        for table in ("sport_teams", "sport_players", "sport_appearances", "sport_player_stints", "sport_player_positions", "sport_teammate_stint_coverage", "sport_players_searchable", "sport_player_images"):
            cols = [row[1] for row in src.execute(f"PRAGMA table_info({table})")]
            tables[table] = (cols, src.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall())
        proofs = src.execute(
            """
            SELECT pa.player_id, pb.player_id, tk.team_id, c.season
              FROM compact_sport_teammates c
              JOIN compact_player_keys pa ON pa.player_key=c.player_a_key
              JOIN compact_player_keys pb ON pb.player_key=c.player_b_key
              JOIN compact_team_keys tk ON tk.team_key=c.team_key
            """
        ).fetchall()
    finally:
        src.close()
    with psycopg.connect(database_url, autocommit=False, prepare_threshold=None) as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '20min'")
            cur.execute(
                """
                INSERT INTO sports (sport_id, display_name, league_name, active, first_season, last_season)
                VALUES (%s, 'Football', 'NFL', true, %s, %s)
                ON CONFLICT (sport_id) DO UPDATE
                SET active=true,
                    first_season=LEAST(COALESCE(sports.first_season, EXCLUDED.first_season), EXCLUDED.first_season),
                    last_season=GREATEST(COALESCE(sports.last_season, EXCLUDED.last_season), EXCLUDED.last_season)
                """,
                (SPORT_ID, season, season),
            )
            cur.execute("DELETE FROM sport_appearances WHERE sport_id=%s AND season=%s", (SPORT_ID, season))
            cur.execute("DELETE FROM sport_player_stints WHERE sport_id=%s AND season=%s", (SPORT_ID, season))
            cur.execute("DELETE FROM sport_teammate_stint_coverage WHERE sport_id=%s AND season=%s", (SPORT_ID, season))
            for table in ("sport_teams", "sport_players", "sport_appearances", "sport_player_stints", "sport_player_positions", "sport_teammate_stint_coverage", "sport_players_searchable", "sport_player_images"):
                cols, rows = tables[table]
                if not rows:
                    continue
                placeholders = ", ".join(["%s"] * len(cols))
                if table in {"sport_appearances", "sport_player_stints", "sport_teammate_stint_coverage"}:
                    copy_rows(cur, f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})", rows)
                else:
                    conflict = {
                        "sport_teams": "(sport_id, team_id, season)",
                        "sport_players": "(sport_id, player_id)",
                        "sport_player_positions": "(sport_id, player_id, position)",
                        "sport_players_searchable": "(sport_id, player_id)",
                        "sport_player_images": "(sport_id, player_id)",
                    }.get(table)
                    updates = ", ".join(f"{col}=EXCLUDED.{col}" for col in cols if col not in {"sport_id", "player_id", "team_id", "season", "position"})
                    copy_rows(cur, f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) ON CONFLICT {conflict} DO UPDATE SET {updates}", rows)
            cur.execute("INSERT INTO compact_player_keys (scope, player_id) SELECT DISTINCT %s, player_id FROM sport_appearances WHERE sport_id=%s AND season=%s ON CONFLICT DO NOTHING", (SPORT_ID, SPORT_ID, season))
            cur.execute("INSERT INTO compact_team_keys (scope, team_id, season) SELECT DISTINCT %s, team_id, season::smallint FROM sport_appearances WHERE sport_id=%s AND season=%s ON CONFLICT DO NOTHING", (SPORT_ID, SPORT_ID, season))
            cur.execute(
                """
                DELETE FROM compact_sport_teammates c
                 USING compact_team_keys tk
                 WHERE c.team_key=tk.team_key
                   AND c.sport_id=%s
                   AND tk.scope=%s
                   AND tk.season=%s
                """,
                (SPORT_ID, SPORT_ID, season),
            )
            copy_rows(
                cur,
                """
                INSERT INTO compact_sport_teammates
                    (sport_id, player_a_key, player_b_key, team_key, season)
                SELECT %s, pa.player_key, pb.player_key, tk.team_key, %s::smallint
                  FROM compact_player_keys pa
                  JOIN compact_player_keys pb ON pb.scope=%s AND pb.player_id=%s
                  JOIN compact_team_keys tk ON tk.scope=%s AND tk.team_id=%s AND tk.season=%s
                 WHERE pa.scope=%s AND pa.player_id=%s
                ON CONFLICT DO NOTHING
                """,
                [(SPORT_ID, yr, SPORT_ID, b, SPORT_ID, team, yr, SPORT_ID, a) for a, b, team, yr in proofs],
            )
            if prune_live_staging:
                cur.execute("DELETE FROM sport_live_player_games WHERE sport_id=%s AND season=%s", (SPORT_ID, season))
                removed_player_games = int(cur.rowcount)
                cur.execute("DELETE FROM sport_live_game_imports WHERE sport_id=%s AND season=%s", (SPORT_ID, season))
                removed_games = int(cur.rowcount)
            else:
                removed_player_games = 0
                removed_games = 0
            cur.execute("ANALYZE compact_sport_teammates")
            db_size = cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))").fetchone()[0]
        conn.commit()
    return {
        "players": len(tables["sport_players"][1]),
        "appearances": len(tables["sport_appearances"][1]),
        "proofs": len(proofs),
        "removed_live_player_games": removed_player_games,
        "removed_live_games": removed_games,
        "database_size": str(db_size),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, default=default_nfl_season())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--skip-collect", action="store_true")
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--prune-live-staging", action="store_true")
    args = parser.parse_args()
    output = args.output or runtime_path(args.season)

    if args.skip_collect:
        if not output.exists():
            raise SystemExit(f"ERROR: --skip-collect requested but local file does not exist: {output}")
        print(f"football compact live local reuse {args.season}: {output}")
    else:
        available, size = snap_asset_available(args.season)
        if not available:
            print(f"nflverse snap_counts_{args.season}.csv is not available yet; size={size}; no-op")
            return 0
        print(f"building local NFL snap source for {args.season}; asset size={size:,}")
        build_snap_source(["--season-start", str(args.season), "--season-end", str(args.season), "--output", str(LIVE_SOURCE)])
        checks = build_local_runtime(args.season, output)
        print(f"local output: {output}")
        print(f"local size: {output.stat().st_size / 1024 / 1024:.1f} MB")
        for key, value in checks.items():
            print(f"{key}: {value:,}")

    if args.upload:
        summary = upload_compact(output, args.season, args.prune_live_staging)
        print("compact upload complete")
        for key, value in summary.items():
            print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
