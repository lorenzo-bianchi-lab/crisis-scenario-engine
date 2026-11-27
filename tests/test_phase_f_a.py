"""SPEC_V2 phase F, part a: a parent and a subsidiary simulated in step (engine/group.py)."""

import math
import shutil

import pytest
import yaml

from engine.config import DEFAULT_CONFIG_DIR, load_config
from engine.group import (
    PARENT,
    SUBSIDIARY,
    GroupChoices,
    consolidate,
    group_scenarios,
    main,
    simulate_group,
    support_available,
)
from engine.model import (
    DEPOSIT_CATEGORIES, DayInput, DayNeed, DayOpen, Transfer, daily_run, simulate,
)
from engine.schema import GROUP_ENTITIES

CONFIG = load_config()
GROUP, LARGE = CONFIG.groups["showcase"], CONFIG.groups["large"]
CRISES = [key for key, scenario in CONFIG.scenarios.items() if scenario.taxonomy is not None]
TOL = 1e-9
BALANCES = [f"dep_{name}" for name in DEPOSIT_CATEGORIES] + [
    "wholesale_funding", "central_bank_funding", "ela_funding", "other_liabilities", "cash",
    "hqla_level1", "hqla_level2", "eligible_collateral", "encumbered_collateral",
    "illiquid_assets", "intragroup_claim", "intragroup_liability",
]


def run(scenario="fast_run", group=GROUP, stressed=PARENT, scenarios=None, **choices):
    return simulate_group(group, CONFIG.banks, CONFIG.model, scenarios or CONFIG.scenarios,
                          scenario, CONFIG.decisions, GroupChoices(**choices), stressed)


def with_values(group, **values):
    return group.model_copy(update={
        name: getattr(group, name).model_copy(update={"value": value})
        for name, value in values.items()})


CASES = [(g, s, c) for g in ("showcase", "large") for s in CRISES
         for c in ({}, {"ring_fence_day": 2}, {"ring_fence_day": 4})]


@pytest.fixture(scope="module")
def results():
    return {(g, s, tuple(c.items())): run(s, CONFIG.groups[g], **c) for g, s, c in CASES}


# ------------------------------------------------- the stepper leaves a single bank alone


@pytest.mark.parametrize("bank", list(CONFIG.banks))
@pytest.mark.parametrize("scenario", ["fast_run", "geopolitical", "cyber_outage"])
def test_a_bank_stepped_with_empty_answers_is_the_bank_simulated_alone(bank, scenario):
    """The generator stopped twice a day and given nothing gives `simulate()`'s result."""
    args = (CONFIG.banks[bank], CONFIG.model, CONFIG.scenarios[scenario], CONFIG.decisions, ())
    alone = simulate(*args)
    stepped = daily_run(*args, grouped=True)
    seen = []
    try:
        item = next(stepped)
        while True:
            seen.append(item)
            item = stepped.send(DayInput() if isinstance(item, DayOpen) else Transfer())
    except StopIteration as finished:
        result = finished.value
    assert [type(item) for item in seen[:2]] == [DayOpen, DayNeed]
    assert [item.day for item in seen if isinstance(item, DayOpen)] == list(range(1, alone.end_day + 1))
    assert (result.outcome, result.end_day, result.events) == (alone.outcome, alone.end_day, alone.events)
    for mine, theirs in zip(result.rows, alone.rows):
        assert {name: mine[name] for name in theirs} == theirs
        assert mine["intragroup_claim"] == mine["support_received"] == 0.0


def test_entities_that_cannot_reach_each_other_are_two_banks_alone():
    cut = with_values(GROUP, transfer_limit_share=0.0, confidence_link=0.0)
    together = run("fast_run", cut)
    scenarios = group_scenarios(cut, CONFIG.scenarios, "fast_run")
    for role in GROUP_ENTITIES:
        bank = CONFIG.banks[cut.entity(role).bank]
        alone = simulate(bank, CONFIG.model, scenarios[role], CONFIG.decisions, ())
        mine = together.of(role)
        assert (mine.outcome, mine.failure_day, mine.end_day) == (alone.outcome, alone.failure_day, alone.end_day)
        assert [row["deposits_total"] for row in mine.rows] == [row["deposits_total"] for row in alone.rows]
    assert together.transfers == ()


# ------------------------------------------------- sanity checks, per entity and consolidated


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c[0]}-{c[1]}-{'-'.join(c[2]) or 'free'}")
def test_balance_sheets_balance_and_nothing_is_negative(results, case):
    result = results[(case[0], case[1], tuple(case[2].items()))]
    for role in GROUP_ENTITIES:
        table = result.of(role).table
        gap = table["total_assets"] - (table["total_liabilities"] + table["cet1"])
        assert gap.abs().max() < 1e-6, role
        assert (table[BALANCES] >= -TOL).all().all(), role
    for row in result.consolidated:
        assert abs(row["total_assets"] - row["total_liabilities"] - row["cet1"]) < 1e-6
        assert abs(row["intragroup_gap"]) < 1e-9  # what one lent, the other owes
    # The consolidated view is the sum of the two, less the intragroup positions.
    for index, row in enumerate(result.consolidated):
        assert row == consolidate(result.parent.rows[index], result.subsidiary.rows[index])


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c[0]}-{c[1]}-{'-'.join(c[2]) or 'free'}")
def test_transfers_stay_within_the_limit_the_floor_and_the_need(results, case):
    result = results[(case[0], case[1], tuple(case[2].items()))]
    group = result.group
    for item in result.transfers:
        lender, borrower = result.of(item.lender), result.of(item.borrower)
        before, after = lender.rows[item.day - 1], lender.rows[item.day]
        bank = CONFIG.banks[group.entity(item.lender).bank]
        assert item.amount > 0 and not after["weekend"] and not borrower.rows[item.day]["weekend"]
        limit = group.transfer_limit_share.value * bank.assets.total()
        floor = group.lender_cash_floor_share.value * bank.liabilities.deposits.total()
        assert after["intragroup_claim"] <= limit + TOL
        assert item.amount <= before["cash"] - floor + TOL
        assert after["support_given"] == pytest.approx(item.amount)
        assert borrower.rows[item.day]["support_received"] == pytest.approx(item.amount)
        # Support covers payments of the day: it never exceeds what the borrower had to pay.
        assert item.amount <= borrower.rows[item.day]["outflow_total"] + TOL
    if result.ring_fence_from is not None:
        assert not any(t.lender == SUBSIDIARY and t.day >= result.ring_fence_from
                       for t in result.transfers)


@pytest.mark.parametrize("group", ["showcase", "large"])
def test_with_zero_runoff_nothing_changes_and_nothing_is_transferred(group):
    scenarios = dict(CONFIG.scenarios)
    quiet = {"severity": CONFIG.scenarios["fast_run"].severity.model_copy(update={"value": 0.0}),
             "news_events": []}
    scenarios["fast_run"] = CONFIG.scenarios["fast_run"].model_copy(update=quiet)
    scenarios[CONFIG.groups[group].calm_scenario] = CONFIG.scenarios[
        CONFIG.groups[group].calm_scenario].model_copy(update=quiet)
    result = run("fast_run", CONFIG.groups[group], scenarios=scenarios)
    assert result.transfers == ()
    for role in GROUP_ENTITIES:
        table = result.of(role).table
        assert len(table) == int(CONFIG.model.horizon_days.value) + 1
        for column in BALANCES:
            assert (table[column] - table[column].iloc[0]).abs().max() < TOL, (role, column)
        assert (table["status"] == "normal").all() and result.of(role).events == ()


@pytest.mark.parametrize("group", ["showcase", "large"])
@pytest.mark.parametrize("scenario", ["fast_run", "geopolitical"])
def test_higher_severity_never_gives_the_stressed_entity_a_later_default(group, scenario):
    days = []
    severities = [i * 0.1 for i in range(0, 61)]
    for severity in severities:
        scenarios = dict(CONFIG.scenarios)
        base = CONFIG.scenarios[scenario]
        scenarios[scenario] = base.model_copy(
            update={"severity": base.severity.model_copy(update={"value": severity})})
        day = run(scenario, CONFIG.groups[group], scenarios=scenarios).parent.failure_day
        days.append(math.inf if day is None else day)
    for lower, higher in zip(days, days[1:]):
        assert higher <= lower, list(zip(severities, days))


# ------------------------------------------------- acceptance: ring-fencing decides the parent's fate


def test_ring_fencing_brings_the_parent_to_a_default_it_avoids_without_it():
    free = run("fast_run")
    assert free.parent.failure_day is None and free.parent.outcome == "under_stress"
    assert [t.day for t in free.transfers] == [4, 5]
    assert all(t.lender == SUBSIDIARY and t.borrower == PARENT for t in free.transfers)
    fenced = run("fast_run", ring_fence_day=2)
    assert fenced.ring_fence_from == 3 and fenced.transfers == ()
    assert fenced.parent.outcome == "payments_default" and fenced.parent.failure_day == 5
    # Without support the parent is the bank simulated alone, but for the news of the fence.
    alone = simulate(CONFIG.banks[GROUP.parent.bank], CONFIG.model, CONFIG.scenarios["fast_run"],
                     CONFIG.decisions, ())
    assert alone.failure_day == 5
    # It is the lost support that decides, not the news: the same with no announcement shock.
    silent = with_values(GROUP, ring_fence_shock=0.0)
    assert run("fast_run", silent, ring_fence_day=2).parent.failure_day == 5
    assert run("fast_run", silent).parent.failure_day is None
    # Later ring-fencing: some support got through, the default comes later; later still, none.
    assert run("fast_run", ring_fence_day=4).parent.failure_day == 8
    assert run("fast_run", ring_fence_day=6).parent.failure_day is None
    # The subsidiary pays for its support with cash, not with its solvency.
    assert free.subsidiary.failure_day is None
    assert min(r["cash"] for r in free.subsidiary.rows) < min(r["cash"] for r in fenced.subsidiary.rows)


def test_ring_fencing_is_announced_to_the_parent_the_day_it_takes_effect():
    fenced = run("fast_run", ring_fence_day=2)
    news = [e for e in fenced.parent.events if e.kind == "news" and "restricted transfers" in e.detail]
    assert [e.day for e in news] == [3]
    assert GROUP.subsidiary.country in news[0].detail and GROUP.subsidiary.name in news[0].detail
    assert not any("restricted transfers" in e.detail for e in run("fast_run").parent.events)


# ------------------------------------------------- confidence and losses pass between the entities


def test_a_stage_of_one_entity_is_news_for_the_other_the_next_day():
    result = run("fast_run", LARGE)
    stage_day = {e.kind: e.day for e in result.parent.events
                 if e.kind in ("recovery", "early_intervention", "failure")}
    news = [e for e in result.subsidiary.events if e.kind == "news" and LARGE.parent.name in e.detail]
    assert {e.day for e in news} == {day + 1 for day in stage_day.values()}
    assert len(news) == len(stage_day)
    # With no link the subsidiary hears nothing and is calmer.
    quiet = run("fast_run", with_values(LARGE, confidence_link=0.0))
    assert not [e for e in quiet.subsidiary.events if e.kind == "news"]
    assert quiet.subsidiary.scorecard.deposits_lost_pct < result.subsidiary.scorecard.deposits_lost_pct


def test_the_lender_writes_off_part_of_its_claim_when_the_borrower_defaults():
    result = run("fast_run", LARGE)
    assert result.parent.failure_day is not None and result.transfers
    lent = sum(t.amount for t in result.transfers)
    day = result.parent.failure_day + 1
    before, after = result.subsidiary.rows[day - 1], result.subsidiary.rows[day]
    share = LARGE.claim_loss_at_default.value
    assert before["intragroup_claim"] == pytest.approx(lent)
    assert after["intragroup_claim"] == pytest.approx(lent * (1 - share))
    assert before["cet1"] - after["cet1"] >= lent * share - 1e-6
    assert after["total_assets"] == pytest.approx(after["total_liabilities"] + after["cet1"])
    # A subsidiary that was ring-fenced in time lent nothing and loses nothing.
    fenced = run("fast_run", LARGE, ring_fence_day=0)
    assert fenced.transfers == () and fenced.subsidiary.rows[-1]["intragroup_claim"] == 0.0


def test_the_stress_can_start_at_the_subsidiary_and_the_parent_supports_it():
    result = run("fast_run", LARGE, stressed=SUBSIDIARY)
    assert result.stressed == SUBSIDIARY
    support = list(result.transfers)
    assert support and all(t.lender == PARENT for t in support)
    # Ring-fencing binds the subsidiary's lending only: the parent may still support it.
    fenced = run("fast_run", LARGE, stressed=SUBSIDIARY, ring_fence_day=0)
    assert [t.lender for t in fenced.transfers] == [t.lender for t in support]


def test_support_available_follows_the_floor_the_limit_and_the_lenders_own_need():
    opened = next(daily_run(CONFIG.banks["digital"], CONFIG.model, CONFIG.scenarios["baseline"],
                            CONFIG.decisions, (), grouped=True))
    state = opened.state
    need = DayNeed(1, state, 0.0, True)
    assets, deposits = 100.0, 50.0
    floor = GROUP.lender_cash_floor_share.value * deposits
    limit = GROUP.transfer_limit_share.value * assets
    assert support_available(need, assets, deposits, GROUP) == pytest.approx(
        min(state.cash - floor, limit))
    assert support_available(DayNeed(1, state, 0.5, True), assets, deposits, GROUP) == 0.0
    assert support_available(DayNeed(1, state, 0.0, False), assets, deposits, GROUP) == 0.0
    assert support_available(need, assets, deposits, with_values(GROUP, transfer_limit_share=0.0)) == 0.0


# ------------------------------------------------- config and CLI


def test_groups_are_assumptions_in_two_countries_with_known_banks(tmp_path):
    for group in CONFIG.groups.values():
        assert group.parent.country != group.subsidiary.country
        assert {group.parent.bank, group.subsidiary.bank} <= set(CONFIG.banks)
        for name in ("transfer_limit_share", "lender_cash_floor_share", "confidence_link",
                     "ring_fence_shock", "claim_loss_at_default"):
            assert getattr(group, name).source == "ASSUMPTION"
    shutil.copytree(DEFAULT_CONFIG_DIR, tmp_path / "config")
    path = tmp_path / "config" / "groups.yaml"
    data = yaml.safe_load(path.read_text())
    data["showcase"]["subsidiary"]["country"] = data["showcase"]["parent"]["country"]
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="two countries"):
        load_config(tmp_path / "config")
    data["showcase"]["subsidiary"]["country"] = "Country B"
    data["showcase"]["subsidiary"]["bank"] = "nonexistent"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="unknown bank"):
        load_config(tmp_path / "config")


def test_cli_prints_both_tables_and_the_transfers(capsys):
    assert main(["--scenario", "fast_run"]) == 0
    free = capsys.readouterr().out
    assert "Group A parent bank (Country A, parent): Still under stress" in free
    assert "Group A subsidiary lends 0.37 EUR bn to Group A parent bank" in free
    assert main(["--scenario", "fast_run", "--ring-fence", "2"]) == 0
    fenced = capsys.readouterr().out
    assert "payments default on day 5" in fenced and "Ring-fencing in effect from day 3." in fenced
    assert "No intragroup transfer." in fenced
