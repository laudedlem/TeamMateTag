#!/usr/bin/env python3
"""Report verified headshot gaps from the local compact runtime.

This is intentionally local-first. The detailed CSVs and summary are written
under raw/ so they can be used as an offline worklist without inflating
Supabase. Use --production only when you want a read-only comparison against
the configured Supabase database.
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None


ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DB = ROOT / "raw" / "runtime_compact" / "teammatetag_runtime_minimal.sqlite"
OUTPUT_DIR = ROOT / "raw" / "headshot_gap_reports"

SPORTS = ("baseball", "basketball", "hockey")
LOCAL_IMAGE_DIRS = {
    "baseball": ROOT / "raw" / "player_headshots" / "baseball",
    "basketball": ROOT / "raw" / "player_headshots" / "basketball",
    "hockey": ROOT / "raw" / "player_headshots" / "hockey",
}
BASEBALL_REGISTRY_DB = ROOT / "raw" / "headshot_registry" / "baseball_headshots.sqlite"


@dataclass(frozen=True)
class Coverage:
    sport: str
    total_players: int
    verified: int
    missing: int
    local_files: int
    local_mb: float
    production_total: int | None = None
    production_verified: int | None = None
    production_missing: int | None = None


def file_count_and_size(path: Path) -> tuple[int, float]:
    if not path.exists():
        return 0, 0.0
    files = [p for p in path.rglob("*") if p.is_file()]
    size = sum(p.stat().st_size for p in files)
    return len(files), round(size / 1024 / 1024, 1)


def verified_clause(alias: str = "h") -> str:
    return (
        f"{alias}.player_id IS NOT NULL "
        f"AND {alias}.status = 'verified' "
        f"AND COALESCE({alias}.source_url, {alias}.fallback_url, '') <> ''"
    )


def write_local_gap_csv(conn: sqlite3.Connection, sport: str, output_dir: Path) -> tuple[int, int, int]:
    output_path = output_dir / f"{sport}_missing_verified_runtime_headshots.csv"
    raw_rows = conn.execute(
        f"""
        SELECT p.scope AS sport,
               p.player_id,
               p.display_name,
               p.external_id,
               p.debut_year,
               p.final_year,
               p.primary_pos,
               h.source_url,
               h.fallback_url,
               h.provider,
               h.status
          FROM runtime_players p
          LEFT JOIN runtime_headshots h
            ON h.scope = p.scope
           AND h.player_id = p.player_id
         WHERE p.scope = ?
           AND NOT ({verified_clause()})
         ORDER BY COALESCE(p.final_year, 0) DESC,
                  p.display_name COLLATE NOCASE,
                  p.player_id
        """,
        (sport,),
    ).fetchall()
    rows = [dict(row) for row in raw_rows]
    if sport == "baseball" and BASEBALL_REGISTRY_DB.exists() and rows:
        with sqlite3.connect(BASEBALL_REGISTRY_DB) as registry:
            registry.row_factory = sqlite3.Row
            details = {
                row["player_id"]: dict(row)
                for row in registry.execute(
                    """
                    SELECT player_id, source_url, fallback_url, provider, status, review_note
                      FROM baseball_headshots
                     WHERE status <> 'verified'
                    """
                )
            }
        for row in rows:
            detail = details.get(row["player_id"])
            if not detail:
                continue
            row["source_url"] = detail.get("source_url")
            row["fallback_url"] = detail.get("fallback_url")
            row["provider"] = detail.get("provider")
            row["status"] = detail.get("status")
            row["review_note"] = detail.get("review_note")
    total = conn.execute("SELECT COUNT(*) FROM runtime_players WHERE scope = ?", (sport,)).fetchone()[0]
    verified = conn.execute(
        f"""
        SELECT COUNT(*)
          FROM runtime_players p
          JOIN runtime_headshots h
            ON h.scope = p.scope
           AND h.player_id = p.player_id
         WHERE p.scope = ?
           AND {verified_clause()}
        """,
        (sport,),
    ).fetchone()[0]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "sport",
                "player_id",
                "display_name",
                "external_id",
                "debut_year",
                "final_year",
                "primary_pos",
                "source_url",
                "fallback_url",
                "provider",
                "status",
                "review_note",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.get("sport"),
                    row.get("player_id"),
                    row.get("display_name"),
                    row.get("external_id"),
                    row.get("debut_year"),
                    row.get("final_year"),
                    row.get("primary_pos"),
                    row.get("source_url"),
                    row.get("fallback_url"),
                    row.get("provider"),
                    row.get("status"),
                    row.get("review_note"),
                ]
            )
    return int(total), int(verified), len(rows)


def production_counts() -> dict[str, tuple[int, int, int]]:
    if load_dotenv:
        load_dotenv(ROOT / ".env")
    database_url = os.environ.get("DATABASE_URL") or os.environ.get("DIRECT_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL or DIRECT_URL is required for --production.")
    if psycopg is None:
        raise RuntimeError("psycopg is required for --production.")
    sql_by_sport = {
        "baseball": """
            SELECT COUNT(*) AS total,
                   COUNT(h.player_id) FILTER (
                       WHERE h.status = 'verified'
                         AND COALESCE(h.source_url, h.fallback_url, '') <> ''
                   ) AS verified
              FROM players p
              LEFT JOIN player_headshots h
                ON h.sport_id = 'baseball'
               AND h.player_id = p.player_id
             WHERE p.final_year >= 2000
        """,
        "basketball": """
            SELECT COUNT(*) AS total,
                   COUNT(h.player_id) FILTER (
                       WHERE h.status = 'verified'
                         AND COALESCE(h.source_url, h.fallback_url, '') <> ''
                   ) AS verified
              FROM sport_players p
              LEFT JOIN player_headshots h
                ON h.sport_id = p.sport_id
               AND h.player_id = p.player_id
             WHERE p.sport_id = 'basketball'
        """,
        "hockey": """
            SELECT COUNT(*) AS total,
                   COUNT(h.player_id) FILTER (
                       WHERE h.status = 'verified'
                         AND COALESCE(h.source_url, h.fallback_url, '') <> ''
                   ) AS verified
              FROM sport_players p
              LEFT JOIN player_headshots h
                ON h.sport_id = p.sport_id
               AND h.player_id = p.player_id
             WHERE p.sport_id = 'hockey'
        """,
    }
    result: dict[str, tuple[int, int, int]] = {}
    with psycopg.connect(database_url, prepare_threshold=None) as pg:
        with pg.cursor() as cur:
            for sport, sql in sql_by_sport.items():
                total, verified = cur.execute(sql).fetchone()
                result[sport] = (int(total), int(verified), int(total) - int(verified))
    return result


def write_summary(coverages: list[Coverage], output_dir: Path) -> None:
    lines = [
        "# Headshot Gap Summary",
        "",
        "Generated from the local compact runtime. Raw images stay offline; online runtime should store one canonical URL row per player, not raw image data in Postgres.",
        "",
        "| Sport | Local Verified | Local Missing | Local Backup Files | Local Backup Size | Production Missing |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in coverages:
        production_missing = "" if item.production_missing is None else f"{item.production_missing:,}"
        lines.append(
            f"| {item.sport.title()} | {item.verified:,}/{item.total_players:,} | "
            f"{item.missing:,} | {item.local_files:,} | {item.local_mb:.1f} MB | {production_missing} |"
        )
    lines.extend(
        [
            "",
            "## Output Files",
            "",
        ]
    )
    for item in coverages:
        lines.append(f"- `{item.sport}_missing_verified_runtime_headshots.csv`")
    lines.extend(
        [
            "",
            "## Storage-Safe Rule",
            "",
            "- Keep full raw/cached image folders under `raw/` locally.",
            "- Promote exactly one verified canonical headshot per player into the compact runtime registry.",
            "- Publish only compressed canonical image objects or trusted external URLs, plus one small `player_headshots` row.",
            "- Do not upload source attempts, raw sheets, duplicate image folders, or full local photo caches to Supabase.",
        ]
    )
    (output_dir / "headshot_gap_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-db", type=Path, default=RUNTIME_DB)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--production", action="store_true", help="Add read-only Supabase comparison counts.")
    args = parser.parse_args()

    if not args.runtime_db.exists():
        raise SystemExit(f"Missing compact runtime DB: {args.runtime_db}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    prod = production_counts() if args.production else {}
    coverages: list[Coverage] = []
    with sqlite3.connect(args.runtime_db) as conn:
        conn.row_factory = sqlite3.Row
        for sport in SPORTS:
            total, verified, missing = write_local_gap_csv(conn, sport, args.output_dir)
            local_files, local_mb = file_count_and_size(LOCAL_IMAGE_DIRS[sport])
            prod_total, prod_verified, prod_missing = prod.get(sport, (None, None, None))
            coverages.append(
                Coverage(
                    sport=sport,
                    total_players=total,
                    verified=verified,
                    missing=missing,
                    local_files=local_files,
                    local_mb=local_mb,
                    production_total=prod_total,
                    production_verified=prod_verified,
                    production_missing=prod_missing,
                )
            )

    write_summary(coverages, args.output_dir)
    for item in coverages:
        prod_text = "" if item.production_missing is None else f", production missing {item.production_missing:,}"
        print(
            f"{item.sport}: local verified {item.verified:,}/{item.total_players:,}; "
            f"missing {item.missing:,}; local backup {item.local_files:,} files / {item.local_mb:.1f} MB{prod_text}"
        )
    print(f"wrote reports to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
