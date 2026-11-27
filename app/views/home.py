"""Home page: what the tool does, the four phases of the Handbook, the three kinds of
exercise, and what a demonstration needs (the tour, the played exercise, the reset)."""

import streamlit as st

from app import tour, ui
from app.charts import build_overview
from app.design import get_config, with_legal
from engine import legal as legal_refs
from engine.decisions import OUTCOME_LABELS, choices_of
from engine.model import simulate

ui.header("Home", "What the tool does")
st.title("Crisis Scenario Engine")
st.markdown(
    "The day-by-day script of a bank crisis for a simulation exercise between authorities: "
    "consistent numbers for a **fictitious** bank, what each team knows and when, the "
    "decisions due, and the console to run the exercise."
)


@st.cache_data
def _showcase():
    """The showcase run, as the Design page opens on it."""
    config = get_config()
    show = config.script.showcase
    bank, scenario = config.banks[show.bank], config.scenarios[show.scenario]
    result = simulate(bank, config.model, scenario, config.decisions,
                      choices_of(config.decisions.paths[show.path]))
    weekends = config.model.with_overrides(scenario.overrides).weekend_rules_on.value >= 1
    return result, bank, scenario, weekends


_result, _bank, _scenario, _weekends = _showcase()
with st.container(key="cse_home_overview"):
    ui.chart(build_overview(_result, _bank.unit, _scenario.start_weekday, _weekends), key="home_overview")
st.caption(
    f"One script at a glance: {_bank.name}, {_scenario.name.lower()}. The line is the liquidity "
    "the bank can still raise; the strip is the stage it is in; each mark is a decision of a "
    f"team. Here it ends on day {_result.end_day}: {OUTCOME_LABELS[_result.outcome].lower()}. "
    "Change a decision and the script changes."
)

st.subheader("The four phases of an exercise")
st.caption("The pages follow the phases of the EBA Handbook on simulation exercises (EBA/REP/2025/20).")
# Why such exercises exist: the row of config/legal.yaml, as context (on with the switch).
_legal = legal_refs.of(with_legal(get_config()).script)
if _why := legal_refs.keys(_legal, ["document:why_exercises"]):
    _row = _legal.references[_why[0]]
    st.caption("Why such exercises exist, in EU law: " + _row.concept.split(": ", 1)[-1] + ". "
               + legal_refs.line(_legal, ["document:why_exercises"]) + ". " + _legal.note)
PHASE_TEXT = (
    "Bank, trigger and decisions to test. See what each decision changes.",
    "The exercise pack: each team's injects, with only the figures it holds.",
    "The live console: a simulated clock, a page per team, decisions recorded.",
    "A draft closing report: what was decided, and what one change would give.",
)
for number, (column, (title, page), text) in enumerate(zip(st.columns(4), ui.PHASES, PHASE_TEXT), start=1):
    with column.container(border=True, key=f"cse_home_phase_{number}"):
        st.markdown(f"#### {number}  {title}")
        st.write(text)
        st.page_link(page, label=f"Open {title}", icon=":material/arrow_forward:")

st.subheader("Three kinds of exercise")
KINDS = (
    (tour.SINGLE, "One bank and its four authorities. Four archetypes, five triggers.",
     "Open a single bank", "home_single"),
    (tour.GROUP, "A parent and a subsidiary in two countries; the host can ring-fence.",
     "Open a cross-border group", "home_group"),
    (tour.SYSTEM, "Two unrelated banks at once; what is done on one changes the other.",
     "Open a system-wide stress", "home_system"),
)
for column, (kind, text, label, key) in zip(st.columns(3), KINDS):
    with column.container(border=True, key=f"cse_{key}"):
        st.markdown(f"#### {kind}")
        st.write(text)
        st.button(label, key=key, on_click=tour.open_kind, args=(kind,))

st.subheader("For a demonstration")
left, middle, right = st.columns(3)
with left.container(border=True, key="cse_home_tour"):
    st.markdown("#### Demo tour")
    st.write(f"{len(tour.STOPS)} stops across the pages and the three kinds of exercise.")
    st.button("Start the demo tour", key="home_tour", type="primary", on_click=tour.start)
with middle.container(border=True, key="cse_home_played"):
    st.markdown("#### An exercise already played")
    st.write("Five decisions entered live, two off plan. Opens on the console.")
    st.button("Load the played exercise", key="home_played", on_click=tour.open_played)
with right.container(border=True, key="cse_home_reset"):
    st.markdown("#### Start again")
    st.write("Forgets this session's choices. Saved exercises are kept.")
    st.button("Reset the demo", key="home_reset", on_click=tour.reset)

st.caption(
    "A prototype from a university project: not a supervisory model, a stress test or a "
    "forecast. Every bank is fictitious; every number without a verifiable source is marked "
    "as an assumption. The tool runs on this machine and needs no connection."
)
