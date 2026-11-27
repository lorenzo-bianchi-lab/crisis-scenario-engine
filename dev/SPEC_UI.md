# SPEC UI - Visual redesign and useful graphics

Applies after phase E. No model change. The rules in CLAUDE.md still apply: plan first (dev/plans/PLAN_UI.md), stop for review.

## Goal
The app must look like a professional tool an authority could have commissioned, not a default Streamlit prototype.
The audience is people seeing it once, projected, for a few minutes. Every screen must be readable from the back of a room.

## Constraints
- Stay on Streamlit. No frontend rewrite. Theme through `.streamlit/config.toml` plus one small CSS file; one shared Plotly template in `app/theme.py` used by every chart.
- Do not use the EBA's logo, name or colours as branding: the tool must not look like an official EBA product.
- Graphics must carry information. No decorative charts, no gauges for their own sake, no 3D, no animation except the day-by-day play.
- Engine untouched. All existing tests pass.

## UI-1 Visual system
- One typeface for text and one for figures (tabular numerals), loaded locally, not from a CDN at runtime (the demo may be offline).
- Palette: one dark neutral, one light background, one accent, plus a fixed semantic scale used everywhere for the bank's stage: normal, recovery, early intervention, likely to fail, FoLtF/resolution, payments default. The same stage always has the same colour in charts, tables, badges and exported documents.
- Check contrast (WCAG AA) and that the stage scale is distinguishable for colour-blind viewers; pair colour with a label or icon.
- Layout: a slim header with the exercise name, the SIMULATION label and the current Handbook phase (Design, Prepare, Deliver, Close) as a stepper. Remove Streamlit chrome (Deploy button, menu, footer).
- KPI cards replacing raw `st.metric` rows; consistent spacing; tables with right-aligned figures, units in headers, no more than 2 decimals.
- Wide layout tuned for a 1920x1080 projector and usable on a 13-inch laptop.

## UI-2 Charts that replace or improve what exists
1. Timeline: one clear hero chart (liquidity buffer) with stage bands as background colour, weekends shaded, decisions and news as labelled markers; deposits and CET1 as two small charts under it sharing the x axis. Sensitivity band lighter and explained in the legend.
2. Funding waterfall per day: stacked bars by source (cash, liquid securities, central bank, ELA, asset sales, unmet), one colour per source.
3. Balance sheet, opening against selected day: two stacked columns for assets and for liabilities and capital.
4. Branch comparison: the two paths on the same chart with the divergence point marked, and the outcome scorecard as a side-by-side bar table.
5. Resolution: the liability stack as a vertical stacked bar showing, per layer, the amount written down or converted against what remains, for the three write-down levels side by side, with the breakpoint drawn as a line.
6. Decision map (from phase E): tree, one level per decision, leaves coloured by outcome with the stage scale.

## UI-3 New graphics, the tool's distinctive views
1. Information map ("who knows what, when"): the players as nodes, arrows for each notification with its delay, and for the selected day what each player holds: figures as of which day. This is the view that shows information asymmetry at a glance. On Design and on the control console.
2. Inject timeline: swimlanes, one lane per player and one for simulated stakeholders, injects as markers coloured by purpose (advance, challenge, control), decision windows as bars from opening to deadline. On Prepare and on the control console (with a "now" line).
3. Control console as a cockpit: clock and stage prominent, decisions open as cards with time remaining, the inject timeline with the "now" line, the full-information table collapsed below.
4. Player page: clean, one column, large type, latest inject on top, the player's figures table with "as of" date always visible.
5. Geographic map: only with phase F (cross-border group). Europe map with the home and host countries highlighted, the group's entities, intragroup liquidity flows as arrows sized by amount, and ring-fencing shown as a blocked arrow. Countries stay fictitious labels (Country A, Country B) placed on real geography only if the project team picks the two countries; otherwise use a schematic two-country diagram, not a map.

## UI-4 Exported documents
- Word documents use the same palette and typefaces: cover page, header with the SIMULATION label, styled tables, stage colours in tables.
- Each player's document opens with a one-page summary chart of what that player sees (within its information set only; the existing test on information leakage must cover images too).
- Closing report: decision timeline chart and counterfactual bar chart.

## Acceptance
- Before/after screenshots of every page in `docs/screenshots/`, taken at 1920x1080.
- No truncated label at 1920x1080 or at 1440x900.
- One Plotly template: a test fails if a chart is built without it.
- The guided demo runs start to end without scrolling horizontally.
- App start and page switch no slower than before.

## Order
UI-1 and UI-2 first (one commit), review, then UI-3 (items 1 to 4), review, then UI-4. UI-3 item 5 is built with phase F.
