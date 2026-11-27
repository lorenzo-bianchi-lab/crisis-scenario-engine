"""Exercise script of a cross-border group (SPEC_V2 and SPEC_V3 phase F, part b).

Pure functions. Each entity of the group gets an ordinary exercise pack (engine/script.py)
built from its own run inside the group, for the authorities of its own country. This
module adds what exists only in a group:

- the teams: each country has its competent authority, resolution authority, central bank
  and deposit guarantee scheme, named with their country;
- cross-border notifications: a stage change at one entity reaches the competent and
  resolution authorities of the other country from that entity's competent authority, with
  the figures it passes on and after the college's delay;
- the group's own decisions (config/college.yaml), as injects to the team that decides and
  to the teams it informs;
- figures: each entity's position towards the rest of its group, for its own competent
  authority; the consolidated figures, older, for the home authorities.

A team sees its own entity's figures as in a single-bank exercise, and of the other entity
only what a notification carries. The college is an assumption of the tool (the Handbook
does not define it).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Sequence

from engine.decisions import FIGURE_LABELS, OUTCOME_LABELS, Choice, format_figure
from engine.group import (
    PARENT,
    SUBSIDIARY,
    GroupChoices,
    GroupResult,
    decision_entity,
    group_scenarios,
    other,
    simulate_group,
)
from engine.schema import (
    DECISION_STAGES,
    GROUP_ENTITIES,
    PLAYERS,
    Bank,
    College,
    DecisionsConfig,
    Group,
    ModelParams,
    Scenario,
    ScriptConfig,
)
from engine.script import (
    CONTROL,
    MODES,
    DecisionDue,
    ExercisePack,
    Figure,
    Inject,
    OptionLine,
    build_branch,
    build_pack,
)

SIDES = {"home": PARENT, "host": SUBSIDIARY}
# Teams that receive a cross-border notification.
CROSS_BORDER_RECIPIENTS = ("competent_authority", "resolution_authority")
# Events of an entity's run that are passed on to the other country.
CROSS_BORDER_KINDS = (*DECISION_STAGES, "foltf", "resolution")
_TITLES = {
    "recovery": "Recovery indicator breached",
    "early_intervention": "Early intervention conditions met",
    "likely_to_fail": "Likely to fail",
    "failure": "Payments default",
    "foltf": "Failing or likely to fail declared",
    "resolution": "End of the run",
}


@dataclass(frozen=True)
class GroupBranch:
    label: str  # "A" or "B"
    name: str
    choices: GroupChoices
    result: GroupResult


@dataclass(frozen=True)
class GroupDecisionPoint:
    """Control team's view of one option of one group decision: where it leads if it is
    taken at the first close it can be, the entities' own decisions as on the branch and no
    other group decision."""

    code: str
    decision: str
    decision_name: str
    team: str
    option: str
    option_name: str
    open_day: Optional[int]
    taken: bool  # the option the branch follows
    parent_outcome: str
    parent_end_day: int
    subsidiary_outcome: str
    subsidiary_end_day: int
    lent: float


@dataclass(frozen=True)
class GroupPack:
    group: Group
    stressed: str
    unit: str
    mode: str
    scenario_name: str
    branches: tuple[GroupBranch, ...]
    packs: dict[str, ExercisePack]  # "parent" / "subsidiary" -> that country's pack
    scripts: dict[str, ScriptConfig]  # the script config with that country's team names
    decision_points: tuple[tuple[str, tuple[GroupDecisionPoint, ...]], ...]  # per branch label


def team_name(college: College, script: ScriptConfig, group: Group, role: str, actor: str) -> str:
    return college.team_name.format(actor=script.actors[actor], country=group.entity(role).country)


def team_script(script: ScriptConfig, college: College, group: Group, role: str) -> ScriptConfig:
    """The script config of one country: its authorities are named with their country. Bank
    management, which the control team plays, is named with its entity."""
    names = {actor: team_name(college, script, group, role, actor) for actor in script.actors}
    names["bank_management"] = f"Management of {group.entity(role).name}"
    return script.model_copy(update={"actors": names})


def resolve_team(reference: str, role: str) -> tuple[str, str]:
    """(entity, actor) of a team reference like "host:competent_authority"; "own" and
    "other" are read from the entity `role`."""
    side, _, actor = reference.partition(":")
    entity = {"own": role, "other": other(role)}.get(side) or SIDES[side]
    return entity, actor


def group_code(college: College, decision: str, option: str) -> str:
    """Stable code of one option of a group decision, e.g. 'G1-b'."""
    number = list(college.decisions).index(decision) + 1
    letter = "abcdefgh"[list(college.decisions[decision].options).index(option)]
    return f"G{number}-{letter}"


def open_day(result: GroupResult, college: College, decision: str) -> Optional[int]:
    """First close at which a group decision is open to its team on this path, or None."""
    definition = college.decisions[decision]
    role = decision_entity(definition.entity, result.stressed)
    stages = {entity: dict(days) for entity, days in result.stage_days}
    level = DECISION_STAGES.index(definition.condition.stage)
    source = role if definition.condition.at == "own" else other(role)
    delay = 0 if definition.condition.at == "own" else int(college.cross_border_delay_days.value)
    days = [day for stage, day in stages[source].items() if DECISION_STAGES.index(stage) >= level]
    if not days:
        return None
    day = min(days) + delay
    last = min(result.parent.end_day, result.subsidiary.end_day)
    ended = result.of(role).outcome not in ("stabilised", "emergency_funding", "under_stress")
    return None if day > result.of(role).end_day or (ended and day >= last) else day


def _trigger_figures(event, unit: str) -> tuple[Figure, ...]:
    """The figures behind a stage change, as the entity's own competent authority got them."""
    out = []
    for figure, value, threshold in event.figures:
        label = FIGURE_LABELS[figure][0]
        limit = "" if figure == "unmet_outflow" else format_figure(figure, threshold, unit)[len(label) + 1:]
        out.append(Figure(figure, label, float(value), event.day, format_figure(figure, value, unit),
                          threshold_text=limit))
    return tuple(out)


def extras_for(
    role: str, result: GroupResult, scenario: Scenario, scripts: dict[str, ScriptConfig],
    college: College, decisions: DecisionsConfig, unit: str, horizon: int, linear: bool,
) -> dict[str, list[Inject]]:
    """What reaches the teams of `role`'s country only because the bank is in a group.
    Nothing in a system: the same authorities hold both banks' files."""
    if result.group.kind == "system":
        return {}
    script, theirs = scripts[role], scripts[other(role)]
    source, source_entity = result.of(other(role)), result.group.entity(other(role))
    delay = int(college.cross_border_delay_days.value)
    lead = int(decisions.lead_days.value)
    out: dict[str, list[Inject]] = {}

    def when(day: int) -> str:
        return scenario.weekday(day) if day >= 1 else ""

    def relayed(sender: str, day: int, clock: str) -> dict:
        """Dynamic exercise: the sender should pass it on; the control team holds a fallback."""
        if linear:
            return {"purpose": "advance", "delivered_by": CONTROL,
                    "trigger": f"Time: day {day} ({when(day)}), {clock}"}
        return {"purpose": "control", "fallback": True,
                "delivered_by": f"{CONTROL}, in place of the {sender}",
                "trigger": f"Fallback: only if the {sender} has not passed this on by {clock} "
                           f"on day {day}"}

    # 1. Stage changes of the other entity, passed on by its competent authority: one
    # notification for everything of one day, and one each for FoLtF and the end of its run.
    sender = theirs.actors["competent_authority"]
    batches: dict[tuple[int, str], list] = {}
    for event in source.events:
        if event.kind in CROSS_BORDER_KINDS and event.day + delay <= horizon:
            group_key = "stage" if event.kind in DECISION_STAGES else event.kind
            batches.setdefault((event.day + delay, group_key), []).append(event)
    for (day, _), events in batches.items():
        text = "\n".join(
            college.texts[event.kind].format(
                sender=sender, entity=source_entity.name, country=source_entity.country,
                **({"outcome": OUTCOME_LABELS[source.outcome].lower()}
                   if event.kind == "resolution" else {}))
            for event in events)
        figures: dict[str, Figure] = {}
        for event in events:
            if event.kind in DECISION_STAGES:
                for figure in _trigger_figures(event, unit):
                    figures[figure.name] = figure
        for actor in CROSS_BORDER_RECIPIENTS:
            out.setdefault(actor, []).append(Inject(
                day=day, weekday=when(day), time=college.cross_border_clock, recipient=actor,
                sender=sender, kind="cross_border",
                title=f"{_TITLES[events[-1].kind]} at {source_entity.name}", text=text,
                headline=tuple(figures.values()),
                expected=college.texts["expected_cross_border"],
                **relayed(sender, day, college.cross_border_clock),
            ))

    # 2. The group's own decisions: open to the deciding team, then told to those it informs.
    last = min(result.parent.end_day, result.subsidiary.end_day)
    for key, definition in college.decisions.items():
        entity = decision_entity(definition.entity, result.stressed)
        opened = open_day(result, college, key)
        decider = scripts[entity].actors[definition.actor]
        names = lambda references: tuple(  # noqa: E731
            scripts[e].actors[a] for e, a in (resolve_team(r, entity) for r in references))
        if entity == role and opened is not None:
            ended = last < horizon
            deadline = max(opened, last - lead) if ended else min(
                horizon, opened + int(script.decision_window_days.value))
            due = DecisionDue(
                decision=key, name=definition.name, question=definition.question.strip(),
                options=tuple(
                    OptionLine(option, item.name, group_code(college, key, option),
                               option == definition.default_option)
                    for option, item in definition.options.items()),
                deadline_day=deadline, consult=names(definition.consult),
                inform=names(definition.inform),
            )
            out.setdefault(definition.actor, []).append(Inject(
                day=opened, weekday=when(opened), time=script.clock["decision_due"],
                recipient=definition.actor, sender="Exercise control", kind="decision_due",
                title="Decision open to you",
                text=college.texts["decision_open"].format(decision_name=definition.name),
                decision_due=due, purpose="control",
                trigger=f"Milestone: the decision \"{definition.name}\" opens (close of day {opened})",
                expected=college.texts["expected_decision"].format(
                    consult=", ".join(due.consult) or "nobody", inform=", ".join(due.inform) or "nobody"),
            ))
        for record in result.decisions:
            if record.decision != key or record.effect_day > horizon:
                continue
            text = college.texts["decision_taken"].format(
                sender=decider, option_name=record.option_name, decision_name=record.decision_name,
                effect_day=record.effect_day)
            for target, actor in dict.fromkeys(resolve_team(r, entity) for r in definition.inform):
                if target != role or (target, actor) == (entity, definition.actor):
                    continue
                out.setdefault(actor, []).append(Inject(
                    day=record.effect_day, weekday=when(record.effect_day),
                    time=script.clock["decision_taken"], recipient=actor, sender=decider,
                    kind="decision_taken", title="Decision by another party", text=text,
                    expected=college.texts["expected_decision_taken"],
                    **relayed(decider, record.effect_day, script.clock["decision_taken"]),
                ))
    return out


def figures_for(role: str, result: GroupResult, college: College, unit: str):
    """Figures a team gets on top of its entity's own: `(actor, day) -> figures`."""
    if result.group.kind == "system":
        return lambda actor, as_of: ()
    rows = result.of(role).rows
    late = int(college.consolidated_delay_days.value)

    def extra(actor: str, as_of: int) -> tuple[Figure, ...]:
        out = []
        if actor in ("competent_authority", "bank_management") and as_of < len(rows):
            for name, label in (("intragroup_claim", "Lent to the rest of the group"),
                                ("intragroup_liability", "Borrowed from the rest of the group")):
                value = float(rows[as_of][name])
                out.append(Figure(name, label, value, as_of, f"{label} {value:.2f} {unit}"))
        if role == PARENT and actor in CROSS_BORDER_RECIPIENTS:
            day = max(0, as_of - late)
            if day < len(result.consolidated):
                for name, title in (("liquidity_buffer", "Group liquidity buffer"),
                                    ("deposits_total", "Group deposits")):
                    label = f"{title}, consolidated, as of day {day}"
                    value = float(result.consolidated[day][name])
                    out.append(Figure(f"group_{name}", label, value, day, f"{label} {value:.2f} {unit}"))
        return tuple(out)

    return extra


def information_for(role: str, college: College) -> dict[str, tuple[str, ...]]:
    """Sentences added to each team's briefing on what it sees."""
    if "information_system" in college.texts:
        return {actor: (college.texts["information_system"],) for actor in PLAYERS}
    late = int(college.consolidated_delay_days.value)
    delay = "no" if late == 0 else "one day" if late == 1 else f"{late} days"
    out: dict[str, tuple[str, ...]] = {}
    for actor in CROSS_BORDER_RECIPIENTS:
        lines = [college.texts["information_cross_border"]]
        if actor == "competent_authority":
            lines.insert(0, college.texts["information_own_position"])
        if role == PARENT:
            lines.append(college.texts["information_consolidated"].format(delay=delay))
        out[actor] = tuple(lines)
    out["bank_management"] = (college.texts["information_own_position"],)
    return out


def decision_points(
    branch: GroupBranch, group: Group, banks: dict[str, Bank], params: ModelParams,
    scenarios: dict[str, Scenario], scenario_key: str, decisions: DecisionsConfig,
    script: ScriptConfig, college: College,
) -> tuple[GroupDecisionPoint, ...]:
    """Where each option of each group decision leads on a branch."""
    result = branch.result
    taken = {record.decision: record.option for record in result.decisions}
    if branch.choices.ring_fence_day is not None:
        taken.setdefault("restrict_transfers", "ring_fence")
    out = []
    for key, definition in college.decisions.items():
        opened = open_day(result, college, key)
        entity = decision_entity(definition.entity, result.stressed)
        for option, item in definition.options.items():
            follows = taken.get(key, definition.default_option) == option
            if follows or opened is None:
                run = result
            else:
                only = () if option == definition.default_option else (Choice(key, option, opened),)
                run = simulate_group(
                    group, banks, params, scenarios, scenario_key, decisions,
                    GroupChoices(branch.choices.parent, branch.choices.subsidiary, only),
                    result.stressed, college)
            out.append(GroupDecisionPoint(
                code=group_code(college, key, option), decision=key, decision_name=definition.name,
                team=team_name(college, script, group, entity, definition.actor)
                if definition.actor != "bank_management" else f"Management of {group.entity(entity).name}",
                option=option, option_name=item.name, open_day=opened, taken=follows,
                parent_outcome=run.parent.outcome, parent_end_day=run.parent.end_day,
                subsidiary_outcome=run.subsidiary.outcome, subsidiary_end_day=run.subsidiary.end_day,
                lent=sum(t.amount for t in run.transfers),
            ))
    return tuple(out)


def entity_branch(
    role: str, result: GroupResult, bank: Bank, params: ModelParams, scenario: Scenario,
    decisions: DecisionsConfig, scripts: dict[str, ScriptConfig], college: College,
    choices: Sequence[Choice], name: str = "", linear: bool = False, ambient: bool = True,
    challenges: Sequence[str] = (),
):
    """The script of one entity on one group run: its own injects and what the group adds.
    Used by a live group exercise, which rebuilds it each time a decision changes the run."""
    unit, horizon = bank.unit, int(params.horizon_days.value)
    return build_branch(
        "A", name or result.group.entity(role).name, bank, params, scenario, decisions,
        scripts[role], choices, ambient, "central", linear, tuple(challenges), False,
        with_points=False, result=result.of(role),
        extra_injects=extras_for(role, result, scenario, scripts, college, decisions, unit,
                                 horizon, linear),
        extra_figures=figures_for(role, result, college, unit),
    )


def _part_of(group: Group, role: str, pack: ExercisePack) -> ExercisePack:
    """The pack of one entity, saying on its covers which exercise it belongs to."""
    other_entity = group.entity(other(role))
    if group.kind == "system":
        kind = (f"System-wide: {group.name}, two unrelated banks of {group.parent.country} "
                f"(the other bank: {other_entity.name})")
    else:
        kind = (f"Cross-border group: {group.name}; this is the pack of {group.entity(role).country}, "
                f"the {role}'s country (the other entity: {other_entity.name}, {other_entity.country})")
    return replace(pack, exercise=kind)


def build_group_pack(
    group: Group,
    banks: dict[str, Bank],
    params: ModelParams,
    scenarios: dict[str, Scenario],
    scenario_key: str,
    decisions: DecisionsConfig,
    script: ScriptConfig,
    college: College,
    paths: Sequence[tuple[str, GroupChoices]],
    stressed: str = PARENT,
    mode: str = "dynamic",
    ambient: bool = True,
) -> GroupPack:
    """The exercise pack of a group for one or two decision paths: one pack per country,
    with what crosses the border added to each."""
    if not 1 <= len(paths) <= 2:
        raise ValueError("an exercise pack takes one or two decision paths")
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if mode == "linear":
        paths = paths[-1:]
    branches = tuple(
        GroupBranch("AB"[index], name, choices, simulate_group(
            group, banks, params, scenarios, scenario_key, decisions, choices, stressed, college))
        for index, (name, choices) in enumerate(paths)
    )
    entity_scenarios = group_scenarios(group, scenarios, scenario_key, stressed)
    scripts = {role: team_script(script, college, group, role) for role in GROUP_ENTITIES}
    unit = banks[group.parent.bank].unit
    horizon = int(params.horizon_days.value)
    packs = {}
    for role in GROUP_ENTITIES:
        entity = group.entity(role)
        bank = banks[entity.bank].model_copy(update={"name": entity.name})
        packs[role] = _part_of(group, role, build_pack(
            bank, params, entity_scenarios[role], decisions, scripts[role],
            [(branch.name, branch.choices.of(role)) for branch in branches],
            ambient=ambient, mode=mode,
            background_key=scenario_key if role == stressed else group.calm_scenario,
            runs=[branch.result.of(role) for branch in branches],
            extras=[extras_for(role, branch.result, entity_scenarios[role], scripts, college,
                               decisions, unit, horizon, mode == "linear") for branch in branches],
            extra_figures=[figures_for(role, branch.result, college, unit) for branch in branches],
            extra_information=information_for(role, college),
        ))
    return GroupPack(
        group=group, stressed=stressed, unit=unit, mode=mode,
        scenario_name=scenarios[scenario_key].name, branches=branches, packs=packs, scripts=scripts,
        decision_points=tuple(
            (branch.label, decision_points(branch, group, banks, params, scenarios, scenario_key,
                                           decisions, script, college))
            for branch in branches),
    )
