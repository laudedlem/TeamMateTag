# Live Season Data Plan

TeamMateTag's live data pipeline is local-first.

Large source data stays under `raw/` as local SQLite/CSV/cache files. Supabase
receives only refined runtime data: player/team catalogs, team-season
appearances, stints, compact teammate proof rows, search/card rollups, headshot
URLs, coverage flags, and small derived qualifier/stat rows needed by gameplay.

## Active Updaters

- Baseball: `scripts/update_mlb_compact_live.py`
- Basketball/Hockey: `scripts/update_cross_sport_compact_live.py basketball|hockey`
- Football: `scripts/update_nfl_compact_live.py`

Each active updater can run offline-only by omitting `--upload`. The scheduled
GitHub workflows run the same scripts with `--upload --prune-live-staging`, so
temporary online staging is removed after compact proof/runtime rows are
refreshed.

## Manual Runs

```bash
python scripts/update_mlb_compact_live.py --season 2026 --backfill-days 3
python scripts/update_cross_sport_compact_live.py basketball --season 2026 --backfill-days 3
python scripts/update_cross_sport_compact_live.py hockey --season 2026 --backfill-days 3
python scripts/update_nfl_compact_live.py --season 2026
```

Add `--upload --prune-live-staging` only after the local output is inspected.

## Runtime Build

```bash
python scripts/build_minimal_runtime_sqlite.py
python scripts/audit_runtime_data_hygiene.py
```

The minimal runtime compiler folds local historical proof DBs plus any live
runtime files into `raw/runtime_compact/teammatetag_runtime_minimal.sqlite`
without copying raw boxscore/player-game/snap rows.

## Deleted Legacy Paths

The old direct-to-Supabase live updaters and one-time Postgres import/migration
scripts were removed after their source-fetching helpers were moved into small
client modules:

- `scripts/live_mlb_client.py`
- `scripts/live_nba_client.py`
- `scripts/live_nhl_client.py`

Do not recreate direct writers that upload raw historical game rows to
Supabase. New source work should first land locally under `raw/`, then be
compiled into compact runtime data.
