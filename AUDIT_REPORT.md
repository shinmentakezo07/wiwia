# wiwi — Deep Audit Report

**Date:** 2026-09-15
**Baseline at audit start:** 1664 tests pass, `ruff check wiwi/ tests/` clean.
**Baseline before fixes:** 1672 tests pass, ruff clean.
**Scope:** every Python module under `wiwi/` (56 files) and every TS/TSX module under
`web/src/` (111 files) — logic errors, unwired logic, missing logic, dead code.

**Method.** Six parallel subsystem reviewers (wire codecs, provider adapters, core
gateway + router, server routes + config, auth/ratelimit/cache/logging, admin SPA),
each required to cite `file:line` and quote source. Every claim in this report was then
re-verified by me against the tree; every finding marked **REPRODUCED** was driven
end-to-end through the real classes or a real ASGI app, not reasoned about.

**Legend.** 🔴 critical · 🟠 high · 🟡 medium · ⚪ low
**Status.** `OPEN` · `FIXED` (this round) · `RE-VERIFIED` (already in `AUDIT.md`)

---

## Environment defect found first

**`import wiwi` resolves to a different checkout.**

```
$ cd /tmp && python3 -c "import wiwi; print(wiwi.__file__)"
/teamspace/studios/this_studio/Fionn/wiwi/__init__.py      # NOT the repo under audit
```

The installed (editable) package points at `/teamspace/studios/this_studio/Fionn`,
whose tree differs from `/teamspace/studios/this_studio/wiwia` in ~20 modules
(`core/gateway.py`, `auth/service.py`, `config.py`, most providers, …). Any script,
REPL, or ad-hoc probe run from outside the repo root silently exercises the wrong
code. `python3 -m pytest` from the repo root is unaffected (rootdir insertion), which
is why the suite never surfaced it.

**Fix:** reinstall the editable package from the audited checkout, or always run with
`PYTHONPATH=/teamspace/studios/this_studio/wiwia`. Every reproduction in this report
asserted `wiwi.__file__` before running.

---

## 🔴 Critical

### C1. Budget caps do not hold — the cap is advisory on both paths
**File:** `wiwi/server/app.py:1313-1320` (non-streaming), `wiwi/server/app.py:1516-1522`
(streaming); `wiwi/auth/service.py:334-362`
**Status:** `FIXED` — shared `record_spend()` helper (`wiwi/server/app.py:993-1014`) trues up the charge via `apply_spend_trueup` when the conditional UPDATE is refused; used by both the non-streaming (`:1336`) and streaming (`:1523`) paths. Live smoke: an over-budget key now returns `402, 402, 402` where it previously returned `200` forever.

`AuthService.update_spend` is a *conditional* UPDATE: when the charge would cross
`max_budget` it returns `False` and leaves `spend_to_date` **unchanged**. Both callers
either answer 402 (non-streaming) or merely flip `ctx.status` (streaming) — after the
upstream has already served and billed the request.

**REPRODUCED** end-to-end (`max_budget=0.005`, each request costs $0.002):

```
non-streaming  #1 200 spend=0.0020 | #2 200 spend=0.0040 | #3..7 402 spend=0.0040   upstream_calls=7
streaming      #1 200 spend=0.0020 | #2 200 spend=0.0040 | #3..6 200 spend=0.0040   text_delivered=True
```

Non-streaming refuses *after* billing, so three requests were served for free.
Streaming never trips at all: content keeps flowing and `spend_to_date` freezes at
0.0040 for the life of the key — the cap is not enforced in any observable way.

**Fix:** on a refused conditional update, record the authoritative charge
unconditionally (`apply_spend_trueup` already exists for exactly this "already happened
upstream" case) and keep refusing *future* requests. The cap becomes a ceiling on new
work, not a mechanism for losing revenue already incurred.

---

### C2. Response cache serves the wrong answer when `extras` differ
**File:** `wiwi/cache/keygen.py:66-75`
**Status:** `FIXED` — `extras` folded into the cache key payload (`wiwi/cache/keygen.py`).

The cache key hashes `group, surface, key_id, model, messages, tools, tool_choice,
gen_params, stream` — but **not `ir_req.extras`**, which *is* forwarded upstream
(`wiwi/providers/openai_adapter.py:242-249`, `anthropic_adapter.py:431`).

**REPRODUCED** through a live ASGI app with `cache_settings.enabled=True`:

```
request 1 (service_tier=flex)     -> FLEX-ANSWER     cache: -
request 2 (service_tier=priority) -> FLEX-ANSWER     cache: HIT     upstream calls: 1
```

Colliding fields include `logit_bias`, `frequency_penalty`, `presence_penalty`,
`top_logprobs`, `user`, `service_tier` (Chat); `store`, `truncate`, `include` (Responses);
and everything the Anthropic codec parks in `extras`. A caller who bans a token with
`logit_bias` can be served a completion generated without that ban, for the whole TTL.

**Fix:** add `extras` (and any other unhashed `Request` field) to the key payload.

---

### C3. An authenticated admin *session* cannot reach 33 of 48 admin routes
**File:** `wiwi/server/app.py:923-927` (`is_admin`), `:1780` (`_require_admin`)
**Status:** `FIXED` — the bearer-only `_require_admin` is deleted; all 40 `/admin/*` sites now use the session-aware `require_admin_dep`. Re-verified across all 17 `/admin` GET routes: anonymous `401`, non-admin session `403` (actor-scoped routes `200` by design), admin session `200`, master bearer `200`.

`README.md:717` and `docs/ADMIN.md:30` both promise "master key **or an authenticated
admin session**". Only three routes use the session-aware `require_admin_dep`; the other
33 call `_require_admin`, which delegates to `is_admin` — a bearer-compare against the
master key alone.

**REPRODUCED** with a user promoted to `role=admin` and logged in by cookie (no bearer):

```
/auth/me -> 200 {"role":"admin"}
  GET /admin/providers         -> 401      GET /admin/keys         -> 200
  GET /admin/provider-catalog  -> 401      GET /admin/models       -> 200
  GET /admin/pricing           -> 401      GET /admin/users        -> 200
  GET /admin/logs/proxy        -> 401      GET /admin/stats/overview -> 200
  GET /admin/alert-rules       -> 401
```

Eight console pages render (the sidebar is role-aware) and then every fetch 401s.
A session admin is also absent from the audit trail's actor for those routes.

**Fix:** route every `/admin/*` guard through `require_admin_dep` so both credential
forms are accepted.

---

## 🟠 High

### H1. Provider rename leaves a stale `alias_to_provider` entry → alias misrouting
**File:** `wiwi/server/app.py:2179-2196`
**Status:** `FIXED` — the `alias_to_provider` repair now runs on every rename (not only when the PATCH carries `alias_id`), and a rename onto a name already used as another provider's alias value is rejected `409`.

The alias-map repair is gated on `if alias_change is not None`, but the map is keyed by
provider *name*.

```
before rename: {'myalias': 'p2'}      resolve('myalias') = myalias
PATCH {"name": "p2ren"} -> 200, alias_id echoed as "myalias"
after  rename: {'myalias': 'p2'}      resolve('myalias') = None        <- 404s
then create a NEW provider named "p2": resolve('myalias') = myalias    <- routes elsewhere
```

The SPA's rename sends `{name}` alone, so this is the ordinary path. The map is
persisted and re-advertised by `/admin/models`, so the misrouting survives restarts.

### H2. `force_stream` non-streaming path: a mid-body drop escapes as a raw `httpx` error
**File:** `wiwi/core/gateway.py:452-463`; `wiwi/router/router.py:957`
**Status:** `FIXED` — `except httpx.TransportError` added to the mid-body read loop (`wiwi/core/gateway.py`), raising `WiwiError(504 timeout)` / `WiwiError(502 api_connection_error)`, both retryable. Accounting stays with `execute_with_retries`' except handler so nothing double-counts. Verified: `err_count=1`, `status='cooling'`, deployment cooldown set.

`_complete_via_stream` catches only `TimeoutError` / `StopAsyncIteration`; the retry
loop catches only `WiwiError`. A chunked peer that closes without its terminal 0-chunk:

```
_complete_via_stream RAISED httpx.ReadError
  is WiwiError (=> retry/failover would run): False
  key err_count=0 status=active
```

No retry, no failover, no key/deployment health accounting. Cline and WorkBuddy are the
`force_stream=True` providers, and their non-streaming surface always takes this path.
`_pump_once` handles the identical failure correctly (`:1093-1101`).

### H3. Same path bills zero and reports `estimated=False`
**File:** `wiwi/core/gateway.py:490-497` vs `:1018-1026`
**Status:** `FIXED` — the same path now estimates usage when the provider omits it (`estimated=True`), matching the stream pump. Before/after: `0/0, estimated=False, cost=0` → `1/2, estimated=True, cost=3e-06`.

Both Cline (`cline_adapter.py:134`) and WorkBuddy (`workbuddy_adapter.py:245`) pop
`stream_options`, so upstream never sends usage — the zero case is the normal case.
The streaming pump falls back to `estimate_tokens_async(..., estimated=True)`; the
non-streaming reassembly prices the zeros directly. Spend and TPM record 0 while
presenting it as provider-reported fact (the `AUDIT.md` #131 mislabelling class).

### H4. Five client-triggerable 500s in the wire codecs
**Status:** `FIXED` — every unguarded client-controlled shape in the three decoders is now guarded (malformed blocks skipped, non-dict schemas coerced), while genuinely invalid bodies still raise `DialectError`. A 1804-mutation sweep went from 42 uncaught `AttributeError`/`TypeError` to **0**.

| Body | Result | Site |
|---|---|---|
| chat `tool` message, `image_url` as a bare string | `AttributeError: 'str' object has no attribute 'get'` | `openai_chat.py:136` |
| chat `image_url.url = 7` | `AttributeError: 'int' object has no attribute 'startswith'` | `openai_chat.py:54` |
| anthropic `source` as string / number | `AttributeError` (3 sites) | `anthropic_messages.py:56, 65, 109` |
| chat `response_format.json_schema = "abc"` | `AttributeError` | `openai_chat.py:193-197` |
| non-dict tool schema (`parameters: "oops"`) | accepted, then crashes `validate_tool_args` **inside the stream pump** → cools a healthy deployment and increments the key's error streak | `openai_chat.py:162`, `openai_responses.py:126`, `anthropic_messages.py:162` |

The Responses codec already has the `isinstance(fmt, dict)` guard that Chat is missing
(`AUDIT.md` #100 fixed only the Responses half).

### H5. NIM and OpenRouter forward dict-valued tool arguments; the Responses encoder dies
**File:** `wiwi/providers/nim_adapter.py:343-347`, `wiwi/providers/openrouter_adapter.py:380`
**Status:** `FIXED` (by a concurrent editor, independently verified) — `providers/base.coerce_args_fragment` normalizes `str`/`dict`/other at every call site in the OpenAI, OpenRouter and NIM adapters.

Same frame through both adapters:

```
openai      -> [('ToolCallOpen',''), ('ToolCallArgsDelta', '{"city": "SF"}')]
openrouter  -> [('ToolCallOpen',''), ('ToolCallArgsDelta', {'city': 'SF'})]
ResponsesStreamEncoder.feed -> TypeError: can only concatenate str (not "dict") to str
```

`OpenAIAdapter` normalizes this shape (`openai_adapter.py:496-502`); the two adapters
that override `decode_stream_event` kept the round-49 `[DONE]` fixes but not this one.

### H6. NIM never flushes tool state on `[DONE]`
**File:** `wiwi/providers/nim_adapter.py:225-228`
**Status:** `FIXED` — the NIM `[DONE]` arm now runs a `_flush_open_tools()` override that closes every open tool call, drains buffered aliased args, and appends `Finish("tool_call")` only when something was flushed.

```
nvidia-nim [ToolCallOpen, ToolCallArgsDelta, StreamEnd]                          leftover open indices: {0}
openai     [ToolCallOpen, ToolCallArgsDelta, ToolCallClose, Finish, StreamEnd]   {}
```

The gateway then synthesizes `Finish("stop")` for a turn that produced tool calls — the
`AUDIT.md` #133 corruption (`end_turn` beside `tool_use`), which terminates Claude Code's
agent loop. For an *aliased* tool the buffered arguments are never emitted at all,
leaving a `tool_use` block with no input. NIM is the third copy-derived site; OpenAI and
OpenRouter were fixed in round 49.

### H7. Non-dict `choices[0]` / `candidates[0]` crash NIM and Gemini
**File:** `wiwi/providers/nim_adapter.py:253-257`, `wiwi/providers/gemini_adapter.py:218`
**Status:** `FIXED` — NIM returns early on a non-dict `choices[0]`; Gemini drops a frame whose first candidate is non-dict *before* opening the stream, and skips non-dict `parts` elements.

`{"choices": [null]}` → `openai: []`, `openrouter: []`, `cline: []`, but
`nvidia-nim` and `gemini`: `AttributeError: 'NoneType' object has no attribute 'get'`
→ 500 + deployment cooldown + key error streak for a frame carrying no semantics.
The round-49 fix guarded the SSE *frame* but not its first element.

### H8. Gemini stream decoder ignores `promptFeedback.blockReason`
**File:** `wiwi/providers/gemini_adapter.py:217-218`
**Status:** `FIXED` — the stream decoder reads `promptFeedback.blockReason` and emits `UsageFinal` (when present), `Finish("content_filter")`, `StreamEnd`, mirroring the sync decoder. Checked before the non-dict-candidate guard so a safety block is never masked.

The sync decoder maps `blockReason` → `content_filter` (`:148-149`); the stream decoder
never reads it. A safety-blocked prompt yields `[StreamStart]` and nothing else, so the
pump calls `_note_stream_failure` → a legitimate content filter cools a healthy
deployment and bills an estimated partial.

### H9. `expire_keys` expires the DB row but not the auth cache
**File:** `wiwi/auth/service.py:457-465`
**Status:** `FIXED` — `expire_keys` now invalidates the auth cache alongside the DB row.

Unlike every sibling mutator, `expire_keys` never pops `self._cache`:

```
DB  expires_at = now                -> DB says EXPIRED: True
cached AuthInfo.expires_at = now+3600 -> authenticate() returns a VALID key
```

Playground keys are minted with `ttl_seconds` and no budget, so they take exactly the
`max_budget is None` cache branch. Rotation leaves up to 60 s of extra validity per
revoked credential.

---

## 🟡 Medium

### M1. Virtual Keys "Edit → Save" silently wipes the model allowlist
**File:** `web/src/pages/VirtualKeys.tsx:270-276`, submit at `:527`
**Status:** `FIXED` — verified in a real browser at 375px and desktop: `openEdit` seeds every editable field, an untouched Save preserves `models: ["smoke-model"]` (pre-fix it sent `[]`), and only the new checkbox sends an empty allowlist.

`openEdit` seeds only `editBudget`; `editRpm`/`editTpm`/`editModels` keep their previous
(or empty) state, yet submit always sends `patch.models = parseCsv(editModels)`.

```
created models allowlist: ['gpt-4o']
after PATCH models=[]   : []      <- empty = ALL MODELS ALLOWED
```

`editRpm`/`editTpm` persist across dialog opens, so editing key B can push key A's limits
onto it.

### M2. Combos "Edit → Save" always 409s after already deleting the unticked deployments
**File:** `web/src/pages/Combos.tsx:205-210`; backend 409 at `wiwi/server/app.py:2806`
**Status:** `FIXED` — the attach loop skips already-attached deployments. Confirmed the premise: re-POSTing an attached deployment returns `409`.
partially mutated while the dialog reports an error.

### M3. `/admin/keys/{id}/disable` inverts `"false"`
**File:** `wiwi/server/app.py:1671`
**Status:** `FIXED` — a string `"false"` is no longer stored as `True`.

```
explicit bool False                -> {'disabled': False}
string "false" (intent: ENABLE)    -> {'disabled': True}    <- stored True
sibling PATCH enabled:"false"      -> 400 "enabled must be a boolean"
unknown key_id                     -> 200
```

The exact `bool()` coercion pattern siblings were fixed to reject (`AUDIT.md` #71/#82);
`set_disabled` also issues an unguarded UPDATE, so an unknown id answers 200.

### M4. Dead Redis fallback guard → silent permanent cache misses
**File:** `wiwi/cache/__init__.py:27-31`
**Status:** `FIXED` — the factory now probes `import redis.asyncio` before selecting the Redis backend, so a configured-but-uninstalled extra falls through to memory **with a warning** instead of degrading silently forever.

The `except ImportError` cannot fire: `RedisResponseCache.__init__` never imports
`redis` (deferred to `_redis()`). With redis uninstalled and a URL configured,
`build_response_cache` returns `RedisResponseCache`, whose `get()` then returns `None`
forever — no log, no startup failure, no fallback.

### M5. `merge_resume_context` overwrites rather than accumulates `_stream_usage`
**File:** `wiwi/core/gateway.py:137-139`
**Status:** `FIXED` — `merge_resume_context` sums instead of overwriting, and the gateway now yields a merged `UsageFinal` so the client sees the same total that is billed. Reproduced pre-fix: client saw `(9,1)` while billing `(10,2)`; post-fix both are `(10,2)`.

Token counters and `cost` accumulate across resumes, but the encoder-facing
`_stream_usage` is assigned wholesale, so with `stream_resume_max_retries > 1` the
client-visible usage block reflects only the last attempt while billing reflects all.

### M6. Prometheus exporter misdeclares its metric types; advertises a metric it never emits
**File:** `wiwi/server/metrics.py:134-138`; `docs/API_REFERENCE.md:79`
**Status:** `FIXED` — the three quantile families are declared `summary`, every family has `# HELP`, the false `wiwi_provider_cooldowns` claim is gone, and `wiwi_request_logs_dropped_total` is emitted. `promtool check metrics`: 12 errors → 0 (2 deliberate `_ms` unit warnings remain).
`expfmt` parser (every `wiwi_request_duration_ms` sample parses as an *empty histogram*,
`SampleCount=0`), so `histogram_quantile()` yields nothing.

`# TYPE … histogram` is declared for `wiwi_request_duration_ms`, `wiwi_ttft_ms`,
`wiwi_tps`, but only `{quantile=…}` samples are emitted — no `_bucket`/`_sum`/`_count`.
`wiwi_provider_cooldowns` is documented in the module docstring and the API reference and
is rendered nowhere.

### M7. Frontend routes that fall through to the marketing landing page
**File:** `web/src/main.tsx:90` (route table), `Integrations.tsx:19-24`, `Migration.tsx:81`,
`Blog.tsx:109`, `OpenSource.tsx:213-216`, `Referrals.tsx:189`,
`components/shared/EnterpriseComponents.tsx:801`
**Status:** `FIXED` — the frontend routes no longer fall through to the marketing landing page.

`/docs/cursor|cline|n8n`, `/blog/<slug>`, `/migration/<slug>`, `/compare/<vendor>`,
`/legal/privacy` all miss the route table and hit the `path="*"` catch-all → `/`.
`AUDIT.md` #113 claims a `/docs/*` redirect was added and marks it **fixed**, but
`grep '"/docs' web/src/main.tsx` returns only the bare `/docs` route — the claimed fix is
not in the tree.

### M8. Dashboard status-mix divides hour-scoped slices by the whole ring
**File:** `web/src/pages/Dashboard.tsx:677` (`total={logs.length}`) vs `:404-412`
(slices scoped to 3600 s); `console-visuals.tsx:504, 526`
**Status:** `FIXED` — the Dashboard status-mix no longer divides an hour-scoped slice by the whole ring.

Both segment widths and the printed percentages use the unscoped denominator, so on any
deployment with more than an hour of traffic the bar under-fills and every legend
percentage is understated.

### M9. ProxyLogs declares 4 headers for 5 body cells
**File:** `web/src/pages/ProxyLogs.tsx:182` vs `:231`
**Status:** `FIXED` — the ProxyLogs header count matches the body cell count.

The level-accent `<td>` is unlabelled, so every header sits one column left of its data
and the request-id column has none. The detail row's `colSpan={5}` (`:257`) shows five
was intended.

### M10. `wiwi --reload` ignores `--config` and `WIWI_CONFIG`
**File:** `wiwi/main.py:54-63`; factory `wiwi/server/app.py:4133`
**Status:** `FIXED` — the precedence rule moved into a shared `_resolve_config`, and `cli` publishes the resolved `--config` as an absolute path through `WIWI_CONFIG_PATH` so the reload subprocess resolves the same source. Verified live: `wiwi --reload --config /tmp/wiwi_smoke.yaml` serves that config's single group and `smoke-model`, not the CWD's `wiwi.yaml`.

The reload branch passes only the import string; uvicorn 0.52 has no `factory_kwargs`
plumbing, so `create_app_from_config_path` always loads the literal `wiwi.yaml` from the
CWD — wrong providers, wrong master key, wrong database, silently.

### M11. `RouterSettings.timeout` is documented, shipped, and never read
**File:** `wiwi/config.py:168`, `wiwi.yaml.example:80`
**Status:** `FIXED` — `ProviderDef.timeout_s` became `float | None = None` and a `WiwiConfig` validator resolves unset provider timeouts from `router_settings.timeout`, routing through the gateway's existing `dep.timeout or dep.provider.timeout_s`. Precedence is now `wiwi_params.timeout` > provider `timeout_s` > `router_settings.timeout`; resolving at validation time keeps `None` from ever reaching `httpx` (which would mean "no timeout at all"). Verified on the wire: `router 45 / provider unset` → `45.0`, `router 45 / provider 300` → `300.0`.

Every request path uses `dep.timeout or dep.provider.timeout_s`; no code reads
`settings.timeout`. The adjacent `ProviderDef.timeout_s` and `DeploymentParams.timeout`
of the same name do work, so the no-op sits exactly where a reader expects it to apply.

### M12. `/admin/stats/*` diverge between the DB and ring backends for `minutes=0`
**File:** `wiwi/server/app.py:3093-3102` (timeseries), same shape in `admin_stats_overview`
**Status:** `FIXED` — `/admin/stats/*` no longer diverge between the DB and ring backends for `minutes=0`.

The DB path treats `minutes=0` as all-time and derives the bucket size from the real
window; the ring fallback rewrites it to 1440 minutes and passes the client's raw
`bucket` string. Same query, two answers — and the ring path is what serves right after a
restart.

### M13. Audit stream has no reader
**File:** `wiwi/logging_core/subsystem.py:136-139`; `db_sink.py:368-374`
**Status:** `FIXED` — `DBSink.read_audit()` + `LoggingSubsystem.read_audit()` added and wired to a new `/admin/logs/audit` route. Verified live: `200` with rows for a master bearer, `401` anonymously.

`log_audit` writes to both an SSE ring and `audit_logs`, but nothing calls
`subscribe`/`replay("audit")`, there is no `read_audit`, no endpoint and no UI page. Both
copies accumulate unread. (Distinct from `AUDIT.md` #38, which is about events dropped
when the sink is absent — that half is fixed.)

### M14. Three `request_logs` indexes serve no query
**File:** `wiwi/logging_core/db_sink.py:200-203`
**Status:** `FIXED` — the three unused indexes are no longer created, and `DROP INDEX IF EXISTS` was added to the idempotent migration list so deployed databases shed them too.
`request_id`; the only predicates are on `ts`, `key_id`, `cost`, `id`. Write amplification
and disk for nothing, plus comments promising filters that do not exist.

---

## ⚪ Unwired and dead — with grep verdicts

### Config fields with zero readers
| Field | Claim in docs | Reality |
|---|---|---|
| `WiwiSettings.log_requests` | `README.md:553`, `detailed.md:117` | no reader anywhere; logging is unconditional, so `false` changes nothing |
| `WiwiSettings.header_allowlist` | `README.md:921` — "controls which inbound headers are forwarded upstream" | no reader; no inbound header is ever forwarded (`gateway.py:228` builds from adapter + `extra_headers` only) |
| `RouterSettings.timeout` | `wiwi.yaml.example:80` | no reader (see M11) |

### Python symbols with zero production callers
| Symbol | Verdict |
|---|---|
| `core/gateway.py:142` `loop_abort_error` | dead — the live loop branch builds its error inline |
| `router/router.py:550` `_CrossProviderWRR._weights` | dead — `pick` recomputes inline |
| `core/recovery.py:91` `CircuitBreaker.dead` | tests only; production reads via `blocked` |
| `cost/pricing.py:45` `CostEngine.register` | tests only; production writes `cost.prices[mid]` directly |
| `auth/service.py:200` `AuthService.evict` | zero callers anywhere; siblings inline `_cache.pop` |
| `logging_core/db_sink.py:148` `invalidate_cache` | test-only; the 5 s TTL staleness it was meant to bound is unbounded |
| `providers/cline_adapter.py:95` `set_header_context` | tests only — `X-Task-ID` is never emitted for a real request |
| `providers/anthropic_adapter.py:40` `_system_text` | zero references; `_system_blocks_or_text` is used |
| `streaming/resume.py:96` `replay_thinking` | tests only (#118 replaced it with `replay_thinking_parts`) |
| `streaming/sse.py:79` `iter_sse_events` | dead (already noted in `AUDIT.md`) |
| `server/app.py:1817` `require_user_dep` | dead; also a trap — a plain function, not a `Depends` |
| `{cline,opencode,workbuddy}_version.py` `get_version` | no caller; headers use the cached sync getters, refreshed by the workers at `app.py:834-847` — **headers are not stale** |
| `ratelimit/memory.py:41` `_inflight` | assigned once, never read |
| `ratelimit/redis.py:64` `RedisRateLimiter` | never constructed (`app.py:536` builds the memory limiter) — the dormant half of `AUDIT.md` #31 |
| `router/router.py:869` `excluded_providers` | never populated; half the cycle-exclusion test is permanently `False` |
| `ir/types.py:312` `class Response` | never constructed anywhere |
| `providers/base.py` `AssistantTurn.raw` | written by 5 adapters, read by nobody |
| `ir/types.py:30` `ImagePart.detail` | no writer, no reader |
| `openai_chat.py:265-266` `ChatStreamEncoder._started/_finished`, `anthropic_messages.py:348` `AnthropicStreamEncoder._started`, `anthropic_adapter.py:164` `_think_indices` | write-only state |
| `core/context.py:36,53` `raw_body_bytes`, `log_buffer` | declared, never read or written; `docs/CORE.md` lists them as contract |
| `logging_core/subsystem.py:32` `_HEAVY_FIELDS` | unread; `_lightweight_copy` passes the literals |
| `logging_core/subsystem.py` `dropped_request_logs` | written in production, read only by a test — invisible to `/health` and `/metrics` |
| `auth/service.py:176-177` | duplicated `self._cache[h] = (info, now)` assignment |
| `auth/users.py:136` `_is_pg` | no dialect branch in the file |
| `server/metrics.py:11` `wiwi_provider_cooldowns` | documented, never emitted |
| `server/app.py:622` `_enable_sqlite_fk` | **not dead** — registered via `event.listens_for` |

### `ctx.metadata` keys written and never read
`tool_args_violations`, `unpriced_model`, `unpriced_model_id`, `response_cached`,
`stream_replayed`, `budget_exceeded` — none reach `build_log_event`, any endpoint, or the
UI.

### Frontend
- **31 of 111 modules have zero importers** (grep-verified): all of
  `components/landing/*` except `Navbar.tsx`, all of `components/shared/*`, and
  `theme.ts` — ~6.8k LOC of an abandoned `llmgateway.io` port whose live equivalents are
  inlined in `pages/Landing.tsx` and `components/PublicLayout.tsx`.
- **Unwired API helpers:** `clineAutoConnect` (so the backend
  `POST /admin/cline/oauth/auto-connect` is unreachable from the SPA — both UIs call
  `clineLoginUrl` instead) and `getRequestLogsWithLimit`.
- **Unused props/types:** `SSEHandlers` (`api/sse.ts:4`), `LatencyRibbon.unitLabel`.

### Dead-code scan noise (checked, no defect)
- `vulture` reported 72 unreferenced functions; 46 are FastAPI route handlers registered
  by decorator, 6 are Pydantic `field_validator`s, 4 are `event.listens_for` callbacks.
  Only the 9 non-framework symbols listed above are genuinely unreferenced.
- 43 fully-silent `except` handlers audited individually: all are intentional
  best-effort paths (cache misses, `QueueFull` drops, cancellation, TTL parsing) or
  documented test seams. No swallowed error hides a caller-visible failure.

---

## Verification log

| What | Command / harness | Result |
|---|---|---|
| Suite before any fix | `python3 -m pytest tests/ -q` | **1672 passed**, ruff clean |
| C1 non-streaming | live ASGI app, `respx` upstream, `max_budget=0.005` | 7 upstream calls, spend frozen at 0.0040, 5×402 |
| C1 streaming | same, `stream: true` | 6×200 with content, spend frozen, cap never trips |
| C2 cache collision | live app, `cache_settings.enabled=True` | request 2 → `x-wiwi-cache: HIT` with request 1's body |
| C3 admin session | live app, promoted user + cookie login | 33/48 `/admin/*` routes → 401 |
| H1 alias rename | live app, `POST` + `PATCH` + reuse | `resolve('myalias')` → `None`, then → another account |
| H2 force_stream drop | raw socket peer closing mid-chunk | `httpx.ReadError`, `err_count=0`, `status=active` |
| H4 codec 500s | direct `decode_request` calls | 5 distinct `AttributeError`s |
| H5 dict args | one frame through both adapters | OpenAI str, OpenRouter dict → `TypeError` in encoder |
| H6 NIM `[DONE]` | direct `decode_stream_event` | `{0}` left open vs OpenAI's `set()` |
| H7 non-dict choice | direct `decode_stream_event` | NIM/Gemini `AttributeError`, OpenAI `[]` |
| H8 Gemini block | direct `decode_stream_event` | `[StreamStart]` only, no `Finish` |
| H9 expire cache | real `AuthService` on SQLite | DB expired, `authenticate()` still valid |
| M1 allowlist wipe | live app PATCH | `['gpt-4o']` → `[]` |
| M2 combos 409 | live app | both re-POSTs 409 |
| M3 disable `"false"` | live app | stored `True`, sibling route 400s |
| M4 redis guard | import-blocked probe | returns `RedisResponseCache`, not memory |
| M6 metrics | `promtool check metrics`, Go `expfmt` | 12 lint errors; histograms parse empty |
| Frontend reachability | import-graph walk from `main.tsx` | 31 zero-importer modules |
