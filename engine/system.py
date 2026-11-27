"""System-wide stress (SPEC_V3 phase F): two unrelated banks of one country at once.

Pure functions. A pair of config/systems.yaml becomes a `Group` of kind "system", so that
the group layer runs it unchanged (engine/group.py, group_script.py, group_live.py):

- the two banks lend nothing to each other: every limit on transfers is zero;
- a stage reached by one is a panic shock for the other the next day, the contagion shocks
  of model.yaml times the similarity of the two banks (engine/contagion.py);
- there is no decision of the group and no restriction of transfers;
- the same four authorities handle both banks.

The first bank takes the place of the "parent" and the second that of the "subsidiary" in
the group layer: these are only the names of the two slots.
"""

from __future__ import annotations

from typing import Optional

from engine.contagion import similarity_factor
from engine.group import PARENT, SUBSIDIARY
from engine.schema import (
    PLAYERS, College, Config, Group, GroupEntity, JointDecision, ModelParams, Parameter, Systems,
)

SLOTS = {"first": PARENT, "second": SUBSIDIARY}
NOTE = (
    "The system in this exercise is an assumption of the tool: two unrelated banks of one "
    "country, linked only by confidence (config/systems.yaml). It is a draft for discussion "
    "in the exercise, not a statement of legal duties."
)
_NOT_USED = "not used: the two banks of a system are unrelated"


def similarity(systems: Systems, key: str, params: ModelParams) -> float:
    """How strongly a stage change at one bank reaches the other: the similarity of two banks
    of the same country, of the same archetype or not, from the parameters of model.yaml."""
    pair = systems.pairs[key]
    return similarity_factor(pair.first.bank == pair.second.bank, True, params)


def as_group(systems: Systems, key: str, params: ModelParams) -> Group:
    """The pair as a group of kind "system"."""
    pair = systems.pairs[key]
    zero = Parameter(value=0.0, unit="share", source=_NOT_USED)
    return Group(
        name=pair.name, kind="system",
        parent=GroupEntity(name=pair.first.name, bank=pair.first.bank, country=pair.country),
        subsidiary=GroupEntity(name=pair.second.name, bank=pair.second.bank, country=pair.country),
        calm_scenario=pair.other_scenario,
        transfer_limit_share=zero, lender_cash_floor_share=zero, claim_loss_at_default=zero,
        ring_fence_shock=Parameter(value=0.0, unit="added to the panic input", source=_NOT_USED),
        confidence_link=Parameter(
            value=similarity(systems, key, params),
            unit="factor on the contagion shocks, computed from model.yaml: "
                 "contagion_similarity_same_country, plus contagion_similarity_same_archetype "
                 "for two banks of the same archetype",
            source="ASSUMPTION"),
        headlines={**systems.headlines, "ring_fence": _NOT_USED},
    )


def forum(systems: Systems) -> College:
    """What the group layer asks of a college, for one country: team names without a
    country, no decision of the group, no delay, the joint decision of the authorities."""
    none = Parameter(value=0.0, unit="days", source=_NOT_USED)
    return College.model_construct(
        team_name="{actor}", cross_border_delay_days=none, consolidated_delay_days=none,
        cross_border_clock="", showcase=None, decisions={}, coordination=[],
        joint_decision=JointDecision(
            name=systems.joint_decision.name, subjects=systems.joint_decision.subjects,
            members=[f"home:{actor}" for actor in systems.joint_decision.members]),
        texts={"information_system": systems.information,
               "coordination_system": "\n".join(systems.coordination)},
    )


def is_system(config: Config, key: str) -> bool:
    return config.systems is not None and key in config.systems.pairs


def pair(config: Config, key: str) -> tuple[Group, Optional[College]]:
    """(group, college) for a key of config/groups.yaml or of config/systems.yaml."""
    if is_system(config, key):
        return as_group(config.systems, key, config.model), forum(config.systems)
    return config.groups[key], config.college


def team_keys() -> tuple[str, ...]:
    """The four teams of a system exercise: each handles both banks."""
    return tuple(f"both.{actor}" for actor in PLAYERS)
