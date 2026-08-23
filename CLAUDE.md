# TeamMateTag project handoff

Update this file whenever product behavior, deployment, data, or active work
changes. It is the concise source of truth for another coding assistant.

## Product and deployment

- Production: `https://teammatetag.com`
- Vercel deployment: `https://teammatetag.vercel.app`
- Repository: `https://github.com/laudedlem/TeamMateTag`
- Local repository folder: `C:\Users\laude\Desktop\base2nerdle`
- Current display version: `0.3.27`
- Stack: Flask + vanilla JavaScript on Vercel, Supabase Postgres, Supabase
  Auth, server-side session cookie.
- Supabase runtime catalog: the non-baseball game data was imported on
  2026-08-01. Database size was roughly 170 MB after the sport-runtime import,
  below the Free plan's 500 MB database quota. The old materialized Baseball
  `teammates` table was removed because it alone consumed roughly 400 MB; all
  game paths derive links from indexed appearances instead.
- Required environment values are documented in `.env.example`. Never commit
  `.env` or any Supabase password/key.
- Runtime schema migrations are skipped during ordinary gameplay to keep Vercel
  cold starts fast. Set `TEAMMATETAG_AUTO_MIGRATE=1` only for an intentional
  migration run, then remove it again.

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
- **Film Review**: nine-player / eight-connection puzzle. Guess team and season.
  A correct answer advances; one correct field is a foul. The first foul in a
  streak is safe; each additional consecutive foul is a strike. Three strikes
  ends the puzzle and shows the completed lineup. Every Film Review connection
  is deliberately generated with one valid team-season answer; a team-season
  cannot repeat within its puzzle. Team-first autocomplete offers matching
  team-season choices across the visible pair's playable career overlap, such
  as `Toronto Blue Jays 2010` through `Toronto Blue Jays 2019`. The guess
  endpoint remains authoritative about whether that selected team-season is a
  real teammate link.
  The generator ranks actual career games above recency, prefers modern players
  for the first two cards, and intentionally gets more difficult later in a
  lineup. League display names are preferred over legal first names so common
  forms such as Geno Smith and C.J. Mosley remain correct.

Update, 2026-08-22 (0.3.25): Film Review team autocomplete is now game-aware
and team-first across every sport. It returns matching team-season options only
inside the two visible players' playable career-overlap range, then lets the
guess endpoint decide Hit, Foul, or Strike. Stored daily puzzles are trusted
at launch instead of re-querying every pair. Normal production requests also
skip the historical runtime-schema migration pass; use
`TEAMMATETAG_AUTO_MIGRATE=1` only for deliberate schema changes.

Update, 2026-08-22 (0.3.26): Film Review accepts a team fragment plus a
typed year in autocomplete. For cross-year leagues, a typed `2011` offers
both `2010-11` and `2011-12` candidates where either could be meant. Film
Review lineup boards now begin collapsed on every screen. The account tray,
Film hub, Manager Mode leaderboard, sport-page return navigation, multiplayer
queue, player cards, Lineup, and out panels received the current UI pass.

Update, 2026-08-22 (0.3.27): Film Review autocomplete now also accepts a
partial season prefix (`2`, `20`, `201`, or `202`) after the team name. The
homepage account tray was simplified, football Film Review now describes its
two 12-player unit puzzles correctly, and the current UI pass further expands
the Film hub, game surfaces, cards, and queue treatment for mobile use.
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
`x/y Lineup`. Finished Film Review chains render
chronologically from top to bottom, and a loss reveal fills the lineup board as
a key with earned players green and missed players red. `/film` sport buttons
and archive dropdowns use statuses `new`, `unseen`,
`in progress`, `benched`, and `fully scouted`, and include puzzle-level fully
scouted status. User-facing percentages were later removed in 0.2.17.

Update, 2026-08-05 (0.1.75): Film Review button statuses are now title case
(`New`, `In Progress`, `Benched`, `Fully Scouted`). Final daily Film
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

Update, 2026-08-14 (0.2.10): investigated the NFL photo failure. A first
500-player byte audit found 479 generic placeholders and 21 unique images,
confirming the old NFL catalog URLs are not reliable coverage. Added
`scripts/refresh_nflverse_headshots.py`, which joins the current nflverse
players file by GSIS ID and refreshed 13,728 NFL source candidates, preferring
nflverse's player-specific NFL asset and using ESPN only when necessary.
Trent Cole and Clifton Geathers were manually visually verified with ESPN
portraits and saved as registry overrides. Do not call the catalog 100% covered
yet: the remaining sources must still pass the resumable byte-level audit and
the flagged players need reviewed replacement URLs.

Update, 2026-08-15 (0.2.10): restarted the NFL scan as durable 500-player
batches after stopping an old all-sport process that did not write results
until completion. The first 3,500 football players are now classified from
their downloaded image bytes: 3,338 are generic placeholders, 78 are
unavailable, and 86 are distinct images. The audit reuses a HTTP session per
worker and can reclassify a byte-identical image shared by multiple players as
a placeholder. Continue the remaining football batches before treating any
candidate URL as usable coverage.

The full 15,236-player football scan was completed the same day. Before
fallbacks it found 7,164 distinct images, 7,385 placeholders, and 687 missing
responses. `scripts/resolve_nfl_espn_headshots.py` tested the ESPN identity
from nflverse for every flagged player and promoted 1,737 additional unique
portraits only after byte-level validation. Current coverage is 8,901 verified
photos, 5,655 placeholders, and 680 missing responses. The unresolved set is
intentionally suppressed in player cards, never displayed as a generic NFL
headshot. `scripts/apply_verified_runtime_headshots.py` records hand-verified
runtime exceptions, currently Devin Hester and Mike Brown; rerun it after any
future forced audit. Trent Cole and Clifton Geathers are verified ESPN images.

Photo sourcing pass, 2026-08-15: `resolve_nfl_wikimedia_headshots.py` ran a
strict name + American-football description + recorded-team match against every
remaining NFL gap. It promoted 30 Commons photos and recorded every other
outcome in `player_headshot_source_attempts`; most unresolved players simply
do not have a matching Wikipedia article. `resolve_nfl_thesportsdb_headshots.py`
is the next fallback. It requires exact name and nflverse birth-date matches,
checks the returned image bytes, and respects TheSportsDB's free 30-request per
minute limit. It has resolved Lance Briggs. The durable long-running pass is
intentionally allowed to continue locally; it must not use placeholders as a
fallback. The first run was interrupted by a free-tier rate limit after 1,001
attempts, so the resolver now uses 25-player batches, a 2.5-second interval,
and a 65-second retry window. A long-running local pass resumes the remaining
queue from its durable attempt records. At that point in the pass, football had
9,112 verified images, 5,458 blocked placeholders, and 666 missing responses.
The SportsDB resolver now orders unattempted records by total recorded NFL
games, so high-visibility playable players are repaired before short-stint
records. There are 31 non-GSIS football IDs needing a later identity-data
review; several are historic names incorrectly attached to a modern season and
must be removed or reconciled, not assigned a speculative photo.

Headshot follow-up, 2026-08-15: The complete unresolved queue can be exported
with `scripts/report_nfl_headshot_gaps.py`; the generated ignored CSV is
`raw/nfl_unresolved_headshots_YYYY-MM-DD.csv`, ordered by career games. The
Wikimedia resolver now accepts a redirect only when first initial and last name
match, then still requires the football-career and team checks. It also treats
a CDN `429` as a temporary byte-check failure when the article identity is
otherwise strict. This recovered high-usage players including Mike Vick, Troy
Polamalu, Antonio Gates, Charles Woodson, Dwight Freeney, and Robert Mathis.
Current snapshot: 9,223 verified football images, 5,347 placeholders, and 666
missing responses. Never replace a blocked card with a generic league image.

ESPN recovery, 2026-08-15: the original ESPN fallback incorrectly looped over
unavailable records and never reached all historical ESPN IDs. It was changed
to use the same durable provider-attempt table and prioritize players by games
played. The complete known-ID pass promoted 1,100 more verified ESPN images,
including Thomas Jones and Fred Taylor. `scripts/index_espn_nfl_athletes.py`
builds a local identity index from ESPN's public 20k-player historical NFL
catalog. Use it to match unresolved nflverse players by name and birth date
before requesting an alternate ESPN/college-football portrait. Do not use
Madden game assets: they are not a licensed public media feed for redistribution.

The ESPN index is checkpointed by catalog page in
`raw/espn_nfl_athlete_pages/page_01.csv` through `page_21.csv`; each file holds
up to 1,000 athlete identities. Run the remaining pages sequentially with
`python scripts/index_espn_nfl_athletes.py --page N --workers 16`. This makes
progress observable and prevents a long scan from losing all work. The
TheSportsDB resolver treats 429/5xx/non-JSON responses as transient and does
not permanently mark them as no-match; retryable attempt rows were cleared on
2026-08-15.

Cross-sport headshot pass, 2026-08-15: `scripts/export_headshot_registry.py`
writes a local ignored snapshot of every production source URL, status, hash,
and provider at `raw/headshot_registry_YYYY-MM-DD.csv`. This is the local
record of confirmed mappings; do not needlessly revisit a verified player.
Checkpointed `audit_runtime_headshots.py` batches are running for baseball,
basketball, and hockey while the football resolvers continue. Audit first,
then use provider-specific fallbacks only for the flagged rows.

Cross-sport ESPN identity pass, 2026-08-15: use
`scripts/index_espn_sport_athletes.py` for checkpointed ESPN athlete identity
catalogs. It writes immutable local CSV pages containing ESPN ID, name, birth
date, debut year, active flag, and position. Catalog sizes: MLB 38 pages,
NBA 1 page, NHL 12 pages; the NFL has its existing dedicated 21-page index.
These jobs collect identities only. After a sport's pages are complete, match
the flagged runtime players to ESPN by normalized name plus birth date/career
context, then request and byte-validate the ESPN portrait URLs. TheSportsDB
will be the secondary source, with exact identity checks and the same image
audit; it must never promote a generic placeholder.

Initial image-audit snapshot, 2026-08-15: all active 2000-present catalogs
have completed the byte-level scan. Baseball: 7,168 total, 4,856 verified,
2,312 flagged. Basketball: 2,566 total, 1,705 verified, 861 flagged.
Football: 15,236 total, 10,552 verified, 4,684 flagged. Hockey: 4,590 total,
3,303 verified, 1,287 flagged. "Flagged" means the current URL was proved to
be a placeholder or unavailable; it is not displayable coverage. "Unchecked"
is zero for all four sports. The local registry export remains the durable
record of source URL, provider, hash, dimensions, and audit state.

Important ESPN catalog limitation: the public NBA core catalog returned 614
current athletes on one page, not the full historical league. It is useful for
current NBA IDs and portraits, but historical NBA identity matching must also
use the local `raw/nba_kaggle/positions_v2/NBA_PLAYERS.csv` birth-date source
and TheSportsDB or another validated historical source. The local NBA source
contains player name, debut/final years, position, and birthday. Do not claim
the ESPN catalog is comprehensive for NBA history.

NBA historical identity bridge: `scripts/build_nba_headshot_identity_map.py`
creates `raw/nba_headshot_identity_matches.csv` from the local NBA career
dataset. The current build produced 2,359 player-to-birth-date matches and
covers 825 of the initially flagged NBA headshots. `resolve_sport_thesportsdb_headshots.py --sport basketball`
uses that local birth date for an exact TheSportsDB identity match, then
downloads and validates the returned portrait before promotion. It runs in
five-player checkpoint batches because the free endpoint is slow and rate
limited.

NBA ESPN catalog portrait pass, 2026-08-15: the completed ESPN current-NBA
index has 614 athletes. `scripts/collect_nba_espn_catalog_headshots.py` matched
432 to local players by normalized name plus exact birthday, downloaded and
byte-validated their ESPN URLs, and saved the durable mapping at
`raw/nba_espn_headshot_catalog.csv`. Results: 431 valid portraits and 1
unavailable response. These were already valid through the NBA source, so this
pass promoted zero flagged players; it exists to validate the cross-sport ESPN
workflow and preserve the mappings for source fallback. The remaining 182 ESPN
records did not have an unambiguous local identity match and were not guessed.

NBA rapid playtest image pass, 2026-08-15: `BasketBall-GM-Rosters` maintains a
community `player-photos.json` map with 5,408 historical NBA image URLs.
`scripts/import_nba_bbgm_playtest_headshots.py` resolves those Basketball-
Reference IDs through the 26 public player-index pages, matches local flagged
players by normalized name and career span, byte-checks every URL, and writes
the result to `raw/nba_bbgm_playtest_headshots.csv`. First pass: 770 matched,
748 usable images promoted. Every promoted row has provider `BBGM community
map` and review note `Playtest-only community mapping; license and source
review required.` This is deliberately separate from vetted sources so it can
be replaced later without losing provenance.

Gap reporting, 2026-08-15: `scripts/export_headshot_gaps.py` writes the active
2000-present unresolved list to `raw/active_headshot_gaps.csv`, including
sport, player ID, display name, career years, current audit status, and review
note. Use this file for targeted source research and user playtest feedback;
do not rely on a stale one-off terminal list.

Small community CSV pass, 2026-08-15: the IvoVillanueva MLB and NHL roster
repositories were assessed. They are current-roster-oriented rather than
historical maps, matching only 8 active baseball gaps and 15 hockey gaps.
`scripts/import_small_community_headshots.py` byte-validated and promoted 7
baseball and 15 hockey URLs as `Community roster CSV`, again marked
playtest-only pending source/license review. Current status after this pass:
baseball 4,863 verified / 2,305 unresolved; basketball 2,463 / 103; football
10,577 / 4,659; hockey 3,318 / 1,272.

Community-source research, 2026-08-15: Reddit and GitHub found no NFL or NHL
equivalent of the 5,408-entry Basketball GM map. NFL community results are
Madden roster/face assets with incomplete or sometimes incorrect portraits,
not a stable player-ID URL database. NHL results include Hockey Legacy Manager
rosters with current-player photos, not a broad downloadable historical map.
MLB has a stronger next lead: the OOTP Developments photo-pack forum hosts
current and historical MLB photo packs, including 2026 updates. Assess the
pack's filename/identifier convention and source before importing; retain each
mapping as playtest-only provenance until a licensing pass.

ESPN/OOTP pass, 2026-08-15: all NFL and NHL ESPN identity pages are complete.
`resolve_sport_espn_headshots.py --sport hockey` matched 1,151 unresolved NHL
identities and promoted 358 validated ESPN portraits. The full NFL catalog
resolver (`collect_nfl_espn_catalog_headshots.py`) matched 2,909 unresolved
players by exact NFLverse birth date but promoted only 2 real images; the rest
were generic, shared, or unavailable ESPN responses. ESPN is now effectively
exhausted for historic NFL gaps.

OOTP local MLB playtest pass, 2026-08-15: Cinemaodyssey Facepack V18 was
downloaded to `raw/ootp/COFacepackV18.zip` (347 MB compressed, 21,264 JPGs).
`import_ootp_mlb_playtest_headshots.py` matched 2,211 active baseball gaps by
unambiguous normalized filename and extracted them under
`raw/ootp/matched_mlb_headshots/`. Local Flask serves them at
`/local-headshots/ootp/<player_id>.jpg` only when `TEAMMATETAG_LOCAL_SPORTS=1`.
Production deliberately suppresses these local-only URLs until images are
migrated to supported storage. All imported records are `OOTP Facepack` with a
playtest-only review note. The all-history record count after import was 7,074
verified. For the current 2000-present playable baseball catalog, the result
is 5,143 verified / 94 unresolved.

OOTP production publishing, 2026-08-15: `scripts/publish_ootp_mlb_headshots.py`
is the resumable production bridge for the 2,211 extracted OOTP photos (40.22
MB). It creates the public Supabase Storage bucket `player-headshots`, uploads
objects under `baseball/ootp/`, and updates the OOTP database records from
local-only `/local-headshots/...` URLs to public storage URLs. It requires
`SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` in the local `.env`, in addition
to the existing `DATABASE_URL`. The service-role key must never be committed or
placed in Vercel/browser code. The public bucket does not consume Postgres
database capacity and requires no Vercel configuration to display its URLs.
The initial production publication completed successfully: all 2,211 files
were uploaded, their database URLs were changed to public Supabase Storage
objects, and a direct public-image request returned HTTP 200 image/jpeg. A
deployment restart clears any warm Vercel card cache that could still hold an
old local-only image URL.

Manual replacement workflow, 2026-08-15: `scripts/export_headshot_submission_sheet.py`
creates an Excel-friendly CSV with stable sport/player IDs and blank
`replacement_url` / `source_note` fields. `scripts/import_headshot_submissions.py`
downloads every supplied URL, requires a decodable non-placeholder image, then
updates the runtime registry. Current review sheets are generated locally under
`raw/mlb_headshot_review_sheet.csv` (94 rows) and
`raw/nba_headshot_review_sheet.csv` (103 rows). The sheets and raw images stay
Git-ignored; a reviewer can fill only the two blank fields and hand the file
back to the assistant for validation and import.

NHL historical-photo pass, 2026-08-15: FHM's Historical Photos Megapack 3.5
was downloaded locally from its maintained FHM forum listing (6,650 photos,
updated June 2026). `scripts/import_fhm_historical_nhl_headshots.py` matched
only normalized-name-plus-birth-year identities and found 691 unambiguous
2000-present NHL gaps. All 691 were byte-checked, had no duplicate selected
image hash, were uploaded to public Supabase Storage under
`hockey/fhm-historical/`, and their runtime URLs now resolve publicly. The
current FHM 24-25 pack added another 5 unambiguous current-player photos. NHL
playable headshot gaps fell from 913 to 217. Both FHM sources are community
playtest sources and remain marked for license/source review. The historical
import uses the local-only `py7zr` package (`python -m pip install py7zr`);
do not add it to production Vercel requirements.

Headshot identity audit, 2026-08-15: source imports had created a small number
of duplicate source IDs for the same person. `synchronize_duplicate_headshot_aliases.py`
now copies a verified image across aliases without touching player or teammate
data. It identifies baseball aliases through a shared RetroSheet ID and NHL
aliases through normalized name, birth year, position, and team-season career.
The OOTP importer also permits same-RetroSheet aliases, which restored Kevin
Youkilis under both legacy player IDs from a single OOTP image. Review-sheet
exports now include position and team context, collapse NHL source aliases, and
leave genuine same-name players as separate rows. Current review sheets:
baseball 92 rows, basketball 103 rows, hockey 210 distinct-player rows. The
only remaining repeated NHL display name is the two distinct Alexandre Picards
(defenseman versus left wing).

## Current handoff: headshot and NHL identity cleanup

Update, 2026-08-15: the immediate user goal is accurate, production-visible
headshots for every player in the current 2000-present playable catalog. The
site to test is `https://teammatetag.com`; Vercel deploys from GitHub `main`.
All confirmed registry URLs are used by production cards. Local source archives
and generated review CSVs are under `raw/` and deliberately Git-ignored.

Production headshot work completed:
- The OOTP MLB Facepack V18 has 2,211 unambiguous matched images published to
  the public Supabase Storage bucket `player-headshots` under `baseball/ootp/`.
  The initial 40.22 MB upload completed successfully. Production URLs return
  HTTP 200. OOTP images may later need provider-specific crop treatment.
- The FHM Historical Photos Megapack 3.5 was downloaded locally from the FHM
  forum/Google Drive. Its 6,396 uniquely keyed player photos matched 691
  unresolved 2000-present NHL players by normalized name plus birth year. All
  691 were published under `hockey/fhm-historical/`. The current FHM 24-25
  pack added 5 more under `hockey/fhm-current/`.
- All community/FHM/OOTP content is explicitly marked playtest-only in
  `player_headshots.review_note`; it needs license/source review before any
  commercial release decision.
- Supabase Storage is being used for images, not Postgres. The bucket is public
  and the web app only reads the public object URLs. `SUPABASE_URL` and the
  service-role key are required only in the ignored local `.env` to publish
  assets. The service-role key was supplied in the previous conversation and
  should be rotated later, with the replacement also updated in Vercel if the
  deployed app uses it for Supabase Auth.

Current playable headshot status (raw source-record counts; aliases remain in
the data until the cleanup below):
- Baseball: 5,145 verified, 92 unresolved. Kevin Youkilis was repaired after
  finding that one person existed under `youkike01` and `youklke01`; both now
  use the same OOTP image in production.
- Basketball: 2,463 verified, 103 unresolved.
- Football: 10,783 verified, 4,453 unresolved (3,787 placeholders and 666
  missing). The long-running SportsDB fallback job may still improve this.
- Hockey: 4,374 verified, 215 unresolved raw records. The reviewed NHL sheet
  collapses known duplicate source identities and currently shows 210 distinct
  people.

Review workflow for manual replacements:
- `scripts/export_headshot_submission_sheet.py` creates CSVs with stable IDs,
  position, teams, career years, and blank `replacement_url` / `source_note`.
- `scripts/import_headshot_submissions.py --input <sheet>` validates direct
  image URLs, blocks known placeholder hashes, then updates the live registry.
- Current local review sheets: `raw/mlb_headshot_review_sheet.csv` (92),
  `raw/nba_headshot_review_sheet.csv` (103), and
  `raw/nhl_headshot_review_sheet.csv` (210). Do not edit IDs or player fields;
  fill only `replacement_url`, optionally `source_note`.
- MLB repeated names remaining in the sheet are genuine different players,
  distinguished by position/team history. NBA's only repeated display name is
  two different Marcus Williams players (PG versus SF). NHL's only remaining
  repeated display name in the corrected sheet is two different Alexandre
  Picards (defenseman versus left wing).
- Specific user questions resolved: `braunry01` is pitcher Ryan Braun
  (2006-07) and is the remaining MLB review-sheet row; famous Brewers outfielder
  Ryan Braun is `braunry02` and has a verified MLBAM photo. Carlos Hernandez
  appears for three distinct older players in the sheet, separated by years,
  position, and teams; the current Kansas City pitcher Carlos Hernandez is a
  separate verified record. Kevin Youkilis is now verified under both legacy
  source IDs and no longer appears in the sheet.

Completed identity cleanup, 2026-08-15:
- `scripts/canonicalize_sport_player_aliases.py` is the production repair pass
  for source-level duplicate sport players and nickname aliases. It writes
  `raw/sport_player_alias_canonicalization.csv` on dry run and applies changes
  only with `--apply`.
- Production NHL cleanup merged 89 Hockey Databank alias records into canonical
  official `nhl:<id>` player records. Examples now resolved: Matthew/Matt
  Grzelcyk, Samuel/Sammy Blais, Alex/Alexander Steen, Dmitri/Dmitry Orlov,
  John/Johnny Gaudreau, Michael/Mike Matheson, Evgeny/Evgenii Dadonov, and
  Anthony/Tony DeAngelo. A final dry run now reports zero NHL merge candidates.
- The same script normalized 1,116 HockeyDB source-team appearances from old
  source codes such as `hdb:CAL`, `hdb:FLO`, `hdb:WAS`, `hdb:CBS`, `hdb:NAS`,
  `hdb:AND`, `hdb:VEG`, and `hdb:PHO` into the runtime team IDs used by the
  game where the target team-season exists.
- `sport_player_aliases` now has 8,849 alias rows. Non-baseball move lookup in
  `game/engine.py` and both local/production sport autocomplete endpoints in
  `web/server.py` now consult this table. Verified examples:
  `Matthew Grzelcyk`, `Samuel Blais`, `Anthony DeAngelo`, `Dmitri Orlov`,
  `John Gaudreau`, `Michael Matheson`, and `Evgeny Dadonov` all return the
  canonical NHL player.
- `scripts/supplement_hockeydb_history.py` was patched so future HockeyDB
  refreshes understand common nickname/formal-name variants and should not
  recreate these duplicate `hdb:*` player rows.

Headshot cleanup snapshot after the identity pass:
- Current unresolved review sheet:
  `raw/headshot_submissions_current.csv`, 4,699 active rows.
- Baseball: 7,168 verified, 0 placeholder in the production headshot
  registry. `raw/mlb_headshot_review_sheet.csv` was regenerated after the
  final 2026-08-15 manual pass and now contains 0 unresolved active MLB rows.
  Mark Johnson pitcher `johnsma03`/BRef `johnsma05` is verified from Wikimedia
  Commons via the user's linked `Mark Johnson (pitcher)` Wikipedia page. Steve
  Sparks pitcher `sparkst02` is verified from the user-supplied local JPEG,
  uploaded to Supabase Storage at
  `https://olqefgxnxifuiyutjyqb.supabase.co/storage/v1/object/public/player-headshots/baseball/manual/sparkst02.jpg`.
  Juan Morillo `morilju01` is now sourced from Baseball Reference using the
  direct URL supplied by the user:
  `https://www.baseball-reference.com/req/2025011210/images/headshots/0/04d67323_davis.jpg`.
  Earlier in the same pass, Luis Matos, Mike Darr, Jose Ortiz, and Brian Hunter
  outfielder were promoted from Wikimedia. Brian Hunter first baseman
  `huntebr01` was explicitly reverted after a same-name false match to the
  outfielder.
- `scripts/resolve_mlb_bref_headshots.py`
  checks unresolved MLB rows against Baseball Reference player pages, extracts
  the structured `image.contentUrl`, rejects known placeholders/tiny images,
  and writes validated URLs directly to the production registry. The key fix
  was using optional `curl_cffi` Safari impersonation as a fallback after plain
  `requests` hit BRef HTTP 403/429, plus using `players.bbref_id` instead of
  assuming `player_id` is always the BRef slug. This promoted 51 additional
  BRef portraits on 2026-08-15, bringing Baseball Reference to 71 verified
  MLB headshots in production. `braunry01`, the pitcher Ryan Braun, was
  verified from his Baseball Reference page; the famous Brewers outfielder
  remains `braunry02` and already had a verified MLBAM headshot.
- `scripts/resolve_mlb_wikimedia_headshots.py` checks unresolved MLB rows
  against Wikipedia/Wikimedia. It searches likely article titles, requires a
  baseball article plus a current TeamMateTag team-context or role match,
  validates image bytes against known placeholders when Wikimedia permits the
  download, and otherwise accepts Wikimedia API image metadata only for a
  matched article image with dimensions over 80px. It now includes birth-year
  title guesses such as `Name (baseball, born YYYY)`, rejects disambiguation
  pages, and rejects article birth-year mismatches to reduce same-name false
  positives. Current Wikimedia promotions include Bill Mueller, Matt Young
  `youngma02` from `Matt Young (outfielder)`, Abraham Nunez infielder, Craig
  Wilson first baseman, Mark McLemore, JD Closser, Ben Johnson, Juan Morillo,
  Luis Matos, Mike Darr, Jose Ortiz, and Brian Hunter outfielder. The generic
  `Matt Young` Wikipedia page remains rejected because it is the older pitcher,
  not TeamMateTag's 2011-12 Braves/Tigers position player.
- OOTP same-name caution: visual review found several incorrect same-name
  matches and they were reverted to unresolved: `castrra02`, `deshide01`,
  `nunezab01`, `penato02`, `wilsocr02`, and `wilsocr03`. The OOTP importer now
  blocks these IDs so they are not re-promoted accidentally. Current safe OOTP
  manual additions include Jerry Hairston Jr, Ramon Castro catcher, Alberto
  Castillo catcher, Tim Raines Jr, Eddy Rodriguez pitcher, and Abraham Nunez
  2002-04.
- Basketball: 2,566 verified, 0 placeholder/missing in production after the
  2026-08-15 Basketball-Reference, Wikimedia, and web-image pass. New script:
  `scripts/resolve_reference_headshots.py --sport basketball` derives
  Basketball-Reference slugs from the alphabetical player index, validates the
  discovered page headshot, and updates Supabase. It promoted 11
  Basketball-Reference portraits. `scripts/resolve_wikimedia_sport_headshots.py
  --sport basketball` was fixed so validated image dicts no longer overwrite
  `status='verified'`; it promoted obvious Wikipedia cases including Speedy
  Claxton, Sarunas Jasikevicius, Zeljko Rebraca, Boo Buie, Marcus Williams
  born 1986, and Clarence Weatherspoon. `scripts/resolve_nba_web_image_headshots.py`
  promoted 73 additional exact-name web-image matches for playtesting. These
  web-image rows are useful for current gameplay but need later source/license
  review. Current review sheet: `raw/nba_headshot_review_sheet.csv`, now 0
  unresolved rows.
- Hockey: 4,501 verified, 0 placeholder/missing in production after the
  2026-08-15 Hockey-Reference, Wikimedia, web-image, and final HockeyDB pass.
  `scripts/resolve_reference_headshots.py --sport hockey` derives
  Hockey-Reference slugs from the alphabetical player index, validates page
  headshots, and updates Supabase. It promoted 16 Hockey-Reference portraits
  after retrying transient 429s. `scripts/resolve_wikimedia_sport_headshots.py
  --sport hockey` promoted/retained 10 Wikimedia portraits, including manual
  exact-identity fixes for Sean Collins defenseman, Alexandre Picard winger,
  Petr Sykora born 1978, and Andy Berenzweig. New script:
  `scripts/resolve_nhl_web_image_headshots.py` promoted 119 exact-name
  web-image matches; five final rows were manually patched from HockeyDB or
  Hockey News Windsor: Melvin Angelstad, Tommy Vestlund/Westlund, Matthieu
  Descoteaux, Michael Rucinski, and William/Billy Bowler. Current review sheet:
  `raw/nhl_headshot_review_sheet.csv`, now 0 unresolved rows. Review fallback
  images at `raw/nhl_headshot_fallback_review.md`; these playtest images need
  later source/license review.
- 2026-08-15 crop cleanup: added `scripts/crop_recent_headshots.py` to normalize
  recent fallback photos into 360x450 card portraits, upload them to Supabase
  Storage under `player-headshots/<sport>/cropped/`, and update both
  `player_headshots` and `sport_player_images`. Published 217 cropped NBA/NHL
  fallback images: all basketball `Web image search` rows, all hockey
  `Web image search` rows, all basketball/hockey `Wikimedia Commons` rows, and
  all hockey `HockeyDB` rows. The cropper has a Wikimedia-specific fetch
  fallback for intermittent HTTP 429s. On 2026-08-15 these 217 rows were
  recropped from their original fallback URLs with looser, higher framing
  (`PORTRAIT_ZOOM=0.95`) after testing showed the first crop was too tight and
  low. Local review files: `raw/cropped_headshots_review.md` and
  `raw/cropped_headshots_review.csv`.
- 2026-08-15 Film Review fixes: archive rows exposed a player's own
  `progress_percent`, but user-facing percentages were removed later in
  0.2.17. A finished daily attempt
  opens as review/retry instead of a dead resume state. Cross-year sports
  (NBA/NHL/NFL) display shared seasons as labels such as `2020-21`, and Film
  Review accepts either year for that season. Added
  `sport_teammate_exclusions` for transaction/date-overlap corrections; seeded
  Brad Wanamaker / Jeff Teague / Boston Celtics / 2020 because Wanamaker left
  before Teague joined for 2020-21. Deleted the cached 2026-08-15 basketball
  puzzle containing that invalid link so it will regenerate.
- Football: 10,863 verified, 666 missing, 3,707 placeholder. The top remaining
  prominent gaps, such as David Harris, Charles Johnson, Adam Jones, and Grady
  Jackson, are still ESPN/nflverse placeholder responses and need another
  source strategy.
- Important current-season data gap: public NHL sources show Matt Grzelcyk had
  a 2025-26 Chicago Blackhawks stint, but the runtime catalog still shows his
  canonical NHL row through Pittsburgh 2024. Treat this as a current-season
  hockey data refresh issue, separate from the alias merge.

Latest code/data commits, all pushed to `main`:
- `f45d23d` Add production MLB headshot publisher.
- `ac85dfc` Record production MLB headshot publication.
- `528892e` Add manual headshot review workflow.
- `79d7241` Import historical NHL playtest headshots.
- `49ee7c8` Audit duplicate player headshot identities.
The current handoff commit includes the most recent team-code/position
normalization and Kevin Youkilis alias repair.

Retention policy, 2026-08-15: preserve every completed ESPN catalog page under
`raw/espn_<league>_athlete_pages/`, including athletes outside the current
2000-present playable catalog. These identity files are a future expansion
asset: when historical players become playable, match their local player record
to the retained ESPN ID and reuse the same validated portrait pipeline. Do not
delete the pre-2000 identity checkpoints merely because they are not currently
eligible in gameplay.

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

Update, 2026-08-15 (0.2.11): Film Review quality and speed pass.
- Bumped visible app version to `0.2.11`; legal/account templates were updated
  from stale `0.1.33` text.
- `/film` now uses a single `archive_summary` request. Preview cards are read
  only from already-stored daily puzzles instead of generating/hydrating every
  sport's current puzzle on page load, and preview images use lazy/async
  loading. Test-client timing dropped to roughly 3.0s cold and 1.8s warm from
  this environment.
- Added `scripts/rebuild_film_review_puzzles.py` for repeatable archive
  rebuilds. It clears stored puzzles/attempts/results for selected sports/date
  ranges, validates every adjacent pair, and stores regenerated puzzles.
- Rebuilt Film Review #1 through #15 (`2026-08-01` through `2026-08-15`) for
  baseball, basketball, and hockey. Football was intentionally left alone for a
  later pass.
- Generator now scores candidates by career games, teammate count, recency, and
  headshot provider reliability. Starters are chosen from the top 5 eligible
  players for the opening slot; early links use narrower high-quality windows,
  while later links gradually allow more obscure players. Fallback-photo
  providers are penalized, so searched-web/Wikimedia/HockeyDB-style images are
  avoided near the beginning unless unavoidable.
- Baseball Film Review slot order now begins with `DH` instead of `C` or `SP`
  to avoid obscure catcher/reliever starters. Current sample openings include
  Robinson Cano, Albert Pujols, Ichiro Suzuki, Carlos Beltran, and Nelson Cruz.
  Basketball sample openings include Russell Westbrook, LeBron James, Chris
  Paul, Kyle Lowry, and Stephen Curry. Hockey sample openings include Alex
  Ovechkin, Brad Marchand, David Perron, and James van Riemsdyk.

Update, 2026-08-15 (0.2.12): strict teammate-overlap validation for NBA/NFL.
- Bumped visible app version to `0.2.12`.
- Added compact strict teammate-validation tables:
  `sport_player_stints` and `sport_teammate_stint_coverage`.
- Added `scripts/build_teammate_stints.py`, which builds local stint ranges and
  publishes them to Supabase when `DATABASE_URL` is available.
- Basketball strict coverage now uses NBA game/date evidence from
  `raw/nba_kaggle/PlayerStatistics.csv`, with NBA season start year parsed from
  `gameId` so the 2019-20 COVID bubble does not get mixed into 2020-21.
- Football strict coverage now uses nflverse weekly roster overlap from
  `raw/nfl/weekly_rosters`, excluding free-agent/cut/traded/retired statuses.
- Runtime teammate checks now reject cross-sport links for covered seasons
  unless both players overlap on the same team in the same stint window. This
  applies through `game.engine.get_shared_seasons`, so Manager Mode, Division
  Rivalry, Playoffs, and most typed-link validation share the correction.
- Current strict coverage published to Supabase: basketball 2000-2025
  (14,049 stints) and football 2002-2025 (54,705 stints), 68,754 total rows.
- Verified against Supabase through `PgEngineConn`: Brad Wanamaker/Jeff Teague
  no longer resolve on Boston 2020; Derrick Rose/Carlos Boozer and Devin
  Hester/Matt Forte still resolve.
- Hockey is not strict yet because the current local NHL cache does not contain
  complete per-game or transaction stint windows. It still uses the
  season-level appearance fallback until NHL overlap evidence is added.

Update, 2026-08-15 (0.2.13): strict teammate overlap across all four sports.
- Bumped visible app version to `0.2.13`.
- Added baseball-only strict tables: `player_stints`,
  `teammate_stint_coverage`, and `teammate_exclusions`, with matching SQLite,
  Postgres, and runtime-schema definitions.
- Patched the baseball branch of `game.engine.get_shared_seasons` so covered
  baseball seasons require overlapping player stint ranges.
- Expanded `scripts/build_teammate_stints.py`:
  - `--sports` selector supports targeted rebuilds.
  - MLB stints are derived from official MLB Stats API regular-season
    boxscores, cached under `raw/mlb_statsapi`, with MLBAM IDs mapped back to
    Lahman/Baseball Reference IDs through the Chadwick register.
  - NHL stints are derived from official NHL GameCenter regular-season
    boxscores, cached under `raw/nhl_gamecenter`.
  - Boxscore fetching is parallel and cache-first.
- Published strict stint data to Supabase:
  - baseball: 37,773 stints, 2000-2025 strict coverage.
  - basketball: 14,049 stints, 2000-2025 strict coverage.
  - football: 54,705 stints, 2002-2025 strict coverage.
  - hockey: 25,739 stints, 2000-2025 strict coverage.
- Verified in production through `PgEngineConn`:
  - Kris Bryant / Frank Schwindel and Anthony Rizzo / Frank Schwindel no
    longer resolve through 2021 Cubs season-only overlap.
  - Anthony Rizzo / David Ross and Anthony Rizzo / Gleyber Torres still
    resolve.
  - Patrick Kane / Jonathan Toews still resolves; Kane / Connor Bedard does
    not.
- Film Review candidate generation now filters through strict overlap SQL for
  baseball and all cross-sport generators, preventing daily puzzle builders
  from proposing stale season-level-only links.
- Rebuilt Film Review archive rows for 2026-08-01 through 2026-08-16 for all
  four sports, including football offense and defense: 80 rows total.

Update, 2026-08-15 (0.2.14): headshot status correction and NFL fallback tooling.
- Bumped visible app version to `0.2.14`.
- Clarified the image-count definition:
  - MLB has full playable 2000-present display coverage.
  - NBA and NHL had verified registry URLs that were not published to
    `sport_player_images`; syncing those fixed display coverage to 100%.
  - NFL still has a large usable-image gap when placeholders are counted as
    missing. The correct current gap after two web-image batches and bad-row
    cleanup is 3,843
    playable NFL players with `player_headshots.status IN ('placeholder',
    'missing')`.
- Synced 74 verified NBA/NHL registry headshots into `sport_player_images`.
- Added `scripts/sync_verified_headshots_to_display.py` so verified
  `player_headshots` URLs can be republished into the app display table.
- Added `scripts/resolve_nfl_web_image_headshots.py`, a football-specific web
  image fallback resolver with exact-name, football-context, team-cue, and
  placeholder-fingerprint checks.
- Ran NFL web fallback batches:
  - First batch: 249 promoted from 250 checked.
  - Second batch: 169 promoted from 500 checked.
  - Third run hit search-provider timeouts and was stopped; no results from
    that run were written.
- Fixed `ensure_runtime_schema()` so it no longer tries to add a primary key to
  `film_review_daily_attempts` when the table already has one.
- Important NFL data-quality fix: nflverse 2025 weekly roster rows contained
  some no-identifier records with implausible `years_exp` values, for example
  Bob Stein and Ernie Koy. The football loaders now skip 2002+ rows without a
  durable player identifier, and 31 bad fallback-ID football records were
  removed from production.

Update, 2026-08-15 (0.2.15): FootballDB NFL headshot resolver.
- Bumped visible app version to `0.2.15`.
- Pro Football Reference is still not script-accessible from this environment:
  both player pages and direct `/req/.../images/headshots/{pfr_id}.jpg` URLs
  return Cloudflare-style `403` responses. Do not keep rerunning broad PFR or
  ESPN scans unless a new access method or new IDs are added.
- FootballDB is script-accessible with browser-style headers and exposes player
  CDN headshots on profile pages, for example
  `https://cdn.footballdb.com/headshots/NFL/2016/hestede01.jpg`.
- Added `scripts/resolve_nfl_footballdb_headshots.py`.
  - Builds/caches a paginated FootballDB player index in
    `raw/footballdb_nfl_player_index.csv`.
  - Matches remaining NFL gaps by normalized exact name.
  - Fetches candidate profile pages and uses image alt text, title, position,
    and team-name overlap to score identity confidence.
  - Validates image bytes against known NFL placeholder hashes/perceptual
    hashes.
  - Promotes accepted images into both `player_headshots` and
    `sport_player_images`, so accepted URLs are immediately visible in the live
    game.
- Made `scripts/resolve_nfl_web_image_headshots.py` flush verified rows every
  batch with `--flush-every`, preventing long web-search runs from losing all
  progress on timeout/interruption.
- Ran the FootballDB resolver against the currently matching unresolved NFL
  set. The first loose exact-name pass promoted 78 candidates, but manual
  inspection showed wrong-player risk on duplicate/common names, for example
  older James Williams rows matching a 2025 FootballDB James Williams page. The
  78 FootballDB promotions were reverted from `player_headshots` and
  `sport_player_images`.
- The FootballDB resolver now generates likely profile slugs such as
  `chris-carter-cartech02`, parses FootballDB NFL stat rows for pro career
  years and teams, and requires career-year plus team evidence before
  promotion. It also supports `--workers` for concurrent profile checks.
- First safe FootballDB live batch promoted 7 rows: Chris Carter, B.J.
  Daniels, John Lotulelei, Gabe Ikard, Dwayne Gratz, Khyri Thornton, and Kevin
  Norwood.
- Current verified/display coverage after this pass:
  - MLB: 5,237 / 5,237 playable 2000-present players verified.
  - NBA: 2,566 / 2,566 playable 2000-present players verified/displayed.
  - NHL: 4,501 / 4,501 playable 2000-present players verified/displayed.
  - NFL: 11,369 verified out of 15,205 playable 2000-present football players;
    3,836 remain with `placeholder`, `missing`, `wrong_player`, or `bad_crop`
    status.
- Remaining NFL examples after FootballDB: Chris Carter (2011-2017 OLB/LB),
  Edward Jasper (2000-2005 NT/DT), P.J. Alexander (2003-2007 G), Dan Buenning
  (2005-2008 G), Eddie Berlin (2001-2005 WR), Leonardo Carson (2000-2004 DT),
  Micah Knorr (2000-2004 P), Moe Williams (2000-2005 RB), Peter Warrick
  (2000-2005 WR), and Scott McGarrahan (2000-2005 SS/DB).

Update, 2026-08-16 (0.2.15 data pass): NFL headshot long-run status.
- User ran the full FootballDB resolver locally. Current Supabase football
  headshot status:
  - `verified`: 12,022
  - `placeholder`: 2,799
  - `missing`: 384
  - remaining unresolved: 3,183
  - FootballDB provider rows: 660
- Verified that all 660 FootballDB rows are also present in
  `sport_player_images` with matching URLs, so they are available to the live
  game.
- `scripts/resolve_nfl_web_image_headshots.py` now supports `--workers` and
  adds team-specific search queries such as `"Player Name" "Team Name" NFL
  headshot`. This is the next unattended local pass after FootballDB.
- Recommended local run for the next pass:
  `python scripts\resolve_nfl_web_image_headshots.py --delay 0.2 --workers 3 --flush-every 10`
- After web-image URL promotion, crop and publish accepted fallback photos to
  Supabase Storage with:
  `python scripts\crop_recent_headshots.py --sport football --provider "Web image search" --workers 8`
- Added review-based candidate collection:
  - `scripts/collect_nfl_headshot_review_candidates.py` gathers loose image
    candidates into `raw/nfl_headshot_review/pending` and creates
    `raw/nfl_headshot_review/review.html`.
  - `scripts/import_nfl_reviewed_headshots.py` imports whatever remains in
    `pending` after manual deletion, crops it, uploads it to Supabase Storage,
    and updates `player_headshots` plus `sport_player_images`.
  - Collector now defaults to a priority queue from the newest
    `raw/nfl_unresolved_headshots_*.csv`, supports `--min-games`,
    `--queries-per-player`, and prints every completed player so it does not
    feel stalled.

Update, 2026-08-16 (NFL priority headshot workflow): manual-first football gap list.
- User wants remaining NFL photos resolved by priority, starting with players
  who have the most corrected games played. College photos are acceptable when
  NFL uniform photos are unavailable, as long as identity is reliable.
- Added `scripts/export_nfl_headshot_priority.py`.
  - Exports every unresolved 2000-present NFL player from Supabase into
    `raw/nfl_headshot_priority_list.csv`.
  - Uses corrected games played: one max games value per player/team/season,
    avoiding inflated totals from duplicate appearance rows.
  - Adds known teams, season/team context, existing attempted sources, Google
    web/image links, college-image search links, and likely FootballDB profile
    guesses.
  - Current all-gap export: 3,163 unresolved NFL players.
- Exported the high-priority subset to
  `raw/nfl_headshot_priority_50plus.csv`: 234 unresolved NFL players with at
  least 50 corrected games played.
- Added `scripts/build_nfl_headshot_priority_review.py`, which turns the 50+
  CSV into `raw/nfl_headshot_priority_50plus_review.html` for browser review.
  Open this file locally to click through candidates/search links.
- Added `raw/nfl_50plus_photo_research_top10.csv` as the first researched
  batch. Good/likely candidates found:
  - Leonardo Carson: Auburn, direct image from Auburn archive:
    `https://www.autigers.com/archive/1997/fb-peach/carson.jpg`.
  - Al Johnson: Wisconsin, UW coach page has a usable current/professional
    image.
  - Otis Grigsby: Kentucky, official Kentucky roster page has a headshot.
  - Cecil Sapp: Colorado State, official CSU hall-of-fame page has an image.
  - Reynaldo Hill: Florida, official Florida roster page plus Getty references.
  - Others in the first ten have college/source context but still need visual
    confirmation or a cleaner image.
- Attempted a Wikipedia college supplement script, but Wikipedia API returned
  429 rate-limit responses quickly. The local nflverse roster files have many
  blank college values for older 2000s players, so the current reliable path is
  source-by-source/manual priority review, not another broad blind scrape.
- The automated DuckDuckGo image candidate collector has been low yield for
  football. Do not rely on it as the primary NFL completion path unless the
  query/source strategy changes.
- Added `scripts/collect_nfl_priority_source_candidates.py` for a better
  semi-automated pass over the 50+ games priority group.
  - It reads `raw/nfl_headshot_priority_50plus.csv`, optionally uses researched
    values from `raw/nfl_50plus_photo_research_top10.csv`, searches likely
    college/team/player source pages, scrapes page images, rejects known NFL
    placeholders and obvious placeholder/logo URLs, and writes:
    - `raw/nfl_priority_source_review/pending/*.jpg`
    - `raw/nfl_priority_source_review/candidates.csv`
    - `raw/nfl_priority_source_review/review.html`
  - It does not promote anything into the game. User should open the review
    HTML, delete bad images from `pending`, then run
    `scripts/import_nfl_reviewed_headshots.py`.
  - Importer was updated to support multiple candidate filenames per player,
    such as `nfl_00-0022079__1.jpg`.
  - First test with top 3 priority players found usable/visible candidates for
    Leonardo Carson and Al Johnson. Micah Knorr still had no candidate in that
    narrow pass. The process correctly remains manual-review-first because
    source pages/search can still surface wrong players or placeholder graphics
    such as the earlier Reynaldo Hill issue.

Update, 2026-08-16 (0.2.16): curated puzzles now require verified headshots.
- User decided to stop chasing every obscure NFL photo for now. Remaining NFL
  placeholder/missing players should stay playable in user-driven modes
  (Division Rivalry and Playoffs) if someone names them, but they should not be
  selected by curated daily experiences.
- Bumped visible app version to `0.2.16`.
- `game/film_review_generator.py` now requires `player_headshots.status =
  'verified'` for Film Review eligibility whenever the headshot registry table
  exists. This blocks missing/placeholder/bad-crop NFL players from starters
  and from every planned Film Review card.
- Existing daily Film Review rows are revalidated in `web/server.py`; cached
  puzzles whose deck includes a non-verified headshot are deleted and rebuilt.
- Manager Mode daily starters now require a verified headshot as well, including
  baseball, basketball, hockey, and football. User-played cards in Manager Mode
  may still show placeholders if the user chooses an unresolved player.
- Updated `scripts/validate_film_review_local.py` so football validation checks
  the actual offense/defense puzzle units instead of the obsolete 24-card full
  football lineup.
- Validation run passed:
  `python scripts\validate_film_review_local.py --days 14 --start 2026-08-16`
  generated 14/14 for baseball, basketball, hockey, football offense, and
  football defense.

Update, 2026-08-17 (0.2.17): playtest bug pass for baseball search and Film Review.
- Bumped visible app version to `0.2.17`.
- Fixed baseball autocomplete excluding active/current players. The `players`
  table stores active players with `final_year = NULL`, so autocomplete now
  uses `COALESCE(final_year, 9999) >= 2000`. Verified that `/api/autocomplete`
  and `/api/sports/baseball/autocomplete` both return Kris Bryant for
  `kris bryant`.
- Patched `/api/sports/baseball/autocomplete` to delegate to the established
  baseball autocomplete path so baseball does not use cross-sport search logic.
- Patched cross-sport Manager Mode endpoints to route baseball engine calls
  through `_engine_sport("baseball")`, keeping baseball on the established MLB
  tables if those endpoints are reached.
- Verified Rizzo to Kris Bryant through `/api/bp/move` returns `valid`.
- Removed Film Review percentage display from the end summary, in-game archive,
  and `/film` mode hub labels. API fields may still exist internally, but the
  player-facing percentage is gone.
- Fixed Film Review progress counting on failure: zero solved links now shows
  `0/total`; after the first solved link, progress resumes from the revealed
  lineup count.
- Added `/api/cron/generate-film-review`, which prebuilds today's Film Review
  puzzles for baseball, basketball, hockey, football offense, and football
  defense. It supports optional `CRON_SECRET` through `Authorization: Bearer`
  or `?token=`.
- Updated `vercel.json` with daily cron hits at `05:05 UTC` and `06:05 UTC`.
  The endpoint is idempotent; the two schedules cover midnight Central across
  daylight/standard time. Manual test generated all five daily puzzle units for
  2026-08-17.

Update, 2026-08-17 (0.2.18): Film Review hub storage/performance pass.
- Bumped visible app version to `0.2.18`.
- Film Review daily puzzle rows now store `preview_cards` directly inside the
  immutable puzzle JSON. This makes `/film` read the day's starter preview from
  the stored daily tape instead of hydrating player cards repeatedly on every
  hub load.
- Existing daily puzzle rows are enriched lazily the next time they are read,
  and newly generated rows are stored with preview data immediately.
- `/api/film/archive_summary` now reads all user Film Review attempts in one
  query and computes archive rows/streaks in memory, instead of running
  repeated sport/unit queries.
- `/api/film/archive_summary` and `/api/film/previews` load all current-day
  puzzle previews in one query through `_film_preview_map_for_day`.
- Local endpoint timing after cached previews: first cold test request about
  6.6 seconds in the local shell, repeated warm requests about 0.9 seconds.
  The midnight Vercel cron remains important because it ensures the daily tape
  and preview payload are present before users open `/film`.

Update, 2026-08-17 (0.2.19): Film Review launch payload cache.
- Bumped visible app version to `0.2.19`.
- Film Review daily puzzle rows now also store the full launch payload needed
  by `/api/fr/new` and `/api/sports/<sport>/fr/new`: `card_map` and
  `shared_per_pair`, in addition to the `preview_cards` from 0.2.18.
- Launch routes now copy cards and teammate-link answers from the immutable
  daily puzzle JSON instead of recalculating every player card and every link
  the first time a player opens that day's puzzle.
- Existing puzzle rows are enriched idempotently when read. The cron endpoint
  now supports `?days=N` with a max of 60, so after deploy run
  `/api/cron/generate-film-review?days=60` once to backfill the archive and
  keep first player puzzle loads from doing enrichment work.
- Local launch timing after enrichment: baseball, basketball, hockey, and
  football Film Review starts were about 0.65-0.75 seconds through Flask's test
  client. The remaining time is mostly database round trips and game-row
  creation, not puzzle generation.

Update, 2026-08-18 (0.2.20): cross-year Film Review season input and card ranges.
- Bumped visible app version to `0.2.20`.
- Basketball, hockey, and football Film Review now use split season inputs
  (`YY - YY`) instead of a single year box. The first box auto-advances after
  two digits and pre-fills the first digit of the next season year when obvious.
- Cross-year Film Review validation now requires the exact season span, such as
  `24-25` for the 2024-25 season. Guessing only the ending calendar year no
  longer counts as a season match.
- Baseball Film Review still uses the existing four-digit year input.
- Cross-sport player cards now render consecutive team spans compactly, e.g.
  `Chicago Blackhawks 2007-2023` instead of
  `2007-08 to 2022-23`. Single cross-year seasons still display as
  `2024-25`, and separated same-team returns remain comma-separated ranges.

Update, 2026-08-18 (0.2.21): Film Review season input simplification.
- Bumped visible app version to `0.2.21`.
- Basketball, hockey, and football Film Review now use one editable full
  start-year field, e.g. `2020`, with a read-only suffix that displays the
  submitted season as `2020-21`.
- The submitted value remains the exact season label (`2020-21`), so backend
  validation from 0.2.20 still requires the correct cross-year season instead
  of accepting either calendar year.
- Updated Film Review page and rules copy from "team and year" to
  "team and season" where appropriate.

Update, 2026-08-20 (0.2.22): Film Review no-repeat opening and faster guess response.
- Bumped visible app version to `0.2.22`.
- Film Review generation now reads prior stored daily puzzles and avoids:
  - any prior first-two opening player appearing in the first two slots again;
  - any prior adjacent teammate pair appearing again anywhere in the lineup.
- The no-repeat rule is enforced by daily generation, cron prebuild, and
  `scripts/rebuild_film_review_puzzles.py`. Existing rows that violate the
  rule are considered invalid and rebuilt when read.
- Added seed-suffix retries so deterministic daily puzzles can find an
  alternate lineup without changing the public puzzle date/number.
- Film Review state responses no longer calculate daily streak on every active
  guess. Streak is now computed only when the puzzle is finished, reducing
  unnecessary DB work before showing Hit/Foul/Strike feedback.
- Dry run against production data showed baseball 2026-08-20 would rebuild away
  from the repeated Cano/Overbay opening to Edwin Encarnacion/Jeff Conine.

Update, 2026-08-20 (0.3.00): UI/UX stage started.
- Bumped visible app version to `0.3.00`; this marks the start of the stage 3
  visual and usability pass.
- Added a shared visual foundation in `web/static/style.css`: darker app-style
  shell, sticky translucent header, stronger button/input styling, sport accent
  colors, mode accent colors, improved sport/mode tiles, polished queue panels,
  refined game surfaces, mobile layout tightening, and clearer powerup chip
  colors.
- Homepage mode links now use mode-specific card classes. Sport pages now apply
  the same Manager Mode, Film Review, Division Rivalry, and Playoffs tile
  language through the shared design system.
- Mode hub pages (`/manager`, `/film`, `/division`, `/playoffs`) now share the
  same hub styling, sport tile polish, and multi-sport queue treatment.
- Powerup references on mode hubs now render with the same chip/icon/color
  pattern used inside Playoffs, so the queue/reference/game UI teaches the same
  visual vocabulary.
- This pass is intentionally UI-first and avoids changing teammate matching,
  timer, queue, archive, or gameplay logic. Next UI passes should focus on
  screen-specific information architecture: Playoffs HUD density, mobile
  gameplay ergonomics, profile/friends polish, and Film Review archive browsing.

Update, 2026-08-20 (0.3.01): homepage redesign pass.
- Bumped visible app version to `0.3.01`.
- Root homepage now uses a compact collapsible account tray instead of a large
  profile/account block. The tray shows the active display name and whether the
  user is signed in or playing as a guest.
- Root homepage sport and mode choices are now one equal 8-tile launch grid
  with large color blocks and inline SVG line icons: baseball, basketball,
  puck, football, headset, video camera, foam finger, and trophy.
- Added a stronger display-font stack and brighter color treatment for the
  homepage only. The sport pages keep their existing shared tile foundation for
  now while page-by-page UI work continues.
- Renamed the in-app `Ref` modal title to `Playoffs Reference`.
- Playoffs reference win-condition content now renders as a table distinct from
  powerup chips. Powerup copy was normalized across sports so same-function
  powerups use matching phrasing and keep their shared color/icon treatment.

Update, 2026-08-20 (0.3.02): homepage polish iteration.
- Bumped visible app version to `0.3.02`.
- Added the user-provided Panton trial webfont files under
  `web/static/fonts/WEB/` and configured the site to use Panton first with
  system fonts as fallback. Confirm licensing before production use beyond
  playtesting.
- Root homepage now separates Sports and Gamemodes into two labeled sections.
  Tiles no longer show `Sport` or `Mode` labels.
- Sports tile captions are now `Hit the Diamond`, `Hit the Court`, `Hit the
  Ice`, and `Hit the Field`. Mode tile captions are now `Solo Lineup`,
  `Daily Puzzle`, `Head-to-head`, and `Powers + Win Conditions`.
- Root homepage copy changed to `Pick a Sport or Gamemode`.
- Account tray no longer appends `- guest` for guest profiles.
- Homepage icons were revised: basketball, puck, football, camera, foam finger,
  and trophy line art were cleaned up. Icons now sit bottom-right while names
  stay top-left and captions sit bottom-left.
- Playoffs Reference powerup rows now match the organized two-column structure
  of the win-condition table area, while preserving powerup chip colors.

Update, 2026-08-20 (0.3.03): homepage icon/reference cleanup.
- Bumped visible app version to `0.3.03`.
- Forced root homepage tile icons to bottom-right with final CSS overrides
  because older duplicated homepage rules were overriding the intended position.
- Reworked the Division Rivalry hand/foam-finger icon and Playoffs trophy icon.
- Changed the Playoffs homepage caption from `Powers + Win Conditions` to
  `Powers and Win Conditions` because the trial Panton plus glyph rendered
  poorly.
- Replaced Playoffs Reference powerup letter placeholders with first-pass inline
  SVG line icons across the in-app reference and mode-hub reference. Baseball
  now has bubblegum, pine tar/bat, bat donut/bat, sunglasses, glove, strike
  zone, and flaming-ball/quick-pitch style icons. Basketball, football, and
  hockey use matching sport-themed first-pass line icons for review.
- Removed duplicate powerup names from the reference copy column. The colored
  chip carries the name/icon; the row copy now focuses on the effect.
- Powerup reference rows now use a distinct gray table-like background separate
  from the default dark modal background.

Update, 2026-08-20 (0.3.04): Carter One font trial.
- Bumped visible app version to `0.3.04`.
- Added the user-provided Carter One font under
  `web/static/fonts/carter-one/` and made it the active global site font.
- Kept the earlier Panton files in `web/static/fonts/WEB/` as a fallback and
  comparison option during the UI/UX pass.

Update, 2026-08-20 (0.3.05): LT Karaoke Body and icon polish.
- Bumped visible app version to `0.3.05`.
- Added the user-provided LT Karaoke Body family under
  `web/static/fonts/lt-karaoke-body/` and made it the active global site font.
  Removed the earlier Carter One and Panton font files from local static assets.
- Homepage mode captions now use `Head-to-Head` and `Powerups and Win
  Conditions`.
- Replaced the Division Rivalry homepage icon with a clearer raised index
  finger/number-one hand.
- Reworked first-pass Playoffs powerup SVGs in both `web/static/main.js` and
  `web/static/mode_hub.js`: candy-wrapper Bubblegum, pine-tree Pine Tar,
  clearer Bat Donut bat, mitt-shaped Backup Mitt, 3-by-3 ABS strike zone,
  home-plate Quick Pitch, improved Heat Check flame, locker Sixth Man, circular
  arrows Switch, overhead court Full-Court Press, brighter Trick Play sparkles,
  X/O Package Change, clearer Breakaway puck, faceoff-dot Line Change, and
  action-line Forecheck.
- Added a brighter homepage atmosphere layer with multi-color radial lighting,
  subtle grid texture, and a glassier home panel to move away from the flatter
  dark background.

Update, 2026-08-21 (0.3.06): homepage structure and rules cleanup.
- Bumped visible app version to `0.3.06`.
- Removed the duplicate root homepage `TeamMateTag` heading beneath the header;
  the homepage now keeps `Pick a Sport or Gamemode` as the first-page prompt.
- Removed the hard-edged glass panel around the root homepage launch grid so the
  sport/mode tiles sit directly on the blended background.
- Changed the account tray affordance from `Edit` to `Login` and added more
  spacing between the opened tray summary and profile/login controls.
- Replaced the Division Rivalry homepage hand icon with a foam-finger-like
  number-one outline to avoid the middle-finger read.
- Rewrote the `?` How to Play copy in `web/static/main.js` and
  `web/static/mode_hub.js` for clearer, shorter mode flow explanations:
  Manager Mode, Film Review, Division Rivalry, and Playoffs now define the
  key flow, team-mark rules, sport-specific out terms, and Film Review feedback
  terminology more directly.

Update, 2026-08-21 (0.3.07): rules sheet restructure and icon note.
- Bumped visible app version to `0.3.07`.
- Simplified the Division Rivalry homepage icon again. The raised pointer is
  now on the edge of the hand, with less interior detail, to avoid reading as a
  middle finger.
- Rebuilt the `?` How to Play content in both `web/static/main.js` and
  `web/static/mode_hub.js` as a single non-redundant rules sheet: game modes
  first, vocabulary with examples second, and a sport-term key at the bottom.
- Added rules modal styling for colored game-mode cards, compact vocabulary
  cards, and a responsive sport-term table.
- TODO for the UI/UX pass: revisit the Playoffs Reference icons. Several are
  acceptable placeholders, but the full powerup icon set still needs a more
  deliberate second pass for clarity and consistency.

Update, 2026-08-21 (0.3.08): tighter rules copy and non-bloom color.
- Bumped visible app version to `0.3.08`.
- Rewrote the How to Play rules sheet again to better match the user's style:
  shorter, less repetitive, baseball terms first, with sport-specific wording
  only in the key. Removed the `Top Player` vocabulary card and changed
  `Team Marks` language to `Team Strikes`.
- Kept all public vocabulary labels capitalized, including multi-word terms
  such as `Teammate Link`, `Team-Season`, `Team Strikes`, `Blocked Guess`, and
  `Win Condition`.
- Replaced the corner/radial bloom page background with angled color bands and
  subtle field/court-like texture so color is present without the repeated
  corner-fade effect. Homepage sport tiles now use angled color bands instead
  of radial blooms for the same reason.

Update, 2026-08-21 (0.3.09): scoped modal color pass.
- Bumped visible app version to `0.3.09`.
- Reverted the global dark page shell back to the restrained dark background.
  The user liked the homepage direction and clarified that additional color
  should be focused on modal/reference screens, not the whole homepage.
- Moved the richer angled color treatment into `.modal-content`, especially for
  `?` How to Play and `Ref` / Playoffs Reference surfaces. Rules cards and the
  sport-term table now carry more color while the main page stays calmer.

Update, 2026-08-22: copy editing worksheet.
- Created `docs/TeamMateTag_Copy_Edit_Worksheet.docx` as an editable worksheet
  for the user to revise the `?` How to Play and `Ref` / Playoffs Reference
  copy outside the app. It includes stable IDs, current text, and blank edit
  columns for game modes, vocabulary, sport terms, reference intros,
  win-condition categories, all sport powerups, and win-condition dropdown
  labels.
- Added `scripts/create_copy_edit_worksheet.py` so the worksheet can be
  regenerated from the current known copy structure if needed.

Update, 2026-08-22 (0.3.10): worksheet copy applied and matte texture.
- Bumped visible app version to `0.3.10`.
- Applied the user's filled worksheet edits to the shared `?` How to Play copy
  in both `web/static/main.js` and `web/static/mode_hub.js`.
- Applied Playoffs Reference copy updates for reference intros, win-condition
  category descriptions, and powerup descriptions across baseball,
  basketball, football, and hockey.
- Renamed several powerup display labels from the worksheet: basketball
  `All-Star Call-Up` now displays as `Star Power`, football `Pro Bowl Call-Up`
  now displays as `Bowler`, and hockey `All-Star Call-Up` now displays as
  `All-Star`.
- Updated local Playoffs gameplay thresholds to match the edited reference text
  where the rule changed: hockey `Breakaway` now requires 400+ career goals,
  hockey `Veteran Presence` now requires 800+ career points, and football
  `Trick Play` now uses a 20+ touchdown season stat instead of loose career
  touchdowns.
- Reworked the global background again: no corner bloom and no flat black.
  The site now uses a restrained matte dark gradient with subtle grain,
  low-contrast color wash, and faint field/court-like texture.

Update, 2026-08-22 (0.3.11): sport-specific rules/reference cleanup.
- Bumped visible app version to `0.3.11`.
- Fixed the missed Sport Terms worksheet edits: the Meaning column now uses the
  user's updated definitions, including `Tipped Pass`, basketball `Foul`, and
  `3 Misses and you're Benched`.
- Changed sport-page `?` modals to show only the How to Play Game Modes
  section, using that sport's terms instead of the full homepage vocabulary and
  sport-term table. Root homepage and gamemode hub `?` modals still show the
  broad overview, vocabulary, and Sport Terms key.
- Changed sport-page `Ref` modals so the Win Conditions section lists the
  actual sport-specific Win Condition names and requirements instead of broad
  category rows. Root homepage/gamemode hub reference surfaces may still use
  broad or all-sport overviews.
- Cleaned the gamemode hub queue copy: `Multi-Sport Queue`, removed the helper
  sentence about picking one or more sports, and removed redundant league text
  from the multisport queue options.
- Locked sport colors to the requested set: baseball red, basketball orange,
  hockey blue, football green. This fixes Baseball and Football both reading as
  green on gamemode queue pages.
- Reworked the background again toward a more colorful matte app/game surface:
  color is back in the page background, while `?` and `Ref` modals use a calmer
  readable dark panel with color reserved for cards/tables.
- Refreshed `scripts/create_copy_edit_worksheet.py` so future copy worksheets
  start from the current terms and Ref intro instead of stale 0.3.10 wording.

Update, 2026-08-22 (0.3.12): context-specific rules and Playoffs label cleanup.
- Bumped visible app version to `0.3.12`.
- Replaced the visible diagonal line background with a smoky multi-color dark
  background. User disliked the colored stripe/line treatment and prefers color
  blended into the page more naturally.
- Added more color structure to rules Vocabulary cards and Ref tables so the
  modal content is not plain dark/gray, while keeping text readable.
- Changed gamemode hub `?` modals (`/manager`, `/film`, `/division`,
  `/playoffs`) to show only the selected mode's rules, with no Vocabulary or
  Sport Terms sections.
- Changed in-game sport-page `?` modals so active games show only that specific
  mode's rules using that sport's terminology. Sport home screens still show
  the condensed all-mode game-mode section.
- Updated Playoffs win-condition dropdown labels on the multisport hub and
  sport pages to include the required amount/threshold, e.g. `Sunset Kingdom:
  3 Japanese Players`.
- Fixed Baseball Ring Chaser reference text to match the server rule: 15
  combined World Series rings.

Update, 2026-08-22 (0.3.13): background and modal color correction.
- Bumped visible app version to `0.3.13`.
- User disliked the colored stripe background and also dislikes repeated
  top-left corner color blooms. Replaced the global background with a broader
  multi-color smoky field using red, orange, yellow, green, blue, and violet
  areas distributed around the viewport.
- Removed the repeated corner-fade treatment from `?` and `Ref` content:
  rules cards, Vocabulary cards, modal panels, and Ref tables now use readable
  horizontal color bands, colored borders, and text labels instead of diagonal
  corner blooms.
- Accessibility direction: do not rely on color alone. Keep labels, headings,
  borders, and icons/powerup names visible for colorblind users.

Update, 2026-08-22 (0.3.14): fixed missed background cascade.
- Bumped visible app version to `0.3.14`.
- Corrected a miss from `0.3.13`: the smoky background was scoped mainly to
  `body.dark`, but gamemode hub pages such as `/playoffs` use
  `body[data-mode-hub]` without the `dark` class. The global smoky background
  now applies to `html`, `body`, sport pages, and gamemode hub pages.
- Removed the old body stripe source and replaced sport-tile stripe overlays
  with a subtler bottom color glow.
- Removed the rainbow Win Conditions / Ref table treatment. Ref tables now use
  neutral readable surfaces with a restrained blue header and clear text labels.
- Continued visual direction: no striped backgrounds, no rainbow tables, no
  repeated corner-bloom treatment inside cards/modals/tables.

Update, 2026-08-22 (0.3.15): tile/icon and rules vocabulary distinction.
- Bumped visible app version to `0.3.15`.
- Made the `?` Vocabulary section visually distinct from Game Modes: it now
  reads as compact definition rows with pill labels and left color bars instead
  of another grid of similar feature cards.
- Added the homepage mode icons to sport-page mode buttons and removed their
  generic colored circle pseudo-icon.
- Rebuilt the Division Rivalry icon from scratch so the raised finger is on the
  edge and reads more clearly as a `#1` hand/foam-finger concept instead of a
  middle-finger shape.
- Cleaned gamemode hub sport buttons: removed the giant league ghost text on
  those pages, hid repeated league text, increased sport-name scale, and added
  sport icons in the lower-right so labels do not overlap.

Update, 2026-08-22 (0.3.16): glossary and foam-finger correction.
- Bumped visible app version to `0.3.16`.
- Fixed `/football` sport-page headline from `Hit The Gridiron` to
  `Hit The Field`.
- Reimagined the `?` Vocabulary section as a compact glossary/reference list
  with term labels, definitions, and category color bars instead of card blocks
  that looked too similar to Game Modes.
- Replaced the Division Rivalry icon again after looking up foam-finger shape
  references: now it is a simple foam prop outline with `#1`, minimal lines,
  and no anatomical finger-detail clutter.
- Cleaned more visible rules/button copy to better preserve the capitalization
  style from the copy worksheet.

Update, 2026-08-22 (0.3.17): foam-finger icon retry from user reference.
- Bumped visible app version to `0.3.17`.
- User provided a reference image for the Division Rivalry foam finger. Replaced
  both homepage/sport-page Division Rivalry SVGs with a simpler foam prop
  silhouette: tall raised index finger on the left, rounded finger bumps to the
  right, chunky palm outline, and minimal `#1` interior marks.

Update, 2026-08-22 (0.3.18): foam-finger interior cleanup.
- Bumped visible app version to `0.3.18`.
- Kept the Division Rivalry foam-finger outline but removed the interior finger
  detail lines the user disliked. Replaced them with a path-drawn `#1` so the
  hashtag renders as actual crossing strokes instead of broken text.

Update, 2026-08-22 (0.3.19): foam-finger #1 placement.
- Bumped visible app version to `0.3.19`.
- Kept the Division Rivalry foam-finger outline unchanged. Moved the path-drawn
  `#1` upward and made it slightly larger so it fills the palm better.

Update, 2026-08-22 (0.3.20): stronger foam-finger #1 adjustment.
- Bumped visible app version to `0.3.20`.
- User still saw too much empty palm space above the `#1`; moved the `#1`
  higher again and enlarged it more noticeably while keeping the outline fixed.

Update, 2026-08-22 (0.3.21): sport-page gamemode color alignment.
- Bumped visible app version to `0.3.21`.
- Left the foam finger unchanged after user said it is okay now, maybe slightly
  too big.
- Added sport-page-specific CSS overrides so gamemode buttons match homepage
  gamemode colors: Manager Mode gray, Film Review violet, Division Rivalry
  pink, Playoffs yellow.
Update, 2026-08-22 (0.3.22): playtest bug pass after mobile/Film Review feedback.

- Film Review now sends the current pair's exact valid team-season answers in
  game state. The browser uses those answers for the autocomplete list, so the
  input is now one combined field such as `Chicago Bears 2020-21` instead of
  separate team and year boxes.
- The Film Review answer suggestions are deduped by displayed team plus season,
  which prevents duplicate source IDs from showing the same visible answer
  twice, such as two `Vegas Golden Knights 2017-18` rows.
- Film Review lineup boards now render as collapsible boards and include small
  headshots for filled slots. On narrow mobile screens the board defaults
  collapsed so the two visible players and answer box stay closer together.
- Film Review generation now uses a tighter early-choice window and early-score
  floor. Early links should skew more recent and recognizable while still
  allowing deeper players later in the lineup if needed.
- Added a football Film Review/gameplay guard against suspicious recent-season
  rows where a long-retired player reappears after a multi-year gap. This was
  triggered by a bad `Philip Rivers 2025 Colts` row and now rejects that class
  of link in Film Review generation, Film Review validation, and the shared
  game engine.
- Added football display-name overrides for `Dak Prescott` and `Nyheim Hines`
  so cards, prompts, and autocomplete use the expected public names even if a
  source table contains legal/given-name variants.
- Mobile Lineup/Out panels were compacted into a two-column layout with
  scrollable sections. The ordered Lineup list now uses tabular numbers so
  double-digit entries do not jump or wrap awkwardly.
