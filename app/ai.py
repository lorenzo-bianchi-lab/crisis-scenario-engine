"""The call to the model that drafts a reply to a player's request (SPEC_V3 phase K).

The only place of the tool that can reach an outside service, and only when
config/ai.yaml says `enabled: true` and `provider: anthropic`. What is sent is built by
engine/replies.py; the key is read from the environment by the provider's client and is
never stored, logged or shown by the tool.

    python -m app.ai --question "What is your cash position now?"

prints the prompt as it would be sent; with --send it makes one real call and prints the
reply, the tokens used and the cost.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from typing import Optional, Sequence

from engine import replies
from engine.schema import AiConfig, Config

KEY_VARIABLE = "ANTHROPIC_API_KEY"
# Beta of the provider that lets another of its models answer if the first declines.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AiUnavailable(Exception):
    """The feature is off, or the call could not be made: the reason is the message."""


@dataclass(frozen=True)
class Raw:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    note: str = ""


def enabled(config: Config) -> bool:
    return config.ai is not None and config.ai.enabled


def has_key() -> bool:
    return bool(os.environ.get(KEY_VARIABLE))


def _call_anthropic(system: str, user: str, settings: AiConfig) -> Raw:
    """One request to the provider. Thinking is left to the model; effort keeps it short."""
    import anthropic  # here: the tool runs without this package when the feature is off

    if not has_key():
        raise AiUnavailable(
            f"No key: the environment variable {KEY_VARIABLE} is not set where the app was started.")
    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=settings.model, max_tokens=settings.max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": settings.effort},
            betas=[FALLBACK_BETA], fallbacks="default",
        )
    except anthropic.AuthenticationError as error:
        raise AiUnavailable("The provider refused the key.") from error
    except anthropic.RateLimitError as error:
        raise AiUnavailable("The provider's rate limit was reached: try again later.") from error
    except anthropic.APIStatusError as error:
        raise AiUnavailable(f"The provider returned an error ({error.status_code}): {error.message}") from error
    except anthropic.APIConnectionError as error:
        raise AiUnavailable("No connection to the provider.") from error
    text = "\n".join(block.text for block in response.content if block.type == "text")
    note = ""
    if response.stop_reason == "refusal":
        note = "The model declined to answer: write the reply by hand."
    elif response.stop_reason == "max_tokens":
        note = "The reply was cut at the token limit."
    return Raw(text, response.model, response.usage.input_tokens, response.usage.output_tokens, note)


# Replaced in the tests, which never reach the network.
CALL = _call_anthropic


def draft(material: replies.Grounding, config: Config) -> replies.Draft:
    """A checked draft for a request. Raises AiUnavailable if the feature is off or the call
    fails; never called when `enabled` is false."""
    settings = config.ai
    if settings is None or not settings.enabled:
        raise AiUnavailable("AI replies are off (config/ai.yaml).")
    if settings.provider == "fixed":
        return replies.checked(replies.fixed_reply(material), material, settings, model="fixed reply")
    system, user = replies.prompt(material, settings)
    raw = CALL(system, user, settings)
    return replies.checked(raw.text, material, settings, raw.model, raw.input_tokens,
                           raw.output_tokens, raw.note)


def showcase_at_close(config: Config, day: int):
    """The showcase exercise stepped to the close of a simulated day, with no decision
    recorded: a moment to ask a stakeholder something."""
    from engine import live
    from engine.decisions import choices_of

    context = live.Context(config.model, config.decisions, config.script)
    show = config.script.showcase
    setup = live.make_setup("Showcase", config.banks[show.bank], config.model,
                            config.scenarios[show.scenario], config.decisions, config.script,
                            choices_of(config.decisions.paths[show.path]))
    state = live.start(setup, "2028-03-01T09:00:00", context)
    while not state.ended and not (state.day >= day and state.time >= live.CLOSE):
        state = live.apply(state, "step", "2028-03-01T09:00:00", context)
    return state, context


def main(argv: Optional[Sequence[str]] = None) -> int:
    from engine.config import load_config

    parser = argparse.ArgumentParser(description="Show, and with --send make, one AI-drafted reply.")
    parser.add_argument("--question", default="What is your cash position this evening, and how long can you keep paying?")
    parser.add_argument("--stakeholder", default="bank_management")
    parser.add_argument("--asked-by", default="competent_authority")
    parser.add_argument("--day", type=int, default=6, help="simulated day, at its close")
    parser.add_argument("--send", action="store_true", help="make one real call (it is billed)")
    args = parser.parse_args(argv)
    config = load_config()
    settings = config.ai.model_copy(update={"enabled": True, "provider": "anthropic"})
    state, context = showcase_at_close(config, args.day)
    material = replies.grounding(state, context, args.stakeholder, args.asked_by, args.question)
    system, user = replies.prompt(material, settings)
    print("SIMULATION - exercise material, not a real event\n")
    print(f"MODEL: {settings.model}, effort {settings.effort}, at most {settings.max_tokens} output tokens\n")
    print("SYSTEM PROMPT, AS SENT\n" + system + "\n")
    print("USER MESSAGE, AS SENT\n" + user + "\n")
    if not args.send:
        print("Nothing was sent. Add --send to make one real call.")
        return 0
    try:
        result = draft(material, config.model_copy(update={"ai": settings}))
    except AiUnavailable as error:
        print(f"NOT SENT: {error}")
        return 1
    print("REPLY, AS RECEIVED\n" + result.text + "\n")
    print(f"Answered by: {result.model}. {result.note}".strip())
    print(f"Tokens: {result.input_tokens} in, {result.output_tokens} out. "
          f"Cost at the configured prices: USD {result.cost:.4f}")
    print("Number check: " + ("passed" if result.ok else f"FAILED, not supplied: {', '.join(result.offending)}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
