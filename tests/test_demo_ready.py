"""Demo readiness: the home page, the demo tour across the pages, the played exercise in one
click, the reset, and no resource fetched from outside the machine."""

import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app import group_view, tour, ui

ROOT = Path(__file__).resolve().parent.parent
APP_PATH = str(ROOT / "app" / "main.py")


@pytest.fixture
def at(monkeypatch):
    monkeypatch.setattr(ui, "LANDING", "home")
    return AppTest.from_file(APP_PATH).run(timeout=60)


def click(at, key: str):
    """Press a button, then let the page it asked for load."""
    at.button(key=key).click().run(timeout=120)
    return at


def test_the_app_opens_on_the_home_page(at):
    assert not at.exception and at.title[0].value == "Crisis Scenario Engine"
    headings = [h.value for h in at.subheader]
    assert headings == ["The four phases of an exercise", "Three kinds of exercise", "For a demonstration"]
    text = " ".join(m.value for m in at.markdown)
    for word in ("Design", "Prepare", "Deliver", "Close", tour.SINGLE, tour.GROUP, tour.SYSTEM):
        assert word in text, word
    assert any("fictitious" in c.value and "needs no connection" in c.value for c in at.caption)


@pytest.mark.parametrize("key, kind, title", [
    ("home_group", tour.GROUP, "Group A"), ("home_system", tour.SYSTEM, "System A")])
def test_a_kind_of_exercise_opens_on_its_showcase(at, key, kind, title):
    click(at, key)
    assert not at.exception and at.title[0].value == "Design"
    assert at.session_state[group_view.KIND_KEY] == kind and at.session_state["demo_on"] is False
    assert any(title in c.value for c in at.caption)


def test_single_bank_opens_on_design_without_the_guided_demo(at):
    click(at, "home_single")
    assert not at.exception and at.title[0].value == "Design"
    assert at.session_state["demo_on"] is False and len(at.tabs) >= 6


def test_the_played_exercise_loads_in_one_click_and_has_its_closing_report(at):
    click(at, "home_played")
    assert not at.exception and not at.error
    live = at.session_state["live"]
    assert live.ended and len(live.decisions) == 5 and at.session_state["live_id"] == tour.PLAYED_ID
    assert sum(d.deviates for d in live.decisions) == 2
    at.switch_page("views/close.py").run(timeout=120)
    assert not at.exception and at.session_state["live"].ended
    assert any("counterfactual" in m.value.lower() or "one decision" in m.value.lower()
               for m in [*at.markdown, *at.subheader])


def test_the_tour_visits_every_page_and_all_three_kinds(at):
    click(at, "home_tour")
    assert at.session_state[tour.ON] and at.session_state[tour.STOP] == 0
    seen, kinds = [], []
    for index, stop in enumerate(tour.STOPS):
        assert not at.exception, stop.title
        assert any(f"Demo tour · {index + 1} of {len(tour.STOPS)}: {stop.title}" in m.value
                   for m in at.markdown), stop.title
        seen.append(stop.page)
        kinds.append(at.session_state.get(group_view.KIND_KEY))
        if stop.played:
            assert at.session_state["live_id"] == tour.PLAYED_ID and "group_live" not in at.session_state
        if stop.group_live:
            exercise = at.session_state["group_live"]
            assert "live" not in at.session_state and not exercise.ended
            assert any("Decision of the group" in c.value for c in at.caption)
            assert any("Restriction of intragroup transfers" in m.value for m in at.markdown)
        if index < len(tour.STOPS) - 1:
            click(at, "tour_next")
    assert set(seen) == {tour.HOME, tour.DESIGN, tour.PREPARE, tour.DELIVER, tour.CLOSE, tour.ASSUMPTIONS}
    assert {tour.SINGLE, tour.GROUP, tour.SYSTEM} <= set(kinds)
    assert at.button(key="tour_next").disabled
    click(at, "tour_back")
    assert at.session_state[tour.STOP] == len(tour.STOPS) - 2
    click(at, "tour_end")
    assert not at.session_state[tour.ON] and not [b for b in at.button if b.key == "tour_next"]


def test_the_guided_demo_of_the_design_page_runs_inside_the_tour(at):
    click(at, "home_tour")
    click(at, "tour_next")
    assert at.title[0].value == "Design" and at.session_state["demo_on"] is True
    assert any("Guided demo · step 1 of 6" in m.value for m in at.markdown)


def test_reset_forgets_the_session_and_returns_home(at):
    click(at, "home_played")
    click(at, "tour_next") if at.session_state.get(tour.ON) else None
    at.switch_page("views/home.py").run(timeout=60)
    click(at, "home_reset")
    assert not at.exception and at.title[0].value == "Crisis Scenario Engine"
    for key in ("live", "live_id", tour.ON, group_view.KIND_KEY, "control_unlocked"):
        assert key not in at.session_state, key


def test_nothing_is_fetched_from_outside_the_machine():
    """No page, style or chart of the app names an outside address: the demo runs offline."""
    pattern = re.compile(r"https?://|@import|googleapis|cdn\.")
    sources = [*(ROOT / "app").rglob("*.py"), *(ROOT / "app").rglob("*.css"),
               ROOT / ".streamlit" / "config.toml"]
    for path in sources:
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            assert not pattern.search(line), f"{path.name}:{number}: {line.strip()[:80]}"
    assert "gatherUsageStats = false" in (ROOT / ".streamlit" / "config.toml").read_text()


def test_the_tour_has_a_stop_on_the_live_group_console_and_back_restores_the_played_exercise(at):
    index = next(i for i, stop in enumerate(tour.STOPS) if stop.group_live)
    assert tour.STOPS[index].page == tour.DELIVER and tour.STOPS[index].kind == tour.GROUP
    click(at, "home_tour")
    for _ in range(index):
        click(at, "tour_next")
    assert not at.exception and at.session_state["group_live_id"] == tour.GROUP_LIVE_ID
    assert len(at.session_state["group_live_codes"]) == 9  # eight teams and the control team
    played = next(i for i, stop in enumerate(tour.STOPS) if stop.played)
    for _ in range(index - played):
        click(at, "tour_back")
    assert at.session_state["live_id"] == tour.PLAYED_ID and "group_live" not in at.session_state
