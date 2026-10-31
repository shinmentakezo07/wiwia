"""IFM (MBZUAI Institute of Foundation Models) request shaping.

The hosted API (``https://api.ifm.ai/v1``) is OpenAI Chat Completions
compatible — the same ``/chat/completions`` path, bearer auth, SSE streaming,
standard ``tools``/``tool_choice`` schema — but the K2 Horizon / Jais 3 chat
templates impose four requirements that a plain OpenAI-shaped body does not
satisfy. Each is documented at ``docs.ifm.ai`` and each is a property of the
*provider*, not a gateway policy, so they live here (imported only by the
adapter) rather than in ``core/``:

**1. ``reasoning_content`` is replayed on every assistant turn (the 400).**
The model is stateless and every request must carry the full history; each
assistant turn has to be echoed back *verbatim*, and a reasoning trace is
part of that turn. The field is ``reasoning_content`` (``reasoning`` is a
mirror kept for older clients; sending either works, sending both is
harmless), and an **empty string is a valid value** — it must be kept rather
than dropped or sent as ``null``. The harness pages state the consequence
plainly: a client that strips the trace gets every turn after the first
rejected with HTTP 400 (Pi's ``requiresReasoningContentOnAssistantMessages``,
OpenCode's ``interleaved: reasoning_content``, and the Hermes provider plugin
all exist for exactly this rule). ``docs.ifm.ai/#/multi-turn``.

**2. Message ``content`` is a string, text only.** IFM takes ``system`` /
``user`` / ``assistant`` / ``tool`` roles and text content only — no image,
audio or document parts. OpenClaw's profile sets ``requiresStringContent:
true``, which is the same fact from the client side: the template reads
``message.content`` as a string, so the content-parts array must be flattened
before it is sent. The OpenAI adapter emits parts arrays for multimodal
turns, so the adapter flattens and reports what it dropped.

**3. ``reasoning_effort`` rides ``chat_template_kwargs``.** IFM is
vLLM-backed and the capability page passes the level as
``chat_template_kwargs: {"reasoning_effort": "high"}``; the legal values are
``low`` / ``medium`` / ``high`` with ``high`` the default. The OpenAI
adapter would put the effort at the body top level (it treats an unknown
provider type as native OpenAI), so it is moved, not merely added.

**4. ``X-Session-ID`` pins a conversation to one worker.** The hosted API
load-balances across compute nodes with the vLLM router's consistent-hash
policy; a stable per-conversation id keeps turn N+1 on the node that already
holds the prefix's KV cache. It is a latency optimisation only — results are
identical without it — so the id is derived from the conversation's shared
prefix (system prompt + first user turn) and never carries user identity.
``docs.ifm.ai/#/session-routing``.

Limits worth knowing (``docs.ifm.ai/#/chat-completions`` and ``/#/limits``):
``stop`` accepts at most four sequences, ``top_k`` is supported (Jais 3: 20),
``response_format`` supports ``json_object`` and ``json_schema``, and the
daily allowance is 10,000,000 tokens per API key (``429`` with
``Retry-After`` past the cap).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

IFM_BASE_URL = "https://api.ifm.ai/v1"

# The only levels the hosted API documents. ``high`` is its default.
IFM_EFFORTS: tuple[str, ...] = ("low", "medium", "high")
IFM_DEFAULT_EFFORT = "high"

# IFM caps ``stop`` at four sequences; a longer list is a 400 upstream.
IFM_MAX_STOP = 4

# Effort spellings the other dialects produce, folded onto IFM's three levels.
# ``minimal`` shares the floor with ``low`` (as it does in the IR's own
# effort→budget ladder) and the two extended levels share the ceiling.
_EFFORT_ALIASES: dict[str, str] = {
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
    "max": "high",
}

# OpenAI-platform parameters the OpenAI adapter forwards from client extras.
# IFM documents none of them, and they are platform-specific rather than
# sampling knobs: an unknown top-level field is at best ignored and at worst a
# 400, so they are dropped and reported to the caller (the same policy NIM
# applies to its own unsupported set). ``logprobs``/``top_logprobs`` are NOT
# here — IFM documents them as accepted for compatibility.
_UNSUPPORTED_PLATFORM_PARAMS: tuple[str, ...] = (
    "prompt_cache_key",
    "safety_identifier",
    "store",
    "verbosity",
    "web_search_options",
    "prediction",
    "modalities",
    "audio",
    "logit_bias",
    "service_tier",
    "metadata",
    "user",
)


def normalize_reasoning_effort(effort: Any) -> str | None:
    """Map any dialect's effort spelling onto an IFM level, or None.

    None means "do not send an effort": either the request expressed no
    preference (IFM then applies its own default of ``high``) or it asked to
    disable thinking (``"none"``), which IFM has no documented switch for —
    the adapter reports that one to the caller rather than inventing a
    ``chat_template_kwargs`` key.
    """
    if not isinstance(effort, str):
        return None
    return _EFFORT_ALIASES.get(effort.strip().lower())


def flatten_content(content: Any) -> tuple[str, set[str]]:
    """Flatten a Chat Completions content value to IFM's string form.

    Returns ``(text, dropped_part_types)``. Text parts are concatenated in
    order; anything else (``image_url``, ``input_audio``, ``file``, …) is
    reported by its wire type so the adapter can tell the caller what the
    text-only upstream never saw.
    """
    if isinstance(content, str):
        return content, set()
    if not isinstance(content, list):
        # A scalar that is neither str nor list (null is handled by the
        # caller) has no text to carry.
        return "", {"non-text"}
    texts: list[str] = []
    dropped: set[str] = set()
    for part in content:
        if isinstance(part, str):
            texts.append(part)
            continue
        if not isinstance(part, dict):
            dropped.add("non-text")
            continue
        ptype = part.get("type")
        if ptype == "text":
            text = part.get("text")
            if isinstance(text, str):
                texts.append(text)
            else:
                dropped.add("text")
            continue
        dropped.add(str(ptype) if ptype else "non-text")
    return "".join(texts), dropped


def shape_messages(messages: list[Any], warn: Callable[[str], None]) -> None:
    """Make a Chat Completions message list legal for IFM, in place.

    Two rules, both documented above: every message's ``content`` becomes a
    string (so ``content: null`` on a tool-call-only assistant turn becomes
    ``""`` and a parts array is flattened), and every assistant message
    carries a string ``reasoning_content`` — padded with ``""`` when the
    caller's transcript lost the trace, because the field's *presence* is
    what the API validates.

    Non-text content parts are dropped and reported once per request: they
    cannot cross a text-only wire, and a caller that attached a screenshot
    must not believe the model saw it.
    """
    dropped: set[str] = set()
    for m in messages:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if content is None:
            m["content"] = ""
        elif not isinstance(content, str):
            text, kinds = flatten_content(content)
            m["content"] = text
            dropped.update(kinds)
        if m.get("role") == "assistant":
            reasoning = m.get("reasoning_content")
            if not isinstance(reasoning, str):
                # "" is a legal trace and the documented value for a turn
                # that produced none; null is not.
                m["reasoning_content"] = ""
    if dropped:
        warn(
            "IFM accepts text content only: dropped "
            + ", ".join(sorted(dropped))
            + " content part(s)"
        )


def conversation_session_id(
    messages: list[Any],
    model_id: str,
    provided: str | None = None,
) -> str:
    """Derive the ``X-Session-ID`` value for this conversation.

    A client-supplied session (wiwi's ``x-wiwi-session-id``) is authoritative:
    it is the identity the caller already uses for deployment affinity, and
    the docs require an id that is stable per conversation, opaque, and never
    shared across users. Otherwise the id is a digest of the conversation's
    shared prefix — model id, system prompt and the first user turn — which is
    stable as the transcript grows (later turns append; the prefix does not
    move), so every turn of one run lands on the worker holding its KV cache.
    Best-effort by design: affinity may break at any time and results never
    depend on it.
    """
    if isinstance(provided, str) and provided.strip():
        return provided.strip()[:128]
    digest = hashlib.sha256()
    digest.update((model_id or "").encode("utf-8", "replace"))
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("system", "user"):
            continue
        digest.update(b"\x00")
        digest.update(role.encode())
        content = m.get("content")
        digest.update((content if isinstance(content, str) else str(content or ""))
                      .encode("utf-8", "replace"))
        if role == "user":
            break
    return "conv_" + digest.hexdigest()[:24]


def unsupported_platform_params() -> tuple[str, ...]:
    """The OpenAI-platform keys the adapter strips from the body."""
    return _UNSUPPORTED_PLATFORM_PARAMS
