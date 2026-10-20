"""Simulate many matches and report how football-like the synthetic data is.

Usage: python scripts/realism_report.py 101 120
Prints per-team-per-match distributions (mean, min, median, max) for match stats,
running data and set pieces, plus invariant checks that must always hold.
"""

import copy
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fullpitch.analysis import MatchData, player_physicals  # noqa: E402
from fullpitch.engine import L, W, Match  # noqa: E402
from fullpitch.scenario import DEFAULT_SCENARIO  # noqa: E402


def one(seed):
    m = Match(copy.deepcopy(DEFAULT_SCENARIO), seed=seed).run()
    ev = m.events
    xy = np.stack(m.frames).astype(float)
    teams = [t["short"] for t in m.sc["teams"]]
    c = Counter(e["type"] for e in ev)
    restarts = Counter((e["team"], e["kind"]) for e in ev if e["type"] == "restart")
    goals = [e for e in ev if e["type"] == "goal"]
    per_team = []
    for t in teams:
        passes = [e for e in ev if e["type"] == "pass" and e["team"] == t]
        shots = [e for e in ev if e["type"] == "shot" and e["team"] == t]
        per_team.append({
            "goals": sum(1 for g in goals if g["team"] == t),
            "shots": len(shots),
            "on target %": 100 * sum(e["outcome"] in ("goal", "saved") for e in shots) / max(1, len(shots)),
            "xG": sum(e["xg"] for e in shots),
            "passes": len(passes),
            "pass accuracy %": 100 * sum(e["outcome"] == "complete" for e in passes) / max(1, len(passes)),
            "fouls": sum(1 for e in ev if e["type"] == "foul" and e["team"] == t),
            "yellow cards": sum(1 for e in ev if e["type"] == "card" and e["card"] == "yellow" and e["team"] == t),
            "corners": restarts[(t, "corner")],
            "throw-ins": restarts[(t, "throw_in")],
            "goal kicks": restarts[(t, "goal_kick")],
            "offsides": sum(1 for e in ev if e["type"] == "offside" and e["team"] == t),
            "substitutions": sum(1 for e in ev if e["type"] == "substitution" and e["team"] == t),
        })
    poss = np.array(m.frame_poss)
    live = poss[poss >= 0]
    md = MatchData({"xy": np.stack(m.frames), "clock": np.array(m.frame_clock),
                    "period": np.array(m.frame_period), "poss": poss}, m.meta(), ev)
    ph = player_physicals(md)
    full = {pid: v for pid, v in ph.items() if v["minutes"] > 85}
    match = {
        "total goals": len(goals),
        "penalties": sum(1 for e in ev if e["type"] == "shot" and e.get("penalty")),
        "red cards": sum(1 for e in ev if e["type"] == "card" and e["card"] == "red"),
        "ball in play (min)": len(live) / m.sc.get("hz", 5) / 60 if "hz" in m.sc else len(live) / 300,
        "home possession %": 100 * float((live == 0).mean()),
        "shot distance (m)": [e["distance"] for e in ev if e["type"] == "shot" and not e.get("penalty")],
        "outfield distance (km)": [v["distance_m"] / 1000 for pid, v in full.items() if not pid.endswith("-01")],
        "goalkeeper distance (km)": [v["distance_m"] / 1000 for pid, v in full.items() if pid.endswith("-01")],
        "top speed (km/h)": [v["top_speed_kmh"] for pid, v in full.items() if not pid.endswith("-01")],
        "sprints per player": [v["sprints"] for pid, v in full.items() if not pid.endswith("-01")],
    }
    on_pitch = np.ones(len(xy), dtype=bool)
    checks = {
        "players stay within 1 m of the pitch (sent-off players wait by the halfway line)":
            bool(np.all(((xy[:, :22, 0] >= -1.01) & (xy[:, :22, 0] <= L + 1.01) & (xy[:, :22, 1] >= -1.01) & (xy[:, :22, 1] <= W + 1.01))
                        | ((np.abs(xy[:, :22, 0] - L / 2) < 0.01) & ((np.abs(xy[:, :22, 1] + 4) < 0.01) | (np.abs(xy[:, :22, 1] - W - 4) < 0.01))))),
        "ball never further than 3.5 m off the pitch":
            bool(np.all((xy[on_pitch, 22, 0] >= -3.5) & (xy[on_pitch, 22, 0] <= L + 3.5) & (xy[on_pitch, 22, 1] >= -3.5) & (xy[on_pitch, 22, 1] <= W + 3.5))),
        "events are in time order": all(a["frame"] <= b["frame"] for a, b in zip(ev, ev[1:])),
        "score equals goals": m.score == [sum(1 for g in goals if g["team"] == t) for t in teams],
        "every goal comes from a shot": sum(1 for e in ev if e["type"] == "shot" and e["outcome"] == "goal") == len(goals),
        "no player booked twice without a red":
            all(v < 2 or any(e["type"] == "card" and e["card"] == "red" and e.get("player") == pid for e in ev)
                for pid, v in Counter(e["player"] for e in ev if e["type"] == "card" and e["card"] == "yellow").items()),
        "at most 5 substitutions per team": all(r["substitutions"] <= 5 for r in per_team),
        "ball moves no faster than a shot (<= 36 m/s, half-time change of ends excepted)":
            int(np.sum((np.linalg.norm(np.diff(xy[:, 22], axis=0), axis=1) * 5 > 36)
                       & (np.diff(np.array(m.frame_period)) == 0))) == 0,
    }
    return per_team, match, checks


def dist(name, vals):
    v = np.array(vals, dtype=float)
    if not len(v):
        return f"{name:28s}  (none)"
    return f"{name:28s} mean {v.mean():7.1f}   min {v.min():7.1f}   median {np.median(v):7.1f}   max {v.max():7.1f}"


def main():
    a, b = int(sys.argv[1]), int(sys.argv[2])
    with ProcessPoolExecutor() as ex:
        res = list(ex.map(one, range(a, b + 1)))
    print(f"{b - a + 1} matches (seeds {a}-{b})\n\nPer team per match")
    rows = [t for per_team, _, _ in res for t in per_team]
    for key in rows[0]:
        print(dist(key, [r[key] for r in rows]))
    print("\nPer match")
    for key in res[0][1]:
        vals = [x for _, m, _ in res for x in (m[key] if isinstance(m[key], list) else [m[key]])]
        print(dist(key, vals))
    print("\nInvariants")
    for key in res[0][2]:
        ok = sum(1 for _, _, c in res if c[key])
        print(f"  [{'OK' if ok == len(res) else 'FAIL'}] {key}: {ok}/{len(res)}")


if __name__ == "__main__":
    main()
