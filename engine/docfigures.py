"""Charts for the exported Word documents (dev/SPEC_UI.md, UI-4), drawn with matplotlib.

Pure functions: data in, PNG bytes out; nothing is read or written. Colours and typefaces
come from engine/style.py. Each chart is drawn from a small data object built first, so a
test can check what a chart shows without reading the image.
"""

from __future__ import annotations

import io
import logging
import textwrap
from dataclasses import dataclass
from typing import Optional, Sequence

import matplotlib
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

from engine import style
from engine.decisions import FIGURE_LABELS, OUTCOME_LABELS
from engine.script import BranchScript

# A typeface of the list that is not installed is skipped without a warning.
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

DPI = 200
# Figures shown in a player's summary, in order of preference: the first three in its scope.
SUMMARY_FIGURES = (
    "liquidity_buffer", "cum_deposit_loss_pct", "cet1_ratio", "survival_days",
    "unencumbered_collateral", "ela_funding", "dep_insured_retail", "outflow_total",
)
SUMMARY_PANELS = 3
# Kinds of inject that tell a player the bank has changed stage.
STAGE_KINDS = ("recovery", "early_intervention", "likely_to_fail", "failure", "foltf", "resolution")
# Lines of the scorecard compared in the counterfactual chart: (field, label, suffix).
COUNTERFACTUAL_LINES = (
    ("deposits_lost_pct", "Deposits lost", "%"),
    ("losses_booked", "Losses booked", "unit"),
    ("ela_outstanding", "ELA outstanding", "unit"),
    ("dgs_payout", "DGS payout", "unit"),
)


def _png(fig: Figure) -> bytes:
    buffer = io.BytesIO()
    FigureCanvasAgg(fig).print_png(buffer, metadata={"Software": None})
    return buffer.getvalue()


def _figure(width: float, height: float) -> Figure:
    pal = style.palette()
    fig = Figure(figsize=(width, height), dpi=DPI, facecolor=pal.surface)
    return fig


def _axis(axis, pal) -> None:
    axis.set_facecolor(pal.surface)
    for side in ("top", "right"):
        axis.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axis.spines[side].set_color(pal.line)
    axis.tick_params(colors=pal.muted, labelsize=7.5, length=3)
    axis.grid(axis="y", color=style.mix(pal.line, pal.surface, 0.6), linewidth=0.6)
    axis.set_axisbelow(True)


def _fonts() -> dict:
    return {"font.family": list(style.CHART_FONTS), "font.size": 8.5,
            "text.color": style.palette().ink}


# ------------------------------------------------- what one player sees


@dataclass(frozen=True)
class PlayerSeries:
    """What one player sees on one path, taken from that player's own injects only."""

    actor: str
    branch: str  # heading of the branch
    end_day: int
    # (figure key, label with unit, ((day the figure refers to, value as shown), ...))
    figures: tuple[tuple[str, str, tuple[tuple[int, float], ...]], ...]
    stages: tuple[tuple[int, str, str], ...]  # (day told, kind, title)
    windows: tuple[tuple[str, int, int], ...]  # (decision name, day opened, deadline day)
    first_report_day: Optional[int]  # the earliest day its figures refer to


def _shown(name: str, value: float) -> float:
    return 100.0 * value if FIGURE_LABELS[name][1] == "ratio" else value


def _label(name: str, unit: str) -> str:
    label, kind = FIGURE_LABELS[name]
    return f"{label} ({'%' if kind in ('pct', 'ratio') else 'days' if kind == 'days' else unit})"


def player_series(branch: BranchScript, actor: str, unit: str, heading: str = "") -> PlayerSeries:
    """The series behind a player's summary chart: its regular reports at the day they
    refer to, the stage changes it is told of and its decision windows. Nothing here comes
    from anywhere but `branch.injects[actor]`."""
    injects = branch.injects[actor]
    points: dict[str, dict[int, float]] = {}
    for inject in injects:
        for figure in inject.figures:
            if figure.block or figure.name not in SUMMARY_FIGURES or figure.value == float("inf"):
                continue
            points.setdefault(figure.name, {})[figure.as_of_day] = _shown(figure.name, figure.value)
    chosen = [name for name in SUMMARY_FIGURES if name in points][:SUMMARY_PANELS]
    stages, windows, seen = [], [], set()
    for inject in injects:
        if inject.kind in STAGE_KINDS:
            stages.append((inject.day, inject.kind, inject.title))
        due = inject.decision_due
        if due is not None and due.decision not in seen:
            seen.add(due.decision)
            windows.append((due.name, inject.day, due.deadline_day))
    with_figures = [day for name in chosen for day in points[name]]
    return PlayerSeries(
        actor=actor, branch=heading or f"Branch {branch.label}: {branch.name}",
        end_day=max([branch.result.end_day] + [inject.day for inject in injects]),
        figures=tuple((name, _label(name, unit), tuple(sorted(points[name].items())))
                      for name in chosen),
        stages=tuple(stages), windows=tuple(windows),
        first_report_day=min(with_figures) if with_figures else None,
    )


def series_text(series: PlayerSeries) -> str:
    """The content of a summary chart in words (the image's alternative text)."""
    parts = [series.branch]
    for _, label, points in series.figures:
        parts.append(f"{label}: " + ", ".join(f"day {day} {value:.2f}" for day, value in points))
    parts += [f"Day {day}: {title}" for day, _, title in series.stages]
    parts += [f"{name}: open from day {start}, decide by day {end}" for name, start, end in series.windows]
    return ". ".join(parts) + "."


def player_summary_png(panels: Sequence[PlayerSeries], name: str) -> bytes:
    """One page: what a player sees over the exercise, one column per branch. Each row is
    one figure of its regular reports, at the day the figure refers to; vertical lines are
    the stage changes it is told of; the bars at the bottom are its decision windows; the
    shaded stretch is the time before its first report."""
    pal = style.palette()
    rows = max([len(series.figures) for series in panels] + [1])
    with matplotlib.rc_context(_fonts()):
        fig = _figure(6.6, 1.55 * rows + 2.3)
        grid = fig.add_gridspec(rows + 1, len(panels), height_ratios=[1.0] * rows + [0.75],
                                hspace=0.38, wspace=0.28, left=0.09, right=0.98, top=0.865,
                                bottom=0.07)
        fig.suptitle(f"What the {name[0].lower() + name[1:]} sees", x=0.09, y=0.985, ha="left",
                     fontsize=12, fontweight="bold", color=pal.ink)
        # The stage lines are named: colour is not the only signal.
        kinds = list(dict.fromkeys(style.stage_of(kind) for series in panels
                                   for _, kind, _ in series.stages))
        if kinds:
            fig.legend(
                [Line2D([], [], color=pal.stages[k], linewidth=1.4, linestyle=(0, (4, 2))) for k in kinds],
                [f"Told: {style.STAGE_LABELS[k]}" for k in kinds],
                loc="upper left", bbox_to_anchor=(0.075, 0.965), ncol=len(kinds), frameon=False,
                fontsize=7.5)
        for column, series in enumerate(panels):
            for row in range(rows):
                axis = fig.add_subplot(grid[row, column])
                _axis(axis, pal)
                axis.set_xlim(-0.5, series.end_day + 0.5)
                if row == 0:
                    axis.set_title(textwrap.fill(series.branch, 44), fontsize=8.5, loc="left",
                                   color=pal.ink)
                if series.first_report_day:
                    axis.axvspan(-0.5, series.first_report_day - 0.5, color=pal.line, alpha=0.45,
                                 linewidth=0)
                if row < len(series.figures):
                    _, label, points = series.figures[row]
                    axis.plot([day for day, _ in points], [value for _, value in points],
                              color=pal.accent, linewidth=1.6, marker="o", markersize=2.6)
                    axis.set_ylabel(textwrap.fill(label, 28), fontsize=7.5, color=pal.muted)
                    axis.set_ylim(bottom=min(0.0, min((v for _, v in points), default=0.0)))
                else:
                    axis.set_yticks([])
                    axis.text(0.5, 0.5, "No figure in this player's reports", fontsize=7.5,
                              transform=axis.transAxes, ha="center", color=pal.muted)
                for day, kind, _ in series.stages:
                    axis.axvline(day, color=style.stage_color(kind, pal), linewidth=1.4,
                                 linestyle=(0, (4, 2)))
                axis.tick_params(labelbottom=False)
            axis = fig.add_subplot(grid[rows, column])
            _axis(axis, pal)
            axis.grid(False)
            axis.set_xlim(-0.5, series.end_day + 0.5)
            axis.set_ylim(-0.6, max(1, len(series.windows)) - 0.4)
            axis.set_yticks([])
            axis.invert_yaxis()
            axis.set_xlabel("Simulated day", fontsize=7.5, color=pal.muted)
            for position, (decision, start, end) in enumerate(series.windows):
                axis.barh(position, end - start + 0.5, left=start, height=0.6,
                          color=style.mix(pal.accent, pal.surface, 0.25), edgecolor=pal.accent,
                          linewidth=0.8)
                axis.text(start + 0.15, position, textwrap.shorten(decision, 34, placeholder="..."),
                          va="center", fontsize=6.8, color=pal.ink)
            if not series.windows:
                axis.text(0.5, 0.5, "No decision of its own", transform=axis.transAxes,
                          ha="center", fontsize=7.5, color=pal.muted)
            for day, kind, title in series.stages:
                axis.axvline(day, color=style.stage_color(kind, pal), linewidth=1.4,
                             linestyle=(0, (4, 2)))
        return _png(fig)


# ------------------------------------------------- closing report


def decision_rows(report) -> list[dict]:
    """The rows of the decision timeline chart, from the report's own decision lines."""
    return [
        dict(day=line.day, label=f"{line.actor}: {line.decision_name}", option=line.option_name,
             as_planned=line.as_planned, applied=line.applied, by_control=line.by_control,
             minutes=line.minutes)
        for line in report.decisions
    ]


def decision_timeline_png(report) -> bytes:
    """Decision timeline: one row per recorded decision on the simulated day axis. A filled
    circle is a decision as planned, a diamond one that differs from the pre-planned path,
    a hollow mark one that was logged without changing the path."""
    pal = style.palette()
    rows = decision_rows(report)
    with matplotlib.rc_context(_fonts()):
        height = 0.42 * max(1, len(rows)) + 1.25
        fig = _figure(9.6, height)
        axis = fig.add_axes([0.34, 0.95 / height, 0.64, 1 - 1.1 / height])
        _axis(axis, pal)
        axis.grid(axis="x", color=style.mix(pal.line, pal.surface, 0.6), linewidth=0.6)
        last = max([row["day"] for row in rows] + [1])
        axis.set_xlim(-0.5, last + max(4.0, 0.6 * last))
        axis.set_ylim(len(rows) - 0.4, -0.6)
        axis.set_yticks(range(len(rows)))
        axis.set_yticklabels([textwrap.shorten(row["label"], 52, placeholder="...") for row in rows],
                             fontsize=8, color=pal.ink)
        axis.set_xticks(range(0, last + 1, max(1, last // 12)))
        axis.set_xlabel("Simulated day the decision was taken", fontsize=8, color=pal.muted)
        for position, row in enumerate(rows):
            color = pal.purposes["advance"] if row["as_planned"] else pal.purposes["challenge"]
            axis.plot([-0.5, row["day"]], [position, position], color=pal.line, linewidth=0.8)
            axis.plot(row["day"], position, marker="o" if row["as_planned"] else "D",
                      markersize=7, color=color, markeredgewidth=1.5,
                      markerfacecolor=color if row["applied"] else pal.surface)
            note = textwrap.shorten(row["option"], 30, placeholder="...")
            if row["minutes"] is not None:
                note += f" ({row['minutes']:.0f} real min)"
            if row["by_control"]:
                note += ", by the control team"
            axis.text(row["day"] + 0.35, position, note, va="center", fontsize=7.3, color=pal.ink)
        handles = [
            axis.plot([], [], "o", color=pal.purposes["advance"], markersize=6)[0],
            axis.plot([], [], "D", color=pal.purposes["challenge"], markersize=6)[0],
            axis.plot([], [], "o", color=pal.muted, markerfacecolor=pal.surface, markersize=6)[0],
        ]
        fig.legend(handles, ["As on the pre-planned path", "Different from the pre-planned path",
                             "Hollow: logged, the path did not change"],
                   loc="lower left", bbox_to_anchor=(0.02, 0.0), ncol=3, frameon=False, fontsize=7.5)
        return _png(fig)


def counterfactual_rows(report) -> list[dict]:
    """The bars of the counterfactual chart: what happened, then each computed change."""
    rows = [dict(label="As it happened", outcome=OUTCOME_LABELS[report.actual.outcome],
                 actual=True, **{field: getattr(report.actual, field) for field, _, _ in COUNTERFACTUAL_LINES})]
    for case in report.counterfactuals:
        if case.possible and case.scorecard is not None:
            rows.append(dict(
                label=f"{case.decision_name}: {case.change}",
                outcome=OUTCOME_LABELS[case.scorecard.outcome], actual=False,
                **{field: getattr(case.scorecard, field) for field, _, _ in COUNTERFACTUAL_LINES}))
    return rows


def counterfactual_png(report) -> bytes:
    """Counterfactual bars: for each line of the scorecard, what happened against the model's
    result with one decision changed. The outcome of each case is written under its name."""
    pal = style.palette()
    rows = counterfactual_rows(report)
    with matplotlib.rc_context(_fonts()):
        height = 0.3 * len(rows) + 1.0
        fig = _figure(9.6, height)
        grid = fig.add_gridspec(1, len(COUNTERFACTUAL_LINES), left=0.47, right=0.985,
                                top=1 - 0.55 / height, bottom=0.3 / height, wspace=0.18)
        for column, (field, label, suffix) in enumerate(COUNTERFACTUAL_LINES):
            axis = fig.add_subplot(grid[0, column])
            _axis(axis, pal)
            axis.grid(axis="y", visible=False)
            axis.grid(axis="x", color=style.mix(pal.line, pal.surface, 0.6), linewidth=0.6)
            values = [row[field] for row in rows]
            axis.barh(range(len(rows)), values, height=0.62,
                      color=[pal.accent if row["actual"] else pal.reference for row in rows])
            top = max(values) or 1.0
            axis.set_xlim(0, top * 1.38)
            for position, value in enumerate(values):
                axis.text(value + top * 0.03, position, f"{value:.1f}" if suffix == "%" else f"{value:.2f}",
                          va="center", fontsize=6.8, color=pal.ink, family="monospace")
            axis.set_ylim(len(rows) - 0.45, -0.55)
            axis.set_title(f"{label}\n({'%' if suffix == '%' else report.unit})", fontsize=7.5,
                           loc="left", color=pal.ink)
            axis.set_yticks(range(len(rows)))
            if column == 0:
                axis.set_yticklabels(
                    [textwrap.fill(f"{row['label']}  ->  {row['outcome']}", 78) for row in rows],
                    fontsize=6.6, color=pal.ink)
            else:
                axis.set_yticklabels([])
        return _png(fig)
