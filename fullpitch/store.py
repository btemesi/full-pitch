"""One loaded match with its precomputed analysis, sliced so nobody sees the future.

Every query takes `at_minute`: the live point of the broadcast, in continuous
match minutes (0 = kick-off, no half-time gap). Anything after it is invisible.
"""

import json
from functools import lru_cache
from pathlib import Path

import numpy as np

from .analysis import L, MatchData, detect_changes, minute_metrics, player_physicals, slot_occupancy, team_rel
from .catchup import player_name, score_at, window_facts
from . import insights


class MatchStore:
    def __init__(self, match_dir):
        d = Path(match_dir)
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        events = [json.loads(x) for x in (d / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.md = MatchData(np.load(d / "tracking.npz"), meta, events)
        self.rows = minute_metrics(self.md)  # per-minute; only rows[:at_minute] are ever exposed
        self.length_min = float(self.md.tmin[-1])
        self.live_changes = self._replay_detection()
        self.runs = insights.off_ball_runs(self.md, self.md.F)  # each run only depends on frames up to it

    def _replay_detection(self):
        """Run the detector minute by minute exactly as it would run live, and remember every
        change once confirmed: a pundit doesn't take back what they said half a minute later.
        Returns versions: list of (confirmed_minute, key, change) where later versions refine earlier ones."""
        versions, keys = [], []
        for a in range(13, len(self.rows) + 1):
            for c in detect_changes(self.rows[:a]):
                key = next((k for k in keys if k[0] == c["team"] and abs(k[1] - c["minute"]) <= 6), None)
                if key is None:
                    key = (c["team"], c["minute"])
                    keys.append(key)
                versions.append((a, key, c))
        return versions

    def live_changes_at(self, at_minute):
        latest = {}
        for a, key, c in self.live_changes:
            if a > at_minute:
                break
            latest[key] = (latest.get(key, (a,))[0], c)  # keep first confirmation time, newest content
        return [dict(c, confirmed_minute=a) for a, c in sorted(latest.values(), key=lambda v: v[1]["minute"])]

    # ---------------------------------------------------------------- helpers
    def frame(self, minute):
        return int(np.clip(minute * 60 * self.md.hz, 0, self.md.F - 1))

    def clock(self, minute):
        return self.md.clock_label(self.frame(minute))

    def _seen(self, at_minute):
        return self.rows[:max(0, int(at_minute))]

    # ---------------------------------------------------------------- queries
    def match_info(self):
        m = self.md.meta
        return {
            "match_id": m["match_id"], "competition": m["competition"], "venue": m["venue"],
            "teams": [{"short": t["short"], "name": t["name"], "starting_formation": t["formation"]} for t in m["teams"]],
            "players": [{"id": p["id"], "name": p["name"], "number": p["number"], "team": p["id"].split("-")[0],
                         "starting_role": p["start_role"]} for p in m["players"]],
            "length_minutes": round(self.length_min, 1),
            "note": "Synthetic match, fictional teams and players.",
        }

    def match_state(self, at_minute):
        f = self.frame(at_minute)
        recent = [e for e in self.md.events if f - 120 * self.md.hz <= e["frame"] <= f
                  and e["type"] in ("goal", "shot", "card", "substitution", "save", "foul")]
        last5 = self.md.poss[max(0, f - 300 * self.md.hz):f + 1]
        live = last5[last5 >= 0]
        shape = {}
        for short in self.md.shorts:
            rows = self._seen(at_minute)[-3:]
            def avg(ph, key):
                v = [r["teams"][short][ph][key] for r in rows if r["teams"][short][ph].get(key) is not None]
                return round(float(np.mean(v)), 1) if v else None
            shape[short] = {"defensive_line_m": avg("def", "def_line"), "back_line_players": avg("def", "n_back"),
                            "team_length_m": avg("def", "length"), "team_width_m": avg("def", "width"),
                            "pressers_near_ball": avg("def", "pressers"),
                            "back_line_players_in_possession": avg("att", "n_back")}
        return {
            "clock": self.clock(at_minute),
            "score": dict(zip(self.md.shorts, score_at(self.md, f))),
            "possession_last_5_min_pct": {s: round(float((live == k).mean() * 100)) if len(live) else None
                                          for k, s in enumerate(self.md.shorts)},
            "shape_last_3_min": shape,
            "recent_events": [self._event_view(e) for e in recent[-8:]],
        }

    def tactical_changes(self, at_minute):
        out = []
        for c in self.live_changes_at(at_minute):
            out.append({"team": c["team"], "since": c["clock"], "since_minute": c["minute"],
                        "confirmed_minute": c["confirmed_minute"],
                        "confidence": "high" if c["strength"] >= 4.5 else "medium",
                        "signals": [{"what": h["label"], "signal": h["signal"], "phase": h["phase"],
                                     "before": h["before"], "after": h["after"], "unit": h["unit"]}
                                    for h in c["signals"][:3]]})
        return out

    def shape_timeline(self, team, from_minute, to_minute, at_minute):
        to_minute = min(to_minute, at_minute)
        out = []
        for r in self._seen(to_minute)[int(max(0, from_minute)):]:
            t = r["teams"][team]
            out.append({"clock": r["clock"],
                        "without_ball": {k: t["def"].get(k) for k in ("def_line", "n_back", "length", "width", "pressers")},
                        "with_ball": {k: t["att"].get(k) for k in ("def_line", "n_back", "length", "width")},
                        "final_third_entries": t["entries"]})
        return out

    def player_report(self, player_id, from_minute, to_minute, at_minute):
        to_minute = min(to_minute, at_minute)
        f0, f1 = self.frame(from_minute), self.frame(to_minute)
        phys = player_physicals(self.md, window=(f0, f1)).get(player_id, {})
        evs = [e for e in self.md.events if f0 <= e["frame"] < f1 and e.get("player") == player_id]
        passes = [e for e in evs if e["type"] == "pass"]
        return {
            "player": player_name(self.md, player_id), "id": player_id,
            "window": [self.clock(from_minute), self.clock(to_minute)],
            "physical": phys,
            "passes": len(passes), "passes_completed": sum(e["outcome"] == "complete" for e in passes),
            "hardest_pass_completed": max((e["difficulty"] for e in passes if e["outcome"] == "complete"), default=None),
            "shots": [{"clock": self.md.clock_label(e["frame"]), "xg": round(e["xg"], 2), "outcome": e["outcome"],
                       "speed_kmh": e["shot_speed_kmh"]} for e in evs if e["type"] == "shot"],
            "goals": sum(1 for e in evs if e["type"] == "goal"),
            "tackles_won": sum(1 for e in evs if e["type"] == "tackle" and e["outcome"] == "won"),
            "interceptions": sum(1 for e in evs if e["type"] == "interception"),
        }

    def catch_up(self, from_minute, to_minute, at_minute, follow_player=None):
        to_minute = min(to_minute, at_minute)
        return window_facts(self.md, self.rows, self.frame(from_minute), self.frame(to_minute), follow=follow_player,
                            changes=self.live_changes_at(to_minute))

    # ---------------------------------------------------------------- for viewers' questions
    @property
    def occupancy(self):
        if not hasattr(self, "_occ"):
            self._occ = slot_occupancy(self.md)
        return self._occ

    def player_at(self, k, s, f):
        return next(p for p, a, b in self.occupancy[k][s] if a <= f < b or (b == self.md.F and f >= a))

    def slot_at(self, pid, f):
        """(team index, slot) of a player on the pitch at frame f, or None."""
        for k, slots in enumerate(self.occupancy):
            for s, spans in enumerate(slots):
                if any(p == pid and a <= f < b for p, a, b in spans) and self.md.active[f, k * 11 + s]:
                    return k, s
        return None

    def positions_at(self, minute, at_minute):
        """Where everyone stood at a moment (never later than now): metres from their own goal line,
        channel from their own view of the pitch, distance to the ball, and who had it."""
        return self._positions(self.frame(min(minute, at_minute)))

    def _positions(self, f):
        md = self.md
        ball = md.xy[f, 22]
        # the minute of the frame actually shown, rounded down so it never reads later than now
        out = {"clock": md.clock_label(f), "minute": float(np.floor(float(md.tmin[f]) * 100) / 100),
               "team_in_possession": md.shorts[int(md.poss[f])] if md.poss[f] >= 0 else None, "players": []}
        for k, short in enumerate(md.shorts):
            for s in range(11):
                if not md.active[f, k * 11 + s]:
                    continue
                pid = self.player_at(k, s, f)
                rel = team_rel(md.xy[f, k * 11 + s], k, int(md.period[f]))
                out["players"].append({
                    "id": pid, "name": player_name(md, pid), "team": short,
                    "metres_from_own_goal": round(float(rel[0]), 1),
                    "metres_from_goal_they_attack": round(float(L - rel[0]), 1),
                    "channel": "left" if rel[1] > 45 else ("right" if rel[1] < 23 else "centre"),
                    "metres_to_ball": round(float(np.linalg.norm(md.xy[f, k * 11 + s] - ball)), 1)})
        out["players"].sort(key=lambda p: p["metres_to_ball"])
        return out

    def attack_channels(self, team, from_minute, at_minute):
        """Completed passes into the final third by channel (left / centre / right, from the attacking
        team's own view), from a minute until now."""
        rows = self._seen(at_minute)[int(max(0, from_minute)):]
        tot = {c: sum(r["teams"][team]["entries"][c] for r in rows) for c in ("left", "centre", "right")}
        n = sum(tot.values())
        return {"team": team, "window": [self.clock(from_minute), self.clock(at_minute)], "entries": tot,
                "share_pct": {c: (round(100 * v / n) if n else None) for c, v in tot.items()}}

    def leaders(self, at_minute, top=5):
        """The players leading each count so far: distance, sprints, top speed, shots, passes, tackles."""
        f = self.frame(at_minute)
        phys = player_physicals(self.md, window=(0, f))
        evs = [e for e in self.md.events if e["frame"] <= f and e.get("player")]
        counts = {}
        for e in evs:
            c = counts.setdefault(e["player"], {"shots": 0, "passes_completed": 0, "tackles_won": 0})
            if e["type"] == "shot":
                c["shots"] += 1
            elif e["type"] == "pass" and e["outcome"] == "complete":
                c["passes_completed"] += 1
            elif e["type"] == "tackle" and e["outcome"] == "won":
                c["tackles_won"] += 1

        def board(values, unit):
            best = sorted(values.items(), key=lambda kv: -kv[1])[:top]
            return [{"id": pid, "name": player_name(self.md, pid), "team": pid.split("-")[0], unit: v} for pid, v in best]
        return {"clock": self.clock(at_minute),
                "distance": board({p: round(v["distance_m"] / 1000, 2) for p, v in phys.items() if v}, "km"),
                "sprints": board({p: v["sprints"] for p, v in phys.items() if v}, "sprints"),
                "top_speed": board({p: v["top_speed_kmh"] for p, v in phys.items() if v}, "kmh"),
                "shots": board({p: c["shots"] for p, c in counts.items()}, "shots"),
                "passes_completed": board({p: c["passes_completed"] for p, c in counts.items()}, "passes"),
                "tackles_won": board({p: c["tackles_won"] for p, c in counts.items()}, "tackles")}

    # ---------------------------------------------------------------- deeper numbers
    def team_stats(self, at_minute):
        return insights.team_stats(self.md, self.frame(at_minute) + 1)

    def xg_timeline(self, at_minute):
        return insights.xg_timeline(self.md, self.frame(at_minute) + 1, self.live_changes_at(at_minute))

    def pass_network(self, team, from_minute, at_minute):
        return insights.pass_network(self.md, team, self.frame(min(from_minute, at_minute)), self.frame(at_minute) + 1)

    def player_profile(self, pid, at_minute):
        return insights.player_profile(self.md, pid, self.frame(at_minute) + 1, self.runs)

    @property
    def ht_minute(self):
        # match minute of the half-time whistle (the last frame of the first half)
        idx = np.flatnonzero(np.asarray(self.md.period) == 2)
        return float(self.md.tmin[int(idx[0]) - 1]) if len(idx) else self.length_min

    def recap(self, kind, at_minute):
        # the half-time recap says what was known at the whistle, whenever it is read
        if kind == "ht" and at_minute >= self.ht_minute:
            at_minute = self.ht_minute
        return insights.recap(self.md, kind, self.frame(at_minute) + 1, self.live_changes_at(at_minute), self.runs)

    def replay(self, kind, minute, team, at_minute, signal=None):
        return insights.replay(self.md, self.rows, kind, min(minute, at_minute), team, at_minute, signal)

    def key_moments(self, at_minute):
        f = self.frame(at_minute)
        out = []
        for moment, e in self._key_events():
            if e["frame"] > f:
                break
            out.append(dict(self._event_view(e), moment=moment))
        return out

    def _key_events(self):
        """(moment id, event) for goals, big chances and cards in match order: 'goal-1', 'chance-3', 'card-2'."""
        n = {}
        for e in self.md.events:
            if e["type"] == "goal" or (e["type"] == "shot" and e["outcome"] != "goal" and e["xg"] >= 0.2) \
                    or e["type"] == "card":
                kind = "chance" if e["type"] == "shot" else e["type"]
                n[kind] = n.get(kind, 0) + 1
                yield f"{kind}-{n[kind]}", e

    def positions_at_moment(self, moment, at_minute):
        """Positions at the exact frame of a key moment ('goal-1' from key_moments), if it has happened."""
        e = next((e for m, e in self._key_events() if m == moment), None)
        if e is None or e["frame"] > self.frame(at_minute):
            raise ValueError(f"no moment {moment!r} so far; key_moments lists them")
        out = self._positions(e["frame"])  # the event's own frame, not a minute converted back
        out["moment"] = dict(self._event_view(e), moment=moment)
        return out

    def _event_view(self, e):
        v = {"clock": self.md.clock_label(e["frame"]), "minute": round(float(self.md.tmin[e["frame"]]), 2),
             "type": e["type"], "team": e.get("team")}
        if e.get("player"):
            v["player"] = player_name(self.md, e["player"])
        for key in ("xg", "outcome", "card", "score", "penalty"):
            if key in e:
                v[key] = round(e[key], 2) if key == "xg" else e[key]
        if e["type"] == "goal" and e.get("assist"):
            v["assist"] = player_name(self.md, e["assist"])
        if e["type"] == "substitution":
            v["on"], v["off"] = player_name(self.md, e["on"]), player_name(self.md, e["off"])
        return v


@lru_cache(maxsize=4)
def load(match_dir):
    return MatchStore(match_dir)
