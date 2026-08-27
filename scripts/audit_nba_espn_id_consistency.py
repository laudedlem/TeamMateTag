#!/usr/bin/env python3
"""Audit ESPN NBA athlete ids against TeamMateTag/NBA person ids.

SportsDataverse ESPN boxscores are the best local Basketball game-proof source,
but TeamMateTag currently uses NBA Stats person ids. This script builds a
candidate crosswalk by comparing normalized names plus season/team footprints.
It writes reviewable CSVs and does not change production data.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

try:
    import psycopg
except ImportError:
    psycopg = None

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


ROOT = Path(__file__).resolve().parent.parent
ESPN_DIR = ROOT / "raw" / "nba"
NBA_STATS = ROOT / "raw" / "nba_kaggle" / "PlayerStatistics.csv"
LOCAL_SPORT_DB = ROOT / "db" / "teammatetag_local.sqlite"
OUT_DIR = ROOT / "raw" / "nba_identity"

ESPN_TO_NBA_TEAM = {
    "1": "1610612737",   # Hawks
    "2": "1610612738",   # Celtics
    "3": "1610612740",   # Pelicans/Hornets
    "4": "1610612741",   # Bulls
    "5": "1610612739",   # Cavaliers
    "6": "1610612742",   # Mavericks
    "7": "1610612743",   # Nuggets
    "8": "1610612765",   # Pistons
    "9": "1610612744",   # Warriors
    "10": "1610612745",  # Rockets
    "11": "1610612754",  # Pacers
    "12": "1610612746",  # Clippers
    "13": "1610612747",  # Lakers
    "14": "1610612748",  # Heat
    "15": "1610612749",  # Bucks
    "16": "1610612750",  # Timberwolves
    "17": "1610612751",  # Nets
    "18": "1610612752",  # Knicks
    "19": "1610612753",  # Magic
    "20": "1610612755",  # 76ers
    "21": "1610612756",  # Suns
    "22": "1610612757",  # Trail Blazers
    "23": "1610612758",  # Kings
    "24": "1610612759",  # Spurs
    "25": "1610612760",  # Thunder/SuperSonics
    "26": "1610612762",  # Jazz
    "27": "1610612764",  # Wizards
    "28": "1610612761",  # Raptors
    "29": "1610612763",  # Grizzlies
    "30": "1610612766",  # Hornets/Bobcats
}

OFFICIAL_NBA_TEAM_IDS = set(ESPN_TO_NBA_TEAM.values())


@dataclass
class IdentityFootprint:
    source_id: str
    names: Counter[str] = field(default_factory=Counter)
    display_names: Counter[str] = field(default_factory=Counter)
    season_teams: set[tuple[int, str]] = field(default_factory=set)
    seasons: set[int] = field(default_factory=set)
    teams: set[str] = field(default_factory=set)
    rows: int = 0
    played_rows: int = 0

    @property
    def normalized_name(self) -> str:
        return self.names.most_common(1)[0][0] if self.names else ""

    @property
    def display_name(self) -> str:
        return self.display_names.most_common(1)[0][0] if self.display_names else ""


def normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    text = text.lower().replace(".", "")
    text = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def season_from_nba_game_id(game_id: str, fallback_date: str = "") -> int | None:
    digits = "".join(ch for ch in str(game_id or "") if ch.isdigit())
    if len(digits) >= 5:
        padded = digits.zfill(10)
        if not padded.startswith("002"):
            return None
        yy = int(padded[3:5])
        return 1900 + yy if yy >= 47 else 2000 + yy
    if fallback_date:
        try:
            year = int(fallback_date[:4])
            month = int(fallback_date[5:7])
        except ValueError:
            return None
        return year - 1 if month <= 6 else year
    return None


def norm_team_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def team_name_map() -> dict[str, str]:
    mapping = {
        "atlanta hawks": "1610612737",
        "boston celtics": "1610612738",
        "cleveland cavaliers": "1610612739",
        "new orleans hornets": "1610612740",
        "new orleans pelicans": "1610612740",
        "oklahoma city hornets": "1610612740",
        "chicago bulls": "1610612741",
        "dallas mavericks": "1610612742",
        "denver nuggets": "1610612743",
        "golden state warriors": "1610612744",
        "houston rockets": "1610612745",
        "la clippers": "1610612746",
        "los angeles clippers": "1610612746",
        "los angeles lakers": "1610612747",
        "miami heat": "1610612748",
        "milwaukee bucks": "1610612749",
        "minnesota timberwolves": "1610612750",
        "new jersey nets": "1610612751",
        "brooklyn nets": "1610612751",
        "new york knicks": "1610612752",
        "orlando magic": "1610612753",
        "indiana pacers": "1610612754",
        "philadelphia 76ers": "1610612755",
        "phoenix suns": "1610612756",
        "portland trail blazers": "1610612757",
        "sacramento kings": "1610612758",
        "san antonio spurs": "1610612759",
        "seattle supersonics": "1610612760",
        "oklahoma city thunder": "1610612760",
        "toronto raptors": "1610612761",
        "utah jazz": "1610612762",
        "vancouver grizzlies": "1610612763",
        "memphis grizzlies": "1610612763",
        "washington wizards": "1610612764",
        "detroit pistons": "1610612765",
        "charlotte bobcats": "1610612766",
        "charlotte hornets": "1610612766",
    }
    if LOCAL_SPORT_DB.exists():
        conn = sqlite3.connect(LOCAL_SPORT_DB)
        try:
            for team_id, name in conn.execute(
                "SELECT DISTINCT team_id, name FROM sport_teams WHERE sport_id='basketball'"
            ):
                if str(team_id) in OFFICIAL_NBA_TEAM_IDS:
                    mapping[norm_team_name(str(name))] = str(team_id)
        finally:
            conn.close()
    return mapping


def parse_minutes(value: str) -> float:
    try:
        return float(value or 0)
    except ValueError:
        return 0.0


def read_espn(season_start: int, season_end: int) -> dict[str, IdentityFootprint]:
    players: dict[str, IdentityFootprint] = {}
    for path in sorted(ESPN_DIR.glob("player_box_*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("season_type") != "2":
                    continue
                try:
                    season = int(row.get("season") or "")
                except ValueError:
                    continue
                if season < season_start or season > season_end:
                    continue
                espn_team_id = (row.get("team_id") or "").strip()
                team_id = ESPN_TO_NBA_TEAM.get(espn_team_id)
                if not team_id:
                    continue
                source_id = (row.get("athlete_id") or "").strip()
                display_name = (row.get("athlete_display_name") or "").strip()
                if not source_id or not display_name:
                    continue
                item = players.setdefault(source_id, IdentityFootprint(source_id))
                item.rows += 1
                item.names[normalize(display_name)] += 1
                item.display_names[display_name] += 1
                if parse_minutes(row.get("minutes", "")) <= 0 or row.get("did_not_play") == "true":
                    continue
                item.played_rows += 1
                item.season_teams.add((season, team_id))
                item.seasons.add(season)
                item.teams.add(team_id)
    return players


def read_nba_stats(season_start: int, season_end: int) -> dict[str, IdentityFootprint]:
    name_to_team = team_name_map()
    players: dict[str, IdentityFootprint] = {}
    with NBA_STATS.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("gameType") != "Regular Season":
                continue
            season = season_from_nba_game_id(row.get("gameId", ""), row.get("gameDate", ""))
            if season is None or season < season_start or season > season_end:
                continue
            raw_team_id = (row.get("playerteamId") or "").strip()
            if raw_team_id in OFFICIAL_NBA_TEAM_IDS:
                team_id = raw_team_id
            else:
                team_id = name_to_team.get(
                    norm_team_name(f"{row.get('playerteamCity', '')} {row.get('playerteamName', '')}")
                )
            if not team_id:
                continue
            source_id = (row.get("personId") or "").strip()
            display_name = f"{row.get('firstName', '').strip()} {row.get('lastName', '').strip()}".strip()
            if not source_id or not display_name:
                continue
            item = players.setdefault(source_id, IdentityFootprint(source_id))
            item.rows += 1
            item.names[normalize(display_name)] += 1
            item.display_names[display_name] += 1
            if parse_minutes(row.get("numMinutes", "")) <= 0:
                continue
            item.played_rows += 1
            item.season_teams.add((season, team_id))
            item.seasons.add(season)
            item.teams.add(team_id)
    return players


def read_production_players() -> dict[str, dict[str, object]]:
    if not psycopg:
        return {}
    if load_dotenv:
        load_dotenv(ROOT / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        return {}
    conn = psycopg.connect(url)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT player_id, external_id, display_name, debut_year, final_year, primary_pos
              FROM sport_players
             WHERE sport_id='basketball'
            """
        )
        return {
            str(row[0]).removeprefix("nba:"): {
                "player_id": row[0],
                "external_id": row[1],
                "display_name": row[2],
                "debut_year": row[3],
                "final_year": row[4],
                "primary_pos": row[5],
            }
            for row in cur.fetchall()
        }
    finally:
        conn.close()


def score_match(espn: IdentityFootprint, nba: IdentityFootprint) -> tuple[float, int, int, int]:
    shared = len(espn.season_teams & nba.season_teams)
    union = len(espn.season_teams | nba.season_teams)
    season_shared = len(espn.seasons & nba.seasons)
    team_shared = len(espn.teams & nba.teams)
    score = (shared * 10.0) + (season_shared * 1.5) + team_shared
    if union:
        score += shared / union
    return score, shared, season_shared, team_shared


def format_set(values: set, limit: int = 18) -> str:
    rendered = [str(value) for value in sorted(values)]
    suffix = "" if len(rendered) <= limit else f";+{len(rendered) - limit}"
    return ";".join(rendered[:limit]) + suffix


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--season-start", type=int, default=2002)
    parser.add_argument("--season-end", type=int, default=2025)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    espn_players = read_espn(args.season_start, args.season_end)
    nba_players = read_nba_stats(args.season_start, args.season_end)
    prod = read_production_players()

    nba_by_name: dict[str, list[IdentityFootprint]] = defaultdict(list)
    for item in nba_players.values():
        nba_by_name[item.normalized_name].append(item)

    rows = []
    status_counts = Counter()
    for espn in sorted(espn_players.values(), key=lambda item: (item.display_name, item.source_id)):
        candidates = []
        for nba in nba_by_name.get(espn.normalized_name, []):
            score, shared, season_shared, team_shared = score_match(espn, nba)
            if shared or season_shared or team_shared:
                candidates.append((score, shared, season_shared, team_shared, nba))
        candidates.sort(key=lambda item: item[:4], reverse=True)
        best = candidates[0] if candidates else None
        second = candidates[1] if len(candidates) > 1 else None
        if best and best[1] >= 1 and (not second or best[0] >= second[0] + 5):
            status = "auto_footprint"
        elif best and best[1] >= 1:
            status = "review_ambiguous"
        elif len(nba_by_name.get(espn.normalized_name, [])) == 1:
            status = "auto_unique_name"
        else:
            status = "unmatched"
        status_counts[status] += 1
        nba = best[4] if best else (
            nba_by_name[espn.normalized_name][0] if len(nba_by_name.get(espn.normalized_name, [])) == 1 else None
        )
        nba_id = nba.source_id if nba else ""
        prod_row = prod.get(nba_id, {})
        rows.append({
            "status": status,
            "espn_id": espn.source_id,
            "espn_name": espn.display_name,
            "nba_person_id": nba_id,
            "production_player_id": prod_row.get("player_id", f"nba:{nba_id}" if nba_id else ""),
            "production_name": prod_row.get("display_name", nba.display_name if nba else ""),
            "score": f"{best[0]:.3f}" if best else "",
            "shared_season_teams": best[1] if best else 0,
            "shared_seasons": best[2] if best else 0,
            "shared_teams": best[3] if best else 0,
            "second_nba_person_id": second[4].source_id if second else "",
            "second_score": f"{second[0]:.3f}" if second else "",
            "espn_seasons": format_set(espn.seasons),
            "espn_teams": format_set(espn.teams),
            "nba_seasons": format_set(nba.seasons) if nba else "",
            "nba_teams": format_set(nba.teams) if nba else "",
            "espn_played_rows": espn.played_rows,
            "nba_played_rows": nba.played_rows if nba else "",
        })

    output = args.out_dir / "espn_to_nba_crosswalk_audit.csv"
    with output.open("w", encoding="utf-8", newline="") as handle:
        fields = [
            "status", "espn_id", "espn_name", "nba_person_id", "production_player_id",
            "production_name", "score", "shared_season_teams", "shared_seasons",
            "shared_teams", "second_nba_person_id", "second_score", "espn_seasons",
            "espn_teams", "nba_seasons", "nba_teams", "espn_played_rows", "nba_played_rows",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    auto = args.out_dir / "espn_to_nba_crosswalk_auto.csv"
    with auto.open("w", encoding="utf-8", newline="") as handle:
        fields = ["espn_id", "nba_person_id", "production_player_id", "espn_name", "production_name"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            if row["status"].startswith("auto_") and row["nba_person_id"]:
                writer.writerow({key: row[key] for key in fields})

    review = args.out_dir / "espn_to_nba_crosswalk_review.csv"
    with review.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            if not row["status"].startswith("auto_"):
                writer.writerow({key: row[key] for key in fields})

    print(f"ESPN athletes: {len(espn_players):,}", flush=True)
    print(f"NBA-id source players: {len(nba_players):,}", flush=True)
    for status, count in status_counts.most_common():
        print(f"{status}: {count:,}", flush=True)
    print(f"Wrote {output}", flush=True)
    print(f"Wrote {auto}", flush=True)
    print(f"Wrote {review}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
