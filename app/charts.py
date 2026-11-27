"""Plotly figures for the app. No Streamlit imports, so the charts can be tested alone.

Every figure is built here and leaves through `theme.apply`, which gives it the app's one
Plotly template (dev/SPEC_UI.md). Colours and typefaces come from app/theme.py only.
"""

from __future__ import annotations

from typing import Optional, Sequence

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from app import theme
from engine.contagion import ContagionResult
from engine.decisions import OUTCOME_LABELS, Scorecard
from engine.model import SimulationResult
from engine.resolution import ResolutionSummary
from engine.schema import WEEKDAYS

# Events marked on the timeline: kind -> (label, stage of the scale that gives its colour).
TRIGGERS = {
    "recovery": ("Recovery", "recovery"),
    "early_intervention": ("Early intervention", "early_intervention"),
    "likely_to_fail": ("Likely to fail", "likely_to_fail"),
    "failure": ("Payments default", "default"),
    "foltf": ("FoLtF declared", "foltf"),
    "resolution": ("Run ends", "foltf"),
}
# Height of each trigger label above the plot (paper coordinates), staggered against overlap.
_LABEL_Y = {
    "recovery": 1.012,
    "early_intervention": 1.052,
    "likely_to_fail": 1.092,
    "failure": 1.132,
    "foltf": 1.132,
    "resolution": 1.172,
}
BAND_NAME = "5th to 95th percentile of the sensitivity runs (assumed ranges, not probabilities)"
# Share of the height of the large chart and of the two small ones.
_HERO_HEIGHTS = [0.56, 0.22, 0.22]


def _panels(unit: str) -> list[tuple[str, str, float, str]]:
    """The three panels of a timeline: title, column, display factor, hover format."""
    return [
        (f"Liquidity buffer ({unit})", "liquidity_buffer", 1.0, "%{y:.2f}"),
        (f"Deposits ({unit})", "deposits_total", 1.0, "%{y:.2f}"),
        ("CET1 ratio (%)", "cet1_ratio", 100.0, "%{y:.1f}%"),
    ]


def _three_panels(unit: str) -> go.Figure:
    """The frame shared by the timeline and the branch comparison: one large chart and two
    small ones on the same day axis, with their titles on the left."""
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.07,
                        row_heights=_HERO_HEIGHTS)
    for row, (title, _, _, _) in enumerate(_panels(unit), start=1):
        domain = fig.layout[f"yaxis{'' if row == 1 else row}"].domain
        fig.add_annotation(
            x=1, y=domain[1], xref="paper", yref="paper", text=f"<b>{title}</b>",
            showarrow=False, xanchor="right", yanchor="bottom", yshift=2, xshift=-4,
            font=dict(size=16 if row == 1 else 14),
        )
    return fig


def weekday_name(start_weekday: str, day: int) -> str:
    return WEEKDAYS[(WEEKDAYS.index(start_weekday) + day - 1) % 7]


def add_calendar(fig: go.Figure, start_weekday: Optional[str], horizon: int, weekends: bool) -> None:
    """Weekday labels on the day axis and, if weekend rules apply, shaded weekend days."""
    if start_weekday is None:
        fig.update_xaxes(range=[0, horizon], dtick=5)
        return
    # One tick per week (every Monday) plus day 1, each labelled with its weekday.
    ticks = sorted({1} | {day for day in range(1, horizon + 1)
                          if weekday_name(start_weekday, day) == "Monday"})
    fig.update_xaxes(
        range=[0, horizon],
        tickvals=ticks,
        ticktext=[f"{weekday_name(start_weekday, day)[:3]} {day}" for day in ticks],
    )
    if weekends:
        shade = theme.rgba(theme.palette().ink, 0.07)
        for day in range(1, horizon + 1):
            if weekday_name(start_weekday, day) in ("Saturday", "Sunday"):
                fig.add_vrect(x0=day - 0.5, x1=min(day + 0.5, horizon), fillcolor=shade,
                              line_width=0, layer="below")


def stage_by_day(result: SimulationResult) -> list[tuple[int, str]]:
    """The stage of the scale on each simulated day: the day's status, and FoLtF or
    resolution from the day the declaration is in effect."""
    out = []
    for row in result.rows:
        stage = theme.stage_of(row["status"])
        if result.foltf_day is not None and row["day"] >= result.foltf_day and stage != "default":
            stage = "foltf"
        out.append((int(row["day"]), stage))
    return out


def stage_bands(result: SimulationResult, until_day: Optional[int] = None) -> list[tuple[int, int, str]]:
    """Runs of days in the same stage, as (first day, last day, stage); normal days left out."""
    bands: list[tuple[int, int, str]] = []
    for day, stage in stage_by_day(result):
        if until_day is not None and day > until_day:
            break
        if stage == "normal":
            continue
        if bands and bands[-1][2] == stage and bands[-1][1] == day - 1:
            bands[-1] = (bands[-1][0], day, stage)
        else:
            bands.append((day, day, stage))
    return bands


def _short(text: str, length: int = 44) -> str:
    return text if len(text) <= length else text[:length - 1].rstrip() + "…"


def build_timeline(
    result: SimulationResult,
    horizon: int,
    unit: str,
    bands: Optional[dict[str, pd.DataFrame]] = None,
    until_day: Optional[int] = None,
    start_weekday: Optional[str] = None,
    weekends: bool = True,
    rises: Optional[Sequence[tuple[int, float, str]]] = None,
) -> go.Figure:
    """The crisis day by day: the liquidity buffer as the large chart, deposits and CET1
    ratio as two small ones below on the same day axis.

    Background colour: the bank's stage on each day (the stage scale of app/theme.py).
    Markers: news and decisions, each with a label. Labels above the chart: stage changes.
    `rises` (optional, from engine.model.buffer_rises) labels the days on which the liquidity
    buffer goes up with the reason.
    `bands` (optional, from engine.montecarlo.bands) adds a light sensitivity band per panel.
    `start_weekday` (optional) labels the day axis with weekdays and shades the weekends.
    `until_day` (optional) reveals the run only up to that day. The axes keep the scale of
    the whole run, and the band is drawn only once the last day is shown.
    """
    pal = theme.palette()
    full_table = result.table
    last_day = int(full_table["day"].max())
    if until_day is None or until_day >= last_day:
        until_day = last_day
    table = full_table[full_table["day"] <= until_day]
    events = [event for event in result.events if event.day <= until_day]
    bands_shown = bands if until_day == last_day else None
    panels = _panels(unit)
    fig = _three_panels(unit)

    for row, (title, column, factor, value_format) in enumerate(panels, start=1):
        top = float(full_table[column].max())
        if bands is not None:
            top = max(top, float(bands[column]["high"].max()))
        if bands_shown is not None:
            band = bands_shown[column]
            # Upper edge first, then the lower edge filled up to it.
            for edge, fill in (("high", None), ("low", "tonexty")):
                fig.add_trace(
                    go.Scatter(
                        x=band["day"], y=band[edge] * factor, mode="lines",
                        name=BAND_NAME, legendgroup="band",
                        showlegend=row == 1 and edge == "low",
                        line=dict(width=0), fill=fill,
                        fillcolor=theme.rgba(pal.accent, 0.10), hoverinfo="skip",
                    ),
                    row=row, col=1,
                )
        fig.add_trace(
            go.Scatter(
                x=table["day"], y=table[column] * factor, mode="lines",
                name="Central run" if row == 1 else title, showlegend=row == 1,
                line=dict(color=pal.accent, width=3.5 if row == 1 else 2.5),
                hovertemplate="Day %{x}: " + value_format + "<extra></extra>",
            ),
            row=row, col=1,
        )
        # Fixed scale, so the axes do not jump while the days are revealed.
        fig.update_yaxes(range=[0, top * factor * 1.08 or 1], row=row, col=1)

    # After the traces: Plotly adds a band only to the panels that already hold a trace.
    for first, last, stage in stage_bands(result, until_day):
        fig.add_vrect(x0=max(0, first - 0.5), x1=min(horizon, last + 0.5),
                      fillcolor=theme.rgba(pal.stages[stage], 0.22), line_width=0, layer="below")

    buffer_at = table.set_index("day")["liquidity_buffer"]
    buffer_top = float(fig.layout.yaxis.range[1])
    news = [event for event in events if event.kind == "news"]
    if news:
        fig.add_trace(
            go.Scatter(
                x=[event.day for event in news],
                y=[buffer_at.get(event.day) for event in news],
                mode="markers+text", name="News",
                marker=dict(symbol="diamond", size=14, color=pal.surface,
                            line=dict(color=pal.ink, width=2)),
                text=["News"] * len(news), textposition="bottom center",
                textfont=dict(size=13, color=pal.ink),
                customdata=[event.detail for event in news],
                hovertemplate="Day %{x} news: %{customdata}<extra></extra>",
            ),
            row=1, col=1,
        )
    decided = [record for record in result.decision_log if record.decided_day <= until_day]
    if decided:
        fig.add_trace(
            go.Scatter(
                x=[record.decided_day for record in decided],
                y=[float(buffer_at[record.decided_day]) for record in decided],
                mode="markers", name="Decision (taken after the close of that day)",
                marker=dict(symbol="triangle-down", size=15, color=pal.ink,
                            line=dict(color=pal.surface, width=2)),
                text=[f"{record.decision_name}: {record.option_name}" for record in decided],
                hovertemplate="Day %{x}, decision: %{text}<extra></extra>",
            ),
            row=1, col=1,
        )
        for number, record in enumerate(decided):
            fig.add_annotation(
                x=record.decided_day, y=float(buffer_at[record.decided_day]), xref="x", yref="y",
                text=f"<b>Decision:</b> {_short(record.option_name)}", showarrow=True,
                arrowhead=0, arrowwidth=1, arrowcolor=pal.ink, ax=16,
                ay=-(44 + 26 * (number % 3)), xanchor="left",
                font=dict(size=13), bgcolor=theme.rgba(pal.surface, 0.9),
            )

    for day, rise, reason in rises or ():
        if day > until_day:
            continue
        fig.add_annotation(
            x=day, y=float(buffer_at[day]), xref="x", yref="y",
            text=f"+{rise:.2f}: {reason}", showarrow=True, arrowhead=2, arrowwidth=1,
            ax=48, ay=34 if float(buffer_at[day]) > 0.5 * buffer_top else -34, xanchor="left",
            font=dict(size=13), bgcolor=theme.rgba(pal.surface, 0.9),
        )

    for event in events:
        if event.kind not in TRIGGERS:
            continue
        label, stage = TRIGGERS[event.kind]
        color = pal.stages[stage]
        fig.add_vline(x=event.day, line=dict(color=color, width=2, dash="dash"))
        fig.add_annotation(
            x=event.day, y=_LABEL_Y[event.kind], xref="x", yref="paper",
            text=f"{label}: day {event.day}", showarrow=False,
            xanchor="left", yanchor="bottom", font=dict(size=13, color=theme.text_on(color)),
            bgcolor=color, borderpad=3,
        )

    add_calendar(fig, start_weekday, horizon, weekends)
    fig.update_xaxes(title_text="Day", row=len(panels), col=1)
    fig.update_layout(
        height=700,
        margin=dict(l=10, r=10, t=140, b=10),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="top", y=-0.09, x=0),
    )
    return theme.apply(fig)


def build_failure_distribution(
    counts: pd.DataFrame, horizon: int, start_weekday: Optional[str] = None
) -> go.Figure:
    """Number of sensitivity runs reaching payments default on each day, plus a bar for the rest.

    `counts` comes from engine.montecarlo.failure_day_counts.
    """
    pal = theme.palette()
    no_failure_x = horizon + 3
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=counts["day"], y=counts["runs"], name="Runs defaulting on this day",
            marker=dict(color=pal.stages["default"]),
            hovertemplate="Day %{x}: %{y} runs<extra></extra>",
        )
    )
    fig.add_trace(
        go.Bar(
            x=[no_failure_x], y=[counts.attrs["no_failure"]], name="No default",
            marker=dict(color=pal.stages["normal"]), text=[counts.attrs["no_failure"]],
            textposition="outside",
            hovertemplate=f"No payments default within {horizon} days: " + "%{y} runs<extra></extra>",
        )
    )
    if start_weekday is None:
        ticks = list(range(0, horizon + 1, 5))
        labels = [str(t) for t in ticks]
    else:
        ticks = sorted(
            {1} | {d for d in range(1, horizon + 1) if weekday_name(start_weekday, d) == "Monday"}
        )
        labels = [f"{weekday_name(start_weekday, d)[:3]} {d}" for d in ticks]
    fig.update_xaxes(
        title_text="Payments default day",
        tickvals=ticks + [no_failure_x],
        ticktext=labels + ["No default"],
        range=[-0.75, no_failure_x + 0.75],
    )
    fig.update_yaxes(title_text="Number of runs", rangemode="tozero")
    fig.update_layout(height=380, showlegend=False, bargap=0.25, margin=dict(l=10, r=10, t=30, b=10))
    return theme.apply(fig)


def build_contagion_chart(contagion: ContagionResult, horizon: int) -> go.Figure:
    """Second bank with and without contagion, with the days the shocks arrive."""
    pal = theme.palette()
    # Levels barely move on a zero-based axis, so the chart shows the mechanism (panic
    # multiplier) and its effect (share of deposits lost) instead.
    panels = [
        ("Second bank: panic multiplier", "panic_multiplier"),
        ("Second bank: deposits lost since the start (%)", "cum_deposit_loss_pct"),
    ]
    runs = [
        ("Without contagion", contagion.second_alone.table,
         dict(color=pal.reference, width=2.5, dash="dot")),
        ("With contagion", contagion.second.table, dict(color=pal.accent, width=2.5)),
    ]
    fig = make_subplots(
        rows=len(panels), cols=1, shared_xaxes=True, vertical_spacing=0.12,
        subplot_titles=[title for title, _ in panels],
    )
    for row, (_, column) in enumerate(panels, start=1):
        for name, table, line in runs:
            fig.add_trace(
                go.Scatter(
                    x=table["day"], y=table[column], mode="lines", name=name, legendgroup=name,
                    showlegend=row == 1, line=line,
                    hovertemplate=name + ", day %{x}: %{y:.2f}<extra></extra>",
                ),
                row=row, col=1,
            )
        fig.update_yaxes(rangemode="tozero", row=row, col=1)
    for index, event in enumerate(contagion.events):
        fig.add_vline(x=event.day, line=dict(color=pal.muted, width=1, dash="dash"))
        fig.add_annotation(
            x=event.day, y=1.08 + 0.05 * index, xref="x", yref="paper",
            text=f"Day {event.day}: shock {event.panic_shock:.3f}", showarrow=False,
            xanchor="left", yanchor="bottom",
        )
    fig.update_xaxes(range=[0, horizon], dtick=5)
    fig.update_xaxes(title_text="Day", row=len(panels), col=1)
    fig.update_layout(
        height=520, margin=dict(l=10, r=10, t=110, b=10), hovermode="x unified",
        legend=dict(orientation="h", yanchor="top", y=-0.15, x=0),
    )
    return theme.apply(fig)


# ----------------------------------------------------------------------------- funding

# Funding source -> column of the daily table.
SOURCE_COLUMNS = {
    "cash": "funded_cash", "hqla": "funded_hqla", "central_bank": "funded_central_bank",
    "ela": "funded_ela", "fire_sale": "funded_fire_sale", "unmet": "unmet_outflow",
}


def build_funding(table: pd.DataFrame, unit: str, start_weekday: Optional[str] = None) -> go.Figure:
    """How each day's outflow was funded: one stacked bar per day, one colour per source, in
    the order the sources are used."""
    pal = theme.palette()
    days = table[table["day"] >= 1]
    fig = go.Figure()
    for source in theme.SOURCES:
        label = theme.SOURCE_LABELS[source]
        fig.add_trace(go.Bar(
            x=days["day"], y=days[SOURCE_COLUMNS[source]], name=label,
            marker=dict(color=pal.sources[source], line=dict(color=pal.surface, width=1)),
            hovertemplate=label + ": %{y:.2f}<extra></extra>",
        ))
    last_day = int(table["day"].max())
    ticks = list(range(1, last_day + 1))
    fig.update_xaxes(
        title_text="Day", range=[0.4, last_day + 0.6], tickvals=ticks,
        ticktext=[str(day) if start_weekday is None
                  else f"{weekday_name(start_weekday, day)[:3]}<br>{day}" for day in ticks],
    )
    fig.update_yaxes(title_text=f"Outflow funded ({unit})", rangemode="tozero")
    fig.update_layout(
        barmode="stack", height=460, bargap=0.25, hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, traceorder="normal"),
        margin=dict(l=10, r=10, t=50, b=10),
    )
    return theme.apply(fig)


# Balance sheet items: (column, label), bottom of the column first.
ASSET_ITEMS = (
    ("cash", "Cash and reserves"),
    ("hqla_level1", "Liquid securities, level 1"),
    ("hqla_level2", "Liquid securities, level 2"),
    ("eligible_collateral", "Other eligible collateral"),
    ("illiquid_assets", "Loans"),
)
LIABILITY_ITEMS = (
    ("cet1", "CET1 capital"),
    ("other_liabilities", "Other liabilities"),
    ("wholesale_funding", "Wholesale funding"),
    ("ela_funding", "ELA"),
    ("central_bank_funding", "Central bank funding"),
    ("dep_financial", "Deposits: financial"),
    ("dep_corporate", "Deposits: corporate"),
    ("dep_uninsured_retail", "Deposits: uninsured retail"),
    ("dep_insured_retail", "Deposits: insured retail"),
)


def balance_sheet_colors(pal: Optional[theme.Palette] = None) -> dict[str, str]:
    """One colour per balance sheet item. Cash, liquid securities, central bank and ELA keep
    the colours they have in the funding chart."""
    pal = pal or theme.palette()
    return {
        "cash": pal.sources["cash"],
        "hqla_level1": pal.sources["hqla"],
        "hqla_level2": theme.mix(pal.sources["hqla"], pal.surface, 0.55),
        "eligible_collateral": pal.sources["central_bank"],
        "illiquid_assets": theme.mix(pal.muted, pal.surface, 0.45),
        "cet1": pal.ink,
        "other_liabilities": theme.mix(pal.muted, pal.surface, 0.45),
        "wholesale_funding": pal.reference,
        "ela_funding": pal.sources["ela"],
        "central_bank_funding": pal.sources["central_bank"],
        "dep_financial": theme.mix(pal.accent, pal.surface, 0.25),
        "dep_corporate": theme.mix(pal.accent, pal.surface, 0.45),
        "dep_uninsured_retail": theme.mix(pal.accent, pal.surface, 0.70),
        "dep_insured_retail": pal.accent,
    }


def build_balance_sheet(opening: dict, selected: dict, unit: str) -> go.Figure:
    """The balance sheet at the opening against a chosen day: two stacked columns for the
    assets and two for the liabilities and capital. `opening` and `selected` are rows of a
    run (SimulationResult.rows)."""
    pal = theme.palette()
    colors = balance_sheet_colors(pal)
    day = int(selected["day"])
    x_assets = ["Assets<br>opening", f"Assets<br>day {day}"]
    x_liabilities = ["Liabilities and capital<br>opening", f"Liabilities and capital<br>day {day}"]
    fig = go.Figure()
    for group, items, xs in (("Assets", ASSET_ITEMS, x_assets),
                             ("Liabilities and capital", LIABILITY_ITEMS, x_liabilities)):
        for position, (column, label) in enumerate(items):
            values = [float(opening[column]), float(selected[column])]
            color = colors[column]
            fig.add_trace(go.Bar(
                x=xs, y=values, name=label, legendgroup=group,
                legendgrouptitle_text=group if position == 0 else None,
                marker=dict(color=color, line=dict(color=pal.surface, width=1)),
                text=[f"{value:.1f}" if value >= 0.03 * float(opening["total_assets"]) else ""
                      for value in values],
                textposition="inside", insidetextanchor="middle",
                textfont=dict(family=theme.FONT_FIGURES, size=13, color=theme.text_on(color, pal)),
                hovertemplate=label + ": %{y:.2f} " + unit + "<extra></extra>",
            ))
    totals = [opening["total_assets"], selected["total_assets"]] * 2
    for x, total in zip(x_assets + x_liabilities, totals):
        fig.add_annotation(x=x, y=float(total), text=f"<b>{total:.1f}</b>", showarrow=False,
                           yanchor="bottom", font=dict(family=theme.FONT_FIGURES, size=14))
    fig.update_xaxes(categoryorder="array", categoryarray=x_assets + x_liabilities)
    fig.update_yaxes(title_text=unit, rangemode="tozero")
    fig.update_layout(
        barmode="stack", height=560, bargap=0.35,
        legend=dict(traceorder="reversed+grouped", groupclick="toggleitem"),
        margin=dict(l=10, r=10, t=30, b=10),
    )
    return theme.apply(fig)


# ----------------------------------------------------------------------------- branches


def divergence_day(reference: SimulationResult, selected: SimulationResult) -> Optional[int]:
    """The last day on which two paths of the same bank and scenario still have the same
    figures, or None if they never part."""
    columns = ("liquidity_buffer", "deposits_total", "cet1_ratio", "status")
    for first, second in zip(reference.rows, selected.rows):
        if any(first[c] != second[c] if c == "status" else abs(first[c] - second[c]) > 1e-9
               for c in columns):
            return int(first["day"]) - 1
    if len(reference.rows) != len(selected.rows):
        return min(len(reference.rows), len(selected.rows)) - 1
    return None


def build_path_comparison(
    reference: SimulationResult,
    selected: SimulationResult,
    reference_name: str,
    selected_name: str,
    horizon: int,
    unit: str,
    start_weekday: Optional[str] = None,
    weekends: bool = True,
) -> go.Figure:
    """Two decision paths of the same bank and scenario on the same axes, with the day
    after which they part marked."""
    pal = theme.palette()
    panels = _panels(unit)
    runs = [
        (reference_name, reference, dict(color=pal.reference, width=3, dash="dot")),
        (selected_name, selected, dict(color=pal.accent, width=3.5)),
    ]
    fig = _three_panels(unit)
    for row, (_, column, factor, value_format) in enumerate(panels, start=1):
        for name, run, line in runs:
            table = run.table
            fig.add_trace(
                go.Scatter(
                    x=table["day"], y=table[column] * factor, mode="lines", name=name,
                    legendgroup=name, showlegend=row == 1,
                    line=line if row == 1 else {**line, "width": 2.5},
                    hovertemplate=name + ", day %{x}: " + value_format + "<extra></extra>",
                ),
                row=row, col=1,
            )
            # The last day of each path, so that a path that stops early is easy to see.
            fig.add_trace(
                go.Scatter(
                    x=[table["day"].iloc[-1]], y=[table[column].iloc[-1] * factor],
                    mode="markers", name=f"{name}: last day", legendgroup=name, showlegend=False,
                    marker=dict(size=11, color=line["color"], line=dict(color=pal.surface, width=2)),
                    hovertemplate=name + " ends on day %{x}<extra></extra>",
                ),
                row=row, col=1,
            )
        fig.update_yaxes(rangemode="tozero", row=row, col=1)
    for name, run, line in runs:
        last = run.rows[-1]
        fig.add_annotation(
            x=int(last["day"]), y=float(last["liquidity_buffer"]), xref="x", yref="y",
            text=f"{name}: {OUTCOME_LABELS[run.outcome]}, day {int(last['day'])}",
            showarrow=True, arrowhead=0, arrowwidth=1, arrowcolor=line["color"],
            ax=-14, ay=-34 if run is selected else -64, xanchor="right",
            font=dict(size=13), bgcolor=theme.rgba(pal.surface, 0.9),
        )
    parted = divergence_day(reference, selected)
    if parted is not None:
        fig.add_vline(x=parted, line=dict(color=pal.ink, width=2, dash="dash"))
        fig.add_annotation(
            x=parted, y=1.012, xref="x", yref="paper", showarrow=False,
            text=f"The paths part after day {parted}", xanchor="left", yanchor="bottom",
            font=dict(size=13, color=pal.surface), bgcolor=pal.ink, borderpad=3,
        )
    add_calendar(fig, start_weekday, horizon, weekends)
    fig.update_xaxes(title_text="Day", row=len(panels), col=1)
    fig.update_layout(
        height=700, margin=dict(l=10, r=10, t=60, b=10), hovermode="x unified",
        legend=dict(orientation="h", yanchor="top", y=-0.09, x=0),
    )
    return theme.apply(fig)


# Lines of the scorecard drawn as bars: (field, label, digits, in the bank's unit?).
SCORECARD_LINES = (
    ("deposits_lost_pct", "Deposits lost (%)", 1, False),
    ("losses_booked", "Losses booked", 2, True),
    ("ela_outstanding", "ELA outstanding at the end", 2, True),
    ("uncovered_deposits", "Uncovered deposits still in the bank", 2, True),
    ("covered_deposits", "Covered deposits still in the bank", 2, True),
    ("queued_withdrawals", "Withdrawals requested, not paid", 2, True),
    ("dgs_payout", "DGS payout", 2, True),
)


def build_scorecard(
    reference: Scorecard, selected: Scorecard, reference_name: str, selected_name: str, unit: str
) -> go.Figure:
    """The outcome scorecard as a table of bars: one row per figure, one column per path.
    The two bars of a row share their scale, so they can be compared at a glance."""
    pal = theme.palette()
    cards = [(reference_name, reference, pal.reference), (selected_name, selected, pal.accent)]
    lines = SCORECARD_LINES
    fig = make_subplots(rows=len(lines), cols=2, shared_yaxes=True, horizontal_spacing=0.04,
                        vertical_spacing=0.02)
    for row, (field, label, digits, in_unit) in enumerate(lines, start=1):
        label = f"{label} ({unit})" if in_unit else label
        top = max(getattr(card, field) for _, card, _ in cards)
        for col, (name, card, color) in enumerate(cards, start=1):
            value = getattr(card, field)
            fig.add_trace(
                go.Bar(
                    x=[value], y=[label], orientation="h", name=name, showlegend=False,
                    marker=dict(color=color), text=[f"{value:.{digits}f}"],
                    textposition="outside", cliponaxis=False,
                    textfont=dict(family=theme.FONT_FIGURES, size=15, color=pal.ink),
                    hovertemplate=f"{name}, {label}: " + "%{x:." + str(digits) + "f}<extra></extra>",
                ),
                row=row, col=col,
            )
            fig.update_xaxes(range=[0, (top or 1) * 1.3], visible=False, row=row, col=col)
        fig.update_yaxes(tickfont=dict(family=theme.FONT_TEXT, size=14, color=pal.ink),
                         ticks="", showgrid=False, row=row, col=1)
        fig.update_yaxes(showgrid=False, ticks="", row=row, col=2)
    for col, (name, card, color) in enumerate(cards, start=1):
        domain = fig.layout[f"xaxis{'' if col == 1 else col}"].domain
        stage = theme.stage_color(card.outcome, pal)
        fig.add_annotation(
            x=domain[0], y=1.0, xref="paper", yref="paper", showarrow=False, xanchor="left",
            yanchor="bottom", yshift=34, text=f"<b>{name}</b>", font=dict(size=16, color=pal.ink),
        )
        fig.add_annotation(
            x=domain[0], y=1.0, xref="paper", yref="paper", showarrow=False, xanchor="left",
            yanchor="bottom", yshift=6, borderpad=3, bgcolor=stage,
            text=f"{theme.STAGE_ICONS[theme.stage_of(card.outcome)]} "
                 f"{OUTCOME_LABELS[card.outcome]}, last day {card.end_day}",
            font=dict(size=13, color=theme.text_on(stage, pal)),
        )
    fig.update_layout(height=46 * len(lines) + 90, margin=dict(l=10, r=30, t=80, b=10),
                      bargap=0.35, plot_bgcolor="rgba(0,0,0,0)")
    return theme.apply(fig)


# ----------------------------------------------------------------------------- resolution

# Layers of the liability stack: key -> share of the accent in the layer's colour.
_LAYER_SHADE = {
    "cet1": 1.0, "at1_t2": 0.78, "senior_non_preferred": 0.58, "senior_preferred": 0.40,
    "uncovered_deposits": 0.24, "covered_deposits": 0.12,
}


def resolution_layers(summary: ResolutionSummary) -> list[tuple[str, str, float, float]]:
    """The layers of one summary as (key, label, absorbed, remaining), first to bear losses
    first. In liquidation the layers are the claims on the estate and "absorbed" is the loss."""
    lines = summary.lines if summary.liquidation is None else tuple(reversed(summary.liquidation.lines))
    return [(line.key, line.label, line.absorbed, max(0.0, line.amount - line.absorbed))
            for line in lines]


def build_resolution_stack(levels: dict[str, ResolutionSummary], unit: str) -> go.Figure:
    """The liability stack for the three assumed write-down levels, side by side: per layer,
    the amount written down or converted (hatched) against what remains (plain). The columns
    are evenly spaced. The dashed line is the write-down up to which available MREL covers
    the need: it stands between the columns it separates, never on one.

    `levels` comes from engine.resolution.resolution_levels.
    """
    pal = theme.palette()
    central = levels["central"]
    liquidation = central.liquidation is not None
    absorbed_word = "Lost" if liquidation else "Written down or converted"
    share = (lambda s: s.liquidation.haircut) if liquidation else (lambda s: s.writedown_share)
    shares = [100 * share(levels[level]) for level in levels]
    xs, width = list(range(len(shares))), 0.6
    stacks = {level: resolution_layers(summary) for level, summary in levels.items()}
    fig = go.Figure()
    for position, (key, label, _, _) in enumerate(stacks["central"]):
        color = theme.mix(pal.accent, pal.surface, _LAYER_SHADE[key])
        for part, hatched in ((2, True), (3, False)):
            values = [stacks[level][position][part] for level in levels]
            fig.add_trace(go.Bar(
                x=xs, y=values, width=width, name=label, legendgroup=key, showlegend=not hatched,
                marker=dict(
                    color=pal.ink if hatched else color, line=dict(color=pal.surface, width=1),
                    pattern=dict(shape="/" if hatched else "", bgcolor=pal.ink,
                                 fgcolor=theme.mix(pal.surface, pal.ink, 0.55), size=8,
                                 solidity=0.18) if hatched else None,
                ),
                text=[f"{value:.2f}" if value >= 0.04 * sum(a + r for _, _, a, r in stacks[level]) else ""
                      for value, level in zip(values, levels)],
                textposition="inside", insidetextanchor="middle",
                textfont=dict(family=theme.FONT_FIGURES, size=13,
                              color=pal.surface if hatched else theme.text_on(color, pal)),
                hovertemplate=label + (f", {absorbed_word.lower()}" if hatched else ", remains")
                + ": %{y:.2f} " + unit + "<extra></extra>",
            ))
    # Legend entry for the hatching.
    fig.add_trace(go.Bar(
        x=[None], y=[None], name=f"Dark, hatched: {absorbed_word.lower()}",
        marker=dict(color=pal.ink, pattern=dict(
            shape="/", bgcolor=pal.ink, fgcolor=theme.mix(pal.surface, pal.ink, 0.55), size=8,
            solidity=0.18)),
    ))
    span = [-0.6, len(xs) - 0.4]
    point = None if liquidation else central.breakpoint_share
    if point is not None and point > 0:
        # Between the last column the MREL still covers and the first it does not.
        x = sum(1 for value in shares if value <= 100 * point + 1e-9) - 0.5
        fig.add_vline(x=x, line=dict(color=pal.stages["likely_to_fail"], width=2.5, dash="dash"))
        fig.add_annotation(
            x=x, y=1.0, xref="x", yref="paper", showarrow=False, yanchor="bottom",
            xanchor="right" if x > sum(span) / 2 else "left" if x < 0 else "center",
            text=f"Breakpoint: MREL covers the need up to {100 * point:.1f}%",
            font=dict(size=13, color=theme.text_on(pal.stages["likely_to_fail"], pal)),
            bgcolor=pal.stages["likely_to_fail"], borderpad=3,
        )
    fig.update_xaxes(
        title_text=("Loss of value of the loan book in liquidation (assumed)" if liquidation
                    else "Valuation write-down of the loan book (assumed)"),
        tickvals=xs, range=span,
        ticktext=[f"{level.capitalize()}: {value:g}%" for level, value in zip(levels, shares)],
    )
    fig.update_yaxes(title_text=unit, rangemode="tozero")
    fig.update_layout(
        barmode="stack", height=600, margin=dict(l=10, r=10, t=50, b=10),
        legend=dict(orientation="h", yanchor="top", y=-0.16, x=0, traceorder="normal"),
    )
    return theme.apply(fig)


# ----------------------------------------------------------------------------- decision map

# Outcomes that share a colour of the stage scale are told apart by shape, and by label.
_OUTCOME_SYMBOLS = {
    "stabilised": "circle", "under_stress": "circle", "emergency_funding": "circle",
    "bail_in": "diamond", "sale_of_business": "square", "liquidation": "x",
    "payments_default": "square",
}


def outcome_color(outcome: str, pal: Optional[theme.Palette] = None) -> str:
    """The colour of an outcome: its place on the stage scale."""
    return theme.stage_color(outcome, pal)


def decision_map_layout(tree) -> tuple[list[dict], list[tuple[int, int]]]:
    """Nodes and edges of the decision map as a tree: one level per decision.

    A node is a dict with level, y, label, and for a leaf its index in `tree.leaves`.
    Leaves keep the order of `tree.leaves`; an inner node sits in the middle of its children.
    """
    nodes: list[dict] = [{"level": 0, "y": 0.0, "label": "", "prefix": (), "leaf": None,
                          "taken": True}]
    index = {(): 0}
    edges = []
    depth = len(tree.decisions)
    for number, leaf in enumerate(tree.leaves):
        for level in range(1, depth + 1):
            prefix = leaf.options[:level]
            if prefix not in index:
                index[prefix] = len(nodes)
                nodes.append({"level": level, "y": 0.0, "label": leaf.option_names[level - 1],
                              "prefix": prefix, "leaf": None, "taken": leaf.taken[level - 1]})
                edges.append((index[prefix[:-1]], index[prefix]))
        nodes[index[leaf.options]]["leaf"] = number
        nodes[index[leaf.options]]["y"] = float(number)
    for level in range(depth - 1, -1, -1):
        for position, node in enumerate(nodes):
            if node["level"] == level:
                children = [nodes[child]["y"] for parent, child in edges if parent == position]
                node["y"] = sum(children) / len(children)
    return nodes, edges


def build_decision_map(tree, outcome_labels: dict[str, str]) -> go.Figure:
    """Decision map: every combination of options as a tree, one level per decision, leaves
    coloured by outcome on the stage scale and labelled with it.

    `tree` comes from engine.objective.decision_map. An option that could not be taken in the
    run (its condition was never met) is drawn with a hollow marker and "(not open)".
    """
    pal = theme.palette()
    nodes, edges = decision_map_layout(tree)
    depth = len(tree.decisions)
    fig = go.Figure()
    xs, ys = [], []
    for parent, child in edges:
        a, b = nodes[parent], nodes[child]
        # Elbow: down the parent's level, then across to the child.
        xs += [a["level"], a["level"], b["level"], None]
        ys += [a["y"], b["y"], b["y"], None]
    fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name="Branches", showlegend=False,
                             line=dict(color=pal.line, width=2), hoverinfo="skip"))
    inner = [node for node in nodes if node["leaf"] is None and node["level"] > 0]
    fig.add_trace(go.Scatter(
        x=[node["level"] for node in inner], y=[node["y"] for node in inner], mode="markers",
        name="Decision", showlegend=False, hoverinfo="skip",
        marker=dict(size=9, color=pal.surface, line=dict(color=pal.muted, width=2)),
    ))
    leaves = [node for node in nodes if node["leaf"] is not None]
    for outcome in dict.fromkeys(leaf.outcome for leaf in tree.leaves):
        group = [node for node in leaves if tree.leaves[node["leaf"]].outcome == outcome]
        cards = [tree.leaves[node["leaf"]].scorecard for node in group]
        color, symbol = outcome_color(outcome, pal), _OUTCOME_SYMBOLS[outcome]
        fig.add_trace(go.Scatter(
            x=[depth] * len(group), y=[node["y"] for node in group], mode="markers",
            name=outcome_labels[outcome],
            marker=dict(
                size=15, color=color,
                symbol=[symbol if all(tree.leaves[node["leaf"]].taken) else f"{symbol}-open"
                        for node in group],
                line=dict(color=color, width=2),
            ),
            text=[
                " | ".join(tree.leaves[node["leaf"]].option_names)
                + f"<br>{outcome_labels[outcome]}, last day {card.end_day}, "
                  f"deposits lost {card.deposits_lost_pct:.1f}%"
                for node, card in zip(group, cards)
            ],
            hovertemplate="%{text}<extra></extra>",
        ))
    for node in nodes:
        if node["level"] == 0:
            continue
        fig.add_annotation(
            x=node["level"] - 0.97, y=node["y"], xref="x", yref="y", showarrow=False,
            text=node["label"] + ("" if node["taken"] else " (not open)"),
            xanchor="left", yanchor="bottom",
            font=dict(size=13, color=pal.ink if node["taken"] else pal.muted),
        )
    if len(tree.leaves) <= 40:
        for node in leaves:
            leaf = tree.leaves[node["leaf"]]
            fig.add_annotation(
                x=depth + 0.03, y=node["y"], xref="x", yref="y", showarrow=False,
                text=f"{outcome_labels[leaf.outcome]}, day {leaf.scorecard.end_day}",
                xanchor="left", yanchor="middle", font=dict(size=13, color=pal.muted),
            )
    for level, name in enumerate(tree.decision_names):
        fig.add_annotation(
            x=level + 0.03, y=1.0, xref="x", yref="paper", showarrow=False, text=f"<b>{name}</b>",
            xanchor="left", yanchor="bottom", font=dict(size=14),
        )
    fig.update_xaxes(visible=False, range=[-0.02, depth + 0.62])
    fig.update_yaxes(visible=False, range=[len(tree.leaves) - 0.4, -0.9])
    fig.update_layout(
        height=max(340, 30 * len(tree.leaves) + 130),
        margin=dict(l=10, r=10, t=40, b=10),
        legend=dict(orientation="h", yanchor="top", y=-0.02, x=0, title_text="Outcome: "),
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return theme.apply(fig)


# ----------------------------------------------------------------- information map (UI-3)

# Where each actor sits: the bank on the left, the competent authority in the middle, the
# authorities it informs on the right.
_MAP_POSITIONS = {
    "bank_management": (0.0, 0.5),
    "competent_authority": (1.2, 0.5),
    "resolution_authority": (2.35, 1.0),
    "central_bank": (2.6, 0.5),
    "deposit_guarantee_scheme": (2.35, 0.0),
}
_NODE_HALF = (0.33, 0.17)  # half width and half height of a node


def _edge_point(origin: tuple[float, float], towards: tuple[float, float]) -> tuple[float, float]:
    """Where the line from one node's centre to another's leaves the first node's box."""
    dx, dy = towards[0] - origin[0], towards[1] - origin[1]
    scale = min(_NODE_HALF[0] / abs(dx) if dx else float("inf"),
                _NODE_HALF[1] / abs(dy) if dy else float("inf"))
    return origin[0] + dx * scale, origin[1] + dy * scale


def knowledge_lines(node) -> list[str]:
    """The lines written in a node of the information map."""
    if not node.informed:
        return ["Not yet informed", f"informed from: {node.informed_from}"]
    age = "same day" if node.lag_days == 0 else (
        "1 day old" if node.lag_days == 1 else f"{node.lag_days} days old")
    stage = theme.stage_of(node.status)
    return [f"Figures as of day {node.figures_as_of} ({age})",
            f"Knows: {theme.STAGE_ICONS[stage]} {theme.STAGE_LABELS[stage]}"]


def build_information_map(info_map, weekday: str = "") -> go.Figure:
    """Information map: who knows what at the close of one simulated day.

    `info_map` comes from engine.script.information_map. A node is an actor: the day its
    figures refer to and the stage it knows of, or "Not yet informed" with a dashed, hollow
    box. An arrow is a notification line with the time it arrives; the lines used on the day
    shown are thick and carry what was notified.
    """
    pal = theme.palette()
    fig = go.Figure()
    pairs = {(link.sender, link.recipient) for link in info_map.links}
    for link in info_map.links:
        a, b = _MAP_POSITIONS[link.sender], _MAP_POSITIONS[link.recipient]
        shift = (0.0, 0.0)
        if (link.recipient, link.sender) in pairs:  # both directions: keep the two apart
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = (dx * dx + dy * dy) ** 0.5
            shift = (-dy / length * 0.035, dx / length * 0.035)
        start, end = _edge_point(a, b), _edge_point(b, a)
        start = (start[0] + shift[0], start[1] + shift[1])
        end = (end[0] + shift[0], end[1] + shift[1])
        color = pal.accent if link.active else pal.reference
        fig.add_annotation(
            x=end[0], y=end[1], ax=start[0], ay=start[1], xref="x", yref="y", axref="x",
            ayref="y", showarrow=True, arrowhead=3, arrowsize=1.1,
            arrowwidth=3.5 if link.active else 1.5, arrowcolor=color, text="",
        )
        text = (f"<b>{link.time} {_short(link.titles[0], 34)}</b>" if link.active
                else f"stage change: same day, {link.time}")
        fig.add_annotation(
            x=(start[0] + end[0]) / 2, y=(start[1] + end[1]) / 2, xref="x", yref="y",
            text=text, showarrow=False, bgcolor=pal.surface, borderpad=2, yshift=14,
            font=dict(size=13, color=pal.ink if link.active else pal.muted),
        )
    for node in info_map.nodes:
        x, y = _MAP_POSITIONS[node.actor]
        w, h = _NODE_HALF
        fig.add_shape(
            type="rect", x0=x - w, x1=x + w, y0=y - h, y1=y + h, layer="above",
            fillcolor=pal.surface if node.informed else pal.paper,
            line=dict(color=pal.ink if node.informed else pal.muted,
                      width=1.5, dash="solid" if node.informed else "dash"),
        )
        if node.informed:  # the stage the actor knows of, as a strip on the left
            fig.add_shape(type="rect", x0=x - w, x1=x - w + 0.035, y0=y - h, y1=y + h,
                          layer="above", line_width=0, fillcolor=theme.stage_color(node.status, pal))
        fig.add_annotation(
            x=x + 0.015, y=y, xref="x", yref="y", showarrow=False, align="center",
            text="<br>".join([f"<b>{node.name}</b>", *knowledge_lines(node)]),
            font=dict(size=14, color=pal.ink if node.informed else pal.muted),
        )
    fig.add_trace(go.Scatter(
        x=[_MAP_POSITIONS[node.actor][0] for node in info_map.nodes],
        y=[_MAP_POSITIONS[node.actor][1] for node in info_map.nodes],
        mode="markers", marker=dict(size=60, opacity=0), showlegend=False, name="Actors",
        text=[
            f"<b>{node.name}</b><br>Figures from {node.source}<br>"
            f"{node.figures_in_scope} figures in scope<br>"
            + ("Received this day:<br>" + "<br>".join(node.received) if node.received
               else "Nothing received this day")
            for node in info_map.nodes
        ],
        hovertemplate="%{text}<extra></extra>",
    ))
    when = f"day {info_map.day}" + (f", {weekday}" if weekday else "")
    fig.update_xaxes(visible=False, range=[-0.4, 3.0], fixedrange=True)
    fig.update_yaxes(visible=False, range=[-0.28, 1.28], fixedrange=True)
    fig.update_layout(
        height=560, margin=dict(l=10, r=10, t=50, b=10), plot_bgcolor="rgba(0,0,0,0)",
        title=dict(text=f"Who knows what at the close of {when}", x=0.01, xanchor="left"),
    )
    return theme.apply(fig)


# ----------------------------------------------------------------- inject timeline (UI-3)

STAKEHOLDER_LANE = "Simulated stakeholders"


def clock_x(day: int, time: str) -> float:
    """A day and a time of day on the day axis: day d runs from d - 0.5 to d + 0.5."""
    hours, minutes = (int(part) for part in time.split(":"))
    return day - 0.5 + (hours + minutes / 60) / 24


def timeline_marks(branch, players: Sequence[str], actor_names: dict[str, str],
                   stakeholders: Sequence[str], released: Optional[set] = None,
                   key_of=None) -> list[dict]:
    """The markers of the inject timeline for one path: every inject in its recipient's
    lane, and once more in the stakeholder lane if the control team delivers it in a
    stakeholder's name. With `released` (keys given by `key_of`), each says whether it is out."""
    marks, seen = [], set()
    for actor in players:
        for inject in branch.injects[actor]:
            out = None if released is None else key_of(inject) in released
            base = dict(x=clock_x(inject.day, inject.time), day=inject.day, time=inject.time,
                        purpose=inject.purpose, title=inject.title, trigger=inject.trigger,
                        delivered_by=inject.delivered_by, fallback=inject.fallback)
            marks.append({**base, "lane": actor_names[actor], "released": out})
            played = (inject.day, inject.time, inject.title, inject.delivered_by)
            if inject.delivered_by in stakeholders:
                if played not in seen:
                    seen.add(played)
                    marks.append({**base, "lane": STAKEHOLDER_LANE, "released": out})
                elif out:  # released to at least one team
                    for mark in marks:
                        if mark["lane"] == STAKEHOLDER_LANE and (
                                mark["day"], mark["time"], mark["title"], mark["delivered_by"]) == played:
                            mark["released"] = True
    return marks


def timeline_windows(branch, players: Sequence[str], actor_names: dict[str, str],
                     decision_time: str, taken: Optional[dict[str, int]] = None) -> list[dict]:
    """Decision windows of one path: from the opening of a decision to the close of its
    deadline day, in the deciding actor's lane (the stakeholder lane if it is not a player).
    `taken` (decision -> day it was taken) ends a window early."""
    windows, seen = [], set()
    for actor, injects in branch.injects.items():
        for inject in injects:
            due = inject.decision_due
            if due is None or due.decision in seen:
                continue
            seen.add(due.decision)
            end_day = due.deadline_day if taken is None else taken.get(due.decision, due.deadline_day)
            windows.append(dict(
                lane=actor_names[actor] if actor in players else STAKEHOLDER_LANE,
                name=due.name, start=clock_x(inject.day, decision_time),
                end=max(end_day + 0.5, clock_x(inject.day, decision_time) + 0.2),
                open_day=inject.day, deadline_day=due.deadline_day,
                taken=None if taken is None else taken.get(due.decision),
            ))
    return windows


def build_inject_timeline(
    marks: Sequence[dict], windows: Sequence[dict], lanes: Sequence[str], last_day: int,
    start_weekday: Optional[str] = None, weekends: bool = False,
    now: Optional[float] = None,
) -> go.Figure:
    """Inject timeline: one lane per player and one for the simulated stakeholders.

    Injects are markers whose colour and shape give the purpose (advance, challenge,
    control); decision windows are bars from opening to deadline. With `now` (the simulated
    clock on the day axis) a line marks it; a mark whose `released` is False is hollow.
    """
    pal = theme.palette()
    fig = go.Figure()
    row = {lane: index for index, lane in enumerate(lanes)}
    # Marks of one lane that would touch are stacked, so none hides another.
    near = 0.009 * (last_day + 1.4)
    last_x: dict[tuple, float] = {}
    for mark in sorted(marks, key=lambda m: m["x"]):
        slot = next((n for n in range(3)
                     if mark["x"] - last_x.get((mark["lane"], n), -99.0) >= near), 2)
        last_x[(mark["lane"], slot)] = mark["x"]
        mark["y"] = row[mark["lane"]] - 0.12 - 0.16 * slot
    for purpose in theme.PURPOSES:
        group = [mark for mark in marks if mark["purpose"] == purpose]
        color, symbol = pal.purposes[purpose], theme.PURPOSE_SYMBOLS[purpose]
        fig.add_trace(go.Scatter(
            x=[mark["x"] for mark in group], y=[mark["y"] for mark in group], mode="markers",
            name=theme.PURPOSE_LABELS[purpose], showlegend=False,
            marker=dict(
                size=13, color=color, line=dict(color=color, width=2),
                symbol=[f"{symbol}-open" if mark["released"] is False else symbol for mark in group],
            ),
            text=[
                f"<b>{mark['title']}</b><br>Day {mark['day']}, {mark['time']}, to: {mark['lane']}"
                f"<br>Delivered by {mark['delivered_by']}"
                + ("<br>Fallback inject" if mark["fallback"] else "")
                + (f"<br>{_short(mark['trigger'], 90)}" if mark["trigger"] else "")
                + ("" if mark["released"] is None else
                   "<br>Released" if mark["released"] else "<br>Not yet released")
                for mark in group
            ],
            hovertemplate="%{text}<extra></extra>",
        ))
    for purpose in theme.PURPOSES:  # the legend's own entries, always filled
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers", name=theme.PURPOSE_LABELS[purpose],
            marker=dict(size=13, color=pal.purposes[purpose], symbol=theme.PURPOSE_SYMBOLS[purpose]),
        ))
    if any(mark["released"] is False for mark in marks):
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers", name="Hollow: not yet released",
            marker=dict(size=13, color=pal.muted, symbol="circle-open", line=dict(width=2)),
        ))
    fig.add_trace(go.Scatter(
        x=[None], y=[None], mode="markers", name="Decision window, opening to deadline",
        marker=dict(symbol="square", size=13, color=theme.mix(pal.accent, pal.surface, 0.25),
                    line=dict(color=pal.accent, width=1.5)),
    ))
    slots: dict[str, int] = {}
    for window in sorted(windows, key=lambda w: w["start"]):
        slot = slots.get(window["lane"], 0)
        slots[window["lane"]] = slot + 1
        y = row[window["lane"]] + 0.13 + 0.19 * (slot % 2)
        fig.add_shape(
            type="rect", x0=window["start"], x1=window["end"], y0=y - 0.075, y1=y + 0.075,
            fillcolor=theme.mix(pal.accent, pal.surface, 0.25), layer="below",
            line=dict(color=pal.accent, width=1.5),
        )
        fig.add_annotation(
            x=window["start"], y=y, xref="x", yref="y", showarrow=False, xanchor="left",
            xshift=5, text=_short(window["name"], 40), font=dict(size=12, color=pal.ink),
        )
    for index in range(len(lanes)):
        fig.add_hline(y=index + 0.5, line=dict(color=pal.line, width=1))
    add_calendar(fig, start_weekday, last_day, weekends)
    fig.update_xaxes(range=[-0.7, last_day + 0.7], title_text="Simulated day")
    if start_weekday is None:
        fig.update_xaxes(dtick=1)
    elif last_day <= 16:  # room for every day
        days = list(range(1, last_day + 1))
        fig.update_xaxes(tickvals=days,
                         ticktext=[f"{weekday_name(start_weekday, d)[:3]} {d}" for d in days])
    if now is not None:
        fig.add_vline(x=now, line=dict(color=pal.ink, width=2))
        fig.add_annotation(x=now, y=1, xref="x", yref="paper", yanchor="bottom", showarrow=False,
                           text="<b>Now</b>", font=dict(size=13, color=pal.ink))
    fig.update_yaxes(
        tickvals=list(range(len(lanes))), ticktext=list(lanes), range=[len(lanes) - 0.5, -0.5],
        showgrid=False, zeroline=False, tickfont=dict(family=theme.FONT_TEXT, size=14, color=pal.ink),
    )
    fig.update_layout(
        height=112 * len(lanes) + 130, margin=dict(l=10, r=10, t=40, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.04, x=0, title_text="Purpose: "),
    )
    return theme.apply(fig)


def build_branch_timeline(
    branch, script, players: Sequence[str], start_weekday: Optional[str] = None,
    weekends: bool = False, now: Optional[tuple[int, str]] = None,
    released: Optional[set] = None, key_of=None, taken: Optional[dict[str, int]] = None,
) -> go.Figure:
    """The inject timeline of one path of the script (`branch`: engine.script.BranchScript).

    On the control console: `now` is the simulated clock, `released` the keys of the injects
    already out and `taken` the day each recorded decision was taken.
    """
    names = script.actors
    stakeholders = [role.name for role in script.roles.values()]
    marks = timeline_marks(branch, players, names, stakeholders, released, key_of)
    windows = timeline_windows(branch, players, names, script.clock["decision_due"], taken)
    lanes = [names[actor] for actor in players] + [STAKEHOLDER_LANE]
    last_day = max([branch.result.end_day] + [mark["day"] for mark in marks])
    return build_inject_timeline(
        marks, windows, lanes, last_day, start_weekday, weekends,
        None if now is None else clock_x(now[0], now[1] or "00:00"),
    )


# ----------------------------------------------------------------- cross-border group (phase F)

_GROUP_STAGES = ("recovery", "early_intervention", "likely_to_fail", "failed")
_STAGE_SYMBOLS = {"recovery": "circle", "early_intervention": "square",
                  "likely_to_fail": "diamond", "failed": "x"}


def _group_colors(pal: theme.Palette) -> dict[str, str]:
    """One colour per entity, the same in every chart of a group."""
    return {"parent": pal.accent, "subsidiary": pal.purposes["challenge"]}


def group_stage_days(run: SimulationResult) -> list[tuple[int, str]]:
    """(day, status) for the first day an entity's status is each stage."""
    seen, out = set(), []
    for row in run.rows:
        status = str(row["status"])
        if status in _GROUP_STAGES and status not in seen:
            seen.add(status)
            out.append((int(row["day"]), status))
    return out


def restriction_label(result) -> str:
    """The restriction of transfers on a group run, in words ("" if none)."""
    if result.ring_fence_from is None:
        return ""
    record = next((r for r in result.decisions if r.decision == "restrict_transfers"), None)
    return record.option_name if record is not None else "Full ring-fence, no further lending"


def build_group_timeline(result, unit: str, start_weekday: Optional[str] = None,
                         weekends: bool = False) -> go.Figure:
    """The two entities of a group on one day axis: liquidity buffer (each entity and
    consolidated), deposits lost, and what is lent inside the group (transfers of the day as
    bars, the amount outstanding as a line). Markers give the first day of each stage; a
    vertical line marks the day a restriction of transfers takes effect.

    `result` comes from engine.group.simulate_group.
    """
    pal = theme.palette()
    colors = _group_colors(pal)
    group = result.group
    last = max(result.parent.end_day, result.subsidiary.end_day)
    # Two unrelated banks of a system lend nothing to each other: no third panel, no total.
    linked = group.kind != "system"
    fig = make_subplots(rows=3 if linked else 2, cols=1, shared_xaxes=True, vertical_spacing=0.08,
                        row_heights=[0.46, 0.24, 0.30] if linked else [0.62, 0.38])
    titles = (f"Liquidity buffer ({unit})", "Deposits lost (%)", f"Lent inside the group ({unit})")
    titles = titles if linked else titles[:2]
    for row, title in enumerate(titles, start=1):
        domain = fig.layout[f"yaxis{'' if row == 1 else row}"].domain
        fig.add_annotation(x=1, y=domain[1], xref="paper", yref="paper", text=f"<b>{title}</b>",
                           showarrow=False, xanchor="right", yanchor="bottom", yshift=2, xshift=-4,
                           font=dict(size=16 if row == 1 else 14))
    for role in ("parent", "subsidiary"):
        run, name = result.of(role), group.entity(role).name
        days = [row["day"] for row in run.rows]
        fig.add_trace(go.Scatter(
            x=days, y=[row["liquidity_buffer"] for row in run.rows], mode="lines", name=name,
            legendgroup=role, line=dict(color=colors[role], width=3),
            hovertemplate=name + ", day %{x}: %{y:.2f} " + unit + "<extra></extra>"), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=days, y=[row["cum_deposit_loss_pct"] for row in run.rows], mode="lines", name=name,
            legendgroup=role, showlegend=False, line=dict(color=colors[role], width=2.5),
            hovertemplate=name + ", day %{x}: %{y:.1f}%<extra></extra>"), row=2, col=1)
        if linked:
          fig.add_trace(go.Scatter(
            x=days, y=[row["intragroup_claim"] for row in run.rows], mode="lines",
            name=f"Lent by {name}, outstanding", legendgroup=f"lent_{role}",
            line=dict(color=colors[role], width=2.5, shape="hv"),
            hovertemplate=f"Lent by {name}, outstanding on day " + "%{x}: %{y:.2f} " + unit
            + "<extra></extra>"), row=3, col=1)
        given = [(row["day"], row["support_given"]) for row in run.rows if row["support_given"] > 0]
        if given and linked:
            fig.add_trace(go.Bar(
                x=[day for day, _ in given], y=[amount for _, amount in given], width=0.5,
                name=f"Lent by {name} that day", legendgroup=f"lent_{role}",
                marker=dict(color=theme.mix(colors[role], pal.surface, 0.45),
                            line=dict(color=colors[role], width=1.5)),
                text=[f"{amount:.2f}" for _, amount in given], textposition="outside",
                textfont=dict(family=theme.FONT_FIGURES, size=13, color=pal.ink),
                hovertemplate=f"{name} lends " + "%{y:.2f} " + unit + " on day %{x}<extra></extra>",
            ), row=3, col=1)
        # The line is named where it ends, so the chart reads without the legend.
        # Under the line's end when the other entity's line passes just above it there.
        theirs = result.of("subsidiary" if role == "parent" else "parent").rows
        end = run.rows[-1]["liquidity_buffer"]
        under = (days[-1] < len(theirs) and end > 0
                 and 0 < theirs[days[-1]]["liquidity_buffer"] - end < 0.25 * max(end, 1e-9))
        fig.add_annotation(
            x=days[-1], y=end, xref="x", yref="y", showarrow=False,
            text=f"<b>{name}</b>", xanchor="right" if days[-1] > last / 2 else "left",
            yanchor="top" if under else "bottom", yshift=-8 if under else 6,
            xshift=-6 if days[-1] > last / 2 else 8,
            font=dict(size=14, color=colors[role]))
        # First day of each stage, on the entity's buffer line: colour, shape and word.
        for day, status in group_stage_days(run):
            stage = theme.stage_of(status)
            fig.add_trace(go.Scatter(
                x=[day], y=[run.rows[day]["liquidity_buffer"]], mode="markers", showlegend=False,
                name=theme.STAGE_LABELS[stage],
                marker=dict(size=13, symbol=_STAGE_SYMBOLS[status], color=pal.stages[stage],
                            line=dict(color=pal.ink, width=1.5)),
                hovertemplate=f"{name}: {theme.STAGE_LABELS[stage].lower()} from day {day}<extra></extra>",
            ), row=1, col=1)
    reached = sorted({status for role in ("parent", "subsidiary")
                      for _, status in group_stage_days(result.of(role))}, key=_GROUP_STAGES.index)
    for status in reached:  # the legend's entries for the stage markers
        stage = theme.stage_of(status)
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers", name=f"First day: {theme.STAGE_LABELS[stage].lower()}",
            marker=dict(size=12, symbol=_STAGE_SYMBOLS[status], color=pal.stages[stage],
                        line=dict(color=pal.ink, width=1.5))), row=1, col=1)
    if linked:
      fig.add_trace(go.Scatter(
        x=[row["day"] for row in result.consolidated],
        y=[row["liquidity_buffer"] for row in result.consolidated], mode="lines",
        name="Group, consolidated", line=dict(color=pal.ink, width=2, dash="dot"),
        hovertemplate="Group, day %{x}: %{y:.2f} " + unit + "<extra></extra>"), row=1, col=1)
    add_calendar(fig, start_weekday, last, weekends)
    if result.ring_fence_from is not None:
        color = pal.stages["likely_to_fail"]
        fig.add_vline(x=result.ring_fence_from, line=dict(color=color, width=2.5, dash="dash"))
        fig.add_annotation(
            x=result.ring_fence_from, y=1.0, xref="x", yref="paper", showarrow=False,
            yanchor="bottom", yshift=22, xanchor="left" if result.ring_fence_from < last / 2 else "right",
            text=f"From day {result.ring_fence_from}: {_short(restriction_label(result), 44)}",
            font=dict(size=13, color=theme.text_on(color, pal)), bgcolor=color, borderpad=3)
    fig.update_yaxes(rangemode="tozero")
    fig.update_xaxes(title_text="Simulated day", row=3 if linked else 2, col=1)
    fig.update_layout(
        height=760 if linked else 620, margin=dict(l=10, r=10, t=70, b=10), barmode="overlay",
        legend=dict(orientation="h", yanchor="top", y=-0.1, x=0),
    )
    return theme.apply(fig)


def build_group_comparison(first, second, first_name: str, second_name: str, unit: str,
                           start_weekday: Optional[str] = None, weekends: bool = False) -> go.Figure:
    """Two paths of the same group: the liquidity buffer of each entity on both paths.
    Solid line: the second path; dashed: the first."""
    pal = theme.palette()
    colors = _group_colors(pal)
    group = second.group
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.1)
    last = 0
    for row, role in enumerate(("parent", "subsidiary"), start=1):
        name = group.entity(role).name
        domain = fig.layout[f"yaxis{'' if row == 1 else row}"].domain
        fig.add_annotation(x=1, y=domain[1], xref="paper", yref="paper", showarrow=False,
                           text=f"<b>{name}: liquidity buffer ({unit})</b>", xanchor="right",
                           yanchor="bottom", yshift=2, xshift=-4, font=dict(size=15))
        for result, path, dash in ((first, first_name, "dash"), (second, second_name, "solid")):
            run = result.of(role)
            last = max(last, run.end_day)
            fig.add_trace(go.Scatter(
                x=[r["day"] for r in run.rows], y=[r["liquidity_buffer"] for r in run.rows],
                mode="lines", name=path, legendgroup=path, showlegend=row == 1,
                line=dict(color=colors[role] if dash == "solid" else pal.reference, width=3, dash=dash),
                hovertemplate=f"{name}, {path}, day " + "%{x}: %{y:.2f} " + unit + "<extra></extra>",
            ), row=row, col=1)
            if run.failure_day is not None:
                color = pal.stages["default"]
                fig.add_trace(go.Scatter(
                    x=[run.failure_day], y=[run.rows[-1]["liquidity_buffer"]], mode="markers+text",
                    showlegend=False, name="Payments default", text=[f"Payments default, day {run.failure_day}"],
                    textposition="top right", textfont=dict(size=13, color=pal.ink),
                    marker=dict(size=13, symbol="x", color=color), hoverinfo="skip",
                ), row=row, col=1)
    add_calendar(fig, start_weekday, last, weekends)
    fig.update_yaxes(rangemode="tozero")
    fig.update_xaxes(title_text="Simulated day", row=2, col=1)
    fig.update_layout(height=560, margin=dict(l=10, r=10, t=50, b=10),
                      legend=dict(orientation="h", yanchor="bottom", y=1.06, x=0))
    return theme.apply(fig)


def group_map_state(result, day: int) -> dict:
    """What the group map shows for one day: each entity's stage and buffer, the transfer of
    the day, what is outstanding, and whether a restriction is in effect."""
    entities = {}
    for role in ("parent", "subsidiary"):
        run = result.of(role)
        row = run.rows[min(day, len(run.rows) - 1)]
        entities[role] = dict(
            name=result.group.entity(role).name, country=result.group.entity(role).country,
            status=str(row["status"]), ended=day > run.end_day,
            buffer=float(row["liquidity_buffer"]), lent=float(row["intragroup_claim"]),
            borrowed=float(row["intragroup_liability"]))
    moved = [t for t in result.transfers if t.day == day]
    restricted = result.ring_fence_from is not None and day >= result.ring_fence_from
    return dict(day=day, entities=entities, transfers=moved, restricted=restricted,
                restriction=restriction_label(result) if restricted else "",
                largest=max((t.amount for t in result.transfers), default=0.0))


def build_group_map(result, day: int, unit: str, teams: dict[str, Sequence[str]],
                    weekday: str = "") -> go.Figure:
    """Schematic map of a cross-border group on one day: the two countries, the entity and
    the authorities of each, the liquidity lent that day as an arrow whose width follows the
    amount, and a restriction of transfers as a blocked arrow with the word on it.

    A diagram, not geography: the countries are fictitious. `teams`: the names of each
    country's authorities, per entity.
    """
    pal = theme.palette()
    state = group_map_state(result, day)
    fig = go.Figure()
    system = result.group.kind == "system"
    # A group: two countries side by side. A system: one country holding both banks.
    boxes = {"parent": (0.3, 9.7)} if system else {"parent": (0.3, 4.5), "subsidiary": (5.5, 9.7)}
    centres = {"parent": 2.4, "subsidiary": 7.6}
    half_w, node_y, half_h = 1.55, 3.7, 0.8
    for role, (x0, x1) in boxes.items():
        entity = state["entities"][role]
        side = "" if system else " (home)" if role == "parent" else " (host)"
        fig.add_shape(type="rect", x0=x0, x1=x1, y0=0.4, y1=5.6, layer="below",
                      fillcolor=theme.mix(pal.line, pal.surface, 0.25),
                      line=dict(color=pal.line, width=1.5))
        fig.add_annotation(x=x0 + 0.15, y=5.6, xref="x", yref="y", showarrow=False, xanchor="left",
                           yanchor="top", yshift=-6, text=f"<b>{entity['country']}</b>{side}",
                           font=dict(size=17, color=pal.ink))
    for role in ("parent", "subsidiary"):
        entity = state["entities"][role]
        x = centres[role]
        stage = theme.stage_of(entity["status"])
        fig.add_shape(type="rect", x0=x - half_w, x1=x + half_w, y0=node_y - half_h, y1=node_y + half_h,
                      fillcolor=pal.surface, line=dict(color=pal.ink, width=1.5))
        fig.add_shape(type="rect", x0=x - half_w, x1=x - half_w + 0.14, y0=node_y - half_h,
                      y1=node_y + half_h, line_width=0, fillcolor=pal.stages[stage])
        lines = [f"<b>{entity['name']}</b>",
                 "Run ended" if entity["ended"] else
                 f"{theme.STAGE_ICONS[stage]} {theme.STAGE_LABELS[stage]}",
                 f"Liquidity buffer {entity['buffer']:.2f} {unit}"]
        fig.add_annotation(x=x + 0.07, y=node_y, xref="x", yref="y", showarrow=False,
                           text="<br>".join(lines), font=dict(size=15, color=pal.ink))
        if not system:
            fig.add_annotation(
                x=x, y=1.55, xref="x", yref="y", showarrow=False, align="center",
                text="Authorities (teams):<br>" + "<br>".join(teams[role]),
                font=dict(size=13, color=pal.muted))
    left, right = centres["parent"] + half_w, centres["subsidiary"] - half_w
    middle = (left + right) / 2
    if system:
        fig.add_annotation(
            x=5.0, y=1.5, xref="x", yref="y", showarrow=False, align="center",
            text="The same authorities (teams) for both banks:<br>" + " · ".join(teams["parent"]),
            font=dict(size=13, color=pal.muted))
        fig.add_shape(type="line", x0=left, x1=right, y0=node_y, y1=node_y,
                      line=dict(color=pal.reference, width=2.5, dash="dot"))
        fig.add_annotation(
            x=middle, y=node_y + 0.5, xref="x", yref="y", showarrow=False,
            text=f"No lending between them.<br>Linked by confidence only<br>"
                 f"(factor {result.group.confidence_link.value:g} on the contagion shocks)",
            font=dict(size=13, color=pal.muted), bgcolor=pal.surface, borderpad=3)
        fig.update_xaxes(visible=False, range=[0, 10], fixedrange=True)
        fig.update_yaxes(visible=False, range=[0, 6], fixedrange=True)
        when = f"day {day}" + (f", {weekday}" if weekday else "")
        fig.update_layout(
            height=520, margin=dict(l=10, r=10, t=50, b=10), plot_bgcolor="rgba(0,0,0,0)",
            title=dict(text=f"{result.group.name} at the close of {when}", x=0.01, xanchor="left"))
        return theme.apply(fig)
    blocked_color = pal.stages["likely_to_fail"]
    if state["transfers"]:
        for item in state["transfers"]:
            to_parent = item.borrower == "parent"
            start, end = (right, left) if to_parent else (left, right)
            width = 3 + 9 * item.amount / state["largest"]
            fig.add_annotation(x=end, y=node_y, ax=start, ay=node_y, xref="x", yref="y", axref="x",
                               ayref="y", showarrow=True, arrowhead=2, arrowsize=0.6,
                               arrowwidth=width, arrowcolor=pal.accent, text="")
            fig.add_annotation(
                x=middle, y=node_y + 0.55, xref="x", yref="y", showarrow=False,
                text=f"<b>{item.amount:.2f} {unit}</b><br>lent on day {day}"
                + (", asked for early" if item.early else ""),
                font=dict(size=14, color=pal.ink), bgcolor=pal.surface, borderpad=3)
    else:
        fig.add_shape(type="line", x0=left, x1=right, y0=node_y, y1=node_y,
                      line=dict(color=blocked_color if state["restricted"] else pal.reference,
                                width=2.5, dash="dash" if state["restricted"] else "dot"))
        fig.add_annotation(x=middle, y=node_y + 0.4, xref="x", yref="y", showarrow=False,
                           text=f"No transfer on day {day}", font=dict(size=14, color=pal.muted),
                           bgcolor=pal.surface, borderpad=3)
    if state["restricted"]:
        # A bar across the line from the subsidiary: its lending is restricted.
        fig.add_shape(type="line", x0=right - 0.25, x1=right - 0.25, y0=node_y - 0.42, y1=node_y + 0.42,
                      line=dict(color=blocked_color, width=7))
        fig.add_annotation(
            x=middle, y=node_y - 0.7, xref="x", yref="y", showarrow=False,
            text=f"<b>Restricted</b>: {_short(state['restriction'], 40)}",
            font=dict(size=14, color=theme.text_on(blocked_color, pal)), bgcolor=blocked_color,
            borderpad=4)
    outstanding = [
        f"{state['entities'][role]['name']} has lent {state['entities'][role]['lent']:.2f} {unit} in all"
        for role in ("parent", "subsidiary") if state["entities"][role]["lent"] > 0]
    fig.add_annotation(x=5.0, y=0.05, xref="x", yref="y", showarrow=False, yanchor="bottom",
                       text="; ".join(outstanding) or "Nothing is lent inside the group so far",
                       font=dict(size=14, color=pal.ink))
    fig.update_xaxes(visible=False, range=[0, 10], fixedrange=True)
    fig.update_yaxes(visible=False, range=[0, 6], fixedrange=True)
    when = f"day {day}" + (f", {weekday}" if weekday else "")
    fig.update_layout(
        height=600, margin=dict(l=10, r=10, t=50, b=10), plot_bgcolor="rgba(0,0,0,0)",
        title=dict(text=f"{result.group.name} at the close of {when}", x=0.01, xanchor="left"),
    )
    return theme.apply(fig)


def build_overview(result: SimulationResult, unit: str, start_weekday: Optional[str] = None,
                   weekends: bool = False) -> go.Figure:
    """A run at a glance, small, for the home page: the liquidity buffer by day, the stage
    the bank is in as a coloured strip with the stage's name where it starts, and the
    decisions taken as marks on the line."""
    pal = theme.palette()
    rows = result.rows
    days = [row["day"] for row in rows]
    last = days[-1]
    fig = go.Figure(go.Scatter(
        x=days, y=[row["liquidity_buffer"] for row in rows], mode="lines", name="Liquidity buffer",
        line=dict(color=pal.accent, width=3),
        hovertemplate="Day %{x}: %{y:.2f} " + unit + "<extra></extra>", showlegend=False))
    # The stage of each day, as a strip above the plot.
    stages = []
    for row in rows:
        declared = result.foltf_day is not None and row["day"] >= result.foltf_day
        stages.append("foltf" if declared and row["status"] != "failed" else theme.stage_of(str(row["status"])))
    start = 0
    for index in range(1, len(stages) + 1):
        if index == len(stages) or stages[index] != stages[start]:
            stage, x0, x1 = stages[start], days[start] - 0.5, days[index - 1] + 0.5
            color = pal.stages[stage]
            fig.add_shape(type="rect", xref="x", yref="paper", x0=max(x0, 0), x1=min(x1, last + 0.5),
                          y0=1.02, y1=1.14, line_width=0, fillcolor=color)
            if x1 - x0 >= 2 or index == len(stages):
                fig.add_annotation(
                    x=max(x0, 0) + 0.15, y=1.08, xref="x", yref="paper", showarrow=False,
                    xanchor="left", yanchor="middle",
                    text=f"{theme.STAGE_ICONS[stage]} {theme.STAGE_LABELS[stage]}",
                    font=dict(size=12, color=theme.text_on(color, pal)))
            start = index
    # Decisions, where they take effect: one mark per day, named below the axis line.
    by_day: dict[int, list[str]] = {}
    for record in result.decision_log:
        if record.effect_day <= last:
            by_day.setdefault(record.effect_day, []).append(f"{record.decision_name}: {record.option_name}")
    for number, (day, names) in enumerate(sorted(by_day.items())):
        value = rows[day]["liquidity_buffer"]
        fig.add_trace(go.Scatter(
            x=[day], y=[value], mode="markers", showlegend=False,
            marker=dict(size=13, symbol="triangle-down", color=pal.ink,
                        line=dict(color=pal.surface, width=1.5)),
            hovertemplate=f"Day {day}: " + "<br>".join(names) + "<extra></extra>"))
        fig.add_annotation(
            x=day, y=value, xref="x", yref="y", showarrow=True, arrowhead=0, arrowwidth=1,
            arrowcolor=pal.muted, ax=0, ay=-34 - 22 * (number % 2), xanchor="right" if day > last / 2 else "left",
            text=_short("; ".join(name.split(":")[1].strip() for name in names), 46),
            font=dict(size=12, color=pal.ink), bgcolor=pal.surface, borderpad=2)
    add_calendar(fig, start_weekday, last, weekends)
    fig.update_xaxes(range=[0, last + 0.5])
    fig.update_yaxes(rangemode="tozero", title_text=f"Liquidity buffer ({unit})")
    fig.update_layout(height=330, margin=dict(l=10, r=10, t=58, b=10))
    return theme.apply(fig)
