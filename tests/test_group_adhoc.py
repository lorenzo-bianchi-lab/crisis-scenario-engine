"""Ad hoc injects, and replies drafted by AI, on the consoles of a cross-border group and of
a system: sent to teams of one country (one bank's file), on the same terms as for a
single bank."""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from app import ai, group_console
from engine import group_live as gl
from engine import live, replies
from engine.config import load_config
from engine.group import PARENT, SUBSIDIARY
from engine.schema import GROUP_ENTITIES, PLAYERS

CONFIG = load_config()
ON = CONFIG.model_copy(update={"ai": CONFIG.ai.model_copy(update={"enabled": True})})
APP_PATH = str(Path(__file__).resolve().parent.parent / "app" / "main.py")
SHOW, SYSTEM = CONFIG.college.showcase, CONFIG.systems.showcase
GROUP_SETUP = gl.GroupLiveSetup("Group", SHOW.group, SHOW.scenario, SHOW.stressed, severity=SHOW.severity)
SYSTEM_SETUP = gl.GroupLiveSetup("System", SYSTEM.pair, SYSTEM.scenario)


def played(setup, steps=12):
    state = gl.start(setup, "2028-03-01T09:00:00", CONFIG)
    for _ in range(steps):
        state = gl.apply(state, "step", "2028-03-01T09:05:00", CONFIG)
    return state


@pytest.fixture(scope="module")
def group():
    return played(GROUP_SETUP)


@pytest.fixture(scope="module")
def system_run():
    return played(SYSTEM_SETUP)


def adhoc(state, side, **data):
    entry = {"recipients": ["central_bank"], "title": "Call from a depositor", "sender": "A depositor",
             "text": "Is my money safe?"}
    return gl.apply(state, "adhoc", "2028-03-01T10:00:00", CONFIG, team=side, **{**entry, **data})


def test_an_ad_hoc_inject_reaches_only_the_teams_of_its_country(group):
    after = adhoc(group, SUBSIDIARY)
    assert after.result == group.result  # no figure changes
    before, views = gl.player_views(group, CONFIG), gl.player_views(after, CONFIG)
    changed = [team for team in views if views[team]["injects"] != before[team]["injects"]]
    assert changed == ["subsidiary.central_bank"]
    assert views["subsidiary.central_bank"]["injects"][-1]["title"] == "Call from a depositor"
    assert gl.apply(after, "undo", "2028-03-01T10:01:00", CONFIG).states == group.states
    loaded, _ = gl.from_saved(gl.to_saved(after, CONFIG))
    assert gl.player_views(loaded, CONFIG) == views


@pytest.mark.parametrize("data", [{"team": "group"}, {"recipients": []}, {"recipients": ["bank_management"]},
                                  {"title": ""}])
def test_an_incomplete_ad_hoc_inject_is_refused(group, data):
    side = data.pop("team", PARENT)
    with pytest.raises(live.LiveError):
        adhoc(group, side, **data)


def test_in_a_system_the_team_reads_it_on_its_one_page_marked_with_the_bank(system_run):
    after = adhoc(system_run, SUBSIDIARY)
    page = gl.player_views(after, CONFIG)["both.central_bank"]
    assert page["injects"][-1]["title"] == "Online bank Y: Call from a depositor"
    others = gl.player_views(after, CONFIG)
    assert all(not any("Call from a depositor" in i["title"] for i in others[team]["injects"])
               for team in others if team != "both.central_bank")


@pytest.mark.parametrize("setup, side", [(GROUP_SETUP, PARENT), (SYSTEM_SETUP, SUBSIDIARY)])
def test_a_reply_drafted_by_ai_uses_that_entitys_figures_and_is_logged(setup, side):
    state = played(setup)
    contexts = gl.contexts_of(setup, CONFIG)
    entity = state.of(side)
    material = replies.grounding(entity, contexts[side], "bank_management", "competent_authority",
                                 "What is your liquidity buffer?")
    as_of = material.as_of_day
    buffer = f"{state.result.of(side).rows[as_of]['liquidity_buffer']:.2f}"
    other = f"{state.result.of(PARENT if side == SUBSIDIARY else SUBSIDIARY).rows[as_of]['liquidity_buffer']:.2f}"
    held = " ".join(material.figures)
    assert buffer in held and (other == buffer or other not in held)
    draft = ai.draft(material, ON)
    assert draft.ok
    assert replies.offending(f"The other bank holds {float(other) + 123.45:.2f}.", material, ON.ai)
    after = gl.apply(state, "adhoc", "2028-03-01T10:00:00", CONFIG, team=side,
                     recipients=["competent_authority"], title=replies.title_of(material),
                     sender=replies.sender_of(material, contexts[side]), text=draft.text,
                     ai=replies.record(material, draft, draft.text))
    assert after.of(side).released[-1].note == "ad hoc, drafted by AI, approved by the control team"
    report = gl.group_report(after, CONFIG)
    assert len(report.countries[side].ai_replies) == 1
    assert report.countries[PARENT if side == SUBSIDIARY else SUBSIDIARY].ai_replies == ()


def console(start_key: str):
    at = AppTest.from_file(APP_PATH)
    at.session_state["control_unlocked"] = True
    at.switch_page("views/console.py").run(timeout=60)
    at.button(key=start_key).click().run(timeout=60)
    for _ in range(4):
        at.button(key="glive_step").click().run(timeout=60)
    return at


@pytest.mark.parametrize("start_key", ["glive_start", "slive_start"])
def test_console_sends_an_ad_hoc_inject_and_shows_no_ai_form_when_off(start_key):
    at = console(start_key)
    assert not [b for b in at.button if b.key == "ai_make"]
    before = sum(len(at.session_state[group_console.KEY].of(r).released) for r in GROUP_ENTITIES)
    at.multiselect(key="glive_adhoc_to").set_value(["resolution_authority"]).run(timeout=60)
    at.text_input(key="glive_adhoc_title").set_value("Question from a journalist").run(timeout=60)
    at.text_area(key="glive_adhoc_text").set_value("Is the bank about to be closed?").run(timeout=60)
    at.button(key="glive_adhoc_send").click().run(timeout=60)
    assert not at.exception and not at.error
    state = at.session_state[group_console.KEY]
    assert sum(len(state.of(r).released) for r in GROUP_ENTITIES) == before + 1
    assert state.of(PARENT).released[-1].inject.title == "Question from a journalist"


def test_console_of_a_group_drafts_a_reply_when_the_switch_is_on(monkeypatch):
    import engine.config
    monkeypatch.setattr(engine.config, "load_config", lambda *a, **k: ON)
    st.cache_resource.clear()
    at = console("glive_start")
    at.radio(key="glive_ai_side").set_value(SUBSIDIARY).run(timeout=60)
    at.text_area(key="ai_question").set_value("What is your liquidity buffer?").run(timeout=60)
    at.button(key="ai_make").click().run(timeout=60)
    assert not at.exception and "ai_draft" in at.session_state
    material, _ = at.session_state["ai_draft"]
    assert material.bank_name == "Group A subsidiary"
    at.button(key="ai_send").click().run(timeout=60)
    state = at.session_state[group_console.KEY]
    assert state.of(SUBSIDIARY).released[-1].note == "ad hoc, drafted by AI, approved by the control team"
    assert state.of(SUBSIDIARY).released[-1].inject.recipient == "competent_authority"
    st.cache_resource.clear()
