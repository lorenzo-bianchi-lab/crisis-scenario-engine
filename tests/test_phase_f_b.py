"""SPEC_V2 and SPEC_V3 phase F, part b: the group's decisions, the college as teams, the
script and the documents of both countries."""

import io
import re

import pytest
from docx import Document

from engine.config import load_config
from engine.decisions import Choice
from engine.group import PARENT, SUBSIDIARY, GroupChoices, main, other, simulate_group
from engine.group_export import folder, group_events_frame, group_files
from engine.group_script import (
    CROSS_BORDER_RECIPIENTS,
    build_group_pack,
    group_code,
    open_day,
    team_name,
)
from engine.schema import ACTORS, DECISION_STAGES, GROUP_ENTITIES, PLAYERS

CONFIG = load_config()
GROUP, COLLEGE, DECISIONS, SCRIPT = CONFIG.groups["showcase"], CONFIG.college, CONFIG.decisions, CONFIG.script
FIGURE_TOKEN = re.compile(r"\d+\.\d+%?|\d+%")
RING_FENCE = GroupChoices(group=(Choice("restrict_transfers", "ring_fence", 1),))
PATHS = [("No decision of the group", GroupChoices()), ("Ring-fenced", RING_FENCE)]


def run(*group_choices, scenario="fast_run", group=GROUP, stressed=PARENT):
    return simulate_group(group, CONFIG.banks, CONFIG.model, CONFIG.scenarios, scenario, DECISIONS,
                          GroupChoices(group=tuple(group_choices)), stressed, COLLEGE)


def build(paths=PATHS, scenario="fast_run", group=GROUP, mode="dynamic"):
    return build_group_pack(group, CONFIG.banks, CONFIG.model, CONFIG.scenarios, scenario, DECISIONS,
                            SCRIPT, COLLEGE, paths, mode=mode)


@pytest.fixture(scope="module")
def pack():
    return build()


@pytest.fixture(scope="module")
def files(pack):
    return group_files(pack, DECISIONS, COLLEGE)


def docx_text(content: bytes) -> str:
    document = Document(io.BytesIO(content))
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


def team_injects(pack, role, actor):
    """(branch, inject) for every inject of one team, on every branch."""
    document = next(d for d in pack.packs[role].documents if d.actor == actor)
    out = []
    for index, branch in enumerate(pack.branches):
        own = dict(document.branches).get(f"Branch {branch.label}", ()) if document.branches else ()
        out += [(branch, inject) for inject in (*document.shared, *own)]
    return out


# ------------------------------------------------- the group's decisions change the run


def test_each_option_of_the_restriction_has_its_effect():
    free = run()
    assert free.decisions == () and free.ring_fence_from is None
    assert sum(t.amount for t in free.transfers) == pytest.approx(1.12, abs=0.01)
    assert free.parent.failure_day is None
    # "No restriction" is the same run.
    same = run(Choice("restrict_transfers", "none", 1))
    assert same.transfers == free.transfers and same.decisions == ()
    # Full ring-fence: nothing more is lent.
    fenced = run(Choice("restrict_transfers", "ring_fence", 1))
    record = fenced.decisions[0]
    assert (record.decision, record.option, record.entity, record.actor) == (
        "restrict_transfers", "ring_fence", SUBSIDIARY, "competent_authority")
    assert (record.decided_day, record.effect_day) == (1, 2) and fenced.ring_fence_from == 2
    assert fenced.transfers == () and fenced.parent.failure_day == 5
    # Cap: lending goes on up to the cap, on top of what was lent before.
    capped = run(Choice("restrict_transfers", "cap", 1))
    cap = COLLEGE.decisions["restrict_transfers"].options["cap"].params["cap_share"].value
    allowed = cap * CONFIG.banks[GROUP.subsidiary.bank].assets.total()
    assert 0 < sum(t.amount for t in capped.transfers) <= allowed + 1e-9
    assert sum(t.amount for t in capped.transfers) == pytest.approx(allowed)
    assert capped.parent.failure_day is not None
    # A cap set after some lending adds to what is already lent.
    late = run(Choice("restrict_transfers", "cap", 4))
    lent_before = sum(t.amount for t in late.transfers if t.day < late.ring_fence_from)
    assert lent_before > 0
    assert sum(t.amount for t in late.transfers) <= lent_before + allowed + 1e-9


def test_the_host_cannot_restrict_before_it_is_told_of_trouble_at_the_parent():
    """The decision is taken at the first close at which its condition holds: the host
    authority has been told that the parent breached a recovery indicator."""
    result = run(Choice("restrict_transfers", "ring_fence", 0))
    told = dict(dict(result.stage_days)[PARENT])["recovery"] + int(COLLEGE.cross_border_delay_days.value)
    assert result.decisions[0].scheduled_day == 0 and result.decisions[0].decided_day == told == 1
    assert open_day(result, COLLEGE, "restrict_transfers") == told
    # In a group whose parent stays calm, the decision never opens and nothing is restricted.
    calm = simulate_group(GROUP, CONFIG.banks, CONFIG.model, CONFIG.scenarios, "baseline", DECISIONS,
                          RING_FENCE, PARENT, COLLEGE)
    assert calm.decisions == () and calm.ring_fence_from is None
    assert open_day(calm, COLLEGE, "restrict_transfers") is None


def test_asking_for_group_support_early_moves_the_money_before_a_ring_fence():
    early = run(Choice("group_support", "early", 1))
    assert [(t.day, t.early, t.lender) for t in early.transfers] == [(2, True, SUBSIDIARY)]
    assert early.decisions[0].entity == PARENT and early.decisions[0].actor == "bank_management"
    assert early.parent.failure_day is None
    # It is within the lender's floor and limit like any support.
    lender = CONFIG.banks[GROUP.subsidiary.bank]
    floor = GROUP.lender_cash_floor_share.value * lender.liabilities.deposits.total()
    assert early.transfers[0].amount <= early.subsidiary.rows[1]["cash"] - floor + 1e-9
    # Asked for a day before the ring-fence takes effect, it gets through and saves the parent.
    both = run(Choice("group_support", "early", 1), Choice("restrict_transfers", "ring_fence", 2))
    assert [t.day for t in both.transfers] == [2] and both.ring_fence_from == 3
    assert both.parent.failure_day is None
    # Asked for the same day the ring-fence takes effect, it gets nothing.
    late = run(Choice("group_support", "early", 1), Choice("restrict_transfers", "ring_fence", 1))
    assert late.transfers == () and late.parent.failure_day == 5
    # "Only when payments cannot be funded otherwise" is the run without the decision.
    assert run(Choice("group_support", "last_moment", 1)).transfers == run().transfers


def test_group_decisions_are_checked():
    with pytest.raises(ValueError, match="unknown group decision"):
        run(Choice("restrict_transfers", "nonsense", 1))
    with pytest.raises(ValueError, match="once"):
        run(Choice("restrict_transfers", "cap", 1), Choice("restrict_transfers", "ring_fence", 2))
    with pytest.raises(ValueError, match="unknown group decision"):
        simulate_group(GROUP, CONFIG.banks, CONFIG.model, CONFIG.scenarios, "fast_run", DECISIONS,
                       RING_FENCE)  # no college given
    for definition in COLLEGE.decisions.values():
        assert definition.options[definition.default_option].effect == "none"
        for option in definition.options.values():
            assert all(item.source == "ASSUMPTION" for item in option.params.values())
    assert COLLEGE.cross_border_delay_days.source == COLLEGE.consolidated_delay_days.source == "ASSUMPTION"


# ------------------------------------------------- teams and what crosses the border


def test_each_country_has_its_four_teams_named_with_their_country(pack):
    for role in GROUP_ENTITIES:
        country = GROUP.entity(role).country
        documents = pack.packs[role].documents
        assert [d.actor for d in documents] == list(ACTORS)
        for document in documents:
            if document.actor in PLAYERS:
                assert document.name == team_name(COLLEGE, SCRIPT, GROUP, role, document.actor)
                assert document.name.endswith(f"of {country}")
            else:
                assert document.name == f"Management of {GROUP.entity(role).name}"
        assert pack.packs[role].bank_name == GROUP.entity(role).name


@pytest.mark.parametrize("role", GROUP_ENTITIES)
def test_a_stage_change_reaches_the_other_countrys_authorities_with_its_figures(pack, role):
    source = other(role)
    delay = int(COLLEGE.cross_border_delay_days.value)
    sender = pack.scripts[source].actors["competent_authority"]
    for actor in ACTORS:
        received = [(b, i) for b, i in team_injects(pack, role, actor) if i.kind == "cross_border"]
        if actor not in CROSS_BORDER_RECIPIENTS:
            assert not received
            continue
        for branch, inject in received:
            events = [e for e in branch.result.of(source).events
                      if e.day + delay == inject.day and e.kind in (*DECISION_STAGES, "foltf", "resolution")]
            assert events and inject.sender == sender and inject.time == COLLEGE.cross_border_clock
            assert GROUP.entity(source).name in inject.text and GROUP.entity(source).country in inject.text
            triggered = {name: value for e in events if e.kind in DECISION_STAGES
                         for name, value, _ in e.figures}
            assert {f.name: f.value for f in inject.headline} == pytest.approx(triggered)
            assert inject.figures == ()  # no regular report of the other entity comes with it
            assert inject.fallback and inject.purpose == "control"  # dynamic: an expected response
        for branch in pack.branches:
            days = {e.day + delay for e in branch.result.of(source).events if e.kind in DECISION_STAGES}
            got = {i.day for b, i in received if b is branch and i.headline}
            assert got == days


def test_the_restriction_is_open_to_the_host_after_it_is_told_and_then_told_to_the_others(pack):
    host = team_injects(pack, SUBSIDIARY, "competent_authority")
    due = [i for _, i in host if i.decision_due and i.decision_due.decision == "restrict_transfers"]
    assert due and all(i.day == 1 for i in due)
    notice = next(i for _, i in host if i.kind == "cross_border")
    assert (notice.day, notice.time) < (due[0].day, due[0].time)  # told first, then asked
    decision = due[0].decision_due
    assert [o.branch_code for o in decision.options] == ["G1-a", "G1-b", "G1-c"]
    assert decision.options[0].is_default
    assert decision.consult[0] == team_name(COLLEGE, SCRIPT, GROUP, PARENT, "competent_authority")
    assert group_code(COLLEGE, "group_support", "early") == "G2-b"
    # No other team is asked.
    for role in GROUP_ENTITIES:
        for actor in PLAYERS:
            if (role, actor) != (SUBSIDIARY, "competent_authority"):
                assert not any(i.decision_due and i.decision_due.decision == "restrict_transfers"
                               for _, i in team_injects(pack, role, actor))
    # On the ring-fenced branch the teams the script names are told, the day it takes effect.
    fenced = pack.branches[1]
    record = fenced.result.decisions[0]
    for role, actor in ((PARENT, "competent_authority"), (PARENT, "resolution_authority"),
                        (SUBSIDIARY, "resolution_authority")):
        told = [i for b, i in team_injects(pack, role, actor)
                if b is fenced and i.kind == "decision_taken" and "Restriction" in i.text]
        assert [i.day for i in told] == [record.effect_day], (role, actor)
        assert "Full ring-fence" in told[0].text
    assert not any(i.kind == "decision_taken" and "Restriction" in i.text
                   for b, i in team_injects(pack, PARENT, "competent_authority") if b is pack.branches[0])


def test_group_management_is_asked_about_support_and_is_played_by_the_control_team(pack):
    asked = [i for _, i in team_injects(pack, PARENT, "bank_management")
             if i.decision_due and i.decision_due.decision == "group_support"]
    assert asked and asked[0].recipient == "bank_management"
    assert "bank_management" not in PLAYERS


# ------------------------------------------------- information sets, for every team of both countries


def check_team(pack, role, actor) -> int:
    """Every figure in a team's injects is one that team may hold, of the day it may hold it.
    Returns the number of figures checked."""
    info = DECISIONS.information[actor]
    late = int(COLLEGE.consolidated_delay_days.value)
    delay = int(COLLEGE.cross_border_delay_days.value)
    checked = 0
    for branch, inject in team_injects(pack, role, actor):
        own, theirs = branch.result.of(role), branch.result.of(other(role))
        for figure in inject.figures:
            if figure.name in ("intragroup_claim", "intragroup_liability"):
                # Its own entity's position towards the group: competent authority and bank only.
                assert actor in ("competent_authority", "bank_management")
                assert figure.value == own.rows[figure.as_of_day][figure.name]
            elif figure.name.startswith("group_"):
                # Consolidated figures: home authorities only, and older than their own.
                assert role == PARENT and actor in CROSS_BORDER_RECIPIENTS
                assert figure.as_of_day <= max(0, inject.day - late)
                assert figure.value == branch.result.consolidated[figure.as_of_day][figure.name[6:]]
            else:
                assert figure.name in info.figures, (role, actor, figure.name)
                assert figure.value == pytest.approx(float(own.rows[figure.as_of_day][figure.name]))
                assert figure.as_of_day <= inject.day
            checked += 1
        if inject.kind == "cross_border":
            # The only figures of the other entity: those behind a stage change, passed on.
            assert actor in CROSS_BORDER_RECIPIENTS
            behind = {name: value for e in theirs.events
                      if e.day + delay == inject.day and e.kind in DECISION_STAGES
                      for name, value, _ in e.figures}
            assert {f.name: f.value for f in inject.headline} == pytest.approx(behind)
            checked += len(inject.headline)
        else:
            # Everything else it is told comes from its own entity's run.
            for figure in (*inject.headline, *inject.notified):
                if figure.as_of_day == inject.day and inject.headline and figure in inject.headline:
                    events = [e for e in own.events if e.day == inject.day and e.kind in DECISION_STAGES]
                    names = {name for e in events for name, _, _ in e.figures}
                    assert figure.name in names, (role, actor, inject.title, figure.name)
    return checked


@pytest.mark.parametrize("role", GROUP_ENTITIES)
@pytest.mark.parametrize("actor", PLAYERS)
def test_no_team_is_given_a_figure_outside_its_information_set(pack, role, actor):
    checked = check_team(pack, role, actor)
    # An authority informed only from a late stage may get no figure at all on these paths.
    assert checked > 0 or DECISIONS.information[actor].informed_from is not None


@pytest.mark.parametrize("scenario", ["geopolitical", "loss_revealed"])
def test_information_sets_hold_in_other_scenarios_and_the_other_group(scenario):
    other_pack = build(scenario=scenario, group=CONFIG.groups["large"])
    for role in GROUP_ENTITIES:
        for actor in PLAYERS:
            check_team(other_pack, role, actor)


@pytest.mark.parametrize("role", GROUP_ENTITIES)
@pytest.mark.parametrize("actor", PLAYERS)
def test_a_teams_document_prints_only_figures_from_its_own_injects(pack, files, role, actor):
    """A reader with only this team's inject sheets cannot see a figure the team was not
    given: every number printed is in one of its injects."""
    text = docx_text(files[f"{folder(pack, role)}/control_team/inject_sheets/{actor}.docx"])
    allowed = set()
    for _, inject in team_injects(pack, role, actor):
        for figure in (*inject.figures, *inject.headline, *inject.notified, *inject.resolution):
            allowed.update(FIGURE_TOKEN.findall(figure.text))
            allowed.update(FIGURE_TOKEN.findall(figure.threshold_text))
        allowed.update(FIGURE_TOKEN.findall(inject.text))
    printed = set(FIGURE_TOKEN.findall(text))
    assert printed <= allowed, printed - allowed
    # An authority informed only from a late stage may be given no figure on these paths.
    assert printed or DECISIONS.information[actor].informed_from is not None


def test_a_host_teams_document_never_shows_a_parent_figure_it_was_not_given(pack, files):
    """Acceptance: the host authorities do not see the parent's reports. Take the parent's
    liquidity buffer and deposits day by day: none of them is printed in a host document,
    unless the same number is one of the host's own."""
    for actor in PLAYERS:
        text = docx_text(files[f"{folder(pack, SUBSIDIARY)}/control_team/inject_sheets/{actor}.docx"])
        own = set()
        for _, inject in team_injects(pack, SUBSIDIARY, actor):
            for figure in (*inject.figures, *inject.headline, *inject.notified, *inject.resolution):
                own.update(FIGURE_TOKEN.findall(figure.text))
        for branch in pack.branches:
            for row in branch.result.parent.rows[1:]:
                for name in ("liquidity_buffer", "deposits_total", "cash"):
                    token = f"{row[name]:.2f}"
                    assert token in own or f"{token} " not in text, (actor, name, row["day"])
    # And the home competent authority sees the consolidated figures, the host does not.
    home = docx_text(files[f"{folder(pack, PARENT)}/control_team/inject_sheets/competent_authority.docx"])
    host = docx_text(files[f"{folder(pack, SUBSIDIARY)}/control_team/inject_sheets/competent_authority.docx"])
    assert "Group liquidity buffer, consolidated" in home and "consolidated" not in host
    assert "Lent to the rest of the group" in host and "Lent to the rest of the group" in home


# ------------------------------------------------- documents


def test_export_has_a_pack_per_country_and_the_college_folder(pack, files):
    folders = {name.split("/")[0] for name in files}
    assert folders == {"country_a_parent", "country_b_subsidiary", "college"}
    for role in GROUP_ENTITIES:
        for actor in PLAYERS:
            assert f"{folder(pack, role)}/control_team/inject_sheets/{actor}.docx" in files
        assert f"{folder(pack, role)}/players/mock_bank_dossier.docx" in files
    text = docx_text(files["college/group_control_pack.docx"])
    for wanted in ("Group control pack", "an assumption of the tool", "Competent authority of Country B",
                   "Restriction of intragroup transfers", "Full ring-fence, no further lending",
                   "Intragroup transfers", "Branch A: No decision of the group", "Branch B: Ring-fenced",
                   "Payments default (disorderly), day 5", "meet as a college"):
        assert wanted in text, wanted
    assert "SIMULATION" in text
    for name, content in files.items():
        if name.endswith(".csv"):
            assert "SIMULATION" in name.rsplit("/", 1)[-1], name


def test_main_events_list_of_the_group_has_a_country_column(pack):
    frame = group_events_frame(pack)
    assert set(frame["Country"]) == {GROUP.parent.country, GROUP.subsidiary.country}
    assert len(frame) == sum(len(pack.packs[role].events) for role in GROUP_ENTITIES)
    assert (frame["Title"] if "Title" in frame else frame.iloc[:, 5]).astype(str).str.len().min() > 0


def test_decision_points_show_where_each_option_of_a_group_decision_leads(pack):
    label, points = pack.decision_points[0]
    by_code = {p.code: p for p in points}
    assert label == "A" and set(by_code) == {"G1-a", "G1-b", "G1-c", "G2-a", "G2-b"}
    assert by_code["G1-a"].taken and by_code["G1-a"].parent_outcome == "under_stress"
    assert by_code["G1-c"].parent_outcome == "payments_default" and by_code["G1-c"].lent == 0
    assert by_code["G2-b"].parent_outcome == "under_stress" and by_code["G2-b"].lent > by_code["G1-a"].lent
    fenced = dict(pack.decision_points)["B"]
    assert next(p for p in fenced if p.code == "G1-c").taken


def test_linear_exercise_has_one_path_and_no_fallback_across_the_border():
    linear = build(mode="linear")
    assert len(linear.branches) == 1 and linear.branches[0].name == "Ring-fenced"
    crossing = [i for role in GROUP_ENTITIES for actor in CROSS_BORDER_RECIPIENTS
                for _, i in team_injects(linear, role, actor) if i.kind in ("cross_border", "decision_taken")]
    assert crossing and not any(i.fallback for i in crossing)


def test_cli_writes_the_group_pack(tmp_path, capsys):
    assert main(["--scenario", "fast_run", "--decide", "restrict_transfers", "ring_fence", "1",
                 "--export-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Restriction of intragroup transfers: Full ring-fence" in out and "Group pack written" in out
    root = tmp_path / "group_pack_showcase_fast_run"
    assert (root / "college" / "group_control_pack.docx").exists()
    assert (root / "country_b_subsidiary" / "control_team" / "inject_sheets" / "competent_authority.docx").exists()
