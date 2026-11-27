"""Word documents as a reader opens them: the page number field, the cover's summary block,
column widths that keep every word whole, and landscape pages for wide tables."""

import io
import re

import pytest
from docx import Document
from docx.oxml.ns import qn
from docx.shared import Emu

from engine import export
from engine.config import load_config
from engine.decisions import choices_of
from engine.group import GroupChoices
from engine.group_export import group_files
from engine.group_script import build_group_pack
from engine.script import build_pack
from engine import system

CONFIG = load_config()
SCRIPT, SHOW = CONFIG.script, CONFIG.script.showcase
PATHS = CONFIG.decisions.paths


@pytest.fixture(scope="module")
def pack():
    return build_pack(CONFIG.banks[SHOW.bank], CONFIG.model, CONFIG.scenarios[SHOW.scenario],
                      CONFIG.decisions, SCRIPT,
                      [(PATHS[key].name, choices_of(PATHS[key])) for key in (SHOW.compare_path, SHOW.path)])


@pytest.fixture(scope="module")
def documents(pack):
    """Every Word document of the three kinds of pack: name -> Document."""
    out = {f"single/{name}": content for name, content in export.pack_files(pack, SCRIPT, CONFIG.decisions).items()}
    show = CONFIG.college.showcase
    from engine.group import at_severity
    from engine.decisions import Choice
    group_pack = build_group_pack(
        CONFIG.groups[show.group], CONFIG.banks, CONFIG.model,
        at_severity(CONFIG.scenarios, show.scenario, show.severity), show.scenario, CONFIG.decisions,
        SCRIPT, CONFIG.college,
        [("No decision of the group", GroupChoices()),
         ("As decided", GroupChoices(group=(Choice(show.decision, show.option, show.day),)))])
    out |= {f"group/{name}": content for name, content in group_files(group_pack, CONFIG.decisions, CONFIG.college).items()}
    pair, forum = system.pair(CONFIG, CONFIG.systems.showcase.pair)
    system_pack = build_group_pack(
        pair, CONFIG.banks, CONFIG.model, CONFIG.scenarios, CONFIG.systems.showcase.scenario,
        CONFIG.decisions, SCRIPT, forum,
        [("Authorities act late", GroupChoices()),
         ("As decided", GroupChoices(parent=choices_of(PATHS["early_foltf"])))])
    out |= {f"system/{name}": content for name, content in group_files(system_pack, CONFIG.decisions, forum).items()}
    return {name: Document(io.BytesIO(content)) for name, content in out.items() if name.endswith(".docx")}


# ------------------------------------------------- 1. page number


def test_footer_says_page_x_of_y_with_fields_that_hold_a_result(documents):
    assert len(documents) > 100
    for name, document in documents.items():
        for section in document.sections:
            footer = section.footer.paragraphs[0]
            fields = footer._p.findall(qn("w:fldSimple"))
            assert [f.get(qn("w:instr")).split()[0] for f in fields] == ["PAGE", "NUMPAGES"], name
            for field in fields:  # a result inside the field: shown even where nothing computes it
                assert field.find(f".//{qn('w:t')}").text == "1", name
            # No field left in the old form, which Word and LibreOffice showed empty.
            assert "w:fldChar" not in footer._p.xml and "w:instrText" not in footer._p.xml, name
            shown = "".join(node.text or "" for node in footer._p.iter(qn("w:t")))
            assert shown.endswith("Page 1 of 1") and export.SIMULATION_NOTICE in shown, name


# ------------------------------------------------- 2. cover


def cover_of(document) -> tuple[dict, list[str]]:
    """(the summary block as a dict, the cover's paragraphs)."""
    table = document.tables[0]
    block = {row.cells[0].text: row.cells[1].text for row in table.rows}
    paragraphs = []
    for paragraph in document.paragraphs:
        paragraphs.append(paragraph)
        if 'w:type="page"' in paragraph._p.xml:
            break
    return block, paragraphs


def test_every_cover_has_the_summary_block(documents):
    for name, document in documents.items():
        block, _ = cover_of(document)
        for label in ("Kind of exercise", "Scenario", "Simulated days", "This document is for", "Contents"):
            assert block.get(label, "").strip(), (name, label)
        assert ("Bank" in block) or ("Group" in block) or ("System" in block), name
        assert "Type of exercise" in block and block["Type of exercise"].startswith("This exercise is"), name
        kind = name.split("/")[0]
        assert block["Kind of exercise"].startswith(
            {"single": "Single bank", "group": "Cross-border group", "system": "System-wide"}[kind]), name


def test_contents_on_the_cover_are_the_documents_main_headings(documents):
    for name, document in documents.items():
        block, _ = cover_of(document)
        headings = [p.text for p in document.paragraphs if p.style.name == "Heading 1" and p.text.strip()]
        listed = block["Contents"].split("\n")
        if not headings:
            assert listed == ["One section, on the next page."], name
        else:
            assert listed[: len(headings)][: export.COVER_CONTENTS_MAX] == headings[: export.COVER_CONTENTS_MAX], name


def test_the_caveats_are_once_on_the_cover_below_the_block_and_smaller(documents):
    notice = SCRIPT.draft_notice.strip()
    for name, document in documents.items():
        _, cover = cover_of(document)
        texts = [p.text for p in document.paragraphs]
        assert sum(notice in text for text in texts) == 1, name
        caveat = next(p for p in cover if notice in p.text)
        assert SCRIPT.disclaimer.strip() in caveat.text, name
        # Below the summary block, in small type.
        body = document.element.body
        assert list(body).index(caveat._p) > list(body).index(document.tables[0]._tbl), name
        assert all(run.font.size.pt < 9 and run.italic for run in caveat.runs), name
        # The note of a pre-filled draft is in the block, not a second bold paragraph.
        assert export.DRAFT_NOTE not in texts, name


def test_players_covers_name_neither_the_scenario_nor_the_branches(documents, pack):
    for name, document in documents.items():
        if "/players/" not in name and "/inject_sheets/" not in name:
            continue
        block, _ = cover_of(document)
        # The inject sheets are handed out one by one by the control team; their cover, with
        # the list of sheets, is not. Its summary rows still give nothing away.
        text = " ".join(value for label, value in block.items()
                        if label != "Contents" or "/players/" in name)
        assert pack.scenario_name not in text and "Branch" not in text, name
        assert "up to 30 simulated days" in block["Simulated days"], name
    control = cover_of(documents["single/control_team/control_team_pack.docx"])[0]
    assert pack.scenario_name in control["Scenario"] and "Branch A" in control["Simulated days"]


# ------------------------------------------------- 3. tables


def words(text: str) -> list[str]:
    return text.split()


def test_column_widths_hold_the_longest_word_and_stay_within_the_page():
    cells = [["Day", "Decision", "Counterfactual", "Notes"],
             ["12", "Emergency liquidity assistance", "Unencumbered", "x " * 60]]
    widths, size = export.column_widths(cells, 6.0)
    assert size == 11 and sum(widths) <= 6.0 + 1e-9
    per = export._CHAR_WIDTH_PER_POINT * size
    assert widths[2] >= len("Counterfactual") * per + export._CELL_PADDING - 1e-9
    assert widths[3] == max(widths)  # the room left goes to the long text
    # Too many long words for the page at 11 points: the type shrinks before any word breaks.
    wide = [[f"Characteristic{i}" for i in range(9)]]
    widths, size = export.column_widths(wide, 9.0)
    assert size < 11 and sum(widths) <= 9.0 + 1e-9
    assert all(w >= len("Characteristic0") * export._CHAR_WIDTH_PER_POINT * size for w in widths)


def test_every_table_has_fixed_widths_within_the_page_and_no_word_wider_than_its_column(documents):
    checked = 0
    for name, document in documents.items():
        body = list(document.element.body)
        sections = document.sections
        for table in document.tables[1:]:
            # The section the table is in: the first one whose end comes after the table.
            position = body.index(table._tbl)
            ends = [i for i, node in enumerate(body) if node.find(f".//{qn('w:sectPr')}") is not None
                    and node.tag != qn("w:tbl")]
            section = sections[sum(1 for end in ends if end < position)] if ends else sections[-1]
            available = export.text_width(section)
            widths = [Emu(cell.width).inches for cell in table.rows[0].cells]
            assert sum(widths) <= available + 0.02, (name, sum(widths), available)
            assert table._tbl.tblPr.find(qn("w:tblLayout")).get(qn("w:type")) == "fixed", name
            sizes = {run.font.size.pt for row in table.rows for cell in row.cells
                     for p in cell.paragraphs for run in p.runs if run.font.size is not None}
            size = min(sizes) if sizes else 11
            for row in table.rows:
                for cell, width in zip(row.cells, widths):
                    longest = max((len(word) for word in words(cell.text)), default=0)
                    assert longest * export._CHAR_WIDTH_PER_POINT * size <= width + 1e-6, (name, cell.text[:40])
            checked += 1
    assert checked > 300


def test_tables_with_more_than_six_columns_are_on_landscape_pages(documents):
    wide = 0
    for name, document in documents.items():
        body = list(document.element.body)
        ends = [i for i, node in enumerate(body) if node.tag == qn("w:p") and node.find(f".//{qn('w:sectPr')}") is not None]
        for table in document.tables[1:]:
            if len(table.columns) <= export.WIDE_TABLE_COLUMNS:
                continue
            section = document.sections[sum(1 for end in ends if end < body.index(table._tbl))]
            assert section.page_width > section.page_height, name
            wide += 1
    assert wide >= 10


def test_a_wide_table_in_a_portrait_document_gets_landscape_pages_of_its_own():
    document = export._start("Test", "Bank", SCRIPT)
    document.add_paragraph("Before")
    export._table(document, [f"Column {i}" for i in range(8)], [[str(i) for i in range(8)]])
    document.add_paragraph("After")
    again = Document(io.BytesIO(export._save(document)))
    shapes = [s.page_width > s.page_height for s in again.sections]
    assert shapes == [False, True, False]
    for section in again.sections:  # the band carries over to the turned pages
        assert export.SIMULATION_NOTICE in "".join(t.text or "" for t in section.footer._element.iter(qn("w:t")))
