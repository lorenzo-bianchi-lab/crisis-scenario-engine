"""SPEC_V3 phase K: replies to player requests drafted by a general-purpose AI, grounded in
the figures of the stakeholder asked, approved by the control team, off by default. No test
reaches the network: the call sits behind app.ai.CALL."""

import io
from pathlib import Path

import pytest
from docx import Document
from streamlit.testing.v1 import AppTest

from app import ai
from engine import live, replies
from engine.config import load_config
from engine.export import closing_report_docx
from engine.report import live_report
from engine.schema import PLAYERS, ROLES

CONFIG = load_config()
ROOT = Path(__file__).resolve().parent.parent
APP_PATH = str(ROOT / "app" / "main.py")
ON = CONFIG.model_copy(update={"ai": CONFIG.ai.model_copy(update={"enabled": True})})
LIVE = CONFIG.model_copy(update={"ai": CONFIG.ai.model_copy(update={"enabled": True, "provider": "anthropic"})})
QUESTION = "What is your cash position this evening?"


@pytest.fixture(scope="module")
def moment():
    return ai.showcase_at_close(CONFIG, 6)


def ground(moment, stakeholder="bank_management", asked_by="competent_authority", question=QUESTION):
    state, context = moment
    return replies.grounding(state, context, stakeholder, asked_by, question)


# ------------------------------------------------- off by default


def test_shipped_off_and_then_no_call_can_be_made(moment, monkeypatch):
    assert CONFIG.ai.enabled is False and CONFIG.ai.provider == "fixed"
    assert not ai.enabled(CONFIG)
    monkeypatch.setattr(ai, "CALL", lambda *a: pytest.fail("a call was made with the feature off"))
    with pytest.raises(ai.AiUnavailable):
        ai.draft(ground(moment), CONFIG)


def test_console_shows_no_ai_form_when_off():
    at = AppTest.from_file(APP_PATH)
    at.session_state["control_unlocked"] = True
    at.switch_page("views/console.py").run(timeout=60)
    at.button(key="live_start").click().run(timeout=60)
    assert not at.exception
    assert not [e for e in at.expander if "drafted by AI" in e.label]
    assert not [b for b in at.button if b.key == "ai_make"]


def test_the_model_client_is_imported_only_inside_the_call():
    for path in [*(ROOT / "app").rglob("*.py"), *(ROOT / "engine").rglob("*.py")]:
        for line in path.read_text().splitlines():
            if line.startswith(("import anthropic", "from anthropic")):
                pytest.fail(f"{path.name} imports the model client at module level")
    assert "import anthropic" in (ROOT / "app" / "ai.py").read_text()
    assert "anthropic" not in "".join(p.read_text() for p in (ROOT / "engine").rglob("*.py")
                                      if p.name != "schema.py")


# ------------------------------------------------- grounding


def test_grounding_holds_only_the_stakeholders_own_figures_at_the_last_close(moment):
    state, context = moment
    material = ground(moment)
    assert (material.day, material.as_of_day) == (6, 6) and material.stakeholder_name == "Bank management"
    wanted = context.decisions.information["bank_management"].figures
    assert len(material.figures) == len(wanted)
    assert all(line.endswith("(at the close of day 6)") for line in material.figures)
    row = state.branch.result.rows[6]
    assert f"{row['liquidity_buffer']:.2f}" in " ".join(material.figures)
    # Nothing of a later day: the path's end is not in the material.
    system, user = replies.prompt(material, ON.ai)
    later = state.branch.result.rows[-1]
    assert f"{later['deposits_total']:.2f}" not in user
    assert "bail-in" not in user.lower() and "liquidation" not in user.lower()


@pytest.mark.parametrize("stakeholder", ROLES)
def test_every_role_card_can_be_asked(moment, stakeholder):
    material = ground(moment, stakeholder)
    assert material.says and material.knows and material.wants
    if stakeholder == "finance_minister":
        assert material.figures == ()
    if stakeholder in ("independent_valuer", "potential_buyers"):
        assert material.figures == ()  # no valuation and no price before FoLtF is in effect
    if stakeholder == "media":
        assert material.figures and all(line.startswith("Day ") for line in material.figures)
        assert all(int(line.split()[1].rstrip(":")) <= 6 for line in material.figures)


def test_valuer_and_buyer_hold_figures_once_foltf_is_in_effect():
    from engine.live_run import scripted_showcase
    state, context = scripted_showcase(CONFIG)  # played to its end, FoLtF declared
    assert replies.grounding(state, context, "independent_valuer", "resolution_authority", "Your valuation?").figures
    price = replies.grounding(state, context, "potential_buyers", "resolution_authority", "Your price?").figures
    assert len(price) == 1 and "non-binding" in price[0]


@pytest.mark.parametrize("stakeholder, asked_by, question", [
    ("competent_authority", "central_bank", QUESTION),  # not a role card
    ("media", "bank_management", QUESTION),  # not a player team
    ("media", "central_bank", "   "),
])
def test_grounding_refuses_what_is_not_a_request(moment, stakeholder, asked_by, question):
    with pytest.raises(live.LiveError):
        ground(moment, stakeholder, asked_by, question)


def test_prompt_is_the_role_card_the_figures_and_the_question_only(moment):
    material = ground(moment)
    system, user = replies.prompt(material, ON.ai)
    assert "not a real event" in system and "Bank management" in system
    for part in (*material.knows, *material.wants, material.says, *material.figures, QUESTION,
                 "Request from the Competent authority"):
        assert part in user
    # Each line of the user message is one of those, a heading or the clock.
    known = {*material.figures, *material.knows, *material.wants}
    for line in user.splitlines():
        body = line.removeprefix("- ")
        assert (not line or body in known or line.endswith(":") or line.startswith(
            ("Simulated time", "Stage of", "What you say", "Request from", "Write your reply"))), line


# ------------------------------------------------- the check


def test_a_draft_with_a_number_not_supplied_is_refused(moment):
    material = ground(moment)
    figure = material.figures[4]  # the liquidity buffer line
    good = replies.checked(f"We hold this: {figure}.", material, ON.ai)
    assert good.ok and good.offending == ()
    bad = replies.checked("Our buffer is 99.99 EUR bn and will last 14 days.", material, ON.ai)
    assert not bad.ok and bad.offending == ("99.99", "14")
    assert not replies.checked("   ", material, ON.ai).ok


def test_the_fixed_reply_passes_the_check_for_every_role(moment):
    for stakeholder in ROLES:
        draft = ai.draft(ground(moment, stakeholder), ON)
        assert draft.ok and draft.model == "fixed reply" and draft.cost == 0.0, stakeholder


def test_a_real_call_goes_through_one_function_and_its_cost_is_computed(moment, monkeypatch):
    sent = {}

    def fake(system, user, settings):
        sent.update(system=system, user=user, model=settings.model)
        return ai.Raw("I do not have that figure.", "model-x", 1000, 200)

    monkeypatch.setattr(ai, "CALL", fake)
    material = ground(moment)
    draft = ai.draft(material, LIVE)
    assert (sent["system"], sent["user"]) == replies.prompt(material, LIVE.ai)
    assert draft.ok and draft.model == "model-x"
    assert draft.cost == pytest.approx((1000 * 4.0 + 200 * 20.0) / 1e6)
    monkeypatch.setattr(ai, "CALL", lambda *a: ai.Raw("We will last 45 days.", "model-x", 10, 10))
    assert ai.draft(material, LIVE).offending == ("45",)


def test_without_a_key_the_call_says_so_and_sends_nothing(moment, monkeypatch):
    monkeypatch.delenv(ai.KEY_VARIABLE, raising=False)
    with pytest.raises(ai.AiUnavailable, match="not set"):
        ai.draft(ground(moment), LIVE)


# ------------------------------------------------- sending, log and report


def test_an_approved_reply_is_an_ad_hoc_inject_logged_as_drafted_by_ai(moment):
    state, context = moment
    material = ground(moment)
    draft = ai.draft(material, ON)
    after = live.apply(state, "adhoc", "2028-03-01T10:00:00", context,
                       recipients=["competent_authority"], title=replies.title_of(material),
                       sender=replies.sender_of(material, context), text=draft.text,
                       ai=replies.record(material, draft, draft.text))
    released = after.released[-1]
    assert released.inject.recipient == "competent_authority" and released.inject.text == draft.text
    assert released.note == "ad hoc, drafted by AI, approved by the control team"
    assert [p for p in PLAYERS if live.released_to(after, p) != live.released_to(state, p)] == ["competent_authority"]
    report = live_report(after, context)
    assert report.ai_replies == ((
        "Day 6, " + after.time, "Competent authority", replies.sender_of(material, context),
        QUESTION, "fixed reply", "sent as drafted"),)
    assert ("Replies drafted by AI, each approved by the control team", "1") in report.conduct
    document = Document(io.BytesIO(closing_report_docx(report, context.script)))
    text = "\n".join(p.text for p in document.paragraphs)
    assert "Replies drafted by AI" in text
    # Taken back: it leaves the report. Without any, the report does not mention AI.
    undone = live.apply(after, "undo", "2028-03-01T10:01:00", context)
    assert live_report(undone, context).ai_replies == ()
    assert not any("AI" in label for label, _ in live_report(state, context).conduct)


def test_console_drafts_checks_and_sends_only_on_approval(monkeypatch):
    import streamlit as st
    import engine.config
    # The pages load the configuration themselves, once: give them one with the feature on.
    monkeypatch.setattr(engine.config, "load_config", lambda *a, **k: ON)
    st.cache_resource.clear()
    at = AppTest.from_file(APP_PATH)
    at.session_state["control_unlocked"] = True
    at.switch_page("views/console.py").run(timeout=60)
    at.button(key="live_start").click().run(timeout=60)
    for _ in range(6):
        at.button(key="live_step").click().run(timeout=60)
    assert [e for e in at.expander if "drafted by AI" in e.label]
    before = len(at.session_state["live"].released)
    at.text_area(key="ai_question").set_value(QUESTION).run(timeout=60)
    at.button(key="ai_make").click().run(timeout=60)
    assert not at.exception and "ai_draft" in at.session_state
    assert len(at.session_state["live"].released) == before  # a draft sends nothing
    at.text_area(key="ai_text").set_value("We will last 45 days.").run(timeout=60)
    assert at.button(key="ai_send").disabled and any("45" in e.value for e in at.error)
    at.text_area(key="ai_text").set_value("I do not have that figure at hand.").run(timeout=60)
    assert not at.button(key="ai_send").disabled
    at.button(key="ai_send").click().run(timeout=60)
    live_state = at.session_state["live"]
    assert len(live_state.released) == before + 1
    assert live_state.released[-1].note == "ad hoc, drafted by AI, approved by the control team"
    assert live_state.entries[-1].data["ai"]["edited"] is True and "ai_draft" not in at.session_state
    st.cache_resource.clear()
