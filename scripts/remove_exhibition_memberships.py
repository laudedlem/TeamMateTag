#!/usr/bin/env python3
"""Remove All-Star/exhibition team memberships from production data."""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

try:
    import psycopg
except ImportError:  # pragma: no cover
    print("ERROR: install psycopg first: pip install 'psycopg[binary]'", file=sys.stderr)
    sys.exit(1)

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
COMPACT_DB = ROOT / "raw" / "runtime_compact" / "teammatetag_runtime_minimal.sqlite"
sys.path.insert(0, str(ROOT / "scripts"))

from name_normalize import normalize  # noqa: E402


def baseball_bad_team_sql(alias: str) -> str:
    return f"""
        {alias}.team_id IN ('AL', 'NL')
        OR replace(lower(COALESCE({alias}.name, '')), '-', ' ') LIKE '%all star%'
    """


def sport_bad_team_sql(alias: str) -> str:
    return f"""
        replace(lower(COALESCE({alias}.name, '')), '-', ' ') LIKE '%all star%'
        OR replace(lower(COALESCE({alias}.name, '')), '-', ' ') LIKE '%rising star%'
        OR replace(lower(COALESCE({alias}.name, '')), '-', ' ') LIKE '%young star%'
        OR replace(lower(COALESCE({alias}.name, '')), '-', ' ') LIKE '%rookie challenge%'
        OR lower(COALESCE({alias}.name, '')) IN ('world', 'usa')
        OR ({alias}.sport_id = 'basketball' AND lower(COALESCE({alias}.name, '')) IN ('ogs', 'stripes'))
        OR ({alias}.sport_id = 'basketball' AND lower(COALESCE({alias}.name, '')) LIKE 'team %')
    """


def refresh_baseball_search(conn: "psycopg.Connection", player_ids: list[str]) -> None:
    if not player_ids:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE players p
            SET debut_year = years.debut_year,
                final_year = years.final_year
            FROM (
                SELECT player_id, MIN(season)::integer AS debut_year, MAX(season)::integer AS final_year
                FROM appearances
                WHERE player_id = ANY(%s)
                GROUP BY player_id
            ) years
            WHERE p.player_id = years.player_id
            """,
            (player_ids,),
        )
        rows = cur.execute(
            """
            WITH careers AS (
                SELECT player_id, SUM(games_total)::integer AS career_games
                FROM appearances
                WHERE player_id = ANY(%s)
                GROUP BY player_id
            ),
            teammate_counts AS (
                SELECT a.player_id, COUNT(DISTINCT b.player_id)::integer AS teammate_count
                FROM appearances a
                JOIN appearances b
                  ON b.team_id = a.team_id
                 AND b.season = a.season
                 AND b.player_id <> a.player_id
                WHERE a.player_id = ANY(%s)
                GROUP BY a.player_id
            )
            SELECT p.player_id, p.name_first, p.name_last, p.primary_pos,
                   p.debut_year, p.final_year,
                   COALESCE(c.career_games, 0), COALESCE(t.teammate_count, 0)
            FROM players p
            LEFT JOIN careers c ON c.player_id = p.player_id
            LEFT JOIN teammate_counts t ON t.player_id = p.player_id
            WHERE p.player_id = ANY(%s)
            """,
            (player_ids, player_ids, player_ids),
        ).fetchall()
        payload = []
        for row in rows:
            name = " ".join(part for part in (row[1], row[2]) if part).strip()
            if not name:
                continue
            payload.append(
                (
                    row[0],
                    name,
                    f"{row[3] or 'MLB'}, {row[4] or '?'}-{row[5] or '?'}",
                    normalize(name),
                    normalize(row[2] or name),
                    row[6],
                    row[7],
                )
            )
        cur.executemany(
            """
            INSERT INTO players_searchable
                (player_id, display_name, disambiguation, search_key, last_key, career_games, teammate_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (player_id) DO UPDATE
            SET display_name = EXCLUDED.display_name,
                disambiguation = EXCLUDED.disambiguation,
                search_key = EXCLUDED.search_key,
                last_key = EXCLUDED.last_key,
                career_games = EXCLUDED.career_games,
                teammate_count = EXCLUDED.teammate_count
            """,
            payload,
        )


def refresh_sport_search(conn: "psycopg.Connection", affected: list[tuple[str, str]]) -> None:
    if not affected:
        return
    by_sport: dict[str, list[str]] = {}
    for sport_id, player_id in affected:
        by_sport.setdefault(sport_id, []).append(player_id)
    if COMPACT_DB.exists():
        with sqlite3.connect(COMPACT_DB) as runtime:
            for sport_id, player_ids in by_sport.items():
                placeholders = ",".join("?" for _ in player_ids)
                rows = runtime.execute(
                    f"""
                    SELECT scope, player_id, display_name,
                           COALESCE(primary_pos, UPPER(scope)) || ', ' ||
                           COALESCE(debut_year, '?') || '-' || COALESCE(final_year, '?'),
                           search_key, last_key, career_games, teammate_count
                      FROM runtime_players
                     WHERE scope = ?
                       AND player_id IN ({placeholders})
                    """,
                    (sport_id, *player_ids),
                ).fetchall()
                if rows:
                    with conn.cursor() as cur:
                        cur.executemany(
                            """
                            INSERT INTO sport_players_searchable
                                (sport_id, player_id, display_name, disambiguation, search_key,
                                 last_key, career_games, teammate_count)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (sport_id, player_id) DO UPDATE
                            SET display_name = EXCLUDED.display_name,
                                disambiguation = EXCLUDED.disambiguation,
                                search_key = EXCLUDED.search_key,
                                last_key = EXCLUDED.last_key,
                                career_games = EXCLUDED.career_games,
                                teammate_count = EXCLUDED.teammate_count
                            """,
                            rows,
                        )
        return
    with conn.cursor() as cur:
        for sport_id, player_ids in by_sport.items():
            cur.execute(
                """
                UPDATE sport_players p
                SET debut_year = years.debut_year,
                    final_year = years.final_year
                FROM (
                    SELECT player_id, MIN(season)::integer AS debut_year, MAX(season)::integer AS final_year
                    FROM sport_appearances
                    WHERE sport_id = %s AND player_id = ANY(%s)
                    GROUP BY player_id
                ) years
                WHERE p.sport_id = %s AND p.player_id = years.player_id
                """,
                (sport_id, player_ids, sport_id),
            )
            rows = cur.execute(
                """
                WITH careers AS (
                    SELECT player_id, SUM(games_total)::integer AS career_games
                    FROM sport_appearances
                    WHERE sport_id = %s AND player_id = ANY(%s)
                    GROUP BY player_id
                ),
                teammate_counts AS (
                    SELECT a.player_id, COUNT(DISTINCT b.player_id)::integer AS teammate_count
                    FROM sport_appearances a
                    JOIN sport_appearances b
                      ON b.sport_id = a.sport_id
                     AND b.team_id = a.team_id
                     AND b.season = a.season
                     AND b.player_id <> a.player_id
                    WHERE a.sport_id = %s AND a.player_id = ANY(%s)
                    GROUP BY a.player_id
                )
                SELECT p.player_id, p.display_name, p.last_name, p.primary_pos,
                       p.debut_year, p.final_year,
                       COALESCE(c.career_games, 0), COALESCE(t.teammate_count, 0)
                FROM sport_players p
                LEFT JOIN careers c ON c.player_id = p.player_id
                LEFT JOIN teammate_counts t ON t.player_id = p.player_id
                WHERE p.sport_id = %s AND p.player_id = ANY(%s)
                """,
                (sport_id, player_ids, sport_id, player_ids, sport_id, player_ids),
            ).fetchall()
            payload = []
            league = {"basketball": "NBA", "hockey": "NHL", "football": "NFL"}.get(sport_id, sport_id.upper())
            for row in rows:
                name = row[1]
                if not name:
                    continue
                payload.append(
                    (
                        sport_id,
                        row[0],
                        name,
                        f"{row[3] or league}, {row[4] or '?'}-{row[5] or '?'}",
                        normalize(name),
                        normalize(row[2] or name),
                        row[6],
                        row[7],
                    )
                )
            cur.executemany(
                """
                INSERT INTO sport_players_searchable
                    (sport_id, player_id, display_name, disambiguation, search_key,
                     last_key, career_games, teammate_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (sport_id, player_id) DO UPDATE
                SET display_name = EXCLUDED.display_name,
                    disambiguation = EXCLUDED.disambiguation,
                    search_key = EXCLUDED.search_key,
                    last_key = EXCLUDED.last_key,
                    career_games = EXCLUDED.career_games,
                    teammate_count = EXCLUDED.teammate_count
                """,
                payload,
            )


def main() -> int:
    database_url = os.environ.get("DATABASE_URL") or os.environ.get("DIRECT_URL")
    if not database_url:
        print("ERROR: DATABASE_URL or DIRECT_URL is required.", file=sys.stderr)
        return 1

    with psycopg.connect(database_url, autocommit=False, prepare_threshold=None) as conn:
        with conn.cursor() as cur:
            baseball_players = [
                row[0]
                for row in cur.execute(
                    f"""
                    SELECT DISTINCT player_id
                    FROM appearances a
                    JOIN teams t ON t.team_id = a.team_id AND t.season = a.season
                    WHERE {baseball_bad_team_sql('t')}
                    """
                ).fetchall()
            ]
            baseball_bad_team_keys = [
                row[0]
                for row in cur.execute(
                    f"""
                    SELECT tk.team_key
                      FROM compact_team_keys tk
                      JOIN teams t
                        ON t.team_id = tk.team_id
                       AND t.season = tk.season
                     WHERE tk.scope = 'baseball'
                       AND ({baseball_bad_team_sql('t')})
                    """
                ).fetchall()
            ]
            baseball_live_games = cur.execute(
                """
                SELECT COUNT(DISTINCT game_pk)
                FROM mlb_live_player_games
                WHERE team_id IN (
                    SELECT team_id FROM teams WHERE team_id IN ('AL', 'NL')
                       OR replace(lower(COALESCE(name, '')), '-', ' ') LIKE '%all star%'
                )
                """
            ).fetchone()[0]
            cur.execute(
                """
                DELETE FROM mlb_live_game_imports
                WHERE game_pk IN (
                    SELECT DISTINCT game_pk FROM mlb_live_player_games
                    WHERE team_id IN (
                        SELECT team_id FROM teams WHERE team_id IN ('AL', 'NL')
                           OR replace(lower(COALESCE(name, '')), '-', ' ') LIKE '%all star%'
                    )
                )
                """
            )
            baseball_appearances = cur.execute(
                f"""
                DELETE FROM appearances a
                USING teams t
                WHERE t.team_id = a.team_id AND t.season = a.season
                  AND ({baseball_bad_team_sql('t')})
                """
            ).rowcount
            cur.execute(
                f"""
                DELETE FROM player_stints s
                USING teams t
                WHERE t.team_id = s.team_id AND t.season = s.season
                  AND ({baseball_bad_team_sql('t')})
                """
            )
            if baseball_bad_team_keys:
                if cur.execute("SELECT to_regclass('compact_mlb_teammate_game_proofs')").fetchone()[0]:
                    cur.execute(
                        "DELETE FROM compact_mlb_teammate_game_proofs WHERE team_key = ANY(%s)",
                        (baseball_bad_team_keys,),
                    )
                cur.execute(
                    "DELETE FROM compact_team_keys WHERE scope='baseball' AND team_key = ANY(%s)",
                    (baseball_bad_team_keys,),
                )
            baseball_teams = cur.execute(f"DELETE FROM teams t WHERE {baseball_bad_team_sql('t')}").rowcount
            cur.execute(
                """
                DELETE FROM franchises
                WHERE franchise_id IN ('AL', 'NL')
                  AND NOT EXISTS (SELECT 1 FROM teams WHERE teams.franchise_id = franchises.franchise_id)
                """
            )
            cur.execute(
                """
                UPDATE data_provenance
                SET row_count = (
                    SELECT COUNT(*) FROM mlb_live_player_games WHERE season = data_provenance.season
                ),
                    fetched_at = now()
                WHERE source = 'mlb_statsapi_game_log'
                """
            )

            sport_affected = [
                (row[0], row[1])
                for row in cur.execute(
                    f"""
                    SELECT DISTINCT a.sport_id, a.player_id
                    FROM sport_appearances a
                    JOIN sport_teams t
                      ON t.sport_id = a.sport_id
                     AND t.team_id = a.team_id
                     AND t.season = a.season
                    WHERE {sport_bad_team_sql('t')}
                    """
                ).fetchall()
            ]
            sport_bad_team_keys = [
                row[0]
                for row in cur.execute(
                    f"""
                    SELECT tk.team_key
                      FROM compact_team_keys tk
                      JOIN sport_teams t
                        ON t.sport_id = tk.scope
                       AND t.team_id = tk.team_id
                       AND t.season = tk.season
                     WHERE {sport_bad_team_sql('t')}
                    """
                ).fetchall()
            ]
            sport_live_games = cur.execute(
                f"""
                SELECT COUNT(DISTINCT g.sport_id || ':' || g.game_id)
                FROM sport_live_game_imports g
                WHERE EXISTS (
                    SELECT 1
                    FROM sport_live_player_games pg
                    JOIN sport_teams t
                      ON t.sport_id = pg.sport_id
                     AND t.team_id = pg.team_id
                     AND t.season = pg.season
                    WHERE pg.sport_id = g.sport_id
                      AND pg.game_id = g.game_id
                      AND ({sport_bad_team_sql('t')})
                )
                """
            ).fetchone()[0]
            cur.execute(
                f"""
                DELETE FROM sport_live_game_imports g
                WHERE EXISTS (
                    SELECT 1
                    FROM sport_live_player_games pg
                    JOIN sport_teams t
                      ON t.sport_id = pg.sport_id
                     AND t.team_id = pg.team_id
                     AND t.season = pg.season
                    WHERE pg.sport_id = g.sport_id
                      AND pg.game_id = g.game_id
                      AND ({sport_bad_team_sql('t')})
                )
                """
            )
            sport_appearances = cur.execute(
                f"""
                DELETE FROM sport_appearances a
                USING sport_teams t
                WHERE t.sport_id = a.sport_id
                  AND t.team_id = a.team_id
                  AND t.season = a.season
                  AND ({sport_bad_team_sql('t')})
                """
            ).rowcount
            cur.execute(
                f"""
                DELETE FROM sport_player_stints s
                USING sport_teams t
                WHERE t.sport_id = s.sport_id
                  AND t.team_id = s.team_id
                  AND t.season = s.season
                  AND ({sport_bad_team_sql('t')})
                """
            )
            if sport_bad_team_keys:
                if cur.execute("SELECT to_regclass('compact_sport_teammates')").fetchone()[0]:
                    cur.execute(
                        "DELETE FROM compact_sport_teammates WHERE team_key = ANY(%s)",
                        (sport_bad_team_keys,),
                    )
                cur.execute(
                    "DELETE FROM compact_team_keys WHERE team_key = ANY(%s)",
                    (sport_bad_team_keys,),
                )
            sport_teams = cur.execute(f"DELETE FROM sport_teams t WHERE {sport_bad_team_sql('t')}").rowcount
            if sport_affected:
                affected_by_sport: dict[str, list[str]] = {}
                for sport_id, player_id in sport_affected:
                    affected_by_sport.setdefault(sport_id, []).append(player_id)
                for sport_id, player_ids in affected_by_sport.items():
                    cur.execute(
                        """
                        DELETE FROM sport_players_searchable s
                         WHERE s.sport_id = %s
                           AND s.player_id = ANY(%s)
                           AND NOT EXISTS (
                               SELECT 1 FROM sport_appearances a
                                WHERE a.sport_id = s.sport_id
                                  AND a.player_id = s.player_id
                           )
                        """,
                        (sport_id, player_ids),
                    )
            cur.execute(
                """
                DELETE FROM sport_players_searchable s
                 WHERE s.sport_id = 'basketball'
                   AND NOT EXISTS (
                       SELECT 1 FROM sport_appearances a
                        WHERE a.sport_id = s.sport_id
                          AND a.player_id = s.player_id
                   )
                """
            )
            cur.execute(
                """
                UPDATE sport_data_provenance
                SET row_count = (
                    SELECT COUNT(*) FROM sport_live_player_games
                    WHERE sport_live_player_games.sport_id = sport_data_provenance.sport_id
                      AND sport_live_player_games.season = sport_data_provenance.season
                ),
                    fetched_at = now()
                WHERE source IN ('nhl_web_api_game_log')
                """
            )
        refresh_baseball_search(conn, baseball_players)
        refresh_sport_search(conn, sport_affected)
        conn.commit()

    print(f"Removed {baseball_appearances} baseball exhibition appearances from {baseball_teams} team-seasons.")
    print(f"Removed {baseball_live_games} baseball live exhibition games.")
    print(f"Removed {sport_appearances} sport exhibition appearances from {sport_teams} team-seasons.")
    print(f"Removed {sport_live_games} sport live exhibition games.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
