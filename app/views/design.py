"""Design page: bank, scenario designer, decision path and sensitivity, with the guided demo.

The parameter sliders are under "Advanced". What is set here is kept in the session as the
"design" that the Prepare page builds the exercise pack from (app/design.py).
"""

import time

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitAPIException

from app import demo, group_view, theme, ui
from app.design import MARKET_KEY, REVERSE_KEY, initial, store_design
from app.charts import (
    build_information_map,
    TRIGGERS,
    build_balance_sheet,
    build_decision_map,
    build_failure_distribution,
    build_funding,
    build_path_comparison,
    build_resolution_stack,
    build_scorecard,
    build_timeline,
    stage_by_day,
)
from engine.config import load_config
from engine.decisions import (
    OPEN_OUTCOMES,
    OUTCOME_LABELS,
    Choice,
    choices_of,
    decision_log_rows,
    intraday_allocation,
    scorecard_rows,
)
from engine.model import buffer_rises, simulate, uninsured_share, with_uninsured_share
from engine.resolution import (
    LEVELS,
    level_value,
    breakpoint_text,
    resolution_levels,
    summary_rows,
)
from engine import objective, reverse
from engine.schema import TAXONOMY, TAXONOMY_TITLES, WEEKDAYS
from engine.script import build_branch, information_map, scenario_with_challenges
from engine.montecarlo import (
    bands,
    describe_failure_day,
    failure_day_counts,
    recentre,
    run_monte_carlo,
    summarise,
)

# Range of the uninsured-share slider (a display limit, not a model parameter).
UNINSURED_SHARE_RANGE = (10, 95)

# Model parameters with a slider: name -> (label, display factor, step, format, help).
PARAMETER_SLIDERS = {
    "panic_max_multiplier": (
        "Maximum panic multiplier", 1.0, 0.5, "%.1f",
        "How many times faster than the base rate deposits leave at peak panic.",
    ),
    "central_bank_mobilisation_lag_days": (
        "Central bank mobilisation lag (days)", 1.0, 1.0, "%d",
        "Days between the recovery trigger and central bank funding becoming usable.",
    ),
    "central_bank_haircut": (
        "Central bank haircut (%)", 100.0, 1.0, "%d",
        "Haircut applied to collateral pledged with the central bank.",
    ),
    "fire_sale_discount": (
        "Fire-sale discount (%)", 100.0, 1.0, "%d",
        "Loss on illiquid assets sold in a hurry; it reduces CET1.",
    ),
}

WATERFALL_COLUMNS = {
    "day": "Day",
    "outflow_total": "Outflow",
    "funded_cash": "Cash",
    "funded_hqla": "Liquid securities",
    "funded_central_bank": "Central bank",
    "funded_ela": "ELA",
    "funded_fire_sale": "Asset sales",
    "unmet_outflow": "Unmet",
    "queued_withdrawals": "Queued",
    "status": "Stage",
}


def _status_label(status: str) -> str:
    """Stage name as shown to the user; the engine calls the payments default 'failed'."""
    return "payments default" if status == "failed" else status.replace("_", " ")


@st.cache_resource
def get_config():
    return load_config()


# ------------------------------------------------- scenario designer


def scenario_designer(config, default: str) -> list[str]:
    """Filters by the EBA Handbook's taxonomy; returns the scenarios that match."""
    chosen = {}
    with st.expander("Scenario designer"):
        st.caption(
            "Filter the scenarios by the taxonomy of the EBA Handbook on simulation exercises "
            "(par. 115): type of issue, systemic relevance, speed of events."
        )
        for dimension, values in TAXONOMY.items():
            chosen[dimension] = st.selectbox(
                TAXONOMY_TITLES[dimension],
                ["any", *values],
                format_func=lambda k, values=values: "Any" if k == "any" else values[k],
                key=f"taxonomy:{dimension}",
            )
    active = {d: v for d, v in chosen.items() if v != "any"}
    if not active:
        options = list(config.scenarios)
    else:
        options = [
            key for key, scenario in config.scenarios.items()
            if scenario.taxonomy is not None
            and all(getattr(scenario.taxonomy, d) == v for d, v in active.items())
        ]
    if not options:
        if active.get("scope") == "system_wide":
            st.warning("System-wide scenarios need several banks: not available yet. All scenarios are shown.")
        else:
            st.warning("No scenario matches this combination. All scenarios are shown.")
        options = list(config.scenarios)
    if st.session_state.get("scenario") not in options:
        st.session_state["scenario"] = default if default in options else options[0]
    return options


# ------------------------------------------------- decisions


def _load_path() -> None:
    """Set every decision widget to the options and days of the chosen named path."""
    catalogue = get_config().decisions
    chosen = {c.decision: c for c in catalogue.paths[st.session_state["decision_path"]].choices}
    for key, decision in catalogue.decisions.items():
        choice = chosen.get(key)
        st.session_state[f"dec:{key}:option"] = choice.option if choice else decision.default_option
        if choice:
            st.session_state[f"dec:{key}:day"] = choice.day


def decision_panel(catalogue, horizon: int) -> tuple[Choice, ...]:
    """Sidebar panel: one option per decision, and the day from which it may be taken."""
    st.selectbox(
        "Decision path",
        list(catalogue.paths),
        format_func=lambda k: catalogue.paths[k].name,
        key="decision_path",
        on_change=_load_path,
        help="A ready-made set of decisions. Change single decisions below.",
    )
    choices = []
    with st.expander("Decisions"):
        st.caption(
            "A decision is taken after the close of a day and is in effect the next day. "
            "If its condition is not met yet, it is taken as soon as it is."
        )
        for key, decision in catalogue.decisions.items():
            actor = decision.actor.replace("_", " ").capitalize()
            option = st.selectbox(
                f"{actor}: {decision.name}",
                list(decision.options),
                index=list(decision.options).index(decision.default_option),
                format_func=lambda k, options=decision.options: options[k].name,
                key=f"dec:{key}:option",
            )
            if option != decision.default_option:
                day_key = f"dec:{key}:day"
                # A value loaded from a named path is already in the session state.
                start = {} if day_key in st.session_state else {"value": 1}
                day = st.number_input(
                    f"{decision.name}: decide from the close of day",
                    min_value=0,
                    max_value=horizon,
                    step=1,
                    key=day_key,
                    **start,
                )
                choices.append(Choice(key, option, int(day)))
    return tuple(choices)


# ------------------------------------------------- guided demo


def _go_to_step(step: int) -> None:
    st.session_state["demo_step"] = step
    tab = demo.DEMO_STEPS[step].tab
    if tab is not None:
        st.session_state["view"] = tab


def _run_pressed() -> None:
    _go_to_step(demo.FIRST_STEP_WITH_RESULTS)
    st.session_state["play_next"] = 0  # the crisis starts playing from day 0


def _exit_demo() -> None:
    st.session_state["demo_on"] = False


def demo_panel(step_index: int) -> None:
    step = demo.DEMO_STEPS[step_index]
    last = len(demo.DEMO_STEPS) - 1
    st.html(demo.highlight_css(step.control))
    with st.container(border=True):
        st.markdown(
            f"**Guided demo · step {step_index + 1} of {len(demo.DEMO_STEPS)}: {step.title}**"
        )
        st.write(step.instruction)
        back, forward, leave, _ = st.columns([1, 2, 2, 5])
        back.button(
            "Back", key="demo_back", disabled=step_index == 0,
            on_click=_go_to_step, args=(max(step_index - 1, 0),),
        )
        if step_index == demo.FIRST_STEP_WITH_RESULTS - 1:
            with forward.container(key="ctl_run"):
                st.button(
                    "Run the simulation", key="demo_run", type="primary", on_click=_run_pressed
                )
        else:
            forward.button(
                "Next", key="demo_next", type="primary", disabled=step_index == last,
                on_click=_go_to_step, args=(min(step_index + 1, last),),
            )
        leave.button("Exit demo", key="demo_exit", on_click=_exit_demo)
        if step_index == last:
            st.page_link("views/prepare.py", label="Open the Prepare page", icon=":material/arrow_forward:")


# ------------------------------------------------- timeline, day by day


def _start_playback() -> None:
    st.session_state["play_next"] = 0


def _stop_playback() -> None:
    st.session_state["play_next"] = None


@st.fragment
def timeline_view(result, horizon, unit, band_data, n_runs, start_weekday, weekends, params) -> None:
    """Timeline revealed up to a chosen day. A fragment, so playback redraws only this part."""
    table = result.table
    rises = buffer_rises(result)
    last_day = int(table["day"].max())
    if st.session_state.get("play_next") is not None:
        st.session_state["day"] = min(st.session_state["play_next"], last_day)
    st.session_state["day"] = min(st.session_state.get("day", last_day), last_day)

    with st.container(key="ctl_play"):
        play_column, slider_column = st.columns([1, 7], vertical_alignment="bottom")
        play_column.button("Play", key="play", on_click=_start_playback)
        day = slider_column.slider(
            "Day shown",
            min_value=0,
            max_value=last_day,
            key="day",
            on_change=_stop_playback,
            help="The chart shows the crisis up to this day.",
        )

    chart_column, day_column = st.columns([3, 1])
    ui.chart(
        build_timeline(
            result, horizon, unit, band_data, until_day=day,
            start_weekday=start_weekday, weekends=weekends, rises=rises,
        ),
        container=chart_column,
    )
    for rise_day, rise, reason in rises:
        if rise_day <= day:
            chart_column.caption(
                f"Why the liquidity buffer rises on day {rise_day} (+{rise:.2f} {unit}): {reason}. "
                "The buffer counts central bank capacity from the day the collateral is usable."
            )
    with chart_column:
        ui.stage_legend(lead="Background colour, stage of the bank:")
    chart_column.caption(
        f"Line: central run. Light band, shown on the last day: 5th to 95th percentile of "
        f"{n_runs} sensitivity runs (assumed ranges, not probabilities); a run that ends in a "
        "payments default keeps its last values. Grey columns: weekends."
    )
    row = table[table["day"] == day].iloc[0]
    with day_column:
        weekday = f", {row['weekday']}" if row["weekday"] else ""
        st.markdown(f"**Day {day}{weekday}: {_status_label(row['status'])}**")
        st.html(theme.stage_badge(dict(stage_by_day(result))[day]))
        if row["weekend"]:
            st.caption("Weekend: no market sales, withdrawals paid from cash only.")
        if row["queued_withdrawals"] > 0:
            st.metric(f"Requested, not yet settled ({unit})", f"{row['queued_withdrawals']:.2f}")
        st.metric(f"Liquidity buffer ({unit})", f"{row['liquidity_buffer']:.2f}")
        st.metric("Deposits lost", f"{row['cum_deposit_loss_pct']:.1f}%")
        st.metric("CET1 ratio", f"{100 * row['cet1_ratio']:.1f}%")
        if row["outflow_total"] > 0:
            st.markdown("**Outflow by time of day**")
            for label, amount, _ in intraday_allocation(row["outflow_total"], params):
                st.markdown(f"- {label}: {amount:.2f}")
            st.caption("An allocation of the daily figure with fixed shares, not a simulation.")
        st.markdown("**Events so far**")
        events = [event for event in result.events if event.day <= day]
        if not events:
            st.caption("Nothing yet.")
        for event in events:
            label = TRIGGERS[event.kind][0] if event.kind in TRIGGERS else event.kind.capitalize()
            st.markdown(f"- Day {event.day}, {label}: {event.detail}")

    if st.session_state.get("play_next") is not None:
        if day < last_day:
            st.session_state["play_next"] = day + 1
            time.sleep(demo.PLAYBACK_SECONDS)
            try:
                st.rerun(scope="fragment")
            except StreamlitAPIException:
                # Playback started by a full page run (the demo's Run button).
                st.rerun()
        st.session_state["play_next"] = None


# ------------------------------------------------- reverse mode


def _load_solution(prefix: str, solution: reverse.Solution) -> None:
    """Put a solved parameter set into the design: the severity on its slider, the rest in
    the session."""
    values = {variable.name: variable.value for variable in solution.variables}
    st.session_state[prefix + "severity"] = round(values[reverse.SEVERITY], 3)
    st.session_state[REVERSE_KEY] = {
        "prefix": prefix,
        "news": round(values[reverse.NEWS_SCALE], 3),
        "speed": round(values[reverse.SPEED_SCALE], 3),
        "weekday": solution.start_weekday,
    }


def reverse_view(start_bank, bank_key, start_scenario, overrides, share_pct, prefix, horizon) -> None:
    """Reverse mode: the user gives a target, the solver returns the parameters."""
    config = get_config()
    st.subheader("Reverse mode: from a target to the parameters")
    st.caption(
        "Give the outcome the exercise needs. The solver searches the scenario's severity, "
        "the size of its news shocks and the archetype's run-off speed, each between its low "
        "and high value, on the path without decisions. The target is the day the model "
        "raises its \"likely to fail\" flag: the FoLtF determination itself is a decision "
        "of the players. Market-shock challenge injects are left out of the search."
    )
    day_column, cause_column, band_column, weekday_column = st.columns([1, 1, 2, 1])
    flag_day = day_column.number_input(
        "Flag raised on day", min_value=1, max_value=horizon, step=1, key="reverse_day",
        **initial("reverse_day", value=8),
    )
    cause = cause_column.radio(
        "Cause of the flag", reverse.CAUSES, key="reverse_cause", format_func=str.capitalize,
    )
    band = band_column.slider(
        "Deposits lost by that day (%)", min_value=0, max_value=100, key="reverse_band",
        **initial("reverse_band", value=(20, 30)),
    )
    weekday = weekday_column.selectbox(
        "Weekday of that day", ["any", *WEEKDAYS], key="reverse_weekday",
        format_func=lambda day: "Any" if day == "any" else day,
        help="If set, the first day of the scenario is moved so that the flag day falls on it.",
    )
    bank = start_bank
    if share_pct != round(100 * uninsured_share(bank)):
        bank = with_uninsured_share(bank, share_pct / 100)
    scenario = start_scenario.model_copy(update={"overrides": overrides})
    target = reverse.Target(
        int(flag_day), cause, (float(band[0]), float(band[1])),
        None if weekday == "any" else weekday,
    )
    # The solution is kept while the inputs stay the same.
    solved_for = (prefix, share_pct, repr(sorted(overrides.items())), target)
    if st.session_state.get("reverse_for") != solved_for:
        started = time.perf_counter()
        st.session_state["reverse_solution"] = (
            reverse.solve(bank, config.model, scenario, target), time.perf_counter() - started
        )
        st.session_state["reverse_for"] = solved_for
    solution, seconds = st.session_state["reverse_solution"]
    if solution.feasible:
        st.success("Feasible: the parameter set below produces the target.")
    else:
        st.error(
            "Infeasible within the ranges. The closest parameter set is shown; gaps: "
            + "; ".join(solution.gaps) + "."
        )
    if solution.flag_day is None:
        st.markdown("**Run of this set:** the flag is not raised.")
    else:
        st.markdown(
            f"**Run of this set:** flag on day {solution.flag_day} "
            f"({solution.scenario.weekday(solution.flag_day)}), cause {solution.cause}, "
            f"deposits lost {solution.deposits_lost_pct:.1f}%. Day 1 is a {solution.start_weekday}."
        )
    st.dataframe(
        pd.DataFrame(
            {
                "Parameter": [v.label for v in solution.variables],
                "Solved value": [v.value for v in solution.variables],
                "Central value": [v.central for v in solution.variables],
                "Low": [v.low for v in solution.variables],
                "High": [v.high for v in solution.variables],
                "Distance from central": [round(100 * v.plausibility) for v in solution.variables],
            }
        ),
        hide_index=True,
        width="stretch",
        column_config={
            "Solved value": st.column_config.NumberColumn(format="%.2f"),
            "Central value": st.column_config.NumberColumn(format="%.2f"),
            "Low": st.column_config.NumberColumn(format="%.2f"),
            "High": st.column_config.NumberColumn(format="%.2f"),
            "Distance from central": st.column_config.ProgressColumn(
                min_value=0, max_value=100, format="%d%%",
                help="0% = the central value, 100% = at the low or high bound. A plausibility "
                "indicator: the further from the central value, the more the scenario leans "
                "on an extreme assumption.",
            ),
        },
    )
    st.caption(
        f"{solution.runs} simulations in {seconds:.1f} seconds. The search is a fixed grid with "
        "refinements, without random numbers: the same target always gives the same answer. "
        "Among the sets that match, the one closest to the central values is returned. The "
        "ranges of the news shocks and of the run-off speed are assumptions."
    )
    st.button(
        "Load this parameter set into the design", key="reverse_load", type="primary",
        on_click=_load_solution, args=(prefix, solution),
    )
    ui.chart(
        build_timeline(
            solution.result, horizon, bank.unit, start_weekday=solution.start_weekday,
            weekends=config.model.with_overrides(overrides).weekend_rules_on.value >= 1,
        ),
        key="reverse_chart",
    )


# ------------------------------------------------- design by objective, decision map


@st.cache_data(show_spinner=False)
def _proposal(decision: str):
    return objective.propose(get_config(), decision)


def _load_setting(bank_key: str, scenario_key: str) -> None:
    st.session_state["bank"] = bank_key
    st.session_state["scenario"] = scenario_key
    for dimension in TAXONOMY:
        st.session_state[f"taxonomy:{dimension}"] = "any"


def objective_view(bank, scenario) -> None:
    config = get_config()
    objectives = config.script.objectives
    st.subheader("Design by objective")
    st.caption(
        "Pick what the exercise should test. The tool runs the decision with each of its "
        "options in every bank and scenario, on the day it opens and one and two days later, "
        "and proposes the setting in which the options differ most and none is lowest on "
        "every compared line. Decisions that must come first, and decisions that depend on the "
        "option taken, are taken at their first close; all others are left untaken. Central "
        "parameters, no challenge injects."
    )
    key = st.selectbox(
        "What do you want to exercise?", list(objectives), key="objective",
        format_func=lambda k: objectives[k].label,
    )
    chosen = objectives[key]
    st.caption(
        "Area of the Handbook's Annex 2: "
        + (chosen.annex2 if chosen.annex2 != "not in Annex 2"
           else "not in Annex 2, which lists the resolution authority's own tasks") + "."
    )
    proposal = _proposal(chosen.decision)
    if proposal.hard:
        st.success(proposal.explanation)
    else:
        st.warning(proposal.explanation)
    setting = proposal.setting
    if setting is not None:
        unit = config.banks[setting.bank_key].unit
        rows = pd.DataFrame(objective.setting_rows(setting, unit))
        st.dataframe(
            rows, hide_index=True, width="stretch",
            column_config={
                column: st.column_config.NumberColumn(format="%.2f")
                for column in rows.columns if rows[column].dtype == float
            },
        )
        st.caption(config.script.objective_texts["directions"])
        if setting.spread > 0:
            st.button(
                f"Load this bank and scenario into the design "
                f"({config.banks[setting.bank_key].name}, "
                f"{config.scenarios[setting.scenario_key].name})",
                key="objective_load", on_click=_load_setting,
                args=(setting.bank_key, setting.scenario_key),
            )

    st.subheader("Decision map")
    decisions = config.decisions.decisions
    st.caption(
        f"For the design on this page ({bank.name}, {scenario.name}): the outcome of every "
        "combination of options of the decisions picked below, each decision taken at the "
        "first close at which it can be. An option that cannot be taken, because its "
        "condition is never met, is marked \"not open\"."
    )
    keys = st.multiselect(
        f"Decisions on the map (up to {objective.MAX_MAP_DECISIONS}, in this order)",
        list(decisions), key="map_decisions", max_selections=objective.MAX_MAP_DECISIONS,
        format_func=lambda k: decisions[k].name,
        **initial("map_decisions", default=["declare_foltf", "resolution_tool"]),
    )
    if not keys:
        st.info("Pick at least one decision.")
        return
    tree = objective.decision_map(bank, config.model, scenario, config.decisions, keys)
    ui.chart(build_decision_map(tree, OUTCOME_LABELS), key="map_chart")
    st.caption(
        f"{len(tree.leaves)} combinations. Filled marker: every option of the combination was "
        "taken. Hollow marker: at least one option was not open."
    )


# ------------------------------------------------- page

config = get_config()
showcase = config.script.showcase
if "decision_path" not in st.session_state:
    # The app opens on the showcase run, with its decision path loaded.
    st.session_state["decision_path"] = showcase.path
    _load_path()
st.session_state.setdefault("demo_on", True)
st.session_state.setdefault("demo_step", 0)

with st.sidebar:
    st.toggle("Guided demo", key="demo_on", help="A six-step walk through the tool.")
    st.radio(
        "Exercise on", [group_view.SINGLE, group_view.GROUP, group_view.SYSTEM], key=group_view.KIND_KEY,
        disabled=st.session_state["demo_on"],
        help="A cross-border group is a parent and a subsidiary in two countries. System-wide is "
        "two unrelated banks of one country under stress at once. The guided demo is on a "
        "single bank: exit it to design either.",
    )
if group_view.is_group():
    group_view.design_page(config)
    st.stop()

with st.sidebar:
    st.header("Scenario")
    with st.container(key="ctl_bank"):
        bank_key = st.selectbox(
            "Bank archetype",
            list(config.banks),
            format_func=lambda k: config.banks[k].name,
            key="bank",
            **initial("bank", index=list(config.banks).index(showcase.bank)),
        )
    with st.container(key="ctl_scenario"):
        scenario_options = scenario_designer(config, showcase.scenario)
        # Its value is set in the session state by the designer, so no index is given here.
        scenario_key = st.selectbox(
            "Trigger scenario",
            scenario_options,
            format_func=lambda k: config.scenarios[k].name,
            key="scenario",
        )
    start_bank = config.banks[bank_key]
    start_scenario = config.scenarios[scenario_key]
    bank, scenario = start_bank, start_scenario
    resolved = config.model.with_overrides(scenario.overrides)
    # Slider keys include the bank and scenario, so the sliders reset when either changes.
    prefix = f"{bank_key}:{scenario_key}:"

    st.header("Decisions")
    with st.container(key="ctl_decisions"):
        choices = decision_panel(config.decisions, int(config.model.horizon_days.value))

    with st.expander("Advanced: parameters"):
        st.caption(
            "Change the model's parameters for this design. Every parameter, with its source, "
            "is on the Assumptions and limits page."
        )
        severity = st.slider(
            "Scenario severity",
            min_value=float(scenario.severity.low),
            max_value=float(scenario.severity.high),
            step=0.1,
            key=prefix + "severity",
            **initial(prefix + "severity", value=float(scenario.severity.value)),
            help="Multiplies every deposit category's base run-off rate.",
        )
        with st.container(key="ctl_uninsured"):
            share_pct = st.slider(
                "Uninsured deposits (% of deposits)",
                min_value=UNINSURED_SHARE_RANGE[0],
                max_value=UNINSURED_SHARE_RANGE[1],
                step=1,
                key=prefix + "uninsured_share",
                **initial(prefix + "uninsured_share", value=round(100 * uninsured_share(bank))),
                help="Everything except insured retail deposits. Total deposits stay the same.",
            )
        overrides = dict(scenario.overrides)
        shown_values = []
        for name, (label, factor, step, fmt, help_text) in PARAMETER_SLIDERS.items():
            base = getattr(config.model, name)
            current = getattr(resolved, name)
            shown = st.slider(
                label,
                min_value=base.low * factor,
                max_value=base.high * factor,
                step=step,
                format=fmt,
                key=prefix + name,
                **initial(prefix + name, value=current.value * factor),
                help=help_text,
            )
            shown_values.append(shown)
            # The slider sets the central value; the uncertainty range moves with it.
            overrides[name] = recentre(name, current, shown / factor)

        n_runs = int(config.model.monte_carlo_runs.value)
        seed = st.number_input(
            "Random seed of the sensitivity runs",
            min_value=0,
            step=1,
            key="mc_seed",
            **initial("mc_seed", value=int(config.model.monte_carlo_seed.value)),
            help=f"The same seed always gives the same {n_runs} runs.",
        )

# When any input changes, playback stops and the timeline shows the whole new run.
signature = (
    prefix, severity, share_pct, *shown_values, choices,
    tuple(st.session_state.get(MARKET_KEY, ())),
    tuple(sorted((st.session_state.get(REVERSE_KEY) or {}).items())),
)
if st.session_state.get("run_signature") != signature:
    st.session_state["run_signature"] = signature
    st.session_state["play_next"] = None
    st.session_state.pop("day", None)

ui.header("Design", f"{bank.name} \u00b7 {scenario.name}")
st.title("Design")
kind = f" ({scenario.taxonomy.label()})" if scenario.taxonomy else ""
st.caption(f"{bank.name} · {scenario.name}{kind}. {scenario.description} Amounts in {bank.unit}.")

demo_on = st.session_state["demo_on"]
demo_step = st.session_state["demo_step"]
if demo_on:
    demo_panel(demo_step)
    if demo_step < demo.FIRST_STEP_WITH_RESULTS:
        st.info("The results appear here once you press Run the simulation (step 3).")
        st.stop()

# The slider starts at the rounded share; only rebuild the bank when it was moved.
if share_pct != round(100 * uninsured_share(bank)):
    bank = with_uninsured_share(bank, share_pct / 100)
scenario = scenario.model_copy(
    update={
        "severity": recentre("severity", scenario.severity, severity),
        "overrides": overrides,
    }
)
# A reverse-mode solution loaded into this design: its severity is on the slider above; the
# news-shock scale, the speed scale and the start weekday are applied here.
applied = st.session_state.get(REVERSE_KEY)
if applied and applied["prefix"] != prefix:
    applied = None
    st.session_state.pop(REVERSE_KEY, None)
if applied:
    bank, scenario = reverse.apply(
        bank, scenario, applied["weekday"], scenario.severity.value, applied["news"],
        applied["speed"],
    )
    note, remove = st.columns([6, 1], vertical_alignment="center")
    note.info(
        f"A reverse-mode solution is loaded: news shocks x {applied['news']:.2f}, run-off "
        f"speed x {applied['speed']:.2f}, day 1 is a {applied['weekday']}; its severity is on "
        "the slider under Advanced."
    )
    remove.button("Remove it", key="reverse_remove",
                  on_click=lambda: st.session_state.pop(REVERSE_KEY, None))
# The only challenge inject that changes the run: a news event with a panic shock.
news_challenges = list(st.session_state.get(MARKET_KEY, ()))
# What the Prepare page builds the exercise pack from: the scenario before the market shocks.
store_design(bank_key, scenario_key, bank, scenario, choices)
if news_challenges:
    st.info(
        "Market shocks switched on in Prepare are part of this run: "
        + ", ".join(config.script.challenges[key].title.lower() for key in news_challenges) + "."
    )
scenario = scenario_with_challenges(
    bank, config.model, scenario, config.decisions, config.script, news_challenges
)
weekends = resolved.weekend_rules_on.value >= 1
result = simulate(bank, config.model, scenario, config.decisions, choices)
reference = simulate(start_bank, config.model, start_scenario, config.decisions, choices)
table = result.table
horizon = int(config.model.horizon_days.value)
# The runs are kept while the inputs stay the same, so playback and tab changes are instant.
runs_key = (signature, n_runs, int(seed))
if st.session_state.get("runs_key") != runs_key:
    st.session_state["runs"] = run_monte_carlo(
        bank, config.model, scenario, n_runs, int(seed), config.decisions, choices
    )
    st.session_state["runs_key"] = runs_key
monte_carlo = st.session_state["runs"]
uncertainty = summarise(monte_carlo)

failure, flag, deposit_loss, funding = st.columns(4)
flag.metric(
    "Likely-to-fail flag",
    f"Day {result.likely_to_fail_day}" if result.likely_to_fail_day is not None else "Not raised",
    help="First day on which liquidity covers too few days of outflows, or capital is "
    "projected below the requirement. A warning by the model; the run continues.",
)
if result.failure_day is not None:
    default_text = f"Day {result.failure_day}"
elif result.outcome in OPEN_OUTCOMES:
    default_text = f"None in {horizon} days"
else:
    default_text = "Avoided"
if result.failure_day is not None:
    default_note = demo.describe_shift(result.failure_day, reference.failure_day)
elif result.outcome in OPEN_OUTCOMES:
    default_note = OUTCOME_LABELS[result.outcome]
else:
    default_note = f"{OUTCOME_LABELS[result.outcome]} on day {result.end_day}"
failure.metric(
    "Payments default",
    default_text,
    delta=default_note,
    delta_color="off",
    delta_arrow="off",
    help="Central run. The line below compares it with the bank and scenario as first shown, "
    "before any slider was moved.",
)
deposit_loss.metric("Deposits lost", f"{table['cum_deposit_loss_pct'].iloc[-1]:.1f}%")
funding.metric(
    f"Resolution funding need ({bank.unit})",
    f"{result.funding_need.need:.2f}" if result.funding_need else "n/a",
    help="Liquidity needed to cover projected outflows after the payments default.",
)


# on_change="rerun" makes the tabs lazy: only the open tab is computed.
(
    timeline_tab,
    decisions_tab,
    funding_tab,
    information_tab,
    uncertainty_tab,
    reverse_tab,
    objective_tab,
) = st.tabs(demo.TABS, key="view", on_change="rerun")

if not timeline_tab.open:
    st.session_state["play_next"] = None

if timeline_tab.open:
    with timeline_tab:
        timeline_view(
            result, horizon, bank.unit, bands(monte_carlo), n_runs,
            scenario.start_weekday, weekends, resolved,
        )

if information_tab.open:
    with information_tab:
        st.subheader("Who knows what, when")
        st.caption(
            "Each actor holds its own figures, at its own delay, and some are informed only "
            "once a stage is reached. The map shows the selected path at the close of the "
            "day chosen: the day each actor's figures refer to, the stage it knows of, and "
            "the notifications of that day. Delays and scopes are assumptions "
            "(config/decisions.yaml)."
        )
        branch = build_branch(
            "A", "Selected path", bank, config.model, scenario, config.decisions, config.script,
            choices, with_points=False,
        )
        map_day = st.slider("Day shown", 0, result.end_day, min(result.end_day, max(
            (e.day for e in result.events if e.kind == "early_intervention"), default=0)),
            key=f"information_day_{result.end_day}")
        knowledge = information_map(branch, config.decisions, config.script, map_day)
        ui.chart(build_information_map(
            knowledge, scenario.weekday(map_day) if map_day >= 1 else ""), key="information_map")
        ui.table(pd.DataFrame([
            {"Actor": node.name,
             "Informed": "from the start" if not node.informed_from else (
                 f"since day {node.informed_since}" if node.informed
                 else f"not yet (from: {node.informed_from})"),
             "Figures as of": f"day {node.figures_as_of}" if node.informed else "",
             "Figures in scope": node.figures_in_scope,
             "Figures from": node.source,
             "Received this day": "; ".join(node.received)}
            for node in knowledge.nodes
        ]))

if decisions_tab.open:
    with decisions_tab:
        paths = config.decisions.paths
        st.subheader("Decision paths compared")
        st.caption(
            "Same bank, scenario and parameters; only the decisions differ. The selected path "
            "is the one set in the sidebar. Decision effects are assumptions."
        )
        reference_key = st.selectbox(
            "Compare the selected path with",
            list(paths),
            index=list(paths).index(config.decisions.default_path),
            format_func=lambda k: paths[k].name,
            key="reference_path",
        )
        other = simulate(
            bank, config.model, scenario, config.decisions, choices_of(paths[reference_key])
        )
        other_name = paths[reference_key].name
        ui.chart(
            build_path_comparison(
                other, result, other_name, "Selected path", horizon, bank.unit,
                scenario.start_weekday, weekends,
            ),
        )
        st.subheader("Outcome scorecard")
        ui.chart(build_scorecard(
            other.scorecard, result.scorecard, other_name, "Selected path", bank.unit))
        st.caption(
            "The two bars of a row share their scale. Covered deposits are the insured "
            "retail deposits."
        )
        left = scorecard_rows(other.scorecard, bank.unit)
        right = scorecard_rows(result.scorecard, bank.unit)
        with st.expander("The scorecard as a table"):
            st.dataframe(
                pd.DataFrame(
                    {
                        "Figure": [label for label, _ in left],
                        other_name: [value for _, value in left],
                        "Selected path": [value for _, value in right],
                    }
                ),
                hide_index=True,
                width="stretch",
            )
        both = {
            other_name: resolution_levels(bank, config.model, other),
            "Selected path": resolution_levels(bank, config.model, result),
        }
        if any(levels is not None for levels in both.values()):
            st.subheader("Resolution summary")
            st.caption(
                "What happens to capital and creditors once the bank is declared failing or "
                "likely to fail. The write-down found by the valuation is not known: pick an "
                "assumed level. Losses fall in the order used in this model, which is not a "
                "statement of law; legal loss-absorption thresholds are not modelled."
            )
            st.radio(
                "Valuation write-down of the loan book (assumption)",
                LEVELS,
                index=1,
                key="writedown_level",
                horizontal=True,
                format_func=lambda level: (
                    f"{level.capitalize()}: "
                    f"{100 * level_value(config.model.resolution_writedown_share, level):.0f}%"
                ),
            )
            level = st.session_state["writedown_level"]
            with_stack = [(name, levels) for name, levels in both.items() if levels is not None]
            for column, (name, levels) in zip(st.columns(len(with_stack)), with_stack):
                column.markdown(f"**{name}: {OUTCOME_LABELS[levels[level].tool]}**")
                ui.chart(build_resolution_stack(levels, bank.unit), key=f"stack_{name}",
                         container=column)
            st.caption(
                "One column per assumed level. Hatched: the part of a layer that is written "
                "down or converted, or lost in liquidation. Plain: what remains. Layers "
                "are stacked in the order they bear losses, from the bottom."
            )
            columns = {
                name: dict(summary_rows(levels[level], bank.unit)) if levels else {}
                for name, levels in both.items()
            }
            labels = list(dict.fromkeys(label for rows in columns.values() for label in rows))
            st.dataframe(
                pd.DataFrame(
                    {"Figure": labels}
                    | {name: [rows.get(label, "") for label in labels] for name, rows in columns.items()}
                ),
                hide_index=True,
                width="stretch",
            )
            for name, levels in both.items():
                if levels is not None:
                    st.caption(f"{name}: {breakpoint_text(levels[level])}")
        st.subheader("Decision log of the selected path")
        if result.decision_log:
            st.dataframe(
                pd.DataFrame(decision_log_rows(result.decision_log, bank.unit)),
                hide_index=True,
                width="stretch",
            )
        else:
            st.info("No decision is taken on the selected path: the authorities act late.")

if funding_tab.open:
    with funding_tab:
        st.subheader("How each day's outflow was funded")
        st.caption(
            "Order of use: cash, sale of liquid assets (HQLA), central bank funding, emergency "
            "liquidity assistance (ELA, only if granted), fire sale of illiquid assets. "
            f"Amounts in {bank.unit}."
        )
        ui.chart(build_funding(table, bank.unit, scenario.start_weekday), key="funding_chart")
        waterfall = table[list(WATERFALL_COLUMNS)].rename(columns=WATERFALL_COLUMNS)
        stages = [stage for _, stage in stage_by_day(result)]
        waterfall["Stage"] = waterfall["Stage"].map(ui.stage_text)
        waterfall.columns = [
            c if c in ("Day", "Stage") else f"{c} ({bank.unit})" for c in waterfall.columns
        ]
        with st.expander("The figures as a table"):
            ui.table(waterfall, stage_column="Stage", stages=stages)

        st.subheader("Balance sheet: opening against a chosen day")
        last_day = int(table["day"].max())
        sheet_day = st.slider(
            "Day compared with the opening", min_value=1, max_value=last_day,
            value=last_day, key=f"balance_day_{last_day}",
        )
        ui.chart(build_balance_sheet(result.rows[0], result.rows[sheet_day], bank.unit),
                 key="balance_chart")
        st.caption(
            "Assets on the left, liabilities and capital on the right: the two totals are "
            "equal on every day. Cash, liquid securities, central bank funding and ELA keep "
            "the colours of the funding chart."
        )

if uncertainty_tab.open:
    with uncertainty_tab:
        st.info(
            "These ranges reflect assumed parameter intervals, not probabilities. Each run "
            "draws every uncertain parameter between its low and high value; the share of "
            "runs with an outcome says how often the assumptions produce it, not how likely "
            "it is."
        )
        if uncertainty.funding_need_low is None:
            need_range = "no run defaults"
        else:
            need_range = (
                f"{uncertainty.funding_need_low:.2f} to {uncertainty.funding_need_high:.2f} {bank.unit}"
            )
        st.caption(
            f"Across {n_runs} runs with uncertain parameters: the bank defaults on payments in "
            f"{uncertainty.fail_share:.0%} of runs; default {describe_failure_day(uncertainty, horizon)}; "
            f"funding need {need_range}. Ranges are 5th to 95th percentiles. The headline figures "
            "above are the central run, which is not always the median."
        )
        st.subheader("Payments default day distribution")
        st.caption(
            f"Number of the {n_runs} runs in which the bank defaults on payments on each day."
        )
        ui.chart(build_failure_distribution(
            failure_day_counts(monte_carlo), horizon, scenario.start_weekday))
        st.caption(
            "Every parameter and its range is listed on the Assumptions page; most are "
            "assumptions without a verifiable source."
        )

if reverse_tab.open:
    with reverse_tab:
        reverse_view(start_bank, bank_key, start_scenario, overrides, share_pct, prefix, horizon)

if objective_tab.open:
    with objective_tab:
        objective_view(bank, scenario)
