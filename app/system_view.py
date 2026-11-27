"""System-wide stress in the app (SPEC_V3 phase F): what the Design page shows when the
exercise is on two unrelated banks of one country.

It only calls the engine (engine/system.py and the group layer it reuses) and the chart
builders of app/charts.py. The Prepare page is the group's (app/group_view.py).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app import ui
from app.charts import build_group_comparison, build_group_map, build_group_timeline, build_information_map
from app.design import initial
from engine import system
from engine.decisions import OUTCOME_LABELS, choices_of
from engine.group import SEVERITY_LEVELS, GroupChoices, other
from engine.group_live import path_text
from engine.schema import GROUP_ENTITIES, PLAYERS, Config
from engine.script import information_map

DESIGN_KEY = "system_design"
TABS = ["System timeline", "Map", "Paths compared", "Information map"]


def showcase_design(config: Config):
    """The system run the app opens on (config/systems.yaml, `showcase`)."""
    from app.group_view import GroupDesign  # the two modules use each other's names

    show, paths = config.systems.showcase, config.decisions.paths
    slot = system.SLOTS[show.stressed]
    choices = {slot: choices_of(paths[show.path]), other(slot): ()}
    reference = {slot: choices_of(paths[show.compare_path]), other(slot): ()}
    return GroupDesign(
        show.pair, show.scenario, slot, GroupChoices(**choices), show.severity,
        GroupChoices(**reference), paths[show.compare_path].name)


def sidebar(config: Config):
    """The system's controls, in the sidebar. The design is kept for the Prepare page."""
    from app.group_view import GroupDesign

    systems, show, paths = config.systems, config.systems.showcase, config.decisions.paths
    crises = [key for key, item in config.scenarios.items() if item.taxonomy is not None]
    with st.sidebar:
        st.header("System")
        key = st.selectbox(
            "Two banks", list(systems.pairs), key="sys_pair",
            format_func=lambda k: f"{systems.pairs[k].name}: {systems.pairs[k].first.name} and "
                                  f"{systems.pairs[k].second.name}",
            help="Two unrelated banks of one country. Both are fictitious (config/systems.yaml).",
            **initial("sys_pair", index=list(systems.pairs).index(show.pair)),
        )
        pair = systems.pairs[key]
        names = {"parent": pair.first.name, "subsidiary": pair.second.name}
        scenario_key = st.selectbox(
            "Trigger scenario", crises, key="sys_scenario",
            format_func=lambda k: config.scenarios[k].name,
            **initial("sys_scenario", index=crises.index(show.scenario)),
        )
        severity_of = config.scenarios[scenario_key].severity
        values = {"low": severity_of.low, "central": severity_of.value, "high": severity_of.high}
        severity = st.radio(
            "Severity", SEVERITY_LEVELS, key="sys_severity", horizontal=True,
            help="The scenario at the low, central or high value of its own severity range "
                 f"(config/scenarios.yaml): {values['low']:g}, {values['central']:g}, {values['high']:g}. "
                 "The value multiplies every deposit run-off rate.",
            **initial("sys_severity", index=SEVERITY_LEVELS.index(show.severity)),
        )
        stressed = st.radio(
            "The scenario hits", GROUP_ENTITIES, key="sys_stressed", format_func=lambda r: names[r],
            **initial("sys_stressed", index=GROUP_ENTITIES.index(system.SLOTS[show.stressed])),
        )
        st.caption(
            f"{names[other(stressed)]} is under a stress of its own: "
            f"{config.scenarios[pair.other_scenario].name.lower()}.")
        st.header("Decisions")
        chosen = {}
        for role in (stressed, other(stressed)):
            default = show.path if role == stressed else show.compare_path
            chosen[role] = st.selectbox(
                f"Decision path on {names[role]}", list(paths), key=f"sys_path_{role}",
                format_func=lambda k: paths[k].name,
                **initial(f"sys_path_{role}", index=list(paths).index(default)),
            )
        compare = st.selectbox(
            f"Compared with, on {names[stressed]}", list(paths), key="sys_compare",
            format_func=lambda k: paths[k].name,
            help="The second path of the comparison and of the exercise pack. The other bank's "
                 "decisions are the same on both.",
            **initial("sys_compare", index=list(paths).index(show.compare_path)),
        )
    choices = GroupChoices(**{role: choices_of(paths[chosen[role]]) for role in GROUP_ENTITIES})
    reference = GroupChoices(**{stressed: choices_of(paths[compare]),
                                other(stressed): choices_of(paths[chosen[other(stressed)]])})
    design = GroupDesign(key, scenario_key, stressed, choices, severity, reference,
                         f"{paths[compare].name} on {names[stressed]}")
    st.session_state[DESIGN_KEY] = design
    return design


def _outcome(run) -> str:
    text = OUTCOME_LABELS[run.outcome]
    return text if run.failure_day is None else f"{text}, day {run.failure_day}"


def _stages(result, role: str) -> str:
    words = lambda stage: stage.replace("_", " ").replace("failure", "payments default")  # noqa: E731
    return "; ".join(f"{words(stage)} day {day}" for stage, day in dict(result.stage_days)[role]) or "none"


def _furthest(result, role: str) -> str:
    reached = dict(result.stage_days)[role]
    if not reached:
        return "None reached"
    stage, day = reached[-1]
    return f"{stage.replace('_', ' ').replace('failure', 'payments default').capitalize()}, day {day}"


def design_page(config: Config) -> None:
    """The Design page for a system."""
    from app.group_view import _pack, _weekends

    design = sidebar(config)
    group, _ = system.pair(config, design.group_key)
    scenario = design.scenarios(config)[design.scenario_key]
    unit = config.banks[group.parent.bank].unit
    ui.header("Design", f"{group.name} · {scenario.name}")
    st.title("Design")
    st.caption(
        f"{group.name}: {group.parent.name} and {group.subsidiary.name}, two unrelated banks of "
        f"{group.parent.country}, handled by the same four authorities. {scenario.name} on "
        f"{group.entity(design.stressed).name}. Amounts in {unit}. {system.NOTE}"
    )
    pack = _pack(config, design)
    result, reference = pack.branches[-1].result, pack.branches[0].result
    weekends = _weekends(config, scenario)
    same = group.parent.bank == group.subsidiary.bank
    ui.cards([
        (group.parent.name, _outcome(result.parent),
         f"{result.parent.scorecard.deposits_lost_pct:.1f}% of deposits lost", result.parent.outcome),
        (group.subsidiary.name, _outcome(result.subsidiary),
         f"{result.subsidiary.scorecard.deposits_lost_pct:.1f}% of deposits lost",
         result.subsidiary.outcome),
        ("Link between the two banks", f"{group.confidence_link.value:g}",
         "Same country" + (" and same archetype" if same else ", different archetypes")),
        (f"{group.entity(other(design.stressed)).name}: furthest stage", _furthest(result, other(design.stressed)),
         "On the selected path. \"Paths compared\" shows the other path."),
    ], columns=4)

    timeline, system_map, compared, information = st.tabs(TABS)
    with timeline:
        ui.chart(build_group_timeline(result, unit, scenario.start_weekday, weekends), key="system_timeline")
        st.caption(
            "The two banks lend nothing to each other. A stage reached by one (recovery indicator "
            "breached, early intervention, payments default) is a panic shock for the other the "
            "next day, scaled by the link above. A bank resolved before it defaults sends the "
            "smaller shock of a resolution in place of that of a payments default. All of it is assumed (config/systems.yaml, config/model.yaml)."
        )
        with st.expander("Both banks day by day"):
            for role in GROUP_ENTITIES:
                st.markdown(f"**{group.entity(role).name}**")
                table = result.of(role).table[[
                    "day", "status", "deposits_total", "cash", "liquidity_buffer", "outflow_total",
                    "unmet_outflow"]]
                stages = list(table["status"])
                table = table.rename(columns={
                    "day": "Day", "status": "Stage", "deposits_total": f"Deposits ({unit})",
                    "cash": f"Cash ({unit})", "liquidity_buffer": f"Liquidity buffer ({unit})",
                    "outflow_total": f"Outflow ({unit})", "unmet_outflow": f"Unpaid ({unit})"})
                table["Stage"] = table["Stage"].map(ui.stage_text)
                ui.table(table, stage_column="Stage", stages=stages)
    with system_map:
        last = max(result.parent.end_day, result.subsidiary.end_day)
        day = st.slider("Day shown", 0, last, min(last, 1), key=f"system_map_day_{last}")
        teams = {role: [pack.scripts[role].actors[actor] for actor in PLAYERS] for role in GROUP_ENTITIES}
        ui.chart(build_group_map(result, day, unit, teams, scenario.weekday(day) if day >= 1 else ""),
                 key="system_map")
        st.caption("A diagram, not geography: the country is fictitious.")
    with compared:
        st.subheader(f"What a decision on {group.entity(design.stressed).name} does to both banks")
        if len(pack.branches) == 1:
            st.info("The two paths set in the sidebar are the same: there is one path. Pick "
                    "another path to compare with.")
        else:
            ui.chart(build_group_comparison(
                reference, result, design.reference_name, "Selected path", unit,
                scenario.start_weekday, weekends), key="system_comparison")
        rows = [
            {"Path": branch.name, "Bank": group.entity(role).name,
             "Path in one line": path_text(branch.result.of(role)),
             "First day of each stage": _stages(branch.result, role)}
            for branch in pack.branches for role in GROUP_ENTITIES]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption(
            "The other bank's own decisions are the same on both paths: what differs for it "
            "comes from the first bank only, through confidence.")
    with information:
        st.subheader("Who knows what, when, on each bank")
        st.caption(
            "The same four teams hold both banks' files. On each bank a team sees the figures "
            "of its own information set, at its own delay, as for a single bank "
            "(config/decisions.yaml).")
        last = max(result.parent.end_day, result.subsidiary.end_day)
        day = st.slider("Day shown", 0, last, min(last, 1), key=f"system_info_day_{last}")
        for role in GROUP_ENTITIES:
            st.markdown(f"**{group.entity(role).name}**")
            ui.chart(build_information_map(
                information_map(pack.packs[role].branches[-1], config.decisions, pack.scripts[role], day),
                scenario.weekday(day) if day >= 1 else ""), key=f"system_information_{role}")
