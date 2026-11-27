"""Export of the exercise pack (SPEC_V2 phase D, SPEC_V3 phase G): the inject sheets of each
player, the main events list, the role cards, the control team pack and the data files, as
bytes and as one zip.

Nothing here reads or writes files; the caller (CLI or app) does.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Optional, Sequence

import pandas as pd
from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Inches, Pt, RGBColor

from engine import docfigures, style
from engine.decisions import OUTCOME_LABELS, actor_label, decision_log_rows, scorecard_rows
from engine.resolution import breakpoint_text, summary_rows
from engine.schema import PLAYERS, ROLES, WEEKDAYS, DecisionsConfig, ScriptConfig
from engine import legal as legal_refs
from engine.script import MODE_TEXT, ExercisePack, Inject, MainEvent, sheet_id
from engine.setup import (
    DRAFT_MARK,
    charter_rows,
    observer_sheet,
    resolve_sessions,
    rules_of_play,
    session_plans,
)

KNOWN_LIMITS = (
    "Legal loss-absorption thresholds are not modelled: the model does not test any minimum "
    "amount of losses that shareholders and creditors must bear before other funds are used.",
    "The resolution figures rest on an assumed write-down of the loan book (low, central or "
    "high) and on the order of loss absorption used in this model, which is not a statement "
    "of law. Sale of business is computed like bail-in. The price in the \"interested "
    "buyer\" inject is an assumption and changes no other figure.",
    "Challenge injects change no figure of the run, except the rating downgrade, which adds "
    "a news shock. Hostile assumptions raise the liquidity need at reopening by an assumed "
    "share; they do not change the run either.",
    "The model stops when the tool is applied: there is no run after the bank reopens.",
    "Days are whole days. Times of day are labels, and the outflow by time block is an "
    "allocation of the daily figure, not a simulation of the day.",
    "On weekends there are no market sales and no central bank drawing; withdrawals are paid "
    "from cash only and the rest waits for Monday. Nobody defaults on a weekend.",
    "All parameters behind the figures are assumptions unless the Assumptions page says "
    "otherwise. Ambient injects change no figure.",
    "Role cards and expected responses describe a plausible behaviour for the exercise, not "
    "what any real institution would do.",
    "The wording, coordination steps and deadlines are a draft without legal references.",
)

TIMELINE_COLUMNS = {
    "day": "Day",
    "status": "Stage",
    "cum_deposit_loss_pct": "Deposits lost (%)",
    "outflow_total": "Outflow",
    "queued_withdrawals": "Requested, not settled",
    "liquidity_buffer": "Liquidity buffer",
    "survival_days": "Survival (days)",
    "cet1_ratio": "CET1 ratio (%)",
    "ela_funding": "ELA outstanding",
}


def weekday_of(pack: ExercisePack, day: int) -> str:
    if day < 1:
        return ""
    return WEEKDAYS[(WEEKDAYS.index(pack.start_weekday) + day - 1) % 7]


def day_label(pack: ExercisePack, day: int) -> str:
    weekday = weekday_of(pack, day)
    return f"Day {day}, {weekday}" if weekday else f"Day {day} (before the start)"


# ------------------------------------------------- the look of every Word document
# Colours and typefaces come from engine/style.py (dev/SPEC_UI.md, UI-4).

_NUMBER = re.compile(r"^[-+]?\d[\d ,]*(\.\d+)?%?$")
# What a stage cell may say, in the documents' own words -> the engine's word.
_STAGE_WORDS = {"payments default": "failed"}


# Word reads the properties of a paragraph, a cell or a table in a fixed order: a new one is
# put before the elements that must follow it.
_AFTER_PARAGRAPH_SHADING = (
    "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap", "w:overflowPunct",
    "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN", "w:bidi", "w:adjustRightInd",
    "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing", "w:mirrorIndents",
    "w:suppressOverlap", "w:jc", "w:textDirection", "w:textAlignment", "w:textboxTightWrap",
    "w:outlineLvl", "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange",
)
_AFTER_CELL_SHADING = ("w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign",
                       "w:hideMark", "w:headers", "w:cellIns", "w:cellDel", "w:cellMerge",
                       "w:tcPrChange")
_AFTER_TABLE_BORDERS = ("w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook", "w:tblCaption",
                        "w:tblDescription", "w:tblPrChange")


def _rgb(color: str) -> RGBColor:
    return RGBColor.from_string(color.lstrip("#").upper())


def _shade(element, color: str) -> None:
    """Fill a table cell or a paragraph with a colour."""
    cell = hasattr(element, "get_or_add_tcPr")
    properties = element.get_or_add_tcPr() if cell else element.get_or_add_pPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), color.lstrip("#").upper())
    properties.insert_element_before(
        shading, *(_AFTER_CELL_SHADING if cell else _AFTER_PARAGRAPH_SHADING))


def _borders(table, color: str) -> None:
    """Thin rules in the line colour around every cell."""
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        rule = OxmlElement(f"w:{edge}")
        rule.set(qn("w:val"), "single")
        rule.set(qn("w:sz"), "4")
        rule.set(qn("w:space"), "0")
        rule.set(qn("w:color"), color.lstrip("#").upper())
        borders.append(rule)
    table._tbl.tblPr.insert_element_before(borders, *_AFTER_TABLE_BORDERS)


def stage_of_cell(text: str) -> Optional[str]:
    """The stage of the scale a table cell names, or None."""
    word = _STAGE_WORDS.get(text.strip().lower(), text.strip().lower().replace(" ", "_"))
    try:
        return style.stage_of(word)
    except ValueError:
        return None


def _table(document, header: list[str], rows: list[list[str]]) -> None:
    """A table in the house style: header row in the ink colour, thin rules, figures
    right-aligned in the figures typeface. A column headed "Stage" takes the colour of the
    stage each cell names, and keeps the stage's name as its text."""
    pal = style.palette()
    # A table with more than six columns goes on landscape pages.
    turned = len(header) > WIDE_TABLE_COLUMNS and not _is_landscape(document.sections[-1])
    if turned:
        _turn(document, landscape=True)
    table = document.add_table(rows=1, cols=len(header))
    table.style = "Table Grid"
    _borders(table, pal.line)
    stage_columns = {index for index, text in enumerate(header) if text == "Stage"}
    for cell, text in zip(table.rows[0].cells, header):
        cell.text = text
        _shade(cell._tc, pal.ink)
        for run in cell.paragraphs[0].runs:
            run.bold = True
            run.font.color.rgb = _rgb(pal.surface)
    for row in rows:
        for index, (cell, text) in enumerate(zip(table.add_row().cells, row)):
            cell.text = str(text)
            paragraph = cell.paragraphs[0]
            stage = stage_of_cell(str(text)) if index in stage_columns else None
            if stage is not None:
                color = pal.stages[stage]
                _shade(cell._tc, color)
                for run in paragraph.runs:
                    run.bold = True
                    run.font.color.rgb = _rgb(style.text_on(color, pal))
            elif _NUMBER.match(str(text).strip()):
                paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                for run in paragraph.runs:
                    run.font.name = style.DOC_FONT_FIGURES
    _fit_columns(document, table, [header, *[[str(text) for text in row] for row in rows]])
    if turned:
        _turn(document, landscape=False)


WIDE_TABLE_COLUMNS = 6  # more columns than this: landscape
# Width of an average character and of a cell's side padding, in inches at 1 pt of type
# size. Rough on purpose, and on the generous side: a column must hold its longest word.
_CHAR_WIDTH_PER_POINT = 0.0082
_FIGURE_WIDTH_PER_POINT = 0.0085  # the figures typeface is monospaced and wider
_CELL_PADDING = 0.19
TABLE_FONT_SIZES = (11, 10, 9, 8, 7)  # tried in turn until every longest word fits


def _is_landscape(section) -> bool:
    return section.page_width > section.page_height


def _turn(document, landscape: bool) -> None:
    """Start a new section on a new page, turned to landscape or back to portrait. The
    header and footer carry over."""
    section = document.add_section(WD_SECTION.NEW_PAGE)
    if _is_landscape(section) != landscape:
        section.orientation = WD_ORIENT.LANDSCAPE if landscape else WD_ORIENT.PORTRAIT
        section.page_width, section.page_height = section.page_height, section.page_width


def text_width(section) -> float:
    """Width of the text area of a section, in inches."""
    return Emu(section.page_width - section.left_margin - section.right_margin).inches


def column_widths(cells: list[list[str]], available: float) -> tuple[list[float], int]:
    """(width of each column in inches, type size in points) for a table: every column is
    wide enough for its longest word, at the largest type size at which the table fits the
    page; the room left goes to the columns with the longest texts. If the longest words do
    not fit even at the smallest size, the widths are scaled down to the page."""
    columns = list(zip(*cells))
    for size in TABLE_FONT_SIZES:
        def needed(text: str, chars: int) -> float:
            per = _FIGURE_WIDTH_PER_POINT if _NUMBER.match(text.strip()) else _CHAR_WIDTH_PER_POINT
            return chars * per * size + _CELL_PADDING
        least = [max(needed(text, max((len(word) for word in text.split()), default=0))
                     for text in column) for column in columns]
        if sum(least) <= available:
            break
    if sum(least) > available:
        return [width * available / sum(least) for width in least], size
    # What each column would like: its longest cell on one line, up to a third of the page.
    wish = [min(max(needed(text, len(text)) for text in column), available / 3) for column in columns]
    extra = [max(want - low, 0.0) for want, low in zip(wish, least)]
    room = available - sum(least)
    share = room / sum(extra) if sum(extra) > room else 1.0
    widths = [low + more * share for low, more in zip(least, extra)]
    return widths, size


def _set_table_width(table, inches: float) -> None:
    """The table's own width, so that every reader lays it out from the same total."""
    properties = table._tbl.tblPr
    for old in properties.findall(qn("w:tblW")):
        properties.remove(old)
    width = OxmlElement("w:tblW")
    width.set(qn("w:w"), str(round(inches * 1440)))
    width.set(qn("w:type"), "dxa")
    properties.insert_element_before(
        width, "w:jc", "w:tblCellSpacing", "w:tblInd", "w:tblBorders", *_AFTER_TABLE_BORDERS)


def _fit_columns(document, table, cells: list[list[str]]) -> None:
    """Fixed column widths, so that Word and LibreOffice lay the table out the same way and
    no word is broken inside itself."""
    widths, size = column_widths(cells, text_width(document.sections[-1]))
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    _set_table_width(table, sum(widths))
    for column, width in zip(table.columns, widths):
        column.width = Inches(width)
    for row in table.rows:
        for cell, width in zip(row.cells, widths):
            cell.width = Inches(width)
            if size != TABLE_FONT_SIZES[0]:
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        run.font.size = Pt(size)


def _picture(document, png: bytes, width: float, text: str) -> None:
    """A chart, with its content in words as the image's alternative text."""
    document.add_picture(io.BytesIO(png), width=Inches(width))
    document.inline_shapes[len(document.inline_shapes) - 1]._inline.docPr.set("descr", text)


def _reference(document, text: str) -> None:
    """A "Reference:" line (config/legal.yaml), small and grey; nothing if `text` is empty."""
    if not text:
        return
    run = document.add_paragraph().add_run(text)
    run.italic, run.font.size, run.font.color.rgb = True, Pt(8.5), _rgb(style.palette().muted)


def decision_references(document, legal, decisions_seen) -> None:
    """One "Reference:" line per decision of `decisions_seen` (name, key) that has a row."""
    done = set()
    for name, key in decisions_seen:
        text = legal_refs.line(legal, [f"decision:{key}"])
        if text and key not in done:
            done.add(key)
            _reference(document, f"{name}. {text}")


def legal_table_page(document, legal) -> None:
    """The whole table of config/legal.yaml, with its status, on a page of its own."""
    if legal_refs.active(legal) is None:
        return
    document.add_page_break()
    document.add_heading("Legal references", level=1)
    document.add_paragraph().add_run(legal.note).bold = True
    for act, text in legal.acts.items():
        _labelled(document, act, text)
    _table(document, ["Concept in the tool", "Reference", "Status"],
           [list(row) for row in legal_refs.table(legal)])
    for status, text in legal.statuses.items():
        _labelled(document, f"Status {status}", text)


def _labelled(document, label: str, text: str) -> None:
    paragraph = document.add_paragraph()
    paragraph.add_run(f"{label}: ").bold = True
    paragraph.add_run(text)


# Every exported document says it is a simulation (EBA Handbook, par. 174): Word files in
# the header and footer of every page and under the title, data files in their name.
SIMULATION_LABEL = "SIMULATION"
TOOL_NAME = "Crisis Scenario Engine"
SIMULATION_NOTICE = "SIMULATION - exercise material, not a real event"


def _field(paragraph, code: str, shown: str = "1") -> None:
    """A field (the page number, the number of pages) at the end of a paragraph, as a simple
    field holding a result: Word and LibreOffice both compute it, and a reader that computes
    nothing still shows `shown` instead of an empty place."""
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), f" {code} \\* MERGEFORMAT ")
    run = OxmlElement("w:r")
    properties = OxmlElement("w:rPr")
    for name in ("w:b", "w:noProof"):
        properties.append(OxmlElement(name))
    color = OxmlElement("w:color")
    color.set(qn("w:val"), style.palette().surface.lstrip("#").upper())
    properties.append(color)
    text = OxmlElement("w:t")
    text.text = shown
    run.append(properties)
    run.append(text)
    field.append(run)
    paragraph._p.append(field)


def _band(paragraph, pal) -> None:
    """A paragraph as a filled band: light bold text on the ink colour."""
    _shade(paragraph._p, pal.ink)
    for run in paragraph.runs:
        run.bold = True
        run.font.color.rgb = _rgb(pal.surface)


def _house_style(document, pal) -> None:
    """Typefaces and heading colours of the document."""
    styles = document.styles
    styles["Normal"].font.name = style.DOC_FONT_TEXT
    styles["Normal"].font.size = Pt(11)
    styles["Normal"].font.color.rgb = _rgb(pal.ink)
    for name, size in (("Title", 28), ("Heading 1", 16), ("Heading 2", 13), ("Heading 3", 12)):
        font = styles[name].font
        font.name, font.size, font.bold = style.DOC_FONT_TEXT, Pt(size), True
        font.color.rgb = _rgb(pal.ink)
        # Word takes the typeface of headings from the theme unless told otherwise.
        fonts = styles[name].element.get_or_add_rPr().get_or_add_rFonts()
        for attribute in ("w:ascii", "w:hAnsi", "w:cs"):
            fonts.set(qn(attribute), style.DOC_FONT_TEXT)
        for theme_attribute in ("w:asciiTheme", "w:hAnsiTheme", "w:cstheme"):
            fonts.attrib.pop(qn(theme_attribute), None)
    for name in ("Title", "Heading 1"):  # a rule in the accent colour under the heading
        properties = styles[name].element.get_or_add_pPr()
        for old in properties.findall(qn("w:pBdr")):
            properties.remove(old)
        border = OxmlElement("w:pBdr")
        rule = OxmlElement("w:bottom")
        rule.set(qn("w:val"), "single")
        rule.set(qn("w:sz"), "12")
        rule.set(qn("w:space"), "4")
        rule.set(qn("w:color"), pal.accent.lstrip("#").upper())
        border.append(rule)
        properties.insert_element_before(border, "w:shd", *_AFTER_PARAGRAPH_SHADING)


def pack_facts(pack: ExercisePack, full: bool = False) -> list[tuple[str, str]]:
    """The summary of an exercise for the cover of a document of its pack. `full`: for the
    control team, with the branches and the day each ends; players and observers are told
    only how far the exercise can run."""
    days = "; ".join(
        (f"Branch {branch.label} ({branch.name}): " if len(pack.branches) > 1 else "")
        + f"day 0 to day {branch.result.end_day}" for branch in pack.branches
    ) if full else f"From day 0, for up to {pack.horizon} simulated days"
    return [
        ("Kind of exercise", pack.exercise),
        ("Bank", pack.bank_name),
        # The scenario's name gives the story away: it is on the control team's documents only.
        ("Scenario", pack.scenario_name + (f" ({pack.taxonomy})" if pack.taxonomy else "") if full
         else "Not named here: the players learn what happens as the exercise unfolds."),
        ("Type of exercise", MODE_TEXT[pack.mode]),
        ("Simulated days", f"{days}. Day 1 is a {pack.start_weekday}."
                           + (f" The model looks {pack.horizon} days ahead." if full else "")),
    ]


COVER_CONTENTS_MAX = 14  # sections named on a cover; more are counted


def _start(title: str, subtitle: str, script: ScriptConfig, mode: str = "",
           landscape: bool = False, audience: str = "", pack: Optional[ExercisePack] = None,
           facts: Sequence[tuple[str, str]] = (), draft: bool = False, full: bool = False):
    """A new document: house style, the SIMULATION label in the header and footer of every
    page, and a cover page with a summary block: what the exercise is (`pack`, or `facts`),
    who the document is for (`audience`) and its contents, filled in when it is saved.
    `draft`: a document pre-filled for the design team to edit. `full`: a document of the
    control team, whose cover may name the branches and the day each ends."""
    pal = style.palette()
    document = Document()
    _house_style(document, pal)
    if landscape:
        for section in document.sections:
            section.orientation = WD_ORIENT.LANDSCAPE
            section.page_width, section.page_height = section.page_height, section.page_width
    document.core_properties.title = f"{SIMULATION_LABEL} - {title}"
    document.core_properties.author = TOOL_NAME
    for section in document.sections:
        for part in (section.header, section.footer):
            paragraph = part.paragraphs[0]
            paragraph.text = SIMULATION_NOTICE
            if part is section.header:
                paragraph.add_run(f"   |   {title}")
            else:
                paragraph.add_run("   |   Page ")
            _band(paragraph, pal)
            if part is section.footer:  # after the band: the fields carry their own format
                _field(paragraph, "PAGE")
                of = paragraph.add_run(" of ")
                of.bold, of.font.color.rgb = True, _rgb(pal.surface)
                _field(paragraph, "NUMPAGES")
    # Cover page.
    tool = document.add_paragraph().add_run(TOOL_NAME.upper())
    tool.font.size, tool.font.color.rgb, tool.bold = Pt(10), _rgb(pal.muted), True
    document.add_heading(title, level=0)
    notice = document.add_paragraph()
    notice.add_run(SIMULATION_NOTICE)
    _band(notice, pal)
    lead = document.add_paragraph().add_run(subtitle)
    lead.font.size, lead.bold = Pt(16), True
    rows = [*(pack_facts(pack, full) if pack is not None else ()), *facts]
    if mode and pack is None and not any(label == "Type of exercise" for label, _ in rows):
        rows.append(("Type of exercise", MODE_TEXT[mode]))
    rows.append(("This document is for", audience or "the control team"))
    if draft:
        rows.append(("Status", DRAFT_NOTE))
    rows.append(("Contents", ""))
    table = document.add_table(rows=len(rows), cols=2)
    table.style = "Table Grid"
    _borders(table, pal.line)
    table.autofit = False
    available = text_width(document.sections[0])
    widths = (2.2, available - 2.2)
    _set_table_width(table, sum(widths))
    for column, width in zip(table.columns, widths):
        column.width = Inches(width)
    for row, (label, value) in zip(table.rows, rows):
        for cell, width in zip(row.cells, widths):
            cell.width = Inches(width)
        row.cells[0].text, row.cells[1].text = label, value
        _shade(row.cells[0]._tc, style.mix(pal.line, pal.surface, 0.35))
        for run in row.cells[0].paragraphs[0].runs:
            run.bold = True
    document._cover_contents = table.rows[-1].cells[1]
    # The caveats, once, lower on the cover and smaller.
    caveat = document.add_paragraph()
    caveat.paragraph_format.space_before = Pt(30)
    for text in (script.disclaimer, script.draft_notice):
        run = caveat.add_run(text.strip() + " ")
        run.italic, run.font.size, run.font.color.rgb = True, Pt(8.5), _rgb(pal.muted)
    document.add_page_break()
    return document


def _save(document) -> bytes:
    cell = getattr(document, "_cover_contents", None)
    if cell is not None:  # the contents on the cover: the document's main headings
        headings = [p.text for p in document.paragraphs if p.style.name == "Heading 1" and p.text.strip()]
        shown = headings[:COVER_CONTENTS_MAX]
        more = len(headings) - len(shown)
        cell.text = ("One section, on the next page." if not headings else
                     "\n".join(shown) + (f"\nand {more} more section{'' if more == 1 else 's'}" if more else ""))
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _glossary(document, script: ScriptConfig) -> None:
    document.add_page_break()
    document.add_heading("Glossary", level=1)
    for entry in script.glossary:
        _labelled(document, entry.term, entry.definition)


def _write_inject(document, pack: ExercisePack, inject: Inject, sheet: str = "") -> None:
    if sheet:
        document.add_paragraph().add_run(f"Sheet {sheet}").bold = True
    document.add_heading(f"{day_label(pack, inject.day)}, {inject.time} - {inject.title}", level=3)
    _labelled(document, "From", inject.sender)
    for line in inject.text.split("\n"):
        document.add_paragraph(line)
    _reference(document, legal_refs.inject_line(inject, pack.legal))
    value = lambda figure: figure.text[len(figure.label) + 1:]  # noqa: E731
    if inject.headline:
        as_of = inject.headline[0].as_of_day
        document.add_paragraph(
            f"Figures behind this notification, as of the close of day {as_of}, as passed on "
            f"by the sender:"
        )
        _table(
            document,
            ["Figure", "Value", "Threshold crossed"],
            [[figure.label, value(figure), figure.threshold_text] for figure in inject.headline],
        )
    if inject.notified:
        document.add_paragraph("What you have been told in notifications so far:")
        _table(
            document,
            ["Figure", "Value", "As of the close of"],
            [[figure.label, value(figure), f"day {figure.as_of_day}"] for figure in inject.notified],
        )
    if inject.resolution:
        document.add_paragraph(
            "Estimates. They rest on an assumed write-down of the loan book and may be revised:"
        )
        _table(
            document,
            ["Figure", "Estimate"],
            [[figure.label, value(figure)] for figure in inject.resolution],
        )
    if inject.figures:
        as_of = inject.figures[0].as_of_day
        document.add_paragraph(f"Your latest regular report, as of the close of day {as_of}:")
        _table(
            document,
            ["Figure", "Value"],
            [[figure.label, value(figure)] for figure in inject.figures],
        )
    due = inject.decision_due
    if due is not None:
        _labelled(document, "Question", due.question)
        if pack.mode == "linear":
            _table(document, ["Option"], [[option.name] for option in due.options])
        else:
            _table(
                document,
                ["Option", "Branch code"],
                [[option.name, option.branch_code] for option in due.options],
            )
        weekday = weekday_of(pack, due.deadline_day)
        _labelled(
            document, "Decide by",
            f"the close of day {due.deadline_day}" + (f" ({weekday})" if weekday else "")
            + (". The branches have different deadlines; this is the earlier one."
               if due.deadline_shared else ""),
        )
        _labelled(document, "Consult first", ", ".join(due.consult) or "nobody")
        _labelled(document, "Inform afterwards", ", ".join(due.inform) or "nobody")
        document.add_paragraph(
            "Tell the control team your choice. It is recorded, but the exercise is linear "
            "and continues on its fixed path."
            if pack.mode == "linear" else
            "Tell the control team the branch code of your choice. A decision is in effect "
            "the day after it is taken."
        )


PURPOSE_LABELS = {"advance": "Advance", "challenge": "Challenge", "control": "Control"}


def _section_label(pack: ExercisePack, section: str) -> str:
    if section:
        return f"Branch {section}"
    return "All branches" if len(pack.branches) > 1 else ""


def _recipients(script: ScriptConfig, event: MainEvent) -> str:
    return ", ".join(script.actors[actor] for actor in event.recipients)


def inject_sheets_docx(pack: ExercisePack, script: ScriptConfig, actor: str) -> bytes:
    """The inject sheets of one recipient: one inject per page, only what it knows by then.

    Kept by the control team, which hands each sheet out when its trigger is met. The first
    page is for the control team; nothing on the other pages goes beyond the recipient's own
    information.
    """
    document_data = next(d for d in pack.documents if d.actor == actor)
    document = _start(
        f"Inject sheets: {document_data.name}", pack.bank_name, script, pack.mode, pack=pack,
        audience=f"the control team, which hands each sheet to the {document_data.name.lower()} "
                 "at its time. Not to be given to the players as a whole",
    )
    document.add_heading("For the control team", level=1)
    document.add_paragraph(
        "Do not hand out this page. Each page after it is one inject for this recipient. Hand "
        "a sheet out only when its trigger on the main events list is met. A fallback sheet "
        "is handed out only if the player who should have passed the information on has not."
    )
    numbers = {
        sheet: event for event in pack.events for sheet in event.sheets
    }
    sections = [("", document_data.shared)] + [
        (title.split()[1], injects) for title, injects in document_data.branches
    ]
    index_rows = []
    for section, injects in sections:
        for position, inject in enumerate(injects):
            sheet = sheet_id(actor, section, position)
            event = numbers.get(sheet)
            index_rows.append([
                sheet,
                _section_label(pack, section),
                f"{day_label(pack, inject.day)}, {inject.time}",
                inject.title,
                "" if event is None else str(event.number),
                PURPOSE_LABELS[inject.purpose] + (" (fallback)" if inject.fallback else ""),
            ])
    _table(document, ["Sheet", "Branch", "When", "Inject", "No. on the main events list",
                      "Purpose"], index_rows)

    document.add_page_break()
    document.add_heading("Briefing sheet: what you see", level=1)
    for sentence in document_data.information:
        document.add_paragraph(sentence, style="List Bullet")
    document.add_paragraph(
        "Times of day are labels. The figures you are given are the only figures you have: "
        "other players may hold more recent ones."
    )
    for section, injects in sections:
        if section and not injects:
            document.add_page_break()
            document.add_heading(f"Branch {section}", level=1)
            document.add_paragraph("You receive nothing further on this branch.")
        for position, inject in enumerate(injects):
            document.add_page_break()
            if section:
                document.add_paragraph(f"Branch {section}")
            _write_inject(document, pack, inject, sheet_id(actor, section, position))
    _glossary(document, script)
    return _save(document)


def main_events_rows(pack: ExercisePack, script: ScriptConfig) -> list[dict[str, str]]:
    """The main events list as rows: one inject per line, in order."""
    return [
        {
            "No.": str(event.number),
            "Branch": _section_label(pack, event.section),
            "Day": day_label(pack, event.day),
            "Time": event.time,
            "Trigger": event.trigger,
            "Inject": event.title,
            "To": _recipients(script, event),
            "Delivered by": event.delivered_by,
            "Purpose": PURPOSE_LABELS[event.purpose] + (" (fallback)" if event.fallback else ""),
            "Expected response": event.expected,
            "Sheets": ", ".join(event.sheets),
        }
        for event in pack.events
    ]


def main_events_list_docx(pack: ExercisePack, script: ScriptConfig) -> bytes:
    """The control team's main events list (EBA Handbook, par. 133 and 177)."""
    document = _start(
        "Main events list", f"{pack.bank_name} - {pack.scenario_name}", script, pack.mode,
        landscape=True, pack=pack, audience="the control team only", full=True,
    )
    document.add_paragraph(
        "For the control team and the observers only. Every inject to a player is one line: "
        "when it is triggered, who delivers it, what it is for and the response to look for. "
        "Purpose: an inject advances the simulation, challenges the players, or controls the "
        "course of the exercise."
    )
    if pack.mode == "dynamic":
        document.add_paragraph(
            "What one player should tell another is not handed out by the control team. It is "
            "listed as the expected response of the player who holds the information. The "
            "lines marked \"fallback\" are used only if that player has not passed it on in time."
        )
    else:
        document.add_paragraph(
            "The exercise is linear. A \"course correction\" line is used only if the players "
            "decide otherwise than the fixed path: the control team then tells them on which "
            "basis to continue and notes the deviation for the debrief."
        )
    rows = main_events_rows(pack, script)
    header = [name for name in rows[0] if name != "Branch" or len(pack.branches) > 1]
    _table(document, header, [[row[name] for name in header] for row in rows])
    document.add_heading("Counts", level=1)
    counts = {
        purpose: sum(1 for event in pack.events if event.purpose == purpose)
        for purpose in PURPOSE_LABELS
    }
    _table(document, ["Purpose", "Injects"],
           [[PURPOSE_LABELS[purpose], str(count)] for purpose, count in counts.items()])
    return _save(document)


def role_card_docx(pack: ExercisePack, script: ScriptConfig, role: str) -> bytes:
    """The card of one stakeholder the control team plays: knows, wants, says, with figures."""
    card = next(c for c in pack.roles if c.role == role)
    document = _start(f"Role card: {card.name}", pack.bank_name, script, pack.mode, pack=pack,
                      audience="the member of the control team who plays this role", full=True)
    document.add_paragraph(
        "For the member of the control team who plays this role. Stay in the role when a "
        "player contacts you. Give no figure that is not on this card."
    )
    _labelled(document, "Played by", "_______________________")
    document.add_heading("What you know", level=1)
    for line in card.knows:
        document.add_paragraph(line, style="List Bullet")
    document.add_heading("What you want", level=1)
    for line in card.wants:
        document.add_paragraph(line, style="List Bullet")
    document.add_heading("What you say, and when", level=1)
    _table(document, ["Moment", "When in this exercise", "What you say"],
           [list(line) for line in card.lines])
    document.add_heading("Figures you hold", level=1)
    if not card.figures:
        document.add_paragraph(
            "None. You hold no figure of the bank. If a player gives you one, you may repeat it."
        )
    for heading, rows in card.figures:
        document.add_heading(heading, level=2)
        _table(document, ["Figure", "Value"], [list(row) for row in rows])
    document.add_heading("Injects you deliver", level=1)
    if not card.delivers:
        document.add_paragraph("None in this pack. You answer if a player contacts you.")
    else:
        _table(
            document,
            ["No.", "Branch", "When", "Inject", "To", "Sheets"],
            [[str(event.number), _section_label(pack, event.section),
              f"{day_label(pack, event.day)}, {event.time}", event.title,
              _recipients(script, event), ", ".join(event.sheets)]
             for event in card.delivers],
        )
    if role == "bank_management":
        bank = next(d for d in pack.documents if d.actor == role)
        document.add_page_break()
        document.add_heading("What you see, day by day", level=1)
        for sentence in bank.information:
            document.add_paragraph(sentence, style="List Bullet")
        for inject in bank.shared:
            _write_inject(document, pack, inject)
        for title, injects in bank.branches:
            document.add_page_break()
            document.add_heading(title, level=1)
            if not injects:
                document.add_paragraph("You see nothing further on this branch.")
            for inject in injects:
                _write_inject(document, pack, inject)
    return _save(document)


# --------------------------------------------------------------------------- set-up documents

DRAFT_NOTE = (
    "Draft generated for the design team. Everything pre-filled here is a proposal to edit, "
    "not a decision."
)


def dossier_docx(pack: ExercisePack, script: ScriptConfig) -> bytes:
    """The mock bank dossier, shared with the players in advance (EBA Handbook, par. 167-168,
    177): the bank as it stands before the exercise starts. No real names."""
    dossier = pack.dossier
    document = _start(f"Mock bank dossier: {dossier.bank_name}", dossier.country, script, pack.mode,
                      pack=pack, audience="the players, before the exercise")
    document.add_paragraph(
        f"For the players, before the exercise. {dossier.bank_name} and {dossier.country} are "
        "fictitious; the figures are the bank's position before the first simulated day. "
        f"Amounts in {dossier.unit}."
    )
    document.add_heading("Business model", level=1)
    document.add_paragraph(f"{dossier.bank_name}: {dossier.business_model}")
    document.add_heading("Economic background", level=1)
    document.add_paragraph(dossier.background)
    document.add_heading("Simplified balance sheet", level=1)
    total = sum(amount for _, amount in dossier.assets)
    _table(document, ["Assets", dossier.unit],
           [[label, f"{amount:.2f}"] for label, amount in dossier.assets]
           + [["Total assets", f"{total:.2f}"]])
    document.add_paragraph()
    _table(document, ["Liabilities and capital", dossier.unit],
           [[label, f"{amount:.2f}"] for label, amount in dossier.liabilities]
           + [["Total liabilities and capital",
               f"{sum(amount for _, amount in dossier.liabilities):.2f}"]])
    document.add_heading("Liability stack", level=1)
    document.add_paragraph(
        "The layers that would absorb losses, in the order used in this exercise, which is "
        "not a statement of law. Covered deposits are protected."
    )
    _table(document, ["Layer", dossier.unit],
           [[label, f"{amount:.2f}"] for label, amount in dossier.stack])
    _reference(document, legal_refs.line(pack.legal, ["document:liability_stack"]))
    document.add_heading("Prudential indicators", level=1)
    _table(document, ["Indicator", "Value"], [list(row) for row in dossier.indicators])
    _glossary(document, script)
    return _save(document)


def rules_of_play_docx(pack: ExercisePack, script: ScriptConfig) -> bytes:
    """Rules of play, shared with players and observers in advance (par. 149-155)."""
    document = _start("Rules of play", pack.bank_name, script, pack.mode, pack=pack, draft=True,
                      audience="players and observers, before the exercise")
    document.add_paragraph(
        "For players and observers, before the exercise. The rules say what you may and may "
        "not do in the exercise. They are fixed once it starts."
    )
    for category, rules in rules_of_play(pack, script):
        document.add_heading(category, level=1)
        for rule in rules:
            document.add_paragraph(rule, style="List Number")
    document.add_heading("To be completed by the design team", level=1)
    for line in ("Channel for exchanges between players:", "How to reach the control team:",
                 "Dates and times of the sessions:", "Confidentiality and data protection:"):
        _labelled(document, line[:-1], "_______________________")
    return _save(document)


def questionnaire_docx(pack: ExercisePack, script: ScriptConfig) -> bytes:
    """Feedback questionnaire: the questions of Annex 7 of the Handbook, as written."""
    document = _start("Feedback questionnaire", pack.bank_name, script, pack.mode, pack=pack,
                      draft=True, audience="players and observers, after the exercise")
    document.add_paragraph(
        "The questions below are quoted from Annex 7 of the EBA Handbook on simulation "
        "exercises for resolution authorities (EBA/REP/2025/20). Select and adapt them."
    )
    document.add_paragraph(script.setup.questionnaire_intro)
    for question in script.setup.questionnaire:
        document.add_paragraph(question, style="List Number")
    return _save(document)


def observer_sheet_docx(pack: ExercisePack, script: ScriptConfig, player: str) -> bytes:
    """What the observer of one team tracks (par. 101). Observers see ahead of the players."""
    sheet = observer_sheet(pack, script, player)
    document = _start(f"Observer sheet: {sheet.name}", pack.bank_name, script, pack.mode, pack=pack,
                      draft=True, landscape=True,
                      audience="the observer who follows this team. Not to be shown to the players")
    document.add_paragraph(
        "For the observer who follows this team. Do not show it to the players: it lists "
        "decisions before they open. Record facts and times; do not judge."
    )
    _labelled(document, "Observer", "_______________________")
    document.add_heading("Decisions of this team", level=1)
    if sheet.decisions:
        header = ["Opens", "Decision", "Decide by", "Should consult", "Should inform",
                  "Real time taken", "Consulted and informed? Notes"]
        rows = [[*row[1:], "", ""] for row in sheet.decisions]
        if len(pack.branches) > 1:
            header, rows = ["Branch", *header], [[row[0], *rest] for row, rest in zip(sheet.decisions, rows)]
        _table(document, header, rows)
    else:
        document.add_paragraph("No decision opens to this team in this pack.")
    document.add_heading("Information this team should pass on", level=1)
    if sheet.relays:
        header = ["By", "What", "To", "Passed on in time?", "Fallback inject used?"]
        rows = [[*row[1:], "", ""] for row in sheet.relays]
        if len(pack.branches) > 1:
            header, rows = ["Branch", *header], [[row[0], *rest] for row, rest in zip(sheet.relays, rows)]
        _table(document, header, rows)
    else:
        document.add_paragraph(
            "Nothing is planned as this team's task to pass on"
            + (": the exercise is linear and the control team delivers every notification."
               if pack.mode == "linear" else ".")
        )
    document.add_heading("Also track", level=1)
    _table(document, ["What to track", "Observations"], [[track, ""] for track in sheet.tracks])
    document.add_heading("Other notes", level=1)
    for _ in range(4):
        document.add_paragraph("_" * 80)
    return _save(document)


def charter_docx(pack: ExercisePack, script: ScriptConfig) -> bytes:
    """SimEx Charter draft, on the template of Annex 1 of the Handbook."""
    document = _start("SimEx Charter (draft)", f"{pack.bank_name} - {pack.scenario_name}",
                      script, pack.mode, pack=pack, draft=True, audience="the design team", full=True)
    document.add_paragraph(
        "The elements and their descriptions are those of Annex 1 of the EBA Handbook "
        f"(EBA/REP/2025/20). Cells that start with [{DRAFT_MARK}] were pre-filled from the "
        "scenario; empty cells are for the design team."
    )
    _table(document, ["Element", "Description", "Concrete elements"],
           [list(row) for row in charter_rows(pack, script)])
    return _save(document)


def session_plan_docx(
    pack: ExercisePack, script: ScriptConfig, decisions: DecisionsConfig,
    sessions: int, hours: float,
) -> bytes:
    """Simulated days mapped to real sessions (par. 139-143)."""
    document = _start("Session plan", f"{pack.bank_name} - {pack.scenario_name}", script,
                      pack.mode, landscape=True, pack=pack, draft=True,
                      audience="the design team and the control team", full=True)
    document.add_paragraph(
        f"Input: {sessions} session{'s' if sessions > 1 else ''} of {hours:g} hours. Simulated "
        "time is accelerated: the last column gives the real time available for one "
        "simulated day, which the players should be told in advance. Breaks are placed at "
        "the FoLtF determination and at the resolution decision where the path has them "
        "and the number of sessions allows; the control team uses them to adjust the "
        "injects of the next session to what the players decided."
    )
    for heading, plan in session_plans(pack, decisions, sessions, hours):
        if heading:
            document.add_heading(heading, level=1)
        _table(
            document,
            ["Session", "Real length", "Simulated days", "Injects", "Real time per simulated day",
             "Ends with", "Date and time (to fill in)"],
            [[str(s.number), f"{hours:g} h",
              day_label(pack, s.first_day) if s.first_day == s.last_day
              else f"{day_label(pack, s.first_day)} to {day_label(pack, s.last_day)}",
              s.injects_text or str(s.injects), f"{s.minutes_per_day:.0f} min", s.ends_with, ""]
             for s in plan],
        )
        if len(plan) < sessions:
            document.add_paragraph(
                f"Only {len(plan)} of the {sessions} sessions are used: the path is too "
                "short for more."
            )
        breaks = {s.ends_with for s in plan}
        result = next(b for b in pack.branches
                      if not heading.startswith("Branch") or b.label == heading[-1]).result
        if result.foltf_day is None:
            document.add_paragraph(
                "No FoLtF is declared on this path, so the breaks are planned pauses only.")
        elif "Break at the resolution decision" not in breaks or \
                "Break at the FoLtF determination" not in breaks:
            document.add_paragraph(
                "There are too few sessions for a break at both the FoLtF determination and "
                "the resolution decision.")
    return _save(document)


# The checklist of Annex 8 of the Handbook, as a blank form.
FEEDBACK_CHECKLIST = (
    ("i) Decision on the timeline of feedback collection", ["Item", "Decision"],
     [["Date(s) and time for debrief", ""],
      ["Date(s) and time for feedback collection at the end of the exercise", ""]]),
    ("ii) Decision on the type of feedback",
     ["Type of feedback", "Description", "Present in the simulation (Yes/No)"],
     [["Hot Debriefing", "Occurs shortly after simulations", ""],
      ["Cold Debriefing", "Occurs after at least 24 hours", ""]]),
    ("iii) Decision on tools/platforms used to collect feedback",
     ["Ref", "Method", "Comments for implementation"],
     [["A", "Surveys/feedback and evaluation forms", ""],
      ["B", "Live session with the controllers", ""],
      ["C", "Automated monitoring tools", ""],
      ["D", "Direct observations from the control team", ""]]),
    ("iv) Decision on person(s) involved in collecting feedback",
     ["Task", "Person(s) responsible for delivery", "Deadline to prepare"],
     [["Preparation of the feedback templates", "", ""],
      ["Feedback collection process", "", ""],
      ["Handling and processing feedback", "", ""]]),
)


def feedback_checklist_docx(pack: ExercisePack, script: ScriptConfig) -> bytes:
    """Checklist for feedback collection: Annex 8 of the Handbook, left blank."""
    document = _start("Checklist for feedback collection", pack.bank_name, script, pack.mode,
                      pack=pack, audience="the design team, to fill in")
    document.add_paragraph(
        "Blank form on the template of Annex 8 of the EBA Handbook (EBA/REP/2025/20), for "
        "the design team to fill in. Nothing is pre-filled."
    )
    for heading, header, rows in FEEDBACK_CHECKLIST:
        document.add_heading(heading, level=1)
        _table(document, header, rows)
    return _save(document)


# Each output of the pack and the part of the EBA Handbook (EBA/REP/2025/20) it serves.
HANDBOOK_MAPPING = (
    ("players/mock_bank_dossier.docx", "Mock bank dossier, shared in advance", "par. 167-168, 177; no recognisable institution: par. 174"),
    ("players/rules_of_play.docx", "Rules of play, by the Handbook's three categories", "par. 149-155; stakeholders played: par. 200"),
    ("players/feedback_questionnaire.docx", "Feedback questionnaire", "Annex 7; par. 216-217"),
    ("observers/observer_sheet_<player>.docx", "What each observer tracks, per team", "par. 99-101"),
    ("control_team/simex_charter_draft.docx", "SimEx Charter draft", "Annex 1 (Section 2.4)"),
    ("control_team/session_plan.docx", "Simulated days mapped to sessions, accelerated time, breaks", "par. 139-143, 195-196"),
    ("control_team/main_events_list.docx", "Every inject: trigger, purpose, expected response, deliverer", "par. 128, 133, 177"),
    ("control_team/inject_sheets/<player>.docx", "Injects per player, within its information set", "par. 128-132, 191-192"),
    ("control_team/role_cards/<role>.docx", "Stakeholders played by the control team", "par. 132, 198-200"),
    ("control_team/control_team_pack.docx", "Simulation operational timeline, decision points, debrief questions", "par. 139-141; scenario type: par. 115"),
    ("Challenge injects and friendly or hostile assumptions", "Second valuation, interested buyer, buyers withdrawing", "par. 123, 129, 192"),
    ("Linear or dynamic exercise, course corrections", "Stated on every document", "par. 124-127"),
    ("control_team/feedback_checklist.docx", "Checklist for feedback collection, blank", "Annex 8"),
    ("SIMULATION label on every document and data file", "Header, footer and title; file names of data", "par. 174"),
    ("Closing report draft (after the exercise, from the control console)", "Decisions with timing and figures held, coordination steps, counterfactuals; lessons and recommendations left blank", "par. 221-225; timing of output against input: par. 209"),
)


def handbook_mapping_docx(pack: ExercisePack, script: ScriptConfig) -> bytes:
    document = _start("Outputs and the Handbook", pack.bank_name, script, pack.mode, landscape=True,
                      pack=pack, audience="the design team", full=True)
    document.add_paragraph(
        "Each output of this pack and the paragraph or annex of the EBA Handbook on "
        "simulation exercises for resolution authorities (EBA/REP/2025/20) it serves. The "
        "mapping is this project's reading of the Handbook."
    )
    _table(document, ["Output", "What it is", "EBA Handbook"],
           [list(row) for row in HANDBOOK_MAPPING])
    return _save(document)


# --------------------------------------------------------------------------- closing report

TO_COMPLETE = "To be completed - not generated"
RECOMMENDATION_ROWS = 5


def report_decisions_frame(report) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "simulated_day": line.day,
            "weekday": line.weekday,
            "decided_by": line.actor,
            "decision": line.decision_name,
            "option": line.option_name,
            "pre_planned_option": line.planned_name,
            "as_pre_planned": line.as_planned,
            "changed_the_path": line.applied,
            "taken_by_the_control_team": line.by_control,
            "consulted_first": ", ".join(line.consulted),
            "figures_held": "; ".join(line.figures),
            "figures_as_of_day": line.figures_day,
            "real_time": line.real_time,
            "real_minutes_taken": "" if line.minutes is None else round(line.minutes, 1),
        }
        for line in report.decisions
    ])


def report_counterfactuals_frame(report) -> pd.DataFrame:
    rows = []
    for case in report.counterfactuals:
        row = {"decided_by": case.actor, "decision": case.decision_name, "change": case.change,
               "computed": case.possible, "difference": case.differences}
        if case.scorecard is not None:
            row |= {label: value for label, value in scorecard_rows(case.scorecard, report.unit)}
        rows.append(row)
    return pd.DataFrame(rows)


def closing_report_docx(report, script: ScriptConfig, together: str = "") -> bytes:
    """Draft closing report (EBA Handbook, par. 221-225). Facts and differences in outcomes;
    lessons and recommendations are left to people."""
    texts = script.report
    document = _start(f"Closing report (draft): {report.title}", report.bank_name, script,
                      report.mode, landscape=True, facts=report.overview[:5],
                      audience="the control team and the design team, to complete")
    document.add_paragraph(texts.intro)
    document.add_paragraph(texts.circulation)

    document.add_heading("1. Overview", level=1)
    _table(document, ["", ""], [list(row) for row in report.overview])
    document.add_heading("Outcome scorecard", level=2)
    _table(document, ["", "As it happened"],
           [list(row) for row in scorecard_rows(report.actual, report.unit)])

    document.add_heading("2. Timeline of decisions", level=1)
    if not report.decisions:
        document.add_paragraph("No decision was recorded.")
    else:
        _table(
            document,
            ["Simulated day", "Decided by", "Decision", "Option", "Pre-planned option",
             "Real time", "Real minutes from opening", "Notes"],
            [[
                f"Day {line.day}" + (f", {line.weekday}" if line.weekday else ""),
                line.actor, line.decision_name, line.option_name, line.planned_name,
                line.real_time.replace("T", " "),
                "not recorded" if line.minutes is None else f"{line.minutes:.1f}",
                "; ".join(note for note in (
                    "" if line.as_planned else "different from the pre-planned path",
                    "taken by the control team" if line.by_control else "",
                    "" if line.applied else "logged; the path did not change",
                ) if note),
            ] for line in report.decisions],
        )
        decision_references(document, legal_refs.of(script),
                            [(line.decision_name, line.decision) for line in report.decisions])
        document.add_paragraph(
            "Real minutes are counted from the moment the decision opened to the moment it "
            "was recorded, pauses excluded."
            if report.live else "The path was not played, so no real time was recorded."
        )
        _picture(document, docfigures.decision_timeline_png(report), 9.0, "Decision timeline: " + "; ".join(
            f"day {row['day']}, {row['label']}, {row['option']}"
            for row in docfigures.decision_rows(report)) + ".")
        document.add_heading("Figures held by the deciding player", level=2)
        document.add_paragraph(
            "For each decision, the figures the deciding player had at that moment: its own "
            "set, at its own reporting delay."
        )
        _table(
            document,
            ["Simulated day", "Decided by", "Decision", "Figures as of", "Figures held"],
            [[f"Day {line.day}", line.actor, line.decision_name, f"day {line.figures_day}",
              "; ".join(line.figures)] for line in report.decisions],
        )

    document.add_heading("3. Coordination steps", level=1)
    if not report.live:
        document.add_paragraph("The path was not played, so no coordination step was recorded.")
    elif not report.steps:
        document.add_paragraph("No coordination step was expected up to this point.")
    else:
        document.add_paragraph(texts.coordination_note)
        _table(
            document,
            ["About", "Step expected by the script", "By", "With or to", "Record"],
            [[step.about, step.step, step.by, step.to,
              ("Recorded as done" if step.recorded else "Not recorded as done") + f": {step.how}"]
             for step in report.steps],
        )
        done = sum(step.recorded for step in report.steps)
        document.add_paragraph(
            f"{done} of {len(report.steps)} steps are recorded as done."
        )

    if report.conduct:
        document.add_heading("4. Conduct of the exercise", level=1)
        _table(document, ["", ""], [list(row) for row in report.conduct])
    if getattr(report, "ai_replies", ()):
        document.add_heading("Replies drafted by AI", level=2)
        document.add_paragraph(
            "Requests from players to a simulated stakeholder for which a general-purpose AI "
            "drafted the reply. The model was given only that stakeholder's role card and the "
            "figures it held at that moment; every number in the draft was checked against "
            "them; a member of the control team read each draft and sent it."
        )
        _table(document, ["Simulated time", "Asked by", "Reply sent as", "Request", "Drafted by", "Before sending"], [list(row) for row in report.ai_replies])

    document.add_heading("5. What the model gives with one decision changed", level=1)
    if report.counterfactuals:
        document.add_paragraph(texts.counterfactual_note)
        _table(
            document,
            ["Decided by", "Decision", "Change", "Difference from what happened"],
            [[case.actor, case.decision_name, case.change,
              case.differences if case.possible else f"Not computed: {case.differences}"]
             for case in report.counterfactuals],
        )
    else:
        document.add_paragraph(together or (
            "This entity is part of a cross-border group: a decision changed here would also "
            "change the other entity. The group's closing report gives what the model gives "
            "with one decision of the group changed."
        ))

    bars = docfigures.counterfactual_rows(report)
    if len(bars) > 1:
        _picture(document, docfigures.counterfactual_png(report), 9.0, "Counterfactuals: " + "; ".join(
            f"{row['label']}, {row['outcome']}" for row in bars) + ".")

    number = 6
    for section in texts.human_sections:
        document.add_heading(f"{number}. {section.title}", level=1)
        document.add_paragraph().add_run(f"[{TO_COMPLETE}]").bold = True
        document.add_paragraph(section.prompt)
        for _ in range(3):
            document.add_paragraph("_" * 100)
        number += 1
    document.add_heading(f"{number}. Recommendations", level=1)
    document.add_paragraph().add_run(f"[{TO_COMPLETE}]").bold = True
    document.add_paragraph(
        "Each recommendation is assigned to a team or an authority, ranked by priority where "
        "relevant, and given a timeframe (EBA Handbook, par. 225)."
    )
    _table(document, ["Recommendation", "Assigned to", "Priority", "To be actioned by"],
           [["", "", "", ""] for _ in range(RECOMMENDATION_ROWS)])
    return _save(document)


def report_files(report, script: ScriptConfig) -> dict[str, bytes]:
    """The closing report and its data: file name -> content."""
    return {
        "SIMULATION_closing_report_draft.docx": closing_report_docx(report, script),
        "SIMULATION_report_decisions.csv": _csv(report_decisions_frame(report)),
        "SIMULATION_report_counterfactuals.csv": _csv(report_counterfactuals_frame(report)),
    }


def report_to_zip(report, script: ScriptConfig) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in report_files(report, script).items():
            archive.writestr(zipfile.ZipInfo(name, date_time=(2028, 1, 1, 0, 0, 0)), content)
    return buffer.getvalue()


def _timeline_rows(pack: ExercisePack, branch) -> list[list[str]]:
    events: dict[int, list[str]] = {}
    for event in branch.result.events:
        events.setdefault(event.day, []).append(f"{event.kind.replace('_', ' ')}: {event.detail}")
    rows = []
    for _, row in branch.result.table.iterrows():
        day = int(row["day"])
        survival = row["survival_days"]
        rows.append([
            day_label(pack, day),
            str(row["status"]).replace("_", " ").replace("failed", "payments default"),
            f"{row['cum_deposit_loss_pct']:.1f}",
            f"{row['outflow_total']:.2f}",
            f"{row['queued_withdrawals']:.2f}",
            f"{row['liquidity_buffer']:.2f}",
            "no outflow" if survival == float("inf") else f"{survival:.1f}",
            f"{100 * row['cet1_ratio']:.1f}",
            f"{row['ela_funding']:.2f}",
            " | ".join(events.get(day, [])),
        ])
    return rows


def control_team_docx(pack: ExercisePack, script: ScriptConfig, decisions: DecisionsConfig) -> bytes:
    """Full information: both branches, every decision point and where each option leads."""
    document = _start(
        "Control team pack", f"{pack.bank_name} - {pack.scenario_name}", script, pack.mode,
        landscape=True, pack=pack, audience="the control team only. Full information: never to "
                                            "be shown to the players", full=True,
    )
    if pack.taxonomy:
        document.add_paragraph(f"Scenario type (EBA Handbook, par. 115): {pack.taxonomy}.")
    document.add_paragraph(
        "For the control team only. The players are the four authorities. Their inject sheets "
        "show each player's own figures, delayed as set out below, and branch codes without "
        "outcomes. Bank management and the other stakeholders are played by the control team."
    )

    document.add_heading("Overview", level=1)
    _table(
        document,
        ["", *(f"Branch {b.label}: {b.name}" for b in pack.branches)],
        [
            [label, *(dict(scorecard_rows(b.result.scorecard, pack.unit))[label] for b in pack.branches)]
            for label, _ in scorecard_rows(pack.branches[0].result.scorecard, pack.unit)
        ],
    )
    document.add_paragraph(
        f"Day 1 is a {pack.start_weekday}. Amounts in {pack.unit}. The horizon is "
        f"{pack.horizon} days."
    )

    document.add_heading("Who sees what, and how late", level=1)
    header = list(pack.information_rows[0])
    _table(document, header, [[row[name] for name in header] for row in pack.information_rows])
    document.add_paragraph(
        "Delays are in whole days. Stage changes, a payments default, the FoLtF declaration "
        "and resolution reach every informed actor the same day; only the figures are delayed."
    )

    # One page per player: what that player sees, drawn from its own injects only.
    for player in PLAYERS:
        document.add_page_break()
        document.add_heading(f"What the players see: {script.actors[player]}", level=1)
        document.add_paragraph(
            "For the control team only. The chart is drawn from this player's own inject "
            "sheets: its regular reports at the day they refer to, the stage changes it is "
            "told of (vertical lines) and its decision windows (bars). The shaded stretch is "
            "the time before its first report."
        )
        panels = [docfigures.player_series(branch, player, pack.unit) for branch in pack.branches]
        _picture(document, docfigures.player_summary_png(panels, script.actors[player]), 6.3,
                 " ".join(docfigures.series_text(series) for series in panels))

    for branch in pack.branches:
        document.add_page_break()
        document.add_heading(f"Branch {branch.label}: {branch.name}", level=1)
        document.add_paragraph(f"Outcome: {OUTCOME_LABELS[branch.result.outcome]}.")
        document.add_heading("Simulation operational timeline (full information)", level=2)
        _table(
            document,
            [*TIMELINE_COLUMNS.values(), "Events"],
            _timeline_rows(pack, branch),
        )
        need = branch.result.funding_need
        if need is not None:
            document.add_paragraph(
                f"Liquidity need over the {need.days} days after the run ends: "
                f"{need.need:.2f} {pack.unit}. This is projected outflows of "
                f"{need.projected_outflows:.2f} plus {need.queued:.2f} requested but not yet "
                f"settled, less {need.remaining_liquidity:.2f} of remaining liquidity."
            )
        if branch.result.resolution_weekend:
            document.add_paragraph(
                "FoLtF is declared after a Friday close: the authorities have a resolution "
                "weekend before the bank would reopen on Monday."
            )
        if branch.resolution is not None:
            document.add_heading("Resolution summary", level=2)
            document.add_paragraph(
                "The valuation write-down is not known: the three columns use the low, central "
                "and high values, all assumptions. Players' documents use the "
                f"{pack.writedown_level} value. Losses fall on CET1 first, then on the layers "
                "below in the order shown, which is the order used in this model."
            )
            rows = {
                level: summary_rows(summary, pack.unit)
                for level, summary in branch.resolution.items()
            }
            _table(
                document,
                ["", "Low", "Central", "High"],
                [
                    [label, rows["low"][index][1], rows["central"][index][1], rows["high"][index][1]]
                    for index, (label, _) in enumerate(rows["central"])
                ],
            )
            document.add_paragraph(breakpoint_text(branch.resolution["central"]))
            _reference(document, legal_refs.line(pack.legal, legal_refs.figure_concepts(
                (label for label, _ in rows["central"]), pack.legal)))
        document.add_heading("Decisions taken on this branch", level=2)
        log = decision_log_rows(branch.result.decision_log, pack.unit)
        if log:
            header = list(log[0])
            _table(document, header, [[row[name] for name in header] for row in log])
        else:
            document.add_paragraph("No decision is taken on this branch.")
        document.add_heading("Decision points and where each option leads", level=2)
        document.add_paragraph(
            "For the option the branch follows, the line shows the branch itself. For any "
            "other option it shows a run in which that option is taken at the first possible "
            "close and nothing is decided afterwards."
            + (" The exercise is linear, so these other runs are not played: they are for "
               "the debrief." if pack.mode == "linear" else "")
        )
        _table(
            document,
            ["Code", "Opens", "Decide by", "Actor", "Decision", "Option", "Followed here",
             "Leads to", "Deposits lost (%)"],
            [
                [
                    point.code,
                    day_label(pack, point.open_day),
                    day_label(pack, point.deadline_day),
                    actor_label(point.actor),
                    point.decision_name,
                    point.option_name,
                    "yes" if point.taken else "",
                    f"{OUTCOME_LABELS[point.outcome]} (day {point.end_day})",
                    f"{point.deposits_lost_pct:.1f}",
                ]
                for point in branch.decision_points
            ],
        )
        decision_references(document, pack.legal,
                            [(point.decision_name, point.decision) for point in branch.decision_points])

    document.add_page_break()
    document.add_heading("Expected coordination steps", level=1)
    _table(
        document,
        ["Decision", "Decided by", "Consult first", "Inform afterwards"],
        [
            [
                definition.name,
                script.actors[definition.actor],
                ", ".join(script.actors[a] for a in script.coordination[key].consult) or "nobody",
                ", ".join(script.actors[a] for a in script.coordination[key].inform) or "nobody",
            ]
            for key, definition in decisions.decisions.items()
        ],
    )
    document.add_heading("Simulated stakeholders", level=1)
    document.add_paragraph(
        "The control team plays these roles; each has a role card. Decide before the "
        "exercise who plays which, and who answers if a player contacts a stakeholder that "
        "is not on this list. Tell the players in advance which stakeholders are played."
    )
    _table(
        document,
        ["Role", "Played by", "Injects it delivers"],
        [[card.name, "", str(len(card.delivers))] for card in pack.roles],
    )
    document.add_heading("Challenge injects and assumptions", level=1)
    document.add_paragraph(
        "Assumptions: " + (
            "hostile. The liquidity need at reopening is shown with a negative market "
            "reaction as well, and the hostile challenge injects are in the pack."
            if pack.hostile else
            "friendly. No challenge from creditors, no buyer withdrawing, no negative market "
            "reaction at reopening."
        )
    )
    _table(
        document,
        ["Challenge inject", "In this pack", "Changes the figures of the run"],
        [[item.title, "yes" if key in pack.challenges else "no",
          "yes: a news shock" if item.effect == "panic_shock" else "no"]
         for key, item in script.challenges.items()],
    )
    if "second_valuation" in script.challenges and pack.writedown_level == "high":
        document.add_paragraph(
            "The players already get the high write-down, so there is no worse valuation to "
            "deliver: the updated valuation is not played."
        )
    document.add_heading("Ambient injects", level=1)
    if not pack.ambient_on:
        document.add_paragraph("Ambient injects are switched off in this pack.")
    else:
        document.add_paragraph(
            "Background noise to add pressure: press, politics, other authorities. They have "
            "no effect on any figure in the model. Leave them out if they distract."
        )
        rows = [
            [f"Branch {branch.label}", day_label(pack, inject.day), inject.time, inject.title,
             script.actors[actor]]
            for branch in pack.branches
            for actor, injects in branch.injects.items() if actor in PLAYERS
            for inject in injects if inject.kind == "ambient"
        ]
        _table(document, ["Branch", "Day", "Time", "Inject", "Recipient"], rows)
    legal_table_page(document, pack.legal)
    document.add_heading("Known limits", level=1)
    for limit in KNOWN_LIMITS:
        document.add_paragraph(limit, style="List Bullet")
    document.add_heading("Questions for the debrief", level=1)
    for question in script.debrief:
        document.add_paragraph(question, style="List Number")
    _glossary(document, script)
    return _save(document)


# --------------------------------------------------------------------------- data


def injects_frame(pack: ExercisePack) -> pd.DataFrame:
    """Every inject of every actor on every branch (control team data)."""
    rows = []
    for branch in pack.branches:
        for actor, injects in branch.injects.items():
            for inject in injects:
                due = inject.decision_due
                rows.append(
                    {
                        "branch": branch.label,
                        "actor": actor,
                        "day": inject.day,
                        "weekday": inject.weekday,
                        "time": inject.time,
                        "kind": inject.kind,
                        "title": inject.title,
                        "from": inject.sender,
                        "text": inject.text,
                        "notification_figures": "; ".join(
                            f"{figure.text} (threshold {figure.threshold_text}, day {figure.as_of_day})"
                            for figure in inject.headline
                        ),
                        "resolution_estimates": "; ".join(f.text for f in inject.resolution),
                        "report_as_of_day": inject.figures[0].as_of_day if inject.figures else "",
                        "report": "; ".join(figure.text for figure in inject.figures),
                        "purpose": inject.purpose,
                        "fallback": inject.fallback,
                        "trigger": inject.trigger,
                        "delivered_by": inject.delivered_by,
                        "expected_response": inject.expected,
                        "decision": due.name if due else "",
                        "options": "; ".join(f"{o.branch_code} {o.name}" for o in due.options)
                        if due else "",
                        "decide_by_day": due.deadline_day if due else "",
                        "consult": ", ".join(due.consult) if due else "",
                        "inform": ", ".join(due.inform) if due else "",
                    }
                )
    return pd.DataFrame(rows)


def decision_points_frame(pack: ExercisePack) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "branch": branch.label,
                "code": point.code,
                "actor": point.actor,
                "decision": point.decision_name,
                "option": point.option_name,
                "opens_day": point.open_day,
                "decide_by_day": point.deadline_day,
                "followed_on_this_branch": point.taken,
                "leads_to": point.outcome,
                "end_day": point.end_day,
                "deposits_lost_pct": round(point.deposits_lost_pct, 2),
                "ela_outstanding": round(point.ela_outstanding, 2),
            }
            for branch in pack.branches
            for point in branch.decision_points
        ]
    )


def _csv(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False).encode("utf-8")


def pack_files(
    pack: ExercisePack, script: ScriptConfig, decisions: DecisionsConfig,
    sessions: Optional[int] = None, session_hours: Optional[float] = None,
) -> dict[str, bytes]:
    """All files of the pack: path inside the zip -> content.

    players/: shared with the players in advance. observers/: for the observers only.
    control_team/: everything else. sessions, session_hours: inputs of the session plan
    (default: config).
    """
    sessions, session_hours = resolve_sessions(script, sessions, session_hours)
    files = {
        "players/mock_bank_dossier.docx": dossier_docx(pack, script),
        "players/rules_of_play.docx": rules_of_play_docx(pack, script),
        "players/feedback_questionnaire.docx": questionnaire_docx(pack, script),
    }
    for player in PLAYERS:
        files[f"observers/observer_sheet_{player}.docx"] = observer_sheet_docx(pack, script, player)
    files["control_team/simex_charter_draft.docx"] = charter_docx(pack, script)
    files["control_team/session_plan.docx"] = session_plan_docx(
        pack, script, decisions, sessions, session_hours)
    files["control_team/feedback_checklist.docx"] = feedback_checklist_docx(pack, script)
    files["control_team/handbook_mapping.docx"] = handbook_mapping_docx(pack, script)
    files |= {
        f"control_team/inject_sheets/{actor}.docx": inject_sheets_docx(pack, script, actor)
        for actor in PLAYERS
    }
    files["control_team/main_events_list.docx"] = main_events_list_docx(pack, script)
    for role in ROLES:
        files[f"control_team/role_cards/{role}.docx"] = role_card_docx(pack, script, role)
    files["control_team/control_team_pack.docx"] = control_team_docx(pack, script, decisions)
    files["control_team/data/SIMULATION_main_events_list.csv"] = _csv(
        pd.DataFrame(main_events_rows(pack, script)))
    files["control_team/data/SIMULATION_injects.csv"] = _csv(injects_frame(pack))
    files["control_team/data/SIMULATION_decision_points.csv"] = _csv(decision_points_frame(pack))
    log_rows = [
        {"branch": branch.label, **row}
        for branch in pack.branches
        for row in decision_log_rows(branch.result.decision_log, pack.unit)
    ]
    files["control_team/data/SIMULATION_decision_log.csv"] = _csv(pd.DataFrame(log_rows))
    for branch in pack.branches:
        files[f"control_team/data/SIMULATION_daily_table_branch_{branch.label}.csv"] = _csv(branch.result.table)
    resolution_rows = [
        {"branch": branch.label, "level": level, "figure": label, "value": value}
        for branch in pack.branches
        if branch.resolution is not None
        for level, summary in branch.resolution.items()
        for label, value in summary_rows(summary, pack.unit)
    ]
    if resolution_rows:
        files["control_team/data/SIMULATION_resolution_summary.csv"] = _csv(pd.DataFrame(resolution_rows))
    return files


def pack_to_zip(
    pack: ExercisePack, script: ScriptConfig, decisions: DecisionsConfig,
    sessions: Optional[int] = None, session_hours: Optional[float] = None,
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        files = pack_files(pack, script, decisions, sessions, session_hours)
        for name, content in files.items():
            # A fixed timestamp keeps the zip identical for identical inputs.
            archive.writestr(zipfile.ZipInfo(name, date_time=(2028, 1, 1, 0, 0, 0)), content)
    return buffer.getvalue()


def daily_table_to_csv(result) -> bytes:
    return result.table.to_csv(index=False).encode("utf-8")
