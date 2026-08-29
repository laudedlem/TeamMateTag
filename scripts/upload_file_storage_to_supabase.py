#!/usr/bin/env python3
"""Replace Supabase Storage runtime files from raw/file_storage.

This uploads only the optimized local mirror: WebP headshots and small JSON
artifacts. Raw source data never goes to Supabase Storage.
"""
from __future__ import annotations

import argparse
import mimetypes
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = ROOT / "raw" / "file_storage"
BUCKETS = ("player-headshots", "teammatetag-runtime")


def load_config() -> tuple[str, str]:
    if load_dotenv:
        load_dotenv(ROOT / ".env")
    base_url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or ""
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_SECRET_KEY") or ""
    if not base_url:
        raise SystemExit("ERROR: SUPABASE_URL is required in .env")
    if not key:
        raise SystemExit("ERROR: SUPABASE_SERVICE_ROLE_KEY or SUPABASE_SECRET_KEY is required in .env for Storage uploads")
    return base_url.rstrip("/"), key


def headers(key: str, content_type: str | None = None) -> dict[str, str]:
    out = {"apikey": key, "Authorization": f"Bearer {key}"}
    if content_type:
        out["Content-Type"] = content_type
    return out


def ensure_bucket(base_url: str, key: str, bucket: str) -> None:
    response = requests.post(
        f"{base_url}/storage/v1/bucket",
        headers=headers(key, "application/json"),
        json={"id": bucket, "name": bucket, "public": True},
        timeout=30,
    )
    if response.status_code in {200, 201, 409} or "BucketAlreadyExists" in response.text:
        return
    raise RuntimeError(f"could not ensure bucket {bucket}: HTTP {response.status_code}: {response.text[:300]}")


def list_objects(base_url: str, key: str, bucket: str, prefix: str = "") -> list[str]:
    objects: list[str] = []
    offset = 0
    while True:
        response = requests.post(
            f"{base_url}/storage/v1/object/list/{bucket}",
            headers=headers(key, "application/json"),
            json={"prefix": prefix, "limit": 1000, "offset": offset, "sortBy": {"column": "name", "order": "asc"}},
            timeout=60,
        )
        if response.status_code == 404:
            return objects
        response.raise_for_status()
        page = response.json()
        if not page:
            break
        for item in page:
            name = item.get("name")
            if not name:
                continue
            object_path = f"{prefix.rstrip('/')}/{name}".strip("/")
            if item.get("metadata") is None:
                objects.extend(list_objects(base_url, key, bucket, object_path))
            else:
                objects.append(object_path)
        if len(page) < 1000:
            break
        offset += len(page)
    return objects


def remove_objects(base_url: str, key: str, bucket: str, objects: list[str]) -> int:
    removed = 0
    for start in range(0, len(objects), 100):
        chunk = objects[start:start + 100]
        response = requests.delete(
            f"{base_url}/storage/v1/object/{bucket}",
            headers=headers(key, "application/json"),
            json={"prefixes": chunk},
            timeout=60,
        )
        response.raise_for_status()
        removed += len(chunk)
        print(f"deleted {bucket}: {removed:,}/{len(objects):,}", flush=True)
    return removed


def upload_file(base_url: str, key: str, bucket: str, source: Path, object_path: str) -> None:
    content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    response = requests.post(
        f"{base_url}/storage/v1/object/{bucket}/{quote(object_path, safe='/')}",
        headers={**headers(key, content_type), "x-upsert": "true", "cache-control": "3600"},
        data=source.read_bytes(),
        timeout=25,
    )
    if response.status_code not in {200, 201}:
        raise RuntimeError(f"upload failed {bucket}/{object_path}: HTTP {response.status_code}: {response.text[:300]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--skip-delete", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Do not delete; skip remote objects that already exist.")
    parser.add_argument("--progress-every", type=int, default=50)
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()

    source = args.source.resolve()
    if not source.exists():
        raise SystemExit(f"ERROR: missing local file-storage mirror: {source}")
    files_by_bucket: dict[str, list[tuple[Path, str]]] = {}
    for bucket in BUCKETS:
        root = source / bucket
        files_by_bucket[bucket] = [
            (path, path.relative_to(root).as_posix())
            for path in sorted(root.rglob("*"))
            if path.is_file()
        ]
    total_files = sum(len(rows) for rows in files_by_bucket.values())
    total_bytes = sum(path.stat().st_size for rows in files_by_bucket.values() for path, _ in rows)
    print(f"local storage payload: {total_files:,} files / {total_bytes / 1024 / 1024:.2f} MB")
    for bucket, rows in files_by_bucket.items():
        print(f"  {bucket}: {len(rows):,} files / {sum(path.stat().st_size for path, _ in rows) / 1024 / 1024:.2f} MB")
    if not args.execute:
        print("dry run only; pass --execute to delete/upload Supabase Storage")
        return 0

    base_url, key = load_config()
    for bucket, rows in files_by_bucket.items():
        ensure_bucket(base_url, key, bucket)
        if args.resume:
            existing = set(list_objects(base_url, key, bucket))
            before = len(rows)
            rows = [(path, object_path) for path, object_path in rows if object_path not in existing]
            print(f"{bucket}: existing remote files={len(existing):,}; remaining uploads={len(rows):,}/{before:,}")
        elif not args.skip_delete:
            existing = list_objects(base_url, key, bucket)
            print(f"{bucket}: existing remote files={len(existing):,}")
            if existing:
                remove_objects(base_url, key, bucket, existing)
        uploaded = 0
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {
                pool.submit(upload_file, base_url, key, bucket, path, object_path): object_path
                for path, object_path in rows
            }
            for future in as_completed(futures):
                future.result()
                uploaded += 1
                if uploaded % max(1, args.progress_every) == 0 or uploaded == len(rows):
                    print(f"uploaded {bucket}: {uploaded:,}/{len(rows):,}", flush=True)
    print("Supabase Storage replacement complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
