"""Legal references, for orientation only (config/legal.yaml, docs/LEGAL_REFERENCES.md):
every reference shown comes from the file; no article is cited in any template outside it;
with the switch off none appears; the file adds nothing to the table it comes from."""

import io
import re
from pathlib import Path

import pytest
from docx import Document
from streamlit.testing.v1 import AppTest

from app import ui
from engine import export, legal, live
from engine import group_live as gl
from engine.config import load_config
from engine.decisions import Choice, choices_of
from engine.group import GroupChoices, at_severity
from engine.group_export import group_files
from engine.group_script import build_group_pack
from engine.live_run import scripted_showcase
from engine.schema import PLAYERS
from engine.script import build_pack
from engine import system

ROOT = Path(__file__).resolve().parent.parent
APP_PATH = str(ROOT / "app" / "main.py")
CONFIG = load_config()
LEGAL = CONFIG.script.legal
OFF = CONFIG.model_copy(update={"script": legal.with_switch(CONFIG.script, False)})
PATHS = CONFIG.decisions.paths
CITATION = re.compile(r"\b(BRRD|DGSD|SRMR)\b|\bArticles?\s+\d")


def packs(config):
    """The documents of the three kinds of pack: name -> text."""
    script, show = config.script, config.script.showcase
    single = build_pack(config.banks[show.bank], config.model, config.scenarios[show.scenario],
                        config.decisions, script,
                        [(PATHS[k].name, choices_of(PATHS[k])) for k in (show.compare_path, show.path)])
    files = {f"single/{k}": v for k, v in export.pack_files(single, script, config.decisions).items()}
    college = config.college.showcase
    group = build_group_pack(
        config.groups[college.group], config.banks, config.model,
        at_severity(config.scenarios, college.scenario, college.severity), college.scenario,
        config.decisions, script, config.college,
        [("No decision of the group", GroupChoices()),
         ("As decided", GroupChoices(group=(Choice(college.decision, college.option, college.day),)))])
    files |= {f"group/{k}": v for k, v in group_files(group, config.decisions, config.college).items()}
    pair, forum = system.pair(config, config.systems.showcase.pair)
    both = build_group_pack(pair, config.banks, config.model, config.scenarios,
                            config.systems.showcase.scenario, config.decisions, script, forum,
                            [("Authorities act late", GroupChoices()),
                             ("As decided", GroupChoices(parent=choices_of(PATHS["early_foltf"])))])
    files |= {f"system/{k}": v for k, v in group_files(both, config.decisions, forum).items()}
    out = {}
    for name, content in files.items():
        if name.endswith(".docx"):
            document = Document(io.BytesIO(content))
            out[name] = "\n".join([p.text for p in document.paragraphs]
                                  + [c.text for t in document.tables for r in t.rows for c in r.cells])
    return out


@pytest.fixture(scope="module")
def shown_on():
    return packs(CONFIG)


@pytest.fixture(scope="module")
def shown_off():
    return packs(OFF)


ALLOWED = {legal.shown(LEGAL, key) for key in LEGAL.references}


def reference_parts(text: str) -> list[str]:
    """The references of every "Reference:" line, read against the file: a part that is not
    one of its references is returned as it stands (and makes the caller's check fail)."""
    parts = []
    for line in text.split("\n"):
        if legal.PREFIX not in line:
            continue
        rest = line.split(legal.PREFIX, 1)[1].rstrip(".")
        while rest:
            match = max((r for r in ALLOWED if rest.startswith(r)), key=len, default=None)
            if match is None:
                parts.append(rest)
                break
            parts.append(match)
            rest = rest[len(match):].removeprefix("; ").removeprefix(". ")
            if rest.startswith(LEGAL.note[:20]):
                break
    return parts


# ------------------------------------------------- the file


def table_of_the_source() -> list[tuple[str, str, str]]:
    rows = []
    for line in (ROOT / "docs" / "LEGAL_REFERENCES.md").read_text().splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[0] not in ("Concept in the tool", "---"):
            rows.append(tuple(cells))
    return rows


def test_the_file_holds_the_table_of_the_source_and_nothing_else():
    source = table_of_the_source()
    assert len(source) == len(LEGAL.references) == 23
    for (concept, reference, status), (key, row) in zip(source, LEGAL.references.items()):
        assert row.concept == concept and row.status == status, key
        # The ELA row of the source ends with an instruction ("Give no article."), not a reference.
        assert row.reference == reference.removesuffix(" Give no article.").rstrip("."), key
    assert LEGAL.show is True
    assert LEGAL.note == "References are for orientation only. The tool does not model the legal conditions."


def test_no_article_is_cited_in_any_template_outside_the_file():
    sources = [*(ROOT / "config").glob("*.yaml"), *(ROOT / "engine").rglob("*.py"),
               *(ROOT / "app").rglob("*.py"), *(ROOT / "app").rglob("*.css")]
    for path in sources:
        if path.name == "legal.yaml":
            continue
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            assert not CITATION.search(line), f"{path.relative_to(ROOT)}:{number}: {line.strip()}"


def test_status_b_carries_to_be_confirmed():
    assert legal.shown(LEGAL, "foltf_deemed") == "BRRD Article 32(4) (to be confirmed)"
    assert legal.shown(LEGAL, "early_intervention") == 'BRRD Article 27 "Early intervention measures"'
    assert legal.shown(LEGAL, "valuation").endswith("(paragraph to be confirmed)")
    assert legal.line(LEGAL, ["decision:public_statement"]) == ""  # no row, no reference


# ------------------------------------------------- shown, from the file only


def test_every_reference_in_the_documents_comes_from_the_file(shown_on):
    parts = [part for text in shown_on.values() for part in reference_parts(text)]
    assert len(parts) > 100
    assert set(parts) <= ALLOWED, set(parts) - ALLOWED
    # Outside the "Reference:" lines and the table page, no document cites an article.
    for name, text in shown_on.items():
        rest = "\n".join(line for line in text.split("\n") if legal.PREFIX not in line
                         and line not in {r for row in legal.table(LEGAL) for r in row}
                         and not any(line == f"{act}: {t}" for act, t in LEGAL.acts.items()))
        rest = rest.replace(LEGAL.note, "")
        for act, t in LEGAL.acts.items():
            rest = rest.replace(t, "")
        assert not re.search(r"\bArticles?\s+\d", rest), name


def test_references_sit_where_the_concepts_are(shown_on):
    pack = shown_on["single/control_team/control_team_pack.docx"]
    assert "Legal references" in pack and LEGAL.note in pack
    assert 'BRRD Article 128c "Crisis management simulations"' in pack  # the whole table
    assert "Failing or likely to fail (FoLtF). Reference: BRRD Article 32(1), point (a)" in pack
    dossier = shown_on["single/players/mock_bank_dossier.docx"]
    assert "Reference: BRRD Article 48 (to be confirmed)" in dossier
    sheets = shown_on["single/control_team/inject_sheets/competent_authority.docx"]
    assert 'Reference: BRRD Article 27 "Early intervention measures"' in sheets
    college = shown_on["group/college/group_control_pack.docx"]
    assert "Reference: BRRD Article 88" in college
    # Emergency liquidity assistance has a row with no article: the row's words, no number.
    central = shown_on["single/control_team/inject_sheets/central_bank.docx"]
    assert "Reference: Not in the BRRD or DGSD: a central bank matter" in central


def test_the_closing_report_and_the_team_pages_carry_them_too():
    state, context = scripted_showcase(CONFIG)
    from engine.report import live_report
    text = "\n".join(p.text for p in Document(io.BytesIO(
        export.closing_report_docx(live_report(state, context), context.script))).paragraphs)
    assert "Failing or likely to fail (FoLtF). Reference: BRRD Article 32(1), point (a)" in text
    views = [live.player_view(state, context, player) for player in PLAYERS]
    references = [inject["reference"] for view in views for inject in view["injects"] if inject["reference"]]
    assert references and {p for r in references for p in reference_parts(r)} <= ALLOWED


# ------------------------------------------------- the switch


def test_with_the_switch_off_no_reference_appears(shown_off):
    assert len(shown_off) > 100
    for name, text in shown_off.items():
        assert legal.PREFIX not in text and "Legal references" not in text, name
        assert not CITATION.search(text), name
    state, context = scripted_showcase(OFF)
    for player in PLAYERS:
        assert all(inject["reference"] == "" for inject in live.player_view(state, context, player)["injects"])
    group = gl.start(gl.GroupLiveSetup("G", CONFIG.college.showcase.group, "fast_run"), "2028-03-01T09:00:00", OFF)
    for view in gl.player_views(group, OFF).values():
        assert all(not inject["reference"] for inject in view["injects"])


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(ui, "LANDING", "home")
    return AppTest.from_file(APP_PATH)


def test_the_app_shows_them_and_the_switch_hides_them(app):
    app.run(timeout=60)
    assert any("Article 128c" in c.value and "in cross-border situations" in c.value for c in app.caption)
    app.session_state["control_unlocked"] = True
    app.switch_page("views/console.py").run(timeout=60)
    assert app.toggle(key="show_legal").value is True
    app.button(key="live_start").click().run(timeout=60)
    for _ in range(14):
        app.button(key="live_step").click().run(timeout=60)
    cards = [c.value for c in app.caption if c.value.startswith(legal.PREFIX)]
    assert cards and set(p for c in cards for p in reference_parts(c)) <= ALLOWED
    # A new exercise started with the switch off shows none.
    app.button(key="live_close").click().run(timeout=60)
    app.toggle(key="show_legal").set_value(False).run(timeout=60)
    app.button(key="live_start").click().run(timeout=60)
    for _ in range(14):
        app.button(key="live_step").click().run(timeout=60)
    assert not any(c.value.startswith(legal.PREFIX) for c in app.caption)
    app.switch_page("views/home.py").run(timeout=60)
    assert not any("Article 128c" in c.value for c in app.caption)


def test_the_assumptions_page_lists_the_whole_table(app):
    app.switch_page("views/assumptions.py").run(timeout=60)
    assert not app.exception and "Legal references" in [tab.label for tab in app.tabs]
    table = next(frame.value for frame in app.dataframe if "Reference" in frame.value.columns)
    assert len(table) == len(LEGAL.references) and set(table["Status"]) == {"A", "B", "-", "A for the article, B for the paragraph"}
    assert any(LEGAL.note in getattr(element, "value", "") for element in app.info)
