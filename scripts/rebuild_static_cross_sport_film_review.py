#!/usr/bin/env python3
"""Rebuild static Film Review puzzles from local compact runtime data."""
from __future__ import annotations

import argparse
from collections import Counter
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
SPORTS = ("baseball", "basketball", "hockey", "football")
STATIC_PUZZLE_PATH = ROOT / "raw" / "file_storage" / "teammatetag-runtime" / "gameplay" / "film_review_daily_puzzles.json"
MAX_PLAYER_USES = {
    "baseball": 1,
    "basketball": 1,
    "hockey": 1,
    "football": 8,
}
FOOTBALL_REPEAT_CAREER_GAMES = 60


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


def local_shared(local_conn: sqlite3.Connection, pg_conn, sport: str, deck: list[str]) -> list[list]:
    if sport == "baseball":
        return [
            [[team_id, int(season), team_name, server._sport_season_label(sport, int(season))]
             for team_id, season, team_name in pair]
            for pair in server._fr_compute_shared(pg_conn, deck)
        ]
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


def local_card_map(local_conn: sqlite3.Connection, pg_conn, sport: str, deck: list[str]) -> dict:
    if sport == "baseball":
        cards = server._hydrate_player_cards(pg_conn, deck)
        return {pid: server.fr_card_dict_from_card(pid, cards[pid]) for pid in deck}
    cards = server._local_sport_cards(local_conn, sport, deck)
    return {pid: server._local_fr_card(sport, pid, cards[pid]) for pid in deck}


def storage_headshot_url(sport: str, player_id: str) -> str | None:
    return server._file_storage_headshot_urls(sport, [player_id]).get(player_id)


def storage_headshot_player_ids(sport: str) -> set[str]:
    manifest_path = ROOT / "raw" / "file_storage" / "manifests" / "headshots" / f"{sport}.json"
    if not manifest_path.exists():
        return set()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        str(row.get("player_id"))
        for row in (payload.get("rows") or [])
        if row.get("player_id") and row.get("object_path")
    }


def players_missing_storage_headshots(local_conn: sqlite3.Connection, pg_conn, sport: str) -> set[str]:
    covered = storage_headshot_player_ids(sport)
    if not covered:
        return set()
    if sport == "baseball":
        player_ids = {
            player_id
            for player_id, in pg_conn.execute(
                "SELECT player_id FROM players WHERE player_id IS NOT NULL"
            ).fetchall()
        }
    else:
        player_ids = {
            player_id
            for player_id, in local_conn.execute(
                "SELECT player_id FROM sport_players WHERE sport_id=?",
                (sport,),
            ).fetchall()
        }
    return player_ids - covered


def build_payload(local_conn: sqlite3.Connection, pg_conn, sport: str, puzzle_day: date, unit: str | None,
                  seed_suffix: str, banned_players: set[str] | None = None) -> dict:
    if sport == "baseball":
        puzzle_dict = server.generate_baseball_film_review(
            pg_conn,
            puzzle_day,
            seed_suffix=seed_suffix,
            banned_players=banned_players,
        )
        deck = list(puzzle_dict["deck"])
        slots = list(puzzle_dict["slots"])
        puzzle_unit = None
        puzzle_date = puzzle_dict["puzzle_date"]
    else:
        puzzle = generate(
            local_conn,
            sport,
            puzzle_day=puzzle_day,
            unit=unit,
            seed_suffix=seed_suffix,
            attempts=60 if sport == "football" else 300,
            banned_players=banned_players,
        )
        deck = list(puzzle.deck)
        slots = list(puzzle.slots)
        puzzle_unit = puzzle.unit
        puzzle_date = puzzle.puzzle_date
    cards = local_card_map(local_conn, pg_conn, sport, deck)
    for player_id, card in cards.items():
        stored = storage_headshot_url(sport, player_id)
        card["headshot_url"] = stored
    preview_cards = [
        {"player_id": pid, "name": cards[pid]["name"], "headshot_url": cards[pid].get("headshot_url")}
        for pid in deck[:2]
    ]
    return {
        "id": f"{sport}_{puzzle_date}_{puzzle_unit or 'full'}",
        "puzzle_date": puzzle_date,
        "puzzle_number": server._film_review_number(puzzle_day),
        "slots": slots,
        "deck": deck,
        "unit": puzzle_unit,
        "preview_cards": preview_cards,
        "card_map": cards,
        "shared_per_pair": local_shared(local_conn, pg_conn, sport, deck),
    }


def payload_is_clean(payload: dict) -> bool:
    shared = payload.get("shared_per_pair") or []
    deck = payload.get("deck") or []
    if len(shared) != max(0, len(deck) - 1):
        return False
    card_map = payload.get("card_map") or {}
    if any(not (isinstance(card_map.get(pid), dict) and card_map[pid].get("headshot_url")) for pid in deck):
        return False
    for pair in shared:
        if not pair or len(pair) > server.FR_MAX_LINK_OPTIONS:
            return False
    return True


def store_payload(conn, sport: str, puzzle_day: date, unit: str | None, payload: dict) -> None:
    conn.execute(
        """INSERT INTO film_review_daily_puzzles (sport_id, puzzle_date, unit, puzzle)
             VALUES (%s,%s,%s,%s)
             ON CONFLICT (sport_id, puzzle_date, unit)
             DO UPDATE SET puzzle=EXCLUDED.puzzle, created_at=now()""",
        (sport, puzzle_day, unit or "", Jsonb(payload)),
    )


def used_player_key(sport: str, unit: str | None) -> str:
    return f"{sport}:{unit or ''}" if sport == "football" else sport


def player_max_uses(local_conn: sqlite3.Connection, key: str, player_id: str,
                    cache: dict[tuple[str, str], int]) -> int:
    sport = key.split(":", 1)[0]
    base_cap = MAX_PLAYER_USES.get(sport, 1)
    if sport != "football" or base_cap <= 1:
        return 1
    cache_key = (key, player_id)
    if cache_key in cache:
        return cache[cache_key]
    row = local_conn.execute(
        """
        SELECT COALESCE(t.career_games, s.career_games, 0),
               COALESCE(t.career_touchdowns, 0),
               COALESCE(t.passing_touchdowns, 0),
               COALESCE(t.rushing_touchdowns, 0),
               COALESCE(t.receiving_touchdowns, 0),
               COALESCE(t.career_sacks, 0),
               COALESCE(t.career_interceptions, 0),
               COALESCE(t.all_star_count, 0),
               COALESCE(t.mvp_count, 0),
               COALESCE(t.championship_count, 0)
          FROM sport_players_searchable s
          LEFT JOIN sport_player_traits t
            ON t.sport_id=s.sport_id AND t.player_id=s.player_id
         WHERE s.sport_id='football' AND s.player_id=?
        """,
        (player_id,),
    ).fetchone()
    if not row:
        cache[cache_key] = 1
        return 1
    career_games, touchdowns, pass_tds, rush_tds, rec_tds, sacks, picks, pro_bowls, mvps, rings = row
    notable = (
        int(career_games or 0) >= FOOTBALL_REPEAT_CAREER_GAMES
        or int(touchdowns or 0) >= 20
        or int(pass_tds or 0) >= 30
        or int(rush_tds or 0) >= 15
        or int(rec_tds or 0) >= 15
        or float(sacks or 0) >= 20
        or int(picks or 0) >= 10
        or int(pro_bowls or 0) > 0
        or int(mvps or 0) > 0
        or int(rings or 0) > 0
    )
    cache[cache_key] = base_cap if notable else 1
    return cache[cache_key]


def load_prior_used_players(conn, sports: list[str], start: date) -> dict[str, Counter]:
    used = {sport: Counter() for sport in sports if sport != "football"}
    if "football" in sports:
        used["football:offense"] = Counter()
        used["football:defense"] = Counter()
    rows = conn.execute(
        """SELECT sport_id, COALESCE(unit, ''), puzzle
             FROM film_review_daily_puzzles
            WHERE sport_id = ANY(%s) AND puzzle_date < %s""",
        (sports, start),
    ).fetchall()
    for sport, unit, puzzle in rows:
        used.setdefault(used_player_key(sport, unit), Counter()).update(puzzle.get("deck") or [])
    return used


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
        used_by_sport = load_prior_used_players(pg, sports, start)
        missing_headshots_by_sport = {
            sport: players_missing_storage_headshots(local_conn, pg, sport)
            for sport in sports
        }
        cap_cache: dict[tuple[str, str], int] = {}
        if not args.no_reset:
            reset_existing(pg, sports, start, end)
        for sport in sports:
            units = ("offense", "defense") if sport == "football" else (None,)
            for puzzle_day in day_range(start, end):
                for unit in units:
                    no_repeat_key = used_player_key(sport, unit)
                    usage = used_by_sport.setdefault(no_repeat_key, Counter())
                    last_error = None
                    for salt in range(800 if sport == "football" else 300):
                        seed_suffix = "" if salt == 0 else f"easy{salt}"
                        try:
                            banned_players = {
                                player_id
                                for player_id, uses in usage.items()
                                if uses >= player_max_uses(local_conn, no_repeat_key, player_id, cap_cache)
                                and player_max_uses(local_conn, no_repeat_key, player_id, cap_cache) <= 1
                            }
                            banned_players.update(missing_headshots_by_sport.get(sport, set()))
                            payload = build_payload(
                                local_conn,
                                pg,
                                sport,
                                puzzle_day,
                                unit,
                                seed_suffix,
                                banned_players=banned_players,
                            )
                            if not payload_is_clean(payload):
                                continue
                            over_limit = [
                                player_id for player_id in payload["deck"]
                                if usage[player_id] >= player_max_uses(local_conn, no_repeat_key, player_id, cap_cache)
                            ]
                            if over_limit:
                                last_error = RuntimeError(
                                    f"{sport} puzzle exceeds player-use cap: {', '.join(sorted(over_limit)[:5])}"
                                )
                                continue
                            store_payload(pg, sport, puzzle_day, unit, payload)
                            usage.update(payload["deck"])
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
