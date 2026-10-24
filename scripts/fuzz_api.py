"""Bug hunt: simulate several matches and call every store/API function at many minutes, for every
player and team, checking invariants. Prints every exception and every invariant breach.

Usage: python scripts/fuzz_api.py 201 206
"""

import copy
import json
import sys
import tempfile
import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fullpitch.engine import Match  # noqa: E402
from fullpitch.scenario import DEFAULT_SCENARIO  # noqa: E402
from fullpitch.store import MatchStore  # noqa: E402
from fullpitch.templates import catch_up_text, change_card, goal_card, recap_text  # noqa: E402


def write(m, d):
    d.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(d / "tracking.npz", xy=np.stack(m.frames), clock=np.array(m.frame_clock, dtype=np.float32),
                        period=np.array(m.frame_period, dtype=np.int8), poss=np.array(m.frame_poss, dtype=np.int8))
    (d / "events.jsonl").write_text("\n".join(json.dumps(e) for e in m.events), encoding="utf-8")
    (d / "meta.json").write_text(json.dumps(m.meta()), encoding="utf-8")


def one(seed):
    problems = []
    m = Match(copy.deepcopy(DEFAULT_SCENARIO), seed=seed).run()
    tags = {"reds": sum(1 for e in m.events if e["type"] == "card" and e["card"] == "red"),
            "pens": sum(1 for e in m.events if e["type"] == "shot" and e.get("penalty")),
            "subs": sum(1 for e in m.events if e["type"] == "substitution")}
    with tempfile.TemporaryDirectory() as tmp:
        write(m, Path(tmp))
        s = MatchStore(tmp)
        names = {t["short"]: t["name"] for t in s.md.meta["teams"]}
        goals = [float(s.md.tmin[e["frame"]]) for e in s.md.events if e["type"] == "goal"]
        players = [p["id"] for p in s.md.meta["players"]]

        def check(label, fn):
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001
                problems.append(f"seed {seed} {label}: {type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}")
                return None

        for at in [0.0, 0.1, 5, 13, 30, 44.9, 45.5, 46.5, 47.2, 50, 63, 75, 88, 92, s.length_min]:
            st = check(f"state@{at}", lambda: s.match_state(at))
            ts = check(f"team_stats@{at}", lambda: s.team_stats(at))
            xg = check(f"xg@{at}", lambda: s.xg_timeline(at))
            ch = check(f"changes@{at}", lambda: s.tactical_changes(at)) or []
            seen_goals = len([g for g in goals if g <= at + 1e-9])
            if ts and sum(t["goals"] for t in ts.values()) != seen_goals:
                problems.append(f"seed {seed} team_stats@{at}: goals {sum(t['goals'] for t in ts.values())} != {seen_goals}")
            if st and sum(st["score"].values()) != seen_goals:
                problems.append(f"seed {seed} state@{at}: score {st['score']} != {seen_goals} goals")
            if xg and any(p[0] > at + 0.02 for ser in xg["series"].values() for p in ser):
                problems.append(f"seed {seed} xg@{at}: point after now")
            for c in ch:
                if c["confirmed_minute"] > at:
                    problems.append(f"seed {seed} change confirmed in the future @{at}")
                for persona in ("casual", "analyst", "player"):
                    for lang in ("en", "hu"):
                        card = check(f"change_card@{at}", lambda: change_card(c, names, persona, lang))
                        if card and ("{" in card["title"] + card["body"] or "nan" in card["body"]):
                            problems.append(f"seed {seed} card text broken @{at}: {card['title']} | {card['body']}")
                rep = check(f"replay change@{at}", lambda: s.replay("change", c["since_minute"], c["team"], at,
                                                                     change_card(c, names)["signal"]))
                if rep and rep["frames"] and rep["frames"][-1]["m"] > at + 0.01:
                    problems.append(f"seed {seed} change replay beyond now @{at}")
            if xg:
                for ser in xg["series"].values():
                    if any(b[1] < a[1] - 1e-9 for a, b in zip(ser, ser[1:])):
                        problems.append(f"seed {seed} xg not monotone @{at}")
            if ts:
                tot = sum(t["possession_pct"] for t in ts.values())
                if at > 1 and not 98 <= tot <= 102:
                    problems.append(f"seed {seed} possession sums to {tot} @{at}")
            f = s.frame(at)
            for k, short in enumerate(s.md.shorts):
                on = int(s.md.active[f, k * 11:(k + 1) * 11].sum())
                reds = sum(1 for e in s.md.events if e["type"] == "card" and e["card"] == "red" and e["team"] == short
                           and e["frame"] <= f)
                if on != 11 - reds:
                    problems.append(f"seed {seed} {short} has {on} on the pitch with {reds} reds @{at}")
                pn = check(f"passnet size {short}@{at}", lambda: s.pass_network(short, 0.0, at))
                if pn and len(pn["nodes"]) > 16:
                    problems.append(f"seed {seed} pass network with {len(pn['nodes'])} nodes @{at}")
            for g in goals:
                if g <= at:
                    rep = check(f"replay goal@{at}", lambda: s.replay("goal", round(g, 2), None, at))
                    if rep and rep["frames"] and rep["frames"][-1]["m"] > at + 0.01:
                        problems.append(f"seed {seed} goal replay beyond now @{at}")
            for team in s.md.shorts:
                for start in (0.0, max(0.0, at - 15)):
                    check(f"passnet {team}@{at}", lambda: s.pass_network(team, start, at))
            for away in (5, 30, 120):
                for follow in (None, players[3], players[20]):
                    f = check(f"catch_up@{at}", lambda: s.catch_up(max(0.0, at - away), at, at, follow))
                    if f:
                        for persona in ("casual", "analyst", "player"):
                            for lang in ("en", "hu"):
                                t = check(f"catch_up_text@{at}", lambda: catch_up_text(f, persona, lang))
                                if t and any("{" in line or "nan" in line for line in t["lines"]):
                                    problems.append(f"seed {seed} catch-up text broken @{at}: {t['lines']}")
            for kind in ("ht", "ft"):
                r = check(f"recap {kind}@{at}", lambda: s.recap(kind, at))
                if r:
                    check(f"recap_text {kind}@{at}", lambda: recap_text(r, "hu"))
            for e in s.key_moments(at):
                if e["type"] == "goal":
                    check("goal_card", lambda: goal_card(e, "hu"))
                # the tools behind 'ask the pitch': every moment so far, never one after now
                pm = check(f"positions_at_moment {e['moment']}@{at}", lambda: s.positions_at_moment(e["moment"], at))
                if pm and pm["minute"] > at + 1e-6:
                    problems.append(f"seed {seed} positions_at_moment beyond now @{at}")
                ev_frame = next(ev["frame"] for m, ev in s._key_events() if m == e["moment"])
                if pm and len(pm["players"]) != int(s.md.active[ev_frame].sum()):
                    problems.append(f"seed {seed} positions_at_moment lists {len(pm['players'])} players @{at}")
            pa = check(f"positions_at future@{at}", lambda: s.positions_at(at + 20, at))
            if pa and pa["minute"] > at + 1e-6:
                problems.append(f"seed {seed} positions_at beyond now @{at}")
            for team in s.md.shorts:
                ac = check(f"attack_channels {team}@{at}", lambda: s.attack_channels(team, 0.0, at))
                if ac and ac["share_pct"]["left"] is not None and not 98 <= sum(ac["share_pct"].values()) <= 102:
                    problems.append(f"seed {seed} channel shares {ac['share_pct']} @{at}")
            check(f"leaders@{at}", lambda: s.leaders(at))
            if [m for m, e in s._key_events() if e["frame"] > s.frame(at)][:1]:
                nxt = [m for m, e in s._key_events() if e["frame"] > s.frame(at)][0]
                try:
                    s.positions_at_moment(nxt, at)
                    problems.append(f"seed {seed} positions_at_moment served a future moment {nxt} @{at}")
                except ValueError:
                    pass
        for at in (30, 70, s.length_min):
            for pid in players:
                p = check(f"profile {pid}@{at}", lambda: s.player_profile(pid, at))
                check(f"player_report {pid}@{at}", lambda: s.player_report(pid, 0, at, at))
                if p and p["fatigue"] and abs(p["fatigue"]["top_speed_drop_kmh"]) > 12:
                    problems.append(f"seed {seed} implausible fatigue {pid}@{at}: {p['fatigue']}")
                r = check(f"report {pid}@{at}", lambda: s.player_report(pid, 0, at, at))
                if r and r["physical"] and r["physical"]["minutes"] > at + 0.5:
                    problems.append(f"seed {seed} {pid} played {r['physical']['minutes']} min by {at}")
                if r and r["physical"] and r["physical"]["top_speed_kmh"] > 36:
                    problems.append(f"seed {seed} {pid} top speed {r['physical']['top_speed_kmh']} @{at}")
                if p and any(sp["km_per_90"] > 16 for sp in p["splits"]):
                    problems.append(f"seed {seed} {pid} split over 16 km/90 @{at}: {[sp['km_per_90'] for sp in p['splits']]}")
    return seed, tags, problems


if __name__ == "__main__":
    a, b = int(sys.argv[1]), int(sys.argv[2])
    total = 0
    with ProcessPoolExecutor() as ex:
        for seed, tags, problems in ex.map(one, range(a, b + 1)):
            total += len(problems)
            print(f"seed {seed} {tags}: {len(problems)} problems", flush=True)
            seen = set()
            for p in problems:
                key = p.split(":")[0].split("@")[0] + (p.split(":", 1)[1][:60] if ":" in p else "")
                if key not in seen:
                    seen.add(key)
                    print("  " + p.strip().replace("\n", "\n    "))
    print(f"TOTAL problems: {total}")
