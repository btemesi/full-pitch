"""Synthetic match engine.

Produces football-realistic tracking data (22 players + ball at 5 Hz) and an
event stream (passes, shots, tackles, pressure, fouls, cards, subs, goals).

How it works, in one paragraph: every player has a home spot in the team's
formation. The whole block slides with the ball, longer and wider in
possession, compact without it, with the back line height capped by the
coach's instructions. Nearest defenders break out of the block to press.
The ball carrier holds, dribbles, then picks a pass by weighing progress,
space and the risk of the passing lane. Outcomes are sampled from simple
probability models (pass success, tackles, an xG model for shots). Tactical
changes from the scenario timeline change the parameters mid-match, which
is what the analysis layer later has to discover from movement alone.

Coordinates are metres: x in [0, 105] (home attacks +x), y in [0, 68].
"""

import math

import numpy as np

from .formations import FORMATIONS
from .scenario import FIRST_NAMES, LAST_NAMES

L, W = 105.0, 68.0
HZ = 5
DT = 1.0 / HZ
POST = 3.66
MIRROR = np.array([L, W])
HALF = 45 * 60


def flip(k, p):
    """Absolute <-> team-relative coordinates (each team attacks +x in its own frame)."""
    return p if k == 0 else MIRROR - p


def unit(v):
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else np.zeros_like(v)


def lane_distance(points, a, b):
    """Distance of each point to segment a-b, and the projection parameter along it."""
    ab = b - a
    t = np.clip(((points - a) @ ab) / max(ab @ ab, 1e-9), 0.0, 1.0)
    proj = a + t[:, None] * ab
    return np.linalg.norm(points - proj, axis=1), t


def xg_model(dist, angle):
    """Logistic xG on distance and goal-mouth opening angle (radians)."""
    z = -1.45 - 0.11 * dist + 1.7 * angle
    return float(1.0 / (1.0 + math.exp(-z)))


def goal_angle(p):
    """Opening angle of the goal as seen from p (team-relative, goal at x=105)."""
    a = np.array([L, W / 2 + POST]) - p
    b = np.array([L, W / 2 - POST]) - p
    cos = (a @ b) / max(np.linalg.norm(a) * np.linalg.norm(b), 1e-9)
    return math.acos(max(-1.0, min(1.0, cos)))


class Match:
    def __init__(self, scenario, seed=1):
        self.sc = scenario
        self.seed = seed
        self.rng = np.random.default_rng(seed)
        self.params = [dict(t["params"]) for t in scenario["teams"]]
        self.formation = [t["formation"] for t in scenario["teams"]]
        self.timeline = sorted(scenario["timeline"], key=lambda e: e["t"])
        self.applied = []
        self._make_squads()

        self.pos = np.zeros((2, 11, 2))
        self.vel = np.zeros((2, 11, 2))
        self.noise = np.zeros((2, 11, 2))
        self.urgent = np.zeros((2, 11), dtype=bool)
        self.smooth_T = None
        self.run_timer = np.zeros((2, 11))
        self.vmax = self.rng.uniform(7.6, 8.9, size=(2, 11))
        self.vmax[:, 0] = 6.0

        self.ball = np.array([L / 2, W / 2])
        self.clock = 0.0
        self.period = 1
        self.score = [0, 0]
        self.poss = 0
        self.mode = "dead"
        self.carrier = None
        self.hold = 0.0
        self.dribble = np.array([1.0, 0.0])
        self.pressured = False
        self.flight = None
        self.restart = None
        self.loose = None
        self.last_pass = None

        self.events = []
        self.frames = []
        self.frame_clock = []
        self.frame_period = []
        self.frame_poss = []
        self.formation_log = [{"t": 0.0, "team": k, "formation": self.formation[k]} for k in (0, 1)]

    # ------------------------------------------------------------------ squads
    def _make_squads(self):
        names = self.rng.permutation(len(FIRST_NAMES))
        lasts = self.rng.permutation(len(LAST_NAMES))
        self.players = []
        self.slot = [[None] * 11 for _ in range(2)]
        self.bench = [[], []]
        i = 0
        for k, team in enumerate(self.sc["teams"]):
            roles = FORMATIONS[team["formation"]]["roles"]
            numbers = list(self.rng.permutation(np.arange(2, 40)))
            for n in range(16):
                pid = f"{team['short']}-{n + 1:02d}"
                number = 1 if n == 0 else int(numbers.pop())
                role = roles[n] if n < 11 else "SUB"
                self.players.append({
                    "id": pid, "team": k, "number": number, "start_role": role,
                    "name": f"{FIRST_NAMES[names[i % len(names)]]} {LAST_NAMES[lasts[i % len(lasts)]]}",
                })
                if n < 11:
                    self.slot[k][n] = pid
                else:
                    self.bench[k].append(pid)
                i += 1

    def pid(self, k, s):
        return self.slot[k][s]

    # ------------------------------------------------------------------ events
    def emit(self, etype, team=None, slot=None, **kw):
        ev = {"id": len(self.events) + 1, "t": round(self.clock, 1), "period": self.period,
              "frame": len(self.frames), "type": etype}
        if team is not None:
            ev["team"] = self.sc["teams"][team]["short"]
        if slot is not None:
            ev["player"] = self.pid(team, slot)
        for key, val in kw.items():
            if isinstance(val, np.ndarray):
                val = [round(float(v), 1) for v in val]
            elif isinstance(val, (float, np.floating)):
                val = round(float(val), 3)
            ev[key] = val
        self.events.append(ev)
        return ev

    # ---------------------------------------------------------------- timeline
    def apply_timeline(self):
        while self.timeline and self.timeline[0]["t"] <= self.clock:
            item = self.timeline.pop(0)
            k = item["team"]
            self.params[k].update(item.get("set", {}))
            for sub in item.get("subs", []):
                s = sub["slot"]
                if not self.bench[k]:
                    continue
                on = self.bench[k].pop(0)
                off = self.slot[k][s]
                self.slot[k][s] = on
                self.vmax[k, s] = self.rng.uniform(8.2, 9.0)  # fresh legs
                self.emit("substitution", team=k, slot=s, off=off, on=on)
            if "formation" in item:
                self.formation[k] = item["formation"]
                self.formation_log.append({"t": self.clock, "team": k, "formation": item["formation"]})
            self.applied.append({**item, "applied_at": round(self.clock, 1)})

    # ----------------------------------------------------------------- targets
    def offside_line(self, k):
        """Team-relative x of the second-last opponent (the offside line for team k)."""
        opp = flip(k, self.pos[1 - k])
        return max(np.sort(opp[:, 0])[-2], L / 2)

    def targets(self, k):
        p = self.params[k]
        f = FORMATIONS[self.formation[k]]
        R = flip(k, self.pos[k])
        b = flip(k, self.ball)
        in_poss = self.poss == k
        U = self.urgent[k]
        U[:] = False
        kickoff = self.mode == "dead" and self.restart and self.restart["kind"] == "kickoff"

        if in_poss and not kickoff:
            coords = f["att"]
            line = np.clip(p["att_line"] - 14 + 0.45 * (b[0] - L / 2), 16, p["att_line"] + 4)
            length, width = p["length_att"], p["width_att"]
            yc = W / 2 + 0.25 * (b[1] - W / 2)
        else:
            coords = f["def"]
            floor = min(9.0, max(3.0, b[0] - 4))
            line = np.clip(p["def_line"] - 8 + 0.45 * (b[0] - L / 2), floor, p["def_line"] + 4)
            length, width = p["length_def"], p["width_def"]
            if b[0] < 35:  # ball in our final third: narrow and compact around the box
                width *= 0.72
                length *= 0.8
            yc = W / 2 + 0.55 * (b[1] - W / 2)
        length = min(length, 101 - line)

        T = np.zeros((11, 2))
        for s in range(1, 11):
            d, lat = coords[s]
            T[s] = (line + d * length, yc + lat * width)

        groups = f["att_groups"] if in_poss else f["def_groups"]
        if in_poss and not kickoff:
            off = max(self.offside_line(k), b[0]) - 0.4
            for s in range(1, 11):
                if groups[s] in ("F", "A") and self.run_timer[k, s] <= 0 and b[0] > 45:
                    if self.rng.random() < 0.09 * DT * (1 + p["tempo"]):
                        self.run_timer[k, s] = self.rng.uniform(2.5, 5.0)
                if self.run_timer[k, s] > 0:
                    T[s, 0] = off + 0.2
                    U[s] = True
                    T[s, 1] += 0.3 * (W / 2 - T[s, 1])
            T[1:, 0] = np.minimum(T[1:, 0], off + 0.2)
        self.run_timer[k] -= DT

        # goalkeeper
        if in_poss:
            T[0] = (np.clip(8 + 0.12 * b[0], 6, 20), W / 2 + 0.15 * (b[1] - W / 2))
        else:
            T[0] = (np.clip(3 + 0.07 * b[0], 2.5, 12), W / 2 + 0.25 * (b[1] - W / 2) * (b[0] < 40))

        if kickoff:
            T[:, 0] = np.minimum(T[:, 0], L / 2 - 1.0)

        T[:, 1] = np.clip(T[:, 1], 1.5, W - 1.5)
        T += self.noise[k] * 0.9
        T[0] -= self.noise[k, 0] * 1.4

        # pressing: nearest defenders break out of the block
        if not in_poss and self.mode == "carry":
            d = np.linalg.norm(R[1:] - b, axis=1)
            order = np.argsort(d) + 1
            trigger = 7 + 16 * p["press"] + (8 if b[0] < 30 else 0)
            own_goal = np.array([0.0, W / 2])
            if d[order[0] - 1] < trigger:
                T[order[0]] = b + unit(own_goal - b) * 0.6
                U[order[0]] = True
            danger = b[0] < 35
            if b[0] < 45:  # man-marking inside our box: free defenders pick up attackers goal-side
                O = flip(k, self.pos[1 - k])
                threats = [j for j in np.argsort(O[:, 0]) if O[j, 0] < 22 and j != 0]
                free = [s for s in range(1, 11) if s not in order[:2]]
                for j in threats:
                    if not free:
                        break
                    dd = [np.linalg.norm(T[s] - O[j]) for s in free]
                    s = free.pop(int(np.argmin(dd)))
                    T[s] = O[j] + unit(own_goal - O[j]) * 1.2
                    U[s] = True
            if (p["press"] > 0.55 or danger) and d[order[1] - 1] < trigger * 0.85:
                T[order[1]] = b + unit(own_goal - b) * 4.0 + np.array([0, 3.0 * np.sign(W / 2 - b[1])])
                U[order[1]] = True
            if danger:  # a third defender stays goal-side of the ball
                T[order[2]] = b + unit(own_goal - b) * 7.0
                U[order[2]] = True

        # ball-related overrides
        if self.mode == "carry" and self.carrier[0] == k:
            s = self.carrier[1]
            T[s] = R[s] + self.dribble * 6.0
            U[s] = True
            T[s] = (np.clip(T[s, 0], 1.0, L - 2.0), np.clip(T[s, 1], 1.0, W - 1.0))
        if self.mode == "flight" and self.flight.get("chaser") and self.flight["chaser"][0] == k:
            T[self.flight["chaser"][1]] = flip(k, self.flight["end"])
            U[self.flight["chaser"][1]] = True
        if self.mode == "loose":
            s, _ = self.nearest(k, self.ball)
            T[s] = flip(k, self.ball)
            U[s] = True
        if self.mode == "dead" and self.restart["team"] == k and self.restart.get("taker") is not None:
            T[self.restart["taker"]] = flip(k, self.restart["pos"])
            U[self.restart["taker"]] = True
        return flip(k, T)

    # ---------------------------------------------------------------- movement
    def move(self):
        fatigue = 1.0 - 0.10 * (self.clock / (2 * HALF))
        for k in (0, 1):
            self.noise[k] += -self.noise[k] * DT / 4.0 + math.sqrt(DT) * 0.25 * self.rng.standard_normal((11, 2))
            T = self.targets(k)
            # off the ball, players react with a delay and jog into position;
            # only urgent jobs (press, mark, chase, carry, run) get full speed
            if self.smooth_T is None:
                self.smooth_T = np.stack([self.pos[0].copy(), self.pos[1].copy()])
            U = self.urgent[k]
            self.smooth_T[k][~U] += (T[~U] - self.smooth_T[k][~U]) * (DT / 2.2)
            self.smooth_T[k][U] = T[U]
            T = self.smooth_T[k]
            tired = fatigue - 0.06 * self.params[k]["press"] * (self.clock / (2 * HALF))
            tau = np.where(U, 0.6, 3.2)[:, None]
            desired = (T - self.pos[k]) / tau
            cap = self.vmax[k] * tired
            if self.mode == "carry" and self.carrier[0] == k:
                s = self.carrier[1]
                ahead = np.linalg.norm(self.pos[1 - k] - self.pos[k, s], axis=1).min()
                cap = cap.copy()
                cap[s] = min(cap[s], 3.5 if ahead < 4 else 5.2)
            speed = np.linalg.norm(desired, axis=1)
            scale = np.minimum(1.0, cap / np.maximum(speed, 1e-9))
            desired *= scale[:, None]
            acc = (desired - self.vel[k]) / DT
            amag = np.linalg.norm(acc, axis=1)
            acc *= np.minimum(1.0, 4.5 / np.maximum(amag, 1e-9))[:, None]
            self.vel[k] += acc * DT
            self.pos[k] += self.vel[k] * DT
            self.pos[k, :, 0] = np.clip(self.pos[k, :, 0], -1, L + 1)
            self.pos[k, :, 1] = np.clip(self.pos[k, :, 1], -1, W + 1)

    # ------------------------------------------------------------ possession
    def give(self, k, s, hold=None):
        """Player s of team k now has the ball at their feet."""
        if self.poss != k and self.mode != "dead":
            self.last_pass = None
        self.poss = k
        self.mode = "carry"
        self.carrier = (k, s)
        self.pressured = False
        tempo = self.params[k]["tempo"]
        if hold is None:
            hold = self.rng.gamma(2.2, 1.35) * (1.25 - 0.6 * tempo)
            if s == 0:
                hold += self.rng.uniform(1.0, 3.0)
        self.hold = hold
        R = flip(k, self.pos[k, s])
        a = self.rng.normal(0, 0.55) + 0.3 * self.params[k]["channel"]
        d = np.array([math.cos(a), math.sin(a)])
        if R[1] < 6:
            d[1] = abs(d[1])
        if R[1] > W - 6:
            d[1] = -abs(d[1])
        self.dribble = d

    def dead_ball(self, k, pos, kind, delay):
        pos = np.array([np.clip(pos[0], 0, L), np.clip(pos[1], 0, W)], dtype=float)
        self.mode = "dead"
        self.poss = k
        self.carrier = None
        if kind == "goal_kick":
            taker = 0
        elif kind == "kickoff":
            roles = FORMATIONS[self.formation[k]]["roles"]
            taker = roles.index("ST") if "ST" in roles else roles.index("LST")
        else:
            d = np.linalg.norm(self.pos[k, 1:] - pos, axis=1)
            taker = int(np.argmin(d)) + 1
        self.restart = {"team": k, "pos": pos, "kind": kind, "timer": delay, "taker": taker}

    def nearest(self, k, point, exclude_gk=True):
        start = 1 if exclude_gk else 0
        d = np.linalg.norm(self.pos[k, start:] - point, axis=1)
        i = int(np.argmin(d))
        return i + start, float(d[i])

    # ---------------------------------------------------------------- decisions
    def decide(self):
        k, s = self.carrier
        opp = 1 - k
        p = self.params[k]
        c = flip(k, self.pos[k, s])
        O = flip(k, self.pos[opp])
        dmin = float(np.min(np.linalg.norm(O - c, axis=1)))
        goal = np.array([L, W / 2])
        dist = float(np.linalg.norm(goal - c))

        if c[0] < 22 and dmin < 5 and s != 0 and abs(c[1] - W / 2) < 26:
            return self.clearance(c)

        if c[0] > 75 and dist < 30 and s != 0:
            xg = xg_model(dist, goal_angle(c))
            lane_d, lane_t = lane_distance(O[1:], c, goal)
            blockers = int(np.sum((lane_d < 1.2) & (lane_t > 0.02)))
            p_shoot = 0.5 * min(0.5, (0.10 if dist < 26 else 0.02) + 1.2 * xg)
            p_shoot *= (0.7 if dmin < 2 else 1.0) * (0.6 ** blockers)
            if self.rng.random() < p_shoot:
                return self.shoot(xg, c, dist, dmin, blockers)

        R = flip(k, self.pos[k])
        cand, scores, info = [], [], []
        for t in range(11):
            if t == s or (t == 0 and c[0] > 32):
                continue
            tp = R[t]
            d = float(np.linalg.norm(tp - c))
            if d < 4:
                continue
            if c[0] > L - 6 and tp[0] > L - 8:
                continue  # on the byline: cut it back or cross it, never pass along the line
            prog = tp[0] - c[0]
            space = float(np.min(np.linalg.norm(O - tp, axis=1)))
            lane = float(np.min(lane_distance(O, c, tp)[0]))
            risk = math.exp(-lane / 1.3) + max(0.0, (d - 30) / 40)
            score = (0.045 * prog * (0.5 + p["tempo"]) + 0.10 * min(space, 8) - 0.035 * max(0, d - 18)
                     - 2.2 * risk + 0.25 * p["channel"] * (tp[1] - W / 2) / (W / 2)
                     + (0.6 if self.run_timer[k, t] > 0 and prog > 0 else 0.0))
            if c[0] > 60 and prog < -25:
                score -= 1.5
            cand.append(t)
            scores.append(score)
            info.append((tp, d, risk, lane))
        if not cand:
            self.hold = 0.6
            return
        w = np.exp((np.array(scores) - max(scores)) / 0.35)
        j = int(self.rng.choice(len(cand), p=w / w.sum()))
        t, (tp, d, risk, lane) = cand[j], info[j]

        p_ok = p["quality"] + 0.16 - 0.0045 * max(0, d - 12) - 0.3 * min(risk, 1.0) - (0.08 if dmin < 2.5 else 0)
        space = float(np.min(np.linalg.norm(O - tp, axis=1)))
        if tp[0] > L - 16.5 and abs(tp[1] - W / 2) < 20.16:
            # balls into the box: crowded, contested, the keeper comes for them
            p_ok *= 0.5 if space < 2.5 else 0.68
            if tp[0] > L - 6:
                p_ok *= 0.6
        p_ok = float(np.clip(p_ok, 0.12, 0.97))
        dur = d / (13 + 0.22 * d)
        lead = (self.vel[k, t] if k == 0 else -self.vel[k, t]) * dur * 0.5
        end_rel = tp + lead
        difficulty = round(min(1.0, (1 - p_ok) * 1.6 + max(0, d - 25) / 60), 2)
        start_abs = self.ball.copy()
        base = dict(start=start_abs, distance=round(d, 1), difficulty=difficulty,
                    under_pressure=dmin < 3.0, receiver=self.pid(k, t))

        if self.rng.random() < p_ok:
            end = flip(k, end_rel)
            self.flight = {"start": start_abs, "end": end, "t": 0.0, "dur": dur, "kind": "pass",
                           "result": "receive", "team": k, "slot": t, "chaser": (k, t), "passer": s}
            self.emit("pass", team=k, slot=s, end=end, outcome="complete", **base)
        else:
            r = self.rng.random()
            dl, tl = lane_distance(O, c, tp)
            jj = int(np.argmin(dl))
            if r < 0.55 and dl[jj] < 6:
                end_rel_i = c + (tp - c) * tl[jj]
                end = flip(k, end_rel_i)
                self.flight = {"start": start_abs, "end": end, "t": 0.0, "dur": max(0.3, dur * tl[jj]),
                               "kind": "pass", "result": "intercept", "team": opp, "slot": jj,
                               "chaser": (opp, jj)}
                self.emit("pass", team=k, slot=s, end=end, outcome="intercepted",
                          intercepted_by=self.pid(opp, jj), **base)
            else:
                over = unit(tp - c) * self.rng.uniform(8, 22) + self.rng.normal(0, 6, 2)
                end_rel_o = tp + over
                end = flip(k, end_rel_o)
                out = not (0 <= end[0] <= L and 0 <= end[1] <= W)
                self.flight = {"start": start_abs, "end": end, "t": 0.0, "dur": dur * 1.25,
                               "kind": "pass", "result": "out" if out else "loose", "team": k}
                self.emit("pass", team=k, slot=s, end=end, outcome="out" if out else "overhit", **base)
        self.mode = "flight"

    def clearance(self, c):
        """Under pressure near our own goal: no risks, kick it long and wide."""
        k, s = self.carrier
        end_rel = np.array([self.rng.uniform(40, 70), c[1] + self.rng.normal(0, 18)])
        end = flip(k, end_rel)
        out = not (0 <= end[1] <= W)
        self.emit("clearance", team=k, slot=s, start=self.ball.copy(), end=end, outcome="out" if out else "loose")
        dist = float(np.linalg.norm(end - self.ball))
        self.flight = {"start": self.ball.copy(), "end": end, "t": 0.0, "dur": dist / 24,
                       "kind": "pass", "result": "out" if out else "loose", "team": k}
        self.mode = "flight"

    def shoot(self, xg, c, dist, dmin, blockers=0):
        k, s = self.carrier
        opp = 1 - k
        finish = self.params[k]["finish"]
        r = self.rng.random()
        if self.rng.random() < 1 - 0.55 ** blockers:
            outcome = "blocked"
            y = None
        elif r < xg * finish:
            outcome = "goal"
            y = W / 2 + self.rng.uniform(-POST + 0.4, POST - 0.4)
        else:
            r2 = self.rng.random()
            if r2 < 0.3:
                outcome = "saved"
                y = W / 2 + self.rng.uniform(-POST + 0.5, POST - 0.5)
            else:
                outcome = "off_target"
                y = W / 2 + self.rng.choice([-1, 1]) * (POST + self.rng.uniform(0.4, 5))
        if outcome == "blocked":
            end_rel = c + unit(np.array([L, W / 2]) - c) * self.rng.uniform(1.5, 4)
        else:
            end_rel = np.array([L if outcome != "saved" else L - 1.0, y])
        end = flip(k, end_rel)
        speed = self.rng.uniform(22, 31)
        self.emit("shot", team=k, slot=s, start=self.ball.copy(), end=end, xg=xg, outcome=outcome,
                  distance=round(dist, 1), shot_speed_kmh=round(speed * 3.6, 1), under_pressure=dmin < 2.0)
        self.flight = {"start": self.ball.copy(), "end": end, "t": 0.0,
                       "dur": max(0.25, float(np.linalg.norm(end - self.ball)) / speed),
                       "kind": "shot", "result": outcome, "team": k, "slot": s}
        if outcome == "saved":
            self.flight["chaser"] = (opp, 0)
        self.mode = "flight"

    # ------------------------------------------------------------- resolution
    def resolve_flight(self):
        f = self.flight
        k = f["team"]
        res = f["result"]
        end = f["end"]
        if res == "receive":
            self.last_pass = (k, f["passer"], f["slot"], self.clock)
            self.collect(k, f["slot"], end)
        elif res == "intercept":
            self.emit("interception", team=k, slot=f["slot"], at=end)
            self.collect(k, f["slot"], end)
        elif res == "loose":
            self.loose_ball(end, prev_team=k)
        elif res == "out":
            self.out_of_play(end, last_team=k)
        elif res == "goal":
            self.score[k] += 1
            assist = None
            if self.last_pass and self.last_pass[0] == k and self.clock - self.last_pass[3] < 12:
                assist = self.pid(k, self.last_pass[1])
            self.emit("goal", team=k, slot=f["slot"], assist=assist, score=list(self.score))
            self.dead_ball(1 - k, np.array([L / 2, W / 2]), "kickoff", self.rng.uniform(35, 50))
        elif res == "saved":
            opp = 1 - k
            self.emit("save", team=opp, slot=0)
            if self.rng.random() < 0.35:
                self.corner(k)
            else:
                self.ball = self.pos[opp, 0].copy()
                self.give(opp, 0)
        elif res == "off_target":
            opp = 1 - k
            gx = 5.5 if opp == 0 else L - 5.5
            self.dead_ball(opp, np.array([gx, W / 2 + self.rng.uniform(-8, 8)]), "goal_kick",
                           self.rng.uniform(12, 20))
        elif res == "blocked":
            self.emit("block", team=1 - k, slot=self.nearest(1 - k, end)[0])
            if self.rng.random() < 0.3:
                self.corner(k)
            else:
                self.loose_ball(end, prev_team=k)
        self.flight = None

    def corner(self, k):
        """Corner for team k at the end it attacks."""
        x = L if k == 0 else 0.0
        y = 0.0 if self.rng.random() < 0.5 else W
        self.emit("out", team=1 - k, at=np.array([x, y]), restart="corner")
        self.dead_ball(k, np.array([x, y]), "corner", self.rng.uniform(14, 22))

    def collect(self, k, s, point):
        """Ball arrives at point: the intended player takes it if close, else it's a loose ball."""
        self.ball = point.copy()
        if np.linalg.norm(self.pos[k, s] - point) < 2.5:
            self.give(k, s)
        else:
            self.loose_ball(point, prev_team=k)

    def loose_ball(self, point, prev_team):
        """Ball sits loose; the nearest player of each team chases it (see loose_step)."""
        self.ball = point.copy()
        self.mode = "loose"
        self.carrier = None
        self.loose = {"prev": prev_team, "timer": 0.0}

    def loose_step(self):
        self.loose["timer"] += DT
        best = None
        for kk in (0, 1):
            s, d = self.nearest(kk, self.ball, exclude_gk=False)
            if d < 1.2 and (best is None or d < best[2]):
                best = (kk, s, d)
        if best is None and self.loose["timer"] > 6:
            kk = min((0, 1), key=lambda t: self.nearest(t, self.ball, exclude_gk=False)[1])
            s, _ = self.nearest(kk, self.ball, exclude_gk=False)
            best = (kk, s, 0)
        if best:
            kk, s, _ = best
            prev = self.loose["prev"]
            self.give(kk, s)
            if kk != prev:
                self.emit("recovery", team=kk, slot=s, at=self.ball.copy())

    def out_of_play(self, point, last_team):
        k = 1 - last_team
        x, y = point
        if 0 <= x <= L:
            kind, pos = "throw_in", (x, 0.0 if y < 0 else W)
        else:
            own_goal_x = 0.0 if k == 0 else L
            if abs(x - own_goal_x) < 10:
                kind, pos = "goal_kick", (5.5 if k == 0 else L - 5.5, W / 2)
            else:
                kind = "corner"
                pos = (L if x > L / 2 else 0.0, 0.0 if y < W / 2 else W)
        self.emit("out", team=last_team, at=np.array(point), restart=kind)
        self.dead_ball(k, np.array(pos), kind, self.rng.uniform(9, 16) if kind != "corner" else self.rng.uniform(16, 25))

    # -------------------------------------------------------------- ball loop
    def ball_step(self):
        if self.mode == "carry":
            k, s = self.carrier
            v = self.vel[k, s]
            self.ball = self.pos[k, s] + unit(v) * 0.5
            opp = 1 - k
            d = np.linalg.norm(self.pos[opp] - self.pos[k, s], axis=1)
            j = int(np.argmin(d))
            dmin = float(d[j])
            if dmin < 3.0 and not self.pressured:
                self.pressured = True
                self.emit("pressure", team=opp, slot=j, on=self.pid(k, s))
            if dmin < 1.7 and s != 0:
                hazard = 0.2 * (0.5 + 0.5 * self.params[opp]["press"])
                if self.rng.random() < hazard * DT:
                    if self.rng.random() < 0.15:
                        self.emit("foul", team=opp, slot=j, on=self.pid(k, s), at=self.ball.copy())
                        if self.rng.random() < 0.13:
                            self.emit("card", team=opp, slot=j, card="yellow")
                        self.dead_ball(k, self.ball.copy(), "free_kick", self.rng.uniform(12, 22))
                    elif self.rng.random() < 0.22:
                        # tackle knocks it out of play: throw-in to the carrier's team
                        self.emit("tackle", team=opp, slot=j, on=self.pid(k, s), at=self.ball.copy(), outcome="out")
                        side = 0.0 if self.ball[1] < W / 2 else W
                        self.dead_ball(k, np.array([self.ball[0], side]), "throw_in", self.rng.uniform(8, 14))
                    else:
                        self.emit("tackle", team=opp, slot=j, on=self.pid(k, s), at=self.ball.copy(), outcome="won")
                        self.give(opp, j)
                    return
            self.hold -= DT * (2.0 if dmin < 4 else 1.0)
            if flip(k, self.pos[k, s])[0] > L - 4:  # run out of pitch: must play it now
                self.hold = 0
            if self.hold <= 0:
                self.decide()
        elif self.mode == "flight":
            f = self.flight
            f["t"] += DT
            a = min(1.0, f["t"] / f["dur"])
            self.ball = f["start"] + (f["end"] - f["start"]) * a
            if a >= 1.0:
                self.resolve_flight()
        elif self.mode == "loose":
            self.loose_step()
        elif self.mode == "dead":
            r = self.restart
            self.ball = r["pos"].copy()
            r["timer"] -= DT
            k = r["team"]
            if r["kind"] in ("throw_in", "free_kick", "corner"):
                r["taker"] = int(np.argmin(np.linalg.norm(self.pos[k, 1:] - r["pos"], axis=1))) + 1
            s = r["taker"]
            close = np.linalg.norm(self.pos[k, s] - r["pos"]) < 1.5
            if r["timer"] <= 0 and (close or r["timer"] < -15):
                self.pos[k, s] = r["pos"].copy()
                self.emit("restart", team=k, slot=s, kind=r["kind"], at=r["pos"])
                self.restart = None
                self.give(k, s, hold=self.rng.uniform(0.3, 0.9))

    # ------------------------------------------------------------------- run
    def kickoff_positions(self, k):
        self.mode = "dead"
        self.dead_ball(k, np.array([L / 2, W / 2]), "kickoff", 1.0)
        for kk in (0, 1):
            self.pos[kk] = self.targets(kk)
            self.vel[kk] = 0
        self.smooth_T = np.stack([self.pos[0].copy(), self.pos[1].copy()])

    def run(self):
        stoppage = self.sc.get("stoppage", [120, 240])
        for period in (1, 2):
            self.period = period
            self.clock = 0.0 if period == 1 else float(HALF)
            end = self.clock + HALF + stoppage[period - 1]
            self.kickoff_positions(0 if period == 1 else 1)
            self.emit("period_start")
            while self.clock < end or self.mode == "flight":
                self.apply_timeline()
                self.move()
                self.ball_step()
                self.record()
                self.clock += DT
            self.emit("period_end", score=list(self.score))
        return self

    def record(self):
        frame = np.vstack([self.pos[0], self.pos[1], self.ball[None, :]])
        self.frames.append(frame.astype(np.float32))
        self.frame_clock.append(self.clock)
        self.frame_period.append(self.period)
        self.frame_poss.append(self.poss if self.mode != "dead" else -1)

    # ------------------------------------------------------------------ output
    def meta(self):
        teams = []
        for k, t in enumerate(self.sc["teams"]):
            teams.append({k2: t[k2] for k2 in ("name", "short", "color", "alt", "formation")})
        return {
            "match_id": self.sc["match_id"], "competition": self.sc.get("competition"),
            "venue": self.sc.get("venue"), "seed": self.seed, "hz": HZ,
            "pitch": {"length": L, "width": W},
            "home_attacks": "+x",
            "teams": teams,
            "players": self.players,
            "tracking_columns": [f"{self.sc['teams'][0]['short']}:slot{s}" for s in range(11)]
                                + [f"{self.sc['teams'][1]['short']}:slot{s}" for s in range(11)] + ["ball"],
            "formations": {name: {k: v for k, v in f.items() if k in ("roles", "att_groups", "def_groups")}
                           for name, f in FORMATIONS.items()},
            "formation_log": self.formation_log,
            "final_score": self.score,
        }

    def truth(self):
        return {"match_id": self.sc["match_id"], "tactical_changes": self.applied}
