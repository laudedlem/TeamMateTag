#!/usr/bin/env python3
"""Validate the compact graph layout before creating a replacement project.

This never contacts Supabase. It verifies that the local runtime can be packed
into per-player adjacency rows and reports a deliberately conservative Free
tier import budget.
"""
from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNTIME = ROOT / "raw" / "runtime_compact" / "teammatetag_runtime_minimal.sqlite"
EDGE_SHIFT = 32
EDGE_MASK = (1 << EDGE_SHIFT) - 1
FREE_TARGET_BYTES = 350 * 1024 * 1024


def encode_edge(other_player_key: int, team_key: int) -> int:
    if not 0 < other_player_key < (1 << 31):
        raise ValueError(f"player key cannot be encoded safely: {other_player_key}")
    if not 0 < team_key <= EDGE_MASK:
        raise ValueError(f"team key cannot be encoded safely: {team_key}")
    return (other_player_key << EDGE_SHIFT) | team_key


def decode_edge(code: int) -> tuple[int, int]:
    return int(code >> EDGE_SHIFT), int(code & EDGE_MASK)


def adjacency_rows(conn: sqlite3.Connection):
    edges: dict[int, set[int]] = defaultdict(set)
    for player_a, player_b, team_key in conn.execute(
        "SELECT player_a_key, player_b_key, team_key FROM teammate_team_seasons"
    ):
        player_a, player_b, team_key = int(player_a), int(player_b), int(team_key)
        if player_a == player_b:
            raise ValueError(f"self edge for player key {player_a}")
        edges[player_a].add(encode_edge(player_b, team_key))
        edges[player_b].add(encode_edge(player_a, team_key))
    for player_key in sorted(edges):
        yield player_key, tuple(sorted(edges[player_key]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-db", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--target-mib", type=int, default=350)
    args = parser.parse_args()
    runtime = args.runtime_db.resolve()
    if not runtime.exists():
        raise SystemExit(f"ERROR: missing runtime database: {runtime}")
    target_bytes = args.target_mib * 1024 * 1024
    with sqlite3.connect(runtime) as conn:
        proof_count = int(conn.execute("SELECT COUNT(*) FROM teammate_team_seasons").fetchone()[0])
        rows = list(adjacency_rows(conn))
        directed_edges = sum(len(codes) for _, codes in rows)
        if directed_edges != proof_count * 2:
            raise SystemExit(
                f"ERROR: expected {proof_count * 2:,} directed edges, got {directed_edges:,}"
            )
        key_count = int(conn.execute("SELECT COUNT(*) FROM compact_player_keys").fetchone()[0])
        max_degree = max((len(codes) for _, codes in rows), default=0)
        max_key, max_codes = max(rows, key=lambda item: len(item[1]))
        for code in max_codes:
            other_key, team_key = decode_edge(code)
            if other_key <= 0 or team_key <= 0:
                raise SystemExit("ERROR: adjacency encoding round-trip failed")

    # PostgreSQL's array header plus a row tuple is well below this allowance;
    # reserving 24 bytes per row here deliberately overstates the graph itself.
    graph_payload = directed_edges * 8 + len(rows) * 24
    runtime_bytes = runtime.stat().st_size
    # The local SQLite file includes legacy proof-table indexes. The target is
    # an intentionally conservative operational budget, not a claim that the
    # whole SQLite artifact will be copied byte-for-byte to Postgres.
    estimate = graph_payload + 180 * 1024 * 1024
    print(f"runtime artifact: {runtime_bytes / 1024 / 1024:.1f} MiB")
    print(f"canonical proofs: {proof_count:,}")
    print(f"adjacency rows: {len(rows):,} of {key_count:,} player keys")
    print(f"directed adjacency edges: {directed_edges:,}")
    print(f"largest player payload: key {max_key} with {len(max_codes):,} edges")
    print(f"packed graph payload upper bound: {graph_payload / 1024 / 1024:.1f} MiB")
    print(f"conservative fresh-project estimate: {estimate / 1024 / 1024:.1f} MiB")
    print(f"required operational ceiling: {target_bytes / 1024 / 1024:.0f} MiB")
    if estimate > target_bytes:
        raise SystemExit("ERROR: conservative estimate exceeds the project ceiling")
    print("PASS: graph is bounded, reversible, and below the fresh-project budget.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
