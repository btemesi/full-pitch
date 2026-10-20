"""The server holds the live clock: no endpoint may reveal what happens after it."""
import pytest
from fastapi.testclient import TestClient

from fullpitch.server import MATCH_DIR, app
from fullpitch.store import load


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def new_session(c, minute):
    return c.post("/api/session", params={"from": minute}).json()["session"]


def goal_minutes():
    s = load(MATCH_DIR)
    return [float(s.md.tmin[e["frame"]]) for e in s.md.events if e["type"] == "goal"]


@pytest.mark.parametrize("at", [5.0, 30.0, 60.0, 85.0])
def test_no_endpoint_reveals_the_future(client, at):
    sid = new_session(client, at)
    seen = len([m for m in goal_minutes() if m <= at])
    cards = client.get("/api/cards", params={"session": sid}).json()
    assert all(c["minute"] <= at + 1e-6 for c in cards)
    state = client.get("/api/state", params={"session": sid}).json()
    assert sum(state["score"].values()) == seen
    catchup = client.get("/api/catchup", params={"session": sid, "away": 120}).json()
    assert len(catchup["facts"]["goals"]) == seen


def test_client_cannot_move_the_clock_through_query_params(client):
    sid = new_session(client, 20)
    cards = client.get("/api/cards", params={"session": sid, "at": 95, "to": 95}).json()
    assert all(c["minute"] <= 20 for c in cards)


def test_seek_moves_the_clock(client):
    sid = new_session(client, 10)
    client.post(f"/api/session/{sid}/seek", params={"to": 70})
    assert abs(client.get("/api/clock", params={"session": sid}).json()["minute"] - 70) < 1e-6


@pytest.mark.parametrize("path,params,code", [
    ("/api/stream", {"speed": 0}, 422),
    ("/api/stream", {"speed": 1000}, 422),
    ("/api/cards", {"lang": "de"}, 422),
    ("/api/cards", {"persona": "coach"}, 422),
    ("/api/catchup", {"away": 0}, 422),
    ("/api/player", {"id": "XXX-99"}, 404),
])
def test_bad_input_is_rejected(client, path, params, code):
    sid = new_session(client, 30)
    assert client.get(path, params={"session": sid, **params}).status_code == code


def test_unknown_session(client):
    assert client.get("/api/state", params={"session": "nope"}).status_code == 404


@pytest.mark.parametrize("at", [20.0, 50.0, 80.0])
def test_deeper_endpoints_stay_in_the_past(client, at):
    sid = new_session(client, at)
    seen = len([m for m in goal_minutes() if m <= at])
    st = client.get("/api/stats", params={"session": sid}).json()
    assert sum(t["goals"] for t in st["teams"].values()) == seen
    assert len(st["xg"]["goals"]) == seen and st["xg"]["now"] <= at + 0.01
    for team in ("RIV", "NOR"):
        assert client.get("/api/passnet", params={"session": sid, "team": team}).status_code == 200
    prof = client.get("/api/profile", params={"session": sid, "id": "RIV-10"}).json()
    assert all(m <= at for m in prof["off_ball_runs"])
    future = [m for m in goal_minutes() if m > at]
    if future:
        assert client.get("/api/replay", params={"session": sid, "kind": "goal", "minute": future[0]}).status_code == 409


def test_recaps_only_after_the_whistle(client):
    sid = new_session(client, 30)
    assert client.get("/api/recap", params={"session": sid, "kind": "ht"}).status_code == 409
    client.post(f"/api/session/{sid}/seek", params={"to": 60})
    assert client.get("/api/recap", params={"session": sid, "kind": "ht"}).status_code == 200
    assert client.get("/api/recap", params={"session": sid, "kind": "ft"}).status_code == 409


def test_the_coaches_plan_is_never_sent(client):
    """Formation changes are what we detect; the page only gets the public starting formations."""
    info = client.get("/api/match").json()
    assert "formation_log" not in info and "formations" not in info


def test_replay_shows_the_score_of_its_moment(client):
    sid = new_session(client, 96)
    goals = goal_minutes()
    if len(goals) >= 2:
        r = client.get("/api/replay", params={"session": sid, "kind": "goal", "minute": round(goals[1], 2)}).json()
        assert sum(r["score"]) == 1  # the clip starts before the second goal
