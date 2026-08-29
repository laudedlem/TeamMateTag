#!/usr/bin/env python3
"""Build the canonical local Football headshot registry/cache.

Football does not yet have complete public headshot coverage. This keeps the
game-ready images clean anyway: one verified local JPEG per resolved player,
no duplicate placeholder image files, plus gap reports for players that should
be excluded from photo-dependent daily picks.
"""
from __future__ import annotations

import argparse
import csv
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


RUNTIME_DB = ROOT / "raw" / "runtime_compact" / "teammatetag_runtime_minimal.sqlite"
REGISTRY_DB = ROOT / "raw" / "headshot_registry" / "football_headshots.sqlite"
CANONICAL_CACHE_DIR = ROOT / "raw" / "player_headshots" / "football"
STAGING_CACHE_DIR = ROOT / "raw" / "player_headshots" / "football_canonical_staging"
LEGACY_CACHE_DIR = ROOT / "raw" / "player_headshots" / "football_legacy_unverified"
ESPN_CATALOG_DIR = ROOT / "raw" / "espn_nfl_athlete_pages"
CSV_SOURCES = [
    ("Manual verified", ROOT / "raw" / "football_manual_headshots.csv"),
    ("Web image search", ROOT / "raw" / "nfl_web_image_headshots.csv"),
    ("TheSportsDB", ROOT / "raw" / "nfl_footballdb_headshots.csv"),
    ("ESPN catalog", ROOT / "raw" / "nfl_espn_catalog_headshots.csv"),
]
USER_AGENT = "TeamMateTag football headshot registry/1.0 (local verification)"
MIN_IMAGE_SIZE = 80
MIN_PRIORITY_GAMES = 50
KNOWN_PLACEHOLDER_SHA256 = {
    # Repeated placeholder image in the old 19 GB Football cache.
    "33be6a8e3c2e353f497f2d70d75211d71d80779dcc2519b17d3045730cfc0598",
}

_thread_local = threading.local()


@dataclass(frozen=True)
class Player:
    player_id: str
    player_name: str
    external_id: str | None
    primary_pos: str | None
    debut_year: int | None
    final_year: int | None
    career_games: int


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


def safe_filename(player_id: str, suffix: str = ".jpg") -> str:
    return player_id.replace(":", "__") + suffix


def old_cache_filename(player_id: str) -> str:
    return player_id.replace(":", "_") + ".png"


def normalize_name(value: str | None) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).lower()
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


def image_meta_bytes(content: bytes) -> tuple[str, str, int, int]:
    perceptual_hash, width, height = dhash(content)
    return hashlib.sha256(content).hexdigest(), perceptual_hash, width, height


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
    response = None
    for attempt in range(4):
        try:
            response = session().get(url, timeout=20)
        except requests.RequestException as error:
            if attempt == 3:
                return {"status": "missing", "error": str(error)[:300]}
            threading.Event().wait(1.5 * (attempt + 1))
            continue
        if response.status_code not in {429, 500, 502, 503, 504}:
            break
        if attempt == 3:
            break
        threading.Event().wait(2.0 * (attempt + 1))
    if response is None:
        return {"status": "missing", "error": "no response"}
    if response.status_code != 200:
        return {"status": "missing", "error": f"HTTP {response.status_code}"}
    if not response.content:
        return {"status": "missing", "error": "empty response"}
    try:
        digest, perceptual_hash, width, height = image_meta_bytes(response.content)
    except Exception as error:
        return {"status": "missing", "error": f"not a decodable image: {error}"}
    if digest in KNOWN_PLACEHOLDER_SHA256:
        return {"status": "placeholder", "error": "known Football placeholder"}
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
    with sqlite3.connect(RUNTIME_DB) as conn:
        rows = conn.execute(
            """
            SELECT player_id, display_name, external_id, primary_pos, debut_year, final_year,
                   COALESCE(career_games, 0)
              FROM runtime_players
             WHERE scope = 'football'
             ORDER BY COALESCE(career_games, 0) DESC, player_id
            """
        ).fetchall()
    return [
        Player(pid, name, str(external_id) if external_id else None, pos, debut, final, int(games or 0))
        for pid, name, external_id, pos, debut, final, games in rows
    ]


def create_registry(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS football_headshots;
        CREATE TABLE football_headshots (
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
            final_year INTEGER,
            career_games INTEGER
        ) WITHOUT ROWID;

        CREATE INDEX idx_football_headshots_status ON football_headshots(status, career_games DESC, provider);
        """
    )


def save_candidate_image(player: Player, content: bytes, source_url: str | None, provider: str, note: str) -> Candidate:
    normalized = canonical_jpeg(content)
    digest, perceptual_hash, width, height = image_meta_bytes(normalized)
    if digest in KNOWN_PLACEHOLDER_SHA256:
        return Candidate(player.player_id, player.player_name, source_url, provider, "placeholder", review_note="known Football placeholder")
    target = STAGING_CACHE_DIR / safe_filename(player.player_id)
    target.write_bytes(normalized)
    return Candidate(
        player.player_id,
        player.player_name,
        source_url,
        provider,
        "verified",
        digest,
        perceptual_hash,
        width,
        height,
        str(target.relative_to(ROOT)).replace("\\", "/"),
        note,
    )


def candidate_from_file(player: Player, path: Path, provider: str, note: str) -> Candidate:
    content = path.read_bytes()
    try:
        digest, _perceptual_hash, width, height = image_meta_bytes(content)
    except Exception as error:
        return Candidate(player.player_id, player.player_name, None, provider, "missing", review_note=f"cached file failed validation: {error}")
    if digest in KNOWN_PLACEHOLDER_SHA256:
        return Candidate(player.player_id, player.player_name, None, provider, "placeholder", review_note="known Football placeholder")
    if width < MIN_IMAGE_SIZE or height < MIN_IMAGE_SIZE:
        return Candidate(player.player_id, player.player_name, None, provider, "missing", review_note=f"cached file too small: {width}x{height}")
    return save_candidate_image(player, content, None, provider, note)


def local_cache_candidates(players: list[Player]) -> dict[str, Candidate]:
    if not CANONICAL_CACHE_DIR.exists():
        return {}
    by_id = {player.player_id: player for player in players}
    results: dict[str, Candidate] = {}
    for index, player in enumerate(players, 1):
        path = CANONICAL_CACHE_DIR / old_cache_filename(player.player_id)
        if not path.exists():
            path = CANONICAL_CACHE_DIR / safe_filename(player.player_id)
        if not path.exists():
            continue
        candidate = candidate_from_file(player, path, "Canonical Local Cache", "Verified from previous local Football cache.")
        if candidate.status == "verified":
            results[player.player_id] = candidate
        if index % 1000 == 0:
            print(f"  local cache checked {index:,}/{len(players):,}", flush=True)
    return results


def runtime_url_candidates(players: list[Player], existing: dict[str, Candidate], workers: int) -> dict[str, Candidate]:
    with sqlite3.connect(RUNTIME_DB) as conn:
        rows = conn.execute(
            """
            SELECT h.player_id, COALESCE(h.source_url, h.fallback_url), h.provider, h.status
              FROM runtime_headshots h
              JOIN runtime_players p ON p.scope = h.scope AND p.player_id = h.player_id
             WHERE h.scope = 'football'
               AND COALESCE(h.source_url, h.fallback_url, '') <> ''
             ORDER BY CASE h.status WHEN 'verified' THEN 0 WHEN 'placeholder' THEN 1 ELSE 2 END,
                      COALESCE(p.career_games, 0) DESC
            """
        ).fetchall()
    by_id = {player.player_id: player for player in players}
    seen: set[tuple[str, str]] = set()
    jobs: list[tuple[Player, str, str, str]] = []
    for player_id, url, provider, status in rows:
        if existing.get(player_id, Candidate("", "", None, "", "")).status == "verified":
            continue
        if not url or (player_id, url) in seen:
            continue
        seen.add((player_id, url))
        player = by_id.get(player_id)
        if not player:
            continue
        if status != "verified" and player.career_games < MIN_PRIORITY_GAMES:
            continue
        jobs.append((player, url, provider or "runtime", "Verified from runtime Football headshot URL."))
    return fetch_candidate_jobs(jobs, workers, "runtime URLs", existing)


def csv_url_candidates(players: list[Player], existing: dict[str, Candidate], workers: int) -> dict[str, Candidate]:
    by_id = {player.player_id: player for player in players}
    jobs: list[tuple[Player, str, str, str]] = []
    for provider, path in CSV_SOURCES:
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                status = (row.get("status") or row.get("result_status") or "").lower()
                if status not in {"verified", "ok"}:
                    continue
                player_id = row.get("player_id") or ""
                player = by_id.get(player_id)
                if not player or existing.get(player_id, Candidate("", "", None, "", "")).status == "verified":
                    continue
                url = row.get("source_url") or ""
                if url:
                    jobs.append((player, url, row.get("provider") or provider, row.get("note") or "Verified Football source CSV."))
    return fetch_candidate_jobs(jobs, workers, "CSV URLs", existing)


def espn_catalog_candidates(players: list[Player], existing: dict[str, Candidate], workers: int) -> dict[str, Candidate]:
    if not ESPN_CATALOG_DIR.exists():
        return {}
    targets = {
        normalize_name(player.player_name): player
        for player in players
        if existing.get(player.player_id, Candidate("", "", None, "", "")).status != "verified"
        and player.career_games >= MIN_PRIORITY_GAMES
    }
    rows_by_name: dict[str, list[dict[str, str]]] = {}
    for path in sorted(ESPN_CATALOG_DIR.glob("page_*.csv")):
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                key = normalize_name(row.get("display_name"))
                if key in targets:
                    rows_by_name.setdefault(key, []).append(row)
    jobs: list[tuple[Player, str, str, str]] = []
    for key, rows in rows_by_name.items():
        player = targets.get(key)
        plausible = []
        for row in rows:
            espn_id = str(row.get("espn_id") or "").strip()
            debut = int(row.get("debut_year") or 0)
            if not espn_id:
                continue
            if player and player.debut_year and debut and abs(debut - player.debut_year) > 4:
                continue
            plausible.append(row)
        if player and len(plausible) == 1:
            espn_id = plausible[0]["espn_id"].strip()
            jobs.append((player, f"https://a.espncdn.com/i/headshots/nfl/players/full/{espn_id}.png", "ESPN NFL catalog", f"Matched local ESPN NFL catalog ID {espn_id}."))
    return fetch_candidate_jobs(jobs, workers, "ESPN catalog", existing)


def fetch_candidate_jobs(
    jobs: list[tuple[Player, str, str, str]],
    workers: int,
    label: str,
    existing: dict[str, Candidate],
) -> dict[str, Candidate]:
    results: dict[str, Candidate] = {}
    if not jobs:
        return results
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fetch_image, url): (player, url, provider, note) for player, url, provider, note in jobs}
        for index, future in enumerate(as_completed(futures), 1):
            player, url, provider, note = futures[future]
            if existing.get(player.player_id, Candidate("", "", None, "", "")).status == "verified":
                continue
            result = future.result()
            if result.get("status") == "ok" and isinstance(result.get("content"), bytes):
                candidate = save_candidate_image(player, result["content"], url, provider, note)
                if candidate.status == "verified":
                    results[player.player_id] = candidate
                    existing[player.player_id] = candidate
            if index % 250 == 0 or index == len(futures):
                print(f"  {label} checked {index:,}/{len(futures):,}", flush=True)
    return results


def insert_candidates(conn: sqlite3.Connection, players: list[Player], candidates: dict[str, Candidate]) -> None:
    rows = []
    for player in players:
        candidate = candidates.get(player.player_id) or Candidate(
            player.player_id,
            player.player_name,
            None,
            "none",
            "missing",
            review_note="No verified Football headshot candidate",
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
                player.career_games,
            )
        )
    conn.executemany(
        """
        INSERT INTO football_headshots (
            player_id, player_name, external_id, source_url, provider, status,
            content_sha256, perceptual_hash, width, height, local_path,
            review_note, debut_year, final_year, career_games
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def write_missing_reports() -> dict[str, int]:
    report_dir = ROOT / "raw" / "headshot_gap_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "football_missing_after_canonical_sync.csv": "",
        "football_missing_50plus_after_canonical_sync.csv": f"career_games >= {MIN_PRIORITY_GAMES}",
    }
    counts = {}
    with sqlite3.connect(REGISTRY_DB) as conn:
        for filename, condition in outputs.items():
            where = "status <> 'verified'"
            if condition:
                where += f" AND {condition}"
            rows = conn.execute(
                f"""
                SELECT player_id, player_name, external_id, debut_year, final_year,
                       career_games, status, provider, review_note
                  FROM football_headshots
                 WHERE {where}
                 ORDER BY career_games DESC, player_name, player_id
                """
            ).fetchall()
            with (report_dir / filename).open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["player_id", "player_name", "external_id", "debut_year", "final_year", "career_games", "status", "provider", "review_note"])
                writer.writerows(rows)
            counts[filename] = len(rows)
    return counts


def replace_canonical_folder() -> None:
    if LEGACY_CACHE_DIR.exists():
        shutil.rmtree(LEGACY_CACHE_DIR)
    if CANONICAL_CACHE_DIR.exists():
        CANONICAL_CACHE_DIR.rename(LEGACY_CACHE_DIR)
    STAGING_CACHE_DIR.rename(CANONICAL_CACHE_DIR)
    shutil.rmtree(LEGACY_CACHE_DIR)
    with sqlite3.connect(REGISTRY_DB) as conn:
        conn.execute(
            """
            UPDATE football_headshots
               SET local_path = replace(local_path, 'football_canonical_staging', 'football')
             WHERE local_path LIKE '%football_canonical_staging%'
            """
        )
        conn.commit()


def replace_current_staging() -> dict[str, int | float]:
    if not STAGING_CACHE_DIR.exists():
        raise SystemExit(f"staging cache does not exist: {STAGING_CACHE_DIR}")
    with sqlite3.connect(REGISTRY_DB) as conn:
        verified = conn.execute("SELECT COUNT(*) FROM football_headshots WHERE status = 'verified'").fetchone()[0]
        staging_paths = conn.execute(
            "SELECT COUNT(*) FROM football_headshots WHERE status = 'verified' AND local_path LIKE '%football_canonical_staging%'"
        ).fetchone()[0]
    staging_files = len([p for p in STAGING_CACHE_DIR.glob("*.*") if p.is_file()])
    if staging_files != verified or staging_paths != verified:
        raise SystemExit(
            f"refusing replace: staging files={staging_files:,}, registry verified={verified:,}, staging paths={staging_paths:,}"
        )
    replace_canonical_folder()
    return {
        "verified": int(verified),
        "cache_files": len([p for p in CANONICAL_CACHE_DIR.glob("*.*") if p.is_file()]),
        "cache_size_mb": round(sum(p.stat().st_size for p in CANONICAL_CACHE_DIR.glob("*.*") if p.is_file()) / 1024 / 1024, 3),
    }


def build_registry(workers: int = 16, limit: int = 0, replace: bool = False) -> dict[str, int | float]:
    players = load_players()
    if limit:
        players = players[:limit]
    REGISTRY_DB.parent.mkdir(parents=True, exist_ok=True)
    if STAGING_CACHE_DIR.exists():
        shutil.rmtree(STAGING_CACHE_DIR)
    STAGING_CACHE_DIR.mkdir(parents=True)
    with sqlite3.connect(REGISTRY_DB) as registry:
        create_registry(registry)
        candidates = local_cache_candidates(players)
        print(f"canonical/source cache verified {sum(1 for c in candidates.values() if c.status == 'verified'):,}/{len(players):,}", flush=True)
        candidates.update(csv_url_candidates(players, candidates, workers))
        print(f"after CSV URLs verified {sum(1 for c in candidates.values() if c.status == 'verified'):,}/{len(players):,}", flush=True)
        candidates.update(espn_catalog_candidates(players, candidates, workers))
        print(f"after ESPN catalog verified {sum(1 for c in candidates.values() if c.status == 'verified'):,}/{len(players):,}", flush=True)
        candidates.update(runtime_url_candidates(players, candidates, workers))
        print(f"after runtime URLs verified {sum(1 for c in candidates.values() if c.status == 'verified'):,}/{len(players):,}", flush=True)
        insert_candidates(registry, players, candidates)
        registry.commit()
        registry.execute("VACUUM")
        counts = dict(registry.execute("SELECT status, COUNT(*) FROM football_headshots GROUP BY status").fetchall())
        provider_counts = dict(
            registry.execute(
                "SELECT provider || ':' || status, COUNT(*) FROM football_headshots GROUP BY provider, status"
            ).fetchall()
        )
        priority_players = registry.execute(
            "SELECT COUNT(*) FROM football_headshots WHERE career_games >= ?",
            (MIN_PRIORITY_GAMES,),
        ).fetchone()[0]
        priority_verified = registry.execute(
            "SELECT COUNT(*) FROM football_headshots WHERE career_games >= ? AND status = 'verified'",
            (MIN_PRIORITY_GAMES,),
        ).fetchone()[0]
    missing_reports = write_missing_reports()
    verified = int(counts.get("verified", 0))
    if replace:
        replace_canonical_folder()
    return {
        "players": len(players),
        "verified": verified,
        "missing": int(counts.get("missing", 0)),
        "placeholder": int(counts.get("placeholder", 0)),
        "priority_players": int(priority_players),
        "priority_verified": int(priority_verified),
        "priority_missing": int(priority_players - priority_verified),
        "staging_cache_files": len([p for p in STAGING_CACHE_DIR.glob("*.*") if p.is_file()]) if STAGING_CACHE_DIR.exists() else 0,
        "cache_files": len([p for p in CANONICAL_CACHE_DIR.glob("*.*") if p.is_file()]) if CANONICAL_CACHE_DIR.exists() else 0,
        "staging_cache_size_mb": round(sum(p.stat().st_size for p in STAGING_CACHE_DIR.glob("*.*") if p.is_file()) / 1024 / 1024, 3) if STAGING_CACHE_DIR.exists() else 0,
        "cache_size_mb": round(sum(p.stat().st_size for p in CANONICAL_CACHE_DIR.glob("*.*") if p.is_file()) / 1024 / 1024, 3) if CANONICAL_CACHE_DIR.exists() else 0,
        "size_mb": round(REGISTRY_DB.stat().st_size / 1024 / 1024, 3),
        **{f"report_{key}": value for key, value in missing_reports.items()},
        **{f"provider_{key}": value for key, value in provider_counts.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--replace", action="store_true", help="Replace raw/player_headshots/football with the clean verified cache.")
    parser.add_argument("--replace-current-staging", action="store_true", help="Replace the old cache with the already-built staging cache after verifying registry/file counts.")
    args = parser.parse_args()
    stats = replace_current_staging() if args.replace_current_staging else build_registry(workers=args.workers, limit=args.limit, replace=args.replace)
    print(f"local registry: {REGISTRY_DB}")
    for key, value in sorted(stats.items()):
        print(f"{key}={value:,}" if isinstance(value, int) else f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
