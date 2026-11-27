# PLAN UI-3 - The tool's distinctive views (items 1 to 4)

Plan for `dev/SPEC_UI.md`, part UI-3, items 1 to 4. Written before the code; nothing is built until it is approved. Item 5 (geographic map) waits for phase F. UI-4 is not in this plan.

## Constraints kept
- No model change, no new dependency, Streamlit stays. Palette A, as chosen on 2026-11-23.
- Every chart is built in `app/charts.py` on the one template and shown through `ui.chart`.
- What a view shows is computed in `engine/` from existing results; the pages only draw it.
- The player page keeps showing only what `live.player_view` returns. The existing test that a player cannot see figures outside its information set stays, and none of the new views is added to the player page.

## 1. Information map ("who knows what, when")
- **Engine**: one pure function in `engine/script.py` that, for a path and a day, returns for each of the five actors: informed or not yet (and from which stage), the day its figures refer to (the day less its delay, with the faster reporting of early intervention if taken), how many figures are in its scope, and the notifications it received that day. And the list of links: who tells whom, with the delay. All of it is read from `config/decisions.yaml` (`information`) and `config/script.yaml` (`clock`), which the script already uses.
- **Chart**: fixed layout. Bank management on the left, competent authority in the middle, resolution authority, central bank and deposit guarantee scheme on the right. One arrow per link, labelled with its delay ("same day, 18:30", "figures of the previous close"). Each node shows the actor, "figures as of day N" and the stage it knows of. An actor not yet informed is drawn hollow and grey with "not yet informed" written on it, so the state does not rest on colour. Arrows active on the selected day are drawn solid, the others thin.
- **Design**: a new tab "Information map" with a day slider, and under the chart a table: actor, informed since, figures as of, figures in scope.
- **Console**: the same chart for the current simulated day, in an expander.

## 2. Inject timeline
- **Engine**: no new logic; the main events list and the decision points already hold day, time, recipient, purpose, who delivers, opening and deadline.
- **Chart**: horizontal axis is simulated time (day and clock time, weekends shaded). Six lanes: the five actors, and one lane "Simulated stakeholders" for injects the control team delivers in a stakeholder's name. Injects are markers; purpose is carried by colour and by shape (advance, challenge, control), with a legend. Decision windows are bars in the deciding actor's lane, from opening to deadline, labelled with the decision's name. Hover gives title, sender and trigger.
- **Tokens**: three purpose colours added to `app/theme.py` for palettes A and B, under the same contrast and colour-blind tests as the stages.
- **Prepare**: the timeline above the main events list, for the exercise as designed.
- **Console**: the same with a "now" line; injects already released are filled, the ones to come hollow; decisions taken end their bar at the day they were taken.

## 3. Control console as a cockpit
Same actions and same state, new order:
1. One band: simulated clock and stage of the bank, large; status and real start time, small.
2. The control buttons (next step, jump, pause, undo, close).
3. Open decisions as cards, one per decision: deciding team, question, closes of day left before the deadline, real time open so far (pauses excluded, as the log already counts it), then option, consulted and Record as today. The card takes the warning colour on the deadline day.
4. "To be passed on by the players", as today.
5. The inject timeline with the "now" line.
6. Collapsed below: information map, full-information table, players' links and saving, ad hoc inject, decisions recorded, log, replay.

## 4. Player page
- One column of fixed reading width, centred; larger type.
- Top: team name, simulated time, exercise status. Nothing else.
- The latest inject as an open card on top; earlier ones collapsed below, latest first.
- Every figures table carries "As of day N" in its heading and as a column, never only in a caption.
- A decision due is a block of its own in the inject: question, options, deadline, whom to consult and inform.

## Tests
- Information map: values match the script's own lag rule on several paths; an actor before its `informed_from` stage holds nothing; with faster reporting the competent authority's day moves.
- Timeline: one marker per event of the main events list, one bar per decision point, lanes complete; "now" line only on the console.
- New builders are caught by the existing one-template test; purpose colours by the contrast and colour-blind tests.
- Console and player page run in AppTest with an exercise in progress; the information-set test on the player's data is unchanged and passes.

## Deliverables
Screenshots at 1920x1080 in `docs/screenshots/ui3/` (Design information map, Prepare timeline, console at three moments, player page), checked also at 1440x900. Tests, `dev/STATUS.md`, one commit `UI-3: ...`, push. Then stop for review.

## Open questions, with the default I will take if you only say "approved"
1. **Time remaining on a decision card.** The model has no real-time deadline. Default: closes of simulated day left, plus real minutes open so far. Alternative: a real-time limit per decision, which would be a new parameter.
2. **Stakeholder lane.** Default: one lane for all simulated stakeholders, the stakeholder named on hover. Alternative: one lane each.
3. **Information map on the console.** Default: in an expander below the timeline, so the cockpit stays short. Alternative: always visible beside the timeline.
4. **Palette B.** Default: kept as an option, new tokens added to both. Alternative: remove it now.
5. **Player page on a phone.** Default: laid out for a laptop, readable on a tablet; not checked on a phone.
