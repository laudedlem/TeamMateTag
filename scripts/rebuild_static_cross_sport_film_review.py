#!/usr/bin/env python3
"""Rebuild cross-sport Film Review puzzles from local compact runtime data."""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
if not os.environ.get("DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ.get("DIRECT_URL", "")
sys.path.insert(0, str(ROOT / "game"))
sys.path.insert(0, str(ROOT / "web"))

from film_review_generator import generate  # noqa: E402
import server  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

RUNTIME_DB = ROOT / "raw" / "runtime_compact" / "teammatetag_runtime_minimal.sqlite"
SPORTS = ("basketball", "hockey", "football")
STATIC_PUZZLE_PATH = ROOT / "raw" / "file_storage" / "teammatetag-runtime" / "gameplay" / "film_review_daily_puzzles.json"


def parse_day(value: str | None) -> date:
    if not value:
        return datetime.now(server.CENTRAL_TIME).date()
    return date.fromisoformat(value)


def day_range(start: date, end: date):
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += timedelta(days=1)


def reset_existing(conn, sports: list[str], start: date, end: date) -> None:
    game_ids = [
        row[0] for row in conn.execute(
            """SELECT game_id FROM film_review_daily_attempts
                WHERE sport_id = ANY(%s) AND puzzle_date BETWEEN %s AND %s""",
            (sports, start, end),
        ).fetchall()
    ]
    conn.execute(
        "DELETE FROM film_review_daily_attempts WHERE sport_id = ANY(%s) AND puzzle_date BETWEEN %s AND %s",
        (sports, start, end),
    )
    conn.execute(
        "DELETE FROM film_review_daily_puzzles WHERE sport_id = ANY(%s) AND puzzle_date BETWEEN %s AND %s",
        (sports, start, end),
    )
    for sport in sports:
        prefixes = [f"{sport}_{day.isoformat()}%" for day in day_range(start, end)]
        if prefixes:
            conn.execute(
                "DELETE FROM fr_results WHERE sport_id=%s AND puzzle_id LIKE ANY(%s)",
                (sport, prefixes),
            )
    if game_ids:
        conn.execute("DELETE FROM fr_games WHERE game_id = ANY(%s)", (game_ids,))


def local_shared(local_conn: sqlite3.Connection, sport: str, deck: list[str]) -> list[list]:
    shared = []
    for index in range(len(deck) - 1):
        first, second = deck[index], deck[index + 1]
        key_rows = dict(local_conn.execute(
            "SELECT player_id, player_key FROM compact_player_keys WHERE scope=? AND player_id IN (?, ?)",
            (sport, first, second),
        ).fetchall())
        first_key = key_rows.get(first)
        second_key = key_rows.get(second)
        if first_key is None or second_key is None:
            raise RuntimeError(f"{sport} pair is missing compact keys: {first} -> {second}")
        low_key, high_key = sorted((first_key, second_key))
        rows = local_conn.execute(
            """
            SELECT tk.team_id, tk.season, tm.name
              FROM teammate_team_seasons proof
              JOIN compact_team_keys tk ON tk.team_key = proof.team_key
              JOIN sport_teams tm
                ON tm.sport_id = proof.scope
               AND tm.team_id = tk.team_id
               AND tm.season = tk.season
             WHERE proof.scope = ?
               AND proof.player_a_key = ?
               AND proof.player_b_key = ?
             ORDER BY tk.season DESC, tm.name
             LIMIT 4
            """,
            (sport, low_key, high_key),
        ).fetchall()
        if not rows:
            raise RuntimeError(f"{sport} pair has no shared link: {first} -> {second}")
        if len(rows) > server.FR_MAX_LINK_OPTIONS:
            raise RuntimeError(f"{sport} pair has too many shared links: {first} -> {second}")
        shared.append([
            [team_id, int(season), server._canonical_sport_team_name(sport, team_id, name),
             server._sport_season_label(sport, int(season))]
            for team_id, season, name in rows
        ])
    return shared


def local_card_map(local_conn: sqlite3.Connection, sport: str, deck: list[str]) -> dict:
    cards = server._local_sport_cards(local_conn, sport, deck)
    return {pid: server._local_fr_card(sport, pid, cards[pid]) for pid in deck}


def storage_headshot_url(sport: str, player_id: str) -> str | None:
    return server._file_storage_headshot_urls(sport, [player_id]).get(player_id)


def build_payload(local_conn: sqlite3.Connection, sport: str, puzzle_day: date, unit: str | None, seed_suffix: str) -> dict:
    puzzle = generate(local_conn, sport, puzzle_day=puzzle_day, unit=unit, seed_suffix=seed_suffix)
    deck = list(puzzle.deck)
    cards = local_card_map(local_conn, sport, deck)
    for player_id, card in cards.items():
        stored = storage_headshot_url(sport, player_id)
        if stored:
            card["headshot_url"] = stored
    preview_cards = [
        {"player_id": pid, "name": cards[pid]["name"], "headshot_url": cards[pid].get("headshot_url")}
        for pid in deck[:2]
    ]
    return {
        "id": f"{sport}_{puzzle.puzzle_date}_{puzzle.unit or 'full'}",
        "puzzle_date": puzzle.puzzle_date,
        "puzzle_number": server._film_review_number(puzzle_day),
        "slots": list(puzzle.slots),
        "deck": deck,
        "unit": puzzle.unit,
        "preview_cards": preview_cards,
        "card_map": cards,
        "shared_per_pair": local_shared(local_conn, sport, deck),
    }


def payload_is_clean(payload: dict) -> bool:
    shared = payload.get("shared_per_pair") or []
    deck = payload.get("deck") or []
    if len(shared) != max(0, len(deck) - 1):
        return False
    card_map = payload.get("card_map") or {}
    if any(not (isinstance(card_map.get(pid), dict) and card_map[pid].get("headshot_url")) for pid in deck):
        return False
    used_links = set()
    for pair in shared:
        if not pair or len(pair) > server.FR_MAX_LINK_OPTIONS:
            return False
        links = {(row[0], int(row[1])) for row in pair}
        if links & used_links:
            return False
        used_links.update(links)
    return True


def store_payload(conn, sport: str, puzzle_day: date, unit: str | None, payload: dict) -> None:
    conn.execute(
        """INSERT INTO film_review_daily_puzzles (sport_id, puzzle_date, unit, puzzle)
             VALUES (%s,%s,%s,%s)
             ON CONFLICT (sport_id, puzzle_date, unit)
             DO UPDATE SET puzzle=EXCLUDED.puzzle, created_at=now()""",
        (sport, puzzle_day, unit or "", Jsonb(payload)),
    )


def export_static_puzzles(conn, start: date, end: date) -> tuple[int, int]:
    rows = conn.execute(
        """SELECT sport_id, puzzle_date::text, unit, puzzle
             FROM film_review_daily_puzzles
            WHERE puzzle_date BETWEEN %s AND %s
            ORDER BY puzzle_date, sport_id, unit""",
        (start, end),
    ).fetchall()
    payload = {
        "purpose": "Static Film Review daily puzzle mirror. Postgres remains the live lookup source.",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "rows": [
            {"sport_id": sport_id, "puzzle_date": puzzle_date, "unit": unit or "", "puzzle": puzzle}
            for sport_id, puzzle_date, unit, puzzle in rows
        ],
    }
    STATIC_PUZZLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    STATIC_PUZZLE_PATH.write_bytes(content)
    with gzip.open(STATIC_PUZZLE_PATH.with_suffix(".json.gz"), "wb", compresslevel=9) as handle:
        handle.write(content)
    return len(rows), len(content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=server.FILM_REVIEW_EPOCH.isoformat())
    parser.add_argument("--end", default="")
    parser.add_argument("--sport", action="append", choices=SPORTS)
    parser.add_argument("--no-reset", action="store_true")
    parser.add_argument("--export-file-storage", action="store_true")
    args = parser.parse_args()

    start = parse_day(args.start)
    end = parse_day(args.end)
    sports = args.sport or list(SPORTS)
    if not RUNTIME_DB.exists():
        raise SystemExit(f"missing compact runtime DB: {RUNTIME_DB}")

    server.ensure_runtime_schema()
    built = []
    with sqlite3.connect(RUNTIME_DB) as local_conn, server.db() as pg:
        if not args.no_reset:
            reset_existing(pg, sports, start, end)
        for sport in sports:
            units = ("offense", "defense") if sport == "football" else (None,)
            for puzzle_day in day_range(start, end):
                for unit in units:
                    last_error = None
                    for salt in range(300):
                        seed_suffix = "" if salt == 0 else f"easy{salt}"
                        try:
                            payload = build_payload(local_conn, sport, puzzle_day, unit, seed_suffix)
                            if not payload_is_clean(payload):
                                continue
                            store_payload(pg, sport, puzzle_day, unit, payload)
                            built.append((sport, puzzle_day.isoformat(), unit or "default", len(payload["deck"])))
                            print(f"built {sport:10} {puzzle_day.isoformat()} {unit or 'default':8} {len(payload['deck'])} cards", flush=True)
                            break
                        except Exception as error:
                            last_error = error
                    else:
                        raise RuntimeError(f"could not build {sport} {puzzle_day} {unit or ''}: {last_error}")
        if args.export_file_storage:
            rows, bytes_ = export_static_puzzles(pg, start, end)
            print(
                f"exported {rows} Film Review puzzle rows to {STATIC_PUZZLE_PATH} "
                f"({bytes_ / 1024:.1f} KB)",
                flush=True,
            )
    print(f"Rebuilt {len(built)} cross-sport Film Review puzzle rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
