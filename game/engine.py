"""
Engine for the base2nerdle teammate-chain game (MVP rule set v1).

Pure-Python validation and state. No I/O — wrappers (CLI, server) own
the timer, presentation, and I/O.

Rule set v1:
  - Two-player alternating turns.
  - 20-second hard-stop timer per turn; on a valid move the turn ends
    immediately and the opponent's clock resets to 20s.
  - Wrong guesses keep the clock running; unlimited attempts.
  - Each (team_id, season) accumulates strikes from successful moves.
    A team-season hits 3 strikes -> burned.
  - **Rule B**: a link between two players is INVALID if any of their
    shared team-seasons is already burned, even if other shared seasons
    aren't. When valid, ALL shared team-seasons gain +1 strike.
  - No-repeat: a player may appear at most once in the chain (the seed
    counts as already-in-chain).
"""
from __future__ import annotations

import sqlite3
import sys
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from name_normalize import normalize  # noqa: E402

STRIKES_TO_BURN = 3
TURN_SECONDS = 30.0
# Historical records stay in the dataset, but live play currently uses the
# modern era. A future historical mode can lower this without a data reload.
MIN_GAMEPLAY_SEASON = 2000


class MoveOutcome(Enum):
    VALID = "valid"
    UNKNOWN_PLAYER = "unknown_player"
    ALREADY_USED = "already_used"
    NOT_TEAMMATE = "not_teammate"
    BLOCKED_BY_BURNED = "blocked_by_burned"


@dataclass
class MoveResult:
    outcome: MoveOutcome
    player_id: str | None = None
    display_name: str | None = None
    disambiguation: str | None = None
    shared_seasons: list[tuple[str, int]] = field(default_factory=list)
    burned_seasons: list[tuple[str, int]] = field(default_factory=list)
    ambiguous_count: int = 0  # >1 if the typed name had multiple hits


@dataclass
class GameState:
    chain: list[str] = field(default_factory=list)
    chain_names: list[str] = field(default_factory=list)
    # chain_shared_with_prev[i] is the (team_id, season) list shared between
    # chain[i-1] and chain[i]. chain_shared_with_prev[0] is always [] (the seed).
    chain_shared_with_prev: list[list[tuple[str, int]]] = field(default_factory=list)
    strikes: dict[tuple[str, int], int] = field(default_factory=dict)

    @property
    def current_player_id(self) -> str:
        return self.chain[-1]

    @property
    def current_player_name(self) -> str:
        return self.chain_names[-1]

    def is_burned(self, ts: tuple[str, int]) -> bool:
        return self.strikes.get(ts, 0) >= STRIKES_TO_BURN

    def burned_team_seasons(self) -> list[tuple[str, int]]:
        return sorted(ts for ts, n in self.strikes.items() if n >= STRIKES_TO_BURN)


def find_player_by_name(conn: sqlite3.Connection, raw: str, sport: str | None = None) -> list[tuple[str, str, str, int]]:
    """Return [(player_id, display_name, disambiguation, career_games), ...]
    sorted by career_games DESC (most famous first). Empty list = no match."""
    q = normalize(raw)
    if not q:
        return []
    if sport:
        q = re.sub(r"[^a-z0-9]", "", q)
        rows = conn.execute(
            """SELECT player_id, display_name, disambiguation, career_games
                 FROM sport_players_searchable
                WHERE sport_id = ? AND search_key = ?
                  AND EXISTS (SELECT 1 FROM sport_appearances a
                              WHERE a.sport_id = sport_players_searchable.sport_id
                                AND a.player_id = sport_players_searchable.player_id
                                AND a.season >= ?)
                ORDER BY career_games DESC""",
            (sport, q, MIN_GAMEPLAY_SEASON),
        ).fetchall()
        if rows:
            return rows
        rows = conn.execute(
            """SELECT s.player_id, s.display_name, s.disambiguation, s.career_games
                 FROM sport_player_aliases a
                 JOIN sport_players_searchable s
                   ON s.sport_id = a.sport_id AND s.player_id = a.player_id
                WHERE a.sport_id = ? AND a.alias_key = ?
                  AND EXISTS (SELECT 1 FROM sport_appearances ap
                              WHERE ap.sport_id = s.sport_id
                                AND ap.player_id = s.player_id
                                AND ap.season >= ?)
                ORDER BY s.career_games DESC""",
            (sport, q, MIN_GAMEPLAY_SEASON),
        ).fetchall()
        if rows:
            return rows
        return conn.execute(
            """SELECT player_id, display_name, disambiguation, career_games
                 FROM sport_players_searchable
                WHERE sport_id = ? AND last_key = ?
                  AND EXISTS (SELECT 1 FROM sport_appearances a
                              WHERE a.sport_id = sport_players_searchable.sport_id
                                AND a.player_id = sport_players_searchable.player_id
                                AND a.season >= ?)
                ORDER BY career_games DESC""",
            (sport, q, MIN_GAMEPLAY_SEASON),
        ).fetchall()
    rows = conn.execute(
        """SELECT player_id, display_name, disambiguation, career_games
             FROM players_searchable
            WHERE search_key = ?
              AND EXISTS (SELECT 1 FROM appearances a
                          WHERE a.player_id = players_searchable.player_id
                            AND a.season >= ?)
            ORDER BY career_games DESC""",
        (q, MIN_GAMEPLAY_SEASON),
    ).fetchall()
    if rows:
        return rows
    return conn.execute(
        """SELECT DISTINCT ps.player_id, ps.display_name, ps.disambiguation, ps.career_games
             FROM nickname_search ns
             JOIN players_searchable ps ON ps.player_id = ns.player_id
            WHERE ns.nickname_key = ?
              AND EXISTS (SELECT 1 FROM appearances a
                          WHERE a.player_id = ps.player_id AND a.season >= ?)
            ORDER BY ps.career_games DESC""",
        (q, MIN_GAMEPLAY_SEASON),
    ).fetchall()


def get_shared_seasons(
    conn: sqlite3.Connection,
    a: str,
    b: str,
    sport: str | None = None,
    min_season: int = MIN_GAMEPLAY_SEASON,
) -> list[tuple[str, int]]:
    if a == b:
        return []
    if sport:
        rows = conn.execute(
            """SELECT a.team_id, a.season
                 FROM sport_appearances a
                 JOIN sport_appearances b
                   ON b.sport_id = a.sport_id AND b.team_id = a.team_id AND b.season = a.season
                WHERE a.sport_id = ? AND a.player_id = ? AND b.player_id = ?
                  AND a.season >= ?
                  AND NOT (
                      a.sport_id = 'football' AND a.season >= 2025
                      AND EXISTS (
                          SELECT 1 FROM sport_players pa
                           WHERE pa.sport_id = a.sport_id AND pa.player_id = a.player_id
                             AND pa.debut_year <= a.season - 4
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM sport_appearances prior_a
                           WHERE prior_a.sport_id = a.sport_id AND prior_a.player_id = a.player_id
                             AND prior_a.season BETWEEN a.season - 2 AND a.season - 1
                      )
                  )
                  AND NOT (
                      b.sport_id = 'football' AND b.season >= 2025
                      AND EXISTS (
                          SELECT 1 FROM sport_players pb
                           WHERE pb.sport_id = b.sport_id AND pb.player_id = b.player_id
                             AND pb.debut_year <= b.season - 4
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM sport_appearances prior_b
                           WHERE prior_b.sport_id = b.sport_id AND prior_b.player_id = b.player_id
                             AND prior_b.season BETWEEN b.season - 2 AND b.season - 1
                      )
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM sport_teammate_exclusions e
                       WHERE e.sport_id = a.sport_id
                         AND e.team_id = a.team_id
                         AND e.season = a.season
                         AND ((e.player_a_id = a.player_id AND e.player_b_id = b.player_id)
                           OR (e.player_a_id = b.player_id AND e.player_b_id = a.player_id))
                  )
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
                ORDER BY a.season, a.team_id""",
            (sport, a, b, min_season),
        ).fetchall()
        return [(t, s) for t, s in rows]
    rows = conn.execute(
        """SELECT a.team_id, a.season
             FROM appearances a
             JOIN appearances b
               ON b.team_id = a.team_id AND b.season = a.season
            WHERE a.player_id = ? AND b.player_id = ? AND a.season >= ?
              AND NOT EXISTS (
                  SELECT 1 FROM teammate_exclusions e
                   WHERE e.team_id = a.team_id
                     AND e.season = a.season
                     AND ((e.player_a_id = a.player_id AND e.player_b_id = b.player_id)
                       OR (e.player_a_id = b.player_id AND e.player_b_id = a.player_id))
              )
              AND (
                  NOT EXISTS (
                      SELECT 1 FROM teammate_stint_coverage c
                       WHERE c.season = a.season
                         AND c.strict <> 0
                  )
                  OR EXISTS (
                      SELECT 1
                        FROM player_stints sa
                        JOIN player_stints sb
                          ON sb.team_id = sa.team_id
                         AND sb.season = sa.season
                       WHERE sa.player_id = a.player_id
                         AND sb.player_id = b.player_id
                         AND sa.team_id = a.team_id
                         AND sa.season = a.season
                         AND sa.first_unit <= sb.last_unit
                         AND sb.first_unit <= sa.last_unit
                  )
              )
            ORDER BY a.season, a.team_id""",
        (a, b, min_season),
    ).fetchall()
    return [(t, s) for t, s in rows]


def validate_and_apply_move(
    state: GameState,
    conn: sqlite3.Connection,
    raw_input: str | None = None,
    *,
    player_id: str | None = None,
    track_strikes: bool = True,
    sport: str | None = None,
) -> MoveResult:
    """Resolve the candidate (by typed text or known id), validate, mutate
    state on success. Pass exactly one of raw_input or player_id.

    track_strikes=True (default): full multiplayer rules. Each shared
    team-season takes a +1 strike on a valid move; Rule B blocks links
    where any shared season is already burned (3 strikes).

    track_strikes=False: Batting Practice mode. No strikes accumulate,
    Rule B is skipped, BLOCKED_BY_BURNED is never returned. Use for
    solo endless-chain play where the only failure mode is naming a
    non-teammate, an already-used player, or an unknown name."""
    if (raw_input is None) == (player_id is None):
        raise ValueError("pass exactly one of raw_input or player_id")

    if raw_input is not None:
        matches = find_player_by_name(conn, raw_input, sport=sport)
        if not matches:
            return MoveResult(MoveOutcome.UNKNOWN_PLAYER)
        player_id, display_name, disambiguation, _ = matches[0]
        ambiguous_count = len(matches)
    else:
        if sport:
            row = conn.execute(
                "SELECT display_name, disambiguation FROM sport_players_searchable WHERE sport_id = ? AND player_id = ?",
                (sport, player_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT display_name, disambiguation FROM players_searchable WHERE player_id = ?",
                (player_id,),
            ).fetchone()
        if not row:
            return MoveResult(MoveOutcome.UNKNOWN_PLAYER)
        display_name, disambiguation = row
        ambiguous_count = 1  # explicit pick: no ambiguity to surface

    if player_id in state.chain:
        return MoveResult(
            MoveOutcome.ALREADY_USED,
            player_id=player_id,
            display_name=display_name,
            disambiguation=disambiguation,
            ambiguous_count=ambiguous_count,
        )

    shared = get_shared_seasons(conn, state.current_player_id, player_id, sport=sport)
    if not shared:
        return MoveResult(
            MoveOutcome.NOT_TEAMMATE,
            player_id=player_id,
            display_name=display_name,
            disambiguation=disambiguation,
            ambiguous_count=ambiguous_count,
        )

    if track_strikes:
        burned = [ts for ts in shared if state.is_burned(ts)]
        if burned:
            return MoveResult(
                MoveOutcome.BLOCKED_BY_BURNED,
                player_id=player_id,
                display_name=display_name,
                disambiguation=disambiguation,
                shared_seasons=shared,
                burned_seasons=burned,
                ambiguous_count=ambiguous_count,
            )
        for ts in shared:
            state.strikes[ts] = state.strikes.get(ts, 0) + 1

    state.chain.append(player_id)
    state.chain_names.append(display_name)
    state.chain_shared_with_prev.append(list(shared))
    return MoveResult(
        MoveOutcome.VALID,
        player_id=player_id,
        display_name=display_name,
        disambiguation=disambiguation,
        shared_seasons=shared,
        ambiguous_count=ambiguous_count,
    )


def seed_game(conn: sqlite3.Connection, player_id: str, sport: str | None = None) -> GameState:
    if sport:
        row = conn.execute(
            "SELECT display_name FROM sport_players_searchable WHERE sport_id = ? AND player_id = ?",
            (sport, player_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT display_name FROM players_searchable WHERE player_id = ?",
            (player_id,),
        ).fetchone()
    if not row:
        raise ValueError(f"seed player_id {player_id!r} not found in players_searchable")
    state = GameState()
    state.chain.append(player_id)
    state.chain_names.append(row[0])
    state.chain_shared_with_prev.append([])  # seed has no predecessor
    return state
