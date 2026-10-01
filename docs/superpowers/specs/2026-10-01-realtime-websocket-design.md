# Realtime WebSocket proxy (`/v1/realtime`)

**Date:** 2026-10-01
**Status:** Accepted — user approved 2026-10-01
**Sub-project:** E of five (A: `/v1/completions` · B: stateful Responses · C: tracing ·
D: shedding/lanes/affinity · **E: this**)

## Goal

Let a Realtime-API client connect through wiwi and reach an OpenAI Realtime
upstream: `wss://…/v1/chat/completions` today, `wss://…/v1/realtime?model=…`
tomorrow. Auth, rate limits, model resolution, deployment selection, cooldowns and
key rotation all happen on the gateway side; the audio frames and session events
pass straight through.

## Context

Every wiwi surface is HTTP: three request/response dialects plus SSE. A Realtime
session is a long-lived bidirectional socket carrying base64 audio chunks in both
directions, session lifecycle events, and — after a client calls
`response.create` — server-sent transcripts that are *also* delivered on the same
socket.

The three obvious implementations, and why each is wrong:

1. **Buffer the socket into one HTTP request.** Impossible: the client sends audio
   for seconds and only then expects a response. Request/response has no shape
   that fits a stream of both directions.
2. **Translate between Realtime events and the IR.** Tempting — the IR already
   models text, audio and tool calls — and it is the wrong abstraction here. The
   session protocol is *stateful and ordered*: `session.update` mutates server-side
   state that later turns depend on, `conversation.item.create` carries prior turns
   by reference rather than by value, and `response.create` can be called many times
   per session. The IR is a value type with no session identity. Reconstructing it
   from socket traffic would require reimplementing the upstream's session store.
3. **Proxy bytes verbatim.** Correct. wiwi owns admission, routing and keys; the
   session protocol belongs to the upstream.

This design is therefore deliberately narrow: **a pass-through relay with real
gateway admission in front of it.** Everything wiwi already does for HTTP happens
here; nothing about the session protocol is reimplemented.

## Design

### Route

```
WS /v1/realtime?model=<group>[&session_id=…]
    Authorization: Bearer sk-wiwi-…      (or ?key=… for browser clients)
```

Auth accepts the same credentials as the HTTP surfaces, and the `key` query
parameter is honoured **only when** `router_settings` (or a new
`realtime.allow_key_in_query`) permits it, because a query string lands in proxy
logs and browser history. Header auth is the default and the documented path.

### Admission, at upgrade

Every check runs **before** the upstream socket is opened, because once the
upstream accepts a session there is a live session to tear down, and a client that
is refused after `101 Switching Protocols` has already seen a success it must then
unwind. The handler, in order:

1. Authenticate (`AuthService.authenticate`), same as HTTP → `401` before upgrade.
2. Rate-limit and budget-admit the virtual key → `429` / `402` before upgrade.
3. Resolve the model group (`Router.resolve_group`) → `404` before upgrade.
4. Pick a deployment (`Router.pick_deployment`), honouring cooldown, probation,
   rpm/tpm, the concurrency cap, the priority lane and session affinity — all of
   D's controls apply, with no new code.
5. Pick a key from that deployment's pool; record a slot reservation so the
   concurrency cap counts the session for its whole life.

Failure at any step returns a normal HTTP error response, not a WebSocket close
frame. This is the single most important property of the design: **the client can
still read the status code and the error body.**

### Relay

On success the handler opens the upstream socket with the deployment's key and
relays both directions:

- **Client → upstream:** verbatim text frames. wiwi never parses session events.
- **Upstream → client:** verbatim text frames, except that server events which
  carry usage (`response.done`, and the `conversation.item.*` variants that
  include usage) are *also* observed for billing — see below.
- **Close either side:** close the other, exactly once.

### Billing

A Realtime session is billed on tokens, and unlike HTTP there is no single
response to price. The rule: observe usage-bearing server events, keep the
**maximum** `total_tokens` reported for the session, and charge that once, at
close. Summing would double-count: `response.done` and the preceding
`response.output_item.done` report overlapping windows of the same conversation,
and a resumed response restates its input.

Max-not-sum is the honest reading of a protocol where the server restates
progress. The alternative — summing — inflates every multi-turn session, and
inflated bills are worse than a slightly under-counted one.

Pricing uses the same `cost/pricing.py` path as HTTP, keyed on the deployment that
served the session.

### Concurrency and lifecycle

A session holds a deployment slot from upgrade until close. That is exactly the
semantics D's `max_inflight` was built for, and it is why D landed first: without
it, long-lived sessions would be invisible to the admission filter and could
occupy an unbounded number of upstream connections.

Sessions are **not** written to the request-log DB as one row per turn. One
`request_logs` row is written at close, carrying the session's aggregate usage and
status. Per-event rows would make the log unreadable and would cost a DB write per
audio chunk batch.

### Capability gating

Only providers whose adapter declares realtime support may serve the route. A new
`ProviderAdapter.realtime_url` capability, defaulting to `None`, means "not
supported": the handler refuses with a `501` naming the provider rather than
dialling a URL that does not exist. `openai` and `openai-compatible` implement it;
the other nine return `None` and are unaffected by this feature entirely.

## Testing

1. **Admission before upgrade** — bad key → 401, over-budget → 402, unknown model
   → 404, non-realtime provider → 501. Each is an HTTP response, not a close.
2. **Relay fidelity** — against a fake upstream `ThreadingHTTPServer` speaking
   the WebSocket handshake: frames both ways arrive byte-identical, and a close on
   either side closes the other.
3. **Billing** — a session reporting usage twice is charged once (max, not sum).
4. **Capacity** — a session occupies its deployment's slot, so a cap of 1 sheds a
   second session with the same 503 as HTTP.
5. **Defaults** — with no realtime provider configured, nothing about existing
   behaviour changes.

## Non-goals

- Translating Realtime sessions into or out of the IR.
- Persisting session state in wiwi, so a client cannot resume a session against a
  different gateway process. (Single-process affinity is the same caveat as D's
  affinity map.)
- Bridging a client that only speaks HTTP to a Realtime upstream.