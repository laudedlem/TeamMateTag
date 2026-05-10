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
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from name_normalize import normalize  # noqa: E402

STRIKES_TO_BURN = 3
TURN_SECONDS = 30.0


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


def find_player_by_name(conn: sqlite3.Connection, raw: str) -> list[tuple[str, str, str, int]]:
    """Return [(player_id, display_name, disambiguation, career_games), ...]
    sorted by career_games DESC (most famous first). Empty list = no match."""
    q = normalize(raw)
    if not q:
        return []
    rows = conn.execute(
        """SELECT player_id, display_name, disambiguation, career_games
             FROM players_searchable
            WHERE search_key = ?
            ORDER BY career_games DESC""",
        (q,),
    ).fetchall()
    if rows:
        return rows
    return conn.execute(
        """SELECT DISTINCT ps.player_id, ps.display_name, ps.disambiguation, ps.career_games
             FROM nickname_search ns
             JOIN players_searchable ps ON ps.player_id = ns.player_id
            WHERE ns.nickname_key = ?
            ORDER BY ps.career_games DESC""",
        (q,),
    ).fetchall()


def get_shared_seasons(conn: sqlite3.Connection, a: str, b: str) -> list[tuple[str, int]]:
    if a == b:
        return []
    a, b = sorted([a, b])
    rows = conn.execute(
        """SELECT team_id, season FROM teammates
            WHERE player_a_id = ? AND player_b_id = ?
            ORDER BY season, team_id""",
        (a, b),
    ).fetchall()
    return [(t, s) for t, s in rows]


def validate_and_apply_move(
    state: GameState,
    conn: sqlite3.Connection,
    raw_input: str | None = None,
    *,
    player_id: str | None = None,
    track_strikes: bool = True,
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
        matches = find_player_by_name(conn, raw_input)
        if not matches:
            return MoveResult(MoveOutcome.UNKNOWN_PLAYER)
        player_id, display_name, disambiguation, _ = matches[0]
        ambiguous_count = len(matches)
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

    shared = get_shared_seasons(conn, state.current_player_id, player_id)
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


def seed_game(conn: sqlite3.Connection, player_id: str) -> GameState:
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
