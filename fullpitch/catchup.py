"""Facts for "What did I miss?": everything that happened between two moments.

Deterministic and live-safe: given the frame the viewer is at now, it only ever
looks at data up to that frame. The narrator agents receive this dict and turn
it into words; they are told to use these facts and nothing else.
"""

import numpy as np

from .analysis import detect_changes, player_physicals, team_rel

BIG_CHANCE_XG = 0.2


def player_name(md, pid):
    for p in md.meta["players"]:
        if p["id"] == pid:
            return p["name"]
    return pid


def score_at(md, frame):
    score = [0, 0]
    for e in md.events:
        if e["frame"] > frame:
            break
        if e["type"] == "goal":
            score = list(e["score"])
    return score


def window_facts(md, rows, frame_from, frame_to, follow=None, changes=None):
    """Facts for the window [frame_from, frame_to). rows: minute_metrics(md) for the whole match
    (only minutes before frame_to are used). follow: optional player id."""
    shorts = md.shorts
    names = {t["short"]: t["name"] for t in md.meta["teams"]}
    evs = [e for e in md.events if frame_from <= e["frame"] < frame_to]
    clock = md.clock_label

    facts = {
        "window": {"from": clock(frame_from), "to": clock(frame_to),
                   "minutes": round((frame_to - frame_from) / md.hz / 60, 1)},
        "teams": names,
        "score_before": dict(zip(shorts, score_at(md, frame_from - 1))),
        "score_now": dict(zip(shorts, score_at(md, frame_to - 1))),
        "goals": [], "big_chances": [], "cards": [], "substitutions": [],
        "match_over": frame_to >= md.F - 1,  # until the final whistle, nothing is decided yet
    }
    for e in evs:
        if e["type"] == "goal":
            facts["goals"].append({"minute": clock(e["frame"]), "team": e["team"],
                                   "scorer": player_name(md, e["player"]),
                                   "assist": player_name(md, e["assist"]) if e.get("assist") else None,
                                   "score_after": dict(zip(shorts, e["score"]))})
        elif e["type"] == "shot" and e["outcome"] != "goal" and e["xg"] >= BIG_CHANCE_XG:
            facts["big_chances"].append({"minute": clock(e["frame"]), "team": e["team"],
                                         "player": player_name(md, e["player"]), "xg": round(e["xg"], 2),
                                         "outcome": e["outcome"].replace("_", " ")})
        elif e["type"] == "card":
            facts["cards"].append({"minute": clock(e["frame"]), "team": e["team"],
                                   "player": player_name(md, e["player"]), "card": e["card"]})
        elif e["type"] == "substitution":
            facts["substitutions"].append({"minute": clock(e["frame"]), "team": e["team"],
                                           "on": player_name(md, e["on"]), "off": player_name(md, e["off"])})

    # possession, shots, territory in the window
    poss = md.poss[frame_from:frame_to]
    live = poss[poss >= 0]
    stats = {}
    for k, short in enumerate(shorts):
        shots = [e for e in evs if e["type"] == "shot" and e["team"] == short]
        ball = team_rel(md.xy[frame_from:frame_to, 22], k, md.period[frame_from:frame_to])
        in_poss = poss == k
        stats[short] = {
            "possession_pct": round(float((live == k).mean() * 100), 0) if len(live) else None,
            "shots": len(shots),
            "xg": round(sum(e["xg"] for e in shots), 2),
            "final_third_time_pct": round(float((ball[in_poss, 0] > 70).mean() * 100), 0) if in_poss.any() else 0,
            "passes_completed": sum(1 for e in evs if e["type"] == "pass" and e["team"] == short
                                    and e["outcome"] == "complete"),
        }
    facts["stats"] = stats

    # tactical changes: detected live, i.e. only from minutes the viewer has already seen
    now_min = int(md.tmin[min(frame_to, md.F - 1)])
    if changes is not None:
        detected = changes
    else:
        detected = detect_changes(rows[:now_min + 1]) if now_min > 12 else []
    from_min = md.tmin[frame_from]
    facts["shape_changes"] = [
        {"team": c["team"], "since": c["clock"],
         "what": [{"signal": h["label"], "key": h["signal"], "phase": h["phase"],
                   "before": h["before"], "after": h["after"], "unit": h["unit"]}
                  for h in c["signals"][:3]]}
        for c in detected if c["minute"] >= from_min - 3
    ]

    if follow:
        phys = player_physicals(md, window=(frame_from, frame_to)).get(follow)
        touches = [e for e in evs if e.get("player") == follow]
        facts["followed_player"] = {
            "name": player_name(md, follow),
            "team": follow.split("-")[0],
            "distance_m": phys["distance_m"] if phys else 0,
            "sprints": phys["sprints"] if phys else 0,
            "top_speed_kmh": phys["top_speed_kmh"] if phys else 0,
            "passes": sum(1 for e in touches if e["type"] == "pass"),
            "passes_completed": sum(1 for e in touches if e["type"] == "pass" and e["outcome"] == "complete"),
            "shots": sum(1 for e in touches if e["type"] == "shot"),
            "tackles_won": sum(1 for e in touches if e["type"] == "tackle" and e.get("outcome") == "won"),
        }
    return facts
