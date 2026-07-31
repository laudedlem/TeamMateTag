# Cross-sport data plan

## Goal

Build validated season-level teammate data for NBA, NHL, and NFL before
enabling their game modes. A player is connected to other qualifying players
through indexed team-season appearances. Materialized pair graphs are optional
because large league roster histories can exceed Supabase storage limits.

## Local development dataset

The current development artifact is the ignored SQLite file
`db/teammatetag_local.sqlite`, built with
`scripts/build_local_sports_dataset.py` and validated by
`scripts/verify_local_sports_dataset.py`. It stores player-team-season
appearances and indexes the two directions needed for on-demand teammate
queries. It deliberately does not store every player pair.

| Sport | Current local source scope | Source |
| --- | --- | --- |
| MLB | 1871-2025 | Lahman CSVs |
| NFL | 1966-2025 | nflverse annual and weekly rosters |
| NBA | 2002-2025 | SportsDataverse ESPN player box scores |
| NHL | 1917-2025 | NHL public roster API |

The NBA pipeline does not yet include a reusable pre-2002 source. This is a
known scope gap, not a claim that NBA history is complete. Raw data and the
generated local SQLite database are ignored by Git.

## Data model

The production tables are defined in `db/cross_sport_schema_postgres.sql`.
Every new table includes `sport_id`, so player and team identifiers from
different leagues cannot collide. Baseball remains in its established tables
until a later optional consolidation.

For all three leagues, retain raw source files outside Git, save source and
row-count metadata in `sport_data_provenance`, and validate before activating
the sport in the public UI.

## Source evaluation as of 2026-07-31

### NFL

- Implemented loader: `scripts/load_nfl_superbowl_era.py`, covering 1966-2025.
- Primary candidate: nflverse weekly rosters, 2002 onward.
- Confirmed reachable source pattern:
  `https://github.com/nflverse/nflverse-data/releases/download/weekly_rosters/roster_weekly_2024.csv`
- The weekly files preserve in-season team changes. For example, 2024 rows
  contain both Las Vegas and New York Jets weeks for Davante Adams.
- Use annual nflverse roster files for 1966-2001, then document that these
  seasons do not have the same in-season transfer fidelity.
- Do not use the annual roster file alone for 2002 onward because it is a
  season-end snapshot and loses earlier-team memberships for traded players.
- Load attempt result: all 1966-2025 raw files were downloaded and 118,070
  player-team-season records were validated, but the free Supabase project
  could not retain those records alongside the existing baseball pair graph.
  The production NFL rows were removed to restore baseball. The cached raw
  files remain in `raw/nfl/` and are ignored by Git.

### NHL

- Implemented in the local builder with NHL public roster endpoints.
- The loader discovers each club code's available roster seasons, fetches
  forwards, defensemen, and goalies, and retries temporary API throttling.
- Local validation covers 1,658 team-seasons and 54,270 player-team-seasons
  across 1917-2025. Team display-name and franchise-history normalization will
  be completed as part of the sport adapter, before the NHL UI is enabled.

### NBA

- SportsDataverse ESPN player box scores provide a repeatable 2002-2025 local
  pipeline. A player is retained only when the box score records nonzero
  minutes, which produces 13,757 player-team-seasons.
- The official `stats.nba.com` endpoint still reset this environment and NBA
  CDN endpoints returned HTTP 403. Do not depend on undocumented scraping.
- Pre-2002 NBA history remains a future licensed-source or permitted-API task.

## Build order

1. Implement a sport-aware, on-demand connection adapter against the local
   schema, replacing the baseball-only pair-table assumptions.
2. Add team/franchise display-name normalization for NFL, NBA, and NHL.
3. Enable Batting Practice first for each
   loaded league.
4. Expand Film Review, Division Rivalry, and Playoffs after solo validation.
5. Select a production database plan only after measuring the on-demand query
   load and data size. Do not load the full local histories to the existing
   free Supabase project.

## Validation requirements

- Count players, player-team-seasons, team-seasons, and on-demand teammate links.
- Verify each league has one usable graph component or document isolates.
- Test known traded-player records for each sport.
- Check autocomplete disambiguation for duplicate player names.
- Keep the sport inactive until move validation and a short manual game test
  pass on production.
