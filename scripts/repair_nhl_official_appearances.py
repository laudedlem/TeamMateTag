#!/usr/bin/env python3
"""Repair NHL team-season appearances from official NHL player landing data."""
from __future__ import annotations

import argparse
import atexit
import json
import os
import sys
import time
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

try:
    import psycopg
except ImportError:  # pragma: no cover
    print("ERROR: install psycopg first: pip install 'psycopg[binary]'", file=sys.stderr)
    sys.exit(1)

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from web.server import NHL_TEAM_NAMES  # noqa: E402


SPORT_ID = "hockey"
SOURCE = "nhl_official_player_landing"
API = "https://api-web.nhle.com/v1/player/{}/landing"
GAME_LOG_API = "https://api-web.nhle.com/v1/player/{}/game-log/{}/2"
CACHE_DIR = ROOT / "raw" / "nhl_official_repair"
LOCK_PATH = CACHE_DIR / "repair.lock"
MAX_HTTP_RETRIES = 6
RETRY_BASE_SLEEP = 2.5


@dataclass(frozen=True)
class OfficialRepair:
    player_id: str
    external_id: str
    name: str
    rows: list[tuple[str, str, int, int]]
    stints: dict[tuple[str, int], tuple[int, int, str, str]]
    error: str | None = None


@dataclass(frozen=True)
class RowDiff:
    missing: list[tuple[int, str]]
    extra: list[tuple[int, str]]

    @property
    def changed(self) -> bool:
        return bool(self.missing or self.extra)

def team_name_key(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFD", value or "")
        if unicodedata.category(char) != "Mn"
    ).lower()


TEAM_ID_BY_NAME = {team_name_key(name): team_id for team_id, name in NHL_TEAM_NAMES.items()}
TEAM_ID_BY_NAME.update(
    {
        team_name_key("arizona coyotes"): "ARI",
        team_name_key("atlanta flames"): "AFM",
        team_name_key("atlanta thrashers"): "ATL",
        team_name_key("california golden seals"): "CLR",
        team_name_key("cleveland barons"): "CLE",
        team_name_key("colorado rockies"): "COR",
        team_name_key("hartford whalers"): "HFD",
        team_name_key("minnesota north stars"): "MNS",
        team_name_key("phoenix coyotes"): "PHX",
        team_name_key("quebec nordiques"): "QUE",
        team_name_key("utah hockey club"): "UTA",
        team_name_key("utah mammoth"): "UTA",
        team_name_key("winnipeg jets"): "WPG",
    }
)


def get_json(url: str, cache_path: Path | None = None) -> dict[str, Any]:
    if cache_path and cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    for attempt in range(MAX_HTTP_RETRIES + 1):
        response = requests.get(url, timeout=30)
        if response.status_code == 429 and attempt < MAX_HTTP_RETRIES:
            retry_after = response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else RETRY_BASE_SLEEP * (attempt + 1)
            except ValueError:
                delay = RETRY_BASE_SLEEP * (attempt + 1)
            delay = min(60.0, max(RETRY_BASE_SLEEP, delay))
            print(f"RATE_LIMIT sleeping {delay:.1f}s for {url}", flush=True)
            time.sleep(delay)
            continue
        if 500 <= response.status_code < 600 and attempt < MAX_HTTP_RETRIES:
            delay = min(45.0, RETRY_BASE_SLEEP * (attempt + 1))
            print(f"SERVER_RETRY {response.status_code} sleeping {delay:.1f}s for {url}", flush=True)
            time.sleep(delay)
            continue
        response.raise_for_status()
        payload = response.json()
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
            tmp_path.write_text(json.dumps(payload), encoding="utf-8")
            tmp_path.replace(cache_path)
        return payload
    raise RuntimeError(f"request failed after retries: {url}")


def localized(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("default") or next(iter(value.values()), "") or "")
    return str(value or "")


def acquire_lock(disabled: bool = False) -> None:
    if disabled:
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit(
            f"Another NHL repair appears to be running: {LOCK_PATH}\n"
            "Stop the other process or delete the lock only after confirming no repair is active."
        )
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))

    def cleanup() -> None:
        try:
            if LOCK_PATH.read_text(encoding="utf-8").strip() == str(os.getpid()):
                LOCK_PATH.unlink()
        except OSError:
            pass

    atexit.register(cleanup)


def season_start(raw_season: Any) -> int | None:
    try:
        return int(str(raw_season)[:4])
    except (TypeError, ValueError):
        return None


def official_rows(
    external_id: str,
    season_since: int = 0,
    season_through: int = 9999,
) -> list[tuple[str, str, int, int]]:
    payload = get_json(API.format(external_id), CACHE_DIR / "landing" / f"{external_id}.json")
    rows: list[tuple[str, str, int, int]] = []
    for item in payload.get("seasonTotals", []) or []:
        if item.get("leagueAbbrev") != "NHL" or int(item.get("gameTypeId") or 0) != 2:
            continue
        games = int(item.get("gamesPlayed") or 0)
        season = season_start(item.get("season"))
        team_name = localized(item.get("teamName")).strip()
        team_id = TEAM_ID_BY_NAME.get(team_name_key(team_name))
        if not team_id or not season or season < season_since or season > season_through or games <= 0:
            continue
        rows.append((team_id, team_name, season, games))
    return rows


def game_log_stints(external_id: str, seasons: set[int]) -> dict[tuple[str, int], tuple[int, int, str, str]]:
    stints: dict[tuple[str, int], tuple[int, int, str, str]] = {}
    for season in sorted(seasons):
        try:
            season_id = f"{season}{season + 1}"
            payload = get_json(
                GAME_LOG_API.format(external_id, season_id),
                CACHE_DIR / "game_log" / f"{external_id}_{season_id}.json",
            )
        except requests.RequestException:
            continue
        dates_by_team: dict[str, list[str]] = defaultdict(list)
        for game in payload.get("gameLog", []) or []:
            team_id = str(game.get("teamAbbrev") or "").strip()
            game_date = str(game.get("gameDate") or "").strip()
            if team_id and game_date:
                dates_by_team[team_id].append(game_date)
        for team_id, dates in dates_by_team.items():
            first = min(dates)
            last = max(dates)
            first_unit = int(datetime.strptime(first, "%Y-%m-%d").strftime("%Y%m%d"))
            last_unit = int(datetime.strptime(last, "%Y-%m-%d").strftime("%Y%m%d"))
            stints[(team_id, season)] = (first_unit, last_unit, first, last)
    return stints


def stint_seasons(
    rows: list[tuple[str, str, int, int]],
    mode: str,
) -> set[int]:
    if mode == "none":
        return set()
    seasons = {season for _, _, season, _ in rows}
    if mode == "all":
        return seasons
    teams_by_season: dict[int, set[str]] = defaultdict(set)
    for team_id, _team_name, season, _games in rows:
        teams_by_season[season].add(team_id)
    return {season for season, team_ids in teams_by_season.items() if len(team_ids) > 1}


def fetch_repair(
    player_id: str,
    external_id: str,
    name: str,
    season_since: int,
    season_through: int,
    stint_mode: str,
) -> OfficialRepair:
    try:
        rows = official_rows(external_id, season_since=season_since, season_through=season_through)
        if not rows:
            return OfficialRepair(player_id, external_id, name, [], {})
        seasons = stint_seasons(rows, stint_mode)
        stints = game_log_stints(external_id, seasons) if seasons else {}
        return OfficialRepair(player_id, external_id, name, rows, stints)
    except Exception as exc:
        return OfficialRepair(player_id, external_id, name, [], {}, str(exc))


def apply_repair(
    conn: "psycopg.Connection",
    player_id: str,
    external_id: str,
    rows: list[tuple[str, str, int, int]],
    stints: dict[tuple[str, int], tuple[int, int, str, str]],
    season_since: int = 0,
    season_through: int = 9999,
) -> tuple[int, int]:
    if not rows:
        return 0, 0
    seasons = {season for _, _, season, _ in rows}
    min_season = min(seasons)
    max_season = max(seasons)
    with conn.cursor() as cur:
        for team_id, team_name, season, _games in rows:
            cur.execute(
                """
                INSERT INTO sport_franchises (sport_id, franchise_id, name, active)
                VALUES (%s, %s, %s, true)
                ON CONFLICT (sport_id, franchise_id) DO UPDATE
                SET name = EXCLUDED.name
                """,
                (SPORT_ID, team_id, team_name),
            )
            cur.execute(
                """
                INSERT INTO sport_teams (sport_id, team_id, season, franchise_id, name)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (sport_id, team_id, season) DO UPDATE
                SET franchise_id = EXCLUDED.franchise_id,
                    name = EXCLUDED.name
                """,
                (SPORT_ID, team_id, season, team_id, team_name),
            )
        cur.execute(
            """
            DELETE FROM sport_appearances
             WHERE sport_id = %s
               AND player_id = %s
               AND season BETWEEN %s AND %s
            """,
            (SPORT_ID, player_id, season_since or min_season, season_through),
        )
        cur.execute(
            """
            DELETE FROM sport_player_stints
             WHERE sport_id = %s
               AND player_id = %s
               AND season BETWEEN %s AND %s
            """,
            (SPORT_ID, player_id, season_since or min_season, season_through),
        )
        cur.executemany(
            """
            INSERT INTO sport_appearances
                (sport_id, player_id, team_id, season, games_total)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (sport_id, player_id, team_id, season) DO UPDATE
            SET games_total = EXCLUDED.games_total
            """,
            [(SPORT_ID, player_id, team_id, season, games) for team_id, _team_name, season, games in rows],
        )
        stint_rows = []
        for team_id, _team_name, season, _games in rows:
            stint = stints.get((team_id, season))
            if not stint:
                first = f"{season}-09-01"
                last = f"{season + 1}-06-30"
                stint = (
                    int(first.replace("-", "")),
                    int(last.replace("-", "")),
                    first,
                    last,
                )
            first_unit, last_unit, first_label, last_label = stint
            stint_rows.append((SPORT_ID, player_id, team_id, season, first_unit, last_unit, first_label, last_label, SOURCE))
        cur.executemany(
            """
            INSERT INTO sport_player_stints
                (sport_id, player_id, team_id, season, first_unit, last_unit, first_label, last_label, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (sport_id, player_id, team_id, season) DO UPDATE
            SET first_unit = EXCLUDED.first_unit,
                last_unit = EXCLUDED.last_unit,
                first_label = EXCLUDED.first_label,
                last_label = EXCLUDED.last_label,
                source = EXCLUDED.source
            """,
            stint_rows,
        )
        cur.executemany(
            """
            INSERT INTO sport_teammate_stint_coverage
                (sport_id, season, coverage_type, strict, source, updated_at)
            VALUES (%s, %s, 'stint_range', 1, %s, now())
            ON CONFLICT (sport_id, season) DO UPDATE
            SET strict = 1,
                source = EXCLUDED.source,
                updated_at = now()
            """,
            [(SPORT_ID, season, SOURCE) for season in seasons],
        )
        cur.execute(
            """
            UPDATE sport_players
               SET debut_year = LEAST(COALESCE(debut_year, %s), %s),
                   final_year = GREATEST(COALESCE(final_year, %s), %s)
             WHERE sport_id = %s AND player_id = %s
            """,
            (min_season, min_season, max_season, max_season, SPORT_ID, player_id),
        )
        cur.execute(
            """
            INSERT INTO data_provenance (source, season, fetched_at, row_count)
            VALUES (%s, %s, now(), %s)
            ON CONFLICT (source, season) DO UPDATE
            SET fetched_at = EXCLUDED.fetched_at,
                row_count = COALESCE(data_provenance.row_count, 0) + EXCLUDED.row_count
            """,
            (f"{SOURCE}:{external_id}", max_season, len(rows)),
        )
    return len(rows), len(seasons)


def repair_player(
    conn: "psycopg.Connection",
    player_id: str,
    external_id: str,
    season_since: int = 0,
    season_through: int = 9999,
    stint_mode: str = "multi-team",
) -> tuple[int, int]:
    payload = fetch_repair(player_id, external_id, "", season_since, season_through, stint_mode)
    if payload.error:
        raise RuntimeError(payload.error)
    return apply_repair(conn, player_id, external_id, payload.rows, payload.stints, season_since, season_through)


def current_team_seasons(
    conn: "psycopg.Connection",
    player_id: str,
    season_since: int,
    season_through: int,
) -> set[tuple[int, str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT season, team_id
              FROM sport_appearances
             WHERE sport_id = %s
               AND player_id = %s
               AND season >= %s
               AND season <= %s
            """,
            (SPORT_ID, player_id, season_since, season_through),
        )
        return set(cur.fetchall())


def diff_repair_rows(
    conn: "psycopg.Connection",
    player_id: str,
    rows: list[tuple[str, str, int, int]],
    season_since: int,
    season_through: int,
) -> RowDiff:
    official = {(season, team_id) for team_id, _team_name, season, _games in rows}
    current = current_team_seasons(conn, player_id, season_since, season_through)
    return RowDiff(missing=sorted(official - current), extra=sorted(current - official))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--player-id", action="append", default=[], help="Internal player id, e.g. nhl:8479400")
    parser.add_argument("--external-id", action="append", default=[], help="NHL numeric player id, e.g. 8479400")
    parser.add_argument("--all", action="store_true", help="Repair every hockey player with an external id")
    parser.add_argument("--audit", action="store_true", help="Only report production rows that differ from official NHL team-season rows")
    parser.add_argument("--min-final-year", type=int, default=0, help="Only inspect players whose stored final_year is this recent")
    parser.add_argument("--season-since", type=int, default=0, help="Only inspect/repair official seasons from this start year onward")
    parser.add_argument("--season-through", type=int, default=9999, help="Only inspect/repair official seasons through this start year")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.04)
    parser.add_argument("--progress-every", type=int, default=25, help="Print cumulative progress every N players")
    parser.add_argument("--workers", type=int, default=1, help="Parallel NHL API fetch workers; DB writes remain sequential")
    parser.add_argument("--only-different", action="store_true", help="Skip DB rewrites when official and current team-season sets already match")
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N selected players")
    parser.add_argument("--skip-provenance", action="store_true", help="Skip players already marked repaired for the requested season-through")
    parser.add_argument("--no-lock", action="store_true", help="Allow multiple repair processes; not recommended for production writes")
    parser.add_argument(
        "--stint-mode",
        choices=("multi-team", "all", "none"),
        default="multi-team",
        help="Fetch exact game-log stint dates for all seasons, only multi-team seasons, or none",
    )
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("DATABASE_URL is required")
    if not (args.all or args.player_id or args.external_id or args.audit):
        raise SystemExit("Use --all, --audit, --player-id, or --external-id")
    acquire_lock(disabled=args.audit or args.no_lock)

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None) as conn:
        with conn.cursor() as cur:
            target_clauses = []
            where_clauses = []
            params: list[Any] = [SPORT_ID]
            if not args.all:
                if args.player_id:
                    target_clauses.append("player_id = ANY(%s)")
                    params.append(args.player_id)
                if args.external_id:
                    target_clauses.append("external_id = ANY(%s)")
                    params.append(args.external_id)
                if target_clauses:
                    where_clauses.append("(" + " OR ".join(target_clauses) + ")")
                elif not args.audit:
                    where_clauses.append("false")
            if args.min_final_year:
                where_clauses.append("final_year >= %s")
                params.append(args.min_final_year)
            if args.skip_provenance:
                where_clauses.append(
                    """
                    NOT EXISTS (
                        SELECT 1 FROM data_provenance dp
                         WHERE dp.source = %s || p.external_id
                           AND dp.season = %s
                    )
                    """
                )
                params.extend((f"{SOURCE}:", args.season_through))
            where = " AND " + " AND ".join(where_clauses) if where_clauses else ""
            cur.execute(
                f"""
                SELECT p.player_id, p.external_id, p.display_name
                  FROM sport_players p
                 WHERE p.sport_id = %s
                   AND p.external_id IS NOT NULL
                   {where}
                 ORDER BY p.final_year DESC NULLS LAST, p.display_name
                """,
                params,
            )
            players = cur.fetchall()
    if args.offset:
        players = players[args.offset:]
    if args.limit:
        players = players[: args.limit]

    if args.audit:
        missing_players = extra_players = bad_players = 0
        missing_pairs = extra_pairs = 0
        samples = []
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None) as conn:
            for index, (player_id, external_id, name) in enumerate(players, 1):
                try:
                    rows = official_rows(
                        str(external_id),
                        season_since=args.season_since,
                        season_through=args.season_through,
                    )
                except Exception as exc:
                    print(f"ERROR {player_id} {name}: {exc}", flush=True)
                    continue
                diff = diff_repair_rows(conn, player_id, rows, args.season_since, args.season_through)
                missing = diff.missing
                extra = diff.extra
                if missing or extra:
                    bad_players += 1
                    missing_players += int(bool(missing))
                    extra_players += int(bool(extra))
                    missing_pairs += len(missing)
                    extra_pairs += len(extra)
                    if len(samples) < 20:
                        samples.append((player_id, name, missing[:8], extra[:8]))
                if args.progress_every and index % args.progress_every == 0:
                    print(f"checked {index:,}/{len(players):,}; bad players {bad_players:,}", flush=True)
                time.sleep(args.sleep)
        print(
            f"Audit complete: {bad_players:,}/{len(players):,} players differ; "
            f"{missing_players:,} have missing rows, {extra_players:,} have extra rows; "
            f"{missing_pairs:,} missing pairs, {extra_pairs:,} extra pairs.",
            flush=True,
        )
        for player_id, name, missing, extra in samples:
            print(f"SAMPLE {player_id} {name}: missing={missing} extra={extra}", flush=True)
        return

    repaired = changed_players = skipped = skipped_no_rows = errors = 0
    rows_total = seasons_total = 0

    def record_progress(index: int, total: int, payload: OfficialRepair, rows: int, seasons: int, changed: bool) -> None:
        nonlocal repaired, changed_players, rows_total, seasons_total
        rows_total += rows
        seasons_total += seasons
        repaired += int(rows > 0)
        changed_players += int(changed)
        if args.progress_every and (index % args.progress_every == 0 or rows):
            print(
                f"{index:,}/{total:,} {payload.player_id} {payload.name}: "
                f"{rows} official rows across {seasons} seasons; changed={changed}; "
                f"cumulative changed {changed_players:,}; cumulative written players {repaired:,}; "
                f"cumulative rows {rows_total:,}; "
                f"cumulative seasons {seasons_total:,}",
                flush=True,
            )

    total_players = len(players)
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None) as conn:
        if args.workers <= 1:
            for index, (player_id, external_id, name) in enumerate(players, 1):
                payload = fetch_repair(player_id, str(external_id), name, args.season_since, args.season_through, args.stint_mode)
                if payload.error:
                    errors += 1
                    print(f"ERROR {player_id} {name}: {payload.error}", flush=True)
                    continue
                if not payload.rows:
                    skipped_no_rows += 1
                    if args.progress_every and index % args.progress_every == 0:
                        print(
                            f"{index:,}/{total_players:,}; skipped unchanged {skipped:,}; "
                            f"skipped no official rows {skipped_no_rows:,}; "
                            f"cumulative changed {changed_players:,}; errors {errors:,}",
                            flush=True,
                        )
                    continue
                diff = diff_repair_rows(conn, player_id, payload.rows, args.season_since, args.season_through)
                if args.only_different and not diff.changed:
                    skipped += 1
                    if args.progress_every and index % args.progress_every == 0:
                        print(
                            f"{index:,}/{total_players:,}; skipped unchanged {skipped:,}; "
                            f"skipped no official rows {skipped_no_rows:,}; "
                            f"cumulative changed {changed_players:,}; errors {errors:,}",
                            flush=True,
                        )
                    continue
                rows, seasons = apply_repair(
                    conn,
                    player_id,
                    str(external_id),
                    payload.rows,
                    payload.stints,
                    season_since=args.season_since,
                    season_through=args.season_through,
                )
                record_progress(index, total_players, payload, rows, seasons, diff.changed)
                time.sleep(args.sleep)
        else:
            with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
                futures = [
                    pool.submit(
                        fetch_repair,
                        player_id,
                        str(external_id),
                        name,
                        args.season_since,
                        args.season_through,
                        args.stint_mode,
                    )
                    for player_id, external_id, name in players
                ]
                for index, future in enumerate(as_completed(futures), 1):
                    payload = future.result()
                    if payload.error:
                        errors += 1
                        print(f"ERROR {payload.player_id} {payload.name}: {payload.error}", flush=True)
                        continue
                    if not payload.rows:
                        skipped_no_rows += 1
                        if args.progress_every and index % args.progress_every == 0:
                            print(
                                f"{index:,}/{total_players:,}; skipped unchanged {skipped:,}; "
                                f"skipped no official rows {skipped_no_rows:,}; "
                                f"cumulative changed {changed_players:,}; errors {errors:,}",
                                flush=True,
                            )
                        continue
                    diff = diff_repair_rows(conn, payload.player_id, payload.rows, args.season_since, args.season_through)
                    if args.only_different and not diff.changed:
                        skipped += 1
                        if args.progress_every and index % args.progress_every == 0:
                            print(
                                f"{index:,}/{total_players:,}; skipped unchanged {skipped:,}; "
                                f"skipped no official rows {skipped_no_rows:,}; "
                                f"cumulative changed {changed_players:,}; errors {errors:,}",
                                flush=True,
                            )
                        continue
                    rows, seasons = apply_repair(
                        conn,
                        payload.player_id,
                        payload.external_id,
                        payload.rows,
                        payload.stints,
                        season_since=args.season_since,
                        season_through=args.season_through,
                    )
                    record_progress(index, total_players, payload, rows, seasons, diff.changed)
    print(
        f"Repaired {repaired:,}/{total_players:,} NHL players; "
        f"changed {changed_players:,}; skipped unchanged {skipped:,}; "
        f"skipped no official rows {skipped_no_rows:,}; "
        f"upserted {rows_total:,} official appearance rows across {seasons_total:,} player-seasons; "
        f"errors {errors:,}.",
        flush=True,
    )


if __name__ == "__main__":
    main()
