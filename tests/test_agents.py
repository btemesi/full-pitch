"""The deterministic guards around the agents, and the agent endpoints: no model is called here."""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import fullpitch.server as server
from fullpitch.agents import (AgentTeam, editor_verdict, expand_codes, fact_check, fact_minutes, polish,
                              unknown_names, wrong_language, wrong_minutes)

NAMES = {"Riverside", "Athletic", "Northgate", "FC", "RIV", "NOR", "Umar", "Dorsey", "Emil", "Holmgrave", "I", "VAR", "xG"}
TEAMS = {"RIV": "Riverside Athletic", "NOR": "Northgate FC"}


def test_fact_check_flags_numbers_not_in_the_data():
    assert fact_check("The line dropped from 30.5m to 24.4m", [30.5, 24.4]) == []
    assert fact_check("The line dropped from 30.5m to 21.0m", [30.5, 24.4]) == ["21.0"]
    assert fact_check("A vonal 30,5 méterről 24,4 méterre esett", [30.5, 24.4]) == []
    # small numbers are checked too: "over 10m" when the rise was 8.9 m
    assert fact_check("higher by over 10m", [8.9, 10.0]) == []
    assert fact_check("higher by over 10m", [8.9]) == ["10"]


def test_minutes_must_be_clocks_of_the_moment():
    facts = {"since": "67'", "window": {"from": "55'", "to": "74'"}, "goals": [{"minute": "90+5'"}]}
    mins = fact_minutes(facts)
    assert wrong_minutes("Since 67', a goal in the 90+5' minute", mins) == []
    assert wrong_minutes("Since the 70' shape change", mins) == ["70"]
    assert wrong_minutes("in the 70th minute", mins) == ["70"]
    assert wrong_minutes("a 70. percben", mins) == ["70"]


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
    assert polish("kétszer is betalált -, de a végén", "hu", NAMES) == "kétszer is betalált, de a végén"


@pytest.mark.parametrize("text,fixed", [
    ("a 53. percben és a 15. percben", "az 53. percben és a 15. percben"),
    ("az 47. percben, Az 35. perctől", "a 47. percben, A 35. perctől"),
    ("az 5. percben, a 1. gól, a 55%", "az 5. percben, az 1. gól, az 55%"),
    ("Emil Holmgrave 90+5. percben szerzett gólja", "Emil Holmgrave 90+5. percben szerzett gólja"),
    ("Holmgrave az 90+5. percben", "Holmgrave a 90+5. percben"),
    ("Varnell az a 88. percben", "Varnell a 88. percben"),
    ("A mérkőzés 53. percében", "A mérkőzés 53. percében"),
])
def test_polish_hungarian_articles(text, fixed):
    assert polish(text, "hu", NAMES) == fixed


def test_team_codes_become_names():
    assert expand_codes("RIV dominate, NOR-nek nehéz", TEAMS) == "Riverside Athletic dominate, Northgate FC-nek nehéz"
    assert expand_codes("player RIV-10", TEAMS) == "player RIV-10"


def test_editor_verdict():
    assert editor_verdict('{"ok": true, "problems": []}') == []
    assert editor_verdict('{"ok": false, "problems": ["wrong minute"]}') == ["wrong minute"]
    assert editor_verdict('{"ok": true, "claims": [{"claim": "two misses", "supported": false}]}') == ["unsupported: two misses"]
    assert editor_verdict("not json") == []


# ------------------------------------------------------------------ a team without a model behind it
class FakeTeam(AgentTeam):
    """The real orchestration (cache, budget, cooldown) with the model calls replaced."""

    def __init__(self, store, fail=False, budget=60):
        self.store, self.cache_path, self.cache = store, None, {}
        self.pending, self.failed, self.runs = {}, {}, __import__("collections").deque()
        self.max_runs_per_hour, self.jobs = budget, asyncio.Semaphore(2)
        self.fail, self.calls = fail, 0

    async def stop(self):
        pass

    async def narrate(self, card, persona, lang, follow=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("model down")
        key = f"{self.event_key(card)}|{persona}|{lang}|{follow or ''}"
        self.cache[key] = {"title": "Agent title", "body": "Agent body", "source": "agent", "trace": []}
        self.cache[f"{self.event_key(card)}|explain"] = {"facts": {"since": "35'"}}
        return self.cache[key]


@pytest.fixture()
def agent_client():
    with TestClient(server.app) as c:
        yield c
    server.TEAM = None


def _poll_cards(c, sid, **params):
    for _ in range(50):
        cards = c.get("/api/cards", params={"session": sid, **params}).json()
        if not any(x.get("pending") for x in cards):
            return cards
        time.sleep(0.02)
    return cards


def test_trace_never_shows_a_card_from_the_future(agent_client):
    server.TEAM = FakeTeam(server.store())
    s = server.store()
    late_goal = [e for e in s.key_moments(s.length_min) if e["type"] == "goal"][-1]
    key = f"goal:{late_goal['minute']:.2f}"
    server.TEAM.cache[f"{key}|casual|en|"] = {"title": "late goal", "body": "x", "source": "agent", "trace": []}
    early = agent_client.post("/api/session", params={"from": 10}).json()["session"]
    assert agent_client.get("/api/trace", params={"key": key, "session": early}).status_code == 404
    late = agent_client.post("/api/session", params={"from": s.length_min}).json()["session"]
    assert agent_client.get("/api/trace", params={"key": key, "session": late}).status_code == 200
    assert agent_client.get("/api/trace", params={"key": key}).status_code == 422  # a session is required


def test_cards_get_the_agents_text_once_written(agent_client):
    server.TEAM = FakeTeam(server.store())
    sid = agent_client.post("/api/session", params={"from": 60}).json()["session"]
    cards = _poll_cards(agent_client, sid)
    written = [c for c in cards if c.get("agent_key")]
    assert written and all(c["source"] == "agent" and c["title"] == "Agent title" for c in written)


def test_a_failing_model_is_not_retried_on_every_poll(agent_client):
    team = server.TEAM = FakeTeam(server.store(), fail=True)
    sid = agent_client.post("/api/session", params={"from": 60}).json()["session"]
    first = _poll_cards(agent_client, sid)
    calls = team.calls
    for _ in range(5):
        cards = agent_client.get("/api/cards", params={"session": sid}).json()
    assert team.calls == calls  # cooled down: the template stays, no new model calls
    assert not any(c.get("pending") or c.get("agent_key") for c in cards)
    assert all(c["source"] == "template" for c in first)


def test_hourly_budget_caps_live_agent_work(agent_client):
    team = server.TEAM = FakeTeam(server.store(), budget=2)
    sid = agent_client.post("/api/session", params={"from": 96}).json()["session"]
    cards = _poll_cards(agent_client, sid, persona="analyst", lang="hu")
    assert team.calls == 2
    assert sum(1 for c in cards if c.get("agent_key")) == 2  # the rest stays on templates, without a trace link


def test_unknown_followed_player_is_rejected(agent_client):
    server.TEAM = FakeTeam(server.store())
    sid = agent_client.post("/api/session", params={"from": 60}).json()["session"]
    r = agent_client.get("/api/cards", params={"session": sid, "persona": "player", "follow": "XXX-99"})
    assert r.status_code == 404


def test_pressure_needs_a_pressing_measurement():
    from fullpitch.agents import unmeasured_pressure
    line_only = {"signals": [{"signal": "line_adj", "before": 30.5, "after": 24.4}]}
    pressing = {"signals": [{"signal": "pressers", "before": 0.9, "after": 2.1}]}
    assert unmeasured_pressure("Northgate FC invited heavy pressure.", line_only)
    assert unmeasured_pressure("A nyomás ellenére 0-0.", line_only)
    assert not unmeasured_pressure("Riverside Athletic started pressing higher.", pressing)
    assert not unmeasured_pressure("Northgate FC dropped their line deeper.", line_only)


def test_certain_cause_and_name_articles():
    from fullpitch.agents import certain_cause, drop_article_before_players
    assert certain_cause("This change more than tripled their xG from 0.4 to 1.28.")
    assert certain_cause("This shift increased possession (43% to 55%).")
    assert not certain_cause("Riverside Athletic scored after the change.")
    players = ["Umar Dorsey", "Emil Holmgrave"]
    assert drop_article_before_players("akik az Umar Dorsey révén, a Riverside Athletic javára", players) == \
        "akik Umar Dorsey révén, a Riverside Athletic javára"
    assert drop_article_before_players("Az Emil Holmgrave gólja", players) == "Emil Holmgrave gólja"


def test_short_hungarian_answers_are_hungarian():
    assert not wrong_language("Quentin Grievson eddig a legtöbbet futotta, 9,0 km-t.", "hu")
    assert not wrong_language("Quentin Grievson futott a legtöbbet.", "hu")
    assert wrong_language("Quentin Grievson has run the most so far.", "hu")


def test_speech_only_speaks_registered_lines(tmp_path):
    from fullpitch.speech import Speech

    class Cred:
        def get_token(self, scope):
            return type("T", (), {"token": "t", "expires_on": time.time() + 3600})()
    sp = Speech("https://example.cognitiveservices.azure.com", tmp_path, credential=Cred())
    calls = []
    sp._synthesise = lambda text, lang: calls.append((text, lang)) or b"mp3"
    sid = sp.register("Northgate  pull one back.", "en")
    assert asyncio.run(sp.audio(sid)) == b"mp3"
    assert asyncio.run(sp.audio(sid)) == b"mp3"      # second play comes from the disk cache
    assert calls == [("Northgate pull one back.", "en")]
    assert asyncio.run(sp.audio("0123456789abcdef")) is None  # nothing that wasn't handed out


def test_foreign_content_is_caught():
    from fullpitch.agents import foreign_content
    assert foreign_content("Visit https://example.com for more.")
    assert foreign_content("write to fan@club.example")
    assert foreign_content("see www.example.org")
    assert foreign_content("<script>alert(1)</script>")
    assert not foreign_content("Quentin Grievson has run 9.0 km, the most so far.")
    assert not foreign_content("Riverside Athletic lead 2-1 at 70'.")


def test_a_new_line_is_synthesised_once_however_many_ask(tmp_path):
    from fullpitch.speech import Speech
    sp = Speech("https://example.cognitiveservices.azure.com", tmp_path, credential=object())
    calls = []

    def slow(text, lang):
        time.sleep(0.2)
        calls.append(text)
        return b"mp3"
    sp._synthesise = slow
    sid = sp.register("Holmgrave pulls one back.", "en")

    async def three():
        return await asyncio.gather(sp.audio(sid), sp.audio(sid), sp.audio(sid))
    assert asyncio.run(three()) == [b"mp3"] * 3
    assert calls == ["Holmgrave pulls one back."]


class AskTeam(FakeTeam):
    def __init__(self, store, answer):
        super().__init__(store)
        self.answer = answer

    async def ask(self, question, at, lang):
        self.calls += 1
        return self.answer


def test_ask_about_a_moment_replays_only_the_past(agent_client):
    s = server.store()
    goal = [e for e in s.key_moments(80) if e["type"] == "goal"][0]
    server.TEAM = AskTeam(s, {"answer": "Dorsey was 2.9 m from goal.", "source": "agent", "trace": [],
                              "show": {"players": ["RIV-10"], "team": None, "channel": None, "minute": goal["minute"]}})
    sid = agent_client.post("/api/session", params={"from": 80}).json()["session"]
    r = agent_client.get("/api/ask", params={"session": sid, "q": "Where was everyone at the first goal?"}).json()
    assert r["show"]["replay"] and r["show"]["rings"][0]["name"] == "Umar Dorsey"
    last = max(f["m"] for f in r["clip"]["frames"])
    assert last <= 80 and abs(min(f["m"] for f in r["clip"]["frames"]) - goal["minute"]) < 0.2


def test_ask_about_the_present_draws_on_live_play(agent_client):
    s = server.store()
    team = server.TEAM = AskTeam(s, {"answer": "Quentin Grievson has run the most.", "source": "agent", "trace": [],
                                     "show": {"players": ["NOR-08"], "team": "NOR", "channel": "centre", "minute": None}})
    sid = agent_client.post("/api/session", params={"from": 72}).json()["session"]
    r = agent_client.get("/api/ask", params={"session": sid, "q": "Who has run the most?"}).json()
    assert r["show"]["replay"] is False and r["clip"] is None
    assert r["show"]["zone"] == {"team_index": 1, "channel": "centre"} and r["show"]["rings"]
    agent_client.get("/api/ask", params={"session": sid, "q": "who has  run the MOST?"})
    assert team.calls == 1  # the same question in the same minute is answered once


def test_ask_rejects_bad_input(agent_client):
    server.TEAM = AskTeam(server.store(), {"answer": None, "source": "failed", "trace": []})
    sid = agent_client.post("/api/session", params={"from": 30}).json()["session"]
    assert agent_client.get("/api/ask", params={"session": sid, "q": "hi"}).status_code == 422
    assert agent_client.get("/api/ask", params={"session": sid, "q": "x" * 201}).status_code == 422
    r = agent_client.get("/api/ask", params={"session": sid, "q": "Who will win?"}).json()
    assert r["answer"] is None and r["clip"] is None and r["speech"] is None


def test_lines_are_spoken_like_a_commentator():
    from fullpitch.speech import spoken
    assert spoken("Holmgrave scored at 66'. It is 2-1 after an 8-pass move.", "en") == \
        "Holmgrave scored in minute 66. It is 2 1 after an 8-pass move."
    assert spoken("Dorsey makes it 1-0 at 90+5'.", "en") == "Dorsey makes it 1 nil at 90 plus 5."
    assert spoken("A 35' óta 2-1.", "hu") == "A 35. perc óta 2 1."


def test_player_ids_never_reach_the_viewer():
    from fullpitch.agents import ids_to_names
    names = {"RIV-10": "Umar Dorsey", "NOR-02": "Ali Bray", "NOR-03": "Samir Yarrowby"}
    assert ids_to_names("Umar Dorsey (RIV-10) scored.", names) == "Umar Dorsey scored."
    assert ids_to_names("defenders (NOR-02, NOR-03) were close", names) == "defenders (Ali Bray, Samir Yarrowby) were close"
    assert ids_to_names("RIV-10 scored.", names) == "Umar Dorsey scored."


def test_a_striker_is_placed_from_the_goal_he_attacks():
    from fullpitch.agents import far_from_own_goal
    assert far_from_own_goal("Dorsey was 102.2 m from his own goal.") == ["102.2"]
    assert far_from_own_goal("The keeper was 4.7 m from his own goal.") == []


def test_distances_to_a_goal_are_checked_against_the_positions():
    import json as _json
    from fullpitch.agents import positions_in, wrong_distances
    s = server.store()
    pos = positions_in([_json.dumps({"data": s.positions_at_moment("goal-3", 72)})])
    teams = {"RIV": "Riverside Athletic", "NOR": "Northgate FC"}
    assert wrong_distances("Emil Holmgrave (6.2 m from Riverside's goal) scored.", pos, teams)
    assert not wrong_distances("Emil Holmgrave scored 3.7 m from Riverside Athletic's goal.", pos, teams)
    assert not wrong_distances("Cyril Ekwuno was 4.7 m from his own goal.", pos, teams)


def test_nothing_is_decided_before_the_final_whistle():
    from fullpitch.agents import decisive_claim
    assert decisive_claim("Varnell sealed it in the 88th.")
    assert decisive_claim("Varnell a 88. percben lezárta a meccset.")
    assert not decisive_claim("Varnell made it 3-1 in the 88th.")


def test_the_goal_tally_matches_the_goals():
    from fullpitch.agents import wrong_goal_tally
    assert wrong_goal_tally("You missed three goals! Riverside went 3-1 up.", 4) == ["three"]
    assert wrong_goal_tally("You missed a goal!", 1) == []
    assert wrong_goal_tally("Lemaradtál két gólról: Dorsey kétszer is betalált.", 2) == []
    assert wrong_goal_tally("Lemaradtál egy gólról!", 2) == ["egy"]
    assert wrong_goal_tally("Dorsey scored two goals.", 4) == []  # one player's goals are not the tally
