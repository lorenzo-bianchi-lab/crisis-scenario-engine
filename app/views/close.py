"""Close page: the draft closing report of a live exercise (SPEC_V3 phase I).

It reads the exercise open on the Deliver page, or one reloaded here with its own code.
"""

import pandas as pd
import streamlit as st

from app import group_console, store, ui
from app.access import require_control_team
from app.design import get_config
from engine import live
from engine.decisions import scorecard_rows
from engine.export import (
    report_counterfactuals_frame,
    report_decisions_frame,
    report_to_zip,
)
from engine.report import live_report

config = get_config()
_live = st.session_state.get("live")
_group = st.session_state.get(group_console.KEY)
ui.header(
    "Close",
    f"{_group.setup.name}: {group_console.kind_words(_group)}" if _group else
    f"{_live.setup.name}: {_live.setup.bank.name}, {_live.setup.scenario.name}" if _live
    else "No exercise open",
)
require_control_team(config.script, "Close: closing report")
context = st.session_state.get("live_context") or live.Context(
    config.model, config.decisions, config.script)


def reload_exercise() -> None:
    state = st.session_state
    chosen, code = state.get("close_reload_id") or "", state.get("close_reload_code", "").strip()
    if store.is_group(chosen):
        if not group_console.reload(chosen, code):
            state["close_error"] = "The exercise could not be reloaded: check its code."
        return
    loaded = store.load(chosen, code)
    if loaded is None:
        state["close_error"] = "The exercise could not be reloaded: check its code."
        return
    state["live"], state["live_context"], state["live_codes"] = loaded
    state["live_id"], state["close_error"] = state["close_reload_id"], ""


st.title("Close")
st.caption(
    "SIMULATION - exercise material, not a real event. Draft closing report: what was "
    "decided, when and on which figures, which coordination steps were recorded, and what "
    "the model gives with one decision changed. It does not assess the players."
)

if group_console.is_open():
    group_console.close_page()
    st.stop()

if "live" not in st.session_state:
    st.info("No exercise is open. Start or reload one on the Deliver page, or reload one here.")
    saved = dict(store.saved_exercises())
    if saved:
        left, right = st.columns(2)
        left.selectbox("Saved exercise", list(saved), key="close_reload_id",
                       format_func=lambda k: saved[k])
        right.text_input("Code of that exercise (shown on its console)", key="close_reload_code",
                         type="password")
        st.button("Reload", on_click=reload_exercise, key="close_reload")
    else:
        st.caption("No exercise has been saved yet.")
    if st.session_state.get("close_error"):
        st.error(st.session_state["close_error"])
    st.stop()

state = st.session_state["live"]
report = live_report(state, context)
exercise_id = st.session_state.get("live_id", "exercise")
st.subheader(f"{report.title}: {report.bank_name}")
with st.container(key="ctl_report"):
    st.download_button(
        "Download the draft closing report (.zip)",
        data=report_to_zip(report, context.script),
        file_name=f"SIMULATION_closing_report_{exercise_id}.zip", mime="application/zip",
        key="close_download_report",
    )
st.caption(
    "The zip holds the report as a Word document and two data files. Observers' notes, "
    "feedback, lessons learned and recommendations are left blank in it, for people to write."
)

st.markdown("### Overview")
left, right = st.columns(2)
left.dataframe(pd.DataFrame(report.overview, columns=["Item", "Exercise"]), hide_index=True,
               width="stretch")
right.dataframe(pd.DataFrame(scorecard_rows(report.actual, report.unit),
                             columns=["Scorecard", "As it happened"]), hide_index=True, width="stretch")

st.markdown("### Timeline of decisions")
if report.decisions:
    frame = report_decisions_frame(report)
    frame.columns = [column.replace("_", " ").capitalize() for column in frame.columns]
    st.dataframe(frame, hide_index=True, width="stretch")
else:
    st.caption("No decision was recorded.")

st.markdown("### Coordination steps")
if report.steps:
    st.caption(context.script.report.coordination_note)
    st.dataframe(
        pd.DataFrame([
            {"About": step.about, "Step expected by the script": step.step, "By": step.by,
             "With or to": step.to,
             "Record": ("Recorded as done" if step.recorded else "Not recorded as done")
             + f": {step.how}"}
            for step in report.steps
        ]),
        hide_index=True, width="stretch",
    )
else:
    st.caption("No coordination step was expected up to this point.")

st.markdown("### What the model gives with one decision changed")
st.caption(context.script.report.counterfactual_note)
cases = report_counterfactuals_frame(report)[["decided_by", "decision", "change", "difference"]]
cases.columns = ["Decided by", "Decision", "Change", "Difference from what happened"]
st.dataframe(cases, hide_index=True, width="stretch")

with st.expander("Conduct of the exercise"):
    st.dataframe(pd.DataFrame(report.conduct, columns=["Item", "Value"]), hide_index=True, width="stretch")
    if report.ai_replies:
        st.caption("Replies drafted by AI, each read and sent by the control team:")
        st.dataframe(pd.DataFrame(report.ai_replies, columns=["Simulated time", "Asked by", "Reply sent as", "Request", "Drafted by", "Before sending"]), hide_index=True, width="stretch")
