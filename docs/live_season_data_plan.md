# Live Season Data Plan

Version 0.4.00 starts with the free, lowest-risk path: MLB daily game imports
from the public MLB Stats API into Supabase Postgres.

## Implemented first

- `scripts/update_mlb_live_data.py` pulls completed MLB games from
  `https://statsapi.mlb.com/api/v1`.
- The import reads schedule windows, fetches each final game's boxscore, stores
  one player/game appearance row, and rolls the season up into `appearances`.
- The script creates and uses `mlb_live_game_imports` and
  `mlb_live_player_games` so repeated runs are idempotent.
- Current-season `player_stints` are rebuilt from actual game dates, which
  makes midseason team ranges clearer than roster-level season stats.
- New MLB players not yet present in the annual Lahman snapshot are inserted
  with stable `mlbam_<id>` ids and searchable names.
- `.github/workflows/update-mlb-live-data.yml` runs the updater daily and can
  be triggered manually with an optional season-to-date backfill.

## Operator setup

GitHub Actions needs this repository secret:

- `DATABASE_URL`: Supabase Postgres connection string.

Useful manual runs:

```bash
python scripts/update_mlb_live_data.py --dry-run --backfill-days 1
python scripts/update_mlb_live_data.py --season 2026 --season-to-date
python scripts/update_mlb_live_data.py --season 2026 --backfill-days 3
```

The first production run for a season automatically expands to season-to-date
if no game imports exist yet.

The 2026 production season-to-date seed was run on 2026-08-23. Supabase held
2,326 unique MLB games, 74,602 player-game rows, 4,068 current-season
player/team appearance rows, and 3,475 searchable live players after the run.

## Next sports

- NHL: use the public `api-web.nhle.com/v1` gamecenter/boxscore endpoints and
  the same game-level staging pattern.
- NFL: use nflverse's free public data releases for roster/player/game updates.
  This is naturally weekly rather than daily for most of the season.
- NBA: use the free community `nba_api`/NBA.com endpoint path or
  SportsDataverse boxscore releases. This is the least official of the four
  free options, so it should be isolated behind the same staging/audit pattern.

## Remaining limitations

- MLB playoff powerup and win-condition derived trait tables are still annual
  Lahman-based. The live importer updates teammate graph/search behavior first.
- Historical annual rebuilds are still needed after Lahman publishes final
  season CSVs, so temporary `mlbam_<id>` player ids can be reconciled later.
- Cross-sport daily importers are not implemented yet; this document records
  the recommended order and source pattern.
