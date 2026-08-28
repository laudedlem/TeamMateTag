import sqlite3

from game.engine import get_shared_seasons


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE sport_appearances (
            sport_id TEXT,
            player_id TEXT,
            team_id TEXT,
            season INTEGER,
            games_total INTEGER
        );
        CREATE TABLE sport_teammate_exclusions (
            sport_id TEXT,
            player_a_id TEXT,
            player_b_id TEXT,
            team_id TEXT,
            season INTEGER,
            reason TEXT
        );
        CREATE TABLE sport_player_stints (
            sport_id TEXT,
            player_id TEXT,
            team_id TEXT,
            season INTEGER,
            first_unit INTEGER,
            last_unit INTEGER,
            first_label TEXT,
            last_label TEXT,
            source TEXT
        );
        CREATE TABLE sport_teammate_stint_coverage (
            sport_id TEXT,
            season INTEGER,
            coverage_type TEXT,
            strict INTEGER,
            source TEXT
        );
        CREATE TABLE sport_teammates (
            sport_id TEXT,
            player_a_id TEXT,
            player_b_id TEXT,
            team_id TEXT,
            season INTEGER
        );
        CREATE TABLE sport_live_player_games (
            sport_id TEXT,
            game_id TEXT,
            game_date TEXT,
            season INTEGER,
            player_id TEXT,
            team_id TEXT,
            position TEXT,
            games_total INTEGER
        );
        CREATE TABLE sport_players (
            sport_id TEXT,
            player_id TEXT,
            debut_year INTEGER
        );
        """
    )
    return conn


def test_hockey_game_boxscore_coverage_requires_proof_row():
    conn = make_conn()
    conn.executemany(
        "INSERT INTO sport_appearances VALUES ('hockey', ?, 'LAK', 2023, 80)",
        [("nhl:current",), ("nhl:loose_only",), ("nhl:proofed",)],
    )
    conn.execute(
        "INSERT INTO sport_teammate_stint_coverage VALUES ('hockey', 2023, 'game_boxscore', 1, 'test')"
    )
    conn.execute(
        "INSERT INTO sport_teammates VALUES ('hockey', 'nhl:current', 'nhl:proofed', 'LAK', 2023)"
    )

    assert get_shared_seasons(conn, "nhl:current", "nhl:proofed", sport="hockey") == [("LAK", 2023)]
    assert get_shared_seasons(conn, "nhl:current", "nhl:loose_only", sport="hockey") == []


def test_hockey_without_game_boxscore_coverage_falls_back_to_appearances():
    conn = make_conn()
    conn.executemany(
        "INSERT INTO sport_appearances VALUES ('hockey', ?, 'LAK', 2023, 80)",
        [("nhl:current",), ("nhl:loose_only",)],
    )

    assert get_shared_seasons(conn, "nhl:current", "nhl:loose_only", sport="hockey") == [("LAK", 2023)]


def test_basketball_game_boxscore_coverage_requires_proof_row():
    conn = make_conn()
    conn.executemany(
        "INSERT INTO sport_appearances VALUES ('basketball', ?, '1610612747', 2025, 80)",
        [("nba:one",), ("nba:loose_only",), ("nba:proofed",)],
    )
    conn.execute(
        "INSERT INTO sport_teammate_stint_coverage VALUES ('basketball', 2025, 'game_boxscore', 1, 'test')"
    )
    conn.execute(
        "INSERT INTO sport_teammates VALUES ('basketball', 'nba:one', 'nba:proofed', '1610612747', 2025)"
    )

    assert get_shared_seasons(conn, "nba:one", "nba:proofed", sport="basketball") == [("1610612747", 2025)]
    assert get_shared_seasons(conn, "nba:one", "nba:loose_only", sport="basketball") == []


def test_football_partial_game_boxscore_coverage_uses_game_rows_and_legacy_fallback():
    conn = make_conn()
    conn.executemany(
        "INSERT INTO sport_appearances VALUES ('football', ?, 'KC', ?, 17)",
        [
            ("nfl:mahomes", 2011),
            ("nfl:kelce", 2011),
            ("nfl:loose_only", 2024),
            ("nfl:proofed", 2024),
            ("nfl:mahomes", 2024),
        ],
    )
    conn.executemany(
        "INSERT INTO sport_player_stints VALUES ('football', ?, 'KC', 2011, 1, 17, '1', '17', 'test')",
        [("nfl:mahomes",), ("nfl:kelce",)],
    )
    conn.execute(
        "INSERT INTO sport_teammate_stint_coverage VALUES ('football', 2011, 'stint_range', 1, 'test')"
    )
    conn.execute(
        "INSERT INTO sport_teammate_stint_coverage VALUES ('football', 2024, 'game_boxscore', 1, 'test')"
    )
    conn.executemany(
        "INSERT INTO sport_live_player_games VALUES ('football', '2024_01_BAL_KC', '2024-09-05', 2024, ?, 'KC', 'QB', 1)",
        [("nfl:mahomes",), ("nfl:proofed",)],
    )

    assert get_shared_seasons(conn, "nfl:mahomes", "nfl:kelce", sport="football") == [("KC", 2011)]
    assert get_shared_seasons(conn, "nfl:mahomes", "nfl:proofed", sport="football") == [("KC", 2024)]
    assert get_shared_seasons(conn, "nfl:mahomes", "nfl:loose_only", sport="football") == []
