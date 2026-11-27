"""Control console and Close page of a live exercise on a cross-border group (SPEC_V2 and
SPEC_V3 phase F, part d). It only calls engine.group_live; after every entry the exercise is
saved (app/store.py), which the teams' pages read.
"""

from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
import streamlit as st

from app import ai, ai_form, store, theme, ui
from app.charts import build_branch_timeline, build_group_map, build_group_timeline
from engine import group_live, live
from engine.decisions import OUTCOME_LABELS
from engine import legal as legal_refs
from engine import system
from engine.group import PARENT, SEVERITY_LEVELS, GroupChoices
from engine.group_export import COLLEGE_NOTE, group_report_to_zip
from engine.group_script import resolve_team
from engine.schema import ACTORS, GROUP_ENTITIES, PLAYERS, Config
from engine.script import MODE_LABELS, MODE_TEXT, MODES

KEY = "group_live"  # the session holds the state here, with its config, id and codes


def kind_words(state) -> str:
    """What an open exercise is on, for the header."""
    system_wide = group_live.kind_of(state.setup, _config()) == "system"
    return "two banks at once" if system_wide else "cross-border group"


def is_open() -> bool:
    return KEY in st.session_state


def _config() -> Config:
    return st.session_state["group_live_config"]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _save() -> None:
    state = st.session_state
    store.save_group(state["group_live_id"], state[KEY], _config(), state["group_live_codes"])


def act(kind: str, **data) -> None:
    """Add one entry to the log; an entry the engine refuses is shown, not applied."""
    try:
        st.session_state[KEY] = group_live.apply(st.session_state[KEY], kind, _now(), _config(), **data)
        st.session_state["group_live_error"] = ""
        _save()
    except live.LiveError as error:
        st.session_state["group_live_error"] = str(error)


def _start(config: Config, prefix: str = "glive") -> None:
    state = st.session_state
    setup = group_live.GroupLiveSetup(
        state[f"{prefix}_name"].strip() or "Exercise", state[f"{prefix}_group"],
        state[f"{prefix}_scenario"], state[f"{prefix}_stressed"], GroupChoices(), state[f"{prefix}_mode"],
        state[f"{prefix}_severity"])
    state["group_live_config"] = config
    state[KEY] = group_live.start(setup, _now(), config)
    state["group_live_id"] = store.new_id()
    state["group_live_codes"] = store.new_group_codes(group_live.kind_of(setup, config))
    state["group_live_error"] = ""
    _save()


def close_exercise() -> None:
    for key in (KEY, "group_live_config", "group_live_id", "group_live_codes"):
        st.session_state.pop(key, None)


def reload(exercise_id: str, code: str) -> bool:
    """Reload a saved group exercise into the session. False if the code is wrong."""
    loaded = store.load_group(exercise_id, code)
    if loaded is None:
        return False
    state = st.session_state
    state[KEY], state["group_live_config"], state["group_live_codes"] = loaded
    state["group_live_id"], state["group_live_error"] = exercise_id, ""
    return True


def start_section(config: Config) -> None:
    """On the console before any exercise is open: start one on a group."""
    st.subheader("Start an exercise on a cross-border group")
    groups = config.groups
    crises = [key for key, item in config.scenarios.items() if item.taxonomy is not None]
    show = config.college.showcase
    left, right = st.columns(2)
    left.text_input("Name of the exercise", value="Group exercise", key="glive_name")
    left.selectbox("Group", list(groups), key="glive_group", index=list(groups).index(show.group),
                   format_func=lambda k: f"{groups[k].name}: {config.banks[groups[k].parent.bank].name} "
                                         f"with {config.banks[groups[k].subsidiary.bank].name}")
    left.selectbox("Trigger scenario", crises, key="glive_scenario", index=crises.index(show.scenario),
                   format_func=lambda k: config.scenarios[k].name)
    left.radio("Severity", SEVERITY_LEVELS, key="glive_severity", horizontal=True,
               index=SEVERITY_LEVELS.index(show.severity),
               help="The scenario at the low, central or high value of its own severity range.")
    right.radio("The scenario hits", GROUP_ENTITIES, key="glive_stressed", horizontal=True,
                index=GROUP_ENTITIES.index(show.stressed), format_func=lambda r: f"the {r}")
    right.radio("Type of exercise", MODES, key="glive_mode", horizontal=True,
                format_func=lambda m: MODE_LABELS[m])
    right.caption(
        "Eight teams, four in each country. The pre-planned path has no decision: the path "
        "changes with what the teams decide. " + COLLEGE_NOTE)
    st.button("Start the group exercise", on_click=_start, args=(config,), key="glive_start")


def system_start_section(config: Config) -> None:
    """On the console before any exercise is open: start one on a system."""
    st.subheader("Start an exercise on a system: two banks at once")
    systems, show = config.systems, config.systems.showcase
    crises = [key for key, item in config.scenarios.items() if item.taxonomy is not None]
    left, right = st.columns(2)
    left.text_input("Name of the exercise", value="System exercise", key="slive_name")
    key = left.selectbox(
        "Two banks", list(systems.pairs), key="slive_group", index=list(systems.pairs).index(show.pair),
        format_func=lambda k: f"{systems.pairs[k].name}: {systems.pairs[k].first.name} and "
                              f"{systems.pairs[k].second.name}")
    left.selectbox("Trigger scenario", crises, key="slive_scenario", index=crises.index(show.scenario),
                   format_func=lambda k: config.scenarios[k].name)
    left.radio("Severity", SEVERITY_LEVELS, key="slive_severity", horizontal=True,
               index=SEVERITY_LEVELS.index(show.severity),
               help="The scenario at the low, central or high value of its own severity range.")
    names = {PARENT: systems.pairs[key].first.name, "subsidiary": systems.pairs[key].second.name}
    right.radio("The scenario hits", GROUP_ENTITIES, key="slive_stressed", horizontal=True,
                index=GROUP_ENTITIES.index(system.SLOTS[show.stressed]), format_func=lambda r: names[r])
    right.radio("Type of exercise", MODES, key="slive_mode", horizontal=True,
                format_func=lambda m: MODE_LABELS[m])
    right.caption(
        "Four teams, each handling both banks. The other bank is under a stress of its own "
        f"({config.scenarios[systems.pairs[key].other_scenario].name.lower()}). The pre-planned "
        "path has no decision: the path changes with what the teams decide. " + system.NOTE)
    st.button("Start the system exercise", on_click=_start, args=(config, "slive"), key="slive_start")


def _when(day: int, time: str, weekday: str = "") -> str:
    return f"Day {day}" + (f", {weekday}" if weekday else "") + f", {time}"


def _stage(run, day: int) -> str:
    row = run.rows[min(day, len(run.rows) - 1)]
    return str(row["status"])


def _record(side: str, decision: str) -> None:
    state = st.session_state
    act("decision", team=side, decision=decision, option=state[f"glive_option_{side}_{decision}"],
        consulted=state.get(f"glive_consulted_{side}_{decision}", []),
        by_control=state.get(f"glive_by_control_{side}_{decision}", False))


JOINT_COLUMNS = ["Simulated time", "Subject", "Outcome", "Took part", "Expected and absent", "Real time", "Real minutes taken", "Note"]


def _record_college() -> None:
    state = st.session_state
    act("college", subject=state["glive_college_subject"], took_part=list(state["glive_college_teams"]),
        agreed=state["glive_college_agreed"] == "Agreed", note=state["glive_college_note"],
        minutes=float(state["glive_college_minutes"]) or None)


def _send_adhoc() -> None:
    state = st.session_state
    side = state["glive_adhoc_side"]
    if state["glive_adhoc_source"] == "Free text":
        act("adhoc", team=side, recipients=state["glive_adhoc_to"], title=state["glive_adhoc_title"],
            text=state["glive_adhoc_text"], sender=state["glive_adhoc_sender"])
    else:
        act("adhoc", team=side, recipients=state["glive_adhoc_to"], library=state["glive_adhoc_item"])


def _adhoc_section(state, config: Config, group, contexts: dict, names: dict, whole: bool) -> None:
    """An inject outside the plan, to teams of one country (of one bank's file in a system),
    and, when config/ai.yaml switches it on, a reply drafted by AI on the same terms."""
    where = "Bank" if whole else "Country"
    label = lambda role: group.entity(role).name if whole else (  # noqa: E731
        f"{group.entity(role).country}: {group.entity(role).name}")
    with st.expander("Send an ad hoc inject"):
        side = st.radio(where, GROUP_ENTITIES, key="glive_adhoc_side", horizontal=True, format_func=label)
        script = contexts[side].script
        st.radio("Source", ["Free text", "From the library"], key="glive_adhoc_source", horizontal=True)
        st.multiselect("To", list(PLAYERS), key="glive_adhoc_to", format_func=lambda k: names[side][k])
        if st.session_state["glive_adhoc_source"] == "Free text":
            st.text_input("Title", key="glive_adhoc_title")
            st.text_input("From", key="glive_adhoc_sender", placeholder="Exercise control")
            st.text_area("Text", key="glive_adhoc_text")
            st.caption("Figures you type here are not checked against the teams' information sets.")
        else:
            # Library injects that carry no figure and change no figure: the others are
            # computed for a bank exercised alone.
            library = {f"ambient:{key}": item.title for key, item in script.ambient.items()}
            library |= {f"challenge:{key}": item.title for key, item in script.challenges.items()
                        if not item.effect and not item.figures}
            st.selectbox("Inject", list(library), key="glive_adhoc_item", format_func=lambda k: library[k])
        st.button("Send", on_click=_send_adhoc, key="glive_adhoc_send",
                  disabled=state.paused or state.ended)
    if ai.enabled(config):
        with st.container(key="cse_glive_ai"):
            side = st.radio(f"{where} of the request drafted by AI", GROUP_ENTITIES, key="glive_ai_side",
                            horizontal=True, format_func=label)
            ai_form.section(state.of(side), contexts[side], config,
                            lambda kind, **data: act(kind, team=side, **data))


def _college_section(state, config: Config, names: dict) -> None:
    """Record a joint decision of the college, and show those recorded."""
    group, college = system.pair(config, state.setup.group_key)
    whole = group.kind == "system"
    joint = college.joint_decision
    teams = list(group_live.teams(group.kind))
    expected = [group_live.team_key("both" if whole else resolve_team(reference, PARENT)[0],
                                    resolve_team(reference, PARENT)[1]) for reference in joint.members]
    st.markdown("### The authorities together" if whole else "### The college")
    with st.container(border=True, key="cse_college"):
        st.caption(
            f"{joint.name}: what the teams decided together"
            + (" on the two banks" if whole else " across the two countries")
            + ". A record for the closing report; it changes nothing in the model.")
        columns = st.columns([3, 5], vertical_alignment="bottom")
        columns[0].selectbox("Subject", joint.subjects, key="glive_college_subject")
        columns[1].multiselect(
            "Took part", teams, default=expected, key="glive_college_teams",
            format_func=lambda team: names[PARENT if whole else team.split(".")[0]][team.split(".")[1]],
            help="At least two teams." if whole else "At least one team from each country.")
        columns = st.columns([2, 2, 4, 1.4], vertical_alignment="bottom")
        columns[0].radio("Outcome", ["Agreed", "Not agreed"], key="glive_college_agreed", horizontal=True)
        columns[1].number_input("Real minutes it took (0 = not noted)", min_value=0, step=5,
                                key="glive_college_minutes")
        columns[2].text_input("Note (what was decided, or why not)", key="glive_college_note")
        columns[3].button("Record", on_click=_record_college, key="glive_college_record",
                          disabled=state.paused or not state.started)
    if state.college:
        rows = group_live.college_rows(state, college, lambda role, actor: names[role][actor])
        st.dataframe(pd.DataFrame(rows, columns=JOINT_COLUMNS), hide_index=True, width="stretch")


def page() -> None:
    """The console of a group exercise, as a cockpit."""
    config = _config()
    state: group_live.GroupLiveState = st.session_state[KEY]
    setup, result = state.setup, state.result
    group, college = system.pair(config, setup.group_key)
    whole = group.kind == "system"
    scenario = group_live.scenario_of(setup, config)
    contexts = group_live.contexts_of(setup, config)
    names = {role: contexts[role].script.actors for role in GROUP_ENTITIES}
    unit = config.banks[group.parent.bank].unit
    weekday = scenario.weekday(state.day) if state.day >= 1 else ""
    status = "Ended" if state.ended else "Paused" if state.paused else "Running"

    hit = group.entity(setup.stressed).name if whole else f"the {setup.stressed}"
    st.subheader(f"{setup.name}: {group.name}, {scenario.name} on {hit}")
    st.caption(MODE_TEXT[setup.mode] + " " + (system.NOTE if whole else COLLEGE_NOTE))
    lent = sum(t.amount for t in result.transfers if t.day <= state.day)
    facts = [("Simulated time", _when(state.day, state.time, weekday), None)]
    for role in GROUP_ENTITIES:
        facts.append((group.entity(role).name, None, _stage(result.of(role), state.day)))
    facts += [("Status", status, None),
              ("Link between the banks", f"{group.confidence_link.value:g}", None) if whole
              else (f"Lent inside the group ({unit})", f"{lent:.2f}", None)]
    for number, (column, (label, value, stage)) in enumerate(
            zip(st.columns([2.4, 1.6, 1.6, 1, 1.4]), facts)):
        with column.container(key=f"cse_fact_{number}"):
            st.caption(label)
            if stage is not None:
                st.html(theme.stage_badge(stage, theme.stage_label(stage)))
            else:
                st.markdown(f"#### {value}")

    buttons = st.columns([1.1, 1.9, 1, 1.6, 1.6])
    buttons[0].button("Next step", type="primary", on_click=act, args=("step",), key="glive_step",
                      disabled=state.ended or state.paused)
    buttons[1].button("Jump to next milestone", on_click=act, args=("jump",), key="glive_jump",
                      disabled=state.ended or state.paused)
    if state.paused:
        buttons[2].button("Resume", on_click=act, args=("resume",), key="glive_resume")
    else:
        buttons[2].button("Pause", on_click=act, args=("pause",), key="glive_pause")
    buttons[3].button("Undo the last entry", on_click=act, args=("undo",), key="glive_undo",
                      disabled=len(state.entries) < 2 or state.paused)
    buttons[4].button("Close the exercise", on_click=close_exercise, key="glive_close",
                      help="Leaves this exercise. It stays saved and can be reloaded with the control team's code.")
    if st.session_state.get("group_live_error"):
        st.error(st.session_state["group_live_error"])
    if state.ended:
        st.success("The exercise has ended. " + "; ".join(
            f"{group.entity(role).name}: {OUTCOME_LABELS[result.of(role).outcome].lower()}"
            for role in GROUP_ENTITIES) + ".")

    st.markdown("### Decisions open to the teams")
    opened = group_live.open_decisions(state, config)
    if not opened:
        st.caption("No decision is open.")
    for position, (side, inject) in enumerate(opened):
        if position % 2 == 0:
            slots = st.columns(2)
        due = inject.decision_due
        if side == group_live.GROUP:
            definition = college.decisions[due.decision]
            entity = group_live.decision_entity(definition.entity, setup.stressed)
            consult_options = list(definition.consult)
            consult_name = lambda ref, e=entity: names[resolve_team(ref, e)[0]][resolve_team(ref, e)[1]]  # noqa: E731
        else:
            definition = config.decisions.decisions[due.decision]
            entity = side
            consult_options = [a for a in ACTORS if a != definition.actor]
            consult_name = lambda a, e=entity: names[e][a]  # noqa: E731
        played = "" if definition.actor in PLAYERS else " (played by the control team)"
        closes_left = due.deadline_day - state.day + 1
        last = closes_left <= 1
        kind = ("Decision of the group" if side == group_live.GROUP
                else group.entity(entity).name if whole else group.entity(entity).country)
        with slots[position % 2].container(
                border=True, key=f"cse_decision_{'last' if last else 'open'}_{side}_{due.decision}"):
            st.caption(f"{kind}: {names[entity][definition.actor]}{played}")
            st.markdown(f"**{due.name}**")
            st.write(due.question)
            if reference := legal_refs.inject_line(inject, legal_refs.of(contexts[entity].script)):
                st.caption(reference)
            st.markdown(
                ("**Deadline: the close of today.** " if last
                 else f"**{closes_left} closes of day left**, today's included. ")
                + f"Open since day {inject.day}; decide by the close of day {due.deadline_day}.")
            columns = st.columns([3, 3], vertical_alignment="center")
            columns[0].selectbox(
                "Option", list(definition.options), key=f"glive_option_{side}_{due.decision}",
                format_func=lambda k, d=definition: d.options[k].name, label_visibility="collapsed")
            columns[1].multiselect(
                "Consulted first", consult_options, key=f"glive_consulted_{side}_{due.decision}",
                format_func=consult_name, placeholder="Consulted first", label_visibility="collapsed",
                help=f"The script expects: {', '.join(due.consult) or 'nobody'}.")
            columns = st.columns([3, 1.4], vertical_alignment="center")
            columns[0].checkbox("By the control team", key=f"glive_by_control_{side}_{due.decision}")
            columns[1].button("Record", on_click=_record, args=(side, due.decision),
                              key=f"glive_record_{side}_{due.decision}",
                              disabled=state.paused or state.ended)

    if setup.mode == "dynamic":
        st.markdown("### To be passed on by the teams")
        pending = group_live.pending_fallbacks(state)
        if not pending:
            st.caption("Nothing is waiting.")
        for side, inject in pending:
            key = live.inject_key(inject)
            columns = st.columns([4, 2, 2.4], vertical_alignment="center")
            columns[0].markdown(
                f"**{inject.sender} -> {names[side][inject.recipient]}**: {inject.title} "
                + (f"at {group.entity(side).name} " if whole else "") +
                f"({_when(inject.day, inject.time)})")
            columns[1].button("Passed on by the team", on_click=act, args=("relay_done",),
                              kwargs={"team": side, "key": key}, key=f"glive_done_{side}_{key}",
                              disabled=state.paused)
            columns[2].button("Release fallback inject", on_click=act, args=("fallback",),
                              kwargs={"team": side, "key": key}, key=f"glive_fallback_{side}_{key}",
                              disabled=state.paused)

    _college_section(state, config, names)
    _adhoc_section(state, config, group, contexts, names, whole)

    st.markdown("### The system today" if whole else "### The group today")
    last_day = max(result.parent.end_day, result.subsidiary.end_day)
    teams = {role: [names[role][actor] for actor in PLAYERS] for role in GROUP_ENTITIES}
    ui.chart(build_group_map(result, min(state.day, last_day), unit, teams, weekday), key="glive_map")
    st.caption("The path as it stands now. A diagram, not geography: "
               + ("the country is fictitious." if whole else "the countries are fictitious."))

    weekends = config.model.with_overrides(scenario.overrides).weekend_rules_on.value >= 1
    with st.expander("Inject timelines, one per " + ("bank" if whole else "country")):
        for role in GROUP_ENTITIES:
            country = state.of(role)
            st.markdown(f"**{group.entity(role).name}**" if whole
                        else f"**{group.entity(role).country}: {group.entity(role).name}**")
            ui.chart(build_branch_timeline(
                country.branch, contexts[role].script, PLAYERS, scenario.start_weekday, weekends,
                now=state.clock, released={live.inject_key(r.inject) for r in country.released},
                key_of=live.inject_key,
                taken={d.decision: d.day for d in country.decisions if d.applied}),
                key=f"glive_timeline_{role}")
    with st.expander("Both " + ("banks" if whole else "entities") + " on the path as it stands (full information)"):
        ui.chart(build_group_timeline(result, unit, scenario.start_weekday, weekends), key="glive_group_timeline")
    with st.expander("Teams' pages and saving"):
        codes, exercise_id = st.session_state["group_live_codes"], st.session_state["group_live_id"]
        st.caption(
            "Give each team its own link. A page shows only that team's injects so far and "
            "updates by itself. The codes keep a team from opening another team's page by "
            "mistake; they are not security.")
        for team in group_live.teams(group.kind):
            role, actor = team.split(".")
            link = f"/?exercise={exercise_id}&player={team}&code={codes[team]}"
            st.markdown(f"- {names[PARENT if whole else role][actor]}: [{link}]({link})")
        st.markdown(
            f"Saved after every entry as `{store.path_of(exercise_id).name}` in the folder "
            f"`exercises/`. Control team's code, to reload it: `{codes[store.CONTROL]}`")
        st.download_button(
            "Download the exercise (.json, to reload or replay)",
            data=json.dumps(group_live.to_saved(state, config)),
            file_name=f"SIMULATION_group_exercise_{exercise_id}.json", mime="application/json",
            key="glive_download")
    with st.expander(f"Released so far ({sum(len(state.of(r).released) for r in GROUP_ENTITIES)})"):
        st.dataframe(pd.DataFrame([
            {"When": _when(item.day, item.time), "To": names[role][item.inject.recipient],
             "Inject": item.inject.title, "From": item.inject.sender, "Released": item.how}
            for role in GROUP_ENTITIES for item in state.of(role).released
            if item.inject.recipient in PLAYERS
        ]), hide_index=True, width="stretch")
    with st.expander(f"Log ({len(state.entries)} entries)"):
        st.dataframe(pd.DataFrame([
            {"Real time": e.real_time, "Entry": e.kind.replace("_", " "),
             "Simulated time after it": _when(e.day, e.time),
             "Details": ", ".join(f"{k}: {v}" for k, v in e.data.items() if k != "key")}
            for e in state.entries
        ]), hide_index=True, width="stretch")


def _system_close(report, scripts: dict, group, college) -> None:
    """The Close page for an exercise on a system."""
    st.download_button(
        "Download the draft closing reports (.zip)",
        data=group_report_to_zip(report, scripts, group, college),
        file_name=f"SIMULATION_system_closing_report_{st.session_state.get('group_live_id', 'exercise')}.zip",
        mime="application/zip", key="glive_download_report")
    st.caption(
        "The zip holds the system's report and one report per bank, as Word documents, and two "
        "data files. Lessons learned and recommendations are left blank, for people to write. "
        + system.NOTE)
    st.markdown("### Overview")
    st.dataframe(pd.DataFrame(report.overview, columns=["Item", "Exercise"]), hide_index=True, width="stretch")
    st.markdown("### Joint decisions of the authorities")
    if report.college:
        st.dataframe(pd.DataFrame(report.college, columns=JOINT_COLUMNS), hide_index=True, width="stretch")
    else:
        st.caption("No joint decision was recorded.")
    for role in GROUP_ENTITIES:
        country = report.countries[role]
        st.markdown(f"### Decisions on {group.entity(role).name}")
        if country.decisions:
            st.dataframe(pd.DataFrame([
                {"Simulated day": line.day, "Decided by": line.actor, "Decision": line.decision_name,
                 "Option": line.option_name, "Figures as of": f"day {line.figures_day}"}
                for line in country.decisions]), hide_index=True, width="stretch")
        else:
            st.caption("No decision was recorded.")
    st.markdown("### What the model gives for both banks with one decision changed")
    st.caption(
        "One recorded decision on one bank changed, everything else as it happened. A change on "
        "one bank reaches the other through confidence only. A model result under assumed "
        "parameters, not a judgement on what was decided.")
    if report.counterfactuals:
        st.dataframe(pd.DataFrame([
            {"Decided by": c.team, "Decision": c.decision_name, "Change": c.change,
             group.parent.name: c.parent, group.subsidiary.name: c.subsidiary}
            for c in report.counterfactuals]), hide_index=True, width="stretch")
    else:
        st.caption("No decision was recorded on either bank.")


def close_page() -> None:
    """The Close page for a group exercise: the group's draft closing report."""
    config = _config()
    state = st.session_state[KEY]
    report = group_live.group_report(state, config)
    contexts = group_live.contexts_of(state.setup, config)
    scripts = {role: contexts[role].script for role in GROUP_ENTITIES}
    group, college = system.pair(config, state.setup.group_key)
    st.subheader(f"{report.title}: {report.group_name}")
    if group.kind == "system":
        _system_close(report, scripts, group, college)
        return
    st.download_button(
        "Download the draft closing reports (.zip)",
        data=group_report_to_zip(report, scripts, group, college),
        file_name=f"SIMULATION_group_closing_report_{st.session_state.get('group_live_id', 'exercise')}.zip",
        mime="application/zip", key="glive_download_report")
    st.caption(
        "The zip holds the group's report and one report per country, as Word documents, and "
        "three data files. Lessons learned and recommendations are left blank, for people to "
        "write. " + COLLEGE_NOTE)
    st.markdown("### Overview")
    st.dataframe(pd.DataFrame(report.overview, columns=["Item", "Exercise"]), hide_index=True, width="stretch")
    st.markdown("### Decisions of the group")
    if report.decisions:
        st.dataframe(pd.DataFrame(report.decisions, columns=[
            "Simulated day", "Decided by", "Decision", "Option", "Pre-planned option",
            "Real minutes from opening", "Notes"]), hide_index=True, width="stretch")
    else:
        st.caption("No decision of the group was recorded.")
    st.markdown("### Joint decisions of the college")
    if report.college:
        st.dataframe(pd.DataFrame(report.college, columns=JOINT_COLUMNS), hide_index=True, width="stretch")
    else:
        st.caption("No joint decision of the college was recorded.")
    for role in GROUP_ENTITIES:
        country = report.countries[role]
        st.markdown(f"### Decisions in {group.entity(role).country}")
        if country.decisions:
            st.dataframe(pd.DataFrame([
                {"Simulated day": line.day, "Decided by": line.actor, "Decision": line.decision_name,
                 "Option": line.option_name, "Figures as of": f"day {line.figures_day}"}
                for line in country.decisions]), hide_index=True, width="stretch")
        else:
            st.caption("No decision was recorded.")
    st.markdown("### Steps across the border")
    if report.crossing:
        st.dataframe(pd.DataFrame(report.crossing, columns=["Simulated day", "What", "By", "To", "Record"]),
                     hide_index=True, width="stretch")
    else:
        st.caption("No step across the border was expected up to this point.")
    st.markdown("### Intragroup transfers")
    if report.transfers:
        st.dataframe(pd.DataFrame(report.transfers, columns=[
            "Simulated day", "Lender", "Borrower", f"Amount ({report.unit})", "Note"]),
            hide_index=True, width="stretch")
    else:
        st.caption("None.")
    st.markdown("### What the model gives with one decision of the group changed")
    if report.counterfactuals:
        st.dataframe(pd.DataFrame([
            {"Decided by": c.team, "Decision": c.decision_name, "Change": c.change, "Parent": c.parent,
             "Subsidiary": c.subsidiary, f"Lent inside the group ({report.unit})": round(c.lent, 2)}
            for c in report.counterfactuals]), hide_index=True, width="stretch")
    else:
        st.caption("No decision of the group was open on this path.")
