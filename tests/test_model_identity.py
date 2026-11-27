"""The model gives the results stored in tests/golden/model.json, to the last decimal.

Written before `simulate()` was split into daily steps (phase F-a): the split, and the
group layer built on it, must not change any figure of a single bank.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "golden"))
from make_model_golden import GOLDEN, all_runs  # noqa: E402

from engine.config import load_config  # noqa: E402

STORED = json.loads(GOLDEN.read_text())
NOW = all_runs(load_config())


def test_every_bank_scenario_and_decision_path_is_covered():
    config = load_config()
    assert set(NOW) == set(STORED)
    assert len(STORED) == len(config.banks) * len(config.scenarios) * (len(config.decisions.paths) + 1)


@pytest.mark.parametrize("run", sorted(STORED))
def test_run_is_identical_to_the_stored_result(run):
    stored, now = STORED[run], NOW[run]
    # New columns may be added to a row; the stored ones must hold the same values.
    assert set(stored["columns"]) <= set(now["columns"])
    assert (now["outcome"], now["end_day"], now["deposits_lost_pct"]) == (
        stored["outcome"], stored["end_day"], stored["deposits_lost_pct"])
    assert now["sha256"] == stored["sha256"]
