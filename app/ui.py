"""Streamlit pieces of the visual system (dev/SPEC_UI.md, UI-1): the header with the
phases as a stepper, KPI cards, stage badges and tables. Colours come from app/theme.py."""

from __future__ import annotations

from html import escape
from typing import Optional, Sequence

import pandas as pd
import streamlit as st

from app import theme

# The Handbook's phases, in order: (title, page file), then the page outside the phases.
PHASES = (
    ("Design", "views/design.py"),
    ("Prepare", "views/prepare.py"),
    ("Deliver", "views/console.py"),
    ("Close", "views/close.py"),
)
OTHER_PAGE = ("Assumptions and limits", "views/assumptions.py")
HOME_PAGE = ("Home", "views/home.py")
# The page the app opens on: "home", or "design" (the tests open the app on Design).
LANDING = "home"
TOOL_NAME = "Crisis Scenario Engine"
SIMULATION = "SIMULATION"


def inject_css() -> None:
    st.html(f"<style>{theme.css()}</style>")


def header(current: Optional[str], exercise: str, navigation: bool = True) -> None:
    """The slim header of every page: the tool and the exercise, the SIMULATION label, and
    the Handbook's phases as a stepper that is also the navigation. `current` is the title
    of the phase shown (None on a page outside the phases)."""
    from app import tour  # here: the tour's pages call this module

    inject_css()
    if navigation:
        tour.follow()
    with st.container(key="cse_header"):
        if not navigation:
            brand, label = st.columns([6, 1.6], vertical_alignment="center")
        else:
            brand, home, *steps, other, label = st.columns(
                [3.0, 0.9, 1, 1.1, 1.1, 1, 2.2, 1.4], vertical_alignment="center")
            with home.container(key="cse_step_current" if current == HOME_PAGE[0] else "cse_step_home"):
                st.page_link(HOME_PAGE[1], label=HOME_PAGE[0])
            for number, (column, (title, page)) in enumerate(zip(steps, PHASES), start=1):
                key = "cse_step_current" if title == current else f"cse_step_{number}"
                with column.container(key=key):
                    st.page_link(page, label=f"{number}  {title}")
            with other.container(key="cse_step_current" if current == OTHER_PAGE[0] else "cse_step_other"):
                st.page_link(OTHER_PAGE[1], label=OTHER_PAGE[0])
        brand.html(
            f'<div class="cse-brand"><div class="cse-tool">{escape(TOOL_NAME)}</div>'
            f'<div class="cse-exercise">{escape(exercise)}</div></div>'
        )
        label.html(f'<span class="cse-simulation">{SIMULATION}</span>')
    if navigation:
        tour.bar()


def cards(items: Sequence[tuple], columns: Optional[int] = None) -> None:
    """A row of KPI cards. Each item: (label, value) or (label, value, note) or
    (label, value, note, stage): with a stage, the card carries the stage's colour and the
    value is shown as the stage's badge. Values are text here; figures use st.metric."""
    parts = []
    for item in items:
        label, value, note, stage = (*item, None, None)[:4]
        style, shown, kind = "", escape(str(value)), "cse-value cse-text"
        if stage is not None:
            style = f' style="border-top-color:{theme.stage_color(stage)}"'
            shown = theme.stage_badge(stage, escape(str(value)))
        note_html = f'<div class="cse-note">{escape(str(note))}</div>' if note else ""
        parts.append(
            f'<div class="cse-card"{style}><div class="cse-label">{escape(str(label))}</div>'
            f'<div class="{kind}">{shown}</div>{note_html}</div>'
        )
    grid = f' style="grid-template-columns: repeat({columns}, 1fr)"' if columns else ""
    st.html(f'<div class="cse-cards"{grid}>{"".join(parts)}</div>')


def stage_legend(stages: Sequence[str] = theme.STAGES, lead: str = "Stage of the bank:") -> None:
    """The stage scale as a row of badges: the key to the colours of charts and tables."""
    st.html(
        f'<div class="cse-legend"><span>{escape(lead)}</span>'
        + "".join(theme.stage_badge(stage) for stage in stages) + "</div>"
    )


def stage_text(status: str) -> str:
    """A day's status as shown to the user, with the stage's shape in front."""
    stage = theme.stage_of(status)
    words = {"failed": "payments default"}.get(status, status.replace("_", " "))
    return f"{theme.STAGE_ICONS[stage]} {words}"


def table(frame: pd.DataFrame, stage_column: Optional[str] = None, stages: Optional[Sequence[str]] = None,
          column_config: Optional[dict] = None, **kwargs) -> None:
    """A table in the house style: figures right-aligned with at most two decimals. With
    `stage_column`, that column's cells take the colour of the stage in `stages` (one
    engine status per row)."""
    config = {}
    for column in frame.columns:
        if pd.api.types.is_float_dtype(frame[column]):
            config[column] = st.column_config.NumberColumn(format="%.2f")
    config.update(column_config or {})
    shown = frame
    if stage_column is not None and stages is not None:
        colors = [theme.stage_color(stage) for stage in stages]

        def paint(column: pd.Series) -> list[str]:
            if column.name != stage_column:
                return [""] * len(column)
            return [f"background-color: {color}; color: {theme.text_on(color)}" for color in colors]

        shown = frame.style.apply(paint, axis=0)
    st.dataframe(shown, hide_index=True, width="stretch", column_config=config, **kwargs)


def chart(fig, key: Optional[str] = None, container=None) -> None:
    """Show a figure of app/charts.py. `theme=None` keeps the app's own Plotly template:
    Streamlit's chart theme would replace its colours and typefaces."""
    (container or st).plotly_chart(
        fig, width="stretch", theme=None, key=key, config={"displayModeBar": False},
    )
