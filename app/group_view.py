"""The cross-border group in the app (SPEC_V2 and SPEC_V3 phase F, part c): what the Design
and Prepare pages show when the exercise is on a group instead of a single bank.

It only calls the engine (engine/group.py, engine/group_script.py, engine/group_export.py)
and the chart builders of app/charts.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import streamlit as st

from app import ui
from app.charts import (
    build_branch_timeline,
    build_group_comparison,
    build_group_map,
    build_group_timeline,
    build_information_map,
    restriction_label,
)
from app import system_view
from app.design import get_config, initial
from engine import system
from engine.decisions import OUTCOME_LABELS, Choice
from engine.group import (
    PARENT, SEVERITY_LEVELS, SUBSIDIARY, GroupChoices, at_severity, simulate_group,
)
from engine.group_export import COLLEGE_NOTE, group_decision_points_frame, group_to_zip
from engine.group_script import build_group_pack
from engine.schema import GROUP_ENTITIES, PLAYERS, Config
from engine.script import MODE_LABELS, MODE_TEXT, MODES, information_map

SINGLE, GROUP, SYSTEM = "Single bank", "Cross-border group", "System-wide"
KIND_KEY = "exercise_kind"
DESIGN_KEY = "group_design"
NO_DECISION = "No decision of the group"
TABS = ["Group timeline", "Map", "Decisions compared", "Information map"]


@dataclass(frozen=True)
class GroupDesign:
    group_key: str
    scenario_key: str
    stressed: str
    choices: GroupChoices
    severity: str = "central"
    # The path the selected one is compared with, and its name.
    reference: GroupChoices = GroupChoices()
    reference_name: str = NO_DECISION

    def scenarios(self, config: Config):
        """The scenarios, the chosen one at the severity of the design."""
        return at_severity(config.scenarios, self.scenario_key, self.severity)


def is_group() -> bool:
    """The exercise is on a group or on a system (and the guided demo, which is on a single
    bank, is off)."""
    return st.session_state.get(KIND_KEY) in (GROUP, SYSTEM) and not st.session_state.get("demo_on")


def is_system() -> bool:
    return st.session_state.get(KIND_KEY) == SYSTEM and not st.session_state.get("demo_on")


NO_PATH = "act_late"  # the decision path with no decision: each country's panel opens on it


def _load_country_path(role: str) -> None:
    """Set a country's decision widgets to the options and days of the chosen named path."""
    catalogue = get_config().decisions
    chosen = {c.decision: c for c in catalogue.paths[st.session_state[f"grp_path_{role}"]].choices}
    for key, decision in catalogue.decisions.items():
        choice = chosen.get(key)
        st.session_state[f"grp_dec_{role}_{key}_option"] = choice.option if choice else decision.default_option
        if choice:
            st.session_state[f"grp_dec_{role}_{key}_day"] = choice.day


def _country_panel(config: Config, group, role: str, horizon: int) -> tuple[Choice, ...]:
    """The decisions of one country's authorities and of its entity's management: a named
    path, and every decision one by one."""
    catalogue, entity = config.decisions, group.entity(role)
    side = "home" if role == PARENT else "host"
    st.header(f"Decisions in {entity.country}")
    st.caption(f"The {side} authorities and the management of {entity.name}.")
    st.selectbox(
        "Decision path", list(catalogue.paths), key=f"grp_path_{role}",
        format_func=lambda k: catalogue.paths[k].name, on_change=_load_country_path, args=(role,),
        help="A ready-made set of decisions for this country. Change single decisions below.",
        **initial(f"grp_path_{role}", index=list(catalogue.paths).index(NO_PATH)),
    )
    choices = []
    with st.expander(f"Decisions, one by one ({entity.country})"):
        st.caption("Taken after the close of a day, in effect the next day; if its condition is "
                   "not met yet, as soon as it is.")
        for key, decision in catalogue.decisions.items():
            actor = decision.actor.replace("_", " ").capitalize()
            option = st.selectbox(
                f"{actor}: {decision.name}", list(decision.options),
                format_func=lambda k, options=decision.options: options[k].name,
                key=f"grp_dec_{role}_{key}_option",
                **initial(f"grp_dec_{role}_{key}_option",
                          index=list(decision.options).index(decision.default_option)),
            )
            if option != decision.default_option:
                day_key = f"grp_dec_{role}_{key}_day"
                day = st.number_input(
                    f"{decision.name}: decide from the close of day", min_value=0, max_value=horizon,
                    step=1, key=day_key, **initial(day_key, value=1))
                choices.append(Choice(key, option, int(day)))
    return tuple(choices)


def sidebar(config: Config) -> GroupDesign:
    """The group's controls, in the sidebar. The design is kept for the Prepare page."""
    groups, college, show = config.groups, config.college, config.college.showcase
    crises = [key for key, item in config.scenarios.items() if item.taxonomy is not None]
    with st.sidebar:
        st.header("Group")
        group_key = st.selectbox(
            "Group", list(groups), key="grp_group",
            format_func=lambda k: f"{groups[k].name}: {config.banks[groups[k].parent.bank].name} "
                                  f"with {config.banks[groups[k].subsidiary.bank].name}",
            help="Parent first, then subsidiary. Both are fictitious (config/groups.yaml).",
            **initial("grp_group", index=list(groups).index(show.group)),
        )
        group = groups[group_key]
        scenario_key = st.selectbox(
            "Trigger scenario", crises, key="grp_scenario",
            format_func=lambda k: config.scenarios[k].name,
            **initial("grp_scenario", index=crises.index(show.scenario)),
        )
        severity_of = config.scenarios[scenario_key].severity
        levels = SEVERITY_LEVELS if severity_of.low is not None else ("central",)
        values = {"low": severity_of.low, "central": severity_of.value, "high": severity_of.high}
        severity = st.radio(
            "Severity", levels, key="grp_severity", horizontal=True,
            help="The scenario at the low, central or high value of its own severity range "
                 f"(config/scenarios.yaml): {values['low']:g}, {values['central']:g}, {values['high']:g}. "
                 "The value multiplies every deposit run-off rate.",
            **initial("grp_severity", index=levels.index(show.severity)),
        )
        stressed = st.radio(
            "The scenario hits", GROUP_ENTITIES, key="grp_stressed", horizontal=True,
            format_func=lambda r: f"the {r}",
            help="The other entity runs the calm scenario and is reached through the group.",
            **initial("grp_stressed", index=GROUP_ENTITIES.index(show.stressed)),
        )
        st.header("Decisions of the group")
        chosen = []
        horizon = int(config.model.horizon_days.value)
        for key, definition in college.decisions.items():
            option = st.selectbox(
                definition.name, list(definition.options), key=f"grp_option_{key}",
                format_func=lambda o, d=definition: d.options[o].name,
                **initial(f"grp_option_{key}", index=list(definition.options).index(
                    show.option if key == show.decision else definition.default_option)),
            )
            if option != definition.default_option:
                day = st.number_input(
                    "Decided after the close of day", min_value=0, max_value=horizon, step=1,
                    key=f"grp_day_{key}",
                    **initial(f"grp_day_{key}", value=show.day if key == show.decision else 1),
                    help="Taken at the first close, from this day on, at which the decision is "
                         "open. In effect the next day.",
                )
                chosen.append(Choice(key, option, int(day)))
        own = {role: _country_panel(config, group, role, horizon) for role in GROUP_ENTITIES}
    design = GroupDesign(
        group_key, scenario_key, stressed,
        GroupChoices(parent=own[PARENT], subsidiary=own[SUBSIDIARY], group=tuple(chosen)), severity,
        # Compared with the same decisions in each country and no decision of the group.
        GroupChoices(parent=own[PARENT], subsidiary=own[SUBSIDIARY]))
    st.session_state[DESIGN_KEY] = design
    return design


def current(config: Config) -> GroupDesign:
    if is_system():
        return st.session_state.get(system_view.DESIGN_KEY) or system_view.showcase_design(config)
    show = config.college.showcase
    return st.session_state.get(DESIGN_KEY) or GroupDesign(
        show.group, show.scenario, show.stressed,
        GroupChoices(group=(Choice(show.decision, show.option, show.day),)), show.severity)


def _pack(config: Config, design: GroupDesign, mode: str = "dynamic"):
    paths = [("Selected path", design.choices)]
    if design.choices != design.reference:
        paths.insert(0, (design.reference_name, design.reference))
    group, college = system.pair(config, design.group_key)
    return build_group_pack(
        group, config.banks, config.model, design.scenarios(config),
        design.scenario_key, config.decisions, config.script, college, paths,
        design.stressed, mode=mode)


def _outcome(run) -> str:
    text = OUTCOME_LABELS[run.outcome]
    return text if run.failure_day is None else f"{text}, day {run.failure_day}"


def _weekends(config: Config, scenario) -> bool:
    return config.model.with_overrides(scenario.overrides).weekend_rules_on.value >= 1


def design_page(config: Config) -> None:
    """The Design page for a group, or for a system."""
    if is_system():
        system_view.design_page(config)
        return
    design = sidebar(config)
    group, college = config.groups[design.group_key], config.college
    scenario = design.scenarios(config)[design.scenario_key]
    unit = config.banks[group.parent.bank].unit
    ui.header("Design", f"{group.name} · {scenario.name}")
    st.title("Design")
    st.caption(
        f"{group.name}: {group.parent.name} in {group.parent.country} (home), {group.subsidiary.name} "
        f"in {group.subsidiary.country} (host). {scenario.name} on the {design.stressed}. "
        f"Amounts in {unit}. {COLLEGE_NOTE}"
    )
    pack = _pack(config, design)
    result = pack.branches[-1].result
    weekends = _weekends(config, scenario)
    lent = sum(item.amount for item in result.transfers)
    ui.cards([
        (group.parent.name, _outcome(result.parent),
         f"{result.parent.scorecard.deposits_lost_pct:.1f}% of deposits lost", result.parent.outcome),
        (group.subsidiary.name, _outcome(result.subsidiary),
         f"{result.subsidiary.scorecard.deposits_lost_pct:.1f}% of deposits lost",
         result.subsidiary.outcome),
        (f"Lent inside the group ({unit})", f"{lent:.2f}",
         f"{len(result.transfers)} transfer{'' if len(result.transfers) == 1 else 's'}"),
        ("Restriction of transfers",
         "None" if result.ring_fence_from is None else f"From day {result.ring_fence_from}",
         restriction_label(result)),
    ], columns=4)

    timeline, group_map, compared, information = st.tabs(TABS)
    with timeline:
        ui.chart(build_group_timeline(result, unit, scenario.start_weekday, weekends),
                 key="group_timeline")
        st.caption(
            "An entity that cannot fund the day's payments from cash, liquid securities and "
            "central bank funding gets cash from the other before it sells loans, within the "
            "lender's limit and above its floor. A stage reached by one entity is a panic shock "
            "for the other the next day. All of it is assumed (config/groups.yaml)."
        )
        with st.expander("Both entities day by day"):
            for role in GROUP_ENTITIES:
                st.markdown(f"**{group.entity(role).name}**")
                table = result.of(role).table[[
                    "day", "status", "deposits_total", "cash", "liquidity_buffer", "outflow_total",
                    "support_received", "support_given", "intragroup_claim", "intragroup_liability",
                    "unmet_outflow"]]
                stages = list(table["status"])
                table = table.rename(columns={
                    "day": "Day", "status": "Stage", "deposits_total": f"Deposits ({unit})",
                    "cash": f"Cash ({unit})", "liquidity_buffer": f"Liquidity buffer ({unit})",
                    "outflow_total": f"Outflow ({unit})", "support_received": f"Support received ({unit})",
                    "support_given": f"Support given ({unit})", "intragroup_claim": f"Lent, outstanding ({unit})",
                    "intragroup_liability": f"Borrowed, outstanding ({unit})",
                    "unmet_outflow": f"Unpaid ({unit})"})
                table["Stage"] = table["Stage"].map(ui.stage_text)
                ui.table(table, stage_column="Stage", stages=stages)
    with group_map:
        last = max(result.parent.end_day, result.subsidiary.end_day)
        moved = [item.day for item in result.transfers]
        day = st.slider("Day shown", 0, last, min(last, moved[0] if moved else (result.ring_fence_from or 1)),
                        key=f"group_map_day_{last}")
        teams = {role: [pack.scripts[role].actors[actor] for actor in PLAYERS] for role in GROUP_ENTITIES}
        ui.chart(build_group_map(result, day, unit, teams,
                                 scenario.weekday(day) if day >= 1 else ""), key="group_map")
        st.caption(
            "A diagram, not geography: the countries are fictitious. The arrow's width follows "
            "the amount lent that day; a restriction by the host authority is the bar across "
            "the line from the subsidiary."
        )
    with compared:
        st.subheader("With and without the decisions of the group")
        if len(pack.branches) == 1:
            st.info("No decision of the group is set in the sidebar: there is one path. Pick a "
                    "restriction of transfers or a request for support to compare.")
        else:
            ui.chart(build_group_comparison(
                pack.branches[0].result, result, NO_DECISION, "Selected path", unit,
                scenario.start_weekday, weekends), key="group_comparison")
        st.subheader("Where each option leads")
        st.caption(
            "Each option taken at the first close at which the decision is open, with no other "
            "decision of the group."
        )
        points = group_decision_points_frame(pack)
        points = points[points["branch"] == pack.branches[-1].label].drop(columns="branch")
        st.dataframe(points, hide_index=True, width="stretch")
    with information:
        st.subheader("Who knows what, when, in each country")
        st.caption(
            "A team sees its own entity's figures at its own delay. Of the other entity it sees "
            "only what a notification from the other country carries. Delays and scopes are "
            "assumptions (config/decisions.yaml, config/college.yaml)."
        )
        last = max(result.parent.end_day, result.subsidiary.end_day)
        day = st.slider("Day shown", 0, last, min(last, 1), key=f"group_info_day_{last}")
        crossing = []
        for role in GROUP_ENTITIES:
            branch = pack.packs[role].branches[-1]
            entity = group.entity(role)
            st.markdown(f"**{entity.country}: {entity.name}**")
            ui.chart(build_information_map(
                information_map(branch, config.decisions, pack.scripts[role], day),
                scenario.weekday(day) if day >= 1 else ""), key=f"group_information_{role}")
            for actor in PLAYERS:
                for inject in branch.injects[actor]:
                    if inject.day == day and inject.kind in ("cross_border", "decision_taken") \
                            and inject.sender not in pack.scripts[role].actors.values():
                        crossing.append({
                            "To": pack.scripts[role].actors[actor], "From": inject.sender,
                            "Time": inject.time, "What": inject.title,
                            "Figures passed on": "; ".join(f.text for f in inject.headline)})
        st.markdown(f"**Across the border on day {day}**")
        if crossing:
            st.dataframe(pd.DataFrame(crossing), hide_index=True, width="stretch")
        else:
            st.caption("Nothing crosses the border on this day.")


def prepare_page(config: Config) -> None:
    """The Prepare page for a group: the pack of both countries and the inject timelines."""
    design = current(config)
    group, college = system.pair(config, design.group_key)
    whole = group.kind == "system"
    scenario = design.scenarios(config)[design.scenario_key]
    ui.header("Prepare", f"{group.name} · {scenario.name}")
    st.title("Prepare")
    st.caption(
        f"Exercise materials for the system set on the Design page: {group.name}, {scenario.name} "
        f"on {group.entity(design.stressed).name}. Change the banks, the scenario or the decisions there."
        if whole else
        f"Exercise materials for the group set on the Design page: {group.name}, {scenario.name} "
        f"on the {design.stressed}. Change the group, the scenario or the decisions there."
    )
    st.subheader("Exercise pack")
    mode = st.radio("Type of exercise", MODES, horizontal=True, key="grp_mode",
                    format_func=lambda m: MODE_LABELS[m])
    st.caption(MODE_TEXT[mode])
    pack = _pack(config, design, mode)
    st.caption(
        "One folder per bank, each with that bank's dossier, rules of play, inject sheets, main "
        "events list, role cards and control team pack; and a folder for the system with the "
        "system control pack and the data. The same four teams play on both banks. "
        + (f"Two branches: \"{pack.branches[0].name}\", and the path set on the Design page."
           if len(pack.branches) == 2 else "One branch: the path set on the Design page.")
        if whole else
        "One folder per country, each with that country's dossier, rules of play, inject sheets, "
        "main events list, role cards and control team pack; and a folder for the college with "
        "the group control pack and the data. A team's inject sheets show its own entity's "
        "figures and, of the other entity, only what a notification carries. "
        + ("Two branches: without any decision of the group, and the path set on the Design page."
           if len(pack.branches) == 2 else "One branch: the path set on the Design page.")
    )
    signature = (design, mode)
    if st.session_state.get("group_zip_for") != signature:
        st.session_state.pop("group_zip", None)
    if "group_zip" not in st.session_state:
        if st.button("Build the exercise pack", key="group_build", type="primary"):
            st.session_state["group_zip"] = group_to_zip(pack, config.decisions, college)
            st.session_state["group_zip_for"] = signature
    if "group_zip" in st.session_state:
        st.download_button(
            "Download the exercise pack (.zip)", data=st.session_state["group_zip"],
            file_name=f"{'system' if whole else 'group'}_pack_{design.group_key}_{design.scenario_key}.zip",
            mime="application/zip", key="group_download")
    st.subheader("Inject timeline")
    st.caption(
        "One chart per bank: a lane per team, and one for what the control team delivers in "
        "the name of a simulated stakeholder. The teams are the same on both charts."
        if whole else
        "One chart per country: a lane per team, and one for what the control team delivers in "
        "the name of a simulated stakeholder. A notification from the other country is in the "
        "lane of the team that receives it."
    )
    index = len(pack.branches) - 1
    if len(pack.branches) > 1:
        index = st.radio(
            "Branch shown", range(len(pack.branches)), horizontal=True, key="group_timeline_branch",
            index=index, format_func=lambda i: f"Branch {pack.branches[i].label}: {pack.branches[i].name}")
    for role in GROUP_ENTITIES:
        entity = group.entity(role)
        st.markdown(f"**{entity.name}**" if whole else f"**{entity.country}: {entity.name}**")
        ui.chart(build_branch_timeline(
            pack.packs[role].branches[index], pack.scripts[role], PLAYERS, scenario.start_weekday,
            _weekends(config, scenario)), key=f"grp_inject_timeline_{role}")
