"""dev/SPEC_UI.md, UI-3: information map, inject timeline, console as a cockpit, player page."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app import charts, store, theme
from engine import live
from engine.config import load_config
from engine.decisions import Choice, choices_of
from engine.live_run import scripted_showcase
from engine.schema import ACTORS, PLAYERS
from engine.script import build_branch, build_pack, information_map

APP_PATH = str(Path(__file__).resolve().parent.parent / "app" / "main.py")
CONFIG = load_config()
SCRIPT, DECISIONS = CONFIG.script, CONFIG.decisions
SHOWCASE = SCRIPT.showcase
BANK, SCENARIO = CONFIG.banks[SHOWCASE.bank], CONFIG.scenarios[SHOWCASE.scenario]
ROLES = [role.name for role in SCRIPT.roles.values()]


def branch_of(choices, bank=BANK, scenario=SCENARIO):
    return build_branch("A", "Path", bank, CONFIG.model, scenario, DECISIONS, SCRIPT, choices,
                        with_points=False)


PATHS = {key: choices_of(path) for key, path in DECISIONS.paths.items()}
BRANCH = branch_of(PATHS[SHOWCASE.path])


def knowledge(branch, day):
    return {node.actor: node for node in information_map(branch, DECISIONS, SCRIPT, day).nodes}


# ------------------------------------------------- 1. information map


@pytest.mark.parametrize("path", list(PATHS))
def test_information_map_gives_the_day_of_the_figures_the_script_gives(path):
    """The map and the injects apply the same rule: every regular report in an inject is of
    the day the map shows for that actor on that day."""
    branch = branch_of(PATHS[path])
    checked = 0
    for actor in ACTORS:
        for inject in branch.injects[actor]:
            if inject.figures:
                node = knowledge(branch, inject.day)[actor]
                assert node.informed, (actor, inject.day)
                assert node.figures_as_of == inject.figures[0].as_of_day, (actor, inject.day)
                checked += 1
    assert checked > 10


def test_an_actor_holds_nothing_before_the_stage_that_informs_it():
    stage_day = {e.kind: e.day for e in BRANCH.result.events}
    for actor, stage in (("resolution_authority", "early_intervention"),
                         ("deposit_guarantee_scheme", "likely_to_fail")):
        before = knowledge(BRANCH, stage_day[stage] - 1)[actor]
        assert not before.informed and before.figures_as_of is None and before.status == ""
        assert before.informed_since == stage_day[stage] and before.informed_from
        after = knowledge(BRANCH, stage_day[stage])[actor]
        assert after.informed and after.figures_as_of == stage_day[stage] - after.lag_days
    # The bank, the competent authority and the central bank are informed from the start.
    start = knowledge(BRANCH, 0)
    assert all(start[a].informed and start[a].figures_as_of == 0
               for a in ("bank_management", "competent_authority", "central_bank"))


def test_an_actor_never_informed_on_a_path_stays_uninformed():
    quiet = branch_of((), scenario=CONFIG.scenarios["baseline"])
    last = knowledge(quiet, quiet.result.end_day)
    assert not last["deposit_guarantee_scheme"].informed
    assert last["deposit_guarantee_scheme"].informed_since is None


def test_faster_reporting_moves_the_competent_authoritys_day():
    day = next(e.day for e in BRANCH.result.events if e.kind == "early_intervention")
    branch = branch_of((Choice("early_intervention_measures", "enhanced_reporting", day),))
    faster_from = dict(branch.result.faster_reporting_from)["competent_authority"]
    assert knowledge(branch, faster_from - 1)["competent_authority"].lag_days == 1
    late = knowledge(branch, faster_from + 1)["competent_authority"]
    assert late.lag_days == 0 and late.figures_as_of == faster_from + 1


def test_links_are_the_chain_of_a_stage_change_and_light_up_on_its_day():
    day = next(e.day for e in BRANCH.result.events if e.kind == "likely_to_fail")
    quiet_day = information_map(BRANCH, DECISIONS, SCRIPT, 1)
    chain = {(link.sender, link.recipient): link for link in quiet_day.links}
    assert set(chain) == {("bank_management", "competent_authority")} | {
        ("competent_authority", a)
        for a in ("resolution_authority", "central_bank", "deposit_guarantee_scheme")}
    assert all(link.standing and not link.active for link in chain.values())
    assert chain[("bank_management", "competent_authority")].time == SCRIPT.clock["stage_relay"]
    busy = {(l.sender, l.recipient): l for l in information_map(BRANCH, DECISIONS, SCRIPT, day).links}
    assert all(busy[pair].active and busy[pair].titles for pair in chain)
    assert busy[("competent_authority", "central_bank")].time == SCRIPT.clock["stage_relay_others"]


def test_information_map_chart_writes_the_state_and_does_not_rest_on_colour():
    day = next(e.day for e in BRANCH.result.events if e.kind == "recovery")
    info = information_map(BRANCH, DECISIONS, SCRIPT, day)
    figure = charts.build_information_map(info, SCENARIO.weekday(day))
    assert theme.uses_template(figure)
    texts = [a.text for a in figure.layout.annotations]
    for node in info.nodes:
        text = next(t for t in texts if t.startswith(f"<b>{node.name}</b>"))
        if node.informed:
            assert f"Figures as of day {node.figures_as_of}" in text and "Knows:" in text
        else:
            assert "Not yet informed" in text
    boxes = [s for s in figure.layout.shapes if s.line.dash in ("solid", "dash") and s.line.width]
    assert sum(s.line.dash == "dash" for s in boxes) == sum(not n.informed for n in info.nodes) > 0
    arrows = [a for a in figure.layout.annotations if a.showarrow]
    assert len(arrows) == len(info.links)
    assert f"day {day}, {SCENARIO.weekday(day)}" in figure.layout.title.text


# ------------------------------------------------- 2. inject timeline


def test_timeline_has_one_marker_per_inject_of_the_main_events_list():
    pack = build_pack(BANK, CONFIG.model, SCENARIO, DECISIONS, SCRIPT,
                      [("Selected", PATHS[SHOWCASE.path])])
    branch = pack.branches[0]
    marks = charts.timeline_marks(branch, PLAYERS, SCRIPT.actors, ROLES)
    in_player_lanes = [m for m in marks if m["lane"] != charts.STAKEHOLDER_LANE]
    assert len(in_player_lanes) == sum(len(event.recipients) for event in pack.events)
    played = [m for m in marks if m["lane"] == charts.STAKEHOLDER_LANE]
    assert played and all(m["delivered_by"] in ROLES for m in played)
    assert {m["purpose"] for m in marks} <= set(theme.PURPOSES)


def test_timeline_has_one_window_per_decision_in_the_deciding_actors_lane():
    windows = charts.timeline_windows(BRANCH, PLAYERS, SCRIPT.actors, SCRIPT.clock["decision_due"])
    due = {i.decision_due.decision: (actor, i) for actor in ACTORS for i in BRANCH.injects[actor]
           if i.decision_due is not None}
    assert len(windows) == len(due) > 3
    by_name = {w["name"]: w for w in windows}
    for decision, (actor, inject) in due.items():
        window = by_name[inject.decision_due.name]
        assert window["lane"] == (SCRIPT.actors[actor] if actor in PLAYERS else charts.STAKEHOLDER_LANE)
        assert window["open_day"] == inject.day and window["start"] < window["end"]
        assert window["end"] == pytest.approx(inject.decision_due.deadline_day + 0.5)
    # A recorded decision ends its window on the day it was taken.
    taken = charts.timeline_windows(BRANCH, PLAYERS, SCRIPT.actors, SCRIPT.clock["decision_due"],
                                    {"ela": 3})
    ela = next(w for w in taken if w["taken"] == 3)
    assert ela["end"] == pytest.approx(3.5)


def test_timeline_chart_has_every_lane_and_the_now_line_only_when_asked():
    plain = charts.build_branch_timeline(BRANCH, SCRIPT, PLAYERS, SCENARIO.start_weekday, True)
    lanes = list(plain.layout.yaxis.ticktext)
    assert lanes == [SCRIPT.actors[a] for a in PLAYERS] + [charts.STAKEHOLDER_LANE]
    assert not any(a.text == "<b>Now</b>" for a in plain.layout.annotations)
    names = [trace.name for trace in plain.data if trace.showlegend is not False]
    assert names[:3] == [theme.PURPOSE_LABELS[p] for p in theme.PURPOSES]
    assert not any("Hollow" in name for name in names)
    for purpose in theme.PURPOSES:  # colour and shape together
        trace = next(t for t in plain.data if t.name == theme.PURPOSE_LABELS[purpose])
        assert trace.marker.color == theme.palette().purposes[purpose]
        assert all(symbol == theme.PURPOSE_SYMBOLS[purpose] for symbol in trace.marker.symbol)
    released = {live.inject_key(i) for a in PLAYERS for i in BRANCH.injects[a] if i.day <= 2}
    console = charts.build_branch_timeline(
        BRANCH, SCRIPT, PLAYERS, SCENARIO.start_weekday, True, now=(2, "19:30"),
        released=released, key_of=live.inject_key)
    assert any(a.text == "<b>Now</b>" for a in console.layout.annotations)
    symbols = [s for t in console.data if t.showlegend is False and t.marker.symbol is not None
               for s in t.marker.symbol]
    assert any(s.endswith("-open") for s in symbols) and any(not s.endswith("-open") for s in symbols)
    assert any("Hollow" in (t.name or "") for t in console.data)


@pytest.mark.parametrize("pal", list(theme.PALETTES.values()), ids=lambda p: p.key)
def test_purpose_colours_stay_apart_and_show_on_the_surface(pal):
    assert set(pal.purposes) == set(theme.PURPOSES) == set(theme.PURPOSE_SYMBOLS)
    for vision in (None, *theme.CVD_MATRICES):
        for first in theme.PURPOSES:
            for second in theme.PURPOSES:
                if first < second:
                    assert theme.distance(pal.purposes[first], pal.purposes[second], vision) >= 8.0
    assert all(theme.contrast(color, pal.surface) >= 3.0 for color in pal.purposes.values())


# ------------------------------------------------- 3. console as a cockpit


def live_state(until):
    """A live exercise of the showcase, stepped until `until(state)` holds."""
    context = live.Context(CONFIG.model, DECISIONS, SCRIPT)
    setup = live.make_setup("Exercise", BANK, CONFIG.model, SCENARIO, DECISIONS, SCRIPT,
                            PATHS[SHOWCASE.path])
    clock = datetime(2026, 1, 5, 9, 0, 0)
    state = live.start(setup, clock.isoformat(timespec="seconds"), context)
    while not until(state) and not state.ended:
        clock += timedelta(seconds=30)
        state = live.apply(state, "step", clock.isoformat(timespec="seconds"), context)
    return state, context


def console(state, context):
    at = AppTest.from_file(APP_PATH)
    at.session_state["control_unlocked"] = True
    at.session_state["live"], at.session_state["live_context"] = state, context
    at.session_state["live_id"], at.session_state["live_codes"] = store.new_id(), store.new_codes()
    at.switch_page("views/console.py").run(timeout=60)
    return at


def test_console_shows_open_decisions_as_cards_with_the_time_left():
    state, context = live_state(lambda s: len(live.open_decisions(s)) >= 2)
    at = console(state, context)
    assert not at.exception
    shown = [m.value for m in at.markdown]
    opened = live.open_decisions(state)
    for inject in opened:
        due = inject.decision_due
        assert f"**{due.name}**" in shown
        left = due.deadline_day - state.day + 1
        assert any(f"**{left} closes of day left**" in text and "real minute" in text
                   and f"decide by the close of day {due.deadline_day}" in text for text in shown)
        assert at.button(key=f"live_record_{due.decision}") is not None
    assert not any("Deadline: the close of today" in text for text in shown)


def test_console_card_warns_on_the_deadline_day():
    state, context = live_state(lambda s: any(
        i.decision_due.deadline_day == s.day for i in live.open_decisions(s)))
    assert not state.ended
    at = console(state, context)
    assert not at.exception
    assert any("**Deadline: the close of today.**" in m.value for m in at.markdown)


def test_console_order_is_clock_decisions_timeline_then_the_rest_collapsed():
    state, context = live_state(lambda s: s.day >= 2)
    at = console(state, context)
    assert not at.exception and not at.metric
    headings = [m.value for m in at.markdown if m.value.startswith("### ")]
    assert headings == ["### Decisions open to the players", "### To be passed on by the players",
                        "### Inject timeline"]
    labels = [e.label for e in at.expander]
    for wanted in ("Information map: who knows what today", "State of the bank (full information)",
                   "Coming next", "Released so far", "Players' pages and saving",
                   "Send an ad hoc inject"):
        assert wanted in labels
    assert labels.index("Information map: who knows what today") < labels.index(
        "State of the bank (full information)")
    assert len(at.get("plotly_chart")) == 2  # the timeline and the information map


# ------------------------------------------------- 4. player page


@pytest.fixture(scope="module")
def played():
    return scripted_showcase(CONFIG)


@pytest.mark.parametrize("player", PLAYERS)
def test_player_page_puts_the_latest_inject_on_top_and_dates_every_figures_table(played, player):
    state, context = played
    exercise_id, codes = store.new_id(), store.new_codes()
    store.save(exercise_id, state, context, codes)  # the folder is the test's own (conftest)
    at = AppTest.from_file(APP_PATH)
    at.query_params["exercise"], at.query_params["player"] = exercise_id, player
    at.query_params["code"] = codes[player]
    at.run(timeout=60)
    assert not at.exception and not at.error
    received = live.released_to(state, player)
    latest = received[-1].inject
    headings = [m.value for m in at.markdown if m.value.startswith("### ")]
    assert headings == [f"### Day {latest.day}, {latest.weekday}, {latest.time} - {latest.title}"]
    # Earlier injects are collapsed, the latest of them first.
    labels = [e.label for e in at.expander]
    assert len(labels) == len(received) - 1
    assert received[-2].inject.title in labels[0] and received[0].inject.title in labels[-1]
    tables = [m.value for m in at.markdown if m.value.startswith("| Figure |")]
    assert tables and all("| As of |" in table.split("\n")[0] for table in tables)
    for table in tables:
        for row in table.split("\n")[2:]:
            assert "day" in row.split("|")[-2], row  # no figure without its date
    # Read-only, and no view of the whole exercise.
    assert not at.button and not at.selectbox and not at.get("plotly_chart")
