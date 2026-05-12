from __future__ import annotations

import csv
import io
import os
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import psycopg

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


ROOT = Path(__file__).resolve().parent.parent
ZIP_PATH = ROOT / "raw" / "lahman_1871-2025_csv.zip"
DATABASE_URL = os.environ.get("DATABASE_URL")
START_YEAR = 2000


def open_csv(zf: zipfile.ZipFile, name: str):
    return csv.DictReader(io.TextIOWrapper(zf.open(name), encoding="utf-8-sig", newline=""))


def main():
    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL is required")
    if not ZIP_PATH.exists():
        raise SystemExit(f"Missing zip: {ZIP_PATH}")

    with psycopg.connect(DATABASE_URL, autocommit=True, prepare_threshold=None) as conn:
        conn.execute("SET default_transaction_read_only = off")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS player_playoff_traits (
                   player_id TEXT PRIMARY KEY REFERENCES players(player_id),
                   birth_country TEXT,
                   is_japanese BOOLEAN NOT NULL DEFAULT false,
                   is_cuban BOOLEAN NOT NULL DEFAULT false,
                   is_canadian BOOLEAN NOT NULL DEFAULT false,
                   mvp_count INTEGER NOT NULL DEFAULT 0,
                   roty_count INTEGER NOT NULL DEFAULT 0,
                   gold_glove_count INTEGER NOT NULL DEFAULT 0,
                   triple_crown_count INTEGER NOT NULL DEFAULT 0,
                   career_hr INTEGER NOT NULL DEFAULT 0,
                   world_series_rings INTEGER NOT NULL DEFAULT 0,
                   team_count INTEGER NOT NULL DEFAULT 0,
                   franchise_count INTEGER NOT NULL DEFAULT 0,
                   season_count INTEGER NOT NULL DEFAULT 0,
                   hound_dog_eligible BOOLEAN NOT NULL DEFAULT false,
                   journeyman_eligible BOOLEAN NOT NULL DEFAULT false,
                   created_at TIMESTAMPTZ NOT NULL DEFAULT now()
               )"""
        )
        conn.execute("DELETE FROM player_playoff_traits")

        players = {row[0] for row in conn.execute("SELECT player_id FROM players").fetchall()}
        team_rows = conn.execute("SELECT team_id, season, franchise_id FROM teams").fetchall()
        team_to_franchise = {(team_id, season): franchise_id for team_id, season, franchise_id in team_rows}
        appearance_rows = conn.execute("SELECT player_id, team_id, season FROM appearances").fetchall()

        team_counts: dict[str, set[str]] = defaultdict(set)
        franchise_counts: dict[str, set[str]] = defaultdict(set)
        season_counts: dict[str, set[int]] = defaultdict(set)
        for player_id, team_id, season in appearance_rows:
            team_counts[player_id].add(team_id)
            season_counts[player_id].add(season)
            franchise_id = team_to_franchise.get((team_id, season))
            if franchise_id:
                franchise_counts[player_id].add(franchise_id)

        birth_country: dict[str, str] = {}
        mvp_count = Counter()
        roty_count = Counter()
        gold_glove_count = Counter()
        triple_crown_count = Counter()
        career_hr = Counter()
        ws_rings = Counter()

        with zipfile.ZipFile(ZIP_PATH) as zf:
            for row in open_csv(zf, "lahman_1871-2025_csv/People.csv"):
                player_id = row["playerID"]
                if player_id in players:
                    birth_country[player_id] = (row.get("birthCountry") or "").strip()

            for row in open_csv(zf, "lahman_1871-2025_csv/Batting.csv"):
                player_id = row["playerID"]
                if player_id in players:
                    career_hr[player_id] += int(row.get("HR") or 0)

            for row in open_csv(zf, "lahman_1871-2025_csv/AwardsPlayers.csv"):
                player_id = row["playerID"]
                if player_id not in players:
                    continue
                award = (row.get("awardID") or "").strip()
                if award == "Most Valuable Player":
                    mvp_count[player_id] += 1
                elif award == "Rookie of the Year":
                    roty_count[player_id] += 1
                elif award == "Gold Glove":
                    gold_glove_count[player_id] += 1
                elif award == "Triple Crown":
                    triple_crown_count[player_id] += 1

            champions: set[tuple[str, int]] = set()
            for row in open_csv(zf, "lahman_1871-2025_csv/Teams.csv"):
                season = int(row.get("yearID") or 0)
                if season >= START_YEAR and (row.get("WSWin") or "") == "Y":
                    champions.add((row["teamID"], season))

        for player_id, team_id, season in appearance_rows:
            if (team_id, season) in champions:
                ws_rings[player_id] += 1

        rows = []
        for player_id in sorted(players):
            country = birth_country.get(player_id, "")
            is_canadian = country in {"Canada", "CAN"}
            is_japanese = country in {"Japan", "JPN"}
            is_cuban = country in {"Cuba", "CUB"}
            team_count = len(team_counts.get(player_id, set()))
            franchise_count = len(franchise_counts.get(player_id, set()))
            seasons = len(season_counts.get(player_id, set()))
            hound_dog_eligible = franchise_count == 1 and seasons >= 10
            journeyman_eligible = team_count >= 7
            rows.append(
                (
                    player_id,
                    country or None,
                    is_japanese,
                    is_cuban,
                    is_canadian,
                    int(mvp_count[player_id]),
                    int(roty_count[player_id]),
                    int(gold_glove_count[player_id]),
                    int(triple_crown_count[player_id]),
                    int(career_hr[player_id]),
                    int(ws_rings[player_id]),
                    team_count,
                    franchise_count,
                    seasons,
                    hound_dog_eligible,
                    journeyman_eligible,
                )
            )

        with conn.transaction():
            for start in range(0, len(rows), 2000):
                chunk = rows[start:start + 2000]
                values = ",".join(["(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"] * len(chunk))
                params = [value for row in chunk for value in row]
                conn.execute(
                    f"""INSERT INTO player_playoff_traits (
                           player_id, birth_country, is_japanese, is_cuban, is_canadian,
                           mvp_count, roty_count, gold_glove_count, triple_crown_count,
                           career_hr, world_series_rings, team_count, franchise_count,
                           season_count, hound_dog_eligible, journeyman_eligible
                       ) VALUES {values}""",
                    params,
                )

        summary = conn.execute(
            """SELECT
                   COUNT(*) FILTER (WHERE is_japanese),
                   COUNT(*) FILTER (WHERE is_cuban),
                   COUNT(*) FILTER (WHERE is_canadian),
                   COUNT(*) FILTER (WHERE mvp_count > 0),
                   COUNT(*) FILTER (WHERE roty_count > 0),
                   COUNT(*) FILTER (WHERE gold_glove_count > 0),
                   COUNT(*) FILTER (WHERE triple_crown_count > 0),
                   COUNT(*) FILTER (WHERE career_hr >= 500),
                   COUNT(*) FILTER (WHERE hound_dog_eligible),
                   COUNT(*) FILTER (WHERE journeyman_eligible)
                 FROM player_playoff_traits"""
        ).fetchone()
        labels = [
            "japanese", "cuban", "canadian", "mvp", "roty", "gold_glove",
            "triple_crown", "500_hr", "hound_dog", "journeyman",
        ]
        for label, count in zip(labels, summary):
            print(f"{label}: {count}")


if __name__ == "__main__":
    main()
