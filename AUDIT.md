# Fionn / wiwi — End-to-End Bug Audit

**Date:** 2026-08-26
**Baseline:** 532 tests pass, ruff clean. Bugs below exist in paths not covered by the thematic regression suite.

Each finding verified against source by reading the cited lines. Severities: 🔴 critical · 🟠 high · 🟡 medium · ⚪ low.

---

## 🟡 Low — round 90 (new)

### 270. Three adapters still keep their own finish-reason map, narrower than the shared one

**Severity:** 🟡 Low · **Status: open (found in round-90 review, deferred — outside the approved file scope)**

**Where:** `wiwi/providers/nim_adapter.py:486`, `wiwi/providers/openrouter_adapter.py:254`,
`wiwi/providers/openrouter_adapter.py:486`.

**What:** Round 90 extracted the finish-reason maps into `wiwi/ir/translation.py`
(`normalize_finish_reason` inbound, `ir_to_openai_finish` outbound) and rewired
`openai_adapter.py` and `wire/openai_chat.py` to use them. Three inline copies
remain, and both flavours are **strictly narrower** than the shared map:

```python
# openrouter_adapter.py:254 and :486 — adds "error" -> "stop", but handles
# none of the nonstandard spellings the shared map accepts.
{"stop": "stop", "length": "length", "tool_calls": "tool_call",
 "content_filter": "content_filter", "error": "stop"}.get(fr, "stop")

# nim_adapter.py:486 — a plain copy with no local addition at all.
{"stop": "stop", "length": "length", "tool_calls": "tool_call",
 "content_filter": "content_filter"}.get(fr, "stop")
```

**Why it matters:** a NIM- or OpenRouter-backed deployment that spells its
tool-call stop `tool_use`, `function_call`, or its length stop `max_tokens`
falls through to the `"stop"` default — the turn's stop reason reaches the
client as `stop` while the turn carries real tool calls. `normalize_finish_reason`
already accepts all three spellings, so the fix is a one-line substitution at
each site, with OpenRouter keeping its local `"error" -> "stop"` addition (which
the shared map does not carry, and should not: it is OpenRouter-specific).

**One-line fix sketch:** replace each inline `.get(fr, "stop")` with
`tr.normalize_finish_reason(fr)`, and for OpenRouter special-case `"error"`
before delegating (it maps to `"stop"`, which the shared map also produces, so
`normalize_finish_reason` alone is sufficient — `"error"` is not a key the
shared map knows and the total function returns `"stop"` for it by design).

**Note:** this is a *narrowing* gap, not a live crash — every affected spelling
degrades to a legal `stop` rather than raising. It was found while verifying the
round-90 residual doubt about `_synthesized_opens`, and deliberately left
unfixed: the round-90 approved scope named only `openai_adapter.py` and
`openai_chat.py` for the shared-map rewiring, and the project rule is that a bug
found outside the current scope is reported, not silently fixed. The
`_synthesized_opens` doubt itself resolved **clean** — see the round-90 ledger.

**Related, same shape:** `wiwi/wire/openai_chat.py:356` and `:468` were rewired to
`ir_to_openai_finish`, which fixes the *outbound* direction — IR `tool_call` no
longer mis-spells as `stop`. But `wiwi/providers/openai_adapter.py:435`
(`_OPENAI_FINISH_OUT`) and the `openai_responses.py` `_INCOMPLETE_REASONS` map are
the only two places the OpenAI vocabularies live; see #271 and #272 below for two
gaps the shared map *opened* rather than closed.

---

### 271. The OpenAI surface's streaming encoder emits `tool_calls` with no tool call

**Severity:** 🟡 Medium · **Status: open (found in round-90 whole-branch review, outside the approved file scope)**

**Where:** `wiwi/wire/openai_chat.py:429-437` (the `Finish` branch, which guards
only the suppressed-builtin case) vs `wiwi/wire/openai_chat.py:345-347`
(non-stream path, which guards unconditionally) and
`wiwi/wire/anthropic_messages.py:594` (Anthropic path, which guards
unconditionally).

**What:** the OpenAI surface's streaming path applies the A1 downgrade guard
**only** when a builtin call was suppressed:

```python
if (self._stop == "tool_call" and self._suppressed_builtin
        and not self._saw_tool_calls):
    self._stop = "stop"
```

The non-streaming encoder on the *same surface* guards unconditionally
(`if fr == "tool_calls" and not tool_calls: fr = "stop"`), and so does the
Anthropic encoder. So a client receives `finish_reason: "tool_calls"` on a chunk
with no `tool_calls` array — a response that no OpenAI client can act on.

**Round-90 widened the reachable set.** `normalize_finish_reason` newly decodes
the Anthropic spelling `tool_use` to IR `tool_call`; the pre-round-90 closures in
`openai_adapter.py` did not accept `tool_use`, so that spelling fell through to
`stop`. Reproduced end-to-end against a fresh `openai` adapter and the surface
encoder:

```
upstream streams finish_reason='tool_use', zero tool calls
  IR deltas : ['TextDelta', 'Finish']
  IR finish : ['tool_call']
  CLIENT    : finish_reason = 'tool_calls'   (zero tool calls)
```

**Why it matters:** a client that trusts the finish reason to decide whether to
run tools either stalls waiting for a `tool_calls` array that never arrives, or
reports a tool turn that produced nothing. This is the same defect class the A1
guard was written for, on the one path that does not apply it.

**One-line fix sketch:** drop the `self._suppressed_builtin` conjunct so the
stream guard matches the non-stream one (the flag then only governs the
`openai_adapter` suppression bookkeeping, which is where it belongs). A
regression test belongs beside the existing A1 tests, driving
`ChatStreamEncoder.final_frame()` with `Finish(stop_reason="tool_call")` and no
`ToolCallOpen`.

**Note:** pre-existing, but *newly reachable* because of this round's own change,
which is why it is recorded here rather than left as "someone else's bug". Left
unfixed because `wiwi/wire/openai_chat.py` was in scope only for the
`ir_to_openai_finish` substitution — widening the fix to the guard is a
behaviour change the round was not approved for.

---

### 272. The Responses surface reports `tool_call` with no tool item as `completed`

**Severity:** ⚪ Low · **Status: open (found in round-90 whole-branch review, outside the approved file scope)**

**Where:** `wiwi/wire/openai_responses.py:394` (`_INCOMPLETE_REASONS`) and the
encoder at `:398-418`.

**What:** `_INCOMPLETE_REASONS` maps only `{"length", "content_filter"}`. Every
other IR reason — including `tool_call`, `pause_turn`, `stop_sequence`,
`context_window_exceeded`, and `compaction` — encodes as
`status: "completed"` with no `incomplete_details`. Observed, encoding each IR
reason through `openai_responses.encode_response` with a text-only turn:

```
stop                     -> status=completed  incomplete=None
tool_call                -> status=completed  incomplete=None
length                   -> status=incomplete {'reason': 'max_output_tokens'}
content_filter           -> status=incomplete {'reason': 'content_filter'}
pause_turn               -> status=completed  incomplete=None
stop_sequence            -> status=completed  incomplete=None
context_window_exceeded  -> status=completed  incomplete=None
compaction               -> status=completed  incomplete=None
```

**Why it matters:** `tool_call` with no tool item is the same invalid shape as
#271 and gets the same "completed" answer. `context_window_exceeded` is the more
interesting one: the IR carries it precisely because collapsing an overflow into
`stop` made Claude Code end turns early and hid the overflow from auto-compact
(AUDIT #156) — here the Responses surface silently re-collapses it to a
successful completion for a Responses client.

**Not a round-90 regression:** the pre-round-90 `openai_adapter` closures did not
produce IR `pause_turn`, `stop_sequence`, `context_window_exceeded`, or
`compaction` from an OpenAI-shaped upstream either, so this map's coverage was
already incomplete. Round 90 widened the IR vocabulary that can *reach* it.

**One-line fix sketch:** decide per reason whether the Responses vocabulary can
express it (`context_window_exceeded` has no `incomplete_details.reason` spelling
today, so it needs either a new mapping or an explicit documented collapse) and
extend `_INCOMPLETE_REASONS`, or add an explicit `tool_call`-without-items guard
mirroring #271. Do not widen this map without checking what a Responses client
does with each new status.

---

## ✅ Fixed — round 91 (2026-09-20)

### 269. A 200 carrying an Anthropic/OpenAI error body was declared HEALTHY

**Severity:** 🟠 High · **Status: fixed** (second half only — the original first-half claim was retracted in review; see the correction note above)

Fixed in `wiwi/core/recovery.py`: `_body_is_error_envelope` now recognizes the
Anthropic (`{"type":"error","error":{…}}`) and OpenAI (`{"error":{…}}`) error
bodies in addition to WorkBuddy's `{"code": N, "msg": …}` envelope — previously
those 200s decoded into an empty-but-successful turn with no exception and no
signal, so the healer called a dead key HEALTHY and restored it.
Regression tests: `tests/test_fix_round91.py`. Gate: `2434 passed`, `ruff` clean.

> **Correction (same round, caught in review).** This entry originally claimed a
> second, larger half: that `_probe_request()` hardcoded
> `model="wiwi-health-probe"` and the placeholder "reached the wire", so every
> probe was rejected 404/401 and the rejection blamed the key or the deployment.
> **That claim was false.** No adapter reads `ir.Request.model` for the wire
> body — every adapter sets it from `encode_request`'s own `model_id` argument
> (`openai_adapter.py:233`, `anthropic_adapter.py:439`,
> `opencode_adapter.py:845`, and the rest via `super()`), and the pre-fix call
> site already passed `dep.model_id` as that argument. Encoding the probe
> request through six provider types with the OLD `_probe_request` and the NEW
> one produces **byte-identical wire bodies**. The placeholder was in a field
> nothing reads: a code-clarity defect, not a misclassification.
> The `_probe_request(stream, model_id)` signature change was kept because it
> makes the two halves agree about who owns the model name, but it is a no-op
> on observable behaviour. See "Why the original claim was wrong" below.

**Where:** `wiwi/core/recovery.py:112` (`probe_verdict`) and its helper
`_body_is_error_envelope` (`wiwi/core/recovery.py:133`).

**What:** `probe_verdict(status, body)` recognized exactly one class of 200
business error — WorkBuddy's `{"code": <non-zero>, "msg": …}` envelope — and
nothing else. An Anthropic- or OpenAI-shaped upstream that answers HTTP 200 with
its own dialect's error object was declared HEALTHY.

Those bodies are invisible one layer down, which is why no existing test caught
it: `decode_response(200, <error envelope>)` returns an **empty but successful**
`AssistantTurn` — no exception, no signal, nothing for the caller to notice.

**Evidence (fresh, this session):**

```
probe_verdict(200, b'{"type":"error","error":{"type":"overloaded_error",…}}')  -> HEALTHY (pre-fix)
probe_verdict(200, b'{"error":{"message":"…"}}')                              -> HEALTHY (pre-fix)
probe_verdict(200, b'{"code":1,"msg":"quota"}')                               -> UNREACHABLE (already handled)
```

**Why it matters:** the healer's whole job is to *restore* a key or deployment
into rotation when a probe succeeds. A 200-is-error body told it a dead
credential was alive, so the key was restored and immediately failed again on
real traffic — the healer re-arming the very failure it exists to clear.

**Why not caught earlier:** `probe_verdict` (#96) and the model-error streak
rule (#97) were fixed at the classification layer, and
`tests/test_recovery.py::TestProbeVerdict` covers literal statuses and bodies —
but only bodies in the shapes that were already handled.

**Fix (as applied):** `_body_is_error_envelope` recognizes the Anthropic
(`{"type":"error","error":{…}}`) and OpenAI (`{"error":{message|type|code|param}}`)
shapes by their own structural markers, in addition to WorkBuddy's envelope.
Deliberately **shape-based rather than adapter-owned**: `probe_verdict` is a
pure synchronous classifier with no adapter in scope, the shapes are unambiguous
by their markers, and recognition keys on structure — never on a
`provider_type == …` branch — so no dialect knowledge is imported into `core/`
and no sixth method was added to the five-method `ProviderAdapter` Protocol for
a consumer that does not exist. An adapter-owned veto hook remains a reasonable
future extension for a provider whose 200 error body is neither shape; none is
known today, so no seam was added speculatively.

**False-positive surface (reviewed, judged acceptable):** the OpenAI arm
requires *any* of `message`/`type`/`code`/`param` inside a non-empty
`{"error": {...}}`, so a CN-style success envelope like
`{"error": {"code": 0, "message": "ok"}}` would be flagged. That shape is not
reachable as a success from any provider in `PROVIDER_TYPES`, and the fleet's
own decoders are *stricter-blind* than this classifier: they treat any
non-empty `{"error": {...}}` as an error with no marker check at all
(`openai_adapter.py:520`, `cline_adapter.py:153`, `gemini_adapter.py:298`, which
reads the inner `code` as a *status*), so a 200 body of that shape already
becomes a `StreamError` one layer down. Recognition here is therefore narrower
than what the adapters already do with the same bytes.

**Why the original claim was wrong (recorded so it is not re-made):** the
finding was produced by a test that passed `"real-model-id"` as *both*
`_probe_request`'s new argument and `encode_request`'s second argument, then
asserted the body carried `"real-model-id"`. Because `encode_request` sets the
body field solely from its own argument, the assertion holds even when
`_probe_request` discards its argument entirely — verified: a `_probe_request`
that ignores `model_id` still passes all nine provider cases. The test failed
against pre-fix source only via `TypeError` (a one-argument function called with
two), a signature mismatch misread as a behavioural catch. The lesson is
general: a parametrized assertion is only RED evidence if it can fail for the
reason claimed.

**Second half of the fix — the SSE frame (found in whole-branch review).** The
force-stream arm was the difference between the fix working and not working on
`cline`/`workbuddy`/`opencode`, which all set `force_stream`: their probe
returns SSE even on HTTP 200, so the error object arrives **inside a `data:`
line**, not as a bare JSON body. Verified against the verbatim pre-fix source
(`git show 365dd2c:wiwi/core/recovery.py`), which is unambiguous — the SSE path
is inside `try/except ValueError, TypeError` and is unconditional, not
`is_error_object`-guarded, so it *is* covered by this round's change:

```
body = b'event: error\ndata: {"type":"error","error":{"type":"overloaded_error",…}}\n\n'
  pre-fix  -> _body_is_error_envelope = False  -> probe_verdict(200, body) = HEALTHY
  post-fix -> _body_is_error_envelope = True   -> probe_verdict(200, body) = UNREACHABLE
```

and one layer down the same bytes decode with no exception at all
(`anthropic_adapter.decode_stream_event(...)` → empty list), which is exactly the
invisible-success shape this entry is about. **A real defect, but not one this
round introduced** — it was open before and closed by the same edit; recorded
here so the fix's true coverage is on the record rather than assumed.

**Residual gap, deliberately left open:** `recovery.py` guards the *bare-JSON*
arm with `try/except (ValueError, TypeError)` but the SSE arm additionally
skips `[DONE]`. Neither arm bounds the *size* of what it parses — a hostile or
broken upstream returning a multi-MB 200 body to a 1-token probe has its whole
body `json.loads`'d. A 1-token probe response should be small, so a byte cap
(e.g. refuse to parse past ~64 KiB and classify by status alone) would be a
cheap hardening; not worth the surface change here, and not a correctness bug.

**Scope caveat, unchanged:** this fixes only the *healer's* classification. A
200-carrying-an-error-body still reaches a normal request's client as an empty
successful turn — `probe_verdict` is not on that path. Whether the gateway
should convert such a body into a dialect-correct error at decode time is a
separate question, **not** claimed or done here.

Regression tests: `tests/test_fix_round91.py` (the nine-way parametrized class
is replaced by a divergence test that passes a *placeholder* to
`_probe_request` and `dep.model_id` to `encode_request`, asserting the wire
carries `dep.model_id` — which does fail if the two disagree) plus the
`TestProbeVerdict` shape cases.

---

## ✅ Fixed

### 268. The free-tier decoy tools answered as tool calls the client cannot execute

**Severity:** 🟠 High (every free-model request; introduced by #267's own fix)
**Files:** `wiwi/providers/opencode_adapter.py` (`encode_request`, decode paths)

**Trigger:** #267 satisfies the free tier's tool-payload condition by injecting
`bash` and `read` into the request. The gate only checks that they are
*offered*, so a model may still *call* one, and the client — which never
declared that tool — receives a tool call it has no implementation for, plus a
`tool_calls` finish reason that keeps it waiting for a dispatch that can never
happen. Probed live 2026-09-19: `mimo-v2.5-free`, asked "Read the file
config.py.", answered with a `read` call carrying `{"path": "config.py"}`.

The cloak cannot prevent it: the decoys must be *offered* for the gate to
pass, and clients that send tools (the case the probe hit) keep
`tool_choice: "auto"` — where a call is always possible. `tool_choice: "none"`
is set only for toolless requests, and even that is a request, not a guarantee.
The filter therefore belongs on the response side.

**Fix:** `encode_request` records the names it actually injected
(`_decoy_names`) — a client tool called `bash` is left in place by the cloak and
is therefore never filtered — and both decode paths drop the Open/Args/Close
triple for those names (`_filter_decoys`, `_filter_decoys_turn`), correcting
`Finish`/`stop_reason` to `stop` when the decoy was the only call. Dropping the
triple whole is legal here: the wire encoders assign client-visible indices
themselves (`anthropic_messages._tool_blocks`) and ignore an `ArgsDelta` whose
block never opened, so the gap in the IR index sequence never reaches a client.

**Status: fixed** — `tests/test_fix_round89.py` (13); 6 of them fail with the
drop logic neutered. Verified live through the gateway: the same "read the file"
prompt now returns `finish: stop`, `tool_calls: None`, and text declining the
tool.


### 266. Zen's transport never declared `forceStream`, so a non-streaming caller got an empty turn

**Severity:** 🟠 High (every non-streaming caller of an `opencode`-type provider)
**Files:** `wiwi/providers/opencode_adapter.py` (`force_stream = False`, pre-fix `encode_request`)

**Trigger:** request any Zen model with `"stream": false` (curl, the OpenAI
SDK's default, a batch script). Zen answers as an event stream regardless, and
`Gateway._call_once` handed that SSE body to `adapter.decode_response`, whose
JSON parse either produced an empty `chat.completion` with zero usage or raised
— surfacing as `upstream zen returned an undecodable 200 response:
JSONDecodeError` (the AUDIT #92 wrapper).

OpenCode's own provider entry carries the flag on the transport:

```
transport: { baseUrl: "https://opencode.ai", forceStream: true, ... }
```

`OpencodeAdapter` is wiwi's transport for the same gateway and shipped the
opposite declaration, while Cline and WorkBuddy — streaming-only upstreams for
the same structural reason — both declare `force_stream = True`.

**Fix:** declare `force_stream = True` and force `body["stream"] = True` in
`encode_request` for the chat, responses and messages routes. The declaration
alone is half a fix: `_complete_via_stream` asks `build_url` for the streaming
URL but encodes the *client's* request, so the body would still have said
"don't stream" on a connection the gateway parses as SSE. Gemini is excluded
from the body force — its wire is selected by the URL
(`:streamGenerateContent?alt=sse`) and a `stream` key in a `generateContent`
body is an unknown field the endpoint rejects.

**Status: fixed** — `tests/test_fix_round87.py` (12); four existing suites that
pinned the pre-fix contract were moved to the new one (rounds 27/63/72/73). See
`UPDATE.md` for the full entry.

**Follow-up (#267):** this fixed the empty-turn half only. The
`403 FreeTierError` the reporter was actually seeing needed two more
request-shape factors, and #174's "free models need a funded key" diagnosis
turned out to be backwards.

### 267. Zen's free tier is a request-shape gate; #174 blamed it on the credential

**Severity:** 🟠 High (every `*-free` deployment — the reported outage)
**Files:** `wiwi/providers/opencode_adapter.py` (`headers()`, `encode_request`)

**Trigger (user-reported):**

```
io requires billing (403): OpenCode's free tier can only be used from within OpenCode
```

AUDIT #174 read this message literally, concluded the free tier was gated on a
**paid workspace**, and recorded "free models now require a valid
`OPENCODE_API_KEY`" in the adapter docstring, `wiwi.yaml.example` and
`UPDATE.md`. It is not. `FreeTierError` means "this request does not look like
the OpenCode client", and the gate is entirely in the request shape.

**Live matrix 2026-09-19** — `mimo-v2.5-free` on `POST /zen/v1/chat/completions`,
one factor changed per row, all others identical to the 200 row:

| changed factor | result |
|---|---|
| *(none — keyless, stream, CLI session, `bash`+`read`)* | **200 SSE** |
| `stream: false` | 403 FreeTierError |
| no tools / user tools only / `bash` without `read` | 403 FreeTierError |
| `x-opencode-session` = `ses_`+24 hex (wiwi's pre-fix `uuid4().hex[:24]`) | 403 FreeTierError |
| session head `11 hex + 15 uppercase` | 403 FreeTierError |
| `User-Agent: opencode` (no version) | 403 FreeTierError |
| `User-Agent: opencode/1.16.0` | 426 UpgradeRequired |
| a canonical session two days old | 200 SSE |
| `Authorization` omitted / `Bearer public` | 200 SSE |
| **a real account Zen key** | **429 FreeUsageLimitError** |

Two conclusions, both the opposite of the recorded ones:

- **The credential is irrelevant.** Keyless returns 200; the account's six real
  Zen keys return 429 because *their* free quota is spent. So `anonymous` is
  the correct setup for a `*-free` deployment, not a deprecated one.
- **The tail length, not the alphabet, kills the old session id.** `ses_` + 26
  all-hex passes; `ses_` + 24 fails. `x-opencode-request` is not validated at
  all (a 6-char id returns 200).

Also corrected: **`union-alpha` is retired upstream.** The live
`/zen/v1/models` catalog (74 ids, 2026-09-19) no longer lists it, and it answers
`401 ModelError: Model union-alpha is not supported` on all three routes — so
its `_MESSAGES_PREFIXES` entry is dead config, and round 63's tests only passed
because they mocked the endpoint. The Messages **route** itself is very much
alive: `claude-*` and `qwen*` (paid) still resolve to it.

**Fix** (`opencode_adapter.py`, all of it confined to the adapter):

- `force_stream = True` + forced `body["stream"] = True` (#266).
- `is_free_model()` — `-free` suffix plus the stealth `big-pickle`.
- `canonical_session_id()`/`canonical_request_id()` emit the CLI's
  `^[ses|msg]_[0-9a-f]{12}[0-9A-Za-z]{14}$` shape; the patterns are exported so
  the tests assert against the minter's own constants.
- `stable_session_id()` reuses one session per credential (SHA-256 bucketed,
  LRU-capped, TTL-evicted) because free quota is accounted per session.
- `_cloak_chat_tools()` / `_cloak_responses_tools()` inject the `bash`/`read`
  decoys per route shape, never replacing a client tool of the same name, and
  only on free models.
- `_force_auto_tool_choice()` collapses `tool_choice` on the allowlisted Muse
  Spark free models, which 400 on every other form.
- Forced `stream_options: {include_usage: true}` on chat-route requests a
  non-streaming client sent: without it Zen omits the usage frame and every
  aggregated turn prices on the estimator. Probed: Zen accepts it and returns
  real counts.

**Status: fixed** — `tests/test_fix_round88.py` (21). Verified live through the
gateway's non-streaming path: `mimo-v2.5-free`, `big-pickle` and
`muse-spark-1.3-contributor-free` each return 200 with `estimated=False` usage
(all three were 403 before). `tests/test_fix_round29.py`'s two
"fresh session per request" tests pinned the *old* behaviour and were rewritten
to the reuse contract.

**Open, not fixed here:** free-tier traffic now succeeds, so #174's
entitlement classification (402/403 `permission_error`, key pool untouched) is
no longer exercised by any real upstream response — the probes that justified it
were these same misdiagnosed 403s. Worth re-examining whether `FreeTierError`
should map to *billing* at all now that wiwi can avoid it by shape.


### 176. Zen's Messages and Gemini routes authenticated with the wrong header scheme

**Severity:** 🟡 Medium (latent — masks every Messages-route model the moment the account is funded)
**Files:** `wiwi/providers/opencode_adapter.py` (`headers()`, pre-fix `Authorization`)

**Trigger:** request any Messages-route model (`union-alpha`, `claude-*`,
`qwen*`) through an `opencode`-type provider. Upstream answers
`401 {"type":"error","error":{"type":"AuthError","message":"Missing API key."}}`
— "Missing", not "Invalid": the credential never arrived.

`OpencodeAdapter.headers()` hand-built one header dict for all four routes and
always emitted `Authorization: Bearer`. But Zen serves the **Anthropic
Messages protocol** on `/messages`, and that front end reads `x-api-key`;
a Bearer token is invisible to it. Chat and Responses are OpenAI-wire and do
read Bearer, so the single-scheme assumption held everywhere except the one
route that needed the other scheme.

Verified live 2026-09-17 against `https://opencode.ai/zen/v1` with a real
`sk-…` Zen key, model `claude-sonnet-5`:

| headers on `/messages` | result |
|---|---|
| `Authorization: Bearer` | 401 `AuthError` "Missing API key." |
| `x-api-key` | 401 `CreditsError` "No payment method" |
| `x-api-key` + `Bearer` | 401 `CreditsError` "No payment method" |

`CreditsError` is the *next* gate, past authentication — it proves the request
cleared auth. `AuthError` means it never did.

**Why it stayed hidden:** every Messages-route test in the suite configures
`key="anonymous"` (`tests/test_fix_round63.py`), which correctly omits
credentials entirely, so the wrong-scheme branch was never exercised with a
real key. It is also currently masked in production: all six `io` keys sit on
unbilled workspaces (`CreditsError` on paid models), and free models are
refused with `403 FreeTierError` on every route and every live key (9/9 free
models probed). So Messages-route traffic fails today for a *billing* reason
and would have kept failing for an *auth* reason after payment was added.

**Status: fixed** — rounds 72/73 (2026-09-17); `tests/test_fix_round72.py` and `tests/test_fix_round73.py`. The route→header table is `_CREDENTIAL_HEADER`.

### The same defect on the Gemini route (found in review, fixed here)

The first fix emitted `x-api-key` for messages and `Authorization: Bearer` for
everything else, and documented the Gemini route as carrying "its key in the
querystring". **That claim was false**, and it was load bearing — the same class
of bug was still live on a fourth route.

Zen's Gemini front end reads **`x-goog-api-key`**. Verified live 2026-09-17,
real Zen key, `gemini-3-flash`, `POST /models/gemini-3-flash:generateContent`:

| credential | result |
|---|---|
| `Authorization: Bearer` (what we sent) | 401 `AuthError` "Missing API key." |
| `x-goog-api-key` | 401 `CreditsError` "No payment method" |
| `?key=` querystring | 401 `AuthError` "Missing API key." |
| none | 401 `AuthError` "Missing API key." |

The querystring never carried a key at all: `wiwi/core/recovery.py:190` appends
one only when `provider_type == "gemini"`, and this deployment's type is
`"opencode"`; `OpencodeAdapter.build_url` returns a bare `...:generateContent`
with no `?key=` placeholder, so the `url.endswith(("?key=", "&key="))` branch
never fires. So the route sent a credential upstream could not read, and the
"querystring" story was a second, compounding error. The route is live, not dead
code: `GET /models` lists seven Gemini models.

**Fix:** replace the two-branch `if` with a route→header table
(`_CREDENTIAL_HEADER`), so the scheme is data rather than a chain of conditions
and a fifth route cannot silently inherit the wrong one. `anonymous` still omits
every credential on every route. Post-fix live check, all three routes with a
real key: gemini → `CreditsError`, messages → `CreditsError`, chat → `403
FreeTierError` (the account gate, which is the correct next wall for a free
model). Every route now clears authentication.

Both failures were also **invisible by construction** — a credential in the
wrong scheme is silently unread upstream, and the resulting 401 is identical to
sending no credential at all — so `headers()` now emits one `log.debug` line
naming the route and the chosen scheme (`opencode_credential_scheme`), and a
mirror line for the sentinel path (`opencode_credential_omitted`, with
`reason="anonymous_sentinel"`). The key value is never logged (only
`label=key.label`, matching `workbuddy_adapter.py:179`). This turns a
misconfigured route from a probe matrix into a one-line log read.

**Status: fixed** — `tests/test_fix_round72.py` (8 tests: messages scheme,
chat/responses/gemini controls, anonymous-sentinel control, fingerprint
preservation, gateway end-to-end on the messages and chat routes) and
`tests/test_fix_round73.py` (7 tests: gemini `x-goog-api-key`, cross-route
scheme isolation, anonymous sentinel, fingerprint, gateway end-to-end on the
gemini route, and a **streaming** messages-route case — the stream pump builds
headers at its own site and round 72 only covered the non-streaming one).

**Not fixed, and not fixable in code:** the `io` account is on an unbilled
workspace, so paid models stay unreachable until a payment method is added.
That half is an upstream account condition. The free-tier half was wrong —
see #267: the 403 was a request-shape gate, and keyless free traffic works.

### 114. Request-log rows older than the poll interval never aged out of the view

**Severity:** ⚪ Low (UI staleness)
**Files:** `web/src/pages/RequestLogs.tsx:496` (pre-fix cutoff), `:510` (pre-fix dependency array)

**Trigger:** open `/console/request-logs`, select any bounded range (5m/15m/1h),
then leave the page open. Rows that fall outside the window stay visible
indefinitely.

The `filtered` memo computed its cutoff as `Date.now() / 1000 - RANGE_SECS[range]`
but listed only `[all, q, model, providerSel, status, surface, range]` as
dependencies. `Date.now()` is not a dependency, so the memo re-ran only when one
of those changed. The 15 s poll *usually* masked this by handing the memo a new
`all` array — except TanStack Query's structural sharing returns the
*referentially identical* array when the fetched payload is unchanged, which is
exactly the steady-state case (no new requests). So the window froze at whatever
moment the last filter change or data change happened: a row from 20 minutes ago
remained on a page claiming "Last 5 minutes".

**Fix:** drive the cutoff off the existing `useNow(15_000)` clock (`now / 1000`)
and add `now` to the dependency array, so the window slides on every tick
independently of whether the query returned new data. No extra timer — `now`
already existed for the `TimeAgo` cells.

**Status: fixed** — same change also lands the range selector work (30m default
plus 5m/15m/30m/1h/6h/24h/7d/all).

**Proof (RED/GREEN).** `web/` has no test runner (see #113), so the regression is
demonstrated with a Playwright harness rather than a pytest file. The
discriminating case is a row sitting just inside the boundary with a payload that
is **byte-identical on every poll**: react-query's structural sharing then hands
the memo the *same* `all` reference, so under the bug the memo never recomputes
at all. One synthetic row aged 285 s against a 5 m window, then 40 s of real time:

| Build | Row at t0 (285 s old) | Row at t+40 s (325 s old) | Verdict |
|---|---|---|---|
| pre-fix (`Date.now()`, no `now` dep) | visible (1 row) | **still visible (1 row)** | 🔴 RED — window frozen |
| fixed (`now / 1000`, `now` in deps) | visible (1 row) | **aged out (0 rows)** | ✅ GREEN |

The fixed bundle is byte-reproducible: rebuilding it yields `index-CI0nFjP2.js`,
the same hash as the original build. Live-data checks against 1837 real rows
(newest 23 h old): 5m/15m/30m/1h/6h → 0 rows, 24h → 17, 7d → 416, all → 1837;
24h+5xx → 10; search `minimax` → 10 of 17 (discriminating), an unmatched needle →
0 with the filter-mismatch empty state. Mobile 375×812: no horizontal overflow,
range control fully in viewport. 0 console errors.

---

### 37. `/docs` served Swagger instead of the built documentation UI

**Severity:** 🟡 Low (routing/UI)
**Files:** `wiwi/server/app.py:867` (pre-fix); `web/src/main.tsx:90`;
`wiwi/server/app.py:3838-3844`
**Trigger:** opening `/docs` in the browser.

FastAPI's `docs_url="/docs"` claimed the path before the root-mounted SPA, so
the browser saw the generated Swagger UI instead of the existing React
documentation page.

**Fix:** set `docs_url=None`; the root SPA fallback serves `/docs` to the React
route. Covered by `tests/test_fix_round37.py`.

**Status: fixed** — Swagger is disabled at `/docs` and the built SPA route is
used instead.

---

### 113. Unknown `/docs/*` paths bounced to the marketing landing page

**Severity:** 🟡 Low (routing/UX)
**Files:** `web/src/main.tsx` (route table, catch-all `*` → `/`)
**Trigger:** visiting any undefined docs URL, e.g. a stale link (`/docs/api`),
a typo (`/docs/quikstart`), or a trailing-slash variant (`/docs/`).

The SPA route table had entries only for the eleven known doc paths. Anything
else under `/docs/` fell through to the global `*` catch-all, which redirects
to `/` — the marketing landing page. A reader following a stale or mistyped
docs link was dumped out of the documentation set entirely instead of being
taken back to the docs hub.

**Fix:** add `<Route path="/docs/*" element={<Navigate to="/docs" replace />} />`
after the explicit docs routes, so unknown docs paths stay inside the docs set.
Unknown endpoint slugs (`/docs/endpoints/<unknown>`) remain handled in-context
by `DocsEndpointDetailPage`'s own NotFound. No regression test — `web/` has no
test runner; verified by build + browser exercise of `/docs/foo`, `/docs/`,
and `/docs/endpoints/unknown`.

**Status: fixed** — unknown docs paths redirect to the docs hub, not `/`.

---

## ✅ Fixed — round 41

The round-41 findings below were implemented and verified green (`1503 passed`,
`ruff` clean). Each fix has a regression test in `tests/test_fix_round41.py`.

| # | Fix | File(s) | Test |
|---|---|---|---|
| 69 | Retired keys recover after a bounded cooldown instead of permanently | `wiwi/router/router.py` (`mark_invalid`, `recover`) | `test_transient_5xx_does_not_permanently_retire_key` |
| 70 | Failed requests refund their estimated RPM/TPM reservation | `wiwi/ratelimit/memory.py` (`release`), `wiwi/server/app.py` | `test_failed_request_releases_tpm_reservation` |
| 71 | Last-admin guard normalizes `disabled` before the guard and write | `wiwi/server/app.py` | `test_last_admin_guard_cannot_be_bypassed_by_truthy_value` |
| 72 | Login throttle keyed on normalized account + IP (IP-only for master key) | `wiwi/server/app.py` | `test_login_throttle_keyed_on_normalized_username` |
| 73 | `X-Forwarded-For` trusted only from configured proxies | `wiwi/server/app.py` (`_client_ip`), `wiwi/config.py` (`trusted_proxies`) | `test_repeated_signup_with_rotating_xff_is_throttled` |
| 78 | `cycle_every_n` counters live on the router and drive key selection | `wiwi/router/router.py` | `test_cycle_every_n_rotates_under_skewed_weights` |
| 80 | Non-string username/password on auth endpoints → 400/401 | `wiwi/auth/users.py`, `wiwi/server/app.py` | `test_non_string_username_is_a_client_error` |
| 81 | `PATCH /admin/users` with a non-bool `disabled` → 400 | `wiwi/server/app.py` | `test_patch_user_non_numeric_disabled_is_a_client_error` |
| 82 | `{"enabled": "false"}` rejected on provider/key PATCH | `wiwi/server/app.py` | `test_provider_key_enabled_string_false_rejected` |
| 83 | Non-numeric `max_tokens`/`max_output_tokens` coerced/dropped on decode | `wiwi/ir/types.py` (`coerce_int`), `wiwi/wire/openai_chat.py`, `wiwi/wire/openai_responses.py` | `test_chat_non_numeric_max_tokens_rejected` |
| 86 | `read_timeseries(key_ids=[])` returns the same bucket grid as the no-rows path | `wiwi/logging_core/db_sink.py` | `test_timeseries_empty_key_ids_matches_no_rows_shape` |

The following round-41 entries are **verified false positives** after reading the
source:

- **#84** `head_evicted(last_seq)` — the arithmetic `first > last_seq + 1` *is* the
  membership test for a contiguous integer seq space. Evicted entries at or below
  `last_seq` are ones the client already received; only seqs in `(last_seq, first)`
  matter, and those are exactly what `first > last_seq + 1` detects. The audit's
  counterexample (`head_evicted(14)` with survivors 15..20 returning `False`) is
  correct behavior, not a bug. `test_head_evicted_is_membership_exact` is retained
  but does not discriminate the two formulations.

Also fixed (round 41, continued):

| # | Fix | File(s) | Test |
|---|---|---|---|
| 74 | OpenRouter flushes a deferred `ToolCallOpen` before closing a reused index | `wiwi/providers/openrouter_adapter.py` | `test_openrouter_reused_index_flushes_deferred_open` |
| 75 | NIM native markup allocates a fresh index instead of stealing a structured one | `wiwi/providers/nim_native_tools.py` | `test_nim_native_markup_does_not_collide_with_structured_index` |
| 76 | Gemini usage-without-finishReason completes cleanly (`UsageFinal`+`Finish`+`StreamEnd`) | `wiwi/providers/gemini_adapter.py` | `test_gemini_usage_without_finish_reason_completes_cleanly` |
| 77 | OpenCode clears per-stream tool state on `response.failed` | `wiwi/providers/opencode_adapter.py` | `test_opencode_failed_clears_resp_tools` |
| 79 | A cleanly completed stream graduates a probation deployment | `wiwi/core/gateway.py` | `test_streaming_success_graduates_probation_deployment` |
| 85 | Non-string provider key secret rejected with 400 on all write paths | `wiwi/server/app.py` | `test_provider_key_non_string_secret_rejected` |
| 87 | Unknown WorkBuddy business envelope is retryable (failover runs) | `wiwi/providers/workbuddy_adapter.py` | `test_workbuddy_unknown_envelope_is_retryable` |
| 88 | NIM synthesizes an Open for args-before-id and adopts a later real id | `wiwi/providers/nim_adapter.py` | `test_nim_synthesized_open_adopts_later_real_id` |

All 38 round-41 tests were verified to fail against the pre-fix source.

---

## ✅ Fixed — round 42

The round-42 findings below were implemented and verified green (`1537 passed`,
`ruff` clean). Each fix has a regression test in `tests/test_fix_round43.py`
(the round-42 number was already taken by the pre-existing proxy-log file).

| # | Fix | File(s) | Test |
|---|---|---|---|
| 89 | Signup throttle consumes a slot on each attempt | `wiwi/server/app.py` (`auth_signup`) | `test_signup_throttle_counts_attempts` |
| 90 | Non-streaming over-budget 402 is logged exactly once | `wiwi/server/app.py` (`run_chat_like`) | `test_over_budget_response_logged_once` |
| 91 | Streaming 401 runs the on-demand token refresh and retries | `wiwi/core/gateway.py` (`_pump_once`) | `test_streaming_401_is_refreshed_and_retried` |
| 92 | An undecodable 200 becomes a retryable `WiwiError` (failover runs) | `wiwi/core/gateway.py` (`_decode_response_guarded`) | `test_undecodable_200_is_retryable_wiwi_error` |
| 93 | A clean completion decays the deployment failure streak | `wiwi/router/router.py` (`record_success`), `wiwi/core/gateway.py` | `test_success_decays_deployment_failures` |
| 94 | Gemini `thought: true` parts emit `ThinkingDelta`, not visible text | `wiwi/providers/gemini_adapter.py` | `test_gemini_thought_part_is_not_visible_text` |
| 95 | OpenRouter dict-form tool arguments decode instead of crashing | `wiwi/providers/openrouter_adapter.py` | `test_openrouter_dict_form_tool_arguments_do_not_crash` |
| 96 | A 200 probe body carrying an error envelope is not HEALTHY | `wiwi/core/recovery.py` (`probe_verdict`, `_probe`) | `test_healer_probe_treats_200_envelope_error_as_unhealthy` |
| 97 | A model-error probe does not grow the key restore streak | `wiwi/core/recovery.py` (`_probe_pair`) | `test_healer_does_not_restore_key_on_model_error_probe` |
| 98 | Resume no longer credits the key at connect time | `wiwi/core/gateway.py` (`_attempt_resume`) | `test_resume_does_not_credit_key_at_connect` |
| 99 | Consumer cancel grace covers `stream_grace_drain_s` | `wiwi/core/gateway.py` (`pump_cancel_grace`) | `test_pump_cancel_grace_follows_configured_drain` |
| 100 | Malformed `text.format` is ignored, not a 500 | `wiwi/wire/openai_responses.py` | `test_responses_malformed_text_format_is_not_a_500` |
| 102 | Gemini request encoder preserves `ThinkingPart` | `wiwi/providers/gemini_adapter.py` | `test_gemini_encoder_preserves_thinking_part` |
| 103 | `redacted_thinking` survives the Anthropic streaming path | `wiwi/providers/anthropic_adapter.py`, `wiwi/streaming/deltas.py`, `wiwi/wire/anthropic_messages.py` | `test_anthropic_stream_preserves_redacted_thinking` |
| 104 | Tool-arg accumulation is list-append/join, not O(n²) | `wiwi/core/gateway.py` (`_apply_event`, pump, `_validate_closed_tool_args`) | `test_arg_buf_accumulation_reassembles_fragments` |
| 106 | A resumed attempt's usage/cost folds into the originating request | `wiwi/core/gateway.py` (`merge_resume_context`) | `test_resume_merges_usage_into_originating_context` |
| 107 | A resumed turn answers replayed `tool_use` with `tool_result`s | `wiwi/streaming/resume.py` (`build_continuation_messages`) | `test_resume_answers_replayed_tool_use` |
| 108 | Loop detection no longer penalises key/deployment health | `wiwi/core/gateway.py` (`loop_abort_error`, pump loop branch) | `test_loop_detection_does_not_penalise_key_health` |
| 109 | An alias cycle fails closed instead of resolving arbitrarily | `wiwi/router/router.py` (`resolve_group`) | `test_alias_cycle_resolves_to_nothing` |
| 110 | Non-dict SSE frames/choices are skipped, never crash | `wiwi/providers/openai_adapter.py`, `wiwi/providers/openrouter_adapter.py` | `test_openrouter_non_dict_sse_frame_does_not_crash`, `test_openai_choice_non_dict_does_not_crash` |

Two existing tests encoded the pre-fix behaviour these fixes correct and were
updated: `tests/test_bugfix_round5.py::test_attempt_resume_calls_on_result`
(now asserts the key is *not* credited at connect — #98) and
`tests/test_recovery.py::test_400_does_not_restore_key_and_escalates_dep_circuit`
(now asserts a 400 does *not* restore the key — #97).

All new round-42 tests were verified to fail against the pre-fix source.

---

## ✅ Fixed — round 44

The round-44 findings below were implemented and verified green (`1556 passed`,
`ruff` clean). Each fix has a regression test in `tests/test_fix_round44.py`.

| # | Fix | File(s) | Test |
|---|---|---|---|
| 111 | WorkBuddy `User-Agent` tracks the live CodeBuddy CLI version (npm-polled, 5-min TTL) | `wiwi/providers/workbuddy_version.py` (new), `wiwi/providers/workbuddy_auth.py`, `wiwi/server/app.py` (lifespan) | `test_headers_use_live_codebuddy_version`, `test_gateway_sends_live_user_agent_upstream` |
| 112 | Bare-token WorkBuddy requests carry `User-Agent` + `X-Requested-With` instead of leaking httpx's own UA | `wiwi/providers/workbuddy_adapter.py` (`headers`) | `test_bare_token_path_carries_live_user_agent` |

Both fixes descend from the same root: the WorkBuddy provider's client
fingerprint was a hardcoded constant rather than a live value. #111 is the
same bug class as the earlier Cline live-version fix (see "✅ Fixed — Cline
live version fingerprint" below); #112 is a second, independent occurrence of
the same omission on the paste-a-token path.

All new round-44 tests were verified to fail against the pre-fix source.

**Upstream evidence for the fingerprint format** (recorded so this is not
re-derived — read from the published `@tencent-ai/codebuddy-code` bundle,
`dist-server/codebuddy.js`). The CLI's chat path *does* run
`delete p.authorization, delete p["user-agent"]`, but that is not the end of
the story — the delete is followed by two restores:

1. `I2()` — resolved to module 26492's `runIdentityHeaders`, which returns
   **identity headers only** (`X-User-Id`, `X-Enterprise-Id`, auth method, id
   source, base64 userinfo) and never a `user-agent`.
2. `UserAgentHttpInterceptor` (module 23994), registered on `restOperations` —
   the exact object the chat path dispatches through
   (`this.restOperations.request(e)`). It rebuilds the header as
   `[productName/productVersion, platform/platformVersion, extension].join(" ")`,
   with each field resolving concretely: `product.json` supplies
   `productName: "CodeBuddy"`, `platform: "CLI"`, `deploymentType: "SaaS"`
   (SaaS is *not* in the interceptor's `{Cloud-Hosted, Self-Hosted}` ASCII-safe
   set, so the `productName/productVersion` branch is the live one), and
   `dist-server/2828.codebuddy.js` supplies `platformVersion = productVersion =
   package version` (`e?.productVersion || oK.version`). `bin/codebuddy` seeds
   that from the npm package version.

Net result: **`CLI/<npm version> CodeBuddy/<npm version>`** — byte-for-byte the
string wiwi now sends, derived from the same npm package wiwi polls. The
`CodeBuddy/` token does not appear anywhere in the bundle as a literal; it is
composed at runtime from `product.json` + the package version.

---

## ✅ Fixed — round 45

The round-45 findings were implemented and verified green (`1570 passed`,
`ruff` clean). Each fix has a regression test in `tests/test_fix_round45.py`,
and every new test was verified to fail against the pre-fix source (RED/GREEN
via targeted `git stash` of the fixed module). These four share a root cause
worth naming: **each is a shipped fix whose revival/refund/replay path is
unreachable or lossy in a state its regression test never exercised.**

| # | Fix | File(s) | Test |
|---|---|---|---|
| 115 | Expired bounded retirement revives on the live path; terminal invalid stays retired; `recover()` honors the distinction; `pick_key`'s soonest-window hint covers invalid keys | `wiwi/router/router.py` (`ProviderKey.available`, `recover`, `pick_key`) | `test_expired_bounded_retirement_is_available_again`, `test_expired_retired_key_revives_through_pick_key`, `test_terminal_invalid_stays_out_of_rotation`, `test_pick_key_soonest_covers_invalid_windows`, `test_expired_retired_key_reaches_pick_deployment` |
| 116 | Non-streaming responses are cached only AFTER the virtual-key budget decision, so a 402'd completion can never be replayed as a free 200 cache hit | `wiwi/server/app.py` (`run_chat_like` non-streaming tail) | `test_cache_never_serves_over_budget_payload`, `test_cache_still_serves_funded_keys` |
| 117 | A reconnect whose replay gate misses adopts the client's stream id and opens its journal BEFORE dispatch; failures before the first chunk release the journal | `wiwi/server/app.py` (replay gate, streaming branch, `_stream_response`, `_abandon_journal`) | `test_reconnect_during_redispatch_ttft_tails_not_double_dispatch`, `test_pre_dispatch_failure_releases_adopted_journal` |
| 118 | Resume continuation preserves thinking fidelity: signatures travel with their block, `redacted_thinking` blobs keep their block type, text-interleaved runs split into separate blocks; `_arg_bufs` also joins once per close (#104 residual) | `wiwi/streaming/resume.py` (`replay_thinking_parts`, `build_continuation_messages`, `replay_tool_calls`) | `test_continuation_keeps_signature_and_redacted_blocks`, `test_continuation_thinking_encodes_to_valid_anthropic_blocks`, `test_continuation_splits_thinking_runs_on_interleaved_text`, `test_replay_thinking_still_returns_joined_text`, `test_tool_args_join_survives_many_fragments` |

**#115 details.** `ProviderKey.available` excluded `"invalid"` unconditionally,
so once a key was retired (5 consecutive non-200s under `any_error`), an
elapsed cooldown window changed nothing: `ProviderAccount.healthy` stayed
False, `pick_deployment` filtered the deployment out, and `pick_key` — the
only production caller of `recover()` — was never reached. The #69 docstring
promised "the key revives itself once the window elapses"; verified by
execution that it did not (single-key providers 503 forever). The round-41
test passed only because it called `key.recover()` manually. Two adjacent
defects fixed in the same pass: (a) `recover()` unconditionally resurrected
*terminal* invalid keys (`mark_invalid(None)`, `cooldown_until == 0.0`),
contradicting the "genuinely dead credentials" contract in `mark_invalid`'s
docstring — now only keys with a timed window revive; (b) `pick_key`'s
`soonest` retry hint ignored invalid windows entirely, so a retried request
could be told 5s when the nearest revival was 5s away *or* be told 30s when
an invalid key's window ended sooner. The stale mirror helper in
`tests/test_fix_round41.py` (which used the pre-#69 terminal
`mark_invalid()`) was updated to mirror production's bounded window.

**#116 details.** The non-streaming tail cached the payload while `ctx.status`
was still 200, *then* ran `update_spend` and flipped to 402. The cache-hit
path returns before dispatch with no `update_spend` anywhere, so every
identical request for the TTL got the full completion as a 200 with zero
spend recorded — a hard budget cap converted into unlimited free replay. The
streaming path never had the bug (it 402s after the fact via
`ctx.metadata["budget_exceeded"]`, with nothing cached). The reorder keeps
402-logging exactly once (#90) and caching exactly once for funded keys.

**#117 details.** The #66 gate is `replay or complete or active`, but all
three halves describe the ORIGINAL stream id — and the re-dispatched attempt
journaled under its own NEW `ctx.request_id`, which the reconnecting client
never saw. Worse, the journal only opened when the response generator first
ran (after the first upstream delta), so a reconnect arriving during TTFT
(slow upstream, multi-deployment failover — easily seconds) found no journal
at all and dispatched a second upstream call while the first was still
running: double call, double billing. The fix opens the journal before
`gateway.stream(ctx)` dispatches, and when the gate misses for a request
carrying `x-wiwi-stream-id` (owner-checked), adopts that id for the attempt's
journal so later reconnects see it active and tail it. Attempts that fail
before the first chunk release the journal (`_abandon_journal`) so nothing
tails a stream that never produced content.

**#118 details.** `build_continuation_messages` used `replay_thinking()` — a
bare-text concatenation that dropped `signature` and `block_type`/`data` —
so a resumed request's final assistant turn carried an unsigned `thinking`
block (Anthropic validates and 400s) and any `redacted_thinking` block
(mandatory before tool use on some turns) vanished. The tape already stored
full fidelity (#103); the continuation builder was the lossy link; the
Anthropic adapter's encoder was verified correct given structured parts. The
new `replay_thinking_parts()` folds consecutive thinking deltas into one
block carrying the last signature seen in the run, keeps redacted deltas as
their own blocks, and starts a new block whenever a text delta interleaves.

**Register correction (#104).** The #104 entry claimed the unbounded
`_arg_bufs` string concatenation was fixed, but the fix landed only in
`gateway.py`'s pump; `resume.py:125` still did `arg_bufs[d.index] += ...`.
Corrected this round (list buffers joined once per close) and the register
entry now reflects both sites.

---

## ✅ Fixed — round 46

The round-46 fix was implemented and verified green. RED/GREEN was verified by
stashing the fixed module: the primary regression fails against the pre-fix
source (the dead key was restored to `probation`) while the healthy-SSE control
keeps passing, so the fix cannot overcorrect into never restoring force_stream
keys.

| # | Fix | File(s) | Test |
|---|---|---|---|
| 119 | A force_stream probe body carrying a business-error envelope inside an SSE `data:` frame is no longer HEALTHY | `wiwi/core/recovery.py` (`_body_is_error_envelope`) | `test_healer_does_not_restore_key_from_sse_error_envelope` (plus control `test_healer_restores_key_from_healthy_sse_probe`) |

**#119 details.** `_probe` sends force_stream (WorkBuddy/Cline) probes with
`stream=True`, so a business error on an HTTP 200 arrives as an SSE body with
the envelope inside a `data:` frame. The #96 fix only `json.loads`-ed the bare
body, so the wrapped envelope raised `ValueError` → "not an envelope" →
HEALTHY, and a still-dead key was restored into probation. `_body_is_error_envelope`
now also parses SSE frames via the shared `LineSSEParser` (multiline `data:`
payloads included; a final frame without a trailing blank line is covered by
`flush()`) and applies the same `{"code": N≠0}` test to each frame's payload.
Bare-JSON 200 bodies keep the original path; `{"code": 0}` success envelopes
and healthy SSE completions remain HEALTHY.

---

## 🟡 Medium — round 45 (new)

Findings from the same audit pass, verified against source but not yet fixed.
None are covered by existing tests. (#119 above was fixed in round 46, #121 in
round 48, and #120 in round 69 — each now carries a **Status: fixed** line
below; the remaining entries are still live.)

### 120. Journal-replay path never reconciles the admission-time TPM reservation and is invisible to request logs
**File:** `wiwi/server/app.py:1122` (reserve), `:1161-1188` (replay return)
**Trigger:** reconnect served from the journal, virtual key with `tpm` set.
`enforce_rate_limit` reserves an estimated TPM slot for every request; a replay serves zero upstream tokens but the replay branch returns without `_release_tpm_reservation` or `log_request` — the cache-hit path states the exact rule ("served locally: the upstream consumed zero tokens, so the estimated reservation taken at admission must be refunded") and follows it. Each reconnect permanently consumes `len(body)/4` estimated TPM for the 60s window and an RPM slot, and replays never appear in `/admin/stats`. Fix: refund (and log) on the replay branch, mirroring the cache-hit path.

**Status: fixed** — round 69 (2026-09-17). The replay branch now logs and refunds
before returning its `StreamingResponse`:

```python
state_.logs.log_request(build_log_event(ctx))
await _release_tpm_reservation(info, ctx)
return StreamingResponse(_replay_iter(), ...)
```

The refund is taken **eagerly** (before the response object is built, not inside
`_replay_iter`) because the reservation was taken eagerly at admission, and a
journal replay consumes no tokens at any later point — the generator only reads
from disk. `build_log_event(ctx)` is called with `ctx.usage` unset and `ctx.cost`
0, so the row records a real request with zero tokens and zero cost: the replay
consumed nothing upstream and billing the caller would be the opposite error.
`ctx.status` keeps its `200` default, which is the status actually returned.

**Why this was still live, and why round 66 sharpened it.** #120 predates the
round-66 refund work (AUDIT #174), which made `release()` strictly
identity-matched and removed the "pop the newest estimated event" fallback. That
fallback was the only thing that could incidentally reclaim a leaked replay
reservation, so #174 — correct in itself — turned a recoverable leak into a
permanent one for the window. Verified against the limiter directly: after a
leaked replay reservation, a later `release()` for a *different* request of the
same key leaves the window total unchanged (`k1:tpm` stays at 150). The fix is
the missing refund, not a looser finder.

**Reachable without any upstream failure**, which is what makes it worth the
severity bump: a client reconnecting after a dropped stream is the ordinary case
(Claude Code re-POSTs with `x-wiwi-stream-id` + `Last-Event-ID`), so each
reconnect silently burns a slot. With `global_rpm` set, the leaked slot is
shared — three reconnects against `global_rpm=3` deny *every other key* for 60 s.

Pinned by `tests/test_fix_round69.py` (4 tests, all failing on the pre-fix code
by reverting `wiwi/server/app.py`): `test_replay_does_not_consume_a_global_rpm_slot`
(behavioral — a later request still finds room), `test_replay_does_not_consume_global_tpm`
(window total ≤ the original stream's real usage), `test_replay_appears_in_the_request_log`,
and `test_replay_log_row_records_zero_upstream_cost` (the control: the added
refund/log must not fabricate usage or cost for a replay).

### 121. `release()` refunds the key's RPM reservation but leaks the global RPM reservation
**File:** `wiwi/ratelimit/memory.py:171-188`; caller `wiwi/server/app.py:1000`
**Trigger:** `global_rpm` configured; any request failing upstream after admission.
`check()` reserves one event in both `global:rpm` and `{key_id}:rpm`; `release()` pops only `f"{key_id}:rpm"`. Verified: `RateLimiter(global_rpm=2)`, one admitted-then-released request leaves a phantom event; the next real request at the cap 429s. Each failed request burns one global-RPM slot for the window. Residual of the #70 fix. Fix: drop the newest RPM event from `"global:rpm"` too.

**Status: fixed** — round 48 (2026-09-12). `release()` now refunds both RPM scopes:

```python
# RPM reservations carry no request id: drop the newest one so the
# failed request does not permanently consume an rpm slot. Admission
# takes a slot in *both* the key window and the global one, so both
# must be refunded (AUDIT #121, residual of the #70 fix).
for scope in (f"{key_id}:rpm", "global:rpm"):
    w = self._windows.get(scope)
    if w is None:
        continue
    self._prune(w, now)
    if w.events:
        w.total = max(0, w.total - w.events.pop().tokens)
```

Two preconditions were verified before widening the refund. (a) Every path that reaches
`release()` is strictly post-admission. The wrapper `_release_tpm_reservation`
(`app.py:982`) is its only caller, from four sites (`app.py:1246, 1279, 1346, 1354`) —
all of them error/cache-hit exits that run after `enforce_rate_limit` returned, and
`enforce_rate_limit`'s denial arm returns at `app.py:1137-1138` *before* a
`RequestContext` exists, so a rejected request cannot reach any of them. (b) `key_id` is
`"k" + secrets.token_hex()` (`auth/service.py:225`) or the literal `"master"`
(`auth/service.py:163`), so no real key can produce a `f"{key_id}:rpm"` equal to
`"global:rpm"` — the loop cannot refund the same window twice.

The pop is deliberately identity-blind (RPM events carry no `request_id`), so under
concurrency it may remove a *different* admitted request's event. That is safe because
attribution is irrelevant to the invariant that matters: admission takes exactly one
event per RPM scope and a failed admitted request refunds exactly one, so the window
tracks the number of *failed* requests and errs permissive — never leaky. Verified
directly: two admissions then two releases leave `global:rpm` at 0 events.

Pinned by `tests/test_fix_round48.py` (`test_release_refunds_global_rpm_reservation`),
with two controls: `test_release_still_refunds_the_key_rpm_reservation` (the #70 refund
must survive) and `test_release_does_not_refund_confirmed_usage` (release must still
never remove reconciled `record_tokens` usage — it refunds estimates only).

> **Register hygiene:** this finding was independently rediscovered during round 48 and
> filed here instead of under a new number, per the do-not-re-report rule. It was briefly
> referred to as "#132" while being worked; **no entry numbered 132 exists**, and the two
> citations that used it (`wiwi/ratelimit/memory.py`, `tests/test_fix_round48.py`) were
> repointed to #121 rather than left as dangling references.

### 122. System-list `text` blocks are never coerced — `system: [{"type":"text","text":null}]` 500s every adapter's encode
**File:** `wiwi/wire/anthropic_messages.py:26`
The message-block path coerces (`raw_text if isinstance(raw_text, str) else ""`, line 53 — UPDATE.md §39.1) but the system branch stores the raw scalar. Verified: decode yields `TextPart(text=None)`; `OpenAIAdapter.encode_request` and `AnthropicAdapter.encode_request` both raise `TypeError` in `" ".join(...)`. `run_chat_like` catches only `(DialectError, ValueError)` at decode, so the sync path is an unhandled 500. Fix: same coercion as line 53.

### 123. Non-string `text` scalars inside list-form content crash decode — one root cause, four sites, all three surfaces 500 on replayed history
**Files:** `wiwi/wire/anthropic_messages.py:93-95` (tool_result content list), `wiwi/wire/openai_chat.py:127-129` (tool message content), `wiwi/wire/openai_responses.py:88-92` (`_item_text`), `:270` (reasoning summary)
Each builds a text list via `b.get("text", "")` without an isinstance filter and joins it; `{"type":"text","text":5}` (or a dict) → `TypeError` out of `decode_request` → 500. Every sibling text scalar in these files is guarded; these four were missed. Fix: filter to `isinstance(t, str)` at each site (mirror of anthropic_messages.py:53).

### 124. Chat codec: scalar-truthy `tool_calls[].function.arguments` (`true`, `5`) → TypeError → 500
**File:** `wiwi/wire/openai_chat.py:96-99`
UPDATE.md §39.1 fixed the dict case (`isinstance(raw_args, dict)`); a truthy scalar falls through `raw_args or "{}"` and `json.loads(True)` raises `TypeError`, which `except json.JSONDecodeError` does not catch. The Responses surface already defends this exact case (`_load_args` catches `TypeError` with a docstring explaining the 500). Fix: `isinstance(raw_args, str)` check or add `TypeError` to the except clause. Same unguarded pattern exists in the provider decoders (`openai_adapter.py:302-304`, `openrouter_adapter.py:186+`) where #92's wrapper downgrades it to a retryable failure; the wire path has no such wrapper.

**Status: fixed** — round 54, with the residual. The wire fix is the `isinstance(raw_args, str)` guard; the provider decoders turned out to be a *worse* variant than the entry assumed (see the residual note below), and all live sites are fixed. `tests/test_fix_round54.py`.

**Residual found while fixing.** The entry called the provider-decoder sites "the same unguarded pattern … where #92's wrapper downgrades it to a retryable failure". That is true of the *non-streaming* path (`openai_adapter.py:295-316`, verified: `TypeError` out of `decode_response`). On the **streaming** path it is worse: `args_fragment` is typed `str`, the scalar was placed on the delta unchecked, the gateway buffered it, and the Chat encoder's frame serialization raised mid-stream — the client received **HTTP 200, a partial `tool_calls` frame, then a synthetic `{"error": …}` frame**. Reproduced live, pre-fix:

```
HTTP 200
data: {…"tool_calls":[{"index":0,"id":"c1","type":"function","function":{"name":"f","arguments":""}}]…}
data: {…"tool_calls":[{"index":0,"function":{"arguments":true}}]…}
data: {"error":{"message":"sequence item 0: expected str instance, bool found","type":"api_error"}}
```

Live sites fixed with a shared `coerce_args_fragment` (`wiwi/providers/base.py`): `openai_adapter.py` (both stream sites + the non-stream parse), `openrouter_adapter.py` (both stream sites + the non-stream parse, which already caught `TypeError` but still failed the response), `nim_adapter.py` (both stream sites, including the aliased-tool buffer that concatenates fragments — `str + bool` raised there too).

### 125. Sync `/v1/messages` encode drops `redacted_thinking` data
**File:** `wiwi/wire/anthropic_messages.py:285-289` (`encode_response`)
Verified output for an upstream turn containing a redacted block: `[{'type': 'thinking', 'thinking': ''}, ...]` — the encrypted blob is silently dropped and the emitted thinking block has no signature, i.e. the next turn's history replay is 400-bait. The streaming encoder got a redacted branch in the #103 fix (feed(), lines 448-463) and the upstream direction honors it (`anthropic_adapter.py:337-341`); the client-facing sync encode never got the mirror branch. Fix: add a `t.block_type == "redacted_thinking"` branch emitting `{"type": "redacted_thinking", "data": t.data}`.

**Status: fixed** — round 54. The mirror branch is in place; verified end-to-end (encode → client echoes history → `AnthropicAdapter.encode_request`) that the blob and the `redacted_thinking` type both survive to the upstream body. `tests/test_fix_round54.py` (`test_sync_anthropic_encode_preserves_redacted_thinking`, `test_redacted_thinking_survives_client_replay_round_trip`), with an ordinary-signed-thinking control.

### 126. ⚪ Tools loop lacks the `isinstance(ttype, str)` guard — non-string `type` crashes in `builtin_tools.canonical_for`
**File:** `wiwi/wire/anthropic_messages.py:157` (crash at `wiwi/ir/builtin_tools.py:92`)
`{"type": 5}` (or any non-str truthy type) in a tool entry passes `if not wire_type: return None`, misses both reverse maps, reaches the Anthropic family-prefix loop, and `5.startswith(...)` raises `AttributeError` → 500. The message-block loop got exactly this guard in the round-30 fix (line 44, with a comment explaining the 500 it prevents); the tools loop never did. Fix: skip non-string `ttype` like the block loop, or make `canonical_for` return None for non-str input.

### 127. ⚪ Non-string `stop` items forwarded upstream (Anthropic codec filters; Chat/Responses don't)
**Files:** `wiwi/wire/openai_chat.py:183`, `wiwi/wire/openai_responses.py:290`
`"stop": [1]` (or a truthy dict) lands verbatim in `GenParams.stop` (typed `list[str]`) and is forwarded to the upstream, which 400s with an error the gateway reports as upstream `invalid_request_error` far from its origin. The exact shape AUDIT #83 fixed for numeric fields; the Anthropic codec already filters (`stop_seqs = [s for s in stop_raw if isinstance(s, str)]`, anthropic_messages.py:251). Fix: mirror that filter in both codecs.

---

## 🔴 Critical — round 42 (new)

### 89. Public signup throttle counts nothing — unlimited account creation
**File:** `wiwi/server/app.py:3183` (call), `221-234` (`check`), `236-241`
(`record_failure`), `520` (limit); `wiwi/auth/users.py:132-148`
**Trigger:** any unauthenticated caller hits `POST /auth/signup` repeatedly from a
fixed address (no header rotation needed).

`auth_signup` consults `state.signup_throttle.check(scope)`, but `_AttemptThrottle.check`
only *filters and re-stores* the existing event list — it never appends a hit. Only
`record_failure` appends, and a repo-wide grep shows it is called solely on
`login_throttle` (`app.py:3280/3287/3290`); nothing ever calls
`signup_throttle.record_failure`. So `check` always sees `< limit` events and returns
`True, 0`. The 5-per-hour cap at `app.py:520` is structurally inert.

**Consequence:** unlimited account creation from one IP, each registration performing a
200k-iteration PBKDF2 hash, writing a `users` row, and minting a playground key. Distinct
from #58 ("no throttling") and #73 (XFF rotation): here the throttle exists but counts
nothing, so even a spoof-proof IP bucket is unlimited.

**Fix:** call `await state.signup_throttle.record_failure(scope)` after a successful
registration, or make `check` consume a slot.

### 90. Non-streaming over-budget response is logged twice — cost/tokens/requests double-counted
**File:** `wiwi/server/app.py:1209` and `1233-1235`; sink `wiwi/logging_core/db_sink.py:226-234`;
broadcast `wiwi/logging_core/subsystem.py:190`; rollups `wiwi/server/stats.py:78-89`
**Trigger:** a virtual key with `max_budget` whose next request's actual `ctx.cost` crosses
the cap, on any non-streaming surface.

`run_chat_like` logs the successful 200 event first (`app.py:1209`). Then when
`state_.auth.update_spend(...)` returns `False` (`app.py:1230-1233`) it sets `ctx.status = 402`
and calls `state_.logs.log_request(build_log_event(ctx))` **a second time** (`app.py:1235`).
Both events carry the same `request_id`, tokens, and cost; `request_logs` has no uniqueness
on `request_id`, so both rows persist and the SSE ring publishes both. `/admin/stats/*` and
`/metrics` then double-count that request. The streaming path logs once (`app.py:1388`, only
annotating metadata on 402 at `1400-1402`) — asymmetric.

**Fix:** move the `log_request` at `app.py:1209` below the budget check, or skip the second
emission and annotate.

### 91. Streaming request has no 401 on-demand token refresh — Cline/WorkBuddy streams fail on rotated tokens
**File:** `wiwi/core/gateway.py:672-688` (`_pump_once` non-200) vs `146-181` (`_call_once`),
`245-281` (`_complete_via_stream`); hooks at `70-78`
**Trigger:** a live client streaming request (`ir_req.stream == True`) to a Cline or WorkBuddy
deployment after the OAuth access token was rotated upstream — their normal steady state.

`_call_once` and `_complete_via_stream` both call `_resolve_refresh_hook(dep)`, rotate the
token, and retry once on 401. `_pump_once` — the path that actually serves every streaming
Cline/WorkBuddy client request — does not: it converts the 401 straight to `err_box`, so a
request that could have succeeded fails with an auth error and (in `any_error` mode) feeds
`err_count += 2`, eventually retiring a healthy key. The `force_stream=True` providers are
exactly the ones whose streaming path is primary, making the non-streaming refresh branch
largely unreachable for them.

**Fix:** mirror the `_call_once` 401 branch in `_pump_once`: refresh hook → rebuild headers
from the live key → re-issue `self._client.stream(...)` once before setting `err_box`.

### 92. A 200 whose body fails to decode bypasses retries, failover, and key/deployment penalties
**File:** `wiwi/core/gateway.py:186` (and `178-181` retry branch); `wiwi/router/router.py:865`
**Trigger:** a provider/proxy returns HTTP 200 with a non-JSON or wrong-shaped body (Cloudflare
HTML interstitial, truncated body, JSON array where an object is expected) on a non-streaming request.

`adapter.decode_response(resp.status_code, resp.content)` runs **outside any try/except** in
`_call_once`. `orjson.loads` raises `JSONDecodeError` and a list body makes `.get()` raise
`AttributeError`; neither is a `WiwiError`, and `execute_with_retries` only catches `WiwiError`
(`router.py:865`). The exception propagates to `run_chat_like`'s `except Exception` → 500, with
no retry, no fallback group, no key cooldown, no `record_fail`. OpenRouter's `decode_response`
compounds this (see #95).

**Fix:** wrap `decode_response` in `try/except Exception` inside `_call_once` and re-raise as a
retryable `WiwiError(502, "api_error", ...)`.

---

## 🟠 High — round 42 (new)

### 93. Deployment cooldown counts absolute failures with no success decay
**File:** `wiwi/router/router.py:248-265` (`record_fail`), `wiwi/core/gateway.py:888`
**Trigger:** a deployment serving steady traffic with a low but non-zero 5xx/408 rate.
`allowed_fails=3`, window `max(300, min(6*cooldown, 3600))` (`router.py:260`).

`record_fail` only appends timestamps and clears them when the cooldown trips (`router.py:265`).
Nothing on the success path prunes or decays `self.fails`, so the test is "≥3 failures in 300 s",
not "3 *consecutive* failures". A deployment at high request volume with a 0.1% error rate
accumulates 3 failures roughly every 30 s and is cooled continuously.

**Consequence:** a healthy deployment is repeatedly marked unavailable, pushing traffic to
worse siblings or (in a single-deployment group) adding latency/503s. This is the opposite
failure mode of #19 and is not registered.

**Fix:** clear/decay `fails` on a successful `execute_with_retries` completion of that
deployment, or track a failure *rate* over the window rather than an absolute count.

### 94. Gemini `thought: true` parts leak as visible assistant text (CoT returned to the client)
**File:** `wiwi/providers/gemini_adapter.py:144-149` (non-stream), `194-196` (stream)
**Trigger:** Gemini 2.5 thinking models return `parts: [{"text": "...", "thought": true,
"thoughtSignature": "..."}]`.

Both loops match `if "text" in part` before checking `part.get("thought")`, so
chain-of-thought is emitted as `TextDelta` / appended to `turn.text` and returned as the
assistant's answer; the `thoughtSignature` is discarded. Non-stream cannot distinguish
reasoning from content; streaming shows raw CoT as the reply.

**Fix:** branch on `part.get("thought")` first → emit `ThinkingDelta` (or append
`ThinkingPart`) and preserve `thoughtSignature` as `signature`; only non-thought `"text"`
becomes visible text.

### 95. OpenRouter `decode_response` crashes on dict-form tool arguments (500 on replayed history)
**File:** `wiwi/providers/openrouter_adapter.py:180-183`; contrast `openai_adapter.py:296-300`
**Trigger:** any OpenRouter response whose `tool_calls[].function.arguments` is a JSON **object**
rather than a string — the args-as-object gateway case the base class explicitly guards
(`openai_adapter.py:296-300`).

`json.loads(raw_args)` on a dict raises `TypeError`, which the `except json.JSONDecodeError`
at line 183 does not catch, so it escapes `decode_response` and surfaces as a 500 on every
turn that replays such history.

**Fix:** before `json.loads`, `if isinstance(raw_args, dict): args = raw_args;
raw_args = json.dumps(raw_args)`.

### 96. HealthHealer treats any HTTP 200 as healthy without decoding the body
**File:** `wiwi/core/recovery.py:443-449` (`_probe`); contrast `wiwi/providers/workbuddy_adapter.py:242-251`
**Trigger:** `healer.enabled: true`; a WorkBuddy (or any provider whose 200 SSE body carries an
error envelope) key has a dead session/business error.

`_probe` returns `HEALTHY` on `resp.status_code == 200` and never inspects the body (its own
docstring says so). WorkBuddy signals a dead session (`code 12153`) or other envelope errors
inside an HTTP-200 SSE frame. The healer classifies a broken target as healthy, increments the
restore streak, and after `probes_to_restore` restores the key/deployment into probation —
re-exposing a still-broken credential to live traffic.

**Fix:** for `force_stream` providers, scan the SSE body (or reuse the adapter's
`decode_stream_event`) and classify an error envelope as a failure before declaring HEALTHY.

### 97. HealthHealer restores a retired key from a probe that failed for model reasons
**File:** `wiwi/core/recovery.py:370-384`, `403-412`
**Trigger:** a key with `status="invalid"` whose probe's 1-token request gets a `400`/`404`
(e.g. `max_tokens=1` rejected by a reasoning model, or model temporarily unavailable).

`probe_verdict` maps 400/404 → `CREDS_VALID_MODEL_BAD`. In `_probe_pair` that branch
increments the **key's** restore streak (`recovery.py:383`) and calls `_maybe_restore_key`
(`384`), restoring the key to probation once the streak reaches `probes_to_restore`. The probe
never actually exercised the key successfully.

**Fix:** only grow the key restore streak on a genuine HEALTHY probe; treat
`CREDS_VALID_MODEL_BAD` as key-health-neutral.

### 98. Resume re-introduces the AUDIT #6 connect-time key credit — plus doubled `req_count`
**File:** `wiwi/core/gateway.py:573` vs `834-847` (`_defer_key_credit` set at `430`)
**Trigger:** a mid-stream resume whose new connection succeeds.

`_attempt_resume` calls `on_result_locked(key, 200, None)` immediately at connect (`gateway.py:573`).
When that pump later completes cleanly it credits the same key again (`gateway.py:839`), so
`req_count` is incremented twice. A resume provider that connects then dies mid-stream resets
`err_count` to 0 and never accumulates a retirement streak — the exact defect #6 fixed for the
primary path. (Distinct from #5, which was locked-vs-unlocked.)

**Fix:** remove the connect-time credit at `gateway.py:573`; let the pump's clean-completion
path be the only credit.

### 99. `stream_grace_drain_s > 1 s` is silently truncated by the 1 s consumer cancel grace
**File:** `wiwi/core/gateway.py:47` (`_PUMP_CANCEL_GRACE_S = 1.0`), `501-503`, `765-769`;
`wiwi/config.py:191`
**Trigger:** `stream_grace_drain_s` configured above 1.0 + a client disconnect mid-stream.

The pump sets `grace_deadline = now + grace_drain_s` to keep reading upstream for billing
accuracy, but the consumer's `finally` only waits `_PUMP_CANCEL_GRACE_S = 1.0 s` before
cancelling the pump. Any configured drain longer than 1 s is cut off at 1 s with no error or
log — the setting has no effect for `>1`.

**Fix:** derive the consumer's cancel grace from `stream_grace_drain_s`
(`max(_PUMP_CANCEL_GRACE_S, grace_drain_s + margin)`).

### 100. A 200 with a malformed `text.format` 500s the Responses surface
**File:** `wiwi/wire/openai_responses.py:172-175`
**Trigger:** `POST /v1/responses` with `"text": {"format": "text"}` (or any non-dict `format`).

`text_field.get("format") or {}` returns the truthy string `"text"`, then `fmt.get("type")`
raises `AttributeError`. Only `DialectError`/`ValueError` are caught upstream, so a trivially
malformed body returns an unhandled HTTP 500.

**Fix:** `fmt = text_field.get("format"); if not isinstance(fmt, dict): return None`.

---

## 🟡 Medium — round 42 (new)

### 101. Deployment `rpm`/`tpm` are parsed but never enforced
**File:** `wiwi/router/router.py:339` (stored), `wiwi/config.py:122-123`; docs
`README.md:507-509`, `detailed.md:92-93`, `:726-727`
**Trigger:** a config with `wiwi_params: {..., tpm: 100000}` (shown in README and detailed.md as
a per-deployment override).

`Deployment.rpm`/`Deployment.tpm` are populated from config but never read anywhere; the only
`.rpm`/`.tpm` uses are the virtual-key limiter in `app.py`. There is no per-deployment rate
check in `pick_deployment`/`execute_with_retries`, so a documented per-deployment cap silently
does nothing.

**Fix:** enforce `dep.rpm`/`dep.tpm` at deployment selection, or reject the fields in config
validation so the no-op is not silent.

**Status: fixed** — round 62 (2026-09-16). Both caps are now enforced as 60-second sliding
windows on the `Deployment` itself, mirroring the virtual-key limiter's window semantics
(`wiwi/ratelimit/memory.py`) so "per minute" means the same interval at both layers. The
resolution followed the finding's first option (enforce, not reject), with the second applied
only to *nonsensical* values:

- `Deployment` gained `_rpm_window`/`_tpm_window` (`_DepWindow`: a deque plus an O(1) running
  total, pruned on access) and `rate_limited` / `reserve_slot` / `settle_tokens` /
  `release_slot` / `retry_after_s`. Windows are created lazily, so an uncapped deployment
  pays nothing and the routing hot path is byte-for-byte unchanged unless an operator sets a
  cap.
- `pick_deployment` filters saturated candidates out, so traffic diverts to a sibling
  deployment instead of failing. The reservation happens at the method's single exit point
  (via the new `_choose` helper) — the first cut reserved inside individual strategy branches
  and silently left `simple-shuffle` and the cross-provider pool uncapped.
- When *every* candidate is saturated, `execute_with_retries` answers **429**
  (`rate_limit_error`, carrying `retry_after` from `retry_after_s`) rather than the previous
  503 — a per-deployment cap is a rate limit, not an outage, and 503 told the client to give
  up on a deployment that is serving fine.
- Admission charges the request's *estimated* tokens (`RequestContext.est_tokens`, seeded from
  the same body-size estimate that feeds the virtual-key limiter in `run_chat_like`).
  `Gateway._price`/`_price_stream` reconcile that estimate to provider-reported usage, so one
  request is charged once against the cap, not estimate + actual. `settle_tokens` is
  idempotent: the pump can price a completed stream and then be cancelled while blocked on the
  output queue, and its cancellation handler prices the same request again — an append there
  would double-charge the window.
- `_refund_deployment_slot` returns the slot when the request never reaches pricing (no live
  key, a `WiwiError` before usage is known, cancellation, or any other abort), preventing the
  #70/#121 phantom-reservation class one layer up. Only still-*estimated* events are
  refundable, so a request that completed — or a stream that delivered tokens and then died —
  keeps its slot and stays billed.
- `DeploymentParams` now rejects `rpm <= 0` / `tpm <= 0` at config validation. A stored `0`
  read as "no requests allowed" but was treated by enforcement as falsy, i.e. "no cap" — the
  exact silent no-op this finding is about.

Verified: `tests/test_fix_round62.py` (14 tests; 13 fail against the pre-fix tree — the
fourteenth is a refund guard that passes pre-fix by construction, documented as such). Full
suite 1822 passed, `ruff check wiwi/ tests/` clean.

Docs note: `README.md:149` and `detailed.md` already advertised these fields as working
overrides; they now describe real behaviour, so no doc change was needed.

### 102. Gemini request encoder silently drops `ThinkingPart` (and Document/Audio)
**File:** `wiwi/providers/gemini_adapter.py:59-78`
**Trigger:** a request whose IR history contains a `ThinkingPart` (e.g. multi-turn
Anthropic↔Gemini replaying a prior assistant thinking turn).

The `for p in m.parts` loop handles only `TextPart`/`ImagePart`/`ToolUsePart`/`ToolResultPart`;
`ThinkingPart` (`and DocumentPart`/`AudioPart`) fall through and disappear, breaking
cross-dialect multi-turn continuity with no warning.

**Fix:** add a `ThinkingPart` branch emitting `{"text": p.text, "thought": True}`, or at minimum
`log.warning("dropping_thinking_part", ...)`.

### 103. Anthropic streaming decoder drops `redacted_thinking` blocks
**File:** `wiwi/providers/anthropic_adapter.py:558-571` (`content_block_start`), `586-592`
(`content_block_stop`); contrast non-stream `509-513`
**Trigger:** Anthropic streams a redacted-thinking block (`content_block_start` with
`type: "redacted_thinking"`) — mandatory before tool use on some extended-thinking turns.

`content_block_start` only recognizes `tool_use`/`server_tool_use`, so the block emits nothing
and is not registered; `content_block_stop` is then a no-op. The encrypted blob is lost on the
streaming path even though the non-streaming decoder preserves it, so a later replay omits the
block and Anthropic rejects the history.

**Fix:** add a `redacted_thinking` branch in `content_block_start` that emits a carrying delta
and registers the block.

### 104. Non-streaming resume/pump `_arg_bufs` accumulation is unbounded and O(n²)
**File:** `wiwi/core/gateway.py:739-742` (`_apply_delta`), `304-306`; `wiwi/streaming/resume.py:125`
**Trigger:** any upstream streaming a large tool-call argument payload (multi-MB JSON arg,
hostile/misbehaving provider).

`_arg_bufs[index] = buf + d.args_fragment` has no cap and allocates a new string of length
`len(buf)` per fragment → `O(k·L)` copy work on the event loop. `MAX_TOOL_ARGS_BYTES`
(`validation.py:22`) is consulted only at `ToolCallClose` (`gateway.py:933`), after the whole
payload is already resident, so a single call can grow memory without limit.

**Fix:** accumulate into a `list[str]` per index and `"".join` at Close; truncate/flag once
the running total exceeds `MAX_TOOL_ARGS_BYTES`.

### 105. Journal replay does blocking FS I/O on the event loop and re-reads the whole file per poll
**File:** `wiwi/streaming/tape_store.py:147-149, 200-225, 227-240`;
`wiwi/server/app.py:1099-1106, 1126-1131`
**Trigger:** any create/reconnect while stream journaling is enabled (ON by default).

`JournalStore.open` runs sync `mkdir`/`touch` under the async lock; `read_after`/`is_complete`/
`owner_of` call sync `path.read_bytes()`; the replay gate invokes them on the request path; and
the tail loop re-reads the full journal every 50 ms. Each call blocks the event loop for the
whole file (up to 1 MiB), stalling all concurrent requests.

**Fix:** route FS calls through `asyncio.to_thread` (as `append` already does) and tail
incrementally by byte offset instead of re-reading.

### 106. Resume discards the resumed attempt's usage and cost
**File:** `wiwi/core/gateway.py:561-568` (`resume_ctx`), `811` → `983-999` (`_price_stream`);
`wiwi/server/app.py:1389-1397`, `1283`
**Trigger:** `stream_resume != "off"` and a mid-stream failure that triggers `_attempt_resume`.

The resumed pump prices into the throwaway `resume_ctx`; the caller only swaps the `pump_task`
reference and never folds `resume_ctx.usage`/`cost`/`attempts` back into the originating `ctx`.
`_stream_response` bills the original `ctx`, and the resumed `UsageFinal` is recorded only into
`ctx._stream_usage`. So resumed tokens are neither charged nor reconciled to TPM, and if the
first attempt priced nothing, `update_spend` is skipped entirely.

**Fix:** merge the resume context's usage/cost/attempts into the originating `ctx` on completion.

### 107. Mid-stream resume can build an unanswered assistant `tool_use` turn → Anthropic 400
**File:** `wiwi/streaming/resume.py:183-215` (`build_continuation_messages`); consumed at
`wiwi/core/gateway.py:530-536`; encoded at `wiwi/providers/anthropic_adapter.py:295-302, 342-349`
**Trigger:** `stream_resume != "off"` and an upstream dies mid-stream after a tool call was
opened/closed.

`replay_tool_calls()` appends `ToolUsePart`s to an assistant `Message`, followed by a user
message containing only plain "Continue" text. Anthropic requires every `tool_use` to be
answered by a `tool_result` in the next user turn, so the resumed request is rejected 400 — a
recoverable mid-stream drop becomes a hard failure.

**Fix:** when tool calls are present, emit a synthetic `ToolResultPart` (`is_error=True`) for
each in the follow-up user message.

### 108. Loop detection is charged to provider/key health, cooling healthy keys
**File:** `wiwi/core/gateway.py:724-735`, `875-909` (`_note_stream_failure`)
**Trigger:** a model degenerates into a repetition loop (`LoopDetector` trips).

The loop branch calls `_note_stream_failure`, which does `dep.record_fail(...)` and
`on_result_locked(real_key, 502, ...)`, incrementing `err_count` and eventually retiring the key
(#69). A model-quality failure therefore cools the deployment and can permanently retire a
healthy key; repeated traffic to a low-quality model can take down the provider. Distinct from
#76 (a *successful* Gemini response misclassified).

**Fix:** abort with `StreamError` on loop detection without calling `_note_stream_failure`; cap
any health impact at a warning.

### 109. Alias chains beyond 8 hops silently truncate; cycles resolve arbitrarily
**File:** `wiwi/router/router.py:363-370`
**Trigger:** an alias chain longer than 8 hops or a cycle.

`resolve_group` walks `for _ in range(8)` then unconditionally returns `self.groups.get(name, [])`
for whatever intermediate name it stopped at, with no truncation/cycle detection. Requests are
silently routed to the 8th intermediate group (or an arbitrary cycle hop) rather than the
intended target, or get a misleading `not_found_error`.

**Fix:** track visited names; on a repeat or exhausted hop budget while a further alias exists,
return `(None, [])` (or a config error) instead of the intermediate group.

### 110. Loop/replay-adjacent nested alias params and non-dict SSE frames crash decoders
**File:** `wiwi/providers/openrouter_adapter.py:164-165, 253`; `wiwi/providers/openai_adapter.py:367, 385`;
`wiwi/providers/openai_responses.py:614`
**Trigger:** (a) OpenRouter `reasoning_details` containing a non-dict element; (b) an SSE frame
whose JSON is a non-dict (`null`/string/array) or whose `choices` contains a non-dict.

`rd.get(...)` on a non-dict, `chunk.get(...)`, and `choices[0].get(...)` all raise
`AttributeError`/`TypeError` outside the `except json.JSONDecodeError` handlers, terminating the
stream with an unhandled exception instead of a clean `StreamError`.

**Fix:** guard `if not isinstance(chunk, dict): return []` after parse and
`if not isinstance(choices[0], dict): return out` after selecting a choice; skip non-dict
`reasoning_details` items.

### 111. Encoders close only one open tool block on terminal frames
**File:** `wiwi/wire/anthropic_messages.py:545` (`final_frame`), `wiwi/wire/openai_responses.py:639-642`
(`_completed`)
**Trigger:** an adapter emits `Finish`/`StreamEnd` while more than one tool block remains open.

`_close_item()`/`_close_block()` close only the single currently-open block; remaining open tool
items never get their `output_item.done`/`content_block_stop`, so the client sees
truncated/never-finished tool calls.

**Fix:** iterate all still-registered tool indices (and any open text/thinking block) and close
each in `_completed`/`final_frame`.

**Status: fixed** — round 47 (2026-09-12). `final_frame` now sweeps
`sorted(self._tool_blocks)` (`anthropic_messages.py:570-571`) and `_completed` sweeps
`sorted(self._tools)` (`openai_responses.py:655-656`) after the single current-block close,
emitting one stop per remaining index. Pinned by
`tests/test_fix_round47.py::test_anthropic_encoder_closes_every_open_tool_block`
and `::test_responses_encoder_closes_every_open_tool_item`.

*Reachability (verified by execution, not by reading):* the OpenAI adapter's `[DONE]`
early return leaves every tool call open, so this is the live path — two parallel tool
calls plus `[DONE]` yielded `content_block_start ×2 / content_block_stop ×1` pre-fix.
The no-arg close runs first and clears `_open_tool`, and `_close_block(tool_index=idx)`
returns `[]` for an index already popped, so the sweep cannot double-close or raise —
confirmed against the worst case (index 0 opened *last*, so it is both the currently-open
block and a sweep target): exactly one stop per index.

> **Register hygiene:** this entry shares the number `111` with the WorkBuddy
> `User-Agent` row in the "✅ Fixed — round 44" table above. Two distinct findings
> were assigned `111` independently; both are now fixed, but the collision means
> "fix #111" is ambiguous in this file.

### 112. Live proxy log never names the provider key on a successful attempt
**Severity:** 🟡 Medium (observability gap — no way to see which round-robin key served a request)
**File:** `wiwi/core/gateway.py` — `_call_once`, `_complete_via_stream`, `_pump_once`
**Trigger:** any successful request. Watch `/admin/logs/proxy` (the proxy-log page's live tail)
while a round-robin provider serves healthy traffic.

Round 23 wired proxy events only for *failures* — upstream 5xx, fallback switches, mid-stream
deaths. Every terminal path that produced proxy output ended in an error, so a healthy
round-robin emitted nothing at all. The request log already carried `provider_key_label` plus a
per-attempt `key` (and the admin request-log UI renders `provider · key`), but the *live* proxy
stream — the surface an operator actually watches while traffic flows — stayed silent on
success. WorkBuddy looked like the only provider exposing its account because its per-key
refresh worker logs `label=` on every rotation; every other provider was invisible.

**Fix:** `_log_attempt(router, ctx, dep, key, status, latency_ms)` emits one `info` proxy line
naming `[provider/key]` at every terminal outcome (`ok`, `http_*`, transport error,
`encode_error`, `ok_after_refresh`) in all three call paths, including the force_stream
`_complete_via_stream` helper.
**Regression test:** `tests/test_fix_round42.py` (3 tests; all fail with `_log_attempt` neutered).

---

## 🔴 Critical — round 41 (new)

### 69. A transient 5xx storm permanently retires a provider key with no recovery path
**File:** `wiwi/router/router.py:186-194`, `54-59`, `41-45`; `wiwi/config.py:241`
**Trigger:** default config (`failover_mode="any_error"`, `key_max_consecutive_fails=5`,
`healer.enabled=False`). Five consecutive non-200 outcomes on the same key — e.g. a
provider-side 500/502/503 storm lasting ~25 s (the any-error cooldown defaults to 5 s,
`min(retry_after, 30.0)`), or as few as three `401`/`403` responses, which count double.

`on_result` increments `err_count` and calls `key.mark_invalid()` at the threshold. That
sets `status = "invalid"`, which `ProviderKey.available` excludes. **`recover()` only
resurrects a key whose status is `"cooling"`** — nothing time-based ever restores an
`invalid` key. The only recovery paths are the HealthHealer (off by default), an admin
`reset_status`, or a Cline/WorkBuddy token refresh.

**Consequence:** when a provider's only key is retired — `wiwi.yaml.example` declares a
single key for `anthropic-main`, `local-ollama`, `openrouter`, `gmicloud`, `bai`,
`nvidia-nim`, `opencode-zen` — `ProviderAccount.healthy` becomes permanently `False`,
`Deployment.available` becomes permanently `False`, and every request to that group
returns `503` forever. Reproduced by execution: after 5 errors `status="invalid"`; ten
subsequent `recover()` calls with the cooldown expired leave it `invalid` and unavailable.

**Fix:** make the retirement a timed cooldown, or reset `err_count`/status when
`cooldown_until` elapses; alternatively require an explicit operator or healer action to
leave `invalid`. Covered by `tests/test_fix_round41.py::test_transient_5xx_does_not_permanently_retire_key`.

### 70. Failed requests leak their estimated TPM reservation — later unrelated requests get 429
**File:** `wiwi/server/app.py:1073-1077`, `1237-1251`, `1168`; `wiwi/ratelimit/memory.py:75-141`
**Trigger:** a virtual key with `tpm` set. Send a large-prompt request whose upstream then
fails (5xx, 429, all-keys-cooling, any `WiwiError` from `execute_with_retries`).

`enforce_rate_limit` reserves an *estimated* tpm event at admission. `_record_tpm_usage` —
the only reconciler — is called on the success paths alone (`app.py:1210` non-streaming,
`app.py:1389` streaming); **no `except` branch and no early return calls it**, including the
response-cache-hit return at `app.py:1168`. `RateLimiter` exposes only `check` and
`record_tokens`: there is no release/refund API, so the estimate cannot be reclaimed.

**Consequence:** the phantom reservation survives the full 60 s window, so unrelated small
requests are rejected with `429 rate_limit_error` even though the upstream consumed zero
tokens. Reproduced by execution: after `check(key_tpm=1000, est_tokens=800)` with no
reconciliation, a subsequent `est_tokens=300` request returns `(False, 60)`. The same root
cause orphans the RPM event. This is distinct from #31/#32, which concern *which*
reservation `record_tokens` replaces — here `record_tokens` is never called at all.

**Fix:** add a `release(key_id, request_id)` to the limiter and call it (or reconcile via
`record_tokens(key_id, 0, request_id)`) on every non-success exit in `run_chat_like`.
Covered by `tests/test_fix_round41.py::test_failed_request_releases_tpm_reservation`.

---
## 🔴 Critical

### 54. Default session secret permits forged admin cookies when `master_key` is unset
**File:** `wiwi/server/app.py:283-295, 1015-1039`; `wiwi/auth/users.py:85-107`; `wiwi/config.py:174-181`
**Trigger:** the gateway is started with the default/empty `general_settings.master_key` and without `WIWI_SESSION_SECRET`.

The configuration permits an empty master key, and startup then selects the public, fixed string `wiwi-default-session-secret` as the session-signing secret. `current_user()` accepts any validly signed cookie whose user id is the literal `master` as a synthetic admin, without requiring a configured master key or checking a database row. An attacker can therefore locally compute `sign_session("wiwi-default-session-secret", "master", "admin", future_expiry)` and access admin endpoints. This was reproduced against an app with an empty master key: the forged cookie returned admin identity from `/auth/me` and successfully called `POST /admin/keys/generate` and `GET /admin/keys`.

**Fix:** fail closed at startup unless a high-entropy master/session secret is configured; never use a fixed fallback for an authorization-bearing signing key. Also reject the synthetic `master` session when no master key is configured.

### 1. Stream pump deadlocks forever if `encode_request` throws before `ready.set()`
**File:** `wiwi/core/gateway.py:284-335` (`_pump_once`)
**Trigger:** Any streaming request whose IR→provider encoding raises (unsupported tool schema, bad content type, `set_tool_context` failure).

`_pump_once` runs `adapter.encode_request` / `set_tool_context` / header building at **lines 292-296 — before the `try:` at line 302**. If any of that raises, the exception propagates out without ever calling `ready.set()`. The caller (`call_one` at line 127) does `await ready.wait()` and **blocks forever**. The `_pump` wrapper's `try/finally` only decrements `dep.inflight` — it does not catch the exception or set `ready`. The outer `except BaseException` in `stream()` never fires because `execute_with_retries` is stuck inside `call_one`, not raising.

**Result:** permanent deadlock, no timeout, leaked task + queue.
**Fix:** wrap the encode phase in `try/except` that sets `err_box[0]` and calls `ready.set()` on failure.

---

## 🟠 High — round 41 (new)

### 71. Last-admin guard is bypassed by any truthy non-boolean `disabled` — unrecoverable admin lockout
**File:** `wiwi/server/app.py:3144`, `3140`; `wiwi/auth/users.py:199-201`
**Trigger:** `PATCH /admin/users/<sole-admin-uid>` with `{"disabled": 1}` (or `"1"`, `1.0`).

The guard that conserves at least one enabled admin is armed with an **identity** check —
`if role == "user" or disabled is True:` (`app.py:3144`) — while the write path coerces
anything `int()`-able: `params["d"] = int(disabled)` (`users.py:201`). The integer `1`
skips the guard and stores `disabled=1`, disabling the only enabled DB admin. Reproduced:
`1`, `"1"`, `1.0` all skip the guard and store `1`.

**Consequence:** `count_admins()` returns 0 and `require_admin_dep` then 401s every session,
so no session can re-enable the account. Recovery requires the bearer master key; if the app
booted on `WIWI_SESSION_SECRET` alone, `is_admin()` returns `False` and there is no HTTP
recovery path at all — the row must be edited directly.

**Fix:** validate `disabled` is a real `bool` before the guard (reject otherwise with 400),
and keep the guard's semantics identical to the write path's. Covered by
`tests/test_fix_round41.py::test_last_admin_guard_cannot_be_bypassed_by_int`.

### 72. Login brute-force throttle is keyed on a caller-controlled, un-normalized `username`
**File:** `wiwi/server/app.py:3254`, `3267-3281`; `wiwi/auth/users.py:114-118`
**Trigger:** repeated `POST /auth/login` while varying the `username` field — case variants
of the target account (`alice` / `ALICE` / `Alice`), or master-key guessing with a fresh
arbitrary `username` each attempt.

The bucket is `f"{_client_ip(request)}:{body.get('username', '')}"`, but `username` is
lower-cased before lookup (`_validate_username`), so every case variant authenticates the
*same* account while landing in a *different* bucket. On the master-key branch the username
is never consulted at all, so an attacker gets a fresh 10-failure budget per arbitrary
string against a credential that grants full admin. AUDIT #55 specified a fix keyed by a
*normalized account identifier* plus source IP; the shipped key is neither normalized nor an
account identifier, so #55's fix is incomplete.

**Fix:** key the throttle on `normalized_username` (and a separate IP-only bucket for the
master-key path). Covered by `tests/test_fix_round41.py::test_login_throttle_keyed_on_normalized_username`.

### 73. Both abuse throttles are keyed on the spoofable `X-Forwarded-For` header
**File:** `wiwi/server/app.py:195-199`, `3177-3183`, `3254-3255`
**Trigger:** any request to `POST /auth/signup` or `POST /auth/login` carrying a different
`X-Forwarded-For` per request, sent directly to the gateway (the shipped Dockerfile/compose
runs uvicorn with no proxy in front).

`_client_ip` unconditionally trusts the left-most `X-Forwarded-For` entry, and both
`signup_throttle` and `login_throttle` are keyed on its return value. Rotating the header
gives every request its own bucket, so an attacker can create unlimited accounts and make
unlimited password guesses. The docstring's reasoning ("a spoofed value can at worst make an
attacker share a bucket", i.e. self-limiting) is wrong for these callers — it gives them a
fresh bucket, not a shared one. This defeats AUDIT #58's signup cap and compounds #55.

**Fix:** trust `X-Forwarded-For` only when a configured trusted-proxy list matches, as
`_request_base` already does for `X-Forwarded-Host`. Covered by
`tests/test_fix_round41.py::test_repeated_signup_with_rotating_xff_is_throttled`.

---
## 🟠 High

### 2. Parallel tool calls corrupt `output_index` and emit premature `done` events (Responses surface)
**File:** `wiwi/wire/openai_responses.py:202-325` (`ResponsesStreamEncoder`)

The encoder uses a single `_open_out` counter shared across all tools. On a second `ToolCallOpen` while another tool is open:
- `_close_item()` (line 305) **prematurely closes** the currently-open tool (emitting `output_item.done` with incomplete args).
- `_next_output_index()` advances `_open_out` to the new tool's index.
- Subsequent `ToolCallArgsDelta` for the first tool (line 324) emit with `output_index = self._open_out` → **wrong index** (points at tool 1).
- `ToolCallClose` (line 209 `_close_tool`) reads `idx = self._open_out` instead of a per-tool stored value → **closes at wrong output_index**.

The IR contract explicitly allows interleaved parallel tool calls (`Open(0)→Args(0)→Open(1)→Args(1)→Close(0)→Close(1)`); the Anthropic adapter emits them this way. The existing test `test_responses_encoder_parallel_tool_calls_preserved` only checks `call_id`/`name` containment, never `output_index` correctness — so it passes despite corruption.

**Fix:** store `output_index` per-tool in the `_tools` dict at `ToolCallOpen` time; read it back in `_close_tool`/`ToolCallArgsDelta`. Do not call `_close_item()` on a sibling `ToolCallOpen`.

### 3. Parallel tool calls misroute args / crash (Anthropic surface)
**File:** `wiwi/wire/anthropic_messages.py:243-247`

`ToolCallArgsDelta` handler computes `idx = int(self._open_block.split(":")[1])` — it uses `_open_block` (the **most recently opened** block) instead of `d.index` (the IR tool index). For interleaved parallel calls, args for tool 0 after tool 1 opened are emitted at tool 1's block index. If `_open_block` is `"text"` or `"thinking"` (no `:`), `split(":")[1]` raises **`IndexError`**, crashing the stream.

**Fix:** use `d.index` to track per-tool block indices; maintain a map from IR tool index → Anthropic block index.

### 4. Streaming path does not parse `Retry-After` header
**File:** `wiwi/core/gateway.py:319-331` (streaming pre-data error path)
**Trigger:** streaming request gets 429 (or other retryable status) with `Retry-After`.

The non-streaming path parses `Retry-After` at line 95-97 and sets `err.retry_after`. The streaming path (lines 319-331) calls `error_from_provider_status` but **never parses `Retry-After`** → `err.retry_after` stays `None`. Consequences: key-pool cooldown uses the 30s default instead of the provider value (`router.py:135`); retry sleep uses exponential backoff instead of the provider value (`router.py:489`).

**Fix:** add `ra = _parse_retry_after(resp.headers.get("retry-after"))` and `if ra is not None: err.retry_after = ra` to the streaming error path.

### 5. Resume pump uses unlocked `on_result`, racing with `pick_key`
**File:** `wiwi/core/gateway.py:255-256, 260`

`_attempt_resume` calls `dep.provider.on_result(key, 200, None)` (unlocked variant) for both success and failure. `pick_key` holds `_rr_lock`; `on_result` does not → `key.req_count += 1` races with concurrent `pick_key` reads, losing counts under concurrent resume load on the same provider.

**Fix:** use `on_result_locked` in `_attempt_resume` for both paths.

### 6. Streaming success counted at connect time, not stream completion
**File:** `wiwi/router/router.py:475`
**Trigger:** flaky upstream that accepts connections then drops them mid-stream.

`on_result_locked(key, 200, None)` fires immediately after `call_one` returns — but for streaming, `call_one` returns the pump task at **connect time** (line 134) before any content flows. A key that connects-then-fails repeatedly accumulates `req_count` (successes) that are never undone, appearing healthier than it is.

**Fix:** for streaming, defer `on_result(200)` until the pump completes successfully.

### 7. Deployment excluded from retries after key-pool exhaustion → premature 503
**File:** `wiwi/router/router.py:468-471`
**Trigger:** single-deployment group where all keys hit 429 simultaneously.

`tried_dep_ids.add(id(dep))` runs at line 468 **before** key selection. If `pick_key` returns `None` (all keys cooling), the deployment is already excluded; next iteration `pick_deployment` returns `None` → loop breaks with `WiwiError(503)` even though keys will cool off within `retry_in` seconds.

**Fix:** only add to `tried_dep_ids` when the deployment actually fails (raises `WiwiError`), not when key selection returns `None`.

### 8. Gemini ignores `reasoning_effort='none'` — thinking stays enabled
**File:** `wiwi/providers/gemini_adapter.py:90-92`
**Trigger:** client sends `reasoning_effort='none'` routed to a Gemini provider.

`effective_thinking_budget()` returns `None` for `'none'` → `if thinking_budget is not None` is `False` → `thinkingConfig` is never set → thinking stays at model default. Every other adapter (Anthropic, OpenAI, NIM, OpenRouter) explicitly disables reasoning for `'none'`. Gemini should set `thinkingConfig={"thinkingBudget": 0}`.

### 9. Anthropic cost double-subtracts cached tokens
**File:** `wiwi/cost/pricing.py:49` + `wiwi/providers/anthropic_adapter.py:213`
**Trigger:** any Anthropic response using prompt caching.

Anthropic's `input_tokens` **already excludes** `cache_read_input_tokens`. The adapter maps `input_tokens` → `prompt_tokens` (line 213) and `cache_read_input_tokens` → `cached_tokens` (line 215). Then `cost_with_status` does `uncached_prompt = max(0, prompt_tokens - cached_tokens)` — double-subtracting. When `cache_read > input_tokens`, fresh input is zeroed and billed at **$0**. For OpenAI/Gemini/NIM (where `prompt_tokens` includes cached) the formula is correct.

**Fix:** Anthropic should price `prompt_tokens` at full input rate (not minus cached) + `cached_tokens` at cache-read rate.

### 10. Anthropic cache-creation (cache-write) tokens billed at $0
**File:** `wiwi/cost/pricing.py` (no `cache_creation` field) + `wiwi/core/gateway.py:490-491, 515`

`cache_creation_tokens` (Anthropic cache-write, populated at `anthropic_adapter.py:216`) is tracked in the IR and `UsageFinal`, but **never passed to `cost_with_status`** (gateway calls it with only `prompt/completion/cached`). There is no pricing field for it (`admin_put_pricing` only accepts `cache_read_per_1m`). Anthropic charges cache writes at ~1.25× input — these tokens are billed at **$0** (undercharge).

### 11. Non-WiwiError first-delta exception leaks upstream streaming connection
**File:** `wiwi/server/app.py:497-505`
**Trigger:** a non-`WiwiError`, non-`StopAsyncIteration` exception during `await anext(stream)`.

The streaming first-delta path catches `StopAsyncIteration` and `WiwiError` (both call `stream.acclose()`), but any other exception falls to the outer `except Exception` (line 527) which does **not** close the stream. The `gateway.stream()` generator holds a `pump_task` with an open httpx connection + queue; without `aclose()`, the generator's `finally` may not run promptly.

**Fix:** add a bare `except` that calls `await stream.aclose()` before re-raising.

### 12. Unauthenticated `/metrics` endpoint exposes usage telemetry
**File:** `wiwi/server/app.py:702-706`
**Trigger:** `GET /metrics` with no `Authorization` header when `prometheus_enabled=true`.

The metrics handler has no `is_admin()` guard, unlike every `/admin/*` endpoint. Any unauthenticated client can read `wiwi_requests_total`, `wiwi_tokens_total`, `wiwi_cost_total`, per-provider counts, latency histograms, and TTFT distributions.

**Fix:** add an auth check (master key or a dedicated scrape token), or bind under `/admin/`.

---

## 🟡 Medium — round 41 (new)

### 74. OpenRouter reuses a tool-call index without flushing the deferred `ToolCallOpen`
**File:** `wiwi/providers/openrouter_adapter.py:301-308`; contrast `wiwi/providers/openai_adapter.py:398-412`
**Trigger:** an OpenRouter stream where a tool call's `ToolCallOpen` was deferred (the common
case: `id`+`name` in one chunk, args in the next) and a second chunk arrives with the *same*
`index` carrying a real `id` — which OpenRouter does on re-issued calls.

The reused-index branch emits `ToolCallClose(index=idx)` without first flushing the pending
open, while the OpenAI base class it was copied from *does* flush. Both `ChatStreamEncoder`
and `AnthropicStreamEncoder` silently drop a Close with no registered open, so the tool call
that was opened by the following args delta has its close swallowed and never terminates
correctly. The client sees a tool invocation with an empty name/id, or none at all.

**Fix:** port the `_pending_opens` flush from `OpenAIAdapter` into the OpenRouter decoder
before emitting the Close. Covered by `tests/test_fix_round41.py::test_openrouter_reused_index_flushes_deferred_open`.

### 75. NIM native-markup tool calls steal index 0 from structured `tool_calls`
**File:** `wiwi/providers/nim_native_tools.py:432-448`; `wiwi/providers/nim_adapter.py:302-322`
**Trigger:** a NIM stream that emits a structured `tool_calls` delta on index 0 (deferred
open) and then leaks a native MiniMax markup block on the same response.

`parse_tool_block` numbers native calls from zero by construction (`NativeToolCall(index=len(calls))`)
with no collision check against indices the structured path already holds open. The gateway's
`_open_tools`/`_arg_bufs` maps key by index only, so the two calls' arguments interleave into
one buffer: the client receives a single tool call whose args concatenate two different
calls, and the second call is lost.

**Fix:** allocate the native call's index from a namespace that cannot collide with the
structured path's open indices. Covered by `tests/test_fix_round41.py::test_nim_native_markup_does_not_collide_with_structured_index`.

### 76. Gemini emits no terminal delta when the final frame carries usage but no `finishReason`
**File:** `wiwi/providers/gemini_adapter.py:216-232`; `wiwi/core/gateway.py:816-825`
**Trigger:** a Gemini SSE response whose terminal candidate omits `finishReason` while
including `usageMetadata` (Gemini omits it on some SAFETY-truncated and mid-stream-cut
responses — the `elif u: pass` arm exists because this shape was observed).

The decoder returns `[]` for that frame, so no `UsageFinal`, `Finish`, or `StreamEnd` is
produced. The pump's clean-end detection then sees `finish is None and not saw_terminal` and
reports `StreamError("upstream stream ended without completion", "connection")` *and* calls
`_note_stream_failure`, which trips the deployment cooldown and the key's error streak. A
fully delivered, successful Gemini response is therefore recorded as a mid-stream failure,
cooling down a healthy deployment and penalising a healthy key — and it feeds directly into
#69's retirement ladder.

**Fix:** treat usage-bearing terminal frames as a clean completion (emit `UsageFinal` +
`Finish` + `StreamEnd`). Covered by `tests/test_fix_round41.py::test_gemini_usage_without_finish_reason_completes_cleanly`.

### 77. OpenCode responses decoder leaks `_resp_tools` state on `response.failed`
**File:** `wiwi/providers/opencode_adapter.py:330-366`; `wiwi/providers/registry.py:121-131`
**Trigger:** a Zen Responses-route stream that fails mid-stream after at least one
`response.output_item.added`, followed by any caller reusing the adapter instance —
`get_adapter("opencode")` (the documented synchronous acquisition).

The `response.failed` branch sets `_resp_ended = True` but never clears `_resp_tools`, unlike
the `response.completed`/`response.incomplete` branch immediately above it. Because
`_resp_ended` also short-circuits every later event, the stale entries cannot be drained;
`_resp_next_index` likewise survives, so tool indices are not stream-local across reuse.

**Fix:** clear `_resp_tools` and reset `_resp_next_index` in the `response.failed` branch.
Covered by `tests/test_fix_round41.py::test_opencode_failed_clears_resp_tools`.

### 78. `cycle_every_n` rotation cadence is inert — its counters are per-request
**File:** `wiwi/router/router.py:767-768`, `796-803`, `858-863`; `wiwi/core/context.py:54`; `wiwi/server/app.py:1076-1077`
**Trigger:** any deployment with `cycle_every_n > 0` (the default, 3) serving a group whose
providers have unequal weights.

`provider_consec`/`key_consec` are stored in `ctx.metadata`, which is a `RequestContext` field
constructed fresh per HTTP request; nothing seeds it from a process-wide store. The counters
are also only *incremented* immediately before `return result`, so within a single request
they can never reach `cycle_n` either. The `prefer_exclude` filter therefore always reads 0,
and the documented "after N consecutive successes, exclude this key" behaviour never fires.
Reproduced: with weights `a=10,b=1`, `cycle_every_n=1` yields the identical pick sequence to
`cycle_every_n=0`. The existing test passes only because it asserts the weak property "no key
appears 4× in a row", which equal-weight smooth-WRR satisfies anyway.

**Fix:** move the counters to router-level state (keyed by provider/key) and increment on the
success path in `execute_with_retries`. Covered by
`tests/test_fix_round41.py::test_cycle_every_n_rotates_under_skewed_weights`.

### 79. A streaming-only workload can never graduate a deployment out of probation
**File:** `wiwi/router/router.py:847-856`, `385-387`; `wiwi/core/gateway.py:430`
**Trigger:** `healer.enabled: true`, so the HealthHealer restores a previously-cooled
deployment via `Deployment.mark_recovered()` (`probation=True`), and that deployment then
receives only **streaming** requests.

Deployment graduation is written in exactly one place — `dep.probation = False` at
`router.py:852` — and that write sits inside `if not getattr(ctx, "_defer_key_credit", False)`.
The streaming path unconditionally sets `ctx._defer_key_credit = True` (`gateway.py:430`) and
the pump that credits the key on clean completion never graduates the deployment, so
`pick_deployment`'s `fresh` filter demotes it relative to its siblings indefinitely. The
comment at `router.py:239-240` ("execute_with_retries graduates on success") does not hold for
streams.

**Fix:** graduate the deployment in the pump's clean-completion path alongside the key credit.
Covered by `tests/test_fix_round41.py::test_streaming_success_graduates_probation_deployment`.

### 80. Non-string `username`/`password` on unauthenticated auth endpoints raise 500
**File:** `wiwi/server/app.py:3191-3192`, `3284-3288`; `wiwi/auth/users.py:114-118`, `135-136`, `73`
**Trigger:** `POST /auth/signup` with `{"username": 1, "password": "x"}` or a non-string
password; `POST /auth/login` with `{"username": true, "password": "x"}`.

`json_body` only guarantees a JSON object, so a nested scalar reaches `_validate_username`,
where `(username or "").strip()` raises `AttributeError`; `create_user`/`hash_password` raise
`TypeError`/`AttributeError` on non-string passwords. The handlers catch only `ValueError`, so
these escape as 500 with a server traceback instead of the 400/401 the endpoint's contract
promises. Same class as #53/#59/#62, on routes those rounds did not cover.

**Fix:** validate `username`/`password` are strings at the handler boundary and return 400.
Covered by `tests/test_fix_round41.py::test_signup_non_string_username_returns_400`.

### 81. `PATCH /admin/users/{uid}` with a non-numeric `disabled` raises 500
**File:** `wiwi/server/app.py:3140`, `3152-3155`; `wiwi/auth/users.py:199-201`
**Trigger:** `PATCH /admin/users/<uid>` with `{"disabled": []}` or `{"disabled": {}}`.

`UserService.patch` calls `int(disabled)` with no type check; a list/dict raises `TypeError`,
which the handler does not catch (it catches only `ValueError`). Contract-bearing difference:
`"false"` → 400, `[]` → 500, for the same malformed field.

**Fix:** coerce/validate `disabled` to `bool` before `patch`, returning 400 on a non-bool.
Covered by `tests/test_fix_round41.py::test_patch_user_non_numeric_disabled_returns_400`.

### 82. `{"enabled": "false"}` silently leaves a provider key enabled
**File:** `wiwi/server/app.py:1799-1801`, `2011-2013`; contrast `2240-2242`
**Trigger:** `PATCH /admin/providers/{name}/keys/{label}` with `{"enabled": "false"}`, or
`PATCH /admin/providers/{name}` with `{"round_robin": "false"}`.

`bool("false")` is `True`, so the key the admin asked to disable is persisted as enabled and
keeps serving traffic — and the response echoes `enabled: true`, so the UI shows the opposite
of the request. The backup-import path for the same field *does* validate (`if "enabled" in
rk and not isinstance(rk["enabled"], bool): return 400`), so the two writers to the same
column disagree about what a legal value is.

**Fix:** require a real `bool` on the PATCH paths, matching the import validator. Covered by
`tests/test_fix_round41.py::test_provider_key_enabled_string_false_rejected`.

### 83. GenParams numeric fields are forwarded upstream with no type validation
**File:** `wiwi/wire/openai_responses.py:283-292`, `wiwi/wire/openai_chat.py:176-195`; `wiwi/providers/openai_adapter.py:161-166`; contrast `wiwi/wire/anthropic_messages.py:294-305`
**Trigger:** `/v1/responses` with `max_output_tokens: {"a": 1}`, or `/v1/chat/completions`
with `max_tokens: {"a": 1}`.

The value is stored unvalidated and passed straight into the upstream body, so the upstream
returns a 400 that the gateway reports as an upstream `invalid_request_error` instead of a
dialect-correct local 400. The Anthropic codec defends this class explicitly
(`max_tokens`/`thinking.budget_tokens` are coerced and non-numeric values dropped), so the
same malformed value is rejected on `/v1/messages` and forwarded on the other two surfaces.
Reproduced: responses stores `{"a": 1}`; chat forwards `max_tokens: {'a': 1}` into the
upstream body.

**Fix:** apply the Anthropic codec's numeric coercion in the Chat and Responses decoders.
Covered by `tests/test_fix_round41.py::test_responses_non_numeric_max_output_tokens_rejected`.

---
## 🟡 Medium

### 13. `_inject_id` stamps a single id across multi-frame SSE chunks, breaking Last-Event-ID resumption
**File:** `wiwi/server/app.py:331-335` + `wiwi/wire/openai_responses.py:265-285` + `wiwi/wire/anthropic_messages.py:220-233`

`_inject_id` prepends one `id: <seq>` line to a chunk that may contain **multiple** SSE frames (Responses encoder joins 2-3 frames per `feed()`; Anthropic joins `content_block_start`+`content_block_delta`). Per the SSE spec, an `id` line sets `last-event-id` for the *next* event dispatched — so the client's `Last-Event-ID` after the chunk points at the first sub-event, not the last. On reconnect the client replays from the wrong offset (re-emitting or skipping events).

**Trigger:** `stream_event_ids=True` with Responses or Anthropic surface, on any delta opening a new content block.
**Fix:** inject the id after each frame's terminating blank line (split on `\n\n`, tag each frame).

### 14. Partial-JSON repair produces invalid JSON on truncated `\uXXXX` escapes
**File:** `wiwi/streaming/partial_json.py:46-66`

`_repair_truncated_json` handles a dangling backslash but not an unterminated `\uXXXX` escape. A fragment split as `{"k":"v\u00` then `41"}` produces `{"k":"v\u00"}` — invalid (`\u00"` needs 4 hex digits). `json.loads` rejects it → `parse_partial` falls back to `({}, False)` — **silent total loss** of tool args.

**Fix:** strip trailing incomplete `\uXXXX` (backslash + 0-3 hex chars) before appending the closing quote.

### 15. Grace-drain queue deadlock when queue fills
**File:** `wiwi/core/gateway.py:415-419`
**Trigger:** client disconnects during a high-volume stream with `stream_grace_drain_s > 0`.

In grace-drain mode the consumer (`stream()`) is cancelled, so nobody calls `queue.get()`. The pump keeps `await queue.put(d)`; once the 4096-slot queue fills, `put` **blocks forever**. The `grace_deadline` check runs only at the top of each iteration (after reading a line), so a stuck `put` never reaches it.

**Fix:** use `put_nowait` with `except QueueFull`, or check `ctx.cancel` before `put`.

### 16. `asyncio.shield(_close_upstream())` can hang if close blocks
**File:** `wiwi/core/gateway.py:445-446`
**Trigger:** client disconnect + wedged upstream transport simultaneously.

`await asyncio.shield(_close_upstream())` inside the `CancelledError` handler blocks forever if `resp_cm.__aexit__` hangs. The original cancellation is never propagated.

**Fix:** wrap in `asyncio.wait_for` with a short timeout.

### 17. 429 causes soft-retry loop burning attempts on the same dead deployment
**File:** `wiwi/router/router.py:483-491`
**Trigger:** single deployment, all keys rate-limited.

On 429, the key cools but `record_fail` is NOT called on the deployment (429 ∉ {408,500,502,503,504,529}). The deployment stays "available" → `pick_deployment` keeps selecting it → `pick_key` keeps returning `None` → retry loop wastes the entire budget sleeping/retrying the same dead deployment, then 503.

**Fix:** `record_fail` on the deployment when all keys are exhausted, or break early when `key is None` for the only available deployment.

### 18. Loop detection misses oscillating loops (A-B-A-B) — **superseded by the O(1) LoopDetector**
**File:** `wiwi/core/gateway.py:406-411` (original); now `wiwi/streaming/loopdetect.py`

**Status: fixed** (register entry was stale) — the consecutive-repetition scan was
replaced by the O(1) `LoopDetector`, which tracks repetition runs per candidate
period 1–8 and **does** catch oscillating loops. Re-verified 2026-09-09 by direct
execution: an A-B-A-B loop trips at token 9 (limit 10), A-B-C at token 11
(limit 12). Only periods > 8 remain undetected — the documented, deliberate
`MAX_LOOP_PERIOD` cap. The original fix sketch ("track a small window of recent
chunks") is what shipped.

### 19. Chronic slow-fail deployments never cooldown (60s window < failure interval)
**File:** `wiwi/router/router.py:158-164`
**Trigger:** a deployment failing once every >60s forever.

`record_fail` prunes to the last 60s. If failures are spaced >60s apart, `len(recent)` is always 1 and cooldown (`>= allowed_fails`) never triggers.

**Fix:** the 60s window should be ≥ 2×`cooldown_time`, or use a decaying count.

### 20. `validate_tool_args` accepts `bool` for `'number'` schema type
**File:** `wiwi/streaming/validation.py:88-94`

`isinstance(True, (int, float))` is `True` because `bool` subclasses `int`. The bool-as-int guard only fires for `expected == 'integer'`. So a number-typed parameter receiving `true` validates as OK.

**Fix:** add `if expected == 'number' and isinstance(value, bool): return False`.

### 21. OpenAI adapter streaming drops `reasoning` field (only checks `reasoning_content`)
**File:** `wiwi/providers/openai_adapter.py:280`

`decode_stream_event` checks only `delta.get('reasoning_content')`, but `decode_response` checks both `reasoning_content` and `reasoning`. An OpenAI-compatible provider streaming via `delta.reasoning` silently drops `ThinkingDelta`s (NIM and OpenRouter handle both field names).

**Fix:** check both `reasoning_content` and `reasoning` in streaming.

### 22. NIM nested aliased param names never un-aliased in tool responses
**File:** `wiwi/providers/nim_tool_schema.py`

`_alias_in_node` recursively aliases unsafe param names (e.g. `type` → `_nim_arg_type`) at all nesting levels, but `collect_nim_tool_aliases` only scans **top-level** properties and `unalias_nim_tool_args` only un-aliases top-level keys. A nested `type` is aliased in the schema sent to NIM, but the model's returned `_nim_arg_type` in a nested object is never un-aliased → client receives `_nim_arg_type`.

**Fix:** recurse in `collect_nim_tool_aliases` and `unalias_nim_tool_args`.

### 23. `error_from_provider_status` 'tokens' substring heuristic misclassifies 400s
**File:** `wiwi/providers/base.py:86-88`

`status == 400 and 'tokens' in msg.lower()` triggers `context_window_fallback` for errors like "max_tokens must be a positive integer" or "reasoning_tokens is not supported" — wasting a fallback slot and routing to a different model unexpectedly.

**Fix:** narrow the heuristic; drop `'tokens'` from the 400 classification, or require stronger context-window markers.

### 24. Spend accounting inconsistency: non-streaming does NOT suppress `update_spend` failures
**File:** `wiwi/server/app.py:517-519` vs `631-633`

Non-streaming: `await state_.auth.update_spend(...)` is **not** wrapped in `contextlib.suppress` — a transient DB error propagates to the outer `except Exception`, overwrites `ctx.status=500`, and returns a 500 to the client **even though the LLM response was already generated successfully**. The streaming path correctly suppresses it.

**Fix:** wrap non-streaming `update_spend` in `contextlib.suppress(Exception)`.

### 25. Task leak in admin SSE keepalive loop
**File:** `wiwi/server/app.py:823-841`

Each iteration creates `get_task` and `shutdown_task`; `asyncio.wait` returns `(done, _pending)` and **`_pending` is never cancelled**. Every 15s idle period leaks one orphaned task. The `finally` only cancels `fwd_tasks`.

**Fix:** `for t in _pending: t.cancel()` after each `asyncio.wait`.

### 26. `base_url` persistence corrupts on non-string input
**File:** `wiwi/server/app.py:1094-1098` (also `1011`)

`str(_interpolate(body['base_url']))` on a dict/list input yields a truthy `str(dict)` like `"{'nested': True}"` that passes the non-empty check and is stored as `acct.base_url`, breaking all upstream requests.

**Fix:** validate that the resolved `base_url` is a `str` and URL-shaped before storing.

### 27. Models.tsx: `setStrategy` mutation throws `ReferenceError` (TDZ)
**File:** `web/src/pages/Models.tsx:142-155`

`setStrategy` is a `useMutation` whose `mutationFn` references `data` (lines 144, 146), but `data` is declared at line 155 with `const` — **after** the mutation. `const` is in the temporal dead zone; when `setStrategy.mutate(s)` runs (strategy dropdown change), the closure throws `ReferenceError: Cannot access 'data' before initialization`. The strategy dropdown is **completely broken**.

**Fix:** move `const data = query.data!` above the `useMutation`, or read `query.data` inside `mutationFn`.

### 28. Settings.tsx InfoTile uses `<a href>` — broken routing under production base path
**File:** `web/src/pages/Settings.tsx:428-443`

InfoTile renders `<a href={props.to}>` with absolute paths (`/models`, `/providers`). In production the SPA is served at `/admin/ui/` with `BrowserRouter basename=BASE_URL`. A plain `<a>` does a **full browser navigation** to `/models`, which FastAPI doesn't serve → 404. Every other nav element uses `<Link to>`.

**Fix:** use `<Link to={props.to}>`.

### 29. Analytics.tsx: over-broad suffix match shows wrong pricing card
**File:** `web/src/pages/Analytics.tsx:731-743`

`selectedModel.endsWith(p.model_id)` is the buggy clause: selecting `gpt-4o` matches a pricing row named `o`; selecting `claude-3-5-sonnet` matches `sonnet` or even `t`. The wrong model's prices and effective $/1M are displayed.

**Fix:** drop the `selectedModel.endsWith(p.model_id)` clause; the `p.model_id.endsWith(selectedModel)` clause already covers the provider-prefix case.

### 30. OpenRouter streaming drops `reasoning.encrypted` details — **fixed**
**File:** `wiwi/providers/openrouter_adapter.py:213-221` (original lines)

Streaming `decode_stream_event` handled `reasoning.text` and `reasoning.summary` but dropped `reasoning.encrypted` (non-streaming `decode_response` handled all three).

**Status: fixed** (register entry was stale) — re-verified 2026-09-09: both the
non-streaming (line 166) and streaming (line 269) decode paths now have an explicit
`elif rtype == "reasoning.encrypted"` branch. The exact fix sketch from the original
entry is what shipped.

---

## ⚪ Low — round 41 (new)

### 84. StreamTape `head_evicted` is not membership-exact (`replay(last_seq)` silent partial)
**File:** `wiwi/streaming/resume.py:72-88`, `68-70`; `wiwi/core/gateway.py:527-529`
**Trigger:** `last_seq` below the tape's first surviving seq — exactly the case the head
eviction creates. With a 60-byte tape retaining seqs 15..20, `head_evicted(0)` returns
`True`, but `head_evicted(14)` returns **`False`** even though seqs 1..14 are gone.

`head_evicted` tests `first > last_seq + 1`, which is only a valid contiguity check when
`last_seq` is adjacent to the survivor set. `_attempt_resume` passes `tape.seq - 1`, which
happens to be adjacent today, so the guard is correct for the current caller but is not the
membership test its contract claims ("True when eviction removed entries the continuation
needs"). Any future caller passing a smaller `last_seq` — the natural reading of the API —
gets a silently partial continuation prefix.

**Fix:** test membership directly (`all(s in survivor_seqs for s in range(last_seq + 1, first))`
or compare `last_seq + 1 < first` against the surviving set) rather than the arithmetic
shortcut. Covered by `tests/test_fix_round41.py::test_head_evicted_is_membership_exact`.

### 85. Provider key `secret` from a non-string body value is silently `str()`-ified and persisted
**File:** `wiwi/server/app.py:1835`, `1893`, `2229-2230`
**Trigger:** `POST /admin/providers/{name}/keys` (or `POST /admin/providers`, or an import
entry) with `{"label": "a", "key": {"nested": true}}`.

`str({'nested': True})` is the truthy string `"{'nested': True}"`, which passes the non-empty
check and is stored as the upstream credential in memory and in `provider_keys.secret`. Every
request through that provider then fails upstream authentication with a misleading
provider-side 401, and the corrupt value survives restarts. The neighbouring `base_url` field
rejects the same shape (`app.py:1885-1890`, `1982-1986`) — the guard was never extended to
`key`. Same class as #26.

**Fix:** require `key` to be a string (or a provider-secret-bearing mapping with a string
leaf) before persisting. Covered by `tests/test_fix_round41.py::test_provider_key_non_string_secret_rejected`.

### 86. `read_timeseries` with `key_ids=[]` returns `[]` buckets while the zero-row path returns a full grid
**File:** `wiwi/logging_core/db_sink.py:561-564`, `620-631`; `wiwi/server/app.py:2935-2948`
**Trigger:** `GET /admin/stats/timeseries?minutes=60&metric=tokens` as a non-admin with no
virtual keys, or an admin after their last key is deleted, with a DB sink configured.

The early return is documented as "the same dict this method returns when no rows match", but
that is false whenever `minutes > 0`: the no-rows path zero-fills `n_buckets` buckets
(`n_fill = n_buckets`), so the same user sees the array go from `[]` to 60 entries on minting
their first key. A client reading `buckets.length` or indexing the last bucket renders
differently for the two "no traffic" cases. `read_overview` does not have this mismatch.

**Fix:** zero-fill the early return identically to the no-rows path. Covered by
`tests/test_fix_round41.py::test_timeseries_empty_key_ids_matches_no_rows_shape`.

### 87. WorkBuddy business envelopes downgrade transient upstream failures to non-retryable
**File:** `wiwi/providers/workbuddy_adapter.py:254-276`
**Trigger:** the WorkBuddy upstream returns an HTTP-200 SSE chunk carrying a `{code, msg}`
envelope whose code is not 12153 and not a credit-exhaustion marker (e.g. 11102).

`_envelope_error` returns `WiwiError(502, "api_error", retryable=False)`. The retry loop only
retries retryable errors and `status_for_key_pool` maps this to `None`, so the request fails
the client immediately instead of failing over to the next deployment/key — the opposite of
what the 12153 branch was written to enable.

**Fix:** classify unknown envelope codes as retryable (or map them through
`status_for_key_pool`). Covered by `tests/test_fix_round41.py::test_workbuddy_unknown_envelope_is_retryable`.

### 88. NIM structured path lacks the synthesized-Open adoption logic the base class has
**File:** `wiwi/providers/nim_adapter.py:277-285`; contrast `wiwi/providers/openai_adapter.py:398-412`
**Trigger:** a NIM model that sends an `arguments` fragment on the first tool chunk with no
`id`, then supplies the real `id` on a later chunk for the same index.

`NimAdapter` overrides `decode_stream_event` entirely and never populates `_synthesized_opens`,
so its `if tc.get("id")` branch takes the generic reused-index path and the args streamed under
`name=""` are never reconciled with the real id. The client receives a tool call with an empty
id, so its tool result cannot be correlated back (`tool_call_id` is empty).

**Fix:** port the synthesized-open adoption half from `OpenAIAdapter`. Covered by
`tests/test_fix_round41.py::test_nim_adopts_synthesized_open_id`.

---

## ⚪ Low

### 31. Redis TPM limiter uses `zcard` (count) instead of token sum — dormant (Redis not wired)
**File:** `wiwi/ratelimit/redis.py:108-131` (original lines)

`zcard` returns member count, not sum of token values. A TPM limit of 10000 is enforced as "10000 requests". `record_tokens` never updates Redis sorted sets (only memory fallback). `zadd` member collisions silently lose reservations. *Currently dormant* — `app.py:170` always uses the memory limiter; `redis_url` is read but unused.

**Status: fixed** (register entry was stale) — re-verified 2026-09-12 against round 48.
The counting half is already fixed: the limiter encodes each reservation's cost in the
sorted-set member string (`"{ts}:{cost}:{uid}"`) and sums it rather than counting members
— `redis.py:127` reads `total = sum(int(m.split(":")[1]) for m in live)` over
`zrange(scope, 0, -1)`, and the uid suffix means `zadd` collisions no longer lose
reservations. Pinned by
`tests/test_fix_round17.py::test_redis_tpm_enforces_token_sum_not_request_count`.

The *dormant* half still holds and is the reason no code changed here:
`wiwi/server/app.py` constructs the memory `RateLimiter`, so `RedisRateLimiter` has no
production caller. Two real gaps remain if it is ever wired — it has **no `release()`**
(the memory limiter's refund path added by #70/#121 has no Redis counterpart, so a failed
request would leak its reservation) and no `record_tokens()` reconciliation. Left as-is
deliberately: writing an untested Redis path against a dormant backend trades a
documented gap for an unverified one. Whoever wires Redis must port both.

### 32. Memory `record_tokens` misattributes actual usage under concurrent same-key requests
**File:** `wiwi/ratelimit/memory.py:90-108`

Replaces the **newest** estimated reservation regardless of which request completed. Out-of-order completion → stale estimate remains, actual count replaces the wrong reservation. No request-id correlation.

### 33. `estimate_tokens` runs blocking tiktoken inside async stream-pump coroutines
**File:** `wiwi/cost/pricing.py:82-93` (called from `gateway.py:425, 481`)

`import tiktoken` + `get_encoding` (disk load) + `encode` (CPU-bound) are all synchronous, blocking the event loop. First-request latency spike + large-prompt stalls.

**Status: fixed** — re-verified 2026-09-18. `estimate_tokens_async`
(`wiwi/cost/pricing.py:192-203`) wraps the call in `asyncio.to_thread`, and it is
the form actually used on the hot path: `wiwi/core/gateway.py:615, 1247, 1412,
1554`. The register had simply not been updated when the code was. Note this is
a *different* defect from #214: #33 was the call blocking the loop, #214 was the
dependency being undeclared so the accurate path silently vanished.

**Fix:** `asyncio.to_thread` or pre-import + cache encodings at startup.

### 34. `count_tokens` / `list_models` over-reserve RPM rate-limit slots
**File:** `wiwi/server/app.py:668, 684`

Both call `authenticate(...)` with default `reserve=True`, adding an RPM event for endpoints that never make an upstream call. A tight loop on `/v1/models` can exhaust a key's RPM quota and block real completions.

**Fix:** pass `reserve=False` for read-only / non-upstream endpoints.

### 35. Percentile math differs between `stats._p95` and `metrics._percentile`
**File:** `wiwi/server/stats.py:42-44` vs `wiwi/server/metrics.py:17-21`

`stats` uses nearest-rank (`ceil`); `metrics` uses truncation (`int`/`floor`). For `n=20, p=95`: stats → index 18, metrics → index 19. `/admin/stats/overview` and `/metrics` report different p95 for the same data.

### 36. Latency `p95_ms` computed over different populations (in-memory vs DB)
**File:** `wiwi/server/stats.py:73` vs `wiwi/logging_core/db_sink.py:285-289`

In-memory overview includes `latency_ms==0` entries; DB-backed overview excludes them (`WHERE latency_ms > 0`). Same data → different p95 depending on which path answers.

### 37. `replay()` reads deque without the lock
**File:** `wiwi/logging_core/subsystem.py:66-70`

`SSEBroadcastSink.replay()` iterates `self._rings[stream]` without `self._lock` while `publish()` mutates the same deque under the lock. Concurrent publish can raise `RuntimeError: deque mutated during iteration`.

### 38. Audit events have no SSE ring — silently dropped when DB is down
**File:** `wiwi/logging_core/subsystem.py:124-128`

`log_audit` writes directly to DB with no in-memory copy. If `db_sink` is `None`, audit events are silently dropped with no error.

### 39. Metrics provider label not escaped in Prometheus exposition
**File:** `wiwi/server/metrics.py:65-67`

Provider names containing `"` or `\` produce malformed Prometheus text. Provider creation validates non-empty but not for quote/backslash characters.

### 40. Budget projection wildly inflates with < 1 hour of history
**File:** `web/src/pages/BudgetsAlerts.tsx:58-87`

`windowDays` floor is 1/24 (1 hour) → scale factor up to 720×. 5 minutes of traffic extrapolated to a month is absurd.

### 41. WRR starvation after key recovery from cooldown
**File:** `wiwi/router/router.py:96-102`

A recovered key's `current_weight` was frozen (possibly negative); it's starved for several rounds until it catches up.

### 42. Latency-based routing pins all traffic to the first cold deployment
**File:** `wiwi/router/router.py:190-191`

`p95_latency()` returns `0.0` for all cold deployments; `min` ties deterministically return the **first** element → it gets all traffic, starving others.

### 43. ProviderDetail stale-name race after rename
**File:** `web/src/pages/ProviderDetail.tsx:~525-570`

`KeyPoolCard` captures `props.p.name` from the initial render; a key mutation immediately after rename PATCHes against the old name → 404. Small race window (invalidate triggers immediate refetch).

### 44. Settings "copied" state reused for cache-clear → misleading button label
**File:** `web/src/pages/Settings.tsx:268-272`

`clearCache()` sets the same `copied` boolean used by the "Clear key" button → "Clear cache" flips the "Clear key" button label to "Cleared".

### 45. Dashboard live-bucket one-shot seed never re-seeds after SSE disconnect
**File:** `web/src/pages/Dashboard.tsx:240-245`

If SSE disconnects >30 min, the seed effect won't re-run (`liveSeededRef` stays true); `liveRef.current` holds stale buckets → flat-zero sparkline until a brand-new SSE event.

### 46. AdminStreamProvider token captured at mount, not reactive to auth changes
**File:** `web/src/components/Layout.tsx` (via `stream.tsx`)

Stream effect captures `getToken()` at mount with `[]` deps. Mostly mitigated by `RequireAuth` unmounting on logout, but fragile if gating is restructured.

### 47. VirtualKeys edit dialog cannot clear budget/rpm/tpm back to unlimited
**File:** `web/src/pages/VirtualKeys.tsx:405-465`

No "Clear budget" checkbox (unlike the expiry clear checkbox). Once a budget is set, it can only be removed via the Budgets page.

### 48. Responses decoder: `json_object` format not handled + system/developer role misrouted
**File:** `wiwi/wire/openai_responses.py:128-135, 73`

`text.format.type=='json_object'` is not decoded (only `json_schema`) → upstream never instructed to produce JSON. System/developer role messages map to `'user'` instead of `'system'`.

### 49. OpenAI Chat decoder: ThinkingPart placed after TextPart, breaking Anthropic extended-thinking
**File:** `wiwi/wire/openai_chat.py:42-52`

Order is `[TextPart, ThinkingPart, ToolUsePart]`; Anthropic extended-thinking requires the final assistant turn to **begin** with a thinking block → Anthropic rejects the request.

### 50. `expires_at` contract inconsistency (POST `ttl_seconds` vs PATCH absolute epoch)
**File:** `wiwi/auth/service.py:119, 171`

`create_key` takes `ttl_seconds` (relative); `update_key` takes `expires_at` (absolute epoch). Admin extending via PATCH must send an absolute epoch, not a TTL — a footgun.

### 51. ChatStreamEncoder emits empty `reasoning_content` for signature-only deltas
**File:** `wiwi/wire/openai_chat.py:226`

`ThinkingDelta` with empty text (signature-only) emits `{reasoning_content: ''}` — a useless empty-string delta. `ResponsesStreamEncoder` correctly returns `None`.

### 52. Hard budget caps can be bypassed when a request exceeds the remaining budget
**File:** `wiwi/server/app.py:640-643, 756-759`; `wiwi/auth/service.py:237-265`
**Trigger:** a priced request's actual cost is greater than the key's remaining `max_budget`.

The request is sent upstream and its response is returned before spend accounting runs. `AuthService.update_spend()` uses a conditional update that returns `False` when adding the actual cost would exceed `max_budget`, leaving `spend_to_date` unchanged. Both response paths ignore that return value while suppressing exceptions. A request costing `$1.20` on a `$1.00` key therefore returns `200`, logs cost `1.2`, and leaves spend at `$0.00`; repeated requests continue to pass the pre-check indefinitely. This was reproduced end-to-end with a registered test price.

**Fix:** reserve budget before dispatch using a conservative token/cost estimate and reconcile afterward, or record the actual charge even when it crosses the cap and block subsequent requests. Do not discard the `False` result from authoritative accounting.

### 53. Non-object JSON bodies cause HTTP 500 instead of a dialect-correct 400
**File:** `wiwi/server/app.py:533-539, 576-582, 762-784`; `wiwi/wire/openai_chat.py:21-27`, `wiwi/wire/openai_responses.py:22-26`, `wiwi/wire/anthropic_messages.py:17-29`
**Trigger:** a caller sends valid JSON whose top-level value is an array, string, number, boolean, or `null` to `/v1/chat/completions`, `/v1/responses`, or `/v1/messages`.

`json_body()` accepts any JSON value and returns it as `Any`, while the three handlers pass it to decoders that immediately call `.get()`. The decoder exception is an `AttributeError`, which is not caught by `run_chat_like()`'s `DialectError`/`ValueError` handler, so the request reaches FastAPI's 500 path. The same behavior was reproduced with `[]` on all three public surfaces.

**Fix:** validate that the parsed body is a mapping before decoding and return the caller's dialect-specific `invalid_request_error` with status 400. Apply the same boundary validation to other JSON endpoints that assume `body.get()`.

### 55. Login endpoint has no brute-force protection
**File:** `wiwi/server/app.py:2041-2074`; `wiwi/auth/users.py:62-73`
**Trigger:** an attacker can reach `/auth/login` repeatedly from the network.

`/auth/login` performs an expensive PBKDF2 password verification for every username/password attempt, but it does not call the gateway's rate limiter and does not maintain per-account, per-source, or global failed-login state. The master-key branch is also checked directly with no attempt throttling. A probe of 20 consecutive incorrect password requests returned `401` for every attempt, with no `429`, delay, lockout, or other backoff. This permits online password guessing and enables CPU exhaustion against the PBKDF2 verifier; if the master key is accepted through this endpoint, it can be guessed without throttling as well.

**Fix:** add a dedicated authentication-attempt limiter keyed by a normalized account identifier and source/IP, with bounded exponential backoff and a global circuit breaker. Apply it before password verification and to master-key attempts, while using a dummy password hash for unknown users to reduce username-enumeration timing differences. Do not reuse the model TPM/RPM limiter without separating authentication scopes.

### 56. Untrusted forwarded headers can redirect Cline OAuth callbacks to an attacker host
**File:** `wiwi/server/app.py:2240-2245, 2294-2300`
**Trigger:** the gateway is reachable directly or through a proxy that does not strip untrusted `X-Forwarded-Host`/`X-Forwarded-Proto` headers, and an admin starts Cline auto-connect.

`_request_base()` trusts client-supplied forwarded headers when constructing the callback URL sent to Cline. A request with `X-Forwarded-Proto: https` and `X-Forwarded-Host: attacker.example` produced both `callback_url` and `redirect_uri` pointing to `https://attacker.example/cline/oauth/callback?...`. After the admin completes authentication at Cline, the embedded authorization code is delivered to that attacker-controlled origin, where it can be decoded into the access token and refresh token. The pending state token does not protect the code from being observed at the poisoned callback host.

**Fix:** derive the callback origin from a configured public/base URL, or only honor forwarded headers after they have been validated by a trusted proxy middleware. Validate the host against an allowlist and reject unexpected schemes/ports; never construct credential-bearing OAuth redirect URIs from arbitrary request headers.

### 57. Users can mint unlimited unbounded virtual keys to bypass per-key limits
**File:** `wiwi/server/app.py:2029-2039, 2102-2116`; `wiwi/auth/service.py:127-149`
**Trigger:** any authenticated non-admin user can call `/auth/playground-key` repeatedly, or create additional keys through `/admin/keys/generate`.

The playground-key path calls `create_key()` with only `alias` and `owner_id`; it supplies no `max_budget`, `rpm`, `tpm`, `models`, or expiry. There is no per-user key-count/quota limit, and the endpoint has no issuance rate limit. A single session minted five distinct keys successfully; all five were stored with `max_budget=None`, `rpm=None`, and `tpm=None`, and each independently authenticated against `/v1/models`. Since the request limiter and spend cap are keyed by virtual-key ID, a user can rotate across unlimited unbounded keys to avoid any per-key RPM/TPM or budget policy and multiply billable upstream traffic.

**Fix:** enforce account-level quotas and aggregate spend/rate limits across all keys owned by a user, or make playground keys short-lived and subject to a configured shared budget and rate limit. Apply issuance throttling and a maximum active-key count; do not treat per-key controls as an account-wide quota when the account can create unlimited keys.

### 58. Public signup has no registration throttling or account quota
**File:** `wiwi/server/app.py:1996-2027`; `wiwi/auth/users.py:132-148`
**Trigger:** an unauthenticated caller can reach `POST /auth/signup`.

The endpoint has no IP/source limiter, global registration budget, email/verification requirement, or account quota. Every accepted registration performs a 200,000-iteration PBKDF2 hash, writes a user row, and attempts to mint a playground virtual key. A probe of eight sequential unique registrations returned `201` for all eight; a 1,000,000-character password was also accepted and hashed. An attacker can therefore create unbounded accounts and database rows, consume CPU with signup hashing, and obtain fresh account-owned API keys without first authenticating. This is distinct from #55, which covers password-verification abuse against existing accounts, and #57, which covers key rotation after authentication.

**Fix:** add registration throttling keyed by source/IP and, where appropriate, a global circuit breaker; cap registrations per time window and total active accounts. Require an operator-selected verification or invitation policy for public deployments, cap password length before hashing, and avoid minting an unlimited unbounded playground key until registration abuse controls pass.

### 59. Negative virtual-key rate limits crash completion requests with HTTP 500
**File:** `wiwi/server/app.py:845-857, 1751-1754`; `wiwi/auth/service.py:127-149`; `wiwi/ratelimit/memory.py:70-81`
**Trigger:** an authenticated user or admin creates a key with `rpm: -1` or `tpm: -1`, then sends a real completion request with that key.

The key-generation and patch handlers pass numeric limit values through without requiring positive integers. The memory limiter treats a negative value as an active limit because it is truthy; the first request has `w.count() + cost > limit`, then the rejection path reads `w.events[0]` even though the window is empty. This raises `IndexError` and returns an internal server error before the upstream provider is called. The behavior was reproduced for both negative `rpm` and negative `tpm`; `/v1/models` does not expose it because that endpoint intentionally skips rate-limit reservation.

**Fix:** reject non-positive or otherwise invalid limit values at every key create/patch boundary (or normalize them explicitly to unlimited), validate finite numeric budgets/TTLs, and make the limiter's rejection path safe when a window has no prior events. Malformed client-controlled limits must produce a dialect-correct 400, never an unhandled exception.

### 60. Configured request-body limit is bypassed for chunked bodies
**File:** `wiwi/server/app.py:40-111`
**Trigger:** a caller sends a request without a `Content-Length` header, such as a chunked HTTP/1.1 or HTTP/2 request, whose body exceeds `wiwi_settings.max_request_body_mb`.

`RequestIdMiddleware` performs the only early body-size check by reading `Content-Length`. If that header is absent or non-numeric, it passes the original ASGI `receive` callable through unchanged. FastAPI then consumes the complete body in `request.json()`, so there is no streaming byte counter or overflow cutoff. A direct middleware probe with a 1 MiB limit delivered a 1.4 MiB body to the downstream application and returned 200 despite the configured cap. An unauthenticated caller can use this to bypass the memory-protection control and drive unbounded request-body buffering/JSON parsing; the same applies to API and admin routes once authenticated.

**Fix:** wrap `receive` with an ASGI byte counter that rejects or truncates once the cumulative body exceeds the configured maximum, while preserving normal `http.disconnect` and `more_body` semantics. Keep the `Content-Length` fast path, but treat it only as an optimization—not as the enforcement mechanism.

### 61. Gemini API keys leak in provider-model connection errors
**File:** `wiwi/server/app.py:1405-1416`
**Trigger:** an admin requests `GET /admin/providers/{name}/models` for a Gemini provider and the upstream request raises an `httpx.HTTPError`.

The endpoint appends the provider key to the Gemini model-list URL as `?key={key.secret}`. Its connection-error handler then returns that complete URL in the client-visible 502 message. A forced `httpx.ConnectError` produced `could not reach 'gem' (https://generativelanguage.googleapis.com/v1beta/models?key=AIza-SUPER-SECRET-123)`. The credential is therefore exposed to the admin browser, reverse-proxy/access logs, API clients, and any error collector that records response bodies. The route is admin-gated, so this is an administrator-side secret disclosure rather than an unauthenticated gateway compromise.

**Fix:** never include credential-bearing URLs in errors. Redact query parameters before formatting connection errors, or keep the message to the provider name and sanitized endpoint origin. Apply the same rule to logs and exception telemetry around all providers.

### 62. Virtual-key mutation accepts malformed types, truncates limits, and returns HTTP 500
**File:** `wiwi/server/app.py:837-864, 1739-1758`; `wiwi/auth/service.py:127-151, 175-222`
**Trigger:** an authenticated user or admin supplies malformed virtual-key fields to `POST /admin/keys/generate` or `PATCH /admin/keys/{key_id}`.

The create handler forwards `max_budget`, `rpm`, `tpm`, and `ttl_seconds` directly to `AuthService.create_key()` without type, finiteness, or range validation. `rpm: "bad"` and `max_budget: "bad"` are accepted and stored as SQLite-coerced zero values; `rpm: 1.5` is accepted and stored as `1`, changing the requested limit; and `ttl_seconds: "bad"` raises an uncaught `TypeError`/`ValueError` and returns HTTP 500. The patch path is worse: `tpm: "bad"`, `max_budget: "bad"`, `ttl_seconds: "bad"`, and `expires_at: "bad"` each return HTTP 500, while `models: "abc"` silently becomes `['a', 'b', 'c']`. These are client-controlled mutation inputs and should never produce an internal error or silently create a different policy than the one requested. This is distinct from #59, which covers valid negative rate limits reaching a broken limiter rejection path.

**Fix:** validate the complete key schema at the HTTP boundary: require finite numbers, integer rate limits, list-of-string model allowlists, and explicit positive/zero semantics for budgets and TTLs. Catch conversion/DB exceptions and return a dialect-correct 400; reject rather than truncate fractional values or coerce strings through SQLite.

---

## Summary by severity

| Severity | Count | Notable |
|---|---:|---|
| 🔴 Critical | 2 | Stream-pump deadlock; forged admin session with default secret |
| 🟠 High | 17 | OAuth callback poisoning, signup/body-size abuse, unlimited-key limit bypass, budget-cap bypass, parallel tool-call corruption, Anthropic cost bugs, /metrics auth gap |
| 🟡 Medium | 21 | Provider-key error leak, negative-limit completion 500s, SSE id injection, partial-JSON, grace-drain deadlock, malformed-body 500s, UI TDZ + routing |
| ⚪ Low | 21 | Redis limiter (dormant), percentile inconsistency, WRR starvation, doc/contract footguns |
| **Total** | **61** | |

## Top recommendations (fix order)

1. **#54** — remove the fixed default session secret and fail closed when no master/session secret is configured.
2. **#56 + #58 + #60** — prevent attacker-controlled OAuth callback origins, throttle public account registration, and enforce request-body limits while receiving.
3. **#57** — enforce account-wide quotas and key issuance limits so users cannot rotate unbounded keys around per-key controls.
4. **#52** — hard budget-cap enforcement. Prevent repeated billable requests from bypassing a configured spend ceiling.
5. **#1** — stream pump deadlock (wrap encode in try/except + `ready.set()`). Trivial fix, prevents permanent hangs.
6. **#2 + #3** — parallel tool-call encoder corruption (Responses + Anthropic). Core translation contract violation; affects every interleaved tool-call stream.
7. **#9 + #10** — Anthropic cost accounting (double-subtraction + unpriced cache-creation). Direct revenue impact.
8. **#4** — streaming Retry-After. Affects cooldown correctness under 429s.
9. **#7** — deployment exclusion after key exhaustion. Causes premature 503s.
10. **#12** — /metrics auth gap. Security exposure.
11. **#53 + #55 + #59 + #61** — reject malformed request/limit values, add authentication-attempt throttling, and redact provider credentials from errors.
12. **#27 + #28** — UI: Models strategy dropdown crash + Settings routing 404. User-visible breakage.

---

## Addendum — Translation-layer 2026 alignment (2026-09-02)

The 28-item translation-layer review (streaming/state, decode robustness, structured outputs, 2026 params, multimodal) is **fully fixed** as of 2026-09-02 — see `UPDATE.md` Round 6 and `tests/test_fix_round24.py` + `tests/test_translation_enhancements.py`. Commits: `381e5d4` (C1+C2), `608f9e1` (C3), `068330a` (C4), `a821230` (C5). Items 27–28 (message.refusal capture, compaction stop reason) landed in `381e5d4`.

Known limitations carried forward (deliberate, not bugs):

- Anthropic stream encoder's `message_start` carries zero usage; real usage only arrives at `UsageFinal` (stream end).
- `cache_creation_tokens` has no OpenAI usage field; Anthropic-surface only.
- `previous_response_id` is rejected on the Responses surface (MVP scope).

The findings below predate this session and remain the live register.

---

## Addendum — parallel tool-call integrity, Round 7 (2026-09-02)

Items **#1, #2, #3, #4** were re-verified against current source and are
**fixed**; do not re-fix. They had shipped without regression tests, which is
why they still read as open. All four are now pinned in
`tests/test_fix_round25.py` (see `UPDATE.md` Round 7).

| Item | State | Test |
|---|---|---|
| #1 stream-pump deadlock on `encode_request` throw | fixed `84b084a`, newly pinned | `test_stream_pump_survives_encode_failure` |
| #2 Responses parallel `output_index` corruption | fixed `84b084a`, newly pinned | `test_responses_parallel_args_route_to_own_output_index`, `test_responses_parallel_close_uses_own_output_index`, `test_responses_sibling_open_does_not_close_first_tool` |
| #3 Anthropic parallel args misroute / `IndexError` | fixed `4a70889f`, newly pinned | `test_anthropic_parallel_args_route_to_own_block_index`, `test_anthropic_args_delta_with_no_open_tool_is_dropped_not_crashed` |
| #4 streaming `Retry-After` never parsed | fixed `84b084a`, newly pinned | `test_stream_error_path_parses_retry_after` |

### New bug found while writing those tests — fixed

**`ResponsesStreamEncoder._close_item()` emitted a duplicate
`output_item.done`.** It read the open tool's entry from `self._tools` without
popping it, so a later `ToolCallClose` for the same index closed it a second
time at the same `output_index`. Triggered by a `TextDelta`/`ThinkingDelta`
while two tool calls are open. Codex CLI counts a phantom tool call.
Fix: `_close_item` delegates to `_close_tool` (which pops).
Covered by `test_responses_no_duplicate_output_item_done_after_text_interleave`
and `..._after_thinking_interleave`.

Note that #2's original finding was partly right for the wrong reason: per-tool
`output_index` bookkeeping was already correct, but the pre-existing test
(`test_responses_encoder_parallel_tool_calls_preserved`, round 6) only asserted
`call_id`/`name` containment and never `output_index` — so the index-corruption
class of bug could pass. The round-25 tests assert index routing directly.

**Baseline after this round:** 1116 tests pass, ruff clean.

---

## Addendum — built-in web search translation, Round 8 (2026-09-02)

The two silent-drop bugs and the Anthropic mangle are **fixed**; do not re-fix.
All covered by `tests/test_web_search_translation.py` (51 tests) + 3 new
properties in `tests/test_property_roundtrip.py` — see `UPDATE.md` Round 8.

| Bug (as it existed) | State |
|---|---|
| Anthropic surface decoded `web_search_20250305` as a *function* tool → upstream received broken `{"name":"web_search","input_schema":{…}}` | fixed — registry-driven builtin decode (`wire/anthropic_messages.py`) |
| Responses surface silently dropped `{"type":"web_search"}` tools (`type=="function"` filter only) | fixed — `web_search`/`web_search_preview`/`web_search_2025_08_26` decode to the builtin IR tool (`wire/openai_responses.py`) |
| Gemini/OpenRouter never encoded a builtin search tool (unreachable) | fixed — `google_search` sibling entry / `openrouter:web_search` hosting (`gemini_adapter.py`, `openrouter_adapter.py`) |

Deliberate v1 losses, **not** bugs (documented in `UPDATE.md` §8.6):
response-side search traces suppressed on Anthropic/Chat surfaces (A1 — a
half-trace would 400 on turn-2 replay while citations are unimplemented);
Responses input history skips `web_search_call` items (A2 — same trap,
inbound side); `max_uses` ↔ `search_context_size` have no clean map;
Anthropic's separate `web_search_requests` billing is unmodeled (pricing
follow-up).

**Baseline after this round:** 1170 tests pass, ruff clean.

---

## Addendum — cache layer + durable stream recovery, Round 9 (2026-09-03)

Two audit findings implemented end to end (the "4. Cache layer" item and the
stream-restart item from the external review), plus one real bug found by the
exploration pass. Tests: `tests/test_cache_and_journal.py` (feature tests) +
`tests/test_fix_round26.py` (bugfix regressions, per file convention).

### Response cache (was: no response cache, no TTL store)

New `wiwi/cache/` package implementing the docs/CORE.md §6 spec:

| Module | Contents |
|---|---|
| `keygen.py` | `response_cache_key()` — SHA-256 over normalized IR (dataclass-aware projection) + group + surface + key_id. Dialects decoding to identical IR share entries; keys are scoped per virtual key. |
| `interface.py` | `CacheBackend` protocol + frozen `CacheEntry` (payload, media_headers, stored_at, request_id, model). Redis/semantic backends plug in here. |
| `response_cache.py` | `MemoryResponseCache` — OrderedDict LRU with lazy TTL eviction. |

Wired in `run_chat_like` for **non-streaming** requests only, after
rate-limit / before gateway dispatch; store happens after
`codec_encode_response`. Bypass with `X-Wiwi-No-Cache: true`. Hit responses
carry `x-wiwi-cache: HIT`. Off by default (`cache_settings.enabled: false`).

**Semantic separation enforced:** a gateway response-cache hit sets
`LogEvent.response_cache_hit`, NOT `cache_hit` — the latter means provider
prompt-cache hit and feeds `wiwi_prompt_cache_hits_total`. Conflating them
would silently inflate the prompt-cache metrics.

### Prompt-cache observability (was: cache_creation never persisted, no hit-rate in Prometheus)

The audit's claim was partly wrong — `tok_cached`/`cache_hit`/`cache_savings`
were already persisted and `cache_hit_rate` existed in
`/admin/stats/overview`. The real gaps, now closed:

- `tok_cache_creation` flows LogEvent → `request_logs` column (idempotent
  migration) → overview/timeseries sums (both DB and ring paths).
- `/metrics` gains `wiwi_prompt_cache_hits_total`,
  `wiwi_prompt_cache_hit_rate`, `wiwi_response_cache_hits_total`, and
  `wiwi_tokens_total{kind="cache_creation"}`.

### Bug fixed: `_complete_via_stream` dropped cache_creation_tokens

`gateway.py`'s UsageFinal→`ir.Usage` fold (force_stream providers: Cline,
WorkBuddy) omitted `cache_creation_tokens` — Anthropic cache-write tokens
never reached pricing or logs for those providers. Fixed; pinned in
`test_fix_round26.py`.

### Durable stream recovery (was: StreamTape in-process only, replay unwired)

Verified exploration also found the client-facing half of StreamTape's
advertised resume was never built: `tape.replay()` had zero production
callers, and `_stream_response`'s SSE ids used a separate seq counter from
the tape. Rather than wiring the dead path, implemented the documented
design (STREAMING_PERFORMANCE_RECOVERY.md #5) with a disk journal:

- New `wiwi/streaming/tape_store.py`: `StreamJournal` (append-only JSONL per
  request_id, per-journal byte cap, `asyncio.to_thread` writes) +
  `JournalStore` (registry, `read_after`, `is_complete`, eager file touch,
  TTL sweep at startup).
- `_stream_response` journals every encoded SSE chunk post-id-injection with
  one shared monotonic seq for both `id:` lines and journal records — the
  id-space mismatch is structurally gone.
- Reconnect protocol: re-POST the same request with
  `x-wiwi-stream-id: <original request id>` + `Last-Event-ID: <chunk seq>`.
  Replay serves purely from the journal (no upstream call, no double
  billing), follows the file tail if the original stream is still running,
  and works **across a wiwi restart** — the durability contract is pinned by
  `test_stream_replay_survives_restart`.
- Default ON (`stream_journal_enabled: true`, dir `.wiwi/journals`, TTL 600s,
  1 MiB/journal cap); `stream_journal_dir` should be on persistent storage in
  containers.

**Baseline after this round:** 1185 tests pass, ruff clean.

## Addendum — OpenCode Zen free-tier client gate, Round 10 (2026-09-07)

**Symptom:** every Zen `-free` model request through wiwi failed with
`400 {"type":"MissingSessionID","message":"Error from provider (Console): OpenCode's free tier can only be used in OpenCode"}`.

**Root cause (verified by live probe matrix, 2026-09-07):** Zen's free-tier
gate changed after the round-27 adapter shipped. It no longer keys on the
`User-Agent` — it now requires the client **session header**
(`x-opencode-session`), matching the real client
(`packages/opencode/src/session/llm/request.ts` sends `x-opencode-session`
+ `x-opencode-client` on every opencode-provider request). Probe results
against `POST /zen/v1/chat/completions`, anonymous, model `mimo-v2.5-free`:

| headers sent | result |
|---|---|
| UA `opencode/1.18.18` only (what wiwi sent) | 400 MissingSessionID |
| `x-opencode-session: ses_x` only, default python-httpx UA | **200** |
| UA + `x-opencode-client: cli`, no session | 400 MissingSessionID |
| UA `opencode/unknown` + session | 200 |
| full client emulation | 200 (streaming too) |

Gate order at the edge: session check (400) → bearer check (401) — a
placeholder bearer with session headers gets `401 Invalid API key`.

**Second issue found by the same probes:** free models serve keyless
(anonymous) traffic, but `KeyDef` validation requires non-empty keys — a
free-tier-only setup had no way to say "no key"; a placeholder bearer is
401-rejected.

**Fix (`wiwi/providers/opencode_adapter.py`):**

- `is_free_model()` — `-free` suffix + unsuffixed stealth free models
  (live catalog 2026-09-07: `big-pickle`; the only one).
- `headers()` adds `x-opencode-session: ses_<hex>` (per-request, stable
  across the 401-refresh retry rebuild) + `x-opencode-client: cli` **for
  free models only**. Paid models never get them: they are not
  session-gated, and session ids actively shard Zen's upstream routing
  (kimi-k2.7-code: fresh session ids fail ~50% on a broken replica).
- `ANONYMOUS_KEY_SENTINEL = "anonymous"` — a config key of the literal
  `anonymous` omits `Authorization` entirely (keyless free tier).
- `wiwi.yaml.example` documents the sentinel.

**Live verification:** anonymous (no key) via the adapter on
`mimo-v2.5-free`, `nemotron-3.5-lightning-free`, `big-pickle` (chat route)
and `muse-spark-1.3-contributor-free` (responses route) — all 200 with
decoded turns. Note `deepseek-v4-flash-free` is retired upstream (400
"Model is unavailable" under all header variants) — replace it with a live
free model.

**Tests:** `tests/test_fix_round29.py` — classification, header gating
(free vs paid, pre-encode fail-safe, stability, reset), anonymous sentinel,
gateway e2e chat/stream upstream-header assertions.

**Baseline after this round:** 1253 tests pass, ruff clean.

## Addendum — Responses tool-call args duplication, Round 11 (2026-09-07)

**Symptom:** client rejected a tool call with `Tool call run_commands emitted
invalid JSON arguments: Tool call arguments could not be parsed as JSON`.

**Root cause (verified by live SSE capture, 2026-09-07, muse-spark free):**
the Responses upstream delivers the same arguments **three times** in three
event types — incremental `response.function_call_arguments.delta` fragments,
then the **cumulative** full-args string on
`response.function_call_arguments.done`, then again on
`response.output_item.done`. The round-27 decoder treated the `.done`
payloads as *more fragments* and re-emitted them as `ToolCallArgsDelta`,
so the client accumulated `{"a":1}{"a":1}...` — invalid JSON. wiwi's own
advisory validator flagged the same concatenated buffers
(`tool_args_invalid_json`, 148–406 bytes, and `bytes=0` single-shot items).

**Fix (`wiwi/providers/opencode_adapter.py`, responses stream decoder):**

- `.delta` events append to a per-entry buffer (fragments remain the only
  emitted args stream).
- `.done` marks entries closed instead of popping: `args.done` then
  `item.done` for the same item no longer re-opens a duplicate tool call.
- `.done`'s cumulative `arguments` is emitted **only** when it adds
  information — the single-shot no-fragments case, or a suffix repair when
  the streamed buffer is a strict prefix (truncated-delta repair). Equal
  buffer: emit nothing. Non-prefix: trust the streamed fragments.
- `response.completed`'s flush-close respects the closed flag (no double
  close).

**Live verification:** pre-fix, tool calls failed exactly as reported
(reported error + validator warnings in the gateway log); post-fix
(18:35 reload), the same pipeline executes tool calls cleanly — this
documented session itself ran through it. Unit-pinned by
`tests/test_fix_round29.py` (`test_responses_stream_done_is_cumulative_not_a_fragment`,
`test_responses_stream_done_only_no_deltas`,
`test_responses_stream_multi_fragment_reassembly`).

**Baseline after this round:** 1256 tests pass, ruff clean.

## Addendum — Anthropic type-less block 500, and Responses error shape (2026-09-07)

**Symptom 1:** `POST /v1/messages` with a content block that carries no `type`
key returned **500 Internal Server Error** — `AttributeError: 'NoneType'
object has no attribute 'endswith'` in the proxy log.

**Root cause 1 (`wiwi/wire/anthropic_messages.py:73`):** the block dispatcher
read `btype = b.get("type")` and matched every arm with `==`, except the
server-tool-result arm, which needed suffix matching for the
`web_search_tool_result` / `code_execution_tool_result` / `mcp_tool_result`
family and so called `btype.endswith("_tool_result")`. A type-less (or
non-string-typed) block made that dereference raise, and `run_chat_like`
catches only `(DialectError, ValueError)` — so one junk block escaped as a
gateway 500. The guard three lines above already skips non-dict blocks for
exactly this reason ("skip rather than 500 on .get"); the `type` field simply
never got the same treatment.

**Fix 1:** hoist an `isinstance(btype, str)` check to the top of the loop body
and `continue` on failure. No branch below can match a non-string type, so
this preserves every legal decode path while closing the crash.

**Symptom 2:** `POST /v1/responses` errors were returned in **Chat
Completions** shape — `{"error":{message,type,code}}` — missing the `param`
key the Responses API documents.

**Root cause 2 (`wiwi/server/app.py`):** `_err` branched
`if surface == "messages": am.error_body else: oc.error_body`, collapsing the
Responses dialect onto Chat. Each route passed its own `error_body` into
`run_chat_like` as `error_body_fn`, but `_err` ignored that parameter entirely
— so `orp.error_body` had **zero callers** in the server. The same
path-inference gap affected `json_body`, which runs before the codec and so
guesses the dialect from the URL: `path.endswith("/messages")` misses both
`/v1/responses` and `/v1/messages/count_tokens` (which does not *end* with
`/messages`), meaning even the Anthropic `count_tokens` surface answered body
errors in the wrong dialect.

**Fix 2:** add `_error_body_for(surface)` — the error-path mirror of the
existing `_encoder_for` — and have `_err` dispatch through it. Replace
`json_body`'s `endswith` heuristic with `_surface_for_path`, which mirrors the
route table with `startswith` (`/v1/messages*` → messages, `/v1/responses` →
responses, else chat). Drop the now-redundant `error_body_fn` parameter from
`run_chat_like` and its three callsites rather than leaving two parallel
conventions for the same decision.

**Live verification:** against a running server — `/v1/responses` 404 and 400
now carry `"param": null`; `/v1/messages/count_tokens` 400 returns
`{"type":"error",...}`; `/v1/chat/completions` is unchanged (no `param`); and
the type-less block that previously 500'd now reaches the upstream (401 from a
deliberately bogus key, i.e. the request decoded).

**Tests:** `tests/test_fix_round30.py` — type-less / non-string-type blocks at
the codec (user turn, assistant turn, sibling preservation), the
`tool_result` and `*_tool_result` arms still decoding, the 500 gone
end-to-end, and per-surface error shape across all three dialects plus
`count_tokens`.

**Baseline after this round:** 1270 tests pass, ruff clean.

---

## Addendum — response-cache determinism guard + Redis backend, Round 34 (2026-09-09)

Two gaps in the Round 9 cache layer, both about *when* and *where* a response
may be reused.

### 34a. Sampled requests were cached (correctness)

**Files:** `wiwi/cache/keygen.py`, `wiwi/server/app.py`

`response_cache_key()` hashes the full normalized IR, so the key can only ever
match an *identical* request — but "identical request" is not "same expected
answer". With `cache_settings.enabled = true`, a client sending
`temperature: 0.8` received the same completion for the whole TTL, because
nothing gated admission on determinism. A "write me a poem" endpoint would
have returned one poem forever.

**Fix:** new `is_cacheable_request()` admission predicate (temperature unset or
`0`; `n == 1`), applied at the cache branch in `run_chat_like`. `seed` is
deliberately not a gate — it is not a portability guarantee across providers or
model versions, and `temperature=0` is already admitted without one.

Verified end-to-end: `temperature` 0.8 / 1.0 reach upstream twice (no
`x-wiwi-cache`), while unset / `0` hit with one upstream call.

### 34b. `redis_url` read but unused for caching (dormant config)

**Files:** `wiwi/cache/redis_cache.py` (new), `wiwi/cache/__init__.py`,
`wiwi/server/app.py`

`CacheBackend` was declared in Round 9 for exactly this seam, but only the
memory backend existed. `GeneralSettings.redis_url` was parsed and never used
for caching (the dormant `RedisRateLimiter` is a separate issue, still unwired
by design — rate limiting was out of scope here).

**Fix:** `RedisResponseCache` over `GET`/`SETEX`/`DEL`, selected by
`build_response_cache()` when `redis_url` is set; memory stays the default.
Design constraints, both real:

- `CacheEntry.payload` is `bytes` and **orjson refuses to serialize `bytes`**
  (`TypeError: Type is not JSON serializable: bytes`), so the entry is a
  base64-armored JSON document, and the client is `decode_responses=False`.
- A cache must never fail a request: every Redis call degrades to a miss, and
  `SETEX` TTL is clamped to `>= 1` because Redis rejects a non-positive expiry
  (`int(0.9) == 0` would raise mid-request).

The backend is closed in `AppState.shutdown()`.

**Note on expected gain:** Redis is *not* a latency win for a single process —
`main.py` runs one uvicorn worker and a dict lookup beats a network round-trip.
It buys restart survival, a shared cache across replicas, and capacity beyond
`max_entries: 256`. Not benchmarked: no Redis server in the dev environment,
so correctness is covered by an injected fake client, not a live instance.

**Tests:** `tests/test_fix_round34.py` (22 tests) — determinism predicate,
byte/unicode round-trip, namespacing, TTL clamp, corrupt-entry tolerance,
never-raise-on-unreachable, plus end-to-end temperature matrix and backend
selection.

**Baseline after this round:** 1387 tests pass, ruff clean.

---

## Addendum — Anthropic prompt-cache breakpoint injection, Round 35 (2026-09-09)

### 35. No prompt caching unless the client sends `cache_control` — cost

**Severity:** 🟡 Medium (silent overspend; no correctness impact)

**Files:** `wiwi/providers/anthropic_adapter.py`,
`wiwi/config.py` (`DeploymentParams`), `wiwi/router/router.py` (`Deployment`),
`wiwi/core/gateway.py` (3 `params` sites)

`cache_control` was pure pass-through. `wiwi` never *originated* a cache
breakpoint, so a client that did not send one paid full input price on every
request — even for the exact shape prompt caching exists for (large static
system prompt + stable tool definitions + a short varying user turn).

**Why the naive fix is a trap** (from the current Anthropic docs):

- A cache write happens **only at the breakpoint**, and a read walks back
  looking for entries *prior requests wrote*. Marking the trailing user turn
  — which differs every request — means every call writes a fresh entry and
  none ever reads one. You pay the **1.25x write premium forever**. The docs
  call this the "common mistake".
- Anthropic's top-level *automatic* caching (`cache_control` at request top
  level) has the same flaw here: it places the breakpoint on the last
  cacheable block, which for static-system + varying-message is the varying
  one.
- Below a model-specific minimum (512–4096 tokens, varies by model) the API
  **silently does not cache**, so marking a short prefix is a write that will
  never be read.

**Fix:** opt-in `prompt_cache` deployment param. Marks the **stable prefix
only** — last tool definition and last system block — never the trailing user
turn. Estimates prefix tokens and skips below `prompt_cache_min_tokens`
(default 1024). Caller-supplied `cache_control` always wins (no
second-guessing, and never exceeds the 4-breakpoint budget). Off by default.

Two ordering constraints discovered while implementing, both now covered by
tests:

- Injection must run at the **end** of `encode_request`; `body["tools"]` does
  not exist until after the tool loop.
- With `response_format` set, the JSON-output instruction is appended as its
  own trailing block. It must stay **outside** the marked prefix — it varies
  with the schema, so marking it would destabilise the cached prefix.

**Tests:** `tests/test_fix_round35.py` (18) — off-by-default, prefix-only
marking, never marking the user turn, threshold boundary, caller markers win,
4-breakpoint budget, JSON-instruction placement.

**Verified:** default path byte-unchanged (`system` stays a `str`, zero
markers); with `prompt_cache: true` the system block is marked, the system
text is identical across turns, and the user turn is never marked.

**Baseline after this round:** 1405 tests pass, ruff clean.

---

## Addendum — Redis deployment ergonomics, Round 36 (2026-09-09)

### 36. `redis_url` had no env-var override; `[redis]` extra not installed in the image

**Severity:** 🟡 Medium (config silently inert in containers)

**Files:** `wiwi/server/app.py` (`AppState.__init__`), `Dockerfile`,
`docker-compose.yml`, `README.md`

Two deployment blockers found while answering "how do I set REDIS_URL in
Docker / Railway":

1. `DATABASE_URL` is read from the environment at runtime
   (`app.py` `init_db`), but `redis_url` was **config-file only**. The shipped
   image boots on `wiwi.yaml.example`, whose `redis_url` is commented out — so
   setting `REDIS_URL` in a container did **nothing**. The only workaround was
   mounting a custom config file, which the compose stack does not do.

   **Fix:** `AppState.__init__` now reads `os.environ.get("REDIS_URL") or
   config.general_settings.redis_url`, mirroring the DATABASE_URL pattern. An
   empty env var falls back to config, so platforms that inject empty values
   (Railway/Render) do not shadow a configured URL.

2. The Dockerfile ran `uv pip install -p … .` **without** the `[redis]`
   extra. `build_response_cache()` catches `ImportError` and falls back to
   memory, so a configured `redis_url` would have quietly kept using memory —
   no error, no log.

   **Fix:** install `.[redis]`; added a `redis:7-alpine` compose service with a
   healthcheck and `REDIS_URL` wired to the `wiwi` service.

Also documented: enabling Redis requires **both** `cache_settings.enabled: true`
and a URL. Setting a URL alone is a no-op, which is an easy misconfiguration.

**Verified against a real Redis 7 container** (previous round only had an
injected fake): bytes/unicode round-trip, TTL enforced by Redis (`SETEX`),
expiry observed live, delete, degrade-to-miss when unreachable, and a full
HTTP end-to-end showing `x-wiwi-cache: HIT` on the second call with one
upstream call.

**Tests:** `tests/test_fix_round34.py` (+3) — REDIS_URL overrides config,
env beats YAML, empty env falls back to config.

**Baseline after this round:** 1409 tests pass, ruff clean.

## Addendum — runtime response-cache toggle, Round 37 (2026-09-09)

`cache_settings.enabled` was config-file-only. There was no admin endpoint for
it, so enabling or disabling the response cache meant editing `wiwi.yaml` and
restarting the gateway — awkward generally, and self-defeating for the Redis
backend specifically, since surviving a restart is the main reason to use it.

Two findings made this more than "add a route":

1. 🟠 **`AppState.__init__` captured the backend once.** The instance was built
   at construction from the loaded config (`wiwi/server/app.py:548`) and
   `run_chat_like` read `state.response_cache` from that single construction.
   So an API that only flipped the config boolean would have had no effect on
   the live backend without a restart. The instance must be (re)derived from
   config on demand.

2. 🟡 **Disabling would strand the backend.** `response_cache = None` alone
   leaks: a memory backend keeps response bodies resident and a Redis client
   stays open. The backend must be `aclose()`d on disable.

**Fix:** `GET`/`PUT /admin/cache/settings` (`{"enabled": bool}`), plus
`AppState._sync_cache_enabled()` which builds or closes the backend to match.
Enabled is idempotent (a second PUT must not rebuild, or it silently flushes
every warm entry). Persisted to the settings table as
`response_cache_enabled` and applied at startup, so the DB overrides YAML in
both directions.

**Security:** the admin view reports `redis_configured` (a bool), never the
URL — `redis://user:password@host` embeds a credential. `backend` is reported
from the live instance rather than config, so a Redis URL with the `redis`
package missing correctly reads as `memory` instead of claiming Redis.

**UI:** new "Response cache" card on Settings → General, with backend badge,
TTL, max entries, bypass header, and an explanatory note that memory is faster
than Redis for a single instance.

**Tests:** `tests/test_fix_round37.py` (15) — auth on both methods, disabled
by default, Redis selected when URL set, no URL/password leakage, live toggle
both directions, idempotent enable, disable closes backend, non-boolean
rejected with state left untouched, persistence both directions across
restarts, YAML honoured when no DB row.

**Verified live** (real uvicorn + a stub upstream that counts calls): disabled
→ 2 calls/2 upstream; enabled → 2 calls/1 upstream with `x-wiwi-cache: HIT`;
disabled again → back to 1 call/1 upstream. Restart durability checked across
three boots including a case where YAML says `true` and the DB overrides it to
`false`.

**Baseline after this round:** 1424 tests pass, ruff clean.

---

## ✅ Fixed — Cline live version fingerprint

### Cline adapter announced wiwi and Python versions as Cline versions

**Severity:** 🟡 Medium (upstream compatibility and misleading client fingerprint)

**Files:** `wiwi/providers/cline_adapter.py` (pre-fix lines 104-114)

**Trigger:** any request routed through the Cline provider before this fix.

The adapter used wiwi's package version for `User-Agent`, `X-CLIENT-VERSION`, and
`X-CORE-VERSION`, and Python's interpreter version for `X-PLATFORM-VERSION`. Cline expects the
live CLI version in all client-version fields and its separately published core package version in
`X-CORE-VERSION`, so the request fingerprint did not match a real Cline CLI.

**Fix:** added `wiwi/providers/cline_version.py`, which independently refreshes `cline` and
`@cline/core` from npm every five minutes on a background task and serves stale values after
failure. `ClineAdapter.headers()` reads the synchronous cache with no request-path I/O, and the
worker is started and stopped by the FastAPI lifespan. Covered by
`tests/test_fix_round36.py`.

---

## Addendum — streaming-layer audit (delta chunks, partial JSON, flow), 2026-09-09

Full pass over `wiwi/streaming/` (deltas, partial_json, coalesce, sse, loopdetect,
resume, tape_store, validation), the gateway pump/consumer, the three wire
StreamEncoders, and `_stream_response`/journal wiring. Five new verified findings
(#63–#67); two register entries marked fixed in place above (#18, #30 — both were
stale). The already-fixed items from prior addenda (#1–#4, #13–#16 hardening, #20,
#21, #51, round-11 args duplication, round-25/26/29 streaming pins) re-verified by
source reading as still fixed — do not re-fix.

### 63. `ResponsesStreamEncoder` leaks builtin-tool args as phantom `function_call_arguments.delta` frames
**Severity:** 🟠 High (client-visible protocol corruption on a default-on path)
**File:** `wiwi/wire/openai_responses.py:577-590` (`ToolCallArgsDelta` arm)

**Status: fixed** — round 38 (2026-09-09). The `ToolCallArgsDelta` arm now
accumulates into the tool's buffer and returns `None` for builtin-tagged
entries, so no `fc_*` frame is emitted; `_close_tool`'s `_builtin_query`
still reads the accumulated args. Pinned by
`tests/test_fix_round38.py::test_responses_builtin_args_emit_no_phantom_function_frames`.
*(Original finding preserved: the `ToolCallArgsDelta` arm emitted
`function_call_arguments.delta` frames for builtin-tagged opens, whose
`fc_<req>_<n>` item ids never had an `output_item.added` — Codex CLI
accumulated fragments against a nonexistent function item. Verified by
execution pre-fix.)*

### 64. `_repair_truncated_json` produces invalid JSON on odd backslash runs ≥ 3
**Severity:** 🟡 Medium (silent total loss of tool args — falls to `{}`)
**File:** `wiwi/streaming/partial_json.py:74` (the escaped-state check)

**Status: fixed** — round 38 (2026-09-09). The endswith heuristic is replaced
by a trailing-backslash **run count**: an odd run strips its final (dangling)
backslash, and the `\uXXXX` strip is now escape-aware — it only fires when the
backslash run before the `u` is odd (a fresh escape opener), so `"C:\\u0f`
(complete pair + literal `u0f`) is left intact while pair + fresh `\u0f` strips
only the fresh tail. Pinned in `tests/test_streaming_improvements.py`
(`test_repair_odd_backslash_run_ge_3`,
`test_repair_escaped_backslash_then_partial_unicode`,
`test_repair_partial_unicode_after_odd_run`,
`test_repair_complete_unicode_escape_not_stripped`).

*(Original finding preserved: the `endswith` guard handled only a single
trailing backslash; odd runs ≥ 3 fell through and produced invalid JSON —
the whole args object silently fell to `{}`. Verified by execution pre-fix,
including the escaped-backslash + partial `\u` case that defeated the
round-14 strip.)*

### 65. Mid-tool-args TextDelta silently truncates the tool call (Responses surface)
**Severity:** 🟠 High (args loss; wrong-but-valid-looking call delivered)
**File:** `wiwi/wire/openai_responses.py:511-531` (`TextDelta` arm closing an open tool item)

**Status: fixed** — round 38 (2026-09-09), by the clean-fix route the finding
recommended: the `TextDelta` (and `ThinkingDelta`) arms now suppress the
interleave while `_item_open == "tool"` — mirroring the Anthropic encoder —
so the tool stays open, its args keep streaming on their own output_index,
and no mid-stream `output_item.done` fires. Pinned by
`tests/test_fix_round38.py::test_responses_interleave_preserves_tool_output_index_routing`
(asserts full post-interleave args on the right index, one done per tool, no
message item synthesized after the fact).

*(Original finding preserved: `_close_item()` popped the tool on interleave,
so later args fragments were dropped and the client received
`{"city": "Tok` as the final call. Verified by execution pre-fix.)*


### 66. Reconnect to an empty journal double-dispatches the request (double billing)
**Severity:** 🟡 Medium (duplicate upstream call + spend for one logical request)
**File:** `wiwi/server/app.py:1084-1087` (replay gate) + `wiwi/streaming/tape_store.py:120` (eager touch)

**Status: fixed** — round 39 (2026-09-10). `JournalStore.is_active()` exposes
same-process liveness; the replay gate is now `replay or complete or ACTIVE`,
so a sub-second reconnect to an open-but-empty journal tails the same journal
instead of dispatching and billing a second upstream call. Pinned by
`tests/test_fix_round39.py::test_reconnect_to_active_journal_tails_not_redispatches`
(e2e: reconnect while the journal file is empty; asserts exactly one upstream
call total).
*(Original finding preserved: the gate `if replay or is_complete(...)` missed
the empty-but-active case — `read_after → []`, `is_complete → False` — so a
reconnect in the sub-second TTFT window fell through to a fresh upstream
dispatch. Verified by execution against the real `JournalStore` pre-fix.)*
### 67. Stream journals are replayable by any authenticated caller (no per-key scoping)
**Severity:** 🟡 Medium (cross-tenant content disclosure within one gateway)
**File:** `wiwi/server/app.py:1078-1114` (replay branch); `wiwi/streaming/tape_store.py:105-107`

**Status: fixed** — round 39 (2026-09-10), by the record-the-originating-key
route the finding recommended: `JournalStore.open()` writes an internal
ownership record (``seq: 0`` + ``owner: <key_id>``) as the journal's first
line — invisible to `read_after`/`is_complete`, which filter or ignore it —
and `owner_of()` reads it back (survives restarts, since it lives in the
file). The replay branch compares `owner_of(replay_id)` against the caller's
`key_id` before serving; mismatched callers get no replay (they fall through
to their own dispatch, never see the other key's content). Journals written
by pre-scoping versions have no owner record and stay readable (restart-
replay back-compat). Pinned by
`tests/test_fix_round39.py::test_cross_key_replay_blocked_same_key_allowed`
plus the `owner_of`/`is_active` unit tests; verified live with two minted
virtual keys against a real app instance.

*(Original finding preserved: the replay branch looked the journal up by id
alone, so any authenticated caller holding another user's stream id could
replay the entire response body.)*

### 68. Tape eviction can desynchronize resume replay (`replay(last_seq)` assumes contiguous availability)
**Severity:** ⚪ Low (resume is off by default; bounded by 256 KiB tape)
**File:** `wiwi/streaming/resume.py:68-70, 134-137`

**Status: fixed** — round 39 (2026-09-10). `StreamTape.head_evicted(last_seq)`
detects a non-contiguous replay head (first surviving seq > last_seq + 1) and
`gateway._attempt_resume` refuses the resume in that case, so the caller
falls back to a fresh attempt instead of silently building a partial
continuation (the evicted-tool-Open case). Pinned by the `head_evicted` unit
tests in `tests/test_fix_round39.py` (gap, contiguous, and empty-replay
cases).

*(Original finding preserved: `replay(last_seq)` filtered survivors only, so
an evicted head produced continuation messages missing the tool call —
verified: `replay_tool_calls` returned `[]` while Args/Close survived.)*

### Coverage gaps found while auditing (no defect — missing pins)

- **No production caller of `PartialJSONParser`/`parse_partial`.** The incremental
  partial-JSON machinery exists (with its own tests) but every production fold
  uses raw `_repair_truncated_json` + `json.loads` directly
  (`core/gateway.py:313/360`, `resume.py:114`, `openai_adapter.py` non-streaming).
  The "render tool arguments as they arrive" feature is unwired. Either wire it
  (e.g. into an admin/SSE preview surface) or note it as deliberately dormant —
  today it is dead code with maintenance cost. *(Disposition 2026-09-10:
  deliberately kept — tested public API of the streaming layer; deleting
  exported, test-pinned API is a product call, not a bugfix. Revisit only if a
  linter/strict-dead-code policy is adopted.)*
- **`iter_sse_events` (sse.py) has zero production callers** — gateway uses
  `LineSSEParser` directly (with the flush fix). Dead convenience wrapper.
  *(Disposition 2026-09-10: kept for the same reason — exported, tested, 8 lines;
  it is a convenience wrapper, not a second convention in the hot path.)*
- **`DeltaCoalescer` default `threshold=100` is unreachable in production** —
  gateway.py wires `max_bytes`/`max_ms` from config but hardcodes the default
  threshold (`DeltaCoalescer(max_bytes=…, max_ms=…)`), and `stream_coalesce`
  defaults to `false` — the entire coalescing feature is off by default with an
  unconfigurable trigger point. `stream_coalesce` has no threshold knob.
- **No runtime journal sweep.** **Status: fixed** — round 40 (2026-09-10).
  `JournalStore.sweep_forever()` / `start()` / `stop()` implement the periodic
  background TTL sweeper; the lifespan in `server/app.py` starts it when
  journaling is enabled (interval = `min(60, max(1, ttl/4))` s) and stops it at
  shutdown. The tape_store docstring no longer overstates the mechanism. Pinned
  by `tests/test_fix_round40.py` (sweeper expiry, start/stop lifecycle, lifespan
  integration, disabled-journaling skip). *(Original gap preserved: `sweep()`
  ran only at startup — `app.py:763` — so a gateway up for > TTL (600 s)
  accumulated dead journals for the process lifetime.)*
- **Two stale register entries** — #18 and #30 were live in the register but
  fixed in code; both now marked fixed in place above. (Found per the
  do-not-re-report rule: neither re-verified state was recorded.)

### Verification method

All five primary findings (#63–#67) reproduced by direct execution against the
real classes (`python3 - <<'EOF'` harness driving the actual encoders, the real
`JournalStore` on a temp dir, and `LoopDetector` runs); reachability confirmed by
reading the producing adapters (`anthropic_adapter.py` `server_tool_use` tagging,
`openai_adapter.py` same-chunk Text+Args emission) and the consuming paths.
No code changes made — findings only, per the report-what-we-missed scope.
Subsequent fix rounds verified the fixes end-to-end: round 38 pins #63–#65,
round 39 pins #66–#68 (`tests/test_fix_round39.py`, 12/12), round 40 pins the
journal-sweep gap (`tests/test_fix_round40.py`). Full suite 1473 passed +
ruff clean after each round.

---

## Addendum — round 47: terminal-frame tool state and adapter flush paths (2026-09-12)

Second pass over the delta/chunk flow, the pump, and the translation layer, with
every claim below **reproduced by execution against the real classes** — not by
reading. Covers the adapter→encoder handoff at end-of-stream and the `[DONE]`
early-return family.

Fixed this round: **#111** (marked fixed in place above; it was already in the
register under that number — see the hygiene note there). The round-47
regression file is `tests/test_fix_round47.py`.

**All nine remaining findings below (#133–#141) were fixed in round 49 (2026-09-13)**,
each marked fixed in place with its fix route and the tests that pin it. Round-49
regression file: `tests/test_fix_round49.py` (46 tests). Two of the nine departed from
the original fix sketch, and both departures are recorded in the entry: #133 needed a
content-derived `Finish` the sketch omitted, and #139 was fixed by stripping the dangling
escape rather than re-encoding through `surrogatepass`.

### 133. `[DONE]`-terminated streams leave every tool call open and report the wrong stop reason

**Severity:** 🟠 High (client-visible corruption on the DeepSeek/B.A.I path round 15 added)
**File:** `wiwi/providers/openai_adapter.py:364-365` (the `[DONE]` early return),
`wiwi/core/gateway.py:1029-1035` (the synthesized `Finish`)

**Trigger:** an OpenAI-compatible upstream that closes with a bare `[DONE]` — no trailing
`finish_reason` chunk — *and* delivered tool calls. That is the exact provider class
round 15 added `saw_terminal` handling for (DeepSeek, B.A.I, and other OpenAI-compatible
servers).

`decode_stream_event` returns `[dl.StreamEnd()]` on `[DONE]` without flushing
`_open_tool_indices`/`_pending_opens`, so the stream ends with tool calls still open. The
gateway then takes the round-15 branch (`finish is None` → `dl.Finish("stop")`) and
synthesizes a **`stop`** stop reason for a turn that actually produced tool calls.

Reproduced — two parallel tool calls then `[DONE]`, driven through the real adapter:

```
adapter deltas:    ToolCallOpen, ToolCallArgsDelta, ToolCallOpen, ToolCallArgsDelta, StreamEnd
open at StreamEnd: {0, 1}
```

Client-visible consequence per surface:

| Surface | Result |
|---|---|
| Anthropic (Claude Code) | `message_delta` carries `stop_reason: "end_turn"` while two `tool_use` blocks were delivered — Claude Code concludes the turn ended **without** tool use and stops the agent loop |
| Chat | `finish_reason: "stop"` on a chunk set containing `tool_calls` |
| Responses (Codex CLI) | terminal payload carries the `function_call` items while the stop reason disagrees |

This is the **root cause #111's encoder fix only masked**: the encoders now close the blocks
the adapter left open, but the stop reason is still wrong, and the encoder's A1 guard
(`anthropic_messages.py:549-551`) cannot help — it only downgrades `tool_use` → `end_turn`,
never the reverse.

**Fix sketch:** in the `[DONE]` arm, flush open tool state before `StreamEnd` exactly as the
`finish_reason` arm already does (`openai_adapter.py:470-486`) — emit deferred
`ToolCallOpen`s, then a `ToolCallClose` per open index, then `StreamEnd`. The gateway then
receives a content-derived `Finish` and the synthesized `"stop"` branch is not taken.
The same early return exists at `openrouter_adapter.py:228` and `opencode_adapter.py:208`;
OpenRouter overrides `decode_stream_event` and needs the same treatment — its
`error`-finish arm at `:249-254` already performs this flush and is the pattern to copy.

**Status: fixed** — round 49 (2026-09-13). `OpenAIAdapter` gained a private
`_flush_open_tools()` helper (`openai_adapter.py:359-379`) that emits deferred `ToolCallOpen`s
and then one `ToolCallClose` per open index, clearing all four per-stream state dicts. The
`[DONE]` arm (`:382-395`) now calls it and appends `Finish("tool_call")` when it returned
anything, then `StreamEnd`. The `[DONE]`-after-`finish_reason` shape is unaffected: the finish
sweep at `:504-519` already cleared the state, so the flush returns `[]` and the arm degrades
to a bare `StreamEnd` — no duplicate `Finish`, no orphan `Close`. `OpenRouterAdapter`'s own
`[DONE]` arm (`openrouter_adapter.py:227-237`) calls the same inherited helper.
`OpencodeAdapter` needed no change: its `[DONE]` arm delegates to `self._sub()` for the chat
and messages routes (`opencode_adapter.py:203-209`), so it inherits the fix.
Pinned by `tests/test_fix_round49.py::test_openai_done_flushes_open_tool_calls`,
`::test_openai_done_emits_finish_tool_call_when_tools_were_delivered`,
`::test_openrouter_done_flushes_open_tool_calls`,
`::test_opencode_done_flushes_open_tool_calls_on_chat_route`, with controls
`::test_openai_done_without_tools_still_emits_bare_stream_end` (the plain-text path must stay
byte-identical so the gateway's round-15 synthesis still owns it) and
`::test_openai_done_after_finish_reason_does_not_double_close`.

Note on scope: the finding's fix sketch said to emit `StreamEnd` only and let the gateway
derive the `Finish`. That is insufficient — the gateway's `finish is None` branch synthesizes
`"stop"`, which is exactly the wrong stop reason this finding is about. The flush must be
accompanied by a content-derived `Finish("tool_call")`, which is why the arm emits it directly.

### 134. Gemini: a `usageMetadata`-bearing non-terminal chunk emits a full terminal tail, truncating the stream

**Severity:** 🔴 Critical (silent truncation, HTTP 200, no error signal)
**File:** `wiwi/providers/gemini_adapter.py:229-268` (the `elif u:` arm at `:252`, tail at `:267-268`)

**Trigger:** any Gemini stream whose **non-terminal** chunk carries `usageMetadata`. The
`elif u:` arm emits `UsageFinal` + `Finish("stop")` + `StreamEnd` for *any* frame carrying
usage, not just the terminal one.

Reproduced against the real adapter:

```
chunk0  {"candidates":[{"content":{"parts":[{"text":"Hel"}]}}],
         "usageMetadata":{"promptTokenCount":5,"totalTokenCount":5}}   (no finishReason)
     -> ['StreamStart','TextDelta','UsageFinal','Finish','StreamEnd']   <-- terminal!
chunk1  {"candidates":[{"content":{"parts":[{"text":"lo!"}]}}]}        -> ['TextDelta']
chunk2  {"candidates":[{"content":{"parts":[]},"finishReason":"STOP"}],
         "usageMetadata":{...}}                                        -> ['UsageFinal','Finish','StreamEnd']
```

The consumer breaks on the first `StreamEnd` (`wiwi/core/gateway.py:601-602`), so chunks 1
and 2 are never read: the client gets `"Hel"` with `finish_reason: "stop"` and `[DONE]` on
an HTTP 200. A full-length answer is silently cut to its first chunk, with no error and no
truncation signal.

This is the #76 fix firing on the wrong frame — #76's entry scopes it to "a Gemini SSE
response whose **terminal** candidate omits `finishReason`", and both
`tests/test_fix_round41.py:627` and `tests/test_providers.py:80` place usage on the *last*
frame, so no test covers the intermediate case. `usageMetadata` on intermediate chunks is
observed behaviour for Gemini 2.5 / Vertex: googleapis/go-genai#15 reports it on the first
chunk (empty values) and again on the last; google-gemini-php#72 reports it on every chunk
with only the final one fully populated.

**Fix sketch:** gate the arm on the frame carrying no content parts —
`elif u and not (cand.get("content") or {}).get("parts"):` — or, more robustly, buffer the
`UsageFinal` and emit the tail only at true upstream EOF.

**Status: fixed** — round 49 (2026-09-13), by the first of the two sketched routes:
`gemini_adapter.py:259` is now `elif u and not (cand.get("content") or {}).get("parts"):`, so
only a usage-bearing frame that carries **no content parts** terminates. A genuine terminal
frame (usage, empty `parts`, no `finishReason`) still emits the full tail, preserving #76.
Pinned by `tests/test_fix_round49.py::test_gemini_intermediate_usage_does_not_terminate_the_stream`,
with controls `::test_gemini_terminal_usage_still_completes_cleanly` (pins #76),
`::test_gemini_finish_reason_with_usage_still_completes`, and
`::test_gemini_usage_without_parts_but_with_candidate_text_still_continues`.

### 135. OpenRouter: `ToolCallArgsDelta` with no preceding `ToolCallOpen` when a tool chunk carries args but no `id`

**Severity:** 🟠 High (the tool call disappears entirely from every dialect)
**File:** `wiwi/providers/openrouter_adapter.py:333-339` (the `if fn.get("arguments")` arm);
the finish sweep at `:341-350` iterates only `_open_tool_indices`

Unlike `openai_adapter.py:450-461` and `nim_adapter.py:317-325`, the OpenRouter adapter has
no `elif idx not in self._open_tool_indices:` synthesize branch, so args arriving for an
index that was never opened are emitted bare.

Reproduced — same three chunks through both adapters:

```
openai      ['ToolCallOpen','ToolCallArgsDelta','ToolCallArgsDelta','ToolCallClose','Finish']
openrouter  ['ToolCallArgsDelta','ToolCallArgsDelta','Finish']
```

Traced through both encoders: `ChatStreamEncoder` emits **0 frames** (it drops args whose
index is not registered) and `AnthropicStreamEncoder` emits **0 frames**, with
`final_frame` reporting `stop_reason: "end_turn"` and no `tool_use` block. The tool call
vanishes from every dialect while the upstream still bills for it, and
`finish_reason: "tool_calls"` is downgraded to `stop`.

**De-duplication:** #74 covers only the reused-index flush (fixed); #88 covers NIM's
adoption path; #129 covers the *OpenAI* adapter's stale `_synthesized_opens` after finish.
None covers this. Git history confirms the gap: the branch was added to
`openai_adapter.py` by `f1b7cc0` and ported to `nim_adapter.py`, while
`openrouter_adapter.py`'s `if fn.get("arguments")` block dates from `2cf107a` and never
received it. `tests/test_openrouter_adapter.py` has no tool-call streaming test at all.

**Fix sketch:** port the `elif idx not in self._open_tool_indices:` synthesize branch from
`openai_adapter.py:450-461`, including `_synthesized_opens` and its `.clear()` in the
finish sweep at `:348-350`.

**Status: fixed** — round 49 (2026-09-13). `OpenRouterAdapter`'s args arm
(`openrouter_adapter.py:362-380`) gained the synthesize branch the base adapter has
(`openai_adapter.py:479-495`), and the id-first arm gained the matching adopt branch
(`openrouter_adapter.py:324-334`) so a real id arriving after a synthesized `Open` is adopted
rather than closing and re-opening the same index. Both adapters now produce identical delta
kind sequences for the same three frames. Pinned by
`tests/test_fix_round49.py::test_openrouter_args_without_id_synthesizes_open`,
`::test_openrouter_args_without_id_matches_openai_adapter` (drives both adapters with one
frame list and asserts equal kind sequences), and
`::test_openrouter_late_id_after_synthesized_open_is_adopted`, with control
`::test_openrouter_id_first_then_args_unchanged`.

**Second defect found while fixing this one, same entry:** the synthesize branch stored the
name fragment in `_tool_names[idx]` but emitted the Open with `name=""`. The first write-up
said "in *both* adapters" — that was a claim about the search, not about the codebase: a third
copy-derived site existed at `nim_adapter.py:332` and was missed because the write-up scoped
itself to the two adapters that had been opened. See **#143** (fixed round 51). The three
synthesize branches are copy-derived, so "fixed one" is a prompt to check the others. The unit tests passed anyway (they
asserted the Open existed and was correctly nested, not what it carried); the real-TCP harness
caught it, because `AnthropicStreamEncoder` renders the Open as a `tool_use` block and a block
with `name: ""` cannot be dispatched by the client. Fixed to emit
`name=self._tool_names[idx]` (`openai_adapter.py:487-495`, `openrouter_adapter.py:370-378`),
the emit sites being `openai_adapter.py:494-495` and `openrouter_adapter.py:378-379`,
and the unit tests tightened to assert the name
(`::test_openrouter_args_without_id_synthesizes_open`,
`::test_openai_synthesized_open_carries_the_tool_name`). Verified over TCP by
`.verify/e2e/run_e2e.py` → "args-without-id synthesizes the tool_use block (#135)".

**Why the unit test missed it, and why "add an encoder assertion" is the wrong lesson:** the
round-49 unit tests assert *delta shape* (which kinds, in what order, correctly nested) and
that is what the finding was about. The tempting conclusion is "assert on the encoded output
instead" — but the e2e file **already had** exactly that assertion
(`run_e2e.py` "tools: tool_use block opens with the upstream tool name" decodes the Anthropic
SSE and checks the rendered `tool_use` block's name), and it still passed against the broken
code. Verified by reverting the fix: the `fake-tools` assertion stays green while the
`fake-argsnoid` one goes red.

The reason is that `fake-tools` sends a real `id` on its first chunk, so it takes the
*id-first* arm and never enters the synthesize branch at all. So the discriminating question is
not "how strong the assertion is" but **"does any fixture drive the specific branch"**. An
encoder-and-back assertion is only as good as the arm its fixture reaches; assertion strength
and branch coverage are independent axes, and it is the second one that failed here.

The cheapest technique of the three, and the one that would have caught the #143 site in the
same breath: **grep for the shape of the bug you just fixed.** `grep -n 'name=""'
wiwi/providers/*.py` returns every copy-derived instance at once, at no cost, where both
running tests and strengthening assertions depend on a fixture reaching the arm. Worth running
over the `synthesize` / `adopt` / `_synthesized_opens` families whenever one of them changes.

Applying that to this file: `fake-tools` covers the id-first arm and `fake-argsnoid` the
synthesize arm, so the pair is complete — but any *third* tools fixture must pick its arm
deliberately rather than assuming the "tools" path is covered by the one that exists.

### 136. Gemini and NIM: a non-dict SSE frame raises `AttributeError` out of the decoder

**Severity:** 🟡 Medium (junk frame cools a healthy key and feeds the retirement ladder)
**File:** `wiwi/providers/gemini_adapter.py:199` (`payload.get("error")`, parsed at `:194-197`
with only a `JSONDecodeError` guard) and `wiwi/providers/nim_adapter.py:237` (`chunk.get("usage")`,
parsed at `:229-232`)

**Trigger:** any non-dict JSON frame — `null`, a number, a string, an array.

Reproduced — `decode_stream_event("", junk)` for each:

```
GeminiAdapter   null / 42 / "hi" / [1,2,3]  -> RAISED AttributeError
NimAdapter      null / 42 / "hi" / [1,2,3]  -> RAISED AttributeError
OpenRouterAdapter, OpenAIAdapter            -> []          (control: #110 guard)
```

The exception lands in `_pump_once`'s generic handler (`gateway.py:1071-1089`) →
`_note_stream_failure` (deployment cooldown + key `err_count`, feeding the #69 retirement
ladder) plus a `StreamError` to the client — for a frame carrying no semantic content.
Reachable for Gemini directly and via `OpencodeAdapter`'s gemini route
(`opencode_adapter.py:211` → `GeminiAdapter`), which raises identically.

**De-duplication:** #110's file list names only `openai_adapter.py`,
`openrouter_adapter.py`, and `openai_responses.py`; `tests/test_fix_round43.py:825-839`
covers only openrouter and openai. No gemini or nim junk-frame test exists.

**Fix sketch:** `if not isinstance(payload, dict): return []` after the parse at
`gemini_adapter.py:197`; `if not isinstance(chunk, dict): return []` after the parse at
`nim_adapter.py:232`.

**Status: fixed** — round 49 (2026-09-13). Both decoders gained the `isinstance(x, dict)`
guard their `OpenAIAdapter` counterpart already had (`openai_adapter.py:400-403`):
`gemini_adapter.py:198-203` and `nim_adapter.py:233-238`. A non-dict frame now yields `[]`
instead of raising `AttributeError` into the pump's generic handler. `OpencodeAdapter`'s
gemini route inherits the fix through `self._gem`. Pinned by
`tests/test_fix_round49.py::test_gemini_non_dict_frame_is_ignored`,
`::test_nim_non_dict_frame_is_ignored`, and
`::test_opencode_gemini_route_non_dict_frame_is_ignored`, each parametrized over
`null` / `42` / `"hi"` / `[1,2,3]`.

### 137. `OpenAIAdapter._emitted_opens` is write-only state that no code reads

**Severity:** ⚪ Low (dead state plus a comment that misdirects a future fixer)
**File:** `wiwi/providers/openai_adapter.py:348` (init), `:361` (reset), `:416` (write),
`:486` (clear)

The dict is written once, in the "adopt the real id" branch, under a comment stating the id
is recorded "for later frames (tool_result correlation)". Nothing ever reads it:
`grep -rn "_emitted_opens"` returns only the four sites above — one write and two clears
(the fourth is the init). The `ToolCallOpen` that branch suppresses was already emitted with
`id=""`, so the real id never reaches any encoder by this path; the chat encoder emits
`"id":""` to the client (verified by execution).

Either the correlation feature is genuinely wanted — in which case the id has to be
surfaced, since the plumbing is missing, not just the reader — or the field should be
deleted and the comment corrected so a later agent does not trust it. Same disposition class
as the `partial_json` / `iter_sse_events` items at the end of the streaming addendum:
deliberately kept, or removed, but not left as an unmarked write-only field.

**Status: fixed** — round 49 (2026-09-13), by the **delete** route. The field was removed
outright (`openai_adapter.py` — dropped from `__init__`, `reset`, the adopt-the-real-id branch,
and the finish sweep). It was never read anywhere in the tree (`grep -rn _emitted_opens`
returns only its own four references), so "surface the id" would have meant inventing a
consumer, not restoring one: the correlation the comment described is already served by
`_tool_names` plus the emitted `ToolCallOpen` deltas the caller sees. Deleting is the smaller
change and leaves no unmarked dead state. Pinned by
`tests/test_fix_round49.py::test_emitted_opens_is_not_write_only_dead_state`, which parses the
module AST and fails if any `Load`-context reference to the name reappears.

### Sweep coverage — checked, no defect found

- **`bai_adapter.py`** — overrides only `encode_request`; `decode_stream_event` is inherited
  from `OpenAIAdapter` with its #110 guards intact.
- **`cline_adapter.py`**, **`workbuddy_adapter.py`** — the `{success,data}` unwrap and
  `{"error":{...}}` branches `isinstance`-guard; `null`/`42`/`"hi"`/`[1,2,3]` all return
  `[]` without raising. (A suspected `_HDR_RE` corruption was chased and **ruled out** — a
  byte dump confirmed the regex is correct and it behaves on multi-line/code-block input.)
- **`opencode_adapter.py`** — its own Responses route guards with `isinstance`; no new
  defect.
- **Reasoning/CoT leakage** — clean on all routes; no path emits reasoning as `TextDelta`.
- **Usage field mapping** — consistent across gemini/nim/openrouter/openai.
- **Terminating with a tool open** — every terminal path closes open indices *except* the
  `[DONE]` family in #133.

### Verification method

Every finding above was reproduced by direct execution against the real adapter and encoder
classes, with the checkout pinned (`.verify/` scripts put their own directory on
`sys.path[0]`, and the editable install resolves `wiwi` to a *different* checkout — a probe
run that way silently tests the wrong tree; see the note below). The round-47 fixes are
pinned by `tests/test_fix_round47.py` (3 tests, RED against pre-fix source, GREEN after).

**Harness hazard worth recording:** `python3 path/to/script.py` sets `sys.path[0]` to the
*script's* directory, not the cwd. With the ambient editable install resolving `wiwi` to
another checkout, a probe under `.verify/` imports the wrong tree and its output is
meaningless — this produced a false "the fix doesn't work" reading during round 47. Run
scratch probes as `PYTHONPATH=<repo> python3 -B script.py`, or via stdin (`python3 - <<PY`),
which puts the cwd on `sys.path[0]`.

### 138. A legal JSON Schema `type` array crashes the stream pump mid-response

**Severity:** 🟠 High (client-visible truncation + raw Python exception text; a healthy key
accumulates failures because of a client-controlled schema shape)
**File:** `wiwi/streaming/validation.py:127` (`_check_type`, `python_type = type_map.get(expected)`),
reached from `:63` (top-level type) and `:87` (per-property type); called at
`wiwi/core/gateway.py:1159` inside `_validate_closed_tool_args`, invoked from `_apply_delta`
at `gateway.py:950-951`

**Trigger:** a request whose tool schema uses the union/nullable form — `{"type":
["object","null"], ...}`, or any property `{"type": ["string","null"]}`. That is the
canonical nullable encoding emitted by OpenAI structured outputs, Pydantic v2, and Claude
Code's own tool definitions. Upstream returns a tool call; at `ToolCallClose` the `list` is
hashed by the `type_map.get(...)` lookup.

Reproduced against the real validator:

```
union prop      -> RAISED TypeError: unhashable type: 'list'
top-level union -> RAISED TypeError: unhashable type: 'list'
```

Reached end-to-end through the real app with a respx-mocked upstream: HTTP 200 plus three
SSE frames, then `data: {"error":{"message":"unhashable type: 'list'","type":"api_error"}}`
— no `finish_reason`, no `[DONE]`, and the tool-call `arguments` truncated mid-string. The
non-streaming path raises the same `TypeError` → 500.

The enclosing `except Exception` at `gateway.py:1071-1082` classifies it as a mid-stream
*provider* failure: `_note_stream_failure` runs `dep.record_fail(...)` and
`on_result_locked(key, 502, ...)`, so a healthy deployment and provider key accumulate
failure counts and can be retired — because of a shape in the caller's own schema.
`_price_partial` bills the partial delivery. The schema is entirely caller-controlled, so
any authenticated caller can force this at will.

**Fix sketch:** in `_check_type`, treat a non-str `expected` as a union —
`if not isinstance(expected, str): return True`, or
`any(_check_type(value, e) for e in expected)`.

**Status: fixed** — round 49 (2026-09-13). `_check_type` (`validation.py:122-152`) now
branches on the expected type's *shape* before hashing it: a `list` is treated as a union
(`return any(_check_type(value, member) for member in expected)`), a non-`str` is
unconstrained, and only a `str` reaches the `type_map` lookup. This also covers the
top-level `type` (`validation.py:63`), which had the same crash for
`{"type": ["object","null"]}`. Pinned
by `tests/test_fix_round49.py::test_union_type_array_in_property_does_not_crash`,
`::test_union_type_array_at_top_level_does_not_crash`,
`::test_union_type_array_accepts_null_for_nullable_property`, with control
`::test_union_type_array_still_rejects_a_real_mismatch` (treating a list as a union must not
disable checking).

### 139. `_repair_truncated_json` emits a lone surrogate, producing args that cannot be serialized

**Severity:** 🟡 Medium (dialect-incorrect 500; the user's turn is lost)
**File:** `wiwi/streaming/partial_json.py:89` (`suffix += _QUOTE`), in `_repair_truncated_json` (`:37`)
**Consumers:** `gateway.py:428`, `:475`, `streaming/resume.py:181,193`,
`providers/openai_adapter.py:309`, `providers/openrouter_adapter.py:201`,
`wire/openai_chat.py:103`, `wire/openai_responses.py:44`

**Trigger A (live):** the upstream truncates a tool-args fragment mid-surrogate-pair —
`{"emoji": "\ud83d` — the exact shape a token-limit cut of a non-ASCII string produces. The
repair closes the string and `json.loads` accepts it, but the value is a lone surrogate.
Reproduced for a high-surrogate cut, a mid-low-surrogate cut, and a bare low surrogate:

```
'{"emoji": "\ud83d'      -> '{"emoji": "\ud83d"}'      -> orjson TypeError
'{"emoji": "\ud83d\ude'  -> '{"emoji": "\ud83d"}'      -> orjson TypeError
'{"emoji": "\ude00'      -> '{"emoji": "\ude00"}'      -> orjson TypeError
```

**Trigger B (fully reproducible today, no upstream cooperation needed):** a client replays
that same truncated `arguments` string in its next-turn history — which is exactly what a
client that received the truncated call does. `wire/openai_chat.py:103` repairs it to a lone
surrogate, the IR keeps it in `ToolUsePart.args`, and on an Anthropic-routed model
`providers/anthropic_adapter.py:307` puts `p.args` into the outbound body where httpx
`encode_json` (`ensure_ascii=False`) raises before the request is sent. Verified end-to-end:
`/v1/chat/completions` → HTTP 500 `{"error":{"message":"internal gateway error",...}}`, with
the traceback through `httpx/_content.py:179 encode_json`. The openai/openrouter adapters
survive only because they prefer `raw_args`; the Anthropic path does not, and neither would
the resume/continuation path. A surrogate reaching `_serialize_turn` (`app.py:1088-1104`)
would also break the response-cache write (`orjson.dumps`, `app.py:1323`).

**Fix sketch:** normalize in the block that already strips a partial `\uXXXX` tail
(`partial_json.py:86-88`) — drop a trailing high/low-surrogate escape, or
`text.encode("utf-8","surrogatepass").decode("utf-8","replace")`.

**Status: fixed** — round 49 (2026-09-13), by the **strip** route rather than the sketched
`surrogatepass` re-encode. `_repair_truncated_json` (`partial_json.py:89-110`) now inspects a
*complete* `\uXXXX` escape at the tail: a high surrogate (`U+D800`–`U+DBFF`) is always dangling
and is removed, and a low surrogate (`U+DC00`–`U+DFFF`) is removed unless a high surrogate
escape immediately precedes it. The `surrogatepass` re-encode would have replaced the lone
half with `U+FFFD` *inside* the value; stripping removes the unusable partial pair entirely,
which is what a truncated escape deserves — the emoji never arrived, so no replacement
character is owed. Complete pairs and escaped backslashes are untouched. Pinned by
`tests/test_fix_round49.py::test_repaired_truncated_json_is_serializable` (parametrized over
the high-half, mid-low-half, and bare-low-half cuts, asserting `json.loads` then
`orjson.dumps` both succeed), `::test_lone_surrogate_is_actually_removed_not_merely_escaped`,
with control `::test_valid_escapes_are_not_mangled_by_the_surrogate_fix`.

### 140. The #68 eviction guard is dead at its only call site

**Severity:** 🟡 Medium (silent resume-prefix corruption; latent — `stream_resume` defaults to `"off"`)
**File:** `wiwi/core/gateway.py:652` (`if tape.head_evicted(tape.seq - 1):`);
`wiwi/streaming/resume.py:48-51` (`seq`), `:88` (`head_evicted`)

`tape.seq` is the **next** sequence number to assign, and the tape only evicts from the head,
so `_entries[0].seq <= tape.seq` always holds. `head_evicted(last_seq)` returns
`first > last_seq + 1`, i.e. the call site tests `first > tape.seq` — **structurally always
False**. Reproduced: a 60-byte tape fed 20 deltas keeps entries `[13..20]`, so
`head_evicted(0)` is `True` while the call site's `head_evicted(tape.seq - 1)` is `False`,
and `replay_text()` returns 56 of 140 chars. Brute-forced across 995 evicting tapes
(1–199 deltas × 5 byte caps): **0 fires**.

**Trigger:** `stream_resume != "off"` (default `"off"`, so latent today), a mid-stream
upstream failure after output exceeds the 256 KiB tape, and a tool call opened before the
evicted range. `build_continuation_messages` then produces a text-only continuation with
`replay_tool_calls() == []` — the tool call is dropped from the assistant prefix, so the
resumed model is asked to continue without a call the client already saw and executed
(duplicate side effects, or a continuation disjoint from the delivered prefix). No billing
effect.

**Fix sketch:** track the last seq the consumer actually yielded and pass that (or pass `0`);
`head_evicted(last_consumed)` is the check the function's own comment describes.

**De-duplication:** #68 is marked fixed (round 39) and #84 is a declared false positive
(`AUDIT.md:124-129`, `1243-1258`) — both address the *function*; neither records that its
sole caller passes an argument that can never trip it. `tests/test_fix_round39.py:139-190`
and `tests/test_fix_round41.py:506-528` call the function directly and never exercise
`_attempt_resume`'s argument.

**Status: fixed** — round 49 (2026-09-13). `gateway.py:660` now passes `0` — "replay from the
very beginning" — which is the question the guard actually asks: whether anything *before* the
first surviving seq was dropped. `tape.seq` is the **next** number to assign, so the old
`tape.seq - 1` was by construction `>=` the first surviving seq and the predicate could never
be true. Verified discriminating: reverting the argument alone turns
`tests/test_fix_round49.py::test_attempt_resume_refuses_when_the_tape_head_was_evicted` red
(`assert True is False`). Pinned alongside
`::test_head_evicted_guard_fires_for_the_last_consumed_seq` and control
`::test_attempt_resume_still_proceeds_when_the_tape_is_intact` (the guard must not refuse every
resume).

### 141. Typeless properties are always rejected (validation false positives)

**Severity:** ⚪ Low (advisory only today; becomes client-visible if the check is promoted)
**File:** `wiwi/streaming/validation.py:87` (`if not want or not _check_type(value, want):`)

**Trigger:** any property schema with no top-level `type` — description-only, enum-only,
anyOf-only, `$ref`-only, or const-only. Reproduced for all five shapes:

```
validate_tool_args('t', '{"a": "x"}',
    {"type":"object","properties":{"a":{"description":"a path"}}})
-> (False, "tool 't': property 'a' expected None, got string")
```

Absent `type` means *unconstrained*, not "must be null" — `_check_type(value, None)` returns
`True`, but the `not want` short-circuit rejects before that is ever consulted.

**Impact today:** advisory only. The message goes to `ctx.metadata["tool_args_violations"]`
via `_flag` (`gateway.py:39-41`) and never reaches `build_log_event`
(`gateway.py:1276-1310`) or any DB/SSE/metric surface — so it is one misleading
`tool_args_property_type_mismatch` proxy-log line per call, plus a flag nothing reads. It
becomes client-visible the moment that metadata is surfaced or the check is promoted to a
hard failure, and `validation.py:78-80` records that the per-property check was itself added
as a correctness fix — so promotion is a plausible next step.

**Fix sketch:** `if want and not _check_type(value, want):` — absent `type` is unconstrained.

**Status: fixed** — round 49 (2026-09-13), by dropping the short-circuit entirely rather than
adding `want and`: `validation.py:94` is now `if not _check_type(value, want):`. The sketch
would have fixed typeless properties but still mis-handled a union, which `_check_type` now
resolves (#138) — and `_check_type(value, None)` already returns `True` for an unknown type,
so the `not want` clause only ever served to reject first. A declared-type mismatch is still
rejected. Pinned by `tests/test_fix_round49.py::test_typeless_property_is_not_rejected`,
`::test_all_typeless_property_shapes_pass` (parametrized over description-only, enum-only,
anyOf-only, `$ref`-only, and const-only specs), with control
`::test_declared_property_type_is_still_enforced`.

### Support-module sweep coverage — verified clean

- **`streaming/tape_store.py`** — seq numbering (`_last_seq` advances only for non-`done`
  records), `_read_records`'s `seq <= last_seq` filter, `is_complete` (uses `0`, correct since
  data starts at seq 1), `_overflow` byte-cap semantics, torn-line tolerance, sweep lifecycle.
  (Blocking FS I/O on the request path is already #105.)
- **`streaming/coalesce.py`** — no drop and no reorder is possible: buffered text is emitted
  only when a later delta arrives, and both the fast path (`:52-56`) and the non-mergeable
  branch (`:69-73`) call `_flush()` *before* appending the control delta; `drain()` runs in
  the generator's post-loop (`gateway.py:603-606`) before any terminal frame.
- **`streaming/loopdetect.py`** — fires at exactly `limit` chunks for periods 1–8; the
  period-9+ misses are the documented deliberate cap. No false positive on repeated
  whitespace/newlines/markdown-table/code-indentation text at the shipped limit of 100.
- **`server/app.py` streaming plumbing** — terminal-frame logic at `_stream_response`
  (`:1444-1469`) is correct on every path: error path emits only `message_stop` for the
  Anthropic style (no `final_frame()`, no `[DONE]`); chat emits `final_frame()` then `[DONE]`;
  responses emits `_completed()`. No double terminal frame, no `[DONE]` after an error, and
  `final_frame()` is reached on every success path.
- **`_apply_delta` forwarding** (`gateway.py:909-953`) — forwards every non-control delta;
  no delta type is dropped or reordered.

---

## Addendum — round 48: token-accounting honesty (2026-09-12)

Two new findings, both fixed this round and pinned by `tests/test_fix_round48.py`.
Both are the same failure mode in different places: **wiwi presented a guess as a
measurement.** The register entry that round 48 also resolved is **#121** (above,
marked fixed in place — it had been rediscovered as a duplicate "#132").

### 130. `/v1/messages/count_tokens` counted only `TextPart` — tool schemas and replayed agentic history were free

**Severity:** 🟠 High (client-visible undercount; a client sizing its context is lied to)
**File:** `wiwi/server/app.py:1538-1560` (pre-fix body)

**Trigger:** any request to the Anthropic `count_tokens` surface whose prompt is not pure
text — i.e. every agentic client. The pre-fix body walked `ir_req.messages` and summed
`len(p.text) // 4 + 1` over `TextPart` instances only, so four part kinds contributed
**zero**: `ToolUsePart`, `ToolResultPart`, `ThinkingPart`, and — the largest omission —
`ir_req.tools`, the tool *schemas*, which are serialized into every upstream request and
are a dominant share of a real Claude Code prompt.

Two defects in one endpoint: an incomplete walk, and a private estimator. `len//4 + 1`
is not `estimate_tokens` (tiktoken where available), so the number the client was told
disagreed with the number wiwi itself uses internally for identical text.

Measured end-to-end on a Claude-Code-shaped payload (3 tool schemas × 15 params, a
`thinking` block, a `tool_use`, a `tool_result`):

| | `input_tokens` |
|---|---|
| pre-fix (TextPart only) | **7** |
| post-fix (shared estimator, all parts) | **2270** |

A 324× undercount. The endpoint and the gateway's own streaming-fallback estimator now
report the identical figure for the same request, which is the property that matters:
the number shown to the client equals the number wiwi bills against when upstream omits
usage.

**Fix:** the endpoint builds a `RequestContext` and counts
`await estimate_tokens_async(flatten_request_text(ctx), ir_req.model)` — the same helper
the streaming fallback uses. `flatten_request_text` (formerly the private `_flatten`) was
promoted to a public name because it now has a consumer outside `core/gateway.py`;
importing a private symbol across a module boundary would have violated Import Rule 4.
Its coverage was extended in the same pass to include tool schemas and the
`raw_args`/`name` of `ToolUsePart`, plus non-text payload references
(`ImagePart.url`, `DocumentPart.context`) so multimodal content is not free either.

Pinned by `tests/test_fix_round48.py`: `test_count_tokens_includes_tool_schemas`,
`test_count_tokens_includes_tool_result_and_thinking`,
`test_count_tokens_uses_the_shared_estimator`, and the
`test_count_tokens_empty_is_at_least_one` control. `tests/test_bugfix_round5.py` was
mechanically updated for the rename (import line plus three call sites).

### 131. The `estimated` flag was write-only — logs, stats, metrics and the DB presented estimated token counts as provider-reported fact

**Severity:** 🟠 High (a spend report cannot distinguish measured traffic from guessed traffic)
**File:** `wiwi/ir/types.py:288-293`, `wiwi/core/gateway.py:1299`,
`wiwi/logging_core/events.py:30-33`, `wiwi/logging_core/db_sink.py`,
`wiwi/server/stats.py`, `wiwi/server/metrics.py`

**Trigger:** any stream where upstream omits usage, so the fallback estimator fills it in.
`UsageFinal.estimated` was set by the estimator and carried into `ir.Usage` — as
`reasoning_estimated`, a name that was itself wrong, since the fallback estimates the
*whole* usage (prompt included), not just reasoning. Then nothing read it. `LogEvent` had
no estimated field at all, so from the DB onward the two were indistinguishable: an
estimated `tok_in`/`tok_out` looked exactly like a provider-reported one in
`/admin/logs/requests`, `/admin/stats/overview`, `/metrics`, and the `requests` table.

**Fix:** four layers, each mirroring the existing `response_cache_hit` plumbing.

1. `ir.Usage.reasoning_estimated` → **`estimated`**, with the comment corrected. Safe
   because `grep` showed **zero read sites** — one definition, one writer, no readers —
   and `cache/keygen.py` walks `__dataclass_fields__` only over
   `messages/tools/tool_choice/gen_params` (`:66-76`), never `Usage`, so no cache key
   changes.
2. `merge_resume_context` propagates the flag: `ou.estimated = ou.estimated or ru.estimated`.
   Without it a merged total containing estimated tokens was reported as provider-reported
   — in exactly the mid-stream-failover path where upstream has already misbehaved.
3. `LogEvent.usage_estimated` (default `False`), set in `build_log_event`.
4. The field is plumbed to every surface: both DDLs plus the `_migrate()` ALTER list and
   `_COLS` in `db_sink.py`, `overview["estimated_requests"]` in `stats.py`, and
   `wiwi_usage_estimated_requests_total` in `metrics.py`.

Verified through the real app: `overview.estimated_requests=1`, `usage_estimated=1` on the
estimated row and `0` on the reported one, and
`wiwi_usage_estimated_requests_total 1` — the flag survives
`LogEvent → DB → admin API → stats → Prometheus`.

Pinned by `tests/test_fix_round48.py`: `test_ir_usage_has_a_general_estimated_flag`,
`test_log_event_records_estimated_usage`, `test_metrics_expose_estimated_requests`,
`test_merged_resume_usage_stays_estimated` (with a clean-halves control),
`test_db_round_trips_the_estimated_flag`.

### Verification method

Both fixes were verified by execution against the real app, not by reading — see the
measured 7 → 2270 `count_tokens` table under #130 and the four-surface `usage_estimated`
trace under #131. RED/GREEN was established by running `tests/test_fix_round48.py`
against the pre-fix source (12 tests; every new assertion fails pre-fix, and the three
controls pass in both trees, so no fix overcorrects). Full gate after the round:
`1587 passed`, `ruff check wiwi/ tests/` clean.

**Harness hazard worth repeating** (same one recorded under round 47): `python3
path/to/script.py` sets `sys.path[0]` to the *script's* directory, not the cwd, and the
ambient editable install resolves `wiwi` to another checkout — so a scratch probe run
that way tests the wrong tree. Run probes via stdin (`python3 - <<PY`) or with an
explicit `PYTHONPATH=/teamspace/studios/this_studio/wiwia`.

## Addendum — round 50: budget refusals answered with the wrong status (2026-09-13)

Found by running the gateway end to end against a fake upstream over real TCP
(`.verify/e2e/`), not through the ASGI test client — every unit test in the
suite drives `httpx.ASGITransport`, which never exercises the wire the way a
real client does, and this defect is invisible from inside it.

### 142. A key at its budget cap is refused with `429 budget_exceeded`, while the post-hoc path refuses the same condition with `402`

**Severity: 🟠** — wrong status code on a client-visible error path; drives
incorrect SDK retry behaviour.

`wiwi/server/app.py:938-941` (pre-fix), in `authenticate`:

```python
        if info.over_budget:
            return None, _err(429, "budget_exceeded",
                              f"budget exhausted ({info.spend_to_date:.4f}"
                              f"/{info.max_budget})", request, surface)
```

`wiwi/server/app.py:1313-1320`, the post-hoc path in `run_chat_like`:

```python
                if not recorded:
                    ctx.status = 402
                    state_.logs.log_request(build_log_event(ctx))
                    return _err(402, "budget_exceeded",
                                "virtual key budget exhausted", request, surface)
```

`info.over_budget` is `max_budget is not None and spend_to_date >= max_budget`
(`wiwi/auth/service.py:81-82`) — the *same* condition the post-hoc branch
enforces via `update_spend` returning `False`. One condition, two status codes,
decided by which check happens to see it first.

**Trigger:** any key already at or over its cap at admission time. Two ordinary
routes reach it: a key minted with `max_budget=0` (refused on its very first
request), and — the common production case — any key whose *previous* request
pushed `spend_to_date` past `max_budget`, so every subsequent request is refused
by the pre-flight check for the rest of the key's life.

**Why 429 is wrong here, not merely inconsistent:**

1. Three documents pin the contract. `docs/API_REFERENCE.md:203` — "`401` (bad
   key), `402` (budget cap exceeded), `404` (unknown model/group), `413` (body
   too large), `429` (rate limit)". `docs/ADMIN.md:98` — "exceeding a cap yields
   `402` on subsequent requests". `docs/ARCHITECTURE.md:58` — "update spend
   (budget cap → 402)". All three describe exactly this pre-flight case and all
   three say 402.
2. 429 means *rate limit* to every SDK and proxy in the ecosystem. It is the one
   status that carries `Retry-After` and that clients back off on. A budget cap
   never clears on its own — telling a caller to retry is telling them to spin.
3. `docs/MVP.md:104` keeps the two conditions deliberately distinct in the
   user-facing spec: "the 61st request in a minute gets a clean `429`, and
   further requests get `budget_exceeded` after $10."

**Fix:** return 402 from the pre-flight branch. The `type` stays
`budget_exceeded` and the message is unchanged, so only the status moves.

```python
        if info.over_budget:
            # 402, not 429 (AUDIT #142): the post-hoc check below refuses the
            # same condition with 402, and docs/API_REFERENCE.md, docs/ADMIN.md
            # and docs/ARCHITECTURE.md all pin "budget cap exceeded" to 402
            # while reserving 429 for rate limits. 429 also tells an SDK to
            # back off and retry — wrong advice for a cap that never clears.
            return None, _err(402, "budget_exceeded",
                              f"budget exhausted ({info.spend_to_date:.4f}"
                              f"/{info.max_budget})", request, surface)
```

**Why the existing suite could not see it.** The only budget assertion in the
tree is `tests/test_fix_round45.py:234-238`, which mints the key at
`max_budget=1e-9`. On the first request `spend_to_date` is `0.0`, and
`0.0 >= 1e-9` is `False` — so the pre-flight check *passes* and the request is
caught by the post-hoc 402 instead. The test therefore pins the post-hoc branch
while the pre-flight branch (the one that fires on every subsequent request) was
never exercised. `grep -rn 'max_budget=0' tests/` returns only that same
`1e-9`-valued test.

**Pinned by** `tests/test_fix_round50.py`:
`test_preflight_over_budget_is_402_not_429` (RED pre-fix: got 429, expected 402),
`test_posthoc_over_budget_still_402` (control — the other branch must not be
"fixed" in the opposite direction; note it needs pricing set, or the unpriced
model costs 0 and `update_spend` succeeds with a plain 200), and
`test_rate_limit_is_still_429` (control — a genuine rate limit keeps 429 *and*
`Retry-After`).

**Not filed as a new number for the neighbouring entries.** #90 (over-budget 402
logged exactly once) and #116 (a 402'd response must never be cached) both touch
the post-hoc branch and both remain correct; this is the *other* branch.

### Verification method

End-to-end, over real TCP against a fake OpenAI-compatible upstream
(`.verify/e2e/`, gitignored): `PYTHONPATH=/teamspace/studios/this_studio/wiwia
timeout 300 python3 -B .verify/e2e/run_e2e.py`. The budget assertion in that
harness now reads `status=402 body={"error":{"message":"budget exhausted
(0.0000/0.0)","type":"budget_exceeded",...}}` where it previously read 429.

Two other failures in that run were harness faults, not product defects, and are
recorded here so the next agent does not re-diagnose them:

- **Rate-limit assertion measured the cache.** `cache_settings.enabled: true` is
  global, and the harness sent five byte-identical bodies, so all five were
  served from wiwi's response cache and never reached the limiter. Fixed by
  giving each request unique content; the section now reports
  `codes=[200, 200, 429, 429, 429]`.
- **`fake-slow` had no deployment.** The model was never added to `model_list`
  (404), and separately the `fake-fail` test cooled the *only* provider account
  — with `num_retries: 0` nothing revives it, so the disconnect test was
  answering `503 no healthy deployment` (`wiwi/router/router.py:885`). Fixed by
  adding `fake-slow` to `model_list`, giving `fake-fail` its own provider
  account, and asserting the slow request actually reached upstream before
  checking the gateway still serves.

## Addendum — round 51: the third site of the synthesized-Open name defect (2026-09-13)

### 143. `NimAdapter`'s args-before-id synthesize branch emits `ToolCallOpen(name="")` — the third adapter carrying the #135 second defect

**Severity: 🟠** — the tool call reaches the client undispatchable, while the
upstream bills for it.

`wiwi/providers/nim_adapter.py:332` (pre-fix), in the `elif idx not in
self._open_tool_indices:` arm:

```python
                    self._open_tool_indices.add(idx)
                    self._tool_names[idx] = name_fragment or ""
                    self._synthesized_opens.add(idx)
                    out.append(dl.ToolCallOpen(index=idx, id="", name=""))
```

The name fragment is captured on the line above and then discarded: the Open is
emitted with a literal empty name. `wiwi/wire/anthropic_messages.py`'s
`StreamEncoder` renders that Open as a `tool_use` content block, and a block
with `name: ""` cannot be dispatched by any client — the call is lost while the
provider still charges for it. Same defect, same shape, as the second defect
recorded under **#135** (`openai_adapter.py` and `openrouter_adapter.py`, both
fixed in round 49); NIM is the third site and was missed because that entry
scoped itself to "both adapters".

**Measured**, one identical frame (name + args, no id) through both adapters:

```
NimAdapter     -> [('', '')]
OpenAIAdapter  -> [('', 'get_weather')]
```

**How it was found — worth recording, because no test run found it.** After the
round-49 fix landed, `grep -n 'name=""' wiwi/providers/*.py` was run to confirm
the fix had removed every instance. It returned one hit: this line. The defect
was located by grepping for the *shape* of a just-fixed bug rather than by
exercising anything — the sibling branches in three adapters are copy-derived,
so a fix in one is a prompt to check the others. The same grep over the
`synthesize`/`adopt`/`_synthesized_opens` families is the cheap check to run
whenever one of them changes.

**Fix:** emit `name=self._tool_names[idx]`, matching `openai_adapter.py:495` and
`openrouter_adapter.py:379`.

**Reachability:** the branch fires when a tool chunk carries `arguments` but no
`id` — the AUDIT #88 shape, for which the synthesize branch exists precisely
because NIM's vLLM backend can emit args before (or entirely without) an id. A
nameless chunk (args, no name either) still correctly synthesizes `name=""`;
the fix stops *discarding* a name that was sent, it does not invent one.

**Pinned by** `tests/test_fix_round51.py`:
`test_nim_synthesized_open_carries_the_tool_name` (RED pre-fix),
`test_nim_synthesized_open_matches_openai_adapter` (control-by-parity — drives
both adapters with one frame and asserts equal Opens, which is the assertion
that catches this class of divergence without knowing which side moved), and
`test_nim_nameless_synthesized_open_stays_empty` (control — a genuinely
nameless chunk must still open, so the fix does not overcorrect into inventing
a name).

**Not a duplicate of #88.** #88 covers NIM's *adoption* of a later real id after
a synthesized Open (`_synthesized_opens` was never populated at all); this is
what the synthesized Open itself carries. Both live in the same arm; #88's fix
populated the set, this one fixes the payload.

## Addendum — round 52: scoped pricing, and two defects it uncovered (2026-09-13)

### 144. The retroactive repricer's serving-attempt rule never fired on real data

**Severity: 🟠** — historical rows were repriced against the wrong provider's
rate (or not at all), silently, and the test suite was green throughout.

`wiwi/logging_core/db_sink.py:330` (pre-fix), in `_row_matches`:

```python
serving = next((a for a in reversed(entries)
                if isinstance(a.get("status"), int)
                and 200 <= a["status"] < 300), entries[-1])
```

`AttemptRecord.status` (`wiwi/core/context.py:18-24`) is a **string** — `"ok"`,
`"ok_after_refresh"`, `"http_429"`, `"TimeoutException"`, `"encode_error"` —
set at `wiwi/core/gateway.py:235,245,293,339,350,488,777,809` and serialized
verbatim into the log row at `:1312`. `isinstance("ok", int)` is False, so the
2xx predicate matched **nothing** in production and `next(...)` always fell
through to its `entries[-1]` default: the *last* attempt, not the serving one.
Whenever a success was followed by a failed retry or a fallback, the row was
matched against the wrong deployment — repricing it at another model's rate, or
skipping it.

Every fixture in `tests/test_fix_round32.py:256-279` hand-builds `"status": 200`
as an **int**, so the dead branch was the only one the suite ever exercised.

**Fix:** replaced `_row_matches` with `_serving_attempt`, which classifies
through `_is_2xx` — accepting both the string form the gateway actually writes
and the int form the fixtures use, and excluding `bool` (an `int` subclass).
Still last-2xx, never first: a 200 whose body fails to decode is recorded `"ok"`
at `gateway.py:488` *before* `_decode_response_guarded` runs and is then
retried, so a later attempt is the one that delivered.

**Status: fixed** — with scoped pricing (below), a wrong serving attempt no
longer just misprices a row; it picks the wrong provider's rate entirely.

**Proof (RED/GREEN).** `tests/test_fix_round52.py::test_repricer_matches_string_status_attempts`
feeds an `attempts` payload shaped exactly as `gateway.py:1312` writes it —
`"status": "ok"` on the serving attempt, then a later `"http_500"` on a
different deployment. Pre-fix the matcher selects the failed attempt and the
row stays at $0; post-fix it selects the serving one and reprices at $3.00.

---

### 145. `cache_creation_per_1m` was accepted, echoed, and never stored

**Severity: 🟡** — an admin-set cache-creation rate silently reverted on the
next restart, while the API kept reporting it.

`wiwi/server/app.py:3152-3154` parsed `cache_creation_per_1m` into the pricing
entry and `:3116-3118` echoed it back on GET, but neither `ConfigStore.upsert_price`
(`wiwi/server/config_store.py:339-366`) nor `load_prices` (`:374-396`) had a
column for it — the value lived in `CostEngine.prices` only. A restart
rehydrates from the DB (`app.py:757-761`), so the rate vanished while every
in-process read still showed it.

The same class of gap hid the schema-evolution hazard: `_migrate`
(`config_store.py:125-152`) only ever migrated the `providers` table, and
`CREATE TABLE IF NOT EXISTS` is a no-op on an existing table — so any column
added to `MODEL_PRICES_DDL` after release would never reach an existing
database.

**Fix:** added `cache_creation_input_cost_per_token` to `MODEL_PRICES_DDL`,
`upsert_price`, and `load_prices`; added `_migrate` coverage for `model_prices`
so pre-existing databases gain the column via `ALTER TABLE ADD COLUMN` (safe on
both SQLite and Postgres, no table rebuild).

**Status: fixed** — `tests/test_fix_round52.py::test_cache_creation_rate_survives_a_db_round_trip`
(DB round-trip) and `::test_migrate_adds_cache_creation_column_to_existing_table`
(builds the old-shaped table, then migrates).

---

### Scoped pricing — per-model-per-provider rates

Not a defect, but recorded here because it changed the cost engine's contract
and is the reason the two bugs above became load-bearing.

`CostEngine` priced a model with exactly one rate pair regardless of which
upstream served the request. A model's entry may now carry a `providers`
sub-map of scoped overrides, resolved most-specific-first: **provider account →
provider type → all-providers base**, applied at every step of the legacy
slash-trim tail walk (`wiwi/cost/pricing.py:_lookup`). Scoped overrides merge
over the base, so one rate can be overridden alone.

Three hazards found while implementing, all closed:

- A base-rate PUT **replaced** the whole entry (`app.py:3161`), so editing the
  all-providers rate would have deleted every per-provider price. It now merges.
- A scope-only entry (no base rates) made `cost_with_status` raise `KeyError` on
  `p["input_cost_per_token"]` for any request falling through to the base. The
  merge now returns None for a rate-less result, which reads as unpriced.
- The retroactive gate `was_unpriced` was per-**model**, so a scope-only first
  price marked the model priced and permanently stranded other accounts at $0.
  It is now per-(model, scope).

**Storage:** a separate `model_price_scopes` table, not a `scope` column on
`model_prices` — that table's primary key is `model_id` and SQLite cannot widen
a primary key with `ALTER TABLE`, so a rebuild would have been required on every
existing database.

**Tests:** `tests/test_fix_round52.py` (24 tests) covers precedence, partial
override inheritance, tail-walk scoping, the namespace collision (the shipped
`wiwi.yaml.example` names an account `openrouter` with type `openrouter`),
`register()` preserving scopes, live-traffic pricing per account,
cache-savings using the scoped rate, the admin API (including 400 on an unknown
scope and scope-preserving base edits), restart rehydration, and both bugs
above. `tests/test_fix_round32.py`'s two direct `reprice_unpriced_history` calls
were updated for the new `rate_for` callable signature; all its assertions are
unchanged.

**UI:** verified in a real browser at 375×812 and 1440×900 via
`.verify/scoped_pricing_ui.py` (gitignored) — both rows render, no horizontal
page overflow, ≥44 px touch targets with `aria-label`s, and the scope selector
lists the accounts that serve the chosen model.

---

### 146. Startup loop on pre-existing databases: rollup index created before its columns exist

**Severity: 🔴** — any deployment against a database created by the previous
schema version crashed in a startup loop (`Application startup failed.
Exiting.`), and never came up.

The rollup commit added `serving_model` and the five `unpriced_*` columns to
the `request_rollups` DDL and added `serving_model` to the recreated
`idx_rollup_unique`. But `_migrate` (`wiwi/logging_core/db_sink.py`, rollup
section) only introspected and widened `request_logs` — never
`request_rollups`. `CREATE TABLE IF NOT EXISTS` is a no-op on an existing
table, so on a pre-existing database the new columns never appeared and
`CREATE UNIQUE INDEX ... (bucket_ts, key_id, model_group, provider,
serving_model)` failed with `asyncpg.UndefinedColumnError: column
"serving_model" does not exist` inside `DBSink.startup()` →
`AppState.init_db` → `lifespan`. The exact same class of gap as AUDIT #145
(model_prices), one table over.

**Fix:** `_migrate` now introspects `request_rollups` (`information_schema`
on Postgres, `PRAGMA table_info` on SQLite) and `ALTER TABLE ADD COLUMN`s
every entry of the new `_ROLLUP_MIGRATE_COLUMNS` constant before the index
statements run. Migrated rows read as `serving_model = ''` / unpriced counts
0 — retroactive pricing still rescans raw `request_logs` rows, so only
already-rolled-up history keeps its old zero cost.

**Status: fixed** — `tests/test_fix_round57.py` builds a database with the
old four-dimension rollup shape, runs `startup()` (crashed before the fix
with the production error), and asserts the six columns are backfilled and
the rollup upsert path records `serving_model` and the unpriced token split
end-to-end.

---

### 147. OpenCode's live version refresh read a per-IP-rate-limited GitHub API, so the User-Agent degraded to `opencode/unknown`

**Severity:** 🟡 Medium (silent fingerprint degradation, not an outage)
**Files:** `wiwi/providers/opencode_version.py:28` (pre-fix `GITHUB_LATEST_URL`), `:71-105` (pre-fix `refresh_version`/`_parse_tag`)

**Trigger:** observed live 2026-09-15 19:40:48 —

```
[warning  ] opencode_version_fetch_bad_status status=403
```

`refresh_version()` read the version from
`https://api.github.com/repos/anomalyco/opencode/releases/latest`. Anonymous
calls to the GitHub REST API are capped at **60 requests/hour per IP**, and
the 5-minute sweep alone spends 12 of them; every other API consumer behind
the same egress IP (shared NAT, another agent CLI on the host) takes the
rest. Once exhausted the API answers a bare `403` with no `Retry-After`, so
`refresh_version()` returned `None`, `_cached_version` stayed empty, and
`OpencodeAdapter.headers()` shipped `User-Agent: opencode/unknown` — exactly
the stale fingerprint the live refresh exists to prevent. Nothing surfaced
this to the caller: a `warning` log was the only trace, and the request
succeeded (Zen's client gate is the `x-opencode-session` header, not the UA
version — verified live, see the Round 10 addendum), so the degradation was
invisible until a version gate eventually rejects it.

**Fix:** read the npm registry instead — the CLI's own distribution source of
truth, the endpoint opencode's `Installation.latest` uses for npm/bun/pnpm
installs, and the source the sibling Cline/WorkBuddy version helpers already
use:

```
GET https://registry.npmjs.org/opencode-ai/latest
-> {"version": "x.y.z"}
```

`_parse_tag` (which stripped GitHub's leading `v`) is replaced by
`_parse_version`, which sanitizes the registry value for a header (CR/LF/NUL
stripped, 256-char cap, non-string rejected) since the response is remote
input that lands in a `User-Agent`. Same rule as the Cline/WorkBuddy helpers.

**Verification:** live `refresh_version()` → `1.18.31` (matches GitHub's
`v1.18.31` tag), adapter ships `User-Agent: opencode/1.18.31`, and a real
`big-pickle` request through the adapter's own headers returns HTTP 200. 30
rapid registry calls all return 200 — no comparable per-IP budget on the
sweep path.

**Status: fixed** — `tests/test_fix_round58.py` pins the source (asserts the
GitHub API is *not* called) plus stale-cache survival on registry failure and
the header sanitizer; `tests/test_fix_round27.py`'s two refresh tests were
migrated to the registry URL, and its `test_parse_tag_strips_v` was deleted
with the helper it covered.

---


---

## ✅ Fixed — DB-layer audit (2026-09-15)

Four gaps found by reading the DB layer (`logging_core/db_sink.py`,
`auth/`, `server/config_store.py`) against its own documented invariants.
**All four are fixed** (#148–#151, below); each entry records what the fix
was, what covers it, and how it was verified. One residual is called out in
#149 (Cline OAuth settings rows across a rename) and left open on purpose.

Every fix was verified three ways: a regression test in
`tests/test_fix_round59.py` that fails on the pre-fix code, a **mutation
check** (re-introducing each bug must fail its test), and a **live
end-to-end run** against a server on an isolated port and a throwaway
database — including a restart, to prove the state is durable and not just
in-memory.

### 148. Disabling a user does not revoke the virtual keys they own

**Severity: 🟠 High** — a disabled account keeps full API access through every
key it ever minted.

**Files:** `wiwi/auth/users.py:203` (`UserService.patch` writes only
`users.disabled`), `wiwi/server/app.py:3436` (the sole caller),
`wiwi/auth/service.py:170` (`AuthService.authenticate`), `wiwi/server/app.py:929`
(`authenticate` on the request path).

**Trigger:** a user mints a key (`POST /admin/keys`, `owner_id` = their user
id), then an admin disables the account via `PATCH /admin/users/{uid}`
with `{"disabled": true}`. The user row flips to `disabled = 1`, and
`current_user` (`app.py:1836`) correctly refuses to mint them a *session* —
but the request path never consults `users` at all. `AuthService.authenticate`
reads only `vkeys.disabled` / `vkeys.expires_at`, and `_lookup_db`
(`auth/service.py:198`) does not join `users`. Every key the account owns
still authenticates, still bills, and still counts against nothing.

Reproduced directly: create a user, mint an owner-scoped key, `patch(uid,
disabled=True)`, then `authenticate(plaintext)` — it returns a live `AuthInfo`
(`owner_id='uc2654cd5f0c3103a'`) rather than `None`. There is no delete-user
endpoint either (`grep '@app.delete'` → 7 routes, none for users), so
disabling is the only revocation lever an operator has, and it does not reach
keys.

This is the DB-side half of AUDIT #57 (unbounded playground keys): #57 capped
how many keys a user can mint, but nothing revokes the ones already issued
when the account is shut off.

**Fix sketch:** in the same transaction that sets `users.disabled = 1`, expire
that owner's keys — `AuthService.expire_keys(owner_id=uid)` already exists and
evicts the auth cache per row (`auth/service.py:457`), so wiring it into
`UserService.patch` (or the handler) is the whole change. Alternatively add an
`owner_id` join to `_lookup_db`; expiring is cheaper and preserves the audit
trail the way `expire_keys` already does.

**Status: fixed** — fixed in both places, because each covers the other's gap:

1. *Enforcement (authoritative).* `AuthService._lookup_db` now refuses a key
   whose owning account is disabled, via a correlated `EXISTS` over `users`
   rather than an outer join — so it fails **closed**: a key with no owner
   (`owner_id IS NULL`, the admin-minted ones) still authenticates, but a key
   whose owner row is *missing* does not. This is what makes the invariant
   hold even when the handler is bypassed (a direct DB edit, or a partial
   failure mid-revocation). `AuthService.startup()` now also creates the
   `users` table from `UserService`'s own `USERS_DDL` constant, so a
   standalone `AuthService` (tests, tools) cannot break on the join.
2. *Immediate effect.* `PATCH /admin/users/{uid}` with `disabled: true` calls
   `expire_keys(owner_id=uid)`, which expires the rows in place (audit trail
   survives) and evicts each credential from the auth cache. The response and
   the audit diff both carry `revoked_keys: <n>`, so an operator sees the
   blast radius.

Deliberately **not** changed: the cached-auth fast path in `authenticate()`
still serves owner-bound keys. Forcing a DB round-trip there would work, but
it would also make H9's regression test
(`test_fix_round55.py::test_expire_keys_evicts_the_auth_cache`) pass
vacuously — the DB re-read alone would reject an expired key, so the test
would no longer prove that `expire_keys` evicts the cache. Verified by
mutation: removing the eviction loop still fails that test.

Covered by `tests/test_fix_round59.py::test_disabled_owner_key_is_rejected_by_the_db_check`,
`::test_disabling_one_owner_leaves_other_owners_keys_alone`,
`::test_key_whose_owner_row_is_missing_fails_closed`,
`::test_reenabling_user_does_not_resurrect_revoked_keys`,
`::test_admin_disable_reports_revoked_key_count`,
`::test_admin_disable_leaves_unowned_keys_alone`. Live-verified end to end:
key returns 200 → `PATCH {"disabled":true}` → `revoked_keys: 3` → key returns
401, and still 401 after a server restart. `tests/test_fix_round55.py`'s
fixture was updated to use a real `users` row, since a synthetic `owner_id`
is unreachable in production (`owner_id` is only ever `actor.id`) and would
now be rejected by the fail-closed check for an unrelated reason.

### 149. `model_price_scopes` rows survive a provider rename and delete, silently re-binding to a recycled name

**Severity: 🟡 Medium** — a deleted provider's negotiated rates can be
inherited by an unrelated provider that later reuses the name.

**Files:** `wiwi/server/config_store.py:273` (`delete_provider`), `:251-268`
(`update_provider`, which rewrites `provider_keys` and `deployments` but not
scopes), `:108` (`model_price_scopes` DDL), `wiwi/server/app.py:2133` and
`:2245` (the callers).

**Trigger:** add provider `acct-a`, bind a scoped price with
`PUT /admin/pricing/{model}?provider=acct-a`, then either rename `acct-a` →
`acct-b` or delete it. Both verified against a real `ConfigStore`: after the
rename the scope row still reads `scope='acct-a'` while the provider is named
`acct-b`; after the delete it is still there with no provider at all. Because
`_valid_pricing_scopes` (`app.py:3163`) validates a scope only at *write*
time and the cost engine resolves scopes by name at *read* time
(`cost/pricing.py:163`, account-first), a later provider created with the
freed name `acct-a` silently inherits those rates — and `provider.delete`
already guards against exactly this class of leak for `alias_to_provider`
(`app.py:2128`) and for the Cline OAuth setting (`app.py:2134`), so the
precedent is established one line away.

`update_provider` is the more likely path in practice: it deliberately updates
child rows first so the rename cannot violate referential integrity
(`config_store.py:257-268`), and simply omits this table.

**Fix sketch:** add `UPDATE model_price_scopes SET scope = :nn WHERE scope =
:name` beside the two existing child updates in `update_provider`, and
`DELETE FROM model_price_scopes WHERE scope = :n` in `delete_provider`. (The
same gap exists for `settings` rows keyed `cline_oauth:<provider>` —
`app.py:3655` — which `delete_provider` cleans up at `app.py:2134` but a
rename leaves stranded.)

**Status: fixed** — the DB half is as sketched above (`config_store.py`,
both statements). The live end-to-end run then exposed a **second half the
source reading had missed**: `ConfigStore` rewrote the persisted rows but the
cost engine reads its own in-memory map, so the running server kept billing
at the renamed account's scoped rate — and `/admin/pricing` kept echoing the
stale scope — until a restart. Reproduced live: after `PATCH
/admin/providers/acct-a {"name":"acct-b"}`, the DB read `acct-b` while
`GET /admin/pricing` still reported `acct-a`. Both admin handlers now update
the in-memory map too (`app.py`, the rename branch beside the
`alias_to_provider` rewrite, and `admin_delete_provider`), matching how the
alias map is already handled in the same code path.

Covered by `tests/test_fix_round59.py::test_provider_rename_rewrites_price_scopes`,
`::test_provider_delete_removes_price_scopes`,
`::test_provider_rename_leaves_other_scopes_untouched`,
`::test_provider_type_scope_survives_account_rename`,
`::test_provider_rename_rewrites_the_in_memory_cost_map`,
`::test_provider_delete_drops_the_in_memory_scope`. Live-verified: `acct-a` →
`acct-b` → `acct-c` tracked the scope with no restart, and deleting the
provider cleared it in both the API and the DB.

**Still open:** the `settings` rows keyed `cline_oauth:<provider>` noted
above — a rename strands them. Left unfixed here to keep this change scoped
to the reported defect; `delete_provider` already cleans them up.

### 150. Timeseries `tps_p95` reports the *sum* of bucket peaks, not the max

**Severity: 🟡 Medium** — the TPS chart overstates p95 whenever a bucket mixes
surviving raw rows with a rolled-up hour.

**Files:** `wiwi/logging_core/db_sink.py:217` (`_BucketSum.__slots__`),
`:220-222` (the additive `__init__`), `:1329` (the merge), `:1357` (the read).

**Trigger:** `_BucketSum` is documented as "an additive view over a raw bucket
row plus its rolled-up counterpart" and adds every field in `__slots__`
uniformly. That is correct for the token counters, but `tps_max` is not
additive — it is a maximum, produced by `MAX(CASE WHEN tps > 0 ...)` on the
raw side (`:1271`) and by `MAX(tps_p95)` on the rollup side (`:1302`), and
consumed as `tps_p95` at `:1357`. Adding two maxima is meaningless.

Reproduced: a raw bucket with `tps_max=50` merged with a rolled-up hour whose
`tps_p95=60` reports `tps_p95 = 110.0` where the true value is `60`. Any
window spanning the raw/rollup boundary (which is the entire point of the
rollup feature) can display a TPS figure that never occurred.

**Fix sketch:** exclude `tps_max` from the additive loop and set it to
`max(getattr(a, "tps_max", 0) or 0, getattr(b, "tps_max", 0) or 0)` after it,
matching how `_merge_p95` (`:225`) already treats percentiles as
non-additive.

**Status: fixed** — exactly as sketched. Live-verified end to end: a raw row
at `tps=50` plus a rolled-up hour at `tps_p95=60` in the same bucket now
reports `tps_p95 = 60.0` through `/admin/stats/timeseries`; before the fix
the same data reported `110.0`.

Covered by `tests/test_fix_round59.py::test_bucket_sum_takes_max_of_tps_peak_not_the_sum`,
`::test_bucket_sum_still_adds_the_token_counters`,
`::test_timeseries_tps_p95_never_exceeds_a_real_peak`.

### 151. `DBSink._query_cache` never evicts on the write path, so it grows without bound

**Severity: ⚪ Low** — slow memory growth proportional to distinct queries
served, not to live data.

**Files:** `wiwi/logging_core/db_sink.py:276` (`_cache_get`), `:288`
(`_cache_put`), `:291` (`invalidate_cache`), `:269` (`_CACHE_TTL`).

**Trigger:** `_cache_get` evicts an entry only when *that same key* is read
again after its 5 s TTL, and its own comment claims this keeps the dict
bounded. It does not: a key that is written once and never read again is never
touched, so it is never evicted. `invalidate_cache()` clears the whole dict,
but only the two write paths call it (`write_requests` `:598`, `write_audit`
`:818`, `rollup_and_prune` `:460`) — a read-heavy workload with no writes
retains every distinct key forever. The cache key includes the caller's
`key_ids` tuple (`:898`, `:984`, `:1194`), so a deployment with many users
multiplies the key space by owner.

Measured: 50,000 distinct `_cache_put` calls followed by 10 re-reads leave
`len(_query_cache) == 50000` — nothing was evicted.

**Fix sketch:** sweep expired entries on insert once the dict exceeds a cap
(the same shape `AuthService._sweep_cache` already uses at
`auth/service.py:120`, which is the established pattern in this codebase), or
bound it with an `OrderedDict` LRU.

**Status: fixed** — `_cache_put` now calls a new `DBSink._sweep_cache()` once
the dict exceeds `_CACHE_MAX_ENTRIES` (4096). It uses the same two-phase rule
as `AuthService._sweep_cache`: drop expired entries first, then the oldest
half if everything is still fresh — the TTL sweep alone cannot bound the dict
under sustained distinct-key traffic.

Covered by `tests/test_fix_round59.py::test_query_cache_evicts_expired_entries_on_insert`,
`::test_query_cache_keeps_live_entries`,
`::test_query_cache_bounds_itself_when_everything_is_fresh`.

### 152. NIM 400s on OpenAI-2026 platform params forwarded from the OpenAI adapter's `_STANDARD` set

**Severity:** 🟠 High — every Codex CLI (`/v1/responses`) request through a
`nvidia-nim` deployment failed with a hard 400; no retry/fallback path recovers
a non-retryable validation error.

**Files:** `wiwi/providers/nim_adapter.py:88-149` (`encode_request`); root
cause spans `wiwi/providers/openai_adapter.py:242-249` (`_STANDARD` forwarding)
and `wiwi/wire/openai_responses.py:323-328` (unmapped Responses params kept in
`req.extras`).

**Trigger:** point a `nvidia-nim` deployment at Codex CLI. The Responses codec
captures `prompt_cache_key` (Codex sends it every request) into `req.extras`,
the OpenAI adapter forwards the OpenAI-2026 standard set
(`prompt_cache_key`, `safety_identifier`, `store`, `verbosity`,
`web_search_options`, `prediction`, `modalities`, `audio`, `logit_bias`,
`service_tier`) upstream by default, and NIM — a vLLM-backed endpoint that
strict-validates params — rejects the body:

```
{"message":"Validation: Unsupported parameter(s): `prompt_cache_key`",
"type":"Bad Request","code":400}
```

The NIM adapter already stripped OpenAI reasoning fields for exactly this
reason but never handled the 2026 platform params. The same 400 is reachable
through the chat-completions surface whenever a client sends any of these keys.

**Fix:** strip the ten platform params in `NimAdapter.encode_request` beside
the existing reasoning-key strip — capability-driven, independent of
`drop_params` (`drop_params` governs *unknown* extras; these are *known*
NIM-unsupported keys, mirroring how the reasoning strip already ignores it).

**Status: fixed** — covered by `tests/test_fix_round60.py`
(5 tests: the full ten-key strip, strip under `drop_params=False`, strip from
deployment `extra_body`, benign params survive, and a control asserting the
plain OpenAI adapter still forwards them).

---

## 🟠 High — round 61 (new, 2026-09-16)

### 153. `AnthropicAdapter.decode_stream_event` crashes on null/typed-wrong payload fields — seven sites, none guarded
**File:** `wiwi/providers/anthropic_adapter.py:536-618`
**Trigger:** any Anthropic-shaped upstream (or the proxy in front of one) that
emits a syntactically-valid-but-empty SSE frame. Reproduced against
`fresh_adapter("anthropic")`:

- `data: null` / `[]` / `5` / `"s"` / `true` → `AttributeError` at `:541`
  (`payload.get`) — the adapter never got the `isinstance(payload, dict)`
  guard that `openai_adapter.py:407` (#110) and `gemini_adapter.py:199-204`
  (#136) received for exactly this frame class.
- `{"type":"message_start","message":null}` → crash at `:545` (`m.get`) —
  `payload.get("message", {})` only defaults a *missing* key, not `null`.
- `{"type":"message_start","message":{"usage":"x"}}` → crash at `:547` —
  `m.get("usage") or {}` passes a truthy non-dict through.
- `{"type":"content_block_start","content_block":null}` → crash at `:553`.
- `{"type":"content_block_delta","delta":null}` → crash at `:576`.
- `{"type":"message_delta","delta":null}` → crash at `:598`.

**Consequence (reproduced end-to-end through `Gateway.stream` with a mocked
upstream):** the `AttributeError` escapes into the pump's mid-stream handler
(`gateway.py:1188-1207`): the client is streamed `StreamError("'NoneType'
object has no attribute 'get'")` after `StreamStart`, the partial output is
billed, `dep.record_fail` fires, and `on_result_locked(key, 502)` cools the
key (`err_count` 0→1, status `active`→`cooling`). A frame carrying *zero*
semantic content penalizes a healthy credential and deployment — the exact
failure mode #110/#136 registered, still open on this adapter.

**Fix sketch:** at `:541` add `if not isinstance(payload, dict): return []`
(mirror `gemini_adapter.py:198-204`); coerce each nested read:
`m = payload.get("message"); m = m if isinstance(m, dict) else {}`, likewise
`usage`/`content_block`/`delta`/`error`. Apply the same pattern at the crash
lines above, not only the entry guard.

**Status: fixed** — the entry guard mirrors `gemini_adapter.py:198-204`
(`non-dict frame → []`) and every nested read is type-coerced to its empty
shape: `message`, `usage`, `content_block`, `delta`, `error`,
`output_tokens_details`. Control test `test_anthropic_happy_path_unharmed`
pins unchanged normal-stream behavior.

Covered by `tests/test_fix_round61.py` (Anthropic: junk frames, null
message/usage/content_block/delta/error, happy-path control) and the
end-to-end `test_anthropic_stream_survives_poison_frames_end_to_end`, which
drives a Claude-Code-shaped streaming request through `create_app` with an
upstream injecting every poison frame mid-stream and asserts the client
still receives the completion with no error event.
**File:** `wiwi/providers/openai_adapter.py:431-442` (+ inherited/forwarded by
`openai-compatible`, `gmicloud`, `bai`, `cline`, `workbuddy`, `openrouter`,
`opencode` chat route); `wiwi/providers/nim_adapter.py:303-310` +
`wiwi/providers/nim_native_tools.py:106`; `wiwi/providers/gemini_adapter.py:264-269,277-283`
**Trigger (each reproduced against `fresh_adapter`):**

- `{"choices":[{"delta":5}]}` → `delta = c.get("delta") or {}` keeps the
  truthy int; `AttributeError` at `openai_adapter.py:432`. Same frame crashes
  openrouter, opencode, gmicloud, bai, cline, workbuddy (inherited path).
- `{"choices":[{"delta":{"tool_calls":[{"index":0,"function":"x"}]}}]}` →
  `fn.get` crash (`:441`); a `null` tool_calls entry crashes at `:440`.
- `{"choices":[{"delta":{"content":5}}]}` → **no crash in the adapter**:
  `if delta.get("content")` admits the int and the decoder emits
  `TextDelta(text=5)`. `TextDelta.text` is contractually `str`
  (`streaming/deltas.py`), so the fault lands downstream: the pump's
  `text_len += len(d.text)` (`gateway.py:1039`) raises `TypeError`, or the
  wire encoder serializes `"content": 5` to the client — an invalid OpenAI
  chunk. Adapters guarantee contract legality; this one does not. NIM's
  variant is worse: the int reaches `MiniMaxFramer.feed` and raises
  `TypeError` at `nim_native_tools.py:106`, escaping `_feed_safely`
  (`nim_adapter.py:411-429`), which catches only `NimToolProtocolError`.
- `{"candidates":[{"content":{"parts":[{"functionCall":null}]}}]}` →
  `AttributeError` at `gemini_adapter.py:269`; `{"candidates":[{"finishReason
  ":"STOP"}],"usageMetadata":"x"}` → crash at `:279` (truthy non-dict passes
  `if u:`). Gemini's *entry* guard (#136) exists but its nested reads do not.

**Consequence:** identical to #153 — mid-stream `StreamError` to the client,
partial billing, deployment `record_fail`, key cooldown/retirement ladder,
for frames carrying no usable content. Reachable from ordinary
compatible-gateway glitches (truncated chunk bodies serialize to scalars).

**Fix sketch:** type-guard per read, not per frame: `delta =
c.get("delta") if isinstance(c.get("delta"), dict) else {}`;
`if not isinstance(tc, dict): continue` in the tool loop and likewise for
`fn`; coerce content/reasoning: `out.append(dl.TextDelta(txt))` only when
`isinstance(txt, str)` (drop otherwise — mirrors `anthropic_adapter.py:578-579`
which already does `raw if isinstance(raw, str) else ""`); NIM: guard
`content` to `str` before the framer (or widen `_feed_safely`'s except to
`Exception`); Gemini: `fc = part["functionCall"]; if not isinstance(fc, dict):
continue`, and `u if isinstance(u, dict) else None` at the two usage reads.

**Status: fixed** — type-guarded per read, not per frame, in all three
decoder implementations (the pure-inheritance adapters — `openai-compatible`,
`gmicloud`, `bai`, `cline`, `workbuddy`, `opencode` chat route — get it from
the OpenAI base): `delta` non-dict decodes as empty (a finish-only chunk
carries no `delta` key and must still reach finish handling); `tool_calls`
non-list becomes empty, null/scalar entries are skipped, `function` is
dict-coerced; `content`/`reasoning` are emitted as deltas only when `str`
(truthy non-str dropped, mirroring `anthropic_adapter.py:578-579`); NIM
str-gates `content`/`reasoning` *before* the framer (better than widening
`_feed_safely`'s except: the framer never sees garbage); `usage` is
dict-gated in both OpenAI-wire adapters; Gemini skips typed-wrong
`functionCall` parts and treats a truthy non-dict `usageMetadata` as absent.
The plain OpenAI-side OpenRouter copy (`openrouter_adapter.py:300-334`)
received the identical patch.

Covered by `tests/test_fix_round61.py` (35 tests: per-adapter poison frames,
drop-not-forward contract tests with real-text controls, and the end-to-end
Anthropic smoke).

---

### 154. Live Neon Postgres credential committed in `.env.example`

**Severity:** 🔴 Critical (live secret in a public repo)
**Files:** `.env.example:14` (pre-fix) — introduced by `8da5440`

**Trigger:** none required. `.env.example` is **tracked**, and
`shinmentakezo07/wiwia` is **public**, so the owner password for the Neon
database was world-readable from the moment `8da5440` was pushed. The string is
not a placeholder: `neondb_owner:npg_cr25hgtaFOmp@ep-wandering-math-…`.

A tracked template is exactly where the repo's own "never commit" rule does not
look — `.env` is gitignored, so copying a real value into `.env.example`
during debugging survives every `git status` check. `git log -S` confirms the
credential is in committed history on `origin/main`, not just the working tree.

**Fix:** replaced the value with a placeholder
(`postgresql://user:password@ep-xxx-pooler.…`) and added `HF_TOKEN=` to the
template as a documented-but-empty entry.

**Status: fixed** — `.env.example:14`.

**Operator action still required.** The fix stops *future* exposure; it does not
revoke the credential, which remains in `origin/main` history and must be
treated as compromised:

1. Roll the Neon role password (Neon console → Roles → Reset password).
2. Put the new value in the gitignored `.env` and a Space secret — never in a
   tracked file.
3. Purge history only if the old value is unacceptable to keep readable; the
   password is public either way, so rotation is the load-bearing step.

**Blast radius:** the credential grants direct database access to whatever the
Neon project holds — request logs, virtual keys, budgets, provider
configurations. No gateway auth is involved, so `WIWI_MASTER_KEY` does not gate
it.

---

## Addendum — round 64: Anthropic `/v1/messages` fidelity for Claude Code (#156) (2026-09-16)

Reported symptom: a Claude Code session on the Anthropic surface could not use
the features and tools it should. Confirmed by driving the real CLI (2.1.273)
through the gateway against a mock Anthropic upstream, capturing the outbound
request, and validating the emitted SSE with the `anthropic` SDK.

**All items fixed; do not re-fix.** Details and rationale in `UPDATE.md`
§ "Anthropic `/v1/messages` fidelity for Claude Code". Regression coverage in
`tests/test_fix_round64.py` (37).

| # | Defect | Location (pre-fix) | Client-visible symptom |
|---|---|---|---|
| 1 | `anthropic-beta` read nowhere, forwarded nowhere | `providers/anthropic_adapter.py:166`, `core/context.py` | 1M context and interleaved thinking silently unavailable; body fields forwarded without their authorizing header → hard 400 |
| 2 | `message_start.usage` hardcoded zeros | `wire/anthropic_messages.py:461` | Claude Code's context meter pinned at 0% all session; auto-compact never fires |
| 3 | `output_config.effort` dropped | `wire/anthropic_messages.py:243`, `:307` | `/effort`, `--effort`, `CLAUDE_CODE_EFFORT_LEVEL` all no-ops |
| 4 | Builtin suppression keyed on tool name | `wire/anthropic_messages.py:336`, `openai_chat.py:266`, `openai_responses.py:432` | A function tool named `web_search` was deleted from the response |
| 5 | Sync path left `server_tool_use` untagged | `providers/anthropic_adapter.py:510` | Phantom client tool call in non-streaming mode only |
| 6 | Stop reasons collapsed to `end_turn` | `ir/types.py:14` | `pause_turn` loops truncated; context overflow indistinguishable from a normal stop |
| 7 | `count_tokens` ignored base64 media | `core/gateway.py:1410` | 300 KB screenshot → 7 tokens; auto-compact overrun |
| 8 | `max_tokens: 0` became 4096 | `providers/anthropic_adapter.py:346` | Cache pre-warm generated and billed 4096 output tokens |
| 9 | Empty `system` blocks forwarded | `providers/anthropic_adapter.py:58` | Hard 400 on an empty text block |
| 10 | Mid-conversation `role: "system"` → `user` | `wire/anthropic_messages.py:166` | Weakened instruction; cache breakpoint lost |
| 11 | `mcp_tool_use` had no decode arm | `wire/anthropic_messages.py:57-164` | Unpaired `mcp_tool_result` on replay |
| 12 | Image/document `cache_control` had no IR field | `ir/types.py:26`, `:88` | Screenshot/PDF prefixes never cached |
| 13 | Error path left blocks open, skipped `message_delta`, always `api_error` | `server/app.py:1474`, `wire/anthropic_messages.py:593` | Tool call with empty args; no final usage; no retryable error class |
| 14 | `ping` documented but never emitted | `docs/API_REFERENCE.md:43` | Idle-proxy disconnect during long thinking |
| 15 | No `x-accel-buffering: no` on `/v1/messages` | `server/app.py:1359` | nginx buffers the stream into one burst |
| 16 | Error bodies lacked `request_id` | `wire/anthropic_messages.py:742` | Incidents not traceable to a log row |
| 16b | Interleaved text/thinking dropped silently | `wire/anthropic_messages.py:469`, `:513` | Model's prose invisible to the user and absent from replayed history |
| 17 | Gemini encoded no `tool_choice`; dropped `DocumentPart` | `providers/gemini_adapter.py:122-141` | Forced/named tool choice became prose; PDFs vanished |
| 18 | OpenAI-family adapters dropped `DocumentPart` | `providers/openai_adapter.py:59-103` | PDF attachment reached the model as nothing |
| 19 | OpenRouter leaked `_synthesized_opens` | `providers/openrouter_adapter.py:411` | Open-less `ToolCallDelta` → truncated tool args |
| 20 | `effort` not resolved on non-Anthropic adapters | `nim`/`openrouter`/`opencode` | Effort selection dropped on every non-Anthropic backend |

### Deliberately not changed

- **`web_search` execution on non-hostable backends.** A provider-hosted
  builtin is still dropped when the target cannot host it (correct — a function
  tool named `web_search` would be called by the model and never executed), but
  the drop remains server-log-only. Surfacing it to the client needs a response
  channel that does not exist yet; recorded in `docs/MVP.md` G22.
- **`disable_parallel_tool_use` on Gemini.** Gemini exposes no knob for it;
  the adapter now warns instead of ignoring it silently.
- **Anthropic `web_search_requests` billing** and response-side search traces
  (`web_search_tool_result` blocks, citations) remain unmodeled — unchanged
  from the Round 8 position.
- **`WiwiSettings.header_allowlist`** is still absent as a config field; the
  allowlist is now a code constant (`server/app.py:_FORWARDABLE_HEADERS`).

---

## ✅ Fixed — round 65: retroactive pricing keyed on a truncated model id (2026-09-16)

### 157. Retroactive pricing conflates models that share a last path segment — one model's history is billed at another's rate

**Severity:** 🟠 High (silent mis-billing + wrong key budgets)
**Files:** `wiwi/logging_core/db_sink.py:421` (rollup truncation),
`wiwi/logging_core/db_sink.py:818-870` (`_serving_attempt` suffix match),
`wiwi/logging_core/db_sink.py:754` (`_reprice_rolled_up` exact match),
`wiwi/server/app.py:3382` (`PUT /admin/pricing` truncation)

**Trigger:** a model id that itself contains `/` — the normal OpenRouter /
gateway form, and what the shipped `wiwi.yaml` uses
(`model_name: stealth/ox-alpha`, `model: stealth/ox-alpha`). Two models whose
ids share a last segment (`stealth/ox-alpha`, `vendor/ox-alpha`) both reduce to
`ox-alpha`.

Three sites each truncated or suffix-matched the model identity:

1. `rollup_and_prune` stored `dep.split("/")[-1]` as `serving_model`, so the
   rollup row lost which model served it.
2. `_serving_attempt` matched `dep.endswith(f"/{match_tail}")`.
3. `PUT /admin/pricing/{model_id}` passed `model_id.split("/")[-1]` as the
   match tail, so even a correct matcher was handed a lossy key.

Pricing `vendor/ox-alpha` then repriced `stealth/ox-alpha` history at
`vendor`'s rate and charged its virtual key — corrupting both the cost column
and that key's `spend_to_date`, which is what budget enforcement reads.

**Reproduced end-to-end** through the real admin route: two requests (one per
model), then `PUT /admin/pricing/vendor/ox-alpha`. Pre-fix the true-up logged
`total_delta=6.0` for what should have been a single row's 3.0, and both
`request_logs` rows carried cost 3.0.

**Root cause:** model identity was *reconstructed* from the concatenated
`"<group>/<model_id>"` deployment string rather than recorded. Both halves of
that string may contain `/`, so no suffix rule can recover the model. Fixing
the matcher alone would have been a symptom fix — the truncation happened at
the source.

**Fix:**
- `AttemptRecord` gained a `model_id` field, populated at all 14
  `ctx.note_attempt(...)` call sites in `core/gateway.py` from `dep.model_id`,
  and serialized into the row's `attempts` JSON. Identity is now recorded, not
  re-derived.
- `_model_id_from_attempt` prefers the recorded field; for rows written before
  it existed it strips the row's own `model_group` prefix from the deployment
  string (exact, and correct for slash-bearing ids), falling back to the lossy
  last segment only when the group is unknown.
- `_serving_attempt` and `_reprice_rolled_up` now compare *whole remaining
  path segments* — the same slash-tail convention `CostEngine._lookup`
  already uses — so a bare-tail price (`claude-sonnet-4`) still matches
  `anthropic/claude-sonnet-4`, while `vendor/ox-alpha` no longer matches
  `stealth/ox-alpha`. The rollup query uses an escaped `LIKE` as a prefilter
  and applies the segment rule in Python.
- `PUT /admin/pricing` passes the full pricing key instead of a pre-truncated
  tail.

**Status: fixed** — covered by `tests/test_fix_round65.py` (9 tests). Three
fail on the pre-fix code (verified by stashing `wiwi/`): the rollup storage
test, the rollup reprice test, and the end-to-end route test. Two controls
guard against over-correcting — a bare-tail key must still reprice its full id
(the `_lookup` convention), and a slash-bearing model's own history must still
be repriced. Legacy rows without the new field are covered by their own test.

**Note for the register:** the pre-existing reprice tests
(`tests/test_fix_round32.py`, `tests/test_fix_round52.py`) only ever use
slash-free ids (`priced-model`, `retro-gpt`), which is why this survived. Any
future test of model-keyed logic should include a slash-bearing id.

**ECC review follow-up (same round).** Two further findings from
`ecc:python-review`, both reproduced before fixing:

- **H1 (fallback truncation)** — see the review follow-up above; the ECC
  reviewer independently reproduced it and rated the committed state FAIL on
  this alone. Fixed.
- **M1 (repricer narrower than the cost engine)** — the gateway prices live
  traffic with `f"{provider_type}/{model_id}"` and `CostEngine._lookup` accepts
  that full prefixed key, but the repricer matched the registered key only
  against slash-tails of the *recorded* id. A price registered as
  `openrouter/anthropic/claude-sonnet-4` (which live traffic honours) silently
  true-upped **nothing** — history stayed at cost 0 and budgets stayed
  under-charged, the same silent mis-accounting this round removes. Fixed by
  matching in **both** directions via a shared `_shares_model_tail` /
  `_slash_tails` helper, which also removes the duplicated inline segment rule
  the reviewer flagged. Covered by
  `test_reprice_accepts_a_provider_prefixed_key` and its control
  `test_reprice_still_rejects_an_unrelated_provider_prefixed_key` (the control
  proves the two-way rule did not resurrect the collision).

Also addressed from the same review: `_model_id_from_attempt` now takes
`dict[str, Any]` and no longer takes an unused `group` parameter (the
signature implied a cross-check that did not happen); the sweep-throttle
docstring no longer implies the window map is hard-bounded; and `release`
returns early on an empty `key_id`.

**Round 68 interaction (same subsystem).** A parallel session found that
stripping a fixed *first* segment is also wrong in the other direction: when
the **group** name contains a slash (the shipped config's
``minimax/minimax-m3``, ``stealth/ox-alpha``), it leaves the rest of the group
glued to the front of the id — ``minimax-m3/MiniMax-M3`` — which matches no
price row, so the rollup stores it unpriced and the row can never be repriced.
Fixed by stripping the row's own ``model_group`` when it prefixes the
deployment (exact, and the common non-failed-over case), keeping the
first-segment strip only for a fallback-served row where the serving group is
not recorded. Covered by ``tests/test_fix_round68.py``.

Note both halves of the deployment string may contain ``/`` and neither is
recoverable from the other in general — that is why identity is now *recorded*
on the attempt rather than re-derived; the fallbacks exist only for rows
written before the field existed, and age out under the 30-day retention
default.

**Known residual (M2, documented not fixed):** for rows logged *before*
`model_id` existed, the raw-row path still falls back to the lossy
`endswith("/" + tail)` rule, so bare-tail pricing can still conflate two
legacy rows sharing a last segment. This is time-bounded by the 30-day
retention default that ages those rows out, and cannot affect rows written
after the fix. Recorded here so a later reader does not re-diagnose it.

**Review follow-up (same round).** Code review found the legacy fallback in
`_model_id_from_attempt` still truncated for **fallback-served** rows: it keyed
the group-prefix strip on the row's `model_group`, which is the *client-requested*
group (`ctx.group`, never reassigned on failover), so a row with
`model_group="A"` served by `deployment="B/stealth/ox-alpha"` missed the prefix
test and fell through to `split("/")[-1]` — silently reintroducing this exact
truncation on the rollup path. Fixed by stripping the deployment's own FIRST
segment (always the serving group), and by returning `""` rather than guessing a
tail when nothing is recoverable. Covered by
`test_legacy_fallback_row_keeps_its_full_model_id_in_the_rollup`.

---

## ✅ Fixed — round 66: rate-limiter RPM refunds and the unbounded sweep (2026-09-16)

Two defects in `wiwi/ratelimit/memory.py`, both found by reading the module
against its own docstrings. **Both fixed**; each had a reachable trigger, and
each is pinned by a test that fails on the pre-fix code.

### 174. `release()` refunds an RPM slot it does not own — admission past the configured cap

**Severity:** 🟠 High (rate-limit bypass)
**File:** `wiwi/ratelimit/memory.py:189-195` (pre-fix `release`),
`:120` (pre-fix admission)

**Trigger:** a request that reserved an RPM slot more than 60 s ago (a long
stream, or any slow turn) and then fails upstream.

Admission appended RPM events with **no identity** —
`_Event(ts=now, tokens=1)` — while `release()` pruned the window and then
popped whatever was *newest*. If the releasing request's own event had already
aged out of the 60 s window, the pop removed a **different, still-in-flight**
request's slot. The window total then under-counted and admission let requests
past the cap.

Reproduced: 3 long-lived streams in flight (`key_rpm=3`), all older than 60 s;
each failed-and-released request freed a live slot, after which 3 *more*
requests were admitted — 6 concurrent against a cap of 3. The global scope is
worse: `global:rpm` is shared by every key, so one spurious refund raises the
effective cap for all of them.

The docstring claimed the opposite was guaranteed — "Safe to call after
``_record_tpm_usage`` too: release only removes still-estimated reservations".
That held for RPM not at all (its events were neither tagged nor filtered) and
for TPM only partially: TPM events *are* tagged, but the shared
``_find_reservation`` falls back to "newest estimated" when the id does not
match, so an unmatched TPM release also freed a live request's tokens
(``release("k1", request_id="ghost")`` dropped a live 300-token reservation to
0). That fallback is correct for ``record_tokens`` — which *replaces* an
estimate for a request that succeeded, so attributing it to another in-flight
estimate keeps the total right — but wrong for ``release``, which *removes*
capacity.

**Fix:** RPM events now carry `request_id` at admission. Both scopes refund via
a new strict `_find_refund`, which has deliberately **no** "newest event"
fallback when an id is supplied — an unmatched release must refund nothing,
because every other event belongs to a live request. The id-less fallback is
retained only for callers that predate request ids, where at most one request
can be in flight. `_find_reservation` keeps its lenient fallback for
``record_tokens``, and `_drop` keeps the running total in sync while tolerating
an already-pruned event.

**Status: fixed** — rounds 66/70 (2026-09-16/17); `tests/test_fix_round66.py`,
`tests/test_fix_round70.py`. (This entry shares its number with the
entitlement-refusal entry below — an authoring slip, kept as-is so neither
entry's inbound references break.)

### 175. The window sweep runs on every admission once over the cap — O(n) scan under the limiter's lock

**Severity:** 🟡 Medium (availability; needs ≥10k distinct keys)
**File:** `wiwi/ratelimit/memory.py:50-60` (pre-fix `_sweep_windows`)

**Trigger:** more than `_max_windows` (10 000) distinct rate-limited keys.

`_sweep_windows` was called from `check()` and, once the map exceeded the cap,
scanned **every** window on **every** admission — inside `self._lock`, so it
serialized all concurrent admissions behind an O(n) scan. Measured: 22 000
windows, **6.28 ms per check**, against 0.005 ms at baseline.

The declared cap was also not a bound: the sweep only deleted windows that were
*empty after pruning*, so a burst of distinct keys left the map permanently
above it (22 000 observed against a cap of 10 000).

> **Commit-message correction (round 69, 2026-09-17).** The message on
> `2b28c5f` states the map grew *without bound* ("never capped the window
> map"). That overstates it: the pre-fix sweep deleted every empty window on
> every pass, so the map was bounded by concurrent *active* keys — the bound
> this entry states, and the same one the fix preserves. The real defect was
> the O(n) scan per admission once the map passed the cap, not an unbounded
> map. No code change: the entry above and the fix are correct, and only the
> commit message is wrong. Recorded here so a reader arriving from `git log`
> is not misled.

**Fix:** the sweep is throttled to one pass per `_SWEEP_INTERVAL_S` (5 s), which
amortizes the scan instead of paying it per request. Correctness does not depend
on sweep frequency — admission already prunes the windows it consults, so the
sweep exists only to reclaim idle keys' memory.

**Deliberately NOT fixed by evicting live windows.** The first attempt capped
the map by evicting the stalest windows; that resets an *actively
rate-limited* key's count and admits it over its cap — a worse bypass than the
one being fixed. A control test (`test_active_key_survives_sweep_pressure`)
pins that a key still holding traffic is never dropped. The map is bounded by
concurrent active keys, which is the algorithm rather than a leak.

**Status: fixed** — covered by `tests/test_fix_round66.py` (11 tests). Six fail
on the pre-fix code (verified by stashing `wiwi/`): five for the refund defect
(four RPM, one TPM), one for the sweep frequency. Five controls guard against
over-correcting — an ordinary failure must still refund its own slot (or AUDIT
#70/#121 return), `record_tokens` must keep its lenient newest-estimated
fallback, a live key must survive sweep pressure, and a genuinely freed slot
must remain reusable.

**Note:** #174 is a residual of the #121 fix. #121 stopped the global slot from
*leaking*; this fixes the opposite failure — refunding a slot that was never
given up. The addendum's claim that the window "errs permissive — never leaky"
was verified only with balanced admit/release pairs, which is exactly the case
that hides this.

---

## Addendum — round 67: Claude Code tool-search and server-tool fidelity (#158) (2026-09-16)

Reported symptom: a Claude Code session through the gateway invoked `Skill` and
`Task` seemingly at random and ignored `/effort`. Diagnosed by decoding a real
`/v1/messages` body through the codecs and comparing each hop's output.

Root cause: the tool *definitions* were fine (`Task`, `Skill`, MCP tools all
round-tripped byte-faithfully), but everything around them that Claude Code uses
to make tool-selection decisions was dropped. With tool search enabled, Claude
Code sends a `tool_search_tool_*` server tool plus `defer_loading` flags; the
gateway dropped the tool and the flags, so the entire MCP/skill catalog loaded
up front. Anthropic's own guidance is that selection accuracy degrades past
30–50 available tools, which is precisely the observed behaviour.

| # | Defect | Location (pre-fix) | Client-visible symptom |
|---|---|---|---|
| 1 | `tool_search_tool_regex_20251119` / `_bm25_` unregistered → dropped on EVERY route, Anthropic→Anthropic included | `ir/builtin_tools.py:18-24`, `providers/anthropic_adapter.py:514` | No search step; every deferred tool loads up front |
| 2 | `defer_loading` absent from the IR entirely | `ir/types.py:150` | Same — even a working search tool would load the whole catalog |
| 3 | `tool_reference` blocks inside a text-bearing `tool_result` discarded | `wire/anthropic_messages.py:133-142` | Model told "found 1 tool", never which one → discovered tools unusable |
| 4 | `output_config.effort` shadowed by `thinking.budget_tokens` | `ir/types.py:217-229` | `/effort`, `--effort`, `CLAUDE_CODE_EFFORT_LEVEL`, per-skill frontmatter all no-ops on non-Anthropic backends (8000 → always `medium`) |
| 5 | Server-tool result blocks dropped on both decode paths; paired `server_tool_use` re-emitted as an unanswered `tool_calls` entry on OpenAI-wire backends | `providers/anthropic_adapter.py:563-599`, `:665`, `providers/openai_adapter.py:107` | No citations/search results; OpenAI-compatible upstreams 400 on the dangling call |
| 6 | `anthropic-beta` dropped on the `opencode` route to a real Messages endpoint | `core/gateway.py:255` | Beta-gated body fields arrive without their authorizing header → hard 400 |
| 7 | `forward_headers` lost on mid-stream resume | `core/gateway.py:861-863` | A recoverable stream error became unrecoverable on failover |
| 8 | `parallel_tool_calls` nested inside `if encoded_tools`; `input_examples` dropped on OpenAI wire; `strict` silently ignored on Gemini | `providers/openai_adapter.py:237`, `:289`, `providers/gemini_adapter.py:135` | Serialized tool calls went concurrent when all declared tools were provider-hosted; worked examples vanished |

**Fix:** `tool_search_bm25`/`tool_search_regex` registered as canonicals (bm25
owns the Responses surface's single `tool_search` spelling, so `_build_reverse`
now lets the FIRST canonical claim an ambiguous wire type);
`Tool.defer_loading` threaded codec→IR→adapter; `ToolResultPart.extra_blocks`
carries nested non-text blocks verbatim for the Anthropic encoder; effort
precedence reordered so an explicit selection outranks the budget-derived guess;
a new `ServerToolResultDelta` plus `AssistantTurn.server_blocks` carry
provider-executed result blocks, and the Anthropic encoder buffers a
`server_tool_use` until its result arrives so the pair is emitted whole (an
unpaired call is still dropped — the A1 invariant is preserved, not weakened).

**Status: fixed** — covered by `tests/test_fix_round67.py` (31 tests). One
pre-existing test (`test_matrix_anthropic_client_receives_suppressed_trace`)
pinned the old discard-everything behaviour and was rewritten to the corrected
pairing contract; a new control (`test_anthropic_client_never_receives_a_half_pair`)
pins that a truncated trace still degrades to text.

**Deliberately NOT changed:** a provider-hosted builtin is still dropped (with a
warning) on a backend that cannot host it — a function tool named `web_search`
would be called by the model and never executed. `input_examples` is rendered
into the description rather than a native field where none exists, so the
information survives without risking a 400 on strict gateways.

---

## ✅ Fixed — round 68: legacy model-id recovery corrupts slash-bearing groups (2026-09-16)

### 158. Legacy model-id fallback strips the serving group's *first segment* — wrong for every slash-bearing `model_name`

**Severity:** 🟠 High (corrupted `serving_model` rollup dimension; a regression introduced by the round-65 review fix, not a pre-existing bug)
**Files:** `wiwi/logging_core/db_sink.py:877` (`_model_id_from_attempt`),
`wiwi/logging_core/db_sink.py:447` (rollup call site)
**Introduced by:** the uncommitted round-65 review change that dropped the
`group` parameter (previously `db_sink.py:857`).

**Trigger:** any model whose *group* (`model_name`) contains a `/` — which is
the normal shape in this repo. The shipped, live `wiwi.yaml` opens its
`model_list` with `model_name: stealth/ox-alpha` and
`model_name: minimax/minimax-m3`, and the round-65 fixtures themselves use
`stealth/ox-alpha` / `vendor/ox-alpha`.

`deployment` is written as `f"{dep.group}/{dep.model_id}"`
(`core/gateway.py:329` and 15 sibling call sites), so for those groups the
string is `"minimax/minimax-m3/MiniMax-M3"`. Stripping the **first** `/`
segment yields `"minimax-m3/MiniMax-M3"` — the tail of the group glued to the
front of the model id. A value that names no real model.

Reproduced against the real rollup path:

```
group='minimax/minimax-m3'  true model='MiniMax-M3/air'
stored serving_model='minimax-m3/MiniMax-M3/air'
```

And against the live config, comparing HEAD to the working tree:

| deployment | HEAD (old) | worktree (new) |
|---|---|---|
| `stealth/ox-alpha/stealth/ox-alpha` | `stealth/ox-alpha` ✅ | `ox-alpha/stealth/ox-alpha` ❌ |
| `minimax/minimax-m3/minimax/minimax-m3` | `minimax/minimax-m3` ✅ | `minimax-m3/minimax/minimax-m3` ❌ |

**Consequence.** `serving_model` is part of the rollup's unique key
(`bucket_ts, key_id, model_group, provider, serving_model`), so a corrupted
value does not merely mislabel a row — it creates a **second bucket for the
same model in the same window** (verified: one model, two rollup rows). The
stored dimension is permanently wrong for every legacy row of a
slash-bearing group.

The old code was correct here because it keyed the strip on the row's
`model_group`, which for a non-failed-over request *is* the serving group,
slashes included. The review fix removed that and replaced it with a
first-segment strip, which fixed the fallback case at the cost of the far more
common one.

**Why the round-65 tests did not catch it:** the fixture sets
`MODEL_A_GROUP == MODEL_A_ID == "stealth/ox-alpha"`, so the group and the model
id are the same string and a corrupted recovery never collides with the
sibling — the conflation assertion passes vacuously. The new test file uses a
slash-bearing group whose model id differs from it.

**Fix:** key the strip on the row's `model_group` when it actually prefixes the
deployment (the exact, non-failed-over case — correct for slash-bearing groups
*and* slash-bearing model ids), and fall back to the first-segment strip only
for a fallback-served row, where the serving group is not recorded at all and
the split point is genuinely ambiguous. The `f"{group}/"` prefix test is
path-boundary-safe, so a group `A` does not match a deployment `AB/x`.

**Severity, measured not assumed.** `reprice_unpriced_history` still finds the
row, because `_shares_model_tail` matches the real id among the corrupted
string's slash-tails — so retroactive pricing *does* recover and no request goes
unbilled. The damage is the stored `serving_model`: it is a rollup dimension, so
one model splits into two buckets in the same window (verified: one model, two
rows, one named `minimax-m3/MiniMax-M3/air`), and the console reports a model
name that does not exist. This is a data-fidelity defect, not a billing one.

Covered by `tests/test_fix_round68.py` (3 tests: direct recovery, rollup
storage, and rollup-dimension split). All three were RED against the
first-segment-only implementation and are GREEN after the fix.

**Residual, deliberately not fixed.** The fallback branch is still lossy when
the *serving* group itself contains a `/`: `deployment="minimax/minimax-m3/MiniMax-M3"`
with `model_group="A"` (a fallback-served row) yields `minimax-m3/MiniMax-M3`.
This is not a regression — HEAD produced the same corrupted value, and the
pre-HEAD `split("/")[-1]` produced `MiniMax-M3`, which is right for this
sub-case but wrong for the slash-bearing-*model-id* case round 65 fixed — so
the change trades one sub-case for another and is net better. It is also
unreachable in the shipped configuration: fallbacks are opt-in and the live
`wiwi.yaml` configures no `fallbacks:` table, and a directly-requested group
takes the exact-strip branch. Repairing it would require recording the serving
group on the attempt, which is a schema change, not a bugfix.

**Backfill deliberately declined.** Rows already written by the buggy version
carry a corrupted `serving_model`. They are not irrecoverable — `_shares_model_tail`
finds the real id among the corrupted string's slash-tails, so repricing still
recovers them — but the stored dimension stays wrong (one model shown twice in
the console). No re-key/backfill is attempted; the corrupted label is left as
history rather than guessed at.

---

## 🟠 Open — round 68 audit sweep: newly confirmed defects (2026-09-16)

Found by a five-agent read of the backend (`core/`, `streaming/`, `router/`,
`auth/`, `ratelimit/`, `cache/`, `wire/`, `providers/`, `server/`,
`logging_core/`), each finding then reproduced independently against the real
modules before being recorded here. Entries marked **disproven** were reported
by an agent but did **not** reproduce on retest — recorded so a later agent
does not chase them.

### 159. Mid-stream resume emits a second `StreamStart` → a second `message_start` mid-stream

**Severity:** 🟠 High (streaming-contract violation on the Anthropic surface)
**Files:** `wiwi/core/gateway.py:725-727` (the `StreamStart` arm), `:690`
(`started` is a local of `stream()`), `:762-764` (resume `continue`)

`started` is initialised once before the consumer loop and the resume branch
`continue`s back into it without resetting the flag. `_attempt_resume` starts a
**new** pump on a **new** adapter instance, and the Anthropic adapter emits its
own `StreamStart` from `message_start` (`providers/anthropic_adapter.py:673`),
so the second one is yielded verbatim.

**Trigger:** `stream_resume="enabled"`, an Anthropic upstream that dies after
content, and a fallback that connects.

**Reproduced** — the consumer observes two `StreamStart`s with the *primary's*
and the *resume's* prompt counts (`prompt=11`, then `prompt=99`), and rendered
through the real `AnthropicStreamEncoder` the client receives:

```
event: message_start      <- original
event: content_block_start
event: content_block_delta
event: message_start      <- resume attempt, mid-stream
event: content_block_delta
```

**Consequence:** `streaming/deltas.py` requires exactly one `StreamStart` first.
Anthropic clients treat `message_start` as the start of a new message, so Claude
Code re-initialises its context meter and accumulator and the turn is split
across two message objects; the resume attempt's usage also *replaces* the
opening usage rather than being summed.

**Why the existing test missed it:** `tests/test_fix_round20.py:481`
(`_assert_single_logical_stream`) asserts exactly one `StreamStart`, but its
harness (`_h2_config`) uses **OpenAI** providers, whose adapter emits no
`StreamStart` — the gateway synthesizes one, and the synthetic branch is
guarded. Only the Anthropic adapter emits one, which is the unguarded path.

**Fix sketch:** reset `started` in the resume branch (or fold the resumed
attempt's `StreamStart` into the existing one instead of re-emitting).

**Status: fixed** — round 68 (2026-09-18). The guard is now `if not started:`
around the yield, with the resumed attempt's counts folded into the single
opening frame instead of re-emitted (`wiwi/core/gateway.py`).

**Coverage added in the same round.** The reason this slipped through is that
the existing guard (`tests/test_fix_round20.py:481`,
`_assert_single_logical_stream`) drives **OpenAI** providers — whose adapter
emits no `StreamStart` at all, so the gateway synthesizes the one and only
frame and the duplicate path is never reached. There was no test on the
Anthropic path. `tests/test_fix_round68.py::test_anthropic_resume_emits_exactly_one_stream_start`
now drives a real Anthropic primary dying mid-stream with a resume onto a
second Anthropic deployment. Verified to fail against the pre-fix code with the
entry's own signature — two `StreamStart`s carrying `prompt=11` then
`prompt=99`.

**Tests:** tests/test_fix_round75.py.

### 160. Anthropic adapter emits `ToolCallArgsDelta(args_fragment=None)` — the gateway's `"".join()` raises

**Severity:** 🟠 High (500 mid-stream)
**Files:** `wiwi/providers/anthropic_adapter.py:730-732`; consumers
`wiwi/core/gateway.py:527` and `:583`

`d.get("partial_json", "")` defaults only a *missing* key; a `null` (or any
non-string) value passes through, unlike every sibling arm in the same method
and every other adapter, which gate with `isinstance(..., str)`.
`streaming/deltas.py` types `args_fragment: str`.

**Reproduced:** frame
`{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":null}}`
→ `ToolCallArgsDelta(args_fragment=None)`; folding it raises
`TypeError: sequence item 0: expected str instance, NoneType found`.

**Fix sketch:** `frag = d.get("partial_json"); args_fragment=frag if isinstance(frag, str) else ""`.

**Status: fixed** — round 78 — `tests/test_fix_round78.py`.


### 161. Gemini can emit two `UsageFinal` / `Finish` / `StreamEnd` for one stream

**Severity:** 🟠 High (billed on the wrong frame; contract violation)
**Files:** `wiwi/providers/gemini_adapter.py:346-396` — both the `if finish:`
arm and the `elif u and not parts:` arm emit a full terminal tail.

**Reproduced:** a parts-less usage-bearing frame followed by a finish frame
yields `[StreamStart, UsageFinal, Finish, StreamEnd, UsageFinal, Finish,
StreamEnd]`. Gemini 2.5 / Vertex attach `usageMetadata` to every chunk, so the
`elif` fires on an intermediate frame and the later finish frame fires the
`if`. Every consumer keeps the last value, so the stream is billed on the
intermediate frame's counts.

**Fix sketch:** a `self._saw_tail` flag set on first emission, checked in the
`elif`.

**Status: fixed** — round 78 — `tests/test_fix_round78.py`.


### 162. Revoking a credential is undone by an in-flight `authenticate()` re-caching its stale `AuthInfo`

**Severity:** 🟠 High (revocation silently ineffective for up to the 60 s TTL)
**Files:** `wiwi/auth/service.py:201` (DB read), `:210-211` (`_sweep_cache` +
store); eviction sites `:304`, `:367`, `:386`, `:526`

`authenticate()` has no lock and does read-modify-write on `self._cache`. Every
revocation path works by *evicting* the entry, which is a no-op while it is
absent — i.e. exactly during the window between the read at `:201` and the
store at `:211`. An admin revoking in that window is overwritten by the late
store.

**Reproduced** with a faithful single-threaded interleaving at the only await
point: after `revoke()`, the in-flight `authenticate()` still returns live info,
re-inserts it into the cache, and a subsequent `authenticate()` also succeeds.

**Consequence:** `DELETE /admin/keys/{id}`, `POST /admin/keys/{id}/disable` and
the owner-revocation `expire_keys` all leave the credential authenticating for
the rest of the TTL. Keys with `max_budget=None` and no expiry are only
revocable by deletion, so this is their only revocation path.

**Fix sketch:** a per-service `asyncio.Lock` around the `_lookup_db`→store pair,
or re-validate liveness after any eviction generation change.

**Status: fixed** — round 80 — `tests/test_fix_round80.py`.


### 163. `rpm: 0` / `tpm: 0` on a virtual key means *unlimited*, not *blocked*

**Severity:** 🟡 Medium (an operator parking a key silently grants it unlimited throughput)
**Files:** `wiwi/auth/service.py:41-43` (`_coerce_limit` rejects only `< 0`),
`wiwi/ratelimit/memory.py:125-128` (`if key_rpm:` / `if key_tpm:`)

**Reproduced:** `check(key_rpm=0, key_tpm=0)` returns allowed five times and
creates no windows at all — the falsy guard skips the scope entirely.

`DeploymentParams` rejects `rpm <= 0` for exactly this semantic (AUDIT #101),
so the two boundaries disagree and the looser one is the one reachable through
the admin API.

**Fix sketch:** reject `<= 0` in `_coerce_limit` for `rpm`/`tpm`, mirroring
`DeploymentParams`.

**Status: fixed** — round 80 — `tests/test_fix_round80.py`.


### 164. Live OAuth credentials in `code.md` at the repo root are untracked but **not** gitignored

**Severity:** 🟠 High (secret exposure risk — same class as #154)
**File:** `code.md` (repo root; `.gitignore` covers `key.md` but not this)

Holds four live WorkBuddy/CodeBuddy JWTs (two `accessToken`, two
`refreshToken`, ~1.4 KB and 700 B each) for two accounts. `.gitignore:13`
protects the sibling `key.md`, and the file's own section documents the same
class of file (`wiwi-providers-*.json`, `workbuddy-auths-*.json`) — but
`code.md` was not added, so a `git add -A` / `git add .` commits it. Confirmed
never committed (`git log --all -- code.md` is empty) and confirmed
`git check-ignore code.md` does not match.

**Fix:** add `code.md` to `.gitignore` beside `key.md`; rotate the tokens if the
file was ever staged.

**Status: fixed** — `.gitignore:18` now lists `code.md` beside `key.md` (verified: `git check-ignore code.md` matches).

### 165. Responses encoder crashes on a hosted `web_search` call whose args aren't a JSON object

**Severity:** 🟠 High (500 mid-stream, after content already sent)
**File:** `wiwi/wire/openai_responses.py:409` (`_builtin_query`, reached from
`:507`)

`args = orjson.loads(arguments) if arguments else {}` then `args.get("query", "")`
with no `isinstance(args, dict)` guard. The sibling path at `:441` is guarded.

**Reproduced:** `ToolCallOpen(index=0, id=..., name="web_search",
builtin="web_search")` + `ToolCallArgsDelta(args_fragment="[1]")` +
`ToolCallClose(0)` raises `AttributeError: 'list' object has no attribute 'get'`
— likewise for `5`, `"abc"`, `null`, `true`.

**Fix sketch:** `return args.get("query", "") if isinstance(args, dict) else ""`.

**Status: fixed** — round 77 — `tests/test_fix_round77.py`.


### 166. Chat codec forwards a non-string `role` to the upstream verbatim

**Severity:** 🟡 Medium (upstream 400 misattributed far from its cause)
**File:** `wiwi/wire/openai_chat.py:177` (`# type: ignore[arg-type]` is the tell)

The Anthropic codec normalizes the role and the Responses codec normalizes it;
Chat does not.

**Reproduced:** `{"role": 7, "content": "hi"}` decodes to `role=7` and the
OpenAI adapter emits `{"role": 7, ...}`; same for `["user"]`, `{"a":1}`, `null`.

**Fix sketch:** coerce to the known role set, defaulting to `"user"` (the
Anthropic codec's `normalized` line).

**Status: fixed** — round 77 — `tests/test_fix_round77.py`.


### 167. Anthropic codec silently drops an assistant turn whose `content` is `null`

**Severity:** 🟡 Medium (turn alternation corrupted on replayed history)
**File:** `wiwi/wire/anthropic_messages.py:212-221` — `if parts:` guards the
append with no `elif role == "assistant"` arm.

**Reproduced:** `[{"role":"assistant","content":None},{"role":"user","content":"hi"}]`
decodes to a single `user` turn on `/v1/messages`, while the Chat codec keeps
the assistant turn (`openai_chat.py:175-176`). `content: null` is legal
Anthropic input — it is what the API emits for a tool-use-only turn.

**Fix sketch:** append an empty-parts assistant message, as the Chat codec does.

**Status: fixed** — round 76 — `tests/test_fix_round76.py`.


### 168. A malformed image block is forwarded upstream as a bogus image instead of being dropped

**Severity:** 🟡 Medium (upstream 400 with no indication of which block was junk)
**Files:** `wiwi/wire/openai_responses.py:247` (`_decode_image(c.get("image_url") or "")`
— the empty string passes the None guard), `wiwi/wire/anthropic_messages.py:69-72`
(`source.type == "base64"` with no `data` → `b64=None`)

**Reproduced** end to end through the real adapters:

```
POST /v1/responses  {"type":"input_image"}                       (no image_url)
  -> outbound {"type":"image_url","image_url":{"url":"data:image/png;base64,None"}}

POST /v1/messages   {"type":"image","source":{"type":"base64"}}   (no data)
  -> outbound {"type":"image","source":{"type":"base64","data":null}}
```

`_decode_image` already returns `None` for non-string input; it just does not for
the empty/absent case, so the malformed block survives as a 4-byte "image" or an
upstream 400 that names no offending block. Note this is the opposite failure
direction from this repo's usual crash-on-malformed.

**Fix sketch:** return `None` from `_decode_image` when the url is empty, and skip
the Anthropic base64/url arm when `data`/`url` is falsy.

**Status: fixed** — rounds 76/77 — `tests/test_fix_round76.py`, `test_fix_round77.py`.

**Tests:** tests/test_fix_round76.py, tests/test_fix_round77.py.

### 169. Cline's on-demand 401 refresh bypasses the sweeper's lock and circuit — a rotating refresh token can be burned twice

**Severity:** 🟠 High (provider permanently marked dead until a human re-authenticates)
**Files:** `wiwi/providers/cline_auto_refresh.py:191-218` (the on-demand hook,
which builds its **own** `ClineAutoRefresh` worker at `:191`) vs `:112-121`
(the sweeper, which takes a per-provider `asyncio.Lock` and re-reads the record
under it); wiring at `wiwi/server/app.py:811-820`

`_do_refresh` is lock-guarded and re-reads the record under the lock. The hook
performs the identical refresh with **neither the lock nor the shared circuit**:
it constructs a fresh worker, so `worker._circuit` is a different object from
the sweeper's.

`cline_oauth.py:12` states the hazard in its own module docstring — *"Refresh
tokens rotate. Each refresh consumes the old refresh_token"* — and
`_UNRECOVERABLE_CODES = {"invalid_grant", "invalid_request"}` (`:39`) maps the
resulting error to `mark_dead(provider)`, which `CircuitBreaker.blocked` treats
as permanent.

**Trigger:** the access token is inside the refresh lead window (the documented
steady state), and a sweeper tick and a client 401 land in the same window.
Both read the same `refresh_token` from the config store and both POST
`/auth/refresh`; the second presents a consumed token.

**Consequence:** the provider stops refreshing until an operator
re-authenticates. The hook path has no lock, so N concurrent 401s make it
N-way. `workbuddy_auto_refresh._worker_for` already routes through the shared
worker — the Cline path is the one that does not.

**Fix sketch:** route the hook through the shared `state.cline_refresh` worker
so both paths share one lock and one circuit (mirroring WorkBuddy).

**Status: fixed** — round 84 — `tests/test_fix_round84.py`.


### 170. WorkBuddy treats an unknown expiry as "refresh now", rotating every key every sweep

**Severity:** 🟡 Medium (needless token rotation + DB writes; false `mark_dead`)
**Files:** `wiwi/providers/workbuddy_auto_refresh.py:118-120` and `:133-135`;
`wiwi/providers/workbuddy_auth.py:177-178`

`expires_within_lead(expires_epoch)` returns `True` when `expires_epoch <= 0`,
and the sweeper uses it as the *only* due-check. `parse_auth` defaults a missing
`expiresAt` to 0, so a stored record with no expiry is "always due".

**Consequence:** with `TICK_S = 60`, such a key gets one POST to
`/v2/plugin/auth/token/refresh` and one `update_key_secret` DB write per minute,
indefinitely — each consuming a rotating refresh token. The first transient
failure trips the circuit, and any 401/403 or "session dead" response calls
`mark_dead` on a key that was never actually expired. The Cline path treats a
missing/unparseable `expires_at` as *not due* (`cline_auto_refresh.py:108-110`),
so the two OAuth sweeps disagree.

**Fix sketch:** skip when `expires_at <= 0`, or keep the `<= 0` short-circuit in
`needs_refresh` only and drop it from the sweeper's due-check.

**Status: fixed** — round 84 — `tests/test_fix_round84.py`.


### 171. A failed request-log DB write silently discards the batch and leaves the drop counter at 0

**Severity:** 🟠 High (unrecoverable accounting loss reported as "healthy")
**Files:** `wiwi/logging_core/subsystem.py:121` (the only increment of
`dropped_request_logs`), `:208-215` (`_emit`, whose `except Exception` logs but
never counts); surfaced at `wiwi/server/app.py:1653`, `:1669` and
`wiwi/server/metrics.py:65`

`_emit` awaits `write_requests(batch)`; on failure the exception is caught and
logged, and the batch is discarded. Nothing counts it — `dropped_request_logs`
is incremented **only** on `asyncio.QueueFull`.

**Trigger:** the database is unavailable, locked, or rejecting writes (SQLite
`database is locked`, Postgres failover, disk full).

**Reproduced** against the real `_emit` with a failing sink:

```
request_log_db_write_failed    count=1 error='database is locked'
dropped_request_logs before: 0
dropped_request_logs after : 0
```

**Consequence:** `/health` reports `"dropped_request_logs": 0` and `/metrics`
exports `wiwi_request_logs_dropped_total 0` while every request row is lost —
and `request_logs` is the only durable copy (the SSE ring is capped and dies
with the process). A spend audit during a DB outage reads as "everything is
fine" and under-reports cost, tokens and per-key budget consumption with no
signal on any operator surface.

**Fix sketch:** increment the counter (or a sibling
`failed_request_log_writes`) in the `except` by `len(batch)`, and expose it
beside the queue-full counter in `/health` and `render_metrics`.

**Status: fixed** — round 82 — `tests/test_fix_round82.py`.


### 172. An invalid `prometheus_path` crashes the gateway at startup instead of degrading metrics

**Severity:** 🟡 Medium (a config typo is a total outage, not a lost feature)
**Files:** `wiwi/config.py:218-220`, `wiwi/server/app.py:1657-1670`

`@app.get(metrics_path)` runs inside `create_app`, so a non-literal path (e.g.
`/metrics/{job}`, or `metrics` with no leading slash) makes FastAPI raise at
route registration and the process fails to boot. Nothing validates the value
against the mounted routes.

**Fix sketch:** validate at config-parse time (`startswith("/")`, no `{`/`}`),
or catch the registration error, warn, and leave metrics disabled.

**Status: fixed** — round 83 — `tests/test_fix_round83.py`.


### 173. Audit and proxy log losses are never counted at all

**Severity:** 🟡 Medium (an admin mutation can succeed while its audit row is lost)
**Files:** `wiwi/logging_core/subsystem.py:128-129` (`log_proxy`'s
`except QueueFull: pass`), `:141-148` (`log_audit`)

`write_audit` is awaited on the request path, so a DB failure there can lose the
audit row while the mutation succeeds; the ring copy is the only trace and is
capped. Proxy-log drops are silent by design but invisible to `/health` and
`/metrics`. #38 covered the missing audit *ring* (fixed); this is the uncounted
loss.

**Fix sketch:** one shared `dropped_log_events` counter across the three
streams, exposed like #171's.

**Status: fixed** — round 82 — `tests/test_fix_round82.py`.


### 174. Entitlement 401/403 retires healthy pool keys as "rejected credentials"

**Severity:** 🟠 High (one account-policy refusal can cool off an entire provider)
**Files:** `wiwi/providers/base.py` (`error_from_provider_status`,
`status_for_key_pool`), `wiwi/router/router.py:1236-1243`

**Trigger:** request `muse-spark-1.3-contributor-free` through the `io`
deployment (OpenCode Zen) from a workspace without paid entitlement. The log
showed `io/3` and `io/4` marked rejected by a request that never reached an
auth check.

`error_from_provider_status` mapped **every** 401/403 to
`authentication_error`, so `status_for_key_pool` reported it to the pool and
`ProviderAccount.on_result` ran `err_count += 2` (retiring after
`key_max_consecutive_fails`). But Zen's Console returns
`403 FreeTierError` for a free model on any workspace without a paid plan —
keyless and with a valid bearer alike (a bearer only clears the session gate,
then hits `401 CreditsError: No payment method`). Every such refusal therefore
burned two healthy keys and cooled the provider, a self-inflicted outage on top
of the upstream policy decision.

**Fix:** classify account refusals by machine `type`/`code`
(`FreeTierError`, `CreditsError`, `MonthlyLimitError`, `UserLimitError`,
region/data-policy, `ModelError`) plus a phrase fallback, splitting them into
billing (→ 402) and policy (→ 403), both `permission_error`; have
`status_for_key_pool` return `None` for a `permission_error` 401/403/402. The
402 is what stops clients rendering a billing gap as "re-enter your API key"
(Cline classes every 401/403 as an auth error). Plain 403s and genuine
`AuthError` bodies keep the historical credential semantics. Retryable stays
True so the request still fails over.

**Status: fixed** — `tests/test_fix_round71.py`; see `UPDATE.md` for the full
probe matrix.

**Corrected by #267 (2026-09-19):** the *classification* above holds — a 403/401
that is not a credential failure must not retire pool keys, and the fix stops
it doing exactly that. What was wrong is the stated cause. `FreeTierError` is
not an account-wide free-tier gate that "is not recoverable by any header
combination": it is a request-shape rejection that wiwi can and now does avoid,
and the keyless path succeeds where a real account key 429s. Read the probe
matrix in `UPDATE.md` alongside #267 rather than on its own.

**Follow-up (round 74):** the fix also made `status_for_key_pool` return
`None` for these errors, and `execute_with_retries`' proxy-log line was gated
on that same value — so the *only* operator-visible trace of why a request
failed over (`upstream <status> on ...`) vanished exactly for entitlement
refusals. The warn is now emitted for a `permission_error` on 401/402/403 with
explicit account-entitlement wording
(`io refused zen/muse-spark-... [main] — account entitlement (402): ...`); the
key-pool charge stays gated on `status` and remains untouched
(`tests/test_fix_round74.py`).

### Disproven on retest (do not re-investigate)

- **NIM adopt-branch alias loss** (`nim_adapter.py:366-383`): reported as
  emitting fragments out of order and leaking `_nim_arg_type`. Retested with a
  real aliased schema (`{"type": ...}` → `_nim_arg_type`) through
  `fresh_adapter("nvidia-nim")` on both orderings — the args concatenate to
  `{"type":"x"}` correctly and the alias *is* restored. Not a defect.
- **Rate-limiter scope collision via a custom key** (`ratelimit/memory.py:126`):
  reported that a custom key's plaintext becomes `key_id`, so a key literally
  named `global` could charge into the shared `global:rpm` window. Retested:
  `AuthInfo.key_id` is always `v.id` (`auth/service.py:243`), the minted
  `"k"+hex`, never the plaintext. Not reachable.

---

## 🟠 Open — round 75 audit sweep: newly confirmed defects (2026-09-17)

Found by a seven-agent read of the backend (`core/`, `streaming/`, `router/`,
`auth/`, `ratelimit/`, `cache/`, `wire/`, `providers/`, `server/`,
`logging_core/`, `web/`), each finding then reproduced independently against
the real modules before being recorded here. Entries marked **disproven** were
reported by an agent but did **not** reproduce on retest — recorded so a later
agent does not chase them.

> **Environment hazard found during this sweep.** `import wiwi` is
> **CWD-dependent**: from `/tmp` it resolves to a *different checkout*
> (`/teamspace/studios/this_studio/Fionn/wiwi`), not this one. One agent's
> first fuzz run reported 52 decoder crashes that vanished entirely once
> `PYTHONPATH` was pinned to `/teamspace/studios/this_studio/wiwia`. Any repro
> script must assert `wiwi.__file__.startswith("/teamspace/studios/this_studio/wiwia/")`
> or pin `PYTHONPATH`, or it silently tests the wrong tree.

### 177. The Anthropic `ping` keep-alive is yielded as raw `bytes` into a pipeline that only accepts deltas — it never reaches the client

**Severity:** 🟠 High (the round-64 fix for this is inert; idle proxies still reap long turns)
**Files:** `wiwi/core/gateway.py:710-723` (emits), `wiwi/server/app.py:1518` and `:1530` (consumes)

AUDIT #156 item 14 recorded "`ping` documented but never emitted" as **fixed**
in round 64 by adding `ping_frame` to the gateway pump. The frame is emitted —
but as raw `bytes`, while every consumer treats each yielded item as an
`IRStreamDelta`:

```python
# gateway.py:710
ping_frame = (b'event: ping\ndata: {"type": "ping"}\n\n'
              if ctx.surface == "messages" and ping_s > 0 else None)
...
# app.py:1530 — every item goes through the encoder, and a falsy result is dropped
chunk = encoder.feed(d)
if chunk:
    async for t in _emit(chunk):
        yield t
```

`AnthropicStreamEncoder.feed(d: dl.IRStreamDelta)` type-checks on delta classes
only; a `bytes` argument falls through every `isinstance` branch and returns
`None`, so the chunk is discarded. Verified against the real encoder:

```
gateway yielded ping frames: 2
client received 'event: ping' occurrences: 0
client received message_start: 2
```

All three stream encoders (`AnthropicStreamEncoder`, `ChatStreamEncoder`,
`ResponsesStreamEncoder`) declare `feed(self, d: dl.IRStreamDelta)` and none
has an `isinstance(d, bytes)` branch, so the seam cannot work by construction.

**Consequence:** `stream_ping_interval_s` (default 15 s) is a no-op. A long
thinking turn that produces no upstream bytes for >30 s is still reaped by an
idle proxy/ALB — the exact failure #156 item 14 was raised to fix. No test in
`tests/` asserts the client receives a ping frame (`grep -rn 'event: ping'
tests/` is empty), which is why the round-64 fix shipped unverified.

**Fix sketch:** have the pump yield a typed keep-alive delta and let each
encoder render it (only Anthropic emits a named event), or special-case
`bytes` in `_stream_response` and pass it straight to `_emit` instead of
through `encoder.feed`.

**Status: fixed** — round 81 — `tests/test_fix_round81.py`.


### 178. `_emit_server_call` closes the wrong content block — duplicate `content_block_stop` and a block that never closes

**Severity:** 🟠 High (malformed SSE on the Anthropic surface; prose and signature lost)
**File:** `wiwi/wire/anthropic_messages.py:608-636` (`_emit_server_call`), `:556-590` (`_flush_deferred`)

When a client tool block is open and interleaved text/thinking has been
deferred, a provider-hosted call arriving with its result runs this sequence:

1. `_close_block()` closes the open tool block (correct).
2. `_flush_deferred()` emits the deferred text and **sets `self._open_block =
   "text"`** and bumps `_block_idx`.
3. `_emit_server_call` then emits `content_block_start` for the server call and
   its `content_block_stop` using `self._block_idx` — the index of the *text
   block that is still open* — and increments, leaving `_open_block` set.

Verified against the real encoder (frames shown per `feed`):

```
ToolCallOpen(1, Grep)        -> content_block_start index=0 type=tool_use
TextDelta('Searching now.')  -> (no bytes — deferred)
ToolCallOpen(3, web_search)  -> (no bytes)
ToolCallClose(3)             -> (no bytes)
ServerToolResultDelta(3)     -> content_block_stop  index=0
                                content_block_start index=1 type=text
                                content_block_delta index=1 text_delta
                                content_block_start index=2 type=tool_use
                                content_block_stop  index=2
                                content_block_stop  index=2   <-- DUPLICATE
                                content_block_start index=3 type=web_search_tool_result
                                content_block_stop  index=3
```

Block 1 (the text) is opened and never stopped; block 2 receives two stops.
With thinking instead of text, a later `signature_delta` is stamped onto the
*result* block.

**Consequence:** Claude Code's SDK sees a `content_block_stop` for a block that
is not open and a text/thinking block with no stop. The message fails to parse
or the block is dropped, so the model's prose (and its signature) are lost from
the turn and from replayed history. Reachable only via the client-tool variant
(a single upstream `server_tool_use` plus text closes its own block correctly).

**Fix sketch:** `_flush_deferred()` already returns its frames — have
`_emit_server_call` capture them and call `_close_block()` afterwards (or set
`self._open_block = None` whenever the deferred buffer is drained).

**Status: fixed** — round 76 — `tests/test_fix_round76.py`.


### 179. A failed spend charge is reported to the caller as success — the budget cap silently stops being enforced

**Severity:** 🟠 High (fail-open accounting: unlimited spend while the write path is down)
**File:** `wiwi/server/app.py:1032-1035` (`record_spend`), consumed at `:1403` (non-streaming) and `:1599` (streaming)

```python
try:
    recorded = await state.auth.update_spend(key_id, cost)
except Exception:  # noqa: BLE001
    return True          # exception == "charge succeeded"
```

On any `UPDATE vkeys SET spend_to_date …` failure (SQLite `database is
locked`, Postgres failover, pool exhaustion) the charge is discarded and the
caller is told it succeeded. `apply_spend_trueup` on the same path is wrapped
in `contextlib.suppress`, so the true-up is lost too.

**Reproduced** with a failing `update_spend` sink: three requests each costing
2.0 against a `max_budget=1.0` key all return **HTTP 200** and `spend_to_date`
stays `0.0` — the cap is never crossed.

**Consequence:** a hard budget cap is unenforced for as long as the write path
fails, with no counter, no `/health` field and no metric. This is the fail-open
mirror of AUDIT #24 (which covers the *old* behaviour where an un-suppressed
failure produced a 500 after a successful completion); #52 covers the `False`
return path, not this exception path.

**Fix sketch:** on exception, log at `error` with the `key_id`/cost, increment
a `spend_charge_failures` counter surfaced in `/health` and `render_metrics`,
and never return `True` silently.

**Status: fixed** — round 81 — `tests/test_fix_round81.py`.


### 180. Prometheus series declared `counter` are recomputed from a 500-event ring each scrape — they decrease

**Severity:** 🟡 Medium (breaks `rate()`/`increase()` for every operator dashboard)
**Files:** `wiwi/server/app.py:1687-1691` (scrape reads the ring), `wiwi/server/metrics.py:92`, `:102`, `:136-149`

`render_metrics(events, …)` sums over the in-memory LogEvent ring
(`deque(maxlen=500)`), but the exposition declares these as counters:

```
wiwi_requests_total 500          # TYPE wiwi_requests_total counter
wiwi_cost_total 0.500000         # TYPE wiwi_cost_total counter
```

**Reproduced** by scraping three times with a rotating workload:

```
scrape1 cheap        wiwi_requests_total 500 / wiwi_cost_total 0.500000
scrape2 EXPENSIVE    wiwi_requests_total 500 / wiwi_cost_total 500.000000
scrape3 cheap        wiwi_requests_total 500 / wiwi_cost_total 0.500000
```

`wiwi_cost_total` goes 0.5 → 500 → 0.5 while the gateway only ever serves more
traffic; `wiwi_requests_total` is pinned at the ring size forever. The same
applies to `wiwi_tokens_total`, `wiwi_prompt_cache_hits_total`,
`wiwi_response_cache_hits_total` and `wiwi_usage_estimated_requests_total`.

**Consequence:** PromQL reading a counter reset treats each eviction as a
process restart, so `rate()` produces negative or wildly wrong values.
`docs/API_REFERENCE.md:79` documents the ring as the data source; what is wrong
is the `# TYPE … counter` declaration. AUDIT #39 covers label escaping in the
same file and #12 the route's auth gap — neither covers this.

**Fix sketch:** either declare them `gauge` with a `_window` suffix, or keep
process-lifetime monotonic counters in `LoggingSubsystem` (as
`dropped_request_logs` already is) and pass them into `render_metrics`.

**Status: fixed** — round 82 — `tests/test_fix_round82.py`.


### 181. Admin provider delete/rename mutates in-memory routing before the DB write, with no rollback

**Severity:** 🟡 Medium (process state and DB disagree in both directions; no audit row)
**Files:** `wiwi/server/app.py:2217-2221` (`DELETE`), `:2300` + `:2343-2344` (`PATCH` rename); contrast `:2169-2172` (`POST`, which persists first)

```python
del state.router.providers[name]                      # in-memory first
if state.config_store:
    await state.config_store.delete_provider(name)    # DB second — can raise
await state.logs.log_audit(...)                       # never reached on failure
```

**Reproduced** with all DB writes failing: `DELETE /admin/providers/ghost`
returns **500** (so the operator retries) but *did* take effect in memory and
not in the DB — the provider and its plaintext key come back after a restart.
The rename path is the mirror image: `PATCH` returns 500 while the process
routes and bills under a name the DB has never heard of. In both cases
`log_audit` is never reached, so the mutation leaves no audit trace.

**Consequence:** the running gateway's routing state is not what the admin UI
(DB-backed) shows, and the next restart silently reverts or resurrects
providers. `POST /admin/providers` and `DELETE /admin/keys/{name}/{label}`
already persist *before* mutating in-memory state — this is a second, opposite
convention inside the same file, which `CLAUDE.md`'s consistency rules prohibit.

**Fix sketch:** persist first and mutate in-memory only on success (matching
the create path), or roll the in-memory mutation back before re-raising.

**Status: fixed** — round 81 — `tests/test_fix_round81.py`.


### 182. A typoed `os.environ/NAME` silently deletes a provider and its model entries at config load

**Severity:** 🟡 Medium (a one-character typo becomes a 404 on every model that provider served)
**Files:** `wiwi/config.py:423-440` (`_validate`'s empty-key filter), `:56-58` (`_interpolate` returns `""` for a missing var)

**Reproduced:** a config declaring two providers and two models, where both
keys resolve to unset env vars, loads with **no warning and no error**:

```
providers declared: 2  -> survived: []
models declared:    2  -> survived: []
```

**Consequence:** requests for the dropped model return a dialect-correct
`404 model not found`, which reads as a routing problem rather than a config
one, and `GET /health` still reports `status: ok` (see #183). The silent filter
is deliberate for the shipped `wiwi.yaml.example` (eleven optional providers),
but it is applied identically to a hand-written config where the typo *is* the
bug.

**Fix sketch:** log a `structlog` warning naming each dropped provider and the
env var it resolved from, or require an explicit `optional: true` marker.

**Status: fixed** — round 83 — `tests/test_fix_round83.py`.


### 183. `/health` reports `status: ok` for a gateway with zero providers — and the Docker `HEALTHCHECK` probes it

**Severity:** 🟡 Medium (a process that cannot serve a single request is marked healthy)
**Files:** `wiwi/server/app.py:1671-1675`; probe at `Dockerfile:51-52`; consumer `web/src/pages/Settings.tsx`

```python
return {"status": "ok", "groups": len(app.state.wiwi.router.groups),
        "providers": len(app.state.wiwi.router.providers), ...}
```

`status` is a constant — never derived from `providers`/`groups`. Verified with
an empty config: `/health` returns `200 {"status": "ok", "providers": 0}` while
every completion 404s. The `HEALTHCHECK` only checks for a 200, so a container
with no usable provider key stays "healthy" indefinitely, and the console's
green badge reads the same constant.

**Fix sketch:** compute `status` from the router (`degraded` when there are no
providers or no available group), keep HTTP 200 so liveness stays separate from
readiness, and have the console render the degraded state.

**Status: fixed** — round 81 — `tests/test_fix_round81.py`.


### 184. A non-string `text`/tool `name` reaches the IR and crashes `flatten_request_text` with a 500

**Severity:** 🟠 High (HTTP 500 with an `internal gateway error` body, on the success path, after the upstream was billed)
**Files:** `wiwi/wire/openai_responses.py:245` (no `isinstance` guard, unlike its siblings at `:243-244` and `:290-291`); crash at `wiwi/core/gateway.py:1522` (`" ".join(out)`), reached from `:616`, `:1248`, `:1413` and `wiwi/server/app.py:1658`

**Reproduced** at the IR level and end to end:

```
POST /v1/responses {"type":"input_text","text":5}
  -> IR TextPart.text = 5   (typed str)          <- contract break
  -> flatten_request_text RAISED: sequence item 0: expected str instance, int found

chat tool name=5    -> flatten TypeError: sequence item 1: expected str instance, int found
resp tool name=5    -> flatten TypeError: sequence item 1: expected str instance, int found
anth tool name=5    -> flatten TypeError: sequence item 0: expected str instance, int found
chat toolcall name=5-> flatten TypeError: sequence item 0: expected str instance, int found
chat tool desc=5    -> flatten TypeError: sequence item 2: expected str instance, int found
```

It fires whenever the provider omits usage and the estimator runs — which the
comment at `gateway.py:608-615` calls "the NORMAL case, not an anomaly" for
streaming-only upstreams.

**Consequence:** a 500 (`internal gateway error`) instead of a dialect-correct
400, raised *after* the upstream has already been billed.

**Fix sketch:** coerce at the decode sites the way `anthropic_messages.py:59`
already does — `raw if isinstance(raw, str) else ""` — and optionally harden
`flatten_request_text` with `str(...)`.

**Status: fixed** — rounds 75/77 — `tests/test_fix_round75.py`, `test_fix_round77.py`.

**Tests:** tests/test_fix_round75.py, tests/test_fix_round77.py.

### 185. `stop` as a non-list scalar is forwarded upstream verbatim

**Severity:** 🟡 Medium (upstream 400 misattributed far from its cause)
**Files:** `wiwi/wire/openai_chat.py:226` (`(body.get("stop") or [])`), `wiwi/wire/openai_responses.py:314` (`(stop_raw or [])`); sinks `providers/openai_adapter.py:225`, `anthropic_adapter.py:456`, `gemini_adapter.py:130`

`GenParams.stop` is typed `list[str]`, but any truthy non-list passes the
`or []` guard. **Reproduced:** `{"stop": true}` decodes to `gen_params.stop=True`
and is emitted as Anthropic `stop_sequences=True`; likewise `{"a":1}` and `7`.
The Anthropic codec already filters correctly (`anthropic_messages.py:340-346`);
this is AUDIT #127's shape one level up (the scalar rather than the list item).

**Fix sketch:** mirror the Anthropic filter in both codecs —
`stop=[s for s in (stop_raw if isinstance(stop_raw, list) else [stop_raw]) if isinstance(s, str)]`.

**Status: fixed** — round 77 — `tests/test_fix_round77.py`.


### 186. `name`/`description` as explicit JSON `null` are forwarded upstream as null

**Severity:** 🟡 Medium (upstream 400 naming no offending block; tool invisible to the model)
**Files:** `wiwi/wire/openai_chat.py:194-195`, `:130`; `wiwi/wire/openai_responses.py:134-135`, `:277`

`fn.get("name", "")` defaults only a *missing* key — an explicit `null` passes
through. **Reproduced:** `IR ToolUsePart.name = None` →
`{"function":{"name":null,...}}` on the OpenAI wire and
`{"type":"tool_use","name":null}` on the Anthropic one; likewise `Tool.name`
and `description`. `ToolUsePart.__post_init__` already coerces a non-string
`id`; `name` has no such coercion. Same class as #168 (malformed input
forwarded rather than dropped).

**Fix sketch:** coerce `name`/`description` at the codec boundary, or extend
the existing `__post_init__` pattern to `ToolUsePart.name` / `Tool.name`.

**Status: fixed** — rounds 76/77 — `tests/test_fix_round76.py`, `test_fix_round77.py`.

**Tests:** tests/test_fix_round76.py, tests/test_fix_round77.py.

### 187. `media_type` and `top_k` as typed-wrong scalars are forwarded upstream verbatim

**Severity:** ⚪ Low (same class as #186; narrow triggers)
**Files:** `wiwi/wire/anthropic_messages.py:71` (`mime=src.get("media_type", "image/png")`), `wiwi/wire/openai_responses.py:316` (`top_k=body.get("top_k")` — no `coerce_int`, unlike `max_output_tokens` on the line above)

**Reproduced:** `ImagePart.mime = None` →
`{"source":{"media_type":null,...}}`; `gen_params.top_k = '7'` →
Anthropic `body['top_k'] = '7'`. `openai_chat.py:223-225` and
`anthropic_messages.py:356` both guard their equivalents; the Responses decoder
is the only one that skips `coerce_int` for `top_k`.

**Fix sketch:** `mime = v if isinstance(v, str) else "image/png"`;
`top_k=ir.coerce_int(body.get("top_k"))`.

**Status: fixed** — rounds 76/77 — `tests/test_fix_round76.py`, `test_fix_round77.py`.

**Tests:** tests/test_fix_round76.py, tests/test_fix_round77.py.

### 188. `release()` skipped the global RPM/TPM refund for an empty `key_id` — **fixed during this sweep**

**Severity:** 🟡 Medium (latent — no caller passes an empty key id today)
**File:** `wiwi/ratelimit/memory.py` (`release`, the `if not key_id: return` guard)

The guard's stated rationale was "an empty key_id would build the literal
scopes `:rpm`/`:tpm`". But `release()` only ever *reads* windows (`.get`) and
never creates one, so the guard prevented nothing — while returning **before
both loops**, so the shared `global:rpm`/`global:tpm` windows that `check("")`
had reserved were never refunded.

**Reproduced:** with `global_rpm=1`, a request admitted under `""` and then
released still left its slot; the next request was refused with
`retry_after=60` — one reconnect burns a global slot for the full window.

**Consequence:** latent, because the only production caller
(`server/app.py:1059`) passes `info.key_id`, which `authenticate` always
populates (`auth/service.py:179`, `:243`).

**Status: fixed** during this sweep — the guard now skips only the *key-scoped*
lookups and both global loops always run (`tests/test_fix_round70.py`, which
had been failing for exactly this reason and now passes).

### 189. A late reconciliation could be admitted past the TPM cap — **fixed during this sweep**

**Severity:** 🟠 High (over-admission: the cap stops binding)
**File:** `wiwi/ratelimit/memory.py` (`record_tokens`'s resolver)

When a request's own reservation had aged out of the 60 s window while the
request was still streaming, `record_tokens` fell back to adopting *another*
request's live estimate. **Reproduced** against the committed revision: with a
1000-token cap and two in-flight requests (600 + 300), ageing out the first and
reconciling it at 700 left the window holding only `700` — the second request's
300-token reservation had been overwritten and flipped to confirmed, so a
further 300-token request was admitted against a window that was already full.

**Status: fixed** during this sweep — `record_tokens` now matches only the
caller's own id and appends the actual usage when it is gone, never adopting a
different request's estimate. The window holds the correct 1000 after the same
sequence (`tests/test_fix_round70.py`, 8 passing).

### Disproven on retest (do not re-investigate)

- **Decoder crash family (#122-#127) still live** — a 300-case junk-shape fuzz
  across all three `decode_request`s produced **0** non-`DialectError`
  exceptions. The first run's 52 "crashes" were an artifact of the
  `/teamspace/studios/this_studio/Fionn` checkout resolving ahead of `wiwia`
  (see the environment hazard note above).
- **Responses and Chat stream encoders** — an exhaustive sweep of 3,316
  contract-legal delta sequences found **0** violations (item pairing,
  `output_index`/`item_id` consistency, no args-delta against a missing or
  closed item, no reopened tool index). The Anthropic encoder was the only one
  that failed (#178).
- **#165 (hosted `web_search` with non-object args)** — not reproducible;
  `encode_response` with `raw_args="[1]"` returns a clean `web_search_call`
  with `query: ""`. The guard is in place.
- **Non-string scalars in most typed IR fields** — an annotation-aware walk
  (`get_type_hints`; `from __future__ import annotations` makes `f.type` a
  string, which silently defeats a naive checker) over ~50 junk variants per
  field found only the sites recorded above. `role`, `tool_choice`,
  `input`/`args`, `tool_use_id`, `strict`, `defer_loading`, `cache_control`,
  stop-items and `response_format` are all correctly guarded.
- **Journal-replay gate with `stream_journal_enabled: false`** — hypothesised
  to return an empty 200 SSE. Not reproducible: `JournalStore.is_active` is
  process-local, so an "active" journal cannot be forged from outside.
- **`wiwi/server/stats.py` and `wiwi/server/config_store.py`** — zero `except`
  clauses between them; every failure propagates.
- **Cancelled streaming connect leaks the upstream response** — reported as a
  leak on the `CancelledError` path's `started=False` branch, then **retracted
  by its own author**: the repro captured the response by patching
  `cm.__aenter__` with a closure over `cm`, and since `AsyncClient.stream()`
  returns a `contextlib._AsyncGeneratorContextManager`, that reference cycle
  delayed the async-generator finalizer that closes the response. With a clean
  `send()`-override capture (and an `httpx.Response.aread` tracer proving the
  pump really was inside `resp.aread()` with `started=False`), the response is
  closed within 100 ms at cancel times 0.2/0.4/0.6/2.0 s. **No leak** — httpx's
  own cancellation handling releases it. (The `started` gate at
  `gateway.py:1303` is still worth a second look for *other* reasons, but there
  is no demonstrated connection leak.)
- **HealthHealer probes the same (deployment, key) pair twice** — reported, then
  **retracted as a misreading of its own output**: `_collect_pairs` returned
  `('gpt-x','k_sick')` and `('gpt-x','k_healthy')`, which are two *distinct*
  pairs. The `seen` set works exactly as documented.
- **Unbounded resume storm / non-terminating resume loop / double `__aexit__`
  on the 401-refresh path / double `record_fail` / healer restoring an
  admin-disabled key / `_attempt_resume` refunding a settled slot /
  `run_one`'s `credited` set racing under `gather`** — each hypothesised and
  each disproven on retest (resume is bounded at 2 attempts for
  `max_resumes` 1/3/5; the branches are mutually exclusive; the admin-disable
  filter is applied at the source; `release_slot` is `estimated`-only).

### 190. `Deployment.settle_tokens` adopts another request's reservation — the per-deployment TPM/RPM cap under-counts

**Severity:** 🟠 High (a documented cap silently admits over-limit traffic)
**File:** `wiwi/router/router.py:461` (`target = w.find_estimated(request_id)`), resolver at `:302-311`

`find_estimated` is documented as "preferring an exact request-id match" but
**falls back to the newest estimated event** — the exact defect rounds 66/70
removed from `ratelimit/memory.py`'s `release`/`record_tokens`, still live in
the router. `settle_tokens` calls it *first*, so the fallback wins before the
id-based `find_event` is ever consulted.

**Reproduced** with a deployment at `tpm: 1000` and two in-flight requests:

```
after 2 reserves:            [('reqA', 500, True), ('reqB', 500, True)] total 1000
after prune (reqA aged out): [('reqB', 500, True)]                      total 500
after reqA settles 500 real: [('reqB', 500, False)]                     total 500
after reqB settles 200 real: [('reqB', 200, False)]                     total 200

TRUE billed usage in the window = 700; the window reports 200
rate_limited(est=800) -> False   (must be True: 700 + 800 > 1000)
```

`reqA`'s 500 real tokens were written onto `reqB`'s event. Three cascading
effects: the window under-counts, `release_slot("reqB")` no longer refunds
(its event is now `estimated=False`), and the rpm window settles the wrong
event. AUDIT #101's fix claimed the deployment path was correct.

**Fix sketch:** mirror `ratelimit/memory.py` — resolve `id → any event for that
id → append`, and never take `find_estimated`'s newest-estimated arm on the
id-carrying path.

**Status: fixed** — round 83 — `tests/test_fix_round83.py`.


### 191. Journal file paths are non-injective — a crafted `x-wiwi-stream-id` aliases another stream's journal

**Severity:** 🟠 High (re-opens the #67 cross-key journal disclosure through a different door)
**File:** `wiwi/streaming/tape_store.py:116-118` (`path_for`), consumed by `owner_of` (`:171`), `is_active` (`:120`), `read_after` (`:186`), `is_complete` (`:227`) and `server/app.py:1236-1243`

```python
def path_for(self, request_id: str) -> Path:
    safe = "".join(c for c in request_id if c.isalnum() or c in "-_")
    return self.dir / f"{safe}.jsonl"
```

Sanitizing by *stripping* is lossy, so distinct ids collide onto one file:

```
path_for('0d76f7149cb048dc')  ==  path_for('0d76f7149cb048dc.')   -> SAME FILE: True
'a/b' -> 'ab.jsonl'   'a.b' -> 'ab.jsonl'   'a b' -> 'ab.jsonl'   'a!b' -> 'ab.jsonl'
'!!!' -> '.jsonl'     ''    -> '.jsonl'
```

A request id is 16 hex chars (`core/context.py:41`) and is returned to every
client in the `x-wiwi-request-id` response header (`server/app.py:1382`), so
ids are not secret. Appending a single `.` to a known id yields a distinct
header value that resolves to the victim's exact journal path, so
`owner_of(replay_id)` reads the *victim's* owner record and the #67 owner gate
is satisfied by varying only the stripped characters.

**Consequence:** the #67 per-key scoping is only as strong as the path lookup.
This is not #67 itself (which was "no owner record at all"); it is the lookup
key being non-injective, which re-opens the same disclosure. Any journal in the
directory can also be aliased or appended into.

**Fix sketch:** reject rather than strip —
`re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id)`, treating a non-match as
"no journal" — or hash the id (`sha256(request_id)`) so the mapping is
injective.

**Status: fixed** — round 79 — `tests/test_fix_round79.py`.


### 192. `validate_tool_args` crashes on a malformed `properties`/`required` — mid-stream 502 and a cooled key

**Severity:** 🟠 High (caller-controlled shape; the turn is lost and a healthy credential is penalized)
**File:** `wiwi/streaming/validation.py:78` (`required = schema.get("required", [])`), `:88` (`properties = schema.get("properties") or {}`), `:90` (`spec = properties.get(prop)`)

The dict guard at `:48` checks only the **top-level** schema; the nested
keyword reads are unguarded, and all three codecs store the schema verbatim
(`wire/openai_chat.py:199`, `wire/anthropic_messages.py:247`,
`wire/openai_responses.py:139` coerce only a non-dict top level).

**Reproduced:**

```
properties: list   -> RAISED AttributeError: 'list' object has no attribute 'get'
properties: str    -> RAISED AttributeError: 'str' object has no attribute 'get'
required: null     -> RAISED TypeError: 'NoneType' object is not iterable
required: int      -> RAISED TypeError: 'int' object is not iterable
```

End to end the exception escapes `_validate_closed_tool_args` into the pump's
mid-stream handler (`core/gateway.py:1311-1320`), which calls
`_note_stream_failure` (key `err_count` 0→1, `active`→`cooling`) and emits a
`StreamError`: the client gets HTTP 200, some content frames, then an error
frame and **no** `finish_reason`/`[DONE]`.

**Consequence:** any caller sending a sloppy-but-plausible schema (a list of
property objects is a common hand-written mistake) aborts its own stream *and*
cools the deployment's key for every other user. This is the failure mode
already fixed at the top level (`validation.py:48`, AUDIT_REPORT H4); the fix
was not applied to the nested keywords.

**Fix sketch:** coerce at the seam — `required` to `[]` unless it is a list,
`properties` to `{}` unless it is a dict.

**Status: fixed** — round 79 — `tests/test_fix_round79.py`.


### 193. `_repair_truncated_json` emits invalid JSON on an even-length backslash run before a `\uXXXX`-shaped tail

**Severity:** 🟠 High (silent loss of the whole tool-argument object on a reachable input)
**File:** `wiwi/streaming/partial_json.py:97-110` (the surrogate-strip block), reached from `_repair_truncated_json` (`:37`)

AUDIT #139 added a parity check for the *odd*-run case but left the
high-surrogate branch at `:100-103` unconditional, so an **even** run (a
literal escaped backslash) followed by `u` + 4 hex digits is treated as a real
escape and stripped, leaving a dangling backslash.

**Reproduced:**

```
n=1 (odd ): '{"a": "\uD83D'    -> '{"a": ""}'     VALID
n=2 (even): '{"a": "\\uD83D'   -> '{"a": "\"}'    INVALID (JSONDecodeError)
n=3 (odd ): '{"a": "\\\uD83D'  -> '{"a": "\\"}'   VALID
n=4 (even): '{"a": "\\\\uD83D' -> '{"a": "\\\"}'  INVALID (JSONDecodeError)
```

Same for `uDE00`; `u0041`/`uZZZZ` are correctly left alone, so the bug is
specific to the surrogate range. Public entry points degrade too:
`parse_partial('{"a": "\\uD83D')` returns `({}, False)` and
`PartialJSONParser.finalize()` returns `{}` — the args silently become `{}`.

**Consequence:** a model emitting a Windows path or regex containing a literal
`\u` + 4 hex chars, cut mid-string, loses the entire argument object. Every
consumer degrades: `wire/openai_chat.py:121`, `wire/openai_responses.py:44`,
`core/gateway.py:523`, `streaming/resume.py:185,197`,
`providers/openai_adapter.py:383`, `providers/openrouter_adapter.py:218`.
Confirmed pre-existing at `4e68062^`, so this is a gap in #139's fix, not a
regression it introduced.

**Fix sketch:** gate the whole surrogate block on the same odd-run parity test
already computed at `:75-76`.

**Status: fixed** — round 79 — `tests/test_fix_round79.py`.


### 194. A JSON `null` usage counter slips past `.get(k, 0)` into `UsageFinal` — six adapters

**Severity:** 🟠 High (mid-stream `TypeError` after partial output; the key is cooled for a frame carrying no content)
**Files:** `providers/openai_adapter.py:491-494`, `providers/openrouter_adapter.py:302-305`, `providers/nim_adapter.py:305-308`, `providers/anthropic_adapter.py:748-753`; inherited by `cline`, `workbuddy`, `bai`

`.get(k, 0)` defaults a *missing* key, not a null one — the AUDIT #160 pattern
in a different field. The poisoned value reaches `core/gateway.py:1465`
(`u.prompt + u.output`) and `:1473` (`u.cached > 0`), both of which raise.

**Fix sketch:** coerce each counter (`v if isinstance(v, int) and not isinstance(v, bool) else 0`) at the six read sites.

**Status: fixed** — round 78 — `tests/test_fix_round78.py`.


### 195. Gemini: a null usage counter raises `TypeError` out of both decoders

**Severity:** 🟠 High (the strongest instance of #194 — an exception in the decoder itself)
**File:** `wiwi/providers/gemini_adapter.py:358-359` (stream), `:254-255` (sync)

`candidatesTokenCount + thoughtsTokenCount` has no guard at all, unlike its
sibling reads. Gemini 2.5/Vertex attach `usageMetadata` to *every* chunk, and a
proxy or a SAFETY-truncated candidate can serialize the counters as null.

**Fix sketch:** `(u.get("candidatesTokenCount") or 0) + (u.get("thoughtsTokenCount") or 0)` at both sites.

**Status: fixed** — round 78 — `tests/test_fix_round78.py`.


### 196. `NimAdapter`'s reused-index branch emits `ToolCallClose` with no preceding `ToolCallOpen`

**Severity:** 🟠 High (contract violation; the client receives an undispatchable `tool_use`)
**File:** `wiwi/providers/nim_adapter.py:384-386` (contrast `providers/openai_adapter.py:546-553`)

NIM's copy of the reused-index branch calls `_flush_buffered_args` and
`ToolCallClose` but never flushes `_pending_opens[idx]`, and — unlike the base
class — never resets `_tool_names[idx]`. Five other adapters agree; NIM alone
diverges, so `Close(0)` reaches the encoder before any `Open(0)`.
`AnthropicStreamEncoder` drops the close (`_close_block` returns `[]` for an
unregistered index), leaving `content_block_start` with `name: ""`.

**Fix sketch:** mirror `openai_adapter.py:550-553` — flush `_pending_opens.pop(idx)` before the Close.

**Status: fixed** — round 78 — `tests/test_fix_round78.py`.


### 197. OpenRouter's streaming `usage` read was never dict-gated, and its `reasoning_details` null text poisons history

**Severity:** 🟡 Medium (two sites; #154's status claims the first was fixed here, but it was not)
**Files:** `wiwi/providers/openrouter_adapter.py:298-301` (contrast `openai_adapter.py:485-490`, `nim_adapter.py:299-304`); `:184-195`

The first still reads `u = chunk.get("usage")` / `if u:` then `u.get(...)` where
its siblings use `isinstance(u, dict)`, so a non-dict `usage` raises
`AttributeError` out of the decoder. The second uses `rd.get("text", "")`, so
`{"type":"reasoning.text","text":null}` decodes to `ThinkingPart(text=None)`;
on replay `OpenAIAdapter._role_parts_to_content` does `reasoning += p.text` and
raises — a 500 on the *next* turn of the conversation, on all six Chat-wire
adapters. No test covers either (`tests/test_fix_round61.py` has no OpenRouter
usage case).

**Fix sketch:** `if isinstance(u, dict):`; `rd.get("text") or ""` (likewise `summary`/`data`).

**Status: fixed** — round 78 — `tests/test_fix_round78.py`.


### 198. `AuthService.create_key`'s per-owner key cap is a check-then-act race

**Severity:** 🟡 Medium (AUDIT #57's stated fix is defeated by concurrency)
**File:** `wiwi/auth/service.py:270` (`if owner_id is not None and await self.count_keys(owner_id) >= self.max_keys_per_user:`)

`count_keys` awaits, so concurrent creates all read the same pre-insert count
and pass. **Reproduced:** `asyncio.gather` of 5 creates against
`max_keys_per_user=1` minted **3** keys. The per-account key cap is the control
#57 added so a user cannot rotate around per-key budgets and rate limits.

**Fix sketch:** count and insert inside one transaction, or take a per-owner `asyncio.Lock`.

**Status: fixed** — round 80 — `tests/test_fix_round80.py`.


### 199. Admin key `reset_status` clears status but not `err_count` — one more failure re-retires the key

**Severity:** 🟡 Medium ("retry this key" silently reverts)
**File:** `wiwi/server/app.py:2042-2045` vs `wiwi/router/router.py:94-101` (`ProviderKey.recover`)

The handler sets `status="active"` and `cooldown_until=0.0` but leaves
`err_count` at its retirement value, so `on_result`'s
`err_count >= key_max_consecutive_fails` fires on the very next non-200 and
re-retires the key. `recover()`/`mark_recovered()` both reset the streak; the
admin path (the operator's explicit "this key is fine now") does not.

**Fix sketch:** call `key.recover()` in the `reset_status` branch.

**Status: fixed** — rounds 81/83 — `tests/test_fix_round81.py`, `test_fix_round83.py`.

**Tests:** tests/test_fix_round81.py, tests/test_fix_round83.py.

### 200. OpenRouter forwards a null mid-stream error message to the client

**Severity:** ⚪ Low (contract-invalid frame)
**File:** `wiwi/providers/openrouter_adapter.py:280` (`top_error.get("message", "OpenRouter stream error")`)

A null `message` passes the missing-key default and reaches the client as
`"message": null`. `ClineAdapter`'s equivalent arm coerces correctly
(`str(... or "Cline stream error")`).

**Fix sketch:** `msg = top_error.get("message") or "OpenRouter stream error"`.

**Status: fixed** — round 78 — `tests/test_fix_round78.py`.


### 201. `nim_tool_schema` alias collision silently destroys a real property

**Severity:** ⚪ Low (narrow trigger, silent data loss)
**File:** `wiwi/providers/nim_tool_schema.py:172` (`aliased_props[alias] = aliased`)

A tool declaring both a `type` parameter and a `_nim_arg_type` parameter: the
alias of `type` is written over the pre-existing `_nim_arg_type` entry, so the
model is told there is one parameter instead of two and `required` names it
twice. `_make_alias`'s `reserved` set is seeded with `set()` per call and only
guards aliases the function itself minted, so it never sees the incoming name.

**Fix sketch:** seed `reserved` with the schema's own property names before aliasing.

**Status: fixed** — round 78 — `tests/test_fix_round78.py`.


### 202. `ttl_seconds: 0` means opposite things on create vs update

**Severity:** ⚪ Low (API/scripting surface; the shipped UI cannot reach it)
**File:** `wiwi/auth/service.py:266,276` (`expires = now + ttl_seconds if ttl_seconds else None`) vs `:338` (`sets["expires_at"] = time.time() + _coerce_limit(val, "ttl_seconds")`)

`0` passes `_coerce_limit` (only `< 0` is rejected), then create's truthy guard
maps it to *no expiry* while update's `val is not None` branch maps it to
*expire immediately*. `None` is the documented "clear" value, so `0` reaching
the immediate-expiry branch is not the intended encoding.

**Fix sketch:** reject `ttl_seconds <= 0` in `_coerce_limit`, or treat `0` as "no expiry" in both.

**Status: fixed** — round 80 — `tests/test_fix_round80.py`.


### 203. `CostEngine.cost_with_status` prices negative token counts into a negative charge

**Severity:** ⚪ Low (needs a bad upstream count; the result is a credit, not a refusal)
**File:** `wiwi/cost/pricing.py:100-106`

The prompt side is floored by `max(0, prompt_tokens - cached_tokens)` only when
`prompt_includes_cached` is True; `completion_tokens` and the cache terms are
unguarded. **Reproduced:** `cost('m', 0, -100)` returns `-0.0002`. That flows to
`record_spend` → `update_spend`, where `add_cost <= 0` is an early
`return True` (`auth/service.py:400-401`), so the negative charge is silently
swallowed and `spend_to_date` is left untouched while the row logs a negative
cost.

**Fix sketch:** clamp all four token terms with `max(0, …)` at the top of `cost_with_status`.

**Status: fixed** — round 83 — `tests/test_fix_round83.py`.


### 205. Playground token-usage panel is permanently dead — the client never asks for usage

**Severity:** 🟠 Medium (a whole feature never renders; silent, not an error)
**File:** `web/src/pages/Playground.tsx:896-900`

The stream request omits `stream_options` entirely, so
`stream_options_include_usage` is `False` (`wire/openai_chat.py:252`), which
gates the **only** place a `usage` key is emitted on a stream frame
(`wire/openai_chat.py:402`). `Playground.tsx:160`'s `if (parsed.usage)` never
fires, so the stats strip (tokens in/out/total, `tok/s`) never renders — only
`ttft` survives, set locally in `onFirstToken`.

**Fix sketch:** add `stream_options: { include_usage: true }` to the request body.

**Status: fixed** — round 68 web — `.verify/webconsole/redgreen.py`.

**Tests:** browser harness (web/ has no test runner — AUDIT #113).

### 206. Built-in Providers "Add account" loses its `?type=` preset

**Severity:** 🟡 Medium (silent wrong default in a form the user then submits)
**Files:** `web/src/pages/BuiltinProviders.tsx:181` (the link), `web/src/main.tsx:236` (the redirect)

The link carries `/providers?type=<ptype>`, but `/providers` is a redirect:
`<Navigate to="/console/providers" replace />` — and `Navigate` resolves its
`to` through `parsePath`, which only splits off `search`/`hash` when they are
present in the `to` string. The query string is discarded, so
`Providers.tsx:351`'s `searchParams.get("type")` is `null` and the deep-link
effect (whose comment documents exactly this intent) never runs.

**Fix sketch:** `to={{ pathname: "/console/providers", search: location.search }}`, or link directly at `/console/providers?type=…`.

**Status: fixed** — round 68 web — `.verify/webconsole/redgreen.py`.

**Tests:** browser harness (web/ has no test runner — AUDIT #113).

### 207. Playground "Retry" after a failed request permanently duplicates the user's message

**Severity:** 🟠 Medium (corrupts the conversation sent upstream, compounding per retry)
**File:** `web/src/pages/Playground.tsx:940-941`, `:998-1003`

`send` already includes the user message in the history it passes, and
`runStream` appends it to state *again* from its `userText` argument. On
failure the cleanup removes **only the assistant** message, leaving the
duplicate user copy in state, while `failedRef` retains the original history —
so `retryFailed` replays it and appends yet another copy. The duplicate
survives a successful retry and is persisted to localStorage.

**Fix sketch:** on the error path also drop the appended user message, or have `retryFailed` pass `history` without `userText`.

**Status: fixed** — round 68 web — `.verify/webconsole/redgreen.py`.

**Tests:** browser harness (web/ has no test runner — AUDIT #113).

### 208. Proxy Logs: an expanded row collapses or jumps to a different row when a new event streams in

**Severity:** 🟡 Medium (the live tail, on by default, destroys the user's expansion)
**File:** `web/src/pages/ProxyLogs.tsx:184-196`, `:99`

The expansion key embeds the **array index** (`` `${l.ts}:${l.message}:${i}` ``)
while the live tail **prepends** to that array (`[evt, ...old].slice(0, 500)`).
Every prepend shifts every index, invalidating every stored key. With repeated
identical lines (the norm for a proxy log) the shifted key can collide with a
*different* physical row, so the panel re-attaches and shows one event's
request id under another's summary.

**Fix sketch:** key on stable identity (e.g. including `request_id`) rather than the index.

**Status: fixed** — round 68 web — `.verify/webconsole/redgreen.py`.

**Tests:** browser harness (web/ has no test runner — AUDIT #113).

### 209. OAuth Cline "Clear all" fires N concurrent read-modify-write deletes and silently keeps some defaults

**Severity:** 🟡 Medium (reports success while leaving defaults behind)
**File:** `web/src/pages/OAuthProviders.tsx:683-686` vs `wiwi/server/app.py:2877-2887`

The client dispatches every delete in one synchronous loop
(`for (const id of savedIds) removeOne.mutate(id);`), and each request is an
unsynchronized read-modify-write on one settings row. The `await` points yield,
so all N read the same pre-state and the last writer wins — four concurrent
deletes of `["a","b","c","d"]` leave `["a","b","c"]`.

**Fix sketch:** add a bulk-clear endpoint (or a `PUT` with the full remaining list) instead of N single deletes.

**Status: fixed** — round 68 web — `.verify/webconsole/redgreen.py`.

**Tests:** browser harness (web/ has no test runner — AUDIT #113).

### 210. Playground chat-list and message actions are hover-only and under the 44 px touch minimum

**Severity:** 🟡 Medium (violates the binding UI/UX rule; undiscoverable on touch)
**File:** `web/src/pages/Playground.tsx:529`, `:1451`, `:1486`

The rename/delete and copy/retry controls are revealed only by `group-hover`
with no `focus-within` or `@media (hover: none)` fallback, so on a touch device
they are invisible (`opacity-0` leaves them in the layout but unseen). Tap
targets are `p-1` around a `size={12}` icon — roughly 20×20 px against the
required 44×44.

**Fix sketch:** reveal on focus-within and at coarse pointers, and enlarge the hit area to 44 px.

**Status: fixed** — round 68 web — `.verify/webconsole/redgreen.py`.

**Tests:** browser harness (web/ has no test runner — AUDIT #113).

### 211. The stream pump's error handler is unguarded — a fault while pricing hangs the client forever

**Severity:** 🔴 Critical (a request that never completes: no terminal frame, no timeout)
**File:** `wiwi/core/gateway.py:1323-1331` (the `except Exception` arm's `else` branch, reached only when `started` is True); identical shape at `:1188-1197` (idle-timeout arm) and `:1243-1255` (clean-completion usage fallback)

The mid-stream failure handler awaits `_note_stream_failure`, `_price_partial`
and `queue.put(StreamError(...))` with **no `try/except` around them**, and
`_close_upstream()` sits after them at `:1331`. If any of those awaits raises,
the pump task dies before the terminal frame is queued — and nothing notices:
the consumer is parked on `await queue.get()` (`:715`) and the consumer's own
`finally` (`:795`) only runs once the consumer exits, which it never does.

**Reproduced end-to-end** through the real `Gateway` with a clean upstream that
dies mid-stream (forcing the `else` arm) and the estimator raising exactly as
AUDIT #184 describes:

```
CLIENT HUNG >8s with no terminal frame. deltas received: ['StreamStart', 'TextDelta', 'TextDelta']
Task exception was never retrieved
future: <Task finished name='Task-2' coro=<Gateway._pump() ...>
         exception=TypeError('sequence item 0: expected str instance, int found')>
  File "wiwi/core/gateway.py", line 1327, in _pump_once
    await self._price_partial(ctx, dep, usage_final, text_len)
  File "wiwi/core/gateway.py", line 1413, in _price_partial
    flatten_request_text(ctx), dep.model_id)
  File "wiwi/core/gateway.py", line 1522, in flatten_request_text
    return " ".join(out)
TypeError: sequence item 0: expected str instance, int found
```

Fault-injection controls isolate the mechanism: the same stream ends normally
(`['StreamStart','TextDelta','StreamError']`) without the fault, and hangs with
it. Note the trigger is not exotic — it fires on a *clean* upstream completion
whenever the provider omits usage, which the code itself calls "the NORMAL
case" for streaming-only upstreams, and AUDIT #184 makes it reachable from a
plain `/v1/responses` request with a non-string `text`.

**Consequence:** AUDIT #184 records the *non-streaming* symptom (a 500 body).
On the streaming path it is strictly worse: the pump dies, the consumer blocks
forever, no terminal frame is sent, and there is no timeout that would notice.
Every client retry re-runs the whole prompt. Fixing #184 at the decode site
removes the demonstrated reachability but not the class.

**Fix sketch:** wrap the handler body in `try/except Exception` and fall back to
`queue.put_nowait(StreamError(...))`; move `_close_upstream()` into a `finally`.

The structural fix is the required one — the `str()` coercion at
`flatten_request_text` (`:1522`) closes only the demonstrated trigger, not the
class. Verified: coercing the join makes the #184 case terminate cleanly
(`['StreamStart','TextDelta','UsageFinal','StreamError']`), but injecting a
fault into the handler's *first* await instead
(`_note_stream_failure`, e.g. a transient DB blip during failure accounting)
still hangs the client with no terminal frame:

```
fault=_note_stream_failure -> CLIENT HUNG >6s; deltas=['StreamStart','TextDelta','UsageFinal']
Task exception was never retrieved ... RuntimeError('db blip during failure accounting')
```

Any exception in that three-await sequence is fatal to the stream, so the
handler must be made total rather than individually patched.

**Status: fixed** — round 75 — `tests/test_fix_round75.py`.


### 212. An in-band upstream error frame is silently dropped on the OpenAI, NIM and B.A.I routes — a clean provider error is recorded as a mid-stream failure

**Severity:** 🟠 High (cools a healthy deployment and feeds the key's retirement ladder)
**Files:** `wiwi/providers/openai_adapter.py:480-496`, `wiwi/providers/nim_adapter.py:296-310`
(B.A.I inherits the base defect via `wiwi/providers/bai_adapter.py:59`)

**Trigger:** an OpenAI-compatible upstream reports a failure in-band as
`{"error": {...}}` with no `choices` array.

`decode_stream_event` built its delta list from `usage` + `choices` only, so the
error frame produced `[]` and vanished. The stream then ended with
`finish is None and not saw_terminal`, so `gateway.py` reported
`StreamError("upstream stream ended without completion", "connection")` **and**
called `_note_stream_failure` — cooling a healthy deployment and feeding the
key's error streak for an error the upstream had reported cleanly.

Same downstream mechanism as #76 (Gemini omitting `finishReason` on a
usage-bearing terminal frame), but a **different trigger**: there the decoder
returns `[]` for a shape it does not model, here for a shape it deliberately
ignores. Fixing one does not fix the other.

OpenRouter already mapped this shape (`openrouter_adapter.py:277-281`); Gemini,
Cline and WorkBuddy map their own variants. OpenAI, NIM (its own copy of the
decode loop, not a delegate) and B.A.I did not.

**Fix:** detect a dict-valued top-level `error`, flush any open tool calls, and
emit `StreamError(message=..., kind="status", etype=<provider type>)` before
returning. The provider's own `type` is preserved as `etype` because a
mid-stream frame carries no HTTP status, so `kind`/`status` alone cannot recover
the classification a client retries on. Covered by
`tests/test_fix_round68.py::test_openai_adapter_maps_in_band_error_frame_to_stream_error`,
`::test_openai_adapter_in_band_error_preserves_provider_type`,
`::test_nim_adapter_maps_in_band_error_frame_to_stream_error`, with
`::test_openai_adapter_still_decodes_a_normal_chunk_after_the_error_guard` as
the control that the guard does not swallow ordinary content frames.

**Status: fixed** — round 68 (2026-09-18).

### 213. `docs/MVP.md` and `docs/PLAN.md` claim `/v1/completions` and `/v1/embeddings` are live — neither route is registered

**Severity:** ⚪ Low (documentation; a client following the docs gets a 404)
**Files:** `docs/MVP.md:26` (F1, "… `/v1/completions`, `/v1/embeddings` … all live"), `docs/PLAN.md:63`

`grep -rn "v1/completions\|v1/embeddings" wiwi/` returns zero hits: no route is
registered for either. The inbound surfaces that exist are
`/v1/chat/completions`, `/v1/responses`, `/v1/messages`,
`/v1/messages/count_tokens` and `GET /v1/models` — which is what
`docs/API_REFERENCE.md` correctly lists. MVP.md and PLAN.md were the stale side.

**Fix:** correct both docs to list only the implemented surfaces and mark the
two as not implemented. **Docs corrected** — round 68 (2026-09-18). The routes
themselves remain unbuilt by choice: implementing them is a feature (new wire
codec + IR support + adapter branches + registry coverage), not a doc fix.

**Status: fixed** (docs) — both files now list only the implemented surfaces and mark `/v1/completions` and `/v1/embeddings` as not implemented.

### 214. `tiktoken` was imported but undeclared — a clean `uv sync` silently degraded cost/budget accounting

**Severity:** 🟠 High (spend enforcement silently falls back to a heuristic)
**Files:** `wiwi/cost/pricing.py:309,316` (the guarded import); `pyproject.toml` (the missing entry)

`_get_tiktoken_encoding` imports `tiktoken` inside a `try/except ImportError`, so
the fallback is **silent by design**. The package was absent from both
`pyproject.toml` and `uv.lock`, and was importable only because an unrelated
package had dragged it into the ambient environment — verified via an
`importlib.metadata` reverse-dependency scan. A clean `uv sync` from the
lockfile would therefore have disabled the accurate tokenizer and fallen back
to chars/4, which feeds **cost and budget enforcement**.

This is distinct from #33 (which was about the call *blocking* the event loop —
fixed by `estimate_tokens_async`); this is about the dependency being *absent*.
`docs/TECHSTACK.md:70,91` additionally claimed tiktoken was deliberately
rejected, which contradicted the shipped code.

**Fix:** declare `tiktoken>=0.7` in `[project].dependencies` and relock; correct
`docs/TECHSTACK.md` to describe the real behaviour. **Status: fixed** — round 68
(2026-09-18).

### 215. `sse-starlette` and `pydantic-settings` were declared runtime dependencies with zero import sites

**Severity:** ⚪ Low (dead dependencies in a shipped image)
**Files:** `pyproject.toml:9` (`sse-starlette`), `:12` (`pydantic-settings`)

`grep -rn "sse_starlette\|pydantic_settings\|BaseSettings\|SettingsConfigDict"
--include=*.py wiwi/ tests/` returns zero hits. `sse-starlette` is *deliberately*
bypassed — `wiwi/server/app.py` serves a plain `StreamingResponse` because
`EventSourceResponse` stalls behind `BaseHTTPMiddleware` on this stack — and
`pydantic-settings` was never adopted at all: config is hand-rolled on
`pydantic.BaseModel` with a custom loader in `wiwi/config.py`.

**Fix:** remove both from `[project].dependencies` and relock (the diff removes
exactly those two packages). Recorded in `docs/TECHSTACK.md`'s rejected table so
neither is re-added. **Status: fixed** — round 68 (2026-09-18).

### 216. `CLAUDE.md`'s Import Rule 1 forbids imports the codebase deliberately makes — and misses one it should forbid

**Severity:** ⚪ Low (documentation; the rule was unenforceable as written)
**Files:** `CLAUDE.md` (Import Rules §1); `wiwi/core/gateway.py:25,31,61`, `wiwi/core/recovery.py:27,28`, `wiwi/router/router.py:19`

Rule 1 said `core/`/`router/` must **never** import from `wiwi.providers`, but
six sites do, and `docs/CORE.md` states a narrower rule for `recovery.py`
("must never import router/gateway to avoid a cycle") that none of them
violate. A rule the codebase routinely breaks trains readers to ignore it.

The imports split cleanly: `providers.base` and `providers.registry` hold only
generic contracts (`WiwiError`, `ProviderKeyRef`, `error_from_provider_status`,
`status_for_key_pool`, `fresh_adapter`) with no provider-specific logic, whereas
`gateway.py:61` imports `providers.opencode_adapter.route_for_model` — a
**concrete adapter** — which is exactly the provider branching the rule exists
to prevent.

**Fix:** narrow Rule 1 to forbid all of `wiwi.wire` plus any concrete
`providers.<name>_adapter`, and explicitly allow `providers.base` /
`providers.registry`. **Docs corrected** — round 68 (2026-09-18). The
`opencode_adapter` import remains live and is a genuine violation of the
narrowed rule; it is left in place because `core/gateway.py` is on the hot path
and the fix (hoisting the route check behind a registry-provided predicate)
should not ride along with a docs pass.

**Status: fixed** (docs) — Import Rule 1 now forbids all of `wiwi.wire` plus any concrete `providers.<name>_adapter`, and explicitly allows `providers.base` / `providers.registry`. The `opencode_adapter.route_for_model` import in `core/gateway.py` remains live and is noted in the rule as a tracked violation.


### 217. A hosted tool-search call was rendered as a web search on the Responses surface

**Severity:** 🟠 High (the client is told a search happened that did not, and the real step is lost)
**File:** `wiwi/wire/openai_responses.py` — `_builtin_call_item`, plus both stream render sites

**Trigger:** any Anthropic-hosted `tool_search_tool_bm25_20251119` /
`tool_search_tool_regex_20251119` step on an `openai_responses` surface (Claude
Code's tool search routed to a Responses-backed model).

`_builtin_call_item` hardcoded `"type": "web_search_call"` for **every** hosted
builtin. The registry distinguishes them — `web_search` → `web_search`, but
`tool_search_bm25`/`tool_search_regex` → `tool_search` — and the OpenAI SDK
models them as separate output items:

| | web search | tool search |
|---|---|---|
| SDK model | `ResponseFunctionWebSearch` | `ResponseToolSearchCall` |
| wire `type` | `web_search_call` | `tool_search_call` |
| payload | `action: {type, query}` | `arguments`, `execution`, `call_id` |

**Fix:** key the item on the registry rather than on a hardcoded type —
`_builtin_is_tool_search()` maps the block name through
`bt.canonical_for`/`bt.wire_type_for` and emits the matching item shape. Applied
at all three render sites (sync `encode_response`, stream `_close_tool`, stream
`response.output_item.added`, which also needed the `ts_` id prefix). Covered by
`tests/test_fix_round68.py::test_responses_sync_labels_tool_search_as_tool_search_call`,
`::test_responses_web_search_still_renders_as_web_search_call` (control) and
`::test_responses_tool_search_item_matches_the_sdk_shape`.

**Status: fixed** — round 68 (2026-09-18).

### 218. A mid-stream failure left Responses output items open forever

**Severity:** 🟡 Medium (client state corruption; asymmetry with the Anthropic surface)
**File:** `wiwi/wire/openai_responses.py` — the `StreamError` arm of `feed`

**Trigger:** any mid-stream failure after a `response.output_item.added`.

`_completed()` sweeps every open output item before its terminal event (the
round-25 fix, AUDIT #111), but the `StreamError` arm emitted `response.failed`
immediately. A client that had already seen `output_item.added` never received
the matching `output_item.done`, so the item stayed `in_progress` permanently.
`AnthropicStreamEncoder` closes its blocks on the same path, so the two surfaces
disagreed about the same failure.

**Fix:** the error arm now runs the same close sweep (current item plus every
remaining index in `self._tools`) before emitting `response.failed`. Covered by
`tests/test_fix_round68.py::test_responses_stream_error_closes_open_message_item`
and `::test_responses_stream_error_closes_open_tool_item`.

**Status: fixed** — round 68 (2026-09-18).

### 219. The Anthropic encoder's deferred buffer grew without limit

**Severity:** 🟡 Medium (unbounded memory growth on a hostile or degenerate stream)
**File:** `wiwi/wire/anthropic_messages.py` — `AnthropicStreamEncoder._deferred`

**Trigger:** a stream that keeps emitting text/thinking while a `tool_use`
block stays open.

Text/thinking that arrives behind an open tool block is buffered in
`_deferred` and flushed when the tool closes (the round-64 fix, AUDIT #156) —
Anthropic content blocks are strictly sequential, so it cannot be emitted
inline. Nothing bounded the list, unlike the sibling guards
`MAX_TOOL_ARGS_BYTES` (`streaming/validation.py`) and the coalescer's
`max_bytes`. A degenerate or hostile stream could therefore grow it without
limit.

**Fix:** added `MAX_DEFERRED_CHARS` (256 KiB) and a `_defer()` helper both
append sites route through. Overflow evicts from the front (newest content
survives), trims the boundary entry from its head so the buffer lands exactly at
the cap, drops emptied entries so the flush loop never opens an empty block, and
handles a single delta larger than the cap. Covered by
`tests/test_fix_round68.py::test_anthropic_deferred_buffer_is_bounded`,
`::test_anthropic_deferred_keeps_the_newest_content_when_capped`,
`::test_anthropic_deferred_caps_a_single_oversized_delta`.

**Status: fixed** — round 68 (2026-09-18).

---

## 🔴 Critical — round 87 audit sweep: newly confirmed defects (2026-09-18)

Every entry below was **reproduced against the current checkout** (not inferred)
before being written here. Baseline at time of sweep: `pytest` 2288 passed,
`ruff` clean, `bun run build` green, `bun run lint` 0 errors — i.e. all of these
are live defects in a suite-passing tree.

> **Register-health note (read first).** `AUDIT.md`'s `**Status: fixed**` markers
> have drifted badly out of date. At the time of this sweep only 101 of ~212
> entries carried the marker, leaving ~111 that *look* open — but a 26-entry
> spot-check across every module (`app.py`, `auth/`, `gateway.py`,
> `tape_store.py`, `providers/`, `config.py`, `metrics.py`, `subsystem.py`)
> found **all 26 already fixed in the code**, each citing its own AUDIT number in
> a comment (e.g. #12, #25, #54, #71, #72, #73, #90, #94, #95, #103, #104, #106,
> #109, #122, #126, #171, #173, #182, #183, #191, #211 are all fixed).
> The reliable signal is the code, not the marker:
> `grep -rhoE 'AUDIT #?[0-9]+' wiwi/ tests/` returns ~133 addressed entries.
> The entries below are genuinely new or genuinely-recurring; the stale markers
> on older entries are a separate cleanup.

### 220. Any non-admin can strip the budget, rate-limit and model-allowlist caps an admin imposed on their own key

**Severity:** 🔴 Critical (privilege escalation; unbounded operator spend)
**File:** `wiwi/server/app.py:3300-3324` (`admin_patch_key`), reached via
`wiwi/auth/service.py:435-438` (`UPDATABLE_FIELDS` / `update_key`)

**Trigger:** a user who owns a virtual key PATCHes it with any policy field.

`admin_patch_key` verifies only **ownership** for a non-admin actor, then falls
through to the **full admin write path**:

```python
if actor.role != "admin":
    owner = await state.auth.key_owner(key_id)
    if owner != actor.id:
        return _err(403, "permission_error", "not your key", request)
# ... no role check on the write itself
fields = {k: body[k] for k in state.auth.UPDATABLE_FIELDS if k in body}
```

`UPDATABLE_FIELDS` is `("max_budget", "rpm", "tpm", "models", "expires_at",
"ttl_seconds")` — every control an operator uses to constrain a tenant.

**Reproduced end-to-end** (admin caps a user-owned key, then the user strips it):

```
admin caps user-owned key -> 200 {'max_budget': 0.0, 'rpm': 1, 'models': ['cheap-only']}
user strips it            -> 200 {'max_budget': None, 'rpm': None, 'models': []}
RESULT: ADMIN CAP REMOVED BY NON-ADMIN
```

Note the sibling case is *not* affected: a key the admin mints themselves has
`owner_id=None`, so a user PATCHing it still correctly gets 403. The boundary is
crossed only for keys the user already owns — which is exactly the shape an
operator uses to cap a tenant.

**Consequence:** total defeat of per-key spend caps, RPM/TPM limits and model
allowlists — the controls AUDIT #52/#57/#59/#116/#163/#198/#202 spent seven
rounds hardening. A capped tenant can mint uncapped spend on the operator's
upstream accounts, and can re-enable a key an admin deliberately parked.
`tests/test_user_accounts.py::test_user_cannot_patch_others_key_403` only covers
the *foreign*-key case, so the suite cannot see this.

**Fix:** when `actor.role != "admin"`, restrict the writable set to owner-facing
fields and reject the policy fields (403); have `AuthService.update_key` take an
allowlist argument so a second call site cannot forget the boundary.

**Status: fixed** — round 90 (2026-09-19); `tests/test_fix_round90.py`. Reproduced against the live tree before the fix and pinned after it.

### 221. The login and signup throttles are bypassed by concurrency (check-then-act)

**Severity:** 🔴 Critical (brute-force defence and signup cap do not hold)
**File:** `wiwi/server/app.py:3827` + `:3851-3865` (`auth_login`), `:3738` +
`:3756` (`auth_signup`), against `_AttemptThrottle.check`/`record_failure` at
`:245-265`

**Trigger:** any concurrent burst against `/auth/login` or `/auth/signup`.

`check(scope)` and `record_failure(scope)` each take `self._lock` **separately**,
with the entire credential verification (a 200,000-iteration PBKDF2) running in
between. `limit` is therefore enforced against the counter *as of the last
recorded failure*: N requests that all pass `check` before any records a failure
all get through.

**Reproduced** (limit 5, 20 attempts):

```
sequential 20 bad logins -> 401:5  429:15
concurrent 20 bad logins -> 401:20 429:0
RESULT: THROTTLE BYPASSED BY CONCURRENCY
```

**Consequence:** the brute-force defence for `/auth/login` — **including the
`{ip}:master` master-key branch** — is effectively "one burst per window",
i.e. unbounded guesses per account per 5 minutes. Signup (limit 5/3600 s) is
likewise over-mintable. Also a PBKDF2 CPU-exhaustion amplifier on unauthenticated
endpoints.

**Fix:** make admission-and-count atomic — one `try_consume(scope)` that checks
the window and appends the attempt under a single lock, called *before*
verification; `reset(scope)` on success.

**Status: fixed** — round 90 (2026-09-19); `tests/test_fix_round90.py`. Reproduced against the live tree before the fix and pinned after it.

### 222. A mid-stream client disconnect aborts the whole teardown tail — request never logged, never charged, TPM never reconciled, journal handle leaked

**Severity:** 🔴 Critical (silent loss of billing and audit on the *normal* abort path)
**File:** `wiwi/server/app.py:1619-1656` (the `finally` of the streaming driver)

**Trigger:** any client that closes the connection mid-stream — browser tab
closed, Ctrl-C, proxy timeout, interrupted `curl`. This is the ordinary way a
long SSE turn ends.

The entire teardown tail lives in one `finally` whose **first** statement is an
`await`:

```python
finally:
    if journal is not None:
        with contextlib.suppress(Exception):
            await journal.finish(_seq)      # 1622  <-- cancellation is delivered here
        state_.journals.release(journal_id)
    ...
    state_.logs.log_request(build_log_event(ctx))   # 1646  never reached
    await _record_tpm_usage(ctx.auth, ctx)          # 1647  never reached
    if (... and not await record_spend(...)):       # 1649  never reached
```

`contextlib.suppress(Exception)` does **not** catch `CancelledError` (it derives
from `BaseException`). Under anyio's level-triggered cancellation the pending
cancel is re-delivered at that first `await`, so nothing after line 1622 runs.

**Mechanism reproduced** (faithful model, unshielded vs shielded):

```
unshielded: ['finally-enter', 'ABORT:CancelledError']
shielded  : ['finally-enter', 'after-first-await', 'log_request']
```

**Reproduced end-to-end** against a real `uvicorn` server (0.52.4) with a real
socket peer, comparing a drained stream against an abandoned one:

```
CONTROL (drained): before=0 after=1 -> LOGGED
DISCONNECT       : before=1 after=1 -> NOT LOGGED
```

**Consequence** (four losses, all on the most common abort path): the request
vanishes from `request_logs` and every rollup built on it (so `/admin/stats`,
the Prometheus counters and the SSE ring omit it, and a client looping on
disconnect leaves no trace); the virtual key is never charged, so a key can
exceed `max_budget` indefinitely by disconnecting early — even though
`ctx.cost` was already computed; the TPM reservation is never reconciled; and
`journal.finish`/`release`/`stream.aclose` are skipped, leaking the `_active`
entry (which makes `is_active()` true forever, so a later reconnect tails a dead
journal instead of re-dispatching).

**Fix:** wrap the teardown tail in `anyio.CancelScope(shield=True)` and re-raise.
`asyncio.shield` alone is not sufficient — the `await` on it is itself cancelled;
the `CancelScope` form works because it defers delivery until exit (verified
above). Note `core/gateway.py:1475-1487` already shields its equivalent on the
pump side, so this is the one unguarded sibling.

---

## 🟠 High — round 87 audit sweep (2026-09-18)

### 223. Username enumeration via a ~46 ms timing oracle on `/auth/login`

**Severity:** 🟠 High (unauthenticated account enumeration)
**File:** `wiwi/auth/users.py:171-182` (`UserService.verify`), from `app.py:3859`

```python
row = (... WHERE username = :u ...).first()
if row is None or not verify_password(password, row[1]):
    return None
```

Python's `or` short-circuits: a missing user returns after one indexed SELECT,
while an existing user pays 200,000 PBKDF2 iterations first.

**Reproduced:**

```
existing user   :   47.59 ms
nonexistent user:    1.60 ms
delta           :   45.99 ms  (ratio 29.7x)
RESULT: ENUMERABLE by timing
```

**Consequence:** an unauthenticated attacker enumerates the username space with
near-zero false-positive rate, then aims credential stuffing at confirmed
accounts. `AUDIT.md` itself names the remediation ("using a dummy password hash
for unknown users") in the #55 write-up; it was never implemented.

**Fix:** when `row is None`, verify against a module-level dummy
`pbkdf2_sha256` record and return `None` regardless, so both paths do identical
work.

**Status: fixed** — round 90 (2026-09-19); `tests/test_fix_round90.py`. Reproduced against the live tree before the fix and pinned after it.

### 224. A truthy non-list `choices` crashes seven adapters — and cools a healthy deployment

**Severity:** 🟠 High (self-inflicted cooldown from an upstream frame the adapter should ignore)
**File:** `wiwi/providers/openai_adapter.py:510` (`choices = chunk.get("choices") or []`), crash at `:527`; same shape at `openrouter_adapter.py:297`/`:338` and `nim_adapter.py:340`/`:343`
**Inherited by:** `cline`, `workbuddy`, `bai`, `opencode` (chat route)

**Trigger:** any SSE frame whose `choices` is truthy but not a list. `or []`
defaults only a *falsy* value, so `5` or `true` survives.

**Reproduced:**

```
openai     CRASH -> TypeError: 'int' object is not subscriptable
openrouter CRASH -> TypeError: 'int' object is not subscriptable
nvidia-nim CRASH -> TypeError: 'int' object is not subscriptable
cline      CRASH -> TypeError: 'int' object is not subscriptable
workbuddy  CRASH -> TypeError: 'int' object is not subscriptable
bai        CRASH -> TypeError: 'int' object is not subscriptable
opencode   CRASH -> TypeError: 'int' object is not subscriptable
openai {"choices": true} -> CRASH TypeError: 'bool' object is not subscriptable
```

The `TypeError` escapes `decode_stream_event` (only `json.JSONDecodeError` is
caught) into the pump's mid-stream handler (`core/gateway.py:1513-1519`), which
routes it to `_fail_stream(..., "connection")` → `_note_stream_failure`, **cooling
a healthy deployment and feeding the key's retirement ladder**.

This is the #110/#136/#153/#154 class one layer up: those fixed the non-dict
*frame* and the non-dict `choices[0]`, but never a non-list `choices` container.
No test covers it.

**Fix:** `choices = chunk.get("choices"); if not isinstance(choices, list): choices = []`.

**Status: fixed** — round 90 (2026-09-19); `tests/test_fix_round90.py`. Reproduced against the live tree before the fix and pinned after it.

### 225. `_complete_via_stream`: a truncated non-200 body escapes as a raw `httpx` error, skipping retry, failover and every penalty

**Severity:** 🟠 High (the H2/AUDIT #92 class on an arm the fix missed)
**File:** `wiwi/core/gateway.py:496` (`raw = await resp.aread()` in the non-200 arm)

**Trigger:** any `force_stream` provider (Cline, WorkBuddy — the only path their
*non-streaming* callers take) returns a non-200 whose body drops mid-read.

**Reproduced** against a real socket peer sending `502` + `Content-Length: 5000`
+ a truncated body:

```
status: 502
RAISES: RemoteProtocolError | is httpx.TransportError: True
```

`execute_with_retries` catches only `WiwiError` (`router.py:1259`), so the raw
exception skips retry, failover, key cooldown and `record_fail` entirely, and the
client gets a generic 500. This `aread()` sits **before** the `try:` at `:545`,
so it is covered by neither the connect arm (`:485`) nor the read loop (`:610`) —
both of which were fixed.

**Fix:** wrap `:496` in `try/except httpx.TransportError` → `WiwiError(502,
"api_connection_error", retryable=True)`, mirroring `:485`.

**Status: fixed** — round 90 (2026-09-19); `tests/test_fix_round90.py`. Reproduced against the live tree before the fix and pinned after it.

### 226. `cycle_every_n` saturates into a permanent no-op — recurrence of #78

**Severity:** 🟠 High (a documented routing guarantee silently stops holding)
**File:** `wiwi/router/router.py:1189-1197` (exclusion), `:1252-1258` (increment), `:174-178` (`pick_key` fallback); promise at `wiwi/config.py:249-253`

**Trigger:** default config (`cycle_every_n: 3`) on any pool with ≥2 keys.

#78's fix moved the counters to router-level state, which made the cadence
*reachable* — but `key_consec` is incremented on every success and reset **only
on error** (`:1272`). Once every key in a pool has served N times, the exclusion
set contains all of them; `pick_key` then hits its "every key excluded" fallback
and ignores the exclusion entirely. The counters keep climbing, so the state is
absorbing: the cadence is dead for the rest of the process lifetime.

**Reproduced through the real `Router`** (weights 10:1):

```
cycle_every_n=3 picks=  12 -> longest_run=3
cycle_every_n=3 picks= 300 -> longest_run=10
cycle_every_n=0 picks= 300 -> longest_run=10     <- indistinguishable from cadence off
final key_consec: {('p1','strong'): 270, ('p1','weak'): 30}
```

**Consequence:** on a skewed pool the weak key is starved to its weight share
exactly as if the cadence were disabled, defeating the operator's reason for
setting it. The existing regressions cannot see it:
`test_fix_round41.py::test_cycle_every_n_rotates_under_skewed_weights` runs only
**4 picks** and asserts merely `len(set(picks)) > 1`; `test_fix_cycle_failover.py`
runs 12 and asserts "no key 4× in a row" — both of which plain smooth-WRR
satisfies.

**Fix:** treat `key_consec` as a *rotation* counter — clear a key's credit at the
point the exclusion is applied, so it cannot saturate.

### 227. OpenCode's Messages route prices Anthropic-shaped usage with the OpenAI formula

**Severity:** 🟠 High (systematic under-billing on the provider's primary use case)
**File:** `wiwi/core/gateway.py:1613` and `:1652`; `wiwi/providers/opencode_adapter.py:85,239-240`; duplicate arithmetic at `wiwi/logging_core/db_sink.py:731`

**Trigger:** any `opencode` deployment serving a `claude-*` / `qwen*` /
`union-alpha*` model — the Messages route, i.e. Claude Code pointed at OpenCode
Zen. `route_for_model` returns `"messages"` for all of those (verified), so
`OpencodeAdapter` delegates to the Anthropic decoder and usage arrives
Anthropic-shaped: `input_tokens` **excludes** cached tokens.

The gateway decides the shape from the provider *type*:

```python
includes_cached = dep.provider.provider_type != "anthropic"   # True for "opencode"
```

so it takes the OpenAI branch and computes `uncached_prompt = prompt_tokens -
cached_tokens`, subtracting tokens that were never in `prompt_tokens`.

**Reproduced** with a realistic Claude Code turn (3 fresh input, 60,000 cache
read, 2,000 cache write, 400 output):

```
opencode (provider_type!='anthropic') -> True :  cost = $0.031500
anthropic                            -> False:  cost = $0.031509
fresh input billed at $0 when includes_cached=True: True
```

**Consequence:** the fresh-input term vanishes entirely once
`cache_read > input_tokens`, which is the *normal* state of a long Claude Code
session, so spend is under-counted and budgets/`max_budget` over-serve. AUDIT #9
fixed exactly this for `provider_type == "anthropic"`; because the fix keyed on
provider type rather than on the *wire shape of the usage*, it cannot cover a
second provider that speaks Messages.

**Fix:** derive the flag from the resolved route/wire shape (Anthropic **or**
`route_for_model(model) == "messages"`), not from the provider type alone.

**Status: fixed** — round 90 (2026-09-19); `tests/test_fix_round90.py`. Reproduced against the live tree before the fix and pinned after it.

### 228. Quadratic string accumulation in `_complete_via_stream` stalls the event loop

**Severity:** 🟠 High (super-linear loop stall; affects every concurrent request)
**File:** `wiwi/core/gateway.py:562` and `:564` (`text += d.text`, `thinking += d.text`)

`text`/`thinking` are declared `nonlocal` in `_apply_event`, so they live in a
closure **cell**, which keeps the intermediate string at refcount 2 and defeats
CPython's in-place `realloc`. Reachable for Cline/WorkBuddy, whose
non-streaming callers are reassembled through this path.

**Reproduced:**

```
n= 20000  closure= 0.202s  plain-local=0.0014s  ratio=  148x
n= 60000  closure= 3.768s  plain-local=0.0031s  ratio= 1224x
n=120000  closure=13.425s  plain-local=0.0066s  ratio= 2022x
```

**Consequence:** the event loop stalls super-linearly with response length,
freezing all concurrent requests. The pump path is immune (it only tracks
`text_len += len(d.text)`) and `streaming/resume.py:168-181` already uses
list-append/join citing AUDIT #104 — this is the one remaining site.

**Fix:** accumulate `list[str]` and `"".join` once, exactly as #104's fix did.

---

## 🟡 Medium — round 87 audit sweep (2026-09-18)

**Status: fixed** — round 90 (2026-09-19); `tests/test_fix_round90.py`. Reproduced against the live tree before the fix and pinned after it.

### 229. A tiktoken special token in the prompt bills $0 — and 500s `count_tokens`

**Severity:** 🟡 Medium (silent $0 billing; one root cause, two symptoms)
**File:** `wiwi/cost/pricing.py:200` (`enc.encode(text)`); swallowed at
`wiwi/core/gateway.py:257-259`; unguarded in the `count_tokens` path

**Trigger:** the request text contains a literal tokenizer special token
(`<|endoftext|>`, `<|fim_prefix|>`, …) — plausible when a prompt quotes
tokenizer documentation.

**Reproduced:**

```
estimate_tokens RAISES: ValueError Encountered text corresponding to disallowed special token '<|endoftext|>'.
with disallowed_special=() -> 13
RESULT control         -> 200 {"input_tokens":2}
RESULT special token   -> UNHANDLED ValueError: ...
```

`usage_fallback` suppresses the failure into `est_prompt = 0`, so the request is
billed **$0.00** for all prompt tokens and presented as `usage_estimated=True,
prompt=0` (the AUDIT #131 mislabelling class). The same helper feeds
`estimate_request_tokens`, which is *not* wrapped, so
`POST /v1/messages/count_tokens` returns a hard 500 on the same input.

**Fix:** `enc.encode(text, disallowed_special=())` in `pricing.estimate_tokens` —
one line, fixes both arms.

### 230. The response cache admits sampling requests whenever `temperature` is omitted

**Severity:** 🟡 Medium (the exact failure round-34a set out to prevent)
**File:** `wiwi/cache/keygen.py:57-60`; enshrined by `tests/test_fix_round34.py:114-118`

```python
if gp.temperature:   # truthy: rejects >0, admits None and 0/0.0
    return False
```

`None` means "the client did not send temperature", **not** "greedy". The adapter
forwards the field only when set (`openai_adapter.py:234`), so the upstream
applies *its own* default — OpenAI's is 1.0, i.e. full sampling. `top_p` is not
consulted either.

**Reproduced:**

```
{} (temperature omitted)  -> cacheable=True    (provider default = 1.0, sampling ON)
{top_p: 0.9}              -> cacheable=True    (sampling ON)
{temperature: 0.0}        -> cacheable=True    (correct)
{temperature: 1.0}        -> cacheable=False   (correct)
```

**Consequence:** "a 'write me a poem' endpoint returns one poem forever" is
reachable through the *default* request shape. It stayed hidden because the
round-34a verification used explicit `temperature: 0.8/1.0`, which the truthy
check does catch.

**Fix:** admit only an explicit greedy signal (`temperature == 0` and `top_p`
unset/`>= 1.0`), treating `None` as unknown ⇒ not cacheable.

### 231. OpenRouter crashes on a truthy non-list `reasoning_details` (stream and non-stream)

**Severity:** 🟡 Medium (cooldown from a malformed upstream frame)
**File:** `wiwi/providers/openrouter_adapter.py:360` (stream), `:191` (non-stream)

**Trigger:** `{"choices":[{"delta":{"reasoning_details": 5}}]}`.

**Reproduced:** `CRASH -> TypeError: 'int' object is not iterable` (stream). On the
non-stream path the gateway's `_decode_response_guarded` downgrades it to a
retryable 502, so only the stream site is a health-accounting bug. AUDIT #110 and
#197 both fixed the non-dict *element*; neither guards a non-list *container*.

**Fix:** `rds = delta.get("reasoning_details"); for rd in (rds if isinstance(rds, list) else []):`.

### 232. An explicit JSON `null` tool name/id reaches the client as `null`

**Severity:** 🟡 Medium (the agent silently loses a tool call)
**File:** `wiwi/providers/gemini_adapter.py:255,361`, `wiwi/providers/anthropic_adapter.py:612,629,715` (and 10 similar `.get(k, "")` sites across the adapters)

**Trigger:** an explicit null where a name/id is expected, e.g. Gemini
`{"functionCall":{"name":null}}`. `dict.get(k, default)` defaults only a
*missing* key.

**Reproduced** end-to-end to the client frame:

```
CLIENT FRAME: ...{"tool_calls":[{"index":0,"id":"call_None_0","type":"function",
  "function":{"name":null,"arguments":""}}]}...
```

Also: the Gemini id becomes the literal `call_None_0`, and an Anthropic null id is
coerced by `ToolUsePart.__post_init__` to the plausible-looking string `"None"`.

This is AUDIT #186 one layer out: that fix coerced null names in the **wire
decoders**; the adapter stream decoders never got the mirror.

**Fix:** coerce at the boundary — `nm = fc.get("name"); name = nm if isinstance(nm, str) else ""`.

### 233. OpenCode's Responses route uses raw `int()` on usage — bypassing the shared coercion

**Severity:** 🟡 Medium (pump cooldown on a malformed usage block)
**File:** `wiwi/providers/opencode_adapter.py:403-406` (stream), `:660-663` (non-stream)

**Trigger:** `{"type":"response.completed","response":{"usage":{"input_tokens":"abc"}}}`.

**Reproduced** (route set to `responses`):

```
CRASH -> ValueError invalid literal for int() with base 10: 'abc'
CRASH -> TypeError int() argument must be ... not 'list'
```

Every other adapter routes usage through `_token_count` (which cites AUDIT #194);
`opencode_adapter.py` imports neither helper (verified). AUDIT #197 fixed the
OpenRouter `usage` *container* gate; this is the same class one layer down, in the
one adapter that skipped the shared coercion.

**Fix:** reuse `_token_count(u.get("input_tokens"))` rather than raw `int()`.

### 234. Admin provider-key and deployment routes mutate in-memory routing before the DB write, with no rollback

**Severity:** 🟡 Medium (state/DB divergence with no audit trace; admin-only)
**File:** `wiwi/server/app.py:2209→2211` (key add), `:2230→2232` (key delete), `:3098,3100→3102` (deployment add), `:3146,3150-3151→3153` (deployment delete)

Each mutates `acct.keys` / `state.router` first and persists second, with no
`except`/rollback. This is the AUDIT #181 class, whose fix was applied only to
provider DELETE/PATCH. Note #181's own text claims
`DELETE /admin/providers/{name}/keys/{label}` already persists first — that claim
is **wrong for the current code** (`:2230` removes from `acct.keys` before
`:2232` calls `delete_key`), which is why the siblings were left unfixed.

**Consequence:** on a DB failure (SQLite lock, Postgres failover, pool exhaustion)
the handler 500s while the mutation is live in memory and absent from the DB;
`log_audit` is never reached, so it leaves no trace, and the next restart
silently reverts it. For `admin_add_deployment` the in-memory append plus
`rebuild_cross_provider_pools()` run *before* the persist, so the gateway routes
to a deployment the DB has never heard of.

**Fix:** persist first, mutate in-memory only on success (the order the corrected
provider routes already use).

### 235. Cache `ttl_s <= 0` is a silent no-cache on memory and a startup crash on Redis

**Severity:** 🟡 Medium (one config, two opposite behaviours)
**File:** `wiwi/cache/response_cache.py:17-19` (no validation),
`wiwi/cache/redis_cache.py:46-47` (raises), `wiwi/config.py:272-281`
(`CacheSettings` has no validator)

**Trigger:** `cache_settings: {enabled: true, ttl_s: 0}`.

**Verified:**

```
memory ttl_s=0 -> get: None                 (every write immediately expired: a silent no-op)
redis  ttl_s=0 -> ValueError ttl_s must be positive  (raised during AppState construction)
```

**Consequence:** `ttl_s: 0` is the natural way to express "no expiry" and reads as
valid, but on memory it silently disables the cache the operator just enabled
(no warning, no metric), and on Redis it refuses to boot with an opaque
traceback naming no config field.

**Fix:** validate `ttl_s > 0` once in `CacheSettings` so both backends and the
config loader agree, and fail loudly at config load.

### 236. The middleware's early 413 is hardcoded OpenAI-shaped, so Anthropic callers get the wrong envelope

**Severity:** 🟡 Medium (dialect contract violation)
**File:** `wiwi/server/app.py:84-95` (middleware) vs `:1131-1135` (handler)

The `Content-Length` fast path builds its body inline as `{"error": {...}}`,
while the handler-level 413 goes through `_err`/`_surface_for_path` and is
dialect-correct. The same condition therefore produces two different envelopes
depending on whether `Content-Length` was present.

**Consequence:** an Anthropic-dialect client (Claude Code) parsing a 413 from the
oversized-body path finds no `type`/`error.type` field and reports an opaque
parse failure instead of the real cause.

**Fix:** pick the body by path in the middleware (`am.error_body(...)` for
`/v1/messages`), or hoist `_surface_for_path` to module scope and call it from
both places.

### 237. Journal replay still does blocking FS I/O on the event loop and re-reads the whole file per poll — #105's read half is unfixed

**Severity:** 🟡 Medium (event-loop stall proportional to concurrent reconnects)
**File:** `wiwi/streaming/tape_store.py:233,237` (`path.exists()` / `path.read_bytes()` in `_read_records`), `:180-182` (`mkdir`/`touch` under the async lock); called from `wiwi/server/app.py:1277,1282-1284` and every 50 ms in the tail loop at `:1303-1310`

AUDIT #105 is *not* fixed. The write/sweep paths were routed through
`asyncio.to_thread` (`append` `:100-101`, `aclose` `:113`, owner record `:195`,
`sweep` `:304`), but the **read** paths were not: `read_after`/`is_complete`/
`owner_of` still call sync `Path` methods, and the replay gate calls four of them
back-to-back on every reconnect while the tail loop re-reads the entire journal
every 50 ms.

**Measured** on a journal at the 1 MiB per-journal cap:

```
journal size: 1023 KiB
synchronous work: 16.1 ms   (one poll: read_after + is_complete)
largest event-loop gap: 8.2 ms  (baseline tick = 1.0 ms)
```

**Consequence:** each poll blocks the event loop for the whole read, stalling
every concurrent request; the tail loop repeats it 20×/second for the journal's
life. Journalling is **on by default** (`stream_journal_enabled: true`), so this
is reachable in every deployment.

**Fix:** route the FS calls through `asyncio.to_thread` (as `append` already does)
and tail incrementally by byte offset instead of re-reading the whole file.

### 238. `probation_weight <= 0` pins a probation key at zero weight forever

**Severity:** 🟡 Medium (the healer restores a key that can never be used)
**File:** `wiwi/config.py:296` (no validator), `wiwi/router/router.py:205-206` (weight use), `:237-245` (graduation)

`probation_weight` is unvalidated and multiplies the WRR weight. At `0` the key
accumulates a zero increment each round and can never be selected; since
graduation is driven by `on_result(key, 200)`, a key that is never picked can
never graduate. A negative value is worse: the deficit goes negative, actively
deprioritising the key.

**Reproduced:**

```
probation_weight=  0.5 -> prob_selected=7/20
probation_weight=  0.0 -> prob_selected=0/20   <- stuck
probation_weight= -1.0 -> prob_selected=0/20   <- stuck
```

**Consequence:** a healer-restored key is restored and then never used, so the
operator pays for probes that restore nothing — reachable exactly in the
single-key outage the healer exists to recover from. Mirror of AUDIT #79.

**Fix:** clamp `probation_weight` into `(0, 1]` in a `HealerSettings` validator.

---

## ⚪ Low — round 87 audit sweep (2026-09-18)

### 239. Unauthenticated `/health` exposes topology and accounting-incident counters

**Severity:** ⚪ Low (reconnaissance signal)
**File:** `wiwi/server/app.py:1721-1767`

`/health` is deliberately unauthenticated (the Docker `HEALTHCHECK` probes it) but
returns `providers`, `groups`, `available_groups` and five loss counters plus
`spend_charge_failures`. Anyone who can reach the port learns the deployment
topology and, from `spend_charge_failures > 0`, that budget enforcement is
currently failing — a useful signal for timing an abuse attempt. The counters were
added by the #171/#179 fixes without revisiting this endpoint's exposure.

**Fix:** keep `status` public for the probe and move the counters/topology behind
an authenticated endpoint or a master-key-gated `?detail=1`.

### 240. `_parse_models_response` raises on a non-standard upstream model listing

**Severity:** ⚪ Low (admin "fetch models" returns an opaque 500)
**File:** `wiwi/server/app.py:474-483`

`orjson.loads(body)` is unguarded and the comprehensions assume
`data["models"]`/`data["data"]` are iterable and that gemini's `m["name"]` is a
string.

**Reproduced:**

```
{"models":5}              -> RAISES TypeError: 'int' object is not iterable
{"data":5}                -> RAISES TypeError: 'int' object is not iterable
{"models":[{"name":123}]} -> RAISES AttributeError: 'int' object has no attribute 'split'
not json at all           -> RAISES JSONDecodeError
```

**Fix:** wrap the parse in `try/except (ValueError, TypeError, AttributeError)`
returning `[]`, and coerce the name with `str(...)`.

### 241. `_put_frame` can silently drop the terminal frame on queue-put timeout

**Severity:** ⚪ Low (re-opens the #211 shape under a wedged consumer)
**File:** `wiwi/core/gateway.py:1157-1159`

If the 5 s `wait_for` bound expires, the delta — which may be the
`StreamError`/`StreamEnd` terminal — is discarded with no log, metric or counter.
#211 built this path to guarantee the terminal frame; this arm quietly re-opens
"queue full → no terminal" when the consumer is already wedged.

**Fix:** increment a counter and log a warning on the timeout rather than
suppressing silently.

### 242. OpenRouter's error arm emits `StreamError` without flushing open tool calls

**Severity:** ⚪ Low (IR-contract violation; not currently client-visible)
**File:** `wiwi/providers/openrouter_adapter.py:298-316`

The tool-flush is gated on `choices[0].get("finish_reason") == "error"` and the
`StreamError` is appended *before* the close; OpenAI (`:504`) and NIM (`:320`)
flush unconditionally and before the error.

**Reproduced:**

```
openai     -> ['ToolCallOpen', 'ToolCallClose', 'StreamError']
nim        -> ['ToolCallOpen', 'ToolCallClose', 'StreamError']
openrouter -> ['StreamError']      <- no flush
```

Not currently client-visible (the Anthropic encoder self-heals on `StreamError`;
the Chat/Responses encoders do not track block state), so this is latent.

**Fix:** hoist the flush above the `StreamError` append and drop the
`finish_reason == "error"` gate.

### 243. Anthropic: a repeated `content_block_start` on an open index orphans a block

**Severity:** ⚪ Low (malformed-upstream only; leaves an unterminated client block)
**File:** `wiwi/providers/anthropic_adapter.py:707` (`self._tool_indices.add(idx)`, no duplicate check), `:714` (unconditional `ToolCallOpen`)

**Reproduced:** two `content_block_start` frames at the same index yield
`[ToolCallOpen(0), ToolCallOpen(0)]` — the encoder opens client block 0 *and*
block 1, and the terminal frame closes only index 1, so block 0 never receives
`content_block_stop` (the AUDIT #111 class). OpenAI (`:576-583`), OpenRouter
(`:412-420`) and NIM (`:414-430`) all have the "new call on a reused index closes
the previous one" branch; Anthropic is the one adapter missing it.

**Fix:** `if idx in self._tool_indices: out.append(dl.ToolCallClose(index=idx))`
before the new `ToolCallOpen`.

### 244. Gemini: a mid-stream `promptFeedback.blockReason` truncates a partially-delivered answer

**Severity:** ⚪ Low (robustness; not observed in the wild)
**File:** `wiwi/providers/gemini_adapter.py:324-336`

The block arm ignores `_saw_tail` and any content already emitted.

**Reproduced:**

```
text -> promptFeedback{blockReason: SAFETY} + more text
  => [StreamStart, TextDelta('partial answer'), Finish('content_filter'), StreamEnd]
```

The second text part is dropped and the stream terminates cleanly at HTTP 200
with no truncation signal. Reported as a robustness observation: the normal case
has `promptFeedback` on the first chunk, so this shape was not confirmed to occur
in production.

**Fix:** guard the block arm with `not self._saw_tail`, and when content already
flowed emit `Finish("content_filter")` without discarding the frame's parts.

### 245. An unguarded `finally: await resp_cm.__aexit__()` can mask the real exception

**Severity:** ⚪ Low (fault-injected precondition; one-line fix)
**File:** `wiwi/core/gateway.py:650-651`

The `_complete_via_stream` teardown awaits `__aexit__` unguarded and unbounded,
unlike the pump's equivalent (`_close_upstream` suppresses and
`asyncio.wait_for(..., 5.0)` at `:1486`). If a fault inside the pump body is
followed by a teardown that also raises, the real retryable `WiwiError` is
destroyed and replaced by a non-`WiwiError` — which then bypasses retry/failover
(see #225). Also unbounded, so a wedged transport blocks the caller here
(AUDIT #16's shape on a site #16 does not cover).

**Fix:** `with contextlib.suppress(Exception): await asyncio.wait_for(
resp_cm.__aexit__(None, None, None), timeout=5.0)`.

---

## 🔴 Critical — round 87 sweep, adapter tool-schema and sync-decode pass (2026-09-18)

Findings from the same sweep, covering the provider adapters' **non-streaming**
decode paths and the NIM tool-schema sanitizer. All reproduced against the
current checkout.

### 246. The NIM tool sanitizer never recurses into `items` — the two defects the module exists to prevent both survive

**Severity:** 🔴 Critical (the module's core purpose silently fails on the most common agent tool shape)
**File:** `wiwi/providers/nim_tool_schema.py:25,28,31` (the three recursion key-sets), `:249` (`collect_nim_tool_aliases`)

`items` — the single most common schema keyword in real agent tool catalogs
(`Edit`'s `edits: [{old_string, new_string, type}]`) — is absent from all three
recursion key-sets:

```python
_SCHEMA_VALUE_KEYS = frozenset({"additionalProperties", "not", "contains",
                                "propertyNames", "if", "then", "else"})   # no "items"
_SCHEMA_LIST_KEYS  = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
_SCHEMA_MAP_KEYS   = frozenset({"properties", "patternProperties", "$defs",
                                "definitions", "dependentSchemas"})
```

**Reproduced** with a tool whose `edits.items` carries both an
`additionalProperties: true` and a parameter named `type`:

```
items sent upstream: {"additionalProperties": true,
                      "properties": {"_nim_arg_type": {"type": "string"}, ...},
                      "type": "object"}
boolean subschema survived  : True
unsafe 'type' param survived: False
alias map collected         : {}
```

Note the result is **self-inconsistent**, which is what makes it so damaging:
`_alias_in_node` recurses on every property value (so it *does* alias inside
`items`), while `_collect_aliases_in_node` only descends through the three
key-sets (so it *never* collects them). Aliasing happens; reversal does not.

**Consequence — two independent failures, both the exact defects the module
exists to prevent:**

1. `items.additionalProperties: true` reaches NIM/vLLM, which rejects boolean
   subschemas — a 400 naming a construct the sanitizer was supposed to strip.
2. The model is told to send `_nim_arg_type`, and the client receives it back
   un-restored:

   ```
   model sent : {"edits": [{"_nim_arg_type": "replace", "old_string": "a"}]}
   client gets: {"edits": [{"_nim_arg_type": "replace", "old_string": "a"}]}
   RESULT: the client receives _nim_arg_type, which its tool schema never declared
   ```

   Claude Code validates args against the declared schema, so the call is
   undispatchable.

**Fix:** add `"items"` to `_SCHEMA_VALUE_KEYS` (its value is a schema).

**Status: fixed** — round 90 (2026-09-19); `tests/test_fix_round90.py`. Reproduced against the live tree before the fix and pinned after it.

### 247. Every non-streaming `decode_response` crashes on a `null`/non-dict nested field — and the failure is charged to key and deployment health

**Severity:** 🔴 Critical (self-inflicted outage from a frame carrying no semantics)
**File:** `wiwi/providers/openai_adapter.py:364-365,375,411`; `openrouter_adapter.py:179-180,219,253`; `anthropic_adapter.py:596,643,646`; `gemini_adapter.py:233,235,238,268`; `opencode_adapter.py:654-663`; inherited by `cline`, `workbuddy`, `bai`, `gmicloud`, `openai-compatible`

The streaming decoders were hardened read-by-read in rounds 49/61
(AUDIT #110/#136/#153/#154). The **sync** decoders were never given the same
treatment, and use `.get(k, {})` / `or {}`, which default only a *missing* key —
an explicit JSON `null` passes straight through:

```python
choice  = (data.get("choices") or [{}])[0]   # {"choices":[null]} -> None
message = choice.get("message", {})          # {"message":null}   -> None
u       = data.get("usage") or {}            # {"usage":"x"}      -> "x"
```

**Reproduced** (all `AttributeError`):

```
openai      {"choices":[{"message":null}]}   -> AttributeError: 'NoneType' object has no attribute 'get'
openrouter  {"choices":[null]}               -> AttributeError: 'NoneType' object has no attribute 'get'
openai      {"choices":[],"usage":"x"}       -> AttributeError: 'str' object has no attribute 'get'
anthropic   {"content":[null]}               -> AttributeError: 'NoneType' object has no attribute 'get'
gemini      {"candidates":[{"content":"str"}]} -> AttributeError: 'str' object has no attribute 'get'
```

**Consequence — worse than a 500.** `_decode_response_guarded`
(`core/gateway.py:85-108`) converts the `AttributeError` into
`WiwiError(502, "api_error", retryable=True)`; `status_for_key_pool` returns 502
(verified), so the router charges the key (`err_count += 1`) **and** calls
`dep.record_fail(...)` (`router.py:1296-1298`, which matches on
`status in (408, 500, 502, 503, 504, 529)`). A frame carrying no semantics
therefore cools a healthy deployment and feeds the key's retirement ladder — the
same shape AUDIT #174 and the `status_for_key_pool` docstring warn about, on the
path the streaming hardening does not protect.

**Fix:** mirror the streaming guards per read —
`choice = choices[0] if choices and isinstance(choices[0], dict) else {}`;
`message = choice.get("message") if isinstance(choice.get("message"), dict) else {}`;
`u = data.get("usage") if isinstance(data.get("usage"), dict) else {}`; and
likewise for `pf`/`cand`/`content`/`out_details`.

---

## 🟠 High — round 87 sweep, adapter tool-schema and sync-decode pass (2026-09-18)

**Status: fixed** — round 90 (2026-09-19); `tests/test_fix_round90.py`. Reproduced against the live tree before the fix and pinned after it.

### 248. `nim_native_tools._unalias_args` is flat while its sibling is recursive — nested aliases leak on the native MiniMax path

**Severity:** 🟠 High (undispatchable tool call on the streaming path)
**File:** `wiwi/providers/nim_native_tools.py:213-217` (called at `:206`)

```python
def _unalias_args(args, aliases):
    out = {}
    for k, v in args.items():
        out[aliases.get(k, k)] = v      # no recursion into v
    return out
```

`nim_tool_schema.unalias_nim_tool_args` recurses (its docstring at
`nim_tool_schema.py:67-71` explicitly promises "into nested dicts and lists so
aliased keys at any depth are restored"). This near-duplicate does not.

**Reproduced:** a tool whose *nested* object property is named `type`, with NIM's
native markup path:

```
aliases:            {'Edit': {'_nim_arg_type': 'type'}}
STREAMED to client: {"op":{"_nim_arg_type":"replace","val":"x"}}   # should be "type"
```

**Consequence:** the client receives a key its tool schema never declared, on the
streaming path where it has already had a 200. AUDIT #22 (nested alias
un-reversal) was applied only to `nim_tool_schema`; this duplicate was missed —
and two conventions for one job is itself the second defect.

**Fix:** delete `_unalias_args` and delegate to
`nim_tool_schema.unalias_nim_tool_args`.

---

## 🟡 Medium — round 87 sweep, adapter tool-schema and sync-decode pass (2026-09-18)

**Status: fixed** — round 90 (2026-09-19); `tests/test_fix_round90.py`. Reproduced against the live tree before the fix and pinned after it.

### 249. Non-string `content` / `reasoning_content` is forwarded verbatim on the sync path

**Severity:** 🟡 Medium (contract-invalid 200 body; or a 500 after upstream billing)
**File:** `wiwi/providers/openai_adapter.py:368-369,372-374`; `openrouter_adapter.py:181,187`; `gemini_adapter.py:244-251`; `opencode_adapter.py:626,630`

The streaming decoders gate every text read with `isinstance(..., str)`
(AUDIT #154, verified present). The sync decoders do not:

```python
turn = ir.AssistantTurn(text=message.get("content") or message.get("refusal") or "")
reasoning = message.get("reasoning_content") or message.get("reasoning")
if reasoning: turn.thinking.append(ir.ThinkingPart(reasoning))
```

**Reproduced — two distinct harms:**

```
turn.text = 5   (field is declared str)
client message: {"role": "assistant", "content": 5}      <- contract-invalid chunk on a 200

thinking part text: {'a': 1}
encode RAISES: TypeError sequence item 0: expected str instance, dict found
```

The second is a 500 on a request the upstream already served and billed. This is
the class AUDIT #194 fixed for usage counters via `_token_count`; the text fields
were left out.

**Fix:** `text = message.get("content"); text if isinstance(text, str) else ""`
and `if isinstance(reasoning, str) and reasoning:`.

### 250. `error_from_provider_status`'s 400 heuristic misclassifies parameter errors as `context_window_exceeded`

**Severity:** 🟡 Medium (wasted billed failover attempts; misleading client error)
**File:** `wiwi/providers/base.py:229-231`

```python
if status == 400 and ("context" in msg.lower() or "maximum" in msg.lower()
                      or "too long" in msg.lower()):
    return WiwiError(400, "context_window_exceeded", msg)
```

**Reproduced:**

```
context_window_exceeded  <- Invalid 'temperature': maximum value is 2            MISCLASSIFIED
context_window_exceeded  <- maximum number of stop sequences is 4                MISCLASSIFIED
context_window_exceeded  <- stop sequence is too long (max 4 characters)         MISCLASSIFIED
context_window_exceeded  <- This model's maximum context length is 128000 tokens (correct)
```

**Consequence:** the router treats a caller-side parameter error as a context
overflow and re-dispatches to every group in `context_window_fallbacks`
(`router.py:1303-1309`, `:1334-1340`) — extra billed attempts against a
larger-context model that rejects the same bad parameter. The client receives a
`context_window_exceeded` error whose message is about `temperature`.

Note AUDIT #23's description ("matching `'tokens'`") does not match the shipped
heuristic; the entry is stale in its specifics but the defect is live.

**Fix:** require a context-specific token (`"context length"`, `"context window"`,
`"maximum context"`, `"too many tokens"`) and drop the bare `maximum`/`too long`.

### 251. `collect_nim_tool_aliases` matches by prefix — a literal `_nim_arg_foo` parameter is renamed or merged away

**Severity:** 🟡 Medium (silent argument corruption/loss)
**File:** `wiwi/providers/nim_tool_schema.py:244-246`

```python
for pname in props:
    if isinstance(pname, str) and pname.startswith(_ALIAS_PREFIX):
        aliases[pname] = pname[len(_ALIAS_PREFIX):]
```

Collection is by *prefix*, with no check that the name was actually minted by
`_renames_for_node`.

**Reproduced:**

```
tool declares : _nim_arg_foo
alias map     : {'t': {'_nim_arg_foo': 'foo'}}
model sends   : {"_nim_arg_foo": "v"}
client gets   : {"foo": "v"}          <- the caller's declared name destroyed

both declared : foo + _nim_arg_foo
model sends   : {"foo": "A", "_nim_arg_foo": "B"}
client gets   : {"foo": "B"}          <- 'foo' value A is LOST
```

**Consequence:** silent, total corruption for any tool declaring a parameter
whose name merely starts with `_nim_arg_`. This is AUDIT #201's shape for the
*collision* case (fixed round 78); the *prefix* case was left open.

**Fix:** record the minted renames during sanitize (return them from
`sanitize_nim_tool_schemas`, or re-derive via `_renames_for_node`) and reverse
only those, rather than pattern-matching the prefix.

### 252. `opencode`'s Responses-route sync decoder has unguarded numeric coercion and non-str text

**Severity:** 🟡 Medium (crash or poisoned turn on a malformed usage/text block)
**File:** `wiwi/providers/opencode_adapter.py:626,630,660-663`

The Responses route does not inherit the OpenAI base decoder, so it missed both
the text and the counter hardening:

```python
turn.text += c.get("text") or c.get("refusal") or ""   # non-str poisons turn.text
turn.thinking.append(ir.ThinkingPart(s["text"]))        # non-str poisons thinking
prompt_tokens=int(u.get("input_tokens", 0) or 0),       # "abc" -> ValueError
```

**Reproduced:**

```
RAISE <- {"output":[{"type":"message","content":[{"type":"output_text","text":5}]}]}
         (TypeError: can only concatenate str (not "int") to str)
OK    <- {"output":[{"type":"reasoning","summary":[{"text":5}]}]}   (silently poisons thinking)
RAISE <- {"output":[],"usage":{"input_tokens":"abc"}}
         (ValueError: invalid literal for int())
```

Every other adapter routes counters through `_token_count`/`ir.coerce_int`, which
return 0 for unparseable values; this is the one place a garbage counter kills
the response instead of degrading. The stream sibling at `:403-406` has the same
pattern and should be fixed together (see #233).

**Fix:** reuse `_token_count` and gate the text reads with `isinstance(..., str)`.

---

## ⚪ Low — round 87 sweep, adapter tool-schema and sync-decode pass (2026-09-18)

### 253. Typed-wrong `temperature` / `top_p` / `seed` are forwarded upstream verbatim

**Severity:** ⚪ Low (upstream 400 naming a type the caller never sent)
**File:** `wire/openai_chat.py:273-274,279`; `wire/anthropic_messages.py:430-431`; `wire/openai_responses.py:353,362` → sinks `openai_adapter.py:234-241`, `anthropic_adapter.py:463-468`, `gemini_adapter.py:140-146`

`max_tokens` gets `ir.coerce_int`, `stop` gets `_stop_list` and `top_k` gets
`ir.coerce_int` (AUDIT #187) — but `temperature`, `top_p` and `seed` are stored
raw, so AUDIT #184/#186/#187's coercion pattern was not extended to them.

**Reproduced** — the same typed-wrong values reach the upstream body across all
three dialects:

```
openai    forwards verbatim: ['"temperature": "hot"', '"top_p": [0.5]', '"seed": "42"']
anthropic forwards verbatim: ['"temperature": "hot"', '"top_p": [0.5]']
gemini    forwards verbatim: ['"temperature": "hot"', '"topP": [0.5]']
```

**Fix:** add a numeric coercion helper (rejecting `bool`, per `ir.coerce_int`'s
documented rule) and apply it to `temperature`/`top_p` in all three decoders and
`seed` where carried.

### 254. `get_adapter()` / `fresh_adapter()` hot-path discipline — verified clean

**Severity:** ⚪ (no defect; recorded so the next sweep does not re-audit it)
**File:** `wiwi/providers/registry.py`

Checked because CLAUDE.md flags it as a known hazard: **no hot-path misuse
exists.** All production call sites use `fresh_adapter` — `core/gateway.py:360`
(`_call_once`), `:1068` (`_pump_once`), `core/recovery.py:486` (healer probe),
`server/app.py:2860`/`:2921` (admin model-list fetches, which call `headers()`
synchronously and hold nothing across an await). `get_adapter` has **zero**
non-test callers, and the registry's coverage `assert` is honest
(`_OPENAI_WIRE_TYPES` does include `bai`, so `_unhandled` is empty).

Likewise verified clean in this pass: alias-map injectivity
(`_renames_for_node` is injective, including over adversarial
`_nim_arg__nim_arg_type` chains); `headers()` leaks no secret into a URL, log or
error message; dict-form tool `arguments` are handled on both OpenAI paths; and
explicit `null` usage counters decode to `0` everywhere via
`_token_count`/`ir.coerce_int` (AUDIT #194/#195).

---

## 🟠 High — round 87 sweep, admin console (`web/`) pass (2026-09-18)

UI findings from the same sweep. `web/` has no test runner, so each is
established by reading the source against the binding UI/UX rule; the three that
most need a browser confirmation (#255, #257, #259) should be checked with
Playwright under `.verify/` before being marked fixed.

### 255. Request Logs' live tail truncates the *shared* `["request-logs"]` cache

**Severity:** 🟠 High (every other console page silently under-reports)
**File:** `web/src/pages/RequestLogs.tsx:517-523` (writer), `:508` (key); consumers `Dashboard.tsx:264`, `Analytics.tsx:943`, `BudgetsAlerts.tsx:225`, `Providers.tsx:360`

```ts
qc.setQueryData<{ logs: RequestLogEntry[] }>(["request-logs"], (old) => {
  if (!old) return { logs: [evt] };
  if (old.logs.some((l) => l.request_id === evt.request_id)) return old;
  return { logs: [evt, ...old.logs].slice(0, 500) };   // <- 500
});
```

The query key is byte-identical to the one Dashboard, Analytics, Budgets and
Providers use, and `getRequestLogs` requests `limit: "10000"`
(`api/client.ts:319`). So a single live event **replaces the whole 10 000-row
payload with 500 rows** and leaves it there until the next 15 s poll.

**Trigger:** open `/console/request-logs`, enable **Live tail**, let one request
complete.

**Consequence:** every other mounted page recomputes its aggregates over 500
events — the Dashboard sparkline re-seeds (`Dashboard.tsx:294`), Analytics'
cost/token breakdown and `pulseEvents`, and Budgets' month-end projection
(`BudgetsAlerts.tsx:230-233`) all under-report. The numbers remain plausible,
which is what makes it hard to notice.

**Fix:** don't write the truncated ring into the shared key — merge into a
separate `["request-logs","live"]` key the page reads, or bound the truncation to
the server's own limit and keep the poll authoritative.

**Status: fixed** — round 90 (2026-09-19); `tests/test_fix_round90.py`. Reproduced against the live tree before the fix and pinned after it.

### 256. Playground never aborts an in-flight stream on unmount

**Severity:** 🟠 High (the upstream completes and the key is charged for output nobody sees)
**File:** `web/src/pages/Playground.tsx:650` (`abortRef`), aborted only at `:816`, `:859`, `:994`

**Trigger:** send a message, then navigate away (or browser Back) while the
response is still streaming.

`abortRef.current` is aborted on chat-switch, clear-all and the Stop/Escape
button only. There is **no unmount cleanup** — verified: `abortRef.current?.abort()`
appears at 816/859/994 and in no `useEffect` return. The `fetch` at `:890` and
`streamSSE`'s reader loop keep running against a component that is gone.

**Consequence:** the upstream request is never cancelled, so the gateway finishes
the completion and the virtual key is charged for output nobody will ever see;
`setMessages` also fires on an unmounted component. The `AbortError` branch at
`:950` is dead for this path because nothing aborts.

**Fix:** `useEffect(() => () => abortRef.current?.abort(), [])`.

---

## 🟡 Medium — round 87 sweep, admin console (`web/`) pass (2026-09-18)

**Status: fixed** — round 90 (2026-09-19); `tests/test_fix_round90.py`. Reproduced against the live tree before the fix and pinned after it.

### 257. `Dialog` / `Drawer` declare `aria-modal="true"` but do not trap focus

**Severity:** 🟡 Medium (binding UI/UX rule 3; keyboard users lose their place)
**File:** `web/src/components/ui.tsx:415-466` (Dialog), `:468-500` (Drawer)

Escape-to-close is implemented (`:422-429`, `:475-482`) — the part usually
checked — but the modal contract stops there. `grep -rn "inert\|focusTrap\|tabbable"`
over `web/src` returns **nothing**: no focus trap, no `inert`/`aria-hidden` on
portal siblings, no initial focus, no focus restore on close.

**Trigger:** open any dialog (e.g. *Add provider*), then press Tab repeatedly.

**Consequence:** focus walks out of the modal into the page behind it — still
visually obscured by the `bg-black/70` scrim — landing on sidebar nav and page
buttons that are neither visible nor contextually valid. On close, focus returns
to `<body>`. Violates binding rule 3 ("Modal/dialog focus trapping … must work on
desktop"); it is the one of the four modal requirements that is missing.

**Fix:** on open move focus to the panel (`tabIndex={-1}` + `.focus()`), cycle Tab
within the portal subtree, restore the previously-focused element in cleanup, and
add `inert` to `#root` while open (React 19 supports `inert` natively).

### 258. Every `<Button>` suppresses the focus ring with no replacement (107 call sites)

**Severity:** 🟡 Medium (binding UI/UX rule 3, on the primary interactive control)
**File:** `web/src/components/ui.tsx:55-63` (`BTN` map), `web/src/styles.css:500` (`outline: none` in `.admin-btn`)

`.admin-btn` sets `outline: none` and the stylesheet defines **no**
`.admin-btn:focus-visible` rule — verified: the only focus rules are
`.admin-input:focus` (`:413`) and `.admin-collapse-btn:focus-visible` (`:990`).
Author styles beat the UA `:focus-visible` outline, so a focused `Button` is
visually identical to an unfocused one.

Also verified that no `focus-visible` class reaches a `<Button>`: `FOCUS_RING`
(`ModelsCatalog.tsx:17`) is applied only to raw `<button>`s, and a scan of all
107 `<Button …>` openings found none containing `focus`.

**Trigger:** Tab to any primary action (Create / Save / Delete / Sign out).

**Consequence:** binding rule 3 ("do not suppress `outline` without providing an
equivalent") is violated app-wide.

**Fix:** add `.admin-btn:focus-visible { outline: none; box-shadow: 0 0 0 3px
rgba(99,102,241,.35); }`, mirroring the `.admin-collapse-btn` treatment already in
the file.

### 259. Docs code-block copy button is hover-only — invisible and unusable on touch

**Severity:** 🟡 Medium (binding UI/UX rule 2)
**File:** `web/src/pages/Docs.tsx:99` and `:199`

```
className="… opacity-0 transition-all hover:text-[…] group-hover:opacity-100"
```

No `group-focus-within:opacity-100`, no `pointer-coarse:opacity-100`, and no
`@media (hover: none)` fallback for `.docs-codeblock` in `styles.css`. Because the
element is `opacity-0` rather than `hidden`, it stays in the tab order while
giving no visual focus indication.

**Trigger:** open `/docs` on a phone (or any coarse pointer) and try to copy a
curl example — the control never appears.

**Consequence:** binding rule 2 ("No information or action may be hover-only").
This is exactly the defect AUDIT #210 fixed in `Playground.tsx`, which now uses
`group-focus-within:opacity-100 pointer-coarse:opacity-100` at `:535`, `:1477`
and `:1514`; the identical pattern in `Docs.tsx` was not swept.

**Fix:** append `group-focus-within:opacity-100 pointer-coarse:opacity-100` to
both class strings.

**Status: fixed** — the docs code copy controls are always visible on coarse pointers
and expose visible keyboard focus states.

### 260. `Toggle` renders `role="switch"` with no accessible name

**Severity:** 🟡 Medium (screen readers announce an unidentifiable control)
**File:** `web/src/components/ui.tsx:168-188`

```tsx
<button type="button" role="switch" aria-checked={props.checked} … >
```

No `aria-label`, no `aria-labelledby`, and the component accepts no id/label prop
(verified: 0 occurrences of `aria-label` in the component). At every call site the
visible text is a sibling element, never an associated label — `Settings.tsx:184,
202,220`, `ProviderDetail.tsx:243`, `OAuthProviders.tsx:852`, `logs-shared.tsx:134`,
`Users.tsx:56`.

**Trigger:** screen reader on Settings → the switches announce as "switch, on"
with no name; on the Virtual Keys table the per-row enable/disable switch is
unidentifiable.

**Fix:** add an optional `label?: string` prop rendering `aria-label`, and pass it
at each call site (or `aria-labelledby` pointing at the existing text node).

### 261. Tap targets below the 44 px minimum (binding rule 2)

**Severity:** 🟡 Medium (touch usability)
**File:** measured from the class strings; none has a compensating `min-h`/`min-w`

| File:line | Control | Approx. size |
|---|---|---|
| `Playground.tsx:1143` | key-mint **Retry** | 22×18 px |
| `Playground.tsx:1190` | error-banner **Retry** | 26×18 px |
| `Playground.tsx:432` | sidebar **Collapse** | 28×28 px |
| `ProviderDetail.tsx:150-156` | key **Reveal/Hide** | 20×20 px |
| `RequestLogs.tsx:212-220` | drawer **Copy** | 21×21 px |
| `ModelsCatalog.tsx:123` | copy model id | 25×25 px |
| `Docs.tsx:121` | `PathCopyBtn` | 19×19 px |
| `Combos.tsx:583` | inline weight edit | 24×18 px |

**Consequence:** fails the 44×44 (HIG) / 48×48 dp (Material) floor. Note the
contrast inside the same codebase: Playground's chat-row actions (`:543`, `:555`)
and `ActionButton` (`:1538`) were deliberately sized to `h-11 w-11` / `min-h-11`
by the #210 fix — the rule was applied to the reported sites but not swept across
the rest of the app.

**Fix:** give each control `min-h-11 min-w-11` (or `p-2.5`) plus
`inline-flex items-center justify-center`.

**Partial fix:** `Docs.tsx` `PathCopyBtn` now uses an `h-11 w-11` inline-flex
target with a visible keyboard focus ring. The other controls in this finding
remain open.

### 262. Users page: the self-demotion guard is dead code, and role changes can race

**Severity:** 🟡 Medium (advertised client-side guard does not exist)
**File:** `web/src/pages/Users.tsx:32-47` (`RoleCell`), `:111` (call site)

The file header states the control is disabled client-side for the acting admin,
and `meId={me?.id}` is threaded into both `RoleCell` (`:111`) and `DisableCell`
(`:114`). But `props.meId` is read in **neither** — verified: it appears only in
the two type declarations (`:32`, `:49`) and the two JSX call sites. `RoleCell`
also has no `patch.isPending` guard, and `Select` (`ui.tsx:132-145`) accepts no
`disabled` prop at all, so it cannot be disabled.

**Trigger:** an admin changes their own row's role from `admin` to `user`.

**Consequence:** the advertised guard doesn't exist — the demote is submitted and
the user learns it was refused only from the backend's 400 (and a non-last admin
demoting themselves simply succeeds, then gets bounced out of `/console/*` by
`RequireAdmin`). Separately, `onChange={(v) => patch.mutate(v)}` has no in-flight
guard, so flipping the dropdown twice fires two overlapping
`PATCH /admin/users/{id}` with no ordering — last-resolved wins, not
last-requested.

**Fix:** read `meId` in `RoleCell`, add a `disabled` prop to `Select`, disable it
for `props.u.id === props.meId`, and gate on `patch.isPending`.

---

## ⚪ Low — round 87 sweep, admin console (`web/`) pass (2026-09-18)

### 263. Playground "New chat" silently discards the unsent draft

**Severity:** ⚪ Low (contradicts the component's own stated invariant)
**File:** `web/src/pages/Playground.tsx:800-809` (`handleNewChat`), contrast `:820` (`handleSelectChat`)

`handleSelectChat` explicitly persists the outgoing composer text before
switching (`draftsRef.current[activeChatId ?? ""] = draft;` at `:820`).
`handleNewChat` does not — it calls `setDraft(draftsRef.current[chat.id] ?? "")`
for the brand-new chat, which is always `""`. Since drafts live only in
`draftsRef` (never in the persisted payload), the text is gone.

**Trigger:** type a half-written message, click **New chat**, then click back to
the previous conversation.

**Consequence:** contradicts the invariant the component states at `:656-658`
("Unsent composer text, kept per chat so switching conversations doesn't lose a
half-written message").

**Fix:** mirror the `:820` line in `handleNewChat` before `setActiveChatId(chat.id)`.

### 264. Analytics / Usage: `pulseEvents` and the sparklines freeze on react-query structural sharing

**Severity:** ⚪ Low (live indicators stop sliding on an idle console)
**File:** `web/src/pages/Analytics.tsx:1154-1167`, `:1258-1263`; `web/src/pages/Usage.tsx:568-573`

```ts
const pulseEvents = useMemo<PulseEvent[]>(() => {
  const nowSec = Math.floor(Date.now() / 1000);
  return logs.filter((l) => l.ts >= nowSec - 60).map(…);
}, [logs]);
```

`Date.now()` is read inside the memo but is not a dependency, so the 60-second
window is recomputed only when `logs` changes identity. TanStack Query's
structural sharing returns the *referentially identical* array whenever the
polled payload is unchanged — the steady state.

**Trigger:** leave Analytics or Usage (both 15 s poll) open with no new traffic.

**Consequence:** the pulse meter and the last-hour sparkline stop sliding; the
window stays pinned at the moment of the last data change and the meter drains to
empty-looking bars that never re-fill. Verified: `useNow` is used by
`RequestLogs.tsx` (2 occurrences) but **not** by `Analytics.tsx` or `Usage.tsx`
(0 each), while both read `Date.now()` inside memos (`Analytics.tsx:1155-1167`,
`Usage.tsx:569`). This is the identical root cause AUDIT #114 fixed for
`RequestLogs`.

**Fix:** use `useNow` from `pages/logs-shared.tsx` and add `now` to the dependency
arrays, exactly as #114 did.

### 265. Console pass — verified clean (recorded so the next sweep does not re-audit)

**Severity:** ⚪ (no defect)

Re-verified as fixed at their cited lines and **not** re-reported: AUDIT
#27 (Models TDZ), #28 (Settings `<a href>`), #29 (Analytics `endsWith`),
#43–#47, #205–#211.

Also checked and found clean: every `addEventListener` has a matching
`removeEventListener` (0 unmatched) and all ten `setInterval`/`setTimeout`
effect sites clean up; `WiwiStream.close()` aborts its controller and the pump
loop exits on `this.closed`; the SSE frame parser (`api/sse.ts`) matches the
server's actual wire format (`subsystem.py:305-308`); no internal `<a href>`
remains (every one is `https:`/`mailto:` or guarded by an `external` flag); and
there are 0 uses of `dangerouslySetInnerHTML` (`Markdown.tsx` builds React nodes).

### 273. Public docs navbar logo has a redundant accessible name

**Severity:** ⚪ Low (accessibility)
**File:** `web/src/components/landing/Navbar.tsx:260`

**Trigger:** load any public page, including `/docs`, with the navbar logo and
the adjacent visible `wiwi` link text.

**Consequence:** screen readers announce the brand as `wiwi wiwi` because the
image alt text duplicates the link text.

**Fix:** make the logo image decorative with `alt=""` and retain the adjacent
link text as the accessible name.

**Status: fixed** — the navbar logo is now decorative.

### 274. Public docs footer uses low-contrast dim text

**Severity:** 🟡 Medium (WCAG 2 AA / binding UI/UX rule)
**File:** `web/src/components/PublicLayout.tsx:84-108`

**Trigger:** view the footer on `/docs` or another public page.

**Consequence:** the `#6b7280` dim text on `#050505` measures 4.21:1, below the
4.5:1 minimum for normal text.

**Fix:** use the existing `--admin-text-muted` token (`#9ca3af`) for footer
labels, links, and metadata.

**Status: fixed** — footer text now uses the higher-contrast muted token.

### 275. Horizontally scrollable docs code regions are not keyboard-focusable

**Severity:** 🟡 Medium (keyboard accessibility)
**File:** `web/src/pages/Docs.tsx:197` and `:571`

**Trigger:** Tab through the docs page on desktop or mobile and reach a code
block whose content overflows horizontally.

**Consequence:** the scrollable `<pre>` is omitted from the tab order, so a
keyboard user cannot move focus to it and scroll the code horizontally. Axe
reports `scrollable-region-focusable` for every affected code block.

**Fix:** give each scrollable code region `tabIndex={0}`, `role="group"`, a
descriptive `aria-label`, and a visible focus ring without introducing duplicate
landmarks.

**Status: fixed** — docs code regions are keyboard-focusable and visibly
focusable.

### 276. Mobile docs section selector label has low contrast

**Severity:** 🟡 Medium (WCAG 2 AA / binding UI/UX rule)
**File:** `web/src/pages/Docs.tsx:684`

**Trigger:** open `/docs` at a narrow mobile viewport and inspect the `On this
page` label above the section selector.

**Consequence:** the dim text measures 4.21:1 against the page background, below
the 4.5:1 minimum for normal text.

**Fix:** use the existing `--admin-text-muted` token for the selector label.

**Status: fixed** — the mobile section selector label now uses the
higher-contrast muted token.
