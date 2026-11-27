"""Fingerprints of the model's results, used by tests/test_model_identity.py.

`python tests/golden/make_model_golden.py` rewrites tests/golden/model.json from the model
as it is now. Run it only when a change of the model's results is intended and approved.
"""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from engine.config import load_config
from engine.decisions import choices_of
from engine.model import simulate

GOLDEN = Path(__file__).resolve().parent / "model.json"


def fingerprint(result) -> dict:
    """Every figure of a run, to the last decimal: a hash of the daily rows, the events,
    the decision log and the scorecard, and a few figures in clear for a readable diff."""
    columns = sorted(result.rows[0])
    parts = [
        repr([[row.get(column) for column in columns] for row in result.rows]),
        repr([(e.day, e.kind, e.detail) for e in result.events]),
        repr([asdict(record) for record in result.decision_log]),
        repr(asdict(result.scorecard)),
        repr((result.failure_day, result.likely_to_fail_day, result.foltf_day, result.end_day,
              result.outcome, result.flag_cleared_day, result.central_bank_from, result.ela_from,
              result.resolution_weekend, result.faster_reporting_from,
              None if result.funding_need is None else asdict(result.funding_need))),
    ]
    return {
        "columns": columns,
        "sha256": hashlib.sha256("\n".join(parts).encode()).hexdigest(),
        "outcome": result.outcome,
        "end_day": result.end_day,
        "deposits_lost_pct": repr(result.scorecard.deposits_lost_pct),
    }


def all_runs(config) -> dict:
    paths = {"(no decision)": ()} | {key: choices_of(path) for key, path in config.decisions.paths.items()}
    out = {}
    for bank_key, bank in config.banks.items():
        for scenario_key, scenario in config.scenarios.items():
            for path_key, choices in paths.items():
                result = simulate(bank, config.model, scenario, config.decisions, choices)
                out[f"{bank_key} | {scenario_key} | {path_key}"] = fingerprint(result)
    return out


if __name__ == "__main__":
    runs = all_runs(load_config())
    GOLDEN.write_text(json.dumps(runs, indent=1, sort_keys=True) + "\n")
    print(f"{len(runs)} runs written to {GOLDEN}")
