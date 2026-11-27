"""The console's form for a reply drafted by AI (SPEC_V3 phase K): request, draft, check,
approval. Shown only when config/ai.yaml has `enabled: true`."""

from __future__ import annotations

import streamlit as st

from app import ai
from engine import live, replies
from engine.schema import PLAYERS, ROLES

KEY = "ai_draft"  # the session holds (grounding, draft) here until it is sent or discarded


def _make(state, context, config) -> None:
    session = st.session_state
    session["ai_error"] = ""
    try:
        material = replies.grounding(state, context, session["ai_stakeholder"], session["ai_asked_by"],
                                     session.get("ai_question", ""))
        draft = ai.draft(material, config)
    except (live.LiveError, ai.AiUnavailable) as error:
        session["ai_error"] = str(error)
        session.pop(KEY, None)
        return
    session[KEY] = (material, draft)
    session["ai_text"] = draft.text


def _discard() -> None:
    for key in (KEY, "ai_text"):
        st.session_state.pop(key, None)


def _send(context, config, act) -> None:
    session = st.session_state
    material, draft = session[KEY]
    text = session.get("ai_text", "").strip()
    if not text or replies.offending(text, material, config.ai):
        return  # the button is disabled in that case; a stale click changes nothing
    asked = next(player for player in PLAYERS if context.script.actors[player] == material.asked_by)
    act("adhoc", recipients=[asked], title=replies.title_of(material),
        sender=replies.sender_of(material, context), text=text,
        ai=replies.record(material, draft, text))
    _discard()
    session["ai_question"] = ""


def section(state, context, config, act) -> None:
    """Request, draft and approval. `act` adds an entry to the exercise's log."""
    settings, session = config.ai, st.session_state
    with st.expander("Reply to a player's request, drafted by AI"):
        st.caption(
            "A player team asks a simulated stakeholder something the script did not foresee. "
            "The model is given only that stakeholder's role card and the figures it holds now; "
            "a draft with any other number cannot be sent; nothing reaches a player until you "
            "send it. Do not type real names or real data in the request. "
            + ("Provider: a fixed reply, no call is made." if settings.provider == "fixed"
               else f"Provider: {settings.model}; each draft is one billed call."))
        left, right = st.columns(2)
        left.selectbox("Asked by", list(PLAYERS), key="ai_asked_by",
                       format_func=lambda player: context.script.actors[player])
        right.selectbox("Asked to", list(ROLES), key="ai_stakeholder",
                        format_func=lambda role: context.script.roles[role].name)
        st.text_area("The request, in the players' words", key="ai_question", height=80)
        st.button("Draft a reply", key="ai_make", on_click=_make, args=(state, context, config),
                  disabled=state.paused or state.ended or not state.started)
        if session.get("ai_error"):
            st.error(session["ai_error"])
        if KEY not in session:
            return
        material, draft = session[KEY]
        st.markdown(f"**Given to the model** (figures at the close of day {material.as_of_day})")
        st.markdown("\n".join(f"- {line}" for line in material.figures) or "- no figures")
        if draft.note:
            st.warning(draft.note)
        st.text_area("Draft: read it, change it if needed", key="ai_text", height=180)
        wrong = replies.offending(session.get("ai_text", ""), material, settings)
        if wrong:
            st.error("This text cannot be sent: it contains numbers that were not given to the "
                     f"model: {', '.join(wrong)}.")
        else:
            st.success("Every number in this text is one of the figures given to the model.")
        st.caption(
            f"Drafted by: {draft.model}."
            + (f" {draft.input_tokens} tokens in, {draft.output_tokens} out, "
               f"USD {draft.cost:.4f} at the configured prices." if draft.input_tokens else ""))
        send, discard, _ = st.columns([2, 1.2, 5])
        send.button(f"Send to the {material.asked_by.lower()}", key="ai_send", type="primary",
                    on_click=_send, args=(context, config, act),
                    disabled=bool(wrong) or not session.get("ai_text", "").strip() or state.paused or state.ended)
        discard.button("Discard", key="ai_discard", on_click=_discard)
