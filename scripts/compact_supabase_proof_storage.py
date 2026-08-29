#!/usr/bin/env python3
"""Compact strict teammate proof storage in Supabase.

Raw game/boxscore/snap files stay local. This migration replaces the large
text-heavy proof tables with compact integer-key tables plus compatibility
views named like the original tables, so existing gameplay queries can keep
using sport_teammates and mlb_teammate_game_proofs.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def database_url() -> str:
    return os.environ.get("DIRECT_URL") or os.environ.get("DATABASE_URL") or ""


def fetch_size(cur: "psycopg.Cursor") -> str:
    return cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))").fetchone()[0]


def relation_kind(cur: "psycopg.Cursor", name: str) -> str | None:
    return cur.execute(
        """
        SELECT c.relkind
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relname = %s
        """,
        (name,),
    ).fetchone()


def scalar(cur: "psycopg.Cursor", sql: str) -> int:
    return int(cur.execute(sql).fetchone()[0])


def build_compact_tables(conn: "psycopg.Connection") -> dict[str, int]:
    with conn.cursor() as cur:
        sport_kind = relation_kind(cur, "sport_teammates")
        mlb_kind = relation_kind(cur, "mlb_teammate_game_proofs")
        if sport_kind is None or sport_kind[0] != "r":
            raise RuntimeError("sport_teammates must be a physical table before compaction")
        if mlb_kind is None or mlb_kind[0] != "r":
            raise RuntimeError("mlb_teammate_game_proofs must be a physical table before compaction")

        before = {
            "sport_rows": scalar(cur, "SELECT COUNT(*) FROM sport_teammates"),
            "mlb_rows": scalar(cur, "SELECT COUNT(*) FROM mlb_teammate_game_proofs"),
        }

        cur.execute("DROP TABLE IF EXISTS compact_sport_teammates")
        cur.execute("DROP TABLE IF EXISTS compact_mlb_teammate_game_proofs")
        cur.execute("DROP TABLE IF EXISTS compact_team_keys")
        cur.execute("DROP TABLE IF EXISTS compact_player_keys")

        cur.execute(
            """
            CREATE TABLE compact_player_keys (
                player_key INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                scope TEXT NOT NULL,
                player_id TEXT NOT NULL,
                UNIQUE (scope, player_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE compact_team_keys (
                team_key INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                scope TEXT NOT NULL,
                team_id TEXT NOT NULL,
                season SMALLINT NOT NULL,
                UNIQUE (scope, team_id, season)
            )
            """
        )
        cur.execute(
            """
            INSERT INTO compact_player_keys (scope, player_id)
            SELECT DISTINCT scope, player_id
              FROM (
                    SELECT sport_id AS scope, player_a_id AS player_id FROM sport_teammates
                    UNION
                    SELECT sport_id AS scope, player_b_id AS player_id FROM sport_teammates
                    UNION
                    SELECT 'baseball' AS scope, player_a_id AS player_id FROM mlb_teammate_game_proofs
                    UNION
                    SELECT 'baseball' AS scope, player_b_id AS player_id FROM mlb_teammate_game_proofs
              ) players
             ORDER BY scope, player_id
            """
        )
        cur.execute(
            """
            INSERT INTO compact_team_keys (scope, team_id, season)
            SELECT DISTINCT scope, team_id, season::smallint
              FROM (
                    SELECT sport_id AS scope, team_id, season FROM sport_teammates
                    UNION
                    SELECT 'baseball' AS scope, team_id, season FROM mlb_teammate_game_proofs
              ) teams
             ORDER BY scope, season, team_id
            """
        )
        cur.execute(
            """
            CREATE TABLE compact_sport_teammates (
                sport_id TEXT NOT NULL,
                player_a_key INTEGER NOT NULL REFERENCES compact_player_keys(player_key),
                player_b_key INTEGER NOT NULL REFERENCES compact_player_keys(player_key),
                team_key INTEGER NOT NULL REFERENCES compact_team_keys(team_key),
                season SMALLINT NOT NULL,
                PRIMARY KEY (sport_id, player_a_key, player_b_key, team_key, season)
            )
            """
        )
        cur.execute(
            """
            INSERT INTO compact_sport_teammates
                (sport_id, player_a_key, player_b_key, team_key, season)
            SELECT t.sport_id, pa.player_key, pb.player_key, team.team_key, t.season::smallint
              FROM sport_teammates t
              JOIN compact_player_keys pa
                ON pa.scope = t.sport_id AND pa.player_id = t.player_a_id
              JOIN compact_player_keys pb
                ON pb.scope = t.sport_id AND pb.player_id = t.player_b_id
              JOIN compact_team_keys team
                ON team.scope = t.sport_id
               AND team.team_id = t.team_id
               AND team.season = t.season
            """
        )
        cur.execute(
            """
            CREATE TABLE compact_mlb_teammate_game_proofs (
                player_a_key INTEGER NOT NULL REFERENCES compact_player_keys(player_key),
                player_b_key INTEGER NOT NULL REFERENCES compact_player_keys(player_key),
                team_key INTEGER NOT NULL REFERENCES compact_team_keys(team_key),
                season SMALLINT NOT NULL,
                shared_games SMALLINT NOT NULL,
                first_game_pk INTEGER NOT NULL,
                first_game_date DATE NOT NULL,
                PRIMARY KEY (player_a_key, player_b_key, team_key, season)
            )
            """
        )
        cur.execute(
            """
            INSERT INTO compact_mlb_teammate_game_proofs
                (player_a_key, player_b_key, team_key, season, shared_games,
                 first_game_pk, first_game_date)
            SELECT pa.player_key, pb.player_key, team.team_key, proof.season::smallint,
                   proof.shared_games::smallint, proof.first_game_pk, proof.first_game_date
              FROM mlb_teammate_game_proofs proof
              JOIN compact_player_keys pa
                ON pa.scope = 'baseball' AND pa.player_id = proof.player_a_id
              JOIN compact_player_keys pb
                ON pb.scope = 'baseball' AND pb.player_id = proof.player_b_id
              JOIN compact_team_keys team
                ON team.scope = 'baseball'
               AND team.team_id = proof.team_id
               AND team.season = proof.season
            """
        )

        after_build = {
            "compact_sport_rows": scalar(cur, "SELECT COUNT(*) FROM compact_sport_teammates"),
            "compact_mlb_rows": scalar(cur, "SELECT COUNT(*) FROM compact_mlb_teammate_game_proofs"),
        }
        if before["sport_rows"] != after_build["compact_sport_rows"]:
            raise RuntimeError(f"sport row mismatch: {before['sport_rows']} vs {after_build['compact_sport_rows']}")
        if before["mlb_rows"] != after_build["compact_mlb_rows"]:
            raise RuntimeError(f"mlb row mismatch: {before['mlb_rows']} vs {after_build['compact_mlb_rows']}")

        return before | after_build


def swap_to_views(conn: "psycopg.Connection") -> None:
    with conn.cursor() as cur:
        cur.execute("DROP TABLE sport_teammates")
        cur.execute("DROP TABLE mlb_teammate_game_proofs")
        cur.execute(
            """
            CREATE VIEW sport_teammates AS
            SELECT c.sport_id,
                   pa.player_id AS player_a_id,
                   pb.player_id AS player_b_id,
                   team.team_id,
                   c.season::integer AS season
              FROM compact_sport_teammates c
              JOIN compact_player_keys pa ON pa.player_key = c.player_a_key
              JOIN compact_player_keys pb ON pb.player_key = c.player_b_key
              JOIN compact_team_keys team ON team.team_key = c.team_key
            """
        )
        cur.execute(
            """
            CREATE VIEW mlb_teammate_game_proofs AS
            SELECT pa.player_id AS player_a_id,
                   pb.player_id AS player_b_id,
                   team.team_id,
                   c.season::integer AS season,
                   c.shared_games::integer AS shared_games,
                   c.first_game_pk,
                   c.first_game_date,
                   'compact_mlb_game_boxscore'::text AS source
              FROM compact_mlb_teammate_game_proofs c
              JOIN compact_player_keys pa ON pa.player_key = c.player_a_key
              JOIN compact_player_keys pb ON pb.player_key = c.player_b_key
              JOIN compact_team_keys team ON team.team_key = c.team_key
            """
        )


def vacuum_tables(conn: "psycopg.Connection") -> None:
    with conn.cursor() as cur:
        for name in (
            "compact_player_keys",
            "compact_team_keys",
            "compact_sport_teammates",
            "compact_mlb_teammate_game_proofs",
            "sport_appearances",
            "sport_player_stints",
        ):
            cur.execute(f"VACUUM (FULL, ANALYZE) {name}")


def smoke(conn: "psycopg.Connection") -> dict[str, int]:
    with conn.cursor() as cur:
        return {
            "sport_view_rows": scalar(cur, "SELECT COUNT(*) FROM sport_teammates"),
            "mlb_view_rows": scalar(cur, "SELECT COUNT(*) FROM mlb_teammate_game_proofs"),
            "compact_sport_rows": scalar(cur, "SELECT COUNT(*) FROM compact_sport_teammates"),
            "compact_mlb_rows": scalar(cur, "SELECT COUNT(*) FROM compact_mlb_teammate_game_proofs"),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    url = database_url()
    if not url:
        print("DIRECT_URL or DATABASE_URL is required in .env.", file=sys.stderr)
        return 1

    with psycopg.connect(url, autocommit=False, prepare_threshold=None) as conn:
        with conn.cursor() as cur:
            print(f"database size before: {fetch_size(cur)}", flush=True)
        if not args.execute:
            print("dry run only. Re-run with --execute to compact proof tables.")
            return 0
        counts = build_compact_tables(conn)
        conn.commit()
        print("built compact tables:", counts, flush=True)
        swap_to_views(conn)
        conn.commit()
        print("swapped original proof tables to compatibility views", flush=True)

    with psycopg.connect(url, autocommit=True, prepare_threshold=None) as conn:
        vacuum_tables(conn)
        checks = smoke(conn)
        with conn.cursor() as cur:
            print("verification:", checks, flush=True)
            print(f"database size after: {fetch_size(cur)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
