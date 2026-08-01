"""Generate and inspect a deterministic local Film Review daily deck."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from game.film_review_generator import generate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sport", choices=("baseball", "football", "hockey", "basketball"))
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    conn = sqlite3.connect(ROOT / "db" / "teammatetag_local.sqlite")
    puzzle = generate(conn, args.sport, args.date)
    print(f"{args.sport.title()} Film Review, {puzzle.puzzle_date}")
    for index, player_id in enumerate(puzzle.deck):
        name = conn.execute("SELECT display_name FROM sport_players WHERE sport_id=? AND player_id=?",
                            (args.sport, player_id)).fetchone()[0]
        print(f"{index + 1:>2}. {puzzle.slots[index]:<3} {name} ({player_id})")
        if index < len(puzzle.links):
            team_id, season = puzzle.links[index]
            team = conn.execute("SELECT name FROM sport_teams WHERE sport_id=? AND team_id=? AND season=?",
                                (args.sport, team_id, season)).fetchone()[0]
            print(f"    {team}, {season}")


if __name__ == "__main__":
    main()
