"""Run many seeds in parallel and print score, shots and goal minutes, to pick a demo match.

Usage: python scripts/scan_seeds.py 1 40
"""

import copy
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fullpitch.engine import Match  # noqa: E402
from fullpitch.scenario import DEFAULT_SCENARIO  # noqa: E402


def one(seed):
    m = Match(copy.deepcopy(DEFAULT_SCENARIO), seed=seed).run()
    goals = [(e["team"], int(e["t"] // 60) + 1) for e in m.events if e["type"] == "goal"]
    shots = {t: sum(1 for e in m.events if e["type"] == "shot" and e["team"] == t) for t in ("RIV", "NOR")}
    oob = sum(1 for e in m.events if e["type"] in ("pass", "shot") and not (0 <= e["start"][0] <= 105))
    return seed, m.score, shots, goals, oob


if __name__ == "__main__":
    a, b = int(sys.argv[1]), int(sys.argv[2])
    with ProcessPoolExecutor() as ex:
        for seed, score, shots, goals, oob in ex.map(one, range(a, b + 1)):
            print(seed, score, shots, goals, "off-pitch actions:", oob, flush=True)
