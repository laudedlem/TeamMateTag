"""Promote NHL source facts where HockeyDB uniquely identifies the player."""
from __future__ import annotations

import csv
import sqlite3
from collections import defaultdict

from build_local_sports_dataset import DEFAULT_DB, ROOT
from name_normalize import normalize
from supplement_nhl_official_ids import SOURCE, source_profiles


MASTER = ROOT / "raw" / "hockeydb" / "Master.csv"
SCORING = ROOT / "raw" / "hockeydb" / "Scoring.csv"


def position_matches(source: set[str], hdb: str) -> bool:
    return any(
        value == hdb or value == "F" and hdb in {"C", "L", "R"} or value in {"LW", "RW"} and hdb in {"L", "R"}
        for value in source
    )


def main() -> None:
    games: dict[str, int] = defaultdict(int)
    for row in csv.DictReader(SCORING.open(encoding="utf-8-sig", newline="")):
        if row.get("lgID") == "NHL":
            games[row["playerID"]] += int(row.get("GP") or 0)
    master = list(csv.DictReader(MASTER.open(encoding="utf-8-sig", newline="")))
    profiles = source_profiles()
    conn = sqlite3.connect(DEFAULT_DB)
    try:
        external = {
            hdb: player_id for hdb, player_id in conn.execute(
                "SELECT external_id,player_id FROM sport_player_external_ids WHERE sport_id='hockey' AND source='hockeydb'"
            )
        }
        refs = conn.execute(
            """SELECT source,reference_key,source_name,season FROM source_player_references r
               WHERE sport_id='hockey' AND source=?
                 AND NOT EXISTS (SELECT 1 FROM player_identity_claims c WHERE c.sport_id=r.sport_id AND c.source=r.source AND c.reference_key=r.reference_key AND c.status='accepted')
                 AND NOT EXISTS (SELECT 1 FROM source_reference_dispositions d WHERE d.sport_id=r.sport_id AND d.source=r.source AND d.reference_key=r.reference_key)""",
            (SOURCE,),
        ).fetchall()
        resolved = 0
        for source, ref, name, season in refs:
            profile = profiles.get((normalize(name), season), {"positions": set(), "games": set()})
            matches = [
                row for row in master
                if normalize(f"{row.get('firstName') or ''} {row.get('lastName') or ''}") == normalize(name)
                and int(row.get("firstNHL") or 0) == season
                and position_matches(profile["positions"], row.get("pos") or "")
                and (not profile["games"] or games[row["playerID"]] in profile["games"])
                and row["playerID"] in external
            ]
            if len(matches) != 1:
                continue
            row = matches[0]
            player_id = external[row["playerID"]]
            conn.execute(
                """INSERT OR REPLACE INTO player_identity_claims
                   (sport_id,source,reference_key,player_id,status,method,confidence,evidence,reviewed_by)
                   VALUES ('hockey', ?, ?, ?, 'accepted', 'hockeydb_career_identity', 100, ?, 'source_identifier')""",
                (source, ref, player_id, f"HockeyDB ID {row['playerID']}; first NHL season {season}, position {row['pos']}, {games[row['playerID']]} NHL games."),
            )
            for honor, fact_season, url in conn.execute("SELECT fact_type,season,source_url FROM source_fact_observations WHERE sport_id='hockey' AND source=? AND reference_key=?", (source, ref)):
                conn.execute("INSERT OR REPLACE INTO sport_honors VALUES ('hockey', ?, ?, ?, ?, ?, ?)", (player_id, honor, fact_season, name, url, source))
                conn.execute("DELETE FROM sport_honor_unresolved WHERE sport_id='hockey' AND source=? AND category=? AND season=? AND source_name=?", (source, honor, fact_season, name))
            resolved += 1
        conn.commit()
    finally:
        conn.close()
    print(f"Resolved {resolved} NHL references through HockeyDB career identities.")


if __name__ == "__main__":
    main()
