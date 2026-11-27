"""dev/SPEC_UI.md, UI-1 (visual system) and UI-2 (charts)."""

import inspect
import re
import tomllib
from itertools import combinations
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app import charts, demo, theme, ui
from engine import objective
from engine.config import load_config
from engine.contagion import simulate_contagion
from engine.decisions import OUTCOME_LABELS, Choice, choices_of
from engine.group import GroupChoices, simulate_group
from engine.model import simulate
from engine.montecarlo import bands, failure_day_counts, run_monte_carlo
from engine.resolution import resolution_levels
from engine.schema import PLAYERS
from engine.script import build_branch, information_map

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
APP_PATH = str(APP / "main.py")
CONFIG = load_config()
HORIZON = int(CONFIG.model.horizon_days.value)
SHOWCASE = CONFIG.script.showcase
BANK, SCENARIO = CONFIG.banks[SHOWCASE.bank], CONFIG.scenarios[SHOWCASE.scenario]
UNIT = BANK.unit


def run(path: str, bank=BANK, scenario=SCENARIO):
    return simulate(bank, CONFIG.model, scenario, CONFIG.decisions,
                    choices_of(CONFIG.decisions.paths[path]))


RESULT = run(SHOWCASE.path)
OTHER = run(SHOWCASE.compare_path)
BRANCH = build_branch("A", "Path", BANK, CONFIG.model, SCENARIO, CONFIG.decisions, CONFIG.script,
                      choices_of(CONFIG.decisions.paths[SHOWCASE.path]), with_points=False)
LATE = run("act_late")
GROUP_FREE = simulate_group(CONFIG.groups["showcase"], CONFIG.banks, CONFIG.model, CONFIG.scenarios,
                            "fast_run", CONFIG.decisions, GroupChoices(), "parent", CONFIG.college)
GROUP_RUN = simulate_group(
    CONFIG.groups["showcase"], CONFIG.banks, CONFIG.model, CONFIG.scenarios, "fast_run",
    CONFIG.decisions, GroupChoices(group=(Choice("restrict_transfers", "ring_fence", 4),)),
    "parent", CONFIG.college)
PALETTES = list(theme.PALETTES.values())
VISIONS = (None, *theme.CVD_MATRICES)
AA = 4.5  # WCAG AA, normal text
APART = 8.0  # OKLab distance x 100 below which two colours are hard to tell apart


# ------------------------------------------------- UI-1: tokens


@pytest.mark.parametrize("pal", PALETTES, ids=lambda p: p.key)
def test_palette_has_every_token(pal):
    assert set(pal.stages) == set(theme.STAGES) == set(theme.STAGE_LABELS) == set(theme.STAGE_ICONS)
    assert set(pal.sources) == set(theme.SOURCES) == set(theme.SOURCE_LABELS)
    assert len(set(pal.stages.values())) == len(theme.STAGES)
    assert len(set(theme.STAGE_ICONS.values())) == len(theme.STAGES)  # a shape per stage


@pytest.mark.parametrize("pal", PALETTES, ids=lambda p: p.key)
def test_text_contrast_is_wcag_aa(pal):
    pairs = {
        "ink on paper": (pal.ink, pal.paper),
        "ink on surface": (pal.ink, pal.surface),
        "muted on paper": (pal.muted, pal.paper),
        "muted on surface": (pal.muted, pal.surface),
        "accent on paper": (pal.accent, pal.paper),
        "text on accent": (pal.on_accent, pal.accent),
        "header text": (pal.paper, pal.ink),
    }
    for stage, color in pal.stages.items():
        pairs[f"label on {stage}"] = (theme.text_on(color, pal), color)
    for name, (text, background) in pairs.items():
        assert theme.contrast(text, background) >= AA, (pal.key, name)


@pytest.mark.parametrize("pal", PALETTES, ids=lambda p: p.key)
@pytest.mark.parametrize("vision", VISIONS, ids=lambda v: v or "normal vision")
def test_stage_scale_and_sources_stay_apart_for_colour_blind_viewers(pal, vision):
    for scale in (pal.stages, pal.sources):
        for (first, a), (second, b) in combinations(scale.items(), 2):
            assert theme.distance(a, b, vision) >= APART, (pal.key, vision, first, second)


def test_colour_maths():
    assert theme.contrast("#000000", "#FFFFFF") == pytest.approx(21)
    assert theme.distance("#123456", "#123456") == 0
    assert theme.mix("#000000", "#FFFFFF", 1) == "#000000" and theme.mix("#000000", "#FFFFFF", 0) == "#FFFFFF"
    assert theme.rgba("#FF0080", 0.5) == "rgba(255, 0, 128, 0.5)"
    # Red and green of equal lightness are far apart for normal vision, close for deuteranopia.
    assert theme.distance("#C8323C", "#4C8A2E") > 2 * theme.distance("#C8323C", "#4C8A2E", "deuteranopia")


def test_every_engine_word_has_one_stage():
    for status in ("normal", "recovery", "early_intervention", "likely_to_fail", "failed",
                   "resolution", "liquidation"):
        assert theme.stage_of(status) in theme.STAGES
    for outcome in OUTCOME_LABELS:
        assert theme.stage_of(outcome) in theme.STAGES
    assert theme.stage_of("failed") == theme.stage_of("payments_default") == "default"
    with pytest.raises(ValueError):
        theme.stage_of("panic")
    badge = theme.stage_badge("failed")
    assert theme.STAGE_LABELS["default"] in badge and theme.STAGE_ICONS["default"] in badge
    assert theme.palette().stages["default"] in badge  # colour and label together


def test_there_is_one_palette_and_the_environment_cannot_change_it(monkeypatch):
    assert list(theme.PALETTES) == ["A"] and theme.palette().key == "A"
    monkeypatch.setenv("CRISIS_PALETTE", "B")
    assert theme.palette().key == "A"
    with pytest.raises(ValueError):
        theme.palette("B")
    assert "CRISIS_PALETTE" not in (ROOT / "start.sh").read_text()


def test_streamlit_config_agrees_with_palette_a_and_removes_the_chrome(capsys):
    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text())
    assert config["theme"] == theme.streamlit_theme(theme.PALETTES["A"])
    assert config["client"]["toolbarMode"] == "minimal"
    css = theme.CSS_FILE.read_text()
    for chrome in ("stAppDeployButton", "stMainMenu", "footer"):
        assert chrome in css
    theme.main()
    assert f'primaryColor = "{theme.palette().accent}"' in capsys.readouterr().out


def test_colours_and_typefaces_live_in_the_theme_only():
    literal = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|rgba?\((?!0,\s?0,\s?0,\s?0\))\s*\d")
    css = theme.CSS_FILE.read_text()
    assert not literal.search(css), literal.search(css)
    assert "var(--cse-" in css
    for path in [*APP.glob("*.py"), *(APP / "views").glob("*.py")]:
        if path.name == "theme.py":
            continue
        found = literal.search(path.read_text())
        assert not found, (path.name, found.group(0))
        assert "font-family" not in path.read_text(), path.name


def test_typefaces_are_local():
    """The demo may be offline: no font or stylesheet is fetched at runtime."""
    for text in (theme.CSS_FILE.read_text(), (ROOT / ".streamlit" / "config.toml").read_text(),
                 theme.FONT_TEXT, theme.FONT_FIGURES):
        assert "http" not in text and "@import" not in text and "url(" not in text
    template = theme.plotly_template()
    assert template.layout.font.family == theme.FONT_TEXT
    assert template.layout.xaxis.tickfont.family == theme.FONT_FIGURES
    assert theme.FONT_TEXT != theme.FONT_FIGURES


# ------------------------------------------------- UI-2: one template


def _tree():
    return objective.decision_map(BANK, CONFIG.model, SCENARIO, CONFIG.decisions,
                                  ("declare_foltf", "resolution_tool"))


def _figures() -> dict:
    """One figure from every chart builder of app/charts.py."""
    monte_carlo = run_monte_carlo(BANK, CONFIG.model, SCENARIO, 40, 1, CONFIG.decisions,
                                  choices_of(CONFIG.decisions.paths[SHOWCASE.path]))
    contagion = simulate_contagion(
        CONFIG.banks["digital"], CONFIG.scenarios["fast_run"], CONFIG.banks["retail"],
        CONFIG.scenarios["baseline"], CONFIG.model, 1.0)
    return {
        "build_timeline": charts.build_timeline(
            RESULT, HORIZON, UNIT, bands(monte_carlo), start_weekday=SCENARIO.start_weekday),
        "build_failure_distribution": charts.build_failure_distribution(
            failure_day_counts(monte_carlo), HORIZON, SCENARIO.start_weekday),
        "build_contagion_chart": charts.build_contagion_chart(contagion, HORIZON),
        "build_funding": charts.build_funding(RESULT.table, UNIT, SCENARIO.start_weekday),
        "build_balance_sheet": charts.build_balance_sheet(RESULT.rows[0], RESULT.rows[-1], UNIT),
        "build_path_comparison": charts.build_path_comparison(
            LATE, RESULT, "Authorities act late", "Selected path", HORIZON, UNIT,
            SCENARIO.start_weekday),
        "build_scorecard": charts.build_scorecard(
            LATE.scorecard, RESULT.scorecard, "Authorities act late", "Selected path", UNIT),
        "build_resolution_stack": charts.build_resolution_stack(
            resolution_levels(BANK, CONFIG.model, RESULT), UNIT),
        "build_decision_map": charts.build_decision_map(_tree(), OUTCOME_LABELS),
        "build_information_map": charts.build_information_map(
            information_map(BRANCH, CONFIG.decisions, CONFIG.script, 6), "Monday"),
        "build_inject_timeline": charts.build_inject_timeline(
            charts.timeline_marks(BRANCH, PLAYERS, CONFIG.script.actors, []), [],
            [CONFIG.script.actors[a] for a in PLAYERS] + [charts.STAKEHOLDER_LANE],
            BRANCH.result.end_day),
        "build_branch_timeline": charts.build_branch_timeline(
            BRANCH, CONFIG.script, PLAYERS, SCENARIO.start_weekday, True),
        "build_group_timeline": charts.build_group_timeline(GROUP_RUN, UNIT, "Wednesday", True),
        "build_group_comparison": charts.build_group_comparison(
            GROUP_FREE, GROUP_RUN, "No decision", "Ring-fenced", UNIT),
        "build_overview": charts.build_overview(RESULT, UNIT, SCENARIO.start_weekday, True),
        "build_group_map": charts.build_group_map(
            GROUP_RUN, 4, UNIT, {"parent": ["Team"], "subsidiary": ["Team"]}),
    }


FIGURES = _figures()


def test_every_chart_builder_uses_the_one_template():
    builders = {name for name, item in inspect.getmembers(charts, inspect.isfunction)
                if name.startswith("build_") and item.__module__ == charts.__name__}
    # A new chart builder must be added to _figures, or this fails.
    assert builders == set(FIGURES)
    for name, figure in FIGURES.items():
        assert theme.uses_template(figure), name
        assert figure.layout.template.layout.font.family == theme.FONT_TEXT, name


def test_a_figure_without_the_template_is_caught():
    import plotly.graph_objects as go
    assert not theme.uses_template(go.Figure())
    assert theme.uses_template(theme.apply(go.Figure()))


def test_pages_build_no_figure_themselves_and_keep_the_template():
    for path in [APP / "main.py", APP / "design.py", APP / "demo.py", *(APP / "views").glob("*.py")]:
        text = path.read_text()
        assert "plotly" not in text, f"{path.name} builds or draws a figure itself"
    drawn = (APP / "ui.py").read_text()
    # Streamlit's own chart theme would replace the template's colours and typefaces.
    assert drawn.count("plotly_chart(") == 1 and "theme=None" in drawn
    for name in ("go.Figure(", "make_subplots("):
        assert name not in drawn


# ------------------------------------------------- UI-2: the charts


def _fills(figure) -> set:
    return {shape.fillcolor for shape in figure.layout.shapes if shape.fillcolor}


def test_timeline_has_a_large_chart_stage_bands_weekends_and_labelled_markers():
    figure = FIGURES["build_timeline"]
    pal = theme.palette()
    hero, small = figure.layout.yaxis.domain, figure.layout.yaxis2.domain
    assert hero[1] - hero[0] > 2 * (small[1] - small[0])
    assert figure.layout.xaxis.matches == figure.layout.xaxis3.matches or figure.layout.xaxis.matches == "x3"
    # Stage bands: one colour per stage reached, taken from the stage scale.
    stages = {stage for _, stage in charts.stage_by_day(RESULT)} - {"normal"}
    assert stages == {"recovery", "likely_to_fail", "foltf"}
    fills = _fills(figure)
    for stage in stages:
        assert any(fill.startswith(theme.rgba(pal.stages[stage], 1)[:-4]) for fill in fills), stage
    assert theme.rgba(pal.ink, 0.07) in fills  # weekends
    # Stage changes are labelled, in the stage's colour.
    labels = {a.text: a for a in figure.layout.annotations}
    assert labels["Recovery: day 2"].bgcolor == pal.stages["recovery"]
    assert labels["FoLtF declared: day 11"].bgcolor == pal.stages["foltf"]
    # Decisions and news are markers with a label.
    by_name = {trace.name: trace for trace in figure.data}
    decisions = next(t for name, t in by_name.items() if name.startswith("Decision"))
    assert list(decisions.x) == [r.decided_day for r in RESULT.decision_log]
    assert sum(text.startswith("<b>Decision:</b>") for text in labels) == len(RESULT.decision_log)
    news = next(t for name, t in by_name.items() if name.startswith("News"))
    assert set(news.text) == {"News"} and len(news.x) == 2
    # The band is light and the legend says what it is.
    band = by_name[charts.BAND_NAME]
    assert "not probabilities" in band.name and band.fillcolor == theme.rgba(pal.accent, 0.10)
    assert figure.layout.showlegend is not False


def test_stage_bands_follow_the_days():
    bands_ = charts.stage_bands(RESULT)
    assert bands_[0] == (2, 5, "recovery")
    assert [stage for _, _, stage in bands_] == ["recovery", "likely_to_fail", "foltf"]
    assert bands_[-1][1] == RESULT.end_day
    assert charts.stage_bands(RESULT, until_day=3) == [(2, 3, "recovery")]
    default = simulate(CONFIG.banks["digital"], CONFIG.model, CONFIG.scenarios["fast_run"])
    assert charts.stage_bands(default)[-1][2] == "default"


def test_funding_chart_stacks_the_sources_in_their_colours():
    figure = FIGURES["build_funding"]
    pal = theme.palette()
    assert figure.layout.barmode == "stack"
    assert [trace.name for trace in figure.data] == [theme.SOURCE_LABELS[s] for s in theme.SOURCES]
    assert [trace.marker.color for trace in figure.data] == [pal.sources[s] for s in theme.SOURCES]
    table = RESULT.table[RESULT.table["day"] >= 1]
    stacked = [sum(values) for values in zip(*(trace.y for trace in figure.data))]
    assert stacked == pytest.approx(list(table["outflow_total"]))


def test_balance_sheet_columns_balance():
    figure = FIGURES["build_balance_sheet"]
    opening, last = RESULT.rows[0], RESULT.rows[-1]
    totals = {}
    for trace in figure.data:
        for x, y in zip(trace.x, trace.y):
            totals[x] = totals.get(x, 0.0) + y
    assert len(totals) == 4
    for x, total in totals.items():
        expected = opening["total_assets"] if "opening" in x else last["total_assets"]
        assert total == pytest.approx(expected), x
    colors = charts.balance_sheet_colors()
    assert set(colors) == {c for c, _ in charts.ASSET_ITEMS + charts.LIABILITY_ITEMS}
    # The colours of the funding chart are kept for the same things.
    pal = theme.palette()
    assert colors["cash"] == pal.sources["cash"] and colors["ela_funding"] == pal.sources["ela"]


def test_branch_comparison_marks_where_the_paths_part():
    parted = charts.divergence_day(LATE, RESULT)
    first_difference = next(
        a["day"] for a, b in zip(LATE.rows, RESULT.rows)
        if a["status"] != b["status"] or abs(a["liquidity_buffer"] - b["liquidity_buffer"]) > 1e-9
        or abs(a["deposits_total"] - b["deposits_total"]) > 1e-9
        or abs(a["cet1_ratio"] - b["cet1_ratio"]) > 1e-9)
    assert parted == first_difference - 1
    assert charts.divergence_day(RESULT, RESULT) is None
    figure = FIGURES["build_path_comparison"]
    labels = [a.text for a in figure.layout.annotations]
    assert f"The paths part after day {parted}" in labels
    assert any(shape.type == "line" and shape.x0 == parted for shape in figure.layout.shapes)
    lines = [t for t in figure.data if t.mode == "lines"]
    assert {t.name for t in lines} == {"Authorities act late", "Selected path"}


def test_scorecard_is_a_table_of_bars_with_a_shared_scale_per_row():
    figure = FIGURES["build_scorecard"]
    bars = [t for t in figure.data if t.type == "bar"]
    assert len(bars) == 2 * len(charts.SCORECARD_LINES)
    for row, (field, _, digits, _) in enumerate(charts.SCORECARD_LINES):
        left, right = bars[2 * row], bars[2 * row + 1]
        assert left.x[0] == getattr(LATE.scorecard, field)
        assert right.x[0] == getattr(RESULT.scorecard, field)
        assert left.text[0] == f"{left.x[0]:.{digits}f}" and digits <= 2
        axes = [figure.layout[t.xaxis.replace("x", "xaxis")] for t in (left, right)]
        assert axes[0].range == axes[1].range
    labels = " ".join(a.text for a in figure.layout.annotations)
    assert OUTCOME_LABELS[LATE.outcome] in labels and OUTCOME_LABELS[RESULT.outcome] in labels


@pytest.mark.parametrize("result", [RESULT, OTHER], ids=["bail-in", "liquidation"])
def test_resolution_stack_shows_each_layer_written_down_against_what_remains(result):
    levels = resolution_levels(BANK, CONFIG.model, result)
    figure = charts.build_resolution_stack(levels, UNIT)
    assert figure.layout.barmode == "stack" and len(figure.layout.xaxis.tickvals) == 3
    for position, (level, summary) in enumerate(levels.items()):
        layers = charts.resolution_layers(summary)
        lines = summary.lines if summary.liquidation is None else summary.liquidation.lines
        assert sum(a + r for _, _, a, r in layers) == pytest.approx(sum(l.amount for l in lines))
        drawn = sum(trace.y[position] for trace in figure.data if trace.y[0] is not None)
        assert drawn == pytest.approx(sum(l.amount for l in lines))
    hatched = [t for t in figure.data if t.marker.pattern.shape == "/"]
    assert hatched
    texts = [a.text for a in figure.layout.annotations]
    point = levels["central"].breakpoint_share
    if result is RESULT:
        assert point is not None and f"Breakpoint: MREL covers the need up to {100 * point:.1f}%" in texts
        # Columns are evenly spaced; the line stands between two of them, on none, with
        # the levels the MREL covers on its left.
        columns = list(figure.layout.xaxis.tickvals)
        assert columns == [0, 1, 2]
        line = next(shape.x0 for shape in figure.layout.shapes if shape.line.dash == "dash")
        width = figure.data[0].width
        assert all(abs(line - column) > width / 2 for column in columns)
        shares = [summary.writedown_share for summary in levels.values()]
        assert [share <= point for share in shares] == [column < line for column in columns]
    else:
        assert not any("Breakpoint" in text for text in texts)


def test_decision_map_leaves_use_the_stage_scale():
    figure = FIGURES["build_decision_map"]
    tree = _tree()
    for outcome in {leaf.outcome for leaf in tree.leaves}:
        trace = next(t for t in figure.data if t.name == OUTCOME_LABELS[outcome])
        assert trace.marker.color == theme.stage_color(outcome)
    # The leaves are labelled with their outcome: colour is not the only signal.
    texts = [a.text for a in figure.layout.annotations]
    assert any(text.startswith(OUTCOME_LABELS["bail_in"]) for text in texts)


# ------------------------------------------------- the pages


def test_every_page_draws_the_header_with_the_phases_as_stepper():
    assert [title for title, _ in ui.PHASES] == ["Design", "Prepare", "Deliver", "Close"]
    for title, page in (*ui.PHASES, ui.OTHER_PAGE):
        text = (APP / page).read_text()
        assert "ui.header(" in text, page
    assert "ui.header(None" in (APP / "views" / "player.py").read_text()
    assert ui.SIMULATION == "SIMULATION"
    assert 'position="hidden"' in (APP / "main.py").read_text()


@pytest.mark.parametrize("page", [page for _, page in (*ui.PHASES, ui.OTHER_PAGE)])
def test_every_page_runs_with_the_header(page):
    at = AppTest.from_file(APP_PATH, default_timeout=60)
    at.session_state["demo_on"] = False
    at.session_state["control_unlocked"] = True
    at.run()
    at.switch_page(page).run()
    assert not at.exception
    assert at.title


def test_funding_tab_has_the_chart_the_table_and_the_balance_sheet():
    at = AppTest.from_file(APP_PATH, default_timeout=60)
    at.session_state["demo_on"] = False
    at.session_state["view"] = demo.TAB_FUNDING
    at.run()
    assert not at.exception
    table = at.dataframe[0].value
    amounts = [c for c in table.columns if c not in ("Day", "Stage")]
    assert amounts and all(c.endswith(f"({UNIT})") for c in amounts)  # units in the headers
    assert any(s.label == "Day compared with the opening" for s in at.slider)
    assert [h.value for h in at.subheader][-1].startswith("Balance sheet")
