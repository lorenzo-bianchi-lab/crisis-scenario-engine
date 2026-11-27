"""Control console (SPEC_V3 phase H): the control team runs a live exercise from here.

The page only calls engine.live. After every entry the exercise is saved to a file
(app/store.py), which the players' pages read and from which it can be reloaded and replayed.
"""

from datetime import datetime

import json

import pandas as pd
import streamlit as st

from app import ai, ai_form, group_console, store, theme, ui
from app.charts import build_branch_timeline, build_information_map
from app.access import require_control_team
from engine import legal as legal_refs
from engine import live
from app.design import legal_switch, with_legal
from engine.config import load_config
from engine.decisions import OUTCOME_LABELS, choices_of
from engine.export import TIMELINE_COLUMNS
from engine.schema import ACTORS, PLAYERS
from engine.script import MODE_LABELS, MODE_TEXT, MODES, information_map


@st.cache_resource
def get_config():
    return load_config()


if not st.session_state.get("live") and not st.session_state.get("group_live"):
    # Set before an exercise starts; an exercise keeps the setting it started with.
    legal_switch(get_config(), note="It applies to exercises started from now on.")
config = with_legal(get_config())
script, decisions = config.script, config.decisions
# A reloaded exercise runs on the configuration saved with it, not on today's files.
context = st.session_state.get("live_context") or live.Context(config.model, decisions, script)
script, decisions = context.script, context.decisions


def save() -> None:
    state = st.session_state
    store.save(state["live_id"], state["live"], context, state["live_codes"])


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def act(kind: str, **data) -> None:
    """Add one entry to the log; an entry the engine refuses is shown, not applied."""
    try:
        st.session_state["live"] = live.apply(st.session_state["live"], kind, now(), context, **data)
        st.session_state["live_error"] = ""
        save()
    except live.LiveError as error:
        st.session_state["live_error"] = str(error)


def start_exercise() -> None:
    state = st.session_state
    setup = live.make_setup(
        state["live_name"].strip() or "Exercise",
        config.banks[state["live_bank"]],
        config.model,
        config.scenarios[state["live_scenario"]],
        decisions,
        script,
        choices_of(decisions.paths[state["live_path"]]),
        mode=state["live_mode"],
        ambient=state["live_ambient"],
        hostile=state["live_assumptions"] == "Hostile",
    )
    state["live"] = live.start(setup, now(), context)
    state["live_error"] = ""
    state["live_id"], state["live_codes"] = store.new_id(), store.new_codes()
    save()


def reload_exercise() -> None:
    state = st.session_state
    chosen, code = state.get("live_reload_id") or "", state.get("live_reload_code", "").strip()
    if store.is_group(chosen):
        if not group_console.reload(chosen, code):
            state["live_error"] = "The exercise could not be reloaded: check the control team's code."
        return
    loaded = store.load(chosen, code)
    if loaded is None:
        state["live_error"] = "The exercise could not be reloaded: check the control team's code."
        return
    state["live"], state["live_context"], state["live_codes"] = loaded
    state["live_id"], state["live_error"] = state["live_reload_id"], ""


def close_exercise() -> None:
    for key in ("live", "live_context", "live_id", "live_codes"):
        st.session_state.pop(key, None)


def record_decision(decision: str) -> None:
    act("decision", decision=decision, option=st.session_state[f"live_option_{decision}"],
        by_control=st.session_state.get(f"live_by_control_{decision}", False),
        consulted=list(st.session_state.get(f"live_consulted_{decision}", [])))


def send_adhoc() -> None:
    state = st.session_state
    if state["live_adhoc_source"] == "Free text":
        act("adhoc", recipients=state["live_adhoc_to"], title=state["live_adhoc_title"],
            text=state["live_adhoc_text"], sender=state["live_adhoc_sender"])
    else:
        act("adhoc", recipients=state["live_adhoc_to"], library=state["live_adhoc_item"])


def when(day: int, time: str, weekday: str = "") -> str:
    return f"Day {day}" + (f", {weekday}" if weekday else "") + f", {time}"


def show_inject(inject, caption: str = "") -> None:
    with st.expander(f"{when(inject.day, inject.time, inject.weekday)} - {inject.title}"):
        if caption:
            st.caption(caption)
        st.markdown(f"*From: {inject.sender}*")
        for line in inject.text.split("\n"):
            st.write(line)
        if reference := legal_refs.inject_line(inject, legal_refs.of(script)):
            st.caption(reference)
        for heading, figures in (
            ("Figures behind this notification", inject.headline),
            ("Estimates", inject.resolution),
            ("Latest regular report", inject.figures),
        ):
            if figures:
                st.caption(heading)
                for figure in figures:
                    st.markdown(f"- {figure.text}")


_live = st.session_state.get("live")
_group = st.session_state.get(group_console.KEY)
ui.header(
    "Deliver",
    f"{_group.setup.name}: {group_console.kind_words(_group)}" if _group else
    f"{_live.setup.name}: {_live.setup.bank.name}, {_live.setup.scenario.name}" if _live
    else "No exercise open",
)
require_control_team(script, "Deliver: control console")
st.title("Control console")
st.caption(
    "SIMULATION - exercise material, not a real event. For the control team: run a live "
    "exercise, record the players' decisions and release the injects."
)

if group_console.is_open():
    group_console.page()
    st.stop()

if "live" not in st.session_state:
    st.subheader("Start an exercise")
    showcase = script.showcase
    left, right = st.columns(2)
    left.text_input("Name of the exercise", value="Exercise", key="live_name")
    left.selectbox("Bank archetype", list(config.banks), key="live_bank",
                   index=list(config.banks).index(showcase.bank),
                   format_func=lambda k: config.banks[k].name)
    crisis = [key for key, item in config.scenarios.items() if item.taxonomy is not None]
    left.selectbox("Trigger scenario", crisis, key="live_scenario",
                   index=crisis.index(showcase.scenario),
                   format_func=lambda k: config.scenarios[k].name)
    right.selectbox(
        "Pre-planned path", list(decisions.paths), key="live_path",
        index=list(decisions.paths).index(showcase.path),
        format_func=lambda k: decisions.paths[k].name,
        help="What the control team expects the players to decide. The exercise follows it "
        "until the players decide otherwise.",
    )
    right.radio("Type of exercise", MODES, key="live_mode", horizontal=True,
                format_func=lambda m: MODE_LABELS[m])
    right.radio("Assumptions", ["Friendly", "Hostile"], key="live_assumptions", horizontal=True)
    right.checkbox("Include ambient injects", value=True, key="live_ambient")
    st.button("Start the exercise", type="primary", on_click=start_exercise, key="live_start")
    st.caption(
        f"In the exercise the bank is called {script.setup.bank_alias}. Simulated time moves "
        "only when you step the clock."
    )
    group_console.start_section(config)
    group_console.system_start_section(config)
    st.subheader("Reload a saved exercise")
    saved = dict(store.saved_exercises())
    if not saved:
        st.caption("No exercise has been saved yet.")
    else:
        left, right = st.columns(2)
        left.selectbox("Saved exercise", list(saved), key="live_reload_id",
                       format_func=lambda k: saved[k])
        right.text_input("Control team's code", key="live_reload_code", type="password",
                         help="Shown on the console of the exercise, under \"Players' pages and saving\".")
        st.button("Reload", on_click=reload_exercise, key="live_reload")
        st.caption("A reloaded exercise continues where it stopped, on the configuration it was started with.")
    if st.session_state.get("live_error"):
        st.error(st.session_state["live_error"])
    st.stop()

state = st.session_state["live"]
setup, branch = state.setup, state.branch
result = branch.result
weekday = setup.scenario.weekday(state.day) if state.day >= 1 else ""
status = "Ended" if state.ended else "Paused" if state.paused else "Running"
row = result.table.set_index("day").loc[min(state.day, result.end_day)]
started = datetime.fromisoformat(state.entries[0].real_time)

st.subheader(f"{setup.name}: {setup.bank.name}, {setup.scenario.name}")
st.caption(MODE_TEXT[setup.mode])
# Plain text in cards, not st.metric: a metric cuts a long value short. The clock and the
# stage are the two large cards.
stage_now = "foltf" if (
    result.foltf_day is not None and state.day >= result.foltf_day and row["status"] != "failed"
) else theme.stage_of(row["status"])
facts = (
    ("Simulated time", when(state.day, state.time, weekday)),
    ("Stage of the bank", str(row["status"]).replace("_", " ").replace("failed", "payments default")),
    ("Status", status),
    ("Injects released to players",
     str(sum(1 for r in state.released if r.inject.recipient in PLAYERS))),
    ("Started (real time)", started.strftime("%H:%M:%S")),
)
for number, (column, (label, value)) in enumerate(zip(st.columns([2.6, 2.2, 1, 1.3, 1.2]), facts)):
    with column.container(key=f"cse_fact_{number}"):
        st.caption(label)
        if label == "Stage of the bank":
            st.html(theme.stage_badge(stage_now, value.capitalize()))
        else:
            st.markdown(f"#### {value}")

buttons = st.columns([1.1, 1.9, 1, 1.6, 1.6])
buttons[0].button("Next step", type="primary", on_click=act, args=("step",), key="live_step",
                  disabled=state.ended or state.paused,
                  help="Move the simulated clock to the next inject time or to the close of the day.")
buttons[1].button("Jump to next milestone", on_click=act, args=("jump",), key="live_jump",
                  disabled=state.ended or state.paused,
                  help="Next stage change or decision opening. Injects on the way are released together.")
if state.paused:
    buttons[2].button("Resume", on_click=act, args=("resume",), key="live_resume")
else:
    buttons[2].button("Pause", on_click=act, args=("pause",), key="live_pause")
buttons[3].button("Undo the last entry", on_click=act, args=("undo",), key="live_undo",
                  disabled=len(state.entries) < 2 or state.paused,
                  help="Takes back the last entry. Both stay in the log.")
buttons[4].button("Close the exercise", on_click=close_exercise, key="live_close",
                  help="Leaves this exercise. It stays saved and can be reloaded with the control team's code.")
if st.session_state.get("live_error"):
    st.error(st.session_state["live_error"])
if state.ended:
    st.success(f"The exercise has ended. Outcome: {OUTCOME_LABELS[result.outcome]}.")


st.markdown("### Decisions open to the players")
opened = live.open_decisions(state)
if not opened:
    st.caption("No decision is open.")
since = {r.inject.decision_due.decision: r.real_time for r in reversed(state.released)
         if r.inject.decision_due is not None}
real_now = now()
for position, inject in enumerate(opened):
    if position % 2 == 0:
        slots = st.columns(2)
    due = inject.decision_due
    definition = decisions.decisions[due.decision]
    played = "" if definition.actor in PLAYERS else " (played by the control team)"
    closes_left = due.deadline_day - state.day + 1
    last = closes_left <= 1
    opened_at = since[due.decision]
    minutes = max(0.0, (datetime.fromisoformat(real_now) - datetime.fromisoformat(opened_at)
                        ).total_seconds() - live.paused_seconds(state, opened_at, real_now)) / 60
    # On the last close before the deadline the card takes the warning colour.
    with slots[position % 2].container(
            border=True, key=f"cse_decision_{'last' if last else 'open'}_{due.decision}"):
        st.caption(f"{script.actors[definition.actor]}{played}")
        st.markdown(f"**{due.name}**")
        st.write(due.question)
        if reference := legal_refs.inject_line(inject, legal_refs.of(script)):
            st.caption(reference)
        st.markdown(
            ("**Deadline: the close of today.** " if last
             else f"**{closes_left} closes of day left**, today's included. ")
            + f"Open since day {inject.day}; decide by the close of day {due.deadline_day}. "
            f"Open for {minutes:.0f} real minute{'' if round(minutes) == 1 else 's'}, pauses excluded."
        )
        columns = st.columns([3, 3], vertical_alignment="center")
        columns[0].selectbox(
            "Option", list(definition.options), key=f"live_option_{due.decision}",
            format_func=lambda k, d=definition: d.options[k].name, label_visibility="collapsed",
        )
        columns[1].multiselect(
            "Consulted first", [a for a in ACTORS if a != definition.actor],
            key=f"live_consulted_{due.decision}", format_func=lambda k: script.actors[k],
            placeholder="Consulted first", label_visibility="collapsed",
            help="Whom the deciding team consulted before deciding, as you observed it. The "
            f"script expects: {', '.join(due.consult) or 'nobody'}.",
        )
        columns = st.columns([3, 1.4], vertical_alignment="center")
        columns[0].checkbox("By the control team", key=f"live_by_control_{due.decision}",
                            help="Tick when the players could not decide and the control team "
                            "took the decision for them.")
        columns[1].button("Record", on_click=record_decision, args=(due.decision,),
                          key=f"live_record_{due.decision}", disabled=state.paused or state.ended)
st.caption(
    "A decision recorded now is taken at the close of this simulated day and is in effect "
    "the next day. The option that means \"no decision yet\" is logged and leaves the decision open."
)

pending = live.pending_fallbacks(state)
if setup.mode == "dynamic":
    st.markdown("### To be passed on by the players")
    if not pending:
        st.caption("Nothing is waiting.")
    for inject in pending:
        key = live.inject_key(inject)
        columns = st.columns([4, 2, 2.4], vertical_alignment="center")
        columns[0].markdown(
            f"**{inject.sender} -> {script.actors[inject.recipient]}**: {inject.title} "
            f"({when(inject.day, inject.time)})"
        )
        columns[1].button("Passed on by player", on_click=act, args=("relay_done",),
                          kwargs={"key": key}, key=f"live_done_{key}", disabled=state.paused)
        columns[2].button("Release fallback inject", on_click=act, args=("fallback",),
                          kwargs={"key": key}, key=f"live_fallback_{key}", disabled=state.paused)

st.markdown("### Inject timeline")
st.caption(
    "One lane per player and one for the simulated stakeholders the control team plays. "
    "Filled: released; hollow: still to come on the path as it stands now. Bars are decision "
    "windows; a bar ends when its decision is recorded."
)
ui.chart(build_branch_timeline(
    branch, script, PLAYERS, setup.scenario.start_weekday,
    context.params.with_overrides(setup.scenario.overrides).weekend_rules_on.value >= 1,
    now=state.clock, released={live.inject_key(r.inject) for r in state.released},
    key_of=live.inject_key,
    taken={d.decision: d.day for d in state.decisions if d.applied or setup.mode == "linear"},
), key="live_timeline")

with st.expander("Information map: who knows what today"):
    ui.chart(build_information_map(
        information_map(branch, decisions, script, state.day), weekday), key="live_information_map")
    st.caption(
        "What each actor holds at the close of the current simulated day, on the path as it "
        "stands now. A notification later than the clock has not been released yet."
    )

with st.expander("State of the bank (full information)"):
    table = result.table[result.table["day"] <= state.day]
    shown = table[list(TIMELINE_COLUMNS)].rename(columns=TIMELINE_COLUMNS).copy()
    day_stages = [
        "foltf" if (result.foltf_day is not None and day >= result.foltf_day and word != "failed")
        else theme.stage_of(word)
        for day, word in zip(table["day"], table["status"])
    ]
    shown["Stage"] = shown["Stage"].map(ui.stage_text)
    shown = shown.rename(columns={
        name: f"{name} ({setup.bank.unit})"
        for name in ("Outflow", "Requested, not settled", "Liquidity buffer", "ELA outstanding")
    })
    shown["CET1 ratio (%)"] = 100 * shown["CET1 ratio (%)"]
    # No outflow, so no survival horizon: an empty cell, not "infinity".
    shown = shown.replace([float("inf")], float("nan"))
    ui.table(shown, stage_column="Stage", stages=day_stages)
    st.caption(
        f"Amounts in {setup.bank.unit}. An empty survival horizon means no outflow that day. Expected from here, if the players decide as planned: "
        f"the path ends on day {result.end_day}, {OUTCOME_LABELS[result.outcome].lower()}."
    )


with st.expander("Coming next"):
    coming = sorted(
        (i for actor in PLAYERS for i in branch.injects[actor] if (i.day, i.time) > state.clock),
        key=lambda i: (i.day, i.time),
    )[:8]
    if coming:
        st.dataframe(
            pd.DataFrame([
                {"When": when(i.day, i.time, i.weekday), "Inject": i.title,
                 "To": script.actors[i.recipient], "Delivered by": i.delivered_by,
                 "Purpose": i.purpose + (" (fallback)" if i.fallback else "")}
                for i in coming
            ]),
            hide_index=True, width="stretch",
        )
    else:
        st.caption("No inject is left on this path.")


with st.expander("Released so far"):
    recipient = st.selectbox("Recipient", list(ACTORS), key="live_recipient",
                             format_func=lambda k: script.actors[k])
    for item in live.released_to(state, recipient):
        held = "" if (item.inject.day, item.inject.time) == (item.day, item.time) else (
            f" Released at day {item.day}, {item.time}.")
        show_inject(item.inject, f"Released {item.real_time} ({item.how}).{held} {item.note}".strip())


with st.expander("Players' pages and saving"):
    codes, exercise_id = st.session_state["live_codes"], st.session_state["live_id"]
    st.caption(
        "Give each team its own link. A page shows only that team's injects so far and "
        "updates by itself every few seconds. On another computer, replace the start of "
        "the link by this computer's network address and port. The codes keep a team from "
        "opening another team's page by mistake; they are not security."
    )
    for player in PLAYERS:
        link = f"/?exercise={exercise_id}&player={player}&code={codes[player]}"
        st.markdown(f"- {script.actors[player]}: [{link}]({link})")
    st.markdown(
        f"Saved after every entry as `{store.path_of(exercise_id).name}` in the folder "
        f"`exercises/`. Control team's code, to reload it: `{codes[store.CONTROL]}`"
    )
    st.caption("The draft closing report of this exercise is on the Close page, at any time.")
    left, right = st.columns(2)
    left.download_button(
        "Download the log (.csv)",
        data=pd.DataFrame(live.log_rows(st.session_state["live"], context)).to_csv(index=False),
        file_name=f"SIMULATION_log_{exercise_id}.csv", mime="text/csv", key="live_download_log",
    )
    right.download_button(
        "Download the exercise (.json, to reload or replay)",
        data=json.dumps(live.to_saved(st.session_state["live"], context)),
        file_name=f"SIMULATION_exercise_{exercise_id}.json", mime="application/json",
        key="live_download_exercise",
    )

with st.expander("Send an ad hoc inject"):
    st.radio("Source", ["Free text", "From the library"], key="live_adhoc_source", horizontal=True)
    st.multiselect("To", list(PLAYERS), key="live_adhoc_to",
                   format_func=lambda k: script.actors[k])
    if st.session_state["live_adhoc_source"] == "Free text":
        st.text_input("Title", key="live_adhoc_title")
        st.text_input("From", key="live_adhoc_sender", placeholder="Exercise control")
        st.text_area("Text", key="live_adhoc_text")
        st.caption("Figures you type here are not checked against the players' information sets.")
    else:
        library = {f"ambient:{key}": item.title for key, item in script.ambient.items()}
        library |= {f"challenge:{key}": item.title for key, item in script.challenges.items()
                    if item.effect != "panic_shock"}
        st.selectbox("Inject", list(library), key="live_adhoc_item",
                     format_func=lambda k: library[k])
        st.caption("An inject that carries resolution figures can be sent once FoLtF is in effect.")
    st.button("Send", on_click=send_adhoc, key="live_adhoc_send",
              disabled=state.paused or state.ended)

if ai.enabled(config):
    ai_form.section(state, context, config, act)

with st.expander(f"Decisions recorded ({len(state.decisions)})"):
    if state.decisions:
        st.dataframe(
            pd.DataFrame([
                {"Simulated day": d.day,
                 "By": script.actors[d.actor],
                 "Decision": decisions.decisions[d.decision].name,
                 "Option": decisions.decisions[d.decision].options[d.option].name,
                 "Taken by the control team": "yes" if d.by_control else "",
                 "Different from the plan": "yes" if d.deviates else "",
                 "Real minutes taken": round(d.seconds_taken / 60, 1),
                 "Real time": d.real_time}
                for d in state.decisions
            ]),
            hide_index=True, width="stretch",
        )
with st.expander(f"Log ({len(state.entries)} entries)"):
    st.dataframe(pd.DataFrame(live.log_rows(state, context)), hide_index=True, width="stretch")

with st.expander("Replay"):
    st.caption(
        "Go back through the exercise entry by entry. This only looks at the log: it "
        "changes nothing."
    )
    if len(state.entries) < 2:
        st.caption("There is nothing to replay yet.")
    else:
        upto = st.slider("Entries replayed", 1, len(state.entries), len(state.entries),
                         key="live_replay_upto")
        past = live.replay(
            setup, [live.Entry(e.kind, e.real_time, e.data) for e in state.entries[:upto]], context)
        entry = past.entries[-1]
        st.markdown(
            f"After entry {upto} ({entry.kind.replace('_', ' ')}, {entry.real_time}): simulated "
            f"time day {past.day}, {past.time}; "
            f"{'paused' if past.paused else 'ended' if past.ended else 'running'}; expected "
            f"outcome then: {OUTCOME_LABELS[past.branch.result.outcome].lower()}."
        )
        st.dataframe(
            pd.DataFrame([
                {"Recipient": script.actors[actor],
                 "Injects received": len(live.released_to(past, actor)),
                 "Last received": (live.released_to(past, actor)[-1].inject.title
                                   if live.released_to(past, actor) else "")}
                for actor in ACTORS
            ]),
            hide_index=True, width="stretch",
        )
