-- base2nerdle schema
-- Works on SQLite (dev) and Postgres (prod, via Supabase) with minimal changes.
-- Differences flagged with -- PG: comments where the Postgres version differs.

-- ============================================================
-- CORE DATA: PLAYERS, TEAMS, APPEARANCES
-- These mirror Lahman's structure but with cleaner names and indexes
-- tuned for our query patterns.
-- ============================================================

-- Franchises: stable identity across team relocations/renames.
-- e.g., MTL Expos and WSN Nationals share a franchise_id.
CREATE TABLE IF NOT EXISTS franchises (
    franchise_id  TEXT PRIMARY KEY,    -- Lahman's franchID, e.g. 'WSN'
    name          TEXT NOT NULL,        -- e.g., 'Washington Nationals'
    active        INTEGER NOT NULL DEFAULT 1
);

-- Teams: a franchise in a given season. Same franchise across years has
-- different rows (because team_id in Lahman is season-specific for some).
-- Lahman's teamID is stable per-franchise per-era, so we use (team_id, season)
-- as the natural key.
CREATE TABLE IF NOT EXISTS teams (
    team_id       TEXT NOT NULL,        -- Lahman's teamID, e.g. 'NYA'
    season        INTEGER NOT NULL,
    franchise_id  TEXT NOT NULL REFERENCES franchises(franchise_id),
    league        TEXT,                 -- 'AL' / 'NL'
    name          TEXT,                 -- 'New York Yankees'
    PRIMARY KEY (team_id, season)
);

-- Players: one row per person. Uses Lahman's playerID (e.g. 'jeterde01')
-- as primary key for stability. External IDs (bbref, mlbam) are kept
-- for cross-referencing with statsapi.mlb.com and Baseball Reference.
CREATE TABLE IF NOT EXISTS players (
    player_id      TEXT PRIMARY KEY,    -- Lahman playerID, e.g. 'jeterde01'
    bbref_id       TEXT,                -- Baseball Reference ID
    retro_id       TEXT,                -- Retrosheet ID
    mlbam_id       INTEGER,             -- MLB Advanced Media ID (used by statsapi)
    name_first     TEXT,
    name_last      TEXT,
    name_given     TEXT,                -- full given name for disambiguation UI
    birth_year     INTEGER,
    debut_year     INTEGER,
    final_year     INTEGER,
    bats           TEXT,                -- 'L' / 'R' / 'B'
    throws         TEXT,                -- 'L' / 'R'
    primary_pos    TEXT,                -- derived from appearances
    name_nick      TEXT                 -- nickname(s), comma-separated; populated by 04
);

CREATE INDEX IF NOT EXISTS idx_players_mlbam     ON players(mlbam_id);
CREATE INDEX IF NOT EXISTS idx_players_lastname  ON players(name_last);
CREATE INDEX IF NOT EXISTS idx_players_bbref     ON players(bbref_id);

-- Appearances: one row per (player, team, season). The atomic fact.
-- games_total > 0 means they appeared in at least one game.
-- Source of truth for teammate derivation.
CREATE TABLE IF NOT EXISTS appearances (
    player_id     TEXT NOT NULL REFERENCES players(player_id),
    team_id       TEXT NOT NULL,
    season        INTEGER NOT NULL,
    games_total   INTEGER NOT NULL DEFAULT 0,
    games_pitched INTEGER NOT NULL DEFAULT 0,
    games_batted  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (player_id, team_id, season),
    FOREIGN KEY (team_id, season) REFERENCES teams(team_id, season)
);

CREATE INDEX IF NOT EXISTS idx_appearances_team_season ON appearances(team_id, season);
CREATE INDEX IF NOT EXISTS idx_appearances_season      ON appearances(season);

-- ============================================================
-- THE TEAMMATE GRAPH (DERIVED)
-- This is what the game queries on every move.
-- Rebuilt by etl/03_build_teammates.py from the appearances table.
-- ============================================================

-- Each row represents: player_a and player_b were teammates
-- (both appeared in >= 1 game) on team_id during season.
-- INVARIANT: player_a_id < player_b_id (lexicographic), so each pair
-- has one canonical row per shared (team, season). This halves storage
-- and makes lookups symmetric.
CREATE TABLE IF NOT EXISTS teammates (
    player_a_id   TEXT NOT NULL REFERENCES players(player_id),
    player_b_id   TEXT NOT NULL REFERENCES players(player_id),
    team_id       TEXT NOT NULL,
    season        INTEGER NOT NULL,
    PRIMARY KEY (player_a_id, player_b_id, team_id, season),
    CHECK (player_a_id < player_b_id)
);

-- Critical for game-time lookups: "are these two players ever teammates?"
-- becomes a covered index lookup.
CREATE INDEX IF NOT EXISTS idx_teammates_a_b   ON teammates(player_a_id, player_b_id);
CREATE INDEX IF NOT EXISTS idx_teammates_b_a   ON teammates(player_b_id, player_a_id);

-- For "list all teammates of player X" queries (used in autocomplete hints,
-- difficulty scoring, etc.).
CREATE INDEX IF NOT EXISTS idx_teammates_a     ON teammates(player_a_id);
CREATE INDEX IF NOT EXISTS idx_teammates_b     ON teammates(player_b_id);

-- ============================================================
-- AUTOCOMPLETE / SEARCH SUPPORT
-- Players the game considers "answerable" — meets minimum thresholds
-- so users can't win by naming September call-ups nobody's heard of.
-- Configurable filter, regenerated whenever appearances change.
-- ============================================================
CREATE TABLE IF NOT EXISTS players_searchable (
    player_id        TEXT PRIMARY KEY REFERENCES players(player_id),
    display_name     TEXT NOT NULL,
    disambiguation   TEXT NOT NULL,    -- 'P, 1995-2007' etc.
    search_key       TEXT NOT NULL,    -- "first last" lowercased
    last_key         TEXT NOT NULL,    -- last name lowercased — for partial-name autocomplete
    career_games     INTEGER NOT NULL,
    teammate_count   INTEGER NOT NULL  -- degree in graph; useful for difficulty
);

CREATE INDEX IF NOT EXISTS idx_searchable_key      ON players_searchable(search_key);
CREATE INDEX IF NOT EXISTS idx_searchable_last_key ON players_searchable(last_key);

-- ============================================================
-- MULTIPLAYER STATE
-- Game sessions, moves, participants. Server-authoritative.
-- For the static graph above we could ship JSON; for these tables
-- we MUST have a real database.
-- ============================================================

-- Auth user. In Supabase, this is auth.users; we keep our own row for
-- gameplay-specific fields (display name, ELO, etc.) and join.
CREATE TABLE IF NOT EXISTS users (
    user_id       TEXT PRIMARY KEY,    -- PG: UUID; SQLite: TEXT
    display_name  TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- PG: created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    elo           INTEGER NOT NULL DEFAULT 1200
);

-- A game session. Status transitions: waiting -> active -> finished.
-- Mode determines the rule pack (no_repeats, year_constrained, etc.).
CREATE TABLE IF NOT EXISTS games (
    game_id       TEXT PRIMARY KEY,
    mode          TEXT NOT NULL,        -- 'endless', 'h2h', 'daily', etc.
    status        TEXT NOT NULL,        -- 'waiting', 'active', 'finished'
    rule_set      TEXT NOT NULL,        -- JSON; see docs/rule_sets.md
    -- PG: rule_set JSONB NOT NULL
    seed_player_id TEXT REFERENCES players(player_id),  -- starting player
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- PG: TIMESTAMPTZ NOT NULL DEFAULT now()
    started_at    TEXT,
    finished_at   TEXT,
    winner_user_id TEXT REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_games_status ON games(status);

-- Players (humans) in a game. A game has 1-N participants depending on mode.
CREATE TABLE IF NOT EXISTS game_participants (
    game_id       TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
    user_id       TEXT NOT NULL REFERENCES users(user_id),
    seat_index    INTEGER NOT NULL,     -- turn order
    is_eliminated INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (game_id, user_id)
);

-- Every move. The whole chain is reconstructible by selecting from this table
-- ordered by turn_number. Used for replay, share-your-chain, anti-cheat.
CREATE TABLE IF NOT EXISTS game_moves (
    game_id           TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
    turn_number       INTEGER NOT NULL,
    user_id           TEXT NOT NULL REFERENCES users(user_id),
    guessed_player_id TEXT REFERENCES players(player_id),  -- NULL if invalid name
    raw_input         TEXT NOT NULL,    -- exactly what they typed
    is_valid          INTEGER NOT NULL, -- did this extend the chain?
    invalid_reason    TEXT,             -- 'not_teammate', 'already_used', 'unknown_player'
    elapsed_ms        INTEGER,          -- for anti-cheat / pacing analysis
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (game_id, turn_number)
);

CREATE INDEX IF NOT EXISTS idx_moves_user ON game_moves(user_id);

-- ============================================================
-- USER-REPORTED ERRORS
-- Critical from day one. Players will find data errors (Lahman has them).
-- ============================================================
CREATE TABLE IF NOT EXISTS connection_reports (
    report_id        INTEGER PRIMARY KEY,    -- PG: BIGSERIAL
    reporter_user_id TEXT REFERENCES users(user_id),
    player_a_id      TEXT NOT NULL,
    player_b_id      TEXT NOT NULL,
    claim            TEXT NOT NULL,          -- 'should_be_teammates' or 'should_not_be_teammates'
    note             TEXT,
    status           TEXT NOT NULL DEFAULT 'open',  -- 'open' / 'reviewed' / 'fixed' / 'rejected'
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- DATA PROVENANCE
-- Tracks when data was last updated, from which source.
-- Lets us know whether a re-run is needed and which years are stale.
-- ============================================================
CREATE TABLE IF NOT EXISTS data_provenance (
    source        TEXT NOT NULL,        -- 'lahman_2025', 'statsapi', 'chadwick_register'
    season        INTEGER,              -- NULL for player-level data
    fetched_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    row_count     INTEGER,
    PRIMARY KEY (source, season)
);
