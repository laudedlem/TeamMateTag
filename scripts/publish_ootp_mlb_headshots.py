"""Publish locally matched facepack headshots to Supabase Storage.

The OOTP archive remains in ``raw/`` and is never committed. This script only
uploads the 2,211 images already matched unambiguously to the playable MLB
catalog, then replaces their local-only card URLs in Postgres with public
Supabase Storage URLs.

Required local .env values:
  DATABASE_URL
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY

The service-role key is used only by this local script. Do not place it in
browser code or commit it to Git.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))
load_dotenv(ROOT / ".env")
import server  # noqa: E402

BUCKET = "player-headshots"


def storage_config() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required in .env. "
            "See .env.example; never commit either secret."
        )
    return url, key


def headers(key: str, content_type: str | None = None) -> dict[str, str]:
    result = {"apikey": key, "Authorization": f"Bearer {key}"}
    if content_type:
        result["Content-Type"] = content_type
    return result


def ensure_public_bucket(base_url: str, key: str) -> None:
    response = requests.post(
        f"{base_url}/storage/v1/bucket",
        headers=headers(key, "application/json"),
        json={"id": BUCKET, "name": BUCKET, "public": True, "file_size_limit": 5242880},
        timeout=30,
    )
    if response.status_code in {200, 201, 409} or "BucketAlreadyExists" in response.text:
        return
    raise RuntimeError(f"Could not create Storage bucket ({response.status_code}): {response.text[:300]}")


def public_url(base_url: str, object_path: str) -> str:
    return f"{base_url}/storage/v1/object/public/{BUCKET}/{quote(object_path, safe='/')}"


def upload_one(base_url: str, key: str, player_id: str, image_path: Path, object_prefix: str) -> tuple[str, str, str]:
    object_path = f"{object_prefix.strip('/')}/{image_path.name}"
    content_type = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
    response = requests.post(
        f"{base_url}/storage/v1/object/{BUCKET}/{quote(object_path, safe='/')}",
        headers={**headers(key, content_type), "x-upsert": "true"},
        data=image_path.read_bytes(),
        timeout=60,
    )
    if response.status_code not in {200, 201}:
        return player_id, "failed", f"HTTP {response.status_code}: {response.text[:240]}"
    return player_id, "published", public_url(base_url, object_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0, help="Publish only the first N records.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sport", default="baseball")
    parser.add_argument("--provider", default="OOTP Facepack")
    parser.add_argument("--local-prefix", default="/local-headshots/ootp/")
    parser.add_argument("--source-dir", type=Path, default=ROOT / "raw" / "ootp" / "matched_mlb_headshots")
    parser.add_argument("--object-prefix", default="baseball/ootp")
    parser.add_argument("--report", type=Path, default=ROOT / "raw" / "ootp_mlb_storage_publish.csv")
    args = parser.parse_args()

    source_dir = args.source_dir
    if not source_dir.exists():
        raise RuntimeError(f"Missing extracted images: {source_dir}")
    base_url, key = storage_config()
    with server.db() as conn:
        rows = conn.execute(
            """SELECT player_id, source_url FROM player_headshots
                 WHERE sport_id=%s AND provider=%s
                   AND source_url LIKE %s AND status='verified'
                 ORDER BY player_id"""
            , (args.sport, args.provider, f"{args.local_prefix}%")
        ).fetchall()
    jobs = [(player_id, source_dir / Path(source_url).name) for player_id, source_url in rows]
    jobs = [(player_id, path) for player_id, path in jobs if path.exists()]
    if args.limit:
        jobs = jobs[:args.limit]
    missing = len(rows) - len(jobs)
    print(f"Prepared {len(jobs):,} {args.provider} images for Supabase Storage ({missing:,} local files missing).", flush=True)
    if args.dry_run:
        return
    ensure_public_bucket(base_url, key)

    results: list[tuple[str, str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(upload_one, base_url, key, player_id, path, args.object_prefix): player_id for player_id, path in jobs}
        for index, future in enumerate(as_completed(futures), 1):
            results.append(future.result())
            if index % 100 == 0 or index == len(jobs):
                published = sum(status == "published" for _, status, _ in results)
                print(f"  uploaded {index:,}/{len(jobs):,} ({published:,} successful)", flush=True)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["player_id", "status", "public_url_or_error"])
        writer.writerows(sorted(results))

    published = [(url, player_id) for player_id, status, url in results if status == "published"]
    with server.db() as conn, conn.cursor() as cur:
        cur.executemany(
            """UPDATE player_headshots SET source_url=%s, fallback_url=NULL,
                   review_note=regexp_replace(review_note, 'Local playtest|Community FHM', 'Published', 'g')
                 WHERE sport_id=%s AND player_id=%s AND provider=%s""",
            [(url, args.sport, player_id, args.provider) for url, player_id in published],
        )
    failed = len(results) - len(published)
    print(f"Published {len(published):,}; failed {failed:,}. Report: {args.report}")


if __name__ == "__main__":
    main()
