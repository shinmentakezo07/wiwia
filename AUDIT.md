# Fionn / wiwi — End-to-End Bug Audit

**Date:** 2026-08-26
**Baseline:** 532 tests pass, ruff clean. Bugs below exist in paths not covered by the thematic regression suite.

Each finding verified against source by reading the cited lines. Severities: 🔴 critical · 🟠 high · 🟡 medium · ⚪ low.

---
## ✅ Fixed

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
**File:** `wiwi/ratelimit/redis.py:108-131`

`zcard` returns member count, not sum of token values. A TPM limit of 10000 is enforced as "10000 requests". `record_tokens` never updates Redis sorted sets (only memory fallback). `zadd` member collisions silently lose reservations. *Currently dormant* — `app.py:170` always uses the memory limiter; `redis_url` is read but unused.

### 32. Memory `record_tokens` misattributes actual usage under concurrent same-key requests
**File:** `wiwi/ratelimit/memory.py:90-108`

Replaces the **newest** estimated reservation regardless of which request completed. Out-of-order completion → stale estimate remains, actual count replaces the wrong reservation. No request-id correlation.

### 33. `estimate_tokens` runs blocking tiktoken inside async stream-pump coroutines
**File:** `wiwi/cost/pricing.py:82-93` (called from `gateway.py:425, 481`)

`import tiktoken` + `get_encoding` (disk load) + `encode` (CPU-bound) are all synchronous, blocking the event loop. First-request latency spike + large-prompt stalls.

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
