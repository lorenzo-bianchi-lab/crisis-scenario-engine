"""SPEC_V2 phase E, part a: reverse mode."""

import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app import demo
from engine import reverse
from engine.config import load_config
from engine.model import simulate
from engine.montecarlo import SAMPLED_PARAMETERS
from engine.reverse import Target, solve

CONFIG = load_config()
APP_PATH = str(Path(__file__).resolve().parent.parent / "app" / "main.py")

# bank, scenario, target: three targets the ranges can reach.
FEASIBLE = [
    ("universal", "fast_run", Target(2, "liquidity", (20, 30))),
    ("universal", "geopolitical", Target(5, "liquidity", (20, 30), "Friday")),
    ("digital", "geopolitical", Target(4, "liquidity", (10, 20), "Thursday")),
]
INFEASIBLE = ("retail", "slow_burn", Target(3, "liquidity", (20, 30)))


def _solve(bank, scenario, target):
    return solve(CONFIG.banks[bank], CONFIG.model, CONFIG.scenarios[scenario], target)


@pytest.mark.parametrize("bank,scenario,target", FEASIBLE)
def test_feasible_target_is_solved_in_time_and_the_run_confirms_it(bank, scenario, target):
    started = time.perf_counter()
    solution = _solve(bank, scenario, target)
    assert time.perf_counter() - started < 10
    assert solution.feasible and solution.gaps == () and solution.verdict == "feasible"
    # Simulated again from the solved bank and scenario, on the path without decisions.
    result = simulate(solution.bank, CONFIG.model, solution.scenario)
    assert result.likely_to_fail_day == target.flag_day
    assert reverse.flag_cause(result) == target.cause
    lost = result.rows[target.flag_day]["cum_deposit_loss_pct"]
    assert target.deposits_lost[0] <= lost <= target.deposits_lost[1]
    assert lost == pytest.approx(solution.deposits_lost_pct)
    if target.weekday:
        assert solution.scenario.weekday(target.flag_day) == target.weekday


@pytest.mark.parametrize("bank,scenario,target", FEASIBLE + [INFEASIBLE])
def test_solved_values_stay_inside_their_ranges(bank, scenario, target):
    solution = _solve(bank, scenario, target)
    assert [v.name for v in solution.variables] == [
        reverse.SEVERITY, reverse.NEWS_SCALE, reverse.SPEED_SCALE]
    for variable in solution.variables:
        assert variable.low - 1e-9 <= variable.value <= variable.high + 1e-9
        assert 0 <= variable.plausibility <= 1


def test_infeasible_target_is_reported_with_its_gaps():
    bank, scenario, target = INFEASIBLE
    started = time.perf_counter()
    solution = _solve(bank, scenario, target)
    assert time.perf_counter() - started < 10
    assert not solution.feasible and solution.verdict == "infeasible"
    assert solution.gaps and "flag" in solution.gaps[0]
    lines = "\n".join(reverse.solution_lines(solution, target))
    assert "infeasible" in lines and "gaps" in lines


def test_a_miss_states_each_gap():
    # The universal bank cannot keep deposits that low by a capital flag on day 4.
    target = Target(4, "capital", (0, 1))
    solution = _solve("universal", "loss_revealed", target)
    assert not solution.feasible
    assert any("percentage points" in gap or "flag day" in gap or "cause" in gap
               for gap in solution.gaps)
    # The closest miss is a real run: its figures are those of a simulation.
    result = simulate(solution.bank, CONFIG.model, solution.scenario)
    assert result.likely_to_fail_day == solution.flag_day


def test_same_target_gives_the_same_answer():
    bank, scenario, target = FEASIBLE[0]
    first, second = _solve(bank, scenario, target), _solve(bank, scenario, target)
    assert first.variables == second.variables and first.runs == second.runs


def test_target_met_by_the_central_run_needs_no_change():
    central = simulate(CONFIG.banks["universal"], CONFIG.model, CONFIG.scenarios["loss_revealed"])
    lost = central.rows[central.likely_to_fail_day]["cum_deposit_loss_pct"]
    target = Target(central.likely_to_fail_day, reverse.flag_cause(central), (lost - 1, lost + 1))
    solution = _solve("universal", "loss_revealed", target)
    assert solution.feasible and solution.runs == 1
    assert all(v.plausibility == 0 and v.value == v.central for v in solution.variables)


def test_among_matches_the_one_closest_to_central_is_kept():
    # A wide band is met by many sets: the answer must not sit at a bound without need.
    solution = _solve("universal", "fast_run", Target(2, "liquidity", (0, 100)))
    assert solution.feasible
    assert max(v.plausibility for v in solution.variables) < 1


def test_weekday_moves_the_start_of_the_scenario():
    scenario = CONFIG.scenarios["fast_run"]
    assert reverse.start_weekday_for(scenario, Target(5, "liquidity", (0, 100), "Friday")) == "Monday"
    assert reverse.start_weekday_for(scenario, Target(1, "liquidity", (0, 100), "Friday")) == "Friday"
    assert reverse.start_weekday_for(scenario, Target(3, "liquidity", (0, 100))) == scenario.start_weekday


def test_apply_scales_news_and_speed_and_leaves_the_inputs_alone():
    bank, scenario = CONFIG.banks["digital"], CONFIG.scenarios["fast_run"]
    solved_bank, solved = reverse.apply(bank, scenario, "Tuesday", 2.5, 0.5, 1.2)
    assert solved_bank.runoff_speed_factor.value == pytest.approx(1.2 * bank.runoff_speed_factor.value)
    assert [e.panic_shock for e in solved.news_events] == pytest.approx(
        [0.5 * e.panic_shock for e in scenario.news_events])
    assert solved.severity.value == 2.5 and solved.start_weekday == "Tuesday"
    assert scenario.severity.value == 3.0 and bank.runoff_speed_factor.value == 1.5


def test_bad_targets_are_refused():
    for args in ((0, "liquidity", (0, 10)), (3, "panic", (0, 10)), (3, "capital", (30, 20)),
                 (3, "capital", (0, 10), "Someday")):
        with pytest.raises(ValueError):
            Target(*args)


def test_search_ranges_are_assumptions_and_not_sampled():
    for name in ("reverse_news_shock_scale", "reverse_speed_factor_scale"):
        parameter = getattr(CONFIG.model, name)
        assert parameter.is_assumption and parameter.low < parameter.value == 1.0 < parameter.high
        assert name not in SAMPLED_PARAMETERS


def test_cli_prints_the_solution(capsys):
    reverse.main(["--bank", "universal", "--scenario", "fast_run", "--flag-day", "2",
                  "--deposits-lost", "20", "30"])
    out = capsys.readouterr().out
    assert "Result: feasible" in out and "Scenario severity" in out


# ------------------------------------------------- app


def _open(tab):
    at = AppTest.from_file(APP_PATH, default_timeout=60)
    at.session_state["demo_on"] = False
    at.session_state["view"] = tab
    return at.run()


def test_uncertainty_tab_is_renamed_and_says_ranges_are_not_probabilities():
    at = _open(demo.TAB_UNCERTAINTY)
    assert not at.exception
    assert demo.TAB_UNCERTAINTY == "Sensitivity ranges"
    assert "Uncertainty" not in [tab.label for tab in at.tabs]
    assert any("assumed parameter intervals, not probabilities" in info.value for info in at.info)


def test_reverse_tab_solves_and_loads_the_solution_into_the_design():
    at = _open(demo.TAB_REVERSE)
    assert not at.exception
    assert at.success or at.error  # a verdict is shown
    at.number_input(key="reverse_day").set_value(5).run()
    at.selectbox(key="reverse_weekday").set_value("Friday").run()
    assert not at.exception
    assert at.success, [e.value for e in at.error]
    solution, _ = at.session_state["reverse_solution"]
    at.button(key="reverse_load").click().run()
    assert not at.exception
    applied = at.session_state["reverse_applied"]
    assert applied["weekday"] == solution.start_weekday
    assert any("reverse-mode solution is loaded" in info.value for info in at.info)
    # The design now runs the solved set: on the path without decisions the flag day matches.
    design = at.session_state["design"]
    result = simulate(design.bank, CONFIG.model, design.scenario)
    assert result.likely_to_fail_day == 5 and design.scenario.weekday(5) == "Friday"
    at.button(key="reverse_remove").click().run()
    assert "reverse_applied" not in at.session_state
