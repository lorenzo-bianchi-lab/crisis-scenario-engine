# PLAN UI-4 - Exported documents

Plan for `dev/SPEC_UI.md`, part UI-4. Approved on 2026-11-23 with the four decisions at the end.

## Constraints kept
- No model change. No figure, wording or structure of the exercise changes: only how the Word documents look, and three charts added.
- `engine/` imports nothing from `app/`. Colours and typefaces stay tokens in `app/theme.py`.
- Nothing is downloaded without approval. No EBA logo, name or colours.

## 1. One look for every Word document
All documents of the pack and the closing report go through `_start` and `_table` in `engine/export.py`, so the look is set in those two places.
- **Style as an argument.** A small `DocStyle` (ink, muted, accent, line, stage colours, purpose colours, text and figures typefaces) is passed to the export functions. `app/theme.py` builds it from the active palette; the pages pass it on.
- **Cover page**: tool name, SIMULATION label, document title, exercise and bank alias, who the document is for (players, one team, observers, control team), type of exercise, the draft notice and the disclaimer. Then a page break. No logo.
- **Header and footer** on every page: SIMULATION label in a filled band, document title, page number.
- **Tables**: header row in the ink colour with light text, thin rules in the line colour, figures right-aligned in the figures typeface, units in the header.
- **Stage colours in tables**: wherever a table has a stage (control team timeline, main events list where an inject announces a stage, closing report), the cell is filled with the stage's colour and carries the stage's label, as in the app.
- **Headings** in the ink colour with the accent as a rule under level 1.

## 2. Charts in the documents
Word needs images. Three charts are drawn, each from data the document already holds:
1. **Player summary** (inject sheets of each player): what that player sees over the exercise. One panel per branch: the figures of its regular reports plotted at the day they refer to (liquidity buffer or its nearest figure in scope, deposits lost, CET1 ratio where in scope), a vertical mark at each stage it is notified of, a bar for each decision window it owns, and a shaded stretch before it is informed. It is built only from that player's own injects.
2. **Decision timeline** (closing report): one row per recorded decision, on the simulated day axis: opening, deadline, day taken, planned day; off-plan decisions marked; real minutes taken written beside.
3. **Counterfactual bars** (closing report): for each "what if" of the report, the scorecard lines against the actual run, as paired bars, outcome written as text.

## 3. Information leakage covers images
- A pure function returns the series behind a player's summary chart; the image is drawn from that and nothing else.
- The existing test on the player's documents is extended: every number in that series is a figure the player received, at the day it received it; and each image in a player's document is the one drawn from its own series (compared by content). The image's alternative text lists the same figures, so the text scan of the document sees them too.

## 4. Tests and acceptance
- Every exported document has the cover, the SIMULATION header and footer, and styled tables; stage cells carry the palette's colour and the label.
- Player documents open with the summary chart; closing report has both charts; with palette B the documents take B's colours.
- All existing export tests pass (their text checks are unchanged).
- Before and after: the charts as PNG in `docs/screenshots/ui4/`. Pages of the Word files as images only if a converter is installed on this machine; if not, I will say so and list the sample files instead (`exports/` is not in git).
- Tests, `dev/STATUS.md`, one commit `UI-4: ...`, push. Then stop for review.

## Decisions taken by the project team on 2026-11-23
1. **Chart images: matplotlib.** A new dependency, added to `requirements.txt`. The three charts are drawn with it, in the palette's colours.
2. **The player summary chart goes in the control team pack only**, one page per player. The players' inject-sheet files carry no image.
3. **Typefaces in Word: Calibri for text, Consolas for figures**, as tokens.
4. **The palette tokens move to a shared file outside `app/`**: `engine/style.py`, with no Plotly or Streamlit import. `app/theme.py` keeps the Plotly template, the CSS and the Streamlit theme, and reads the tokens from there. Documents written from the command line and from the app look the same. The "Style as an argument" point of section 1 falls away: the export reads the shared tokens itself.

Consequence for section 3: the leakage test checks that each player's chart in the control team pack is drawn only from that player's own injects, and that no player file holds an image.
