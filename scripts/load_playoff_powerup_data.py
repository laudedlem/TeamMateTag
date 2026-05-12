from __future__ import annotations

import csv
import io
import os
import zipfile
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
        team_rows = conn.execute("SELECT team_id, season, franchise_id FROM teams").fetchall()
        team_map = {(team_id, season): franchise_id for team_id, season, franchise_id in team_rows}
        appearance_rows = conn.execute(
            "SELECT player_id, team_id, season FROM appearances WHERE season >= %s",
            (START_YEAR,),
        ).fetchall()
        appearances_by_player_year: dict[tuple[str, int], set[str]] = {}
        for player_id, team_id, season in appearance_rows:
            appearances_by_player_year.setdefault((player_id, season), set()).add(team_id)

        conn.execute(
            """CREATE TABLE IF NOT EXISTS player_powerup_qualifications (
                   player_id TEXT NOT NULL REFERENCES players(player_id),
                   powerup_key TEXT NOT NULL,
                   franchise_id TEXT NOT NULL REFERENCES franchises(franchise_id),
                   team_id TEXT NOT NULL,
                   season INTEGER NOT NULL,
                   PRIMARY KEY (player_id, powerup_key, team_id, season)
               )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ppq_lookup "
            "ON player_powerup_qualifications(powerup_key, franchise_id, player_id)"
        )
        conn.execute("DELETE FROM player_powerup_qualifications")

        rows = []
        with zipfile.ZipFile(ZIP_PATH) as zf:
            for row in open_csv(zf, "lahman_1871-2025_csv/Batting.csv"):
                year = int(row["yearID"] or 0)
                hr = int(row["HR"] or 0)
                key = (row["teamID"], year)
                franchise_id = team_map.get(key)
                if year >= START_YEAR and hr >= 40 and franchise_id:
                    rows.append((row["playerID"], "bubblegum", franchise_id, row["teamID"], year))

            for row in open_csv(zf, "lahman_1871-2025_csv/Pitching.csv"):
                year = int(row["yearID"] or 0)
                strikeouts = int(row["SO"] or 0)
                key = (row["teamID"], year)
                franchise_id = team_map.get(key)
                if year >= START_YEAR and strikeouts >= 200 and franchise_id:
                    rows.append((row["playerID"], "pine_tar", franchise_id, row["teamID"], year))

            for row in open_csv(zf, "lahman_1871-2025_csv/AwardsPlayers.csv"):
                year = int(row["yearID"] or 0)
                award = (row["awardID"] or "").strip()
                team_ids = appearances_by_player_year.get((row["playerID"], year), set())
                if year < START_YEAR or not team_ids:
                    continue
                powerup_key = None
                if award == "Silver Slugger":
                    powerup_key = "bat_donut"
                elif award == "Gold Glove":
                    powerup_key = "backup_mitt"
                if not powerup_key:
                    continue
                for team_id in team_ids:
                    franchise_id = team_map.get((team_id, year))
                    if franchise_id:
                        rows.append((row["playerID"], powerup_key, franchise_id, team_id, year))

            seen_allstar = set()
            for row in open_csv(zf, "lahman_1871-2025_csv/AllstarFull.csv"):
                year = int(row["yearID"] or 0)
                team_id = row["teamID"]
                key = (team_id, year)
                franchise_id = team_map.get(key)
                dedupe = (row["playerID"], team_id, year)
                if year >= START_YEAR and franchise_id and dedupe not in seen_allstar:
                    seen_allstar.add(dedupe)
                    rows.append((row["playerID"], "sunglasses", franchise_id, team_id, year))

        unique_rows = sorted(set(rows))
        with conn.transaction():
            for chunk_start in range(0, len(unique_rows), 5000):
                chunk = unique_rows[chunk_start:chunk_start + 5000]
                values = ",".join(["(%s,%s,%s,%s,%s)"] * len(chunk))
                params = [value for row in chunk for value in row]
                conn.execute(
                    f"""INSERT INTO player_powerup_qualifications
                           (player_id, powerup_key, franchise_id, team_id, season)
                        VALUES {values}""",
                    params,
                )

        counts = conn.execute(
            """SELECT powerup_key, COUNT(*)
                 FROM player_powerup_qualifications
                GROUP BY powerup_key
                ORDER BY powerup_key"""
        ).fetchall()
        for key, count in counts:
            print(f"{key}: {count}")


if __name__ == "__main__":
    main()
