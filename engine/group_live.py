"""A live exercise on a cross-border group (SPEC_V2 and SPEC_V3 phase F, part d).

Pure functions. Each country runs an ordinary live exercise (engine/live.py) with its own
teams; this module keeps the two together:

- one clock: a step or a jump moves both exercises to the same simulated moment;
- one path: each entity's script is built from its run inside the group, so a decision
  recorded in one country, or a decision of the group, changes what both countries see;
- the group's own decisions (config/college.yaml) are recorded here.

As in engine/live.py, a live group exercise is a set-up plus an ordered log, and the state
is what replaying the log gives. Nothing here reads the clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Optional, Sequence

from engine import live, system
from engine.decisions import OPEN_OUTCOMES, OUTCOME_LABELS, Choice
from engine.group import (
    PARENT,
    SUBSIDIARY,
    GroupChoices,
    GroupResult,
    decision_entity,
    at_severity,
    group_scenarios,
    other,
    simulate_group,
)
from engine.group_script import entity_branch, open_day, resolve_team, team_script
from engine.live import CLOSE, Entry, LiveError, LiveState
from engine.report import ClosingReport, live_report
from engine.schema import GROUP_ENTITIES, PLAYERS, Config
from engine.script import Inject, active_challenges

GROUP = "group"  # the "team" of an entry that records a decision of the group
SIDES = (*GROUP_ENTITIES, GROUP)
ENTRY_KINDS = ("start", "step", "jump", "decision", "college", "adhoc", "fallback", "relay_done",
               "pause", "resume", "undo")
SAVE_FORMAT = "crisis-scenario-engine/group-live/1"


@dataclass(frozen=True)
class GroupLiveSetup:
    name: str
    group_key: str
    scenario_key: str
    stressed: str = PARENT
    planned: GroupChoices = GroupChoices()  # the pre-planned path
    mode: str = "dynamic"
    severity: str = "central"  # the scenario at this value of its own severity range


@dataclass(frozen=True)
class GroupRecorded:
    """A decision of the group as recorded by the control team."""

    decision: str
    option: str
    entity: str  # "parent" or "subsidiary": the entity whose team decided
    actor: str
    day: int  # taken at the close of this simulated day
    real_time: str
    by_control: bool
    applied: bool  # False: "no decision yet", or a linear exercise
    planned_option: str
    deviates: bool
    seconds_taken: Optional[float]
    consulted: tuple[str, ...] = ()  # team references, e.g. "home:competent_authority"


@dataclass(frozen=True)
class CollegeRecord:
    """A joint decision of the college as recorded by the control team. A record only:
    it changes nothing in the model."""

    subject: str
    took_part: tuple[str, ...]  # team keys, e.g. "parent.resolution_authority"
    agreed: bool
    note: str
    day: int
    time: str
    real_time: str
    minutes: Optional[float]  # real minutes the meeting took, if the control team noted it


@dataclass(frozen=True)
class GroupLiveState:
    setup: GroupLiveSetup
    entries: tuple[Entry, ...] = ()
    states: dict = field(default_factory=dict)  # "parent" / "subsidiary" -> LiveState
    decisions: tuple[GroupRecorded, ...] = ()
    college: tuple[CollegeRecord, ...] = ()  # joint decisions of the college
    result: Optional[GroupResult] = None  # the group's run as known now
    day: int = 0
    time: str = ""
    started: bool = False
    paused: bool = False
    ended: bool = False

    @property
    def clock(self) -> tuple[int, str]:
        return (self.day, self.time)

    def of(self, role: str) -> LiveState:
        return self.states[role]


def scenario_of(setup: GroupLiveSetup, config: Config):
    """The scenario of the entity under stress, at the severity of the exercise."""
    return at_severity(config.scenarios, setup.scenario_key, setup.severity)[setup.scenario_key]


class _Session:
    """Everything a replay needs that is derived from the configuration: the two country
    contexts, whose paths are built from the group's run as it stands."""

    def __init__(self, setup: GroupLiveSetup, config: Config):
        self.setup, self.config = setup, config
        self.group, self.college = system.pair(config, setup.group_key)
        self.all_scenarios = at_severity(config.scenarios, setup.scenario_key, setup.severity)
        self.scenarios = group_scenarios(self.group, self.all_scenarios, setup.scenario_key,
                                         setup.stressed)
        self.scripts = {role: team_script(config.script, self.college, self.group, role)
                        for role in GROUP_ENTITIES}
        self.banks = {
            role: config.banks[self.group.entity(role).bank].model_copy(
                update={"name": self.group.entity(role).name})
            for role in GROUP_ENTITIES}
        # Challenge injects that change no figure; those that change the run cannot be added
        # to a run that is computed with the other entity.
        self.challenges = tuple(
            key for key in active_challenges(config.script, None, False)
            if config.script.challenges[key].effect != "panic_shock")
        self.linear = setup.mode == "linear"
        self._runs: dict[GroupChoices, GroupResult] = {}
        # The decisions the path rests on now, kept in step by `settle`.
        self.current = GroupChoices(setup.planned.parent, setup.planned.subsidiary, setup.planned.group)
        planned = self.run(setup.planned)
        self.planned_group = {r.decision: (r.option, r.decided_day) for r in planned.decisions}
        self.contexts = {
            role: live.Context(
                config.model, config.decisions, self.scripts[role],
                branch_builder=self._builder(role),
                planned_days={r.decision: (r.option, r.decided_day)
                              for r in planned.of(role).decision_log},
            )
            for role in GROUP_ENTITIES
        }
        self.setups = {
            role: live.LiveSetup(
                name=setup.name, bank=self.banks[role], scenario=self.scenarios[role],
                planned=setup.planned.of(role), mode=setup.mode, challenges=self.challenges)
            for role in GROUP_ENTITIES
        }

    def run(self, choices: GroupChoices) -> GroupResult:
        if choices not in self._runs:
            self._runs[choices] = simulate_group(
                self.group, self.config.banks, self.config.model, self.all_scenarios,
                self.setup.scenario_key, self.config.decisions, choices, self.setup.stressed,
                self.college)
        return self._runs[choices]

    def _builder(self, role: str):
        def build(setup: live.LiveSetup, choices: Sequence[Choice]):
            mine = tuple(choices)
            theirs = self.current.of(other(role))
            together = GroupChoices(
                parent=mine if role == PARENT else theirs,
                subsidiary=mine if role == SUBSIDIARY else theirs,
                group=self.current.group)
            return entity_branch(
                role, self.run(together), self.banks[role], self.config.model,
                self.scenarios[role], self.config.decisions, self.scripts, self.college, mine,
                setup.name, self.linear, setup.ambient, self.challenges)
        return build

    def group_choices(self, state: GroupLiveState) -> tuple[Choice, ...]:
        """The group's decisions the path rests on: those recorded, plus the planned ones
        still ahead."""
        if self.linear:
            return self.setup.planned.group
        taken = {d.decision: d for d in state.decisions if d.applied}
        choices = [Choice(d.decision, d.option, d.day) for d in taken.values()]
        for decision, (option, day) in self.planned_group.items():
            if decision not in taken and day >= state.day:
                choices.append(Choice(decision, option, day))
        return tuple(choices)

    def settle(self, state: GroupLiveState, now: str) -> GroupLiveState:
        """Bring both countries' paths in line with every decision recorded so far."""
        states = dict(state.states)
        for _ in range(4):
            wanted = GroupChoices(
                parent=live.effective_choices(states[PARENT], self.contexts[PARENT]),
                subsidiary=live.effective_choices(states[SUBSIDIARY], self.contexts[SUBSIDIARY]),
                group=self.group_choices(state))
            if wanted == self.current:
                break
            self.current = wanted
            for role in GROUP_ENTITIES:
                states[role] = live.apply(states[role], "sync", now, self.contexts[role])
        result = self.run(self.current)
        last = (max(result.parent.end_day, result.subsidiary.end_day), CLOSE)
        horizon = int(self.config.model.horizon_days.value)
        over = all(result.of(role).outcome not in OPEN_OUTCOMES for role in GROUP_ENTITIES)
        ended = state.clock >= last and (over or last[0] >= horizon)
        return replace(state, states=states, result=result, ended=ended)


# --------------------------------------------------------------------------- what is open


def _injects(state: GroupLiveState) -> list[Inject]:
    return [inject for role in GROUP_ENTITIES for injects in state.of(role).branch.injects.values()
            for inject in injects]


def _last(state: GroupLiveState) -> tuple[int, str]:
    return (max(state.result.parent.end_day, state.result.subsidiary.end_day), CLOSE)


def next_moment(state: GroupLiveState) -> Optional[tuple[int, str]]:
    """Where "next step" takes the clock: the next inject time of either country, or the
    close of the day; None at the end."""
    last = _last(state)
    moments = {(i.day, i.time) for i in _injects(state)}
    moments |= {(day, CLOSE) for day in range(state.day, last[0] + 1)}
    later = sorted(m for m in moments if state.clock < m <= last)
    return later[0] if later else None


def next_milestone(state: GroupLiveState) -> Optional[tuple[int, str]]:
    """Where "jump" takes the clock: the next stage change or decision opening in either
    country, a notification across the border, or the end."""
    last = _last(state)
    kinds = live.MILESTONES + ("cross_border",)
    later = sorted((i.day, i.time) for i in _injects(state)
                   if i.kind in kinds and state.clock < (i.day, i.time) <= last)
    if later:
        return later[0]
    return last if state.clock < last else None


def open_decisions(state: GroupLiveState, config: Config) -> tuple[tuple[str, Inject], ...]:
    """(team side, inject) for every decision that can be recorded now: "parent" or
    "subsidiary" for a decision of that country's authorities or bank, "group" for a
    decision of the group (its inject says which team decides)."""
    decided = {d.decision for d in state.decisions if d.applied or state.setup.mode == "linear"}
    out = []
    for role in GROUP_ENTITIES:
        for inject in live.open_decisions(state.of(role)):
            key = inject.decision_due.decision
            if key in system.pair(config, state.setup.group_key)[1].decisions:
                if key not in decided:
                    out.append((GROUP, inject))
            else:
                out.append((role, inject))
    return tuple(out)


def pending_fallbacks(state: GroupLiveState) -> tuple[tuple[str, Inject], ...]:
    """(country side, inject) for what a team should have passed on by now."""
    return tuple((role, inject) for role in GROUP_ENTITIES
                 for inject in live.pending_fallbacks(state.of(role)))


# --------------------------------------------------------------------------- applying entries


def _record_group(state: GroupLiveState, entry: Entry, session: _Session) -> GroupLiveState:
    data, now = entry.data, entry.real_time
    key, option = data.get("decision"), data.get("option")
    college = session.college
    opened = {i.decision_due.decision: (role, i) for role, i in
              ((r, i) for r in GROUP_ENTITIES for i in live.open_decisions(state.of(r)))
              if i.decision_due.decision in college.decisions}
    done = {d.decision for d in state.decisions if d.applied or session.linear}
    if key not in opened or key in done:
        raise LiveError(f"the decision '{key}' is not open")
    definition = college.decisions[key]
    if option not in definition.options:
        raise LiveError(f"'{option}' is not an option of '{key}'")
    role, inject = opened[key]
    since = next(r.real_time for r in state.of(role).released if r.inject is inject)
    taken = (datetime.fromisoformat(now) - datetime.fromisoformat(since)).total_seconds()
    planned_option = session.planned_group.get(key, (definition.default_option, 0))[0]
    record = GroupRecorded(
        decision=key, option=option, entity=decision_entity(definition.entity, state.setup.stressed),
        actor=definition.actor, day=state.day, real_time=now,
        by_control=bool(data.get("by_control", False)),
        applied=not session.linear and option != definition.default_option,
        planned_option=planned_option, deviates=option != planned_option,
        seconds_taken=taken - live.paused_seconds(state.of(role), since, now),
        consulted=tuple(data.get("consulted", ())),
    )
    return replace(state, decisions=state.decisions + (record,))


def _record_college(state: GroupLiveState, entry: Entry, session: _Session) -> GroupLiveState:
    data = entry.data
    subject, took_part = data.get("subject"), tuple(data.get("took_part", ()))
    if subject not in session.college.joint_decision.subjects:
        raise LiveError(f"'{subject}' is not a subject of a joint decision")
    kind = session.group.kind
    known = set(teams(kind))
    if not took_part or any(team not in known for team in took_part) or len(set(took_part)) != len(took_part):
        raise LiveError("say which teams took part")
    if kind == "system":
        if len(took_part) < 2:
            raise LiveError("a joint decision needs at least two teams")
    elif {team.partition(".")[0] for team in took_part} != set(GROUP_ENTITIES):
        raise LiveError("a joint decision needs at least one team from each country")
    if not isinstance(data.get("agreed"), bool):
        raise LiveError("say whether the teams agreed")
    minutes = data.get("minutes")
    if minutes is not None and (not isinstance(minutes, (int, float)) or minutes < 0):
        raise LiveError("the minutes the meeting took cannot be negative")
    record = CollegeRecord(
        subject=subject, took_part=took_part, agreed=data["agreed"],
        note=str(data.get("note", "")).strip(), day=state.day, time=state.time,
        real_time=entry.real_time, minutes=float(minutes) if minutes else None)
    return replace(state, college=state.college + (record,))


def _apply(state: GroupLiveState, entry: Entry, session: _Session) -> GroupLiveState:
    kind, data, now = entry.kind, entry.data, entry.real_time
    contexts = session.contexts
    if kind == "start":
        if state.started:
            raise LiveError("the exercise has already started")
        states = {role: live.start(session.setups[role], now, contexts[role]) for role in GROUP_ENTITIES}
        state = replace(state, states=states, started=True, day=0,
                        time=session.config.script.clock["opening"])
        return session.settle(state, now)
    if not state.started:
        raise LiveError("the exercise has not started")
    if state.ended and kind in ("step", "jump", "decision"):
        raise LiveError("the exercise has ended")
    if state.paused and kind != "resume":
        raise LiveError("the exercise is paused: resume it first")
    states = dict(state.states)
    if kind in ("step", "jump"):
        target = next_moment(state) if kind == "step" else next_milestone(state)
        if target is None:
            raise LiveError("the exercise is at its end")
        how = "clock" if kind == "step" else "jump"
        for role in GROUP_ENTITIES:
            states[role] = live.apply(states[role], "goto", now, contexts[role],
                                      day=target[0], time=target[1], how=how)
        return session.settle(replace(state, states=states, day=target[0], time=target[1]), now)
    if kind == "decision":
        side = data.get("team")
        if side == GROUP:
            return session.settle(_record_group(state, entry, session), now)
        if side not in GROUP_ENTITIES:
            raise LiveError(f"a decision needs a team among {SIDES}")
        if data.get("decision") in session.college.decisions:
            raise LiveError("a decision of the group is recorded for the group")
        states[side] = live.apply(
            states[side], "decision", now, contexts[side], decision=data.get("decision"),
            option=data.get("option"), by_control=data.get("by_control", False),
            consulted=data.get("consulted", ()))
        return session.settle(replace(state, states=states), now)
    if kind == "college":
        return _record_college(state, entry, session)
    if kind in ("fallback", "relay_done", "adhoc"):
        side = data.get("team")
        if side not in GROUP_ENTITIES:
            raise LiveError("this entry needs a country: parent or subsidiary")
        rest = {name: value for name, value in data.items() if name != "team"}
        states[side] = live.apply(states[side], kind, now, contexts[side], **rest)
        return replace(state, states=states)
    if kind in ("pause", "resume"):
        if kind == "resume" and not state.paused:
            raise LiveError("the exercise is not paused")
        for role in GROUP_ENTITIES:
            states[role] = live.apply(states[role], kind, now, contexts[role])
        return replace(state, states=states, paused=kind == "pause")
    raise LiveError(f"unknown entry '{kind}'")


def _current(state: GroupLiveState, session: _Session) -> GroupChoices:
    """The decisions a state's paths rest on (what `settle` left in the session)."""
    if not state.started:
        return session.current
    return GroupChoices(
        parent=live.effective_choices(state.of(PARENT), session.contexts[PARENT]),
        subsidiary=live.effective_choices(state.of(SUBSIDIARY), session.contexts[SUBSIDIARY]),
        group=session.group_choices(state))


def replay(setup: GroupLiveSetup, entries: Sequence[Entry], config: Config) -> GroupLiveState:
    """The state after a log. An "undo" entry takes back the last entry not yet undone."""
    if not system.is_system(config, setup.group_key) and (
            config.college is None or setup.group_key not in config.groups):
        raise LiveError("the configuration has no such group, or no college")
    session = _Session(setup, config)
    # Each state is kept with the decisions its paths rest on, so that it can be taken back.
    states = [(GroupLiveState(setup=setup), session.current)]
    log = []
    for entry in entries:
        if entry.kind not in ENTRY_KINDS:
            raise LiveError(f"unknown entry '{entry.kind}'")
        if entry.kind == "undo":
            if len(states) <= 2:  # nothing, or only the start
                raise LiveError("there is nothing to undo")
            states.pop()
            session.current = states[-1][1]
        else:
            after = _apply(states[-1][0], entry, session)
            states.append((after, session.current))
        log.append(Entry(entry.kind, entry.real_time, entry.data, *states[-1][0].clock))
    return replace(states[-1][0], entries=tuple(log))


def apply(state: GroupLiveState, kind: str, real_time: str, config: Config, **data: Any) -> GroupLiveState:
    """Add one entry to the log and return the new state. Raises LiveError if it cannot be."""
    entry = Entry(kind, real_time, dict(data))
    if kind == "undo" or kind not in ENTRY_KINDS:
        bare = [Entry(e.kind, e.real_time, e.data) for e in state.entries]
        return replay(state.setup, [*bare, entry], config)
    session = _Session(state.setup, config)
    session.current = _current(state, session)
    after = _apply(state, entry, session)  # the same as replaying the longer log
    return replace(after, entries=state.entries + (replace(entry, day=after.day, time=after.time),))


def start(setup: GroupLiveSetup, real_time: str, config: Config) -> GroupLiveState:
    return replay(setup, [Entry("start", real_time)], config)


# --------------------------------------------------------------------------- views and saving


def team_key(role: str, actor: str) -> str:
    return f"{role}.{actor}"


def teams(kind: str = "cross_border") -> tuple[str, ...]:
    """The key of every player team: four in each country of a group, four in all in a
    system, where each team handles both banks."""
    if kind == "system":
        return system.team_keys()
    return tuple(team_key(role, actor) for role in GROUP_ENTITIES for actor in PLAYERS)


def kind_of(setup: GroupLiveSetup, config: Config) -> str:
    return "system" if system.is_system(config, setup.group_key) else "cross_border"


def contexts_of(setup: GroupLiveSetup, config: Config) -> dict:
    """The two countries' live contexts, for display (names, decisions)."""
    return _Session(setup, config).contexts


def player_view(state: GroupLiveState, config: Config, role: str, actor: str) -> dict:
    """What one team's page shows: its own injects so far, as plain data."""
    session = _Session(state.setup, config)
    session.current = _current(state, session)
    view = live.player_view(state.of(role), session.contexts[role], actor)
    view.update(day=state.day, time=state.time,
                status="ended" if state.ended else "paused" if state.paused else "running")
    return view


def player_views(state: GroupLiveState, config: Config) -> dict[str, dict]:
    """Every team's page, by team key. In a system a team's page holds its injects on both
    banks in the order they arrived, each marked with the bank it is about."""
    views = {team_key(role, actor): player_view(state, config, role, actor)
             for role in GROUP_ENTITIES for actor in PLAYERS}
    if kind_of(state.setup, config) != "system":
        return views
    merged = {}
    for actor in PLAYERS:
        first, second = (views[team_key(role, actor)] for role in GROUP_ENTITIES)
        injects = [{**inject, "title": f"{view['bank']}: {inject['title']}"}
                   for view in (first, second) for inject in view["injects"]]
        injects.sort(key=lambda inject: (inject["day"], inject["time"], inject["received"]))
        merged[f"both.{actor}"] = {**first, "bank": f"{first['bank']} and {second['bank']}",
                                   "injects": injects}
    return merged


def to_saved(state: GroupLiveState, config: Config) -> dict:
    """The exercise as plain data: set-up, the configuration it ran on, and the log."""
    setup = state.setup
    as_lists = lambda choices: [[c.decision, c.option, c.day] for c in choices]  # noqa: E731
    return {
        "format": SAVE_FORMAT,
        "label": "SIMULATION - exercise material, not a real event",
        "setup": {
            "name": setup.name, "group_key": setup.group_key, "scenario_key": setup.scenario_key,
            "stressed": setup.stressed, "mode": setup.mode, "severity": setup.severity,
            "planned": {side: as_lists(getattr(setup.planned, side)) for side in SIDES},
        },
        "config": config.model_dump(),
        "log": [{"kind": e.kind, "real_time": e.real_time, "data": e.data, "day": e.day, "time": e.time}
                for e in state.entries],
    }


def from_saved(data: dict) -> tuple[GroupLiveState, Config]:
    """Rebuild a group exercise by replaying its log on the configuration saved with it."""
    if data.get("format") != SAVE_FORMAT:
        raise LiveError("this file is not a saved group exercise of this version")
    config = Config.model_validate(data["config"])
    saved = data["setup"]
    planned = GroupChoices(**{
        side: tuple(Choice(*choice) for choice in saved["planned"][side]) for side in SIDES})
    setup = GroupLiveSetup(saved["name"], saved["group_key"], saved["scenario_key"],
                           saved["stressed"], planned, saved["mode"],
                           saved.get("severity", "central"))
    entries = [Entry(e["kind"], e["real_time"], dict(e["data"])) for e in data["log"]]
    return replay(setup, entries, config), config


# --------------------------------------------------------------------------- closing report


@dataclass(frozen=True)
class GroupCounterfactual:
    decision_name: str
    team: str
    change: str
    parent: str  # outcome and last day
    subsidiary: str
    lent: float


@dataclass(frozen=True)
class GroupReport:
    title: str
    group_name: str
    unit: str
    overview: tuple[tuple[str, str], ...]
    countries: dict  # "parent" / "subsidiary" -> that country's ClosingReport
    decisions: tuple[tuple[str, ...], ...]  # the group's decisions, as rows
    college: tuple[tuple[str, ...], ...]  # joint decisions of the college, as rows
    crossing: tuple[tuple[str, ...], ...]  # steps across the border, recorded as done or not
    transfers: tuple[tuple[str, ...], ...]
    counterfactuals: tuple[GroupCounterfactual, ...]
    kind: str = "cross_border"


def _outcome(run) -> str:
    return f"{OUTCOME_LABELS[run.outcome]}, day {run.end_day}"


_STAGE_WORDS = (("failed", "payments default"), ("likely_to_fail", "likely to fail"),
                ("early_intervention", "early intervention"), ("recovery", "recovery indicator breached"))


def path_text(run) -> str:
    """A run in one line: outcome, deposits lost and the furthest stage reached, with its
    first day. Two runs with the same outcome can differ in the rest."""
    statuses = [str(row["status"]) for row in run.rows]
    stage = next((f"{words} from day {statuses.index(status)}"
                  for status, words in _STAGE_WORDS if status in statuses), "no stage reached")
    return f"{_outcome(run)}; {run.scorecard.deposits_lost_pct:.1f}% of deposits lost; {stage}"


def _system_cases(state: GroupLiveState, session: _Session, names) -> list[GroupCounterfactual]:
    """What the model gives for both banks with one recorded decision on one bank changed:
    every other option, at the same close. Everything else as it happened."""
    base, catalogue, cases = session.current, session.config.decisions.decisions, []
    for role in GROUP_ENTITIES:
        recorded = {}
        for d in state.of(role).decisions:
            if d.decision not in recorded or d.applied or not recorded[d.decision].applied:
                recorded[d.decision] = d
        for key, record in recorded.items():
            definition = catalogue[key]
            log = {r.decision: r for r in state.result.of(role).decision_log}
            current = log[key].option if key in log else definition.default_option
            at = log[key].decided_day if key in log else record.day
            others = tuple(c for c in base.of(role) if c.decision != key)
            for option, item in definition.options.items():
                if option == current:
                    continue
                mine = others + (() if option == definition.default_option else (Choice(key, option, at),))
                run = session.run(GroupChoices(
                    parent=mine if role == PARENT else base.parent,
                    subsidiary=mine if role == SUBSIDIARY else base.subsidiary, group=base.group))
                cases.append(GroupCounterfactual(
                    f"{definition.name} ({session.group.entity(role).name})",
                    names(role, definition.actor),
                    f"\"{item.name}\" instead of \"{definition.options[current].name}\", "
                    f"at the close of day {at}",
                    path_text(run.parent), path_text(run.subsidiary), 0.0))
    return cases


def college_rows(state: GroupLiveState, college, names) -> tuple[tuple[str, ...], ...]:
    """Joint decisions of the college as rows: who took part, who of the expected members
    did not. `names(role, actor)` gives a team's name."""
    both = any(c.took_part and c.took_part[0].startswith("both.") for c in state.college)
    expected = [team_key("both" if both else resolve_team(reference, PARENT)[0],
                         resolve_team(reference, PARENT)[1])
                for reference in college.joint_decision.members]
    team_name = lambda team: names(PARENT if team.startswith("both.") else team.split(".")[0],  # noqa: E731
                                   team.split(".")[1])
    return tuple(
        (f"Day {c.day}, {c.time}", c.subject, "Agreed" if c.agreed else "Not agreed",
         ", ".join(team_name(team) for team in c.took_part),
         ", ".join(team_name(team) for team in expected if team not in c.took_part) or "none",
         c.real_time.replace("T", " "),
         "not recorded" if c.minutes is None else f"{c.minutes:.0f}", c.note)
        for c in state.college)


def group_report(state: GroupLiveState, config: Config) -> GroupReport:
    """The draft closing report of a live group exercise: each country's own report, and
    what belongs to the group. Facts and differences in outcomes only."""
    session = _Session(state.setup, config)
    session.current = _current(state, session)
    group, college, result = session.group, session.college, state.result
    unit = session.banks[PARENT].unit
    countries: dict[str, ClosingReport] = {
        role: live_report(state.of(role), session.contexts[role]) for role in GROUP_ENTITIES}
    names = lambda role, actor: session.scripts[role].actors[actor]  # noqa: E731

    decisions = tuple(
        (f"Day {d.day}", names(d.entity, d.actor), college.decisions[d.decision].name,
         college.decisions[d.decision].options[d.option].name,
         college.decisions[d.decision].options[d.planned_option].name,
         "not recorded" if d.seconds_taken is None else f"{d.seconds_taken / 60:.1f}",
         "; ".join(note for note in (
             "" if not d.deviates else "different from the pre-planned path",
             "taken by the control team" if d.by_control else "",
             "" if d.applied else "logged; the path did not change") if note))
        for d in state.decisions)

    joint = college_rows(state, college, names)

    # Steps across the border: a notification from the other country, or a decision of the
    # group told to another team. Each is recorded as passed on by the team, or not.
    crossing = []
    for role in GROUP_ENTITIES:
        country_state = state.of(role)
        released = {live.inject_key(r.inject) for r in country_state.released if r.how == "fallback"}
        own = set(session.scripts[role].actors.values())
        for actor in PLAYERS:
            for inject in country_state.branch.injects[actor]:
                if not inject.fallback or inject.sender in own or (inject.day, inject.time) > state.clock:
                    continue
                key = live.inject_key(inject)
                how = ("passed on by the team" if key in country_state.passed_on else
                       "the control team released the fallback inject" if key in released else "no record")
                crossing.append((f"Day {inject.day}", inject.title, inject.sender,
                                 names(role, actor), how))
    crossing.sort()

    transfers = tuple(
        (f"Day {t.day}", group.entity(t.lender).name, group.entity(t.borrower).name,
         f"{t.amount:.2f}", "asked for early" if t.early else "")
        for t in result.transfers)

    # What the model gives with one decision of the group changed, all else as it happened.
    cases = []
    taken = {d.decision: d for d in state.decisions if d.applied}
    base = session.current
    for key, definition in college.decisions.items():
        role = decision_entity(definition.entity, state.setup.stressed)
        current = taken[key].option if key in taken else definition.default_option
        at = taken[key].day if key in taken else next(
            (r.decided_day for r in result.decisions if r.decision == key), None)
        if at is None:
            at = open_day(result, college, key)
        if at is None:
            continue
        others = tuple(c for c in base.group if c.decision != key)
        for option, item in definition.options.items():
            if option == current:
                continue
            extra = () if option == definition.default_option else (Choice(key, option, at),)
            run = session.run(GroupChoices(base.parent, base.subsidiary, others + extra))
            cases.append(GroupCounterfactual(
                definition.name,
                names(role, definition.actor),
                f"\"{item.name}\" instead of \"{definition.options[current].name}\", at the close of day {at}",
                _outcome(run.parent), _outcome(run.subsidiary), sum(t.amount for t in run.transfers)))

    entries = state.entries
    if group.kind == "system":
        cases = _system_cases(state, session, names)
        overview = (
            ("Exercise", state.setup.name),
            ("System", f"{group.name}: {group.parent.name} and {group.subsidiary.name}, two unrelated "
                       f"banks of {group.parent.country}"),
            ("Scenario", f"{scenario_of(state.setup, config).name} on {group.entity(state.setup.stressed).name}; "
                         f"{config.scenarios[group.calm_scenario].name} on the other bank"),
            ("Type of exercise", state.setup.mode.capitalize()),
            ("Started (real time)", entries[0].real_time.replace("T", " ")),
            ("Last entry (real time)", entries[-1].real_time.replace("T", " ")),
            ("Simulated time reached", f"Day {state.day}, {state.time}"),
            ("Status", "Ended" if state.ended else "Not finished when this report was drawn up"),
            (f"{group.parent.name}", path_text(result.parent)),
            (f"{group.subsidiary.name}", path_text(result.subsidiary)),
            ("Strength of the link between the two banks",
             f"{group.confidence_link.value:g} (factor on the contagion shocks)"),
            ("Joint decisions of the authorities recorded",
             f"{len(state.college)}, of which agreed {sum(c.agreed for c in state.college)}"),
        )
        return GroupReport(
            title=state.setup.name, group_name=group.name, unit=unit, overview=overview,
            countries=countries, decisions=(), college=joint, crossing=(), transfers=(),
            counterfactuals=tuple(cases), kind="system",
        )
    overview = (
        ("Exercise", state.setup.name),
        ("Group", f"{group.name}: {group.parent.name} ({group.parent.country}) and "
                  f"{group.subsidiary.name} ({group.subsidiary.country})"),
        ("Scenario", f"{scenario_of(state.setup, config).name}, on the {state.setup.stressed}"),
        ("Type of exercise", state.setup.mode.capitalize()),
        ("Started (real time)", entries[0].real_time.replace("T", " ")),
        ("Last entry (real time)", entries[-1].real_time.replace("T", " ")),
        ("Simulated time reached", f"Day {state.day}, {state.time}"),
        ("Status", "Ended" if state.ended else "Not finished when this report was drawn up"),
        (f"{group.parent.name}", _outcome(result.parent)),
        (f"{group.subsidiary.name}", _outcome(result.subsidiary)),
        (f"Lent inside the group ({unit})", f"{sum(t.amount for t in result.transfers):.2f}"),
        ("Restriction of transfers in effect",
         "no" if result.ring_fence_from is None else f"from day {result.ring_fence_from}"),
        ("Decisions of the group recorded", str(len(state.decisions))),
        ("Joint decisions of the college recorded",
         f"{len(state.college)}, of which agreed {sum(c.agreed for c in state.college)}"),
    )
    return GroupReport(
        title=state.setup.name, group_name=group.name, unit=unit, overview=overview,
        countries=countries, decisions=decisions, college=joint, crossing=tuple(crossing), transfers=transfers,
        counterfactuals=tuple(cases),
    )
