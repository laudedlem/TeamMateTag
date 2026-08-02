# TeamMateTag project handoff

Update this file whenever product behavior, deployment, data, or active work
changes. It is the concise source of truth for another coding assistant.

## Product and deployment

- Production: `https://teammatetag.com`
- Vercel deployment: `https://teammatetag.vercel.app`
- Repository: `https://github.com/laudedlem/TeamMateTag`
- Local repository folder: `C:\Users\laude\Desktop\base2nerdle`
- Current display version: `0.1.36`
- Stack: Flask + vanilla JavaScript on Vercel, Supabase Postgres, Supabase
  Auth, server-side session cookie.
- Supabase runtime catalog: the non-baseball game data was imported on
  2026-08-01. Database size is 170 MB, below the Free plan's 500 MB database
  quota. The old materialized Baseball `teammates` table was removed because
  it alone consumed roughly 400 MB; all game paths derive links from indexed
  appearances instead.
- Required environment values are documented in `.env.example`. Never commit
  `.env` or any Supabase password/key.

## Current user experience

- `/` is the sport-selection home. It includes account/profile access and
  links to Baseball, Basketball, Hockey, and Football.
- `/baseball` is the live baseball game hub with four modes.
- `/basketball`, `/hockey`, and `/football` are local playtesting hubs when
  `TEAMMATETAG_LOCAL_SPORTS=1`. Their roster graphs live in local SQLite and
  are now mirrored into Supabase but are not yet used by Vercel. The remaining
  work is to replace their local SQLite adapters with Postgres-backed runtime
  adapters.
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

## Cross-sport multiplayer staging

- Basketball, hockey, and football now expose local Division Rivalry through
  `/api/local/<sport>/dr/*`. The client uses the same queue, game, move,
  server-clock, forfeit, rematch, and requeue contract as production baseball.
- These local matches are process-memory playtesting only. They intentionally
  omit account statistics, challenge codes, and friends until the sports data
  moves to persistent shared storage for deployment.
- The frontend selects the local adapter only for non-baseball sports. A
  production migration can preserve the client endpoints and replace the
  local adapter with a database-backed implementation.
- Basketball, hockey, and football now also have local Playoffs at
  `/api/local/<sport>/po/*`. Each has sport-specific names for five tactical
  powerups and selectable win conditions using indexed career, peak-season,
  award, longevity, movement, and championship data.
- `scripts/load_local_sport_traits.py --download-nhl` builds the ignored,
  additive `sport_player_traits` table. It currently loads NBA regular-season
  totals from the local CC0 Kaggle archive, NFL weekly totals from nflverse,
  and NHL career totals from the CC BY Kaggle player database.
- `sport_player_season_traits` is the indexed local table for condition
  evaluation. It holds 25,525 NBA, 47,577 NFL, and 44,611 NHL player-season
  rows, so peak-season win conditions are a lookup rather than a runtime scan.
  NBA season stats are current through 2025, NFL through 2024, and HockeyDB
  season statistics through 2017; modern NHL career traits remain available.
- Current source gaps are explicit in `sport_trait_provenance`: 746 NHL
  career-stat source names did not uniquely resolve to local player IDs.
- NBA awards resolve 498 local players with MVP, Rookie of the Year, or
  All-Star counts. NHL awards now resolve 322 players with Hart, Calder, or
  First/Second Team All-Star counts through season-aware Hockey Databank name
  matching. NFL honors are stored in the local honors table.
- Championship counts are championship credits, not a claim that every player
  received a physical ring. NBA derives 73 Finals champion seasons from local
  box scores. NFL honors history credits Super Bowl I-LIX roster membership.
  NHL credits playoff participants from HockeyDB through 2017 and season-roster
  membership for 2018 onward. NHL covers all 107 awarded Cup seasons and
  explicitly excludes 1918-19 and 2004-05, when no Cup was awarded.
- `scripts/load_local_honors_history.py` loads NFL Super Bowl I-LIX, honors,
  and all-pro history. It also creates `sport_honors` and
  `sport_honor_unresolved`, preserving honors facts and any unresolved source
  rows for later matching. NFL coverage includes AP MVP, offensive/defensive
  ROY, Pro Bowl selections, and AP first-team All-Pro selections (1999-2025).
  See `docs/local_honors_data.md` for the required refresh order.
- Historical honors cleanup on 2026-07-31 added conservative season-aware and
  unique-surname resolution. It reduced the persisted unmatched history to 43
  NBA, 565 NHL, and 825 NFL source records without guessing among duplicate
  players. The loader explicitly excludes the cancelled 2004-05 NHL season
  from Stanley Cup credits.
- `scripts/reconcile_local_identities.py` is the durable, source-aware local
  identity layer. It imports raw honors observations, accepted legacy links,
  and ranked candidates into separate SQLite tables, then writes
  `db/identity_review_queue.csv` for review. Manual accepted matches are
  preserved across refreshes. See `docs/player_identity_reconciliation.md`.
- `scripts/supplement_hockeydb_history.py` is the NHL historical graph
  supplement. It stores HockeyDB external IDs, fills NHL player-team-season
  stints missing from the roster API, and adds official NHL stats IDs and
  appearances for 2024-25 and 2025-26. Run it before honors and reconciliation
  refreshes. The active identity review queue is now 285 references: 4 NBA,
  179 NFL, and 102 NHL. NFL's remaining entries are same-name collisions and
  require source-page identifier extraction rather than name matching.
- Historical source scope is MLB 1903 onward, NBA/BAA 1946-47 onward (never
  ABA), NHL 1917-18 onward, and NFL 1966 onward. Run
  `scripts/supplement_nfl_reference_ids.py` after honors and before
  reconciliation to bridge Wikipedia award pages to nflverse roster IDs.
- The current active identity queue is 195: 4 NBA, 91 NFL, and 100 NHL after
  the identifier and season-label cleanup passes.
- `scripts/supplement_nhl_official_ids.py` resolves NHL career-stat references
  through official NHL search and per-season game logs. It records brief
  call-up team stints and reduced the active queue to 131: 4 NBA, 91 NFL,
  and 36 NHL.
- Pro Bowl source positions now combine with season and career dates to resolve
  same-name NFL players. The active queue is 103: 4 NBA, 71 NFL, and 28 NHL.
- `scripts/supplement_nba_historical_ids.py` preserves evidence-backed
  NBA/BAA historical identities absent from the base archive. It resolves the
  final four NBA award references: the 1980-81 Atlanta guard Eddie Johnson and
  Alex Groza's Indianapolis Olympians seasons.
- `scripts/supplement_nhl_official_ids.py` now also compares source career-game
  totals with official NHL records to split same-name players. The active
  identity queue is now 96: 71 NFL and 25 NHL. There are no active NBA
  references.
- Official NHL search-name aliases resolve transliteration and punctuation
  differences only after position and season-game-log verification. This
  reduced the active queue to 90: 71 NFL and 19 NHL.
- `scripts/download_nfl_rosters.py` caches the missing 2002-25 nflverse
  roster releases. `scripts/supplement_nfl_roster_identities.py` uses a
  unique player-year roster identity to add missing canonical players and
  promote source honors, including initials written with or without spaces.
  `scripts/supplement_hockeydb_identity_claims.py` resolves historical NHL
  identities only when HockeyDB's name, first season, position, and career
  games all agree. The active queue is now 43: 27 NFL and 16 NHL.
- `scripts/supplement_hockey_reference_identities.py` uses verified
  Hockey-Reference player IDs for historical names unavailable or differently
  spelled in HockeyDB and the NHL API. NHL's active reconciliation queue is
  now zero; the remaining queue is 27 NFL references.
- `scripts/supplement_pfr_identity_claims.py` resolves the final NFL
  same-name records using reviewed Pro Football Reference player IDs, position,
  and career-window evidence. The active cross-sport identity reconciliation
  queue is now zero. Data-only reconciliation passes update this file but do
  not change the visible application version.
- `scripts/build_game_data_catalog.py` builds the canonical game-facing SQLite
  views: `game_player_catalog`, `game_team_season_catalog`, and
  `game_teammate_links`. It also writes `game_data_audit` with per-sport
  coverage and the active reconciliation count. Future cross-sport game logic
  should use these views rather than the raw import tables.
- Cross-sport Playoffs uses `LOCAL_PLAYOFF_CONFIG` in `web/server.py` for
  basketball, football, and hockey. Each sport has its own powerup names,
  qualification stat, timer pressure, and 11 distinct condition types. Every
  sport has seven powerups: five +5-second expanded-link tools, one +15-second
  clock tool, and one opponent-time pressure tool. The
  current pool avoids duplicated career-stat objectives: where a stat appears
  twice, one is a peak-season feat and the other is career-based. Do not bump
  the visible version for data-only maintenance.
- Playoffs reference behavior: the global home page provides a sport selector;
  a sport page shows only that sport's powerups. Cross-sport win conditions
  now include All-Star or Pro Bowl selection marathons and championship totals.
  Three burned team-seasons or a completed win condition ends the game.
- Playoffs condition pools are intentionally broad and selectable. Basketball,
  football, and hockey each have 11 options, including peak-season feats,
  career totals, career-franchise, longevity, movement, honors, and combined
  championship totals. Baseball has eleven options. The condition eligibility
  audit is run against the local catalog before changing thresholds; the rare
  single-player options currently have 11-48 qualifiers, while combined and
  multi-player options have larger pools by design.
- Refresh order for full local Playoffs data: run
  `python scripts/load_local_sport_traits.py`, then
  `python scripts/load_local_honors_history.py`. The second command restores
  complete NFL championship credits after the nflverse trait refresh.
- `scripts/migrate_cross_sport_to_postgres.py` is the idempotent deployment
  importer. It replaces only Basketball, Football, and Hockey runtime rows in
  Supabase, preserving Baseball and account data. It imports franchises,
  team-seasons, players, appearances, search rows, Playoffs traits, and season
  traits. It intentionally excludes raw sources, identity curation, unresolved
  records, and headshots. Do not materialize `sport_teammates` or `teammates`:
  indexed appearances are the source of teammate links and keep Free-tier
  storage viable.

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

Baseball, football, hockey, and basketball can generate valid
role-constrained chains. Basketball uses a Wikidata cache keyed by NBA.com
player ID for exact PG/SG/SF/PF/C eligibility. Its game-by-game source still
provides only broad G/F/C, so do not claim that the listed order of multiple
career roles measures minutes or starts at each position.

The initial exact basketball-position coverage is 93.01% (4,548 of 4,890
local NBA players). `scripts/refresh_bref_nba_position_gaps.py` is a slow,
resumable Basketball-Reference fallback for the remaining records. It must be
run conservatively because the source rate-limits requests. Do not call the
NBA position metadata complete until the fallback resolves the remaining
eligible players and a quality pass is rerun.

### Cross-sport Film Review (local playtesting)

Update, 2026-08-01: the compact cross-sport catalog is now in Supabase and
contains 39,947 players, 37,261 position rows, 210,721 appearances, 24,088
aggregate trait rows, 117,227 season trait rows, and 27,431 remote headshot
URL records. The live database measures roughly 165-171 MB, below the Free
plan's 500 MB database threshold. Raw source archives and the 20+ GB local
headshot cache are intentionally not uploaded. `sport_teammates` and the old
Baseball `teammates` materialized pair graph must remain empty/dropped: gameplay
derives links from indexed appearances.

`web/server.py` now has Postgres-backed routes for non-baseball Batting
Practice and Film Review: `/api/sports/<sport>/bp/*` and
`/api/sports/<sport>/fr/*`. These are persistent and use the exact same game
engine as local play. `TEAMMATETAG_LOCAL_SPORTS=1` still intentionally routes
local development through SQLite.

Update, 2026-08-01 (0.1.34): non-baseball Division Rivalry and Playoffs now
use `sport_online_games`, `sport_online_queue`, and sport-scoped rematch/
postgame tables in Supabase. Each sport has independent random queues, game
state, Playoffs condition selection, seven powerups, player usage, ELO, and
Division Rivalry result rows. The original Baseball tables and routes remain
unchanged. Cross-sport friend/code challenges are intentionally hidden for now
because their existing Baseball invitation/history tables do not have a sport
field; do not claim those are cross-sport-ready until ported.

Update, 2026-08-01 (0.1.35): Playoffs powerups render in a closed `Powerups`
dropdown directly above the guess form, keeping the current player card visible
immediately below the form. The shared deck renderer now tolerates a transient
missing chain response without clearing visible cards. Verified NFL DR: Devin
Hester to Matt Forte is valid through the 2008-2013 Chicago Bears and the
Postgres response contains both cards and a fresh turn clock.

Update, 2026-08-01 (0.1.36): fixed the Postgres grouping error in the
cross-sport Playoffs trait query. Previously a valid NFL Playoffs link could
be applied and then fail while evaluating its win condition, leaving the
browser on stale state. Devin Hester to Matt Forte now returns `valid` through
the online Playoffs endpoint. The powerup dropdown lays both players' lists
side-by-side when opened, and win-condition boxes are more compact.

Profile responses now include `stats.sports.baseball|basketball|football|hockey`.
New cross-sport BP/FR results write their sport ID, so stats are isolated.
`guest_sport_ratings` and `sport_player_usage` are ready for cross-sport
multiplayer results and data reporting.

Film Review is available on every ready sport page. Baseball continues to use
the production Postgres Film Review API. Basketball, hockey, and football use
the local SQLite daily-deck adapter at `/api/local/<sport>/fr/*` while their
appearance data remains local. The new local decks use the required lineup
sizes: basketball 12, hockey 11, football 12 per selected unit. Football asks
the player to choose offense (11 positions plus K) or defense (11 positions
plus P). Film Review returns explicit lineup slots so the frontend can fill a
baseball diamond, basketball rotation, hockey lines, or football formation as
players are revealed. The board begins with only the leadoff player filled;
each later position fills only after the preceding team/year connection is
solved. Hockey presents two rows under adjacent LW, C, RW, LD, RD, and G
columns. Film Review feedback is sport-specific: baseball Hit/Foul/Strike,
basketball Bucket/Rim Out/Turnover, hockey Goal/Offside/Penalty, and football
Complete/Incomplete/Turnover. They use the same team-plus-year
guessing, foul streak, three-strike, and full-lineup reveal behavior as
baseball. Do not describe the non-baseball modes as deployed until the
cross-sport data is moved out of the ignored local SQLite database.

Football board order is fixed for presentation: offense has OT, OG, C, OG, OT;
then WR, WR, WR, TE, RB; then QB and K. Defense has EDGE, DT, DT, EDGE; then
OLB, MIKE, OLB; then CB, S, P, S, CB.

Use `python scripts\generate_film_review_local.py football --unit offense`
or `--unit defense` to print a daily Film Review test key.

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
