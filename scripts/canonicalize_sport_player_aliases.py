"""Merge source-level duplicate sport players and seed nickname aliases.

The production game now uses 2000-present player records from several sources.
Some NHL Hockey Databank rows duplicate official NHL API rows under a formal
name or nickname variant. This script merges those HDB rows into the canonical
``nhl:<id>`` player and preserves the old name as a searchable alias.

It also populates conservative first-name nickname aliases for all non-baseball
sports. Aliases are lookup hints only; they do not merge player records.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "web"))

from name_normalize import normalize  # noqa: E402
import server  # noqa: E402


OUTPUT = ROOT / "raw" / "sport_player_alias_canonicalization.csv"
SPORTS = ("basketball", "hockey", "football")
HDB_TEAM_ALIASES = {
    "hdb:AND": "ANA",
    "hdb:CAL": "CGY",
    "hdb:CBS": "CBJ",
    "hdb:FLO": "FLA",
    "hdb:NAS": "NSH",
    "hdb:PHO": "PHX",
    "hdb:VEG": "VGK",
    "hdb:WAS": "WSH",
    "AND": "ANA",
    "CAL": "CGY",
    "CBS": "CBJ",
    "FLO": "FLA",
    "NAS": "NSH",
    "PHO": "PHX",
    "VEG": "VGK",
    "WAS": "WSH",
}

FIRST_NAME_GROUPS = [
    ("alex", "alexander", "alexandre", "aleksander"),
    ("andrei", "andrey"),
    ("anthony", "tony"),
    ("antti jussi", "anttijussi"),
    ("ben", "benjamin"),
    ("cal", "calvin"),
    ("cam", "cameron"),
    ("chris", "christopher", "kris", "kristopher"),
    ("dan", "danny", "daniel"),
    ("dave", "david"),
    ("denis", "dj", "d j"),
    ("dimitri", "dmitry"),
    ("don", "donald"),
    ("fredrik", "freddy"),
    ("jf", "j f", "jean francois"),
    ("jim", "jimmy", "james"),
    ("joe", "joey", "joseph"),
    ("jon", "john", "johnny", "jonathan", "jonathon"),
    ("ken", "kenneth"),
    ("louie", "louis"),
    ("matt", "matthew"),
    ("max", "maxime"),
    ("micheal", "michael", "mike", "mikey"),
    ("mitchell", "mitch"),
    ("nick", "nicholas", "nicklas", "niclas"),
    ("nikolai", "nikolay"),
    ("olie", "olaf"),
    ("pat", "patrick"),
    ("ray", "raymond"),
    ("rob", "robert"),
    ("sam", "sammy", "samuel"),
    ("steve", "steven", "stephen"),
    ("theo", "theoren"),
    ("toby", "tobias"),
    ("tom", "thomas"),
    ("vern", "vernon"),
    ("vinnie", "vincent", "vinny"),
]


def key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize(value))


def name_parts(display_name: str) -> tuple[str, str]:
    parts = normalize(display_name).split()
    if not parts:
        return "", ""
    suffixes = {"jr", "sr", "ii", "iii", "iv", "v"}
    if len(parts) > 1 and parts[-1] in suffixes:
        parts = parts[:-1]
    return parts[0], parts[-1]


def variant_first_names(first: str) -> set[str]:
    result = {first}
    for group in FIRST_NAME_GROUPS:
        if first in {key(item) for item in group}:
            result.update(key(item) for item in group)
    return {item for item in result if item}


def first_names_related(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return left[:1] == right[:1] or bool(variant_first_names(left) & variant_first_names(right))


def nickname_alias_keys(display_name: str) -> set[str]:
    first, last = name_parts(display_name)
    if not first or not last:
        return set()
    return {key(f"{variant} {last}") for variant in variant_first_names(first)}


def canonical_hockey_team_id(team_id: str) -> str:
    return HDB_TEAM_ALIASES.get(team_id, HDB_TEAM_ALIASES.get(team_id.replace("hdb:", ""), team_id.replace("hdb:", "")))


def hockey_alias_candidates(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT p.player_id,p.display_name,p.birth_year,p.debut_year,p.final_year,p.primary_pos,
                  COALESCE(s.career_games,0) AS career_games
             FROM sport_players p
             LEFT JOIN sport_players_searchable s
               ON s.sport_id=p.sport_id AND s.player_id=p.player_id
            WHERE p.sport_id='hockey' AND p.final_year >= 2000
              AND p.birth_year IS NOT NULL
              AND (p.player_id LIKE 'hdb:%%' OR p.player_id LIKE 'nhl:%%')"""
    ).fetchall()
    appearances = defaultdict(set)
    for player_id, team_id, season in conn.execute(
        """SELECT player_id, regexp_replace(team_id, '^hdb:', ''), season
             FROM sport_appearances
            WHERE sport_id='hockey' AND season >= 2000"""
    ):
        appearances[player_id].add((canonical_hockey_team_id(team_id), season))

    groups = defaultdict(list)
    for row in rows:
        player_id, name, birth_year, debut, final, pos, career_games = row
        first, last = name_parts(name)
        if first and last:
            groups[(birth_year, last)].append(row)

    candidates: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for _group_key, items in groups.items():
        hdb_rows = [row for row in items if row[0].startswith("hdb:")]
        nhl_rows = [row for row in items if row[0].startswith("nhl:")]
        for hdb in hdb_rows:
            for nhl in nhl_rows:
                if key(hdb[1]) == key(nhl[1]):
                    continue
                hdb_first, _ = name_parts(hdb[1])
                nhl_first, _ = name_parts(nhl[1])
                if not first_names_related(hdb_first, nhl_first):
                    continue
                overlap = sorted(appearances[hdb[0]] & appearances[nhl[0]])
                if not overlap:
                    continue
                pair = (hdb[0], nhl[0])
                if pair in seen:
                    continue
                seen.add(pair)
                candidates.append({
                    "sport": "hockey",
                    "alias_player_id": hdb[0],
                    "alias_name": hdb[1],
                    "alias_years": f"{hdb[3] or '?'}-{hdb[4] or '?'}",
                    "alias_pos": hdb[5] or "",
                    "canonical_player_id": nhl[0],
                    "canonical_name": nhl[1],
                    "canonical_years": f"{nhl[3] or '?'}-{nhl[4] or '?'}",
                    "canonical_pos": nhl[5] or "",
                    "overlap": ",".join(f"{team}:{season}" for team, season in overlap[:12]),
                    "decision": "merge",
                    "reason": "same birth year, surname, first initial, and overlapping NHL team-season",
                })
    return sorted(candidates, key=lambda row: (row["alias_name"], row["canonical_name"]))


def write_report(rows: list[dict], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "sport", "alias_player_id", "alias_name", "alias_years", "alias_pos",
        "canonical_player_id", "canonical_name", "canonical_years",
        "canonical_pos", "overlap", "decision", "reason",
    ]
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def canonical_headshot_values(cur, sport: str, alias_id: str, canonical_id: str):
    cur.execute(
        """SELECT player_id,status,source_url,fallback_url,provider,content_sha256,
                  perceptual_hash,width,height,review_note
             FROM player_headshots
            WHERE sport_id=%s AND player_id IN (%s,%s)""",
        (sport, alias_id, canonical_id),
    )
    rows = cur.fetchall()
    verified = [row for row in rows if row[1] == "verified" and row[2]]
    if verified:
        return sorted(verified, key=lambda row: (row[0] != canonical_id, row[0]))[0]
    return None


def merge_hockey_alias(cur, row: dict) -> None:
    sport = row["sport"]
    alias_id = row["alias_player_id"]
    canonical_id = row["canonical_player_id"]

    cur.execute("SELECT display_name FROM sport_players WHERE sport_id=%s AND player_id=%s", (sport, alias_id))
    alias_name = cur.fetchone()[0]
    cur.execute("SELECT display_name FROM sport_players WHERE sport_id=%s AND player_id=%s", (sport, canonical_id))
    canonical_name = cur.fetchone()[0]

    for alias_key in nickname_alias_keys(alias_name) | {key(alias_name), key(canonical_name)}:
        cur.execute(
            """INSERT INTO sport_player_aliases (sport_id, player_id, alias_key)
               VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
            (sport, canonical_id, alias_key),
        )

    cur.execute(
        """INSERT INTO sport_appearances (sport_id, player_id, team_id, season, games_total)
           SELECT sport_id, %s,
                  CASE team_id
                    WHEN 'hdb:AND' THEN 'ANA'
                    WHEN 'hdb:CAL' THEN 'CGY'
                    WHEN 'hdb:CBS' THEN 'CBJ'
                    WHEN 'hdb:FLO' THEN 'FLA'
                    WHEN 'hdb:NAS' THEN 'NSH'
                    WHEN 'hdb:PHO' THEN 'PHX'
                    WHEN 'hdb:VEG' THEN 'VGK'
                    WHEN 'hdb:WAS' THEN 'WSH'
                    ELSE regexp_replace(team_id, '^hdb:', '')
                  END AS team_id,
                  season, games_total
             FROM sport_appearances
            WHERE sport_id=%s AND player_id=%s
           ON CONFLICT (sport_id, player_id, team_id, season)
           DO UPDATE SET games_total=GREATEST(sport_appearances.games_total, EXCLUDED.games_total)""",
        (canonical_id, sport, alias_id),
    )
    cur.execute(
        """INSERT INTO sport_player_positions (sport_id, player_id, position, games)
           SELECT sport_id, %s, position, games
             FROM sport_player_positions
            WHERE sport_id=%s AND player_id=%s
           ON CONFLICT (sport_id, player_id, position)
           DO UPDATE SET games=GREATEST(sport_player_positions.games, EXCLUDED.games)""",
        (canonical_id, sport, alias_id),
    )
    cur.execute(
        """INSERT INTO sport_player_traits
           SELECT sport_id, %s, career_games, career_points, career_goals,
                  career_assists, career_touchdowns, passing_touchdowns,
                  rushing_touchdowns, receiving_touchdowns, career_sacks,
                  career_interceptions, all_star_count, mvp_count, roty_count,
                  championship_count, source, updated_at
             FROM sport_player_traits
            WHERE sport_id=%s AND player_id=%s
           ON CONFLICT (sport_id, player_id) DO UPDATE SET
             career_games=GREATEST(sport_player_traits.career_games, EXCLUDED.career_games),
             career_points=GREATEST(sport_player_traits.career_points, EXCLUDED.career_points),
             career_goals=GREATEST(sport_player_traits.career_goals, EXCLUDED.career_goals),
             career_assists=GREATEST(sport_player_traits.career_assists, EXCLUDED.career_assists),
             career_touchdowns=GREATEST(sport_player_traits.career_touchdowns, EXCLUDED.career_touchdowns),
             passing_touchdowns=GREATEST(sport_player_traits.passing_touchdowns, EXCLUDED.passing_touchdowns),
             rushing_touchdowns=GREATEST(sport_player_traits.rushing_touchdowns, EXCLUDED.rushing_touchdowns),
             receiving_touchdowns=GREATEST(sport_player_traits.receiving_touchdowns, EXCLUDED.receiving_touchdowns),
             career_sacks=GREATEST(sport_player_traits.career_sacks, EXCLUDED.career_sacks),
             career_interceptions=GREATEST(sport_player_traits.career_interceptions, EXCLUDED.career_interceptions),
             all_star_count=GREATEST(sport_player_traits.all_star_count, EXCLUDED.all_star_count),
             mvp_count=GREATEST(sport_player_traits.mvp_count, EXCLUDED.mvp_count),
             roty_count=GREATEST(sport_player_traits.roty_count, EXCLUDED.roty_count),
             championship_count=GREATEST(sport_player_traits.championship_count, EXCLUDED.championship_count),
             updated_at=now()""",
        (canonical_id, sport, alias_id),
    )
    cur.execute(
        """INSERT INTO sport_player_season_traits
           SELECT sport_id, %s, season, games, points, goals, assists,
                  touchdowns, passing_touchdowns, rushing_touchdowns,
                  receiving_touchdowns, sacks, interceptions, source
             FROM sport_player_season_traits
            WHERE sport_id=%s AND player_id=%s
           ON CONFLICT (sport_id, player_id, season) DO UPDATE SET
             games=GREATEST(sport_player_season_traits.games, EXCLUDED.games),
             points=GREATEST(sport_player_season_traits.points, EXCLUDED.points),
             goals=GREATEST(sport_player_season_traits.goals, EXCLUDED.goals),
             assists=GREATEST(sport_player_season_traits.assists, EXCLUDED.assists),
             touchdowns=GREATEST(sport_player_season_traits.touchdowns, EXCLUDED.touchdowns),
             passing_touchdowns=GREATEST(sport_player_season_traits.passing_touchdowns, EXCLUDED.passing_touchdowns),
             rushing_touchdowns=GREATEST(sport_player_season_traits.rushing_touchdowns, EXCLUDED.rushing_touchdowns),
             receiving_touchdowns=GREATEST(sport_player_season_traits.receiving_touchdowns, EXCLUDED.receiving_touchdowns),
             sacks=GREATEST(sport_player_season_traits.sacks, EXCLUDED.sacks),
             interceptions=GREATEST(sport_player_season_traits.interceptions, EXCLUDED.interceptions)""",
        (canonical_id, sport, alias_id),
    )
    cur.execute(
        """INSERT INTO sport_player_images (sport_id, player_id, source_url, content_type)
           SELECT sport_id, %s, source_url, content_type
             FROM sport_player_images
            WHERE sport_id=%s AND player_id=%s
           ON CONFLICT DO NOTHING""",
        (canonical_id, sport, alias_id),
    )
    cur.execute(
        """INSERT INTO sport_player_usage (sport_id, player_id, total_count, bp_count, dr_count, last_used_at)
           SELECT sport_id, %s, total_count, bp_count, dr_count, last_used_at
             FROM sport_player_usage
            WHERE sport_id=%s AND player_id=%s
           ON CONFLICT (sport_id, player_id) DO UPDATE SET
             total_count=sport_player_usage.total_count + EXCLUDED.total_count,
             bp_count=sport_player_usage.bp_count + EXCLUDED.bp_count,
             dr_count=sport_player_usage.dr_count + EXCLUDED.dr_count,
             last_used_at=GREATEST(sport_player_usage.last_used_at, EXCLUDED.last_used_at)""",
        (canonical_id, sport, alias_id),
    )
    cur.execute(
        """INSERT INTO player_headshot_source_attempts (sport_id, player_id, provider, status, source_url, checked_at)
           SELECT sport_id, %s, provider, status, source_url, checked_at
             FROM player_headshot_source_attempts
            WHERE sport_id=%s AND player_id=%s
           ON CONFLICT DO NOTHING""",
        (canonical_id, sport, alias_id),
    )
    cur.execute(
        """UPDATE manager_daily_starters
              SET player_id=%s
            WHERE sport_id=%s AND player_id=%s
              AND NOT EXISTS (
                SELECT 1 FROM manager_daily_starters other
                 WHERE other.sport_id=manager_daily_starters.sport_id
                   AND other.starter_date=manager_daily_starters.starter_date
                   AND other.player_id=%s)""",
        (canonical_id, sport, alias_id, canonical_id),
    )

    headshot = canonical_headshot_values(cur, sport, alias_id, canonical_id)
    if headshot:
        _, _status, source_url, fallback_url, provider, digest, phash, width, height, note = headshot
        cur.execute(
            """UPDATE player_headshots
                  SET source_url=%s,fallback_url=%s,provider=%s,status='verified',
                      content_sha256=%s,perceptual_hash=%s,width=%s,height=%s,
                      review_note=%s,reviewed_at=now()
                WHERE sport_id=%s AND player_id=%s""",
            (
                source_url, fallback_url, provider, digest, phash, width, height,
                f"Canonical player retained verified image during merge from {alias_id}. {note or ''}"[:1000],
                sport, canonical_id,
            ),
        )

    for table in (
        "sport_appearances", "sport_player_aliases", "sport_player_images",
        "sport_player_positions", "sport_player_season_traits",
        "sport_player_traits", "sport_players_searchable",
        "player_headshots", "player_headshot_source_attempts",
        "sport_player_usage",
    ):
        cur.execute(f"DELETE FROM {table} WHERE sport_id=%s AND player_id=%s", (sport, alias_id))
    cur.execute("DELETE FROM sport_teammates WHERE sport_id=%s AND (player_a_id=%s OR player_b_id=%s)", (sport, alias_id, alias_id))
    cur.execute("DELETE FROM manager_daily_starters WHERE sport_id=%s AND player_id=%s", (sport, alias_id))
    cur.execute("DELETE FROM sport_players WHERE sport_id=%s AND player_id=%s", (sport, alias_id))


def normalize_hockey_source_team_ids(cur) -> int:
    total = 0
    for source_id, target_id in sorted(HDB_TEAM_ALIASES.items()):
        if not source_id.startswith("hdb:"):
            continue
        cur.execute(
            """INSERT INTO sport_appearances (sport_id, player_id, team_id, season, games_total)
               SELECT sport_id, player_id, %s, season, games_total
                 FROM sport_appearances
                WHERE sport_id='hockey' AND team_id=%s
               ON CONFLICT (sport_id, player_id, team_id, season)
               DO UPDATE SET games_total=GREATEST(sport_appearances.games_total, EXCLUDED.games_total)""",
            (target_id, source_id),
        )
        total += cur.rowcount
        cur.execute("DELETE FROM sport_appearances WHERE sport_id='hockey' AND team_id=%s", (source_id,))
    return total


def refresh_search_rows(cur) -> None:
    cur.execute(
        """UPDATE sport_players p
              SET debut_year=stats.debut_year,
                  final_year=stats.final_year
             FROM (
               SELECT sport_id, player_id, MIN(season) AS debut_year, MAX(season) AS final_year
                 FROM sport_appearances
                WHERE season >= 2000
                GROUP BY sport_id, player_id
             ) stats
            WHERE p.sport_id=stats.sport_id AND p.player_id=stats.player_id
              AND p.sport_id IN ('basketball','hockey','football')"""
    )
    cur.execute(
        """UPDATE sport_players_searchable s
              SET display_name=p.display_name,
                  disambiguation=concat(coalesce(p.primary_pos,'?'), ', ', coalesce(p.debut_year::text,'?'), '-', coalesce(p.final_year::text,'?')),
                  career_games=COALESCE(stats.games, s.career_games)
             FROM sport_players p
             LEFT JOIN (
               SELECT sport_id, player_id, SUM(games_total) AS games
                 FROM sport_appearances
                WHERE season >= 2000
                GROUP BY sport_id, player_id
             ) stats ON stats.sport_id=p.sport_id AND stats.player_id=p.player_id
            WHERE s.sport_id=p.sport_id AND s.player_id=p.player_id
              AND s.sport_id IN ('basketball','hockey','football')"""
    )


def populate_name_aliases(cur) -> int:
    cur.execute(
        """SELECT p.sport_id,p.player_id,p.display_name,s.search_key
             FROM sport_players p
             JOIN sport_players_searchable s ON s.sport_id=p.sport_id AND s.player_id=p.player_id
            WHERE p.sport_id IN ('basketball','hockey','football') AND p.final_year >= 2000"""
    )
    rows = cur.fetchall()
    canonical_search = defaultdict(set)
    for sport, player_id, _name, search_key in rows:
        canonical_search[(sport, search_key)].add(player_id)

    aliases: list[tuple[str, str, str]] = []
    for sport, player_id, name, search_key in rows:
        for alias_key in nickname_alias_keys(name):
            if not alias_key or alias_key == search_key:
                continue
            owners = canonical_search.get((sport, alias_key), set())
            if owners and owners != {player_id}:
                continue
            aliases.append((sport, player_id, alias_key))
    cur.executemany(
        """INSERT INTO sport_player_aliases (sport_id, player_id, alias_key)
           VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
        aliases,
    )
    return len(aliases)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write database changes")
    parser.add_argument("--report", type=Path, default=OUTPUT)
    args = parser.parse_args()

    with server.db() as conn:
        candidates = hockey_alias_candidates(conn)
        write_report(candidates, args.report)
        if not args.apply:
            print(f"Dry run: wrote {len(candidates):,} NHL merge candidates to {args.report}")
            print("Run again with --apply to merge and seed aliases.")
            return
        with conn.cursor() as cur:
            normalized_teams = normalize_hockey_source_team_ids(cur)
            for row in candidates:
                if row["decision"] == "merge":
                    merge_hockey_alias(cur, row)
            refresh_search_rows(cur)
            alias_count = populate_name_aliases(cur)
    print(f"Normalized {normalized_teams:,} HockeyDB team-code appearances.")
    print(f"Merged {len(candidates):,} NHL source aliases and attempted {alias_count:,} cross-sport nickname aliases.")
    print(f"Wrote review report to {args.report}")


if __name__ == "__main__":
    main()
