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
