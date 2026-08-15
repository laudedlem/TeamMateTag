# TeamMateTag project handoff

Update this file whenever product behavior, deployment, data, or active work
changes. It is the concise source of truth for another coding assistant.

## Product and deployment

- Production: `https://teammatetag.com`
- Vercel deployment: `https://teammatetag.vercel.app`
- Repository: `https://github.com/laudedlem/TeamMateTag`
- Local repository folder: `C:\Users\laude\Desktop\base2nerdle`
- Current display version: `0.3.0`
- Stack: Flask + vanilla JavaScript on Vercel, Supabase Postgres, Supabase
  Auth, server-side session cookie.
- Supabase runtime catalog: the non-baseball game data was imported on
  2026-08-01. Database size was roughly 170 MB after the sport-runtime import,
  below the Free plan's 500 MB database quota. The old materialized Baseball
  `teammates` table was removed because it alone consumed roughly 400 MB; all
  game paths derive links from indexed appearances instead.
- Required environment values are documented in `.env.example`. Never commit
  `.env` or any Supabase password/key.

## Current user experience

- `/` is the sport-selection home. It includes account/profile access and
  links to Baseball, Basketball, Hockey, and Football.
- `/baseball` is the live baseball game hub with four modes.
- `/basketball`, `/hockey`, and `/football` now use the shared sport runtime
  online when `DATABASE_URL` is present. Local playtesting can still use SQLite
  by running with `TEAMMATETAG_LOCAL_SPORTS=1`.
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

Update, 2026-08-01 (0.1.37): cross-sport multiplayer now reaps expired
Supabase games before every queue, status, game, move, and timeout request.
This is required on Vercel because a stopped browser poll cannot be trusted to
finish a 20-second game. It prevents abandoned games from being returned as
active forever and blocking either user from requeueing. Existing stale NBA,
NFL, and NHL test rows were finalized during this pass.

Update, 2026-08-01 (0.1.38): the shared Baseball engine now derives links
from indexed `appearances` rows, exactly as the cross-sport engine does. This
fixes Baseball Division Rivalry after removal of the obsolete 400 MB
`teammates` pair table; verified Anthony Rizzo to Kris Bryant returns valid.
Cross-sport Division Rivalry server state now reports client mode `mp` rather
than database mode `dr`. The former disabled the browser's multiplayer
countdown, timer, polling, and game-over handling. Verified NFL DR now returns
`mp`, a three-second countdown, and a 20-second clock.

Update, 2026-08-01 (0.1.39): cross-sport multiplayer polling is single-flight
and pauses while a move is submitted. It rejects an older response that would
shorten the chain, renders cards only when game state actually changes, and
does not reset the visual timer for ordinary poll drift. Cross-sport player
cards and team names are cached in a warm Flask process, avoiding repeated
Supabase hydration on every poll. This fixes delayed, disappearing, and
repeatedly animating cards seen in NBA/NHL Division Rivalry and applies to
Playoffs as well.

Update, 2026-08-01 (0.1.40): removed NBA exhibition records `All-Star Giannis`,
`All-Star LeBron`, `OGs`, and `Stripes` from the local source and production
catalog (18 appearances across four team-seasons). The importer excludes them
on future refreshes. They therefore cannot show on player cards or create
links. Profile Baseball struck-out teams now explicitly filter to Baseball,
and profile labels adapt to the selected sport. Playoffs condition widgets
show the full requirement in small italic text; multiplayer replay buttons
use sport-specific wording.

Update, 2026-08-02 (0.1.41): cross-sport rematch status now has the same
complete contract as Baseball, so a live opponent no longer incorrectly shows
"rematch unavailable". If the opponent leaves while a player has requested a
rematch, that player is automatically returned to the random queue; requesting
after a departure also requeues immediately. Move feedback uses sport-specific
out terminology. `patch_hockey_utah_transition.py` repairs eight continuous
Arizona-to-Utah player records, including Clayton Keller: ARI through 2023 and
UTA from 2024. Online hockey cards and link labels normalize to full team names.

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

Update, 2026-08-02 (0.1.42): cross-sport Film Review now uses a daily
Central-time puzzle identity beginning with Film Review #1 on 2026-08-01.
Each signed-in or guest profile gets one official attempt per sport per day;
the official game resumes after refresh and its result feeds a per-sport daily
win streak. The in-game Film Review Archive shows current and prior days as
unseen, completed, failed, or in progress. Older days can be reviewed after
completion or retried as archive practice without changing the daily streak.
The archive currently covers basketball, football, and hockey, whose
deterministic generators are backed by the production compact catalog.
Baseball still uses its established rotating Film Review deck and needs a
separate historical daily-deck generator before it can join this archive.
Basketball cards without a stored catalog image now fall back to NBA's official
headshot CDN, including Al Harrington and Austin Croshere. Cross-sport
multiplayer game-end summaries now use the correct sport language, including
"teams with game misconducts" in hockey.

Update, 2026-08-02 (0.1.43): Film Review #1 is now the 2026-08-01 archive
puzzle and the current 2026-08-02 puzzle is Film Review #2. The archive panel
was moved to the top of the Film Review screen so it cannot interrupt the
lineup board and the player-card connection chain.

Update, 2026-08-02 (0.1.44): Baseball Film Review now uses the same daily
attempt, archive, review, retry, and streak model as the other three sports.
Its former fixed rotating deck was removed. `scripts/load_baseball_film_review_positions.py`
loads compact Lahman position totals into `baseball_player_positions`; the
daily generator uses these roles plus historical teammates to deterministically
build a distinct 10-player chain with no repeated team-year link. The puzzle
date is its seed, so midnight Central automatically advances every sport to a
new shared puzzle without a scheduled task. The production position table was
loaded with 48,699 player-position totals. Film Review #1 is 2026-08-01 and
Film Review #2 is 2026-08-02.

Update, 2026-08-02 (0.1.45): fixed a Film Review archive loading race on
sport pages. The archive now waits for, and retries after, guest-profile
bootstrap instead of remaining empty if a player opens Film Review quickly.
Also added a one-time compatibility migration: an August 2 game created when
that date was incorrectly numbered #1 is replaced on the next start with the
correct deterministic Film Review #2. No browser storage clearing is needed.

Update, 2026-08-02 (0.1.46): daily Film Review decks are now immutable global
records in `film_review_daily_puzzles`, keyed by sport, Central date, and
Football unit. On first access for a date, the server generates and saves one
deck; every player then receives that exact deck forever in the archive, even
after data refreshes. No cron is required: archive dates are derived from the
Central calendar and midnight automatically exposes the new current day, whose
deck is created lazily on its first play. Verified valid 2026-08-03 generators
for Baseball (10), Basketball (12), Hockey (11), and Football offense/defense
(12 each).

Update, 2026-08-02 (0.1.47): existing in-progress daily games now seed their
deck into the immutable global puzzle catalog the next time they are resumed.
This closes the migration edge case from before the catalog was introduced and
ensures every active and future Film Review has a permanent archive deck.

Update, 2026-08-02 (0.1.48): Film Review titles display their puzzle date.
Football profile Film Review records now separately display offense and defense
results for newly completed games. The delete-account password input spans the
full profile form. The first solo mode is consistently named Manager Mode.
The home page now offers sport-first and mode-first navigation; dedicated
`/manager`, `/film`, `/division`, and `/playoffs` pages
route a selected sport into its existing game surface via `?mode=`. Multi-sport
online queue selection is intentionally not implemented yet: it needs a shared
lobby that atomically removes the player from every selected sport queue when
one match forms.

Update, 2026-08-02 (0.1.49): mode hubs now bootstrap the local profile.
Manager Mode sport tiles show the player's per-sport longest lineup. Film
Review lists each sport's current-day state and direct archive links, colored
by unseen, completed, or failed status. Query-mode launches wait for profile
bootstrap, so a Manager Mode or Film Review sport button enters the selected
mode directly instead of stopping on the sport home page.

Update, 2026-08-03 (0.1.50): mode hubs gained a first shared multi-sport
queue backed by `multi_sport_queue`: a player can select one or more sports,
match only on overlapping sport choices, and get redirected directly into the
matched sport page with `?mode=<mp|po>&game_id=...`. This pass originally kept
Baseball's old game tables underneath and was superseded by 0.1.51.

Update, 2026-08-03 (0.1.51): Baseball Division Rivalry and Playoffs now use the
same `sport_online_games`, `sport_online_queue`, `sport_online_rematches`,
`sport_online_postgame_exits`, and `sport_online_invites` lifecycle as
Basketball, Hockey, and Football. `/api/sports/baseball/<dr|po>/*` is handled
by the same generic multiplayer routes as the other sports. The shared helpers
adapt Baseball by using the original Baseball player, team, appearance,
powerup, and win-condition tables for validation and rendering while storing
the online game lifecycle in the same sport-scoped tables as every other
sport. Baseball challenge codes also use `sport_online_invites`; do not route
new Baseball multiplayer work through legacy `dr_games`, `po_games`,
`dr_queue`, `po_queue`, `dr_invites`, or `po_invites`.

Update, 2026-08-03 (0.1.52): the top sport tiles on `/division-rivalry` and
`/playoffs` now queue immediately for that selected sport instead of navigating
to the sport home page. Manager Mode and Film Review sport tiles still launch
directly into those games. The separate multi-sport queue panel remains for
players who want to select more than one sport before searching.

Update, 2026-08-03 (0.1.53): fixed mode-hub queue scope and Film Review
archives. Clicking a sport tile on `/division-rivalry` or `/playoffs` now
continues polling only that selected sport; it does not switch to the
multi-sport checkbox selection unless the player clicks the multi-sport
`Find Match` button. Manager Mode and Film Review query launches now call
`startBp()` and `startFr()` directly after profile bootstrap, rather than
routing through the sport home mode picker. `/film-review` shows a same-width
archive dropdown directly under each sport tile, with labels such as
`#2 - Aug 2 - in progress`; selecting an archived puzzle navigates directly to
that sport's Film Review puzzle URL.

Update, 2026-08-03 (0.1.54): static CSS and JavaScript assets now include the
visible app version as a query string, for example `main.js?v=0.1.54`, so
browsers and Vercel do not keep stale hub launch code after deploys. Sport page
query launches now run after the profile bootstrap attempt even if bootstrap
throws, so `/baseball?mode=bp` calls `startBp()` directly instead of leaving
the player on the Baseball mode picker.

Update, 2026-08-03 (0.1.55): fixed the sport-page query launch blocker. Sport
pages do not render the home-page profile/account/friends controls, but
`main.js` was binding those listeners unconditionally before the query-launch
handler. The bindings are now null-safe, and profile, leaderboard, and friends
refreshes skip home-only DOM when absent. This allows `/baseball?mode=bp` and
other mode-hub URLs to reach `startBp()` or `startFr()` instead of staying on
the sport mode picker.

Update, 2026-08-03 (0.1.56): sport pages now render direct-launch query
parameters into `body` data attributes, such as `data-launch-mode="bp"`.
`main.js` reads those attributes before `window.location.search`. This makes
the path from `/manager-mode` -> Baseball equivalent to clicking Baseball ->
Manager Mode, even if the URL query string is later removed by history cleanup
or browser navigation quirks.

Update, 2026-08-03 (0.1.57): mode-first solo/daily launches no longer depend
on query strings or profile bootstrap timing. `/manager-mode/<sport>` and
`/film-review/<sport>` render the sport page with the appropriate launch mode.
The mode hub sport tiles use those routes. On sport pages, Manager Mode and
Film Review launch immediately when `data-launch-mode` is `bp` or `fr`, while
profile bootstrap continues in the background. This makes Homepage -> Baseball
-> Manager Mode and Homepage -> Manager Mode -> Baseball converge on the same
in-game state.

Update, 2026-08-03 (0.1.58): `/manager-mode/<sport>` and
`/film-review/<sport>` are now canonical launch shortcuts instead of alternate
sport-page URLs. They set a short-lived launch cookie and redirect to
`/<sport>`, so Homepage -> Baseball -> Manager Mode and Homepage -> Manager
Mode -> Baseball both end at `/baseball` while still starting Manager Mode.

Update, 2026-08-03 (0.1.59): mode-first launches now preserve their return
context. The game still runs at the canonical sport URL, but a launch from
`/manager-mode/<sport>` exits back to `/manager-mode`, and a launch from
`/film-review/<sport>` exits back to `/film-review`. Sport-first launches keep
the existing behavior and exit back to that sport's mode picker.

Update, 2026-08-03 (0.1.60): public mode URLs are now short and hyphen-free:
`/manager`, `/film`, `/division`, and `/playoffs`. Mode-first sport launches
use `/manager/<sport>` and `/film/<sport>`, then redirect into the canonical
sport URL for gameplay. Old `/manager-mode`, `/film-review`, and
`/division-rivalry` paths remain compatibility redirects only.

Update, 2026-08-03 (0.1.61): Manager Mode loading labels are sport-specific
and capitalized: Baseball Leadoff, Basketball Tipoff, Football Snapper, and
Hockey Faceoff. The server now uses a small psycopg connection pool on warm
instances (`psycopg[binary,pool]`) while retaining the old direct-connection
fallback. This should reduce repeated Supabase connection overhead after cold
start; true first-load latency may still include Vercel and Supabase wake-up
time.

Update, 2026-08-03 (0.1.62): `/film` archive dropdowns now use a neutral
selector row, so choosing Today's puzzle triggers navigation even when Today
is already the default listing. Opening an unseen archive Film Review records
that puzzle as in progress; completed/failed archive attempts update the
archive status but daily streak logic still only counts won dates. `/manager`
now shows each sport's daily starter with headshot, uses the same daily starter
when a Manager Mode run starts, and includes a sport-filtered Manager
leaderboard panel with personal/global all-time, personal/global today, and a
daily records dropdown.

Update, 2026-08-03 (0.1.63): `/film` now loads all sport archive dropdowns
through one `/api/film/archive_summary` request and uses the label `Choose
lineup...` with two-digit years in archive dates. Returning users on mode hubs
use the locally stored guest ID immediately and refresh profile bootstrap in
the background, avoiding a slow profile roundtrip before archives or Manager
leaderboards render. `/manager` daily starter cards are laid out side-by-side
inside sport tiles, and daily starter selection now uses recent, higher-volume
players with image-backed candidates where available. Manager Mode scores are
stored as full lineup length, including the starter, and the records archive
only shows entries from 2026-08-01 onward. Manager timeout finalization retries
until the server confirms game over, fixing missing game-over banners.

Open data-quality note: Alex Groza exposed that early NBA/BAA teammate coverage
and search availability still need a dedicated historical audit. The daily
starter pool now avoids these early-era records, but the broader historical NBA
graph should be validated before old NBA players are used as default starters
or prominent puzzle anchors.

Update, 2026-08-03 (0.1.64): fixed two cross-sport Manager Mode parity bugs.
`bp_runs.seed_player_id` no longer carries the original Baseball-only foreign
key to `players(player_id)`, because NBA/NHL/NFL seeds use sport-prefixed IDs
such as `nhl:8471469`. That obsolete constraint could make non-baseball
timeouts crash while saving the run, returning an HTML error page and causing
the browser's JSON parse failure instead of a game-over banner. Cross-sport
card hydration now deduplicates player-team-season rows before building team
year ranges, fixing repeated Hockey lines such as Pekka Rinne's Nashville
tenure. The shared `api()` helper now handles non-JSON server responses with a
readable error instead of surfacing raw `JSON.parse` text.

Update, 2026-08-04 (0.1.65): Manager Mode game-over text now reports full
lineup length including the starter, matching saved leaderboard scores. Film
Review daily attempts are now keyed by unit as well as sport/date, so Football
offense and defense are separate 12-player lineups with separate archive rows,
statuses, and solve rates. `/film/football` defaults to offense if no unit is
provided, preventing the old combined offense+defense deck. `/film` shows
sport streaks, today solve percentages, and a two-player preview for today's
lineup; Football exposes explicit Offense and Defense buttons and archive
entries include unit labels. `/manager` now loads lightweight tile data from
`/api/manager/tiles` first, then fills the heavier leaderboard from
`/api/manager/summary` afterward.

Update, 2026-08-04 (0.1.66): Manager Mode daily starters are now persisted in
`manager_daily_starters` by sport and Central date, so they do not change
between requests or Vercel instances during the same day. `/film` renders
player previews as a right-side column inside sport tiles. Football Film
Review is represented by one football tile split horizontally into Offense and
Defense halves; each half has its own streak, preview, and solve rate, while
the shared Football archive dropdown includes unit labels. Archive dropdowns
now say `Select Archived Tape...`.

Update, 2026-08-04 (0.1.67): `/film` Football now renders Offense and Defense
as two stacked selectable boxes rather than side-by-side controls. Film preview
names get a wider adaptive column and no longer break inside words. Manager
Mode daily starter selection avoids repeating starters from the prior 21 days
when choosing future persisted starters, while preserving existing saved daily
rows forever. Timed move submissions now send the browser's remaining time and
the server allows a narrow 1.25-second grace window when the player submitted
at the buzzer, preventing valid Manager, Division Rivalry, and Playoffs moves
from losing solely to request latency.

Update, 2026-08-04 (0.1.68): `/film` hub tiles now put league, sport, streak,
and today's solve percentage on the left side, with only the two preview
players on the right. Football Film Review is two independent stacked cards:
Offense has its own archive dropdown between Offense and Defense, while
Defense has its own archive dropdown below Defense aligned with the other
sport archive row. Hovering one Football unit no longer highlights the other.

Update, 2026-08-04 (0.1.69): `/film` applies a compact Hockey-only tile rule
so Hockey's main Film Review button stays in line with the tighter Football
Offense/Defense cards without changing the Baseball and Basketball buttons
the user liked.

Update, 2026-08-04 (0.1.70): `/film` sport tiles no longer stretch to match
the tallest item in their grid row. This keeps Hockey and its archive dropdown
at the same natural size as Football Offense, leaving the empty space below
Hockey and beside Football Defense intentionally unused for now.

Update, 2026-08-04 (0.1.71): `/film` now treats Football Offense and Football
Defense as two ordinary sport-grid items rather than nesting both inside one
Football tile. Baseball, Basketball, Hockey, Football Offense, and Football
Defense therefore use the same button and archive dropdown sizing in the same
two-column grid. Removed the special Football/Hockey sizing overrides that
caused the previous stretch/shrink cycle.

Update, 2026-08-04 (0.1.72): `/film` keeps Football Defense in the right
column directly below Football Offense by inserting a desktop-only empty grid
spacer before the Defense tile. The spacer is hidden on mobile, where the Film
choices collapse to one column.

Update, 2026-08-04 (0.1.73): API routes now return JSON for unexpected server
exceptions instead of Flask HTML error pages, so the browser no longer reports
opaque `500 non-JSON response` alerts. Daily Film Review puzzle rows are also
validated before use; if an old persisted row has the wrong date, unit, deck,
or slot shape, the server deletes and regenerates that row for the requested
sport/date/unit. This was added after a reported Football Defense archive #3
launch failure.

Update, 2026-08-04 (0.1.74): Film Review terminology and results were updated
across sports. Football feedback now says `COMPLETION` and `INCOMPLETION`.
Winning shows `Fully Scouted`; losing shows `Benched`; final detail text is
`x/y Lineup` plus `% Fully Scouted`. Finished Film Review chains render
chronologically from top to bottom, and a loss reveal fills the lineup board as
a key with earned players green and missed players red. `/film` sport buttons
and archive dropdowns now say `% Fully Scouted`, use statuses `new`, `unseen`,
`in progress`, `benched`, and `fully scouted`, and include puzzle-level fully
scouted percentages. Percentages count all finished daily or archive attempts
and ignore unseen or in-progress attempts.

Update, 2026-08-05 (0.1.75): Film Review button statuses are now title case
(`New`, `In Progress`, `Benched`, `Fully Scouted`) and the compact button text
uses `% Fully Scouted` instead of `% Fully Scouted Today`. Final daily Film
Review banners show the current streak; archived attempts omit streak text.
The in-game Film Review Archive filters Football by the active unit, so
Defense no longer lists Offense tapes, and archive actions preserve the unit.
Cross-sport Film Review team autocomplete now uses explicit current NBA, NHL,
and NFL team-name lists instead of raw `sport_teams` names, avoiding entries
such as `AFM`, `ANA`, `ARI`, or historical relocated names. Shared-link display
also canonicalizes old/current franchise names for Film Review answers.

Update, 2026-08-05 (0.1.76): Mode hub pages (`/manager`, `/film`,
`/division`, `/playoffs`) now use normal header controls: Exit to home, a `?`
rules modal, and Playoffs-specific Win Conditions and Powerups reference
buttons. Sport/home page `?` rules were expanded into structured explanations
for all four modes, and the home/sport `Ref` button now opens Playoffs
win-condition and powerup reference content. Film Review archive rows now show
one `Continue` button for in-progress old games; only Benched/Fully Scouted
archive rows show Review and Retry. Football Film hub typography was normalized
through the shared Film tile styles.

Update, 2026-08-05 (0.1.77): Added a global `[hidden]` CSS rule so author
styles cannot override hidden UI state. This fixes the Lineup and sport-specific
out toggles appearing on the homepage, sport pages, and Film Review screens;
they should only appear during active Manager Mode, Division Rivalry, or
Playoffs games.

Update, 2026-08-05 (0.2.0): Cross-sport mode integration is now considered the
first 0.2 baseline. The visible How to Play rules were expanded on homepage,
sport pages, and mode hubs to explain teammate links, player actions, team
marks, maxed teams, daily Film Review tapes, online turn flow, rematches,
Playoffs powerups, and Playoffs win conditions. Shared mode-hub queue status
now checks Baseball in the same `sport_online_games` table as NBA, NHL, and
NFL, fixing a Baseball `/division` or `/playoffs` wait-state mismatch after a
match is created. Hub-created multiplayer redirects now include
`source=division` or `source=playoffs`, so exiting a hub-launched match can
return to the proper mode page.

Update, 2026-08-14 (0.2.1): direct sport tiles on `/division` and `/playoffs`
now navigate to that sport's normal queue screen, preserving its single-sport
queue, challenge-code, and Playoffs preference controls. The shared multi-sport
queue and direct sport queues now match each other under the same per-mode
advisory lock. A direct Hockey queue can therefore immediately match a
Baseball/Hockey/Football multi-sport search on Hockey; the resulting game uses
the existing sport page and game lifecycle. Verified this exact Division
Rivalry scenario against the runtime configuration.

Update, 2026-08-14 (0.2.2): Film hub summary no longer generates and hydrates
daily preview cards for all sports during page load, removing the largest
source of repeat navigation delay. Direct mode and matched-game launches hide
the sport home screen while the launch request bootstraps, preventing a visible
flash of the sport mode tiles before the requested queue or game screen.

Update, 2026-08-14 (0.2.3): Film hub archive rates are now fetched as one
grouped aggregate per sport rather than one query per archive day, reducing
runtime summary response from roughly 5.25 seconds to 1.5 seconds in the
runtime check. Player preview cards load in a separate non-blocking request,
so the Film hub is usable before their data arrives. Multi-sport queue choices
and Playoffs preferences are stored locally; `Find New Match` after a game
entered from `/division` or `/playoffs` returns to that hub and resumes the
same multi-sport search. Sport-page games retain their sport-page requeue flow.

Update, 2026-08-14 (0.2.4): multi-sport matched-game redirects now fetch the
game immediately with the stored guest id rather than waiting for profile
bootstrap. Returning to a multi-sport mode hub with `Find New Match` preserves
the selected sports and Playoffs condition choices but does not automatically
requeue; the player can adjust the loadout and explicitly search again.

Update, 2026-08-14 (0.2.5): all active game modes now enforce a modern-era
floor of 2000 for autocomplete eligibility and teammate links across MLB, NBA,
NHL, and NFL. Historical records remain in the catalog for a later expansion,
but a pre-2000-only player or a pre-2000 teammate connection cannot be used
today. Random Playoffs assignments now exclude each guest's three most recent
random conditions within the same sport; explicit condition choices are not
altered. The local SQLite playtest path applies the same recent-repeat rule
for its in-memory session.

Update, 2026-08-14 (0.2.6): reset all derived gameplay records to align with
the modern-era scope, including scores, leaderboards, ELO, team-out counts,
daily attempts, active games, daily starters, and persisted Film Review decks.
Accounts, auth, friendships, and athlete catalog records were retained. Film
Review was backfilled from 2026-08-01 through 2026-08-14 for all sports and
both Football units (70 puzzles). New dates are still generated and persisted
on first request. Daily generators now prefer player pairs with exactly one
valid shared team-year; when a multi-year overlap is used, solved links show
all valid answers. The TeamMateTag header now links home without an underline,
all sports are marked playable on the home page, and sport/mode copy was
rewritten to describe the actual rules.

Update, 2026-08-14 (0.2.7): Supabase was pruned to the active 2000-present
catalog while retaining a complete local historical snapshot at
`db/archive/teammatetag_historical_catalog_2026-08-14.sqlite` (SHA-256
`221eb75ef592de85115c057b15a07f061282081b25b193519a4e17736dc69f29`). The
archive is deliberately ignored by Git. Removed cross-sport pre-2000
appearances, team seasons, season traits, and historical-only player metadata;
Baseball was already modern-era for appearances, so only historical-only player
metadata was removed. After `VACUUM FULL`, public Supabase table storage fell
from about 170 MB to about 72 MB. All five Film Review generators were checked
against the reduced runtime catalog successfully.

Update, 2026-08-14 (0.2.8): added a verified-headshot pipeline. The
`player_headshots` registry stores a source URL, provider, byte hash,
perceptual hash, dimensions, audit status, and reviewer notes per player;
Supabase stores this lightweight metadata, not image binaries. Player cards
now suppress sources marked `placeholder`, `missing`, `wrong_player`, or
`bad_crop` unless a reviewed fallback URL exists. `scripts/audit_runtime_headshots.py`
downloads and decodes actual image bytes, detects known provider placeholders
and byte-identical collisions, then writes flagged rows to
`raw/headshot_audit_report.csv`. It is resumable by sport/offset/limit because
league CDN rate limits make a 29,560-player scan unsuitable as one request
burst. A localhost-only review UI is available at `/headshot-audit` when the
local Flask server is running; production access requires setting
`HEADSHOT_AUDIT_TOKEN`. Brian Schneider, Luke Ridnour, Trent Cole, Clifton
Geathers, and Jason Krog are seeded as blocked placeholders. The full photo
coverage goal remains active: do not count a HTTP 200 response as a headshot.

Update, 2026-08-14 (0.2.9): the headshot scanner now skips every already
checked registry record by default, not just manual approvals. Repeated
`--limit 500` batches therefore advance through the remaining catalog. Use
`--force` only when intentionally rechecking prior results.

Update, 2026-08-14 (0.3.0): investigated the NFL photo failure. A first
500-player byte audit found 479 generic placeholders and 21 unique images,
confirming the old NFL catalog URLs are not reliable coverage. Added
`scripts/refresh_nflverse_headshots.py`, which joins the current nflverse
players file by GSIS ID and refreshed 13,728 NFL source candidates, preferring
nflverse's player-specific NFL asset and using ESPN only when necessary.
Trent Cole and Clifton Geathers were manually visually verified with ESPN
portraits and saved as registry overrides. Do not call the catalog 100% covered
yet: the remaining sources must still pass the resumable byte-level audit and
the flagged players need reviewed replacement URLs.

- The apex DNS record is currently missing: public resolvers return no A/AAAA
  record for `teammatetag.com`, while `www.teammatetag.com` has a Vercel CNAME.
  This explains the Firefox failure and can also affect Chrome when its cache
  expires. In Cloudflare DNS, add the exact apex A record shown in Vercel's
  Project Settings > Domains. Vercel's general fallback is `76.76.21.21`.
- Supabase password-reset links may still route through an unwanted Vercel
  sign-in flow. This was explicitly deferred.
- Update `README.md` after the cross-sport data sources and first loader are
  selected. It is currently stale.
- 0.2 follow-up priorities: finish quality-testing `/division` and `/playoffs`
  queue/rematch behavior across all sports, overhaul the UI after functionality
  is stable, fill player-picture gaps with clearer fallback handling, and
  profile storage/load-time/database growth as user volume increases.
- Supabase storage: full NFL roster data (118,070 player-team-seasons) plus
  baseball pair edges exceeded the free project capacity. Do not rerun the
  historical NFL or baseball loaders against production until moving to a
  larger database or changing the connection storage/query strategy.
