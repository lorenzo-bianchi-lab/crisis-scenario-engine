# Crisis Scenario Engine

University project (ESCP Business School, MiM), released publicly for anyone running crisis simulation exercises.
It generates the quantitative script of a bank crisis (deposit run -> liquidity stress -> triggers -> resolution funding need), and the materials and console to run an exercise on it.

This file and the specs, plans and status are development notes, kept in `dev/`. Paths below are from the repository root.

Full spec: @dev/SPEC.md  (read it before any work)
Second spec, phases A-F (current work): @dev/SPEC_V2.md
Third spec, phases G-K (after C2): @dev/SPEC_V3.md. Reference: the EBA Handbook (EBA/REP/2025/20), not in the repository: https://www.eba.europa.eu/publications-and-media/press-releases/eba-publishes-handbook-simulation-exercises-resolution-authorities. A local copy, if any, is the user's own file outside the repository.
Legal references shown by the tool: `config/legal.yaml` only (source: docs/LEGAL_REFERENCES.md).
Current status: @dev/STATUS.md

## Stack
- Python 3.11+, pandas, numpy, pydantic, plotly, streamlit, pytest, pyyaml, python-docx
- Run app: `streamlit run app/main.py`
- Run tests: `pytest -q`

## Architecture rules
- `engine/` is pure Python: no Streamlit imports, no file I/O inside model functions. The UI only calls the engine.
- Every model parameter lives in `config/*.yaml`, never hard-coded. Each parameter has `value`, `unit`, `source`.
- If a parameter has no verifiable source, set `source: ASSUMPTION`. Never invent citations, data or URLs.
- Simulations must be reproducible: fixed random seed passed explicitly.
- All code, UI text and docs in English.

## Workflow rules
- Work one milestone at a time, in the order of dev/SPEC.md. Do not start the next one unprompted.
- Before coding a milestone: give a short plan and list open questions. Wait for approval.
- A milestone is done only when: tests pass, the app runs, and the acceptance checks in the spec are shown with real output.
- At the end of each milestone: update `dev/STATUS.md` (what was built, how to run it, test output, deviations from spec, open questions, max 40 lines) and make one git commit `M<n>: <summary>`.
- If the spec is ambiguous or seems wrong, stop and ask. Do not silently change the model.
- Do not add features, dependencies or abstractions that the spec does not ask for.
- If a data download fails, do not guess another URL: stop and tell the user which file to download manually into `data/raw/`.

## Domain sanity checks (must always hold, covered by tests)
- Balance sheet identity: assets = liabilities + equity, every simulated day.
- No negative balances; outflows cannot exceed the remaining stock.
- With zero run-off, nothing changes over time.
- Higher severity never produces a later failure day.
