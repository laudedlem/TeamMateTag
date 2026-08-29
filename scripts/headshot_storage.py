"""Shared Supabase Storage helpers for reviewed headshot tools."""
from __future__ import annotations

import os
from urllib.parse import quote

import requests

BUCKET = "player-headshots"


def storage_config() -> tuple[str, str]:
    base_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_SECRET_KEY", "")
    if not base_url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required in .env")
    return base_url, key


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
