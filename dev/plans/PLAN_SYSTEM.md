# PLAN - System-wide variant (SPEC_V3 phase F, second part)

Two unrelated banks under stress at once, where an action on the first bank changes the
second bank's path. Built on the group layer of phase F. Defaults below are mine, as asked;
each is marked so it can be overruled.

## What the Handbook says
EBA/REP/2025/20, par. 115(b), read in the Handbook: systemic relevance is one of the
elements of the problem to be solved: "system-wide issue (affecting several entities with
bank runs resulting from direct or indirect contagion, and if the players may need to
resolve several banks failing at the same time) or idiosyncratic". Nothing more is said on
how to build one. Everything else here is an assumption of the tool.

## Defaults taken
1. **One country, one set of authorities.** The Handbook's wording is that *the players* may
   have to resolve several banks at the same time, so the four player teams handle both
   banks. (Alternative not built: two countries, as in the cross-border group.)
2. **No link but confidence.** The two banks lend nothing to each other. A stage reached by
   one (recovery, early intervention, payments default) is a panic shock for the other the
   next day: the contagion shocks already in `config/model.yaml`.
3. **Strength of the link from the parameters that exist**: `contagion_similarity_same_country`
   (0.5) plus `contagion_similarity_same_archetype` (0.5) when the two banks are of the same
   archetype, as `engine/contagion.py` computes it. No new parameter.
4. **Both banks are under stress**: the first runs the chosen scenario, the second a scenario
   of its own, set per pair in config (default: slow burn). The first/second roles can be swapped.
5. **Aware of decisions** comes for free: each bank runs the ordinary daily loop with its own
   decision path, so a bank resolved before it defaults never sends the "failure" shock.
   An orderly resolution sends no shock of its own. This is a limit, to be stated in LIMITS.
6. **No joint decision with a model effect.** The record of a joint decision (item 2 of this
   round) is offered, named for one country.

## Feasibility, checked before writing this plan (current parameters)
First bank left to default, against first bank declared FoLtF early and bailed in:
- two online banks (link 1.0), fast run on the first, slow burn on the second: the second is
  judged likely to fail on day 12, 28.2% of deposits lost; against never, 22.3%;
- universal bank and specialised lender (link 0.5): the second reaches early intervention on
  day 24, 40.6% lost; against never, 33.6%.
So the acceptance run exists with no parameter changed.

## Build
- **Config** `config/systems.yaml`: pairs (name, country, first and second bank with archetype
  and name, scenario of the second bank, headlines), the showcase pair, and the texts for the
  teams. Two pairs shipped: two online banks; universal bank with specialised lender.
- **Engine**: a pair becomes a `Group` of kind "system" (no transfers, no ring-fencing, link
  computed), so `simulate_group`, the stepper and the live layer run it unchanged. CLI:
  `python -m engine.group --system [KEY] ...`.
- **Script**: one pack per bank, same four teams. No cross-border notification (the same
  authority already holds the other bank's file) and no consolidated figures. A team's
  briefing says it handles two banks. Control pack and data files worded for a system.
- **Design**: "Exercise on" gains "System-wide". Timeline of both banks, the map (two banks
  in one country, no arrow), the comparison of two decision paths of the first bank, the
  information map per bank. A decision path for each bank (the paths of `decisions.yaml`).
- **Console**: start on a pair; cockpit with both banks' stage; decision cards per bank; four
  team pages, each showing both banks' injects in time order, marked with the bank's name.
- **Closing report**: the system's report (joint decisions, what the model gives with the
  first bank's path changed, for both banks) and one report per bank.
- **Wording**: the group pages say parent, subsidiary, home, host, college. A small
  vocabulary per kind replaces these words; the cross-border pages must read as before.

## Tests
Acceptance run (both pairs); no transfer ever; link equals the similarity factor; identity
and no-negative checks on both banks every day; a decision on the first bank changes the
second bank's rows in a live exercise and equals the direct simulation; four team pages, each
with only its own injects from both banks; documents hold no "parent", "subsidiary", "home",
"host" or "college"; the cross-border group's tests unchanged; pages run.

## Not in this plan
More than two banks; a shared resource that runs short (deposit guarantee fund, central bank
capacity); a shock from an orderly resolution; the two-country reading.
