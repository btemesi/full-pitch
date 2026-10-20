"""How long after a coach's change does the live detector first confirm it? (minutes)

Usage: python scripts/confirm_delay.py 25 40
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
    md = MatchData({"xy": np.stack(m.frames), "clock": np.array(m.frame_clock), "period": np.array(m.frame_period),
                    "poss": np.array(m.frame_poss)}, m.meta(), m.events)
    rows = minute_metrics(md)
    st1 = DEFAULT_SCENARIO["stoppage"][0] / 60
    truth = [(m.sc["teams"][x["team"]]["short"], x["applied_at"] / 60 + (st1 if x["applied_at"] >= 2700 else 0))
             for x in m.applied]
    out = []
    for team, tm in truth:
        for a in range(13, len(rows) + 1):
            if any(c["team"] == team and abs(c["minute"] - tm) <= 4 for c in detect_changes(rows[:a])):
                out.append(round(a - tm, 1))
                break
    return out


if __name__ == "__main__":
    a, b = int(sys.argv[1]), int(sys.argv[2])
    with ProcessPoolExecutor() as ex:
        d = [x for r in ex.map(one, range(a, b + 1)) for x in r]
    d = np.array(d)
    print(f"{len(d)} confirmed changes: median {np.median(d):.1f} min, middle half {np.percentile(d, 25):.1f}-{np.percentile(d, 75):.1f}, max {d.max():.1f}")
