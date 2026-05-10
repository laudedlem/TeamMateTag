#!/usr/bin/env python3
"""
04_load_chadwick_ids.py — enrich players with mlbam_id and nicknames from
the Chadwick Bureau Register.

Why this matters: Lahman gives us bbrefID and retroID for every historical
player but NOT mlbam_id (MLB Advanced Media's player ID, used by statsapi.mlb.com).
Without mlbam_id on historical rows, the daily in-season updater can't match
players who appear on rosters but haven't been "touched" before.

The Chadwick Register is the canonical crosswalk between every baseball ID
system. We match by bbref_id (which we already have) to fill in mlbam_id.

Run after 02_load_lahman.py. Re-run only when refreshing Lahman or expanding
the year window — Chadwick changes slowly.

Source: https://github.com/chadwickbureau/register
The register splits people across 16 files (people-0.csv through people-f.csv,
hex-numbered) for size reasons. We download all 16 in parallel.
"""
import argparse
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import StringIO
from pathlib import Path

import csv
import requests

# 16 hex-numbered files at /data/people-{hex}.csv.
HEX = "0123456789abcdef"
URL_TMPL = "https://github.com/chadwickbureau/register/raw/master/data/people-{}.csv"
LOCAL_TMPL = "people-{}.csv"


def fetch_one(hex_char: str, cache_dir: Path) -> Path:
    """Download one shard, cache locally. Return path."""
    cached = cache_dir / LOCAL_TMPL.format(hex_char)
    if cached.exists():
        return cached
    r = requests.get(URL_TMPL.format(hex_char), timeout=120)
    r.raise_for_status()
    cached.write_bytes(r.content)
    return cached


def fetch_all(cache_dir: Path) -> list[Path]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(fetch_one, h, cache_dir): h for h in HEX}
        for f in as_completed(futs):
            paths.append(f.result())
            print(f"  fetched people-{futs[f]}.csv")
    return paths


def load(conn: sqlite3.Connection, paths: list[Path]) -> tuple[int, int]:
    """Update players.mlbam_id and players.name_nick where bbref_id matches."""
    # Build a one-pass lookup of bbref_id -> our player_id, so we can match
    # Chadwick rows in a single scan of each shard.
    by_bbref = dict(conn.execute(
        "SELECT bbref_id, player_id FROM players WHERE bbref_id IS NOT NULL"
    ).fetchall())
    print(f"  {len(by_bbref):,} of our players have a bbref_id to match on")

    updates: list[tuple[int | None, str | None, str]] = []
    nicknames: list[tuple[str, str]] = []   # (player_id, nickname)
    seen_player_ids = set()

    for path in paths:
        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                bbref = row.get("key_bbref") or ""
                if not bbref or bbref not in by_bbref:
                    continue
                player_id = by_bbref[bbref]
                if player_id in seen_player_ids:
                    continue  # Chadwick can have multiple rows for one bbref under rare conditions
                seen_player_ids.add(player_id)

                mlbam_raw = row.get("key_mlbam") or ""
                mlbam_id = int(mlbam_raw) if mlbam_raw.isdigit() else None
                nick = (row.get("name_nick") or "").strip() or None

                updates.append((mlbam_id, nick, player_id))
                if nick:
                    # name_nick may be comma-separated ("Big Papi, Cookie Monster")
                    for n in nick.split(","):
                        n = n.strip()
                        if n:
                            nicknames.append((player_id, n))

    cur = conn.cursor()
    cur.executemany(
        "UPDATE players SET mlbam_id = COALESCE(?, mlbam_id), name_nick = COALESCE(?, name_nick) WHERE player_id = ?",
        updates,
    )

    # Stash nicknames in a side table for autocomplete.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS player_nicknames (
            player_id  TEXT NOT NULL REFERENCES players(player_id),
            nickname   TEXT NOT NULL,
            PRIMARY KEY (player_id, nickname)
        )
    """)
    cur.execute("DELETE FROM player_nicknames")
    cur.executemany(
        "INSERT OR IGNORE INTO player_nicknames (player_id, nickname) VALUES (?, ?)",
        nicknames,
    )
    cur.execute(
        "INSERT OR REPLACE INTO data_provenance (source, season, row_count) VALUES (?, ?, ?)",
        ("chadwick_register", None, len(updates)),
    )
    conn.commit()
    return len(updates), len(nicknames)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="db/base2nerdle.sqlite")
    ap.add_argument("--cache-dir", default="raw/chadwick")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"DB not found: {db}. Run 02_load_lahman.py first.", file=sys.stderr)
        return 1

    # The schema file might predate the name_nick column on players; add it
    # idempotently here so this script is independently runnable.
    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(players)")}
    if "name_nick" not in cols:
        conn.execute("ALTER TABLE players ADD COLUMN name_nick TEXT")

    print("fetching Chadwick register (16 shards)")
    paths = fetch_all(Path(args.cache_dir))

    print("matching against our players")
    n_updates, n_nicks = load(conn, paths)
    print(f"  -> updated {n_updates:,} players")
    print(f"  -> recorded {n_nicks:,} nicknames")

    # Quick check on coverage.
    rows = conn.execute(
        "SELECT COUNT(*), SUM(CASE WHEN mlbam_id IS NOT NULL THEN 1 ELSE 0 END) FROM players"
    ).fetchone()
    print(f"\ncoverage: {rows[1]:,}/{rows[0]:,} players now have mlbam_id ({100*rows[1]/rows[0]:.1f}%)")
    conn.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
