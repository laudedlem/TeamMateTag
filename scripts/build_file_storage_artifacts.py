#!/usr/bin/env python3
"""Build deploy-ready file-storage artifacts from local canonical data.

The output under raw/file_storage/ is the local mirror of what should later be
uploaded to Supabase Storage. Keep source/raw data local, upload only these
small runtime files plus tiny database references.
"""
from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNTIME_DB = ROOT / "raw" / "runtime_compact" / "teammatetag_runtime_minimal.sqlite"
DEFAULT_OUTPUT = ROOT / "raw" / "file_storage"
HEADSHOT_REGISTRIES = {
    "baseball": (ROOT / "raw" / "headshot_registry" / "baseball_headshots.sqlite", "baseball_headshots"),
    "basketball": (ROOT / "raw" / "headshot_registry" / "basketball_headshots.sqlite", "basketball_headshots"),
    "hockey": (ROOT / "raw" / "headshot_registry" / "hockey_headshots.sqlite", "hockey_headshots"),
    "football": (ROOT / "raw" / "headshot_registry" / "football_headshots.sqlite", "football_headshots"),
}
TARGET_SIZE = (288, 360)
WEBP_QUALITY = 82
HEADSHOT_BUCKET = "player-headshots"
STATIC_BUCKET = "teammatetag-runtime"


@dataclass(frozen=True)
class BuiltArtifact:
    path: str
    files: int
    bytes: int


def safe_player_file(player_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "__" for ch in player_id)
    return f"{safe}.webp"


def file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def folder_size(path: Path) -> tuple[int, int]:
    files = [item for item in path.rglob("*") if item.is_file()]
    return len(files), sum(item.stat().st_size for item in files)


def write_json(path: Path, payload: object, *, gzip_copy: bool = True) -> BuiltArtifact:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    path.write_bytes(content)
    total = len(content)
    files = 1
    if gzip_copy:
        gz_path = path.with_suffix(path.suffix + ".gz")
        with gzip.open(gz_path, "wb", compresslevel=9) as handle:
            handle.write(content)
        total += file_size(gz_path)
        files += 1
    return BuiltArtifact(str(path.relative_to(ROOT)), files, total)


def iter_verified_headshots(sport: str) -> Iterable[dict]:
    db_path, table = HEADSHOT_REGISTRIES[sport]
    if not db_path.exists():
        raise SystemExit(f"missing canonical {sport} registry: {db_path}")
    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        public_expr = "public_url" if "public_url" in columns else "NULL"
        fallback_expr = "fallback_url" if "fallback_url" in columns else "NULL"
        rows = conn.execute(
            f"""
            SELECT player_id, local_path, source_url, {public_expr}, {fallback_expr}, provider, status
              FROM {table}
             WHERE status = 'verified'
             ORDER BY player_id
            """
        ).fetchall()
    for player_id, local_path, source_url, public_url, fallback_url, provider, status in rows:
        if not local_path:
            raise SystemExit(f"{sport} {player_id} has no local_path in canonical registry")
        path = Path(local_path)
        if not path.is_absolute():
            path = ROOT / path
        if not path.exists():
            raise SystemExit(f"{sport} {player_id} missing local headshot file: {path}")
        yield {
            "sport": sport,
            "player_id": player_id,
            "local_path": path,
            "source_url": source_url,
            "public_url": public_url,
            "fallback_url": fallback_url,
            "provider": provider or "Canonical Local Cache",
            "status": status,
        }


def missing_headshot_rows(sport: str) -> list[dict]:
    db_path, table = HEADSHOT_REGISTRIES[sport]
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        name_expr = "player_name" if "player_name" in columns else "player_id"
        external_expr = "external_id" if "external_id" in columns else "NULL"
        debut_expr = "debut_year" if "debut_year" in columns else "NULL"
        final_expr = "final_year" if "final_year" in columns else "NULL"
        career_expr = "career_games" if "career_games" in columns else "NULL"
        rows = conn.execute(
            f"""
            SELECT player_id, {name_expr} AS player_name, {external_expr} AS external_id,
                   {debut_expr} AS debut_year, {final_expr} AS final_year,
                   {career_expr} AS career_games, status, provider, review_note
              FROM {table}
             WHERE status <> 'verified'
             ORDER BY COALESCE({career_expr}, 0) DESC, player_name, player_id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def optimize_headshot(source: Path, target: Path) -> tuple[int, int, int]:
    target.parent.mkdir(parents=True, exist_ok=True)
    image = Image.open(source)
    image = ImageOps.exif_transpose(image)
    if image.mode in {"RGBA", "LA"}:
        background = Image.new("RGB", image.size, (246, 248, 252))
        alpha = image.getchannel("A") if image.mode == "RGBA" else image.getchannel(1)
        background.paste(image.convert("RGBA"), mask=alpha)
        image = background
    else:
        image = image.convert("RGB")
    image.thumbnail(TARGET_SIZE, Image.Resampling.LANCZOS)
    image.save(target, "WEBP", quality=WEBP_QUALITY, method=6)
    return image.width, image.height, target.stat().st_size


def build_headshots(output: Path, sports: list[str]) -> list[BuiltArtifact]:
    artifacts: list[BuiltArtifact] = []
    root = output / HEADSHOT_BUCKET
    root.mkdir(parents=True, exist_ok=True)
    for sport in sports:
        sport_dir = root / sport
        if sport_dir.exists():
            shutil.rmtree(sport_dir)
        sport_dir.mkdir(parents=True)
        manifest_rows = []
        original_bytes = 0
        optimized_bytes = 0
        for row in iter_verified_headshots(sport):
            player_id = row["player_id"]
            original_bytes += row["local_path"].stat().st_size
            filename = safe_player_file(player_id)
            object_path = f"{sport}/{filename}"
            storage_path = f"{HEADSHOT_BUCKET}/{object_path}"
            width, height, size = optimize_headshot(row["local_path"], sport_dir / filename)
            optimized_bytes += size
            manifest_rows.append(
                {
                    "player_id": player_id,
                    "status": "verified",
                    "provider": row["provider"],
                    "object_path": object_path,
                    "storage_path": storage_path,
                    "content_type": "image/webp",
                    "width": width,
                    "height": height,
                    "bytes": size,
                }
            )
        artifacts.append(write_json(output / "manifests" / "headshots" / f"{sport}.json", {
            "sport": sport,
            "bucket": HEADSHOT_BUCKET,
            "target_size": list(TARGET_SIZE),
            "format": "webp",
            "quality": WEBP_QUALITY,
            "players": len(manifest_rows),
            "original_bytes": original_bytes,
            "optimized_bytes": optimized_bytes,
            "rows": manifest_rows,
        }))
        missing_rows = missing_headshot_rows(sport)
        if missing_rows:
            artifacts.append(write_json(output / "manifests" / "headshots" / f"{sport}_missing.json", {
                "sport": sport,
                "purpose": "Players without verified headshots; avoid for photo-dependent starters and Film Review puzzles.",
                "players": len(missing_rows),
                "rows": missing_rows,
            }))
        files, bytes_ = folder_size(sport_dir)
        artifacts.append(BuiltArtifact(str(sport_dir.relative_to(ROOT)), files, bytes_))
    return artifacts


def export_table(conn: sqlite3.Connection, table: str) -> list[dict]:
    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    columns = [col[0] for col in conn.execute(f"SELECT * FROM {table} LIMIT 0").description]
    return [dict(zip(columns, row)) for row in rows]


def build_static_runtime(output: Path, runtime_db: Path) -> list[BuiltArtifact]:
    if not runtime_db.exists():
        raise SystemExit(f"missing runtime database: {runtime_db}")
    artifacts: list[BuiltArtifact] = []
    with sqlite3.connect(runtime_db) as conn:
        conn.row_factory = sqlite3.Row
        baseball_traits = [dict(row) for row in conn.execute("SELECT * FROM player_playoff_traits ORDER BY player_id")]
        baseball_powerups = [
            dict(row)
            for row in conn.execute(
                """
                SELECT powerup_key, franchise_id, team_id, season, player_id
                  FROM player_powerup_qualifications
                 ORDER BY powerup_key, franchise_id, team_id, season, player_id
                """
            )
        ]
        coverage = [
            dict(row)
            for row in conn.execute(
                "SELECT scope AS sport_id, season, coverage_type, strict, source FROM runtime_coverage ORDER BY scope, season"
            )
        ]
    base = output / STATIC_BUCKET / "gameplay"
    artifacts.append(write_json(base / "baseball_player_playoff_traits.json", {
        "sport": "baseball",
        "rows": baseball_traits,
    }))
    artifacts.append(write_json(base / "baseball_player_powerup_qualifications.json", {
        "sport": "baseball",
        "rows": baseball_powerups,
    }))
    artifacts.append(write_json(base / "runtime_coverage.json", {"rows": coverage}))
    return artifacts


def build(output: Path, runtime_db: Path, sports: list[str]) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    artifacts = []
    artifacts.extend(build_headshots(output, sports))
    artifacts.extend(build_static_runtime(output, runtime_db))
    total_files, total_bytes = folder_size(output)
    manifest = {
        "purpose": "Local mirror of deploy-ready Supabase Storage artifacts.",
        "database_rule": (
            "Keep only query-critical rows in Postgres. Store image bytes and "
            "static/generated runtime artifacts here, with database rows holding "
            "only IDs, status, and object paths when needed."
        ),
        "headshot_bucket": HEADSHOT_BUCKET,
        "static_bucket": STATIC_BUCKET,
        "sports": sports,
        "artifacts": [asdict(item) for item in artifacts],
        "total_files": total_files,
        "total_bytes": total_bytes,
    }
    write_json(output / "manifest.json", manifest, gzip_copy=False)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--runtime-db", type=Path, default=DEFAULT_RUNTIME_DB)
    parser.add_argument("--sports", nargs="+", default=["baseball", "basketball", "hockey", "football"], choices=sorted(HEADSHOT_REGISTRIES))
    args = parser.parse_args()
    manifest = build(args.output, args.runtime_db, args.sports)
    print(f"output: {args.output}")
    print(f"files: {manifest['total_files']:,}")
    print(f"size_mb: {manifest['total_bytes'] / 1024 / 1024:.2f}")
    for artifact in manifest["artifacts"]:
        print(f"{artifact['path']}: {artifact['files']:,} files / {artifact['bytes'] / 1024 / 1024:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
