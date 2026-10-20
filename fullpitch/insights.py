"""Deeper team and player numbers, recaps and replay overlays. All "as of" a frame.

Everything here is measured from tracking and events; nothing is generated.
Coordinates handed to the page for mini-pitch panels are team-relative
(the team always attacks to the right), so they read the same in both halves.
"""

from collections import Counter, defaultdict

import numpy as np

from .analysis import BALL_SHIFT, L, W, attacks_plus_x, player_physicals, slot_occupancy, team_rel
from .catchup import player_name

HEAT_BINS = (21, 14)          # 5 m x ~4.9 m cells
PPDA_OWN_ZONE = 63.0          # opponent's build-up zone: their first 60% of the pitch
RUN_MIN_SPEED = 6.0           # m/s, forward running speed for an off-ball run
RUN_COOLDOWN_S = 6.0


def _evs(md, frame_to, frame_from=0):
    return [e for e in md.events if frame_from <= e["frame"] < frame_to]


def _k(md, short):
    return md.shorts.index(short)


# --------------------------------------------------------------- team stats
def team_stats(md, frame_to):
    evs = _evs(md, frame_to)
    poss = md.poss[:frame_to]
    live = poss[poss >= 0]
    out = {}
    for k, short in enumerate(md.shorts):
        passes = [e for e in evs if e["type"] == "pass" and e["team"] == short]
        shots = [e for e in evs if e["type"] == "shot" and e["team"] == short]
        ball = team_rel(md.xy[:frame_to, 22], k, md.period[:frame_to])
        mine = poss == k
        thirds = [float(((ball[mine, 0] < 35)).mean()), float(((ball[mine, 0] >= 35) & (ball[mine, 0] < 70)).mean()),
                  float((ball[mine, 0] >= 70).mean())] if mine.any() else [0, 0, 0]
        # PPDA: opponent passes in their build-up zone per defensive action of ours in that zone
        opp = md.shorts[1 - k]
        opp_passes = sum(1 for e in evs if e["type"] == "pass" and e["team"] == opp
                         and team_rel(np.array(e["start"]), 1 - k, e["period"])[0] < PPDA_OWN_ZONE)
        def_actions = sum(1 for e in evs if e.get("team") == short and e["type"] in ("tackle", "interception", "foul")
                          and "at" in e and team_rel(np.array(e["at"]), k, e["period"])[0] > L - PPDA_OWN_ZONE)
        out[short] = {
            "goals": sum(1 for e in evs if e["type"] == "goal" and e["team"] == short),
            "possession_pct": round(float((live == k).mean() * 100)) if len(live) else 50,
            "shots": len(shots),
            "shots_on_target": sum(1 for e in shots if e["outcome"] in ("goal", "saved")),
            "xg": round(sum(e["xg"] for e in shots), 2),
            "passes": len(passes),
            "pass_accuracy_pct": round(100 * sum(e["outcome"] == "complete" for e in passes) / max(1, len(passes))),
            "possession_by_third_pct": [round(100 * t) for t in thirds],
            "ppda": round(opp_passes / def_actions, 1) if def_actions else None,
            "corners": sum(1 for e in evs if e["type"] == "restart" and e.get("kind") == "corner" and e["team"] == short),
            "fouls": sum(1 for e in evs if e["type"] == "foul" and e["team"] == short),
            "offsides": sum(1 for e in evs if e["type"] == "offside" and e["team"] == short),
            "yellow_cards": sum(1 for e in evs if e["type"] == "card" and e["card"] == "yellow" and e["team"] == short),
            "red_cards": sum(1 for e in evs if e["type"] == "card" and e["card"] == "red" and e["team"] == short),
        }
    return out


def xg_timeline(md, frame_to, changes):
    """Cumulative xG per team, with goals and confirmed shape changes as markers."""
    series = {s: [[0.0, 0.0]] for s in md.shorts}
    goals = []
    for e in _evs(md, frame_to):
        m = round(float(md.tmin[e["frame"]]), 2)
        if e["type"] == "shot":
            last = series[e["team"]][-1][1]
            series[e["team"]].append([m, round(last + e["xg"], 3)])
        if e["type"] == "goal":
            goals.append({"team": e["team"], "minute": m, "clock": md.clock_label(e["frame"])})
    now = round(float(md.tmin[max(0, frame_to - 1)]), 2)
    for s in series:
        series[s].append([now, series[s][-1][1]])
    marks = [{"team": c["team"], "minute": c["minute"], "clock": c["clock"]} for c in changes]
    return {"series": series, "goals": goals, "changes": marks, "now": now}


# --------------------------------------------------------------- pass network
def pass_network(md, short, frame_from, frame_to, min_passes=2):
    """Average positions (in possession) and completed-pass links for one team in a window."""
    k = _k(md, short)
    occ = slot_occupancy(md)
    pos = defaultdict(list)
    team_xy = md.team(k)
    for s in range(11):
        for pid, a, b in occ[k][s]:
            a2, b2 = max(a, frame_from), min(b, frame_to)
            if b2 <= a2:
                continue
            f = np.arange(a2, b2)
            f = f[(md.poss[f] == k) & md.active[f, k * 11 + s]]
            if len(f):
                pos[pid].append(team_xy[f, s])
    nodes = []
    touches = Counter(e["player"] for e in _evs(md, frame_to, frame_from) if e["type"] == "pass" and e["team"] == short)
    for pid, chunks in pos.items():
        p = np.concatenate(chunks)
        if len(p) < 25 or touches[pid] == 0:
            continue
        nodes.append({"id": pid, "name": player_name(md, pid), "number": _number(md, pid),
                      "x": round(float(p[:, 0].mean()), 1), "y": round(float(p[:, 1].mean()), 1), "passes": touches[pid]})
    ids = {n["id"] for n in nodes}
    links = Counter()
    for e in _evs(md, frame_to, frame_from):
        if e["type"] == "pass" and e["team"] == short and e["outcome"] == "complete" and e["player"] in ids \
                and e.get("receiver") in ids:
            links[tuple(sorted((e["player"], e["receiver"])))] += 1
    edges = [{"a": a, "b": b, "count": n} for (a, b), n in links.items() if n >= min_passes]
    return {"team": short, "nodes": nodes, "edges": edges,
            "window": [md.clock_label(frame_from), md.clock_label(max(frame_from, frame_to - 1))]}


def _number(md, pid):
    for p in md.meta["players"]:
        if p["id"] == pid:
            return p["number"]
    return None


# --------------------------------------------------------------- off-ball runs
def off_ball_runs(md, frame_to, frame_from=0):
    """Runs beyond the opponents' last line while the team has the ball and the runner doesn't.
    Returns {player_id: [minute, ...]}."""
    occ = slot_occupancy(md)
    out = defaultdict(list)
    cooldown = int(RUN_COOLDOWN_S * md.hz)
    for k in (0, 1):
        R = md.team(k)
        O = md.team(1 - k)
        B = md.ball(k)
        last = np.full(11, -10 ** 9)
        for f in range(max(1, frame_from), frame_to, 2):
            if md.poss[f] != k:
                continue
            opp_x = np.sort(O[f, :, 0][md.active[f, (1 - k) * 11:(2 - k) * 11]])
            line = max(opp_x[-2], L / 2)
            vx = (R[f, :, 0] - R[f - 1, :, 0]) * md.hz
            near_ball = np.linalg.norm(R[f] - B[f], axis=1) < 2.0
            for s in range(1, 11):
                if near_ball[s] or not md.active[f, k * 11 + s] or f - last[s] < cooldown:
                    continue
                if vx[s] > RUN_MIN_SPEED and R[f, s, 0] > line - 0.5 and R[f, s, 0] > 60:
                    last[s] = f
                    pid = next(p for p, a, b in occ[k][s] if a <= f < b)
                    out[pid].append(round(float(md.tmin[f]), 1))
    return dict(out)


# --------------------------------------------------------------- player maps
def runs_until(runs, minute):
    return {pid: [m for m in ms if m <= minute] for pid, ms in runs.items()}


def player_profile(md, pid, frame_to, runs=None):
    """Heatmap, passes, shots, 15-minute running splits and off-ball runs for one player, up to now."""
    k = _k(md, pid.split("-")[0])
    occ = slot_occupancy(md)
    team_xy = md.team(k)
    heat = np.zeros(HEAT_BINS)
    for s in range(11):
        for p, a, b in occ[k][s]:
            if p != pid:
                continue
            f = np.arange(a, min(b, frame_to))
            f = f[md.active[f, k * 11 + s]]
            xy = team_xy[f, s]
            ix = np.clip((xy[:, 0] / L * HEAT_BINS[0]).astype(int), 0, HEAT_BINS[0] - 1)
            iy = np.clip((xy[:, 1] / W * HEAT_BINS[1]).astype(int), 0, HEAT_BINS[1] - 1)
            np.add.at(heat, (ix, iy), 1)
    if heat.max() > 0:
        heat = heat / heat.max()
    evs = [e for e in _evs(md, frame_to) if e.get("player") == pid]
    rel = lambda p, e: [round(float(v), 1) for v in team_rel(np.array(p), k, e["period"])]  # noqa: E731
    passes = [{"from": rel(e["start"], e), "to": rel(e["end"], e), "ok": e["outcome"] == "complete"}
              for e in evs if e["type"] == "pass"]
    shots = [{"at": rel(e["start"], e), "xg": round(e["xg"], 2), "outcome": e["outcome"],
              "clock": md.clock_label(e["frame"])} for e in evs if e["type"] == "shot"]
    splits = []
    step = 15 * 60 * md.hz
    for a in range(0, frame_to, step):
        b = min(a + step, frame_to)
        ph = player_physicals(md, (a, b)).get(pid)
        if ph and ph["minutes"] > 3:
            splits.append({"from": md.clock_label(a), "minutes": ph["minutes"],
                           "km_per_90": round(ph["distance_m"] / ph["minutes"] * 90 / 1000, 1),
                           "top_speed_kmh": ph["top_speed_kmh"], "sprints": ph["sprints"]})
    now_min = float(md.tmin[max(0, frame_to - 1)])
    runs = runs_until(runs if runs is not None else off_ball_runs(md, frame_to), now_min).get(pid, [])
    goals = sum(1 for e in _evs(md, frame_to) if e["type"] == "goal" and e.get("player") == pid)
    assists = sum(1 for e in _evs(md, frame_to) if e["type"] == "goal" and e.get("assist") == pid)
    fatigue = None
    if len(splits) >= 3:
        first = np.mean([s["top_speed_kmh"] for s in splits[:2]])
        lastv = splits[-1]["top_speed_kmh"]
        fatigue = {"top_speed_drop_kmh": round(float(first - lastv), 1),
                   "km_per_90_drop": round(float(np.mean([s["km_per_90"] for s in splits[:2]]) - splits[-1]["km_per_90"]), 1)}
    return {"id": pid, "name": player_name(md, pid), "team": md.shorts[k],
            "heatmap": [[round(float(v), 2) for v in row] for row in heat], "heat_bins": HEAT_BINS,
            "passes": passes, "shots": shots, "splits": splits, "fatigue": fatigue,
            "off_ball_runs": runs, "goals": goals, "assists": assists,
            "xg": round(sum(s["xg"] for s in shots), 2),
            "pass_accuracy_pct": round(100 * sum(p["ok"] for p in passes) / max(1, len(passes)))}


# --------------------------------------------------------------- recap
def player_ratings(md, frame_to, runs=None):
    """A transparent match rating: goals 3, assists 2, xG x2, shots on target 0.3, tackles won 0.3,
    interceptions 0.3, completed passes 0.01, off-ball runs 0.1."""
    evs = _evs(md, frame_to)
    score = Counter()
    for e in evs:
        p = e.get("player")
        if e["type"] == "goal":
            score[p] += 3
            if e.get("assist"):
                score[e["assist"]] += 2
        elif e["type"] == "shot":
            score[p] += 2 * e["xg"] + (0.3 if e["outcome"] in ("goal", "saved") else 0)
        elif e["type"] == "tackle" and e.get("outcome") == "won":
            score[p] += 0.3
        elif e["type"] == "interception":
            score[p] += 0.3
        elif e["type"] == "pass" and e["outcome"] == "complete":
            score[p] += 0.01
    all_runs = runs if runs is not None else off_ball_runs(md, frame_to)
    for pid, ms in runs_until(all_runs, float(md.tmin[max(0, frame_to - 1)])).items():
        score[pid] += 0.1 * len(ms)
    return score


def recap(md, kind, frame_to, changes, runs=None):
    """Half-time or full-time facts; None until that moment has been reached."""
    period = np.asarray(md.period)
    if kind == "ht":
        idx = np.flatnonzero(period == 2)
        end = int(idx[0]) if len(idx) else md.F
    else:
        end = md.F
    if frame_to < end:
        return None
    stats = team_stats(md, end)
    goals = [{"clock": md.clock_label(e["frame"]), "team": e["team"], "scorer": player_name(md, e["player"]),
              "assist": player_name(md, e["assist"]) if e.get("assist") else None}
             for e in _evs(md, end) if e["type"] == "goal"]
    big = sorted([e for e in _evs(md, end) if e["type"] == "shot" and e["outcome"] != "goal"],
                 key=lambda e: -e["xg"])[:2]
    ratings = player_ratings(md, end, runs)
    best = max(ratings.items(), key=lambda kv: kv[1]) if ratings else (None, 0)
    return {"kind": kind, "clock": md.clock_label(end - 1),
            "score": {s: stats[s]["goals"] for s in md.shorts}, "stats": stats, "goals": goals,
            "big_misses": [{"clock": md.clock_label(e["frame"]), "team": e["team"], "player": player_name(md, e["player"]),
                            "xg": round(e["xg"], 2)} for e in big],
            "changes": [{"team": c["team"], "since": c["clock"]} for c in changes if c["minute"] <= md.tmin[end - 1]],
            "player_of_the_match": {"id": best[0], "name": player_name(md, best[0]) if best[0] else None,
                                    "team": best[0].split("-")[0] if best[0] else None, "rating": round(best[1], 2)}}


# --------------------------------------------------------------- replay overlays
def frames_payload(md, f_from, f_to):
    return [{"f": f, "c": md.clock_label(f), "p": int(md.poss[f]), "t": round(float(md.clock[f]), 1),
             "m": round(float(md.tmin[f]), 3), "xy": [round(float(v), 1) for v in md.xy[f].ravel()]}
            for f in range(f_from, f_to)]


def replay(md, rows, kind, minute, team, now_minute, signal=None):
    """Frames around a moment plus what to draw on top. Never extends past now."""
    hz = md.hz
    f_now = int(min(now_minute * 60 * hz, md.F - 1))
    if kind == "goal":
        f_goal = int(minute * 60 * hz)
        f_from, f_to = max(0, f_goal - 15 * hz), min(f_now, f_goal + 3 * hz)
        goal = next((e for e in md.events if e["type"] == "goal" and abs(e["frame"] - f_goal) <= hz), None)
        overlay = {"type": "goal"}
        if goal:
            overlay.update({"scorer": player_name(md, goal["player"]),
                            "assist": player_name(md, goal["assist"]) if goal.get("assist") else None})
            shot = next((e for e in reversed(md.events) if e["type"] == "shot" and e["frame"] <= goal["frame"]
                         and e["team"] == goal["team"]), None)
            if shot:
                overlay["xg"] = round(shot["xg"], 2)
        return {"frames": frames_payload(md, f_from, max(f_from + 1, f_to)), "overlay": overlay}

    # a shape change: 20 seconds of play after it began, with what to look at drawn on top
    k = md.shorts.index(team)
    m = int(minute)
    seen = rows[:max(1, int(now_minute))]

    def avg(lo, hi, key="line_adj", ph="def"):
        v = [r["teams"][team][ph].get(key) for r in seen[max(0, lo):hi] if r["teams"][team][ph].get(key) is not None]
        return float(np.mean(v)) if v else None
    before, after = avg(m - 12, m), avg(m, m + 12)
    defending = signal not in ("n_back",)  # a back three shows best in possession, the rest out of it
    f_from = min(int((m + 1) * 60 * hz), max(0, f_now - 20 * hz))
    for cand in range(f_from, max(f_from + 1, f_now - 20 * hz), hz * 5):
        want = (1 - k) if defending else k
        if (md.poss[cand:cand + 20 * hz] == want).mean() > 0.6:
            f_from = cand
            break
    f_to = min(f_now, f_from + 20 * hz)
    frames = range(f_from, max(f_from + 1, f_to))
    R, B = md.team(k), md.ball(k)
    ghost, ring = {"before": [], "now": []}, []
    for f in frames:
        period = int(md.period[f])
        to_abs = (lambda x: x) if attacks_plus_x(k, period) else (lambda x: L - x)
        act = md.active[f, k * 11 + 1:(k + 1) * 11]
        x = R[f, 1:, 0]
        # where the back line would stand with the old instruction vs the new one, for this ball position
        for label, adj in (("before", before), ("now", after)):
            if adj is not None:
                ghost[label].append(round(float(to_abs(np.clip(adj + BALL_SHIFT * (B[f, 0] - L / 2), 3, 70))), 1))
        if signal == "pressers":
            d = np.linalg.norm(R[f, 1:] - B[f], axis=1)
            ring.append([int(s + 1) for s in range(10) if act[s] and d[s] < 10])
        elif signal in ("n_back", "n_back_def"):
            deepest = x[act].min()
            ring.append([int(s + 1) for s in range(10) if act[s] and x[s] < deepest + 5.0])
        else:
            ring.append([])
    overlay = {"type": "change", "team": team, "team_index": k, "signal": signal,
               "ghost": ghost if signal in ("line_adj", "def_line", None, "") else {},
               "ring": ring, "channel": signal == "left_share",
               "before_m": round(before, 1) if before is not None else None,
               "after_m": round(after, 1) if after is not None else None}
    return {"frames": frames_payload(md, f_from, max(f_from + 1, f_to)), "overlay": overlay}
