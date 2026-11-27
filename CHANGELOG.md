# Changelog

## v1.0 (2026-11-27)

First public release. A prototype from a university project (ESCP Business School, MiM), released for anyone running crisis simulation exercises between authorities.

**Model**
- A fictitious bank simulated day by day over 30 days: deposit run with a panic that can rise and fall, wholesale roll-over, a funding waterfall (cash, liquid assets, central bank funding, emergency liquidity assistance, fire sales), weekends and payment outages, stages from recovery to failing or likely to fail.
- Four archetypes (two calibrated on the EBA 2025 EU-wide Transparency Exercise) and five trigger scenarios placed on the Handbook's taxonomy.
- Decisions of every authority as data, with an effect on the run: early intervention, FoLtF, emergency liquidity, moratorium, resolution tool, deposit guarantee payout.
- Resolution figures: losses, recapitalisation need, the liability stack, liquidation and payout.
- Sensitivity ranges, reverse mode (from a target outcome to the parameters) and design by objective, with the outcome ranking as a setting.

**Three kinds of exercise**
- Single bank.
- Cross-border group: parent and subsidiary in two countries, intragroup liquidity, host ring-fencing, the authorities of both countries as a college, a joint decision of the college.
- System-wide: two unrelated banks of one country under stress at once, linked by confidence; an orderly resolution and a default each send a shock to the other bank.

**Exercise materials and live exercise**
- Exercise packs in Word and CSV: mock bank dossier, rules of play, inject sheets per team (only the figures that team holds), main events list, role cards, session plan, charter draft, control team pack, observer sheets, feedback forms. Every document carries the SIMULATION label and a cover with a summary.
- A live console with a simulated clock, a page per team, decisions recorded as taken, ad hoc injects, undo, save and reload; a draft closing report with counterfactuals.
- Legal references for orientation (BRRD, DGSD) from one file, with a switch.
- Replies to teams' requests drafted by a general-purpose AI model: off as shipped, prepared but not tested with a real model.
- A home page, a demo tour, an exercise already played, a reset; runs offline.

**Known limits**: see `docs/LIMITS.md`. Texts not reviewed by a practitioner; legal references partly to be confirmed.
