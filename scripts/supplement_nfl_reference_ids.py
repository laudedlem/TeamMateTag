"""Resolve NFL award-name collisions through nflverse and Wikipedia PFR IDs."""
from __future__ import annotations

import csv
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote

import requests

from build_local_sports_dataset import DEFAULT_DB, ROOT


RAW_NFL = ROOT / "raw" / "nfl"
CACHE = ROOT / "raw" / "wikipedia_pfr"
WIKI = "https://en.wikipedia.org/wiki/"
HEADERS = {"User-Agent": "TeamMateTag/0.1 data refresh (contact@teammatetag.com)"}
SOURCES = ("wikipedia_nfl_honors", "wikipedia_nfl_all_pro")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sport_player_external_ids (
  sport_id TEXT NOT NULL, player_id TEXT NOT NULL, source TEXT NOT NULL, external_id TEXT NOT NULL,
  PRIMARY KEY (sport_id, source, external_id), UNIQUE (sport_id, player_id, source)
);
"""


def cache_pfr_id(name: str) -> str | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / (re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") + ".json")
    cached = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if cached.get("pfr_id"):
        return cached["pfr_id"]

    def pfr_from_title(title: str) -> str | None:
        response = requests.get(WIKI + quote(title.replace(" ", "_")), headers=HEADERS, timeout=60)
        if not response.ok:
            return None
        match = re.search(r"pro-football-reference\.com/players/[A-Z]/([^/?#\"']+)", response.text, flags=re.I)
        return match.group(1).removesuffix(".htm") if match else None

    pfr = pfr_from_title(name)
    if not pfr:
        search = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": f"{name} NFL", "srlimit": 5, "format": "json"},
            headers=HEADERS, timeout=60,
        )
        if search.ok:
            for result in search.json().get("query", {}).get("search", []):
                pfr = pfr_from_title(result.get("title") or "")
                if pfr:
                    break
    path.write_text(json.dumps({"name": name, "pfr_id": pfr}), encoding="utf-8")
    return pfr


def main() -> None:
    conn = sqlite3.connect(DEFAULT_DB)
    try:
        conn.executescript(SCHEMA)
        inserted = 0
        for path in RAW_NFL.rglob("*.csv"):
            with path.open(encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    gsis, pfr = (row.get("gsis_id") or "").strip(), (row.get("pfr_id") or "").strip()
                    if not gsis or not pfr:
                        continue
                    player_id = f"nfl:{gsis}"
                    if conn.execute("SELECT 1 FROM sport_players WHERE sport_id='football' AND player_id=?", (player_id,)).fetchone():
                        conn.execute("INSERT OR IGNORE INTO sport_player_external_ids VALUES ('football', ?, 'pfr', ?)", (player_id, pfr))
                        inserted += 1
        pfr_players = {pfr: pid for pfr, pid in conn.execute("SELECT external_id, player_id FROM sport_player_external_ids WHERE sport_id='football' AND source='pfr'")}
        placeholders = ",".join("?" for _ in SOURCES)
        refs = conn.execute(
            f"""SELECT DISTINCT sport_id, source, reference_key, source_name, season
                FROM source_player_references r
                WHERE sport_id='football' AND source IN ({placeholders})
                  AND NOT EXISTS (SELECT 1 FROM player_identity_claims c WHERE c.sport_id=r.sport_id AND c.source=r.source AND c.reference_key=r.reference_key AND c.status='accepted')
                  AND NOT EXISTS (SELECT 1 FROM source_reference_dispositions d WHERE d.sport_id=r.sport_id AND d.source=r.source AND d.reference_key=r.reference_key)""",
            SOURCES,
        ).fetchall()
        resolved = 0
        for sport, source, ref, name, season in refs:
            pfr = cache_pfr_id(name)
            player_id = pfr_players.get(pfr or "")
            if not player_id:
                continue
            conn.execute(
                """INSERT OR REPLACE INTO player_identity_claims
                   (sport_id, source, reference_key, player_id, status, method, confidence, evidence, reviewed_by)
                   VALUES (?, ?, ?, ?, 'accepted', 'wikipedia_pfr_id', 100, ?, 'source_identifier')""",
                (sport, source, ref, player_id, f"Wikipedia player page PFR ID: {pfr}"),
            )
            facts = conn.execute(
                """SELECT fact_type, season, source_url FROM source_fact_observations
                   WHERE sport_id=? AND source=? AND reference_key=?""",
                (sport, source, ref),
            ).fetchall()
            for category, fact_season, source_url in facts:
                conn.execute(
                    """INSERT OR REPLACE INTO sport_honors
                       VALUES ('football', ?, ?, ?, ?, ?, ?)""",
                    (player_id, category, fact_season, name, source_url, source),
                )
                conn.execute(
                    """DELETE FROM sport_honor_unresolved
                       WHERE sport_id='football' AND category=? AND season=?
                         AND source_name=? AND source=?""",
                    (category, fact_season, name, source),
                )
            resolved += 1
        # Also promote verified IDs from a prior run. This makes the script
        # idempotent when code or source facts change after the claim exists.
        verified = conn.execute(
            """SELECT c.player_id, r.source, r.source_name, f.fact_type, f.season, f.source_url
                FROM player_identity_claims c
                JOIN source_player_references r ON r.sport_id=c.sport_id AND r.source=c.source AND r.reference_key=c.reference_key
                JOIN source_fact_observations f ON f.sport_id=r.sport_id AND f.source=r.source AND f.reference_key=r.reference_key
                WHERE c.sport_id='football' AND c.method='wikipedia_pfr_id' AND c.status='accepted'"""
        ).fetchall()
        for player_id, source, name, category, fact_season, source_url in verified:
            conn.execute("INSERT OR REPLACE INTO sport_honors VALUES ('football', ?, ?, ?, ?, ?, ?)", (player_id, category, fact_season, name, source_url, source))
            conn.execute("DELETE FROM sport_honor_unresolved WHERE sport_id='football' AND category=? AND season=? AND source_name=? AND source=?", (category, fact_season, name, source))
        conn.execute("""UPDATE sport_player_traits SET mvp_count=(SELECT COUNT(*) FROM sport_honors h WHERE h.sport_id='football' AND h.player_id=sport_player_traits.player_id AND h.honor='mvp'),
            roty_count=(SELECT COUNT(*) FROM sport_honors h WHERE h.sport_id='football' AND h.player_id=sport_player_traits.player_id AND h.honor IN ('offensive_roty','defensive_roty')),
            all_star_count=(SELECT COUNT(*) FROM sport_honors h WHERE h.sport_id='football' AND h.player_id=sport_player_traits.player_id AND h.honor='pro_bowl')
            WHERE sport_id='football'""")
        conn.commit()
    finally:
        conn.close()
    print(f"Indexed {inserted:,} nflverse PFR identifiers and resolved {resolved:,} award references.")


if __name__ == "__main__":
    main()
