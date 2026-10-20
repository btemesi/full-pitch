from fullpitch.templates import catch_up_text, change_card, hu_article

NAMES = {"RIV": "Riverside Athletic", "NOR": "Northgate FC"}


def test_hungarian_article():
    assert hu_article("Northgate FC") == "A"
    assert hu_article("Arsenal") == "Az"
    assert hu_article("Újpest") == "Az"


def test_back_three_headline_beats_line_height():
    change = {"team": "NOR", "since": "56'", "signals": [
        {"what": "defensive line height without the ball", "signal": "line_adj", "phase": "def", "before": 22, "after": 31},
        {"what": "players in the back line in possession", "signal": "n_back", "phase": "att", "before": 2.4, "after": 3.1},
    ]}
    en = change_card(change, NAMES, "casual", "en")
    assert en["title"] == "Northgate FC have switched to a back three"
    hu = change_card(change, NAMES, "casual", "hu")
    assert hu["title"].startswith("A Northgate FC háromvédős")


def test_small_back_line_wobble_is_not_called_a_formation_change():
    change = {"team": "NOR", "since": "31'", "signals": [
        {"what": "players in the back line in possession", "signal": "n_back", "phase": "att", "before": 2.6, "after": 2.9},
        {"what": "defensive line height without the ball", "signal": "line_adj", "phase": "def", "before": 31, "after": 21},
    ]}
    assert change_card(change, NAMES)["title"] == "Northgate FC have dropped deeper"


def test_hungarian_decimal_comma_and_team_names_in_shots_line():
    facts = {"teams": NAMES, "window": {"from": "50'", "to": "60'"}, "score_before": {"RIV": 0, "NOR": 0},
             "score_now": {"RIV": 0, "NOR": 0}, "goals": [], "big_chances": [], "cards": [], "substitutions": [],
             "shape_changes": [], "stats": {"RIV": {"possession_pct": 55, "shots": 2, "xg": 0.31},
                                            "NOR": {"possession_pct": 45, "shots": 1, "xg": 0.05}}}
    lines = catch_up_text(facts, "analyst", "hu")["lines"]
    assert any("RIV 2, NOR 1" in x and "0,31" in x for x in lines)
