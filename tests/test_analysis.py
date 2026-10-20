import numpy as np

from fullpitch.analysis import count_runs, detect_changes, minute_metrics, player_physicals, team_rel


def test_team_rel_follows_the_change_of_ends():
    p = np.array([10.0, 20.0])
    assert np.allclose(team_rel(p, 0, 1), [10, 20])
    assert np.allclose(team_rel(p, 0, 2), [95, 48])
    assert np.allclose(team_rel(p, 1, 1), [95, 48])
    assert np.allclose(team_rel(p, 1, 2), [10, 20])
    rows = np.array([[10.0, 20.0], [10.0, 20.0]])
    assert np.allclose(team_rel(rows, 0, np.array([1, 2])), [[10, 20], [95, 48]])


def test_sprint_needs_a_full_second():
    assert count_runs(np.array([1, 1, 1, 1, 0, 1, 1, 1, 1, 1], dtype=bool), 5) == 1
    assert count_runs(np.array([1] * 12, dtype=bool), 5) == 1
    assert count_runs(np.array([1, 0] * 10, dtype=bool), 5) == 0


def test_running_numbers_are_human(match_data):
    phys = player_physicals(match_data)
    outfield = [v for pid, v in phys.items() if not pid.endswith("-01") and v["minutes"] > 85]
    assert all(v["top_speed_kmh"] <= 36 for v in outfield)
    assert 7 < np.median([v["distance_m"] / 1000 for v in outfield]) < 13


def test_split_windows_add_up(match_data):
    """Measuring two halves separately gives the same distance as the whole (no window-edge artefacts)."""
    F = match_data.F
    whole = player_physicals(match_data)
    a, b = player_physicals(match_data, (0, F // 2)), player_physicals(match_data, (F // 2, F))
    for pid in whole:
        if pid in a and pid in b:
            assert abs(a[pid]["distance_m"] + b[pid]["distance_m"] - whole[pid]["distance_m"]) < 15


def test_detection_only_uses_the_past(match_data):
    """Changes reported with data up to minute 50 all start before minute 50."""
    rows = minute_metrics(match_data)
    assert all(c["minute"] < 50 for c in detect_changes(rows[:50]))
