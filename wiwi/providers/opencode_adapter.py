"""OpenCode Console / Zen provider adapter (``opencode.ai``).

Console is a multi-protocol gateway: the same base URL serves four upstream
wire formats, chosen per model from the "Endpoints" table at
``https://opencode.ai/docs/zen`` (see ``_ROUTE_RULES``, which is the
transcription of that table and the authority for routing)::

    responses  gpt-*, grok-*, muse-spark-*            POST {base}/responses
    messages   claude-*, qwen3.8-flash, qwen3.7-*,
               qwen3.6-*, qwen3.5-*                  POST {base}/messages
    gemini     gemini-*                               POST {base}/models/{id}:...
    chat       everything else                        POST {base}/chat/completions

The base is ``https://opencode.ai/inference/v1`` — what ``models.dev`` lists as
the ``opencode`` provider's ``api`` and what the docs table uses. The older
``https://opencode.ai/zen/v1`` still routes (verified 2026-10-14: a paid model
there returns the account's ``402 Insufficient account funds``, i.e. it reaches
the API and authenticates), so an existing ``base_url`` pointing at ``zen/v1``
keeps working; this constant only sets the default for deployments that do not
set one. The catalog is **not** uniform enough for prefix guessing: the qwen
family alone spans two wires (``qwen3.8-max`` is chat, ``qwen3.8-flash`` is
messages) and ``models.dev`` carries no per-model overrides, so routing is
table-driven and falls through to chat.

The adapter delegates chat/messages/gemini to the existing adapters, and
implements a minimal Responses upstream (text + reasoning + function tools) for
the responses family.

The transport declares ``force_stream`` (OpenCode's own provider entry carries
``transport.forceStream: true``): Console answers as an event stream, so every
upstream request asks for SSE — ``encode_request`` forces ``stream: true`` on
the bodies that carry the field — and a non-streaming caller's reply is
pumped and reassembled into one ``AssistantTurn`` by
``Gateway._complete_via_stream``, exactly as for the streaming-only Cline and
WorkBuddy upstreams. The Gemini route is the exception: its wire is selected
by the URL (``:streamGenerateContent?alt=sse``), so its body carries no
``stream`` field and none is added.

Auth follows the **wire**, not the adapter: each of Zen's four front ends reads
its own credential scheme, and a credential sent in the wrong one is simply not
read — Zen answers ``401 AuthError "Missing API key."`` ("Missing", not
"Invalid": the credential never arrived)::

    chat / responses   Authorization: Bearer <key>   (OpenAI wire)
    messages           x-api-key: <key>              (Anthropic wire)
    gemini             x-goog-api-key: <key>         (Gemini wire)

All three verified live 2026-09-17 against a real ``sk-…`` Zen key: the correct
scheme on each route advances the request to the *next* gate
(``401 CreditsError "No payment method"``), while every wrong scheme — and the
no-credential case — return the identical ``AuthError``. Per-route probe
matrices live in ``tests/test_fix_round72.py`` (messages) and
``tests/test_fix_round73.py`` (gemini). The Gemini route does **not** use the
querystring: ``?key=`` probes identically to sending no credential at all, and
``wiwi/core/recovery.py:build_url`` appends a querystring key only when
``provider_type == "gemini"`` — this adapter's type is ``"opencode"``, so
nothing was ever appended there.

Every request also carries a live
``User-Agent: opencode/<version>`` — Cloudflare returns ``403 error code:
1010`` without a browser-like UA, so the version is read live from
:mod:`wiwi.providers.opencode_version` (5-min TTL background refresh, no
restart needed).

Every request also carries the official client's fingerprint headers, re-checked
against the client's own source on 2026-10-15 (``packages/core/src/session/
model-request.ts``, commit ``4397ee4``; the path in the 2026-09 docs —
``packages/opencode/src/session/llm/request.ts`` — is where it used to live):
``x-opencode-session`` + ``x-opencode-session-id`` carrying one ``ses_`` + 12 hex
+ 14 base62 id, ``x-opencode-client``, and ``x-opencode-project``. No
``x-opencode-request`` — the v2 client sends none and the free tier 403s when one
is present.

The client's request *bodies* on these wires carry no client ``metadata`` block,
and Zen's upstreams refuse one (``400 [unsupported_metadata] Client metadata is
not supported.``). A caller's ``metadata`` therefore never goes upstream from
here — see :func:`_drop_client_metadata`.

**The free tier is a request-shape gate, and it is the v2 shape.** On 2026-09-19
the gate was derived by probe matrix and this adapter spoke the v1 client's
request. The client has since moved to v2 and the two drifted apart, so every
``*-free`` request answered ``403 FreeTierError`` while the real client worked
from the same host — the error the caller sees is wiwi's own classification of it
(``402 … requires billing (403)`` from
:func:`wiwi.providers.base.error_from_provider_status`, which leaves the key
pool alone).

The v2 shape was re-derived on 2026-10-14 by capturing the real client's request
and bisecting it one factor at a time (the matrix is in
``tests/test_fix_round149.py``). Four things had to change, all of them "still
v1": the decoy pair became ``read``/``shell`` (v2 renamed ``bash``) **with the
client's own parameter schemas**; the anonymous sentinel started *sending*
``Authorization: Bearer public`` instead of omitting it; ``x-opencode-session-id``
was added; and the invented ``HTTP-Referer`` and ``x-opencode-request`` headers
were removed — the client sends neither, and each one alone turns a 200 into a
403. The ``User-Agent`` form was already right, but its cold-cache *fallback* was
not (see :mod:`wiwi.providers.opencode_version`).

The decoy tools stay behind ``opencode_free_tier_cloak``, which is **on by
default** because the gate needs them; set it ``false`` for a deployment that
serves only paid models. Free quota is accounted per session (the client reuses
one long-lived session per identity), so ``x-opencode-session`` is reused per
credential rather than re-minted per request.

**DeepSeek models on the chat route carry DeepSeek's thinking-mode rule.**
Zen's catalogue includes the ``deepseek-*`` models, and the provider *behind*
Zen is DeepSeek, whose thinking mode rejects a request whose history assistant
turns do not all carry ``reasoning_content`` once the request carries tools:

    400 invalid_request_error: The reasoning_content in the thinking mode
    must be passed back to the API.

The chat route delegates to the OpenAI adapter, which emits
``reasoning_content`` only on the history turns that *have* thinking — so the
turns whose reasoning the client stripped were the ones that failed. The
round-trip is applied here through
:func:`wiwi.providers.deepseek.apply_deepseek_reasoning_roundtrip`, the same
helper the B.A.I adapter uses (AUDIT #507).

``anthropic-version`` rides the messages route only; ``X-Title`` is referral
attribution and, unlike the ``HTTP-Referer`` that used to sit beside it, does not
trip the gate.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import string
import time
from typing import Any, Literal

import orjson
import structlog

from wiwi.ir import types as ir
from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.providers.base import (
    ProviderKeyRef,
    WarningMixin,
    as_dict,
    as_list,
    take_adapter_warnings,
    unhonoured_platform_params,
)
from wiwi.providers.deepseek import apply_deepseek_reasoning_roundtrip
from wiwi.providers.gemini_adapter import GeminiAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter
from wiwi.providers.opencode_version import build_user_agent
from wiwi.streaming import deltas as dl

log = structlog.get_logger("wiwi.opencode_adapter")

# Default base when a deployment does not set one. Console's canonical API root
# per both models.dev and the docs table; the legacy ``/zen/v1`` root still
# routes and is left working for existing configs (see the module docstring).
OPENCODE_ZEN_BASE = "https://opencode.ai/inference/v1"

Route = Literal["responses", "messages", "gemini", "chat"]

# Per-model endpoint table, transcribed from the "Endpoints" section of
# https://opencode.ai/docs/zen (fetched 2026-10-14), which is now the authority:
#
#     responses   gpt-*, grok-*, muse-spark-*        POST {base}/responses
#     messages    claude-*, qwen3.8-flash,
#                 qwen3.7-*, qwen3.6-*, qwen3.5-*   POST {base}/messages
#     gemini      gemini-*                           POST {base}/models/{id}:...
#     chat        everything else                    POST {base}/chat/completions
#
# **Prefix guessing does not work on this catalog, which is why this is a table.**
# The qwen family alone straddles two wires — `qwen3.8-max` is on chat while
# `qwen3.8-flash` and the qwen3.7/3.6/3.5 lines are on messages — so the old
# single `"qwen"` prefix sent `qwen3.8-max` to the Messages endpoint, where it
# cannot answer. `models.dev` lists the provider as `@ai-sdk/openai-compatible`
# with **no** per-model overrides, so the split is not discoverable from the
# machine-readable catalog either; only this page carries it. Anything not named
# here falls through to chat, which is where the bulk of the catalog (deepseek-*,
# glm-*, kimi-*, minimax-*, mistral-*, big-pickle, every free chat model) lives.
#
# Widening a rule is therefore a docs edit, not a guess: add the model's row.
#
# `jev-*` is the one documented model with no chat wire at all — it is a System
# One model on `/systemone` (a typed state+questions → values/probabilities
# protocol), not a completion API. It falls through to chat and fails upstream;
# it is not routable through `/v1/chat/completions`, `/v1/responses` or
# `/v1/messages`, so this adapter does not claim to serve it.
_ROUTE_RULES: tuple[tuple[tuple[str, ...], Route], ...] = (
    (("gpt-", "grok-", "muse-spark-"), "responses"),
    (("claude-", "union-alpha"), "messages"),
    # Not a bare "qwen": qwen3.8-max is the exception and lives on chat.
    (("qwen3.8-flash", "qwen3.7-", "qwen3.6-", "qwen3.5-"), "messages"),
    (("gemini-",), "gemini"),
)

# The free tier used to be a request-shape gate rather than a credential gate:
# probed live 2026-09-19, `mimo-v2.5-free` answered 200 keyless, and a `*-free`
# request was admitted only when it (a) streamed, (b) carried a client-shaped
# session id, (c) presented the client's tool pair, and (d) sent a recent
# `opencode/<v>` User-Agent.
#
# **The gate never closed; the client moved.** On 2026-10-14 every free model
# answered ``403 FreeTierError`` for the v1 shape this module still spoke, while
# the *real* client succeeded from the same host on the same model. Capturing
# and bisecting that request gave the v2 conditions recorded at `_DECOY_NAMES`,
# in `headers()` and in `opencode_version.FALLBACK_VERSION`: the tool pair is now
# ``read``/``shell`` carrying the client's real schemas, ``Authorization`` must
# be present (``Bearer public`` when anonymous), ``x-opencode-session-id`` must
# ride along, and ``HTTP-Referer``/``x-opencode-request`` must be absent.
#
# An interim round (147) read the 403s as "upstream retired the free tier" and
# made the cloak opt-in. That was wrong, and the reason is worth recording: its
# control run of the real client was executed from a directory whose
# `opencode.json` overrode the provider `baseURL`, so the failure was an artefact
# of the override rather than evidence about the gate. Re-run cleanly, the real
# client answered 200. The cloak is therefore on by default again
# (``opencode_free_tier_cloak``, opt-*out* for paid-only deployments).
_FREE_SUFFIX = "-free"
# Stealth free models: free upstream with no `-free` suffix in the id.
_STEALTH_FREE_MODELS = frozenset({"big-pickle"})
# Models whose Responses tool_choice must collapse to "auto": probed 2026-09-19,
# the free Muse Spark endpoints reject `required`, `none`, `""` and every named
# form with 400 invalid_request_error (param `tool_choice`), so the alternative
# to forcing is a hard failure. Allowlisted per upstream evidence — widen it
# only with a probe, never by prefix guessing.
_FORCE_AUTO_TOOL_CHOICE = frozenset({
    "muse-spark-1.2-contributor-free",
    "muse-spark-1.3-contributor-free",
})


def is_free_model(model_id: str) -> bool:
    """True when Zen serves this model from the free tier."""
    m = (model_id or "").strip().lower()
    return m.endswith(_FREE_SUFFIX) or m in _STEALTH_FREE_MODELS


# Deployment param that turns the free-tier decoy cloak **off**. On by default,
# because the gate it satisfies is real: probed 2026-10-14, a `*-free` request
# without `read`+`shell` in the tool payload answers 403 FreeTierError. Round 147
# briefly made this an opt-*in* on the belief the free tier was closed; it was
# not — the v2 client had simply moved on from the v1 shape this module spoke,
# and the anonymous request also lost its credential header. Set
# ``opencode_free_tier_cloak: false`` only when a deployment serves only paid
# models, where no cloak is wanted.
FREE_TIER_CLOAK_PARAM = "opencode_free_tier_cloak"


def _cloak_requested(deployment_params: dict[str, Any]) -> bool:
    """True when this deployment wants the free-tier decoy cloak (the default).

    Opt-*out*: only an explicit ``False`` disables it, so a deployment that never
    heard of the flag gets the gate satisfied.
    """
    return deployment_params.get(FREE_TIER_CLOAK_PARAM) is not False


# -- the CLI fingerprint's shape ----------------------------------------------
# The edge validates `x-opencode-session` against
# ``^ses_[0-9a-f]{12}[0-9A-Za-z]{14}$`` — the pattern the CLI exports. Measured
# 2026-09-19 by flipping one property at a time: `ses_` + 12 hex + 14 more
# returns 200 (even all-hex, so the tail alphabet is not the check), while the
# pre-fix ``uuid4().hex[:24]`` — a 24-char tail, two short of the required 26 —
# and an 11-hex-head id both return 403 FreeTierError. `x-opencode-request` is
# not shape-checked at all (a 6-char id still returns 200), so only the session
# is load bearing; both are minted in the CLI's form because that is what the
# client sends.
_BASE62 = string.digits + string.ascii_uppercase + string.ascii_lowercase
_SESSION_RE_HEX = 12  # hex chars of the encoded timestamp
_ID_TAIL = 14         # base62 chars of randomness
# The edge's own shape, exported so the tests assert against the one pattern
# the minter and the validator share (mirrors the CLI's OPENCODE_SESSION_RE).
OPENCODE_SESSION_RE = re.compile(
    rf"^ses_[0-9a-f]{{{_SESSION_RE_HEX}}}[0-9A-Za-z]{{{_ID_TAIL}}}$")
# The v1 client also sent an `x-opencode-request: msg_...` header whose id had
# this shape. The v2 client sends no such header and the free tier answers 403
# when one is present, so the pattern went with it (round 149).


def _id_prefix(invert: bool) -> str:
    # The client inverts the session timestamp (newest first when sorted). It is
    # never decoded upstream -- the shape is what is checked -- but matching it
    # keeps the fingerprint byte-honest rather than merely pattern-satisfying.
    cur = (int(time.time() * 1000) * 0x1000 + 1)
    if invert:
        cur = ~cur
    return "".join(f"{(cur >> (40 - 8 * i)) & 0xFF:02x}" for i in range(_SESSION_RE_HEX // 2))


def canonical_session_id() -> str:
    return ("ses_" + _id_prefix(True)
            + "".join(random.choice(_BASE62) for _ in range(_ID_TAIL)))


# Upstream free-tier quota is accounted **per session**. Minting a fresh
# session for every request spreads the traffic over new buckets and returns
# 429 FreeUsageLimitError with growing reset-after delays; the real CLI reuses
# one long-lived session per identity. Bucket by credential (anonymous free
# setups share one bucket), LRU-capped and TTL-evicted so the map cannot grow
# with the number of distinct keys ever seen.
_SESSION_TTL_S = 3600.0
_MAX_SESSION_BUCKETS = 1000
_stable_sessions: dict[str, tuple[str, float]] = {}


def stable_session_id(bucket: str) -> str:
    key = hashlib.sha256(bucket.encode()).hexdigest()[:32]
    now = time.monotonic()
    hit = _stable_sessions.get(key)
    if hit is not None and now - hit[1] < _SESSION_TTL_S:
        _stable_sessions.pop(key)
        _stable_sessions[key] = (hit[0], now)
        return hit[0]
    if len(_stable_sessions) >= _MAX_SESSION_BUCKETS:
        _stable_sessions.pop(next(iter(_stable_sessions)))
    session = canonical_session_id()
    _stable_sessions[key] = (session, now)
    return session


def _reset_stable_sessions_for_tests() -> None:
    _stable_sessions.clear()


# The free tier is a request-shape gate, and the shape it wants is the v2
# client's, not the v1 one this module originally spoke. Re-derived live
# 2026-10-14 by capturing the real `opencode run` request and bisecting it one
# factor at a time; see the module docstring. Two conditions must hold:
#
# 1. The tool payload must contain **``read`` and ``shell`` carrying the client's
#    own parameter schemas.** v1 named the second tool ``bash``; v2 renamed it
#    ``shell``. The *names* are not enough and the *count* is not enough: six
#    inert decoys 403, six real tools with their names rewritten 403, and
#    ``read``+``shell`` with ``parameters`` emptied 403. Keeping the schemas and
#    swapping the description for an inert one is enough (verified 200).
# 2. ``Authorization`` must be *present* (any value; the anonymous client sends
#    ``Bearer public``), and ``x-opencode-session-id`` must ride along with
#    ``x-opencode-session``. Dropping either 403s.
#
# The definitions below are the captured client ones with the description
# replaced, so the gate sees its expected payload while the model is told the
# tools are unavailable. A real client tool of the same name wins and is left in
# place (and is therefore not filtered on the way out).
_DECOY_DESCRIPTION = "This tool is currently unavailable and must not be used."
_DECOY_NAMES = ("read", "shell")
_DECOY_PARAMETERS: dict[str, dict[str, Any]] = {
    "read": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File or directory to read"},
            "offset": {"type": "integer", "minimum": 0,
                       "description": "The line or directory entry to start reading "
                                      "from (1-based)"},
            "limit": {"type": "integer", "minimum": 0,
                      "description": "The maximum number of lines or directory "
                                     "entries to read (defaults to and capped at 2000)"},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    "shell": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command string to execute"},
            "workdir": {"type": "string",
                        "description": "Working directory to execute the command in. "
                                       "Defaults to the current working directory. When "
                                       "possible, avoid changing directories in the "
                                       "command and set the working directory here "
                                       "instead."},
            "timeout": {"type": "integer", "minimum": 0,
                        "description": "Timeout in milliseconds. Set to 0 to disable the "
                                       "timeout. Defaults to 120000 for foreground "
                                       "commands. Background commands have no timeout "
                                       "by default."},
            "background": {"type": "boolean",
                           "description": "Run the command in the background and return "
                                          "immediately (useful for dev servers and "
                                          "long-running builds). You do not need to use "
                                          "'&' at the end of the command when using this "
                                          "parameter. You will be notified when it "
                                          "completes. DO NOT poll for completion."},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
}


def _decoys_chat() -> list[dict[str, Any]]:
    return [{"type": "function",
             "function": {"name": n, "description": _DECOY_DESCRIPTION,
                          "parameters": _DECOY_PARAMETERS[n]}}
            for n in _DECOY_NAMES]


def _decoys_responses() -> list[dict[str, Any]]:
    return [{"type": "function", "name": n, "description": _DECOY_DESCRIPTION,
             "parameters": _DECOY_PARAMETERS[n]}
            for n in _DECOY_NAMES]


def _cloak_chat_tools(body: dict[str, Any]) -> list[str]:
    """Add the free tier's decoy tools; return the names actually injected.

    The return value drives the decode-side filter (``_filter_decoys``): a
    client that declares its own ``bash`` keeps it, and its calls must reach
    the caller untouched, so only the names *this* function added are dropped.
    """
    tools = body.get("tools")
    if not isinstance(tools, list) or not tools:
        body["tools"] = _decoys_chat()
        body.setdefault("tool_choice", "none")
        return list(_DECOY_NAMES)
    names = {t.get("function", {}).get("name") or t.get("name")
             for t in tools if isinstance(t, dict)}
    added = [n for n in _DECOY_NAMES if n not in names]
    body["tools"] = tools + [d for d in _decoys_chat()
                             if d["function"]["name"] in added]
    return added


def _cloak_responses_tools(body: dict[str, Any]) -> list[str]:
    tools = body.get("tools")
    tools = tools if isinstance(tools, list) else []
    names = {t.get("name") or (t.get("function") or {}).get("name")
             for t in tools if isinstance(t, dict)}
    added = [n for n in _DECOY_NAMES if n not in names]
    body["tools"] = tools + [d for d in _decoys_responses() if d["name"] in added]
    body.setdefault("tool_choice", "auto")
    return added


def _force_auto_tool_choice(body: dict[str, Any], model_id: str) -> None:
    m = (model_id or "").strip().lower()
    # Only an allowlisted model: the upstream rejects every other tool_choice
    # form on it, and a named/required call the client asked for must survive
    # on all the rest.
    if m in _FORCE_AUTO_TOOL_CHOICE and body.get("tool_choice") not in (None, "auto"):
        log.debug("opencode_tool_choice_forced_auto", model=m)
        body["tool_choice"] = "auto"


def _drop_client_metadata(body: dict[str, Any], req: ir.Request,
                          deployment_params: dict[str, Any], route: Route) -> None:
    """Drop the caller's client-metadata block before it reaches Zen.

    Zen's upstreams answer a client-supplied ``metadata`` object with
    ``400 [unsupported_metadata] Client metadata is not supported.`` Console
    relays that as ``Upstream request failed: [unsupported_metadata] Client
    metadata is not supported.``, keeping the *upstream's* code in brackets
    because it strips codes outside its own allow-list — opencode's own client
    pins that format in ``packages/ai/test/provider-error.test.ts`` ("Zen drops
    upstream codes outside its allow-list and replaces the type, but
    ``Protocol.errorMessage`` keeps the original code as a ``[code]`` prefix").
    Observed on this adapter 2026-10-15 against a free model (reported with the
    Console wrapper above, wiwi rendering it as ``<account>: <message>``).

    The block is the *caller's*, not ours: the inbound codecs capture a
    top-level ``metadata`` into ``req.extras`` and both delegates forward it
    (``openai_adapter._STANDARD``, ``anthropic_adapter._ANTHROPIC_STANDARD``) —
    correct for the vendors that support the field, wrong here. Zen's own client
    puts no ``metadata`` on these wires (its request builder attaches one only on
    the Responses protocol, and only when provider options set it), so nothing
    downstream is waiting for it.

    An operator who wants the field on the wire anyway declares it explicitly
    with ``extra_body: {metadata: ...}`` on the deployment; that is left alone.
    """
    if "metadata" not in req.extras:
        return
    if "metadata" in (deployment_params.get("extra_body") or {}):
        return
    if "metadata" in body:
        body.pop("metadata")
        log.debug("opencode_client_metadata_dropped", route=route)



# Config keys are validated non-empty (KeyDef._key_required), so a keyless
# free-tier setup declares itself with this literal sentinel. It does **not**
# omit the credential header — the free tier's gate reads its presence, and the
# anonymous client sends ``Bearer public`` (see ``headers()``).
ANONYMOUS_KEY_SENTINEL = "anonymous"
ANONYMOUS_BEARER = "Bearer public"

# Each Zen front end reads its own credential header, and a credential sent in
# any other one is invisible to it (see the module docstring for the live probe
# matrix). The value here is the *header name*; "Authorization" additionally
# needs the "Bearer " prefix, applied at the write site.
_CREDENTIAL_HEADER: dict[Route, str] = {
    "messages": "x-api-key",
    "gemini": "x-goog-api-key",
    "chat": "Authorization",
    "responses": "Authorization",
}


def route_for_model(model_id: str) -> Route:
    """Pick the Zen upstream protocol for a native model id.

    Table-driven (``_ROUTE_RULES``), defaulting to the chat wire. Deliberately
    *not* a prefix guess: the catalog's qwen family spans two endpoints.
    """
    m = (model_id or "").strip().lower()
    for prefixes, route in _ROUTE_RULES:
        if m.startswith(prefixes):
            return route
    return "chat"


def _base(base_url: str) -> str:
    return (base_url or OPENCODE_ZEN_BASE).rstrip("/") or OPENCODE_ZEN_BASE


class OpencodeAdapter(WarningMixin):
    """Multi-protocol Zen adapter with live opencode User-Agent headers."""

    provider_type = "opencode"
    # Zen answers the free tier as an event stream — the request's `stream`
    # flag is ignored there, so a non-streaming caller's reply arrives as SSE
    # and the plain JSON decode path reads it as an empty turn. Declared on the
    # transport (the official client's `forceStream`), so the gateway pumps the
    # stream and reassembles the deltas into an AssistantTurn instead — exactly
    # what Cline (`force_stream = True`) and WorkBuddy already do.
    force_stream = True

    def __init__(self) -> None:
        self._init_warnings()
        self._chat = OpenAIAdapter()
        self._msg = AnthropicAdapter()
        self._gem = GeminiAdapter()
        self._last_route: Route = "chat"
        # Official-client fingerprint id: generated lazily so it stays stable
        # across the 401-refresh retry path's header rebuilds (same adapter
        # instance) while each request reuses the credential's long-lived id.
        self._spoof_session: str | None = None
        # Responses-upstream per-stream state (mirrors OpenAIAdapter's).
        self._resp_tools: dict[str, dict[str, Any]] = {}  # item_id -> entry
        self._resp_next_index = 0
        self._resp_started = False
        self._resp_ended = False
        # Decoy suppression (round 88): the names `encode_request` injected on
        # this request and the stream indices they turn out to occupy, so a
        # model that calls a decoy anyway cannot hand the client a tool call it
        # has no implementation for (AUDIT #267).
        self._decoy_names: frozenset[str] = frozenset()
        # Stream indices whose ToolCallOpen was dropped, so their args/close
        # deltas go with them, and whether a *real* call survived.
        self._decoy_indices: set[int] = set()
        self._kept_real_call = False

    def reset(self) -> None:
        self._chat.reset()
        self._msg.reset()
        self._gem.reset()
        self._last_route = "chat"
        self._spoof_session = None
        self._resp_tools.clear()
        self._resp_next_index = 0
        self._resp_started = False
        self._resp_ended = False
        self._decoy_names = frozenset()
        self._decoy_indices.clear()
        self._kept_real_call = False
        self._reset_warnings()

    def _collect_sub_warnings(self) -> None:
        """Move the route adapter's caller-visible warnings up to this adapter.

        OpenCode routes a request to one of three wire shapes, each encoded
        by its own adapter (OpenAI chat, Anthropic messages, Gemini). Those
        adapters raise the caller-visible translation warnings (a dropped
        builtin tool, an unsupported ``strict``, a dropped ``top_k``) into
        their OWN lists — but the gateway drains exactly one list, this
        adapter's. Without this forwarding every warning raised on a
        sub-route was collected and thrown away, so the caller learned
        nothing on those routes (AUDIT #498).
        """
        for sub in (self._chat, self._msg, self._gem):
            for msg in take_adapter_warnings(sub):
                self.warn(msg)

    # -- auth / URL ------------------------------------------------------
    def headers(self, key: ProviderKeyRef) -> dict[str, str]:
        # Exactly the headers the v2 client puts on the wire, minus the ones the
        # gate does not read (`Accept`, `Content-Type`, `Accept-Encoding`) and
        # minus two this adapter used to add that the free tier **rejects**:
        # `HTTP-Referer` and `x-opencode-request` (bisected live 2026-10-14 —
        # each one alone turns a 200 into a 403 FreeTierError). The client sends
        # neither, so they were pure liability. `X-Title` stays: referral
        # attribution, and it does not trip the gate (verified 200).
        h: dict[str, str] = {
            "User-Agent": build_user_agent(),
            "X-Title": "opencode",
        }
        # Anthropic's API version names its own Messages endpoint: it rides
        # the messages route only (genuine Anthropic wire via _msg, or Zen's
        # /messages). The Responses/Chat/Gemini endpoints never need it.
        if self._last_route == "messages":
            h["anthropic-version"] = "2023-06-01"
        # Anonymous sentinel: omits every credential. It was the working
        # free-tier path while the free tier answered at all (probed
        # 2026-09-19: keyless 200, a real Zen key 429 FreeUsageLimitError).
        # Since 2026-10-14 the free tier is closed to everyone, signed in or
        # not, so the sentinel no longer buys anything on its own — keep it
        # for deployments that deliberately send no credential, but do not read
        # it as free-tier access. Paid models need a real Console key.
        if key.secret.strip().lower() != ANONYMOUS_KEY_SENTINEL:
            # Scheme follows the route; see _CREDENTIAL_HEADER and the module
            # docstring. `_last_route` is written by build_url()/encode_request()
            # ahead of headers() on every hot path; a cold call defaults to
            # "chat" (the OpenAI-wire scheme). The debug line names route and
            # scheme — a wrong scheme is silently unread upstream, so this is
            # what turns that into a one-line diagnosis. Never logs the key.
            scheme = _CREDENTIAL_HEADER.get(self._last_route, "Authorization")
            value = key.secret.strip()
            h[scheme] = value if scheme != "Authorization" else f"Bearer {value}"
            log.debug("opencode_credential_scheme", route=self._last_route,
                      scheme=scheme, label=key.label)
        else:
            # The sentinel still *sends* a credential, because the free tier's
            # gate reads the header's presence: the anonymous client puts
            # `Bearer public` on the wire (captured 2026-10-14) and omitting
            # Authorization answers 403 FreeTierError even with a perfectly
            # shaped request. `public` is not a secret and not an account — it
            # is the client's own anonymous marker, and a real key here yields
            # the upstream's ordinary 401/402 instead.
            # Only where the capture shows it. The Messages and Gemini front ends
            # read their own header (`x-api-key` / `x-goog-api-key`) and have no
            # observed anonymous form, so they stay credential-free rather than
            # being handed an invented `Authorization`.
            if _CREDENTIAL_HEADER.get(self._last_route) == "Authorization":
                h["Authorization"] = ANONYMOUS_BEARER
                log.debug("opencode_credential_anonymous", route=self._last_route,
                          label=key.label)
            else:
                log.debug("opencode_credential_omitted", route=self._last_route,
                          reason="anonymous_sentinel", label=key.label)
        # Official-client fingerprint, on every model, mirroring the opencode v2
        # client's own header set (captured live 2026-10-14): the session id on
        # both `x-opencode-session` and `x-opencode-session-id`, the client tag,
        # and the project id.
        #
        # The session id is load bearing for the free tier: the edge matches the
        # client's `ses_` + 12 hex + 14 base62 shape, so a uuid-hex id — same
        # length, wrong alphabet — is rejected with 403 FreeTierError (and so is
        # the v2 CLI's own `ses_`+64-hex form, which is a different id space
        # than this header carries). The upstream also accounts free quota per
        # session, so the id is reused per credential rather than re-minted per
        # request (429 class). It stays stable on one adapter instance across the
        # 401-refresh retry path's header rebuilds and rotates on reset()/fresh
        # instance.
        if self._spoof_session is None:
            self._spoof_session = stable_session_id(
                f"{self.provider_type}:{key.label}:{key.secret}")
        h["x-opencode-session"] = self._spoof_session
        # The v2 client sends this alongside the session header, and the free
        # tier rejects a request that carries only one of the pair (bisected live
        # 2026-10-14: dropping `x-opencode-session-id` turns a 200 into a 403).
        # It sends no `x-opencode-request` at all, and adding one also 403s — see
        # the note in headers().
        h["x-opencode-session-id"] = self._spoof_session
        h["x-opencode-client"] = "cli"
        h["x-opencode-project"] = "global"
        return h

    def realtime_url(self, base_url: str) -> str | None:
        """No Realtime-style WebSocket surface on this provider.

        The /v1/realtime route reads this and answers 501 rather than dialling
        a URL that does not exist, so a client learns the provider cannot serve
        a session instead of hanging on a connection that will never open.
        """
        return None

    def build_url(self, base_url: str, model_id: str, stream: bool) -> str:
        route = route_for_model(model_id)
        self._last_route = route
        base = _base(base_url)
        if route == "responses":
            return f"{base}/responses"
        if route == "messages":
            return f"{base}/messages"
        if route == "gemini":
            if stream:
                return f"{base}/models/{model_id}:streamGenerateContent?alt=sse"
            return f"{base}/models/{model_id}:generateContent"
        return f"{base}/chat/completions"

    # -- request encoding --------------------------------------------------
    def encode_request(self, req: ir.Request, model_id: str,
                       deployment_params: dict[str, Any]) -> dict[str, Any]:
        route = route_for_model(model_id)
        self._last_route = route
        free = is_free_model(model_id)
        # Per-request filter state; cleared up front so the Gemini early return
        # below cannot leave a previous request's decoys armed.
        self._decoy_names = frozenset()
        self._decoy_indices.clear()
        self._kept_real_call = False
        if route == "messages":
            body = self._msg.encode_request(req, model_id, dict(deployment_params))
        elif route == "gemini":
            # Gemini picks the wire from the URL (`:streamGenerateContent
            # ?alt=sse` — see build_url, which the pump already calls with
            # stream=True), not from a body field; a `stream` key here is an
            # unknown field the endpoint rejects.
            body = self._gem.encode_request(req, model_id, dict(deployment_params))
            # The sub-adapter's caller-visible warnings belong to THIS
            # adapter as far as the gateway is concerned — it drains one
            # list, this adapter's. Forward them (AUDIT #498); otherwise
            # OpenCode's Gemini route would swallow every warning its
            # sub-adapters raised.
            self._collect_sub_warnings()
            return body
        elif route == "responses":
            body = _encode_responses_request(req, model_id, deployment_params,
                                             warn=self.warn)
        else:
            params = dict(deployment_params)
            params["provider_type"] = "openai"
            body = self._chat.encode_request(req, model_id, params)
        self._collect_sub_warnings()
        # Zen refuses the caller's client-metadata block on every route that can
        # carry one (see _drop_client_metadata). The Gemini route returns above
        # and the Responses encoder never forwards extras, so neither can.
        _drop_client_metadata(body, req, deployment_params, route)
        # force_stream transport: the declaration above only moves the gateway
        # onto the pump; the request still has to ask for SSE. The client's own
        # flag is irrelevant — the pump reassembles either way — so it is
        # forced here rather than trusted, mirroring Cline/WorkBuddy, whose
        # encoders overwrite the same field for the same reason. On the free
        # tier a `stream: false` body is also the 403 FreeTierError trigger.
        streaming_forced = not req.stream
        body["stream"] = True
        if route == "chat" and streaming_forced:
            # The client never asked for a stream, so it never asked for
            # `stream_options` either — and without it Zen's chat route omits
            # the usage chunk, leaving the aggregated turn to be priced on the
            # estimator. Probed live: Zen accepts the field and answers with a
            # real usage frame. The Gemini/messages/responses routes carry
            # usage natively, and only the chat wire has the field.
            body["stream_options"] = {"include_usage": True}
        if free and _cloak_requested(deployment_params):
            # Opt-in only. `bash` and `read` ride in as inert decoys; a client
            # tool of the same name wins and is left untouched — and is
            # therefore not filtered on the way out. The messages route is not
            # cloaked: its only member seen on the free tier (`union-alpha`)
            # is retired upstream (`401 ModelError` on every route, 2026-09-19),
            # so there is no evidence for a tool shape there, and an
            # OpenAI-format decoy in an Anthropic body would be an invented
            # field.
            if route == "responses":
                self._decoy_names = frozenset(_cloak_responses_tools(body))
            elif route == "chat":
                self._decoy_names = frozenset(_cloak_chat_tools(body))
        if free and route == "responses":
            # Independent of the cloak: the free Muse Spark endpoints reject
            # every non-auto tool_choice with 400, and forcing is still the
            # lesser evil there. Allowlisted per model (see
            # _FORCE_AUTO_TOOL_CHOICE), so it cannot touch other traffic.
            _force_auto_tool_choice(body, model_id)
        if route == "chat" and apply_deepseek_reasoning_roundtrip(
                body, req, model_id, has_tools=bool(body.get("tools"))):
            # DeepSeek's thinking mode demands that EVERY history assistant
            # turn carry `reasoning_content` once the request carries tools —
            # including turns whose reasoning the client (or an earlier hop)
            # stripped, which is exactly the transcript shape a router
            # rebuilds. Without it DeepSeek answers
            # `400 invalid_request_error: The reasoning_content in the
            # thinking mode must be passed back to the API` (AUDIT #507).
            #
            # The OpenAI sub-adapter is told `provider_type="openai"`, so it
            # emits `reasoning_content` on the history turns that HAVE
            # thinking; the turns without it are the ones that need the
            # empty-string pad. The shared helper owns the rule — see
            # `wiwi/providers/deepseek.py` and the B.A.I adapter, which
            # applies the same rule for the same reason.
            #
            # `has_tools` is the tools ON THE WIRE, not the ones the caller
            # declared: the free-tier cloak injects `read`/`shell` into the
            # body, which makes even a tool-less call tool-carrying upstream.
            # Placed AFTER the cloak on purpose, for that reason.
            log.debug("opencode_deepseek_reasoning_roundtrip",
                      model=model_id, messages=len(body.get("messages") or []))
        return body

    # -- response decoding ---------------------------------------------------
    def decode_response(self, status: int, body: bytes) -> ir.AssistantTurn:
        if self._last_route == "responses":
            return self._filter_decoys_turn(_decode_responses_response(body))
        if self._last_route == "messages":
            return self._msg.decode_response(status, body)
        if self._last_route == "gemini":
            return self._gem.decode_response(status, body)
        return self._filter_decoys_turn(self._chat.decode_response(status, body))

    def decode_stream_event(self, event: str, data: str) -> list[dl.IRStreamDelta]:
        if data == "[DONE]":
            if self._last_route == "responses":
                if self._resp_ended:
                    return []
                self._resp_ended = True
                # A stream that ends on the sentinel instead of
                # ``response.completed`` still delivered function_call items;
                # returning a bare StreamEnd left them unterminated and the
                # gateway synthesized Finish("stop") for a tool-call turn
                # (AUDIT #133 class). ``_resp_ended`` makes every later event a
                # no-op, so those entries could never be drained afterwards.
                # Mirrors the completed/incomplete branch below.
                out: list[dl.IRStreamDelta] = []
                for entry in sorted(self._resp_tools.values(),
                                    key=lambda e: e["index"]):
                    if not entry.get("closed"):
                        out.append(dl.ToolCallClose(index=entry["index"]))
                self._resp_tools.clear()
                had_calls = self._resp_next_index > 0
                self._resp_next_index = 0
                if had_calls:
                    # A function call opened during this stream: the turn ended
                    # with tool calls, so the stop reason is content-derived.
                    out.append(dl.Finish("tool_call"))
                out.append(dl.StreamEnd())
                return self._filter_decoys(out)
            return self._filter_decoys(self._sub().decode_stream_event(event, data))
        if self._last_route == "responses" and self._resp_ended:
            return []
        err = _envelope_stream_error(data)
        if err is not None:
            if self._last_route == "responses":
                self._resp_ended = True
            return [err]
        if self._last_route == "responses":
            return self._filter_decoys(self._decode_responses_stream_event(event, data))
        return self._filter_decoys(self._sub().decode_stream_event(event, data))

    # -- decoy suppression --------------------------------------------------
    def _filter_decoys(self, deltas: list[dl.IRStreamDelta]) -> list[dl.IRStreamDelta]:
        """Drop tool calls aimed at this request's injected decoy tools.

        The free-tier gate demands `bash`/`read` in the tool list, but the model
        may still *call* one (probed live 2026-09-19: `mimo-v2.5-free` answers
        "read the file config.py" with a `read` call). The client never declared
        that tool and cannot execute it, so forwarding the call hands it a
        guaranteed-failing dispatch. Dropping the Open/Args/Close triple whole
        is legal at this layer: the wire encoders assign client-visible indices
        themselves (`anthropic_messages._tool_blocks`) and ignore an ArgsDelta
        whose block never opened, so a gap in the IR index sequence is invisible
        downstream. `Finish` is corrected to `stop` when the decoy was the only
        call — otherwise the client waits for a result that was never sent.

        A client's own tool named `bash` is absent from `_decoy_names` (the
        cloak kept its entry), so its calls pass through untouched.
        """
        if not self._decoy_names:
            return deltas
        out: list[dl.IRStreamDelta] = []
        for d in deltas:
            if isinstance(d, dl.ToolCallOpen):
                if d.name in self._decoy_names:
                    self._decoy_indices.add(d.index)
                    log.debug("opencode_decoy_call_dropped", tool=d.name,
                              index=d.index)
                    continue
                self._kept_real_call = True
            elif (isinstance(d, (dl.ToolCallArgsDelta, dl.ToolCallClose))
                  and d.index in self._decoy_indices):
                continue
            elif (isinstance(d, dl.Finish) and d.stop_reason == "tool_call"
                  and not self._kept_real_call):
                out.append(dl.Finish("stop", d.stop_sequence))
                continue
            out.append(d)
        return out

    def _filter_decoys_turn(self, turn: ir.AssistantTurn) -> ir.AssistantTurn:
        """Non-streaming twin of `_filter_decoys` (same rationale)."""
        if not self._decoy_names or not turn.tool_calls:
            return turn
        kept = [c for c in turn.tool_calls if c.name not in self._decoy_names]
        if len(kept) == len(turn.tool_calls):
            return turn
        log.debug("opencode_decoy_calls_dropped",
                  dropped=len(turn.tool_calls) - len(kept), kept=len(kept))
        turn.tool_calls = kept
        if turn.stop_reason == "tool_call" and not kept:
            turn.stop_reason = "stop"
        return turn

    def _sub(self) -> Any:
        if self._last_route == "messages":
            return self._msg
        if self._last_route == "gemini":
            return self._gem
        return self._chat


    # -- responses stream decode (stateful) ----------------------------------
    def _decode_responses_stream_event(self, event: str, data: str) -> list[dl.IRStreamDelta]:
        try:
            payload = orjson.loads(data)
        except (json.JSONDecodeError, ValueError):
            return []
        if not isinstance(payload, dict):
            return []
        etype = str(payload.get("type") or event or "")
        out: list[dl.IRStreamDelta] = []
        if not self._resp_started:
            out.append(dl.StreamStart(model=""))
            self._resp_started = True
        if etype == "response.output_text.delta":
            delta = payload.get("delta")
            if isinstance(delta, str) and delta:
                out.append(dl.TextDelta(delta))
            return out
        if etype == "response.reasoning_summary_text.delta":
            delta = payload.get("delta")
            if isinstance(delta, str) and delta:
                out.append(dl.ThinkingDelta(delta))
            return out
        if etype == "response.output_item.added":
            item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
            if item.get("type") == "function_call":
                item_id = str(item.get("id") or item.get("call_id") or "")
                if item_id and item_id not in self._resp_tools:
                    idx = self._resp_next_index
                    self._resp_next_index += 1
                    self._resp_tools[item_id] = {
                        "index": idx, "name": str(item.get("name") or ""),
                        "call_id": str(item.get("call_id") or item_id),
                        "buf": "",  # accumulated .delta fragments
                    }
                    out.append(dl.ToolCallOpen(index=idx,
                                               id=self._resp_tools[item_id]["call_id"],
                                               name=self._resp_tools[item_id]["name"]))
            return out
        if etype == "response.function_call_arguments.delta":
            item_id = str(payload.get("item_id") or "")
            delta = payload.get("delta")
            entry = self._resp_tools.get(item_id)
            if entry is None or not isinstance(delta, str) or not delta:
                return out
            entry["buf"] += delta
            out.append(dl.ToolCallArgsDelta(index=entry["index"], args_fragment=delta))
            return out
        if etype in ("response.function_call_arguments.done",
                     "response.output_item.done"):
            item = payload.get("item") if isinstance(payload.get("item"), dict) else None
            item_id = str(payload.get("item_id") or (item or {}).get("id") or "")
            if etype == "response.output_item.done" and item is not None:
                if item.get("type") != "function_call":
                    return out
                item_id = str(item.get("id") or item_id)
                entry = self._resp_tools.get(item_id)
                if entry is None:
                    # Done without a prior added (single-shot item): open then close.
                    idx = self._resp_next_index
                    self._resp_next_index += 1
                    args = item.get("arguments") or "{}"
                    out.append(dl.ToolCallOpen(index=idx,
                                               id=str(item.get("call_id") or item_id),
                                               name=str(item.get("name") or "")))
                    if isinstance(args, str) and args and args != "{}":
                        out.append(dl.ToolCallArgsDelta(index=idx, args_fragment=args))
                    out.append(dl.ToolCallClose(index=idx))
                    return out
                if entry.get("closed"):
                    # args.done already closed this item (the normal order is
                    # added → deltas → args.done → item.done): the item is
                    # fully delivered; emitting anything again would reopen a
                    # duplicate tool call.
                    return out
                entry["closed"] = True
                out.append(dl.ToolCallClose(index=entry["index"]))
                return out
            entry = self._resp_tools.get(item_id)
            if entry is None or entry.get("closed"):
                return out
            entry["closed"] = True
            if etype == "response.function_call_arguments.done":
                # ``arguments`` on .done is CUMULATIVE — the complete final
                # string, not a fragment (verified live 2026-09-07). The
                # incremental .delta events already delivered it; re-emitting
                # the full string as another fragment duplicates the payload
                # and the client's concatenated arguments stop parsing as
                # JSON. Only emit when it adds information: the no-fragments
                # single-shot case, or a suffix repairing a truncated delta
                # stream.
                args = payload.get("arguments")
                if isinstance(args, str) and args and args != "{}":
                    buf = entry.get("buf") or ""
                    if not buf:
                        out.append(dl.ToolCallArgsDelta(index=entry["index"],
                                                        args_fragment=args))
                    elif args.startswith(buf) and len(args) > len(buf):
                        out.append(dl.ToolCallArgsDelta(
                            index=entry["index"], args_fragment=args[len(buf):]))
                    # buf == args: fragments already complete — emit nothing.
                    # buf not a prefix: trust the streamed fragments.
            out.append(dl.ToolCallClose(index=entry["index"]))
            return out
        if etype in ("response.completed", "response.incomplete"):
            resp = payload.get("response") if isinstance(payload.get("response"), dict) else {}
            u = resp.get("usage") if isinstance(resp.get("usage"), dict) else {}
            in_det = u.get("input_tokens_details") if isinstance(
                u.get("input_tokens_details"), dict) else {}
            out_det = u.get("output_tokens_details") if isinstance(
                u.get("output_tokens_details"), dict) else {}
            # Raw int() on a typed-wrong value raised mid-stream and the pump
            # routed it to a provider cooldown; use the shared coercion every
            # other adapter uses (AUDIT #194/#233).
            out.append(dl.UsageFinal(
                prompt=ir.coerce_int(u.get("input_tokens")) or 0,
                cached=ir.coerce_int(in_det.get("cached_tokens")) or 0,
                reasoning=ir.coerce_int(out_det.get("reasoning_tokens")) or 0,
                output=ir.coerce_int(u.get("output_tokens")) or 0,
                # Responses-wire convention: input_tokens INCLUDES cached
                # (mirrors the base OpenAI adapter's record).
                cached_in_prompt=True))
            for entry in sorted(self._resp_tools.values(), key=lambda e: e["index"]):
                if not entry.get("closed"):
                    out.append(dl.ToolCallClose(index=entry["index"]))
            self._resp_tools.clear()
            incomplete = etype == "response.incomplete" or resp.get("status") == "incomplete"
            if incomplete:
                out.append(dl.Finish("length"))
            elif self._resp_next_index > 0:
                # A function call opened during this stream: the turn ended
                # with tool calls (mirrors the OpenAI finish_reason mapping).
                out.append(dl.Finish("tool_call"))
            else:
                out.append(dl.Finish("stop"))
            out.append(dl.StreamEnd())
            self._resp_ended = True
            return out
        if etype == "response.failed":
            resp = payload.get("response") if isinstance(payload.get("response"), dict) else {}
            err_obj = resp.get("error") if isinstance(resp.get("error"), dict) else {}
            msg = str(err_obj.get("message") or payload.get("message")
                      or "opencode responses stream failed")
            # Clear per-stream tool state before short-circuiting: `_resp_ended`
            # makes every later event a no-op, so stale entries could never be
            # drained and would leak into the next use of a shared adapter
            # (AUDIT #77). Mirrors the completed/incomplete branch above.
            self._resp_tools.clear()
            self._resp_next_index = 0
            self._resp_ended = True
            return out + [dl.StreamError(message=msg, kind="status")]
        return out


def _envelope_stream_error(data: str) -> dl.StreamError | None:
    """Surface Zen/Cloudflare error envelopes riding a 200 SSE chunk."""
    try:
        chunk = orjson.loads(data)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(chunk, dict) or chunk.get("choices"):
        return None
    if isinstance(chunk.get("output"), list):
        return None
    err = chunk.get("error")
    if isinstance(err, dict):
        msg = str(err.get("message") or "opencode stream error")
        return dl.StreamError(message=msg, kind="status")
    if isinstance(err, str) and err.strip():
        # Zen also sends the bare-string shape: {"error": "rate limited"}.
        return dl.StreamError(message=err.strip(), kind="status")
    return None


def _encode_responses_request(req: ir.Request, model_id: str,
                              deployment_params: dict[str, Any],
                              warn: Any = None) -> dict[str, Any]:
    """Render IR as an OpenAI Responses API request body for Zen.

    ``warn`` is the caller-visible warning sink (an adapter's ``warn``
    method). It is optional so the function stays callable on its own, but
    the OpenCode adapter always passes one: a dropped attachment is
    information the caller needs, and the operator log alone does not reach
    them (AUDIT #498).
    """
    g = req.gen_params
    # The OpenAI-2026 platform params the caller set. This route does not
    # forward extras at all, so every one of them is dropped here — and the
    # caller is told which ones (AUDIT #501).
    if callable(warn):
        for name in unhonoured_platform_params(req.extras):
            warn(f"'{name}' is not supported on this route and was dropped")
    instructions_parts: list[str] = []
    input_items: list[dict[str, Any]] = []
    for m in req.messages:
        if m.role == "system":
            instructions_parts.extend(p.text for p in m.parts
                                      if isinstance(p, ir.TextPart) and p.text)
            continue
        if m.role == "tool":
            tool_text: list[str] = []
            for p in m.parts:
                if isinstance(p, ir.ToolResultPart):
                    if p.block_type != "tool_result":
                        # Provider-executed tool result: no function_call exists
                        # for it, so a function_call_output would be an orphan.
                        tool_text.append(p.content or "")
                        continue
                    input_items.append({"type": "function_call_output",
                                        "call_id": p.tool_use_id,
                                        "output": p.content or ""})
            if tool_text:
                input_items.append({"type": "message", "role": "user",
                                    "content": [{"type": "input_text", "text": t}
                                                for t in tool_text if t]})
            continue
        # user / assistant roles
        text_buf: list[str] = []
        image_parts: list[dict[str, Any]] = []
        for p in m.parts:
            if isinstance(p, ir.TextPart):
                if p.text:
                    text_buf.append(p.text)
            elif isinstance(p, ir.ImagePart):
                if p.file_id:
                    # Files-API reference: no Responses input_image spelling
                    # without bytes. Dropped with a warning rather than
                    # stringified into an empty url (the openai base arm has
                    # the same rule).
                    log.warning("dropping_file_id_image", route="responses")
                    if callable(warn):
                        warn("a Files-API image reference was dropped: this "
                             "route has no equivalent")
                    continue
                url = p.url or (f"data:{p.mime};base64,{p.b64}" if p.b64 else "")
                if url:
                    image_parts.append({"type": "input_image", "image_url": url})
            elif isinstance(p, ir.ToolUsePart):
                if p.builtin is not None:
                    # Provider-hosted call: the backend cannot host it and the
                    # client cannot dispatch it, so a function_call here is a
                    # phantom with no output. Its result rides as text.
                    continue
                input_items.append({"type": "function_call",
                                    "call_id": p.id, "name": p.name,
                                    "arguments": json.dumps(p.args)})
            elif isinstance(p, ir.ToolResultPart):
                if p.block_type != "tool_result":
                    # Provider-executed result: fold the payload into the
                    # message text rather than emitting an orphan output.
                    if p.content:
                        text_buf.append(p.content)
                    continue
                input_items.append({"type": "function_call_output",
                                    "call_id": p.tool_use_id,
                                    "output": p.content or ""})
            elif isinstance(p, ir.ThinkingPart) and (p.text or p.encrypted_content):
                # The blob must be sent back verbatim: the Responses API
                # rejects a reasoning item whose ``encrypted_content`` is
                # missing, which is what broke turn 2 of every reasoning
                # session (AUDIT #355).
                item: dict[str, Any] = {"type": "reasoning"}
                if p.text:
                    item["summary"] = [{"type": "summary_text", "text": p.text}]
                if p.encrypted_content:
                    item["encrypted_content"] = p.encrypted_content
                input_items.append(item)
            elif isinstance(p, (ir.AudioPart, ir.DocumentPart)):
                log.warning("dropping_unsupported_part_for_responses",
                            part=type(p).__name__, provider="opencode")
                # The CALLER is told too (AUDIT #498) — the log line is the
                # operator's view, and the caller who attached the file had
                # no way to learn it never reached the model.
                if callable(warn):
                    warn(
                        f"a {type(p).__name__.replace('Part', '').lower()} "
                        f"attachment was dropped: this route does not "
                        f"accept it")
        if text_buf or image_parts:
            content: list[dict[str, Any]] = []
            for t in text_buf:
                ctype = "output_text" if m.role == "assistant" else "input_text"
                content.append({"type": ctype, "text": t})
            content.extend(image_parts)
            input_items.append({"type": "message",
                                "role": "assistant" if m.role == "assistant" else "user",
                                "content": content})
    body: dict[str, Any] = {"model": model_id, "input": input_items,
                            "stream": req.stream}
    if instructions_parts:
        body["instructions"] = "\n".join(instructions_parts)
    mt = g.max_tokens or deployment_params.get("max_tokens")
    if mt:
        body["max_output_tokens"] = mt
    if g.temperature is not None:
        body["temperature"] = g.temperature
    if g.top_p is not None:
        body["top_p"] = g.top_p
    # Reconcile all three effort spellings through the shared resolver so an
    # Anthropic output_config.effort reaches this route too (AUDIT #156).
    effort = g.effective_reasoning_effort()
    if effort:
        body["reasoning"] = {"effort": effort}
    if g.response_format and g.response_format.type != "text":
        if g.response_format.type == "json_schema" and g.response_format.json_schema:
            fmt: dict[str, Any] = {"type": "json_schema",
                                   "name": g.response_format.name or "response",
                                   "schema": g.response_format.json_schema}
            if g.response_format.strict is not None:
                fmt["strict"] = g.response_format.strict
            body["text"] = {"format": fmt}
        elif g.response_format.type == "json_object":
            body["text"] = {"format": {"type": "json_object"}}
    if req.tools:
        tools: list[dict[str, Any]] = []
        for t in req.tools:
            if t.builtin is not None:
                if t.builtin == "web_search":
                    tools.append({"type": "web_search"})
                else:
                    log.warning("dropping_unhostable_builtin_tool",
                                builtin=t.builtin, provider="opencode")
                continue
            fn: dict[str, Any] = {"type": "function", "name": t.name,
                                  "description": t.description,
                                  "parameters": t.parameters_json_schema}
            if t.strict is not None:
                fn["strict"] = t.strict
            # The Responses dialect is the one non-Anthropic surface that hosts
            # tool search, so a deferral flag rides through natively rather
            # than being flattened.
            if t.defer_loading is not None:
                fn["defer_loading"] = t.defer_loading
            # No Responses field for Anthropic's examples: render them into the
            # description so a tool that arrived with worked examples is not
            # indistinguishable from one without.
            if t.input_examples:
                rendered = json.dumps(t.input_examples, ensure_ascii=False)
                fn["description"] = (
                    f"{t.description}\n\nExample inputs:\n{rendered}"
                    if t.description else f"Example inputs:\n{rendered}")
            tools.append(fn)
        if tools:
            body["tools"] = tools
            tc = req.tool_choice
            if isinstance(tc, ir.ToolChoiceNone):
                body["tool_choice"] = "none"
            elif isinstance(tc, ir.ToolChoiceAuto):
                body["tool_choice"] = "auto"
            elif isinstance(tc, ir.ToolChoiceRequired):
                body["tool_choice"] = "required"
            elif isinstance(tc, ir.ToolChoiceNamed):
                body["tool_choice"] = {"type": "function", "name": tc.name}
    # The Responses dialect spells it ``parallel_tool_calls``; an Anthropic
    # client sets ``disable_parallel_tool_use`` instead, which the IR keeps
    # separate. Reading only the former meant a Claude Code request that
    # explicitly serialized its tool calls got concurrent ones anyway
    # (AUDIT #156).
    parallel = g.parallel_tool_calls
    if parallel is None and g.disable_parallel_tool_use is not None:
        parallel = not g.disable_parallel_tool_use
    if parallel is not None:
        body["parallel_tool_calls"] = parallel
    for k, v in deployment_params.get("extra_body", {}).items():
        body.setdefault(k, v)
    return body


def _decode_responses_response(body: bytes) -> ir.AssistantTurn:
    data = orjson.loads(body)
    turn = ir.AssistantTurn(raw=data if isinstance(data, dict) else {})
    if not isinstance(data, dict):
        return turn
    for item in as_list(data.get("output")):
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype == "message":
            for c in as_list(item.get("content")):
                if isinstance(c, dict) and c.get("type") in (
                        "output_text", "text", "refusal"):
                    turn.text += c.get("text") or c.get("refusal") or ""
        elif itype == "reasoning":
            # The opaque ``encrypted_content`` blob rides on the part: the
            # Responses API demands it back on the next turn, and dropping it
            # here broke reasoning-model multi-turn on turn 2 (AUDIT #355).
            blob = item.get("encrypted_content")
            blob = blob if isinstance(blob, str) and blob else None
            for s in as_list(item.get("summary")):
                if isinstance(s, dict) and s.get("text"):
                    turn.thinking.append(ir.ThinkingPart(
                        s["text"], encrypted_content=blob))
            if blob and not turn.thinking:
                turn.thinking.append(ir.ThinkingPart("", encrypted_content=blob))
        elif itype == "function_call":
            raw_args = item.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else {}
            except json.JSONDecodeError:
                args = {}
            turn.tool_calls.append(ir.ToolUsePart(
                id=str(item.get("call_id") or item.get("id") or ""),
                name=str(item.get("name") or ""),
                args=args if isinstance(args, dict) else {},
                raw_args=raw_args if isinstance(raw_args, str) else "{}"))
        # web_search_call and other hosted traces are provider-executed:
        # their text (if any) already arrived as message items — never emit
        # a phantom function call the client would try to execute.
    status = data.get("status")
    if status == "incomplete":
        turn.stop_reason = "length"
    elif status == "failed":
        turn.stop_reason = "stop"
    elif turn.tool_calls:
        turn.stop_reason = "tool_call"
    else:
        turn.stop_reason = "stop"
    u = as_dict(data.get("usage"))
    in_det = as_dict(u.get("input_tokens_details"))
    out_det = as_dict(u.get("output_tokens_details"))
    # Shared coercion, not raw int() — a typed-wrong usage value must not
    # 502 the sync decode (AUDIT #233).
    turn.usage = ir.Usage(
        prompt_tokens=ir.coerce_int(u.get("input_tokens")) or 0,
        completion_tokens=ir.coerce_int(u.get("output_tokens")) or 0,
        cached_tokens=ir.coerce_int(in_det.get("cached_tokens")) or 0,
        reasoning_tokens=ir.coerce_int(out_det.get("reasoning_tokens")) or 0,
        # Responses-wire convention: input_tokens INCLUDES cached.
        cached_in_prompt=True,
    )
    return turn
