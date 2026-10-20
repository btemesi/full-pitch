"""Invariants the synthetic match must always respect."""
from collections import Counter

import numpy as np

from fullpitch.engine import L, W


def test_score_matches_goals_and_shots(match):
    goals = [e for e in match.events if e["type"] == "goal"]
    shorts = [t["short"] for t in match.sc["teams"]]
    assert match.score == [sum(g["team"] == s for g in goals) for s in shorts]
    assert sum(e["type"] == "shot" and e["outcome"] == "goal" for e in match.events) == len(goals)


def test_events_in_time_order(match):
    frames = [e["frame"] for e in match.events]
    assert frames == sorted(frames)


def test_players_stay_on_or_beside_the_pitch(match):
    xy = np.stack(match.frames)[:, :22]
    on = (xy[..., 0] >= -1.01) & (xy[..., 0] <= L + 1.01) & (xy[..., 1] >= -1.01) & (xy[..., 1] <= W + 1.01)
    parked = (np.abs(xy[..., 0] - L / 2) < 0.01) & ((np.abs(xy[..., 1] + 4) < 0.01) | (np.abs(xy[..., 1] - W - 4) < 0.01))
    assert np.all(on | parked)


def test_ball_never_faster_than_a_shot(match):
    ball = np.stack(match.frames)[:, 22]
    speed = np.linalg.norm(np.diff(ball, axis=0), axis=1) * 5
    same_half = np.diff(np.array(match.frame_period)) == 0  # ends change at half-time
    assert np.all(speed[same_half] <= 36)


def test_substitution_and_card_rules(match):
    subs = Counter(e["team"] for e in match.events if e["type"] == "substitution")
    assert all(n <= 5 for n in subs.values())
    yellows = Counter(e["player"] for e in match.events if e["type"] == "card" and e["card"] == "yellow")
    reds = {e["player"] for e in match.events if e["type"] == "card" and e["card"] == "red"}
    assert all(n < 2 or pid in reds for pid, n in yellows.items())


def test_subs_only_at_stoppages(match):
    """A substitution is made while the ball is dead."""
    poss = np.array(match.frame_poss)
    for e in match.events:
        if e["type"] == "substitution":
            assert poss[min(e["frame"], len(poss) - 1)] == -1


def test_teams_change_ends(match):
    """The home keeper defends x~0 in the first half and x~105 in the second."""
    xy = np.stack(match.frames)
    period = np.array(match.frame_period)
    gk = xy[:, 0, 0]
    assert gk[period == 1].mean() < L / 2 < gk[period == 2].mean()


def test_tactical_changes_are_not_in_the_stream(match):
    assert not any(e["type"] in ("tactical", "formation") for e in match.events)
    assert len(match.applied) == 3
