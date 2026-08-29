#!/usr/bin/env python3
"""Build the canonical local Basketball headshot registry/cache.

This mirrors the Baseball cleanup: every playable Basketball player gets one
validated local image under raw/player_headshots/basketball, and the registry
records the active source. It does not upload anything to Supabase.
"""
from __future__ import annotations

import argparse
import csv
import html
import hashlib
import io
import re
import shutil
import sqlite3
import sys
import threading
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import requests

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover
    Image = None


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))

import server  # noqa: E402


RUNTIME_DB = ROOT / "raw" / "runtime_compact" / "teammatetag_runtime_minimal.sqlite"
REGISTRY_DB = ROOT / "raw" / "headshot_registry" / "basketball_headshots.sqlite"
CANONICAL_CACHE_DIR = ROOT / "raw" / "player_headshots" / "basketball"
USER_AGENT = "TeamMateTag basketball headshot registry/1.0 (local verification)"
MIN_IMAGE_SIZE = 80
NBA_PLACEHOLDER_URL = "https://cdn.nba.com/headshots/nba/latest/1040x760/2557.png"
BBGM_PHOTO_MAP_URL = "https://raw.githubusercontent.com/alexnoob/BasketBall-GM-Rosters/master/player-photos.json"
BBREF_INDEX_URL = "https://www.basketball-reference.com/players/{letter}/"
CROPPED_REVIEW = ROOT / "raw" / "cropped_headshots_review.csv"
CROPPED_DIR = ROOT / "raw" / "cropped_headshots" / "basketball"

_thread_local = threading.local()


@dataclass(frozen=True)
class Player:
    player_id: str
    player_name: str
    external_id: str | None
    debut_year: int | None
    final_year: int | None


@dataclass
class Candidate:
    player_id: str
    player_name: str
    source_url: str | None
    provider: str
    status: str
    content_sha256: str | None = None
    perceptual_hash: str | None = None
    width: int | None = None
    height: int | None = None
    local_path: str | None = None
    review_note: str | None = None


def safe_filename(player_id: str, suffix: str = ".png") -> str:
    return player_id.replace(":", "__") + suffix


def normalize_name(value: str | None) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).lower()
    value = value.replace("clar.", "clarence")
    value = value.replace("mickael", "michael")
    return re.sub(r"[^a-z0-9]", "", value)


def session() -> requests.Session:
    existing = getattr(_thread_local, "session", None)
    if existing is not None:
        return existing
    created = requests.Session()
    created.headers.update({"User-Agent": USER_AGENT})
    _thread_local.session = created
    return created


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


def canonical_jpeg(content: bytes) -> bytes:
    if Image is None:
        raise RuntimeError("Pillow is required to normalize images")
    image = Image.open(io.BytesIO(content))
    image = ImageOps.exif_transpose(image)
    if image.mode in {"RGBA", "LA"}:
        background = Image.new("RGB", image.size, (246, 248, 252))
        background.paste(image, mask=image.getchannel("A"))
        image = background
    else:
        image = image.convert("RGB")
    image.thumbnail((360, 450), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=88, optimize=True, progressive=True)
    return buffer.getvalue()


def fetch_image(url: str | None) -> dict[str, object]:
    if not url:
        return {"status": "missing", "error": "no candidate URL"}
    try:
        response = session().get(url, timeout=15)
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


def placeholder_fingerprints() -> tuple[set[str], set[str]]:
    placeholder = fetch_image(NBA_PLACEHOLDER_URL)
    if placeholder.get("status") != "ok":
        return set(), set()
    return {str(placeholder["content_sha256"])}, {str(placeholder["perceptual_hash"])}


def is_placeholder(result: dict[str, object], hashes: set[str], phashes: set[str]) -> bool:
    if result.get("status") != "ok":
        return False
    digest = str(result.get("content_sha256") or "")
    phash = str(result.get("perceptual_hash") or "")
    return digest in hashes or any(hamming(phash, known) <= 4 for known in phashes)


def load_players() -> list[Player]:
    with sqlite3.connect(RUNTIME_DB) as conn:
        rows = conn.execute(
            """
            SELECT player_id, display_name, external_id, debut_year, final_year
              FROM runtime_players
             WHERE scope = 'basketball'
             ORDER BY player_id
            """
        ).fetchall()
    return [Player(pid, name, str(external_id) if external_id else None, debut, final) for pid, name, external_id, debut, final in rows]


def create_registry(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS basketball_headshots;
        CREATE TABLE basketball_headshots (
            player_id TEXT PRIMARY KEY,
            player_name TEXT NOT NULL,
            external_id TEXT,
            source_url TEXT,
            provider TEXT NOT NULL,
            status TEXT NOT NULL,
            content_sha256 TEXT,
            perceptual_hash TEXT,
            width INTEGER,
            height INTEGER,
            local_path TEXT,
            review_note TEXT,
            debut_year INTEGER,
            final_year INTEGER
        ) WITHOUT ROWID;

        CREATE INDEX idx_basketball_headshots_status ON basketball_headshots(status, provider);
        """
    )


def local_cache_candidates(players: list[Player]) -> dict[str, Candidate]:
    if not CANONICAL_CACHE_DIR.exists():
        return {}
    by_id = {player.player_id: player for player in players}
    result: dict[str, Candidate] = {}
    for image_path in sorted(CANONICAL_CACHE_DIR.glob("*.*")):
        if image_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        stem = image_path.stem.replace("__", ":")
        player = by_id.get(stem)
        if not player:
            player = by_id.get(f"nba:{image_path.stem.removeprefix('nba_')}")
        if not player:
            continue
        try:
            digest, perceptual_hash, width, height = image_meta_file(image_path)
        except Exception as error:
            result[player.player_id] = Candidate(
                player.player_id,
                player.player_name,
                None,
                "Canonical Local Cache",
                "missing",
                local_path=str(image_path.relative_to(ROOT)).replace("\\", "/"),
                review_note=f"Canonical cached image failed validation: {error}",
            )
            continue
        if width < MIN_IMAGE_SIZE or height < MIN_IMAGE_SIZE:
            status = "missing"
            note = f"Canonical cached image too small: {width}x{height}"
        else:
            status = "verified"
            note = None
        canonical = CANONICAL_CACHE_DIR / safe_filename(player.player_id, image_path.suffix.lower())
        local_path = str(canonical.relative_to(ROOT)).replace("\\", "/")
        if image_path != canonical and status == "verified":
            canonical.write_bytes(image_path.read_bytes())
        result[player.player_id] = Candidate(
            player.player_id,
            player.player_name,
            f"/local-headshots/basketball/{canonical.name}" if status == "verified" else None,
            "Canonical Local Cache",
            status,
            digest,
            perceptual_hash,
            width,
            height,
            local_path,
            note,
        )
    return result


def cropped_review_candidates(players: list[Player], existing: dict[str, Candidate]) -> dict[str, Candidate]:
    if not CROPPED_REVIEW.exists() or not CROPPED_DIR.exists():
        return {}
    by_id = {player.player_id: player for player in players}
    results: dict[str, Candidate] = {}
    with CROPPED_REVIEW.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if (row.get("sport") or row.get("sport_id")) != "basketball":
                continue
            player_id = row.get("player_id") or ""
            player = by_id.get(player_id)
            if not player:
                continue
            if existing.get(player_id) and existing[player_id].status == "verified":
                continue
            path_value = row.get("local_path") or ""
            image_path = Path(path_value) if path_value else CROPPED_DIR / safe_filename(player_id, ".jpg").replace("__", "_")
            if not image_path.is_absolute():
                image_path = ROOT / image_path
            if not image_path.exists():
                continue
            try:
                digest, perceptual_hash, width, height = image_meta_file(image_path)
            except Exception:
                continue
            if width < MIN_IMAGE_SIZE or height < MIN_IMAGE_SIZE:
                continue
            canonical = CANONICAL_CACHE_DIR / safe_filename(player_id, image_path.suffix.lower())
            canonical.write_bytes(image_path.read_bytes())
            results[player_id] = Candidate(
                player.player_id,
                player.player_name,
                f"/local-headshots/basketball/{canonical.name}",
                row.get("provider") or "Reviewed crop",
                "verified",
                digest,
                perceptual_hash,
                width,
                height,
                str(canonical.relative_to(ROOT)).replace("\\", "/"),
                row.get("note") or "Reviewed cropped Basketball headshot.",
            )
    return results


def basketball_reference_index() -> dict[str, list[tuple[str, str, int | None, int | None]]]:
    by_name: dict[str, list[tuple[str, str, int | None, int | None]]] = {}
    for letter in "abcdefghijklmnopqrstuvwxyz":
        response = session().get(BBREF_INDEX_URL.format(letter=letter), timeout=30)
        response.raise_for_status()
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", response.text, flags=re.S):
            match = re.search(r'href="/players/[a-z]/([a-z0-9]+)\.html"[^>]*>(.*?)</a>', row, flags=re.S)
            if not match:
                continue
            years = re.findall(r'data-stat="year_(?:min|max)"[^>]*>(\d{4})', row)
            debut = int(years[0]) if years else None
            final = int(years[1]) if len(years) > 1 else debut
            name = html.unescape(re.sub(r"<[^>]+>", "", match.group(2))).strip()
            by_name.setdefault(normalize_name(name), []).append((match.group(1), name, debut, final))
    return by_name


def bbgm_map_candidates(players: list[Player], existing: dict[str, Candidate], workers: int) -> dict[str, Candidate]:
    targets = [player for player in players if not (existing.get(player.player_id) and existing[player.player_id].status == "verified")]
    if not targets:
        return {}
    try:
        photos = session().get(BBGM_PHOTO_MAP_URL, timeout=60).json()
        index = basketball_reference_index()
    except Exception as error:
        print(f"  BBGM map skipped: {error}", flush=True)
        return {}

    jobs: list[tuple[Player, str, str]] = []
    for player in targets:
        candidates = [item for item in index.get(normalize_name(player.player_name), []) if item[0] in photos]
        if not candidates:
            continue
        def score(item: tuple[str, str, int | None, int | None]) -> tuple[int, int]:
            _slug, _name, debut, final = item
            debut_score = abs((debut or player.debut_year or 0) - (player.debut_year or 0))
            final_score = abs((final or player.final_year or 0) - (player.final_year or 0))
            exact_years = 0 if debut == player.debut_year and final == player.final_year else 1
            return exact_years, debut_score + final_score
        ranked = sorted(candidates, key=score)
        if len(ranked) > 1 and score(ranked[0]) == score(ranked[1]):
            continue
        slug, _name, _debut, _final = ranked[0]
        jobs.append((player, slug, photos[slug]))
    if not jobs:
        return {}

    hashes, phashes = placeholder_fingerprints()
    results: dict[str, Candidate] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fetch_image, url): (player, slug, url) for player, slug, url in jobs}
        for index, future in enumerate(as_completed(futures), 1):
            player, slug, url = futures[future]
            result = future.result()
            if result.get("status") == "ok" and not is_placeholder(result, hashes, phashes):
                results[player.player_id] = Candidate(
                    player.player_id,
                    player.player_name,
                    url,
                    "BBGM community map",
                    "verified",
                    str(result.get("content_sha256")),
                    str(result.get("perceptual_hash")),
                    int(result["width"]) if result.get("width") else None,
                    int(result["height"]) if result.get("height") else None,
                    None,
                    f"Identity matched to Basketball-Reference slug {slug} by name and career years; playtest-only community mapping.",
                )
            if index % 100 == 0 or index == len(futures):
                print(f"  BBGM map checked {index:,}/{len(futures):,}", flush=True)
    return results


def official_nba_candidates(players: list[Player], covered_ids: set[str], workers: int) -> dict[str, Candidate]:
    hashes, phashes = placeholder_fingerprints()
    rows = [player for player in players if player.player_id not in covered_ids and player.external_id]

    def inspect_player(player: Player) -> tuple[str, str, dict[str, object]]:
        urls = [
            f"https://cdn.nba.com/headshots/nba/latest/1040x760/{player.external_id}.png",
            f"https://cdn.nba.com/headshots/nba/latest/260x190/{player.external_id}.png",
        ]
        last = {"status": "missing", "error": "no candidate URL"}
        for url in urls:
            result = fetch_image(url)
            last = result
            if result.get("status") == "ok" and not is_placeholder(result, hashes, phashes):
                return player.player_id, url, result
        return player.player_id, urls[0], last

    results: dict[str, tuple[str, dict[str, object]]] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(inspect_player, player): player for player in rows}
        for index, future in enumerate(as_completed(futures), 1):
            player_id, url, result = future.result()
            results[player_id] = (url, result)
            if index % 250 == 0 or index == len(futures):
                print(f"  NBA CDN checked {index:,}/{len(futures):,}", flush=True)
    candidates: dict[str, Candidate] = {}
    for player in rows:
        url, result = results[player.player_id]
        status = str(result.get("status", "missing"))
        note = result.get("error")
        if status == "ok":
            if is_placeholder(result, hashes, phashes):
                status = "placeholder"
                note = "NBA generic placeholder image"
            else:
                status = "verified"
                note = None
        candidates[player.player_id] = Candidate(
            player.player_id,
            player.player_name,
            url if status == "verified" else None,
            "NBA CDN",
            status,
            str(result.get("content_sha256")) if result.get("content_sha256") else None,
            str(result.get("perceptual_hash")) if result.get("perceptual_hash") else None,
            int(result["width"]) if result.get("width") else None,
            int(result["height"]) if result.get("height") else None,
            None,
            str(note) if note else None,
        )
    return candidates


def cache_verified_images(workers: int) -> int:
    CANONICAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(REGISTRY_DB) as registry:
        rows = registry.execute(
            """
            SELECT player_id, source_url, content_sha256, local_path
              FROM basketball_headshots
             WHERE status = 'verified'
             ORDER BY player_id
            """
        ).fetchall()

    def cache_one(row: tuple[str, str | None, str | None, str | None]) -> tuple[str, str | None]:
        player_id, url, expected_digest, local_path = row
        existing_path = ROOT / local_path if local_path else None
        content: bytes | None = None
        if existing_path and existing_path.exists():
            content = existing_path.read_bytes()
            digest, _phash, _width, _height = image_meta_bytes(content)
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
        try:
            output = canonical_jpeg(content)
        except Exception:
            return player_id, None
        path = CANONICAL_CACHE_DIR / safe_filename(player_id, ".jpg")
        path.write_bytes(output)
        try:
            digest, perceptual_hash, width, height = image_meta_bytes(output)
        except Exception:
            return player_id, None
        return player_id, str(path.relative_to(ROOT)).replace("\\", "/"), digest, perceptual_hash, width, height

    cached: list[tuple[str, str, str, str, int, int, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(cache_one, row): row[0] for row in rows}
        for index, future in enumerate(as_completed(futures), 1):
            result = future.result()
            player_id = result[0]
            local_path = result[1] if len(result) > 1 else None
            if local_path:
                _pid, local_path, digest, perceptual_hash, width, height = result
                cached.append(
                    (
                        local_path,
                        f"/local-headshots/basketball/{Path(local_path).name}",
                        digest,
                        perceptual_hash,
                        width,
                        height,
                        player_id,
                    )
                )
            if index % 500 == 0 or index == len(rows):
                print(f"  cached canonical images {index:,}/{len(rows):,}", flush=True)
    if cached:
        with sqlite3.connect(REGISTRY_DB) as registry:
            registry.executemany(
                """
                UPDATE basketball_headshots
                   SET local_path = ?,
                       source_url = ?,
                       content_sha256 = ?,
                       perceptual_hash = ?,
                       width = ?,
                       height = ?
                 WHERE player_id = ?
                """,
                cached,
            )
            registry.commit()
    return len(cached)


def insert_candidates(conn: sqlite3.Connection, players: list[Player], candidates: dict[str, Candidate]) -> None:
    rows = []
    for player in players:
        candidate = candidates.get(player.player_id) or Candidate(
            player.player_id,
            player.player_name,
            None,
            "none",
            "missing",
            review_note="No verified Basketball headshot candidate",
        )
        rows.append(
            (
                player.player_id,
                player.player_name,
                player.external_id,
                candidate.source_url,
                candidate.provider,
                candidate.status,
                candidate.content_sha256,
                candidate.perceptual_hash,
                candidate.width,
                candidate.height,
                candidate.local_path,
                candidate.review_note,
                player.debut_year,
                player.final_year,
            )
        )
    conn.executemany(
        """
        INSERT INTO basketball_headshots (
            player_id, player_name, external_id, source_url, provider, status,
            content_sha256, perceptual_hash, width, height, local_path,
            review_note, debut_year, final_year
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def remove_legacy_external_id_files() -> int:
    removed = 0
    active = {safe_filename(player.player_id, ".jpg") for player in load_players()}
    for image_path in sorted(CANONICAL_CACHE_DIR.glob("*.*")):
        if image_path.name in active:
            continue
        image_path.unlink()
        removed += 1
    return removed


def build_registry(workers: int = 16, limit: int = 0) -> dict[str, int | float]:
    players = load_players()
    if limit:
        players = players[:limit]
    REGISTRY_DB.parent.mkdir(parents=True, exist_ok=True)
    CANONICAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(REGISTRY_DB) as registry:
        create_registry(registry)
        cache = local_cache_candidates(players)
        verified_cache = {player_id for player_id, row in cache.items() if row.status == "verified"}
        print(f"canonical cache verified {len(verified_cache):,}/{len(players):,}", flush=True)
        nba = official_nba_candidates(players, verified_cache, workers)
        candidates = {**nba, **cache}
        cropped = cropped_review_candidates(players, candidates)
        candidates.update(cropped)
        bbgm = bbgm_map_candidates(players, candidates, workers)
        candidates.update(bbgm)
        insert_candidates(registry, players, candidates)
        registry.commit()
        registry.execute("VACUUM")
        counts = dict(registry.execute("SELECT status, COUNT(*) FROM basketball_headshots GROUP BY status").fetchall())
        provider_counts = dict(
            registry.execute(
                "SELECT provider || ':' || status, COUNT(*) FROM basketball_headshots GROUP BY provider, status"
            ).fetchall()
        )
    cached = cache_verified_images(workers)
    removed_legacy = remove_legacy_external_id_files()
    return {
        "players": len(players),
        "verified": int(counts.get("verified", 0)),
        "cached_images": cached,
        "placeholder": int(counts.get("placeholder", 0)),
        "missing": int(counts.get("missing", 0)),
        "removed_legacy_external_id_files": removed_legacy,
        "cache_size_mb": round(sum(p.stat().st_size for p in CANONICAL_CACHE_DIR.glob("*.*") if p.is_file()) / 1024 / 1024, 3),
        "size_mb": round(REGISTRY_DB.stat().st_size / 1024 / 1024, 3),
        **{f"provider_{key}": value for key, value in provider_counts.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    stats = build_registry(workers=args.workers, limit=args.limit)
    print(f"local registry: {REGISTRY_DB}")
    for key, value in sorted(stats.items()):
        print(f"{key}={value:,}" if isinstance(value, int) else f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
