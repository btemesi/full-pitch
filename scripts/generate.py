"""Generate one synthetic match: tracking.npz, events.jsonl, meta.json, truth.json.

Usage: python scripts/generate.py [--seed N] [--out data]
"""

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fullpitch.engine import Match  # noqa: E402
from fullpitch.scenario import DEFAULT_SCENARIO  # noqa: E402


def summary(m):
    teams = [t["short"] for t in m.sc["teams"]]
    ev = m.events
    out = {}
    for short in teams:
        passes = [e for e in ev if e["type"] == "pass" and e["team"] == short]
        shots = [e for e in ev if e["type"] == "shot" and e["team"] == short]
        out[short] = {
            "goals": sum(1 for e in ev if e["type"] == "goal" and e["team"] == short),
            "shots": len(shots),
            "on_target": sum(1 for e in shots if e["outcome"] in ("goal", "saved")),
            "xg": round(sum(e["xg"] for e in shots), 2),
            "passes": len(passes),
            "pass_acc": round(sum(e["outcome"] == "complete" for e in passes) / max(len(passes), 1), 3),
            "tackles": sum(1 for e in ev if e["type"] == "tackle" and e["team"] == short),
            "fouls": sum(1 for e in ev if e["type"] == "foul" and e["team"] == short),
            "cards": sum(1 for e in ev if e["type"] == "card" and e["team"] == short),
            "corners": sum(1 for e in ev if e["type"] == "restart" and e.get("kind") == "corner" and e["team"] == short),
        }
    poss = np.array(m.frame_poss)
    live = poss[poss >= 0]
    out["possession_home"] = round(float((live == 0).mean()), 3)
    out["ball_in_play_min"] = round(len(live) / 5 / 60, 1)
    out["event_types"] = dict(Counter(e["type"] for e in ev))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="data")
    args = ap.parse_args()

    t0 = time.time()
    m = Match(DEFAULT_SCENARIO, seed=args.seed).run()
    dt = time.time() - t0

    out = Path(args.out) / m.sc["match_id"]
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out / "tracking.npz", xy=np.stack(m.frames), clock=np.array(m.frame_clock, dtype=np.float32),
                        period=np.array(m.frame_period, dtype=np.int8), poss=np.array(m.frame_poss, dtype=np.int8))
    with open(out / "events.jsonl", "w", encoding="utf-8") as f:
        for e in m.events:
            f.write(json.dumps(e) + "\n")
    (out / "meta.json").write_text(json.dumps(m.meta(), indent=2), encoding="utf-8")
    (out / "truth.json").write_text(json.dumps(m.truth(), indent=2), encoding="utf-8")

    s = summary(m)
    print(f"seed {args.seed}: {len(m.frames)} frames in {dt:.1f}s -> {out}")
    print(json.dumps(s, indent=2))


if __name__ == "__main__":
    main()
