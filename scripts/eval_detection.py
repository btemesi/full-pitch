"""Check change detection against the scenario truth on many seeds.

Usage: python scripts/eval_detection.py 1 8
A detection counts as a hit if it's the right team within +-4 minutes of a truth
change (truth minutes are converted to continuous match time, i.e. including the
first-half stoppage).
"""

import copy
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fullpitch.analysis import MatchData, detect_changes, minute_metrics  # noqa: E402
from fullpitch.engine import Match  # noqa: E402
from fullpitch.scenario import DEFAULT_SCENARIO  # noqa: E402


def one(seed):
    m = Match(copy.deepcopy(DEFAULT_SCENARIO), seed=seed).run()
    tracking = {"xy": np.stack(m.frames), "clock": np.array(m.frame_clock), "period": np.array(m.frame_period),
                "poss": np.array(m.frame_poss)}
    md = MatchData(tracking, m.meta(), m.events)
    found = detect_changes(minute_metrics(md), threshold=float(sys.argv[3]) if len(sys.argv) > 3 else 3.0, strong=float(sys.argv[4]) if len(sys.argv) > 4 else 4.0)
    stoppage1 = DEFAULT_SCENARIO["stoppage"][0] / 60
    truth = [(m.sc["teams"][x["team"]]["short"],
              x["applied_at"] / 60 + (stoppage1 if x["applied_at"] >= 2700 else 0)) for x in m.applied]
    hits = sum(any(f["team"] == t and abs(f["minute"] - tm) <= 4 for f in found) for t, tm in truth)
    own = [any(f["team"] == t and abs(f["minute"] - tm) <= 4 for t, tm in truth) for f in found]
    # a shift right after the *opponent's* change is a real reaction, not a false alarm
    reaction = sum(not o and any(abs(f["minute"] - tm) <= 4 for _, tm in truth) for f, o in zip(found, own))
    false = sum(not o and not any(abs(f["minute"] - tm) <= 4 for _, tm in truth) for f, o in zip(found, own))
    return seed, hits, len(truth), false, reaction, [(f["team"], f["minute"]) for f in found]


if __name__ == "__main__":
    a, b = int(sys.argv[1]), int(sys.argv[2])
    tot_h = tot_t = tot_f = tot_r = 0
    with ProcessPoolExecutor() as ex:
        for seed, h, t, f, r, found in ex.map(one, range(a, b + 1)):
            tot_h, tot_t, tot_f, tot_r = tot_h + h, tot_t + t, tot_f + f, tot_r + r
            print(f"seed {seed}: {h}/{t} found, {f} false, {r} reactions  {found}", flush=True)
    print(f"TOTAL recall {tot_h}/{tot_t}, false alarms {tot_f}, reactions {tot_r}")
