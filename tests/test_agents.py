"""The deterministic guards around the agents: no model is called here."""
from fullpitch.agents import fact_check, polish, unknown_names, wrong_language

NAMES = {"Riverside", "Athletic", "Northgate", "FC", "RIV", "NOR", "Umar", "Dorsey", "Emil", "Holmgrave", "I", "VAR", "xG"}


def test_fact_check_flags_numbers_not_in_the_data():
    assert fact_check("The line dropped from 30.5m to 24.4m", [30.5, 24.4]) == []
    assert fact_check("The line dropped from 30.5m to 21.0m", [30.5, 24.4]) == ["21.0"]
    assert fact_check("A vonal 30,5 méterről 24,4 méterre esett", [30.5, 24.4]) == []


def test_unknown_names_catches_invented_clubs_but_not_case_endings():
    assert unknown_names("Dorsey scored for Riverside Athletic.", NAMES) == []
    assert "Riverstone" in unknown_names("Dorsey scored for Riverstone.", NAMES)
    assert unknown_names("Gól a Northgate-nek, Riverside Athleticnak örülhet.", NAMES) == []
    assert unknown_names("Dorsey's goal won him Player of the Match.", NAMES) == []


def test_wrong_language():
    assert wrong_language("Northgate FC changes tactics at 53'. The team pushed higher.", "hu")
    assert not wrong_language("A Northgate FC az 53. perctől feljebb tolta a védelmét, és többet birtokolja a labdát.", "hu")
    assert not wrong_language("Northgate FC pushed their line higher after 53'.", "en")


def test_polish_house_style():
    assert polish("Riverside Athletic Edge Northgate FC 3–2", "en", NAMES, title=True) == "Riverside Athletic edge Northgate FC 3-2"
    assert polish("Holmgrave's Close-Range Goal", "en", NAMES, title=True) == "Holmgrave's close-range goal"
    assert polish("Feljebb tolták – és ez működött", "hu", NAMES) == "Feljebb tolták, és ez működött"
    assert polish("a 53. percben és a 15. percben", "hu", NAMES) == "az 53. percben és a 15. percben"
