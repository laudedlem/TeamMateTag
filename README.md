# Teammate Tag — data pipeline

This is the data layer for Teammate Tag (codename `base2nerdle`,
production domain teammatetag.com): a baseball teammate-chain game.
The pipeline produces a SQLite database (or Postgres, in production) with:

- **`players`** — every MLB player who played in your year window
- **`teams`** — every team-season in the window
- **`appearances`** — one row per (player, team, season) with games played
- **`teammates`** — the derived graph: one row per (player_a, player_b, team, season) where they shared a roster and both appeared in ≥1 game
- **`players_searchable`** — autocomplete-ready view with display labels and degree counts
- Multiplayer state: `users`, `games`, `game_participants`, `game_moves`, `connection_reports`

Everything is keyed for the three goals: incremental update during the season, easy expansion to earlier years, ready for multiplayer from day one.

## Run it

You need Python 3.10+ and `requests`:

```bash
pip install requests
```

### One-time: full historical load (2000–latest completed season)

```bash
python3 etl/01_download_lahman.py             # ~30 MB, takes <1 min
python3 etl/02_load_lahman.py --start-year 2000
python3 etl/04_load_chadwick_ids.py           # adds mlbam_id + nicknames
python3 etl/03_build_teammates.py             # build graph + searchable index
python3 tests/verify_graph.py                 # sanity-check
python3 scripts/analyze_graph.py              # graph diagnostics
```

This produces `db/base2nerdle.sqlite`. Expect roughly:
- ~7,000 players (debuted 2000+ or active in/after 2000)
- ~600 team-seasons
- ~50,000 appearances
- ~3 million teammate-season edges  (one per shared roster-year)
- ~600,000 unique teammate pair edges  (deduplicated across seasons)

The order matters: load Chadwick before building the searchable index, so
nicknames flow through to the autocomplete table.

### Daily during the season: refresh current year

```bash
python3 etl/05_update_current_season.py --season 2026
python3 etl/03_build_teammates.py --season 2026
```

`--season` on the rebuild means we only redo the current year's edges, not the whole graph. Takes ~1 second.

Wire this up as a daily cron / GitHub Action / Supabase scheduled function. Twenty minutes of API calls during the season, free.

### Annually: full Lahman rebase

After each season ends and SABR releases an updated Lahman, re-run the historical load. This corrects any in-season data drift and ingests the official, audited season totals.

### Expand backward (1990s, 1980s, deadball era…)

Just rerun the loaders with a wider window:

```bash
python3 etl/02_load_lahman.py --start-year 1990
python3 etl/03_build_teammates.py
```

No schema changes needed. Lahman has clean data back to ~1900; before that, things get messier.

## Try it without downloading the real data

The repo ships with hand-crafted sample data covering several real 2000s teams (`data/sample/`). Useful for testing changes without re-running the full load:

```bash
python3 etl/02_load_lahman.py --raw-dir data/sample --start-year 2000 --end-year 2010
python3 etl/03_build_teammates.py
python3 tests/verify_graph.py
python3 scripts/query_examples.py
```

`query_examples.py` shows the three queries your multiplayer server runs on every move:
1. **autocomplete** — search-as-you-type
2. **teammate_check** — was X ever a teammate of Y?
3. **is_valid_move** — full validation including no-repeats-in-chain

## Switching to Postgres

The schema is written in standard-ish SQL. To run in Postgres (Supabase, RDS, etc.):

1. Search/replace the `-- PG:` comments in `db/schema.sql`:
   - `TEXT` → `UUID` for `user_id`, `game_id` columns
   - `TEXT … DEFAULT CURRENT_TIMESTAMP` → `TIMESTAMPTZ … DEFAULT now()` for timestamps
   - `rule_set TEXT` → `rule_set JSONB`
2. Replace `sqlite3.connect(...)` with `psycopg.connect(...)` in the loader scripts.
3. The teammate-graph build SQL (the self-join) runs as-is on Postgres.

Do this when you're ready to run multiplayer; for solo dev, SQLite is fine.

## Critical design decisions

**"At least one game together" is approximated at the season level.** A player traded in May and a callup in August on the same team in the same year will be marked teammates even if they never overlapped. Rare; handled via `connection_reports`.

**Player ID stability.** We use Lahman's `playerID` as primary key (e.g. `jeterde01`), with `bbref_id`, `retro_id`, and `mlbam_id` as crosswalks. The `mlbam_id` is the bridge to the MLB Stats API for in-season updates.

**Stub player IDs.** When statsapi gives us a player who's not in Lahman yet (debuted this season, Lahman hasn't shipped this year's release), we generate a stub like `lastnxx99_mlbam12345`. These get reconciled to canonical Lahman IDs at the next annual rebase.

**Server-authoritative.** All move validation runs against this DB on the server. Never trust the client; users will inspect the network tab.

## What's not here yet

- **Hosting / deployment.** Local web app only at the moment. Vercel +
  Supabase Postgres + Supabase Auth is the planned stack; accounts are
  active, deployment is the immediate next step.
- **Persistence.** Game state lives in process memory; no leaderboards,
  no game history. Migrating to Supabase Postgres replaces the in-memory
  dicts in `web/server.py`.
- **Auth.** Supabase Auth will fill the `users` table.
- **Real-time multiplayer.** Currently only same-keyboard Division Rivalry
  works. Cross-network multiplayer needs Supabase Realtime.
- **Retrosheet game-log integration** for strict same-day-roster overlap.
  The current season-level proxy handles >99% of cases correctly.
  False positives flow through `connection_reports`.

## Game runtime

The web app lives in `web/`, the rules engine in `game/engine.py`, and a
terminal CLI fallback in `game/cli.py`. Three modes:

- **Batting Practice** — solo, 30s timer, team strikeouts. Replay to beat
  your longest Lineup.
- **Film Review** — daily 9-player puzzle, guess the team and year
  linking each pair. 3 strikes you're out.
- **Division Rivalry** — two-player head to head, 30s clock per turn.

To run:

```bash
pip install -r requirements.txt
python web/server.py
# then open http://127.0.0.1:5000/
```

CLI fallback (Division Rivalry only, two humans at one keyboard):
`python game/cli.py`.

For deployment, see `DEPLOY_VERCEL.md`.
See `CLAUDE.md` for full mechanics, API surface, and roadmap.

## Tools in the kit

| Script                              | When to run                                                |
|-------------------------------------|------------------------------------------------------------|
| `etl/01_download_lahman.py`         | Annually, after each MLB season ends                       |
| `etl/02_load_lahman.py`             | After download, or when expanding year range               |
| `etl/04_load_chadwick_ids.py`       | After Lahman load, or when refreshing nicknames            |
| `etl/03_build_teammates.py`         | After any data change (idempotent; supports `--season`)    |
| `etl/05_update_current_season.py`   | Daily during the season                                    |
| `tests/verify_graph.py`             | After every data refresh — your regression guardrail       |
| `scripts/analyze_graph.py`          | When tuning difficulty or picking daily-challenge starters |
| `scripts/query_examples.py`         | Reference: the queries your game server runs               |
| `scripts/name_normalize.py`         | Library: import wherever you accept user-typed names       |
