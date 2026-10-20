"""Run the shape & stats layer on a match and compare detected changes with the truth.

Usage: python scripts/analyze.py [data/FP-0001]
Writes analysis.json next to the match data.
"""

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fullpitch.analysis import MatchData, detect_changes, minute_metrics, player_physicals  # noqa: E402


def main():
    d = Path(sys.argv[1] if len(sys.argv) > 1 else "data/FP-0001")
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    events = [json.loads(x) for x in (d / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    md = MatchData(np.load(d / "tracking.npz"), meta, events)

    rows = minute_metrics(md)
    phys = player_physicals(md)
    changes = detect_changes(rows)
    (d / "analysis.json").write_text(json.dumps({"minutes": rows, "players": phys, "changes": changes}, indent=1),
                                     encoding="utf-8")

    print("Detected shape changes:")
    for c in changes:
        top = ", ".join(f"{h['signal']}({h['phase']}) {h['before']}->{h['after']} [{h['strength']}]"
                        for h in c["signals"][:4])
        print(f"  {c['clock']:>6} {c['team']}  {top}")

    truth = d / "truth.json"
    if truth.exists():
        t = json.loads(truth.read_text(encoding="utf-8"))
        print("\nGround truth (what the coaches did):")
        for x in t["tactical_changes"]:
            # truth time is match clock; convert to continuous minute (second half starts after 1st-half stoppage)
            print(f"  clock {x['applied_at'] / 60:5.1f}  {meta['teams'][x['team']]['short']}  {x['label']}")

    top = sorted(phys.items(), key=lambda kv: -kv[1]["distance_m"])[:3]
    print("\nMost distance covered:", ", ".join(f"{pid} {v['distance_m'] / 1000:.1f} km" for pid, v in top))
    fastest = max(phys.items(), key=lambda kv: kv[1]["top_speed_kmh"])
    print("Fastest:", fastest[0], fastest[1]["top_speed_kmh"], "km/h")


if __name__ == "__main__":
    main()
