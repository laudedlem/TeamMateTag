"""
CLI for base2nerdle MVP. Two humans alternate turns at the same keyboard.

Each turn is a 20-second budget; multiple typed guesses allowed within
that budget. A valid move ends the turn; the opponent gets a fresh 20s.
The game ends when a player's clock expires before they make a valid
move — the other player wins.

Run: python game/cli.py [--seed PLAYER_ID] [--p1 NAME] [--p2 NAME]
"""
from __future__ import annotations

import argparse
import queue
import sqlite3
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine import (  # noqa: E402
    STRIKES_TO_BURN,
    TURN_SECONDS,
    GameState,
    MoveOutcome,
    MoveResult,
    seed_game,
    validate_and_apply_move,
)

DEFAULT_SEED = "rizzoan01"  # Anthony Rizzo
DEFAULT_DB = "db/base2nerdle.sqlite"


def fmt_team_season(ts: tuple[str, int]) -> str:
    return f"{ts[0]} {ts[1]}"


def fmt_seasons(seasons: list[tuple[str, int]]) -> str:
    return ", ".join(fmt_team_season(t) for t in seasons)


def fmt_strikes_line(state: GameState) -> str:
    items = sorted(state.strikes.items())  # [((team, season), count), ...]
    return " ".join(
        f"{t}/{s}:{n}{'*' if n >= STRIKES_TO_BURN else ''}"
        for (t, s), n in items
    )


def display_with_disambiguation(name: str | None, disambiguation: str | None) -> str:
    if name and disambiguation:
        return f"{name} ({disambiguation})"
    return name or "?"


def reader_thread(q: queue.Queue):
    try:
        while True:
            line = sys.stdin.readline()
            if not line:
                return
            q.put(line.rstrip("\r\n"))
    except Exception:
        pass


def drain(q: queue.Queue):
    while not q.empty():
        try:
            q.get_nowait()
        except queue.Empty:
            return


def play_turn(
    state: GameState,
    conn: sqlite3.Connection,
    player_label: str,
    inputs: queue.Queue,
    turn_seconds: float,
) -> bool:
    """Return True if the player made a valid move; False on timeout."""
    drain(inputs)

    print()
    print("=" * 70)
    print(f"{player_label}'s turn -- name a teammate of {state.current_player_name}.")
    if state.strikes:
        print(f"Strikes: {fmt_strikes_line(state)}")
    print(f"({turn_seconds:.0f}s, unlimited guesses; ENTER submits each.)")
    print("=" * 70)
    sys.stdout.flush()

    deadline = time.monotonic() + turn_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print("  [TIME] out of seconds.")
            return False
        try:
            raw = inputs.get(timeout=remaining)
        except queue.Empty:
            print("  [TIME] out of seconds.")
            return False
        if not raw.strip():
            continue
        result = validate_and_apply_move(state, conn, raw)
        report(result, state, deadline)
        if result.outcome == MoveOutcome.VALID:
            return True


def report(result: MoveResult, state: GameState, deadline: float):
    remaining = max(0.0, deadline - time.monotonic())
    pretty = display_with_disambiguation(result.display_name, result.disambiguation)
    ambig_note = (
        f" (auto-picked from {result.ambiguous_count} matches)"
        if result.ambiguous_count > 1
        else ""
    )

    if result.outcome == MoveOutcome.VALID:
        # The strike has already been applied; figure out which seasons just burned.
        new_burns = [
            ts for ts in result.shared_seasons
            if state.strikes.get(ts, 0) >= STRIKES_TO_BURN
        ]
        seasons = fmt_seasons(result.shared_seasons)
        print(f"  [OK] {pretty}{ambig_note} -- teammates on {seasons}.")
        if new_burns:
            print(f"       BURNED this move: {fmt_seasons(new_burns)}")
        return

    if result.outcome == MoveOutcome.UNKNOWN_PLAYER:
        print(f"  [X]  unknown player. ({remaining:.1f}s left)")
    elif result.outcome == MoveOutcome.ALREADY_USED:
        print(f"  [X]  {pretty} already used in this chain. ({remaining:.1f}s left)")
    elif result.outcome == MoveOutcome.NOT_TEAMMATE:
        print(
            f"  [X]  {pretty}{ambig_note} was never a teammate of "
            f"{state.current_player_name}. ({remaining:.1f}s left)"
        )
    elif result.outcome == MoveOutcome.BLOCKED_BY_BURNED:
        all_shared = fmt_seasons(result.shared_seasons)
        burned = fmt_seasons(result.burned_seasons)
        verb = "is" if len(result.burned_seasons) == 1 else "are"
        print(
            f"  [X]  {pretty}{ambig_note} & {state.current_player_name} were teammates on {all_shared},"
        )
        print(f"       but {burned} {verb} burned (rule B). Pick someone else.")
        print(f"       ({remaining:.1f}s left)")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # accented names print on Windows
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", default=DEFAULT_SEED, help="seed player_id (Lahman)")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--p1", default="Player 1")
    ap.add_argument("--p2", default="Player 2")
    ap.add_argument("--turn-seconds", type=float, default=TURN_SECONDS,
                    help="per-turn budget (default 20)")
    args = ap.parse_args()

    if not Path(args.db).exists():
        print(f"DB not found: {args.db}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    try:
        state = seed_game(conn, args.seed)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1

    inputs: queue.Queue[str] = queue.Queue()
    threading.Thread(target=reader_thread, args=(inputs,), daemon=True).start()

    print()
    print("=== base2nerdle (MVP) ===")
    print(f"Seed player: {state.current_player_name}")
    print(f"{args.p1} goes first vs {args.p2}.")
    print(
        "Rule B: a link is invalid if any of the shared team-seasons is already "
        "burned (3 strikes), even if other shared seasons aren't."
    )
    print("Strike state is shown each turn; * marks a burned team-season.")

    labels = [args.p1, args.p2]
    turn = 0
    try:
        while True:
            label = labels[turn % 2]
            success = play_turn(state, conn, label, inputs, args.turn_seconds)
            if not success:
                winner = labels[(turn + 1) % 2]
                print()
                print("=" * 70)
                print(f"{winner} wins!")
                print(f"Chain ({len(state.chain)} players):")
                print("  " + " -> ".join(state.chain_names))
                burned = state.burned_team_seasons()
                if burned:
                    print(f"Burned: {fmt_seasons(burned)}")
                print("=" * 70)
                return 0
            turn += 1
    except KeyboardInterrupt:
        print("\n(interrupted)")
        return 130


if __name__ == "__main__":
    sys.exit(main() or 0)
