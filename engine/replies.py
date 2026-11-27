"""Replies to player requests drafted by a general-purpose AI (SPEC_V3 phase K): what the
model is given, and the check on what it returns.

Pure functions: no network, no file. The call to the model is made elsewhere (app/ai.py).

The model gets only this, for the stakeholder asked (one of the role cards the control team
plays): what the role knows, wants and says at the stage the bank is in now, and the figures
that stakeholder holds at the last close, each with its day. Nothing of another actor,
nothing of a later day, no decision not yet taken. A draft is refused if it contains a
number that was not in that material.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from engine import live
from engine.resolution import indicative_bid
from engine.schema import PLAYERS, ROLE_STAGES, ROLES, AiConfig
from engine.decisions import format_figure

_MOMENTS = tuple(ROLE_STAGES)  # normal ... resolution, in order
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


@dataclass(frozen=True)
class Grounding:
    """Everything the model is given for one request."""

    stakeholder: str  # key of the role card
    stakeholder_name: str
    asked_by: str  # the player team that asks, as named in the exercise
    bank_name: str
    day: int
    weekday: str
    time: str
    as_of_day: int  # the last close: the day the figures are of
    stage: str  # the moment of the role card that applies
    knows: tuple[str, ...]
    wants: tuple[str, ...]
    says: str
    figures: tuple[str, ...]  # each as printed, with its day
    question: str


@dataclass(frozen=True)
class Draft:
    text: str
    ok: bool  # every number in the text was supplied
    offending: tuple[str, ...]  # the numbers that were not
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0  # in the currency of the configured prices
    note: str = ""  # e.g. the model declined


def _moment(state: live.LiveState, as_of: int) -> str:
    result = state.branch.result
    if result.outcome not in ("stabilised", "emergency_funding", "under_stress", "payments_default") \
            and result.end_day <= as_of:
        return "resolution"
    if result.foltf_day is not None and result.foltf_day <= as_of:
        return "foltf"
    status = str(result.rows[min(as_of, len(result.rows) - 1)]["status"])
    return {"failed": "likely_to_fail"}.get(status, status)


def _figures(state: live.LiveState, context: live.Context, stakeholder: str, as_of: int) -> tuple[str, ...]:
    """The figures a stakeholder holds at the close of `as_of`, as printed, with their day."""
    setup, result = state.setup, state.branch.result
    unit, when = setup.bank.unit, f"at the close of day {as_of}"
    row = result.rows[min(as_of, len(result.rows) - 1)]
    declared = result.foltf_day is not None and result.foltf_day <= as_of
    resolution = state.branch.resolution
    if stakeholder == "bank_management":
        names = context.decisions.information["bank_management"].figures
        return tuple(f"{format_figure(name, float(row[name]), unit)} ({when})" for name in names)
    if stakeholder == "media":
        return tuple(f"Day {event.day}: {event.detail}" for event in result.events
                     if event.kind == "news" and event.day <= state.day)
    if stakeholder == "independent_valuer" and declared and resolution is not None:
        from engine.script import _resolution_rows

        level = setup.writedown_level
        return tuple(f"{label}: {value} ({level} write-down)"
                     for label, value in _resolution_rows(resolution[level], unit))
    if stakeholder == "potential_buyers" and declared and resolution is not None:
        resolved = context.params.with_overrides(setup.scenario.overrides)
        price = indicative_bid(resolution[setup.writedown_level], resolved)
        return (f"Your indicative, non-binding price: {price:.2f} {unit}",)
    return ()


def grounding(state: live.LiveState, context: live.Context, stakeholder: str, asked_by: str,
              question: str) -> Grounding:
    """What the model gets for a request to `stakeholder` from the player team `asked_by`."""
    if stakeholder not in ROLES:
        raise live.LiveError(f"'{stakeholder}' is not a stakeholder the control team plays")
    if asked_by not in PLAYERS:
        raise live.LiveError(f"'{asked_by}' is not a player team")
    question = " ".join(str(question).split())
    if not question:
        raise live.LiveError("type the players' request")
    if not state.started:
        raise live.LiveError("the exercise has not started")
    setup, role = state.setup, context.script.roles[stakeholder]
    as_of = max(0, state.day if state.time >= live.CLOSE else state.day - 1)
    as_of = min(as_of, state.branch.result.end_day)
    moment = _moment(state, as_of)
    # The latest line the role has, at or before the moment reached.
    reached = _MOMENTS[: _MOMENTS.index(moment) + 1] if moment in _MOMENTS else _MOMENTS[:1]
    stage = next((m for m in reversed(reached) if m in role.lines), next(iter(role.lines)))
    fill = lambda line: line.format(bank_name=setup.bank.name)  # noqa: E731
    return Grounding(
        stakeholder=stakeholder, stakeholder_name=role.name,
        asked_by=context.script.actors[asked_by], bank_name=setup.bank.name,
        day=state.day, weekday=setup.scenario.weekday(state.day) if state.day >= 1 else "",
        time=state.time, as_of_day=as_of, stage=ROLE_STAGES[stage],
        knows=tuple(fill(line) for line in role.knows), wants=tuple(fill(line) for line in role.wants),
        says=fill(role.lines[stage]), figures=_figures(state, context, stakeholder, as_of),
        question=question,
    )


def prompt(material: Grounding, config: AiConfig) -> tuple[str, str]:
    """(system, user) exactly as sent to the model."""
    system = config.instructions.format(
        stakeholder=material.stakeholder_name, bank_name=material.bank_name)
    lines = [
        f"Simulated time: day {material.day}"
        + (f", {material.weekday}" if material.weekday else "") + f", {material.time}.",
        f"Stage of {material.bank_name}, as you see it: {material.stage}.",
        "", "What you know:", *(f"- {line}" for line in material.knows),
        "", "What you want:", *(f"- {line}" for line in material.wants),
        "", f"What you say at this stage: {material.says}",
        "", "Figures you hold:",
        *((f"- {line}" for line in material.figures) if material.figures else ("- none",)),
        "", f"Request from the {material.asked_by}: {material.question}",
        "", "Write your reply.",
    ]
    return system, "\n".join(lines)


def numbers(text: str) -> tuple[float, ...]:
    return tuple(float(token.replace(",", ".")) for token in _NUMBER.findall(text))


def offending(text: str, material: Grounding, config: AiConfig) -> tuple[str, ...]:
    """The numbers in `text` that are not in the material the model was given."""
    system, user = prompt(material, config)
    supplied = set(numbers(system + "\n" + user))
    return tuple(dict.fromkeys(token for token in _NUMBER.findall(text)
                               if float(token.replace(",", ".")) not in supplied))


def checked(text: str, material: Grounding, config: AiConfig, model: str = "",
            input_tokens: int = 0, output_tokens: int = 0, note: str = "") -> Draft:
    """A draft with the result of the check and the cost of the call."""
    text = text.strip()
    wrong = offending(text, material, config)
    cost = (input_tokens * config.price_input.value + output_tokens * config.price_output.value) / 1e6
    return Draft(text, bool(text) and not wrong, wrong, model, input_tokens, output_tokens, cost, note)


def fixed_reply(material: Grounding) -> str:
    """A canned reply, with no model: the role's line and the first figures it holds. Used
    by the "fixed" provider, to try the form and in the tests."""
    held = " ".join(f"{line}." for line in material.figures[:2])
    return (f"{material.says} " + (f"What I can give you: {held}" if held
                                   else "I hold no figure on this that I can give you.")).strip()


def record(material: Grounding, draft: Draft, sent_text: str) -> dict:
    """What is logged with the inject sent: the request, the draft and its origin."""
    return {"stakeholder": material.stakeholder, "asked_by": material.asked_by,
            "question": material.question, "draft": draft.text, "model": draft.model or "fixed reply",
            "edited": sent_text.strip() != draft.text.strip()}


def title_of(material: Grounding) -> str:
    return f"Reply from {material.stakeholder_name[0].lower() + material.stakeholder_name[1:]}"


def sender_of(material: Grounding, context: live.Context) -> str:
    if material.stakeholder == "bank_management":
        return context.script.actors.get("bank_management", material.stakeholder_name)
    return material.stakeholder_name


def as_optional(value: Optional[dict]) -> Optional[dict]:
    return value if isinstance(value, dict) and value.get("question") else None
