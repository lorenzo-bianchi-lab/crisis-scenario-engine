# LIMITS

What the Crisis Scenario Engine does not cover. One page, kept in step with the code. Parameter sources are in `docs/CALIBRATION.md` and on the app's Assumptions page.

## What it is
A generator of the quantitative script of a bank crisis for a simulation exercise, with a console to run the exercise. It is not a supervisory model, a stress test or a forecast. Every bank is fictitious.

## The model
- **One currency, whole days; one bank, or two (see below).** Times of day are labels; the outflow by time block is an allocation of the daily figure. No intraday liquidity, no payment systems.
- **Assumptions, not estimates.** Run-off rates, the panic curve, haircuts, flight-prone shares, weekend rules and every challenge effect are assumptions unless the Assumptions page gives a source. The comparison with real cases is an order-of-magnitude check.
- **Ranges are not probabilities.** The sensitivity runs sample assumed intervals.
- **Liquidity first.** Capital moves only through fire-sale losses, portfolio sales and a loss revealed by a news event. No credit risk model, no earnings, no market risk, no interest-rate risk, no derivatives book.
- **Simple funding.** One wholesale ladder; collateral in four classes with fixed haircuts; one ordinary central bank facility and ELA. No repo market, no collateral calls, no intragroup funding.
- **Deposits.** Four categories. Covered deposits are the insured retail deposits; no single-customer view, no depositor concentration.
- **Market shocks** (rating downgrade, fall in share and bond prices) are a panic shock and a cut in wholesale roll-over. The cut is not in the projected outflows behind the "likely to fail" warning. No share price, bond price or rating is modelled.
- **Two banks at most.** A second bank exists only as the other entity of a cross-border group or the other bank of a system (below). The one-way contagion code of the first version is not used in the app; its similarity rule is.
- **Cross-border group: on the Design, Prepare, Deliver and Close pages, and from the command line** (`python -m engine.group`). On Design each country has its own decision panel (a named path, and every decision one by one) next to the two decisions of the group. A live group exercise starts with no pre-planned decision; its ad hoc injects are free text or library injects that carry and change no figure, sent to teams of one country; its challenge injects are the ones that change no figure. The map is a schematic of two fictitious countries, not geography. A parent and a subsidiary advance together; an entity that cannot fund its payments gets cash from the other before it sells loans, within a limit and above the lender's floor; the host authority can cap or stop the subsidiary's lending once it has been told of trouble at the parent; group management can ask for support early; a stage at one entity is a panic shock for the other the next day; a lender writes off part of its claim if the borrower defaults. All of it is assumed (`config/groups.yaml`, `config/college.yaml`). **The college is this tool's assumption**: the Handbook (par. 43) says a decision-making exercise should train colleges and does not define one. Each country has its four authorities as teams; a team sees its own entity's figures, its position towards the group (competent authority), the consolidated figures a day older (home authorities), and of the other entity only what a stage notification carries. Not built: a joint decision of the college (its meeting is a coordination step on paper), the home authorities' view of the subsidiary's own reports, market-shock challenge injects in a group, the counterfactuals of an entity's own decisions in a group's closing report (only the group's decisions are varied). Not modelled: a call for repayment, opening intragroup positions, internal MREL and loss transfer, support in securities, more than two entities.
- **System-wide stress: two unrelated banks of one country, on the Design, Prepare, Deliver and Close pages, and from the command line** (`python -m engine.group --system`). The Handbook (par. 115(b)) names a system-wide issue, with contagion and players who may have to resolve several banks at once, and does not say how to build one: **the system is this tool's assumption** (`config/systems.yaml`). The two banks lend nothing to each other. A stage reached by one (recovery indicator breached, early intervention, payments default) is a panic shock for the other the next day, at a strength computed from the similarity parameters of `config/model.yaml` (same country, plus same archetype): 1.0 for the two online banks shipped, 0.5 for the universal bank with the specialised lender. **A bank resolved before it defaults sends a smaller shock** (`contagion_shock_resolution`, 0.06 against 0.12 for a payments default, both assumed; 0 switches it off): how much better an early resolution is for the other bank rests on the gap between these two numbers and on nothing observed. The effect on the second bank is a change of path (deposits lost, stages reached, their days), not of its final outcome, in the two pairs shipped. The same four teams handle both banks: no notification between them is scripted, and nothing they share runs short (deposit guarantee fund, central bank capacity, staff). The second bank's scenario is fixed per pair. A live exercise on a system starts with no pre-planned decision. Not built: more than two banks, two countries.

## Reverse mode and design by objective
- **The solver returns one parameter set, not a likelihood.** It searches three variables only (severity, size of the news shocks, run-off speed); the ranges of the last two are assumptions. The target is the model's "likely to fail" flag on the path without decisions, not a FoLtF determination.
- **"Hardest" is a rule of the tool**: the largest difference between options where none is lowest on every compared line. The lines are the outcome, ranked by a setting of `config/script.yaml` (as shipped: the bank stays open and is not likely to fail at the end; resolution or liquidation, the three tools on one rank; the bank left open while still flagged as likely to fail on the last day; payments default, the worst), deposits lost, losses booked, ELA outstanding, DGS payout, what creditors other than shareholders bear at the central write-down, and the days until covered depositors are paid. The ranking and "lower is preferred" are assumptions. An orderly resolution does not rank below a bank left open in a likely-to-fail state; a bank left open with the flag cleared still ranks above a resolution, whatever it has lost. Decisions the tested one depends on, and decisions that depend on the option taken, are taken at their first close with their first option that is not "no decision" (after FoLtF: a one-day moratorium and bail-in); all others are left untaken, so payout preparation shows no effect. Central parameters, no challenge injects.
- **The decision map** takes each decision at the first close at which it can be; timing is not varied.

## Resolution
- **Order of loss absorption is the model's**, not a statement of law. Legal loss-absorption thresholds, the use of resolution financing arrangements and State aid are not modelled.
- **The valuation is an assumed write-down** of the loan book (low, central, high). No valuation methodology, no "no creditor worse off" comparison.
- **Sale of business is computed like bail-in.** The buyer's price is an assumption that changes no other figure; no bids, no perimeter, no data room. No bridge institution, no asset separation.
- **The run stops when the tool is applied.** Nothing after the bank reopens: no market reaction to the decision, no restructuring plan.
- **Liquidation** is a waterfall on assumed asset values; the deposit guarantee scheme's funding is not modelled.

## The exercise script
- **Wording is a draft** by this project: injects, role cards, rules of play, charter proposals, expected responses and coordination steps have not been reviewed by a practitioner and carry no legal references.
- **Who is consulted and informed is a proposal**, not a statement of legal duties. The competent authority relays every stage change.
- **Five simulated stakeholders** only: bank management, independent valuer, media, finance minister, potential buyers. No shareholders, auditors, market authority, competition authority, foreign authorities, central counterparties or other banks.
- **Information challenges are fixed patterns**: a press conference, an incomplete notification, a notification to the wrong player. Leaks and misinformation are not generated.

## Live mode and closing report
- **No accounts and no security.** Link codes and the control team's code prevent mistakes only; the control team's code is in the config file; saved exercises are plain files.
- **Local use.** One machine or a local network; pages refresh by polling, a few seconds behind.
- **Decisions take effect the next simulated day.** The path assumes the planned decisions until their day has passed. Injects already delivered are not rewritten when the path changes.
- **The report records, it does not assess.** "Not recorded as done" means no record. Counterfactuals change one decision, use the central parameters and inherit every limit above.

## Legal references
- **Labels, not law.** The "Reference:" lines (`config/legal.yaml`, from `docs/LEGAL_REFERENCES.md`) only say where a concept sits in the BRRD or the DGSD. The tool models none of the legal conditions: not the conditions for early intervention or resolution, not the public interest assessment, not the minimum contribution before the resolution financing arrangement, not "no creditor worse off", not the deposit coverage level or the repayment period. Several references are marked "to be confirmed": they have not been re-read in the Official Journal text and need a practitioner's check. Emergency liquidity assistance has no article: it is a central bank matter. Not legal advice and not a statement of any authority's duties.

## Replies drafted by AI (off as shipped; prepared, not tested with a real model)
- **Prepared, not tested with a real model.** Everything was built and tested with a fixed reply that stands in for the model: the material given to the model, the number check, the approval step, the log and the report. No call to a real model has been made, so nothing is known about the quality of a real draft, how often it would fail the number check, what it costs, or whether the request as written is accepted by the provider.
- **A draft is a draft.** The check refuses a text with a number that was not given to the model; it does not check that the words are true, in role or wise. A person reads every draft before it is sent (Handbook, Annex 6, par. 15-16).
- **One request and one reply**, no memory of earlier requests. In a group or a system the request goes to a stakeholder of one entity and is grounded in that entity's figures only. Valuer and buyers hold figures only once FoLtF is in effect; the finance minister holds none.
- **The request is typed by the control team**: the tool cannot stop a real name or real data being typed into it. Whether an authority may send even fictitious exercise text to an outside service is its own decision.
- **Prices in `config/ai.yaml` are not verified** against the provider's price list; the cost shown rests on them.

## Areas of the Handbook's Annex 2 outside the tool
Annex 2 (EBA/REP/2025/20) lists areas a resolution authority could test. The tool gives figures and injects for a few of them and plays none of the work itself.

| Annex 2 area | In the tool |
|---|---|
| Internal preparation (confidentiality protocols, insiders list) | No |
| Hiring legal or strategic advisers and a valuer | No. A valuer is played by the control team |
| Request for access to the entity's information | No |
| Analysis of potential bidders and sales structures | No. One inject: a buyer shows interest, or all withdraw |
| Coordination with competition, market, foreign-investment and non-banking supervisors | No. Only the four authorities of the script |
| Feedback on market interest | No |
| Valuation report: request, preparation, assessment | Partly: a provisional and an updated valuation as figures |
| Setting up a virtual data room | No |
| Receiving binding offers | No |
| Analysing resolution conditions, with or without State aid | Partly: the tool decision and its figures; no public interest assessment, no State aid |
| Drafting the resolution decision; consultation in a resolution college | No |
| Write-down or conversion of capital instruments and bail-in of senior debt | Partly: amounts by layer; no execution |
| Sale of business by buy-side and sell-side teams | No |
| Approving a bridge institution | No |
| Results of the tool for shareholders; restructuring plan | No |

The decisions the tool does script (FoLtF determination, ELA, early intervention, moratorium, payout preparation and payout) are not in Annex 2, which covers the resolution authority's own tasks.
