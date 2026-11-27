"""SPEC_V2 and SPEC_V3 phase F, part d: a live exercise on a cross-border group, its teams'
pages and its closing report."""

import io
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from docx import Document
from streamlit.testing.v1 import AppTest

from app import group_console, store
from engine import group_live as gl
from engine import live
from engine.config import load_config
from engine.decisions import Choice
from engine.group import PARENT, SUBSIDIARY, GroupChoices, simulate_group
from engine.group_export import group_report_files
from engine.schema import GROUP_ENTITIES, PLAYERS

APP_PATH = str(Path(__file__).resolve().parent.parent / "app" / "main.py")
CONFIG = load_config()
GROUP = CONFIG.groups["showcase"]
SETUP = gl.GroupLiveSetup("Group exercise", "showcase", "fast_run")
FIGURE_TOKEN = re.compile(r"\d+\.\d+%?|\d+%")


class Clock:
    def __init__(self):
        self.now = datetime(2026, 1, 5, 9, 0, 0)

    def __call__(self, minutes: float = 2) -> str:
        self.now += timedelta(minutes=minutes)
        return self.now.isoformat(timespec="seconds")


def direct(choices: GroupChoices, scenario="fast_run", stressed=PARENT):
    return simulate_group(GROUP, CONFIG.banks, CONFIG.model, CONFIG.scenarios, scenario,
                          CONFIG.decisions, choices, stressed, CONFIG.college)


def step_until(state, clock, until, limit=120):
    for _ in range(limit):
        if until(state) or state.ended:
            break
        state = gl.apply(state, "step", clock(), CONFIG)
    return state


def open_keys(state):
    return {(side, inject.decision_due.decision) for side, inject in gl.open_decisions(state, CONFIG)}


def played(ring_fence_day=2, handle_fallbacks="fallback"):
    """A whole exercise: the host ring-fences on `ring_fence_day`; what teams should pass on
    is handled as `handle_fallbacks` says (the first notification across the border is
    passed on by the team)."""
    clock = Clock()
    state = gl.start(SETUP, clock(), CONFIG)
    first = True
    while not state.ended:
        for side, inject in gl.pending_fallbacks(state):
            kind = "relay_done" if first and inject.kind == "cross_border" else handle_fallbacks
            first = first and inject.kind != "cross_border"
            state = gl.apply(state, kind, clock(), CONFIG, team=side, key=live.inject_key(inject))
        if (gl.GROUP, "restrict_transfers") in open_keys(state) and state.day >= ring_fence_day:
            state = gl.apply(state, "decision", clock(5), CONFIG, team=gl.GROUP,
                             decision="restrict_transfers", option="ring_fence",
                             consulted=["home:competent_authority"])
        state = gl.apply(state, "step", clock(), CONFIG)
    return state


@pytest.fixture(scope="module")
def exercise():
    return played()


# ------------------------------------------------- one clock, one path


def test_both_countries_share_one_clock_and_the_path_is_the_groups_run():
    clock = Clock()
    state = gl.start(SETUP, clock(), CONFIG)
    assert state.started and state.clock == (0, CONFIG.script.clock["opening"])
    assert state.result == direct(GroupChoices())
    for _ in range(12):
        state = gl.apply(state, "step", clock(), CONFIG)
        assert state.of(PARENT).clock == state.of(SUBSIDIARY).clock == state.clock
    # A jump goes to the next stage change, decision opening or notification across the border.
    before = state.clock
    state = gl.apply(state, "jump", clock(), CONFIG)
    assert state.clock > before and state.of(PARENT).clock == state.clock
    # Each country's script is its entity's run inside the group, not the bank alone.
    for role in GROUP_ENTITIES:
        assert state.of(role).branch.result is state.result.of(role)
        assert "intragroup_claim" in state.of(role).branch.result.rows[0]


def test_teams_are_told_across_the_border_and_the_control_team_holds_the_fallback():
    clock = Clock()
    state = step_until(gl.start(SETUP, clock(), CONFIG), clock, lambda s: s.clock >= (1, "19:30"))
    waiting = gl.pending_fallbacks(state)
    crossing = [(side, i) for side, i in waiting if i.kind == "cross_border"]
    assert {side for side, _ in crossing} == {SUBSIDIARY}  # the parent is in trouble: the host is told
    assert {i.recipient for _, i in crossing} == {"competent_authority", "resolution_authority"}
    side, inject = crossing[0]
    assert inject.sender == "Competent authority of Country A"
    # Not on the team's page until it is passed on or the fallback is released.
    assert inject.title not in [r.inject.title for r in live.released_to(state.of(side), inject.recipient)]
    released = gl.apply(state, "fallback", clock(), CONFIG, team=side, key=live.inject_key(inject))
    assert inject.title in [r.inject.title for r in live.released_to(released.of(side), inject.recipient)]
    passed = gl.apply(state, "relay_done", clock(), CONFIG, team=side, key=live.inject_key(inject))
    assert live.inject_key(inject) in passed.of(side).passed_on
    with pytest.raises(live.LiveError, match="country"):
        gl.apply(state, "fallback", clock(), CONFIG, key=live.inject_key(inject))


def test_a_decision_of_the_group_changes_what_both_countries_see():
    clock = Clock()
    state = step_until(gl.start(SETUP, clock(), CONFIG), clock,
                       lambda s: (gl.GROUP, "restrict_transfers") in open_keys(s))
    assert state.day == 1 and state.result.parent.failure_day is None
    after = gl.apply(state, "decision", clock(4), CONFIG, team=gl.GROUP,
                     decision="restrict_transfers", option="ring_fence")
    record = after.decisions[0]
    assert (record.decision, record.option, record.entity, record.actor, record.day) == (
        "restrict_transfers", "ring_fence", SUBSIDIARY, "competent_authority", 1)
    assert record.applied and record.deviates and record.planned_option == "none"
    assert record.seconds_taken == pytest.approx(240, abs=121)
    # The path is the group's run with that decision, for both countries.
    assert after.result == direct(GroupChoices(group=(Choice("restrict_transfers", "ring_fence", 1),)))
    assert after.result.parent.failure_day == 5 and after.result.transfers == ()
    assert after.of(PARENT).branch.result.failure_day == 5
    assert (gl.GROUP, "restrict_transfers") not in open_keys(after)
    # The home authorities are to be told: a fallback waits once the decision is in effect.
    later = step_until(after, clock, lambda s: s.day >= 2 and s.time >= "08:00")
    told = [i for side, i in gl.pending_fallbacks(later) if i.kind == "decision_taken" and side == PARENT]
    assert told and "Full ring-fence" in told[0].text
    # "No restriction" is logged and leaves the decision open.
    kept = gl.apply(state, "decision", clock(), CONFIG, team=gl.GROUP,
                    decision="restrict_transfers", option="none")
    assert not kept.decisions[0].applied and (gl.GROUP, "restrict_transfers") in open_keys(kept)
    assert kept.result == state.result


def test_a_decision_in_one_country_changes_the_other_countrys_path():
    """Group management asks for support early: the subsidiary lends on day 2, so the host
    authorities' figures change although no host team decided anything."""
    clock = Clock()
    state = step_until(gl.start(SETUP, clock(), CONFIG), clock,
                       lambda s: (gl.GROUP, "group_support") in open_keys(s))
    before = state.of(SUBSIDIARY).branch.result.rows[2]["cash"]
    after = gl.apply(state, "decision", clock(), CONFIG, team=gl.GROUP, decision="group_support",
                     option="early", by_control=True)
    assert after.decisions[0].by_control and after.decisions[0].entity == PARENT
    assert [(t.day, t.early) for t in after.result.transfers] == [(2, True)]
    assert after.of(SUBSIDIARY).branch.result.rows[2]["cash"] < before
    # And a decision of the home central bank changes the group's run too.
    opened = step_until(state, clock, lambda s: (PARENT, "ela") in open_keys(s))
    granted = gl.apply(opened, "decision", clock(), CONFIG, team=PARENT, decision="ela",
                       option="grant_full", consulted=["competent_authority"])
    assert granted.of(PARENT).decisions[-1].option == "grant_full"
    expected = direct(GroupChoices(parent=(Choice("ela", "grant_full", granted.of(PARENT).decisions[-1].day),)))
    assert granted.result == expected
    assert granted.of(SUBSIDIARY).branch.result is granted.result.subsidiary
    assert granted.result.transfers != opened.result.transfers


def test_entries_that_cannot_be_applied_are_refused():
    clock = Clock()
    state = gl.start(SETUP, clock(), CONFIG)
    with pytest.raises(live.LiveError, match="not open"):
        gl.apply(state, "decision", clock(), CONFIG, team=gl.GROUP,
                 decision="restrict_transfers", option="ring_fence")
    opened = step_until(state, clock, lambda s: (gl.GROUP, "restrict_transfers") in open_keys(s))
    with pytest.raises(live.LiveError, match="not an option"):
        gl.apply(opened, "decision", clock(), CONFIG, team=gl.GROUP,
                 decision="restrict_transfers", option="nonsense")
    with pytest.raises(live.LiveError, match="recorded for the group"):
        gl.apply(opened, "decision", clock(), CONFIG, team=SUBSIDIARY,
                 decision="restrict_transfers", option="ring_fence")
    with pytest.raises(live.LiveError, match="team"):
        gl.apply(opened, "decision", clock(), CONFIG, decision="ela", option="refuse")
    paused = gl.apply(opened, "pause", clock(), CONFIG)
    assert paused.paused and all(paused.of(role).paused for role in GROUP_ENTITIES)
    with pytest.raises(live.LiveError, match="paused"):
        gl.apply(paused, "step", clock(), CONFIG)
    assert not gl.apply(paused, "resume", clock(), CONFIG).paused
    with pytest.raises(live.LiveError, match="nothing to undo"):
        gl.apply(state, "undo", clock(), CONFIG)


def test_undo_takes_back_a_decision_of_the_group_in_both_countries():
    clock = Clock()
    state = step_until(gl.start(SETUP, clock(), CONFIG), clock,
                       lambda s: (gl.GROUP, "restrict_transfers") in open_keys(s))
    decided = gl.apply(state, "decision", clock(), CONFIG, team=gl.GROUP,
                       decision="restrict_transfers", option="ring_fence")
    undone = gl.apply(decided, "undo", clock(), CONFIG)
    assert undone.decisions == () and undone.result == state.result
    assert undone.of(PARENT).branch.result.failure_day is None
    assert [e.kind for e in undone.entries][-2:] == ["decision", "undo"]  # both stay in the log
    assert (gl.GROUP, "restrict_transfers") in open_keys(undone)


def test_linear_exercise_keeps_to_the_plan_whatever_is_recorded():
    clock = Clock()
    setup = gl.GroupLiveSetup("Linear", "showcase", "fast_run", mode="linear")
    state = step_until(gl.start(setup, clock(), CONFIG), clock,
                       lambda s: (gl.GROUP, "restrict_transfers") in open_keys(s))
    after = gl.apply(state, "decision", clock(), CONFIG, team=gl.GROUP,
                     decision="restrict_transfers", option="ring_fence")
    assert not after.decisions[0].applied and after.result == state.result
    assert not gl.pending_fallbacks(after)  # nothing is left to the teams to pass on


# ------------------------------------------------- a whole exercise, replayed and saved


def test_a_whole_exercise_ends_with_the_groups_outcome_and_replays_the_same(exercise):
    assert exercise.ended and exercise.day == 30
    assert exercise.result.parent.outcome == "payments_default" and exercise.result.parent.failure_day == 5
    assert exercise.result.ring_fence_from == 3 and exercise.result.transfers == ()
    # The parent's run ended on day 5; its country's clock goes on with the group's.
    assert exercise.result.parent.end_day == 5 and exercise.of(PARENT).clock == exercise.clock
    again = gl.replay(exercise.setup, [live.Entry(e.kind, e.real_time, e.data) for e in exercise.entries],
                      CONFIG)
    assert again.clock == exercise.clock and again.result == exercise.result
    assert again.decisions == exercise.decisions
    for role in GROUP_ENTITIES:
        assert [live.inject_key(r.inject) for r in again.of(role).released] == [
            live.inject_key(r.inject) for r in exercise.of(role).released]


def test_saved_exercise_reloads_and_each_team_reads_only_its_own_page(exercise):
    exercise_id, codes = store.new_id(), store.new_group_codes()
    assert set(codes) == set(gl.teams()) | {store.CONTROL} and len(gl.teams()) == 8
    store.save_group(exercise_id, exercise, CONFIG, codes)
    assert store.is_group(exercise_id)
    state, config, loaded_codes = store.load_group(exercise_id, codes[store.CONTROL])
    assert state.clock == exercise.clock and state.result == exercise.result and loaded_codes == codes
    assert store.load_group(exercise_id, "wrong") is None
    assert store.load(exercise_id, codes[store.CONTROL]) is None  # not a single-bank exercise
    for role in GROUP_ENTITIES:
        for actor in PLAYERS:
            team = gl.team_key(role, actor)
            view = store.player_view(exercise_id, team, codes[team])
            assert view["player"].endswith(f"of {GROUP.entity(role).country}")
            mine = [r.inject.title for r in live.released_to(exercise.of(role), actor)]
            assert [i["title"] for i in view["injects"]] == mine
            # Another team's code, or the same actor of the other country, does not open it.
            other_team = gl.team_key(SUBSIDIARY if role == PARENT else PARENT, actor)
            assert store.player_view(exercise_id, team, codes[other_team]) is None
            # Every number on the page is in one of the team's own injects.
            allowed = set()
            for item in live.released_to(exercise.of(role), actor):
                inject = item.inject
                for figure in (*inject.figures, *inject.headline, *inject.notified, *inject.resolution):
                    allowed.update(FIGURE_TOKEN.findall(figure.text))
                    allowed.update(FIGURE_TOKEN.findall(figure.threshold_text))
                allowed.update(FIGURE_TOKEN.findall(inject.text))
            shown = set(FIGURE_TOKEN.findall(json.dumps(
                [{k: v for k, v in i.items() if k != "received"} for i in view["injects"]])))
            assert shown <= allowed, (team, shown - allowed)
    assert store.player_view(exercise_id, "parent.bank_management", "x") is None


# ------------------------------------------------- closing report


def test_group_report_states_decisions_steps_and_counterfactuals(exercise):
    report = gl.group_report(exercise, CONFIG)
    overview = dict(report.overview)
    assert overview["Status"] == "Ended" and "Payments default" in overview[GROUP.parent.name]
    assert overview["Restriction of transfers in effect"] == "from day 3"
    row = report.decisions[0]
    assert row[1] == "Competent authority of Country B" and row[3].startswith("Full ring-fence")
    assert row[4] == "No restriction" and "different from the pre-planned path" in row[6]
    assert float(row[5]) > 0
    # Steps across the border: recorded as passed on by the team, or as a fallback released.
    records = [line[4] for line in report.crossing]
    assert records.count("passed on by the team") == 1
    assert "the control team released the fallback inject" in records
    assert set(records) <= {"passed on by the team", "the control team released the fallback inject",
                            "no record"}
    assert any("Likely to fail at Group A parent bank" in line[1] for line in report.crossing)
    assert all(line[2].endswith(("Country A", "Country B")) for line in report.crossing)
    # One decision of the group changed, all else as it happened: the model's run.
    free = next(c for c in report.counterfactuals if c.change.startswith('"No restriction"'))
    run = direct(GroupChoices())
    assert free.parent == "Still under stress, no default, day 30"
    assert free.lent == pytest.approx(sum(t.amount for t in run.transfers))
    early = next(c for c in report.counterfactuals if "Ask now" in c.change)
    assert early.team == f"Management of {GROUP.parent.name}"
    # Each country's own report has no single-bank counterfactual.
    for role in GROUP_ENTITIES:
        assert report.countries[role].counterfactuals == ()
        assert report.countries[role].bank_name == GROUP.entity(role).name
    # No sentence assesses the teams.
    text = json.dumps([report.overview, report.decisions, report.crossing]).lower()
    for word in ("should have", "failed to", "mistake", "wrong", "too late", "poor"):
        assert word not in text


def test_group_report_export_has_the_groups_document_and_one_per_country(exercise):
    report = gl.group_report(exercise, CONFIG)
    contexts = gl.contexts_of(exercise.setup, CONFIG)
    files = group_report_files(report, {r: contexts[r].script for r in GROUP_ENTITIES}, GROUP, CONFIG.college)
    assert set(files) == {
        "college/SIMULATION_group_closing_report_draft.docx",
        "country_a_parent/SIMULATION_closing_report_draft.docx",
        "country_b_subsidiary/SIMULATION_closing_report_draft.docx",
        "college/SIMULATION_group_report_decisions.csv", "college/SIMULATION_group_report_steps.csv",
        "college/SIMULATION_group_report_joint_decisions.csv"}
    document = Document(io.BytesIO(files["college/SIMULATION_group_closing_report_draft.docx"]))
    text = "\n".join([p.text for p in document.paragraphs]
                     + [c.text for t in document.tables for r in t.rows for c in r.cells])
    for wanted in ("Group closing report (draft)", "SIMULATION", "an assumption of the tool",
                   "Steps across the border", "Full ring-fence, no further lending",
                   "To be completed - not generated"):
        assert wanted in text, wanted
    country = Document(io.BytesIO(files["country_a_parent/SIMULATION_closing_report_draft.docx"]))
    assert any("part of a cross-border group" in p.text for p in country.paragraphs)


# ------------------------------------------------- console, Close page and a team's page


def test_console_runs_a_group_exercise_and_the_close_page_reports_it():
    at = AppTest.from_file(APP_PATH)
    at.session_state["control_unlocked"] = True
    at.switch_page("views/console.py").run(timeout=60)
    assert "Start an exercise on a cross-border group" in [h.value for h in at.subheader]
    at.button(key="glive_start").click().run(timeout=60)
    assert not at.exception and group_console.KEY in at.session_state
    assert "#### Day 0, 08:00" in [m.value for m in at.markdown] and not at.metric
    for _ in range(40):
        if (gl.GROUP, "restrict_transfers") in open_keys(at.session_state[group_console.KEY]):
            break
        at.button(key="glive_step").click().run(timeout=60)
    at.selectbox(key="glive_option_group_restrict_transfers").set_value("ring_fence").run(timeout=60)
    at.button(key="glive_record_group_restrict_transfers").click().run(timeout=60)
    assert not at.exception and not at.error
    state = at.session_state[group_console.KEY]
    assert state.decisions[0].option == "ring_fence"
    assert state.setup.severity == "high" and state.result.parent.failure_day == 9
    exercise_id, codes = at.session_state["group_live_id"], at.session_state["group_live_codes"]
    assert store.is_group(exercise_id)
    assert any(f"player=subsidiary.central_bank&code={codes['subsidiary.central_bank']}" in m.value
               for m in at.markdown)
    assert len(at.get("plotly_chart")) >= 2  # the map and the inject timelines
    at.button(key="glive_undo").click().run(timeout=60)
    assert at.session_state[group_console.KEY].decisions == ()
    at.switch_page("views/close.py").run(timeout=60)
    assert not at.exception
    headings = [m.value for m in at.markdown if m.value.startswith("### ")]
    assert "### Steps across the border" in headings and "### Decisions in Country B" in headings
    # A team's page, opened with its own link.
    page = AppTest.from_file(APP_PATH)
    page.query_params["exercise"], page.query_params["player"] = exercise_id, "subsidiary.competent_authority"
    page.query_params["code"] = codes["subsidiary.competent_authority"]
    page.run(timeout=60)
    assert not page.exception and not page.error
    assert page.title[0].value == "Competent authority of Country B" and not page.button
    # Leave, and reload it with the control team's code.
    at.switch_page("views/console.py").run(timeout=60)
    at.button(key="glive_close").click().run(timeout=60)
    assert group_console.KEY not in at.session_state
    at.selectbox(key="live_reload_id").set_value(exercise_id).run(timeout=60)
    at.text_input(key="live_reload_code").set_value(codes[store.CONTROL]).run(timeout=60)
    at.button(key="live_reload").click().run(timeout=60)
    assert not at.exception and at.session_state[group_console.KEY].clock == state.clock


def test_a_single_bank_exercise_still_starts_from_the_same_page():
    at = AppTest.from_file(APP_PATH)
    at.session_state["control_unlocked"] = True
    at.switch_page("views/console.py").run(timeout=60)
    at.button(key="live_start").click().run(timeout=60)
    assert not at.exception and "live" in at.session_state and group_console.KEY not in at.session_state


# ------------------------------------------------- joint decision of the college


def docx_text(content: bytes) -> str:
    document = Document(io.BytesIO(content))
    return "\n".join([p.text for p in document.paragraphs]
                     + [c.text for t in document.tables for r in t.rows for c in r.cells])


def _college(state, **data):
    entry = {"subject": CONFIG.college.joint_decision.subjects[0],
             "took_part": ["parent.resolution_authority", "subsidiary.resolution_authority"],
             "agreed": True, "note": "Sale of business for the parent", "minutes": 25}
    return gl.apply(state, "college", "2026-01-01T10:30:00", CONFIG, **{**entry, **data})


def test_joint_decision_is_recorded_and_changes_no_figure():
    state = gl.start(SETUP, "2026-01-01T09:00:00", CONFIG)
    after = _college(state)
    record = after.college[0]
    assert (record.agreed, record.minutes, record.real_time) == (True, 25.0, "2026-01-01T10:30:00")
    assert (record.day, record.time) == state.clock
    assert after.result == state.result and after.states == state.states
    again = _college(after, agreed=False, minutes=None, note="")
    assert [c.agreed for c in again.college] == [True, False] and again.college[1].minutes is None
    # Undo, replay and a saved file keep the record.
    assert gl.apply(again, "undo", "2026-01-01T10:31:00", CONFIG).college == after.college
    loaded, _ = gl.from_saved(gl.to_saved(again, CONFIG))
    assert loaded.college == again.college


@pytest.mark.parametrize("data", [
    {"subject": "Something else"},
    {"took_part": []},
    {"took_part": ["parent.resolution_authority", "parent.competent_authority"]},  # one country
    {"took_part": ["parent.resolution_authority", "subsidiary.nobody"]},
    {"agreed": "yes"},
    {"minutes": -5},
])
def test_joint_decision_refuses_an_incomplete_record(data):
    state = gl.start(SETUP, "2026-01-01T09:00:00", CONFIG)
    with pytest.raises(live.LiveError):
        _college(state, **data)


def test_joint_decision_is_in_the_closing_report_and_its_files():
    state = _college(gl.start(SETUP, "2026-01-01T09:00:00", CONFIG))
    report = gl.group_report(state, CONFIG)
    row = report.college[0]
    assert row[2] == "Agreed" and "Resolution authority of Country A" in row[3]
    # The two competent authorities were expected and did not take part.
    assert "Competent authority of Country A" in row[4] and "Competent authority of Country B" in row[4]
    assert ("Joint decisions of the college recorded", "1, of which agreed 1") in report.overview
    contexts = gl.contexts_of(SETUP, CONFIG)
    scripts = {role: contexts[role].script for role in contexts}
    files = group_report_files(report, scripts, GROUP, CONFIG.college)
    data = files["college/SIMULATION_group_report_joint_decisions.csv"].decode()
    assert "Sale of business for the parent" in data
    text = docx_text(files["college/SIMULATION_group_closing_report_draft.docx"])
    assert "Joint decisions of the college" in text and "Sale of business for the parent" in text


def test_console_records_a_joint_decision():
    at = AppTest.from_file(APP_PATH)
    at.session_state["control_unlocked"] = True
    at.switch_page("views/console.py").run(timeout=60)
    at.button(key="glive_start").click().run(timeout=60)
    at.text_input(key="glive_college_note").set_value("Agreed to wait one day").run(timeout=60)
    at.button(key="glive_college_record").click().run(timeout=60)
    assert not at.exception and not at.error
    state = at.session_state[group_console.KEY]
    assert len(state.college) == 1 and state.college[0].note == "Agreed to wait one day"
    assert len(state.college[0].took_part) == 4 and state.college[0].minutes is None
