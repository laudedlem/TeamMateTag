#!/usr/bin/env python3
"""
02_load_lahman.py — load Lahman CSVs into the database.

Filters by season range (default 2000+) so the initial DB stays small.
Re-running with a wider range is how you "expand backward" later: just
run with --start-year 1990 and you get the older data added.

Idempotent: uses INSERT OR REPLACE so re-running with refreshed CSVs
updates existing rows without creating duplicates.
"""
import argparse
import csv
import sqlite3
import sys
from pathlib import Path


def init_db(db_path: Path, schema_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    with open(schema_path) as f:
        conn.executescript(f.read())
    return conn


def load_people(conn: sqlite3.Connection, csv_path: Path, debut_min_year: int) -> int:
    """Load players. Filters out players who never debuted in our window
    (or never debuted at all — managers/umpires only)."""
    rows = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            debut = r.get("debut") or ""
            if not debut:
                continue
            try:
                debut_year = int(debut[:4])
            except ValueError:
                continue
            final = r.get("finalGame") or ""
            try:
                final_year = int(final[:4]) if final else None
            except ValueError:
                final_year = None
            # Player must have played at least one game in or after debut_min_year
            # (so a guy who retired in 1995 is filtered out for a 2000+ DB).
            last_active = final_year or debut_year
            if last_active < debut_min_year:
                continue

            rows.append((
                r["playerID"],
                r.get("bbrefID") or None,
                r.get("retroID") or None,
                None,  # mlbam_id filled by 04_load_chadwick_ids.py
                r.get("nameFirst") or None,
                r.get("nameLast") or None,
                r.get("nameGiven") or None,
                int(r["birthYear"]) if r.get("birthYear") else None,
                debut_year,
                final_year,
                r.get("bats") or None,
                r.get("throws") or None,
                None,  # primary_pos derived later
            ))

    conn.executemany(
        """INSERT OR REPLACE INTO players
           (player_id, bbref_id, retro_id, mlbam_id, name_first, name_last,
            name_given, birth_year, debut_year, final_year, bats, throws, primary_pos, name_nick)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
        rows,
    )
    return len(rows)


def load_teams(conn: sqlite3.Connection, csv_path: Path, start_year: int, end_year: int) -> int:
    franchises = {}
    teams = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            year = int(r["yearID"])
            if not (start_year <= year <= end_year):
                continue
            franch_id = r["franchID"]
            franchises.setdefault(franch_id, r.get("name") or franch_id)
            teams.append((
                r["teamID"],
                year,
                franch_id,
                r.get("lgID") or None,
                r.get("name") or None,
            ))

    conn.executemany(
        "INSERT OR REPLACE INTO franchises (franchise_id, name) VALUES (?, ?)",
        list(franchises.items()),
    )
    conn.executemany(
        """INSERT OR REPLACE INTO teams (team_id, season, franchise_id, league, name)
           VALUES (?, ?, ?, ?, ?)""",
        teams,
    )
    return len(teams)


def load_appearances(conn: sqlite3.Connection, csv_path: Path, start_year: int, end_year: int) -> int:
    # Build a set of valid (player_id, team_id, season) we know about, so we
    # don't insert orphan rows that violate FK constraints.
    valid_players = {p[0] for p in conn.execute("SELECT player_id FROM players")}
    valid_teams = {(t[0], t[1]) for t in conn.execute("SELECT team_id, season FROM teams")}

    rows = []
    skipped_player = skipped_team = 0
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            year = int(r["yearID"])
            if not (start_year <= year <= end_year):
                continue

            player_id = r["playerID"]
            team_id = r["teamID"]
            if player_id not in valid_players:
                skipped_player += 1
                continue
            if (team_id, year) not in valid_teams:
                skipped_team += 1
                continue

            g_all = int(r.get("G_all") or 0)
            if g_all == 0:
                # The "must have played in at least one game together" rule
                # means we drop zero-game appearances entirely.
                continue

            rows.append((
                player_id, team_id, year, g_all,
                int(r.get("G_p") or 0),
                int(r.get("G_batting") or 0),
            ))

    conn.executemany(
        """INSERT OR REPLACE INTO appearances
           (player_id, team_id, season, games_total, games_pitched, games_batted)
           VALUES (?, ?, ?, ?, ?, ?)""",
        rows,
    )
    if skipped_player or skipped_team:
        print(f"  skipped: {skipped_player} unknown players, {skipped_team} unknown teams "
              f"(expected — these had careers entirely outside our window)")
    return len(rows)


def record_provenance(conn: sqlite3.Connection, source: str, season: int | None, count: int):
    conn.execute(
        "INSERT OR REPLACE INTO data_provenance (source, season, row_count) VALUES (?, ?, ?)",
        (source, season, count),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", default="raw", help="dir containing Lahman CSVs")
    ap.add_argument("--db", default="db/base2nerdle.sqlite")
    ap.add_argument("--schema", default="db/schema.sql")
    ap.add_argument("--start-year", type=int, default=2000)
    ap.add_argument("--end-year", type=int, default=2100)
    args = ap.parse_args()

    raw = Path(args.raw_dir)
    for f in ("People.csv", "Teams.csv", "Appearances.csv"):
        if not (raw / f).exists():
            print(f"missing {raw / f} — run 01_download_lahman.py first", file=sys.stderr)
            return 1

    conn = init_db(Path(args.db), Path(args.schema))

    print(f"loading People.csv (debut_min_year={args.start_year})")
    n = load_people(conn, raw / "People.csv", args.start_year)
    print(f"  -> {n:,} players")
    record_provenance(conn, "lahman_people", None, n)

    print(f"loading Teams.csv (years {args.start_year}-{args.end_year})")
    n = load_teams(conn, raw / "Teams.csv", args.start_year, args.end_year)
    print(f"  -> {n:,} team-seasons")
    record_provenance(conn, "lahman_teams", None, n)

    print(f"loading Appearances.csv (years {args.start_year}-{args.end_year})")
    n = load_appearances(conn, raw / "Appearances.csv", args.start_year, args.end_year)
    print(f"  -> {n:,} player-team-seasons")
    record_provenance(conn, "lahman_appearances", None, n)

    conn.commit()
    conn.close()
    print(f"\nDB written to {args.db}")


if __name__ == "__main__":
    sys.exit(main() or 0)
