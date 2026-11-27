"""Assumptions and limits page: every parameter with its value and source (spec section
4.5), and what the tool does not cover (docs/LIMITS.md)."""

from pathlib import Path

import pandas as pd
import streamlit as st

from app import theme, ui
from app.design import current_design

from app.assumptions import (
    FLAG,
    assumption_counts,
    bank_table,
    decision_table,
    group_table,
    information_table,
    model_table,
    scenario_table,
)
from engine import legal as legal_refs
from engine.config import load_config


@st.cache_resource
def get_config():
    return load_config()


def show(table):
    """Show a table with the ASSUMPTION rows highlighted as well as labelled."""
    styled = table.style.apply(
        lambda row: [
            f"background-color: {theme.rgba(theme.palette().stages['recovery'], 0.28)}"
            if row["Status"] == FLAG else ""
        ] * len(row),
        axis=1,
    ).format(
        lambda value: f"{value:g}",
        subset=[c for c in ("Value", "Low", "High") if c in table.columns],
        na_rep="",
    )
    st.dataframe(styled, hide_index=True, width="stretch")


config = get_config()
model, scenarios, banks = model_table(config), scenario_table(config), bank_table(config)
decisions = decision_table(config)
information = information_table(config)
groups = group_table(config)
flagged, total = assumption_counts(model, scenarios, banks, decisions, information, groups)

_design = current_design(config)
ui.header(ui.OTHER_PAGE[0], f"{_design.bank.name} \u00b7 {_design.scenario.name}")
st.title("Assumptions and limits")
assumptions_tab, limits_tab, legal_tab = st.tabs(["Assumptions", "Limits", "Legal references"])
with legal_tab:
    legal = config.script.legal
    if legal is None:
        st.caption("No legal references are configured.")
    else:
        st.info(legal.note)
        st.caption(
            "Where a concept of the tool sits in EU law, from config/legal.yaml (source: "
            "docs/LEGAL_REFERENCES.md). Not legal advice and not a statement of any authority's "
            "duties. Shown in the app and the documents as a small \"Reference:\" line "
            + ("(switched on)." if legal.show else "(switched off in config/legal.yaml)."))
        for act, text in legal.acts.items():
            st.markdown(f"**{act}**: {text}")
        st.dataframe(pd.DataFrame(legal_refs.table(legal), columns=["Concept in the tool", "Reference", "Status"]),
                     hide_index=True, width="stretch")
        for status, text in legal.statuses.items():
            st.caption(f"Status {status}: {text}")
with limits_tab:
    limits = Path(__file__).resolve().parents[2] / "docs" / "LIMITS.md"
    # The page already has a title: the file's own first heading is left out.
    st.markdown(limits.read_text(encoding="utf-8").split("\n", 1)[1])
with assumptions_tab:
    st.warning(
        f"{flagged} of the {total} values on this page are assumptions: they have no verifiable "
        "source yet. They are labelled ASSUMPTION and highlighted. Results must not be read as "
        "calibrated estimates."
    )
    st.caption(
        "Sampled = drawn at random between Low and High in each Monte Carlo run "
        "(triangular distribution peaking at Value, each parameter independently)."
    )

    st.subheader("Model parameters")
    show(model)

    st.subheader("Scenarios")
    show(scenarios)

    st.subheader("Decisions")
    st.caption("Timing rule and the effect of each decision option.")
    show(decisions)

    st.subheader("Information sets")
    st.caption(
        "What each actor sees in the exercise script and in the decision log, and how old its "
        "figures are. They do not change the simulation."
    )
    st.dataframe(information, hide_index=True, width="stretch")

    st.subheader("Cross-border groups, the college and system-wide stress")
    st.caption(
        "How liquidity moves inside a group, what the host authority can restrict, and how "
        "late information crosses the border. The college itself is an assumption of the tool: "
        "the Handbook does not define it. For two unrelated banks of a system the only link "
        "is confidence, at a strength computed from the similarity parameters above."
    )
    show(groups)

    st.subheader("Bank archetypes")
    st.caption("Fictitious opening balance sheets.")
    show(banks)
