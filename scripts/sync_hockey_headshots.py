#!/usr/bin/env python3
"""Build the canonical local Hockey headshot registry/cache.

Every playable Hockey player should end with one verified local image under
raw/player_headshots/hockey plus one registry row. This is offline/local-first
and does not upload anything to Supabase.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
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
REGISTRY_DB = ROOT / "raw" / "headshot_registry" / "hockey_headshots.sqlite"
CANONICAL_CACHE_DIR = ROOT / "raw" / "player_headshots" / "hockey"
STAGING_CACHE_DIR = ROOT / "raw" / "player_headshots" / "hockey_canonical_staging"
LEGACY_CACHE_DIR = ROOT / "raw" / "player_headshots" / "hockey_legacy_unverified"
FHM_DIRS = [
    ROOT / "raw" / "fhm" / "historical_matched_nhl_headshots",
    ROOT / "raw" / "fhm" / "matched_nhl_headshots",
]
NHL_ROSTER_DIR = ROOT / "raw" / "nhl"
ESPN_CATALOG_DIR = ROOT / "raw" / "espn_nhl_athlete_pages"
CROPPED_REVIEW = ROOT / "raw" / "cropped_headshots_review.csv"
CROPPED_DIR = ROOT / "raw" / "cropped_headshots" / "hockey"
CSV_SOURCES = [
    ("Manual verified Hockey source", ROOT / "raw" / "hockey_manual_headshots.csv"),
    ("Web image search", ROOT / "raw" / "nhl_web_image_headshots.csv"),
    ("Hockey-Reference", ROOT / "raw" / "hockey_reference_headshots.csv"),
    ("Wikimedia Commons", ROOT / "raw" / "hockey_wikimedia_headshots.csv"),
]
USER_AGENT = "TeamMateTag hockey headshot registry/1.0 (local verification)"
MIN_IMAGE_SIZE = 80
KNOWN_PLACEHOLDER_SHA256 = {
    # NHL missing-image graphic, repeated in the old raw cache.
    "ae3f3b1a9ba14a92c24dfe676a09505bd17436d56aee1d13ec580696ee141151",
}
EXTERNAL_NHL_ID_OVERRIDES = {
    # Runtime source only had HockeyDB IDs for these older rows.
    "hdb:almqvad01": "8475332",
}
FIRST_NAME_ALIASES = {
    ("anthony", "tony"),
}
TEAM_ABBREV_OVERRIDES = {
    "hdb:WAS": "WSH",
    "WAS": "WSH",
    "hdb:FLO": "FLA",
    "FLO": "FLA",
    "hdb:CAL": "CGY",
    "CAL": "CGY",
    "hdb:CBS": "CBJ",
    "CBS": "CBJ",
    "hdb:NAS": "NSH",
    "NAS": "NSH",
    "hdb:PHO": "ARI",
    "PHO": "ARI",
    "hdb:VEG": "VGK",
    "VEG": "VGK",
    "hdb:AND": "ANA",
    "AND": "ANA",
    "hdb:ATL": "ATL",
}

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


def safe_filename(player_id: str, suffix: str = ".jpg") -> str:
    return player_id.replace(":", "__") + suffix


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


def hamming(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


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
        alpha = image.getchannel("A") if image.mode == "RGBA" else image.getchannel(1)
        background.paste(image.convert("RGBA"), mask=alpha)
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
        response = session().get(url, timeout=20)
    except requests.RequestException as error:
        return {"status": "missing", "error": str(error)[:300]}
    if response.status_code != 200:
        return {"status": "missing", "error": f"HTTP {response.status_code}"}
    if not response.content:
        return {"status": "missing", "error": "empty response"}
    return inspect_content(response.content)


def inspect_content(content: bytes) -> dict[str, object]:
    try:
        digest, perceptual_hash, width, height = image_meta_bytes(content)
    except Exception as error:
        return {"status": "missing", "error": f"not a decodable image: {error}"}
    if width < MIN_IMAGE_SIZE or height < MIN_IMAGE_SIZE:
        return {"status": "missing", "error": f"image too small: {width}x{height}"}
    if digest in KNOWN_PLACEHOLDER_SHA256:
        return {
            "status": "placeholder",
            "error": "NHL missing-image graphic",
            "content_sha256": digest,
            "perceptual_hash": perceptual_hash,
            "width": width,
            "height": height,
        }
    return {
        "status": "ok",
        "content": content,
        "content_sha256": digest,
        "perceptual_hash": perceptual_hash,
        "width": width,
        "height": height,
    }


def load_players() -> list[Player]:
    with sqlite3.connect(RUNTIME_DB) as conn:
        rows = conn.execute(
            """
            SELECT player_id, display_name, external_id, debut_year, final_year
              FROM runtime_players
             WHERE scope = 'hockey'
             ORDER BY player_id
            """
        ).fetchall()
    return [Player(pid, name, str(external_id) if external_id else None, debut, final) for pid, name, external_id, debut, final in rows]


def create_registry(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS hockey_headshots;
        CREATE TABLE hockey_headshots (
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

        CREATE INDEX idx_hockey_headshots_status ON hockey_headshots(status, provider);
        """
    )


def save_candidate_image(player: Player, content: bytes, source_url: str | None, provider: str, note: str | None = None) -> Candidate:
    output = canonical_jpeg(content)
    result = inspect_content(output)
    if result.get("status") != "ok":
        return Candidate(player.player_id, player.player_name, source_url, provider, str(result.get("status") or "missing"), review_note=str(result.get("error") or "failed validation"))
    path = STAGING_CACHE_DIR / safe_filename(player.player_id)
    path.write_bytes(output)
    local_path = str(path.relative_to(ROOT)).replace("\\", "/")
    return Candidate(
        player.player_id,
        player.player_name,
        f"/local-headshots/hockey/{path.name}",
        provider,
        "verified",
        str(result["content_sha256"]),
        str(result["perceptual_hash"]),
        int(result["width"]),
        int(result["height"]),
        local_path,
        note,
    )


def candidate_from_file(player: Player, path: Path, provider: str, note: str | None = None) -> Candidate:
    try:
        content = path.read_bytes()
    except OSError as error:
        return Candidate(player.player_id, player.player_name, None, provider, "missing", review_note=str(error))
    result = inspect_content(content)
    if result.get("status") != "ok":
        return Candidate(player.player_id, player.player_name, None, provider, str(result.get("status") or "missing"), review_note=str(result.get("error") or "failed validation"))
    return save_candidate_image(player, content, None, provider, note)


def local_cache_candidates(players: list[Player]) -> dict[str, Candidate]:
    by_id = {player.player_id: player for player in players}
    by_external = {player.external_id: player for player in players if player.external_id}
    result: dict[str, Candidate] = {}
    if not CANONICAL_CACHE_DIR.exists():
        return result
    for image_path in sorted(CANONICAL_CACHE_DIR.glob("*.*")):
        if image_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        stem = image_path.stem
        player = by_id.get(stem.replace("__", ":"))
        if not player and stem.startswith("nhl_"):
            player = by_id.get("nhl:" + stem.removeprefix("nhl_")) or by_external.get(stem.removeprefix("nhl_"))
        if not player:
            continue
        candidate = candidate_from_file(player, image_path, "Canonical Local Cache")
        if candidate.status == "verified":
            result[player.player_id] = candidate
        else:
            result.setdefault(player.player_id, candidate)
    return result


def reviewed_crop_candidates(players: list[Player], existing: dict[str, Candidate]) -> dict[str, Candidate]:
    if not CROPPED_REVIEW.exists() or not CROPPED_DIR.exists():
        return {}
    by_id = {player.player_id: player for player in players}
    results: dict[str, Candidate] = {}
    with CROPPED_REVIEW.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if (row.get("sport") or row.get("sport_id")) != "hockey":
                continue
            player_id = row.get("player_id") or ""
            player = by_id.get(player_id)
            if not player or existing.get(player_id, Candidate("", "", None, "", "")).status == "verified":
                continue
            path_value = row.get("local_path") or ""
            image_path = Path(path_value) if path_value else CROPPED_DIR / safe_filename(player_id).replace("__", "_")
            if not image_path.is_absolute():
                image_path = ROOT / image_path
            if not image_path.exists():
                continue
            candidate = candidate_from_file(player, image_path, row.get("provider") or "Reviewed crop", row.get("note") or "Reviewed cropped Hockey headshot.")
            if candidate.status == "verified":
                results[player_id] = candidate
    return results


def fhm_candidates(players: list[Player], existing: dict[str, Candidate]) -> dict[str, Candidate]:
    by_id = {player.player_id: player for player in players}
    results: dict[str, Candidate] = {}
    for folder in FHM_DIRS:
        if not folder.exists():
            continue
        provider = "FHM Historical Photos Megapack" if "historical" in folder.name else "FHM Facepack"
        for image_path in sorted(folder.glob("*.*")):
            stem = image_path.stem.replace("_", ":")
            player = by_id.get(stem)
            if not player or existing.get(player.player_id, Candidate("", "", None, "", "")).status == "verified":
                continue
            candidate = candidate_from_file(player, image_path, provider, "Local FHM source matched by previous identity pass.")
            if candidate.status == "verified":
                results[player.player_id] = candidate
                existing[player.player_id] = candidate
    return results


def csv_url_candidates(players: list[Player], existing: dict[str, Candidate], workers: int) -> dict[str, Candidate]:
    by_id = {player.player_id: player for player in players}
    jobs: list[tuple[Player, str, str, str]] = []
    for provider, path in CSV_SOURCES:
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                if (row.get("status") or "").lower() != "verified":
                    continue
                player_id = row.get("player_id") or ""
                player = by_id.get(player_id)
                if not player or existing.get(player_id, Candidate("", "", None, "", "")).status == "verified":
                    continue
                url = row.get("source_url") or ""
                if url:
                    jobs.append((player, url, row.get("provider") or provider, row.get("note") or "Verified Hockey source CSV."))
    results: dict[str, Candidate] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fetch_image, url): (player, url, provider, note) for player, url, provider, note in jobs}
        for index, future in enumerate(as_completed(futures), 1):
            player, url, provider, note = futures[future]
            result = future.result()
            if result.get("status") == "ok" and isinstance(result.get("content"), bytes):
                candidate = save_candidate_image(player, result["content"], url, provider, note)
                if candidate.status == "verified" and player.player_id not in results:
                    results[player.player_id] = candidate
                    existing[player.player_id] = candidate
            if index % 50 == 0 or index == len(futures):
                print(f"  CSV URLs checked {index:,}/{len(futures):,}", flush=True)
    return results


def roster_people(payload: object) -> list[dict]:
    people: list[dict] = []
    if isinstance(payload, dict):
        for key in ("forwards", "defensemen", "goalies"):
            values = payload.get(key)
            if isinstance(values, list):
                people.extend(item for item in values if isinstance(item, dict))
    return people


def localized_name(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("default") or "")
    return str(value or "")


def split_person_name(value: str) -> tuple[str, str]:
    parts = [part for part in re.split(r"\s+", value.strip()) if part]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def roster_name_match(runtime_name: str, roster_name: str) -> bool:
    if normalize_name(runtime_name) == normalize_name(roster_name):
        return True
    runtime_first, runtime_last = split_person_name(runtime_name)
    roster_first, roster_last = split_person_name(roster_name)
    if not runtime_first or not roster_first or not runtime_last or not roster_last:
        return False
    if normalize_name(runtime_last) != normalize_name(roster_last):
        return False
    runtime_first_norm = normalize_name(runtime_first)
    roster_first_norm = normalize_name(roster_first)
    if (runtime_first_norm, roster_first_norm) in FIRST_NAME_ALIASES or (roster_first_norm, runtime_first_norm) in FIRST_NAME_ALIASES:
        return True
    runtime_initial = runtime_first_norm[:1]
    roster_initial = roster_first_norm[:1]
    return bool(runtime_initial and runtime_initial == roster_initial)


def nhl_roster_candidates(players: list[Player], existing: dict[str, Candidate], workers: int) -> dict[str, Candidate]:
    if not NHL_ROSTER_DIR.exists():
        return {}
    by_id = {player.player_id: player for player in players}
    with sqlite3.connect(RUNTIME_DB) as conn:
        player_team_seasons = {
            player.player_id: [
                (TEAM_ABBREV_OVERRIDES.get(team_id, team_id.replace("hdb:", "")), int(season))
                for team_id, season in conn.execute(
                    """
                    SELECT team_id, season
                      FROM runtime_player_team_seasons
                     WHERE scope = 'hockey'
                       AND player_id = ?
                    """,
                    (player.player_id,),
                ).fetchall()
            ]
            for player in players
        }
    roster_index: dict[tuple[str, int], list[dict]] = {}
    for path in NHL_ROSTER_DIR.glob("*/*.json"):
        try:
            team = path.parent.name
            season = int(path.stem[:4])
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError, json.JSONDecodeError):
            continue
        roster_index[(team, season)] = roster_people(payload)
    jobs: list[tuple[Player, str, str]] = []
    for player in players:
        if existing.get(player.player_id, Candidate("", "", None, "", "")).status == "verified":
            continue
        matches = []
        target_name = normalize_name(player.player_name)
        for team, season in player_team_seasons.get(player.player_id, []):
            for person in roster_index.get((team, season), []):
                full_name = f"{localized_name(person.get('firstName'))} {localized_name(person.get('lastName'))}".strip()
                if normalize_name(full_name) != target_name and not roster_name_match(player.player_name, full_name):
                    continue
                url = person.get("headshot")
                nhl_id = str(person.get("id") or "")
                if url:
                    matches.append((season, team, nhl_id, str(url)))
        by_nhl_id: dict[str, list[tuple[int, str, str]]] = {}
        for season, team, nhl_id, url in matches:
            by_nhl_id.setdefault(nhl_id, []).append((season, team, url))
        if len(by_nhl_id) != 1:
            continue
        nhl_id, team_urls = next(iter(by_nhl_id.items()))
        season, team, url = sorted(team_urls, reverse=True)[0]
        jobs.append((player, url, f"Matched local NHL roster {team} {season} to NHL ID {nhl_id}."))
    results: dict[str, Candidate] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fetch_image, url): (player, url, note) for player, url, note in jobs}
        for index, future in enumerate(as_completed(futures), 1):
            player, url, note = futures[future]
            result = future.result()
            if result.get("status") == "ok" and isinstance(result.get("content"), bytes):
                candidate = save_candidate_image(player, result["content"], url, "NHL roster cache", note)
                if candidate.status == "verified":
                    results[player.player_id] = candidate
                    existing[player.player_id] = candidate
            if index % 50 == 0 or index == len(futures):
                print(f"  NHL roster cache checked {index:,}/{len(futures):,}", flush=True)
    return results


def espn_catalog_candidates(players: list[Player], existing: dict[str, Candidate], workers: int) -> dict[str, Candidate]:
    if not ESPN_CATALOG_DIR.exists():
        return {}
    targets = {
        normalize_name(player.player_name): player
        for player in players
        if existing.get(player.player_id, Candidate("", "", None, "", "")).status != "verified"
    }
    if not targets:
        return {}
    rows_by_name: dict[str, list[dict[str, str]]] = {}
    for path in sorted(ESPN_CATALOG_DIR.glob("page_*.csv")):
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    key = normalize_name(row.get("display_name"))
                    display_name = row.get("display_name") or ""
                    if key in targets:
                        rows_by_name.setdefault(key, []).append(row)
                    else:
                        for target_key, player in targets.items():
                            if roster_name_match(player.player_name, display_name):
                                rows_by_name.setdefault(target_key, []).append(row)
        except OSError:
            continue
    jobs: list[tuple[Player, str, str]] = []
    for key, rows in rows_by_name.items():
        player = targets.get(key)
        if not player:
            continue
        # Avoid duplicate-name traps unless exactly one local ESPN identity is a
        # plausible age/debut match for the runtime player.
        plausible = []
        for row in rows:
            espn_id = str(row.get("espn_id") or "").strip()
            birth_year = int(str(row.get("birth_date") or "0")[:4] or 0)
            debut_year = int(row.get("debut_year") or 0)
            if not espn_id:
                continue
            if player.debut_year and debut_year and abs(debut_year - player.debut_year) > 4:
                continue
            if player.debut_year and birth_year and not (player.debut_year - 45 <= birth_year <= player.debut_year - 16):
                continue
            plausible.append(row)
        if len(plausible) != 1:
            continue
        espn_id = plausible[0]["espn_id"].strip()
        url = f"https://a.espncdn.com/i/headshots/nhl/players/full/{espn_id}.png"
        jobs.append((player, url, f"Matched local ESPN NHL catalog ID {espn_id}."))
    results: dict[str, Candidate] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fetch_image, url): (player, url, note) for player, url, note in jobs}
        for index, future in enumerate(as_completed(futures), 1):
            player, url, note = futures[future]
            result = future.result()
            if result.get("status") == "ok" and isinstance(result.get("content"), bytes):
                candidate = save_candidate_image(player, result["content"], url, "ESPN NHL catalog", note)
                if candidate.status == "verified":
                    results[player.player_id] = candidate
                    existing[player.player_id] = candidate
            if index % 50 == 0 or index == len(futures):
                print(f"  ESPN catalog checked {index:,}/{len(futures):,}", flush=True)
    return results


def compatible_position(left: str | None, right: str | None) -> bool:
    left = {"L": "W", "R": "W"}.get(left or "", left or "")
    right = {"L": "W", "R": "W"}.get(right or "", right or "")
    return not left or not right or left == right or {left, right} <= {"C", "W"}


def alias_candidates(players: list[Player], existing: dict[str, Candidate]) -> dict[str, Candidate]:
    missing = [player for player in players if existing.get(player.player_id, Candidate("", "", None, "", "")).status != "verified"]
    if not missing:
        return {}
    by_id = {player.player_id: player for player in players}
    with sqlite3.connect(RUNTIME_DB) as conn:
        rows = conn.execute(
            """
            SELECT p.player_id, p.display_name, p.primary_pos, p.debut_year, p.final_year,
                   group_concat(a.team_id || ':' || a.season, ',') AS teams
              FROM runtime_players p
              JOIN runtime_player_team_seasons a
                ON a.scope = p.scope
               AND a.player_id = p.player_id
             WHERE p.scope = 'hockey'
             GROUP BY p.player_id, p.display_name, p.primary_pos, p.debut_year, p.final_year
            """
        ).fetchall()
    signatures = {}
    by_name: dict[str, list[dict]] = {}
    for player_id, name, pos, debut, final, teams in rows:
        team_set = set((teams or "").split(",")) if teams else set()
        item = {
            "player_id": player_id,
            "name": name,
            "pos": pos,
            "debut": int(debut or 0),
            "final": int(final or 0),
            "teams": team_set,
        }
        signatures[player_id] = item
        by_name.setdefault(normalize_name(name), []).append(item)
    results: dict[str, Candidate] = {}
    for player in missing:
        sig = signatures.get(player.player_id)
        if not sig:
            continue
        verified = []
        for other in by_name.get(normalize_name(player.player_name), []):
            if other["player_id"] == player.player_id:
                continue
            source = existing.get(other["player_id"])
            if not source or source.status != "verified" or not source.local_path:
                continue
            if not compatible_position(sig["pos"], other["pos"]):
                continue
            overlap = sig["teams"] & other["teams"]
            same_slice = (
                sig["debut"] >= other["debut"] - 1
                and sig["debut"] <= other["final"] + 1
                and sig["final"] <= other["final"] + 1
            )
            if overlap or same_slice:
                verified.append((len(overlap), other, source))
        if len(verified) != 1:
            continue
        _overlap_count, other, source = verified[0]
        source_path = ROOT / source.local_path
        candidate = candidate_from_file(
            player,
            source_path,
            source.provider,
            f"Shared verified image with duplicate Hockey identity {other['player_id']}.",
        )
        if candidate.status == "verified":
            results[player.player_id] = candidate
            existing[player.player_id] = candidate
    return results


def official_nhl_id(player: Player) -> str | None:
    return EXTERNAL_NHL_ID_OVERRIDES.get(player.player_id) or player.external_id


def official_nhl_candidates(players: list[Player], existing: dict[str, Candidate], workers: int) -> dict[str, Candidate]:
    targets = [player for player in players if official_nhl_id(player) and existing.get(player.player_id, Candidate("", "", None, "", "")).status != "verified"]
    with sqlite3.connect(RUNTIME_DB) as conn:
        team_seasons = {
            player.player_id: [
                (TEAM_ABBREV_OVERRIDES.get(team_id, team_id.replace("hdb:", "")), int(season))
                for team_id, season in conn.execute(
                    """
                    SELECT team_id, season
                      FROM runtime_player_team_seasons
                     WHERE scope = 'hockey'
                       AND player_id = ?
                     ORDER BY season DESC, team_id
                    """,
                    (player.player_id,),
                ).fetchall()
            ]
            for player in targets
        }

    def urls(player: Player) -> list[str]:
        external_id = official_nhl_id(player) or ""
        candidates = [
            f"https://assets.nhle.com/mugs/nhl/latest/{external_id}.png",
        ]
        seen = set(candidates)
        for team_id, season in team_seasons.get(player.player_id, []):
            if not team_id:
                continue
            url = f"https://assets.nhle.com/mugs/nhl/{season}{season + 1}/{team_id}/{external_id}.png"
            if url not in seen:
                seen.add(url)
                candidates.append(url)
        candidates.append(f"https://api-web.nhle.com/v1/player/{external_id}/landing")
        return candidates

    def inspect_player(player: Player) -> Candidate:
        last_note = "no candidate URL"
        for url in urls(player):
            if url.endswith("/landing"):
                try:
                    response = session().get(url, timeout=20)
                    if response.status_code != 200:
                        last_note = f"landing HTTP {response.status_code}"
                        continue
                    headshot = (response.json() or {}).get("headshot")
                    if not headshot:
                        last_note = "landing has no headshot"
                        continue
                    url = headshot
                except (requests.RequestException, json.JSONDecodeError, ValueError) as error:
                    last_note = str(error)[:300]
                    continue
            result = fetch_image(url)
            last_note = str(result.get("error") or result.get("status"))
            if result.get("status") == "ok" and isinstance(result.get("content"), bytes):
                return save_candidate_image(player, result["content"], url, "NHL CDN", "Verified NHL CDN headshot.")
        return Candidate(player.player_id, player.player_name, None, "NHL CDN", "missing", review_note=last_note)

    results: dict[str, Candidate] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(inspect_player, player): player for player in targets}
        for index, future in enumerate(as_completed(futures), 1):
            candidate = future.result()
            results[candidate.player_id] = candidate
            if candidate.status == "verified":
                existing[candidate.player_id] = candidate
            if index % 100 == 0 or index == len(futures):
                print(f"  NHL CDN checked {index:,}/{len(futures):,}", flush=True)
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
            review_note="No verified Hockey headshot candidate",
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
        INSERT INTO hockey_headshots (
            player_id, player_name, external_id, source_url, provider, status,
            content_sha256, perceptual_hash, width, height, local_path,
            review_note, debut_year, final_year
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


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
            UPDATE hockey_headshots
               SET local_path = replace(local_path, 'hockey_canonical_staging', 'hockey')
             WHERE local_path LIKE '%hockey_canonical_staging%'
            """
        )
        conn.commit()


def write_missing_report() -> int:
    report = ROOT / "raw" / "headshot_gap_reports" / "hockey_missing_after_canonical_sync.csv"
    report.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(REGISTRY_DB) as conn, report.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["player_id", "player_name", "external_id", "debut_year", "final_year", "status", "provider", "review_note"])
        rows = conn.execute(
            """
            SELECT player_id, player_name, external_id, debut_year, final_year, status, provider, review_note
              FROM hockey_headshots
             WHERE status <> 'verified'
             ORDER BY player_name, player_id
            """
        ).fetchall()
        writer.writerows(rows)
    return len(rows)


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
        candidates.update(reviewed_crop_candidates(players, candidates))
        print(f"after reviewed crops verified {sum(1 for c in candidates.values() if c.status == 'verified'):,}/{len(players):,}", flush=True)
        candidates.update(fhm_candidates(players, candidates))
        print(f"after FHM verified {sum(1 for c in candidates.values() if c.status == 'verified'):,}/{len(players):,}", flush=True)
        candidates.update(csv_url_candidates(players, candidates, workers))
        print(f"after CSV URLs verified {sum(1 for c in candidates.values() if c.status == 'verified'):,}/{len(players):,}", flush=True)
        candidates.update(nhl_roster_candidates(players, candidates, workers))
        print(f"after NHL roster cache verified {sum(1 for c in candidates.values() if c.status == 'verified'):,}/{len(players):,}", flush=True)
        candidates.update(espn_catalog_candidates(players, candidates, workers))
        print(f"after ESPN catalog verified {sum(1 for c in candidates.values() if c.status == 'verified'):,}/{len(players):,}", flush=True)
        candidates.update(alias_candidates(players, candidates))
        print(f"after alias fill verified {sum(1 for c in candidates.values() if c.status == 'verified'):,}/{len(players):,}", flush=True)
        candidates.update(official_nhl_candidates(players, candidates, workers))
        print(f"after NHL CDN verified {sum(1 for c in candidates.values() if c.status == 'verified'):,}/{len(players):,}", flush=True)
        insert_candidates(registry, players, candidates)
        registry.commit()
        registry.execute("VACUUM")
        counts = dict(registry.execute("SELECT status, COUNT(*) FROM hockey_headshots GROUP BY status").fetchall())
        provider_counts = dict(
            registry.execute(
                "SELECT provider || ':' || status, COUNT(*) FROM hockey_headshots GROUP BY provider, status"
            ).fetchall()
        )
    missing = write_missing_report()
    verified = int(counts.get("verified", 0))
    if replace and missing == 0 and verified == len(players):
        replace_canonical_folder()
    return {
        "players": len(players),
        "verified": verified,
        "placeholder": int(counts.get("placeholder", 0)),
        "missing": missing,
        "staging_cache_files": len([p for p in STAGING_CACHE_DIR.glob("*.*") if p.is_file()]) if STAGING_CACHE_DIR.exists() else 0,
        "cache_files": len([p for p in CANONICAL_CACHE_DIR.glob("*.*") if p.is_file()]) if CANONICAL_CACHE_DIR.exists() else 0,
        "staging_cache_size_mb": round(sum(p.stat().st_size for p in STAGING_CACHE_DIR.glob("*.*") if p.is_file()) / 1024 / 1024, 3) if STAGING_CACHE_DIR.exists() else 0,
        "cache_size_mb": round(sum(p.stat().st_size for p in CANONICAL_CACHE_DIR.glob("*.*") if p.is_file()) / 1024 / 1024, 3) if CANONICAL_CACHE_DIR.exists() else 0,
        "size_mb": round(REGISTRY_DB.stat().st_size / 1024 / 1024, 3),
        **{f"provider_{key}": value for key, value in provider_counts.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--replace", action="store_true", help="Replace raw/player_headshots/hockey only if every runtime player is verified.")
    args = parser.parse_args()
    stats = build_registry(workers=args.workers, limit=args.limit, replace=args.replace)
    print(f"local registry: {REGISTRY_DB}")
    for key, value in sorted(stats.items()):
        print(f"{key}={value:,}" if isinstance(value, int) else f"{key}={value}")
    return 0 if stats["missing"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
