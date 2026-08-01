"""Resolve the residual NFL honors queue with reviewed Pro Football Reference IDs."""
from __future__ import annotations

import sqlite3

from build_local_sports_dataset import DEFAULT_DB, key
from reconcile_local_identities import reference_key


# Canonical local player IDs are checked against PFR's player identifier,
# position, and career window. PFR disambiguates same-name players.
RULES = {
    "Joe Thomas": ("nfl:00-0025390", "ThomJo28", "T", 2007, 2017),
    "Michael Lewis": ("nfl:00-0021185", "LewiMi00", "SS", 2002, 2010),
    "Reggie White": ("nfl:00-0017567", "WhitRe00", "DE", 1985, 2000),
    "Dennis Smith": ("nfl:00-0015146", "SmitDe01", "DB", 1981, 1994),
    "Doug Smith": ("nfl:dougsmith:1959-06-13", "SmitDo20", "DT", 1985, 1992),
    "Keith Bishop": ("nfl:keithbishop:1957-03-10", "BishKe00", "G-C", 1980, 1989),
    "Gary Anderson": ("nfl:00-0000313", "AndeGa00", "K", 1982, 2004),
    "Mark Murphy": ("nfl:markmurphy:1955-07-13", "MurpMa20", "DB", 1977, 1984),
    "Fred Dean": ("nfl:freddean:1952-02-24", "DeanFr00", "DE", 1975, 1985),
    "Gary Johnson": ("nfl:garyjohnson:1952-08-31", "JohnGa00", "DT", 1975, 1985),
    "Bob Brown": ("nfl:bobbrown:1941-12-08", "BrowBo03", "T", 1964, 1973),
    "Jim Norton": ("nfl:jimnorton:1938-10-20", "NortJi00", "DB-P", 1960, 1968),
}


def main() -> None:
    conn = sqlite3.connect(DEFAULT_DB)
    try:
        resolved = 0
        for name, (player_id, pfr, position, debut, final) in RULES.items():
            if not conn.execute("SELECT 1 FROM sport_players WHERE sport_id='football' AND player_id=?", (player_id,)).fetchone():
                continue
            rows = conn.execute(
                """SELECT source,reference_key,season FROM source_player_references r WHERE sport_id='football' AND source IN ('wikipedia_nfl_honors','wikipedia_nfl_all_pro')
                   AND source_name=? AND NOT EXISTS (SELECT 1 FROM player_identity_claims c WHERE c.sport_id=r.sport_id AND c.source=r.source AND c.reference_key=r.reference_key AND c.status='accepted')""",
                (name,),
            ).fetchall()
            for source, ref, season in rows:
                if not debut <= season <= final:
                    continue
                url = f"https://www.pro-football-reference.com/players/{pfr[0]}/{pfr}.htm"
                conn.execute("INSERT OR IGNORE INTO sport_player_external_ids VALUES ('football', ?, 'pro_football_reference', ?)", (player_id, pfr))
                conn.execute(
                    """INSERT OR REPLACE INTO player_identity_claims
                       (sport_id,source,reference_key,player_id,status,method,confidence,evidence,reviewed_by)
                       VALUES ('football',?,?,?,'accepted','pro_football_reference_player_id',100,?,'sports_reference_review')""",
                    (source, ref, player_id, f"Pro Football Reference ID {pfr}; {position}, {debut}-{final}: {url}"),
                )
                for honor, fact_season, source_url in conn.execute("SELECT fact_type,season,source_url FROM source_fact_observations WHERE sport_id='football' AND source=? AND reference_key=?", (source, ref)):
                    conn.execute("INSERT OR REPLACE INTO sport_honors VALUES ('football', ?, ?, ?, ?, ?, ?)", (player_id, honor, fact_season, name, source_url, source))
                    conn.execute("DELETE FROM sport_honor_unresolved WHERE sport_id='football' AND source=? AND category=? AND season=? AND source_name=?", (source, honor, fact_season, name))
                resolved += 1
        # Josh Cribbs is absent from the old local roster graph, so retain a
        # PFR-keyed canonical identity for the source facts.
        player_id, pfr = "pfr:CribJo01", "CribJo01"
        conn.execute("INSERT OR IGNORE INTO sport_players (sport_id,player_id,external_id,display_name,first_name,last_name,debut_year,final_year,primary_pos) VALUES ('football', ?, ?, 'Josh Cribbs', 'Josh', 'Cribbs', 2005, 2014, 'WR')", (player_id, pfr))
        conn.execute("INSERT OR IGNORE INTO sport_player_external_ids VALUES ('football', ?, 'pro_football_reference', ?)", (player_id, pfr))
        conn.execute("INSERT OR IGNORE INTO sport_players_searchable VALUES ('football', ?, 'Josh Cribbs', 'WR, 2005-2014', ?, ?, 0, 0)", (player_id, key('Josh Cribbs'), key('Cribbs')))
        for source, ref, season in conn.execute("SELECT source,reference_key,season FROM source_player_references WHERE sport_id='football' AND source_name='Josh Cribbs'"):
            conn.execute(
                """INSERT OR REPLACE INTO player_identity_claims
                   (sport_id,source,reference_key,player_id,status,method,confidence,evidence,reviewed_by)
                   VALUES ('football',?,?,?,'accepted','pro_football_reference_player_id',100,
                           'Pro Football Reference ID CribJo01, WR, 2005-2014.','sports_reference_review')""",
                (source, ref, player_id),
            )
            resolved += 1
        conn.commit()
    finally:
        conn.close()
    print(f"Resolved {resolved} NFL references through Pro Football Reference IDs.")


if __name__ == '__main__':
    main()
