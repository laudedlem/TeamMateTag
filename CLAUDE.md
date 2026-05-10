# Teammate Tag — project context

Production domain: **teammatetag.com**. Repo, file paths, and the sqlite
filename still use the old codename `base2nerdle`; treat them as synonyms.
A folder/sqlite rename is queued for before deployment but hasn't been
done yet, so any new code should reference paths via `Path(__file__).parent`
rather than the literal name.

This file is read automatically at the start of every Claude Code session.
Update it as decisions evolve. It's the source of truth for what this
project is and what the current rules are.

## What it is

A baseball lineup-chain game. Three modes are playable locally today at
http://127.0.0.1:5000/ (Flask + vanilla JS):

- **Batting Practice** (solo, timed). Name a teammate of the last player
  in the Lineup. 30s per turn, the clock resets on each successful link.
  Team strikeouts apply (Rule B). The run ends when your timer hits zero.
  Score is the longest Lineup you build. Replay to beat your own record.
- **Film Review** (daily puzzle). Nine players form an eight-link chain.
  You see the first two; guess the team and year that links each pair.
  Hit (both correct) reveals the next player. Foul (only one of team/year
  correct) retries. Strike (both wrong) retries. Two fouls in a row
  promotes the second foul into a strike. Three strikes ends the review.
- **Division Rivalry** (multiplayer, 2 players head to head). Alternating
  turns, 30s clock per turn, team strikeouts. You win when your opponent's
  clock runs out before they name a teammate. Was previously called
  "Lineup Battle"; renamed in 2026-05.

Batting Practice and Division Rivalry both display a 3-second pre-game
countdown in the timer slot before the live clock starts. Input is
disabled during the countdown.

Data scope: **2000–present**. Players whose careers extend before 2000 are
eligible, but only via their 2000-or-later team-seasons.

## Core mechanics (shared by BP and DR)

### Team-strike system
- A successful link strikes EVERY team-season the two players shared. Linking
  A-Rod → Jeter strikes every Yankees year they overlapped (2004 NYY, 2005
  NYY, 2006 NYY, …). One strike per season, spread across all of them. Long
  shared tenures are strategically expensive because they burn through many
  team-seasons at once.
- A team-season with **3 strikes is Struck Out** and can no longer be used
  to link two players.
- **Rule B**: a link is invalid if any single shared team-season is already
  Struck Out, even if other shared seasons aren't. One burned year poisons
  the entire pair.

Film Review doesn't use this system — instead it just checks the user's
typed (team, year) against the actual shared seasons of each puzzle pair.

### Lineup
- The chain of players starting from the leadoff (seed). Each link must be
  a teammate of the previous player.
- A `player_id` appears at most once per Lineup.

### Timer (BP + DR)
- 30 seconds per turn. Server-authoritative: the client's countdown is
  informational only; the server's monotonic clock decides. Premature
  client-claimed timeouts are rejected (server allows a 0.25s grace).
- 3-second pre-game countdown shown in the timer slot before the live clock
  starts. The big-overlay version was tried and rejected; the countdown
  now just counts down in the timer text.

### Feedback on invalid moves
- Unknown player → "unknown player."
- Already-used → "X already used in this lineup."
- Not-a-teammate → "X was never a teammate of Y."
- Blocked-by-burned (Rule B) → reports which team-season is already
  Struck Out so the player can adjust strategy.

## Data layer (`db/`, `etl/`)

### Schema highlights (`db/schema.sql`)
- `players` — every MLB player active 2000+ (Lahman + Chadwick crosswalk for IDs).
- `teams` — every team-season in the window. `teams.name` is season-specific
  (`Montreal Expos` 2003 vs `Washington Nationals` 2005), distinct from
  `franchises.name` which collapses to whichever name the loader saw first.
- `appearances` — who played for whom in which year (source of truth).
- `teammates` — derived graph: one row per (player_a, player_b, team, season)
  where both appeared in ≥1 game. The "at least one game together" rule is
  approximated at the season level. False positives (player traded in May
  vs. a callup in August) are handled via `connection_reports`, not by
  trying to fix in data.
- `players_searchable` — autocomplete-ready table with normalized search
  keys (accents stripped, periods removed), nicknames, career game counts,
  teammate-degree counts.
- Multiplayer state tables (`users`, `games`, `game_participants`,
  `game_moves`, `connection_reports`) — schema exists but is unused for
  now; all game state lives in process memory until we move to Supabase.

### What's NOT yet in the schema
- `game_team_strikes` — (game_id, team_id, season, strike_count) per active game.
- `game_player_powerups` — (game_id, user_id, powerup_type, remaining_count).
- `bp_runs` / `bp_leaderboard` — solo run records.
- `fr_puzzles` / `fr_attempts` — daily puzzle pool + per-user attempt history.

All of these wait for the Supabase migration.

### Pipeline (`etl/` — run in this order)
1. `01_download_lahman.py` — fetches Lahman CSVs.
2. `02_load_lahman.py` — loads CSVs into SQLite.
3. `04_load_chadwick_ids.py` — enriches with `mlbam_id` + nicknames.
4. `03_build_teammates.py` — derives teammate graph + searchable index.
5. `05_update_current_season.py` — daily refresh from statsapi.mlb.com during the season.

Verification: `tests/verify_graph.py`. Diagnostics: `scripts/analyze_graph.py`.
Game-time query reference: `scripts/query_examples.py`. Name normalization:
`scripts/name_normalize.py`.

### Data state (as of 2026-05-10)

- **Loaded**: 2000–2025 (26 seasons). Sources: SABR Lahman 2025 release for
  players/teams/appearances; Chadwick Register for `mlbam_id` + nicknames.
  Counts: 7,170 players, 780 team-seasons, 37,867 appearances, 916,127
  teammate-season edges. One connected component, 100% of players reachable.
- **Deferred**: 2026 in-season data (`etl/05_update_current_season.py
  --season 2026` before launch). Pre-2000 expansion (`02_load_lahman.py
  --start-year 1990` etc.). These come after deployment.
- **Refresh procedure** (annual): SABR releases live in a Box.com folder
  with no scriptable URL. Manually download "Comma-delimited version"
  from sabr.org/lahman-database, place the zip in `raw/`, extract
  People/Teams/Appearances CSVs, re-run step 02.
- **Known minor blemish**: ~21 phantom appearance rows linger from an
  early sample-data load. ~0.05% of total; `verify_graph.py` passes.
- **Top-degree starter candidates**: journeyman pitchers + long-tenured
  veterans dominate. Rich Hill (944), Edwin Jackson (871), Jesse Chavez
  (858) lead. The 100–500 teammate band is approachable; 500+ is hard.

## Game runtime

### Engine (`game/engine.py`)
Pure logic, no I/O. Imported by both the CLI and the web server.

- `GameState` — `chain`, `chain_names`, `chain_shared_with_prev`, `strikes`.
- `MoveResult` — `outcome` (`valid`, `unknown_player`, `already_used`,
  `not_teammate`, `blocked_by_burned`), `shared_seasons`, `burned_seasons`,
  `ambiguous_count`, `display_name`, `disambiguation`.
- `validate_and_apply_move(state, conn, raw_input | player_id,
  track_strikes=True)`:
  - Resolves candidate by typed text or by known `player_id`.
  - When `track_strikes=True` (default — used by MP and current BP),
    Rule B applies and strikes accumulate.
  - When `track_strikes=False` (kept available for future modes), no
    strikes accumulate and Rule B is skipped. Current BP no longer uses
    this — BP now uses full strikes per the 2026-05 rule update.
- `seed_game(conn, player_id)` — seeds a fresh state with the leadoff
  player.
- `get_shared_seasons(conn, a_id, b_id)` — shared (team_id, season) list.
  Used by both move validation and Film Review answer-checking.
- `TURN_SECONDS = 30.0`, `STRIKES_TO_BURN = 3`.

### CLI (`game/cli.py`)
Terminal two-player loop, sharing one keyboard. Useful for engine-only
testing. Prints a compact `Strikes:` line each turn. Run:
```
python game/cli.py [--seed PLAYER_ID] [--p1 NAME] [--p2 NAME] [--turn-seconds N]
```

### Web (`web/`)
Flask + vanilla JS, single-page, dark mode.

```
web/server.py             Flask app + /api/* endpoints + in-memory game state
web/templates/index.html  home / mp-setup / shared gameplay / FR screens + rules modal
web/static/main.js        mode router, autocomplete, render functions, countdown
web/static/style.css      dark theme, mode tiles, player cards, connection bars
```

Run: `pip install flask requests`, then `python web/server.py`, then open
http://127.0.0.1:5000/.

#### Screens
- **Home** — three mode tiles (Batting Practice, Film Review, Division
  Rivalry). The header is minimal here.
- **MP setup** — player-name inputs and a Start Game button (with a `←
  Home` back link).
- **Shared gameplay screen** (used by MP and BP) — turn-card with timer +
  autocomplete input on top of a stack of player cards (newest first).
  Side panel: Lineup + Struck Out, both toggleable from header.
- **FR screen** — turn-card with team + year inputs above a 9-card stack.
  Cards in FR omit the Teams career list (giveaway).
- **Game-over banner** appears inline at the top of the gameplay screen
  when the game ends; the Lineup stays visible for review. BP: "Take
  more cuts" + Home. MP: "Let's play two." + Home. FR: a Home button on
  its own summary banner.

#### Header
- **Exit button** (`×`) — abandons the current game and returns to home.
  Visible during any game, hidden on the home screen.
- **Rules `?` button** — opens a mode-aware How to Play modal. Content
  changes based on `currentMode`.
- **Lineup / Struck Out toggle checkboxes** — appear on the shared
  gameplay screen only; control side-panel section visibility.

#### Autocomplete dropdowns
- **Player input** (MP + BP): hits `/api/autocomplete?q=` for up to 4
  popularity-sorted matches on full name, last name, or nickname. Click
  or arrow-then-Enter selects; selection submits with the player's
  `player_id` so the server skips name resolution.
- **FR team input**: hits `/api/fr/team_autocomplete?q=` for up to 6
  matches. The autocomplete list is *consolidated* to one entry per
  franchise (e.g., "Los Angeles Angels", "Miami Marlins", "Expos/Nationals")
  with aliases mapped server-side so old/new names both match.

#### API endpoints (JSON)
| Endpoint | Method | Body / query | Returns |
|---|---|---|---|
| `/api/new_game` | POST | `{p1, p2, seed?, turn_seconds?}` | DR state |
| `/api/move` | POST | `{game_id, raw OR player_id}` | DR state + `last_move` |
| `/api/timeout` | POST | `{game_id}` | DR state (finished if server agrees) |
| `/api/bp/new` | POST | `{seed?, turn_seconds?}` | BP state |
| `/api/bp/move` | POST | `{game_id, raw OR player_id}` | BP state + `last_move` |
| `/api/bp/timeout` | POST | `{game_id}` | BP state (finished if server agrees) |
| `/api/fr/new` | POST | `{}` | FR state with first 2 players revealed |
| `/api/fr/guess` | POST | `{game_id, team, year}` | FR state with hit/foul/strike |
| `/api/fr/reveal_answer` | POST | `{game_id}` | per-pair shared seasons (post-game only) |
| `/api/autocomplete` | GET | `?q=` | up to 4 player matches |
| `/api/fr/team_autocomplete` | GET | `?q=` | up to 6 team matches |

### Locked rules
- **Rule B** in MP and BP. FR uses straight (team, year) matching with
  consolidated franchise aliases.
- **30s turn timer** in MP and BP. **3s pre-game countdown** in BP and DR.
- **No-repeat** within a Lineup (the seed counts as used).
- **Default leadoff**: Anthony Rizzo (`rizzoan01`) for BP and DR. FR uses
  today's puzzle (currently hardcoded to one puzzle, rotates by date once
  more land).
- **Disambiguation**: player autocomplete lets users pick a specific
  `player_id`; raw text submits auto-pick the highest-`career_games` match.

### Today's Film Review puzzle (hardcoded)
9-player chain with 8 distinct team-year links:
`Albert Pujols → Torii Hunter → Miguel Cabrera → Juan Pierre → Jimmy Rollins
→ Adrian Gonzalez → David Ortiz → A. J. Pierzynski → Adrian Beltre`. Links
are LAA 2012, DET 2014, MIA 2005, PHI 2012, LAD 2015, BOS 2012, MIN 2002,
TEX 2013.

### Out of scope today (next up, roughly)
- **Hosting**: Vercel for the frontend, Supabase Postgres + auth for state.
  Accounts are active; deployment work is the immediate next step.
- **Persistence**: in-memory only today. The Supabase migration adds
  durable game state, accounts, leaderboards.
- **Daily BP starter rotation + FR puzzle pool** (currently single hardcoded
  values).
- **Powerups, Walkoffs (win conditions), ELO, leaderboards** — all in the
  roadmap (`teammatetag_roadmap.md` memory), gated behind hosting + accounts.
- **2026 in-season data and pre-2000 expansion** — deferred until post-launch.

## Tech stack
- **Database**: SQLite for dev → **Supabase Postgres** in prod (account active).
- **Realtime / auth**: **Supabase Auth + Realtime** (account active).
- **Frontend hosting**: **Vercel** (account active).
- **Daily in-season updates**: GitHub Actions or Supabase scheduled
  function running `etl/05_update_current_season.py`.

## Working style notes

The owner is learning to code as the project goes. Walk through reasoning
before running scripts. Pause for confirmation before downloads or
destructive operations. When showing data, prefer formatted tables over
raw SQL output. Explain decisions in plain language alongside the code.
Never use em dashes in user-facing text or in this project's strings.

## Open decisions (parking lot)

- Daily BP starter: globally identical (Wordle-style), or seeded per-region
  / ELO bracket?
- Powerup balance (counts, magnitudes) — see roadmap doc.
- Soft-launch venue (r/baseball, baseball Discords, Product Hunt).
- Leaderboard scoring: longest chain, fastest win, hybrid?
- FR puzzle generation: hand-curated vs. auto-generated from the graph
  (and how strict to be about the "exactly one shared team-season per
  pair" constraint).

## Invariants to preserve when coding

- Player IDs use Lahman format (`jeterde01`). All cross-references go
  through this.
- The `teammates` table enforces `player_a_id < player_b_id` — always
  sort the pair before lookup or insert.
- Season-level teammate proxy is intentional; don't "fix" with game-log
  data unless explicitly requested.
- Server-authoritative validation. Never trust the client. Once we're
  on Supabase, validation lives in a Postgres function or edge function.
- Display the **season-specific** team name (`teams.name`), not the
  franchise's current name (`franchises.name`). The Expos/Nationals
  bug surfaced this in 2026-05.
- Never use em dashes in any string the user will read.
