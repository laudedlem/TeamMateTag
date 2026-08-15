"""Publish the locally matched OOTP MLB headshots to Supabase Storage.

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
PREFIX = "baseball/ootp"
SOURCE_DIR = ROOT / "raw" / "ootp" / "matched_mlb_headshots"
REPORT = ROOT / "raw" / "ootp_mlb_storage_publish.csv"


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
    if response.status_code in {200, 201, 409}:
        return
    raise RuntimeError(f"Could not create Storage bucket ({response.status_code}): {response.text[:300]}")


def public_url(base_url: str, object_path: str) -> str:
    return f"{base_url}/storage/v1/object/public/{BUCKET}/{quote(object_path, safe='/')}"


def upload_one(base_url: str, key: str, player_id: str, image_path: Path) -> tuple[str, str, str]:
    object_path = f"{PREFIX}/{image_path.name}"
    response = requests.post(
        f"{base_url}/storage/v1/object/{BUCKET}/{quote(object_path, safe='/')}",
        headers={**headers(key, "image/jpeg"), "x-upsert": "true"},
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
    args = parser.parse_args()

    if not SOURCE_DIR.exists():
        raise RuntimeError(f"Missing extracted OOTP images: {SOURCE_DIR}")
    base_url, key = storage_config()
    with server.db() as conn:
        rows = conn.execute(
            """SELECT player_id FROM player_headshots
                 WHERE sport_id='baseball' AND provider='OOTP Facepack'
                   AND source_url LIKE '/local-headshots/ootp/%' AND status='verified'
                 ORDER BY player_id"""
        ).fetchall()
    jobs = [(player_id, SOURCE_DIR / f"{player_id}.jpg") for (player_id,) in rows]
    jobs = [(player_id, path) for player_id, path in jobs if path.exists()]
    if args.limit:
        jobs = jobs[:args.limit]
    missing = len(rows) - len(jobs)
    print(f"Prepared {len(jobs):,} OOTP images for Supabase Storage ({missing:,} local files missing).", flush=True)
    if args.dry_run:
        return
    ensure_public_bucket(base_url, key)

    results: list[tuple[str, str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(upload_one, base_url, key, player_id, path): player_id for player_id, path in jobs}
        for index, future in enumerate(as_completed(futures), 1):
            results.append(future.result())
            if index % 100 == 0 or index == len(jobs):
                published = sum(status == "published" for _, status, _ in results)
                print(f"  uploaded {index:,}/{len(jobs):,} ({published:,} successful)", flush=True)

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with REPORT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["player_id", "status", "public_url_or_error"])
        writer.writerows(sorted(results))

    published = [(url, player_id) for player_id, status, url in results if status == "published"]
    with server.db() as conn, conn.cursor() as cur:
        cur.executemany(
            """UPDATE player_headshots SET source_url=%s, fallback_url=NULL,
                   review_note='OOTP Facepack image published in Supabase Storage for playtesting; license/source review required.'
                 WHERE sport_id='baseball' AND player_id=%s AND provider='OOTP Facepack'""",
            published,
        )
    failed = len(results) - len(published)
    print(f"Published {len(published):,}; failed {failed:,}. Report: {REPORT}")


if __name__ == "__main__":
    main()
