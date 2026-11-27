"""dev/SPEC_UI.md, UI-4: exported Word documents in the house style, with charts."""

import inspect
import io
import re
from pathlib import Path

import pytest
from docx import Document
from docx.oxml.ns import qn

import app.theme as theme
from engine import docfigures, export, style
from engine.config import load_config
from engine.decisions import choices_of
from engine.live_run import scripted_showcase
from engine.report import live_report
from engine.schema import PLAYERS
from engine.script import build_pack

ROOT = Path(__file__).resolve().parent.parent
CONFIG = load_config()
SCRIPT, DECISIONS = CONFIG.script, CONFIG.decisions
SHOWCASE = SCRIPT.showcase
BANK, SCENARIO = CONFIG.banks[SHOWCASE.bank], CONFIG.scenarios[SHOWCASE.scenario]
PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(scope="module")
def pack():
    paths = DECISIONS.paths
    return build_pack(BANK, CONFIG.model, SCENARIO, DECISIONS, SCRIPT, [
        (paths[SHOWCASE.compare_path].name, choices_of(paths[SHOWCASE.compare_path])),
        ("Path selected on the Design page", choices_of(paths[SHOWCASE.path])),
    ])


@pytest.fixture(scope="module")
def files(pack):
    return export.pack_files(pack, SCRIPT, DECISIONS)


@pytest.fixture(scope="module")
def report():
    state, context = scripted_showcase(CONFIG)
    return live_report(state, context)


def images(content: bytes) -> list[bytes]:
    document = Document(io.BytesIO(content))
    return [part.blob for part in document.part.related_parts.values()
            if part.content_type.startswith("image/")]


def fills(document) -> list[tuple[str, str]]:
    """(cell text, fill colour) of every shaded table cell."""
    out = []
    for table in document.tables[1:]:  # after the cover's summary block
        for row in table.rows:
            for cell in row.cells:
                shading = cell._tc.find(f".//{qn('w:shd')}")
                if shading is not None:
                    out.append((cell.text, "#" + shading.get(qn("w:fill"))))
    return out


# ------------------------------------------------- shared tokens


def test_tokens_live_in_one_shared_file_outside_the_app():
    """Colours and typefaces are in engine/style.py only: the app and the documents written
    from the command line read the same ones."""
    assert theme.palette() is style.palette() and theme.PALETTES is style.PALETTES
    source = inspect.getsource(style)
    assert "import plotly" not in source and "import streamlit" not in source
    literal = re.compile(r"#[0-9a-fA-F]{6}\b")
    for path in [*(ROOT / "engine").glob("*.py"), ROOT / "app" / "theme.py"]:
        if path.name != "style.py":
            assert not literal.search(path.read_text()), path.name
    assert not any("app" == line.split()[1].split(".")[0]
                   for path in (ROOT / "engine").glob("*.py")
                   for line in path.read_text().splitlines()
                   if line.startswith(("from ", "import ")))
    assert (style.DOC_FONT_TEXT, style.DOC_FONT_FIGURES) == ("Calibri", "Consolas")
    assert "matplotlib" in (ROOT / "requirements.txt").read_text()


# ------------------------------------------------- the look of every document


def test_every_document_has_the_cover_the_band_and_the_house_typefaces(files, report):
    pal = style.palette()
    documents = {name: content for name, content in files.items() if name.endswith(".docx")}
    documents["closing_report.docx"] = export.closing_report_docx(report, SCRIPT)
    assert len(documents) >= 12
    for name, content in documents.items():
        document = Document(io.BytesIO(content))
        first = [p.text for p in document.paragraphs[:3]]
        assert first[0] == export.TOOL_NAME.upper(), name
        assert first[2] == export.SIMULATION_NOTICE, name
        # The cover ends with a page break before the first content.
        cover = []
        for paragraph in document.paragraphs:
            cover.append(paragraph.text)
            if 'w:type="page"' in paragraph._p.xml:
                break
        assert any(SCRIPT.disclaimer.strip() in text for text in cover) and len(cover) < 12, name
        assert document.styles["Normal"].font.name == style.DOC_FONT_TEXT, name
        assert document.styles["Heading 1"].font.color.rgb == export._rgb(pal.ink), name
        for section in document.sections:
            for part in (section.header, section.footer):
                band = part.paragraphs[0]
                assert export.SIMULATION_NOTICE in band.text, name
                assert band._p.find(f".//{qn('w:shd')}").get(qn("w:fill")) == pal.ink.lstrip("#"), name
            assert "PAGE" in section.footer.paragraphs[0]._p.xml, name


def test_tables_have_a_dark_header_and_figures_in_the_figures_typeface(files):
    pal = style.palette()
    document = Document(io.BytesIO(files["control_team/control_team_pack.docx"]))
    for table in document.tables[1:]:
        for cell in table.rows[0].cells:
            assert cell._tc.find(f".//{qn('w:shd')}").get(qn("w:fill")) == pal.ink.lstrip("#")
        assert table._tbl.tblPr.find(qn("w:tblBorders")) is not None
    timeline = next(t for t in document.tables if t.rows[0].cells[1].text == "Stage")
    figure_cell = timeline.rows[1].cells[3]
    assert figure_cell.paragraphs[0].runs[0].font.name == style.DOC_FONT_FIGURES
    assert figure_cell.paragraphs[0].alignment is not None


def test_stage_cells_take_the_stage_colour_and_keep_the_stage_name(files):
    pal = style.palette()
    document = Document(io.BytesIO(files["control_team/control_team_pack.docx"]))
    shaded = [(text, color) for text, color in fills(document) if color != pal.ink.upper()]
    assert shaded
    for text, color in shaded:
        stage = export.stage_of_cell(text)
        assert stage is not None and color == pal.stages[stage].upper(), text
    seen = {export.stage_of_cell(text) for text, _ in shaded}
    assert {"recovery", "likely_to_fail"} <= seen
    assert export.stage_of_cell("payments default") == "default"
    assert export.stage_of_cell("Friday") is None


# ------------------------------------------------- charts and information sets


@pytest.mark.parametrize("player", PLAYERS)
def test_summary_chart_is_drawn_from_that_players_own_injects_only(pack, player):
    """What the chart of a player shows is a figure that player received, at the day it
    refers to; nothing else can reach the image, which is drawn from this series."""
    for branch in pack.branches:
        series = docfigures.player_series(branch, player, pack.unit)
        own = {(f.name, f.as_of_day, round(docfigures._shown(f.name, f.value), 9))
               for inject in branch.injects[player] for f in inject.figures}
        drawn = {(name, day, round(value, 9)) for name, _, points in series.figures
                 for day, value in points}
        assert drawn and drawn <= own
        scope = set(DECISIONS.information[player].figures)
        assert {name for name, _, _ in series.figures} <= scope
        assert {day for day, _, _ in series.stages} <= {i.day for i in branch.injects[player]}
        mine = {i.decision_due.name for i in branch.injects[player] if i.decision_due}
        assert {name for name, _, _ in series.windows} == mine
        # The other players' figures are not in it.
        others = {(f.name, f.as_of_day, round(docfigures._shown(f.name, f.value), 9))
                  for other in PLAYERS if other != player
                  for inject in branch.injects[other] for f in inject.figures}
        assert not (drawn & (others - own))


def test_player_files_hold_no_image_and_the_control_pack_one_page_per_player(pack, files):
    for name, content in files.items():
        if name.endswith(".docx") and not name.endswith("control_team_pack.docx"):
            assert not images(content), name
    content = files["control_team/control_team_pack.docx"]
    found = images(content)
    assert len(found) == len(PLAYERS) and all(blob.startswith(PNG) for blob in found)
    expected = [
        docfigures.player_summary_png(
            [docfigures.player_series(branch, player, pack.unit) for branch in pack.branches],
            SCRIPT.actors[player])
        for player in PLAYERS
    ]
    assert sorted(found) == sorted(expected)  # each image is the one drawn from its series
    document = Document(io.BytesIO(content))
    headings = [p.text for p in document.paragraphs if p.text.startswith("What the players see: ")]
    assert headings == [f"What the players see: {SCRIPT.actors[p]}" for p in PLAYERS]
    described = [shape._inline.docPr.get("descr") for shape in document.inline_shapes]
    for player, text in zip(PLAYERS, described):
        series = docfigures.player_series(pack.branches[0], player, pack.unit)
        assert docfigures.series_text(series) in text


def test_charts_are_the_same_bytes_every_time(pack, report):
    panels = [docfigures.player_series(b, "central_bank", pack.unit) for b in pack.branches]
    assert docfigures.player_summary_png(panels, "Central bank") == docfigures.player_summary_png(
        panels, "Central bank")
    assert docfigures.decision_timeline_png(report) == docfigures.decision_timeline_png(report)


def test_closing_report_has_the_decision_timeline_and_the_counterfactual_bars(report):
    content = export.closing_report_docx(report, SCRIPT)
    found = images(content)
    assert sorted(found) == sorted([docfigures.decision_timeline_png(report),
                                    docfigures.counterfactual_png(report)])
    rows = docfigures.decision_rows(report)
    assert [(r["day"], r["as_planned"], r["applied"]) for r in rows] == [
        (line.day, line.as_planned, line.applied) for line in report.decisions]
    bars = docfigures.counterfactual_rows(report)
    assert bars[0]["actual"] and bars[0]["deposits_lost_pct"] == report.actual.deposits_lost_pct
    possible = [case for case in report.counterfactuals if case.possible]
    assert len(bars) == 1 + len(possible)
    for bar, case in zip(bars[1:], possible):
        assert bar["losses_booked"] == case.scorecard.losses_booked and case.decision_name in bar["label"]
    document = Document(io.BytesIO(content))
    described = [shape._inline.docPr.get("descr") for shape in document.inline_shapes]
    assert described[0].startswith("Decision timeline:") and described[1].startswith("Counterfactuals:")


def test_command_line_export_looks_the_same_as_the_apps(tmp_path, capsys, files):
    """The CLI reads the same tokens: its control team pack carries the palette's colours."""
    from engine.run import main
    assert main(["--showcase", "--export-dir", str(tmp_path)]) == 0
    capsys.readouterr()
    written = next(tmp_path.rglob("control_team_pack.docx"))
    document = Document(str(written))
    pal = style.palette()
    colours = {color for _, color in fills(document)}
    assert pal.ink.upper() in colours and pal.stages["recovery"].upper() in colours
    assert len(images(written.read_bytes())) == len(PLAYERS)
