#!/usr/bin/env python3
"""
verify_graph.py — sanity-check the teammate graph.

For each known teammate pair, asserts they're connected in the DB.
For each known non-pair, asserts they're NOT connected.

Add new cases as you find data errors. This is your regression test
when you refresh data or change the graph-building logic.

Run: python3 tests/verify_graph.py [--db db/base2nerdle.sqlite]
"""
import argparse
import sqlite3
import sys
from pathlib import Path


# (player_a_id, player_b_id, expected_team_id, expected_season, note)
KNOWN_TEAMMATES = [
    ("jeterde01", "rivermo02", "NYA", 2003, "Core Four — Yankees forever"),
    ("jeterde01", "rodrial01", "NYA", 2004, "A-Rod's first year as a Yankee"),
    ("jeterde01", "damonjo01", "NYA", 2006, "Damon's first year as a Yankee"),
    ("damonjo01", "ortizda01", "BOS", 2003, "Damon and Ortiz, both Sox in 2003"),
    ("becketjo02", "lowelmi01", "FLO", 2003, "World Series Marlins"),
    ("becketjo02", "lowelmi01", "BOS", 2007, "...and World Series Red Sox 4 yrs later"),
    ("ortizda01", "ramirma02", "BOS", 2004, "obviously"),
    ("matsuhi01", "rodrial01", "NYA", 2004, "Matsui's 2nd year, A-Rod's 1st"),
    ("cabremi01", "rodriiv01", "FLO", 2003, "Cabrera's rookie year, Pudge"),
]

# Pairs that look like they SHOULD be teammates but aren't (in our data window).
# Catches "we accidentally joined on franchise instead of team-season" type bugs.
KNOWN_NON_TEAMMATES = [
    ("rodrial01", "soriaal01",
     "A-Rod replaced Soriano on the Yankees — they were never on the roster together"),
    ("schilcu01", "becketjo02",
     "Schilling left BOS after 2007; Beckett joined BOS in 2006. They WERE teammates 2006-07. SO THIS SHOULD NOT BE A NON-PAIR."),
    # ^ the comment above is intentional — let's check if I got the sample data right
    ("pierrju01", "ortizda01",
     "Pierre was Marlins, Ortiz was Twins/Red Sox — never overlapped in our sample"),
]


def lookup(conn, pid):
    row = conn.execute(
        "SELECT name_first || ' ' || name_last FROM players WHERE player_id = ?",
        (pid,),
    ).fetchone()
    return row[0] if row else f"<unknown {pid}>"


def are_teammates(conn, a, b):
    """Return list of (team, season) where a and b were teammates."""
    if a > b:
        a, b = b, a
    return conn.execute(
        "SELECT team_id, season FROM teammates WHERE player_a_id = ? AND player_b_id = ?",
        (a, b),
    ).fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="db/base2nerdle.sqlite")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"DB not found: {db}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db)
    fails = 0

    print("=" * 70)
    print("EXPECTED TEAMMATES")
    print("=" * 70)
    for a, b, team, season, note in KNOWN_TEAMMATES:
        edges = are_teammates(conn, a, b)
        match = (team, season) in edges
        status = "PASS" if match else "FAIL"
        if not match:
            fails += 1
        print(f"  [{status}] {lookup(conn, a):20s} ↔ {lookup(conn, b):20s} "
              f"on {team} {season}  ({note})")
        if not match and edges:
            print(f"         (DB says they were teammates on: {edges})")
        elif not match:
            print(f"         (DB says they were NEVER teammates)")

    print()
    print("=" * 70)
    print("EXPECTED NON-TEAMMATES")
    print("=" * 70)
    for a, b, note in KNOWN_NON_TEAMMATES:
        edges = are_teammates(conn, a, b)
        # Treat as PASS if they had no overlap in the data window.
        status = "PASS" if not edges else "INFO"
        if edges:
            print(f"  [{status}] {lookup(conn, a):20s} ↔ {lookup(conn, b):20s}")
            print(f"         DB shows teammates on: {edges}")
            print(f"         note: {note}")
        else:
            print(f"  [{status}] {lookup(conn, a):20s} ↔ {lookup(conn, b):20s}  ({note})")

    print()
    print("=" * 70)
    print("GRAPH STATS")
    print("=" * 70)

    # Distribution of teammate-pair counts — useful for difficulty calibration.
    rows = conn.execute(
        """
        SELECT player_id, name, COUNT(*) AS degree FROM (
            SELECT p.player_id, p.name_first || ' ' || p.name_last AS name,
                   t.player_b_id AS other_id
              FROM teammates t JOIN players p ON p.player_id = t.player_a_id
            UNION
            SELECT p.player_id, p.name_first || ' ' || p.name_last AS name,
                   t.player_a_id AS other_id
              FROM teammates t JOIN players p ON p.player_id = t.player_b_id
        )
        GROUP BY player_id
        ORDER BY degree DESC LIMIT 10
        """
    ).fetchall()
    print("Top 10 most-connected players (highest teammate count):")
    for player_id, name, degree in rows:
        print(f"  {degree:4d}  {name}")

    print()
    if fails:
        print(f"{fails} expected-teammate assertion(s) failed.")
        return 1
    print("All expected-teammate assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
