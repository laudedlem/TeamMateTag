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
    recency = max(0, min(2026, final_year) - 2015) * 90
    current = 500 if final_year >= 2024 else 0
    modern = 250 if final_year >= 2020 else 0
    return int(career_games or 0) + int(teammate_count or 0) * 4 + recency + current + modern + _photo_bonus(sport, provider)


def _recency_score_sql() -> str:
    return (
        "CASE WHEN COALESCE(p.final_year, 2000) > 2015 THEN "
        "((CASE WHEN COALESCE(p.final_year, 2000) > 2026 THEN 2026 ELSE COALESCE(p.final_year, 2000) END) - 2015) "
        "ELSE 0 END"
    )


def _choice_window(slot_index: int, total_slots: int, choices_len: int) -> int:
    if slot_index <= 2:
        return min(8, choices_len)
    if slot_index <= max(4, total_slots // 2):
        return min(28, choices_len)
    if slot_index <= total_slots - 3:
        return min(60, choices_len)
    return min(140, choices_len)


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
    recency_sql = _recency_score_sql()
    if slot == "ANY":
        return {row[0]: row[1] for row in conn.execute(f"""
            SELECT p.player_id,
                   (s.career_games + s.teammate_count * 4
                    + CASE WHEN p.final_year >= 2024 THEN 500 ELSE 0 END
                    + CASE WHEN p.final_year >= 2020 THEN 250 ELSE 0 END
                    + ({recency_sql}) * 90
                    + CASE
                        WHEN {provider_select} IN ({",".join("?" for _ in STABLE_HEADSHOT_PROVIDERS.get(sport, set())) or "NULL"}) THEN 600
                        WHEN {provider_select} IN ({",".join("?" for _ in FALLBACK_HEADSHOT_PROVIDERS)}) THEN -450
                        ELSE 0
                      END) AS quality_score
            FROM sport_players p JOIN sport_players_searchable s
              ON s.sport_id=p.sport_id AND s.player_id=p.player_id
            {headshot_join}
            WHERE p.sport_id=? AND s.career_games>=? AND p.final_year>=?
        """, (*sorted(STABLE_HEADSHOT_PROVIDERS.get(sport, set())), *sorted(FALLBACK_HEADSHOT_PROVIDERS),
              sport, career_floor, modern_final_year))}
    expected = FOOTBALL_ROLE_POSITIONS.get(slot, {slot}) if sport == "football" else {slot}
    placeholders = ",".join("?" for _ in expected)
    return {row[0]: row[1] for row in conn.execute(
        f"""SELECT DISTINCT pp.player_id,
                    (s.career_games + s.teammate_count * 4
                     + CASE WHEN p.final_year >= 2024 THEN 500 ELSE 0 END
                     + CASE WHEN p.final_year >= 2020 THEN 250 ELSE 0 END
                     + ({recency_sql}) * 90
                     + CASE
                         WHEN {provider_select} IN ({",".join("?" for _ in STABLE_HEADSHOT_PROVIDERS.get(sport, set())) or "NULL"}) THEN 600
                         WHEN {provider_select} IN ({",".join("?" for _ in FALLBACK_HEADSHOT_PROVIDERS)}) THEN -450
                         ELSE 0
                       END) AS quality_score
             FROM sport_player_positions pp
             JOIN sport_players p ON p.sport_id=pp.sport_id AND p.player_id=pp.player_id
             JOIN sport_players_searchable s ON s.sport_id=pp.sport_id AND s.player_id=pp.player_id
             {headshot_join}
             WHERE pp.sport_id=? AND pp.position IN ({placeholders})
               AND s.career_games>=? AND p.final_year>=?""",
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
        if (candidate in eligible and candidate not in used_players
                and (team_id, season) not in used_links):
            by_candidate.setdefault(candidate, []).append((team_id, season))
    # A single shared team-year makes a clean daily puzzle. Retain broader
    # overlaps as a fallback so position-constrained lineups remain feasible.
    unique = [(candidate, links[0]) for candidate, links in by_candidate.items() if len(links) == 1]
    if unique:
        return sorted(unique, key=lambda item: eligible[item[0]], reverse=True)
    return sorted(
        [(candidate, link) for candidate, links in by_candidate.items() for link in links],
        key=lambda item: eligible[item[0]],
        reverse=True,
    )


def generate(conn: sqlite3.Connection, sport: str, puzzle_day: date | None = None,
             attempts: int = 300, unit: str | None = None) -> GeneratedPuzzle:
    if sport not in LINEUP_SLOTS:
        raise ValueError(f"unsupported sport {sport!r}")
    puzzle_day = puzzle_day or date.today()
    slots = lineup_slots(sport, unit)
    pools = {slot: _eligible(conn, sport, slot) for slot in set(slots)}
    missing = [slot for slot in set(slots) if not pools[slot]]
    if missing:
        raise ValueError(f"{sport} is missing exact position data for: {', '.join(sorted(missing))}")
    rng = random.Random(f"{sport}:{puzzle_day.isoformat()}")

    for _ in range(attempts):
        starters = sorted(pools[slots[0]], key=pools[slots[0]].get, reverse=True)
        deck = [rng.choice(starters[:min(5, len(starters))])]
        links: list[tuple[str, int]] = []
        used_players, used_links = {deck[0]}, set()
        failed = False
        for slot_index, slot in enumerate(slots[1:], 1):
            choices = _candidate_links(conn, sport, deck[-1], pools[slot], used_players, used_links)
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
