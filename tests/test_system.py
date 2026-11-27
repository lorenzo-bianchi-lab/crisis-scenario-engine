"""System-wide variant (SPEC_V3 phase F): two unrelated banks of one country at once, where
an action on the first bank changes the second bank's path."""

import io

import pytest
from docx import Document
from streamlit.testing.v1 import AppTest

from app import group_console, group_view, store
from engine import group_live as gl
from engine.legal import table as legal_table
from engine import live, system
from engine.config import load_config
from engine.contagion import similarity_factor
from engine.decisions import choices_of
from engine.group import PARENT, SUBSIDIARY, GroupChoices, simulate_group
from engine.group_export import group_files, group_report_files
from engine.group_script import build_group_pack
from engine.schema import GROUP_ENTITIES, PLAYERS
from pathlib import Path

CONFIG = load_config()
SHOW = CONFIG.systems.showcase
PATHS = CONFIG.decisions.paths
APP_PATH = str(Path(__file__).resolve().parent.parent / "app" / "main.py")
GROUP_WORDS = ("parent", "subsidiary", "college", "intragroup", "ring-fenc", "home authorit",
               "host authorit", "across the border")


def run(key: str, path: str, scenario: str = "fast_run"):
    group, college = system.pair(CONFIG, key)
    return simulate_group(group, CONFIG.banks, CONFIG.model, CONFIG.scenarios, scenario,
                          CONFIG.decisions, GroupChoices(parent=choices_of(PATHS[path])), PARENT, college)


def docx_text(content: bytes) -> str:
    document = Document(io.BytesIO(content))
    return "\n".join([p.text for p in document.paragraphs]
                     + [c.text for t in document.tables for r in t.rows for c in r.cells])


# ------------------------------------------------- the pair as a group


@pytest.mark.parametrize("key", list(CONFIG.systems.pairs))
def test_a_pair_is_a_group_with_no_link_but_confidence(key):
    pair = CONFIG.systems.pairs[key]
    group, college = system.pair(CONFIG, key)
    assert group.kind == "system" and group.parent.country == group.subsidiary.country
    assert group.transfer_limit_share.value == 0 and group.ring_fence_shock.value == 0
    assert group.confidence_link.value == similarity_factor(
        pair.first.bank == pair.second.bank, True, CONFIG.model)
    assert group.confidence_link.is_assumption
    assert college.decisions == {}


def test_the_two_shipped_pairs_have_the_two_strengths_of_link():
    links = {key: system.pair(CONFIG, key)[0].confidence_link.value for key in CONFIG.systems.pairs}
    assert links == {"online": 1.0, "mixed": 0.5}


@pytest.mark.parametrize("key", list(CONFIG.systems.pairs))
@pytest.mark.parametrize("path", ["act_late", "early_foltf"])
def test_no_transfer_and_the_sanity_checks_hold_for_both_banks(key, path):
    result = run(key, path)
    assert not result.transfers and result.ring_fence_from is None and not result.decisions
    for role in GROUP_ENTITIES:
        for row in result.of(role).rows:
            assert row["intragroup_claim"] == 0 and row["intragroup_liability"] == 0
            assert abs(row["total_assets"] - row["total_liabilities"] - row["cet1"]) < 1e-6
            assert min(row["cash"], row["deposits_total"], row["liquidity_buffer"]) >= -1e-9


# ------------------------------------------------- acceptance


def test_an_action_on_the_first_bank_changes_the_second_banks_path():
    late, early = run(SHOW.pair, SHOW.compare_path, SHOW.scenario), run(SHOW.pair, SHOW.path, SHOW.scenario)
    assert late.parent.outcome == "payments_default" and early.parent.outcome == "bail_in"
    stages = lambda result: dict(dict(result.stage_days)[SUBSIDIARY])  # noqa: E731
    # Left to default: the second bank is flagged on day 12. Resolved early: ten days later.
    assert stages(late)["likely_to_fail"] == 12 and stages(early)["likely_to_fail"] == 22
    assert late.subsidiary.scorecard.deposits_lost_pct > early.subsidiary.scorecard.deposits_lost_pct + 2
    # The second bank's own decisions and scenario are the same on both runs.
    assert late.subsidiary.rows[1]["deposits_total"] == early.subsidiary.rows[1]["deposits_total"]


def test_the_mixed_pair_shows_it_too_at_half_the_link():
    late, early = run("mixed", "act_late"), run("mixed", "early_foltf")
    stages = lambda result: dict(dict(result.stage_days)[SUBSIDIARY])  # noqa: E731
    assert stages(late)["early_intervention"] < stages(early)["early_intervention"]


def with_resolution_shock(value: float):
    shock = CONFIG.model.contagion_shock_resolution.model_copy(update={"value": value})
    return CONFIG.model.model_copy(update={"contagion_shock_resolution": shock})


def run_with(params, path: str, key: str = SHOW.pair):
    group, college = system.pair(CONFIG, key)
    return simulate_group(group, CONFIG.banks, params, CONFIG.scenarios, SHOW.scenario, CONFIG.decisions,
                          GroupChoices(parent=choices_of(PATHS[path])), PARENT, college)


def test_an_orderly_resolution_sends_a_smaller_shock_than_a_default():
    shock = CONFIG.model.contagion_shock_resolution
    assert shock.is_assumption and 0 < shock.value < CONFIG.model.contagion_shock_failure.value
    early = run_with(CONFIG.model, SHOW.path)
    # The day after the first bank's run ends in resolution, the second reads it as news.
    day = early.parent.end_day + 1
    events = [e for e in early.subsidiary.events if e.day == day]
    assert any("put in resolution" in str(e) for e in events), events
    silent = run_with(with_resolution_shock(0.0), SHOW.path)
    assert silent.subsidiary.scorecard.deposits_lost_pct < early.subsidiary.scorecard.deposits_lost_pct
    assert "likely_to_fail" not in dict(dict(silent.stage_days)[SUBSIDIARY])
    # The shock reaches the second bank only: the first bank's run is the same.
    assert silent.parent.rows == early.parent.rows


def test_a_larger_resolution_shock_never_helps_the_second_bank():
    losses = [run_with(with_resolution_shock(value), SHOW.path).subsidiary.scorecard.deposits_lost_pct
              for value in (0.0, 0.03, 0.06, 0.12)]
    assert losses == sorted(losses)
    # Without a resolution (the first bank defaults) the parameter changes nothing.
    assert run_with(with_resolution_shock(0.0), SHOW.compare_path).subsidiary.rows == \
        run_with(CONFIG.model, SHOW.compare_path).subsidiary.rows


def test_a_cross_border_group_does_not_use_the_resolution_shock():
    from engine.group import at_severity
    show = CONFIG.college.showcase
    scenarios = at_severity(CONFIG.scenarios, show.scenario, show.severity)
    choices = GroupChoices(parent=choices_of(PATHS["early_foltf"]))
    runs = [simulate_group(CONFIG.groups[show.group], CONFIG.banks, params, scenarios, show.scenario,
                           CONFIG.decisions, choices, PARENT, CONFIG.college)
            for params in (CONFIG.model, with_resolution_shock(0.0))]
    assert runs[0].parent.outcome == "bail_in" and runs[0].subsidiary.rows == runs[1].subsidiary.rows


def test_without_a_link_the_second_bank_ignores_the_first():
    group, college = system.pair(CONFIG, SHOW.pair)
    alone = group.model_copy(update={"confidence_link": group.confidence_link.model_copy(update={"value": 0.0})})
    runs = [simulate_group(alone, CONFIG.banks, CONFIG.model, CONFIG.scenarios, SHOW.scenario,
                           CONFIG.decisions, GroupChoices(parent=choices_of(PATHS[path])), PARENT, college)
            for path in (SHOW.compare_path, SHOW.path)]
    assert runs[0].subsidiary.rows == runs[1].subsidiary.rows


# ------------------------------------------------- script and documents


@pytest.fixture(scope="module")
def pack():
    group, college = system.pair(CONFIG, SHOW.pair)
    paths = [(PATHS[SHOW.compare_path].name, GroupChoices(parent=choices_of(PATHS[SHOW.compare_path]))),
             ("Selected path", GroupChoices(parent=choices_of(PATHS[SHOW.path])))]
    return build_group_pack(group, CONFIG.banks, CONFIG.model, CONFIG.scenarios, SHOW.scenario,
                            CONFIG.decisions, CONFIG.script, college, paths), college


def test_the_same_four_teams_play_on_both_banks_and_nothing_is_notified_between_them(pack):
    built, _ = pack
    assert [built.scripts[PARENT].actors[a] for a in PLAYERS] == [built.scripts[SUBSIDIARY].actors[a] for a in PLAYERS]
    assert "Country" not in " ".join(built.scripts[PARENT].actors[a] for a in PLAYERS)
    for role in GROUP_ENTITIES:
        for branch in built.packs[role].branches:
            assert not [i for actor in PLAYERS for i in branch.injects[actor] if i.kind == "cross_border"]
    assert built.decision_points == (("A", ()), ("B", ()))


def test_pack_files_are_worded_for_a_system(pack):
    built, college = pack
    files = group_files(built, CONFIG.decisions, college)
    assert {name.split("/")[0] for name in files} == {"bank_1_online_bank_x", "bank_2_online_bank_y", "system"}
    control = docx_text(files["system/system_control_pack.docx"])
    for wanted in ("System control pack", "SIMULATION", "an assumption of the tool", "Online bank X",
                   "Online bank Y", "lend nothing to each other", "likely to fail day 12", "likely to fail day 22"):
        assert wanted in control, wanted
    # The page of legal references lists the whole table of config/legal.yaml, colleges
    # included: the system's own wording is checked without it.
    own = "\n".join(line for line in control.split("\n")
                    if line not in {cell for row in legal_table(CONFIG.script.legal) for cell in row})
    assert "Legal references" in control
    for word in GROUP_WORDS:
        assert word not in own.lower(), word
    data = files["system/data/SIMULATION_system_both_banks.csv"].decode()
    assert "Online bank Y" in data and "likely to fail" in data


# ------------------------------------------------- live exercise


SETUP = gl.GroupLiveSetup("System exercise", SHOW.pair, SHOW.scenario)


def play(state, steps: int):
    for number in range(steps):
        state = gl.apply(state, "step", f"2026-01-01T09:{number:02d}:30", CONFIG)
    return state


@pytest.fixture(scope="module")
def exercise():
    """Played until the first bank's FoLtF decision is open, then declared early."""
    state = gl.start(SETUP, "2026-01-01T09:00:00", CONFIG)
    for number in range(60):
        opened = {(side, i.decision_due.decision) for side, i in gl.open_decisions(state, CONFIG)}
        if (PARENT, "declare_foltf") in opened:
            break
        state = gl.apply(state, "step", f"2026-01-01T09:{number:02d}:30", CONFIG)
    return state


@pytest.fixture(scope="module")
def resolved(exercise):
    """The teams take the decisions of the showcase path on the first bank, as they open."""
    state = exercise
    for number, choice in enumerate(PATHS[SHOW.path].choices):
        assert (PARENT, choice.decision) in {
            (side, i.decision_due.decision) for side, i in gl.open_decisions(state, CONFIG)}
        state = gl.apply(state, "decision", f"2026-01-01T10:3{number}:00", CONFIG, team=PARENT,
                         decision=choice.decision, option=choice.option)
    return state


def test_a_decision_on_the_first_bank_changes_the_second_banks_path_live(exercise, resolved):
    before, after = exercise.result, resolved
    assert before.parent.outcome == "payments_default" and after.result.parent.outcome == "bail_in"
    stages = lambda result: dict(dict(result.stage_days)[SUBSIDIARY])  # noqa: E731
    assert stages(before)["likely_to_fail"] < stages(after.result)["likely_to_fail"]
    assert after.result.subsidiary.rows != before.subsidiary.rows
    # It equals the direct simulation with the same decisions.
    session_choices = GroupChoices(
        parent=live.effective_choices(after.of(PARENT), gl.contexts_of(SETUP, CONFIG)[PARENT]),
        subsidiary=())
    group, college = system.pair(CONFIG, SHOW.pair)
    direct = simulate_group(group, CONFIG.banks, CONFIG.model, CONFIG.scenarios, SHOW.scenario,
                            CONFIG.decisions, session_choices, PARENT, college)
    assert direct.subsidiary.rows == after.result.subsidiary.rows


def test_four_team_pages_each_with_its_own_injects_on_both_banks(exercise):
    views = gl.player_views(exercise, CONFIG)
    assert set(views) == set(system.team_keys()) == {f"both.{actor}" for actor in PLAYERS}
    for actor in PLAYERS:
        view = views[f"both.{actor}"]
        assert view["bank"] == "Online bank X and Online bank Y"
        own = [(i.inject.day, i.inject.time, f"{exercise.of(role).setup.bank.name}: {i.inject.title}")
               for role in GROUP_ENTITIES for i in live.released_to(exercise.of(role), actor)]
        assert sorted(own) == sorted((i["day"], i["time"], i["title"]) for i in view["injects"])
        assert [(i["day"], i["time"]) for i in view["injects"]] == sorted((i["day"], i["time"]) for i in view["injects"])
    titles = [i["title"] for i in views["both.competent_authority"]["injects"]]
    assert any(t.startswith("Online bank X: ") for t in titles) and any(t.startswith("Online bank Y: ") for t in titles)


def test_joint_decision_of_the_authorities_needs_two_teams(exercise):
    with pytest.raises(live.LiveError):
        gl.apply(exercise, "college", "2026-01-01T10:40:00", CONFIG, subject="Other",
                 took_part=["both.resolution_authority"], agreed=True)
    after = gl.apply(exercise, "college", "2026-01-01T10:40:00", CONFIG,
                     subject=CONFIG.systems.joint_decision.subjects[0],
                     took_part=["both.resolution_authority", "both.competent_authority"], agreed=False,
                     note="No agreement on which bank first")
    row = gl.group_report(after, CONFIG).college[0]
    assert row[2] == "Not agreed" and row[3] == "Resolution authority, Competent authority"
    assert "Central bank" in row[4]


def test_closing_report_gives_both_banks_with_one_decision_changed(resolved):
    state = resolved
    report = gl.group_report(state, CONFIG)
    assert report.kind == "system" and not report.decisions and not report.transfers
    assert report.counterfactuals
    assert any(case.subsidiary != gl.path_text(state.result.subsidiary) for case in report.counterfactuals)
    contexts = gl.contexts_of(SETUP, CONFIG)
    group, college = system.pair(CONFIG, SHOW.pair)
    files = group_report_files(report, {r: contexts[r].script for r in GROUP_ENTITIES}, group, college)
    assert set(files) == {
        "system/SIMULATION_system_closing_report_draft.docx",
        "bank_1_online_bank_x/SIMULATION_closing_report_draft.docx",
        "bank_2_online_bank_y/SIMULATION_closing_report_draft.docx",
        "system/SIMULATION_system_report_joint_decisions.csv",
        "system/SIMULATION_system_report_one_decision_changed.csv"}
    text = docx_text(files["system/SIMULATION_system_closing_report_draft.docx"])
    for wanted in ("System closing report (draft)", "SIMULATION", "with one decision changed",
                   "To be completed - not generated"):
        assert wanted in text, wanted
    for name, content in files.items():
        if name.endswith(".docx"):
            for word in GROUP_WORDS:
                assert word not in docx_text(content).lower(), (name, word)


def test_saved_exercise_reloads_and_serves_the_four_pages(exercise):
    codes = store.new_group_codes("system")
    assert set(codes) == {*system.team_keys(), store.CONTROL}
    store.save_group("5ca1ab1e", exercise, CONFIG, codes)
    loaded = store.load_group("5ca1ab1e", codes[store.CONTROL])
    assert loaded is not None and loaded[0].result == exercise.result
    team = "both.central_bank"
    assert store.player_view("5ca1ab1e", team, codes[team])["player"] == "Central bank"
    assert store.player_view("5ca1ab1e", team, codes["both.competent_authority"]) is None
    assert store.player_view("5ca1ab1e", "parent.central_bank", codes[team]) is None


# ------------------------------------------------- pages


def test_design_and_prepare_pages_show_the_system():
    at = AppTest.from_file(APP_PATH)
    at.session_state["demo_on"] = False
    at.session_state[group_view.KIND_KEY] = group_view.SYSTEM
    at.run(timeout=60)
    assert not at.exception and at.title[0].value == "Design"
    assert [tab.label for tab in at.tabs] == ["System timeline", "Map", "Paths compared", "Information map"]
    assert len(at.get("plotly_chart")) == 5
    table = next(frame.value for frame in at.dataframe if "Path in one line" in frame.value.columns)
    second = table[table["Bank"] == "Online bank Y"]["Path in one line"].tolist()
    assert len(second) == 2 and "likely to fail from day 12" in second[0] and "likely to fail from day 22" in second[1]
    at.selectbox(key="sys_compare").set_value(SHOW.path).run(timeout=60)
    assert not at.exception and len(at.get("plotly_chart")) == 4  # one path: no comparison
    at.switch_page("views/prepare.py").run(timeout=60)
    assert not at.exception and any("One folder per bank" in c.value for c in at.caption)


def test_console_and_close_page_run_a_system_exercise():
    at = AppTest.from_file(APP_PATH)
    at.session_state["control_unlocked"] = True
    at.switch_page("views/console.py").run(timeout=60)
    assert "Start an exercise on a system: two banks at once" in [h.value for h in at.subheader]
    at.button(key="slive_start").click().run(timeout=60)
    assert not at.exception and not at.error
    state = at.session_state[group_console.KEY]
    assert gl.kind_of(state.setup, CONFIG) == "system"
    assert set(at.session_state["group_live_codes"]) == {*system.team_keys(), store.CONTROL}
    for _ in range(8):
        at.button(key="glive_step").click().run(timeout=60)
    at.button(key="glive_college_record").click().run(timeout=60)
    assert not at.exception and not at.error
    assert len(at.session_state[group_console.KEY].college[0].took_part) == 4
    assert any("player=both.central_bank&code=" in m.value for m in at.markdown)
    at.switch_page("views/close.py").run(timeout=120)
    assert not at.exception
    headings = [m.value for m in at.markdown if m.value.startswith("###")]
    assert "### Joint decisions of the authorities" in headings
    assert "### What the model gives for both banks with one decision changed" in headings
    assert not any("group" in h.lower() or "border" in h.lower() for h in headings)
