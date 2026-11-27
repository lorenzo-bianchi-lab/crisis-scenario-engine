"""SPEC_V3 phase E, part b: design by objective and the decision map."""

from itertools import product
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app import demo
from app import theme
from app.charts import build_decision_map, decision_map_layout, outcome_color
from engine import objective
from engine.config import load_config
from engine.decisions import OUTCOME_LABELS, Choice, choices_of

ROOT = Path(__file__).resolve().parent.parent
from engine.model import simulate

CONFIG = load_config()
DECISIONS = CONFIG.decisions
APP_PATH = str(Path(__file__).resolve().parent.parent / "app" / "main.py")
OBJECTIVES = [item.decision for item in CONFIG.script.objectives.values()]
PROPOSALS = {decision: objective.propose(CONFIG, decision) for decision in OBJECTIVES}
HARD = [decision for decision, proposal in PROPOSALS.items() if proposal.hard]
NOT_HARD = [decision for decision, proposal in PROPOSALS.items() if not proposal.hard]


def test_objectives_are_decisions_and_annex2_is_claimed_only_where_limits_lists_it():
    limits = (Path(__file__).resolve().parent.parent / "docs" / "LIMITS.md").read_text()
    assert len(CONFIG.script.objectives) >= 6
    for item in CONFIG.script.objectives.values():
        assert item.decision in DECISIONS.decisions
        assert item.annex2 == "not in Annex 2" or f"| {item.annex2} |" in limits


def test_every_objective_gets_an_answer_in_one_paragraph():
    for decision, proposal in PROPOSALS.items():
        assert proposal.explanation and "\n" not in proposal.explanation
        assert DECISIONS.decisions[decision].name in proposal.explanation
        assert "{" not in proposal.explanation


def test_at_least_three_objectives_have_a_hard_setting_and_the_rest_say_why_not():
    assert len(HARD) >= 3, HARD
    assert "declare_foltf" in HARD  # the FoLtF determination is a hard decision somewhere
    for decision in NOT_HARD:
        text = PROPOSALS[decision].explanation
        assert "at least as low as every other option" in text or "same figures" in text


@pytest.mark.parametrize("decision", HARD)
def test_proposed_setting_is_confirmed_by_direct_simulation(decision):
    proposal = PROPOSALS[decision]
    setting = proposal.setting
    bank, scenario = CONFIG.banks[setting.bank_key], CONFIG.scenarios[setting.scenario_key]
    definition = DECISIONS.decisions[decision]
    assert [run.option for run in setting.runs] == list(definition.options)
    cards = []
    for run in setting.runs:
        choices = list(setting.prerequisites)
        if run.option != definition.default_option:
            choices.append(Choice(decision, run.option, setting.day))
            choices += objective.dependents(DECISIONS, decision, run.option)
        result = simulate(bank, CONFIG.model, scenario, DECISIONS, choices)
        assert result.scorecard == run.scorecard
        assert run == objective.option_run(bank, CONFIG.model, result, run.option,
                                           run.option_name, objective.outcome_ranks(CONFIG))
        assert run.outcome_rank == CONFIG.script.objective_outcome_rank[objective.rank_key(result)]
        if run.option != definition.default_option:
            record = next(r for r in result.decision_log if r.decision == decision)
            assert (record.option, record.decided_day) == (run.option, setting.day)
        cards.append(run)
    # No option is at least as low as every other on every compared line.
    for card in cards:
        assert any(
            objective.line_value(card, line) > objective.line_value(other, line) + 1e-9
            for other in cards for line, *_ in objective.LINES
        )
    assert setting.dominant is None and setting.delay in objective.DELAYS
    # The paragraph names the proposed bank, scenario and day, and gives the reason.
    text = proposal.explanation
    assert bank.name in text and scenario.name in text and f"day {setting.day}" in text
    assert "No option is lowest on every line" in text


@pytest.mark.parametrize("decision", HARD)
def test_proposal_has_the_largest_spread_among_hard_settings(decision):
    hard = [s for s in objective.all_settings(CONFIG, decision) if s.dominant is None]
    assert PROPOSALS[decision].setting.spread == max(s.spread for s in hard)
    assert PROPOSALS[decision].setting == objective.propose(CONFIG, decision).setting


def test_a_dominated_setting_is_reported_as_such():
    """Settings in which one option dominates still exist; `propose` reports one when a
    decision has no hard setting (here on a catalogue reduced to such settings)."""
    dominated = [s for d in OBJECTIVES for s in objective.all_settings(CONFIG, d)
                 if s.dominant is not None and s.spread > 1e-9]
    assert dominated, "expected a setting in which one option dominates"
    for setting in dominated[:20]:
        best = next(run for run in setting.runs if run.option == setting.dominant)
        for other in setting.runs:
            for line, *_ in objective.LINES:
                assert objective.line_value(best, line) <= objective.line_value(other, line) + 1e-9


def test_outcome_ranking_is_a_setting_and_an_assumption():
    rank = CONFIG.script.objective_outcome_rank
    flagged = {"under_stress_likely_to_fail", "emergency_funding_likely_to_fail"}
    assert set(rank) == set(OUTCOME_LABELS) | flagged and CONFIG.script.objective_rank_source == "ASSUMPTION"
    assert rank["stabilised"] < rank["under_stress"] < rank["emergency_funding"]
    assert rank["emergency_funding"] < rank["bail_in"] == rank["sale_of_business"] == rank["liquidation"]
    # An orderly resolution does not rank below a bank left open while likely to fail.
    assert all(rank["liquidation"] < rank[key] < rank["payments_default"] for key in flagged)
    assert rank["payments_default"] == max(rank.values())


def test_a_bank_left_open_while_likely_to_fail_ranks_below_an_orderly_resolution():
    bank, scenario = CONFIG.banks["universal"], CONFIG.scenarios["geopolitical"]
    late = simulate(bank, CONFIG.model, scenario, DECISIONS, [])
    assert late.outcome == "under_stress" and late.rows[-1]["flag_active"]
    assert objective.rank_key(late) == "under_stress_likely_to_fail"
    resolved = simulate(bank, CONFIG.model, scenario, DECISIONS,
                        choices_of(DECISIONS.paths["early_foltf"]))
    assert objective.rank_key(resolved) == "bail_in"
    ranks = objective.outcome_ranks(CONFIG)
    assert ranks[objective.rank_key(resolved)] < ranks[objective.rank_key(late)]
    # A bank that stays open with the flag cleared, or never raised, keeps the plain rank.
    calm = simulate(bank, CONFIG.model, CONFIG.scenarios["slow_burn"], DECISIONS, [])
    assert objective.rank_key(calm) == "under_stress" and not calm.rows[-1]["flag_active"]


def test_the_ranking_can_be_changed_in_config_and_must_be_complete():
    import yaml
    from pydantic import ValidationError
    from engine.schema import ScriptConfig
    data = yaml.safe_load((ROOT / "config" / "script.yaml").read_text())
    data["objective_outcome_rank"]["under_stress_likely_to_fail"] = 1  # the old reading
    assert ScriptConfig(**data).objective_outcome_rank["under_stress_likely_to_fail"] == 1
    del data["objective_outcome_rank"]["payments_default"]
    with pytest.raises(ValidationError):
        ScriptConfig(**data)
    assert [line for line, *_ in objective.LINES][0] == "outcome_rank"
    assert {"creditor_loss", "payout_days"} <= {line for line, *_ in objective.LINES}


def test_dependent_decisions_follow_at_their_first_close():
    assert objective.dependents(DECISIONS, "declare_foltf", "wait") == ()
    after = objective.dependents(DECISIONS, "declare_foltf", "declare")
    assert Choice("resolution_tool", "bail_in", 0) in after
    assert all(choice.day == 0 for choice in after)
    assert objective.dependents(DECISIONS, "resolution_tool", "liquidation") == (
        Choice("dgs_payout", "pay_out", 0),)
    assert objective.dependents(DECISIONS, "resolution_tool", "bail_in") == ()
    assert objective.dependents(DECISIONS, "ela", "grant_full") == ()
    # So declaring FoLtF ends in a resolution, and liquidation in a payout with its delay.
    foltf = PROPOSALS["declare_foltf"].setting
    declared = next(run for run in foltf.runs if run.option == "declare")
    assert declared.scorecard.outcome == "bail_in" and declared.creditor_loss > 0
    tool = PROPOSALS["resolution_tool"].setting
    wound_up = next(run for run in tool.runs if run.option == "liquidation")
    assert wound_up.scorecard.dgs_payout > 0 and wound_up.payout_days > 0
    assert "follow at their first close" in PROPOSALS["declare_foltf"].explanation


def test_a_decision_others_depend_on_is_never_said_to_matter_for_the_script_only():
    with_dependents = [d for d in DECISIONS.decisions if objective.has_dependents(DECISIONS, d)]
    assert {"declare_foltf", "resolution_tool"} <= set(with_dependents)
    for decision in with_dependents:
        assert "for the script, not for the outcome" not in objective.propose(CONFIG, decision).explanation
    texts = CONFIG.script.objective_texts
    assert "for the script" not in texts["no_difference_dependents"]
    # Were its options ever to give the same figures, such a decision gets the other text.
    original = objective.spread_of, objective.dominant_option
    try:
        objective.spread_of = lambda runs, *args: (0.0, 1)
        objective.dominant_option = lambda runs: runs[0].option
        proposal = objective.propose(CONFIG, "declare_foltf")
        plain = objective.propose(CONFIG, "prepare_payout")
    finally:
        objective.spread_of, objective.dominant_option = original
    assert "for the script, not for the outcome" in plain.explanation  # nothing depends on it
    assert not proposal.hard and "for the script" not in proposal.explanation
    assert "later decisions depend on it" in proposal.explanation


def test_spread_and_dominance_on_hand_made_scorecards():
    base = PROPOSALS[OBJECTIVES[0]].setting.runs[0].scorecard
    from dataclasses import replace
    a = objective.OptionRun("a", "A", replace(base, outcome="stabilised", deposits_lost_pct=10.0,
                                              losses_booked=2.0, ela_outstanding=0.0, dgs_payout=0.0))
    b = objective.OptionRun("b", "B", replace(base, outcome="liquidation", deposits_lost_pct=30.0,
                                              losses_booked=1.0, ela_outstanding=0.0, dgs_payout=0.0))
    c = objective.OptionRun("c", "C", replace(a.scorecard, losses_booked=1.0))
    assert objective.dominant_option([a, b]) is None
    assert objective.dominant_option([a, b, c]) == "c"
    spread, outcomes = objective.spread_of([a, b], 100.0)
    assert outcomes == 2 and spread == pytest.approx(1 + 0.20 + 0.01)
    assert objective.spread_of([a, a], 100.0) == (0.0, 1)


def test_prerequisites_follow_the_conditions_in_order():
    assert objective.prerequisites(DECISIONS, "ela") == ()
    assert objective.prerequisites(DECISIONS, "resolution_tool") == (Choice("declare_foltf", "declare", 0),)
    assert objective.prerequisites(DECISIONS, "dgs_payout") == (
        Choice("declare_foltf", "declare", 0), Choice("resolution_tool", "liquidation", 0))


def test_calm_baseline_is_not_searched():
    assert all(CONFIG.scenarios[s.scenario_key].taxonomy is not None
               for s in objective.all_settings(CONFIG, "ela"))


# ------------------------------------------------- decision map

MAPS = [
    ("retail", "geopolitical", ("declare_foltf", "resolution_tool")),
    ("universal", "fast_run", ("ela", "declare_foltf", "moratorium", "resolution_tool")),
    ("digital", "loss_revealed", ("public_statement", "recovery_options", "ela")),
]


@pytest.mark.parametrize("bank,scenario,keys", MAPS)
def test_decision_map_has_every_combination_and_each_leaf_is_a_direct_run(bank, scenario, keys):
    tree = objective.decision_map(CONFIG.banks[bank], CONFIG.model, CONFIG.scenarios[scenario],
                                  DECISIONS, keys)
    combinations = list(product(*(DECISIONS.decisions[key].options for key in keys)))
    assert [leaf.options for leaf in tree.leaves] == combinations
    for leaf in tree.leaves:
        choices = [Choice(key, option, 0) for key, option in zip(keys, leaf.options)
                   if option != DECISIONS.decisions[key].default_option]
        result = simulate(CONFIG.banks[bank], CONFIG.model, CONFIG.scenarios[scenario], DECISIONS, choices)
        assert result.scorecard == leaf.scorecard and result.outcome == leaf.outcome
        taken = {record.decision for record in result.decision_log}
        for key, option, was_taken in zip(keys, leaf.options, leaf.taken):
            assert was_taken == (option == DECISIONS.decisions[key].default_option or key in taken)


def test_decision_map_refuses_too_many_or_repeated_decisions():
    bank, scenario = CONFIG.banks["retail"], CONFIG.scenarios["geopolitical"]
    for keys in ((), ("ela", "ela"), tuple(DECISIONS.decisions)[:5]):
        with pytest.raises(ValueError):
            objective.decision_map(bank, CONFIG.model, scenario, DECISIONS, keys)


def test_decision_map_chart_is_a_tree_with_one_level_per_decision():
    bank, scenario, keys = MAPS[0]
    tree = objective.decision_map(CONFIG.banks[bank], CONFIG.model, CONFIG.scenarios[scenario],
                                  DECISIONS, keys)
    nodes, edges = decision_map_layout(tree)
    assert {node["level"] for node in nodes} == {0, 1, 2}
    assert len(edges) == len(nodes) - 1  # a tree
    assert sum(node["leaf"] is not None for node in nodes) == len(tree.leaves)
    fig = build_decision_map(tree, OUTCOME_LABELS)
    outcomes = {leaf.outcome for leaf in tree.leaves}
    by_name = {trace.name: trace for trace in fig.data}
    for outcome in outcomes:
        trace = by_name[OUTCOME_LABELS[outcome]]
        assert trace.marker.color == outcome_color(outcome) == theme.stage_color(outcome)
        assert len(trace.x) == sum(leaf.outcome == outcome for leaf in tree.leaves)
    labels = [annotation.text for annotation in fig.layout.annotations]
    for name in tree.decision_names:
        assert f"<b>{name}</b>" in labels
    assert any(label.endswith("(not open)") for label in labels)
    assert all(theme.stage_of(outcome) in theme.STAGES for outcome in OUTCOME_LABELS)


def test_cli_prints_proposal_and_map(capsys):
    objective.main(["--decision", "ela"])
    out = capsys.readouterr().out
    assert "is hardest for" in out and "Lower is taken as preferred" in out
    objective.main(["--map", "declare_foltf", "resolution_tool"])
    out = capsys.readouterr().out
    assert "8 combinations" in out and "(not open)" in out


# ------------------------------------------------- app


def test_objective_tab_proposes_and_loads_the_setting_and_draws_the_map():
    at = AppTest.from_file(APP_PATH, default_timeout=60)
    at.session_state["demo_on"] = False
    at.session_state["view"] = demo.TAB_OBJECTIVE
    at.run()
    assert not at.exception
    objectives = CONFIG.script.objectives
    for key, item in objectives.items():
        at.selectbox(key="objective").set_value(key).run()
        assert not at.exception
        shown = [m.value for m in at.success] + [m.value for m in at.warning]
        assert PROPOSALS[item.decision].explanation in shown
    key = next(k for k, item in objectives.items() if item.decision == HARD[0])
    at.selectbox(key="objective").set_value(key).run()
    setting = PROPOSALS[HARD[0]].setting
    at.button(key="objective_load").click().run()
    assert not at.exception
    assert at.session_state["bank"] == setting.bank_key
    assert at.session_state["scenario"] == setting.scenario_key
    assert at.session_state["design"].bank_key == setting.bank_key
    at.multiselect(key="map_decisions").set_value(["ela", "declare_foltf", "resolution_tool"]).run()
    assert not at.exception
    assert any("32 combinations" in caption.value for caption in at.caption)
