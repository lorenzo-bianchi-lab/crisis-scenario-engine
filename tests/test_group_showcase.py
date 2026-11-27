"""The group showcase: a run of about ten days across a weekend in which the host's
ring-fencing changes the parent's outcome, with no parameter changed for it."""

import pytest

from engine.config import load_config
from engine.decisions import Choice
from engine.group import SEVERITY_LEVELS, GroupChoices, at_severity, simulate_group
from engine import group_live as gl

CONFIG = load_config()
SHOW = CONFIG.college.showcase


def run(choices: GroupChoices):
    scenarios = at_severity(CONFIG.scenarios, SHOW.scenario, SHOW.severity)
    return simulate_group(
        CONFIG.groups[SHOW.group], CONFIG.banks, CONFIG.model, scenarios, SHOW.scenario,
        CONFIG.decisions, choices, SHOW.stressed, CONFIG.college)


DECIDED = GroupChoices(group=(Choice(SHOW.decision, SHOW.option, SHOW.day),))


def test_severity_is_a_value_the_scenario_already_has():
    scenario = CONFIG.scenarios[SHOW.scenario]
    changed = at_severity(CONFIG.scenarios, SHOW.scenario, SHOW.severity)[SHOW.scenario]
    assert changed.severity.value == getattr(scenario.severity, SHOW.severity)
    assert changed.name.endswith(f"{SHOW.severity} severity")
    assert at_severity(CONFIG.scenarios, SHOW.scenario, "central") is CONFIG.scenarios
    # Nothing else differs from the preset.
    assert changed.model_copy(update={"name": scenario.name, "severity": scenario.severity}) == scenario


def test_unknown_severity_is_refused():
    with pytest.raises(ValueError):
        at_severity(CONFIG.scenarios, SHOW.scenario, "extreme")
    assert SEVERITY_LEVELS == ("low", "central", "high")


def test_ring_fencing_changes_the_parents_outcome():
    without, decided = run(GroupChoices()), run(DECIDED)
    assert without.parent.failure_day is None and without.parent.end_day == 30
    assert without.transfers, "the parent is carried by a transfer from the subsidiary"
    assert decided.parent.outcome == "payments_default"
    assert not decided.transfers
    assert decided.subsidiary.failure_day is None


def test_about_ten_days_across_a_weekend():
    decided = run(DECIDED)
    assert 8 <= decided.parent.failure_day <= 12
    weekdays = [row["weekday"] for row in decided.parent.rows[1:]]
    assert "Saturday" in weekdays and "Sunday" in weekdays
    # The decision falls after the weekend and before the default.
    record = decided.decisions[0]
    assert weekdays.index("Sunday") + 1 < record.effect_day < decided.parent.failure_day


def test_live_exercise_at_the_showcase_severity_matches_the_direct_run():
    setup = gl.GroupLiveSetup("Showcase", SHOW.group, SHOW.scenario, SHOW.stressed,
                              DECIDED, "linear", SHOW.severity)
    state = gl.start(setup, "2026-01-01T09:00:00", CONFIG)
    assert state.result.parent.failure_day == run(DECIDED).parent.failure_day
    saved = gl.to_saved(state, CONFIG)
    again, _ = gl.from_saved(saved)
    assert again.setup.severity == SHOW.severity
    assert gl.scenario_of(setup, CONFIG).name.endswith("high severity")
