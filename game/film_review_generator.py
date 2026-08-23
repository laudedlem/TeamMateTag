"""Deterministic local Film Review puzzle generation.

Each deck is a chain. Every adjacent pair shares a distinct team-season, and
each card fills one declared roster slot. The generator is intentionally
separate from the web API until its daily decks have been validated.
"""
from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from datetime import date

STABLE_HEADSHOT_PROVIDERS = {
    "baseball": {"MLBAM", "OOTP Facepack"},
    "basketball": {"catalog", "NBA", "BBGM community map"},
    "hockey": {"catalog", "NHL", "ESPN", "FHM Historical Photos Megapack 3.5", "FHM Facepack 24-25"},
    "football": {"ESPN", "ESPN catalog", "NFL", "nflverse"},
}

FALLBACK_HEADSHOT_PROVIDERS = {
    "Web image search", "Wikimedia Commons", "HockeyDB", "Basketball Reference",
    "Hockey Reference", "Baseball Reference", "manual_submission", "manual_upload",
    "Community roster CSV", "TheSportsDB",
}


LINEUP_SLOTS = {
    "baseball": ("C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH", "SP"),
    "football": ("QB", "RB", "WR", "WR", "WR", "TE", "OL", "OL", "OL", "OL", "OL", "K",
                 "DL", "DL", "DL", "DL", "LB", "LB", "LB", "CB", "CB", "S", "S", "P"),
    "hockey": ("LW", "LW", "C", "C", "RW", "RW", "D", "D", "D", "D", "G"),
    "basketball": ("PG", "PG", "SG", "SG", "SF", "SF", "PF", "PF", "C", "C", "ANY", "ANY"),
}

FOOTBALL_UNITS = {
    "offense": ("QB", "RB", "WR", "WR", "WR", "TE", "OL", "OL", "OL", "OL", "OL", "K"),
    "defense": ("DL", "DL", "DL", "DL", "LB", "LB", "LB", "CB", "CB", "S", "S", "P"),
}

FOOTBALL_ROLE_POSITIONS = {
    "QB": {"QB"}, "RB": {"RB", "HB", "FB"}, "WR": {"WR"}, "TE": {"TE"},
    "OL": {"C", "G", "OG", "T", "OT", "OL"}, "K": {"K"},
    "DL": {"DE", "DT", "NT", "DL"}, "LB": {"LB", "ILB", "OLB", "EDGE"},
    "CB": {"CB"}, "S": {"S", "SS", "FS"}, "P": {"P"},
}

QUALITY_FLOORS = {
    "baseball": (250, 2000),
    "football": (32, 2000),
    # NHL roster-history rows currently count seasons rather than games.
    "hockey": (5, 2000),
    # The local NBA importer stores one row per player-team-season, so its
    # career count is seasons rather than total games.
    "basketball": (5, 2000),
}


@dataclass(frozen=True)
class GeneratedPuzzle:
    sport: str
    puzzle_date: str
    slots: tuple[str, ...]
    deck: tuple[str, ...]
    links: tuple[tuple[str, int], ...]
    unit: str | None = None


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    if hasattr(conn, "_conn"):
        return bool(conn.execute("SELECT 1 FROM information_schema.tables WHERE table_name=?", (name,)).fetchone())
    try:
        return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())
    except Exception:
        return False


def _photo_bonus(sport: str, provider: str | None) -> int:
    if provider in STABLE_HEADSHOT_PROVIDERS.get(sport, set()):
        return 600
    if provider in FALLBACK_HEADSHOT_PROVIDERS:
        return -450
    return 0


def _player_score(sport: str, career_games: int, teammate_count: int, final_year: int | None, provider: str | None) -> int:
    final_year = final_year or 2000
    # Film Review should open with recognizable players.  The runtime catalog's
    # searchable career count is not equally reliable for every sport, so this
    # score receives the curated career-games value from sport_player_traits.
    recency = max(0, min(2026, final_year) - 2015) * 30
    current = 250 if final_year >= 2024 else 0
    modern = 125 if final_year >= 2020 else 0
    return int(career_games or 0) * 3 + int(teammate_count or 0) * 2 + recency + current + modern + _photo_bonus(sport, provider)


def _recency_score_sql() -> str:
    return (
        "CASE WHEN COALESCE(p.final_year, 2000) > 2015 THEN "
        "((CASE WHEN COALESCE(p.final_year, 2000) > 2026 THEN 2026 ELSE COALESCE(p.final_year, 2000) END) - 2015) "
        "ELSE 0 END"
    )


def _choice_window(slot_index: int, total_slots: int, choices_len: int) -> int:
    if slot_index == 1:
        return min(3, choices_len)
    if slot_index <= 2:
        return min(5, choices_len)
    if slot_index <= max(4, total_slots // 2):
        return min(18, choices_len)
    if slot_index <= total_slots - 3:
        return min(60, choices_len)
    return min(140, choices_len)


def _early_choice_floor(sport: str, slot_index: int) -> int:
    # The score combines real career games, a modest recent-player bonus, and
    # headshot reliability. Early cards should be familiar; later slots can
    # become more challenging without turning the opening link into a deep cut.
    if slot_index > 5:
        return 0
    early = {"baseball": 2300, "basketball": 2250, "hockey": 2300, "football": 1300}
    middle = {"baseball": 1800, "basketball": 1650, "hockey": 1950, "football": 1050}
    return (early if slot_index <= 2 else middle).get(sport, 0)


def lineup_slots(sport: str, unit: str | None = None) -> tuple[str, ...]:
    if sport == "football" and unit:
        if unit not in FOOTBALL_UNITS:
            raise ValueError("football Film Review unit must be offense or defense")
        return FOOTBALL_UNITS[unit]
    return LINEUP_SLOTS[sport]


def _eligible(conn: sqlite3.Connection, sport: str, slot: str) -> dict[str, int]:
    career_floor, modern_final_year = QUALITY_FLOORS[sport]
    has_headshots = _table_exists(conn, "player_headshots")
    headshot_join = "LEFT JOIN player_headshots h ON h.sport_id=p.sport_id AND h.player_id=p.player_id" if has_headshots else ""
    provider_select = "h.provider" if has_headshots else "NULL"
    headshot_filter = "AND h.status='verified'" if has_headshots else ""
    recency_sql = _recency_score_sql()
    if slot == "ANY":
        return {row[0]: row[1] for row in conn.execute(f"""
            SELECT p.player_id,
                   (COALESCE(t.career_games, s.career_games) * 3 + s.teammate_count * 2
                    + CASE WHEN p.final_year >= 2024 THEN 250 ELSE 0 END
                    + CASE WHEN p.final_year >= 2020 THEN 125 ELSE 0 END
                    + ({recency_sql}) * 30
                    + CASE
                        WHEN {provider_select} IN ({",".join("?" for _ in STABLE_HEADSHOT_PROVIDERS.get(sport, set())) or "NULL"}) THEN 600
                        WHEN {provider_select} IN ({",".join("?" for _ in FALLBACK_HEADSHOT_PROVIDERS)}) THEN -450
                        ELSE 0
                      END) AS quality_score
            FROM sport_players p JOIN sport_players_searchable s
              ON s.sport_id=p.sport_id AND s.player_id=p.player_id
            LEFT JOIN sport_player_traits t ON t.sport_id=p.sport_id AND t.player_id=p.player_id
            {headshot_join}
            WHERE p.sport_id=? AND s.career_games>=? AND p.final_year>=?
              {headshot_filter}
        """, (*sorted(STABLE_HEADSHOT_PROVIDERS.get(sport, set())), *sorted(FALLBACK_HEADSHOT_PROVIDERS),
              sport, career_floor, modern_final_year))}
    expected = FOOTBALL_ROLE_POSITIONS.get(slot, {slot}) if sport == "football" else {slot}
    placeholders = ",".join("?" for _ in expected)
    return {row[0]: row[1] for row in conn.execute(
        f"""SELECT DISTINCT pp.player_id,
                    (COALESCE(t.career_games, s.career_games) * 3 + s.teammate_count * 2
                     + CASE WHEN p.final_year >= 2024 THEN 250 ELSE 0 END
                     + CASE WHEN p.final_year >= 2020 THEN 125 ELSE 0 END
                     + ({recency_sql}) * 30
                     + CASE
                         WHEN {provider_select} IN ({",".join("?" for _ in STABLE_HEADSHOT_PROVIDERS.get(sport, set())) or "NULL"}) THEN 600
                         WHEN {provider_select} IN ({",".join("?" for _ in FALLBACK_HEADSHOT_PROVIDERS)}) THEN -450
                         ELSE 0
                       END) AS quality_score
             FROM sport_player_positions pp
             JOIN sport_players p ON p.sport_id=pp.sport_id AND p.player_id=pp.player_id
             JOIN sport_players_searchable s ON s.sport_id=pp.sport_id AND s.player_id=pp.player_id
             LEFT JOIN sport_player_traits t ON t.sport_id=p.sport_id AND t.player_id=p.player_id
             {headshot_join}
             WHERE pp.sport_id=? AND pp.position IN ({placeholders})
               AND s.career_games>=? AND p.final_year>=?
               {headshot_filter}""",
        (*sorted(STABLE_HEADSHOT_PROVIDERS.get(sport, set())), *sorted(FALLBACK_HEADSHOT_PROVIDERS),
         sport, *sorted(expected), career_floor, modern_final_year))}


def _candidate_links(conn: sqlite3.Connection, sport: str, player_id: str,
                     eligible: dict[str, int], used_players: set[str],
                     used_links: set[tuple[str, int]]) -> list[tuple[str, tuple[str, int]]]:
    _career_floor, modern_final_year = QUALITY_FLOORS[sport]
    exclusion_clause = ""
    if _table_exists(conn, "sport_teammate_exclusions"):
        exclusion_clause = """
          AND NOT EXISTS (
              SELECT 1 FROM sport_teammate_exclusions e
               WHERE e.sport_id=a.sport_id AND e.team_id=a.team_id AND e.season=a.season
                 AND ((e.player_a_id=a.player_id AND e.player_b_id=b.player_id)
                   OR (e.player_a_id=b.player_id AND e.player_b_id=a.player_id))
          )
        """
    rows = conn.execute(f"""
        SELECT b.player_id, a.team_id, a.season
        FROM sport_appearances a
        JOIN sport_appearances b
          ON b.sport_id=a.sport_id AND b.team_id=a.team_id AND b.season=a.season
        JOIN sport_players b_player ON b_player.sport_id=b.sport_id AND b_player.player_id=b.player_id
        WHERE a.sport_id=? AND a.player_id=? AND b.player_id<>? AND a.season>=?
          AND NOT (
              a.sport_id='football' AND a.season>=2025
              AND EXISTS (
                  SELECT 1 FROM sport_players pa
                   WHERE pa.sport_id=a.sport_id AND pa.player_id=a.player_id
                     AND pa.debut_year <= a.season - 4
              )
              AND NOT EXISTS (
                  SELECT 1 FROM sport_appearances prior_a
                   WHERE prior_a.sport_id=a.sport_id AND prior_a.player_id=a.player_id
                     AND prior_a.season BETWEEN a.season - 2 AND a.season - 1
              )
          )
          AND NOT (
              b.sport_id='football' AND b.season>=2025
              AND b_player.debut_year <= b.season - 4
              AND NOT EXISTS (
                  SELECT 1 FROM sport_appearances prior_b
                   WHERE prior_b.sport_id=b.sport_id AND prior_b.player_id=b.player_id
                     AND prior_b.season BETWEEN b.season - 2 AND b.season - 1
              )
          )
        {exclusion_clause}
          AND (
              NOT EXISTS (
                  SELECT 1 FROM sport_teammate_stint_coverage c
                   WHERE c.sport_id = a.sport_id
                     AND c.season = a.season
                     AND c.strict <> 0
              )
              OR EXISTS (
                  SELECT 1
                    FROM sport_player_stints sa
                    JOIN sport_player_stints sb
                      ON sb.sport_id = sa.sport_id
                     AND sb.team_id = sa.team_id
                     AND sb.season = sa.season
                   WHERE sa.sport_id = a.sport_id
                     AND sa.player_id = a.player_id
                     AND sb.player_id = b.player_id
                     AND sa.team_id = a.team_id
                     AND sa.season = a.season
                     AND sa.first_unit <= sb.last_unit
                     AND sb.first_unit <= sa.last_unit
              )
          )
    """, (sport, player_id, player_id, modern_final_year))
    by_candidate: dict[str, list[tuple[str, int]]] = {}
    for candidate, team_id, season in rows:
        if candidate in eligible and candidate not in used_players:
            by_candidate.setdefault(candidate, []).append((team_id, season))
    # Film Review has one answer per link. Multi-season connections belong in
    # the other modes, where naming any valid teammate season is intentional.
    unique = [
        (candidate, links[0])
        for candidate, links in by_candidate.items()
        if len(links) == 1 and links[0] not in used_links
    ]
    return sorted(unique, key=lambda item: eligible[item[0]], reverse=True)


def _pair_key(first: str, second: str) -> tuple[str, str]:
    return tuple(sorted((first, second)))


def generate(conn: sqlite3.Connection, sport: str, puzzle_day: date | None = None,
             attempts: int = 300, unit: str | None = None, seed_suffix: str = "",
             banned_opening_players: set[str] | None = None,
             banned_adjacent_pairs: set[tuple[str, str]] | None = None) -> GeneratedPuzzle:
    if sport not in LINEUP_SLOTS:
        raise ValueError(f"unsupported sport {sport!r}")
    puzzle_day = puzzle_day or date.today()
    slots = lineup_slots(sport, unit)
    pools = {slot: _eligible(conn, sport, slot) for slot in set(slots)}
    missing = [slot for slot in set(slots) if not pools[slot]]
    if missing:
        raise ValueError(f"{sport} is missing exact position data for: {', '.join(sorted(missing))}")
    # The first connection is the on-ramp to a daily puzzle. Keep both visible
    # players in the modern era where the current player base is most fluent.
    recent_players = {
        row[0] for row in conn.execute(
            "SELECT player_id FROM sport_players WHERE sport_id=? AND final_year>=?",
            (sport, 2016),
        ).fetchall()
    }
    rng = random.Random(f"{sport}:{puzzle_day.isoformat()}:{unit or ''}:{seed_suffix}")
    banned_opening_players = banned_opening_players or set()
    banned_adjacent_pairs = banned_adjacent_pairs or set()

    for _ in range(attempts):
        starters = [
            player_id
            for player_id in sorted(pools[slots[0]], key=pools[slots[0]].get, reverse=True)
            if player_id not in banned_opening_players and player_id in recent_players
        ]
        if not starters:
            starters = [
                player_id
                for player_id in sorted(pools[slots[0]], key=pools[slots[0]].get, reverse=True)
                if player_id not in banned_opening_players
            ]
        if not starters:
            starters = sorted(pools[slots[0]], key=pools[slots[0]].get, reverse=True)
        deck = [rng.choice(starters[:min(5, len(starters))])]
        links: list[tuple[str, int]] = []
        used_players, used_links = {deck[0]}, set()
        failed = False
        for slot_index, slot in enumerate(slots[1:], 1):
            choices = [
                item for item in _candidate_links(conn, sport, deck[-1], pools[slot], used_players, used_links)
                if _pair_key(deck[-1], item[0]) not in banned_adjacent_pairs
                   and not (slot_index == 1 and item[0] in banned_opening_players)
                   and not (slot_index == 1 and item[0] not in recent_players)
            ]
            preferred_floor = _early_choice_floor(sport, slot_index)
            if preferred_floor:
                preferred = [item for item in choices if pools[slot][item[0]] >= preferred_floor]
                if preferred:
                    choices = preferred
            if not choices:
                failed = True
                break
            rng.shuffle(choices)
            choices.sort(key=lambda item: pools[slot][item[0]], reverse=True)
            next_player, link = rng.choice(choices[:_choice_window(slot_index, len(slots), len(choices))])
            deck.append(next_player)
            links.append(link)
            used_players.add(next_player)
            used_links.add(link)
        if not failed:
            return GeneratedPuzzle(sport, puzzle_day.isoformat(), slots, tuple(deck), tuple(links), unit)
    raise RuntimeError(f"could not generate a {sport} puzzle after {attempts} attempts")
