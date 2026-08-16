-- Teammate Tag - Postgres schema (production target).
-- Mirrors db/schema.sql (SQLite, dev) with Postgres-native types,
-- and adds the game state tables that replace the in-memory dicts
-- in web/server.py (BP_GAMES, GAMES, FR_GAMES).
--
-- Run on a fresh Supabase project via scripts/migrate_to_postgres.py.

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

-- ============================================================
-- STATIC DATA
-- Loaded once from the SQLite snapshot by the migration script.
-- ============================================================

CREATE TABLE IF NOT EXISTS franchises (
    franchise_id  TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    active        BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS teams (
    team_id       TEXT NOT NULL,
    season        INTEGER NOT NULL,
    franchise_id  TEXT NOT NULL REFERENCES franchises(franchise_id),
    league        TEXT,
    name          TEXT,
    PRIMARY KEY (team_id, season)
);

CREATE TABLE IF NOT EXISTS players (
    player_id    TEXT PRIMARY KEY,
    bbref_id     TEXT,
    retro_id     TEXT,
    mlbam_id     INTEGER,
    name_first   TEXT,
    name_last    TEXT,
    name_given   TEXT,
    birth_year   INTEGER,
    debut_year   INTEGER,
    final_year   INTEGER,
    bats         TEXT,
    throws       TEXT,
    primary_pos  TEXT,
    name_nick    TEXT
);

CREATE INDEX IF NOT EXISTS idx_players_mlbam    ON players(mlbam_id);
CREATE INDEX IF NOT EXISTS idx_players_lastname ON players(name_last);
CREATE INDEX IF NOT EXISTS idx_players_bbref    ON players(bbref_id);

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
CREATE INDEX IF NOT EXISTS idx_appearances_player      ON appearances(player_id);

CREATE TABLE IF NOT EXISTS player_stints (
    player_id   TEXT NOT NULL REFERENCES players(player_id),
    team_id     TEXT NOT NULL,
    season      INTEGER NOT NULL,
    first_unit  INTEGER NOT NULL,
    last_unit   INTEGER NOT NULL,
    first_label TEXT,
    last_label  TEXT,
    source      TEXT,
    PRIMARY KEY (player_id, team_id, season),
    FOREIGN KEY (team_id, season) REFERENCES teams(team_id, season)
);

CREATE INDEX IF NOT EXISTS idx_player_stints_link
    ON player_stints(team_id, season, player_id);

CREATE TABLE IF NOT EXISTS teammate_stint_coverage (
    season        INTEGER PRIMARY KEY,
    coverage_type TEXT NOT NULL,
    strict        INTEGER NOT NULL DEFAULT 1,
    source        TEXT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS teammate_exclusions (
    player_a_id TEXT NOT NULL REFERENCES players(player_id),
    player_b_id TEXT NOT NULL REFERENCES players(player_id),
    team_id     TEXT NOT NULL,
    season      INTEGER NOT NULL,
    reason      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (player_a_id, player_b_id, team_id, season)
);

-- Teammate links are derived from the two indexed appearance rows at query
-- time. Do not materialize every player pair: the old table consumed roughly
-- 400 MB by itself and is unnecessary for the runtime query path.

CREATE TABLE IF NOT EXISTS players_searchable (
    player_id       TEXT PRIMARY KEY REFERENCES players(player_id),
    display_name    TEXT NOT NULL,
    disambiguation  TEXT NOT NULL,
    search_key      TEXT NOT NULL,
    last_key        TEXT NOT NULL,
    career_games    INTEGER NOT NULL,
    teammate_count  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_searchable_key      ON players_searchable(search_key);
CREATE INDEX IF NOT EXISTS idx_searchable_last_key ON players_searchable(last_key);

CREATE TABLE IF NOT EXISTS nickname_search (
    nickname_key TEXT NOT NULL,
    player_id    TEXT NOT NULL REFERENCES players(player_id),
    PRIMARY KEY (nickname_key, player_id)
);
CREATE INDEX IF NOT EXISTS idx_nickname_key ON nickname_search(nickname_key);

CREATE TABLE IF NOT EXISTS player_nicknames (
    player_id TEXT NOT NULL REFERENCES players(player_id),
    nickname  TEXT NOT NULL,
    PRIMARY KEY (player_id, nickname)
);

-- season is nullable (e.g. 'lahman_people' has no season concept), so we
-- use a synthetic surrogate key. The UNIQUE constraint with NULLS NOT
-- DISTINCT (Postgres 15+) preserves the SQLite "one row per source+season"
-- semantic the original PRIMARY KEY (source, season) was after.
CREATE TABLE IF NOT EXISTS data_provenance (
    id         BIGSERIAL PRIMARY KEY,
    source     TEXT NOT NULL,
    season     INTEGER,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    row_count  INTEGER,
    UNIQUE NULLS NOT DISTINCT (source, season)
);

-- ============================================================
-- USER-REPORTED ERRORS
-- ============================================================

CREATE TABLE IF NOT EXISTS connection_reports (
    report_id        BIGSERIAL PRIMARY KEY,
    reporter_user_id UUID,
    player_a_id      TEXT NOT NULL,
    player_b_id      TEXT NOT NULL,
    claim            TEXT NOT NULL,
    note             TEXT,
    status           TEXT NOT NULL DEFAULT 'open',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- ACCOUNTS
-- `users` mirrors Supabase auth.users; `guests` covers anonymous play.
-- Game-state tables reference one of the two as the owner.
-- ============================================================

CREATE TABLE IF NOT EXISTS users (
    user_id      UUID PRIMARY KEY,                      -- mirrors auth.users.id
    display_name TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    elo          INTEGER NOT NULL DEFAULT 1200
);

CREATE TABLE IF NOT EXISTS guests (
    guest_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- ACTIVE GAME STATE (server-authoritative)
-- One row per in-flight game. JSONB holds the GameState contents
-- so we don't need a wide normalized schema for chain/strikes.
-- ============================================================

-- Each in-flight game stores its complete state as a single JSONB blob.
-- This keeps load/save in the server code to a single SELECT and UPDATE,
-- with no column-by-column serialization. `finished` is mirrored as a
-- real column so the cleanup job can index on it without parsing JSON.
-- Once we add leaderboards/history, extract fields out of `state` into
-- dedicated tables (e.g., bp_runs) at game-end time.

CREATE TABLE IF NOT EXISTS bp_games (
    game_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    state      JSONB NOT NULL,
    finished   BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_bp_games_active  ON bp_games(created_at DESC) WHERE NOT finished;
CREATE INDEX IF NOT EXISTS idx_bp_games_created ON bp_games(created_at DESC);

CREATE TABLE IF NOT EXISTS dr_games (
    game_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    state      JSONB NOT NULL,
    finished   BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_dr_games_active  ON dr_games(created_at DESC) WHERE NOT finished;
CREATE INDEX IF NOT EXISTS idx_dr_games_created ON dr_games(created_at DESC);

CREATE TABLE IF NOT EXISTS fr_games (
    game_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    state      JSONB NOT NULL,
    finished   BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_fr_games_active  ON fr_games(created_at DESC) WHERE NOT finished;
CREATE INDEX IF NOT EXISTS idx_fr_games_created ON fr_games(created_at DESC);

-- ============================================================
-- LEADERBOARDS (filled in as games finish; future expansion)
-- ============================================================

-- Recorded BP runs for the longest-lineup leaderboard.
CREATE TABLE IF NOT EXISTS bp_runs (
    run_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id   UUID REFERENCES users(user_id) ON DELETE SET NULL,
    owner_guest_id  UUID REFERENCES guests(guest_id) ON DELETE SET NULL,
    seed_player_id  TEXT NOT NULL,
    chain_length    INTEGER NOT NULL,
    finished_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_bp_runs_length   ON bp_runs(chain_length DESC);
CREATE INDEX IF NOT EXISTS idx_bp_runs_finished ON bp_runs(finished_at DESC);
