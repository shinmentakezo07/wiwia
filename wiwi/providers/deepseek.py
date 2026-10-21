"""DeepSeek thinking-mode quirks, shared by every adapter that fronts DeepSeek.

DeepSeek's thinking mode is documented at
``api-docs.deepseek.com/guides/thinking_mode``. Two of its rules concern the
*request* side and are provider facts, not gateway policy, so every adapter
that puts a DeepSeek model on the wire has to honour them:

**1. The ``reasoning_content`` round-trip (the widespread 400).** When a
request carries the ``tools`` parameter, the ``reasoning_content`` of *all*
previous assistant turns must be passed back to the API — **even for turns
where the model made no tool call**. A router that rebuilds history from the
client's own transcript loses the field on any turn whose reasoning it did
not echo, and DeepSeek answers:

    400 invalid_request_error: The reasoning_content in the thinking mode
    must be passed back to the API.

What the API validates is the *presence* of the field on every history
assistant message, so a turn whose reasoning was stripped is padded with an
empty string — the same repair claude-code-router and CodeRouter apply.

Without ``tools`` the rule does not apply: DeepSeek ignores history
``reasoning_content`` entirely (and older R1-era deployments 400'd on it),
so it is stripped rather than forwarded.

**2. ``reasoning_effort`` has no ``"none"``.** Disabling thinking is
``{"thinking": {"type": "disabled"}}``; ``"none"`` is the OpenAI/Responses
spelling of the same intent and is not a valid ``reasoning_effort`` value
anywhere on the OpenAI wire.

Why a shared module rather than a copy in each adapter: the rule is a
property of DeepSeek, and two adapters that each carry their own copy drift
(the B.A.I copy landed first; the OpenCode copy that followed this bug
report would have been a second dialect of the same fact). This module is
provider-specific by construction — it lives under ``providers/`` for that
reason, and it is imported only by adapters, never by ``core/``.

The effort vocabulary differs per host: DeepSeek's own API accepts
``low``/``high``/``max`` (it maps ``medium``→``high`` internally), while the
gateways that resell DeepSeek accept the wider OpenAI set. The helpers here
do not police that — each adapter forwards the effort its own upstream
accepts — they only decide *whether* the round-trip rule applies and *how*
the history is shaped.
"""

from __future__ import annotations

from typing import Any

from wiwi.ir import types as ir


def is_deepseek_model(model_id: str) -> bool:
    """True for DeepSeek models (deepseek-reasoner, deepseek-v3.x/v4, R1 ids,
    optionally vendor-prefixed like ``deepseek/deepseek-v4``).

    Substring match, not a prefix: the ids arrive from three directions —
    DeepSeek's own catalogue (``deepseek-v4-pro``), a vendor-prefixed
    catalogue (``deepseek-ai/DeepSeek-V3.2``) and a reseller's alias — and a
    prefix test misses the middle one. Case-insensitive, because a reseller
    is free to spell the id ``DeepSeek-R1``.
    """
    return "deepseek" in (model_id or "").lower()


def deepseek_thinking_enabled(req: ir.Request) -> bool:
    """True when DeepSeek's thinking mode is on for this request.

    Thinking is **on by default** for DeepSeek models, so the only way it is
    off is an explicit disable. Two spellings of that intent reach the IR:
    ``reasoning_effort="none"`` (OpenAI/Responses) and
    ``thinking.type="disabled"`` (Anthropic's ``thinking`` block, which the
    Anthropic codec normalises to ``reasoning_effort="none"``).

    A request that says nothing is thinking-on — which is why an absent
    effort must not be read as "disabled" here.
    """
    return req.gen_params.effective_reasoning_effort() != "none"


def deepseek_roundtrip_required(req: ir.Request, model_id: str) -> bool:
    """True when the request must replay ``reasoning_content`` on history.

    The rule is exactly DeepSeek's: a DeepSeek model, thinking on, and a
    request that carries ``tools``. Any one of the three missing and the
    round-trip does not apply — without tools DeepSeek ignores the field, and
    with thinking off there is no reasoning to replay.

    This reads the tools off the **IR** (``req.tools``), which is the tools
    the caller declared. See
    :func:`apply_deepseek_reasoning_roundtrip` for why the wire-level
    question is asked separately.
    """
    return (
        is_deepseek_model(model_id)
        and bool(req.tools)
        and deepseek_thinking_enabled(req)
    )


def apply_deepseek_reasoning_roundtrip(
    body: dict[str, Any],
    req: ir.Request,
    model_id: str,
    *,
    has_tools: bool,
) -> bool:
    """Shape a Chat Completions body for DeepSeek's thinking-mode rule.

    Ensures every history assistant message carries ``reasoning_content``,
    padding with an empty string the turns whose reasoning the caller (or an
    earlier hop) dropped. Returns True when the body was touched.

    **``has_tools`` is the caller's to answer**, because "does the upstream
    see tools?" has two different, equally correct answers depending on the
    adapter, and the rule turns on it:

    - **the tools the caller declared** (``bool(req.tools)``) — the B.A.I
      adapter's reading, unchanged from before this rule was shared. It is
      the right question there: B.A.I adds no tools of its own, so the
      declared tools are the tools on the wire.
    - **the tools on the wire** (``bool(body.get("tools"))``) — the OpenCode
      adapter's reading. Its free-tier cloak injects ``read``/``shell`` decoys
      into the body, which makes even a caller that declared *no* tools
      request tool-carrying upstream. Keying off the IR there would leave
      that request without the round-trip, and DeepSeek would reject it with
      the same 400 the rule exists to prevent.

    A body with no tools means the upstream sees no tools, so the rule does
    not apply and history reasoning is left alone.

    Idempotent: a message that already carries the field is left alone, so
    applying this to an already-shaped body is a no-op.
    """
    if not is_deepseek_model(model_id) or not deepseek_thinking_enabled(req):
        return False
    if not has_tools:
        # No tools upstream: DeepSeek ignores history reasoning entirely.
        return False
    messages = body.get("messages")
    if not isinstance(messages, list):
        return False
    changed = False
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        if "reasoning_content" not in m:
            m["reasoning_content"] = ""
            changed = True
    return changed
