# TeamMateTag project handoff

Update this file whenever product behavior, deployment, data, or active work
changes. It is the concise source of truth for another coding assistant.

## Product and deployment

- Production: `https://teammatetag.com`
- Vercel deployment: `https://teammatetag.vercel.app`
- Repository: `https://github.com/laudedlem/TeamMateTag`
- Local repository folder: `C:\Users\laude\Desktop\base2nerdle`
- Current display version: `0.1.22`
- Stack: Flask + vanilla JavaScript on Vercel, Supabase Postgres, Supabase
  Auth, server-side session cookie.
- Required environment values are documented in `.env.example`. Never commit
  `.env` or any Supabase password/key.

## Current user experience

- `/` is the sport-selection home. It includes account/profile access and
  links to Baseball, Basketball, Hockey, and Football.
- `/baseball` is the live baseball game hub with four modes.
- `/basketball`, `/hockey`, and `/football` are intentionally visible but
  unavailable until their roster graphs are loaded.
- The header brand is `TeamMateTag`; version number is shown beside it.
- Use ASCII in code and player-facing strings. Do not use em dashes.

## Baseball modes

- **Batting Practice**: solo endless lineup, 20-second server-authoritative
  turn timer, 3-second opening countdown, Rule B team strikes, daily top-nine
  longest-lineup leaderboard.
- **Film Review**: nine-player / eight-connection puzzle. Guess team and year.
  A correct answer advances; one correct field is a foul. The first foul in a
  streak is safe; each additional consecutive foul is a strike. Three strikes
  ends the puzzle and shows the completed lineup.
- **Division Rivalry**: online two-player alternating lineup. 20-second turns,
  Rule B team strikes, matchmaking, challenge codes, friends, rematches,
  requeueing, ELO, and profile statistics.
- **Playoffs**: Division Rivalry plus one of each powerup per player and a
  chosen or random win condition. Each player's latest win-condition choice
  is remembered for the next match. Balance and quality testing are deferred.

## Shared lineup rules

- A move must name a teammate of the last player, and a player cannot repeat
  in the same lineup.
- Every shared team-season receives a strike on a valid connection.
- At three strikes a team-season is **Struck Out**.
- Rule B: if any shared team-season for a proposed pair is Struck Out, the
  entire move is invalid even if another shared team-season remains open.
- Teammate membership is season-level: both players need an appearance for
  that team in that season. Rare real-world non-overlaps are accepted.

## Baseball data

- Current production scope: MLB 2000 through 2025. Lahman files locally cover
  1871-2025, but the full historical pair graph does not fit alongside the
  live graph in the current free Supabase database. Historical expansion is
  blocked on storage migration or a game-engine move to on-demand connections.
- Production tables: `players`, `teams`, `appearances`, `teammates`,
  `players_searchable`, plus supporting franchise and nickname tables.
- IDs are Lahman-style player IDs. Pair table invariant:
  `player_a_id < player_b_id`.
- Baseball sources/pipeline: Lahman plus Chadwick crosswalk. See `etl/`,
  `db/schema_postgres.sql`, and `scripts/migrate_to_postgres.py`.
- 2026 and pre-2000 baseball expansion remain deferred.

## Cross-sport expansion: active work

Goal: NBA, NHL, and NFL should use the same four-mode structure as baseball.
Baseball terminology can be generalized later, after the game data works.

### Local multi-sport dataset

`scripts/build_local_sports_dataset.py` builds the ignored local SQLite file
`db/teammatetag_local.sqlite`. It uses indexed player-team-season appearances,
not a materialized player-pair graph, so the full local histories remain
practical to build and query. Validate it with:

```powershell
python scripts\build_local_sports_dataset.py
python scripts\verify_local_sports_dataset.py
python scripts\clean_local_sport_data.py
```

Current source scopes are MLB 1871-2025 (Lahman), NFL 1966-2025 (nflverse),
NBA 2002-2025 (SportsDataverse ESPN box scores), and NHL 1917-2025 (NHL public
roster API). The NBA source has no reusable pre-2002 history in this pipeline;
do not describe it as a full NBA-history dataset until that source gap is
resolved. Raw downloads and the SQLite database are intentionally ignored by
Git and must not be committed. This local dataset has not been loaded to
Supabase.

Do not label data as complete through 2026 yet. As of July 2026, Lahman only
contains completed seasons through 2025. NFL and NHL can be refreshed during
their active seasons from their roster sources; NBA needs a verified current
season source refresh.

### Current data-quality blockers

- The NHL builder includes the `20252026` source season, but the official
  roster snapshot cached at `raw/nhl/WPG/20252026.json` does not contain
  Jonathan Toews. Treat historical NHL roster snapshots as incomplete until a
  second source based on player-game appearances or transactions is added.
- Jonathan Toews is a confirmed example: his official player landing record
  reports Winnipeg Jets, 82 games, 2025-26. The local appearance was inserted
  and `scripts/refresh_nhl_player_seasons.py` generalizes this player-season
  backfill method.
- Do not promise a headshot for every historical player yet. Native NBA and
  NFL URLs leave legacy gaps. Before production, build a cached photo resolver
  with league-native URLs first and a rights-cleared fallback source, recording
  source and license per image.
- Team stints on local cards now consolidate separated returns to one team into
  comma-separated year ranges.
- `scripts/audit_local_sport_data.py` reports coverage and short career gaps.
  `scripts/run_local_data_quality_pass.py` recomputes football/hockey career
  positions from raw source rows and writes a structural quality report.
  `scripts/build_local_photo_cache.py` caches league-native NBA, NFL, and NHL
  headshots locally with source URLs. Do not use Wikimedia fallbacks in public
  production without a licensing review.
- Native cache result: 30,561 images, 19.82 GiB. NBA native coverage is
  complete; NHL is missing 7 players; NFL is missing 8,741, mostly historical
  records without a native image URL. `scripts/report_missing_headshots.py`
  writes the unresolved-player CSV for the licensed fallback-source decision.
- `scripts/remove_placeholder_headshots.py` removes known NBA/NFL silhouette
  responses that otherwise return HTTP 200 and would be mistaken for photos.
- `scripts/apply_verified_photo_overrides.py` holds reviewed Commons fallback
  images with source and license notes. Current overrides: Devin Hester (public
  domain) and Lance Briggs (CC BY-SA 3.0).
- `scripts/find_wikimedia_photo_candidates.py` records explicit Commons
  candidates or `no_candidate` states for native-image misses. It does not
  publish candidates until the image/player match and license are reviewed.

The preferred NBA-history replacement is Kaggle dataset
`eoinamoore/historical-nba-data-and-player-box-scores`, which is marked CC0
and reports player box scores from 1947 onward. It is about 1.86 GB. After
extracting it into `raw/nba_kaggle/`, run
`python scripts\import_nba_kaggle.py`. The importer replaces only local NBA
rows and must be validated before changing the stated NBA scope.

1. Data source evaluation and ingestion for NBA, NHL, NFL.
2. Build season-level roster/appearance records and derived teammate graphs.
3. `game/engine.py` now supports local sport-aware player lookup and
   teammate validation through the appearance model. It is backward compatible
   with the live baseball pair-table engine.
4. Next implementation: local browser Batting Practice for NFL, NBA, and NHL,
   then a generic server/frontend adapter before any production data migration.

The new additive schema is `db/cross_sport_schema_postgres.sql`:

- `sports`, `sport_franchises`, `sport_teams`
- `sport_players`, `sport_appearances`, optional `sport_teammates`
- `sport_players_searchable`, `sport_player_aliases`,
  `sport_data_provenance`

It deliberately does not alter the working baseball tables. Apply it with:

```powershell
cd C:\Users\laude\Desktop\base2nerdle
python scripts\setup_cross_sport_schema.py
```

That migration is additive and safe to run repeatedly.

Data-source research and rollout criteria are maintained in
`docs/cross_sport_data_plan.md`. `scripts/load_nfl_superbowl_era.py` remains a
production loader only for a future larger database. Do not run it against the
current free Supabase project. The local builder is now the source-of-truth
development artifact until the generic on-demand connection engine is built.

## Important files

- `web/server.py`: Flask routes, database access, game state, auth, online play.
- `web/static/main.js`: client state, rendering, timers, autocomplete, polling.
- `web/static/style.css`: dark UI styles.
- `web/templates/index.html`: sport home and current baseball screens.
- `game/engine.py`: shared baseball lineup validation.
- `db/schema_postgres.sql`: original baseball production schema.
- `db/cross_sport_schema_postgres.sql`: generic NBA/NHL/NFL schema.
- `scripts/build_local_sports_dataset.py`: local all-sport dataset builder.
- `scripts/verify_local_sports_dataset.py`: local scope and teammate validator.

## Known follow-ups

### Film Review lineup generation (in progress)

Film Review is moving from a static nine-card baseball deck to deterministic
daily lineup chains. The requested card counts and slots are documented in
`docs/film_review_lineups.md`:

- Baseball: 10 cards: C, 1B, 2B, 3B, SS, LF, CF, RF, DH, SP.
- Football: 24 cards: a complete 11-player offense plus K, then a complete
  11-player defense plus P.
- Hockey: 11 cards: 2 LW, 2 C, 2 RW, 4 D, G.
- Basketball: 12 cards: 2 PG, 2 SG, 2 SF, 2 PF, 2 C, 2 flexible cards.

`game/film_review_generator.py` creates deterministic local chains for a
given sport and calendar day. It requires every adjacent pair to share a
unique team-season, never repeats a player, and favors modern established
players. `scripts/run_local_data_quality_pass.py` now populates
`sport_player_positions`, which preserves all usable player positions rather
than reducing each player to one card label.

Use these local checks before connecting generated decks to the web mode:

```powershell
python scripts\run_local_data_quality_pass.py
python scripts\generate_film_review_local.py baseball
python scripts\validate_film_review_local.py --days 14
```

Baseball, football, and hockey can generate valid role-constrained chains.
Basketball remains intentionally blocked because the current source only
provides broad G/F/C labels. Do not map those labels to PG/SG/SF/PF. A source
with real basketball positions is required before activating its lineup mode.

- The apex DNS record is currently missing: public resolvers return no A/AAAA
  record for `teammatetag.com`, while `www.teammatetag.com` has a Vercel CNAME.
  This explains the Firefox failure and can also affect Chrome when its cache
  expires. In Cloudflare DNS, add the exact apex A record shown in Vercel's
  Project Settings > Domains. Vercel's general fallback is `76.76.21.21`.
- Supabase password-reset links may still route through an unwanted Vercel
  sign-in flow. This was explicitly deferred.
- Update `README.md` after the cross-sport data sources and first loader are
  selected. It is currently stale.
- Complete a later baseball quality, rules, and Playoffs balance pass.
- Supabase storage: full NFL roster data (118,070 player-team-seasons) plus
  baseball pair edges exceeded the free project capacity. Do not rerun the
  historical NFL or baseball loaders against production until moving to a
  larger database or changing the connection storage/query strategy.
