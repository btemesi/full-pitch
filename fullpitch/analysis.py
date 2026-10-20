"""Shape & stats layer: turns raw tracking + events into football meaning.

Everything here is plain, deterministic Python. No LLM touches a number: the
agents later read these results and explain them, they never compute them.

Three outputs:
  minute_metrics()   team shape per minute, split by phase (with / without ball)
  player_physicals() distance, top speed, sprints, high-intensity running
  detect_changes()   sustained shifts in team shape = tactical changes,
                     found from movement alone (no access to the coaches' plan)

Coordinates: team-relative, metres, own goal at x=0, attacking +x, y=left positive.
Teams change ends at half-time, so "team-relative" depends on the period.
"""

import numpy as np

L, W = 105.0, 68.0
SPRINT = 7.0          # m/s  (25.2 km/h)
SPRINT_MIN_S = 1.0    # a sprint must last at least this long
SETTLED_S = 5.0       # possession counts as settled after this long
HIGH_INTENSITY = 5.5  # m/s  (19.8 km/h)


def attacks_plus_x(k, period):
    """Home (k=0) attacks +x in the first half, -x in the second; away the opposite."""
    return (k == 0) == (int(period) == 1)


def team_rel(xy, k, period=1):
    """Team-relative coordinates for team k. `period` may be a scalar or one value per row of xy."""
    period = np.asarray(period)
    if period.ndim == 0:
        return xy if attacks_plus_x(k, period) else np.array([L, W]) - xy
    keep = np.array([attacks_plus_x(k, p) for p in period])
    shape = (-1,) + (1,) * (xy.ndim - 1)
    return np.where(keep.reshape(shape), xy, np.array([L, W]) - xy)


class MatchData:
    def __init__(self, tracking, meta, events):
        self.xy = tracking["xy"].astype(np.float64)      # (F, 23, 2)
        self.clock = tracking["clock"]
        self.period = tracking["period"]
        self.poss = tracking["poss"]
        self.meta = meta
        self.events = events
        self.hz = meta["hz"]
        self.F = len(self.clock)
        self.shorts = [t["short"] for t in meta["teams"]]
        # continuous match time in minutes (no half-time gap), used for windows
        self.tmin = np.arange(self.F) / self.hz / 60.0
        self.active = self._active_mask()

    def _active_mask(self):
        """(F, 22) bool: False for a slot after its player was sent off."""
        act = np.ones((self.F, 22), dtype=bool)
        occ = slot_occupancy(self)
        for e in self.events:
            if e["type"] == "card" and e.get("card") == "red":
                k = self.shorts.index(e["team"])
                for s in range(11):
                    if any(pid == e["player"] and a <= e["frame"] < b for pid, a, b in occ[k][s]):
                        act[e["frame"]:, k * 11 + s] = False
        return act

    def team(self, k):
        return team_rel(self.xy[:, k * 11:(k + 1) * 11], k, self.period)

    def ball(self, k):
        return team_rel(self.xy[:, 22], k, self.period)

    def clock_label(self, frame):
        c = float(self.clock[frame])
        p = int(self.period[frame])
        base = 0 if p == 1 else 45 * 60
        extra = c - base - 45 * 60
        if extra > 0:
            return f"{45 * p}+{int(extra // 60) + 1}'"
        return f"{int(c // 60) + 1}'"


# ---------------------------------------------------------------- frame shape
BALL_SHIFT = 0.45  # how far a typical block slides per metre of ball movement


def frame_shape(P, ball_x, active=None):
    """Shape metrics for one team in one frame. P: (11, 2) team-relative, slot 0 = GK.
    active: optional (11,) mask; sent-off players are left out."""
    out = P[1:] if active is None else P[1:][active[1:]]
    x = out[:, 0]
    order = np.argsort(x)
    deepest = x[order[0]]
    back = x < deepest + 5.0
    return {
        "def_line": float(x[back].mean()),
        # line height with the ball's position factored out: the coach's chosen height
        "line_adj": float(x[back].mean() - BALL_SHIFT * (ball_x - L / 2)),
        "n_back": int(back.sum()),
        "length": float(x[order[-1]] - deepest),
        "width": float(out[:, 1].max() - out[:, 1].min()),
    }


# -------------------------------------------------------------- minute metrics
def minute_metrics(md):
    """Per-minute team shape, split by phase. Returns list of dicts, one per minute."""
    n_min = int(np.ceil(md.tmin[-1]))
    teams = [md.team(k) for k in (0, 1)]
    balls = [md.ball(k) for k in (0, 1)]
    keys = ["def_line", "line_adj", "n_back", "length", "width"]
    acc = {(k, ph, m): {key: [] for key in keys + ["pressers"]}
           for k in (0, 1) for ph in ("att", "def") for m in range(n_min)}
    # how long the current possession has lasted: shape "with the ball" is read from settled possession
    age = np.zeros(md.F)
    for f in range(1, md.F):
        age[f] = age[f - 1] + 1 if md.poss[f] == md.poss[f - 1] and md.poss[f] >= 0 else 0
    settled = age >= SETTLED_S * md.hz
    step = 1
    for f in range(0, md.F, step):
        pk = md.poss[f]
        if pk < 0:
            continue
        m = int(md.tmin[f])
        for k in (0, 1):
            ph = "att" if pk == k else "def"
            s = frame_shape(teams[k][f], balls[k][f, 0], md.active[f, k * 11:(k + 1) * 11])
            a = acc[(k, ph, m)]
            for key in keys:
                if ph == "att" and not settled[f]:
                    continue  # a team needs a few seconds to open up after winning the ball
                if key == "line_adj" and not (35 < balls[k][f, 0] < 85):
                    continue  # elsewhere the box or the coach's cap pins the line, hiding the instruction
                a[key].append(s[key])
            if ph == "def":
                act = md.active[f, k * 11 + 1:(k + 1) * 11]
                d = np.linalg.norm(teams[k][f, 1:][act] - balls[k][f], axis=1)
                a["pressers"].append(int((d < 10).sum()))

    # event-based: final-third entries by channel (left / centre / right, from the attacking team's view)
    entries = {(k, m): [0, 0, 0] for k in (0, 1) for m in range(n_min)}
    for e in md.events:
        if e.get("team") is None or e["type"] != "pass" or e.get("outcome") != "complete":
            continue
        k = md.shorts.index(e["team"])
        m = min(int(e["frame"] / md.hz / 60), n_min - 1)
        a = team_rel(np.array(e["start"]), k, e["period"])
        b = team_rel(np.array(e["end"]), k, e["period"])
        if b[0] > 70 and a[0] <= 70:
            lane = 0 if b[1] > 45 else (2 if b[1] < 23 else 1)
            entries[(k, m)][lane] += 1

    rows = []
    for m in range(n_min):
        f0 = min(int(m * 60 * md.hz), md.F - 1)
        row = {"minute": m, "clock": md.clock_label(f0), "teams": {}}
        for k in (0, 1):
            t = {}
            for ph in ("att", "def"):
                a = acc[(k, ph, m)]
                t[ph] = {key: (round(float(np.mean(v)), 2) if v else None) for key, v in a.items()
                         if ph == "def" or key != "pressers"}
                t[ph]["samples"] = len(a["def_line"])
                t[ph]["n"] = {key: len(v) for key, v in a.items()}
            t["entries"] = dict(zip(("left", "centre", "right"), entries[(k, m)]))
            row["teams"][md.shorts[k]] = t
        rows.append(row)
    return rows


# ------------------------------------------------------------ player physicals
def player_physicals(md, window=None):
    """Distance, top speed, sprints, high-intensity distance per player.

    window: optional (frame_from, frame_to) to measure only part of the match.
    Slots are mapped to player ids through substitution events.
    """
    f_from, f_to = window or (0, md.F)
    if f_to - f_from < 2:
        return {}
    occupancy = slot_occupancy(md)
    xy = md.xy[f_from:f_to, :22]
    # tracking providers smooth positions before measuring running; we use a 1 s moving average,
    # padded with the edge values so the window borders don't invent movement
    kernel = np.ones(md.hz) / md.hz
    h = md.hz // 2
    flat = np.pad(xy.reshape(len(xy), -1), ((h, md.hz - 1 - h), (0, 0)), mode="edge")
    xs = np.apply_along_axis(lambda c: np.convolve(c, kernel, mode="valid"), 0, flat).reshape(xy.shape)
    v = np.linalg.norm(np.diff(xs, axis=0), axis=2) * md.hz          # (F-1, 22) m/s
    v[v > 9.6] = 0.0  # faster than any human (34.5 km/h+): the half-time change of ends, not running
    v[~md.active[f_from + 1:f_to, :22]] = 0.0  # a sent-off player is not running
    vs = v
    min_len = int(SPRINT_MIN_S * md.hz)
    stats = {}
    for col in range(22):
        k, s = divmod(col, 11)
        for pid, a, b in occupancy[k][s]:
            a2, b2 = max(a, f_from) - f_from, min(b, f_to) - f_from - 1
            if b2 <= a2:
                continue
            seg = vs[a2:b2, col]
            raw = v[a2:b2, col]
            on = md.active[f_from + 1 + a2:f_from + 1 + b2, col]  # minutes on the pitch only
            starts = count_runs(seg > SPRINT, min_len)
            st = stats.setdefault(pid, {"distance_m": 0.0, "top_speed_kmh": 0.0, "sprints": 0,
                                        "hi_distance_m": 0.0, "minutes": 0.0})
            st["distance_m"] += float(raw.sum() / md.hz)
            st["hi_distance_m"] += float(raw[seg > HIGH_INTENSITY].sum() / md.hz)
            st["top_speed_kmh"] = max(st["top_speed_kmh"], float(seg.max() * 3.6) if len(seg) else 0.0)
            st["sprints"] += starts
            st["minutes"] += float(on.sum()) / md.hz / 60
    for st in stats.values():
        for key in ("distance_m", "hi_distance_m", "top_speed_kmh", "minutes"):
            st[key] = round(st[key], 1)
    return stats


def lines_by_gaps(x, n_lines=3):
    """Split players into lines by depth: cut at the (n_lines-1) largest gaps between neighbours.
    Returns a line index per player (0 = deepest). Uses positions only, never the coach's plan."""
    order = np.argsort(x)
    xs = x[order]
    gaps = np.diff(xs)
    cuts = sorted(np.argsort(gaps)[-(n_lines - 1):]) if len(xs) > n_lines else []
    label = np.zeros(len(x), dtype=int)
    line = 0
    for i, idx in enumerate(order):
        label[idx] = line
        if line < len(cuts) and i == cuts[line]:
            line += 1
    return label


def count_runs(mask, min_len):
    """Number of runs of True that last at least min_len samples."""
    n, run = 0, 0
    for v in mask:
        run = run + 1 if v else 0
        if run == min_len:
            n += 1
    return n


def slot_occupancy(md):
    """For each team and slot: list of (player_id, frame_from, frame_to)."""
    occ = []
    for k, short in enumerate(md.shorts):
        slots = []
        for s in range(11):
            pid = f"{short}-{s + 1:02d}"
            spans, start = [], 0
            for e in md.events:
                if e["type"] == "substitution" and e["team"] == short and e["off"] == pid:
                    spans.append((pid, start, e["frame"]))
                    pid, start = e["on"], e["frame"]
            spans.append((pid, start, md.F))
            slots.append(spans)
        occ.append(slots)
    return occ


# ------------------------------------------------------------ change detection
# (metric path, phase, label, unit) — the shape signals a coach actually changes
SIGNALS = [
    ("def", "line_adj", "defensive line height without the ball", "m"),
    ("def", "length", "team length without the ball", "m"),
    ("def", "width", "team width without the ball", "m"),
    ("def", "n_back", "players in the back line without the ball", ""),
    ("def", "pressers", "players pressing near the ball", ""),
    ("att", "n_back", "players in the back line in possession", ""),  # back three vs back four
    ("att", "line_adj", "defensive line height in possession", "m"),
]


def series(rows, short, phase, key):
    vals = [r["teams"][short][phase].get(key) for r in rows]
    return np.array([np.nan if v is None else v for v in vals], dtype=float)


def weights(rows, short, phase, key):
    return np.array([r["teams"][short][phase].get("n", {}).get(key, 1) for r in rows], dtype=float)


def channel_scores(rows, short, win=12, min_n=8):
    """Shift in the share of final-third entries down the left: a two-proportion z-test of the
    window before against the window after each minute. Returns (score, before_share, after_share)."""
    left = np.array([r["teams"][short]["entries"]["left"] for r in rows], dtype=float)
    tot = np.array([sum(r["teams"][short]["entries"].values()) for r in rows], dtype=float)
    n = len(rows)
    score, pb, pa = np.zeros(n), np.full(n, np.nan), np.full(n, np.nan)
    for t in range(win, n - win // 2):
        lb, nb = left[t - win:t].sum(), tot[t - win:t].sum()
        la, na = left[t:t + win].sum(), tot[t:t + win].sum()
        if nb < min_n or na < min_n:
            continue
        p_b, p_a, p = lb / nb, la / na, (lb + la) / (nb + na)
        se = np.sqrt(max(p * (1 - p), 1e-6) * (1 / nb + 1 / na))
        score[t], pb[t], pa[t] = abs(p_a - p_b) / se, p_b, p_a
    return score, pb, pa


def shift_scores(x, win=12, w=None):
    """For each minute t: (mean after - mean before) / standard error, over +-win minutes.

    Minutes are weighted by how many samples they hold (w); the noise level is
    estimated from how much minutes scatter given their sample counts."""
    n = len(x)
    w = np.ones(n) if w is None else w
    ok = ~np.isnan(x) & (w > 0)
    score = np.zeros(n)
    delta = np.zeros(n)
    # sample-level variance implied by minute-to-minute scatter: var(minute mean) ~ s2 / n_i
    xi, wi = x[ok], w[ok]
    m = np.convolve(xi, np.ones(5) / 5, mode="same")
    s2 = float(np.sum(wi * (xi - m) ** 2) / max(len(xi) - 1, 1)) + 1e-9
    for t in range(win, n - win // 2):
        bs, as_ = slice(t - win, t), slice(t, t + win)
        bo, ao = ok[bs], ok[as_]
        if bo.sum() < win // 2 or ao.sum() < win // 4:
            continue
        bx, bw = x[bs][bo], w[bs][bo]
        ax, aw = x[as_][ao], w[as_][ao]
        delta[t] = np.average(ax, weights=aw) - np.average(bx, weights=bw)
        se = np.sqrt(s2 / aw.sum() + s2 / bw.sum())
        score[t] = abs(delta[t]) / se
    return score, delta


def detect_changes(rows, threshold=3.0, strong=4.0, merge_within=8, start_minute=6):
    """Find sustained shape shifts per team and merge co-occurring signals into one change."""
    shorts = list(rows[0]["teams"].keys())
    found = []
    for short in shorts:
        hits = []
        sigs = [(ph, key, label, unit, series(rows, short, ph, key), weights(rows, short, ph, key))
                for ph, key, label, unit in SIGNALS]
        csc, cpb, cpa = channel_scores(rows, short)
        for t in range(start_minute, len(rows)):
            if csc[t] >= threshold and csc[t] >= csc[max(0, t - 2):t + 3].max():
                hits.append({"minute": t, "signal": "left_share", "phase": "att",
                             "label": "share of attacks down their left", "unit": "%",
                             "before": round(float(cpb[t] * 100), 1), "after": round(float(cpa[t] * 100), 1),
                             "strength": round(float(csc[t]), 1)})
        for ph, key, label, unit, x, w in sigs:
            sc, dl = shift_scores(x, w=w)
            for t in range(start_minute, len(x)):
                if sc[t] < threshold:
                    continue
                if sc[t] < sc[max(0, t - 2):t + 3].max():
                    continue  # keep local peaks only
                before = np.nanmean(x[max(0, t - 12):t])
                after = np.nanmean(x[t:t + 12])
                hits.append({"minute": t, "signal": key, "phase": ph, "label": label, "unit": unit,
                             "before": round(float(before), 1), "after": round(float(after), 1),
                             "strength": round(float(sc[t]), 1)})
        hits.sort(key=lambda h: h["minute"])
        groups = []
        for h in hits:
            if groups and h["minute"] - groups[-1][-1]["minute"] <= merge_within:
                groups[-1].append(h)
            else:
                groups.append([h])
        for g in groups:
            g.sort(key=lambda h: -h["strength"])
            seen = set()
            g = [h for h in g if not ((h["signal"], h["phase"]) in seen or seen.add((h["signal"], h["phase"])))]
            # one strong signal, or two independent signals agreeing
            if g[0]["strength"] < strong and len({h["signal"] + h["phase"] for h in g}) < 2:
                continue
            m = g[0]["minute"]  # timing from the clearest signal
            found.append({"team": short, "minute": m, "clock": rows[m]["clock"],
                          "strength": g[0]["strength"], "signals": g})
    found.sort(key=lambda c: c["minute"])
    return found
