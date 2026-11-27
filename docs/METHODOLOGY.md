# METHODOLOGY

Crisis Scenario Engine: how the figures in an exercise are produced, what they rest on, and what they must not be used for. Written for a reader from a supervisory or resolution authority. It makes no claim beyond `docs/CALIBRATION.md`, `docs/LIMITS.md` and the automated tests; where those say "assumption", so does this.

## 1. Purpose and status

Crisis simulation exercises between authorities are part of how the EU resolution framework is meant to work: the BRRD foresees Union-wide exercises on the application of the BRRD, the SRMR and the DGSD in cross-border situations (BRRD Article 128c "Crisis management simulations"), and authorities also run exercises of their own. That is the context of the tool, not its purpose: it is a general aid for anyone preparing such an exercise.

The EBA Handbook on simulation exercises for resolution authorities (EBA/REP/2025/20) describes how to design, prepare, run and close an exercise. An exercise needs a scenario whose numbers hold together from one day to the next and from one actor to another: what the bank has lost, what it can still raise, who has been told, which decision is due and what follows from each option. Writing those numbers by hand is slow and they rarely stay consistent once a decision changes the story.

The tool produces that quantitative script for a fictitious bank, and the documents and console to run an exercise on it. It follows the Handbook's four phases: design (choose the bank, the scenario and the decisions to test), prepare (the exercise pack), deliver (a live console and a page per team) and close (a draft closing report).

It is a prototype built in a university project (ESCP Business School, MiM) and released publicly for anyone running crisis simulation exercises. It is **not** a supervisory model, a stress test, an early-warning system or a forecast. Every bank, country and name in it is fictitious. Its outputs describe what one set of stated assumptions implies; they say nothing about the likelihood of any event at any real institution.

Three rules shape everything below:

- **Every number is in a configuration file** with a value, a unit and a source. A value with no verifiable source is marked `ASSUMPTION`. The app lists all of them on its Assumptions page.
- **The model is deterministic.** The same inputs give the same script. The only random draws are in the sensitivity ranges, with a fixed seed.
- **Model and presentation are separate.** The engine is plain Python with no user interface in it; the app and the documents only display what the engine returns.

## 2. The bank

A bank is a simplified balance sheet at the start of day 0:

- **Assets**: cash and central bank reserves; high-quality liquid assets, Level 1 and Level 2; other collateral eligible at the central bank; illiquid assets (loans).
- **Liabilities**: deposits in four categories (insured retail, uninsured retail, corporate, financial); wholesale funding with a maturity ladder; other liabilities.
- **Capital and loss absorption**: CET1, risk-weighted assets, a capital requirement, and a liability stack (CET1, AT1 and Tier 2, senior non-preferred, senior preferred, uncovered deposits, covered deposits) with an MREL requirement.

Four archetypes are shipped: a large universal bank, a mid-size retail bank, a digital deposit bank and a specialised lender with concentrated uninsured deposits.

**What is sourced.** For the universal and retail archetypes, the shares of total assets of cash, deposits by counterparty, debt securities issued, CET1 and risk exposure are the medians of a peer group in the EBA's 2025 EU-wide Transparency Exercise, June 2025 data (28 banks with assets of EUR 250 bn or more; 40 banks below that with household deposits of at least 40% of assets). `docs/CALIBRATION.md` gives the file, the item codes and the script that reproduces the figures; a test compares the configuration with the script's output when the raw file is present.

**What is assumed.** The liquid asset lines, the split of retail deposits into insured and uninsured, the wholesale maturity ladder, the capital requirement, the liability stack, the size of every archetype, and the digital and specialised archetypes entirely: the data used has no field for any of these.

## 3. The daily loop

The horizon is 30 days in daily steps. Each day has four parts.

### 3.1 Run-off

For each deposit category the outflow is a base daily rate, times the scenario's severity, times the archetype's speed factor, times a panic multiplier. Only a "flight-prone" share of each category's opening stock can leave (20% of insured retail deposits, 100% of financial deposits, in the shipped configuration); the rest is a stable core.

The panic multiplier is a logistic curve of a panic input `x`:

    multiplier = 1 + (max - 1) x logistic(steepness x (x - midpoint))

`x` is a fading memory: each day it loses a fixed share, then the day's deposit loss (as a share of opening deposits) and any news shock are added. News can be bad or reassuring. So a run can feed itself, and it can also die down: panic falls when outflows slow and nothing new happens.

Maturing wholesale funding rolls over in a share that falls as panic rises.

All of these values are assumptions: the base rates, the flight-prone shares, the curve's three parameters, the decay, the roll-over sensitivity. Run-off speed is the weakest part of any such model; section 7 says how the tool treats that.

### 3.2 Funding the outflow

The day's outflow is met in a fixed order:

1. cash and reserves;
2. sale of liquid assets, at a haircut that is booked as a loss;
3. ordinary central bank funding against eligible collateral, at a haircut, available after a mobilisation lag;
4. emergency liquidity assistance, if the central bank has granted it: against the remaining collateral and a share of the loan book, at a higher haircut, up to a cap if one was set;
5. sale of loans at a discount, limited to a share of the book per day; the loss reduces CET1.

What cannot be met is unpaid.

### 3.3 Calendar

Day 1 has a weekday. On weekend days there are no market sales and no central bank drawings, wholesale maturities move to Monday, and withdrawals are requested at a reduced rate and paid from cash; what is not paid waits for Monday. Nobody defaults on a weekend. A payment outage (the cyber scenario) queues withdrawals and releases the queue when service returns. Times of day in the script are labels: the outflow "by 12:00" is a fixed allocation of the day's figure, not an intraday model.

### 3.4 Indicators and stages

Each day the model gives the liquidity buffer, an LCR proxy, the survival horizon in days at the current outflow, cumulative deposit loss, the CET1 ratio and its projection, and unencumbered collateral. The LCR proxy uses assumed outflow rates and is not the regulatory ratio.

A bank moves through stages that never go back:

- **recovery**: the LCR proxy is below 100% or 5% of deposits are lost;
- **early intervention**: the LCR proxy is below 75%, or CET1 is within 1.5 points of the requirement;
- **likely to fail**: the survival horizon is below 3 days, or CET1 is projected below the requirement. This is a forward-looking flag raised by the model. It is not a determination: declaring a bank failing or likely to fail is a decision of the competent authority's team (section 4);
- **payments default**: outflows cannot be paid. The run stops.

The thresholds are assumptions set in the configuration. They are not the legal conditions for early intervention or for failing or likely to fail.

### 3.5 What always holds

Four properties are enforced by tests on every archetype and scenario: assets equal liabilities plus equity on every simulated day; no balance is negative and no outflow exceeds the remaining stock; with zero run-off nothing changes over time; a higher severity never produces a later default. A fingerprint of 216 runs is stored, so that a change to the engine that alters any figure fails the tests until it is acknowledged.

## 4. Decisions

A script in which nothing the players do matters is a film, not an exercise. Decisions are data in `config/decisions.yaml`; each has an actor, a condition for being open, options, and an effect on the model.

| Actor | Decision | Effect in the model |
|---|---|---|
| Bank management | Public statement | A reassuring or a backfiring panic shock |
| Bank management | Recovery options | Sale of a portfolio: liquidity in, loss booked |
| Competent authority | Early intervention measures | Faster reporting, or the recovery options are activated |
| Competent authority | Declare failing or likely to fail | Opens the resolution decisions |
| Central bank | Emergency liquidity assistance | Refused, granted with a cap, granted in full |
| Resolution authority | Moratorium | Outflows suspended for one or two business days |
| Resolution authority | Resolution tool | Bail-in, sale of business, or liquidation |
| Deposit guarantee scheme | Prepare payout; pay out | Shortens the days until covered depositors are paid |

A decision takes effect on the next simulated day. A set of decisions with their days is a path; two paths on the same bank and scenario are two branches of the script. In a **dynamic** exercise the players' decisions change the path; in a **linear** one they are recorded and the path stays as planned (Handbook, par. 124-126).

The effect of each decision is tested: the same bank on the same scenario ends differently on different paths. For the universal bank in the fast run, current configuration:

| Path | Outcome | Last day | Deposits lost |
|---|---|---|---|
| Authorities act late; or emergency liquidity refused | Payments default | 5 | 57.1% |
| Emergency liquidity granted | Stabilised | 30 | 63.2% |
| Failing or likely to fail declared on day 1, moratorium, bail-in | Resolution: bail-in | 4 | 15.2% |

Read the second row with care: "stabilised" is the model's word for outflows that have fallen below a threshold for three days, here after the bank has lost most of its deposits and is funded by the central bank.

## 5. Resolution figures

When a tool is applied the model computes, from the liability stack: losses to date; an assumed write-down of the loan book (low, central, high), standing in for a valuation; the amount needed to restore the capital requirement; what each layer of the stack bears, in order; any shortfall; the liquidity needed for the following days; and, in liquidation, the covered deposits to be paid out and a waterfall on assumed asset values.

These are arithmetic on assumptions, useful to give the resolution authority's team figures to argue over. The order of loss absorption is the model's and not a statement of law. Legal thresholds, resolution financing arrangements, State aid, a valuation methodology and the "no creditor worse off" comparison are not modelled. Sale of business is computed like bail-in, with an assumed price that changes nothing else. The run stops when the tool is applied.

## 6. Scenarios

A scenario is a severity (with a low and a high value), dated news events with their panic shocks, a start weekday and, where needed, overrides of model parameters. Five are shipped, placed on the Handbook's taxonomy (par. 115): a fast digital run, a slow burn, a cyber outage, a geopolitical shock with market closure, and a loss revealed (solvency-led).

With no decision taken ("authorities act late"), central values:

| Scenario | Bank | Likely to fail (day) | Payments default (day) | Deposits lost |
|---|---|---|---|---|
| Fast digital run | Universal | 1 | 5 | 57.1% |
| | Retail | 1 | 5 | 40.7% |
| | Digital | 1 | 2 | 28.8% |
| | Specialised | 1 | 4 | 62.0% |
| Slow burn | Universal | - | - | 24.5% |
| | Retail | - | - | 13.5% |
| | Digital | - | - | 17.8% |
| | Specialised | - | - | 29.4% |
| Cyber outage | Universal | 4 | - | 27.7% |
| | Retail | - | - | 14.1% |
| | Digital | 4 | - | 19.4% |
| | Specialised | - | - | 33.5% |
| Geopolitical shock | Universal | 3 | - | 57.9% |
| | Retail | 6 | - | 39.0% |
| | Digital | 3 | 9 | 35.8% |
| | Specialised | 3 | 10 | 61.6% |
| Loss revealed | Universal | 3 | - | 48.3% |
| | Retail | 3 | - | 28.0% |
| | Digital | 3 | - | 33.8% |
| | Specialised | 11 | - | 59.6% |

Six of twenty pairs end in a payments default within 30 days; the others are still paying on day 30, several of them after losing a large share of deposits and being flagged as likely to fail. A bank that "survives" in this table is not a healthy bank.

## 7. How far the numbers can be trusted

### 7.1 Comparison with documented runs

The speed of deposit loss was compared with three cases, using only figures read in primary documents (reviews by the Federal Reserve and its Inspector General, a statement of the FDIC Chairman, FINMA's report on Credit Suisse); `docs/CALIBRATION.md` lists each figure with its page.

- **Fast run.** The universal and specialised archetypes lose 15-16% of deposits on day 1 and 38-43% by day 2. First Republic lost about 17% in one day and about 44% over two business days; Silicon Valley Bank about 25% in one day. The model's fast run is of the same order as the first and slower than the second. It was not tuned to either.
- **Slow burn.** The universal archetype loses 24% in 30 days; Credit Suisse lost about 25% of deposits in its worst month. This match was obtained by setting the scenario's severity on that one figure. It is an order-of-magnitude check, not a calibration.

Limits of the comparison: three cases, two of them in one week and one country, with very high shares of uninsured deposits; the real figures mix withdrawals, requests and net outflows; the model stops at the default, real banks kept losing deposits until an authority acted. Two further cases were examined and left out because no outflow figure was found in a primary document.

### 7.2 Sensitivity ranges

Because run-off parameters cannot be pinned down, the key ones carry a low and a high value, and the tool can run 500 draws within those intervals and show ranges instead of single numbers, for example the spread of the default day. The seed is fixed, so the result is reproducible.

**These ranges are not probabilities.** They show how much the result moves when assumed intervals are sampled uniformly. An interval that is itself an assumption cannot produce a likelihood.

### 7.3 Reverse mode and design by objective

Reverse mode starts from the exercise the designer wants ("likely to fail on day 5, a Friday, with 20% to 30% of deposits lost") and searches severity, the size of news shocks and the run-off speed, within their ranges, for a parameter set that gives it; it reports the gap if none does, and how far each solved value is from its central value. It returns one parameter set, not a likelihood.

Design by objective ranks decisions by how much the options differ and whether one option is better on every compared line. "Hardest" is a rule of the tool. The ranking of outcomes it uses is a setting in `config/script.yaml`, and an assumption. As shipped, from best to worst: the bank stays open and is not flagged as likely to fail on the last day; resolution or liquidation, the three tools on one rank; the bank is left open while still flagged as likely to fail on the last day; payments default. So an orderly resolution never ranks below a bank left open in a likely-to-fail state. A bank that stays open with the flag cleared still ranks above a resolution, even after losing a large share of its deposits; deposits lost is a compared line of its own. "Lower is preferred" on every line is also an assumption.

## 8. From numbers to an exercise

### 8.1 Who knows what, when

In a real crisis the actors do not hold the same figures at the same time. Each actor has an information set in the configuration: which figures, from whom, how old, and from which stage.

| Actor | Figures from | Age | From |
|---|---|---|---|
| Bank management (played by the control team) | its own treasury | same day, by time of day | the start |
| Competent authority | supervisory reporting | one day; same day once it requires daily reporting | the start |
| Central bank | its own payment and collateral records | same day | the start |
| Resolution authority | the competent authority | one day | early intervention |
| Deposit guarantee scheme | the resolution authority | one day | likely to fail |

A test enforces it: every number in a team's document, and on a team's page in a live exercise, is one that team holds at that time. The delays and scopes are assumptions, as is who is consulted and informed around each decision; none is a statement of legal duties.

### 8.2 The script

Each inject states the day, weekday and time; the recipient and the sender; the figures in the recipient's information set; and, where a decision is due, the options, the deadline, who should be consulted and informed, and where each option leads. Injects carry a purpose (advance, challenge, control: Handbook, par. 128). The pack contains, per team, the inject sheets and a briefing; for the control team, the main events list, role cards for five simulated stakeholders, the full-information timeline and the decision points; and a mock bank dossier, rules of play and a session plan. Every document is labelled SIMULATION (par. 174).

The wording of injects, role cards and expected responses is a draft by this project. It has not been reviewed by a practitioner and carries no legal references.

### 8.3 Live exercise and closing report

The console steps a simulated clock, releases each team's injects to its own page, and records decisions: who decided, when, how long it took in real time, who was consulted. In a dynamic exercise a recorded decision recomputes the path from that day on. The exercise is a set-up plus a log, so it can be replayed, undone step by step, saved and reloaded.

The closing report is a draft that records and does not assess: decisions against the pre-planned path, coordination steps recorded as done or not, and what the model gives with one decision changed and everything else as it happened. Lessons learned and recommendations are left blank for people to write.

There are no accounts and no security: the codes on team links prevent mistakes only.

## 9. Two banks

### 9.1 Cross-border group

A parent and a subsidiary in two countries, each with its own balance sheet and its own four authorities, run the daily loop in step. An entity that cannot fund its payments from cash, liquid assets and central bank funding gets cash from the other before it sells loans, within a limit on what the lender may have lent and above a floor on the lender's cash. The host competent authority can cap or stop the subsidiary's lending (ring-fencing) once it has been told of trouble at the parent. A stage reached by one entity is a panic shock for the other the next day. A lender writes off part of its claim if the borrower defaults.

In the showcase run (the geopolitical shock at the high end of that scenario's own severity range, starting on a Wednesday), the parent is still paying on day 30 with one transfer from the subsidiary on day 8; with a full ring-fence decided on the Monday after the weekend it defaults on its payments on day 10, a Friday.

The Handbook says a decision-making exercise should be used to train resolution colleges (par. 43) and does not define one. **The college in the tool is its own assumption**: who sits in it, who tells whom, how late. A team sees its own entity's figures and, of the other entity, only what a notification carries. A joint decision of the college can be recorded (who took part, agreed or not); it changes nothing in the model. Not modelled: a call for repayment, internal MREL, support in securities, more than two entities.

### 9.2 System-wide stress

The Handbook names a system-wide issue as one kind of problem to be solved: several entities affected, contagion, players who may have to resolve several banks at the same time (par. 115(b)). It does not say how to build one. In the tool it is two unrelated banks of one country, handled by the same four authorities. They lend nothing to each other. The only link is confidence: a stage reached by one is a panic shock for the other the next day, at a strength computed from two similarity parameters (same country; same archetype), all assumed.

Because each bank runs the ordinary loop with its own decisions, an action on one changes the other's path. A payments default sends the largest shock; a bank put in resolution or liquidation before it defaults sends a smaller one, since an orderly resolution is also news. With two online banks, if the first is left to default on its payments the second is flagged as likely to fail on day 12 and loses 28.2% of its deposits; if the first is declared failing or likely to fail early and bailed in, the second is flagged on day 22 and loses 25.7%.

One assumption decides how to read this: the shock of a resolution is set at half that of a payments default (0.06 against 0.12, both assumed). With it at zero the second bank is never flagged and loses 22.3%; with the two equal, resolving early would change nothing for the second bank. The result shows the mechanism the exercise is meant to make players discuss; it is not evidence about the size of the effect.

## 10. Legal references

Where a decision, a stage or a resolution figure appears in an inject, a control team document, the mock bank's liability stack, the closing report or a decision card of the console, a small "Reference:" line says where the concept sits in EU law (BRRD, DGSD). The references are a table compiled by the author (`docs/LEGAL_REFERENCES.md`), held in one file, `config/legal.yaml`, and nowhere else; a concept with no row gets no reference. References marked "to be confirmed" have not been re-read in the Official Journal text and must be confirmed by a practitioner. They are for orientation only: the tool does not model the legal conditions, and a reference next to a figure does not mean the figure meets the condition it names. A switch turns them off in the app and in the exported documents.

## 11. What the tool does not do

`docs/LIMITS.md` is the full list, kept in step with the code. The points a supervisory reader should hold on to:

- **Assumptions, not estimates.** Run-off rates, the panic curve, haircuts, thresholds, information delays and every contagion and group parameter are assumptions unless the Assumptions page gives a source.
- **Liquidity first.** Capital moves only through losses on sales and a loss revealed by a news event. No credit risk model, earnings, market risk, interest-rate risk or derivatives.
- **Simple funding.** One wholesale ladder, four collateral classes with fixed haircuts, one central bank facility and emergency liquidity. No repo market, no collateral calls, no payment systems, no intraday liquidity.
- **One currency, whole days.**
- **Nothing after resolution.** No market reaction, no reopening, no restructuring.
- **Two banks at most**, linked by lending inside a group or by confidence. No shared resource that runs short.
- **One optional use of a general-purpose AI**, off as shipped and prepared, not tested with a real model: it was built and tested with a fixed reply in the model's place, and no real call has been made. It is for drafting a simulated stakeholder's reply to a player's request, from that stakeholder's role card and figures only, checked for numbers and sent only by a person (Handbook, Annex 6, par. 6 and 11-16). No figure, inject or document of the tool is generated by AI.
- **The exercise materials are drafts.** Wording and coordination steps need a practitioner's review.
- **Most of the Handbook's Annex 2** (areas a resolution authority could test, such as the data room, bidders, the valuation process, the drafting of the decision) is outside the tool; `docs/LIMITS.md` has the table.

## 12. Verification and reproduction

- 1,959 automated tests cover the engine, the script, the documents and the pages. Among them: the four properties of section 3.5; the effect of each decision; the ordering of the liability stack; the weekend rules; the information rule of section 8.1 on every team's document and page; the equality of a replayed, a saved and a directly simulated exercise; the stored fingerprint of 216 runs.
- The Word documents are generated and their content and structure (page number fields, cover block, column widths, landscape pages) are checked by the tests. The author opened them in Word: they render correctly.
- Everything can be reproduced from the repository: `./start.sh` for the app, `pytest -q` for the tests, `python -m engine.run --showcase` for the single-bank showcase, `python -m engine.group --showcase` and `python -m engine.group --system` for the two-bank runs, and `scripts/calibrate_te2025.py` for the calibration once the public data file is in `data/raw/`.
- Sources: the EBA Handbook (EBA/REP/2025/20) for the structure of an exercise and its vocabulary; the EBA 2025 EU-wide Transparency Exercise for two archetypes' proportions; four public reports for the comparison of section 7.1; the table of legal references compiled by the author (`docs/LEGAL_REFERENCES.md`), part of it still to be confirmed. Nothing else in the tool is sourced.
