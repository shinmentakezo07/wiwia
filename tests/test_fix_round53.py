"""WorkBuddy sends the caller agent's reasoning effort, verbatim, uncapped.

Contract (UPDATE.md #54): whatever reasoning level the caller's agent client
expresses must reach the WorkBuddy upstream as the same ``reasoning_effort``
value — ``reasoning_effort`` (OpenAI Chat), ``reasoning.effort`` (Responses /
Codex CLI), or ``thinking.budget_tokens`` (Anthropic / Claude Code). No cap is
added anywhere on the path; the only permitted substitutions are:

* no caller preference at all → ``"max"`` (WorkBuddy's documented default);
* an Anthropic token budget → the *nearest* effort level (budget→level
  translation, never a clamp: a huge budget still maps to ``"xhigh"``);
* an explicit disable (``"none"``, budget 0, ``thinking: disabled``) →
  ``"none"``, never silently re-enabled.

These tests pin the contract end-to-end: real agent payloads through the wire
codecs into ``WorkBuddyAdapter.encode_request``, not hand-built IR.
"""

from __future__ import annotations

from wiwi.providers.workbuddy_adapter import WorkBuddyAdapter
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_responses as orx

_PARAMS = {"max_tokens": 10, "extra_body": {}, "drop_params": True}
_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max", "none")


def _wb_effort(body: dict, codec) -> str:
    req = codec.decode_request(body)
    return WorkBuddyAdapter().encode_request(req, "glm-5.3", _PARAMS)["reasoning_effort"]


def _chat_body(effort: str | None) -> dict:
    body = {"model": "glm-5.3", "messages": [{"role": "user", "content": "hi"}]}
    if effort is not None:
        body["reasoning_effort"] = effort
    return body


def _responses_body(effort: str | None) -> dict:
    body = {
        "model": "glm-5.3",
        "input": [{"type": "message", "role": "user",
                   "content": [{"type": "input_text", "text": "hi"}]}],
    }
    if effort is not None:
        body["reasoning"] = {"effort": effort}
    return body


def _anthropic_body(thinking: dict | None) -> dict:
    body = {"model": "glm-5.3", "max_tokens": 64000,
            "messages": [{"role": "user", "content": "hi"}]}
    if thinking is not None:
        body["thinking"] = thinking
    return body


# -- Codex CLI (/v1/responses): reasoning.effort passes through verbatim ------

def test_responses_effort_levels_pass_through_verbatim():
    for effort in _EFFORTS:
        assert _wb_effort(_responses_body(effort), orx) == effort


# -- OpenAI Chat (/v1/chat/completions): reasoning_effort verbatim ------------

def test_chat_effort_levels_pass_through_verbatim():
    for effort in _EFFORTS:
        assert _wb_effort(_chat_body(effort), oc) == effort


def test_chat_unknown_effort_not_filtered():
    """No whitelist on this path: a level WorkBuddy understands that the IR map
    does not know yet must still reach the upstream exactly as the agent sent
    it — WorkBuddy owns validation of its own reasoning field."""
    assert _wb_effort(_chat_body("deeper"), oc) == "deeper"


# -- Claude Code (/v1/messages): thinking budget → nearest level, no cap ------

def test_anthropic_budget_maps_to_nearest_level():
    assert _wb_effort(_anthropic_body(
        {"type": "enabled", "budget_tokens": 1024}), am) == "low"
    assert _wb_effort(_anthropic_body(
        {"type": "enabled", "budget_tokens": 8000}), am) == "medium"
    assert _wb_effort(_anthropic_body(
        {"type": "enabled", "budget_tokens": 32000}), am) == "high"
    assert _wb_effort(_anthropic_body(
        {"type": "enabled", "budget_tokens": 64000}), am) == "xhigh"


def test_anthropic_budget_is_never_capped():
    """A huge budget maps to the top level — never clamped down or rejected."""
    assert _wb_effort(_anthropic_body(
        {"type": "enabled", "budget_tokens": 999_999}), am) == "xhigh"


def test_anthropic_disabled_and_zero_stay_disabled():
    assert _wb_effort(_anthropic_body({"type": "disabled"}), am) == "none"
    assert _wb_effort(_anthropic_body(
        {"type": "enabled", "budget_tokens": 0}), am) == "none"


def test_anthropic_adaptive_defaults_to_max():
    """Adaptive carries no level of its own; WorkBuddy's default applies."""
    assert _wb_effort(_anthropic_body({"type": "adaptive"}), am) == "max"


# -- No client preference → WorkBuddy's documented "max" default --------------

def test_no_client_preference_defaults_to_max():
    for codec, body in ((oc, _chat_body(None)), (orx, _responses_body(None)),
                        (am, _anthropic_body(None))):
        assert _wb_effort(body, codec) == "max"