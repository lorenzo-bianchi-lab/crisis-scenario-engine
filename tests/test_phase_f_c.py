"""SPEC_V2 and SPEC_V3 phase F, part c: the cross-border group in the app, and the map
(dev/SPEC_UI.md, UI-3 item 5)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app import charts, group_view, theme
from app.assumptions import FLAG, group_table
from engine.config import load_config
from engine.decisions import Choice, choices_of
from engine.group import PARENT, SUBSIDIARY, GroupChoices, simulate_group

APP_PATH = str(Path(__file__).resolve().parent.parent / "app" / "main.py")
CONFIG = load_config()
GROUP = CONFIG.groups["showcase"]
UNIT = CONFIG.banks[GROUP.parent.bank].unit
TEAMS = {"parent": ["Competent authority of Country A"], "subsidiary": ["Competent authority of Country B"]}


def run(*choices):
    return simulate_group(GROUP, CONFIG.banks, CONFIG.model, CONFIG.scenarios, "fast_run",
                          CONFIG.decisions, GroupChoices(group=tuple(choices)), PARENT, CONFIG.college)


FREE = run()
FENCED = run(Choice("restrict_transfers", "ring_fence", 4))
CAPPED = run(Choice("restrict_transfers", "cap", 1))


def texts(figure) -> list[str]:
    return [annotation.text for annotation in figure.layout.annotations]


# ------------------------------------------------- the map


def test_map_state_is_the_days_transfer_and_restriction():
    state = charts.group_map_state(FREE, 4)
    assert [(t.lender, t.borrower) for t in state["transfers"]] == [(SUBSIDIARY, PARENT)]
    assert not state["restricted"] and state["restriction"] == ""
    assert state["entities"]["parent"]["borrowed"] == pytest.approx(state["transfers"][0].amount)
    assert state["entities"]["subsidiary"]["lent"] == pytest.approx(state["transfers"][0].amount)
    assert state["largest"] == max(t.amount for t in FREE.transfers)
    later = charts.group_map_state(FENCED, FENCED.ring_fence_from)
    assert later["restricted"] and later["restriction"] == "Full ring-fence, no further lending"
    assert later["transfers"] == []
    assert not charts.group_map_state(FENCED, FENCED.ring_fence_from - 1)["restricted"]
    assert charts.group_map_state(CAPPED, 3)["restriction"] == "Cap further lending to the group"
    ended = charts.group_map_state(FENCED, FENCED.parent.end_day + 1)
    assert ended["entities"]["parent"]["ended"] and not ended["entities"]["subsidiary"]["ended"]


def test_map_draws_both_countries_the_flow_sized_by_amount_and_the_block_in_words():
    first, second = FREE.transfers
    small = charts.build_group_map(FREE, first.day, UNIT, TEAMS, "Thursday")
    large = charts.build_group_map(FREE, second.day, UNIT, TEAMS)
    assert theme.uses_template(small)
    words = " | ".join(texts(small))
    for wanted in ("<b>Country A</b> (home)", "<b>Country B</b> (host)", GROUP.parent.name,
                   GROUP.subsidiary.name, "Competent authority of Country B",
                   f"<b>{first.amount:.2f} {UNIT}</b>", f"lent on day {first.day}"):
        assert wanted in words, wanted
    assert f"day {first.day}, Thursday" in small.layout.title.text
    width = lambda figure: next(a.arrowwidth for a in figure.layout.annotations if a.showarrow)  # noqa: E731
    assert second.amount > first.amount and width(large) > width(small)
    # The arrow points at the borrower: from the subsidiary's box to the parent's.
    arrow = next(a for a in small.layout.annotations if a.showarrow)
    assert arrow.ax > arrow.x
    # No transfer and no restriction: a plain line, said in words.
    quiet = charts.build_group_map(FREE, 1, UNIT, TEAMS)
    assert "No transfer on day 1" in texts(quiet) and not any(a.showarrow for a in quiet.layout.annotations)
    assert not any("Restricted" in text for text in texts(quiet))
    # Ring-fenced: the word is on the chart, with a bar across the subsidiary's line.
    fenced = charts.build_group_map(FENCED, FENCED.ring_fence_from, UNIT, TEAMS)
    assert any(text.startswith("<b>Restricted</b>: Full ring-fence") for text in texts(fenced))
    assert any(shape.type == "line" and shape.line.width == 7 for shape in fenced.layout.shapes)
    assert not any(shape.type == "line" and shape.line.width == 7 for shape in quiet.layout.shapes)
    # A diagram, not geography: no map trace of any kind.
    assert not any(trace.type.startswith(("scattergeo", "choropleth")) for trace in fenced.data)


def test_group_timeline_shows_both_entities_the_transfers_and_the_restriction():
    figure = charts.build_group_timeline(FENCED, UNIT, "Wednesday", True)
    assert theme.uses_template(figure)
    names = [trace.name for trace in figure.data]
    assert GROUP.parent.name in names and GROUP.subsidiary.name in names
    assert "Group, consolidated" in names
    bars = [trace for trace in figure.data if trace.type == "bar"]
    assert sum(sum(trace.y) for trace in bars) == pytest.approx(sum(t.amount for t in FENCED.transfers))
    assert any(f"From day {FENCED.ring_fence_from}: Full ring-fence" in text for text in texts(figure))
    assert not any("From day" in text for text in texts(charts.build_group_timeline(FREE, UNIT)))
    # Stage markers carry the stage's colour and a shape, and are named in the legend.
    assert "First day: likely to fail" in names and "First day: payments default" in names
    parent = next(t for t in figure.data if t.name == GROUP.parent.name)
    subsidiary = next(t for t in figure.data if t.name == GROUP.subsidiary.name)
    assert parent.line.color != subsidiary.line.color
    assert theme.distance(parent.line.color, subsidiary.line.color, "deuteranopia") >= 8.0


def test_group_comparison_has_both_paths_for_both_entities():
    figure = charts.build_group_comparison(FREE, run(Choice("restrict_transfers", "ring_fence", 1)),
                                           "No decision of the group", "Selected path", UNIT)
    lines = [trace for trace in figure.data if trace.mode == "lines"]
    assert len(lines) == 4 and {trace.name for trace in lines} == {"No decision of the group", "Selected path"}
    assert {trace.line.dash for trace in lines} == {"dash", "solid"}
    assert any(trace.text and "Payments default, day 5" in trace.text[0] for trace in figure.data
               if trace.mode == "markers+text")


# ------------------------------------------------- the pages


def group_app():
    at = AppTest.from_file(APP_PATH)
    at.session_state["demo_on"] = False
    at.session_state[group_view.KIND_KEY] = group_view.GROUP
    return at.run(timeout=60)


def test_design_page_shows_the_group_with_its_four_views():
    at = group_app()
    assert not at.exception and at.title[0].value == "Design"
    assert [tab.label for tab in at.tabs] == group_view.TABS
    # timeline, map, comparison (the showcase decision is loaded), one information map per country
    assert len(at.get("plotly_chart")) == 5
    show = CONFIG.college.showcase
    design = at.session_state[group_view.DESIGN_KEY]
    assert (design.group_key, design.scenario_key, design.severity) == (show.group, show.scenario, show.severity)
    assert design.choices.group == (Choice(show.decision, show.option, show.day),)
    assert any("an assumption of the tool" in c.value for c in at.caption)
    at.selectbox(key="grp_option_restrict_transfers").set_value("none").run(timeout=60)
    assert not at.exception
    assert at.session_state[group_view.DESIGN_KEY].choices == GroupChoices()
    assert len(at.get("plotly_chart")) == 4  # no second path, no comparison
    at.selectbox(key="grp_option_restrict_transfers").set_value("ring_fence").run(timeout=60)
    assert len(at.get("plotly_chart")) == 5  # the comparison of the two paths is back
    table = next(frame.value for frame in at.dataframe if "code" in frame.value.columns)
    assert list(table["code"]) == ["G1-a", "G1-b", "G1-c", "G2-a", "G2-b"]
    assert table.loc[table["code"] == "G1-c", "followed on this branch"].iloc[0] == "yes"
    at.selectbox(key="grp_group").set_value("large").run(timeout=60)
    at.radio(key="grp_stressed").set_value("subsidiary").run(timeout=60)
    assert not at.exception and at.session_state[group_view.DESIGN_KEY].stressed == "subsidiary"


def test_prepare_page_builds_the_groups_pack_and_keeps_the_design():
    at = group_app()
    at.selectbox(key="grp_option_group_support").set_value("early").run(timeout=60)
    at.switch_page("views/prepare.py").run(timeout=60)
    assert not at.exception and at.title[0].value == "Prepare"
    assert len(at.get("plotly_chart")) == 2  # one inject timeline per country
    assert not at.get("download_button")
    at.button(key="group_build").click().run(timeout=180)
    assert not at.exception and at.session_state["group_zip"][:2] == b"PK"
    at.switch_page("views/design.py").run(timeout=60)
    assert not at.exception
    assert at.selectbox(key="grp_option_group_support").value == "early"  # kept across pages


def test_guided_demo_stays_on_a_single_bank():
    at = AppTest.from_file(APP_PATH).run(timeout=60)
    assert at.session_state["demo_on"] and not at.exception
    assert at.radio(key=group_view.KIND_KEY).disabled
    # Even with the group chosen, the demo shows the single bank.
    at = AppTest.from_file(APP_PATH)
    at.session_state[group_view.KIND_KEY] = group_view.GROUP
    at.run(timeout=60)
    assert not at.exception and not group_view.DESIGN_KEY in at.session_state
    assert any("Guided demo" in m.value for m in at.markdown)


def test_single_bank_is_the_default_and_unchanged():
    at = AppTest.from_file(APP_PATH)
    at.session_state["demo_on"] = False
    at.run(timeout=60)
    assert not at.exception and at.radio(key=group_view.KIND_KEY).value == group_view.SINGLE
    assert at.selectbox(key="bank").value == CONFIG.script.showcase.bank


def test_assumptions_page_lists_the_group_and_college_parameters():
    table = group_table(CONFIG)
    assert (table["Status"] == FLAG).all() and len(table) >= 15
    assert {"Group A", "Group B", "College", "Restriction of intragroup transfers"} <= set(table["Where"])
    at = AppTest.from_file(APP_PATH)
    at.switch_page("views/assumptions.py").run(timeout=60)
    assert not at.exception
    assert "Cross-border groups, the college and system-wide stress" in [h.value for h in at.subheader]


# ------------------------------------------------- a decision panel per country


def test_design_has_a_decision_panel_for_each_country():
    at = group_app()
    headers = [h.value for h in at.sidebar.header]
    assert "Decisions in Country A" in headers and "Decisions in Country B" in headers
    design = at.session_state[group_view.DESIGN_KEY]
    assert design.choices.parent == () and design.choices.subsidiary == ()
    before = at.session_state[group_view.DESIGN_KEY]
    # The home authorities declare the parent failing early and bail it in.
    at.selectbox(key="grp_path_parent").set_value("early_foltf").run(timeout=60)
    assert not at.exception
    design = at.session_state[group_view.DESIGN_KEY]
    assert design.choices.parent == choices_of(CONFIG.decisions.paths["early_foltf"])
    assert design.choices.subsidiary == () and design.choices.group == before.choices.group
    # The comparison keeps each country's decisions and drops only those of the group.
    assert design.reference == GroupChoices(parent=design.choices.parent)
    # One decision changed by hand in the host country.
    at.selectbox(key="grp_dec_subsidiary_public_statement_option").set_value("reassuring").run(timeout=60)
    assert not at.exception
    design = at.session_state[group_view.DESIGN_KEY]
    assert [(c.decision, c.option) for c in design.choices.subsidiary] == [("public_statement", "reassuring")]


def test_a_countrys_decisions_change_the_groups_run_on_design():
    from engine.group import at_severity
    show = CONFIG.college.showcase
    scenarios = at_severity(CONFIG.scenarios, show.scenario, show.severity)
    ring = (Choice(show.decision, show.option, show.day),)
    run_with = lambda parent: simulate_group(  # noqa: E731
        CONFIG.groups[show.group], CONFIG.banks, CONFIG.model, scenarios, show.scenario,
        CONFIG.decisions, GroupChoices(parent=parent, group=ring), PARENT, CONFIG.college)
    late, early = run_with(()), run_with(choices_of(CONFIG.decisions.paths["early_foltf"]))
    assert late.parent.outcome == "payments_default" and early.parent.outcome == "bail_in"
