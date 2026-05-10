#!/usr/bin/env python3
"""
query_examples.py — the queries your multiplayer server will run.

Every game move executes a small set of these. They're shown in plain
SQLite/Python here, but they translate directly to a Postgres function
or a Supabase edge function. Each one is an O(log N) index lookup.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from name_normalize import normalize  # noqa: E402

DB = "db/base2nerdle.sqlite"
conn = sqlite3.connect(DB)


def autocomplete(prefix: str, limit: int = 10):
    """
    Search-as-you-type. Matches against:
      - full normalized name ("derek jet..." -> Derek Jeter)
      - last name only ("jet..." -> Jeter)
      - nicknames if the Chadwick load has run ("big papi" -> Ortiz)
    Ranked by career_games as a fame proxy.
    """
    p = normalize(prefix)
    if not p:
        return []
    return conn.execute(
        """
        SELECT DISTINCT ps.player_id, ps.display_name, ps.disambiguation, ps.career_games
          FROM players_searchable ps
          LEFT JOIN nickname_search ns ON ns.player_id = ps.player_id
         WHERE ps.search_key LIKE ? || '%'
            OR ps.last_key   LIKE ? || '%'
            OR ns.nickname_key LIKE ? || '%'
         ORDER BY ps.career_games DESC
         LIMIT ?
        """,
        (p, p, p, limit),
    ).fetchall()


def teammate_check(prev_player_id: str, guessed_player_id: str):
    """
    THE core game query. Was the guess ever a teammate of the previous player?
    Returns the list of (team, season) pairs they shared, or empty list.
    """
    a, b = sorted([prev_player_id, guessed_player_id])
    return conn.execute(
        """SELECT team_id, season FROM teammates
            WHERE player_a_id = ? AND player_b_id = ?
            ORDER BY season""",
        (a, b),
    ).fetchall()


def is_valid_move(chain: list[str], guessed_player_id: str):
    """
    Full validation: the move is valid iff the guess (1) is a real player,
    (2) hasn't been used in this chain, and (3) was a teammate of the most
    recent player in the chain.
    Returns (is_valid: bool, reason: str, evidence: list).
    """
    # (1) Real player?
    row = conn.execute(
        "SELECT 1 FROM players_searchable WHERE player_id = ?",
        (guessed_player_id,),
    ).fetchone()
    if not row:
        return (False, "unknown_player", [])

    # (2) Already used?
    if guessed_player_id in chain:
        return (False, "already_used", [])

    # (3) Teammate of most recent?
    if not chain:
        return (True, "first_move", [])
    evidence = teammate_check(chain[-1], guessed_player_id)
    if not evidence:
        return (False, "not_teammate", [])
    return (True, "valid", evidence)


def hint_for_player(player_id: str, exclude: set[str], limit: int = 5):
    """
    Possible next moves from this player, excluding already-used players.
    Useful for hints, AI opponents, and difficulty scoring.
    """
    placeholders = ",".join("?" * len(exclude)) or "''"
    rows = conn.execute(
        f"""SELECT DISTINCT
                CASE WHEN t.player_a_id = ? THEN t.player_b_id ELSE t.player_a_id END
                  AS other_id,
                ps.display_name, ps.career_games
              FROM teammates t
              JOIN players_searchable ps
                ON ps.player_id = CASE
                    WHEN t.player_a_id = ? THEN t.player_b_id ELSE t.player_a_id
                END
             WHERE (t.player_a_id = ? OR t.player_b_id = ?)
               AND ps.player_id NOT IN ({placeholders})
             ORDER BY ps.career_games DESC
             LIMIT ?""",
        (player_id, player_id, player_id, player_id, *exclude, limit),
    ).fetchall()
    return rows


# ============================================================
# DEMO: walk through a sample chain
# ============================================================
if __name__ == "__main__":
    print("=" * 70)
    print("AUTOCOMPLETE: 'jet'")
    print("=" * 70)
    for row in autocomplete("jet"):
        print(f"  {row}")

    print()
    print("=" * 70)
    print("SAMPLE CHAIN")
    print("=" * 70)
    chain: list[str] = []
    moves = [
        "jeterde01",     # Jeter — first move
        "rivermo02",     # Rivera — Yankees teammate, valid
        "posadjo01",     # Posada — Yankees teammate, valid
        "rodrial01",     # A-Rod — also Yankees, valid
        "rodriiv01",     # Pudge — never a teammate of A-Rod in 2000+ window? Should fail.
        "matsuhi01",     # Matsui — A-Rod teammate on Yankees, valid
        "damonjo01",     # Damon — Matsui teammate (Yankees), valid
        "ortizda01",     # Ortiz — Damon teammate on Red Sox, valid
        "becketjo02",    # Beckett — Ortiz teammate on '07/'08 Sox, valid
        "lowelmi01",     # Lowell — Beckett teammate (Marlins '03 & Sox '07+), valid
        "cabremi01",     # Cabrera — Lowell teammate on '03-05 Marlins, valid
        "rodrial01",     # A-Rod again — already used, should fail
    ]
    for move in moves:
        valid, reason, evidence = is_valid_move(chain, move)
        name = conn.execute(
            "SELECT name_first || ' ' || name_last FROM players WHERE player_id = ?",
            (move,),
        ).fetchone()[0]
        if valid:
            chain.append(move)
            evid = f"  [{', '.join(f'{t}{s}' for t,s in evidence)}]" if evidence else ""
            print(f"  + {name:20s} VALID ({reason}){evid}")
        else:
            print(f"  ✗ {name:20s} INVALID ({reason}) — chain ends")
            break

    print()
    print(f"Final chain length: {len(chain)}")

    print()
    print("=" * 70)
    print("HINTS for next move from end of chain")
    print("=" * 70)
    if chain:
        for row in hint_for_player(chain[-1], set(chain), limit=10):
            print(f"  {row[1]:25s} ({row[2]} career games)")
