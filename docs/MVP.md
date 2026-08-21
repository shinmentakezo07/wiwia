# wiwi — MVP Definition

The MVP is a single-binary gateway exposing three native API surfaces — OpenAI Chat Completions (`/v1/chat/completions`), OpenAI Responses (`/v1/responses`, for Codex CLI), and Anthropic Messages (`/v1/messages`, for Claude Code / Anthropic SDK) — routing all of them to OpenAI/Anthropic/Gemini/any-OpenAI-compatible providers through one canonical internal format. It ships LiteLLM-style config, load balancing with retries and fallbacks, virtual keys, rate limits, budgets, spend tracking, and request logs. No web UI in the MVP; management happens through `wiwi.yaml` and a small admin REST API.

---

## 1. Problem statement

Teams using more than one LLM provider face: per-provider SDKs and payload formats, no unified place to control keys, no failover during provider outages, no visibility into who spent what, and clients hardwired to one vendor's API shape. Agent tooling deepens the lock-in: Codex CLI requires the OpenAI Responses API, Claude Code requires the Anthropic Messages API. wiwi sits between applications and providers as a unified proxy that speaks every major dialect natively and translates across them, with routing, access control, and cost accounting.

## 2. Target users

1. **App developers** who want to call any model through the OpenAI SDK they already use.
2. **Agent users** who run Codex CLI or Claude Code and want those tools to work against any provider, with keys/budgets/logging applied.
3. **Platform/team leads** who need key issuance, quotas, and cost visibility per project or teammate.
4. **Self-hosters** running local models (Ollama/vLLM) alongside cloud APIs behind one endpoint.

## 3. MVP scope

### In scope (must ship)

| # | Feature | Acceptance criteria |
|---|---|---|
| F1 | Three native API surfaces | `POST /v1/chat/completions`, `/v1/completions`, `/v1/embeddings`, `POST /v1/responses`, `POST /v1/messages`, `GET /v1/models` all live. Official `openai` SDK (chat + responses clients), official `anthropic` SDK, and raw curl succeed against each respective surface |
| F2 | Cross-dialect translation | Any surface reaches any provider: Claude Code (`ANTHROPIC_BASE_URL=wiwi`) completes sessions backed by OpenAI models; Codex CLI (wiwi as model provider) completes sessions backed by Anthropic models; tool calls/results translate correctly in both directions |
| F3 | Streaming in caller's dialect | `"stream": true` yields correct SSE per surface: `chat.completion.chunk` frames, Anthropic `message_start`/`content_block_delta`/`message_stop` sequences, Responses `response.output_*`/`response.completed` events; tool-call argument deltas stream in all three; final frame carries usage |
| F4 | Config-driven models | `wiwi.yaml` `model_list` with `model_name` + `wiwi_params`; same-name entries form a load-balanced group; `os.environ/` interpolation everywhere |
| F5 | Provider adapters | `openai/`, `anthropic/`, `gemini/`, `openai-compatible/` encode IR → provider and decode back, streams included; golden-file tested both directions |
| F6 | Load balancing + retries + cooldowns | Strategies `simple-shuffle`, `least-busy`, `latency-based`; retry on 408/429/5xx/connect within group; cooldown after `allowed_fails` |
| F7 | Fallbacks | `fallbacks` moves to backup groups after retries exhaust; `context_window_fallbacks` supported |
| F8 | Master + virtual keys | Master key from config/env; `/key/generate|info|update|delete`; hashed storage; model allowlists enforced; `Authorization: Bearer` everywhere plus `x-api-key` accepted on `/v1/messages` |
| F9 | Rate limits | rpm/tpm per key and global; sliding window; 429 + `Retry-After`; memory backend (Redis optional) |
| F10 | Budgets | `max_budget` per key with optional reset duration; rejected once over; resets on schedule |
| F11 | Spend tracking + logs | Per-request row (surface, tokens, cost, latency, status); daily aggregates via `/spend/report`; bundled pricing table + custom overrides |
| F12 | Admin API + ops basics | Admin endpoints master-key-guarded; `/health`; structured JSON logs with request ids; Dockerfile + compose; graceful shutdown draining streams |
| F13 | Client-compat essentials | `POST /v1/messages/count_tokens` works (local estimate); `x-wiwi-request-id` echoed on every response; beta/version headers (`anthropic-beta`, `anthropic-version`, `openai-*`) forwarded per allowlist so thinking/caching features survive; `stream_options.include_usage` honored and usage frames always emitted |
| F14 | Model aliases | `model_group_alias` map (e.g., `gpt-4 → gpt-4o`) resolvable on every surface without duplicating deployments |
| F15 | Prompt-cache control passthrough | Anthropic `cache_control` breakpoints on content blocks round-trip through IR losslessly (explicit part field) |

### Out of scope (post-MVP)

- Web admin UI
- Teams/users hierarchy (keys carry budgets directly in MVP)
- Stateful Responses API: server-side `previous_response_id` storage, hosted tool execution (`web_search`, `file_search`, computer use)
- Response caching (exact or semantic)
- Guardrails / prompt-injection filters / PII masking
- Azure OpenAI, Bedrock, Vertex AI adapters
- Pass-through endpoints, fine-tuning/files/batches APIs
- Prometheus metrics, tracing export (Langfuse etc.)
- SSO/SAML, audit logs, DB-managed model overlay, multi-region

## 3.1 Gap register (full audit vs LiteLLM/OpenRouter/Portkey)

Everything identified in the proxy audit, tracked to a phase. "P2" etc. refer to PLAN.md phases; "G" numbers are stable ids for referencing.

| Id | Gap | Why it matters | Phase |
|---|---|---|---|
| G1 | `/v1/messages/count_tokens` | Claude Code calls it constantly; 404 degrades the client | **MVP (F13)** |
| G2 | Beta/version header forwarding (`anthropic-beta`, `openai-*`) | thinking/caching features silently break without it | **MVP (F13)** |
| G3 | IR fields: `response_format`/json_schema, `parallel_tool_calls`, tool `strict`, `n` policy | structured outputs + tool-call control are table stakes | **MVP — IR defined in M2** |
| G4 | `stream_options.include_usage` honored/forced | OpenAI clients expect usage frames | **MVP (F13)** |
| G5 | `CredentialProvider` interface (static key vs Entra-ID/OAuth/SigV4) | Azure/Vertex/Bedrock need non-static auth later; pools must not hardcode secrets | interface in **M3**, impls post-MVP |
| G6 | Request-id echo header | greppable support story | **MVP (F13)** |
| G7 | Model aliases (`model_group_alias`) | zero-duplication renaming | **MVP (F14)** |
| G8 | Prompt-cache control passthrough (`cache_control`) | provider prompt caching must stay controllable | **MVP (F15)** |
| G9 | WebSocket proxying (OpenAI Realtime `/v1/realtime`) | voice agents; WS is outside the HTTP pipeline shape | post-MVP |
| G10 | Non-chat endpoints: audio speech/transcriptions, images, moderations | binary/streaming bodies bypass IR; thin authenticated passthroughs | post-MVP |
| G11 | Request hedging (second upstream on slow TTFT) | big p99 win for latency-sensitive agents | post-MVP |
| G12 | Load shedding (global/per-provider in-flight caps → fast 503) | fail fast instead of queueing into timeouts | post-MVP |
| G13 | Session affinity routing (sticky deployment per key) | makes provider prompt caches actually hit; free money | post-MVP |
| G14 | Per-request cost cap + pre-call estimate | reject before dialing when estimate exceeds remaining budget margin | post-MVP |
| G15 | Files + Batch APIs (`/v1/files`, `/v1/batches`) | async job polling proxy | post-MVP |
| G16 | Pass-through endpoints (`/anthropic/...`, `/openai/...`) | escape hatch for untranslated features | post-MVP |
| G17 | Mock provider (`mock/gpt-4o` canned SSE) | trivial CI + admin "Test group" button | post-MVP (small) |
| G18 | Request replay from logs page | one-click re-run of failed requests | post-MVP (UI) |
| G19 | Priority lanes (`priority: high` routing) | premium deployments for urgent traffic | post-MVP |
| G20 | Webhook event system spec (events, signatures, retries) | outbound integrations beyond SSE | post-MVP |

Also reserved: IR multimodal part kinds (`audio`, `video`, `document`) get field names in M2 even though translation ships later (Gemini video, Anthropic PDFs).

## 4. Non-functional requirements

| Requirement | Target |
|---|---|
| Proxy overhead (non-streaming) | < 30ms p50 added latency vs direct provider call |
| Streaming first-byte relay | < 50ms from upstream byte arrival to client byte sent |
| Concurrent streams per instance | ≥ 200 on one small container |
| Dialect fidelity | Full agentic Codex and Claude Code sessions (multi-turn tool loops) run unmodified through wiwi |
| Config validation | Fail fast at startup with file/line-precise errors |
| Data safety | Keys hashed at rest; secrets never logged; bodies not persisted by default |
| Compatibility | Any client speaking OpenAI Chat, OpenAI Responses, or Anthropic Messages works unmodified |

## 5. User stories (MVP acceptance)

1. As a developer, I point `OPENAI_BASE_URL` at wiwi, use my wiwi virtual key, set `model: claude-sonnet`, and get a streamed Claude response through the OpenAI SDK.
2. As a Codex user, I configure wiwi as my model provider (`base_url` → wiwi `/v1`); Codex plans, edits files, and runs its tool loop entirely through wiwi while the backing model is Claude.
3. As a Claude Code user, I set `ANTHROPIC_BASE_URL` to wiwi; Claude Code lists models from wiwi, streams responses, and executes tool loops even when the deployment is GPT or a local Ollama model.
4. As an admin, I list two `gpt-4o` deployments with different keys; when one hits its provider rate limit, wiwi retries on the other transparently.
5. As an admin, I configure `fallbacks: {claude-sonnet: [gpt-4o]}`; during an Anthropic outage, traffic automatically serves from OpenAI.
6. As a team lead, I generate a key capped at $10/month and 60 rpm; the 61st request in a minute gets a clean 429, and further requests get `budget_exceeded` after $10.
7. As an admin, I call `/spend/report?start=2026-08-01&group_by=key,model` and see which key/model consumed what.
8. As a self-hoster, I add `openai-compatible/llama3 @ http://localhost:11434/v1` next to cloud models and route between them by config only.

## 6. Milestones

| Milestone | Contents | Demo gate |
|---|---|---|
| M1 — Walking skeleton | FastAPI app, config loader, openai adapter, non-streaming `/v1/chat/completions` passthrough, health, request-id echo (G6) | curl through wiwi to OpenAI succeeds |
| M2 — IR + wire codecs + streaming | Canonical IR incl. G3 fields + reserved multimodal kinds; codecs for all three surfaces; SSE relay; anthropic + gemini + openai-compat adapters; golden-file tests; header forwarding (G2); `count_tokens` (G1); `stream_options` (G4); `cache_control` passthrough (G8); model aliases (G7) | OpenAI SDK, Claude Code (`ANTHROPIC_BASE_URL`), and Codex CLI each complete a streamed tool-calling session through wiwi |
| M3 — Router | Model groups, strategies, retries, cooldowns, fallbacks, timeouts; `CredentialProvider` interface seam (G5) | kill one deployment mid-test; traffic shifts automatically |
| M4 — Control plane | Master/virtual keys, key CRUD API, rate limits, budgets | unauthorized and over-limit requests rejected correctly on all three surfaces |
| M5 — Money + observability | Token counting, pricing engine, spend upserts, request logs, `/spend/report` | spend report matches hand-computed totals |
| M6 — Ship | Docker image, compose file, docs, sample configs, load test pass | fresh clone → `docker compose up` → working gateway in < 5 min |

M6 complete = MVP done.

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Stream/event translation edge cases (tool args, usage frames, reasoning items) differ per dialect | Golden-file fixtures captured from real clients (Codex, Claude Code, OpenAI SDK) replayed in CI both directions |
| Codex/Claude Code ship new client behaviors (e.g., gateway model discovery, encrypted reasoning round-trip) | Adapters isolated; contract tests pinned to client versions; weekly live smoke tests |
| Cost accuracy disputes | Prefer provider-reported usage; document estimation fallback; store raw token counts alongside cost |
| Scope creep toward full LiteLLM parity | Post-MVP list explicit; new features require updating this doc first |
| Single-dev velocity | Thin core: FastAPI + httpx + SQLAlchemy only; no other framework lock-in |

## 8. Success metrics (first month after MVP)

- One real Claude Code session and one real Codex session run entirely through wiwi, cross-provider, without patching either tool.
- 100% of test-suite requests succeed through wiwi vs direct calls (translation fidelity).
- p95 added latency < 100ms including auth and logging.
- Zero plaintext credentials in logs or DB (verified by audit script).

## 9. Open questions

1. Default token estimator for non-OpenAI models: chars/4 heuristic vs bundling small tokenizers?
2. Should `/v1/models` hide wildcard/passthrough groups from non-master keys? (Lean yes.)
3. Redis required for rpm/tpm correctness at 2+ replicas from day one, or accept slight over-admission in MVP? (Lean accept.)
4. Preserve Anthropic `thinking` signatures when routing Anthropic-dialect requests to non-Anthropic backends, or strip and warn? (Lean strip + warn.)
5. `n > 1` on surfaces backed by providers without native support: reject, or fan out to n upstream calls and merge? (Lean reject in MVP.)
6. Session affinity (G13): default-on per key with a config kill-switch, or opt-in flag per virtual key? (Lean opt-in.)
