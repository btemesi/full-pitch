import copy
import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ["FULLPITCH_AGENTS"] = "0"  # tests never call the model
sys.path.insert(0, str(ROOT))

from fullpitch.engine import Match  # noqa: E402
from fullpitch.scenario import DEFAULT_SCENARIO  # noqa: E402


@pytest.fixture(scope="session")
def match():
    """One full simulated match (seed 7), shared by the engine and analysis tests."""
    return Match(copy.deepcopy(DEFAULT_SCENARIO), seed=7).run()


@pytest.fixture(scope="session")
def match_data(match):
    from fullpitch.analysis import MatchData
    return MatchData({"xy": np.stack(match.frames), "clock": np.array(match.frame_clock),
                      "period": np.array(match.frame_period), "poss": np.array(match.frame_poss)},
                     match.meta(), match.events)
