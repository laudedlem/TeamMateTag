"""Resolve NFL honors through the season roster rows that define each player.

Award pages provide a display name and year. nflverse roster CSVs additionally
provide the player's identifier, birth date, position, and club for that year.
This supplement uses that evidence to add missing historical players to the
local graph and promote only uniquely identified source facts.
"""
from __future__ import annotations

import csv
import re
import sqlite3
from collections import Counter, defaultdict

from build_local_sports_dataset import DEFAULT_DB, ROOT, key
from name_normalize import normalize


RAW_NFL = ROOT / "raw" / "nfl"
SOURCES = ("wikipedia_nfl_honors", "wikipedia_nfl_all_pro")
MERGED_SOURCE_ARTIFACTS = {
    ("Brett FavreBarry Sanders", 1997),
    ("Buddy CurryAl Richardson", 1980),
}


def match_name(value: str) -> str:
    """Match source initials whether they are written J.C. or J. C."""
    return re.sub(r"\s+", "", normalize(value))


def identity(row: dict[str, str]) -> str:
    """Prefer source identifiers and retain a birth-date fallback for 1960s rows."""
    if row.get("pfr_id"):
        return f"pfr:{row['pfr_id']}"
    if row.get("gsis_id"):
        return f"gsis:{row['gsis_id']}"
    return f"name:{normalize(row.get('full_name') or '')}|{row.get('birth_date') or ''}"


def player_id(rows: list[dict[str, str]]) -> str:
    row = rows[0]
    if row.get("gsis_id"):
        return f"nfl:{row['gsis_id']}"
    if row.get("pfr_id"):
        return f"nfl:{row['pfr_id']}"
    return "nfl:roster:" + key(f"{row.get('full_name')}|{row.get('birth_date')}")


def promote(conn: sqlite3.Connection, source: str, reference_key: str, source_name: str, canonical_id: str) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO player_identity_claims
           (sport_id,source,reference_key,player_id,status,method,confidence,evidence,reviewed_by)
           VALUES ('football', ?, ?, ?, 'accepted', 'nflverse_roster_season_identity', 100,
                   'Unique nflverse roster player matching source name and season.', 'source_identifier')""",
        (source, reference_key, canonical_id),
    )
    for honor, season, source_url in conn.execute(
        "SELECT fact_type,season,source_url FROM source_fact_observations WHERE sport_id='football' AND source=? AND reference_key=?",
        (source, reference_key),
    ):
        conn.execute(
            "INSERT OR REPLACE INTO sport_honors VALUES ('football', ?, ?, ?, ?, ?, ?)",
            (canonical_id, honor, season, source_name, source_url, source),
        )
        conn.execute(
            "DELETE FROM sport_honor_unresolved WHERE sport_id='football' AND source=? AND category=? AND season=? AND source_name=?",
            (source, honor, season, source_name),
        )


def main() -> None:
    rows_by_identity: dict[str, list[dict[str, str]]] = defaultdict(list)
    candidates: dict[tuple[str, int], set[str]] = defaultdict(set)
    for path in RAW_NFL.rglob("*.csv"):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                try:
                    season = int(row.get("season") or "")
                except ValueError:
                    continue
                name = row.get("full_name") or ""
                if not name:
                    continue
                token = identity(row)
                rows_by_identity[token].append(row)
                candidates[(match_name(name), season)].add(token)

    conn = sqlite3.connect(DEFAULT_DB)
    try:
        placeholders = ",".join("?" for _ in SOURCES)
        refs = conn.execute(
            f"""SELECT source,reference_key,source_name,season FROM source_player_references r
                WHERE sport_id='football' AND source IN ({placeholders})
                  AND NOT EXISTS (SELECT 1 FROM player_identity_claims c WHERE c.sport_id=r.sport_id AND c.source=r.source AND c.reference_key=r.reference_key AND c.status='accepted')
                  AND NOT EXISTS (SELECT 1 FROM source_reference_dispositions d WHERE d.sport_id=r.sport_id AND d.source=r.source AND d.reference_key=r.reference_key)""",
            SOURCES,
        ).fetchall()
        resolved = artifacts = 0
        for source, ref, name, season in refs:
            if (name, season) in MERGED_SOURCE_ARTIFACTS:
                conn.execute(
                    """INSERT OR REPLACE INTO source_reference_dispositions
                       (sport_id,source,reference_key,disposition,evidence,reviewed_by)
                       VALUES ('football', ?, ?, 'source_artifact', 'Merged adjacent award-page names; no single player identity exists.', 'roster_identity_supplement')""",
                    (source, ref),
                )
                artifacts += 1
                continue
            matches = candidates.get((match_name(name), season), set())
            if len(matches) != 1:
                continue
            roster_rows = rows_by_identity[next(iter(matches))]
            canonical_id = player_id(roster_rows)
            positions = Counter(row.get("position") or "?" for row in roster_rows)
            first_year = min(int(row["season"]) for row in roster_rows)
            last_year = max(int(row["season"]) for row in roster_rows)
            display = roster_rows[0].get("full_name") or name
            first = roster_rows[0].get("first_name") or display.split()[0]
            last = roster_rows[0].get("last_name") or display.split()[-1]
            birth = (roster_rows[0].get("birth_date") or "")[:4]
            conn.execute(
                """INSERT OR IGNORE INTO sport_players
                   (sport_id,player_id,external_id,display_name,first_name,last_name,birth_year,debut_year,final_year,primary_pos)
                   VALUES ('football', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (canonical_id, roster_rows[0].get("gsis_id") or roster_rows[0].get("pfr_id"), display, first, last,
                 int(birth) if birth.isdigit() else None, first_year, last_year, positions.most_common(1)[0][0]),
            )
            pfr = roster_rows[0].get("pfr_id") or ""
            if pfr:
                conn.execute("INSERT OR IGNORE INTO sport_player_external_ids VALUES ('football', ?, 'pfr', ?)", (canonical_id, pfr))
            for row in roster_rows:
                year, team = int(row["season"]), row.get("team") or "UNK"
                team_name = conn.execute("SELECT name FROM sport_teams WHERE sport_id='football' AND team_id=? ORDER BY season DESC LIMIT 1", (team,)).fetchone()
                team_name = team_name[0] if team_name else team
                conn.execute("INSERT OR IGNORE INTO sport_franchises VALUES ('football', ?, ?)", (team, team_name))
                conn.execute("INSERT OR IGNORE INTO sport_teams VALUES ('football', ?, ?, ?, ?)", (team, year, team, team_name))
                conn.execute("INSERT OR IGNORE INTO sport_appearances VALUES ('football', ?, ?, ?, 1)", (canonical_id, team, year))
            career = conn.execute("SELECT COUNT(*) FROM sport_appearances WHERE sport_id='football' AND player_id=?", (canonical_id,)).fetchone()[0]
            conn.execute(
                """INSERT INTO sport_players_searchable VALUES ('football', ?, ?, ?, ?, ?, ?, 0)
                   ON CONFLICT(sport_id,player_id) DO UPDATE SET career_games=MAX(career_games,excluded.career_games)""",
                (canonical_id, display, f"{positions.most_common(1)[0][0]}, {first_year}-{last_year}", key(display), key(last), career),
            )
            promote(conn, source, ref, name, canonical_id)
            resolved += 1
        conn.commit()
    finally:
        conn.close()
    print(f"Resolved {resolved} NFL source references from unique nflverse roster identities; documented {artifacts} parser artifacts.")


if __name__ == "__main__":
    main()
