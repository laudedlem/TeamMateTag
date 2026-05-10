#!/usr/bin/env python3
"""
analyze_graph.py — diagnostics on the teammate graph.

What this tells you:

1. Is the graph connected?  If not, players in disconnected components
   will create dead-end games when used as starters. The daily-challenge
   picker MUST select from the giant component.

2. Degree distribution. Helps with difficulty calibration: a high-degree
   player (Bartolo Colon, Octavio Dotel) creates easy chains because they
   have hundreds of valid teammates. Low-degree (one-team career, short
   tenure) makes harder chains.

3. Famous-player list. The pool of acceptable "starter" players for daily
   challenges and matchmaking — well-known and well-connected enough that
   the average player can extend the chain.

4. Pair distance probe. Given two players, how many hops apart are they?
   Helps you understand the graph density empirically.
"""
import argparse
import sqlite3
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path


def load_adjacency(conn) -> dict[str, set[str]]:
    """Build undirected adjacency dict from teammates table."""
    adj: dict[str, set[str]] = defaultdict(set)
    for a, b in conn.execute("SELECT player_a_id, player_b_id FROM teammates"):
        adj[a].add(b)
        adj[b].add(a)
    return adj


def connected_components(adj: dict[str, set[str]]) -> list[set[str]]:
    """Return list of components, sorted by size (largest first)."""
    seen: set[str] = set()
    components: list[set[str]] = []
    for start in adj:
        if start in seen:
            continue
        comp: set[str] = set()
        q = deque([start])
        while q:
            node = q.popleft()
            if node in seen:
                continue
            seen.add(node)
            comp.add(node)
            q.extend(adj[node] - seen)
        components.append(comp)
    components.sort(key=len, reverse=True)
    return components


def shortest_path(adj: dict[str, set[str]], src: str, dst: str) -> list[str] | None:
    """BFS for shortest chain from src to dst. Returns the path or None."""
    if src == dst:
        return [src]
    if src not in adj or dst not in adj:
        return None
    parent = {src: None}
    q = deque([src])
    while q:
        node = q.popleft()
        for nb in adj[node]:
            if nb in parent:
                continue
            parent[nb] = node
            if nb == dst:
                # reconstruct
                path = [dst]
                cur = node
                while cur is not None:
                    path.append(cur)
                    cur = parent[cur]
                return list(reversed(path))
            q.append(nb)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="db/base2nerdle.sqlite")
    ap.add_argument("--probe", nargs=2, metavar=("PLAYER_A", "PLAYER_B"),
                    help="show shortest chain between two player_ids")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"DB not found: {db}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db)

    # Stats from the DB.
    n_players, n_appear, n_edges, min_y, max_y = conn.execute(
        """SELECT
             (SELECT COUNT(*) FROM players),
             (SELECT COUNT(*) FROM appearances),
             (SELECT COUNT(*) FROM teammates),
             (SELECT MIN(season) FROM appearances),
             (SELECT MAX(season) FROM appearances)"""
    ).fetchone()
    print(f"Players: {n_players:,}    Appearances: {n_appear:,}    "
          f"Teammate-season edges: {n_edges:,}    Years: {min_y}-{max_y}")
    print()

    print("Building undirected adjacency (deduplicating across seasons)...")
    adj = load_adjacency(conn)
    n_nodes = len(adj)
    n_edges_undir = sum(len(v) for v in adj.values()) // 2
    print(f"  {n_nodes:,} nodes, {n_edges_undir:,} unique pair edges (one per teammate pair)")
    print()

    # 1. CONNECTIVITY
    print("=" * 70)
    print("CONNECTIVITY")
    print("=" * 70)
    comps = connected_components(adj)
    print(f"  {len(comps)} component(s)")
    print(f"  Giant component:  {len(comps[0]):,} players ({100*len(comps[0])/n_nodes:.1f}%)")
    if len(comps) > 1:
        print(f"  Other components: {[len(c) for c in comps[1:11]]}{'...' if len(comps) > 11 else ''}")
        # Players in tiny components are starter-poison: don't pick them for daily challenges.
        small_pool = sum(len(c) for c in comps[1:])
        print(f"  Players outside the giant component: {small_pool:,}")
    print()

    # 2. DEGREE DISTRIBUTION
    print("=" * 70)
    print("DEGREE DISTRIBUTION (number of unique career teammates)")
    print("=" * 70)
    degrees = sorted(len(v) for v in adj.values())
    if degrees:
        bins = [1, 5, 10, 25, 50, 100, 200, 500, 1000]
        counts = Counter()
        for d in degrees:
            bucket = next((b for b in bins if d < b), bins[-1])
            counts[bucket] += 1
        print(f"  min: {degrees[0]}    median: {degrees[len(degrees)//2]}    "
              f"mean: {sum(degrees)/len(degrees):.1f}    max: {degrees[-1]}")
        print()
        running = 0
        prev = 0
        for b in bins:
            n = counts[b]
            running += n
            if n:
                print(f"  {prev:>4}-{b-1:<4} teammates : {n:5d} players  "
                      f"(cum {100*running/n_nodes:.1f}%)")
            prev = b
    print()

    # 3. FAMOUS / WELL-CONNECTED PLAYERS
    print("=" * 70)
    print("TOP 20 BY DEGREE (good seed candidates)")
    print("=" * 70)
    rows = conn.execute(
        """
        SELECT p.player_id, p.name_first || ' ' || p.name_last,
               ps.career_games, ps.teammate_count
          FROM players p
          JOIN players_searchable ps ON ps.player_id = p.player_id
         ORDER BY ps.teammate_count DESC LIMIT 20
        """
    ).fetchall()
    for pid, name, games, deg in rows:
        print(f"  {deg:5d}  {name:30s}  ({games:,} career games)")
    print()

    # 4. OPTIONAL: shortest-path probe
    if args.probe:
        a, b = args.probe
        path = shortest_path(adj, a, b)
        print("=" * 70)
        print(f"SHORTEST CHAIN: {a} -> {b}")
        print("=" * 70)
        if path is None:
            print("  no path (different components)")
        else:
            for pid in path:
                row = conn.execute(
                    "SELECT name_first || ' ' || name_last FROM players WHERE player_id = ?",
                    (pid,),
                ).fetchone()
                print(f"  -> {(row[0] if row else pid)}")
            print(f"  ({len(path)-1} hops)")

    conn.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
