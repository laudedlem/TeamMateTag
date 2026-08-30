from web.server import _sport_card_display_stint_label, _sport_season_label


def test_cross_year_card_stints_use_full_calendar_years():
    assert _sport_card_display_stint_label("hockey", 2018, 2025, {}, "2018-10-08", "2026-04-11") == "2018-2026"
    assert _sport_card_display_stint_label("basketball", 2018, 2025, {}, "2018-10-18", "2026-06-13") == "2018-2026"
    assert _sport_card_display_stint_label("football", 2018, 2025, {}) == "2018-2026"


def test_cross_year_single_team_card_stint_uses_season_end_year():
    assert _sport_card_display_stint_label("hockey", 2025, 2025, {}, "2026-01-10", "2026-04-11") == "2026"
    assert _sport_card_display_stint_label("basketball", 2025, 2025, {}, "2026-02-09", "2026-06-13") == "2026"
    assert _sport_card_display_stint_label("football", 2025, 2025, {}) == "2026"


def test_cross_year_card_stint_can_end_before_january():
    assert _sport_card_display_stint_label("hockey", 2025, 2025, {}, "2025-10-09", "2025-12-20") == "2025"
    assert _sport_card_display_stint_label("basketball", 2025, 2025, {}, "2025-10-24", "2025-12-31") == "2025"


def test_game_season_labels_still_use_league_season_format():
    assert _sport_season_label("hockey", 2025) == "2025-26"
    assert _sport_season_label("basketball", 2025) == "2025-26"
    assert _sport_season_label("football", 2025) == "2025-26"
    assert _sport_season_label("baseball", 2025) == "2025"
