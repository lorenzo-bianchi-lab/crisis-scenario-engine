# PLAN - Phase K: replies to player requests, drafted by a general-purpose AI

No code yet: this plan is for review. Phase J stays out.

## What Annex 6 of the Handbook says
Read in the Handbook (EBA/REP/2025/20, Annex 6, "Using GPAI in SimEx", pages 51-53).

Use cases it lists:
- brainstorming the design of a simulation (par. 3-4);
- creating resources: narrative, bank description, injects (par. 2, 5);
- **during the simulation, dynamic answers to player requests** (par. 6): if players ask the
  control team for input that one of the simulated entities should give, the control team can
  prompt the tool with the request and ask for an answer to give to the players;
- content in picture, audio or video form (par. 7); synthetic data (par. 8);
- after-action insights: flagging no action or long delays, mapping interactions, summaries
  for the closing report (par. 9).

Limitations and caveats it lists:
- respect the rules in force on such tools; with several authorities in one simulation,
  follow the strictest (par. 11);
- confidential or personal input only in line with the authority's own governance, security
  and IT measures (par. 12);
- no real data unless public, or unless all input stays within the authority's domain (par. 13);
- no personal or confidential information in prompts: no names of participants, no emails,
  no real institution names (par. 14);
- a person always checks the output and proposes it only if satisfied; results may be
  inaccurate or unexplainable; made-up content must be clearly not a real event (par. 15-16).

Phase K is the use case of par. 6 and nothing else. The Annex does not say how to build it.

## What would be built
A player team asks a simulated stakeholder something the script did not foresee ("What is
your cash position now?", to bank management). Today the control team writes a free-text ad
hoc inject by hand, marked "figures not checked". Phase K drafts that reply.

1. **Request**: on the console, the control team picks the asking team, the stakeholder
   asked (one of the role cards: bank management, valuer, media, finance minister, buyer),
   and types the question.
2. **Grounding**: the engine builds the only material the model gets:
   - the role card of that stakeholder at the current stage (knows, wants, says);
   - the figures that stakeholder holds at this moment, from its information set, each with
     its day: the same figures the engine already puts on that role's card;
   - the simulated day and time, the bank's fictitious name, the question.
   Nothing else: no other actor's figures, no future day, no decision not yet taken, no
   branch outcome, no name of a participant, no free text from earlier in the exercise.
3. **Draft**: one call to the model, asking for a short reply in the stakeholder's voice
   that uses only the figures supplied and says "I do not have that" for anything else.
4. **Check by the engine, before a person sees it**: every number in the draft must be one
   of the figures supplied (same rule as the test on players' pages). A draft that fails is
   shown as failed, with the offending numbers, and cannot be sent as it is.
5. **Approval**: the control team reads the draft next to the figures supplied, edits it or
   discards it, and sends it. Nothing reaches a player without that click. The inject is
   an ordinary ad hoc inject, logged as "drafted by AI, approved by the control team" with
   the question, the draft and the text sent; the closing report lists them.
6. **Label**: the reply carries the SIMULATION label like every inject.

## Off by default, and the app without it
- A setting `ai_replies` in config, `false` as shipped. With it off, or with no key, the
  console shows the hand-written ad hoc inject only, as today, and no call is ever made.
- No test needs the network: the model call sits behind one function, replaced in tests by
  a fixed reply. The demo runs offline with the feature off.
- One new dependency (the model provider's Python client), imported only when the feature
  is on.

## Where it stands against the Annex
- par. 13-14: the prompt holds fictitious figures and fictitious names only. The question is
  typed by the control team, so a real name could be typed by mistake: a notice says not to,
  and the question is refused if it contains the name of a saved team member (none are
  stored today) - in practice this rests on the control team.
- par. 11-12: whether an authority may send even fictitious exercise text to an outside
  service is its own decision. The plan cannot settle it; the README would say so.
- par. 15-16: the approval step, the number check, the label.

## What I need from you
1. **Go or no-go** on sending fictitious exercise text to an outside model. If no, phase K
   stops here.
2. **An API key** for the model provider, set by you as an environment variable on the
   machine that runs the app (I do not handle or store keys; the key never goes in the repo
   or in a saved exercise). Without one I can build and test everything except a real call.
3. **Which provider and model.** My default: Anthropic's API with a current Claude model;
   to be confirmed with you before any call, with the cost of a reply stated.
4. **Which stakeholders** may be asked: all five role cards (my default), or bank management only.
5. **Language**: English only (my default, as the rest of the tool).

## Acceptance proposed
- With the setting off: no change anywhere, and a test that no call can be made.
- With it on and the fixed reply: a draft appears, a draft with a number not supplied is
  refused, nothing is sent without approval, the log and the closing report record it.
- One real call shown to you, with the prompt as sent, before the phase is called done.

## Not in this plan
Generated narrative, injects or bank descriptions (par. 3-5); pictures, audio, video (par. 7);
synthetic data (par. 8); summaries of the exercise by AI (par. 9); any reply sent without a
person's approval.
