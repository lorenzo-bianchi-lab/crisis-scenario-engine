"""The demo tour, the played exercise and the reset (demo readiness).

A tour is a list of stops across the pages. Its bar sits under the header of every page
while the tour is on: where the audience is, one or two sentences on what to look at, and
Back / Next. A stop can set what the page shows (single bank, cross-border group, system)
and load the played exercise. Nothing here is a model parameter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import streamlit as st

from app import store

ON, STOP, GO = "tour_on", "tour_stop", "tour_go"
KIND_KEY = "exercise_kind"  # the "Exercise on" choice of the Design page (app/group_view.py)
SINGLE, GROUP, SYSTEM = "Single bank", "Cross-border group", "System-wide"
# The played exercise is saved under one id, so loading it again replaces the file.
PLAYED_ID = "de300001"
HOME, DESIGN, PREPARE, DELIVER, CLOSE, ASSUMPTIONS = (
    "views/home.py", "views/design.py", "views/prepare.py", "views/console.py", "views/close.py",
    "views/assumptions.py")


@dataclass(frozen=True)
class Stop:
    title: str
    text: str
    page: str
    kind: Optional[str] = None  # what the Design and Prepare pages show
    guided: Optional[bool] = None  # the six-step guided demo of the Design page, on or off
    played: bool = False  # the played exercise is loaded
    group_live: bool = False  # a live exercise on the group showcase is opened on the console


STOPS = (
    Stop("What the tool is",
         "A generator of the day-by-day figures of a bank crisis for a simulation exercise, "
         "with the documents and the console to run it. The pages follow the four phases of "
         "the EBA Handbook.", HOME),
    Stop("Design: a single bank",
         "The six steps below pick a bank and a trigger, run the crisis and change a decision. "
         "Follow them, then press Next here.", DESIGN, SINGLE, True),
    Stop("Prepare: the exercise pack",
         "The same design as documents: a dossier and rules of play for the players, inject "
         "sheets for each team with only the figures that team holds, and the control team's "
         "pack. Further down, the inject timeline.", PREPARE, SINGLE, False),
    Stop("Deliver: an exercise that was played",
         "The control team's console on an exercise played by script: five decisions entered "
         "live, two of them different from the plan, an ad hoc inject and a pause. The log is "
         "at the bottom; each team had its own page.", DELIVER, SINGLE, False, True),
    Stop("Close: the draft closing report",
         "What was decided against the plan, which coordination steps were recorded, and what "
         "the model gives with one decision changed. Lessons learned are left for people to write.",
         CLOSE, SINGLE, False, True),
    Stop("Design: a cross-border group",
         "A parent and a subsidiary in two countries, about ten days across a weekend. Open "
         "\"Decisions compared\": the host authority's ring-fencing on the Monday decides "
         "whether the parent is still paying on the second Friday.", DESIGN, GROUP, False),
    Stop("Deliver: the group, live",
         "The same group as a live exercise, stopped at the moment the host authority's "
         "decision on transfers is open. Eight teams, four in each country, each with its own "
         "page; the cards are the decisions open now. Record one and both countries' paths change.",
         DELIVER, GROUP, False, False, True),
    Stop("Design: system-wide stress",
         "Two unrelated banks of one country, the same four authorities. Open \"Paths "
         "compared\": resolving the first bank early changes the second bank's path.",
         DESIGN, SYSTEM, False),
    Stop("Assumptions and limits",
         "Every number with its value, unit and source; those without a verifiable source are "
         "flagged. The Limits tab says what the tool does not cover.", ASSUMPTIONS, None, False),
)


def load_played() -> None:
    """Play the showcase exercise by script (engine/live_run.py) and open it on the console.
    It is computed here, so it always matches the model, and saved like any exercise."""
    from app.design import get_config
    from engine.live_run import scripted_showcase

    state = st.session_state
    played, context = scripted_showcase(get_config())
    for key in ("group_live", "group_live_config", "group_live_id", "group_live_codes"):
        state.pop(key, None)
    state["live"], state["live_context"] = played, context
    state["live_id"], state["live_codes"], state["live_error"] = PLAYED_ID, store.new_codes(), ""
    state["control_unlocked"] = True
    store.save(PLAYED_ID, played, context, state["live_codes"])


GROUP_LIVE_ID = "de300002"


def load_group_live() -> None:
    """Start a live exercise on the group showcase and step it to the first moment the
    decision of the showcase is open to its team. Nothing is decided: that is for the demo."""
    from app.design import get_config
    from engine import group_live

    state, config = st.session_state, get_config()
    show = config.college.showcase
    setup = group_live.GroupLiveSetup("Group exercise", show.group, show.scenario, show.stressed,
                                      severity=show.severity)
    exercise = group_live.start(setup, "2028-03-01T09:00:00", config)
    for _ in range(80):
        opened = {(side, inject.decision_due.decision)
                  for side, inject in group_live.open_decisions(exercise, config)}
        if (group_live.GROUP, show.decision) in opened or exercise.ended:
            break
        exercise = group_live.apply(exercise, "step", "2028-03-01T09:00:00", config)
    for key in ("live", "live_context", "live_id", "live_codes"):
        state.pop(key, None)
    state["group_live"], state["group_live_config"] = exercise, config
    state["group_live_id"], state["group_live_codes"] = GROUP_LIVE_ID, store.new_group_codes()
    state["group_live_error"], state["control_unlocked"] = "", True
    store.save_group(GROUP_LIVE_ID, exercise, config, state["group_live_codes"])


def _is_played() -> bool:
    return st.session_state.get("live_id") == PLAYED_ID and "live" in st.session_state


def show(kind: str) -> None:
    """Set what the Design and Prepare pages show, on its showcase run."""
    state = st.session_state
    state[KIND_KEY] = kind
    state["demo_on"] = False
    for key in list(state):
        if isinstance(key, str) and key.startswith(("grp_", "sys_")):
            del state[key]
    for key in ("group_design", "system_design"):
        state.pop(key, None)


def go(index: int) -> None:
    """Move the tour to a stop: set what its page needs, then ask for the page."""
    state = st.session_state
    stop = STOPS[index]
    state[ON], state[STOP] = True, index
    if stop.kind is not None:
        show(stop.kind)
    if stop.guided is not None:
        state["demo_on"] = stop.guided
        if stop.guided:
            state["demo_step"] = 0
    if stop.played and (not _is_played() or "group_live" in state):
        load_played()
    if stop.group_live and state.get("group_live_id") != GROUP_LIVE_ID:
        load_group_live()
    state[GO] = stop.page


def start() -> None:
    go(0)


def end() -> None:
    st.session_state[ON] = False


def open_kind(kind: str) -> None:
    show(kind)
    st.session_state[GO] = DESIGN


def open_played() -> None:
    load_played()
    st.session_state[GO] = DELIVER


def reset() -> None:
    """Forget everything of this browser session and go back to the home page. Saved
    exercises stay in the folder `exercises/`."""
    st.session_state.clear()
    st.session_state[GO] = HOME


def follow() -> None:
    """Go to the page a button asked for (a button's callback cannot change page itself)."""
    page = st.session_state.pop(GO, None)
    if page is not None:
        st.switch_page(page)


def bar() -> None:
    """The tour's bar, under the header of a page."""
    state = st.session_state
    if not state.get(ON):
        return
    index = min(int(state.get(STOP, 0)), len(STOPS) - 1)
    stop, last = STOPS[index], len(STOPS) - 1
    with st.container(border=True, key="cse_tour"):
        text, back, forward, leave = st.columns([10, 0.9, 0.9, 1.2], vertical_alignment="center")
        text.markdown(f"**Demo tour · {index + 1} of {len(STOPS)}: {stop.title}**  \n{stop.text}")
        back.button("Back", key="tour_back", disabled=index == 0, on_click=go, args=(max(index - 1, 0),))
        forward.button("Next", key="tour_next", type="primary", disabled=index == last,
                       on_click=go, args=(min(index + 1, last),))
        leave.button("End tour", key="tour_end", on_click=end)
