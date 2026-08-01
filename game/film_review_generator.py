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


LINEUP_SLOTS = {
    "baseball": ("C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH", "SP"),
    "football": ("QB", "RB", "WR", "WR", "WR", "TE", "OL", "OL", "OL", "OL", "OL", "K",
                 "DL", "DL", "DL", "DL", "LB", "LB", "LB", "CB", "CB", "S", "S", "P"),
    "hockey": ("LW", "LW", "C", "C", "RW", "RW", "D", "D", "D", "D", "G"),
    "basketball": ("PG", "PG", "SG", "SG", "SF", "SF", "PF", "PF", "C", "C", "ANY", "ANY"),
}

FOOTBALL_ROLE_POSITIONS = {
    "QB": {"QB"}, "RB": {"RB", "HB", "FB"}, "WR": {"WR"}, "TE": {"TE"},
    "OL": {"C", "G", "OG", "T", "OT", "OL"}, "K": {"K"},
    "DL": {"DE", "DT", "NT", "DL"}, "LB": {"LB", "ILB", "OLB", "EDGE"},
    "CB": {"CB"}, "S": {"S", "SS", "FS"}, "P": {"P"},
}

QUALITY_FLOORS = {
    "baseball": (250, 2000),
    "football": (32, 1990),
    # NHL roster-history rows currently count seasons rather than games.
    "hockey": (5, 1995),
    "basketball": (100, 2000),
}


@dataclass(frozen=True)
class GeneratedPuzzle:
    sport: str
    puzzle_date: str
    slots: tuple[str, ...]
    deck: tuple[str, ...]
    links: tuple[tuple[str, int], ...]


def _eligible(conn: sqlite3.Connection, sport: str, slot: str) -> dict[str, int]:
    career_floor, modern_final_year = QUALITY_FLOORS[sport]
    if slot == "ANY":
        return {row[0]: row[1] for row in conn.execute("""
            SELECT p.player_id, s.career_games
            FROM sport_players p JOIN sport_players_searchable s
              ON s.sport_id=p.sport_id AND s.player_id=p.player_id
            WHERE p.sport_id=? AND s.career_games>=? AND p.final_year>=?
        """, (sport, career_floor, modern_final_year))}
    expected = FOOTBALL_ROLE_POSITIONS.get(slot, {slot}) if sport == "football" else {slot}
    placeholders = ",".join("?" for _ in expected)
    return {row[0]: row[1] for row in conn.execute(
        f"""SELECT DISTINCT pp.player_id, s.career_games
             FROM sport_player_positions pp
             JOIN sport_players p ON p.sport_id=pp.sport_id AND p.player_id=pp.player_id
             JOIN sport_players_searchable s ON s.sport_id=pp.sport_id AND s.player_id=pp.player_id
             WHERE pp.sport_id=? AND pp.position IN ({placeholders})
               AND s.career_games>=? AND p.final_year>=?""",
        (sport, *sorted(expected), career_floor, modern_final_year))}


def _candidate_links(conn: sqlite3.Connection, sport: str, player_id: str,
                     eligible: dict[str, int], used_players: set[str],
                     used_links: set[tuple[str, int]]) -> list[tuple[str, tuple[str, int]]]:
    _career_floor, modern_final_year = QUALITY_FLOORS[sport]
    rows = conn.execute("""
        SELECT b.player_id, a.team_id, a.season
        FROM sport_appearances a
        JOIN sport_appearances b
          ON b.sport_id=a.sport_id AND b.team_id=a.team_id AND b.season=a.season
        JOIN sport_players b_player ON b_player.sport_id=b.sport_id AND b_player.player_id=b.player_id
        WHERE a.sport_id=? AND a.player_id=? AND b.player_id<>? AND a.season>=?
    """, (sport, player_id, player_id, modern_final_year))
    return [(candidate, (team_id, season)) for candidate, team_id, season in rows
            if candidate in eligible and candidate not in used_players
            and (team_id, season) not in used_links]


def generate(conn: sqlite3.Connection, sport: str, puzzle_day: date | None = None,
             attempts: int = 300) -> GeneratedPuzzle:
    if sport not in LINEUP_SLOTS:
        raise ValueError(f"unsupported sport {sport!r}")
    puzzle_day = puzzle_day or date.today()
    slots = LINEUP_SLOTS[sport]
    pools = {slot: _eligible(conn, sport, slot) for slot in set(slots)}
    missing = [slot for slot in set(slots) if not pools[slot]]
    if missing:
        raise ValueError(f"{sport} is missing exact position data for: {', '.join(sorted(missing))}")
    rng = random.Random(f"{sport}:{puzzle_day.isoformat()}")

    for _ in range(attempts):
        starters = sorted(pools[slots[0]], key=pools[slots[0]].get, reverse=True)
        deck = [rng.choice(starters[:min(500, len(starters))])]
        links: list[tuple[str, int]] = []
        used_players, used_links = {deck[0]}, set()
        failed = False
        for slot in slots[1:]:
            choices = _candidate_links(conn, sport, deck[-1], pools[slot], used_players, used_links)
            if not choices:
                failed = True
                break
            rng.shuffle(choices)
            choices.sort(key=lambda item: pools[slot][item[0]], reverse=True)
            next_player, link = rng.choice(choices[:min(40, len(choices))])
            deck.append(next_player)
            links.append(link)
            used_players.add(next_player)
            used_links.add(link)
        if not failed:
            return GeneratedPuzzle(sport, puzzle_day.isoformat(), slots, tuple(deck), tuple(links))
    raise RuntimeError(f"could not generate a {sport} puzzle after {attempts} attempts")
