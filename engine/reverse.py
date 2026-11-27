"""Reverse mode (SPEC_V2 phase E): from a target outcome to the parameters that produce it.

Pure functions, no random numbers: the same target always gives the same answer.

The target is the day on which the model raises its "likely to fail" flag on the path
without decisions, the cause of the flag, and a band for the deposits lost by that day.
The solver searches three variables within their low and high values: the scenario's
severity, one scale on every news shock of the scenario, and one scale on the archetype's
run-off speed factor. It returns the matching set closest to the central values, or the
closest miss with each gap stated.

CLI: python -m engine.reverse --bank universal --scenario fast_run --flag-day 2 \
         --cause liquidity --deposits-lost 20 30
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Optional, Sequence

from engine.config import DEFAULT_CONFIG_DIR, load_config
from engine.model import SimulationResult, simulate
from engine.schema import WEEKDAYS, Bank, ModelParams, Parameter, Scenario

CAUSE_LIQUIDITY = "liquidity"
CAUSE_CAPITAL = "capital"
CAUSE_BOTH = "liquidity and capital"
CAUSES = (CAUSE_LIQUIDITY, CAUSE_CAPITAL)
# Figure that crossed its threshold when the flag was raised -> cause.
_CAUSE_OF = {
    "survival_days": CAUSE_LIQUIDITY,
    "unmet_outflow": CAUSE_LIQUIDITY,
    "cet1_ratio": CAUSE_CAPITAL,
    "cet1_ratio_projected": CAUSE_CAPITAL,
}

SEVERITY = "severity"
NEWS_SCALE = "news_shock_scale"
SPEED_SCALE = "speed_factor_scale"
VARIABLE_LABELS = {
    SEVERITY: "Scenario severity",
    NEWS_SCALE: "Scale of the news shocks",
    SPEED_SCALE: "Scale of the archetype's run-off speed",
}
# Points per variable on the first grid, and on each refinement around the best points.
_GRID = (9, 7, 7)
_REFINE_POINTS = 5
_REFINE_ROUNDS = 2
_REFINE_SEEDS = 3
# Weights of the gaps of a miss: one day counts as much as ten percentage points of deposits.
_DAY_WEIGHT = 10.0
_CAUSE_WEIGHT = 5.0


@dataclass(frozen=True)
class Target:
    flag_day: int
    cause: str  # liquidity | capital
    deposits_lost: tuple[float, float]  # band, % of opening deposits lost by the flag day
    weekday: Optional[str] = None  # weekday the flag day must fall on

    def __post_init__(self) -> None:
        if self.flag_day < 1:
            raise ValueError("the flag day must be 1 or later")
        if self.cause not in CAUSES:
            raise ValueError(f"cause must be one of {list(CAUSES)}")
        low, high = self.deposits_lost
        if not 0 <= low <= high <= 100:
            raise ValueError("deposits lost: expected 0 <= low <= high <= 100")
        if self.weekday is not None and self.weekday not in WEEKDAYS:
            raise ValueError(f"weekday must be one of {list(WEEKDAYS)}")


@dataclass(frozen=True)
class Variable:
    """One solved variable. `plausibility` is its distance from the central value as a
    share of the distance to the bound on that side: 0 = central, 1 = at the bound."""

    name: str
    label: str
    value: float
    central: float
    low: float
    high: float
    plausibility: float


@dataclass(frozen=True)
class Solution:
    feasible: bool
    variables: tuple[Variable, ...]
    start_weekday: str
    bank: Bank  # with the solved speed factor
    scenario: Scenario  # with the solved severity, news shocks and start weekday
    result: SimulationResult  # the run of the solved set, without decisions
    flag_day: Optional[int]
    cause: Optional[str]
    deposits_lost_pct: Optional[float]  # at the flag day
    gaps: tuple[str, ...]  # empty when feasible
    runs: int  # simulations used

    @property
    def verdict(self) -> str:
        return "feasible" if self.feasible else "infeasible"


# --------------------------------------------------------------------------- pieces


def flag_cause(result: SimulationResult) -> Optional[str]:
    """Why the "likely to fail" flag was raised, from the figures of its event."""
    event = next((e for e in result.events if e.kind == "likely_to_fail"), None)
    if event is None:
        return None
    causes = {_CAUSE_OF[name] for name, _, _ in event.figures if name in _CAUSE_OF}
    if len(causes) == 2:
        return CAUSE_BOTH
    return causes.pop() if causes else None


def start_weekday_for(scenario: Scenario, target: Target) -> str:
    """The start weekday that puts the target's flag day on its weekday."""
    if target.weekday is None:
        return scenario.start_weekday
    return WEEKDAYS[(WEEKDAYS.index(target.weekday) - (target.flag_day - 1)) % 7]


def ranges(scenario: Scenario, params: ModelParams) -> dict[str, Parameter]:
    """The three search variables with their central, low and high values."""
    if scenario.severity.low is None:
        raise ValueError("the scenario's severity has no low and high value to search")
    return {
        SEVERITY: scenario.severity,
        NEWS_SCALE: params.reverse_news_shock_scale,
        SPEED_SCALE: params.reverse_speed_factor_scale,
    }


def apply(
    bank: Bank, scenario: Scenario, start_weekday: str, severity: float, news_scale: float,
    speed_scale: float,
) -> tuple[Bank, Scenario]:
    """The bank and scenario with the three variables set."""
    speed = bank.runoff_speed_factor
    solved_bank = bank.model_copy(update={
        "runoff_speed_factor": speed.model_copy(update={"value": speed.value * speed_scale}),
    })
    solved_scenario = scenario.model_copy(update={
        "severity": scenario.severity.model_copy(update={"value": severity}),
        "start_weekday": start_weekday,
        "news_events": [
            event.model_copy(update={"panic_shock": event.panic_shock * news_scale})
            for event in scenario.news_events
        ],
    })
    return solved_bank, solved_scenario


def plausibility(value: float, parameter: Parameter) -> float:
    bound = parameter.high if value >= parameter.value else parameter.low
    span = abs(bound - parameter.value)
    if span <= 0:
        return 0.0
    return min(1.0, abs(value - parameter.value) / span)


def _observed(result: SimulationResult) -> tuple[Optional[int], Optional[str], Optional[float]]:
    day = result.likely_to_fail_day
    if day is None:
        return None, None, None
    return day, flag_cause(result), float(result.rows[day]["cum_deposit_loss_pct"])


def _gaps(target: Target, observed, horizon: int) -> tuple[float, tuple[str, ...]]:
    """Size of the miss (0 = match) and its description, gap by gap."""
    day, cause, lost = observed
    if day is None:
        return _DAY_WEIGHT * (horizon + 1 - target.flag_day) + _CAUSE_WEIGHT + 100.0, (
            f"flag day: the flag is not raised within {horizon} days (target: day {target.flag_day})",
        )
    size, texts = 0.0, []
    if day != target.flag_day:
        size += _DAY_WEIGHT * abs(day - target.flag_day)
        days = abs(day - target.flag_day)
        texts.append(
            f"flag day: day {day}, {days} day{'s' if days > 1 else ''} "
            f"{'later' if day > target.flag_day else 'earlier'} than the target"
        )
    if cause != target.cause:
        size += _CAUSE_WEIGHT
        texts.append(f"cause: {cause}, target {target.cause}")
    low, high = target.deposits_lost
    if lost < low or lost > high:
        gap = low - lost if lost < low else lost - high
        size += gap
        texts.append(
            f"deposits lost by the flag day: {lost:.1f}%, {gap:.1f} percentage points "
            f"{'below' if lost < low else 'above'} the band {low:g}% to {high:g}%"
        )
    return size, tuple(texts)


def _axis(low: float, high: float, points: int) -> list[float]:
    if points == 1 or high <= low:
        return [low]
    return [low + (high - low) * i / (points - 1) for i in range(points)]


# --------------------------------------------------------------------------- solver


def solve(bank: Bank, params: ModelParams, scenario: Scenario, target: Target) -> Solution:
    """Search the three variables for the target. Deterministic: a grid, then refinements
    around the best points. The run is the path without decisions."""
    space = ranges(scenario, params)
    names = (SEVERITY, NEWS_SCALE, SPEED_SCALE)
    central = tuple(space[name].value for name in names)
    weekday = start_weekday_for(scenario, target)
    horizon = int(params.with_overrides(scenario.overrides).horizon_days.value)
    seen: dict[tuple[float, ...], tuple[float, float, tuple[str, ...], SimulationResult]] = {}

    def distance(point: tuple[float, ...]) -> float:
        return sum(plausibility(value, space[name]) ** 2 for name, value in zip(names, point))

    def evaluate(point: tuple[float, ...]) -> None:
        point = tuple(round(value, 6) for value in point)
        if point in seen:
            return
        solved_bank, solved_scenario = apply(bank, scenario, weekday, *point)
        result = simulate(solved_bank, params, solved_scenario)
        size, texts = _gaps(target, _observed(result), horizon)
        seen[point] = (size, distance(point), texts, result)

    def ranked() -> list[tuple[float, ...]]:
        # Matches first (miss size 0), the closest to the central values first; then misses
        # by size. The point itself breaks ties, so the order never depends on insertion.
        return sorted(seen, key=lambda point: (seen[point][0], seen[point][1], point))

    evaluate(central)
    if seen[ranked()[0]][0] > 0:
        axes = [_axis(space[name].low, space[name].high, n) for name, n in zip(names, _GRID)]
        for severity in axes[0]:
            for news in axes[1]:
                for speed in axes[2]:
                    evaluate((severity, news, speed))
    steps = [
        (space[name].high - space[name].low) / (n - 1) for name, n in zip(names, _GRID)
    ]
    for _ in range(_REFINE_ROUNDS):
        if seen[ranked()[0]][1] == 0.0 and seen[ranked()[0]][0] == 0.0:
            break  # the central values already match
        for seed in ranked()[:_REFINE_SEEDS]:
            axes = [
                _axis(max(space[name].low, value - step), min(space[name].high, value + step),
                      _REFINE_POINTS)
                for name, value, step in zip(names, seed, steps)
            ]
            for severity in axes[0]:
                for news in axes[1]:
                    for speed in axes[2]:
                        evaluate((severity, news, speed))
        steps = [step / 2 for step in steps]

    best = ranked()[0]
    size, _, texts, result = seen[best]
    solved_bank, solved_scenario = apply(bank, scenario, weekday, *best)
    day, cause, lost = _observed(result)
    return Solution(
        feasible=size == 0,
        variables=tuple(
            Variable(name, VARIABLE_LABELS[name], value, space[name].value, space[name].low,
                     space[name].high, plausibility(value, space[name]))
            for name, value in zip(names, best)
        ),
        start_weekday=weekday,
        bank=solved_bank,
        scenario=solved_scenario,
        result=result,
        flag_day=day,
        cause=cause,
        deposits_lost_pct=lost,
        gaps=texts,
        runs=len(seen),
    )


def solution_lines(solution: Solution, target: Target) -> list[str]:
    """The solution in plain words, for the CLI and the app."""
    low, high = target.deposits_lost
    on = f" ({target.weekday})" if target.weekday else ""
    lines = [
        f"Target: \"likely to fail\" flag on day {target.flag_day}{on}, cause {target.cause}, "
        f"deposits lost by then between {low:g}% and {high:g}%.",
    ]
    if solution.feasible:
        lines.append("Result: feasible. The parameter set below matches the target.")
    else:
        lines.append(
            "Result: infeasible within the ranges. Closest parameter set below; gaps: "
            + "; ".join(solution.gaps) + "."
        )
    if solution.flag_day is None:
        lines.append("Run of this set: the flag is not raised.")
    else:
        lines.append(
            f"Run of this set: flag on day {solution.flag_day} "
            f"({solution.scenario.weekday(solution.flag_day)}), cause {solution.cause}, "
            f"deposits lost {solution.deposits_lost_pct:.1f}%. Day 1 is a {solution.start_weekday}."
        )
    for variable in solution.variables:
        lines.append(
            f"- {variable.label}: {variable.value:.3f} (central {variable.central:.3f}, range "
            f"{variable.low:.3f} to {variable.high:.3f}); distance from central "
            f"{variable.plausibility:.0%} of the way to the bound"
        )
    lines.append(f"Simulations used: {solution.runs}.")
    return lines


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Reverse mode: solve for a target outcome.")
    parser.add_argument("--bank", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--flag-day", type=int, required=True)
    parser.add_argument("--cause", choices=CAUSES, default=CAUSE_LIQUIDITY)
    parser.add_argument("--deposits-lost", type=float, nargs=2, default=(0.0, 100.0),
                        metavar=("LOW", "HIGH"))
    parser.add_argument("--weekday", choices=WEEKDAYS)
    parser.add_argument("--config-dir", default=str(DEFAULT_CONFIG_DIR))
    args = parser.parse_args(argv)
    config = load_config(args.config_dir)
    target = Target(args.flag_day, args.cause, tuple(args.deposits_lost), args.weekday)
    solution = solve(config.banks[args.bank], config.model, config.scenarios[args.scenario], target)
    print("\n".join(solution_lines(solution, target)))


if __name__ == "__main__":
    main()
