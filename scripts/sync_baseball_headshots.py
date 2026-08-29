#!/usr/bin/env python3
"""Build and optionally publish the canonical local Baseball headshot registry.

Priority order:
1. Verified canonical local cache images.
2. Verified local OOTP facepack images, when the source archive/staging exists.
3. Verified MLBAM CDN images for players not covered locally.
4. Durable verified historical URL overrides.

The registry is local-first and stored under raw/. It records every active
Baseball player as verified, placeholder, missing, or needs_review so gaps are
explicit instead of silently falling through to a generic image.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import os
import sqlite3
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))

import server  # noqa: E402


SOURCE_DIR = ROOT / "raw" / "ootp" / "matched_mlb_headshots"
REGISTRY_DB = ROOT / "raw" / "headshot_registry" / "baseball_headshots.sqlite"
LEGACY_REGISTRY_DB = ROOT / "raw" / "headshot_registry" / "baseball_ootp_headshots.sqlite"
RUNTIME_DB = ROOT / "raw" / "runtime_compact" / "teammatetag_runtime_minimal.sqlite"
BASEBALL_DB = ROOT / "db" / "base2nerdle.sqlite"
BUCKET = "player-headshots"
OBJECT_PREFIX = "baseball"
USER_AGENT = "TeamMateTag baseball headshot registry/2.0 (local verification)"
MLBAM_PLACEHOLDER_ID = 150411
MIN_IMAGE_SIZE = 80
VERIFIED_OVERRIDES = ROOT / "raw" / "headshot_registry" / "baseball_verified_overrides.csv"
CANONICAL_CACHE_DIR = ROOT / "raw" / "player_headshots" / "baseball"

_thread_local = threading.local()


@dataclass(frozen=True)
class Player:
    player_id: str
    player_name: str
    mlbam_id: str | None
    debut_year: int | None
    final_year: int | None


@dataclass
class Candidate:
    player_id: str
    player_name: str
    source_url: str | None
    fallback_url: str | None
    provider: str
    status: str
    content_sha256: str | None = None
    perceptual_hash: str | None = None
    width: int | None = None
    height: int | None = None
    local_path: str | None = None
    object_path: str | None = None
    public_url: str | None = None
    review_note: str | None = None


def session() -> requests.Session:
    existing = getattr(_thread_local, "session", None)
    if existing is not None:
        return existing
    created = requests.Session()
    created.headers.update({"User-Agent": USER_AGENT})
    _thread_local.session = created
    return created


def storage_config() -> tuple[str, str]:
    if load_dotenv:
        load_dotenv(ROOT / ".env")
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


def dhash(content: bytes) -> tuple[str, int, int]:
    if Image is None:
        raise RuntimeError("Pillow is required to validate images")
    image = Image.open(io.BytesIO(content)).convert("L")
    width, height = image.size
    resized = image.resize((9, 8))
    pixels = list(resized.getdata())
    bits = "".join(
        "1" if pixels[row * 9 + col] > pixels[row * 9 + col + 1] else "0"
        for row in range(8)
        for col in range(8)
    )
    return f"{int(bits, 2):016x}", width, height


def hamming(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def image_meta_bytes(content: bytes) -> tuple[str, str, int, int]:
    perceptual_hash, width, height = dhash(content)
    return hashlib.sha256(content).hexdigest(), perceptual_hash, width, height


def image_meta_file(path: Path) -> tuple[str, str, int, int]:
    return image_meta_bytes(path.read_bytes())


def fetch_image(url: str | None) -> dict[str, object]:
    if not url:
        return {"status": "missing", "error": "no candidate URL"}
    try:
        response = session().get(url, timeout=10)
    except requests.RequestException as error:
        return {"status": "missing", "error": str(error)[:300]}
    if response.status_code != 200:
        return {"status": "missing", "error": f"HTTP {response.status_code}"}
    if not response.content:
        return {"status": "missing", "error": "empty response"}
    try:
        digest, perceptual_hash, width, height = image_meta_bytes(response.content)
    except Exception as error:
        return {"status": "missing", "error": f"not a decodable image: {error}"}
    if width < MIN_IMAGE_SIZE or height < MIN_IMAGE_SIZE:
        return {"status": "missing", "error": f"image too small: {width}x{height}"}
    return {
        "status": "ok",
        "content": response.content,
        "content_sha256": digest,
        "perceptual_hash": perceptual_hash,
        "width": width,
        "height": height,
    }


def load_players() -> list[Player]:
    if RUNTIME_DB.exists():
        with sqlite3.connect(RUNTIME_DB) as conn:
            rows = conn.execute(
                """
                SELECT player_id, display_name, external_id, debut_year, final_year
                  FROM runtime_players
                 WHERE scope = 'baseball'
                 ORDER BY player_id
                """
            ).fetchall()
        return [
            Player(player_id, name, str(external_id) if external_id else None, debut, final)
            for player_id, name, external_id, debut, final in rows
        ]
    with sqlite3.connect(BASEBALL_DB) as conn:
        rows = conn.execute(
            """
            SELECT player_id, TRIM(COALESCE(name_first, '') || ' ' || COALESCE(name_last, '')),
                   mlbam_id, debut_year, final_year
              FROM players
             WHERE final_year >= 2000
             ORDER BY player_id
            """
        ).fetchall()
    return [
        Player(player_id, name, str(mlbam_id) if mlbam_id else None, debut, final)
        for player_id, name, mlbam_id, debut, final in rows
    ]


def create_registry(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS baseball_headshots;
        CREATE TABLE baseball_headshots (
            player_id TEXT PRIMARY KEY,
            player_name TEXT NOT NULL,
            mlbam_id TEXT,
            source_url TEXT,
            fallback_url TEXT,
            provider TEXT NOT NULL,
            status TEXT NOT NULL,
            content_sha256 TEXT,
            perceptual_hash TEXT,
            width INTEGER,
            height INTEGER,
            local_path TEXT,
            object_path TEXT,
            public_url TEXT,
            review_note TEXT,
            debut_year INTEGER,
            final_year INTEGER
        ) WITHOUT ROWID;

        CREATE INDEX idx_baseball_headshots_status ON baseball_headshots(status, provider);
        """
    )


def ootp_candidates(players: list[Player]) -> dict[str, Candidate]:
    if not SOURCE_DIR.exists():
        return {}
    by_id = {player.player_id: player for player in players}
    result: dict[str, Candidate] = {}
    for image_path in sorted(SOURCE_DIR.glob("*.*")):
        if image_path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            continue
        player = by_id.get(image_path.stem)
        if not player:
            continue
        try:
            digest, perceptual_hash, width, height = image_meta_file(image_path)
        except Exception as error:
            result[player.player_id] = Candidate(
                player.player_id,
                player.player_name,
                None,
                None,
                "OOTP Facepack",
                "missing",
                local_path=str(image_path.relative_to(ROOT)).replace("\\", "/"),
                review_note=f"Local OOTP image failed validation: {error}",
            )
            continue
        status = "verified" if width >= MIN_IMAGE_SIZE and height >= MIN_IMAGE_SIZE else "missing"
        note = None if status == "verified" else f"Local OOTP image too small: {width}x{height}"
        object_path = f"{OBJECT_PREFIX}/ootp/{image_path.name}"
        result[player.player_id] = Candidate(
            player.player_id,
            player.player_name,
            f"/local-headshots/ootp/{image_path.name}",
            None,
            "OOTP Facepack",
            status,
            digest,
            perceptual_hash,
            width,
            height,
            str(image_path.relative_to(ROOT)).replace("\\", "/"),
            object_path,
            None,
            note,
        )
    return result


def local_cache_candidates(players: list[Player]) -> dict[str, Candidate]:
    """Use the canonical saved headshot store as the first-class source."""
    if not CANONICAL_CACHE_DIR.exists():
        return {}
    by_id = {player.player_id: player for player in players}
    result: dict[str, Candidate] = {}
    for image_path in sorted(CANONICAL_CACHE_DIR.glob("*.*")):
        if image_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        player = by_id.get(image_path.stem)
        if not player:
            continue
        try:
            digest, perceptual_hash, width, height = image_meta_file(image_path)
        except Exception as error:
            result[player.player_id] = Candidate(
                player.player_id,
                player.player_name,
                None,
                None,
                "Canonical Local Cache",
                "missing",
                local_path=str(image_path.relative_to(ROOT)).replace("\\", "/"),
                review_note=f"Canonical cached image failed validation: {error}",
            )
            continue
        status = "verified" if width >= MIN_IMAGE_SIZE and height >= MIN_IMAGE_SIZE else "missing"
        note = None if status == "verified" else f"Canonical cached image too small: {width}x{height}"
        object_path = f"{OBJECT_PREFIX}/canonical/{image_path.name}"
        result[player.player_id] = Candidate(
            player.player_id,
            player.player_name,
            f"/local-headshots/baseball/{image_path.name}",
            None,
            "Canonical Local Cache",
            status,
            digest,
            perceptual_hash,
            width,
            height,
            str(image_path.relative_to(ROOT)).replace("\\", "/"),
            object_path,
            None,
            note,
        )
    return result


def classify_mlbam(players: list[Player], covered_ids: set[str], workers: int) -> dict[str, Candidate]:
    placeholder = fetch_image(server.HEADSHOT_URL.format(MLBAM_PLACEHOLDER_ID))
    placeholder_digests = {str(placeholder["content_sha256"])} if placeholder.get("status") == "ok" else set()
    placeholder_hashes = {str(placeholder["perceptual_hash"])} if placeholder.get("status") == "ok" else set()

    rows = [player for player in players if player.player_id not in covered_ids]
    results: dict[str, dict[str, object]] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(fetch_image, server.HEADSHOT_URL.format(player.mlbam_id) if player.mlbam_id else None): player
            for player in rows
        }
        for index, future in enumerate(as_completed(futures), 1):
            player = futures[future]
            results[player.player_id] = future.result()
            if index % 500 == 0 or index == len(futures):
                print(f"  MLBAM checked {index:,}/{len(futures):,}", flush=True)

    duplicate_counts = Counter(
        str(result.get("content_sha256"))
        for result in results.values()
        if result.get("status") == "ok" and result.get("content_sha256")
    )
    candidates: dict[str, Candidate] = {}
    for player in rows:
        url = server.HEADSHOT_URL.format(player.mlbam_id) if player.mlbam_id else None
        result = results[player.player_id]
        status = str(result.get("status", "missing"))
        note = result.get("error")
        if status == "ok":
            digest = str(result["content_sha256"])
            perceptual_hash = str(result["perceptual_hash"])
            is_placeholder = digest in placeholder_digests or any(
                hamming(perceptual_hash, known) <= 4 for known in placeholder_hashes
            )
            is_mass_duplicate = duplicate_counts[digest] > 2
            if is_placeholder or is_mass_duplicate:
                status = "placeholder"
                note = "MLBAM generic placeholder image" if is_placeholder else "MLBAM byte-identical image shared by multiple players"
            else:
                status = "verified"
                note = None
        candidates[player.player_id] = Candidate(
            player.player_id,
            player.player_name,
            url if status == "verified" else None,
            url if status != "verified" else None,
            "MLBAM",
            status,
            str(result.get("content_sha256")) if result.get("content_sha256") else None,
            str(result.get("perceptual_hash")) if result.get("perceptual_hash") else None,
            int(result["width"]) if result.get("width") else None,
            int(result["height"]) if result.get("height") else None,
            None,
            None,
            None,
            str(note) if note else None,
        )
    return candidates


def verified_override_candidates(
    players: list[Player],
    existing: dict[str, Candidate],
    workers: int,
) -> dict[str, Candidate]:
    if not VERIFIED_OVERRIDES.exists():
        return {}
    by_id = {player.player_id: player for player in players}
    candidate_rows: list[tuple[Player, str, str, str]] = []
    with VERIFIED_OVERRIDES.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            player_id = row.get("player_id") or ""
            player = by_id.get(player_id)
            if not player:
                continue
            if existing.get(player_id) and existing[player_id].status == "verified":
                continue
            url = row.get("source_url") or ""
            provider = row.get("provider") or "verified override"
            if not url:
                continue
            note = row.get("review_note") or f"{provider} verified fallback"
            candidate_rows.append((player, provider, url, note))
    if not candidate_rows:
        return {}

    results: dict[str, Candidate] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fetch_image, url): (player, provider, url, note) for player, provider, url, note in candidate_rows}
        for future in as_completed(futures):
            player, provider, url, note = futures[future]
            result = future.result()
            if result.get("status") != "ok":
                continue
            results[player.player_id] = Candidate(
                player.player_id,
                player.player_name,
                url,
                None,
                provider,
                "verified",
                str(result.get("content_sha256")),
                str(result.get("perceptual_hash")),
                int(result["width"]) if result.get("width") else None,
                int(result["height"]) if result.get("height") else None,
                None,
                None,
                None,
                note[:300],
            )
    return results


def extension_for_url(url: str) -> str:
    clean = url.split("?", 1)[0].lower()
    if clean.endswith(".jpg") or clean.endswith(".jpeg"):
        return ".jpg"
    if clean.endswith(".webp"):
        return ".webp"
    return ".png"


def cache_verified_images(workers: int) -> int:
    CANONICAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(REGISTRY_DB) as registry:
        rows = registry.execute(
            """
            SELECT player_id, source_url, provider, content_sha256, local_path
              FROM baseball_headshots
             WHERE status = 'verified'
             ORDER BY player_id
            """
        ).fetchall()

    def cache_one(row: tuple[str, str | None, str, str | None, str | None]) -> tuple[str, str | None]:
        player_id, url, provider, expected_digest, local_path = row
        content: bytes | None = None
        suffix = ".jpg"
        if provider == "OOTP Facepack" and local_path:
            source_path = ROOT / local_path
            if not source_path.exists():
                return player_id, None
            content = source_path.read_bytes()
            suffix = source_path.suffix.lower() if source_path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} else ".jpg"
            try:
                digest, _perceptual_hash, _width, _height = image_meta_bytes(content)
            except Exception:
                return player_id, None
            if expected_digest and digest != expected_digest:
                return player_id, None
        else:
            result = fetch_image(url)
            if result.get("status") != "ok":
                return player_id, None
            content = result.get("content") if isinstance(result.get("content"), bytes) else None
            digest = result.get("content_sha256")
            if content is None or (expected_digest and digest != expected_digest):
                return player_id, None
            suffix = extension_for_url(url or "")
        path = CANONICAL_CACHE_DIR / f"{player_id}{suffix}"
        path.write_bytes(content)
        return player_id, str(path.relative_to(ROOT)).replace("\\", "/")

    cached: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(cache_one, row): row[0] for row in rows}
        for index, future in enumerate(as_completed(futures), 1):
            player_id, local_path = future.result()
            if local_path:
                cached.append((local_path, player_id))
            if index % 500 == 0 or index == len(rows):
                print(f"  cached canonical images {index:,}/{len(rows):,}", flush=True)
    if cached:
        with sqlite3.connect(REGISTRY_DB) as registry:
            registry.executemany(
                "UPDATE baseball_headshots SET local_path = ? WHERE player_id = ?",
                cached,
            )
            registry.commit()
    return len(cached)


def insert_candidates(conn: sqlite3.Connection, players: list[Player], candidates: dict[str, Candidate]) -> None:
    by_id = {player.player_id: player for player in players}
    rows = []
    for player_id in sorted(by_id):
        player = by_id[player_id]
        candidate = candidates.get(player_id) or Candidate(
            player.player_id,
            player.player_name,
            None,
            None,
            "none",
            "missing",
            review_note="No OOTP or MLBAM candidate URL",
        )
        rows.append(
            (
                player.player_id,
                player.player_name,
                player.mlbam_id,
                candidate.source_url,
                candidate.fallback_url,
                candidate.provider,
                candidate.status,
                candidate.content_sha256,
                candidate.perceptual_hash,
                candidate.width,
                candidate.height,
                candidate.local_path,
                candidate.object_path,
                candidate.public_url,
                candidate.review_note,
                player.debut_year,
                player.final_year,
            )
        )
    conn.executemany(
        """
        INSERT INTO baseball_headshots (
            player_id, player_name, mlbam_id, source_url, fallback_url,
            provider, status, content_sha256, perceptual_hash, width, height,
            local_path, object_path, public_url, review_note, debut_year, final_year
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def build_registry(workers: int = 16, limit: int = 0) -> dict[str, int | float]:
    players = load_players()
    if limit:
        players = players[:limit]
    REGISTRY_DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(REGISTRY_DB) as registry:
        create_registry(registry)
        cache = local_cache_candidates(players)
        verified_cache = {player_id for player_id, row in cache.items() if row.status == "verified"}
        print(f"canonical cache verified {len(verified_cache):,}/{len(players):,}", flush=True)
        ootp = ootp_candidates([player for player in players if player.player_id not in verified_cache])
        verified_ootp = {player_id for player_id, row in ootp.items() if row.status == "verified"}
        print(f"OOTP verified {len(verified_ootp):,}/{len(players):,}", flush=True)
        covered = verified_cache | verified_ootp
        mlbam = classify_mlbam(players, covered, workers)
        candidates = {**mlbam, **ootp, **cache}
        overrides = verified_override_candidates(players, candidates, workers)
        candidates.update(overrides)
        insert_candidates(registry, players, candidates)
        registry.commit()
        registry.execute("VACUUM")
        counts = dict(registry.execute("SELECT status, COUNT(*) FROM baseball_headshots GROUP BY status").fetchall())
        provider_counts = dict(
            registry.execute(
                "SELECT provider || ':' || status, COUNT(*) FROM baseball_headshots GROUP BY provider, status"
            ).fetchall()
        )
    if LEGACY_REGISTRY_DB.exists():
        LEGACY_REGISTRY_DB.unlink()
    cached = cache_verified_images(workers)
    return {
        "players": len(players),
        "verified": int(counts.get("verified", 0)),
        "cached_url_images": cached,
        "placeholder": int(counts.get("placeholder", 0)),
        "missing": int(counts.get("missing", 0)),
        "needs_review": int(counts.get("needs_review", 0)),
        "size_mb": round(REGISTRY_DB.stat().st_size / 1024 / 1024, 3),
        **{f"provider_{key}": value for key, value in provider_counts.items()},
    }


def upload_one(base_url: str, key: str, row: tuple[str, str, str]) -> tuple[str, str, str]:
    player_id, local_path, object_path = row
    image_path = ROOT / local_path
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


def publish_registry(workers: int = 8, limit: int = 0) -> dict[str, int]:
    if not REGISTRY_DB.exists():
        build_registry()
    base_url, key = storage_config()
    ensure_public_bucket(base_url, key)
    with sqlite3.connect(REGISTRY_DB) as registry:
        rows = registry.execute(
            """
            SELECT player_id, local_path, object_path
              FROM baseball_headshots
             WHERE status = 'verified'
               AND provider = 'OOTP Facepack'
               AND local_path IS NOT NULL
             ORDER BY player_id
            """
        ).fetchall()
    if limit:
        rows = rows[:limit]
    results = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(upload_one, base_url, key, row): row[0] for row in rows}
        for index, future in enumerate(as_completed(futures), 1):
            results.append(future.result())
            if index % 250 == 0 or index == len(rows):
                ok = sum(status == "published" for _pid, status, _url in results)
                print(f"uploaded {index:,}/{len(rows):,} ({ok:,} ok)", flush=True)
    published_urls = {pid: url for pid, status, url in results if status == "published"}
    if published_urls:
        with sqlite3.connect(REGISTRY_DB) as registry:
            registry.executemany(
                "UPDATE baseball_headshots SET public_url = ?, source_url = ? WHERE player_id = ?",
                [(url, url, player_id) for player_id, url in published_urls.items()],
            )
            registry.commit()
    with sqlite3.connect(REGISTRY_DB) as registry:
        details = registry.execute(
            """
            SELECT player_id, source_url, fallback_url, provider, status,
                   content_sha256, perceptual_hash, width, height, review_note
              FROM baseball_headshots
             WHERE status = 'verified'
            """
        ).fetchall()
    with server.db() as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO player_headshots
                (sport_id, player_id, source_url, fallback_url, provider, status,
                 content_sha256, perceptual_hash, width, height, checked_at, reviewed_at, review_note)
            VALUES ('baseball', %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now(), %s)
            ON CONFLICT (sport_id, player_id) DO UPDATE SET
                source_url = EXCLUDED.source_url,
                fallback_url = EXCLUDED.fallback_url,
                provider = EXCLUDED.provider,
                status = EXCLUDED.status,
                content_sha256 = EXCLUDED.content_sha256,
                perceptual_hash = EXCLUDED.perceptual_hash,
                width = EXCLUDED.width,
                height = EXCLUDED.height,
                checked_at = now(),
                reviewed_at = now(),
                review_note = EXCLUDED.review_note
            """,
            details,
        )
    return {"attempted": len(rows), "published": len(published_urls), "failed": len(rows) - len(published_urls)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    stats = build_registry(workers=args.workers, limit=args.limit)
    print(f"local registry: {REGISTRY_DB}")
    for key, value in sorted(stats.items()):
        print(f"{key}={value:,}" if isinstance(value, int) else f"{key}={value}")
    if args.upload:
        published = publish_registry(workers=args.workers, limit=args.limit)
        print(
            f"published={published['published']:,} attempted={published['attempted']:,} "
            f"failed={published['failed']:,}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
