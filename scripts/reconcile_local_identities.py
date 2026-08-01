"""Build TeamMateTag's local, source-aware identity reconciliation layer.

Sports sources disagree on player identifiers, spelling, career dates, and
occasionally the player represented by an identical display name. This script
does not alter the playable roster graph. It records every imported source
fact, its source-specific player reference, established match claims, and
ranked candidates for records that still need a reviewed decision.

Run after the local dataset and honors refresh:

    python scripts\reconcile_local_identities.py

The local SQLite file is ignored by Git. The schema and this repeatable import
are the project-owned database design; raw source files remain in raw/.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from build_local_sports_dataset import DEFAULT_DB
from name_normalize import normalize


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORT = ROOT / "db" / "identity_review_queue.csv"
IMPORT_RUN = "local_honors_import_v1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_player_references (
  sport_id TEXT NOT NULL,
  source TEXT NOT NULL,
  reference_key TEXT NOT NULL,
  source_name TEXT NOT NULL,
  season INTEGER,
  source_url TEXT,
  raw_payload TEXT,
  first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (sport_id, source, reference_key)
);
CREATE INDEX IF NOT EXISTS idx_source_player_references_name
  ON source_player_references(sport_id, source_name);

CREATE TABLE IF NOT EXISTS source_fact_observations (
  sport_id TEXT NOT NULL,
  source TEXT NOT NULL,
  fact_key TEXT NOT NULL,
  reference_key TEXT NOT NULL,
  fact_type TEXT NOT NULL,
  season INTEGER,
  source_url TEXT,
  raw_payload TEXT,
  imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (sport_id, source, fact_key),
  FOREIGN KEY (sport_id, source, reference_key)
    REFERENCES source_player_references(sport_id, source, reference_key)
);
CREATE INDEX IF NOT EXISTS idx_source_fact_observations_reference
  ON source_fact_observations(sport_id, source, reference_key);

CREATE TABLE IF NOT EXISTS player_identity_claims (
  sport_id TEXT NOT NULL,
  source TEXT NOT NULL,
  reference_key TEXT NOT NULL,
  player_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('accepted', 'rejected', 'needs_review')),
  method TEXT NOT NULL,
  confidence INTEGER NOT NULL CHECK (confidence BETWEEN 0 AND 100),
  evidence TEXT,
  reviewed_by TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (sport_id, source, reference_key, player_id),
  FOREIGN KEY (sport_id, player_id) REFERENCES sport_players(sport_id, player_id)
);
CREATE INDEX IF NOT EXISTS idx_player_identity_claims_review
  ON player_identity_claims(sport_id, status, confidence DESC);

CREATE TABLE IF NOT EXISTS player_identity_candidates (
  sport_id TEXT NOT NULL,
  source TEXT NOT NULL,
  reference_key TEXT NOT NULL,
  player_id TEXT NOT NULL,
  rank INTEGER NOT NULL,
  score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
  rationale TEXT NOT NULL,
  generated_by TEXT NOT NULL,
  generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (sport_id, source, reference_key, player_id),
  FOREIGN KEY (sport_id, player_id) REFERENCES sport_players(sport_id, player_id)
);
CREATE INDEX IF NOT EXISTS idx_player_identity_candidates_queue
  ON player_identity_candidates(sport_id, source, reference_key, rank);
"""


def digest(*values: object) -> str:
    text = "|".join("" if value is None else str(value) for value in values)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def reference_key(source_name: str, season: int | None) -> str:
    # A season is intentionally part of the identity reference. Two athletes
    # with the same source spelling can occur in distinct historical eras.
    return digest(normalize(source_name), season)


def fact_key(category: str, source_name: str, season: int | None) -> str:
    return digest(category, normalize(source_name), season)


def candidate_score(source_name: str, season: int | None, player: sqlite3.Row) -> tuple[int, str]:
    source_key = normalize(source_name)
    player_key = normalize(player["display_name"])
    source_last = normalize(source_name.split()[-1])
    player_last = normalize(player["last_name"] or player["display_name"].split()[-1])

    if source_key == player_key:
        score, rationale = 100, "exact normalized full name"
    elif source_last == player_last and normalize(player["first_name"] or "")[:1] == normalize(source_name)[:1]:
        score, rationale = 70, "same surname and first initial"
    elif source_last == player_last:
        score, rationale = 45, "same surname only"
    else:
        return 0, ""
    if season and (not player["debut_year"] or player["debut_year"] <= season + 1) and (not player["final_year"] or player["final_year"] >= season - 1):
        score = min(100, score + 20)
        rationale += "; career overlaps season"
    return score, rationale


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def import_honors(conn: sqlite3.Connection) -> tuple[int, int]:
    """Copy current resolved and unresolved honors into source-aware records."""
    conn.execute("DELETE FROM player_identity_candidates WHERE generated_by=?", (IMPORT_RUN,))
    conn.execute("DELETE FROM player_identity_claims WHERE method=?", (IMPORT_RUN,))
    fact_rows = conn.execute(
        """SELECT sport_id, player_id, honor, season, source_name, source_url, source
             FROM sport_honors"""
    ).fetchall()
    unresolved_rows = conn.execute(
        """SELECT sport_id, category, season, source_name, source_url, source, reason
             FROM sport_honor_unresolved"""
    ).fetchall()
    imported = accepted = 0

    def ingest(sport: str, category: str, season: int | None, name: str, url: str | None,
               source: str, payload: dict, player_id: str | None) -> None:
        nonlocal imported, accepted
        ref = reference_key(name, season)
        fact = fact_key(category, name, season)
        raw = json.dumps(payload, sort_keys=True)
        conn.execute(
            """INSERT INTO source_player_references
               (sport_id, source, reference_key, source_name, season, source_url, raw_payload)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(sport_id, source, reference_key) DO UPDATE SET
                 source_name=excluded.source_name, season=excluded.season,
                 source_url=excluded.source_url, raw_payload=excluded.raw_payload,
                 last_seen_at=CURRENT_TIMESTAMP""",
            (sport, source, ref, name, season, url, raw),
        )
        conn.execute(
            """INSERT INTO source_fact_observations
               (sport_id, source, fact_key, reference_key, fact_type, season, source_url, raw_payload)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(sport_id, source, fact_key) DO UPDATE SET
                 reference_key=excluded.reference_key, source_url=excluded.source_url,
                 raw_payload=excluded.raw_payload, imported_at=CURRENT_TIMESTAMP""",
            (sport, source, fact, ref, category, season, url, raw),
        )
        imported += 1
        if player_id:
            conn.execute(
                """INSERT INTO player_identity_claims
                   (sport_id, source, reference_key, player_id, status, method, confidence, evidence)
                   VALUES (?, ?, ?, ?, 'accepted', ?, 100, ?)
                   ON CONFLICT(sport_id, source, reference_key, player_id) DO UPDATE SET
                     status='accepted', confidence=100, evidence=excluded.evidence,
                     updated_at=CURRENT_TIMESTAMP""",
                (sport, source, ref, player_id, IMPORT_RUN, "Existing local honors resolution"),
            )
            accepted += 1

    for row in fact_rows:
        ingest(*row[:1], row[2], row[3], row[4], row[5], row[6], {"status": "resolved"}, row[1])
    for row in unresolved_rows:
        ingest(row[0], row[1], row[2], row[3], row[4], row[5], {"status": "unresolved", "reason": row[6]}, None)
    return imported, accepted


def build_candidates(conn: sqlite3.Connection) -> int:
    conn.row_factory = sqlite3.Row
    players: dict[str, dict[str, list[sqlite3.Row]]] = defaultdict(lambda: defaultdict(list))
    for player in conn.execute("SELECT sport_id, player_id, display_name, first_name, last_name, debut_year, final_year FROM sport_players"):
        last = normalize(player["last_name"] or player["display_name"].split()[-1])
        players[player["sport_id"]][last].append(player)
    refs = conn.execute(
        """SELECT r.sport_id, r.source, r.reference_key, r.source_name, r.season
             FROM source_player_references r
             WHERE NOT EXISTS (
               SELECT 1 FROM player_identity_claims c
               WHERE c.sport_id=r.sport_id AND c.source=r.source
                 AND c.reference_key=r.reference_key AND c.status='accepted'
             )"""
    ).fetchall()
    conn.execute("DELETE FROM player_identity_candidates WHERE generated_by=?", (IMPORT_RUN,))
    count = 0
    for ref in refs:
        ranked = []
        source_last = normalize(ref["source_name"].split()[-1])
        for player in players[ref["sport_id"]].get(source_last, []):
            score, rationale = candidate_score(ref["source_name"], ref["season"], player)
            if score:
                ranked.append((score, player, rationale))
        ranked.sort(key=lambda item: (-item[0], item[1]["display_name"], item[1]["player_id"]))
        for rank, (score, player, rationale) in enumerate(ranked[:5], start=1):
            conn.execute(
                """INSERT INTO player_identity_candidates
                   (sport_id, source, reference_key, player_id, rank, score, rationale, generated_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(sport_id, source, reference_key, player_id) DO UPDATE SET
                     rank=excluded.rank, score=excluded.score, rationale=excluded.rationale,
                     generated_by=excluded.generated_by, generated_at=CURRENT_TIMESTAMP""",
                (ref["sport_id"], ref["source"], ref["reference_key"], player["player_id"], rank, score, rationale, IMPORT_RUN),
            )
            count += 1
    return count


def write_report(conn: sqlite3.Connection, report: Path) -> int:
    report.parent.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        """SELECT r.sport_id, r.source, r.source_name, r.season,
                  GROUP_CONCAT(p.display_name || ' [' || c.score || ']: ' || c.rationale, ' | ') AS candidates
             FROM source_player_references r
             LEFT JOIN player_identity_claims accepted ON accepted.sport_id=r.sport_id
               AND accepted.source=r.source AND accepted.reference_key=r.reference_key
               AND accepted.status='accepted'
             LEFT JOIN player_identity_candidates c ON c.sport_id=r.sport_id
               AND c.source=r.source AND c.reference_key=r.reference_key
             LEFT JOIN sport_players p ON p.sport_id=c.sport_id AND p.player_id=c.player_id
             WHERE accepted.player_id IS NULL
             GROUP BY r.sport_id, r.source, r.reference_key, r.source_name, r.season
             ORDER BY r.sport_id, r.source, r.season, r.source_name"""
    ).fetchall()
    with report.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("sport", "source", "source_name", "season", "ranked_candidates"))
        writer.writerows(rows)
    return len(rows)


def accept_match(conn: sqlite3.Connection, sport: str, source: str, source_name: str,
                 season: int, player_id: str, evidence: str) -> None:
    """Persist a reviewed source-player link without changing raw source facts."""
    ref = reference_key(source_name, season)
    exists = conn.execute(
        """SELECT 1 FROM source_player_references
           WHERE sport_id=? AND source=? AND reference_key=?""",
        (sport, source, ref),
    ).fetchone()
    if not exists:
        raise SystemExit("No imported source reference matches that sport, source, player name, and season.")
    player = conn.execute(
        "SELECT display_name FROM sport_players WHERE sport_id=? AND player_id=?", (sport, player_id)
    ).fetchone()
    if not player:
        raise SystemExit("The requested local player_id does not exist for that sport.")
    conn.execute(
        """INSERT INTO player_identity_claims
           (sport_id, source, reference_key, player_id, status, method, confidence, evidence, reviewed_by)
           VALUES (?, ?, ?, ?, 'accepted', 'manual_review', 100, ?, 'project_review')
           ON CONFLICT(sport_id, source, reference_key, player_id) DO UPDATE SET
             status='accepted', method='manual_review', confidence=100,
             evidence=excluded.evidence, reviewed_by='project_review',
             updated_at=CURRENT_TIMESTAMP""",
        (sport, source, ref, player_id, evidence),
    )
    print(f"Accepted {source_name} ({season}) as {player[0]} [{player_id}].")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build source-aware local player identity review data.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--accept", nargs=5, metavar=("SPORT", "SOURCE", "SOURCE_NAME", "SEASON", "PLAYER_ID"),
        help="Accept one reviewed match. Quote SOURCE_NAME when it contains spaces.",
    )
    parser.add_argument("--evidence", default="Reviewed against independent historical sources.")
    args = parser.parse_args()
    conn = sqlite3.connect(args.db)
    try:
        ensure_schema(conn)
        facts, accepted = import_honors(conn)
        candidates = build_candidates(conn)
        if args.accept:
            sport, source, source_name, season, player_id = args.accept
            accept_match(conn, sport, source, source_name, int(season), player_id, args.evidence)
        queue = write_report(conn, args.report)
        conn.commit()
    finally:
        conn.close()
    print(f"Imported {facts:,} honor facts and {accepted:,} accepted identity claims.")
    print(f"Generated {candidates:,} ranked candidates across {queue:,} review references.")
    print(f"Review queue: {args.report}")


if __name__ == "__main__":
    main()
