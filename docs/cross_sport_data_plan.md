# Cross-sport data plan

## Goal

Build a validated season-level teammate graph for NBA, NHL, and NFL before
enabling their game modes. A player is connected to every other qualifying
player on the same team in the same season. This matches the existing baseball
model and makes shared game rules portable.

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

### NHL

- Primary candidate: NHL public club endpoints.
- Confirmed reachable source patterns:
  `https://api-web.nhle.com/v1/roster/BOS/20242025`
  and `https://api-web.nhle.com/v1/club-stats/BOS/20242025/2`.
- The loader should fetch every active team code for every season, combine
  skaters and goalies, and deduplicate each player-team-season.
- Before the full load, validate historic franchise codes and relocations,
  especially Atlanta/Winnipeg, Minnesota, and Arizona/Utah.

### NBA

- The official `stats.nba.com` player-season endpoint was tested but reset the
  connection from this environment. NBA CDN endpoints returned HTTP 403.
- Basketball Reference is technically reachable, but its terms should be
  reviewed or a license obtained before using it as an automated production
  source. Do not make the public game depend on undocumented scraping.
- Next decision: locate a reusable licensed NBA player-team-season dataset or
  confirm a permitted official API access path. This is the current blocker
  for a fully automated NBA ingestion pipeline.

## Build order

1. Run and validate the NFL Super Bowl-era loader.
2. Implement and validate NHL loader using official club roster data.
3. Resolve NBA source and loader.
4. Add a sport-aware game adapter and enable Batting Practice first for each
   loaded league.
5. Expand Film Review, Division Rivalry, and Playoffs after solo validation.

## Validation requirements

- Count players, player-team-seasons, team-seasons, and teammate edges.
- Verify each league has one usable graph component or document isolates.
- Test known traded-player records for each sport.
- Check autocomplete disambiguation for duplicate player names.
- Keep the sport inactive until move validation and a short manual game test
  pass on production.
