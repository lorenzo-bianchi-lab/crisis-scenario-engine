# PLAN F - Cross-border group

Plan for phase F of `dev/SPEC_V2.md` and `dev/SPEC_V3.md`, and for item 5 of UI-3 in `dev/SPEC_UI.md`. Written before the code; nothing is built until it is approved. Scope as set by the project team on 2026-11-23: parent and subsidiary in two countries, home and host authorities as a resolution college, intragroup transfers, host ring-fencing, and the map.

This is the largest phase so far: it touches the model, the decisions, the script, the documents and the app. It is cut in four parts, each ending with tests, `dev/STATUS.md`, one commit and a stop for review.

## What the sources say, and what is our own design
- SPEC_V2 asks for two entities, each with its own balance sheet, competent authority, resolution authority and DGS; intragroup transfers with a limit; a host decision to restrict them; confidence passing between the entities; injects for home and host with the coordination steps. Accept: one run where host ring-fencing changes the outcome for the parent; per-actor documents for both countries.
- The Handbook (par. 43) says a decision-making exercise should be used to train resolution colleges and find areas for improvement. It does not say who sits in a college or how it decides. So the college below (who is in it, who tells whom, how late) is this tool's assumption, written as such in config and on the Assumptions page. No legal basis is cited.

## F-a Engine: two entities in step
1. **The daily loop becomes a stepper.** Today `simulate()` runs all 30 days in one function, so two banks cannot exchange liquidity day by day. It is split into "set up", "one day" and "result"; `simulate()` stays and calls them. This changes the code of the model, not its behaviour: a test compares every figure of every bank, scenario and decision path with the results stored before the change, to the last decimal. All existing tests must pass untouched.
2. **`engine/group.py`**: parent and subsidiary advance together, one day at a time:
   - each entity runs its own day (run-off, funding waterfall, triggers, its own authorities' decisions);
   - **support transfer**: an entity that cannot fund the day's outflow from its own cash and central bank funding asks the other for liquidity before selling loans. The other gives what it has above a floor, up to a limit. Booked as an intragroup claim of the lender and an intragroup liability of the borrower, so each balance sheet still balances and the two cancel out in the consolidated view;
   - **ring-fencing**: once the host authority restricts transfers, the subsidiary gives nothing more (or up to a lower cap), and may call back what it lent, as the option says;
   - **confidence**: a stage reached by one entity is a panic shock for the other the next day, using the existing contagion parameters with a "same group" factor. A ring-fencing that becomes public is itself a shock for the parent (configurable, can be zero).
3. **Config**: `config/groups.yaml`. A group names two existing archetypes as parent and subsidiary, their countries (Country A and Country B), the transfer limit, the floor, the opening intragroup positions and the "same group" factor. Every value `ASSUMPTION`. One group is shipped for the showcase.
4. **Sanity checks, per entity and consolidated**: assets = liabilities + equity every day; no negative balance; a transfer never exceeds the limit or what the lender has; with zero run-off nothing changes and nothing is transferred; a single bank gives the same result alone as before. "Higher severity never produces a later failure day" is tested for the entity under stress and for the group; if support from the other entity breaks it for one entity, I stop and report rather than change the rule.
5. **CLI**: `python -m engine.group --group showcase --scenario fast_run [--ring-fence DAY]` prints both daily tables and the transfers.
Accept F-a: the identity test; the sanity checks; one printed run in which ring-fencing on a given day brings the parent to a payments default it avoids without it.

## F-b Decisions, college and script
1. **Decisions per entity.** The existing decisions apply to an entity and belong to that country's authority: home competent authority, home resolution authority, home DGS for the parent; host ones for the subsidiary.
2. **New decisions**: host competent authority, **restrict intragroup transfers** (no restriction; cap; full ring-fence). Group management (played by the control team), **ask for group support early** rather than at the last moment.
3. **The college as players.** Home and host authorities are separate teams with separate information sets: the host sees the subsidiary's figures and what the home authority passes on; the home sees the parent and the group's consolidated figures, the subsidiary's with a delay. Stage changes go from each entity to its own authority, then across the border with a configurable delay.
4. **Coordination steps expected by the script**: home informs host of a stage change at the parent; host consults home before ring-fencing; both resolution authorities meet as a college before a tool is chosen for either entity. As today, each is an expected response with a fallback inject, recorded as done or not.
5. **Documents**: inject sheets for every team of both countries, main events list with a country column, control team pack with both entities and the transfers, in the house style of UI-4. The information-set test runs for every team of both countries: a host team's document never shows a parent figure it was not given.
Accept F-b: the full export for the showcase group with two branches (ring-fenced or not); the leak test for all teams; a test for each new decision's effect.

## F-c App: Design, Prepare and the map
1. **Design**: a switch "single bank / cross-border group". For a group: the two timelines on one day axis, the intragroup position by day, the consolidated buffer, and the branch comparison with and without ring-fencing.
2. **Map (UI-3 item 5)**: home and host countries, the two entities, intragroup flows as arrows sized by amount on the chosen day, and ring-fencing as a blocked arrow with the word on it. Default: a schematic two-country diagram labelled Country A and Country B, as the spec says when no countries are picked.
3. **Information map and inject timeline** of UI-3 extended to the teams of both countries.
4. **Prepare**: the pack for a group.
Accept F-c: screenshots at 1920x1080; the guided demo still runs on the single bank.

## F-d Live exercise and closing report for a group
The control console, the players' pages and the closing report take a group: more teams, decisions of both countries, the college steps in the report. Kept apart because it is a large change to working parts.

## Not in this plan
- The system-wide variant of SPEC_V3 (two unrelated banks under stress at once). It was not in the scope given on 2026-11-23; it can reuse F-a's stepper afterwards.
- Internal MREL, loss transfer from subsidiary to parent, single or multiple point of entry.
- More than two entities, branches as opposed to subsidiaries, more than one currency.

## Questions, with the default I will take if you only say "approved"
1. **Real countries or a schematic?** A map of Europe needs the team to pick two countries and a file with country outlines, which would be a download to approve (Plotly's own maps are fetched from the internet at runtime, which a demo cannot rely on). Default: schematic diagram, Country A and Country B.
2. **Central bank.** Default: one central bank per country, each lending to its own entity; ELA is a national decision. Alternative: one central bank for both.
3. **Which archetypes form the showcase group?** Default: large universal bank as parent, mid-size retail bank as subsidiary, the stress starting at the parent.
4. **Direction of support.** Default: both ways, each entity up to its own limit; ring-fencing is the host's decision only. Alternative: the home authority can restrict too.
5. **Does the college decide anything in the model?** Default: no. Its meetings are coordination steps of the script; the tools stay decisions of each resolution authority for its own entity. Alternative: one joint decision on a group resolution scheme, which needs the loss transfer listed under "Not in this plan".
6. **The stepper refactor** changes `engine/model.py`. Default: do it, guarded by the identity test. Alternative: leave `simulate()` alone and approximate the group by simulating the entities in turn until the transfers settle, which is less faithful and can fail to settle.
7. **Order.** Default: F-a, F-b, F-c, F-d, a stop after each. If time is short, F-a to F-c give the demo; F-d can wait.
