"""Small, bounded updates for the packed Supabase teammate graph."""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable


EDGE_SHIFT = 32
EDGE_MASK = (1 << EDGE_SHIFT) - 1


def _edge(other_key: int, team_key: int) -> int:
    return (int(other_key) << EDGE_SHIFT) | int(team_key)


def uses_compact_adjacency(cur) -> bool:
    return bool(cur.execute("SELECT to_regclass('compact_teammate_adjacency')").fetchone()[0])


def replace_live_season_proofs(
    cur,
    scope: str,
    season: int,
    proofs: Iterable[tuple[str, str, str, int]],
) -> None:
    """Replace one current season without rewriting historical graph payloads."""
    source = {(str(a), str(b), str(team), int(year)) for a, b, team, year in proofs}
    player_ids = sorted({player for a, b, _team, _year in source for player in (a, b)})
    team_ids = sorted({team for _a, _b, team, year in source if year == season})
    player_keys = {}
    team_keys = {}
    if player_ids:
        cur.execute(
            "SELECT player_id, player_key FROM compact_player_keys WHERE scope=%s AND player_id = ANY(%s)",
            (scope, player_ids),
        )
        player_keys = {str(player_id): int(key) for player_id, key in cur.fetchall()}
    if team_ids:
        cur.execute(
            """SELECT team_id, team_key FROM compact_team_keys
                 WHERE scope=%s AND season=%s AND team_id = ANY(%s)""",
            (scope, season, team_ids),
        )
        team_keys = {str(team_id): int(key) for team_id, key in cur.fetchall()}
    replacement = {
        (player_keys[a], player_keys[b], team_keys[team])
        for a, b, team, year in source
        if year == season and a in player_keys and b in player_keys and team in team_keys
    }
    if len(replacement) != len(source):
        raise RuntimeError("compact key lookup missed a current-season teammate proof")

    cur.execute(
        """SELECT player_a_key, player_b_key, team_key
             FROM compact_live_season_proofs WHERE scope=%s AND season=%s""",
        (scope, season),
    )
    previous = {(int(a), int(b), int(team)) for a, b, team in cur.fetchall()}
    affected: dict[int, set[int]] = defaultdict(set)
    removed: dict[int, set[int]] = defaultdict(set)
    for a, b, team in previous:
        affected[a].add(a)
        affected[b].add(b)
        removed[a].add(_edge(b, team))
        removed[b].add(_edge(a, team))
    additions: dict[int, set[int]] = defaultdict(set)
    for a, b, team in replacement:
        affected[a].add(a)
        affected[b].add(b)
        additions[a].add(_edge(b, team))
        additions[b].add(_edge(a, team))

    affected_keys = sorted(affected)
    existing: dict[int, set[int]] = {}
    if affected_keys:
        cur.execute(
            "SELECT player_key, edge_codes FROM compact_teammate_adjacency WHERE player_key = ANY(%s)",
            (affected_keys,),
        )
        existing = {int(key): {int(code) for code in (codes or [])} for key, codes in cur.fetchall()}
    rows = []
    for player_key in affected_keys:
        codes = existing.get(player_key, set()) - removed[player_key]
        codes.update(additions[player_key])
        rows.append((player_key, sorted(codes)))
    if rows:
        cur.executemany(
            """INSERT INTO compact_teammate_adjacency (player_key, edge_codes, updated_at)
                 VALUES (%s, %s, now())
                 ON CONFLICT (player_key) DO UPDATE
                 SET edge_codes=EXCLUDED.edge_codes, updated_at=now()""",
            rows,
        )
    cur.execute("DELETE FROM compact_live_season_proofs WHERE scope=%s AND season=%s", (scope, season))
    if replacement:
        cur.executemany(
            """INSERT INTO compact_live_season_proofs
                    (scope, season, player_a_key, player_b_key, team_key)
                 VALUES (%s, %s, %s, %s, %s)""",
            [(scope, season, a, b, team) for a, b, team in sorted(replacement)],
        )
