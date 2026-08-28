#!/usr/bin/env python3
"""Build NFL 2000-2012 teammate proof input from official Game Books.

Pre-2013 public snap-count data is sparse, but official NFL Game Books list
game participants. For TeamMateTag's Football rule, Lineups plus Substitutions
are enough: they prove the player appeared in the game, while Did Not Play and
Not Active are excluded.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sqlite3
import sys
import time
import unicodedata
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import fitz


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "raw" / "nfl_game_teammates" / "nfl_gamebook_teammates.sqlite"
CACHE = ROOT / "raw" / "nfl" / "gamebooks"
SCHEDULE_CACHE = ROOT / "raw" / "nfl" / "games.csv"
SCHEDULE_URL = "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"
SOURCE_NAME = "official_nfl_gamebook_participation"
USER_AGENT = "TeamMateTag football data audit/1.0"

TEAM_SLUG = {
    "ARI": "cardinals",
    "ARZ": "cardinals",
    "ATL": "falcons",
    "BAL": "ravens",
    "BUF": "bills",
    "CAR": "panthers",
    "CHI": "bears",
    "CIN": "bengals",
    "CLE": "browns",
    "CLV": "browns",
    "DAL": "cowboys",
    "DEN": "broncos",
    "DET": "lions",
    "GB": "packers",
    "GNB": "packers",
    "HOU": "texans",
    "IND": "colts",
    "JAX": "jaguars",
    "JAC": "jaguars",
    "KC": "chiefs",
    "LA": "rams",
    "LAR": "rams",
    "STL": "rams",
    "SD": "chargers",
    "LAC": "chargers",
    "LV": "raiders",
    "OAK": "raiders",
    "MIA": "dolphins",
    "MIN": "vikings",
    "NE": "patriots",
    "NO": "saints",
    "NYG": "giants",
    "NYJ": "jets",
    "PHI": "eagles",
    "PIT": "steelers",
    "SEA": "seahawks",
    "SF": "49ers",
    "TB": "buccaneers",
    "TEN": "titans",
    "WAS": "redskins",
}

TEAM_FULL_NAME = {
    "ARI": "Arizona Cardinals",
    "ARZ": "Arizona Cardinals",
    "ATL": "Atlanta Falcons",
    "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills",
    "CAR": "Carolina Panthers",
    "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals",
    "CLE": "Cleveland Browns",
    "CLV": "Cleveland Browns",
    "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos",
    "DET": "Detroit Lions",
    "GB": "Green Bay Packers",
    "GNB": "Green Bay Packers",
    "HOU": "Houston Texans",
    "IND": "Indianapolis Colts",
    "JAX": "Jacksonville Jaguars",
    "JAC": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs",
    "LA": "Los Angeles Rams",
    "LAR": "Los Angeles Rams",
    "STL": "St. Louis Rams",
    "SD": "San Diego Chargers",
    "LAC": "Los Angeles Chargers",
    "LV": "Las Vegas Raiders",
    "OAK": "Oakland Raiders",
    "MIA": "Miami Dolphins",
    "MIN": "Minnesota Vikings",
    "NE": "New England Patriots",
    "NO": "New Orleans Saints",
    "NYG": "New York Giants",
    "NYJ": "New York Jets",
    "PHI": "Philadelphia Eagles",
    "PIT": "Pittsburgh Steelers",
    "SEA": "Seattle Seahawks",
    "SF": "San Francisco 49ers",
    "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans",
    "WAS": "Washington Redskins",
}

ROSTER_TEAM_ALIASES = {
    "ARI": ("ARI", "ARZ"),
    "BAL": ("BAL", "BLT"),
    "CLE": ("CLE", "CLV"),
    "HOU": ("HOU", "HST"),
    "STL": ("STL", "SL"),
}

ENTRY_POS = {
    "QB", "RB", "FB", "WR", "TE", "LT", "LG", "C", "RG", "RT", "G", "T", "C/G", "G/T", "OL",
    "K", "PK", "P", "LS", "KR", "PR",
    "LE", "RE", "LDE", "RDE", "DE", "LDT", "RDT", "DT", "NT", "DL",
    "LB", "ILB", "MLB", "OLB", "SLB", "WLB", "BLB", "LOLB", "ROLB", "LLB", "RLB",
    "CB", "LCB", "RCB", "DB", "S", "SS", "FS",
}

PLAYER_REGISTRY = ROOT / "raw" / "nfl" / "players.csv"
MANUAL_CORRECTIONS = ROOT / "scripts" / "data" / "nfl_gamebook_manual_corrections.csv"
PDF_OVERRIDES = ROOT / "scripts" / "data" / "nfl_gamebook_pdf_overrides.csv"

SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS nfl_gamebook_games (
    game_id TEXT PRIMARY KEY,
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    gameday TEXT NOT NULL,
    away_team TEXT NOT NULL,
    home_team TEXT NOT NULL,
    nfl_url TEXT NOT NULL,
    pdf_url TEXT,
    status TEXT NOT NULL,
    message TEXT,
    parsed_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS nfl_player_game_snap_appearances (
    game_id TEXT NOT NULL,
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    team_id TEXT NOT NULL,
    player_id TEXT NOT NULL,
    pfr_player_id TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL,
    position TEXT,
    offense_snaps INTEGER NOT NULL DEFAULT 0,
    defense_snaps INTEGER NOT NULL DEFAULT 0,
    special_teams_snaps INTEGER NOT NULL DEFAULT 0,
    total_snaps INTEGER NOT NULL DEFAULT 1,
    source_section TEXT NOT NULL DEFAULT 'gamebook',
    PRIMARY KEY (game_id, player_id, team_id)
);
CREATE INDEX IF NOT EXISTS idx_nfl_gamebook_player
    ON nfl_player_game_snap_appearances(player_id, season, team_id);
CREATE INDEX IF NOT EXISTS idx_nfl_gamebook_game_team
    ON nfl_player_game_snap_appearances(game_id, team_id, player_id);
CREATE TABLE IF NOT EXISTS nfl_snap_players (
    player_id TEXT PRIMARY KEY,
    pfr_player_id TEXT NOT NULL DEFAULT '',
    gsis_id TEXT,
    display_name TEXT NOT NULL,
    first_name TEXT,
    last_name TEXT,
    birth_year INTEGER,
    primary_pos TEXT,
    headshot_url TEXT
);
CREATE TABLE IF NOT EXISTS nfl_snap_unmapped_players (
    game_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    jersey_number TEXT NOT NULL,
    gamebook_name TEXT NOT NULL,
    position TEXT,
    reason TEXT NOT NULL,
    PRIMARY KEY (game_id, team_id, jersey_number, gamebook_name, position)
);
CREATE TABLE IF NOT EXISTS nfl_snap_build_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class ScheduleGame:
    game_id: str
    season: int
    week: int
    gameday: str
    away_team: str
    home_team: str
    gsis: str


@dataclass(frozen=True)
class ParsedEntry:
    team_id: str
    position: str
    jersey: str
    gamebook_name: str
    section: str


@dataclass(frozen=True)
class ResolvedEntry:
    entry: ParsedEntry
    player_id: str
    roster_row: dict[str, str]
    warning: str | None


@dataclass(frozen=True)
class UnresolvedEntry:
    entry: ParsedEntry
    reason: str


@dataclass(frozen=True)
class GameResult:
    game: ScheduleGame
    pdf_url: str | None
    status: str
    message: str | None
    entries: int
    resolved: list[ResolvedEntry]
    unresolved: list[UnresolvedEntry]


class PdfGameMismatch(RuntimeError):
    pass


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", value.lower())


def valid_position(pos: str) -> bool:
    parts = re.split(r"[/\-]", (pos or "").strip())
    return bool(parts) and all(part in ENTRY_POS for part in parts)


def download(url: str, destination: Path, *, binary: bool = False) -> bytes | str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        data = destination.read_bytes()
        return data if binary else data.decode("utf-8", errors="replace")
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=90) as response:
        data = response.read()
    destination.write_bytes(data)
    return data if binary else data.decode("utf-8", errors="replace")


def pdf_matches_game(pdf_path: Path, game: ScheduleGame) -> bool:
    doc = fitz.open(pdf_path)
    text = normalize(doc[0].get_text()[:5000]) if len(doc) else ""
    away = normalize(TEAM_FULL_NAME.get(game.away_team, game.away_team))
    home = normalize(TEAM_FULL_NAME.get(game.home_team, game.home_team))
    return bool(away and home and away in text and home in text)


def download_game_pdf(pdf_url: str, pdf_path: Path, game: ScheduleGame) -> bytes:
    data = download(pdf_url, pdf_path, binary=True)
    if not bytes(data).startswith(b"%PDF"):
        raise RuntimeError("downloaded file is not a PDF")
    if pdf_matches_game(pdf_path, game):
        return bytes(data)
    pdf_path.unlink(missing_ok=True)
    data = download(pdf_url, pdf_path, binary=True)
    if not bytes(data).startswith(b"%PDF"):
        raise RuntimeError("downloaded file is not a PDF")
    if not pdf_matches_game(pdf_path, game):
        raise PdfGameMismatch("PDF does not match scheduled teams")
    return bytes(data)


def load_schedule(start: int, end: int) -> list[ScheduleGame]:
    text = download(SCHEDULE_URL, SCHEDULE_CACHE)
    games: list[ScheduleGame] = []
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("game_type") != "REG":
            continue
        season = int(row["season"])
        if start <= season <= end:
            games.append(
                ScheduleGame(
                    game_id=row["game_id"],
                    season=season,
                    week=int(row["week"]),
                    gameday=row["gameday"],
                    away_team=row["away_team"],
                    home_team=row["home_team"],
                    gsis=row.get("gsis") or "",
                )
            )
    return games


def nfl_game_url(game: ScheduleGame) -> str:
    away = TEAM_SLUG.get(game.away_team, game.away_team.lower())
    home = TEAM_SLUG.get(game.home_team, game.home_team.lower())
    return f"https://www.nfl.com/games/{away}-at-{home}-{game.season}-reg-{game.week}"


def load_pdf_overrides() -> dict[str, str]:
    if not PDF_OVERRIDES.exists():
        return {}
    with PDF_OVERRIDES.open(encoding="utf-8-sig", newline="") as handle:
        return {
            row["game_id"]: row["pdf_url"]
            for row in csv.DictReader(handle)
            if row.get("game_id") and row.get("pdf_url")
        }


def discover_pdf_url(game: ScheduleGame, pdf_overrides: dict[str, str]) -> tuple[str | None, str | None]:
    if game.game_id in pdf_overrides:
        return pdf_overrides[game.game_id], None
    url = nfl_game_url(game)
    html_path = CACHE / "html" / f"{game.game_id}.html"
    try:
        html = download(url, html_path)
    except (HTTPError, URLError, TimeoutError) as exc:
        return None, f"page fetch failed: {exc}"
    pdfs = re.findall(
        r'href="(https://static\.www\.nfl\.com/image/upload/[^"]+?\.pdf)"[^>]*>.*?Download Game Book',
        html,
        flags=re.S,
    )
    if not pdfs and "Game Book" in html:
        gamebook_index = html.find("Game Book")
        window = html[max(0, gamebook_index - 2000) : gamebook_index + 2000]
        pdfs = re.findall(r"https://static\.www\.nfl\.com/image/upload/[^\"\\]+?\.pdf", window)
    pdfs = [pdf for pdf in pdfs if "media-guides" not in pdf]
    if not pdfs:
        if game.gsis:
            return f"https://www.nflgsis.com/{game.season}/Reg/{game.week:02d}/{game.gsis}/Gamebook.pdf", None
        return None, "no gamebook pdf link found"
    return pdfs[0], None


def line_y(span: dict) -> float:
    return round(float(span["bbox"][1]), 1)


def page_spans(pdf_path: Path) -> list[tuple[float, float, str]]:
    doc = fitz.open(pdf_path)
    spans: list[tuple[float, float, str]] = []
    for page in doc[:1]:
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if text:
                        x0, y0, _x1, _y1 = span["bbox"]
                        spans.append((float(x0), float(y0), text))
    return spans


def find_section_y(spans: list[tuple[float, float, str]], text: str) -> float | None:
    matches = [y for _x, y, value in spans if value == text]
    return min(matches) if matches else None


def parse_lineups(spans: list[tuple[float, float, str]], away: str, home: str) -> list[ParsedEntry]:
    start = find_section_y(spans, "Offense")
    sub_y = find_section_y(spans, "Substitutions")
    if start is None or sub_y is None:
        return []
    rows: dict[float, list[tuple[float, str]]] = {}
    for x, y, value in spans:
        if y <= start or y >= sub_y:
            continue
        rows.setdefault(round(y, 1), []).append((x, value))
    entries: list[ParsedEntry] = []
    for _y, parts in sorted(rows.items()):
        parts = sorted(parts)
        cells = [value for _x, value in parts]
        if len(cells) < 3:
            continue
        for i in range(0, len(cells) - 2, 3):
            pos, jersey, name = cells[i], cells[i + 1], cells[i + 2]
            if valid_position(pos) and jersey.isdigit():
                x = parts[i][0]
                team = away if x < 306 else home
                entries.append(ParsedEntry(team, pos, jersey, name, "Lineups"))
    return entries


SUB_ENTRY_RE = re.compile(
    r"^(?:(?P<pos>[A-Z]{1,4}(?:[/\-][A-Z]{1,4})*)\s+)?"
    r"(?P<jersey>\d{1,2})\s+(?P<name>.+?)$"
)


def parse_sub_block(text: str, team: str) -> list[ParsedEntry]:
    entries: list[ParsedEntry] = []
    text = re.sub(r"\s+", " ", text).strip().strip(",")
    last_pos = ""
    for chunk in (part.strip() for part in text.split(",")):
        if not chunk:
            continue
        match = SUB_ENTRY_RE.match(chunk)
        if not match:
            continue
        pos = (match.group("pos") or last_pos).strip()
        if not valid_position(pos):
            continue
        last_pos = pos
        name = match.group("name").strip(" ,")
        if name:
            entries.append(ParsedEntry(team, pos, match.group("jersey"), name, "Substitutions"))
    return entries


def parse_substitutions(spans: list[tuple[float, float, str]], away: str, home: str) -> list[ParsedEntry]:
    sub_y = find_section_y(spans, "Substitutions")
    dnp_y = find_section_y(spans, "Did Not Play")
    if sub_y is None or dnp_y is None:
        return []
    left: list[tuple[float, str]] = []
    right: list[tuple[float, str]] = []
    for x, y, value in spans:
        if y <= sub_y or y >= dnp_y:
            continue
        if x < 306:
            left.append((y, value))
        else:
            right.append((y, value))
    left_text = " ".join(value for _y, value in sorted(left))
    right_text = " ".join(value for _y, value in sorted(right))
    return parse_sub_block(left_text, away) + parse_sub_block(right_text, home)


def parse_gamebook(pdf_path: Path, away: str, home: str) -> list[ParsedEntry]:
    spans = page_spans(pdf_path)
    entries = parse_lineups(spans, away, home) + parse_substitutions(spans, away, home)
    seen: set[tuple[str, str, str]] = set()
    deduped: list[ParsedEntry] = []
    for entry in entries:
        key = (entry.team_id, entry.jersey, entry.gamebook_name)
        if key not in seen:
            seen.add(key)
            deduped.append(entry)
    return deduped


def roster_paths(season: int) -> list[Path]:
    return [
        ROOT / "raw" / "nfl" / "weekly_rosters" / f"roster_weekly_{season}.csv",
        ROOT / "raw" / "nfl" / "rosters" / f"roster_{season}.csv",
    ]


def load_roster_index(seasons: set[int]) -> dict[tuple[int, int | None, str, str], list[dict[str, str]]]:
    index: dict[tuple[int, int | None, str, str], list[dict[str, str]]] = {}
    for season in sorted(seasons):
        for path in roster_paths(season):
            if not path.exists():
                continue
            with path.open(encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    gsis = (row.get("gsis_id") or "").strip()
                    jersey = (row.get("jersey_number") or "").strip()
                    team = (row.get("team") or "").strip().upper()
                    if not gsis or not jersey or not team:
                        continue
                    week_text = (row.get("week") or "").strip()
                    week = int(week_text) if week_text.isdigit() else None
                    game_type = (row.get("game_type") or "").strip()
                    if week is not None and game_type and game_type != "REG":
                        continue
                    index.setdefault((season, week, team, jersey), []).append(row)
                    index.setdefault((season, week, team, "*"), []).append(row)
                    index.setdefault((season, None, team, "*"), []).append(row)
    return index


def load_player_registry() -> list[dict[str, str]]:
    if not PLAYER_REGISTRY.exists():
        return []
    with PLAYER_REGISTRY.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def correction_key(game_id: str, entry: ParsedEntry) -> tuple[str, str, str, str, str]:
    return (game_id, entry.team_id, entry.jersey, entry.gamebook_name, entry.position)


def load_manual_corrections() -> dict[tuple[str, str, str, str, str], dict[str, str]]:
    if not MANUAL_CORRECTIONS.exists():
        return {}
    with MANUAL_CORRECTIONS.open(encoding="utf-8-sig", newline="") as handle:
        return {
            (row["game_id"], row["team_id"], row["jersey_number"], row["gamebook_name"], row["position"]): row
            for row in csv.DictReader(handle)
        }


def registry_by_player_id(player_registry: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {
        f"nfl:{row['gsis_id'].strip()}": row
        for row in player_registry
        if (row.get("gsis_id") or "").strip()
    }


def split_gamebook_name(name: str) -> tuple[str, str]:
    if "." in name:
        first, last = name.split(".", 1)
        return normalize(first), normalize(last)
    parts = name.split()
    if len(parts) >= 2:
        return normalize(parts[0]), normalize(" ".join(parts[1:]))
    return "", normalize(name)


def name_matches(gamebook_name: str, row: dict[str, str]) -> bool:
    first_hint, last_hint = split_gamebook_name(gamebook_name)
    roster_first = normalize(row.get("first_name") or row.get("football_name") or "")
    roster_common = normalize(row.get("common_first_name") or row.get("football_name") or "")
    roster_football = normalize(row.get("football_name") or "")
    roster_last = normalize(row.get("last_name") or "")
    roster_full = normalize(row.get("full_name") or "")
    roster_display = normalize(row.get("display_name") or "")
    if last_hint and last_hint not in {roster_last, normalize(row.get("full_name", "").split()[-1] if row.get("full_name") else "")}:
        if last_hint not in roster_full and last_hint not in roster_display:
            return False
    if first_hint and not (
        roster_first.startswith(first_hint)
        or roster_common.startswith(first_hint)
        or roster_football.startswith(first_hint)
    ):
        return False
    return True


def last_name_matches(gamebook_name: str, row: dict[str, str]) -> bool:
    _first_hint, last_hint = split_gamebook_name(gamebook_name)
    roster_last = normalize(row.get("last_name") or "")
    roster_full = normalize(row.get("full_name") or "")
    roster_display = normalize(row.get("display_name") or "")
    return bool(last_hint and (last_hint == roster_last or last_hint in roster_full or last_hint in roster_display))


def plausible_registry_season(row: dict[str, str], season: int) -> bool:
    starts = [
        row.get("rookie_season"),
        row.get("rookie_year"),
        row.get("draft_year"),
    ]
    ends = [
        row.get("last_season"),
        row.get("to"),
    ]
    start_years = [int(value) for value in starts if (value or "").isdigit()]
    end_years = [int(value) for value in ends if (value or "").isdigit()]
    if start_years and season < min(start_years) - 1:
        return False
    if end_years and season > max(end_years) + 1:
        return False
    return True


def registry_row_to_roster(row: dict[str, str], entry: ParsedEntry) -> dict[str, str]:
    return {
        "gsis_id": row.get("gsis_id") or "",
        "full_name": row.get("display_name") or entry.gamebook_name,
        "first_name": row.get("first_name") or row.get("common_first_name") or "",
        "last_name": row.get("last_name") or "",
        "football_name": row.get("football_name") or row.get("common_first_name") or "",
        "birth_date": row.get("birth_date") or "",
        "position": row.get("position") or entry.position,
        "pfr_id": row.get("pfr_id") or "",
        "headshot_url": row.get("headshot") or row.get("headshot_url") or "",
    }


def registry_lookup(
    entry: ParsedEntry,
    game: ScheduleGame,
    player_registry: list[dict[str, str]],
) -> tuple[str | None, dict[str, str] | None, str | None]:
    registry_candidates = [
        row
        for row in player_registry
        if (row.get("gsis_id") or "").strip()
        and name_matches(entry.gamebook_name, row)
        and plausible_registry_season(row, game.season)
    ]
    if entry.jersey:
        jersey_matches = [
            row
            for row in registry_candidates
            if (row.get("jersey_number") or "").strip() in {"", "0", entry.jersey}
        ]
        if jersey_matches:
            registry_candidates = jersey_matches
    deduped_registry = {}
    for row in registry_candidates:
        deduped_registry.setdefault(row.get("gsis_id") or "", row)
    registry_candidates = list(deduped_registry.values())
    if len(registry_candidates) == 1:
        row = registry_row_to_roster(registry_candidates[0], entry)
        return f"nfl:{row['gsis_id'].strip()}", row, "accepted unique player-registry match; roster cache missing/mismarked game row"
    return None, None, None


def resolve_entry(
    entry: ParsedEntry,
    game: ScheduleGame,
    roster_index: dict[tuple[int, int | None, str, str], list[dict[str, str]]],
    player_registry: list[dict[str, str]],
) -> tuple[str | None, dict[str, str] | None, str | None]:
    team_codes = ROSTER_TEAM_ALIASES.get(entry.team_id, (entry.team_id,))
    candidates = []
    for team_code in team_codes:
        candidates.extend(roster_index.get((game.season, game.week, team_code, entry.jersey), []))
    if not candidates:
        for team_code in team_codes:
            candidates.extend(roster_index.get((game.season, None, team_code, entry.jersey), []))
    if not candidates:
        name_candidates = []
        for team_code in team_codes:
            name_candidates.extend(
                row
                for row in roster_index.get((game.season, game.week, team_code, "*"), [])
                if name_matches(entry.gamebook_name, row) or last_name_matches(entry.gamebook_name, row)
            )
            name_candidates.extend(
                row
                for row in roster_index.get((game.season, None, team_code, "*"), [])
                if name_matches(entry.gamebook_name, row) or last_name_matches(entry.gamebook_name, row)
            )
        deduped_names = {}
        for row in name_candidates:
            deduped_names.setdefault((row.get("gsis_id") or "", row.get("full_name") or ""), row)
        name_candidates = list(deduped_names.values())
        if len(name_candidates) == 1:
            row = name_candidates[0]
            return f"nfl:{row['gsis_id'].strip()}", row, "accepted unique team/name match despite missing jersey match"
        registry_player_id, registry_row, registry_warning = registry_lookup(entry, game, player_registry)
        if registry_player_id and registry_row:
            return registry_player_id, registry_row, registry_warning
        return None, None, "no roster candidate for team/week/jersey"
    deduped = {}
    for row in candidates:
        deduped.setdefault((row.get("gsis_id") or "", row.get("full_name") or ""), row)
    candidates = list(deduped.values())
    matching = [row for row in candidates if name_matches(entry.gamebook_name, row)]
    if len(matching) == 1:
        row = matching[0]
        return f"nfl:{row['gsis_id'].strip()}", row, None
    last_matching = [row for row in candidates if last_name_matches(entry.gamebook_name, row)]
    if len(last_matching) == 1:
        row = last_matching[0]
        return f"nfl:{row['gsis_id'].strip()}", row, "accepted unique last-name/jersey match despite first-name mismatch"
    if len(candidates) == 1:
        row = candidates[0]
        return f"nfl:{row['gsis_id'].strip()}", row, "accepted unique jersey candidate despite abbreviated-name mismatch"
    if len(matching) > 1:
        row = matching[0]
        return f"nfl:{row['gsis_id'].strip()}", row, "multiple matching candidates; used first"
    registry_player_id, registry_row, registry_warning = registry_lookup(entry, game, player_registry)
    if registry_player_id and registry_row:
        return registry_player_id, registry_row, registry_warning
    return None, None, f"ambiguous roster candidates: {len(candidates)}"


def process_game(
    game: ScheduleGame,
    roster_index: dict[tuple[int, int | None, str, str], list[dict[str, str]]],
    player_registry: list[dict[str, str]],
    player_registry_by_id: dict[str, dict[str, str]],
    manual_corrections: dict[tuple[str, str, str, str, str], dict[str, str]],
    pdf_overrides: dict[str, str],
) -> GameResult:
    pdf_url, error = discover_pdf_url(game, pdf_overrides)
    status = "missing_pdf" if error else "ok"
    message = error
    parsed_entries: list[ParsedEntry] = []
    if pdf_url:
        pdf_path = CACHE / "pdf" / f"{game.game_id}.pdf"
        try:
            download_game_pdf(pdf_url, pdf_path, game)
            parsed_entries = parse_gamebook(pdf_path, game.away_team, game.home_team)
        except Exception as exc:
            status = "parse_error"
            message = str(exc)
    resolved: list[ResolvedEntry] = []
    unresolved: list[UnresolvedEntry] = []
    counted_entries = 0
    for entry in parsed_entries:
        correction = manual_corrections.get(correction_key(game.game_id, entry))
        if correction and correction.get("action") == "exclude":
            continue
        counted_entries += 1
        if correction and correction.get("action") == "map":
            player_id = correction.get("player_id") or ""
            registry_row = player_registry_by_id.get(player_id)
            if player_id and registry_row:
                row = registry_row_to_roster(registry_row, entry)
                resolved.append(ResolvedEntry(entry, player_id, row, correction.get("note") or "manual correction"))
                continue
        player_id, row, warning = resolve_entry(entry, game, roster_index, player_registry)
        if player_id is None or row is None:
            unresolved.append(UnresolvedEntry(entry, warning or "unresolved"))
        else:
            resolved.append(ResolvedEntry(entry, player_id, row, warning))
    return GameResult(game, pdf_url, status, message, counted_entries, resolved, unresolved)


def build(args: argparse.Namespace) -> None:
    games = load_schedule(args.season_start, args.season_end)
    if args.limit:
        games = games[: args.limit]
    roster_index = load_roster_index({game.season for game in games})
    player_registry = load_player_registry()
    player_registry_by_id = registry_by_player_id(player_registry)
    manual_corrections = load_manual_corrections()
    pdf_overrides = load_pdf_overrides()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.output)
    conn.executescript(SCHEMA)
    conn.execute("DELETE FROM nfl_gamebook_games")
    conn.execute("DELETE FROM nfl_player_game_snap_appearances")
    conn.execute("DELETE FROM nfl_snap_players")
    conn.execute("DELETE FROM nfl_snap_unmapped_players")
    conn.execute("DELETE FROM nfl_snap_build_meta")

    total_entries = 0
    resolved = 0
    status_counts: dict[str, int] = {}
    player_meta: dict[str, dict[str, str | int | None]] = {}
    def write_result(result: GameResult) -> None:
        nonlocal total_entries, resolved
        game = result.game
        conn.execute(
            """
            INSERT OR REPLACE INTO nfl_gamebook_games
                (game_id, season, week, gameday, away_team, home_team, nfl_url,
                 pdf_url, status, message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                game.game_id,
                game.season,
                game.week,
                game.gameday,
                game.away_team,
                game.home_team,
                nfl_game_url(game),
                result.pdf_url,
                result.status,
                result.message,
            ),
        )
        status_counts[result.status] = status_counts.get(result.status, 0) + 1
        total_entries += result.entries
        for item in result.unresolved:
            entry = item.entry
            conn.execute(
                """
                INSERT OR REPLACE INTO nfl_snap_unmapped_players
                    (game_id, team_id, season, week, jersey_number, gamebook_name, position, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (game.game_id, entry.team_id, game.season, game.week, entry.jersey, entry.gamebook_name, entry.position, item.reason),
            )
        for item in result.resolved:
            entry = item.entry
            row = item.roster_row
            player_id = item.player_id
            resolved += 1
            display_name = row.get("full_name") or entry.gamebook_name
            pfr = row.get("pfr_id") or ""
            conn.execute(
                """
                INSERT OR REPLACE INTO nfl_player_game_snap_appearances
                    (game_id, season, week, team_id, player_id, pfr_player_id,
                     display_name, position, total_snaps, source_section)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (game.game_id, game.season, game.week, entry.team_id, player_id, pfr, display_name, entry.position, entry.section),
            )
            birth = (row.get("birth_date") or "")[:4]
            player_meta.setdefault(
                player_id,
                {
                    "pfr_player_id": pfr,
                    "gsis_id": row.get("gsis_id") or player_id.removeprefix("nfl:"),
                    "display_name": display_name,
                    "first_name": row.get("first_name") or None,
                    "last_name": row.get("last_name") or None,
                    "birth_year": int(birth) if birth.isdigit() else None,
                    "primary_pos": row.get("position") or entry.position,
                    "headshot_url": row.get("headshot_url") or None,
                },
            )
    if args.workers <= 1:
        iterable = (
            (idx, process_game(game, roster_index, player_registry, player_registry_by_id, manual_corrections, pdf_overrides))
            for idx, game in enumerate(games, start=1)
        )
        for idx, result in iterable:
            write_result(result)
            if idx % args.commit_every == 0:
                conn.commit()
            if idx % args.progress_every == 0:
                print(
                    f"{idx:,}/{len(games):,} games; entries {total_entries:,}; resolved {resolved:,}; statuses {status_counts}",
                    flush=True,
                )
            if args.sleep:
                time.sleep(args.sleep)
    else:
        completed = 0
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [
                pool.submit(process_game, game, roster_index, player_registry, player_registry_by_id, manual_corrections, pdf_overrides)
                for game in games
            ]
            for future in as_completed(futures):
                completed += 1
                write_result(future.result())
                if completed % args.commit_every == 0:
                    conn.commit()
                if completed % args.progress_every == 0:
                    print(
                        f"{completed:,}/{len(games):,} games; entries {total_entries:,}; resolved {resolved:,}; statuses {status_counts}",
                        flush=True,
                    )
    if len(games) % args.progress_every:
        print(
            f"{len(games):,}/{len(games):,} games; entries {total_entries:,}; resolved {resolved:,}; statuses {status_counts}",
            flush=True,
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO nfl_snap_players
            (player_id, pfr_player_id, gsis_id, display_name, first_name,
             last_name, birth_year, primary_pos, headshot_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                player_id,
                str(meta["pfr_player_id"] or ""),
                str(meta["gsis_id"] or ""),
                str(meta["display_name"] or ""),
                meta["first_name"],
                meta["last_name"],
                meta["birth_year"],
                meta["primary_pos"],
                meta["headshot_url"],
            )
            for player_id, meta in sorted(player_meta.items())
        ],
    )
    stored_appearances = conn.execute("SELECT COUNT(*) FROM nfl_player_game_snap_appearances").fetchone()[0]
    stored_players = conn.execute("SELECT COUNT(*) FROM nfl_snap_players").fetchone()[0]
    unresolved_entries = conn.execute("SELECT COUNT(*) FROM nfl_snap_unmapped_players").fetchone()[0]
    for key, value in {
        "source": SOURCE_NAME,
        "season_start": str(args.season_start),
        "season_end": str(args.season_end),
        "games": str(len(games)),
        "gamebook_entries": str(total_entries),
        "snap_appearances": str(stored_appearances),
        "players": str(stored_players),
        "unresolved_entries": str(unresolved_entries),
        "status_counts": json.dumps(status_counts, sort_keys=True),
    }.items():
        conn.execute("INSERT OR REPLACE INTO nfl_snap_build_meta (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    print(
        f"built {args.output}: games {len(games):,}; entries {total_entries:,}; "
        f"stored appearances {stored_appearances:,}; unresolved {unresolved_entries:,}; statuses {status_counts}",
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--season-start", type=int, default=2000)
    parser.add_argument("--season-end", type=int, default=2012)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--commit-every", type=int, default=100)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)
    build(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
