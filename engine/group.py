"""Cross-border group (SPEC_V2 phase F, part a): a parent and a subsidiary simulated in step.

Pure functions. Each entity runs the ordinary daily loop of engine/model.py on its own
balance sheet; the two loops advance one day at a time and exchange three things:

- support: an entity that cannot fund the day's payments from cash, liquid securities and
  central bank funding gets cash from the other entity before it sells loans, if the other
  has cash above its floor and has not reached its lending limit. The loan is an intragroup
  claim of the lender and an intragroup liability of the borrower;
- ring-fencing: once the host authority restricts transfers, the subsidiary lends no more.
  What it has already lent stays lent: a borrower asks for support when its cash is gone,
  so a call for repayment would find nothing to take;
- confidence: a stage reached by one entity is a panic shock for the other the next day.

Payments between the entities settle on days both have payments open (no weekend, no
moratorium, no outage).

CLI: python -m engine.group --showcase | --group showcase --scenario fast_run [--severity high] [--ring-fence DAY]
     [--decide restrict_transfers cap 2] [--decide group_support early 1]
"""

from __future__ import annotations

import argparse
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Sequence

from engine.config import DEFAULT_CONFIG_DIR, load_config
from engine.decisions import OPEN_OUTCOMES, OUTCOME_LABELS, Choice
from engine.model import (
    EPS,
    DayInput,
    DayNeed,
    DayOpen,
    SimulationResult,
    Transfer,
    daily_run,
)
from engine.schema import (
    DECISION_STAGES, GROUP_ENTITIES, Bank, College, DecisionsConfig, Group, ModelParams, Scenario,
)

PARENT, SUBSIDIARY = GROUP_ENTITIES
# A group run takes its scenario at one of the three values of the scenario's severity.
SEVERITY_LEVELS = ("low", "central", "high")
# Status of a day, in order of gravity, and the contagion shock each new stage carries.
_RANK = {"normal": 0, "recovery": 1, "early_intervention": 2, "likely_to_fail": 3, "failed": 4}
_SHOCKS = {
    1: ("recovery", "contagion_shock_recovery"),
    2: ("early_intervention", "contagion_shock_early_intervention"),
    4: ("failure", "contagion_shock_failure"),
}


@dataclass(frozen=True)
class GroupChoices:
    """The decision set of a group run."""

    parent: tuple[Choice, ...] = ()  # decisions of the home authorities and the parent
    subsidiary: tuple[Choice, ...] = ()  # decisions of the host authorities and the subsidiary
    # Decisions of the group (config/college.yaml): restriction of transfers by the host
    # authority, request for group support. Each is taken at the first close, from its day
    # on, at which its condition holds, and is in effect `lead_days` later.
    group: tuple[Choice, ...] = ()
    # Shorthand for the command line and for tests: a full ring-fence decided after the
    # close of this day whatever the host authority knows by then.
    ring_fence_day: Optional[int] = None

    def of(self, role: str) -> tuple[Choice, ...]:
        return self.parent if role == PARENT else self.subsidiary


@dataclass(frozen=True)
class TransferRecord:
    day: int
    lender: str  # "parent" or "subsidiary"
    borrower: str
    amount: float
    early: bool = False  # asked for by the group before payments could not be funded


@dataclass(frozen=True)
class GroupDecisionRecord:
    """One group decision as taken in a run."""

    decision: str
    decision_name: str
    option: str
    option_name: str
    actor: str
    entity: str  # "parent" or "subsidiary": the entity whose team decided
    scheduled_day: int
    decided_day: int  # taken after the close of this day
    effect_day: int


@dataclass(frozen=True)
class GroupResult:
    group: Group
    stressed: str  # the entity the scenario hits
    parent: SimulationResult
    subsidiary: SimulationResult
    transfers: tuple[TransferRecord, ...]
    ring_fence_from: Optional[int]  # first day a restriction of transfers is in effect
    consolidated: tuple[dict, ...]  # per day both entities are still running
    decisions: tuple[GroupDecisionRecord, ...] = ()
    # First day each entity's status reached each stage (recovery, early_intervention, ...).
    stage_days: tuple[tuple[str, tuple[tuple[str, int], ...]], ...] = ()

    def of(self, role: str) -> SimulationResult:
        return self.parent if role == PARENT else self.subsidiary


def other(role: str) -> str:
    return SUBSIDIARY if role == PARENT else PARENT


def group_scenarios(
    group: Group, scenarios: dict[str, Scenario], scenario_key: str, stressed: str = PARENT,
) -> dict[str, Scenario]:
    """The scenario of each entity: the chosen one for the entity under stress, the group's
    calm scenario for the other, on the same calendar."""
    hit = scenarios[scenario_key]
    calm = scenarios[group.calm_scenario].model_copy(update={"start_weekday": hit.start_weekday})
    return {stressed: hit, other(stressed): calm}


def at_severity(
    scenarios: dict[str, Scenario], scenario_key: str, level: str = "central",
) -> dict[str, Scenario]:
    """The scenarios, with the chosen one at the low or the high end of its own severity
    range (config/scenarios.yaml). "central" changes nothing. The scenario's name says so."""
    if level not in SEVERITY_LEVELS:
        raise ValueError(f"severity must be one of {SEVERITY_LEVELS}")
    if level == "central":
        return scenarios
    scenario = scenarios[scenario_key]
    value = getattr(scenario.severity, level)
    if value is None:
        raise ValueError(f"scenario {scenario_key} has no {level} severity")
    changed = scenario.model_copy(update={
        "name": f"{scenario.name}, {level} severity",
        "severity": scenario.severity.model_copy(update={"value": value}),
    })
    return {**scenarios, scenario_key: changed}


def support_available(
    lender: DayNeed, opening_assets: float, opening_deposits: float, group: Group,
    cap: Optional[float] = None,
) -> float:
    """Cash the lender can give today: what it holds above its floor, within its limit.
    `cap`: a lower limit on what it may have lent in all, set by a restriction."""
    if not lender.payments_open or lender.shortfall > EPS:
        return 0.0
    above_floor = lender.state.cash - group.lender_cash_floor_share.value * opening_deposits
    limit = group.transfer_limit_share.value * opening_assets
    if cap is not None:
        limit = min(limit, cap)
    return max(0.0, min(above_floor, limit - lender.state.intragroup_claim))


def decision_entity(entity: str, stressed: str) -> str:
    """The entity ("parent" or "subsidiary") a group decision belongs to."""
    return stressed if entity == "stressed" else entity


def consolidate(parent_row: dict, subsidiary_row: dict) -> dict:
    """The two balance sheets as one: intragroup positions cancel out."""
    claims = parent_row["intragroup_claim"] + subsidiary_row["intragroup_claim"]
    liabilities = parent_row["intragroup_liability"] + subsidiary_row["intragroup_liability"]
    both = lambda name: parent_row[name] + subsidiary_row[name]  # noqa: E731
    return {
        "day": parent_row["day"],
        "total_assets": both("total_assets") - claims,
        "total_liabilities": both("total_liabilities") - liabilities,
        "cet1": both("cet1"),
        "deposits_total": both("deposits_total"),
        "cash": both("cash"),
        "liquidity_buffer": both("liquidity_buffer"),
        "intragroup_gap": claims - liabilities,  # 0 unless a claim was written off
    }


def simulate_group(
    group: Group,
    banks: dict[str, Bank],
    params: ModelParams,
    scenarios: dict[str, Scenario],
    scenario_key: str,
    decisions: Optional[DecisionsConfig] = None,
    choices: GroupChoices = GroupChoices(),
    stressed: str = PARENT,
    college: Optional[College] = None,
) -> GroupResult:
    """Run the parent and the subsidiary together over the horizon. Group decisions
    (`choices.group`) need the `college` that defines them."""
    if stressed not in GROUP_ENTITIES:
        raise ValueError(f"stressed must be one of {GROUP_ENTITIES}")
    if choices.ring_fence_day is not None and choices.ring_fence_day < 0:
        raise ValueError("ring_fence_day must be >= 0")
    catalogue = college.decisions if college is not None else {}
    for choice in choices.group:
        if choice.decision not in catalogue or choice.option not in catalogue[choice.decision].options:
            raise ValueError(f"unknown group decision or option: {choice.decision}, {choice.option}")
    if len({choice.decision for choice in choices.group}) != len(choices.group):
        raise ValueError("a group decision can be taken once")
    entity_banks = {role: banks[group.entity(role).bank].model_copy(
        update={"name": group.entity(role).name}) for role in GROUP_ENTITIES}
    entity_scenarios = group_scenarios(group, scenarios, scenario_key, stressed)
    lead = int(decisions.lead_days.value) if decisions is not None else 1
    delay = int(college.cross_border_delay_days.value) if college is not None else 0
    opening_assets = {role: bank.assets.total() for role, bank in entity_banks.items()}
    opening_deposits = {role: bank.liabilities.deposits.total() for role, bank in entity_banks.items()}

    runs = {
        role: daily_run(entity_banks[role], params, entity_scenarios[role], decisions,
                        choices.of(role), grouped=True)
        for role in GROUP_ENTITIES
    }
    results: dict[str, SimulationResult] = {}
    waiting: dict[str, object] = {}  # what each running entity has yielded
    rank = {role: 0 for role in GROUP_ENTITIES}  # stages already passed on to the other entity
    reached: dict[str, dict[int, int]] = {role: {} for role in GROUP_ENTITIES}  # level -> day
    transfers: list[TransferRecord] = []
    taken: list[GroupDecisionRecord] = []
    pending = [choice for choice in choices.group
               if catalogue[choice.decision].options[choice.option].effect != "none"]
    effects: dict[int, list[tuple[str, str, str]]] = {}  # effect day -> (decision, option, role)
    if choices.ring_fence_day is not None:
        effects.setdefault(choices.ring_fence_day + lead, []).append(("", "", SUBSIDIARY))
    ring_fence_from: Optional[int] = None
    lending_cap: Optional[float] = None  # most the subsidiary may have lent, once restricted
    early: Optional[tuple[str, float]] = None  # (borrower, share) of a request still to settle
    announced = False  # the news of the restriction has reached the parent
    defaulted: set[str] = set()  # entities whose payments default is still to be passed on
    resolved: set[str] = set()  # banks of a system whose resolution is still to be passed on

    def advance(role: str, answer) -> None:
        try:
            waiting[role] = next(runs[role]) if answer is None else runs[role].send(answer)
        except StopIteration as finished:
            results[role] = finished.value
            waiting.pop(role, None)

    def news_from(role: str, status: str) -> tuple[float, tuple[str, ...]]:
        """The shock and the headlines the other entity gets for the stages `role` reached."""
        new_rank = _RANK.get(status, rank[role])
        shock, headlines = 0.0, []
        for level in range(rank[role] + 1, new_rank + 1):
            if level in _SHOCKS:
                kind, parameter = _SHOCKS[level]
                size = getattr(params, parameter).value * group.confidence_link.value
                if size > 0:
                    shock += size
                    headlines.append(group.headlines[kind].format(entity=group.entity(role).name))
        rank[role] = max(rank[role], new_rank)
        return shock, tuple(headlines)

    def note_stage(role: str, status: str, day: int) -> None:
        for level in range(1, _RANK.get(status, 0) + 1):
            reached[role].setdefault(level, day)

    def condition_holds(decision: str, role: str, close: int) -> bool:
        condition = catalogue[decision].condition
        level = DECISION_STAGES.index(condition.stage) + 1
        if condition.at == "own":
            day = reached[role].get(level)
            return day is not None and day <= close
        day = reached[other(role)].get(level)
        return day is not None and day + delay <= close

    for role in GROUP_ENTITIES:
        advance(role, None)
    for role, result in results.items():  # a run that ended on the opening day
        note_stage(role, str(result.rows[-1]["status"]), result.end_day)

    while waiting:
        # --- start of the day: the previous close is known
        day = next(iter(waiting.values())).day
        close = day - 1
        for role in list(waiting):
            opened: DayOpen = waiting[role]
            note_stage(role, str(opened.row["status"]), close)
        # Group decisions taken after that close.
        for choice in list(pending):
            role = decision_entity(catalogue[choice.decision].entity, stressed)
            if choice.day > close or role not in waiting or not condition_holds(choice.decision, role, close):
                continue
            pending.remove(choice)
            definition = catalogue[choice.decision]
            effects.setdefault(close + lead, []).append((choice.decision, choice.option, role))
            taken.append(GroupDecisionRecord(
                choice.decision, definition.name, choice.option, definition.options[choice.option].name,
                definition.actor, role, choice.day, close, close + lead))
        # Decisions in effect from today.
        for decision, option, role in effects.pop(day, []):
            values = {} if not decision else {
                name: item.value for name, item in catalogue[decision].options[option].params.items()}
            effect = catalogue[decision].options[option].effect if decision else "restrict"
            if effect == "restrict":
                lent = waiting[SUBSIDIARY].state.intragroup_claim if SUBSIDIARY in waiting else 0.0
                cap = lent + values.get("cap_share", 0.0) * opening_assets[SUBSIDIARY]
                lending_cap = cap if lending_cap is None else min(lending_cap, cap)
                ring_fence_from = day if ring_fence_from is None else ring_fence_from
            elif effect == "early_support":
                early = (role, values["share"])

        inputs = {}
        for role in list(waiting):
            source = other(role)
            shock, headlines, claim_loss = 0.0, (), 0.0
            if source in waiting:
                shock, headlines = news_from(source, str(waiting[source].row["status"]))
            elif source in defaulted:
                shock, headlines = news_from(source, "failed")
                claim_loss = group.claim_loss_at_default.value * waiting[role].state.intragroup_claim
                defaulted.discard(source)
            elif source in resolved:
                resolved.discard(source)
                size = params.contagion_shock_resolution.value * group.confidence_link.value
                if size > 0:
                    shock = size
                    headlines = (group.headlines["resolution"].format(entity=group.entity(source).name),)
            if role == PARENT and ring_fence_from is not None and not announced:
                announced = True
                if group.ring_fence_shock.value > 0:
                    shock += group.ring_fence_shock.value
                    headlines += (group.headlines["ring_fence"].format(
                        entity=group.subsidiary.name, country=group.subsidiary.country),)
            inputs[role] = DayInput(shock=shock, headlines=headlines, claim_loss=claim_loss)
        for role in list(waiting):
            advance(role, inputs[role])
        for role, result in results.items():
            note_stage(role, str(result.rows[-1]["status"]), result.end_day)

        # --- before the day's payments: intragroup transfers
        needs: dict[str, DayNeed] = {role: need for role, need in waiting.items()
                                     if isinstance(need, DayNeed)}
        moved = {role: Transfer() for role in needs}
        both_open = len(needs) == 2 and all(need.payments_open for need in needs.values())

        def available(lender: str) -> float:
            return support_available(
                needs[lender], opening_assets[lender], opening_deposits[lender], group,
                lending_cap if lender == SUBSIDIARY else None)

        def lend(lender: str, borrower: str, amount: float, asked_early: bool) -> None:
            moved[borrower] = Transfer(cash=amount, liability=amount)
            moved[lender] = Transfer(cash=-amount, claim=amount)
            transfers.append(TransferRecord(day, lender, borrower, amount, asked_early))

        if both_open and early is not None:
            borrower, share = early
            amount = share * available(other(borrower))
            if amount > EPS:
                lend(other(borrower), borrower, amount, True)
            early = None
        elif both_open:
            for borrower in GROUP_ENTITIES:
                if needs[borrower].shortfall <= EPS:
                    continue
                amount = min(needs[borrower].shortfall, available(other(borrower)))
                if amount > EPS:
                    lend(other(borrower), borrower, amount, False)
                break  # one direction a day: an entity that needs support does not lend
        for role in list(needs):
            advance(role, moved[role])
            if role in results:
                note_stage(role, str(results[role].rows[-1]["status"]), results[role].end_day)
                if results[role].failure_day is not None:
                    defaulted.add(role)
                elif group.kind == "system" and results[role].outcome not in OPEN_OUTCOMES:
                    resolved.add(role)  # a tool was applied: an orderly end, and news

    parent, subsidiary = results[PARENT], results[SUBSIDIARY]
    shared = min(len(parent.rows), len(subsidiary.rows))
    names = {index + 1: stage for index, stage in enumerate(DECISION_STAGES)}
    return GroupResult(
        group=group, stressed=stressed, parent=parent, subsidiary=subsidiary,
        transfers=tuple(transfers), ring_fence_from=ring_fence_from,
        consolidated=tuple(consolidate(parent.rows[i], subsidiary.rows[i]) for i in range(shared)),
        decisions=tuple(taken),
        stage_days=tuple(
            (role, tuple((names[level], day) for level, day in sorted(reached[role].items())))
            for role in GROUP_ENTITIES),
    )


# --------------------------------------------------------------------------- CLI

_COLUMNS = (
    ("day", "day", 3, ".0f"), ("status", "status", 18, "s"),
    ("deposits_total", "deposits", 9, ".2f"), ("cash", "cash", 8, ".2f"),
    ("liquidity_buffer", "buffer", 8, ".2f"), ("outflow_total", "outflow", 8, ".2f"),
    ("support_received", "support in", 10, ".2f"), ("support_given", "support out", 11, ".2f"),
    ("intragroup_claim", "lent", 7, ".2f"), ("intragroup_liability", "borrowed", 8, ".2f"),
    ("unmet_outflow", "unmet", 7, ".2f"), ("cet1_ratio", "CET1", 6, ".3f"),
)


def table_lines(result: SimulationResult) -> list[str]:
    """The daily table of one entity, as text."""
    lines = ["  ".join(f"{title:>{width}}" for _, title, width, _ in _COLUMNS)]
    for row in result.rows:
        lines.append("  ".join(f"{row[name]:>{width}{form}}" for name, _, width, form in _COLUMNS))
    return lines


def summary_lines(result: GroupResult, unit: str) -> list[str]:
    lines = []
    if result.group.kind == "system":
        stages = dict(result.stage_days)
        for role, slot in zip(GROUP_ENTITIES, ("first bank", "second bank")):
            entity, run = result.group.entity(role), result.of(role)
            reached = "; ".join(f"{stage.replace('_', ' ')} day {day}" for stage, day in stages[role])
            lines.append(
                f"{entity.name} ({slot}): {OUTCOME_LABELS[run.outcome]}, last day {run.end_day}, "
                f"deposits lost {run.scorecard.deposits_lost_pct:.1f}%; stages: {reached or 'none'}")
        lines.append(f"Link between the two banks: {result.group.confidence_link.value:g} "
                     "(factor on the contagion shocks). They lend nothing to each other.")
        return lines
    for role in GROUP_ENTITIES:
        entity, run = result.group.entity(role), result.of(role)
        lines.append(
            f"{entity.name} ({entity.country}, {role}): {OUTCOME_LABELS[run.outcome]}, last day "
            f"{run.end_day}, deposits lost {run.scorecard.deposits_lost_pct:.1f}%"
            + (f", payments default on day {run.failure_day}" if run.failure_day is not None else "")
        )
    for record in result.decisions:
        lines.append(
            f"{record.decision_name}: {record.option_name}, decided at the close of day "
            f"{record.decided_day}, in effect from day {record.effect_day}.")
    if result.ring_fence_from is not None:
        lines.append(f"Ring-fencing in effect from day {result.ring_fence_from}.")
    if not result.transfers:
        lines.append("No intragroup transfer.")
    for item in result.transfers:
        lender, borrower = result.group.entity(item.lender).name, result.group.entity(item.borrower).name
        lines.append(f"Day {item.day}: {lender} lends {item.amount:.2f} {unit} to {borrower}"
                     + (" (asked for early)" if item.early else ""))
    return lines


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Simulate a cross-border group.")
    parser.add_argument("--group", default="showcase")
    parser.add_argument("--system", metavar="KEY", nargs="?", const="",
                        help="two unrelated banks of config/systems.yaml (no key: the showcase "
                             "system, with its scenario); --path sets the first bank's decisions")
    parser.add_argument("--path", help="with --system: decision path of the bank the scenario hits")
    parser.add_argument("--scenario", help="scenario of the entity under stress")
    parser.add_argument("--stressed", choices=GROUP_ENTITIES, default=PARENT)
    parser.add_argument("--severity", choices=SEVERITY_LEVELS, default="central",
                        help="the scenario at the low or high end of its own severity range")
    parser.add_argument("--showcase", action="store_true",
                        help="the group showcase of config/college.yaml, with its decision")
    parser.add_argument("--ring-fence", type=int, metavar="DAY",
                        help="the host authority restricts transfers after the close of DAY")
    parser.add_argument("--decide", nargs=3, action="append", default=[],
                        metavar=("DECISION", "OPTION", "DAY"),
                        help="a group decision of config/college.yaml, e.g. "
                             "restrict_transfers cap 2, or group_support early 1")
    parser.add_argument("--export-dir", type=Path,
                        help="write the group's exercise pack here: the path as decided, and "
                             "as a second branch the same path without any group decision")
    parser.add_argument("--config-dir", default=str(DEFAULT_CONFIG_DIR))
    parser.add_argument("--no-legal-references", action="store_true",
                        help="leave the legal references of config/legal.yaml out of the documents")
    args = parser.parse_args(argv)
    config = load_config(args.config_dir)
    if args.no_legal_references:
        from engine.legal import with_switch

        config = config.model_copy(update={"script": with_switch(config.script, False)})
    decided = [tuple(item) for item in args.decide]
    if args.showcase:
        show = config.college.showcase
        args.group, args.scenario, args.severity, args.stressed = (
            show.group, show.scenario, show.severity, show.stressed)
        decided = decided or [(show.decision, show.option, show.day)]
    college, own = config.college, {}
    if args.system is not None:
        from engine import system  # built on this module
        from engine.decisions import choices_of

        show = config.systems.showcase
        key = args.system or show.pair
        if not args.system:
            args.scenario = args.scenario or show.scenario
            args.stressed = system.SLOTS[show.stressed]
        if args.scenario is None:
            parser.error("give --scenario with --system KEY")
        group, college = system.pair(config, key)
        args.group = key
        own = {args.stressed: choices_of(config.decisions.paths[args.path or show.compare_path])}
    else:
        if args.scenario is None:
            parser.error("give --scenario, or --showcase")
        group = config.groups[args.group]
    scenarios = at_severity(config.scenarios, args.scenario, args.severity)
    choices = GroupChoices(ring_fence_day=args.ring_fence, **own,
                           group=tuple(Choice(d, o, int(day)) for d, o, day in decided))
    result = simulate_group(
        group, config.banks, config.model, scenarios, args.scenario, config.decisions,
        choices, args.stressed, college,
    )
    unit = config.banks[group.parent.bank].unit
    print(f"{group.name}, scenario '{scenarios[args.scenario].name}' on "
          f"{group.entity(args.stressed).name if group.kind == 'system' else 'the ' + args.stressed}. "
          f"Amounts in {unit}.")
    for line in summary_lines(result, unit):
        print(line)
    for role in GROUP_ENTITIES:
        print(f"\n{group.entity(role).name}")
        for line in table_lines(result.of(role)):
            print(line)
    if args.export_dir is not None:
        # Imported here: the script and export modules are built on this one.
        from engine.group_export import group_files
        from engine.group_script import build_group_pack

        whole = group.kind == "system"
        paths = [("As decided", choices)]
        if choices != GroupChoices():
            paths.insert(0, ("Authorities act late" if whole else "No decision of the group",
                             GroupChoices()))
        pack = build_group_pack(
            group, config.banks, config.model, scenarios, args.scenario, config.decisions,
            config.script, college, paths, args.stressed)
        files = group_files(pack, config.decisions, college)
        stem = args.export_dir / f"{'system' if whole else 'group'}_pack_{args.group}_{args.scenario}"
        for name, content in files.items():
            target = stem / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        print(f"\n{'System' if whole else 'Group'} pack written to {stem} ({len(files)} files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
