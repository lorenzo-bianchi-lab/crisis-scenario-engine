"""Export of a cross-border group's exercise pack (SPEC_V2 and SPEC_V3 phase F, part b).

One folder per country, each holding that country's ordinary pack (engine/export.py), and a
folder for the college: the group control pack, the main events list of both countries and
the data. Nothing here reads or writes files; the caller does.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Optional

import pandas as pd

from engine.decisions import OUTCOME_LABELS
from engine import legal as legal_refs
from engine.export import (
    _reference,
    legal_table_page,
    TO_COMPLETE,
    _csv,
    _save,
    _start,
    _table,
    main_events_rows,
    closing_report_docx,
    pack_files,
)
from engine.group import PARENT, SUBSIDIARY
from engine.group_script import GroupPack, resolve_team
from engine.schema import GROUP_ENTITIES, PLAYERS, College, DecisionsConfig

COLLEGE_NOTE = (
    "The college in this exercise is an assumption of the tool: who sits in it, who tells "
    "whom and how late are set in config/college.yaml. It is a draft for discussion in the "
    "exercise, not a statement of legal duties."
)


SYSTEM_NOTE = (
    "The system in this exercise is an assumption of the tool: two unrelated banks of one "
    "country, linked only by confidence (config/systems.yaml). It is a draft for discussion "
    "in the exercise, not a statement of legal duties."
)
TOGETHER_SYSTEM = (
    "This bank was exercised together with another bank of the same country: a decision "
    "changed here would also change the other bank's path. The system's closing report gives "
    "what the model gives for both banks with one decision changed."
)


def _pair_facts(pack: GroupPack) -> list[tuple[str, str]]:
    """The summary of a group or system exercise for the cover of its control pack."""
    group, whole = pack.group, pack.group.kind == "system"
    days = "; ".join(
        f"Branch {branch.label} ({branch.name}): " + ", ".join(
            f"{group.entity(role).name} to day {branch.result.of(role).end_day}" for role in GROUP_ENTITIES)
        for branch in pack.branches)
    return [
        ("Kind of exercise", "System-wide: two unrelated banks of one country" if whole
         else "Cross-border group: a parent and a subsidiary in two countries"),
        ("System" if whole else "Group",
         f"{group.name}: {group.parent.name} and {group.subsidiary.name}, {group.parent.country}" if whole
         else f"{group.name}: {group.parent.name} ({group.parent.country}, home) and "
              f"{group.subsidiary.name} ({group.subsidiary.country}, host)"),
        ("Scenario", f"{pack.scenario_name}, on {group.entity(pack.stressed).name}"),
        ("Simulated days", days),
    ]


def _bank_folder(group, role: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "_", group.entity(role).name.lower()).strip("_")
    return f"bank_{GROUP_ENTITIES.index(role) + 1}_{name}"


def _stage_words(status: str) -> str:
    return status.replace("_", " ").replace("failed", "payments default")


def both_banks_frame(pack: GroupPack) -> pd.DataFrame:
    """Both banks of a system day by day, on every branch (full information)."""
    rows = []
    for branch in pack.branches:
        for role in GROUP_ENTITIES:
            for row in branch.result.of(role).rows:
                rows.append({
                    "branch": branch.label, "bank": pack.group.entity(role).name, "day": row["day"],
                    "stage": _stage_words(str(row["status"])),
                    "deposits": round(float(row["deposits_total"]), 4),
                    "liquidity_buffer": round(float(row["liquidity_buffer"]), 4),
                    "outflow": round(float(row["outflow_total"]), 4)})
    return pd.DataFrame(rows)


def system_control_docx(pack: GroupPack, college: College) -> bytes:
    """The control pack of a system: both banks, how one reaches the other, and what each
    branch gives for both. Full information: control team only."""
    group = pack.group
    document = _start(
        "System control pack", f"{group.name} - {pack.scenario_name}", pack.scripts[PARENT],
        pack.mode, audience="the control team only", facts=_pair_facts(pack), landscape=True,
    )
    document.add_paragraph(SYSTEM_NOTE)
    document.add_paragraph(
        f"Amounts in {pack.unit}. Each bank's own pack is in its folder: {folder(pack, PARENT)} "
        f"and {folder(pack, SUBSIDIARY)}. The same four teams play on both banks."
    )
    document.add_heading("The two banks", level=1)
    _table(document, ["", "Bank", "Scenario", "Played by the control team"], [
        [f"{'First' if role == PARENT else 'Second'} bank", group.entity(role).name,
         pack.packs[role].scenario_name, pack.scripts[role].actors["bank_management"]]
        for role in GROUP_ENTITIES
    ])
    document.add_paragraph(
        "Teams (players), in " + group.parent.country + ": "
        + "; ".join(pack.scripts[PARENT].actors[actor] for actor in PLAYERS) + ".")
    document.add_heading("How one bank reaches the other", level=1)
    for line in (
        "The two banks lend nothing to each other and have no claim on each other.",
        "A stage reached by one bank (recovery indicator breached, early intervention, payments "
        "default) is a panic shock for the other the next day, and a headline its depositors read.",
        f"Strength of the link: {group.confidence_link.value:g} ({group.confidence_link.unit}).",
        "A bank put in resolution or liquidation before it defaults on its payments sends the "
        "shock of a resolution, smaller than that of a payments default (both assumed).",
        "The teams hold both banks' files: no notification between them is scripted.",
    ):
        document.add_paragraph(line, style="List Bullet")
    document.add_heading("Overview", level=1)
    _table(
        document,
        ["", *(f"Branch {b.label}: {b.name}" for b in pack.branches)],
        [
            *([f"{group.entity(role).name}: outcome",
               *(f"{OUTCOME_LABELS[b.result.of(role).outcome]}, last day {b.result.of(role).end_day}"
                 for b in pack.branches)] for role in GROUP_ENTITIES),
            *([f"{group.entity(role).name}: deposits lost (%)",
               *(f"{b.result.of(role).scorecard.deposits_lost_pct:.1f}" for b in pack.branches)]
              for role in GROUP_ENTITIES),
            *([f"{group.entity(role).name}: first day of each stage",
               *("; ".join(f"{_stage_words(stage)} day {day}"
                           for stage, day in dict(b.result.stage_days)[role]) or "none"
                 for b in pack.branches)] for role in GROUP_ENTITIES),
        ],
    )
    steps = [line for line in college.texts.get("coordination_system", "").split("\n") if line]
    if steps:
        document.add_heading("Coordination steps expected between the teams", level=1)
        document.add_paragraph("A draft for discussion in the exercise, not a statement of legal duties.")
        for line in steps:
            document.add_paragraph(line, style="List Bullet")
    for branch in pack.branches:
        document.add_page_break()
        document.add_heading(f"Branch {branch.label}: {branch.name}", level=1)
        result = branch.result
        document.add_heading("The two banks day by day (full information)", level=2)
        header = ["Day"]
        for role in GROUP_ENTITIES:
            name = group.entity(role).name
            header += [f"{name}: stage", f"{name}: buffer", f"{name}: deposits"]
        rows = []
        for day in range(max(result.parent.end_day, result.subsidiary.end_day) + 1):
            line = [f"Day {day}"]
            for role in GROUP_ENTITIES:
                run = result.of(role)
                if day < len(run.rows):
                    row = run.rows[day]
                    line += [_stage_words(str(row["status"])), f"{row['liquidity_buffer']:.2f}",
                             f"{row['deposits_total']:.2f}"]
                else:
                    line += ["run ended", "", ""]
            rows.append(line)
        _table(document, header, rows)
    legal_table_page(document, legal_refs.of(pack.scripts[PARENT]))
    return _save(document)


def folder(pack: GroupPack, role: str) -> str:
    """Folder of one country's pack inside the zip, e.g. 'country_a_parent'; of one bank's
    pack for a system, e.g. 'bank_1_online_bank_x'."""
    if pack.group.kind == "system":
        return _bank_folder(pack.group, role)
    country = re.sub(r"[^a-z0-9]+", "_", pack.group.entity(role).country.lower()).strip("_")
    return f"{country}_{role}"


def group_events_frame(pack: GroupPack) -> pd.DataFrame:
    """The main events lists of both countries as one, with a country column."""
    rows = []
    for role in GROUP_ENTITIES:
        entity = pack.group.entity(role)
        for row in main_events_rows(pack.packs[role], pack.scripts[role]):
            rows.append({"Country": entity.country, "Entity": entity.name, **row})
    frame = pd.DataFrame(rows)
    order = [name for name in ("Branch", "When") if name in frame.columns]
    return frame if not order else frame.sort_values(order, kind="stable").reset_index(drop=True)


def transfers_frame(pack: GroupPack) -> pd.DataFrame:
    return pd.DataFrame([
        {"branch": branch.label, "day": item.day,
         "lender": pack.group.entity(item.lender).name,
         "borrower": pack.group.entity(item.borrower).name,
         f"amount ({pack.unit})": item.amount,
         "asked for early": "yes" if item.early else ""}
        for branch in pack.branches for item in branch.result.transfers
    ], columns=["branch", "day", "lender", "borrower", f"amount ({pack.unit})", "asked for early"])


def consolidated_frame(pack: GroupPack) -> pd.DataFrame:
    return pd.DataFrame([
        {"branch": branch.label, **row}
        for branch in pack.branches for row in branch.result.consolidated
    ])


def group_decision_points_frame(pack: GroupPack) -> pd.DataFrame:
    return pd.DataFrame([
        {"branch": label, "code": point.code, "decision": point.decision_name, "team": point.team,
         "option": point.option_name,
         "opens at the close of day": "" if point.open_day is None else point.open_day,
         "followed on this branch": "yes" if point.taken else "",
         "parent": OUTCOME_LABELS[point.parent_outcome], "parent, last day": point.parent_end_day,
         "subsidiary": OUTCOME_LABELS[point.subsidiary_outcome],
         "subsidiary, last day": point.subsidiary_end_day,
         f"lent inside the group ({pack.unit})": round(point.lent, 2)}
        for label, points in pack.decision_points for point in points
    ])


def group_control_docx(pack: GroupPack, college: College) -> bytes:
    """The college's control pack: both entities, the transfers, the group's decisions and
    the coordination steps between the two countries. Full information: control team only."""
    group = pack.group
    document = _start(
        "Group control pack", f"{group.name} - {pack.scenario_name}", pack.scripts[PARENT],
        pack.mode, audience="the control team only", facts=_pair_facts(pack), landscape=True,
    )
    document.add_paragraph(COLLEGE_NOTE)
    _reference(document, legal_refs.line(legal_refs.of(pack.scripts[PARENT]), ["document:college"]))
    document.add_paragraph(
        f"The scenario hits {group.entity(pack.stressed).name}. Amounts in {pack.unit}. Each "
        f"country's own pack is in its folder: {folder(pack, PARENT)} and {folder(pack, SUBSIDIARY)}."
    )

    document.add_heading("The two entities and their teams", level=1)
    _table(document, ["", "Entity", "Country", "Teams (players)", "Played by the control team"], [
        [role.capitalize(), group.entity(role).name, group.entity(role).country,
         "; ".join(pack.scripts[role].actors[actor] for actor in PLAYERS),
         pack.scripts[role].actors["bank_management"]]
        for role in GROUP_ENTITIES
    ])

    document.add_heading("Overview", level=1)
    _table(
        document,
        ["", *(f"Branch {b.label}: {b.name}" for b in pack.branches)],
        [
            *([f"{group.entity(role).name}: outcome",
               *(f"{OUTCOME_LABELS[b.result.of(role).outcome]}, last day {b.result.of(role).end_day}"
                 for b in pack.branches)] for role in GROUP_ENTITIES),
            *([f"{group.entity(role).name}: deposits lost (%)",
               *(f"{b.result.of(role).scorecard.deposits_lost_pct:.1f}" for b in pack.branches)]
              for role in GROUP_ENTITIES),
            [f"Lent inside the group ({pack.unit})",
             *(f"{sum(t.amount for t in b.result.transfers):.2f}" for b in pack.branches)],
            ["Restriction of transfers in effect",
             *("no" if b.result.ring_fence_from is None else f"from day {b.result.ring_fence_from}"
               for b in pack.branches)],
        ],
    )

    document.add_heading("What crosses the border", level=1)
    late = int(college.consolidated_delay_days.value)
    delay = int(college.cross_border_delay_days.value)
    for line in (
        "Each team sees its own entity's figures, as in a single-bank exercise, at its own delay.",
        "A stage change at one entity is passed on by that entity's competent authority to the "
        "competent and resolution authorities of the other country, "
        + ("the same day" if delay == 0 else f"{delay} day{'s' if delay > 1 else ''} later")
        + f" ({college.cross_border_clock}), with the figures behind it. In a dynamic exercise this "
        "is an expected response, with a fallback inject held by the control team.",
        "Each competent authority also sees what its own entity has lent to, and borrowed from, "
        "the rest of the group.",
        f"The home authorities also receive the group's consolidated liquidity buffer and deposits, "
        f"{late} day{'s' if late != 1 else ''} older than their own figures.",
        "No team sees the other entity's regular reports.",
    ):
        document.add_paragraph(line, style="List Bullet")

    document.add_heading("Decisions of the group", level=1)
    for key, definition in college.decisions.items():
        document.add_heading(definition.name, level=2)
        document.add_paragraph(definition.question.strip())
    for label, points in pack.decision_points:
        document.add_heading(f"Where each option leads on branch {label}", level=2)
        document.add_paragraph(
            "Each option taken at the first close at which the decision is open, the "
            "entities' own decisions as on the branch, no other group decision."
        )
        _table(
            document,
            ["Code", "Decision", "Team", "Option", "Opens (close of day)", "On this branch",
             "Parent", "Subsidiary", f"Lent ({pack.unit})"],
            [[p.code, p.decision_name, p.team, p.option_name,
              "never" if p.open_day is None else str(p.open_day), "followed" if p.taken else "",
              f"{OUTCOME_LABELS[p.parent_outcome]}, day {p.parent_end_day}",
              f"{OUTCOME_LABELS[p.subsidiary_outcome]}, day {p.subsidiary_end_day}",
              f"{p.lent:.2f}"] for p in points],
        )

    document.add_heading("Coordination steps between the two countries", level=1)
    document.add_paragraph("Expected by the script. A draft for discussion in the exercise.")

    def names(reference: str) -> str:
        """A team reference in words; "own" and "other" stay general."""
        side, _, actor = reference.strip().partition(":")
        if side in ("own", "other"):
            words = "its own country" if side == "own" else "the other country"
            return f"The {actor.replace('_', ' ')} of {words}"
        entity, actor = resolve_team(reference.strip(), PARENT)
        return pack.scripts[entity].actors[actor]

    _table(document, ["About", "Step", "By", "With or to"], [
        [step.about, step.step, names(step.by), "; ".join(names(item) for item in step.to.split(","))]
        for step in college.coordination
    ])

    for branch in pack.branches:
        document.add_page_break()
        document.add_heading(f"Branch {branch.label}: {branch.name}", level=1)
        result = branch.result
        document.add_heading("Decisions of the group taken", level=2)
        if result.decisions:
            _table(document, ["Decision", "Option", "Team", "Decided (close of day)", "In effect from day"], [
                [r.decision_name, r.option_name,
                 pack.scripts[r.entity].actors[r.actor], str(r.decided_day), str(r.effect_day)]
                for r in result.decisions
            ])
        else:
            document.add_paragraph("None.")
        document.add_heading("Intragroup transfers", level=2)
        if result.transfers:
            _table(document, ["Day", "Lender", "Borrower", f"Amount ({pack.unit})", "Note"], [
                [f"Day {t.day}", group.entity(t.lender).name, group.entity(t.borrower).name,
                 f"{t.amount:.2f}", "asked for early" if t.early else "payments could not be funded otherwise"]
                for t in result.transfers
            ])
        else:
            document.add_paragraph("None.")
        document.add_heading("The two entities day by day (full information)", level=2)
        header = ["Day"]
        for role in GROUP_ENTITIES:
            short = role.capitalize()
            header += [f"{short}: stage", f"{short}: buffer", f"{short}: lent", f"{short}: borrowed"]
        header += ["Group buffer", "Group deposits"]
        rows = []
        last = max(result.parent.end_day, result.subsidiary.end_day)
        for day in range(last + 1):
            line = [f"Day {day}"]
            for role in GROUP_ENTITIES:
                run = result.of(role)
                if day < len(run.rows):
                    row = run.rows[day]
                    line += [str(row["status"]).replace("_", " ").replace("failed", "payments default"),
                             f"{row['liquidity_buffer']:.2f}", f"{row['intragroup_claim']:.2f}",
                             f"{row['intragroup_liability']:.2f}"]
                else:
                    line += ["run ended", "", "", ""]
            if day < len(result.consolidated):
                line += [f"{result.consolidated[day]['liquidity_buffer']:.2f}",
                         f"{result.consolidated[day]['deposits_total']:.2f}"]
            else:
                line += ["", ""]
            rows.append(line)
        _table(document, header, rows)
    legal_table_page(document, legal_refs.of(pack.scripts[PARENT]))
    return _save(document)


def group_files(
    pack: GroupPack, decisions: DecisionsConfig, college: College,
    sessions: Optional[int] = None, session_hours: Optional[float] = None,
) -> dict[str, bytes]:
    """All files of a group's pack: path inside the zip -> content."""
    files = {}
    for role in GROUP_ENTITIES:
        for name, content in pack_files(pack.packs[role], pack.scripts[role], decisions,
                                        sessions, session_hours).items():
            files[f"{folder(pack, role)}/{name}"] = content
    if pack.group.kind == "system":
        files["system/system_control_pack.docx"] = system_control_docx(pack, college)
        files["system/data/SIMULATION_system_main_events_list.csv"] = _csv(
            group_events_frame(pack).rename(columns={"Entity": "Bank"}).drop(columns="Country"))
        files["system/data/SIMULATION_system_both_banks.csv"] = _csv(both_banks_frame(pack))
        return files
    files["college/group_control_pack.docx"] = group_control_docx(pack, college)
    files["college/data/SIMULATION_group_main_events_list.csv"] = _csv(group_events_frame(pack))
    files["college/data/SIMULATION_group_transfers.csv"] = _csv(transfers_frame(pack))
    files["college/data/SIMULATION_group_consolidated.csv"] = _csv(consolidated_frame(pack))
    files["college/data/SIMULATION_group_decision_points.csv"] = _csv(group_decision_points_frame(pack))
    return files


def group_to_zip(pack: GroupPack, decisions: DecisionsConfig, college: College) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in group_files(pack, decisions, college).items():
            archive.writestr(name, content)
    return buffer.getvalue()


# --------------------------------------------------------------------------- closing report


JOINT_COLUMNS = ["Simulated time", "Subject", "Outcome", "Took part", "Expected and absent",
                 "Real time", "Real minutes taken", "Note"]


def system_report_docx(report, script) -> bytes:
    """Draft closing report of a live exercise on a system: what belongs to both banks. Each
    bank's own report is a document of its own. Facts and differences in outcomes only."""
    document = _start(f"System closing report (draft): {report.title}", report.group_name, script,
                      audience="the control team and the design team, to complete", landscape=True,
                      facts=[("Kind of exercise", "System-wide"), *report.overview[:4]])
    document.add_paragraph(SYSTEM_NOTE)
    document.add_paragraph(script.report.intro)
    document.add_heading("1. Overview", level=1)
    _table(document, ["", ""], [list(row) for row in report.overview])
    document.add_heading("2. Joint decisions of the authorities", level=1)
    document.add_paragraph(
        "What the teams decided together on the two banks, as the control team recorded it: who "
        "took part, which of the expected teams did not, and whether they agreed. A record: a "
        "joint decision changes nothing in the model."
    )
    if report.college:
        _table(document, JOINT_COLUMNS, [list(row) for row in report.college])
    else:
        document.add_paragraph("No joint decision was recorded.")
    document.add_heading("3. What the model gives for both banks with one decision changed", level=1)
    document.add_paragraph(
        "One recorded decision on one bank changed, everything else as it happened. A change on "
        "one bank reaches the other through confidence only. A model result under assumed "
        "parameters, not a judgement on what was decided."
    )
    first, second = (name for name, _ in report.overview[8:10])
    if report.counterfactuals:
        _table(document, ["Decided by", "Decision", "Change", first, second],
               [[c.team, c.decision_name, c.change, c.parent, c.subsidiary]
                for c in report.counterfactuals])
    else:
        document.add_paragraph("No decision was recorded on either bank.")
    document.add_heading("4. Lessons learned and recommendations", level=1)
    document.add_paragraph().add_run(f"[{TO_COMPLETE}]").bold = True
    for _ in range(3):
        document.add_paragraph("_" * 100)
    return _save(document)


def group_report_docx(report, script, college: College) -> bytes:
    """Draft closing report of a live group exercise: what belongs to the group. Each
    country's own report is a document of its own. Facts and differences in outcomes only."""
    if report.kind == "system":
        return system_report_docx(report, script)
    document = _start(f"Group closing report (draft): {report.title}", report.group_name, script,
                      audience="the control team and the design team, to complete", landscape=True,
                      facts=[("Kind of exercise", "Cross-border group"), *report.overview[:4]])
    document.add_paragraph(COLLEGE_NOTE)
    _reference(document, legal_refs.line(legal_refs.of(script), ["document:college"]))
    document.add_paragraph(script.report.intro)
    document.add_heading("1. Overview", level=1)
    _table(document, ["", ""], [list(row) for row in report.overview])
    document.add_heading("2. Decisions of the group", level=1)
    if report.decisions:
        _table(document, ["Simulated day", "Decided by", "Decision", "Option", "Pre-planned option",
                          "Real minutes from opening", "Notes"], [list(row) for row in report.decisions])
    else:
        document.add_paragraph("No decision of the group was recorded.")
    document.add_heading("3. Joint decisions of the college", level=1)
    document.add_paragraph(
        "What the teams of both countries decided together, as the control team recorded it: "
        "who took part, which of the expected members did not, and whether they agreed. A "
        "record: a joint decision changes nothing in the model."
    )
    if report.college:
        _table(document, ["Simulated time", "Subject", "Outcome", "Took part", "Expected and absent", "Real time", "Real minutes taken", "Note"], [list(row) for row in report.college])
    else:
        document.add_paragraph("No joint decision of the college was recorded.")
    document.add_heading("4. Steps across the border", level=1)
    document.add_paragraph(
        "What one country's team should have told the other country's teams, and whether the "
        "control team recorded it as passed on. A record of what was logged, not an assessment."
    )
    if report.crossing:
        _table(document, ["Simulated day", "What", "By", "To", "Record"],
               [list(row) for row in report.crossing])
    else:
        document.add_paragraph("No step across the border was expected up to this point.")
    document.add_heading("5. Intragroup transfers", level=1)
    if report.transfers:
        _table(document, ["Simulated day", "Lender", "Borrower", f"Amount ({report.unit})", "Note"],
               [list(row) for row in report.transfers])
    else:
        document.add_paragraph("None.")
    document.add_heading("6. What the model gives with one decision of the group changed", level=1)
    document.add_paragraph(
        "Everything else as it happened. A model result under assumed parameters, not a "
        "judgement on what was decided."
    )
    if report.counterfactuals:
        _table(document, ["Decided by", "Decision", "Change", "Parent", "Subsidiary",
                          f"Lent inside the group ({report.unit})"],
               [[c.team, c.decision_name, c.change, c.parent, c.subsidiary, f"{c.lent:.2f}"]
                for c in report.counterfactuals])
    else:
        document.add_paragraph("No decision of the group was open on this path.")
    document.add_heading("7. Lessons learned and recommendations", level=1)
    document.add_paragraph().add_run(f"[{TO_COMPLETE}]").bold = True
    for _ in range(3):
        document.add_paragraph("_" * 100)
    return _save(document)


def group_report_files(report, scripts: dict, group, college: College) -> dict[str, bytes]:
    """The group's closing report, each country's own, and the data: path -> content."""
    if group.kind == "system":
        files = {"system/SIMULATION_system_closing_report_draft.docx":
                 system_report_docx(report, scripts[PARENT])}
        for role in GROUP_ENTITIES:
            files[f"{_bank_folder(group, role)}/SIMULATION_closing_report_draft.docx"] = closing_report_docx(
                report.countries[role], scripts[role], TOGETHER_SYSTEM)
        files["system/SIMULATION_system_report_joint_decisions.csv"] = _csv(pd.DataFrame(
            report.college, columns=["simulated_time", "subject", "outcome", "took_part",
                                     "expected_and_absent", "real_time", "real_minutes", "note"]))
        files["system/SIMULATION_system_report_one_decision_changed.csv"] = _csv(pd.DataFrame(
            [(c.team, c.decision_name, c.change, c.parent, c.subsidiary) for c in report.counterfactuals],
            columns=["decided_by", "decision", "change", "first_bank", "second_bank"]))
        return files
    files = {"college/SIMULATION_group_closing_report_draft.docx":
             group_report_docx(report, scripts[PARENT], college)}
    for role in GROUP_ENTITIES:
        country = re.sub(r"[^a-z0-9]+", "_", group.entity(role).country.lower()).strip("_")
        files[f"{country}_{role}/SIMULATION_closing_report_draft.docx"] = closing_report_docx(
            report.countries[role], scripts[role])
    files["college/SIMULATION_group_report_decisions.csv"] = _csv(pd.DataFrame(
        report.decisions, columns=["day", "decided_by", "decision", "option", "planned_option",
                                   "real_minutes", "notes"]))
    files["college/SIMULATION_group_report_joint_decisions.csv"] = _csv(pd.DataFrame(
        report.college, columns=["simulated_time", "subject", "outcome", "took_part",
                                 "expected_and_absent", "real_time", "real_minutes", "note"]))
    files["college/SIMULATION_group_report_steps.csv"] = _csv(pd.DataFrame(
        report.crossing, columns=["day", "what", "by", "to", "record"]))
    return files


def group_report_to_zip(report, scripts: dict, group, college: College) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in group_report_files(report, scripts, group, college).items():
            archive.writestr(name, content)
    return buffer.getvalue()
