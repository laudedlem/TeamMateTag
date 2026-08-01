"""Resolve historical NHL source rows through verified Hockey-Reference IDs."""
from __future__ import annotations

import sqlite3

from build_local_sports_dataset import DEFAULT_DB, key
from reconcile_local_identities import reference_key


# Verified from Hockey-Reference's player-search redirects. These are stable
# Sports Reference identifiers, retained with their direct player-page URLs.
PLAYERS = {
    "Clarence MacKenzie": (1932, "mackecl01", "RW"),
    "Evgeny Namestnikov": (1993, "namesjo01", "D"),
    "Frank Breault": (1990, "breaufr01", "RW"),
    "Gord Turlik": (1959, "turligo01", "LW/C"),
    "Grigorijs Panteļejevs": (1992, "pantegr01", "LW"),
    "Harijs Vītoliņš": (1993, "vitolha01", "C"),
    "Jimmy Herbert": (1924, "herbeji01", "C/RW"),
    "Jonas Røndbjerg": (2021, "rondbjo01", "RW"),
    "Jordan Smotherman": (2007, "lavaljo01", "LW"),
    "Josh Brown": (2018, "brownjo01", "D"),
    "Kaspars Astašenko": (1999, "astaska01", "D"),
    "Louis Berlinguette": (1917, "berlilo01", "LW"),
    "Mikkel Bødker": (2008, "boedkmi01", "RW"),
    "Samuel Walker": (2022, "walkesa01", "C"),
    "Viktors Ignatjevs": (1998, "ignatvi01", "D"),
    "Walter Kalbfleisch": (1933, "kalbfwa01", "D"),
}


def main() -> None:
    conn = sqlite3.connect(DEFAULT_DB)
    try:
        resolved = 0
        for name, (season, identifier, position) in PLAYERS.items():
            ref = reference_key(name, season)
            exists = conn.execute("SELECT 1 FROM source_player_references WHERE sport_id='hockey' AND source='kaggle_nhl_stat_audit' AND reference_key=?", (ref,)).fetchone()
            if not exists:
                continue
            player_id = f"hockeyref:{identifier}"
            first, _, last = name.rpartition(" ")
            conn.execute(
                """INSERT OR IGNORE INTO sport_players
                   (sport_id,player_id,external_id,display_name,first_name,last_name,debut_year,final_year,primary_pos)
                   VALUES ('hockey', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (player_id, identifier, name, first or None, last or name, season, season, position),
            )
            conn.execute("INSERT OR REPLACE INTO sport_player_external_ids VALUES ('hockey', ?, 'hockey_reference', ?)", (player_id, identifier))
            conn.execute(
                """INSERT INTO sport_players_searchable VALUES ('hockey', ?, ?, ?, ?, ?, 0, 0)
                   ON CONFLICT(sport_id,player_id) DO UPDATE SET display_name=excluded.display_name""",
                (player_id, name, f"{position}, {season}-?", key(name), key(last or name)),
            )
            url = f"https://www.hockey-reference.com/players/{identifier[0]}/{identifier}.html"
            conn.execute(
                """INSERT OR REPLACE INTO player_identity_claims
                   (sport_id,source,reference_key,player_id,status,method,confidence,evidence,reviewed_by)
                   VALUES ('hockey','kaggle_nhl_stat_audit',?,?,'accepted','hockey_reference_player_id',100,?,'sports_reference_review')""",
                (ref, player_id, f"Hockey-Reference player ID {identifier}: {url}"),
            )
            resolved += 1
        conn.commit()
    finally:
        conn.close()
    print(f"Resolved {resolved} NHL references through Hockey-Reference IDs.")


if __name__ == "__main__":
    main()
