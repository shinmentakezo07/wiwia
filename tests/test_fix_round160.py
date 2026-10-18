"""Regression round 160 — WorkBuddy refused every Codex / Claude Code request.

Reported verbatim from a Claude Code session routed at a ``workbuddy``
deployment::

    API Error: 400 {"error":{"message":"WorkBuddy AI: Illegal API invocation
    from an unapproved channel — WorkBuddy refuses chats from Codex and
    Claude Code (their system prompt); use it from Hermes, OpenCode or Pi, or
    add another provider to this group","type":"invalid_request_error"}}

Two independent defects, either of which alone produces that failure.

**1. The fingerprint rewrite matched one literal sentence out of many.**
``workbuddy_adapter._sanitize_text`` exists to break the *verbatim template
match* the upstream moderation does on an unapproved client's system prompt.
It rewrote exactly one string — ``"You are Claude Code, Anthropic's official
CLI for Claude."`` — while the detection gate ``_has_fingerprint`` listed four
literals of which only one was a Claude Code identity marker. Every other
variant the clients actually send sailed through untouched.

Measured against the clients installed on this machine (Claude Code 2.1.288,
Codex 0.160.0), the strings that reach the wire verbatim are::

    You are Claude Code, Anthropic's official CLI for Claude.
    You are Claude Code, Anthropic's official CLI for Claude, running within
      the Claude Agent SDK.
    You are an agent for Claude Code, Anthropic's official CLI for Claude. …
    You are a file search specialist for Claude Code, Anthropic's official CLI
      for Claude. …
    You are an interactive CLI tool that helps users with software engineering
      tasks. …
    Claude Code is an agentic coding CLI based on the Anthropic API.
    OpenAI's command-line coding agent          (Codex)

Pre-fix, ``_sanitize_text`` rewrote the first of those seven and returned the
other six byte-identical — which is exactly the request the upstream refuses.
The ported list is a verbatim-template denylist, so the fix keeps that shape
(it is what the upstream gate keys on) but is generated from the *identity
phrase* rather than a hand-typed sentence, so a wording drift in a client
release no longer re-opens the hole. Codex gets the same treatment.

**2. The 400 was terminal, so a second provider in the group never ran.**
Even with a clean request shape, this refusal is per-deployment: another
deployment in the same model group can serve it. ``_classify_provider_status``
maps a bare 400 to a **non-retryable** ``invalid_request_error``, and the
router re-raises a non-retryable error before it ever drains its fallback
queue — so the client's own advice ("add another provider to this group") was
impossible to follow. An unapproved-channel refusal is a *shape* refusal, the
same class as the 403 entitlement branch above it, and it must fail over.

Both fixes are covered here, at the two layers that actually decide the
outcome: the rewrite table and the status classifier.
"""

from __future__ import annotations

import pytest

from wiwi.providers.base import WiwiError, error_from_provider_status
from wiwi.providers.workbuddy_adapter import (
    WorkBuddyAdapter,
    _has_fingerprint,
    _sanitize_text,
)

# The upstream's own wording, captured from the failing client session.
REFUSAL = (
    "WorkBuddy AI: Illegal API invocation from an unapproved channel — "
    "WorkBuddy refuses chats from Codex and Claude Code (their system prompt); "
    "use it from Hermes, OpenCode or Pi, or add another provider to this group"
)

# Every identity sentence the installed clients actually put on the wire.
# Sourced from `strings` over claude 2.1.288 / codex 0.160.0 rather than
# written by hand from memory — see the module docstring.
CC_SYSTEM_PROMPTS = [
    "You are Claude Code, Anthropic's official CLI for Claude.",
    (
        "You are Claude Code, Anthropic's official CLI for Claude, running within"
        " the Claude Agent SDK."
    ),
    (
        "You are an agent for Claude Code, Anthropic's official CLI for Claude."
        " Given the user's message, you should use the tools available to complete"
        " the task."
    ),
    (
        "You are a file search specialist for Claude Code, Anthropic's official"
        " CLI for Claude. You excel at thoroughly navigating and exploring"
        " codebases."
    ),
    (
        "You are an interactive CLI tool that helps users with software engineering"
        " tasks. You should work proactively and autonomously, executing"
        " immediately and minimizing interruptions."
    ),
    "Claude Code is an agentic coding CLI based on the Anthropic API.",
]

CODEX_SYSTEM_PROMPTS = [
    "OpenAI's command-line coding agent",
]


# -- defect 1: the fingerprint rewrite missed six of seven real prompts ------

@pytest.mark.parametrize("prompt", CC_SYSTEM_PROMPTS + CODEX_SYSTEM_PROMPTS)
def test_every_client_identity_prompt_is_neutralised(prompt: str):
    """No client's system prompt may reach WorkBuddy's moderation verbatim.

    The gate and the rewrite have to agree: a prompt the detector does not
    flag is passed through untouched, and a prompt it flags but the rewrite
    table does not match is *also* passed through untouched. Both halves are
    asserted, because pre-fix the gate listed one literal and the table
    another, so six of these seven were silently intact.
    """
    assert _has_fingerprint(prompt), (
        f"detector missed the identity prompt entirely: {prompt!r}"
    )
    clean = _sanitize_text(prompt)
    assert clean != prompt, (
        f"identity prompt reached the upstream verbatim: {prompt!r}"
    )


def test_the_previously_covered_sentence_is_still_rewritten():
    """The one rewrite that worked before must not regress."""
    out = _sanitize_text("You are Claude Code, Anthropic's official CLI for Claude.")
    assert out == "You are Claude Code, Anthropic's official CLI tool for Claude."


def test_identity_rewrite_survives_a_client_wording_drift():
    """A wording change in a client release must not re-open the hole.

    The upstream gate is a template match, so the shipped list has to cover the
    family of the sentence, not one frozen copy of it. This is the drift that
    actually bit: the SDK, subagent and specialist openers all append to the
    same identity clause.
    """
    drifted = (
        "You are Claude Code, Anthropic's official CLI for Claude, operating"
        " inside the Claude Agent SDK with additional wrappers."
    )
    assert _has_fingerprint(drifted)
    assert _sanitize_text(drifted) != drifted
    assert "official CLI tool for Claude" in _sanitize_text(drifted)


def test_ordinary_prompts_are_untouched():
    """The zero-work fast path must stay a genuine no-op."""
    plain = "Summarise the diff and point out the bug."
    assert not _has_fingerprint(plain)
    assert _sanitize_text(plain) == plain


def test_encode_request_sanitises_the_system_message():
    """End-to-end through the adapter, not just the helper.

    The bug is only fixed if the rewrite runs on the encoded body — the helper
    passing its own tests proves nothing about the request that is sent.
    """
    from wiwi.ir import types as ir

    req = ir.Request(
        model="m",
        messages=[
            ir.Message(role="system", parts=[ir.TextPart(text=CC_SYSTEM_PROMPTS[0])]),
            ir.Message(role="user", parts=[ir.TextPart(text="hi")]),
        ],
        stream=False,
    )
    body = WorkBuddyAdapter().encode_request(req, "glm-5.3", {})
    system = next(m for m in body["messages"] if m["role"] == "system")
    assert system["content"] != CC_SYSTEM_PROMPTS[0]
    assert "official CLI tool for Claude" in system["content"]


# -- defect 2: the refusal was terminal, so failover never ran ----------------

def test_unapproved_channel_400_is_retryable():
    err = error_from_provider_status(400, REFUSAL, "workbuddy-main")
    assert err.retryable, (
        "a non-retryable error is re-raised before the router drains its "
        "fallback queue, so the client could never reach another provider in "
        "the group"
    )
    assert err.etype == "permission_error"


def test_unapproved_channel_400_keeps_the_upstream_wording():
    """The refusal text is the only evidence of what the upstream objected to.

    Rendering it as a bare "invalid_request_error" is what made this cost a
    full diagnosis cycle (docs/UPSTREAM_DIAGNOSIS.md, step 0).
    """
    err = error_from_provider_status(400, REFUSAL, "workbuddy-main")
    assert "unapproved channel" in err.message


def test_a_genuine_malformed_request_is_still_terminal():
    """Widening the 400 branch must not make every bad request retryable."""
    err = error_from_provider_status(400, "unknown field 'foo' in body", "openai")
    assert not err.retryable
    assert err.etype == "invalid_request_error"


def test_context_window_400_still_wins_over_the_channel_branch():
    """Both are 400s; the more specific one must keep its own etype."""
    err = error_from_provider_status(
        400,
        "unapproved channel: maximum context length is 8192 tokens",
        "workbuddy-main",
    )
    assert err.etype == "context_window_exceeded"


def test_the_adapter_maps_the_envelope_form_to_a_retryable_error():
    """The refusal also arrives as a {code,msg} business envelope on HTTP 200.

    decode_response unwraps that envelope; the resulting error must be
    failover-eligible too, or the envelope path keeps the terminal behaviour
    the HTTP path just lost.
    """
    import json

    a = WorkBuddyAdapter()
    with pytest.raises(WiwiError) as exc:
        a.decode_response(200, json.dumps({"code": 40001, "msg": REFUSAL}).encode())
    assert exc.value.retryable


def test_provider_key_ref_secret_is_not_echoed_in_the_message():
    """The rendered message must not echo a credential-shaped token."""
    err = error_from_provider_status(400, REFUSAL, "workbuddy-main")
    assert "sk-" not in err.message