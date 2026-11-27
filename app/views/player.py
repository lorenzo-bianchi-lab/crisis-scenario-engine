"""A player's page (SPEC_V3 phase H): read-only, only that player's injects so far.

Opened with a link from the control console: ?exercise=<id>&player=<player>&code=<code>.
The page reads the saved exercise file and shows the view stored there for this player.
"""

import streamlit as st

from app import store, ui

REFRESH_SECONDS = 3
query = st.query_params
exercise_id, player, code = query.get("exercise", ""), query.get("player", ""), query.get("code", "")


def figures_table(heading: str, as_of: str, rows: list, threshold: bool = False) -> None:
    """One table of figures. The day the figures refer to is in the heading and in a column
    of its own, so a figure is never read without its date. A row of three without
    `threshold` carries its own day."""
    st.markdown(f"**{heading}: as of {as_of}**" if as_of else f"**{heading}: as of the day shown**")
    lines = ["| Figure | Value | Threshold | As of |" if threshold else "| Figure | Value | As of |",
             "|:--|--:|:--|:--|" if threshold else "|:--|--:|:--|"]
    for label, value, *more in rows:
        if threshold:
            lines.append(f"| {label} | {value} | {more[0] if more else ''} | {as_of} |")
        else:
            lines.append(f"| {label} | {value} | {more[0] if more else as_of} |")
    st.markdown("\n".join(lines))


def inject_title(inject: dict) -> str:
    weekday = f", {inject['weekday']}" if inject["weekday"] else ""
    return f"Day {inject['day']}{weekday}, {inject['time']} - {inject['title']}"


def show_inject(inject: dict, number: int) -> None:
    st.caption(f"From: {inject['sender']}")
    for line in inject["text"]:
        st.write(line)
    if inject.get("reference"):
        st.caption(inject["reference"])
    if inject["headline"]:
        figures_table("Figures behind this notification, as passed on by the sender",
                      f"the close of day {inject['headline_day']}", inject["headline"], threshold=True)
    if inject["notified"]:
        figures_table("What you have been told in notifications so far", "", inject["notified"])
    if inject["estimates"]:
        day = inject.get("estimates_day")
        figures_table("Estimates. They rest on an assumed write-down and may be revised",
                      f"day {day}" if day is not None else "the day of this inject",
                      inject["estimates"])
    if inject["report"]:
        figures_table("Your latest regular report",
                      f"the close of day {inject['report_day']}", inject["report"])
    decision = inject["decision"]
    if decision:
        with st.container(border=True, key=f"cse_due_{number}"):
            st.caption("Decision due")
            st.markdown(f"**{decision['question']}**")
            for name, branch_code in decision["options"]:
                st.markdown(f"- `{branch_code}` {name}" if branch_code else f"- {name}")
            st.markdown(
                f"**Decide by** the close of day {decision['deadline_day']}.  \n"
                f"**Consult first:** {', '.join(decision['consult']) or 'nobody'}.  \n"
                f"**Inform afterwards:** {', '.join(decision['inform']) or 'nobody'}.  \n"
                "Give your decision to the control team."
            )


@st.fragment(run_every=REFRESH_SECONDS)
def page() -> None:
    view = store.player_view(exercise_id, player, code)
    ui.header(None, f"{view['exercise']}: {view['bank']}" if view else "Player page",
              navigation=False)
    if view is None:
        st.error(
            "This page cannot be opened. Check the link you were given by the control team: "
            "it holds the exercise, your team and your code."
        )
        return
    # One column of reading width, centred, in large type (app/theme.css).
    with st.container(key="cse_player"):
        st.title(view["player"])
        st.caption(
            f"SIMULATION - exercise material, not a real event. {view['exercise']}: {view['bank']}. "
            "This page shows what your team has received so far. It updates by itself."
        )
        weekday = f", {view['weekday']}" if view["weekday"] else ""
        left, right = st.columns([2, 1])
        with left.container(key="cse_fact_0"):
            st.caption("Simulated time")
            st.markdown(f"#### Day {view['day']}{weekday}, {view['time']}")
        with right.container(key="cse_fact_2"):
            st.caption("Exercise")
            st.markdown(f"#### {view['status'].capitalize()}")
        if view["status"] == "paused":
            st.warning("The exercise is paused. Wait for the control team.")
        if view["status"] == "ended":
            st.success("The exercise has ended. The debrief follows.")
        if not view["injects"]:
            st.info("Nothing has reached your team yet.")
            return
        latest, *earlier = reversed(view["injects"])  # the latest first
        with st.container(border=True, key="cse_player_latest"):
            st.caption("Latest")
            st.markdown(f"### {inject_title(latest)}")
            show_inject(latest, 0)
        if earlier:
            st.markdown("#### Earlier")
        for number, inject in enumerate(earlier, start=1):
            # A decision in a collapsed inject must not be missed: its deadline is in the label.
            due = inject["decision"]
            mark = f" (decide by the close of day {due['deadline_day']})" if due else ""
            with st.expander(inject_title(inject) + mark):
                show_inject(inject, number)


page()
