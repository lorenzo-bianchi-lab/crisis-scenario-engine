"""Design by objective and decision map (SPEC_V3 phase E).

Pure functions on the central parameters, without challenge injects.

Design by objective: the design team names a decision it wants to exercise. For every bank
and scenario, and for the decision taken on its opening day or one or two days later, each
option is run. A decision is "hard" where its options lead to clearly different outcomes
(spread) and no option is at least as low as every other option on every compared line
(no dominance). Lower is taken as preferred on each line: an assumption. The lines are the
outcome, ranked (config/script.yaml), four lines of the scorecard, what creditors other than
shareholders bear, and the days until covered depositors are paid. Decisions that must come
first, and decisions that depend on the one exercised, are taken at their first close.

Decision map: for one bank and scenario, every combination of options of up to four
decisions, each taken at its first possible close.

CLI: python -m engine.objective --decision resolution_tool
     python -m engine.objective --map declare_foltf resolution_tool --bank retail --scenario geopolitical
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from itertools import product
from typing import Optional, Sequence

from engine.config import DEFAULT_CONFIG_DIR, load_config
from engine.decisions import (
    OUTCOME_EMERGENCY_FUNDING, OUTCOME_LABELS, OUTCOME_UNDER_STRESS, Choice, Scorecard,
)
from engine.model import SimulationResult, simulate
from engine.resolution import resolution_summary
from engine.schema import Bank, Config, DecisionsConfig, ModelParams, Scenario

# Compared lines of the scorecard: (field, label, digits, suffix; "unit" = the bank's unit).
# Compared lines: (field, label, digits, suffix). Suffix "unit" = the bank's unit; "rank" =
# the ranked outcome; "days" = whole days. A field is read from the option's run, or from
# its scorecard.
LINES = (
    ("outcome_rank", "the outcome", 0, "rank"),
    ("deposits_lost_pct", "deposits lost", 1, "%"),
    ("losses_booked", "losses booked", 2, "unit"),
    ("ela_outstanding", "ELA outstanding at the end", 2, "unit"),
    ("dgs_payout", "DGS payout", 2, "unit"),
    ("creditor_loss", "borne by creditors other than shareholders", 2, "unit"),
    ("payout_days", "days until covered depositors are paid", 0, "days"),
)
# Layers of the liability stack counted as creditors (not shareholders, not covered deposits).
CREDITOR_LAYERS = ("at1_t2", "senior_non_preferred", "senior_preferred", "uncovered_deposits")
DELAYS = (0, 1, 2)  # days after the decision opens
MAX_MAP_DECISIONS = 4
_TOLERANCE = 1e-9


@dataclass(frozen=True)
class OptionRun:
    option: str
    option_name: str
    scorecard: Scorecard
    outcome_rank: int = 0  # the outcome's rank (config/script.yaml); lower is preferred
    # Written down, converted or lost by creditors other than shareholders, at the central
    # write-down level; 0 if the run has no resolution or liquidation.
    creditor_loss: float = 0.0
    payout_days: int = 0  # days until covered depositors are paid; 0 if there is no payout


@dataclass(frozen=True)
class Setting:
    """One bank, scenario and timing in which the decision was run with each option."""

    bank_key: str
    scenario_key: str
    open_day: int  # first close at which the decision can be taken
    day: int  # close at which it is taken here
    prerequisites: tuple[Choice, ...]
    dependents: tuple[Choice, ...]  # later decisions, taken at their first close
    runs: tuple[OptionRun, ...]
    spread: float
    distinct_outcomes: int
    dominant: Optional[str]  # option at least as low as every other on every line, if any

    @property
    def delay(self) -> int:
        return self.day - self.open_day


@dataclass(frozen=True)
class Proposal:
    decision: str
    decision_name: str
    hard: bool  # a setting without a dominant option was found
    setting: Optional[Setting]  # the proposed one, or the widest one if none is hard
    explanation: str
    settings: int  # settings in which the decision opens


# --------------------------------------------------------------------------- pieces


def prerequisites(decisions: DecisionsConfig, decision: str) -> tuple[Choice, ...]:
    """The decisions that must come first, each at its first possible close, in order."""
    chain: list[Choice] = []
    condition = decisions.decisions[decision].condition
    while condition.after is not None:
        before = decisions.decisions[condition.after]
        option = condition.after_option
        if option is None:
            option = next(key for key in before.options if key != before.default_option)
        chain.insert(0, Choice(condition.after, option, 0))
        condition = before.condition
    return tuple(chain)


def dependents(decisions: DecisionsConfig, decision: str, option: str) -> tuple[Choice, ...]:
    """The decisions that can only follow `option` of `decision`, and those that follow
    them, each at its first possible close with its first option that is not the default."""
    out: list[Choice] = []
    for key, later in decisions.decisions.items():
        condition = later.condition
        if condition.after != decision or condition.after_option not in (None, option):
            continue
        chosen = next(name for name in later.options if name != later.default_option)
        out.append(Choice(key, chosen, 0))
        out.extend(dependents(decisions, key, chosen))
    return tuple(out)


def has_dependents(decisions: DecisionsConfig, decision: str) -> bool:
    return any(item.condition.after == decision for item in decisions.decisions.values())


def line_value(run: OptionRun, line: str) -> float:
    """The value of a compared line for one option."""
    return float(getattr(run, line) if line in ("outcome_rank", "creditor_loss", "payout_days")
                 else getattr(run.scorecard, line))


def _scaled(run: OptionRun, line: str, suffix: str, opening_deposits: float,
            top_rank: int, horizon: int) -> float:
    value = line_value(run, line)
    if suffix == "%":
        return value / 100.0
    if suffix == "rank":
        return value / max(1, top_rank)
    if suffix == "days":
        return value / max(1, horizon)
    return value / opening_deposits


def spread_of(runs: Sequence[OptionRun], opening_deposits: float, top_rank: int = 1,
              horizon: int = 30) -> tuple[float, int]:
    """Spread across the options: distinct outcomes beyond the first, plus the range of each
    compared line (amounts as a share of opening deposits, the rank as a share of the worst
    rank, days as a share of the horizon). Also the number of outcomes."""
    outcomes = len({run.scorecard.outcome for run in runs})
    total = float(outcomes - 1)
    for line, _, _, suffix in LINES:
        values = [_scaled(run, line, suffix, opening_deposits, top_rank, horizon) for run in runs]
        total += max(values) - min(values)
    return total, outcomes


def dominant_option(runs: Sequence[OptionRun]) -> Optional[str]:
    """The first option that is at least as low as every other option on every line."""
    for run in runs:
        if all(
            line_value(run, line) <= line_value(other, line) + _TOLERANCE
            for other in runs for line, *_ in LINES
        ):
            return run.option
    return None


FLAGGED = "_likely_to_fail"  # suffix of the rank of an open outcome with the flag still raised


def rank_key(result: SimulationResult) -> str:
    """The key of a run's rank in config/script.yaml: its outcome, or, for a bank left open
    with the model's "likely to fail" flag still raised on its last day, that outcome with
    the suffix `_likely_to_fail`."""
    flagged = result.outcome in (OUTCOME_UNDER_STRESS, OUTCOME_EMERGENCY_FUNDING) \
        and bool(result.rows[-1].get("flag_active"))
    return result.outcome + (FLAGGED if flagged else "")


def option_run(
    bank: Bank, params: ModelParams, result: SimulationResult, option: str, option_name: str,
    outcome_rank: dict[str, int],
) -> OptionRun:
    """One option's run with the values of the lines the scorecard does not hold."""
    summary = resolution_summary(bank, params, result)
    lines = () if summary is None else (
        summary.liquidation.lines if summary.liquidation is not None else summary.lines)
    return OptionRun(
        option, option_name, result.scorecard,
        outcome_rank=int(outcome_rank[rank_key(result)]),
        creditor_loss=sum(line.absorbed for line in lines if line.key in CREDITOR_LAYERS),
        payout_days=int(result.scorecard.payout_days or 0),
    )


def _decided(result: SimulationResult, decision: str) -> Optional[tuple[str, int]]:
    record = next((r for r in result.decision_log if r.decision == decision), None)
    return None if record is None else (record.option, record.decided_day)


def settings_for(
    bank_key: str, bank: Bank, params: ModelParams, scenario_key: str, scenario: Scenario,
    decisions: DecisionsConfig, decision: str, outcome_rank: dict[str, int],
) -> list[Setting]:
    """The decision run with each option, on its opening day and one and two days later.
    With an option taken, the decisions that depend on it follow at their first close."""
    definition = decisions.decisions[decision]
    before = prerequisites(decisions, decision)
    probe_option = next(key for key in definition.options if key != definition.default_option)
    probe = simulate(bank, params, scenario, decisions, [*before, Choice(decision, probe_option, 0)])
    opened = _decided(probe, decision)
    if opened is None:
        return []
    open_day = opened[1]
    opening_deposits = bank.liabilities.deposits.total()
    top_rank, horizon = max(outcome_rank.values()), int(params.horizon_days.value)
    after = {option: dependents(decisions, decision, option) for option in definition.options}
    out = []
    for delay in DELAYS:
        day = open_day + delay
        runs = []
        for option, item in definition.options.items():
            if option == definition.default_option:
                result = simulate(bank, params, scenario, decisions, before)
            else:
                result = simulate(bank, params, scenario, decisions,
                                  [*before, Choice(decision, option, day), *after[option]])
                if _decided(result, decision) != (option, day):
                    break  # the run ends before this close
            runs.append(option_run(bank, params, result, option, item.name, outcome_rank))
        else:
            spread, outcomes = spread_of(runs, opening_deposits, top_rank, horizon)
            later = tuple(dict.fromkeys(choice for option in definition.options
                                        for choice in after[option]))
            out.append(Setting(bank_key, scenario_key, open_day, day, before, later,
                               tuple(runs), spread, outcomes, dominant_option(runs)))
    return out


def all_settings(config: Config, decision: str) -> list[Setting]:
    """Every bank and every crisis scenario (those with a place in the taxonomy)."""
    out = []
    for bank_key, bank in config.banks.items():
        for scenario_key, scenario in config.scenarios.items():
            if scenario.taxonomy is None:
                continue
            out.extend(settings_for(bank_key, bank, config.model, scenario_key, scenario,
                                    config.decisions, decision, outcome_ranks(config)))
    return out


def outcome_ranks(config: Config) -> dict[str, int]:
    return {outcome: int(rank) for outcome, rank in config.script.objective_outcome_rank.items()}


# --------------------------------------------------------------------------- proposal


def _amount(value: float, digits: int, suffix: str, unit: str) -> str:
    if suffix == "days":
        return "no payout" if value == 0 else f"{value:.0f} days"
    return f"{value:.{digits}f}{'%' if suffix == '%' else ' ' + unit}"


def explain(config: Config, decision: str, setting: Setting) -> str:
    """One paragraph, built from the numbers of the setting, on why the decision is hard."""
    texts = config.script.objective_texts
    bank, scenario = config.banks[setting.bank_key], config.scenarios[setting.scenario_key]
    name = config.decisions.decisions[decision].name
    timing = (
        "the first close at which it can be taken" if setting.delay == 0 else
        f"{setting.delay} day{'s' if setting.delay > 1 else ''} after it can first be taken"
    )
    parts = [texts["proposal"].format(
        decision_name=name, bank_name=bank.name, scenario_name=scenario.name, day=setting.day,
        weekday=scenario.weekday(setting.day), timing=timing,
    )]
    by_outcome: dict[str, list[str]] = {}
    for run in setting.runs:
        by_outcome.setdefault(OUTCOME_LABELS[run.scorecard.outcome], []).append(f"\"{run.option_name}\"")
    if len(by_outcome) == 1:
        parts.append(texts["one_outcome"].format(outcome=next(iter(by_outcome))))
    else:
        parts.append(texts["outcomes"].format(
            count=len(by_outcome),
            outcomes="; ".join(
                f"{label} with {', '.join(options)}" for label, options in by_outcome.items()),
        ))
    lowest, ranges = [], []
    for line, label, digits, suffix in LINES:
        values = [line_value(run, line) for run in setting.runs]
        if round(max(values), digits) == round(min(values), digits):
            continue
        best = [f"\"{run.option_name}\"" for run in setting.runs
                if line_value(run, line) <= min(values) + _TOLERANCE]
        lowest.append(f"{' and '.join(best)} on {label}")
        if suffix == "rank":
            continue  # the outcomes are named above
        ranges.append(f"{label} from {_amount(min(values), digits, suffix, bank.unit)} to "
                      f"{_amount(max(values), digits, suffix, bank.unit)}")
    if lowest:
        parts.append(texts["trade_off"].format(lowest="; ".join(lowest)))
    if ranges:
        parts.append(texts["ranges"].format(ranges="; ".join(ranges)))
    if setting.dependents:
        parts.append(texts["followed_by"].format(later=_later(config, setting)))
    return " ".join(parts)


def _later(config: Config, setting: Setting) -> str:
    catalogue = config.decisions.decisions
    return "; ".join(
        f"\"{catalogue[c.decision].name}\" with \"{catalogue[c.decision].options[c.option].name}\""
        for c in setting.dependents)


def propose(config: Config, decision: str) -> Proposal:
    """The bank, scenario and timing in which the decision is hardest: the largest spread
    among the settings where no option dominates. If an option dominates everywhere, say so."""
    name = config.decisions.decisions[decision].name
    texts = config.script.objective_texts
    settings = all_settings(config, decision)
    if not settings:
        return Proposal(decision, name, False, None,
                        texts["never_open"].format(decision_name=name), 0)
    # max() keeps the first of equal spreads: banks and scenarios in the order of the config.
    hard = [setting for setting in settings if setting.dominant is None]
    if hard:
        best = max(hard, key=lambda setting: setting.spread)
        return Proposal(decision, name, True, best, explain(config, decision, best), len(settings))
    widest = max(settings, key=lambda setting: setting.spread)
    if widest.spread <= _TOLERANCE:
        # Never "for the script, not for the outcome" where a later decision depends on it.
        kind = "no_difference_dependents" if has_dependents(config.decisions, decision) else "no_difference"
        text = texts[kind].format(decision_name=name)
    else:
        option = next(run.option_name for run in widest.runs if run.option == widest.dominant)
        text = texts["dominated"].format(
            decision_name=name, option_name=option, bank_name=config.banks[widest.bank_key].name,
            scenario_name=config.scenarios[widest.scenario_key].name, day=widest.day,
        )
    return Proposal(decision, name, False, widest, text, len(settings))


def setting_rows(setting: Setting, unit: str) -> list[dict[str, object]]:
    """The options of a setting side by side, as rows."""
    return [
        {
            "Option": run.option_name,
            "Outcome": OUTCOME_LABELS[run.scorecard.outcome],
            "Last day": run.scorecard.end_day,
            "Deposits lost (%)": run.scorecard.deposits_lost_pct,
            f"Losses booked ({unit})": run.scorecard.losses_booked,
            f"ELA outstanding ({unit})": run.scorecard.ela_outstanding,
            f"DGS payout ({unit})": run.scorecard.dgs_payout,
            f"Borne by creditors ({unit})": run.creditor_loss,
            "Days to payout": str(run.payout_days) if run.payout_days else "no payout",
        }
        for run in setting.runs
    ]


# --------------------------------------------------------------------------- decision map


@dataclass(frozen=True)
class Leaf:
    """One combination of options. `taken` says, per decision, whether the option was
    actually taken in the run (a decision whose condition is never met stays untaken)."""

    options: tuple[str, ...]
    option_names: tuple[str, ...]
    taken: tuple[bool, ...]
    outcome: str
    scorecard: Scorecard


@dataclass(frozen=True)
class DecisionMap:
    decisions: tuple[str, ...]
    decision_names: tuple[str, ...]
    leaves: tuple[Leaf, ...]


def map_choices(decisions: DecisionsConfig, keys: Sequence[str], options: Sequence[str]) -> list[Choice]:
    """The decision set of one combination: every non-default option at its first close."""
    return [
        Choice(key, option, 0) for key, option in zip(keys, options)
        if option != decisions.decisions[key].default_option
    ]


def decision_map(
    bank: Bank, params: ModelParams, scenario: Scenario, decisions: DecisionsConfig,
    keys: Sequence[str],
) -> DecisionMap:
    """The tree of outcomes over all option combinations of the chosen decisions."""
    keys = tuple(keys)
    if not 1 <= len(keys) <= MAX_MAP_DECISIONS or len(set(keys)) != len(keys):
        raise ValueError(f"pick 1 to {MAX_MAP_DECISIONS} different decisions")
    definitions = [decisions.decisions[key] for key in keys]
    leaves = []
    for options in product(*(definition.options for definition in definitions)):
        result = simulate(bank, params, scenario, decisions, map_choices(decisions, keys, options))
        taken = tuple(
            option == definition.default_option or _decided(result, key) is not None
            for key, definition, option in zip(keys, definitions, options)
        )
        leaves.append(Leaf(
            options, tuple(d.options[o].name for d, o in zip(definitions, options)), taken,
            result.outcome, result.scorecard,
        ))
    return DecisionMap(keys, tuple(definition.name for definition in definitions), tuple(leaves))


# --------------------------------------------------------------------------- CLI


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Design by objective, or a decision map.")
    parser.add_argument("--decision", help="decision to exercise (design by objective)")
    parser.add_argument("--map", nargs="+", metavar="DECISION", help="decisions of a decision map")
    parser.add_argument("--bank")
    parser.add_argument("--scenario")
    parser.add_argument("--config-dir", default=str(DEFAULT_CONFIG_DIR))
    args = parser.parse_args(argv)
    config = load_config(args.config_dir)
    if args.map:
        showcase = config.script.showcase
        bank = config.banks[args.bank or showcase.bank]
        scenario = config.scenarios[args.scenario or showcase.scenario]
        tree = decision_map(bank, config.model, scenario, config.decisions, args.map)
        print(f"{bank.name}, {scenario.name}: {len(tree.leaves)} combinations of "
              + ", ".join(tree.decision_names))
        for leaf in tree.leaves:
            print("  " + " | ".join(
                name if taken else f"{name} (not open)"
                for name, taken in zip(leaf.option_names, leaf.taken)
            ) + f" -> {OUTCOME_LABELS[leaf.outcome]}, day {leaf.scorecard.end_day}, "
                f"deposits lost {leaf.scorecard.deposits_lost_pct:.1f}%")
        return
    if not args.decision:
        parser.error("give --decision or --map")
    proposal = propose(config, args.decision)
    print(proposal.explanation)
    if proposal.setting is not None:
        unit = config.banks[proposal.setting.bank_key].unit
        for row in setting_rows(proposal.setting, unit):
            print("  " + "; ".join(
                f"{key}: {value:.2f}" if isinstance(value, float) else f"{key}: {value}"
                for key, value in row.items()))
    print(config.script.objective_texts["directions"])


if __name__ == "__main__":
    main()
