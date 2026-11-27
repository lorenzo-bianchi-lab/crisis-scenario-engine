# PLAN UI - Visual system (UI-1) and charts (UI-2)

Plan for `dev/SPEC_UI.md`, parts UI-1 and UI-2. Written before the code and pushed with it not yet started. The project team asked on 2026-11-22 to build both parts without a stop for the palette: two palettes are built and shown side by side, and the review comes after. UI-3 and UI-4 are not in this plan.

## Constraints kept
- Streamlit stays. No engine change. No new dependency.
- No EBA logo, name or colours as branding.
- Nothing is downloaded: the typefaces are the ones Streamlit ships and serves itself (no CDN at runtime).

## UI-1 Visual system
1. **`app/theme.py`, the only place with colours and typefaces.** A `Palette` holds: dark neutral, light background, surface, line, muted text, accent, the stage scale, the funding-source colours and the series colours. Two palettes, `A` and `B`. The active one comes from the environment variable `CRISIS_PALETTE` (default `A`).
2. **Stage scale**, six steps, the same everywhere: normal, recovery, early intervention, likely to fail, FoLtF or resolution, payments default. Every use carries the stage's label. A test checks WCAG AA contrast of the text placed on each colour and, under simulated protanopia, deuteranopia and tritanopia, that every pair of stages stays apart.
3. **Typefaces**: Source Sans for text, Source Code Pro for figures (fixed-width numerals), both shipped inside Streamlit. Tokens in `app/theme.py`, so another pair can be swapped in once its files are in the repository.
4. **`.streamlit/config.toml`** holds palette A's theme and the minimal toolbar; **`app/theme.css`** is the one small CSS file (header, cards, badges, chrome removal). A test checks that the config file agrees with `app/theme.py`. `./start.sh` passes palette B's theme to Streamlit when `CRISIS_PALETTE=B`.
5. **Header** on every page: tool name, exercise name, SIMULATION label, and the Handbook phases (Design, Prepare, Deliver, Close) as a stepper that is also the navigation. The sidebar keeps only the Design controls. Streamlit's Deploy button, menu and footer are removed.
6. **KPI cards** in place of `st.metric` rows (Design headline, day panel, console, player page). **Tables**: figures right-aligned, units in the headers, at most two decimals.
7. Layout checked at 1920x1080 and at 1440x900.

## UI-2 Charts
All built in `app/charts.py` on one Plotly template from `app/theme.py`; a test fails if a chart builder returns a figure without it, or if a page builds a figure itself.
1. **Timeline**: liquidity buffer as the large chart, stage bands as background, weekends shaded, decisions and news as labelled markers; deposits and CET1 as two small charts below on the same day axis; lighter sensitivity band, explained in the legend.
2. **Funding by day**: stacked bars by source (cash, liquid securities, central bank, ELA, asset sales, unmet).
3. **Balance sheet**: opening against a chosen day, stacked columns for assets and for liabilities and capital.
4. **Branch comparison**: both paths on one chart, the day they part marked; the scorecard as paired bars.
5. **Resolution**: the liability stack as stacked bars, written down or converted against what remains, for the three write-down levels, with the breakpoint as a line.
6. **Decision map**: the tree of phase E, leaves in the stage scale.

## Deliverables
- Screenshots of the main pages at 1920x1080 in `docs/screenshots/ui_A/` and `docs/screenshots/ui_B/`, and the state before in `docs/screenshots/ui_before/`.
- Tests, `dev/STATUS.md`, one commit, push. Then stop for review.

## Choices made without asking, to be reviewed
- The two palettes and which is the default.
- Typefaces limited to what Streamlit ships, because nothing may be downloaded without approval.
- Outcomes on the decision map use the stage scale: stabilised as normal, still under stress as recovery, emergency funding as early intervention, the three resolution outcomes as FoLtF or resolution (told apart by marker shape and label), payments default as itself.
