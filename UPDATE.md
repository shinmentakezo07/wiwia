# UPDATE.md — Translation Layer Fixes & OpenRouter Adapter

> **Session date**: 2026-08-23
> **Status**: All changes verified — 213 tests pass, ruff clean
> **Scope**: OpenAI ↔ Anthropic cross-provider translation fixes, OpenRouter dedicated adapter, multi-turn conversation bug fix

**This document is binding reference material.** Any agent touching the translation layer — OpenAI↔Anthropic cross-provider translation, OpenRouter adapter, multi-turn conversation handling, `reasoning`/`reasoning_effort` mapping, tool_result / `content: null`, `stream_options`, or upstream error extraction — **MUST read this file first** before changing any wire codec (`wiwi/wire/`) or provider adapter (`wiwi/providers/`). The fixes recorded here are the reason the current code behaves correctly; changing those paths without reading this doc is the most common way to silently re-introduce a bug that was already fixed.

Each entry below records the before→after state, the exact files and lines changed, and the tests that cover it. When extending or modifying any of those areas, check this file for existing invariants before writing new code. When a new fix lands in one of these areas, add an entry to this file so a later agent does not rediscover and re-fix the same thing.

## Round 204 — implicit state machines declared and made checkable (AUDIT #595) (2026-11-09)

Three state machines were implemented to the streaming contract but none was
*checkable*. This round makes them explicit without changing what any encoder
emits.

### The delta-stream contract is now executable

`wiwi/streaming/deltas.py` documented the ordering rules in prose and every
encoder relied on them ("adapters guarantee legality; encoders never defend").
A violation was therefore invisible: an `ArgsDelta` for a closed index or a
`Finish` before `StreamStart` produced a malformed client stream with no log
line.

`wiwi/streaming/machine.py` adds `DeltaStreamState` — a pure observer whose
`observe(delta)` returns the first problem or `None` and **never raises** — plus
`validate_stream()` for tests. The subtle parts of the contract are honoured
rather than simplified: parallel tool calls are siblings that may close in any
order, `UsageFinal` may repeat and may precede the last content delta, and
`StreamError` is legal from any phase (including after `Finish`) and needs no
`StreamEnd`.

Wired into the live loop in `wiwi/server/app.py` as `_watch_delta`, next to the
existing `_capture_delta` seam. It is strictly diagnostic — the only outcome is a
`stream_contract_violation` warning carrying the delta index and state — so it
cannot alter or break a response.

### The provider-key lifecycle is declared once

`ProviderKey.status` had five states mutated from six sites. Two of them are
both `"invalid"` and differ only by `cooldown_until`, so the state of a key was
legible only by knowing how four fields interact. Now:

- `KEY_STATUSES` plus a transition table at the head of `router.py` states the
  lifecycle in one place.
- `ProviderKey.explain()` renders the derived meaning in one line, so a timed
  retirement ("revives in 540s") is distinguishable from a dead credential
  ("until an operator or the healer resets it").
- The five OAuth-grant resets (`server/app.py` ×2 and
  `cline`/`hermes`/`workbuddy_auto_refresh.py`) now call
  `recover(force=True)` instead of hand-setting `status`/`cooldown_until`. The
  hand-rolled form omitted the `err_count` reset that makes a revival survive
  the next failure — the exact defect AUDIT #199 fixed on the admin path but
  not on these five.

### The encoders share one state vocabulary

`ChatStreamEncoder`, `AnthropicStreamEncoder`, `ResponsesStreamEncoder` and
`CompletionStreamEncoder` now each expose `explain()`, all delegating the
formatting to `streaming.machine.explain_encoder`. Previously the same concept
had three private names (`_open_block`/`_tool_blocks`, `_item_open`/`_tools`,
`_open_calls`/`_deferred`) and tests coupled to them.

```text
ChatStreamEncoder	open=none tool_calls=2 open_tools=[0, 1] deferred=1item/4ch usage=none
```

### Verification

`tests/test_fix_round204.py` — 27 tests: legality of every documented shape,
specific reasons for seven violation classes, `observe()` never raising and
keeping the first problem, the key transitions and `explain()` output, the OAuth
sites no longer hand-setting status, and `explain()` on all four encoders. The
live-path observer was confirmed silent across `test_integration.py`,
`test_streaming_improvements.py` and `test_codecs.py` (110 tests, zero
`stream_contract_violation` warnings).

## Round 202 — IR reasoning accessors are total; extras denylist guarded (AUDIT #591–#593) (2026-11-08)

Three fixes in `wiwi/ir/`, found by fuzzing every exported helper and driving the
reasoning knobs through all 13 adapters' `encode_request`. **All three were
latent** — every live codec already guards effort at the boundary — but the
helpers are exported and their contract is totality, so the guards belong there
(the rule #351/#387/#562–#564 set for this package).

- **A typed-wrong effort no longer reaches an adapter.**
  `GenParams.effective_reasoning_effort` returned `reasoning_effort`/`effort`
  verbatim; `nim_adapter.py:192` hashes the result and raised
  `TypeError: unhashable type: 'list'` — a 500 blaming a healthy upstream. Both
  reads in both accessors now `isinstance(..., str)`-guard. A non-`str` counts as
  *absent*, so the documented precedence continues to the next knob rather than
  discarding the whole reasoning intent. **Invariant to preserve:** an explicit
  off-switch (`thinking_type=disabled`) still outranks a separate effort knob
  (round 178), and an empty effort string is still falsy.
- **The fractional-budget contract is now documented honestly.**
  `effective_thinking_budget`'s docstring claimed any float was preserved, but
  #389 routed the field through `coerce_int`, which requires `.is_integer()`.
  The prose was corrected rather than the code re-widened: a token budget is an
  integer on every wire, and `test_fix_round124.py:220` pins the integral-float
  case. **Consequence worth knowing:** a fractional budget resolves to `None`,
  and a caller that also set `thinking_type=enabled` then falls back to the
  default level's budget (8000), not to no thinking.
- **`carry_extras` guards `known`, not just `source`.** A non-container denylist
  raised `TypeError` from `k not in known`, and `len(known)` raised separately.
  An unusable denylist now drops nothing, which is the conservative reading —
  losing an unmodelled body key is the defect the helper exists to prevent.

**Files:** `wiwi/ir/types.py`, `wiwi/ir/translation.py`,
`tests/test_fix_round202.py`.

---

## Round 201 — cancelled streams do not mint completed Responses (AUDIT #590) (2026-11-05)

A streamed Responses turn is now persisted only after its `StreamEnd` delta.
Previously, teardown checked only for an error frame; a client disconnect or an
abandoned generator could therefore persist partial content as a `completed`
response, leaving a valid-looking `previous_response_id` handle for an unfinished
turn. The store gate now requires a clean terminal delta, while preserving the
existing streamed payload rebuild and error handling.

**Files:** `wiwi/server/app.py`, `tests/test_fix_round201.py`.

---

## Round 200 — SSE event order and parallel-tool group boundaries (AUDIT #588–#589) (2026-11-05)

Two streaming fixes, reproduced through the real parser and wire encoders:

- **Mixed framing preserves arrival order.** `LineSSEParser(allow_unframed=True)`
  previously returned a bare JSON event immediately while an earlier `data:`
  frame was still buffered. The later event now waits in a one-slot pending
  queue, so `feed_line` returns the older frame first and the next line or
  `flush()` returns the queued event. No events are dropped and the existing
  one-event-per-call API is unchanged.
- **Text and thinking wait for every sibling tool.** Closing the most recently
  opened tool no longer clears the shared open-tool state when another sibling
  remains. Anthropic therefore does not open a text block inside a tool group,
  and Responses does not open a message before the remaining function call's
  done event. `_open_tool` follows a remaining sibling so `StreamEnd` closes it
  before the deferred message.

**Files:** `wiwi/streaming/sse.py`, `wiwi/wire/anthropic_messages.py`,
`wiwi/wire/openai_responses.py`, `tests/test_fix_round200.py`.

---

## Round 198 — document file ids, Completions suffix refusal, zero token caps (AUDIT #583–#585) (2026-11-05)

Three request-translation fixes from the wire review:

- **Responses document references survive.** `input_file.file_id` now decodes to
  `DocumentPart(file_id=..., name=...)` instead of disappearing. The existing
  Anthropic adapter emits it as a native `document` block with
  `source.type=file` and preserves `filename` as `title`.
- **Completions rejects unsupported infill.** A non-empty `suffix` now raises
  `DialectError` rather than being concatenated after the prompt. Infill means
  generation *before* the suffix; concatenation silently requested a different
  completion. The old behavior-pinning codec test was replaced.
- **Explicit zero token caps survive provider encoding.** OpenAI, Gemini and
  OpenCode now distinguish an absent cap (`None`, allowing the deployment
  default) from an explicit zero, which is emitted unchanged. This matches the
  existing Anthropic precedence and prevents a zero-cap request from becoming
  default-sized or uncapped.

**Files:** `wiwi/wire/openai_responses.py`, `wiwi/wire/openai_completions.py`,
`wiwi/providers/openai_adapter.py`, `wiwi/providers/gemini_adapter.py`,
`wiwi/providers/opencode_adapter.py`, `tests/test_codecs.py`,
`tests/test_fix_round198.py`.

---

## Round 196 — hosted-call replay decodes, not drops; streamed store failures are loud (AUDIT #573–#575) (2026-11-04)

The `previous_response_id` continuation path is where a turn's search trace
used to vanish. The Responses surface itself *emits* `web_search_call` and
`tool_search_call` items, and the store persists the streamed turn verbatim
(round 195). But the replay decoder (`decode_request`) skipped both item
types with only a `log.warning("responses_unsupported_input_item")` — so the
next turn's upstream saw the follow-up user message and the assistant's text,
but no record that a search had run. Codex users hit this every time a
hosted-search turn was continued by id.

- **I1 (decoding):** `web_search_call` → `ToolUsePart(id=<item.id>,
  name="web_search", builtin="web_search", block_type="server_tool_use",
  args={"query": action.query})`. `tool_search_call` →
  `builtin="tool_search_bm25"` (bm25 owns the shared `tool_search` wire type
  by registration order, so re-encoding lands back on `tool_search_call`).
  The `call_id` on a `tool_search_call` is preferred over the `id` for the
  IR `id` when present; `web_search_call` has only `id`. An item with no
  readable id is dropped (it pairs with nothing).
- **I1b (Anthropic pairing gate):** the Anthropic adapter's request path
  previously re-emitted a hosted `ToolUsePart` verbatim with no pairing
  check, so a stored Responses continuation replayed to Anthropic would
  carry an unpaired `server_tool_use` and the API would reject the history
  with a 400. `encode_request` now computes the hosted ids and their paired
  results in the history and: replays a paired call verbatim as the same
  block type (the Anthropic→Anthropic round trip, unchanged), drops an
  unpaired hosted call with a caller-visible warning, and folds an
  unpaired hosted result's payload to text (images and `tool_reference`
  blocks keep their form). Because the Responses dialect carries no result
  item at all, a Responses→Anthropic continuation ships no hosted block;
  paired history is the only shape that goes to Anthropic. Prior revisions
  of this entry claimed the suppression existed — it did not, on the
  request path; round 196 implements it.
- **I2 (round trip):** the Responses sync encoder re-emits the same hosted
  item keyed off `builtin` (`_builtin_call_item`, unchanged), so an
  Anthropic→Responses or Chat→Responses continuation carries the search the
  turn performed. The Chat surface drops it with a warning — neither
  invents a `tool_calls` frame the client cannot dispatch, and neither
  sends an unpaired `server_tool_use` to Anthropic.
- **I3 (force_stream arm):** `gateway.py` previously discarded
  `ToolCallOpen.builtin` when building the IR `ToolUsePart` — so even a
  correctly decoded/streamed hosted turn lost its identity on a
  non-streaming path. The carrier fields are now forwarded like the pump
  does (plus `block_type`).
- **I4 (store failure is loud):** the streamed store write inside
  `_teardown_tail` was wrapped in `contextlib.suppress(Exception)` — a DB
  downtime silently lost every streamed continuation id with no signal. It
  now catches the exception and logs a warning + `log_proxy("error", …)`,
  matching the cache-hit and singleflight twins. The turn still completes;
  the store stays best-effort.
- **I5 (#573 declined):** a `tool_call` stop with no tool item remains
  `completed` on the Responses surface. AUDIT #272/#358 already ruled this
  non-actionable; round 196 re-verified against source and did not invent a
  wire `incomplete_details.reason` the spec does not define.

**Invariants.**
1. Hosted calls are *evidence*, not instructions: no dialect emits them as
   client-dispatched calls.
2. A hosted call without a paired result is never re-emitted to a surface
   that would reject the unpaired shape; paired history replays verbatim
   and unpaired history is dropped or folded to text. Responses is the one
   surface whose protocol models the call as self-contained.
3. Unknown hosted items (`computer_call`, `mcp_call`, `file_search_call`, …)
   are still skipped with the warning.
4. Search *results* are never fabricated — the gateway stores what the
   provider produced, nothing more.

**Files:** `wiwi/wire/openai_responses.py` (decode), `wiwi/core/gateway.py`
(force_stream carrier), `wiwi/server/app.py` (streamed store failure log),
`wiwi/providers/anthropic_adapter.py` (request-side pairing gate + image-block
helper), `tests/test_web_search_translation.py` (pinned test flipped to the
new behaviour), `tests/test_fix_round196.py`.

**Tests:** `tests/test_fix_round196.py`.

---

## IFM (MBZUAI) adapter — reasoning_content replay and `chat_template_kwargs` effort (2026-10-30)

New provider type `ifm` (`wiwi/providers/ifm.py`, `wiwi/providers/ifm_adapter.py`).
IFM's hosted API is OpenAI Chat Completions shaped, but its K2 Horizon / Jais 3
chat templates reject an ordinary OpenAI body. Each requirement is documented at
`docs.ifm.ai` and is provider-specific, so it lives under `providers/`:

- **`reasoning_content` on every assistant turn.** The model is stateless, and
  a history turn whose trace was stripped is rejected with a 400 on turn 2.
  The field is `reasoning_content` (`reasoning` is a mirror), and **`""` is a
  legal trace** — it must be replayed rather than dropped or sent as `null`.
  The base `OpenAIAdapter` only emits the field when the IR turn carried
  reasoning, so the IFM adapter pads the rest with `""`. This is the same
  *presence* rule the DeepSeek helper applies (`providers/deepseek.py`), but
  unconditional here: IFM has no "tools required" gate.
- **Text-only string content.** Content-parts arrays are flattened and
  `image_url` / `input_audio` / `file` parts are dropped with a
  caller-visible warning; `content: null` on a tool-call-only assistant turn
  becomes `""`.
- **`reasoning_effort` via `chat_template_kwargs`.** The base adapter emits a
  top-level `reasoning_effort` for any provider type it does not recognise as
  a compatible gateway, so the adapter removes that and writes
  `chat_template_kwargs.reasoning_effort`, clamped to IFM's `low` / `medium` /
  `high`. `minimal`→`low`, `xhigh`/`max`→`high`; `"none"` is dropped with a
  warning because IFM always reasons.
- **`X-Session-ID` affinity.** Stable per conversation — the caller's
  `x-wiwi-session-id` when present, otherwise a digest of the shared prefix
  (model, system prompt, first user turn) — so turn N+1 lands on the worker
  holding the prefix's KV cache. The gateway now passes `ctx.session_id` into
  `encode_request`'s `deployment_params` on all three paths as generic request
  context.
- **`stop` capped at four sequences**; **`top_k` forwarded** (see below);
  OpenAI-platform parameters IFM does not document (`store`, `metadata`,
  `prompt_cache_key`, `logit_bias`, `service_tier`, …) dropped with warnings.
- **`OpenAIAdapter.supports_top_k`.** The base adapter used to drop `top_k`
  with a warning for every Chat-wire provider. A vLLM-backed upstream that
  honours it now opts in via the class flag; the default (`False`) keeps every
  existing provider byte-identical.

Coverage: `tests/test_ifm_adapter.py` (29 tests), including an end-to-end
Anthropic-in → IFM-out → Anthropic-back request against a mocked endpoint.

---

## Round 186 — an unreadable Anthropic block index is dropped, not guessed onto block 0 (AUDIT #419) (2026-10-30)

`wiwi/providers/anthropic_adapter.py` — the upstream Messages SSE decoder.

- **`_block_index` now returns `int | None`.** A numerically whole float is
  still normalized (`1.0` → `1`, the same block); a value that names no block
  (list/dict/str/bool/non-integral float) returns `None` instead of `0`. Round
  128's `0` fallback kept the frame "so the fragment is not lost", but `0` is a
  real block: with a call open there, the fragment was appended to a
  *different* call's JSON and the stream still terminated cleanly, so no
  downstream layer could detect the corruption.
- **`input_json_delta` gates on `idx in self._tool_indices`.** An args fragment
  is only emitted inside `ToolCallOpen → ArgsDelta* → ToolCallClose`; an orphan
  fragment (unreadable index, or a block that never opened) is dropped instead
  of emitted for the encoders to discard.
- **The start and result arms drop an unreadable index too**, so a malformed
  `content_block_start` cannot supersede the call already live on block 0.
- **An unreadable `content_block_stop` closes the only open call** when exactly
  one is open (the only block it can refer to); with several open it is
  ambiguous and the frame is dropped rather than closing the wrong call. This
  keeps a tool block from being orphaned by a malformed stop.
- **#420 re-pinned, unchanged.** The `[DONE]` terminator on an anthropic-typed
  upstream was already fixed in round 162; the new coverage keeps it fixed.

Coverage: `tests/test_fix_round186.py` (10 tests), plus the round-128 and
round-78 tests that had pinned the `0` fallback (they now open the block they
send a fragment for, which is the IR contract).

---

## Round 184 — the flagged-network 403 is an entitlement refusal (AUDIT #557) (2026-10-30)

One classification gap in `wiwi/providers/base.py`, in the same family as the
quota 429 above.

- **`403` + network-abuse wording → `permission_error`.** OpenCode's free tier
  refuses a request from a flagged egress with a 403 that says "we've detected
  abusive traffic from this IP address. This isn't related to your account." No
  entitlement marker matched, so `_classify_provider_status` took the credential
  branch: `authentication_error`, `status_for_key_pool` 403, and
  `ProviderAccount.on_result` retired a healthy key for 600 s — the one reading
  the vendor's body rules out. `_entitlement_kind` now returns a third kind,
  `"network"` (markers `_NETWORK_BLOCK_MARKERS`), and the classifier renders
  `403 permission_error` with "blocked this network" in the message. The key is
  left in rotation and the request still fails over.
- **Deliberately not fixed by rotation.** The same egress gets the same 403;
  changing the source address or session is the vendor's abuse control, not a
  repair. The docstring and `docs/OPENCODE.md` say so, so the next agent does
  not read "blocked network" as "retry harder".

Coverage: `tests/test_fix_round184.py` (7 tests) — the live wording and three
variants, the key-not-retired accounting, and controls for a genuine 403 and the
`FreeTierError` shape refusal.

---

## Round 183 — a quota 429 is an entitlement refusal, not a rate limit (AUDIT #556) (2026-10-30)

One classification defect, reachable from every inbound dialect through the
`opencode` provider, plus the mid-stream half of the same defect.

- **`429` + quota marker → `402 permission_error`** (`wiwi/providers/base.py`).
  `_classify_provider_status` keyed the 429 arm on the status alone, so OpenCode
  Zen's `429 FreeUsageLimitError` ("Free usage limit reached … subscribe to
  OpenCode Go") became `429 rate_limit_error` and `status_for_key_pool` charged
  it to the key. Every free Zen deployment shares one `anonymous` sentinel, so
  one exhausted allowance cooled the only credential they all use and the tier
  answered `503 no healthy deployment` for the stated `Retry-After` (hours).
  The arm now consults `is_quota_refusal(body_text)` — the same billing markers
  the 401/403 branch already used (`FreeUsageLimitError`, `GoUsageLimitError`,
  `BlackUsageLimitError`, `MonthlyLimitError`, `UserLimitError`, plus the
  "usage limit reached" wording) — and returns `402 permission_error`,
  `retryable=True`, `upstream_type` preserved. The key is left in rotation and
  the request still fails over. A 429 with no quota wording is unchanged.
  `status_for_key_pool`'s docstring records the new branch.
- **The mid-stream envelope takes the same classification**
  (`wiwi/providers/opencode_adapter.py`). A refusal can arrive inside a 200 SSE
  stream, where there is no HTTP status; `_envelope_stream_error` and the
  Responses `response.failed` arm now build their frame through `_stream_error`,
  which tags a quota refusal `status=402, etype="permission_error"` and logs
  `opencode_free_quota_exhausted`. Without it, `Gateway._note_stream_failure`
  derived 502 → 429-class accounting from the bare frame and charged the shared
  sentinel mid-stream.
- **Docs corrected, not appended** (`docs/OPENCODE.md`, adapter docstring).
  The claim "free quota is accounted per session" was wrong: OpenCode's support
  statement (2026-10-22, issue #58205) says the allowance is enforced for the
  account, workspace **and network**, and the reset window is not exposed. The
  adapter reuses one session per credential because that is what the client
  does, not because it buys quota, and it deliberately does **not** rotate the
  session id or the egress address on an exhaustion error — the vendor's
  separate "abusive traffic from this IP address" refusal exists to detect
  exactly that.

Coverage: `tests/test_fix_round183.py` (13 tests) — classification from the type
and from the wording alone, plain-429 control, sentinel-in-rotation in both
failover modes, mid-stream and Responses tagging, generic-stream control.

---

## Round 182 — the wire half of the cache/server/streaming/wire sweep (AUDIT #546-#554) (2026-10-30)

Nine codec/translation defects, one of them (the finish-reason map) reachable
from every inbound surface. Nothing here changes a *shape* the adapters already
emit; each fix makes an inbound dialect decode, or an outbound encoder emit,
what the sibling surface already did.

- **`context_window_exceeded` → `length`** (`ir/translation.py`). The IR keeps
  the overflow reason distinct so an auto-compact client can see it, but
  `_IR_TO_OPENAI_FINISH` had no entry and the fallback produced `"stop"` — the
  one value a Chat/Completions client does *not* read as "context full". The
  Responses surface already reported `incomplete` (AUDIT #358); the two
  OpenAI-shaped surfaces now agree. `pause_turn`/`compaction` keep the
  documented fallback (no OpenAI spelling exists).
- **Legacy `functions` / `function_call` / `role:"function"`**
  (`wire/openai_chat.py`). `functions[]` is decoded into the same `ir.Tool` set
  as `tools[]` (a type-less entry counts as `type=="function"`, wrapped or
  inline), `assistant.function_call` becomes a `ToolUsePart`, and a
  `role:"function"` message becomes a `ToolResultPart` instead of being coerced
  to `"user"`. The old protocol carries **no call id** on either side of the
  pair, so `_legacy_call_id(name)` derives one from the function name: the same
  name on both sides pairs, which is exactly the association the legacy wire
  encodes. The request-level `function_call` maps onto the same `ToolChoice`
  the modern `tool_choice` produces.
- **`/v1/completions` explicit defaults** (`wire/openai_completions.py`).
  `logprobs: false` and `echo: false` are the wire defaults and are now
  accepted (only a truthy request is unsupported); a present but non-integer
  `best_of` (`"2"`) is a 400 instead of being forwarded through `extras`.
- **Nothing content-bearing after `event: error`**
  (`wire/anthropic_messages.py`). The `StreamError` arm now clears the
  interleaved-text buffer and the pending thinking signature. The app still
  calls `final_frame()` on that path to terminate the stream well-formed
  (`message_delta` + `message_stop`); it no longer resurrects buffered content
  as new blocks *after* the terminal error.
- **One id per hosted item, and linear argument accumulation**
  (`wire/openai_responses.py`). `_hosted_item_id` mints `ts_`/`ws_` from the
  builtin for both `output_item.added` and `output_item.done` (the close half
  always minted `ws_`, so a `tool_search` was announced as one item and
  completed as another). Tool arguments accumulate into a **list** joined once
  in `_close_tool`; the previous `+=` per fragment made a large call quadratic
  on the event loop, the same shape the gateway pump and the Anthropic encoder
  already use.
- **A `file` block with `file_id` is a DOCUMENT** (`wire/openai_chat.py`, plus
  `providers/openai_adapter.py`). It decoded to `ImagePart` while `file_data`
  decoded to `DocumentPart`, so a PDF file id re-encoded on an Anthropic
  upstream as an `image` block and was rejected. The adapter now re-emits
  `{"type":"file","file":{"file_id":…}}` for a `DocumentPart` instead of
  dropping the reference.
- **`input_audio` type guard** (`wire/openai_chat.py`). A non-string payload
  (`data: 5`, a dict) is dropped rather than forwarded as `AudioPart(b64=5)`,
  matching the Responses twin; a non-string `format` falls back to `wav`.
- **`with_history` leaves a malformed `input` alone**
  (`wire/openai_responses.py`). It used to replace a non-list/non-string
  `input` with the stored history, so the caller's turn vanished and
  `decode_request`'s own rejection (AUDIT #352) never ran. The bad value now
  reaches the decoder, which answers 400.

**Tests:** `tests/test_fix_round182.py`; `tests/test_fix_round172.py::
test_chat_decode_reads_file_part` and `tests/test_bugfix_round5.py::
test_responses_encoder_args_buf_is_instance_attr` were updated where the audit
changed the behaviour they pinned.


## Round 179 — unknown server tools round-trip on their own surface (AUDIT #531) (2026-10-29)

`builtin_config["_wire_type"]` was written by both decoders and read by nobody.
An Anthropic client that sent a server tool the canonical registry does not
know (`code_execution_20250522`, `bash_20250124`, `computer_*`, `memory_*`, …)
got a silently tool-less request even against an Anthropic deployment, because
`anthropic_adapter._encode_tools` consulted `wire_type_for()` alone and dropped
what it could not map. The drop was visible only to the operator.

- `builtin_tools.WIRE_SURFACE_KEY` (`"_wire_surface"`) now records the surface
  that DECLARED the spelling, written beside `_wire_type` by
  `wire/anthropic_messages.py` and `wire/openai_responses.py`.
- `builtin_tools.passthrough_wire_type(surface, tool)` returns that raw type
  only when it names the target surface, and is total against a missing /
  non-mapping config or a non-`str` value.
- The Anthropic encoder re-emits the raw spelling for its own surface and keeps
  dropping a foreign one (Responses' `file_search` must never become a Messages
  tool). Every drop now also calls `self.warn(...)`, so the caller learns the
  tool did not apply (the #498 channel).
- Other adapters are unchanged: Gemini/OpenAI still drop an Anthropic-declared
  unknown builtin, pinned by a test.

**Tests:** `tests/test_fix_round179.py` (12; also pins AUDIT #529/#530).


## Round 174 — structured-output schema coerced at every inbound decoder (AUDIT #359) (2026-10-25)
`ResponseFormat.json_schema` is typed `dict | None` but is a plain dataclass
with no runtime enforcement, and all three inbound decoders accepted any
client value verbatim. A junk `{"schema": "oops"}` crossed the IR and
re-encoded upstream — Anthropic's native `output_config.format.schema`, the
OpenAI dialect's `response_format.json_schema.schema`, Gemini's
`responseSchema` — producing an upstream 400 that named no field the caller
controls. The registered entry blamed two decoders; the third (Chat) was
unguarded on its *inner* schema (`openai_chat.py` guarded only the wrapper,
AUDIT #100's fix), and a junk `name` rode along in all three.

All three decoders now coerce a non-dict `schema` to `{"type":"object"}` and a
non-string `name` to `None` — the same convention `parameters_json_schema`
already uses for tool schemas. One trap closed while fixing: Chat's
`json_object` arm must keep `json_schema=None`, or the coercion would leak a
`json_schema` member into a wire shape (`response_format.type=json_object`)
that has none and the API would 400.

**Tests:** `tests/test_fix_round174.py` (12).


## Round 173 — Gemini mid-conversation system messages, pinned (2026-10-25)

The Gemini **encoder**'s system-message rule, now that it is pinned
(`tests/test_fix_round173.py`; AUDIT #328's status corrected — the fix was in
the code, only the pin was missing):

- **Leading** `system` messages (and only those) become
  `systemInstruction` — consecutive leading ones join.
- A system message that appears **after** the first non-system turn cannot be
  its own `contents` entry (Gemini accepts only `user`/`model` roles), so it
  is folded **in place** as `[system]`-marked user text: merged into the
  adjacent preceding user entry when there is one, or opening its own user
  entry after a model turn. Hoisting it to the front — the pre-#328
  behaviour — destroys its position relative to the turns around it.
- An empty mid-conversation system part contributes nothing: no phantom user
  entry, no empty instruction part.

The Anthropic adapter keeps its own rule (mid-conversation system stays a
`role: "system"` message, `anthropic_adapter.py`): Anthropic's wire has the
role, Gemini's does not. Do not "unify" these into one helper.

## Round 164 — The translation layer's loss points (2026-10-19)

Found while auditing the whole decode → IR → re-encode path — every inbound
codec, every outbound adapter, the IR and the delta taxonomy — for
information that is lost on the way through. Entries 496–501 in `AUDIT.md`;
regression coverage in `tests/test_fix_round164.py`.

The unifying shape: the IR is not the problem (it models the intersection of
the dialects correctly, and the lossless paths are lossless) — the loss is
in three places around it. A *carrier* gap (the IR has no field for
something), a *silence* gap (the carrier exists but the caller is not told),
and a *billed-but-unreturned* gap (the upstream charged for a feature the
client never received).

### 164.1 `logprobs` now round-trips through the IR

**Files**: `wiwi/ir/types.py` (`AssistantTurn.logprobs`), `wiwi/streaming/deltas.py`
(`LogprobsDelta`), `wiwi/providers/openai_adapter.py` (decode),
`wiwi/wire/openai_chat.py` (encode + stream encode),
`wiwi/wire/openai_responses.py` (encode), `AUDIT.md` #326.

**Symptom**: a client sends `{"logprobs": true, "top_logprobs": 5}`. The value
rides `Request.extras` untyped, is forwarded upstream, and the tokens are
**billed as output** — but no adapter decoded `choices[0].logprobs`, no IR
field held it, and no encoder emitted it. The client silently received no
logprobs for money it had already paid.

**After**: `AssistantTurn.logprobs: dict | None` holds the provider's own
`choices[0].logprobs` dict, and `dl.LogprobsDelta` carries the streaming
half (a token's logprobs arrive with that token, so they cannot wait for the
turn's end). The OpenAI adapter decodes both; the Chat and Responses
encoders emit both.

**Invariant**: `logprobs is None` means "the upstream returned no logprobs"
and is distinct from an empty dict, which means "the upstream answered and
had no content to score". Both encoder keys are omitted when the value is
`None`, so a response never grows a phantom field.

**Watch out**: `ChatStreamEncoder` reuses one `choices[0]` dict across every
frame, so the `logprobs` key must be popped in `_shell` — otherwise one
token's logprobs ride along on every later frame and claim to describe a
different token.

### 164.2 `stop_sequence` reaches the OpenAI-shaped callers

**Files**: `wiwi/wire/openai_chat.py` (`encode_response`),
`wiwi/wire/openai_responses.py` (`encode_response`), `AUDIT.md` #497.

**Symptom**: `AssistantTurn.stop_sequence` exists to carry "which
caller-supplied stop sequence fired", and the Anthropic adapter populates
it — but no OpenAI-shaped encoder read it. OpenAI's `finish_reason`
vocabulary has no `stop_sequence` value, so the information stopped at the
encoder.

**After**: `openai_chat` emits `choice["stop_sequence"]` and `openai_responses`
emits `response["stop_sequence"]`, both only when the value is present.

**Invariant**: the matched sequence is a *caller-visible* fact, not an
internal one. A turn that stopped on a caller-supplied sequence must be
distinguishable from one that stopped on its own.

### 164.3 The caller-visible warning channel is wired into every adapter

**Files**: `wiwi/providers/base.py` (new `WarningMixin`),
`wiwi/providers/{openai,openrouter,anthropic,gemini,opencode}_adapter.py`,
`wiwi/server/app.py` (`_with_warnings`), `AUDIT.md` #498.

**Symptom**: `providers/base.py` documents a mechanism for telling the
*caller* that the provider could not honour something the caller asked for —
and only the Gemini adapter used it. Every other drop (a hosted tool the
backend cannot run, a `top_k` it has no field for, a `seed`, a dropped
attachment) was visible only to the *operator* in the proxy log. Even
Gemini's warnings went only into the request log: the caller had no channel
at all.

**After**: three parts.

1. `providers/base.py` gains `WarningMixin` — the attribute, its reset, and
   one `warn()` method. It lives in `base.py` because it is a generic
   contract with no provider-specific logic, which is the only part of
   `providers/` the layering rule lets `core/` import. Adapters that do not
   inherit it are unaffected (`take_adapter_warnings` is total).
2. Every adapter now warns on the drop it performs. The OpenCode adapter
   forwards its three sub-adapters' warnings up to itself — they would
   otherwise be collected and thrown away, so the caller learned nothing on
   those routes.
3. `server/app.py` gains `_with_warnings`, which puts the messages in an
   `x-wiwi-translation-warnings` response header.

**Invariant**: a warning is keyed on the **flag**, never the name. A caller
may legitimately define a function tool called `web_search`; a name match
deleted that real call once (AUDIT #156) and would now report a perfectly
honoured tool as dropped.

**Why a header**: the response body is dialect-owned. An OpenAI-shaped body
cannot carry an Anthropic-shaped note without breaking the client's schema
expectations. A header is additive and every HTTP client surfaces it. It is
absent when there are no warnings, so a clean request's response is
unchanged; the full list always remains in the request log.

### 164.4 An unsigned thinking block is dropped, not sent to Anthropic

**Files**: `wiwi/providers/anthropic_adapter.py` (`encode_request`),
`AUDIT.md` #500.

**Symptom**: `RESEARCH.md:67` — "Anthropic signatures MUST round-trip
unmodified or 400." The adapter sent `{"type": "thinking", "thinking": ...}`
with **no `signature`** whenever the source had none, so the block was
rejected by the upstream on replay, naming nothing the caller sent.

Two reachable paths: a Responses `reasoning.encrypted_content` blob (which
no Anthropic-shaped source signs) replayed to an Anthropic upstream — AUDIT
#355 added the carrier, and its fix sketch also said to "skip (or refuse) a
`ThinkingPart` with no signature rather than send it unsigned", but only
the `encrypted_content` half landed — and an OpenAI-shaped client's
`reasoning_content` echoed back as history (the Chat codec lifts it into a
`ThinkingPart`, and the OpenAI dialect has no notion of a signature to sign
it with).

**After**: the block is dropped and the caller is warned. A dropped thinking
block is a history the API still accepts — the model just loses the visible
reasoning it produced. An unsigned replay is a guaranteed 400. The
`redacted_thinking` blob is unaffected: it IS the signed material, and it
still replays verbatim.

### 164.5 `json_schema` reaches Gemini as `responseSchema`

**Files**: `wiwi/providers/gemini_adapter.py` (`encode_request`),
`AUDIT.md` #496.

**Symptom**: only `response_format.type == "json_object"` was handled (as
`responseMimeType`). A caller asking for `json_schema` got **nothing** — no
mime type, no schema, no error — so the model answered in prose and the
caller had no way to know its constraint was ignored.

**After**: `json_schema` emits both `responseMimeType: "application/json"`
and `responseSchema` (with the schema's `name` as `title` when present). A
schema that is not a JSON object is dropped with a caller-visible warning
rather than forwarded as a malformed `responseSchema`.

**Note**: the Anthropic surface already had a native spelling
(`output_config.format`) and was never affected — check the sibling adapter
before assuming a gap.

### 164.6 The OpenAI-2026 platform params a non-OpenAI backend cannot honour are now reported

**Files**: `wiwi/providers/base.py` (new `OPENAI_PLATFORM_PARAMS`,
`unhonoured_platform_params`), `wiwi/providers/anthropic_adapter.py`,
`wiwi/providers/gemini_adapter.py`, `AUDIT.md` #501.

**Symptom**: `logit_bias`, `frequency_penalty`, `service_tier` and nine
siblings are honoured and billed by an OpenAI-shaped upstream and simply
absent on the Messages API and in Gemini's `generationConfig`. A caller
that set one of them against a non-OpenAI deployment got a request the
backend ignored, with nothing anywhere saying so.

**After**: the Anthropic and Gemini adapters report each such param by
name. The parameter is still **forwarded** — suppressing it would be a
silent behaviour change for any deployment whose *typed* provider
understates what it actually talks to, and the provider type is
configuration, not a fact about the endpoint. The OpenAI-shaped adapters
stay silent, because they honour the params: a warning there would be a
false alarm on every ordinary request that sets `logprobs` or
`service_tier`.

**Invariant**: the param table lives in `providers/base.py`, not in one
adapter, because BOTH layers need it — the OpenAI-shaped adapters forward
these (they CAN honour them) and the Anthropic/Gemini adapters must report
that they cannot. A single table keeps the two sides from drifting: a param
added to the forward list without a row here would be silently dropped on
every non-OpenAI backend, which is the exact defect the table prevents.

**Two more strippers, found while closing this**: NIM strict-validates its
body and 400s on these ("Unsupported parameter(s): `prompt_cache_key`"), so
its strip is mandatory — and the OpenCode Responses route never forwards
extras at all. Both were silent about what they removed; both now report it.

---

## Round 158 — Inbound-surface divergences on the translation path (2026-10-18)

Found while auditing each inbound surface, and each path of a surface, against
its siblings. The unifying shape: two surfaces (or two paths of one surface)
disagreeing about the same event, so the caller's dialect decided what they were
told. Entries 477–484 in `AUDIT.md`; regression coverage in
`tests/test_fix_round158.py`.

### 158.1 A content-filtered turn must announce `response.incomplete` on the Responses wire

**Files**: `wiwi/wire/openai_responses.py` (`ResponsesStreamEncoder._completed`, `_INCOMPLETE_REASONS`), `AUDIT.md` #477.

**Symptom**: an OpenAI upstream reporting `finish_reason: "content_filter"` was
re-encoded on the Responses surface as `response.completed` wrapping a payload
with `"status": "incomplete"` + `incomplete_details` — one event, two
contradictory statements.

**Before**:

```python
incomplete = self._stop == "length"
```

**After**: the event type consults the same table `_response_obj` keys the
payload's `status` off:

```python
incomplete = self._stop in _INCOMPLETE_REASONS
```

**Invariant**: the event type and the payload's `status` are the same fact and
must never disagree for any stop the table knows. A stop the table has never
seen still completes (unchanged behaviour). `length` was already right, which is
why the gap survived.

### 158.2 A mid-stream failure keeps its classification on every dialect

**Files**: `wiwi/streaming/deltas.py` (`stream_error_type`), `wiwi/wire/openai_chat.py`, `wiwi/wire/openai_completions.py`, `wiwi/wire/openai_responses.py`, `wiwi/wire/anthropic_messages.py` (`_stream_error_type`, `_ANTHROPIC_STREAM_TYPES`), `AUDIT.md` #478, #484.

**Symptom**: a mid-stream 429/529/timeout reached an OpenAI client as an opaque
`api_error` — while the identical failure reached Claude Code classified, since
the Anthropic encoder had done this since round 1.

**Before** (both OpenAI-shaped stream encoders, and the Responses `response.failed`
payload):

```python
err = {"error": {"message": d.message, "type": "api_error"}}
```

**After**: one classifier beside the delta it interprets, in the vocabulary
`providers/base.error_from_provider_status` already puts on `WiwiError.etype`:

```python
def stream_error_type(d: StreamError) -> str:
    if d.etype:                      return d.etype
    if d.status == 529:              return "overloaded_error"
    if d.status == 429:              return "rate_limit_error"
    if d.status == 504 or d.kind == "timeout": return "timeout"
    if d.status in (408, 500, 502, 503):       return "api_connection_error"
    return "api_error"
```

**Invariants**:

- The vendor's own `etype` wins over the status — it is the most precise signal.
- Each dialect spells the result its own way: Anthropic maps `timeout` →
  `timeout_error` (`_ANTHROPIC_STREAM_TYPES`), the OpenAI surfaces pass it
  straight into `error.type`/`error.code`, matching what their **sync**
  `error_body` does with the same etype. Streaming and non-streaming paths of one
  dialect must classify identically.
- The sync Anthropic etype table maps wiwi's `timeout` → `timeout_error` (it used
  to map it to `api_error`), and `_ANTHROPIC_TYPE_BY_STATUS` has a 504 arm for a
  504 whose etype the table has never seen (#484).

### 158.3 A server-tool `id` and its result must be one identity

**Files**: `wiwi/wire/anthropic_messages.py` (`decode_request`'s `server_tool_use`/`mcp_tool_use` arm, the `ServerToolResultDelta` fallback arm), `AUDIT.md` #479.

**Symptom**: two halves of one contract. Decode forwarded a non-`str` id (JSON
`null`, a number, a list) into `ToolUsePart.id`, so replay re-emitted `"None"` on
a `*_tool_result` pair naming nothing. And the single-call fallback shipped the
result with the upstream's unrecognized id while the call shipped its real one —
two ids for one pair, the unpaired-on-replay shape the buffering exists to
prevent.

**Before / After**:

```python
id=b.get("id", "")                       # defaults only a MISSING key
id=raw_id if isinstance(raw_id, str) else ""   # guard like ``name``
```

```python
content_block = d.block
if content_block.get("tool_use_id") != call["id"]:
    content_block = {**content_block, "tool_use_id": call["id"]}
```

**Invariants**:

- The client-dispatched `tool_use` arm already refuses a missing/non-string id
  with `DialectError` (AUDIT #368). The server-tool arm cannot raise the same way
  — normalize instead, matching the pre-existing `""` fallback for a missing id.
- An `mcp_tool_use` must go back as `mcp_tool_use` (not `server_tool_use`), or
  the paired `mcp_tool_result` is rejected on replay. The guard must not disturb
  `block_type` or `builtin`.
- A result block and the call it is paired with carry ONE id.

### 158.4 The stored copy of a streamed hosted search carries the real query

**Files**: `wiwi/server/app.py` (`_streamed_response_payload`), `wiwi/wire/openai_responses.py` (`_builtin_query`, `_close_tool`), `AUDIT.md` #480.

**Symptom**: the live SSE frame carried the real query (extracted by `_close_tool`
via `_builtin_query`), while the stored turn — read back by
`previous_response_id` — claimed an empty query, because the accumulator writes
only raw fragments (`_args`) and never a parsed `query` key.

**Before / After**:

```python
orp._builtin_call_item(tc["id"], tc.get("query", ""), tc["builtin"])
orp._builtin_call_item(tc["id"],
                       orp._builtin_query("".join(tc.get("_args") or [])),
                       tc["builtin"])
```

**Invariant**: the stored turn and the streamed turn are the same turn; a hosted
call's query is always extracted from the accumulated raw argument string with
`_builtin_query`, never read from a parsed key the accumulator does not write.

### 158.5 Repaired tool arguments must not invent a value the model never wrote

**Files**: `wiwi/streaming/partial_json.py` (`_scan_json`, `_trim_incomplete_value`, `_DANGLING_NUMBER`, `_PARTIAL_LITERALS`, `_repair_truncated_json`), `AUDIT.md` #482.

**Symptom**: the repair appended closers to a half-written token, producing
`{"a": 1,}` / `{"b": }` / `{"a": tru}` / `{"n": 1.}` — none of which is JSON — so
`json.loads` raised in every consumer (`core/gateway.py:_apply_event`,
`wire/openai_chat.py`, `wire/openai_responses.py`) and the **entire** argument
object was lost, which round 96.1 then propagated as an empty IR `args`.

**After**: the tail is trimmed to the last complete element before the closers
are appended. A half-written literal is completed (`tru` → `true`, unambiguous
prefix); a trailing comma, a lone sign, a dangling colon, a dangling key and a
number tail that cannot terminate (`.`, `e`, `e+`) are dropped.

**Invariants**:

- The number trimmer matches the **whole token** (`[0-9][0-9.eE+-]*[.eE+-]$`),
  never just the offending `.e+`. Dropping only the punctuation would repair
  `{"n": 1.` to `{"n": 1}` — asserting a number the model never wrote, and this
  repaired text is what the tool call is executed with.
- A number whose digits are complete (`{"n": 1.5`, `{"limit": 5`) is untouched.
- The number tail is tested BEFORE the literal branch: an exponent's `e` is a
  letter, so `{"n": 1e` would otherwise look like a half-written literal.
- Every other key in the object survives the cut — pinned by a property test over
  every prefix of a real payload.
- Interaction with `wiwi/streaming/validation.py`: a repairable (truncated)
  payload is not an argument violation; only genuinely unrepairable input is.
  `tests/test_fix_round20.py::test_stream_pump_validates_tool_args_on_close` was
  retargeted to `{"broken": }` for exactly this reason (see `AUDIT.md` #482).

### 158.6 A tape that lost an entry must still prove the gap

**Files**: `wiwi/streaming/resume.py` (`StreamTape._evict`), `AUDIT.md` #483.

**Symptom**: a delta larger than the tape's whole budget was evicted by its own
append, leaving the tape empty — `bytes == 0` and `head_evicted` answering False
for the vacuous empty case, so the continuation guard could not tell "nothing was
ever emitted" from "everything was dropped" and rebuilt the assistant prefix from
an empty tape.

**After**: `while self._bytes > self._max_bytes and len(self._entries) > 1` — the
newest entry is never evicted.

**Invariant**: a tape that dropped anything answers `head_evicted(pos) is True`
for a position before the retained head, at any size; `replay` from a complete
position is never silently partial.

---

## Round 156 — An echoed tool-call `id` reopened the call on every arguments chunk (2026-10-16)

### 156.1 A repeated `id` on an open index is a continuation, not a second call

**Files**: `wiwi/providers/openai_adapter.py` (`_tool_call_continues()`, `_open_tool_ids`, the `tc.get("id")` branch of `decode_stream_event`, `reset()`, `_flush_open_tools()`, the `finish_reason` sweep); `wiwi/providers/openrouter_adapter.py` and `wiwi/providers/nim_adapter.py` (the same branch); `AUDIT.md` #473.

**Symptom**: a single tool call streamed over N arguments chunks arrived on the
Anthropic surface as N `tool_use` content blocks, **all carrying the same
`id`**. The Arguments of the tool call were duplicated across blocks too.

**Before** — in all three OpenAI-shaped adapters:

```python
if tc.get("id"):
    ...
    # a new tool call opening on the same index closes the previous one
    if idx in self._open_tool_indices:
        ...
        out.append(dl.ToolCallClose(index=idx))
    self._open_tool_indices.add(idx)
    self._tool_names[idx] = name_fragment or ""
    self._pending_opens[idx] = (tc["id"], self._tool_names[idx])
```

OpenAI's own streaming sends `id`+`name` only on a call's **first** chunk, so
this branch is only reached by the OpenAI-compatible upstreams that echo both on
**every** arguments chunk — and by wiwi's own test fixtures, which had all been
written in that shape, which is why the defect survived so long.

**After**:

```python
if self._tool_call_continues(idx, tc["id"]):
    # same call, still open; only a deferred Open's name can still grow
    ...
else:
    # a new tool call opening on the same index closes the previous one
    ...
```

`_tool_call_continues(idx, call_id)` is `bool(call_id) and self._open_tool_ids.get(idx) == call_id`.
A *different* id on an open index still takes the close/reopen path, so genuine
index reuse and parallel indices are unchanged — both covered by tests.

**Invariants this must not break** (all covered in `tests/test_fix_round156.py`):

- A tool call's arguments are fragments of **one** JSON document. Duplicating the
  header must not duplicate the fragments; they belong to the single Open/Close
  pair for that index.
- `ToolCallOpen.index` is the tool call's identity, not a fragment counter. An
  encoder maps IR index → client block index, so a second Open for the same index
  orphans the first block and re-points the args at the new one.
- Anthropic `tool_use` ids must be unique within one assistant message. The
  echoed-id shape violated that before the fix.

**Tests**: `tests/test_fix_round156.py` (22) — one Open/Close per call across the
OpenAI, OpenRouter and NIM adapters; the Open name/args survive; a different id on
the same index still opens a second call; parallel indices untouched; OpenAI's own
id-on-first-chunk-only shape unchanged; `reset()` clears the ids; and end to end
through `/v1/messages` both the block count and the TPS window.

**Metric side effect**: this churn was stamping the gateway's `last_token_at` on
every fragment, which is why the round-155 TPS window fix appeared not to matter
for these providers. See `AUDIT.md` #474.

---

## Round 151 — Zen refuses the caller's client-metadata block (2026-10-15)

### 151.1 A caller's `metadata` must not reach Zen

**Files**: `wiwi/providers/opencode_adapter.py` (`_drop_client_metadata()`, called from `encode_request()`); `docs/OPENCODE.md`; `AUDIT.md` #466.

**Symptom** (reported 2026-10-15, `*-free` model):

```
400 <account>: Upstream request failed: [unsupported_metadata] Client metadata is not supported.
```

**Before**: the chat and messages routes delegate to `openai_adapter` /
`anthropic_adapter`, and both forward a caller-supplied `metadata` from
`req.extras` (`_STANDARD` / `_ANTHROPIC_STANDARD`). That is correct for the
vendors that support the field and wrong behind Zen: Console relays the body to
the provider serving the model, that provider refuses the block, and Console
wraps the refusal as `Upstream request failed: [<code>] <message>` — the
bracketed code is the *upstream's*, preserved because Console strips codes
outside its own allow-list (opencode's client pins the format in
`packages/ai/test/provider-error.test.ts`). wiwi's 400 branch then renders it as
`<provider account>: <message>`.

**After**:

```python
# encode_request(), after the delegate builds the body
_drop_client_metadata(body, req, deployment_params, route)
```

It removes `metadata` **only when the caller supplied it** (`"metadata" in
req.extras`) and leaves an operator's `extra_body: {metadata: …}` untouched, so a
deployment that deliberately puts the field on the wire still can. No route gate
is needed: the Responses encoder never forwarded extras, and the Gemini route
returns before the call.

**Why the field rather than the fingerprint**: the report arrived framed as "our
client metadata is stale, refresh it from the client", so the fingerprint was
re-derived from the client's own source (`sst/opencode` @ `4397ee4`) —
`Identifier.descending()` still mints `ses_` + 12 hex + 14 base62,
`ProjectID.global` is literally `global`, and the client sends no
`x-opencode-request` — and every value was probed. The only client metadata wiwi
put on the wire that the real client does not is the caller's `metadata` block.
The one path this does **not** cover is the Responses route (see AUDIT #466).

**Tests**: `tests/test_fix_round151.py` (11) — the drop on the chat and messages
routes, provider-wide (paid models too), `metadata: null`, the Responses-route
no-op, the other extras still forwarded, the debug log line, the `extra_body`
escape hatch, and the boundary (native OpenAI/Anthropic adapters still forward
the field).

---


## Round 203 — the Chat stream encoder defers prose while a parallel tool call is open (2026-11-09)

**Translation-relevant change in `wiwi/wire/openai_chat.py`.** The Chat surface
was the only one of the three inbound encoders that emitted `content` between
two `tool_calls` fragments of the same turn. Full register: AUDIT #594. Tests:
`tests/test_fix_round203.py` (10).

**The sequence.** The Anthropic upstream can interleave prose with a
*still-open* sibling call: index 0 opens, index 1 opens and closes, the model
writes text under index 0, then index 0's `content_block_stop` arrives. The
decoder forwards, verbatim and legally:

```
ToolCallOpen(0) ToolCallOpen(1) ToolCallClose(1)
TextDelta("Let me also check X.")
ToolCallArgsDelta(0) ToolCallClose(0)
```

`ChatStreamEncoder` had no tool-open tracking, so the text became a `content`
chunk before tool 0's arguments had finished streaming. The other two encoders
already deferred in this situation (Anthropic since #156, Responses since #367);
the asymmetry was the bug.

**The fix.** `_open_calls` tracks the set of client-visible open calls
(a suppressed builtin never enters it — no client-side call exists to hold text
back). `TextDelta` and `ThinkingDelta` buffer into `_deferred` while that set
is non-empty and flush — in arrival order, one frame per entry — on the close of
the **last** sibling. `final_frame` drains the buffer before the finish chunk,
because a turn can end with a call still open and would otherwise lose the
model's words.

**Invariants preserved, pinned by the round-203 tests:** exactly one `tool_calls`
Open per call (no phantom call is introduced by deferring), later args fragments
still route to their own index, a plain text stream is still emitted inline
chunk-for-chunk, and text after a call has fully closed is inline again.
