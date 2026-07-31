# TeamMateTag project handoff

Update this file whenever product behavior, deployment, data, or active work
changes. It is the concise source of truth for another coding assistant.

## Product and deployment

- Production: `https://teammatetag.com`
- Vercel deployment: `https://teammatetag.vercel.app`
- Repository: `https://github.com/laudedlem/TeamMateTag`
- Local repository folder: `C:\Users\laude\Desktop\base2nerdle`
- Current display version: `0.1.20`
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
  secret win condition. Balance and quality testing are deferred.

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

- Scope loaded: MLB 2000 through 2025.
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

1. Data source evaluation and ingestion for NBA, NHL, NFL.
2. Build season-level roster/appearance records and derived teammate graphs.
3. Add a sport adapter to the server and frontend APIs.
4. Enable the four modes sport by sport, starting only after data validation.

The new additive schema is `db/cross_sport_schema_postgres.sql`:

- `sports`, `sport_franchises`, `sport_teams`
- `sport_players`, `sport_appearances`, `sport_teammates`
- `sport_players_searchable`, `sport_player_aliases`,
  `sport_data_provenance`

It deliberately does not alter the working baseball tables. Apply it with:

```powershell
cd C:\Users\laude\Desktop\base2nerdle
python scripts\setup_cross_sport_schema.py
```

That migration is additive and safe to run repeatedly.

Data-source research and rollout criteria are maintained in
`docs/cross_sport_data_plan.md`. Current order: NFL first, NHL second, NBA
after a permitted and dependable historical player-team-season source is
selected.

## Important files

- `web/server.py`: Flask routes, database access, game state, auth, online play.
- `web/static/main.js`: client state, rendering, timers, autocomplete, polling.
- `web/static/style.css`: dark UI styles.
- `web/templates/index.html`: sport home and current baseball screens.
- `game/engine.py`: shared baseball lineup validation.
- `db/schema_postgres.sql`: original baseball production schema.
- `db/cross_sport_schema_postgres.sql`: generic NBA/NHL/NFL schema.

## Known follow-ups

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
