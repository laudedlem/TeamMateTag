#!/usr/bin/env python3
"""Import strict NHL game-level teammate proofs into Supabase.

Source rows come from raw/nhl_game_teammates/nhl_game_teammates.sqlite, built
from official NHL regular-season boxscores. A Hockey teammate link is valid
only when both players had TOI greater than zero for the same NHL team in the
same regular-season game.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import psycopg
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from name_normalize import normalize  # noqa: E402

SOURCE = ROOT / "raw" / "nhl_game_teammates" / "nhl_game_teammates.sqlite"
SCHEMA = ROOT / "db" / "cross_sport_schema_postgres.sql"
SPORT_ID = "hockey"
SOURCE_NAME = "nhl_api_web_gamecenter_boxscore"
API = "https://api-web.nhle.com/v1"


def localized(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("default") or next(iter(value.values()), "") or "")
    return str(value or "")


def fetch_player_name(external_id: str, fallback: str) -> tuple[str, str | None, str | None]:
    try:
        payload = requests.get(f"{API}/player/{external_id}/landing", timeout=20).json()
    except requests.RequestException:
        payload = {}
    first = localized(payload.get("firstName")) or None
    last = localized(payload.get("lastName")) or None
    name = " ".join(part for part in (first, last) if part).strip() or fallback
    return name, first, last


def ensure_tables(conn: "psycopg.Connection") -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA.read_text(encoding="utf-8"))
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sport_teammates (
                sport_id TEXT NOT NULL REFERENCES sports(sport_id),
                player_a_id TEXT NOT NULL,
                player_b_id TEXT NOT NULL,
                team_id TEXT NOT NULL,
                season INTEGER NOT NULL,
                PRIMARY KEY (sport_id, player_a_id, player_b_id, team_id, season),
                CHECK (player_a_id < player_b_id),
                FOREIGN KEY (sport_id, player_a_id)
                    REFERENCES sport_players(sport_id, player_id),
                FOREIGN KEY (sport_id, player_b_id)
                    REFERENCES sport_players(sport_id, player_id)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_sport_teammates_pair "
            "ON sport_teammates(sport_id, player_a_id, player_b_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_sport_teammates_a "
            "ON sport_teammates(sport_id, player_a_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_sport_teammates_b "
            "ON sport_teammates(sport_id, player_b_id)"
        )


def source_summary(src: sqlite3.Connection) -> tuple[int, int, int, int, int]:
    games = src.execute("SELECT COUNT(*) FROM nhl_games").fetchone()[0]
    appearances = src.execute("SELECT COUNT(*) FROM nhl_player_game_appearances").fetchone()[0]
    proofs = src.execute("SELECT COUNT(*) FROM nhl_teammate_game_proofs").fetchone()[0]
    season_start, season_end = src.execute(
        "SELECT MIN(season), MAX(season) FROM nhl_games"
    ).fetchone()
    return int(games), int(appearances), int(proofs), int(season_start), int(season_end)


def placeholders(values: list[str]) -> str:
    return ",".join("?" for _ in values)


def backfill_missing_players(src: sqlite3.Connection, dst: "psycopg.Connection") -> int:
    source_ids = {
        row[0]
        for row in src.execute(
            """
            SELECT player_a_id FROM nhl_teammate_game_proofs
            UNION
            SELECT player_b_id FROM nhl_teammate_game_proofs
            """
        )
    }
    with dst.cursor() as cur:
        production_ids = set()
        for player_id, external_id in cur.execute(
            "SELECT player_id, external_id FROM sport_players WHERE sport_id = %s",
            (SPORT_ID,),
        ):
            if player_id:
                production_ids.add(player_id)
            if external_id:
                production_ids.add(f"nhl:{external_id}")
    missing_ids = sorted(source_ids - production_ids)
    if not missing_ids:
        print("No missing Hockey proof players to backfill.")
        return 0

    meta: dict[str, dict[str, Any]] = {}
    for player_id, external_id, name, debut, final, games in src.execute(
        f"""
        SELECT player_id, external_id, MAX(display_name), MIN(season), MAX(season), COUNT(*)
          FROM nhl_player_game_appearances
         WHERE player_id IN ({placeholders(missing_ids)})
         GROUP BY player_id, external_id
        """,
        missing_ids,
    ):
        meta[player_id] = {
            "external_id": str(external_id),
            "fallback_name": name,
            "debut": int(debut),
            "final": int(final),
            "career_games": int(games),
        }

    positions = Counter()
    for player_id, position in src.execute(
        f"""
        SELECT player_id, position
          FROM nhl_player_game_appearances
         WHERE player_id IN ({placeholders(missing_ids)})
           AND COALESCE(position, '') <> ''
        """,
        missing_ids,
    ):
        positions[(player_id, position)] += 1
    primary_pos: dict[str, str] = {}
    for (player_id, position), _count in positions.most_common():
        primary_pos.setdefault(player_id, position)

    stint_rows = src.execute(
        f"""
        SELECT a.player_id, a.team_id,
               MAX(CASE WHEN a.team_id = g.away_team_id THEN g.away_team_name ELSE g.home_team_name END),
               a.season, COUNT(*) AS games_total, MIN(a.game_date), MAX(a.game_date)
          FROM nhl_player_game_appearances a
          JOIN nhl_games g ON g.game_id = a.game_id
         WHERE a.player_id IN ({placeholders(missing_ids)})
         GROUP BY a.player_id, a.team_id, a.season
        """,
        missing_ids,
    ).fetchall()

    with dst.cursor() as cur:
        for player_id in missing_ids:
            info = meta.get(player_id)
            if not info:
                continue
            name, first, last = fetch_player_name(info["external_id"], info["fallback_name"])
            cur.execute(
                """
                INSERT INTO sport_players
                    (sport_id, player_id, external_id, display_name, first_name, last_name,
                     debut_year, final_year, primary_pos)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (sport_id, player_id) DO UPDATE
                SET external_id = COALESCE(sport_players.external_id, EXCLUDED.external_id),
                    display_name = EXCLUDED.display_name,
                    first_name = COALESCE(EXCLUDED.first_name, sport_players.first_name),
                    last_name = COALESCE(EXCLUDED.last_name, sport_players.last_name),
                    debut_year = LEAST(COALESCE(sport_players.debut_year, EXCLUDED.debut_year), EXCLUDED.debut_year),
                    final_year = GREATEST(COALESCE(sport_players.final_year, EXCLUDED.final_year), EXCLUDED.final_year),
                    primary_pos = COALESCE(sport_players.primary_pos, EXCLUDED.primary_pos)
                """,
                (
                    SPORT_ID,
                    player_id,
                    info["external_id"],
                    name,
                    first,
                    last,
                    info["debut"],
                    info["final"],
                    primary_pos.get(player_id),
                ),
            )
            cur.execute(
                """
                INSERT INTO sport_players_searchable
                    (sport_id, player_id, display_name, disambiguation, search_key,
                     last_key, career_games, teammate_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 0)
                ON CONFLICT (sport_id, player_id) DO UPDATE
                SET display_name = EXCLUDED.display_name,
                    disambiguation = EXCLUDED.disambiguation,
                    search_key = EXCLUDED.search_key,
                    last_key = EXCLUDED.last_key,
                    career_games = GREATEST(sport_players_searchable.career_games, EXCLUDED.career_games)
                """,
                (
                    SPORT_ID,
                    player_id,
                    name,
                    f"{primary_pos.get(player_id) or 'NHL'}, {info['debut']}-{info['final']}",
                    normalize(name),
                    normalize(last or name),
                    info["career_games"],
                ),
            )
        for player_id, team_id, team_name, season, games_total, first_date, last_date in stint_rows:
            cur.execute(
                """
                INSERT INTO sport_franchises (sport_id, franchise_id, name, active)
                VALUES (%s, %s, %s, true)
                ON CONFLICT (sport_id, franchise_id) DO UPDATE
                SET name = EXCLUDED.name,
                    active = true
                """,
                (SPORT_ID, team_id, team_name),
            )
            cur.execute(
                """
                INSERT INTO sport_teams (sport_id, team_id, season, franchise_id, name)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (sport_id, team_id, season) DO UPDATE
                SET franchise_id = EXCLUDED.franchise_id,
                    name = EXCLUDED.name
                """,
                (SPORT_ID, team_id, season, team_id, team_name),
            )
            cur.execute(
                """
                INSERT INTO sport_appearances (sport_id, player_id, team_id, season, games_total)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (sport_id, player_id, team_id, season) DO UPDATE
                SET games_total = GREATEST(sport_appearances.games_total, EXCLUDED.games_total)
                """,
                (SPORT_ID, player_id, team_id, season, games_total),
            )
            cur.execute(
                """
                INSERT INTO sport_player_stints
                    (sport_id, player_id, team_id, season, first_unit, last_unit,
                     first_label, last_label, source)
                VALUES (%s, %s, %s, %s,
                        to_char(%s::date, 'YYYYMMDD')::integer,
                        to_char(%s::date, 'YYYYMMDD')::integer,
                        %s, %s, %s)
                ON CONFLICT (sport_id, player_id, team_id, season) DO UPDATE
                SET first_unit = LEAST(sport_player_stints.first_unit, EXCLUDED.first_unit),
                    last_unit = GREATEST(sport_player_stints.last_unit, EXCLUDED.last_unit),
                    first_label = LEAST(sport_player_stints.first_label, EXCLUDED.first_label),
                    last_label = GREATEST(sport_player_stints.last_label, EXCLUDED.last_label),
                    source = EXCLUDED.source
                """,
                (
                    SPORT_ID,
                    player_id,
                    team_id,
                    season,
                    first_date,
                    last_date,
                    first_date,
                    last_date,
                    SOURCE_NAME,
                ),
            )
        position_payload = [
            (SPORT_ID, player_id, position, count)
            for (player_id, position), count in positions.items()
        ]
        cur.executemany(
            """
            INSERT INTO sport_player_positions (sport_id, player_id, position, games)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (sport_id, player_id, position) DO UPDATE
            SET games = GREATEST(sport_player_positions.games, EXCLUDED.games)
            """,
            position_payload,
        )

    print(f"Backfilled {len(missing_ids):,} missing Hockey proof players.")
    return len(missing_ids)


def copy_proofs(src: sqlite3.Connection, dst: "psycopg.Connection") -> int:
    rows = src.execute(
        """
        SELECT player_a_id, player_b_id, team_id, season
          FROM nhl_teammate_game_proofs
         ORDER BY season, team_id, player_a_id, player_b_id
        """
    )
    count = 0
    with dst.cursor() as cur:
        cur.execute(
            """
            CREATE TEMP TABLE tmp_nhl_game_teammates (
                player_a_nhl_id TEXT NOT NULL,
                player_a_external_id TEXT NOT NULL,
                player_b_nhl_id TEXT NOT NULL,
                player_b_external_id TEXT NOT NULL,
                team_id TEXT NOT NULL,
                season INTEGER NOT NULL
            ) ON COMMIT DROP
            """
        )
        with cur.copy(
            "COPY tmp_nhl_game_teammates "
            "(player_a_nhl_id, player_a_external_id, player_b_nhl_id, player_b_external_id, team_id, season) "
            "FROM STDIN"
        ) as copy:
            for player_a_id, player_b_id, team_id, season in rows:
                copy.write_row((
                    player_a_id,
                    player_a_id.replace("nhl:", ""),
                    player_b_id,
                    player_b_id.replace("nhl:", ""),
                    team_id,
                    season,
                ))
                count += 1
        cur.execute("CREATE INDEX ON tmp_nhl_game_teammates (player_a_external_id)")
        cur.execute("CREATE INDEX ON tmp_nhl_game_teammates (player_b_external_id)")
    return count


def import_proofs(dst: "psycopg.Connection", season_start: int, season_end: int) -> tuple[int, int]:
    with dst.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '15min'")
        cur.execute(
            """
            CREATE TEMP TABLE tmp_nhl_player_map AS
            WITH source_ids AS (
                SELECT player_a_nhl_id AS nhl_id, player_a_external_id AS external_id
                  FROM tmp_nhl_game_teammates
                UNION
                SELECT player_b_nhl_id AS nhl_id, player_b_external_id AS external_id
                  FROM tmp_nhl_game_teammates
            )
            SELECT DISTINCT ON (source_ids.nhl_id)
                   source_ids.nhl_id, player.player_id
              FROM source_ids
              JOIN sport_players player
                ON player.sport_id = %s
               AND (player.player_id = source_ids.nhl_id OR player.external_id = source_ids.external_id)
             ORDER BY source_ids.nhl_id, (player.player_id = source_ids.nhl_id) DESC, player.player_id
            """,
            (SPORT_ID,),
        )
        cur.execute("CREATE UNIQUE INDEX ON tmp_nhl_player_map (nhl_id)")
        cur.execute("DELETE FROM sport_teammates WHERE sport_id = %s", (SPORT_ID,))
        cur.execute(
            """
            INSERT INTO sport_teammates (sport_id, player_a_id, player_b_id, team_id, season)
            SELECT DISTINCT
                   %s AS sport_id,
                   LEAST(pa.player_id, pb.player_id) AS player_a_id,
                   GREATEST(pa.player_id, pb.player_id) AS player_b_id,
                   proof.team_id,
                   proof.season
              FROM tmp_nhl_game_teammates proof
              JOIN tmp_nhl_player_map pa ON pa.nhl_id = proof.player_a_nhl_id
              JOIN tmp_nhl_player_map pb ON pb.nhl_id = proof.player_b_nhl_id
             WHERE pa.player_id <> pb.player_id
            ON CONFLICT DO NOTHING
            """,
            (SPORT_ID,),
        )
        inserted = cur.rowcount
        cur.execute(
            """
            INSERT INTO sport_teammate_stint_coverage
                (sport_id, season, coverage_type, strict, source, updated_at)
            SELECT %s, season, 'game_boxscore', 1, %s, now()
              FROM generate_series(%s::integer, %s::integer) AS season
            ON CONFLICT (sport_id, season) DO UPDATE
            SET coverage_type = EXCLUDED.coverage_type,
                strict = EXCLUDED.strict,
                source = EXCLUDED.source,
                updated_at = now()
            """,
            (SPORT_ID, SOURCE_NAME, season_start, season_end),
        )
        cur.execute(
            """
            WITH teammate_counts AS (
                SELECT player_id, COUNT(DISTINCT teammate_id)::integer AS teammate_count
                  FROM (
                        SELECT player_a_id AS player_id, player_b_id AS teammate_id
                          FROM sport_teammates WHERE sport_id = %s
                        UNION ALL
                        SELECT player_b_id AS player_id, player_a_id AS teammate_id
                          FROM sport_teammates WHERE sport_id = %s
                  ) links
                 GROUP BY player_id
            )
            UPDATE sport_players_searchable ps
               SET teammate_count = COALESCE(tc.teammate_count, 0)
              FROM sport_players p
              LEFT JOIN teammate_counts tc ON tc.player_id = p.player_id
             WHERE ps.sport_id = %s
               AND p.sport_id = ps.sport_id
               AND p.player_id = ps.player_id
            """,
            (SPORT_ID, SPORT_ID, SPORT_ID),
        )
        cur.execute(
            """
            SELECT COUNT(*)
              FROM tmp_nhl_game_teammates proof
             WHERE NOT EXISTS (
                   SELECT 1 FROM tmp_nhl_player_map pa
                    WHERE pa.nhl_id = proof.player_a_nhl_id
             )
                OR NOT EXISTS (
                   SELECT 1 FROM tmp_nhl_player_map pb
                    WHERE pb.nhl_id = proof.player_b_nhl_id
             )
            """,
        )
        unmapped = int(cur.fetchone()[0])
    return int(inserted), unmapped


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required in .env.", file=sys.stderr)
        return 1
    if not SOURCE.exists():
        print(f"Missing source database: {SOURCE}", file=sys.stderr)
        return 1

    src = sqlite3.connect(SOURCE)
    try:
        games, appearances, proofs, season_start, season_end = source_summary(src)
        print(
            f"Source: {games:,} games; {appearances:,} player-games; "
            f"{proofs:,} proof rows; seasons {season_start}-{season_end}"
        )
        started = time.monotonic()
        with psycopg.connect(database_url, autocommit=False, prepare_threshold=None) as dst:
            ensure_tables(dst)
            backfill_missing_players(src, dst)
            copied = copy_proofs(src, dst)
            inserted, unmapped = import_proofs(dst, season_start, season_end)
            dst.commit()
        print(f"Copied {copied:,} source proofs.")
        print(f"Imported {inserted:,} Hockey strict teammate rows.")
        print(f"Unmapped source proof rows: {unmapped:,}")
        print(f"Done in {time.monotonic() - started:.1f}s.")
    finally:
        src.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
