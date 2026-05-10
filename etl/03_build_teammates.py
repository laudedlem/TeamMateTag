#!/usr/bin/env python3
"""
03_build_teammates.py — derive the teammate graph.

Reads the appearances table and writes one row per (player_a, player_b,
team, season) where both players appeared in >= 1 game for that team
in that season. This is the table the game queries on every move.

The "at least one game together" rule, as discussed: this is a
season-level proxy. If player A was traded away in May and player B
was called up in August, this WILL list them as teammates even though
they never shared a clubhouse. Resolving that requires game-log data
from Retrosheet, which is a much bigger lift; the "report wrong
connection" UX in the game handles the rare false positives.

Idempotent. Safe to re-run after refreshing appearances. Can rebuild
just specific seasons via --season for fast incremental updates.
"""
import argparse
import sqlite3
import sys
import time
from pathlib import Path


def build_teammates(conn: sqlite3.Connection, season: int | None = None) -> int:
    """Returns count of teammate edges written."""
    cur = conn.cursor()

    if season is not None:
        cur.execute("DELETE FROM teammates WHERE season = ?", (season,))
        season_filter = "WHERE a.season = ?"
        params: tuple = (season,)
    else:
        cur.execute("DELETE FROM teammates")
        season_filter = ""
        params = ()

    # Self-join appearances on (team_id, season). The CHECK constraint
    # player_a_id < player_b_id is enforced in the join condition, which
    # also halves the work (we'd otherwise produce both A,B and B,A).
    sql = f"""
        INSERT INTO teammates (player_a_id, player_b_id, team_id, season)
        SELECT a.player_id, b.player_id, a.team_id, a.season
          FROM appearances a
          JOIN appearances b
            ON a.team_id = b.team_id
           AND a.season  = b.season
           AND a.player_id < b.player_id
         {season_filter}
    """
    cur.execute(sql, params)
    count = cur.rowcount
    conn.commit()
    return count


def rebuild_searchable(conn: sqlite3.Connection, min_career_games: int = 1) -> int:
    """
    Regenerate the players_searchable table.

    `min_career_games` lets you tune what counts as "answerable." Default 1
    (everyone who's played) for the initial build; raise it later if September
    call-ups cluttering the autocomplete becomes an issue.

    Search keys go through `name_normalize.normalize()` so the user can type
    "jose bautista" or "jd drew" and find matches without diacritics/periods.
    The same normalizer must run on the query side at lookup time.
    """
    # Local import so this script is runnable standalone without the
    # scripts/ dir being on PYTHONPATH at module-load time.
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "scripts"))
    from name_normalize import normalize, first_last

    cur = conn.cursor()
    cur.execute("DELETE FROM players_searchable")

    # Pull raw rows + computed stats; normalize in Python.
    rows = cur.execute(
        """
        SELECT
            p.player_id,
            p.name_first, p.name_last, p.primary_pos,
            p.debut_year, p.final_year, p.name_nick,
            COALESCE(ag.career_games, 0) AS career_games,
            COALESCE(td.degree, 0) AS teammate_count
        FROM players p
        LEFT JOIN (
            SELECT player_id, SUM(games_total) AS career_games
              FROM appearances GROUP BY player_id
        ) ag ON ag.player_id = p.player_id
        LEFT JOIN (
            SELECT player_id, COUNT(*) AS degree FROM (
                SELECT player_a_id AS player_id, player_b_id AS other FROM teammates
                UNION
                SELECT player_b_id AS player_id, player_a_id AS other FROM teammates
            ) GROUP BY player_id
        ) td ON td.player_id = p.player_id
        WHERE COALESCE(ag.career_games, 0) >= ?
        """,
        (min_career_games,),
    ).fetchall()

    out = []
    nick_index = []   # rows for the nickname search index
    for (player_id, first, last, pos, debut, final, nick, career_games, degree) in rows:
        display = f"{first or ''} {last or ''}".strip()
        if debut and final:
            disambig = f"{pos or '?'}, {debut}-{final}"
        elif debut:
            disambig = f"{pos or '?'}, {debut}-?"
        else:
            disambig = pos or "?"
        search_key = first_last(first, last)
        last_key = normalize(last or "")
        out.append((
            player_id, display, disambig, search_key, last_key,
            career_games, degree,
        ))
        if nick:
            for n in nick.split(","):
                n = normalize(n)
                if n:
                    nick_index.append((player_id, n))

    cur.executemany(
        """INSERT INTO players_searchable
           (player_id, display_name, disambiguation, search_key, last_key,
            career_games, teammate_count)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        out,
    )

    # Nickname search index: secondary lookup table.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS nickname_search (
            nickname_key TEXT NOT NULL,
            player_id    TEXT NOT NULL REFERENCES players(player_id),
            PRIMARY KEY (nickname_key, player_id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_nickname_key ON nickname_search(nickname_key)")
    cur.execute("DELETE FROM nickname_search")
    cur.executemany(
        "INSERT OR IGNORE INTO nickname_search (nickname_key, player_id) VALUES (?, ?)",
        [(n, pid) for (pid, n) in nick_index],
    )

    conn.commit()
    return len(out)


def derive_primary_positions(conn: sqlite3.Connection):
    """Pitcher if >50% of career games were pitching, else position player.
    Crude but useful for the disambiguation label. Refine later with full
    Fielding.csv data."""
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE players SET primary_pos = (
            SELECT CASE
                WHEN SUM(games_pitched) * 1.0 / NULLIF(SUM(games_total), 0) > 0.5 THEN 'P'
                ELSE 'POS'
            END
            FROM appearances WHERE appearances.player_id = players.player_id
        )
        WHERE EXISTS (SELECT 1 FROM appearances WHERE appearances.player_id = players.player_id)
        """
    )
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="db/base2nerdle.sqlite")
    ap.add_argument("--season", type=int, help="rebuild only this season (for daily updates)")
    ap.add_argument(
        "--min-career-games",
        type=int,
        default=1,
        help="players with fewer career games excluded from autocomplete (raise to filter cup-of-coffee guys)",
    )
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"DB not found: {db}. Run 02_load_lahman.py first.", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")

    t0 = time.time()
    print(f"deriving primary positions")
    derive_primary_positions(conn)

    print(f"building teammate graph" + (f" for season {args.season}" if args.season else ""))
    n = build_teammates(conn, season=args.season)
    print(f"  -> {n:,} teammate edges in {time.time() - t0:.1f}s")

    print(f"rebuilding searchable index (min_career_games={args.min_career_games})")
    n = rebuild_searchable(conn, min_career_games=args.min_career_games)
    print(f"  -> {n:,} searchable players")

    # Useful sanity stats.
    rows = conn.execute(
        """SELECT
             (SELECT COUNT(*) FROM players),
             (SELECT COUNT(*) FROM appearances),
             (SELECT COUNT(*) FROM teammates),
             (SELECT MIN(season) FROM appearances),
             (SELECT MAX(season) FROM appearances)"""
    ).fetchone()
    print(f"\nstats: {rows[0]:,} players, {rows[1]:,} appearances, "
          f"{rows[2]:,} teammate edges, seasons {rows[3]}-{rows[4]}")

    conn.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
