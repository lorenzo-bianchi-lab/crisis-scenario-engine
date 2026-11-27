# STATUS

Last completed: **v1.0 preparation: a public project** (2026-11-27). University project (ESCP Business School, MiM), released publicly for anyone running crisis simulation exercises. Done before: M0-M6, SPEC_V2 A-D and F, SPEC_V3 G, H, I, E, F (group and system-wide), K (prepared, not tested with a real model), SPEC_UI UI-1 to UI-4, the demo round, the Word documents round. Not built: phase J.

## What was built
- **Framing**: every statement that the tool was made for, presented to or demoed to the EBA is gone (README, docs, development notes). The EBA Handbook (EBA/REP/2025/20) and EU law stay as cited public sources.
- **Legal references** (`docs/LEGAL_REFERENCES.md`, section "How to use them in the tool"):
  - `config/legal.yaml` holds the table (23 rows: concept, reference, status) and the switch `show: true`; `engine/legal.py` turns the tool's concepts into a "Reference:" line. Status B shows "(to be confirmed)"; the valuation row "(paragraph to be confirmed)"; ELA shows its row's words and no article.
  - Shown on: injects (inject sheets, team pages, console), the control team pack (resolution summary, decision points, a page with the whole table and the note), the dossier's liability stack, the closing report, the decision cards of the single-bank and group consoles, the group control pack and report (colleges).
  - Switch: *Show legal references* on the Prepare and Deliver pages (an exercise keeps the setting it started with); `--no-legal-references` on the command line. A tab *Legal references* under *Assumptions and limits*.
  - Article 128c is cited on the home page and in METHODOLOGY as context only (why such exercises exist), with "in cross-border situations". METHODOLOGY and LIMITS have one paragraph each.
- **Layout**: `dev/` holds CLAUDE.md, the specs, the plans and this file. The Handbook PDF is removed; README links to the EBA page. No code read it.
- **Screenshots** retaken (37 views at both sizes) and a hero image of the live console; packs regenerated in `exports/packs/`.
- **Public project files**: README rewritten for a stranger, LICENSE (MIT), CITATION.cff, CHANGELOG.md, `.github/workflows/tests.yml` (fast subset: 915 tests in about 20 seconds), badges. Repository description and topics set on GitHub.
- **Account rename**: remote and links on `Lorenzo-bianchi-lab`; the repository's commit address is the new noreply address.

## How to run
- App: `./start.sh` | Tests: `.venv/bin/pytest -q` | Packs: see README, "Without the app"

## Acceptance, with real output
- `tests/test_legal.py`, 9 tests: the file equals the table of `docs/LEGAL_REFERENCES.md` (23 rows); no article is cited in any template outside it (config, engine, app); every "Reference:" part in the 112 documents of the three packs is one of the file's references; references sit where the concepts are; the closing report and the team pages carry them; with the switch off no reference and no article appear in any document or team page; the app shows them, and a new exercise started with the switch off shows none; the Assumptions tab lists the whole table.
- **`pytest -q` -> 1959 passed in one clean run (12 min 3 s).** A run before it had two failures, both expected: the Assumptions page has one more tab and one more table; the two tests were updated, then the whole suite was run again.

## Deviations and choices
- **No code read the Handbook PDF**: there was no code path to keep working. README says where to download it.
- Concepts with no row get no reference: recovery stage, payments default, public statement, recovery options, decisions of the group other than the college.
- Not shown in the main events list (already 11 columns): its injects carry the reference on the inject sheets.
- The Word documents have still not been reviewed page by page as rendered (no LibreOffice on this machine).

## Open questions
- From before: decisions taken after FoLtF in design by objective; wording and legal references need a practitioner's review.
