"""Expand production baseball data from 2000-present to the Lahman era.

This is an additive migration for the existing production database. It loads
1871-1999 from raw Lahman CSVs, retains current 2000-2025 data, then rebuilds
the teammate graph and autocomplete index across the complete window.

Run from the repository root:
    python scripts/expand_baseball_history.py

Do not use one-off Postgres migration loaders for this task. Production data
should be refreshed from compact runtime/import paths, not table-truncating
baseline loaders.
"""
from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

import psycopg

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent


def read_rows(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=1871)
    parser.add_argument("--end-year", type=int, default=1999)
    parser.add_argument("--raw-dir", default=str(ROOT / "raw"))
    args = parser.parse_args()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required. Set it in .env first.")
    raw_dir = Path(args.raw_dir)
    for filename in ("People.csv", "Teams.csv", "Appearances.csv"):
        if not (raw_dir / filename).exists():
            raise SystemExit(f"Missing {raw_dir / filename}")

    people = []
    for row in read_rows(raw_dir / "People.csv"):
        debut_raw = (row.get("debut") or "")[:4]
        final_raw = (row.get("finalGame") or "")[:4]
        if not debut_raw.isdigit():
            continue
        debut = int(debut_raw)
        final = int(final_raw) if final_raw.isdigit() else debut
        if final < args.start_year or debut > args.end_year:
            continue
        people.append((
            row["playerID"], row.get("bbrefID") or None, row.get("retroID") or None,
            row.get("nameFirst") or None, row.get("nameLast") or None,
            row.get("nameGiven") or None,
            int(row["birthYear"]) if (row.get("birthYear") or "").isdigit() else None,
            debut, final, row.get("bats") or None, row.get("throws") or None,
        ))

    franchises: dict[str, str] = {}
    teams = []
    for row in read_rows(raw_dir / "Teams.csv"):
        season = int(row["yearID"])
        if not args.start_year <= season <= args.end_year:
            continue
        franchise_id = row["franchID"]
        franchises.setdefault(franchise_id, row.get("name") or franchise_id)
        teams.append((row["teamID"], season, franchise_id, row.get("lgID") or None, row.get("name") or None))

    valid_player_ids = {row[0] for row in people}
    appearances = []
    skipped_orphans = 0
    for row in read_rows(raw_dir / "Appearances.csv"):
        season = int(row["yearID"])
        games_total = int(row.get("G_all") or 0)
        if args.start_year <= season <= args.end_year and games_total > 0:
            if row["playerID"] not in valid_player_ids:
                skipped_orphans += 1
                continue
            appearances.append((
                row["playerID"], row["teamID"], season, games_total,
                int(row.get("G_p") or 0), int(row.get("G_batting") or 0),
            ))

    if skipped_orphans:
        print(f"Skipping {skipped_orphans:,} Lahman appearances with no matching player record.")

    with psycopg.connect(database_url, autocommit=True, prepare_threshold=None) as conn:
        conn.execute("SET default_transaction_read_only = off")
        conn.cursor().executemany(
            """INSERT INTO players
               (player_id, bbref_id, retro_id, name_first, name_last, name_given,
                birth_year, debut_year, final_year, bats, throws)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (player_id) DO UPDATE
               SET bbref_id = COALESCE(players.bbref_id, EXCLUDED.bbref_id),
                   retro_id = COALESCE(players.retro_id, EXCLUDED.retro_id),
                   name_first = COALESCE(players.name_first, EXCLUDED.name_first),
                   name_last = COALESCE(players.name_last, EXCLUDED.name_last),
                   name_given = COALESCE(players.name_given, EXCLUDED.name_given),
                   birth_year = COALESCE(players.birth_year, EXCLUDED.birth_year),
                   debut_year = LEAST(players.debut_year, EXCLUDED.debut_year),
                   final_year = GREATEST(players.final_year, EXCLUDED.final_year),
                   bats = COALESCE(players.bats, EXCLUDED.bats),
                   throws = COALESCE(players.throws, EXCLUDED.throws)""",
            people,
        )
        conn.cursor().executemany(
            """INSERT INTO franchises (franchise_id, name)
               VALUES (%s, %s) ON CONFLICT (franchise_id) DO NOTHING""",
            list(franchises.items()),
        )
        conn.cursor().executemany(
            """INSERT INTO teams (team_id, season, franchise_id, league, name)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (team_id, season) DO UPDATE
               SET franchise_id = EXCLUDED.franchise_id,
                   league = EXCLUDED.league, name = EXCLUDED.name""",
            teams,
        )
        conn.cursor().executemany(
            """INSERT INTO appearances
               (player_id, team_id, season, games_total, games_pitched, games_batted)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (player_id, team_id, season) DO UPDATE
               SET games_total = EXCLUDED.games_total,
                   games_pitched = EXCLUDED.games_pitched,
                   games_batted = EXCLUDED.games_batted""",
            appearances,
        )
        print(f"Loaded {len(people):,} players, {len(teams):,} team-seasons, "
              f"and {len(appearances):,} player-team-seasons.")

        print("Rebuilding the baseball teammate graph. This can take several minutes.")
        conn.execute("DELETE FROM teammates")
        edges = conn.execute(
            """WITH inserted AS (
                   INSERT INTO teammates (player_a_id, player_b_id, team_id, season)
                   SELECT a.player_id, b.player_id, a.team_id, a.season
                     FROM appearances a
                     JOIN appearances b
                       ON a.team_id = b.team_id
                      AND a.season = b.season
                      AND a.player_id < b.player_id
                   RETURNING 1
               ) SELECT COUNT(*) FROM inserted"""
        ).fetchone()[0]
        conn.execute(
            """UPDATE players p SET primary_pos = src.primary_pos
                 FROM (
                     SELECT player_id,
                            CASE WHEN SUM(games_pitched)::float /
                                      NULLIF(SUM(games_total), 0) > 0.5
                                 THEN 'P' ELSE 'POS' END AS primary_pos
                       FROM appearances GROUP BY player_id
                 ) src WHERE src.player_id = p.player_id"""
        )
        rows = conn.execute(
            """SELECT p.player_id, p.name_first, p.name_last, p.primary_pos,
                      p.debut_year, p.final_year, COALESCE(p.name_nick, ''),
                      COALESCE(g.games, 0), COALESCE(d.degree, 0)
                 FROM players p
                 JOIN (SELECT player_id, SUM(games_total) AS games
                         FROM appearances GROUP BY player_id) g ON g.player_id = p.player_id
                 LEFT JOIN (
                     SELECT player_id, COUNT(*) AS degree FROM (
                         SELECT player_a_id AS player_id FROM teammates
                         UNION ALL SELECT player_b_id FROM teammates
                     ) x GROUP BY player_id
                 ) d ON d.player_id = p.player_id"""
        ).fetchall()
        conn.execute("DELETE FROM players_searchable")
        conn.cursor().executemany(
            """INSERT INTO players_searchable
               (player_id, display_name, disambiguation, search_key, last_key,
                career_games, teammate_count)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            [
                (
                    player_id, f"{first or ''} {last or ''}".strip(),
                    f"{pos or '?'}, {debut}-{final or '?'}",
                    f"{first or ''}{last or ''}".lower().replace(" ", ""),
                    (last or "").lower().replace(" ", ""), int(games), int(degree),
                )
                for player_id, first, last, pos, debut, final, nick, games, degree in rows
            ],
        )
        conn.execute(
            """INSERT INTO data_provenance (source, season, row_count)
               VALUES ('lahman_history_expansion', %s, %s)
               ON CONFLICT (source, season) DO UPDATE
               SET row_count = EXCLUDED.row_count, fetched_at = now()""",
            (args.end_year, len(appearances)),
        )
    print(f"Baseball history is now {args.start_year}-2025 with {edges:,} teammate edges.")


if __name__ == "__main__":
    main()
