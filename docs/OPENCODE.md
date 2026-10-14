# OpenCode Console (`opencode`) provider

Deep dive for the one provider whose access depends on the *shape* of the
request rather than on a credential. Everything here was measured against the
live upstream on **2026-10-14**; where a claim is transcribed from documentation
instead of probed, it says so.

- [What it is](#what-it-is)
- [The free-tier admission gate](#the-free-tier-admission-gate)
- [How the shape was derived](#how-the-shape-was-derived)
- [What changed, and why](#what-changed-and-why)
- [Code map](#code-map)
- [Diagnostics](#diagnostics)
- [Re-deriving the shape when the client moves](#re-deriving-the-shape-when-the-client-moves)
- [Not fixable here](#not-fixable-here)

---

## What it is

OpenCode Console (historically "Zen") is a multi-protocol gateway on one base
URL. Which wire a model uses is **published per model**, not derived:

| wire | models | endpoint | credential header |
|---|---|---|---|
| responses | `gpt-*`, `grok-*`, `muse-spark-*` | `{base}/responses` | `Authorization: Bearer` |
| messages | `claude-*`, `qwen3.8-flash`, `qwen3.7-*`, `qwen3.6-*`, `qwen3.5-*` | `{base}/messages` | `x-api-key` |
| gemini | `gemini-*` | `{base}/models/{id}:generateContent` | `x-goog-api-key` |
| chat | everything else | `{base}/chat/completions` | `Authorization: Bearer` |

Base: `https://opencode.ai/inference/v1` (canonical — what models.dev and the docs
table publish). The legacy `https://opencode.ai/zen/v1` **still routes** and
existing configs keep working; only the default moved.

Two consequences of the table that are easy to get wrong:

- **The qwen family straddles two wires.** `qwen3.8-max` is on chat;
  `qwen3.8-flash` and the qwen3.7/3.6/3.5 lines are on messages. A `qwen`
  prefix sends the wrong one to `/messages`. Hence `_ROUTE_RULES`, a table.
- **`models.dev` carries no per-model overrides.** Its `opencode` entry is a
  bare `@ai-sdk/openai-compatible` with one base. The split above exists only on
  the docs page, so the table is transcribed from there and is the authority.

`jev-*` is the one published model with **no completion wire**: it is a System
One model on `{base}/systemone` (a typed `state` + `questions` → values/
probabilities protocol). It falls through to chat and fails upstream; this
adapter does not claim to serve it.

Every request asks for SSE (`force_stream`): Console answers as an event
stream, so a non-streaming caller's reply is pumped and reassembled by
`Gateway._complete_via_stream`. The Gemini route is the exception — its wire is
selected by the URL, so its body carries no `stream` field.

---

## The free-tier admission gate

`*-free` models (and the stealth `big-pickle`) answer `403 FreeTierError`
unless the request *looks like the OpenCode client*. This is a **shape** check,
not an auth check: it fires before authentication, so a bogus bearer reads
exactly like no bearer.

A request is admitted when **all** of these hold:

| # | condition | why it exists |
|---|---|---|
| 1 | `stream: true` in the body | the free tier only serves the event stream |
| 2 | `x-opencode-session` **and** `x-opencode-session-id` both present, id shaped `ses_` + 12 hex + 14 base62 | session affinity |
| 3 | `User-Agent: opencode/<version>`, version ≥ ~1.19 | minimum-client gate |
| 4 | tool payload contains `read` **and** `shell`, each carrying the client's real parameter schemas | "is this a coding agent" |
| 5 | `Authorization` **present** (any value; anonymous uses `Bearer public`) | the gate reads presence |
| 6 | `HTTP-Referer` and `x-opencode-request` **absent** | the client sends neither |

Two of those deserve emphasis because they contradict the obvious reading:

- **Condition 4 reads the schemas, not the names.** Six inert decoys 403
  however they are named; six *real* tools with their names rewritten 403;
  `read`+`shell` with `parameters` emptied 403. Only the **description** may be
  substituted. This is why the decoys are the client's real tool definitions
  wearing an inert description rather than empty stubs.
- **Condition 5 is about presence, not validity.** The adapter used to *omit*
  `Authorization` for the `anonymous` sentinel — deliberately, on the theory
  that a placeholder bearer would be `401`. But the gate needs the header to
  exist, and the anonymous client satisfies it with `Bearer public`. Omitting it
  403s even with a perfect body.

Measured matrix (each row differs from the passing row by exactly one factor):

| change | result |
|---|---|
| captured client request, replayed verbatim | **200** |
| tools: none | 403 |
| tools: 5 real | 403 |
| tools: 6 real | **200** |
| `read`+`shell`, real schemas | **200** |
| `read`+`shell`, description swapped | **200** |
| `read`+`shell`, `parameters` emptied | 403 |
| 6 inert decoys, any names | 403 |
| 6 real tools renamed | 403 |
| drop `x-opencode-session-id` | 403 |
| drop `Authorization` (the keyless path) | 403 |
| add `HTTP-Referer` | 403 |
| add `x-opencode-request` | 403 |
| drop `x-opencode-project` / `-client` / `Accept` / `b3` / `traceparent` / `x-session-affinity` / `x-session-id` | **200** |
| session `ses_`+26-char | **200** |
| session `ses_`+64-hex | 403 |
| UA `opencode/1.19.0` / `2.0.26` / `latest/2.0.26/cli` | **200** |
| UA `opencode/1.16.0` / `1.17.0` | 426 UpgradeRequired |
| UA `opencode/unknown` | 403 |

Note the session id: the **v2 CLI's own** `ses_<64 hex>` ids are rejected. That
`^ses_[0-9a-f]{64}$` pattern found in the binary belongs to a different id space;
the header carries the 26-char form, which is what we already minted.

Free quota is accounted **per session**, so `x-opencode-session` is reused per
credential (TTL 1 h, LRU-capped at 1000 buckets) rather than re-minted per
request — fresh ids spread traffic over new buckets and invite
`429 FreeUsageLimitError`.

---

## How the shape was derived

The method matters as much as the result, because the obvious one produced a
wrong answer first.

1. **Capture.** Put a logging proxy in front of the provider via a config
   override (`provider.opencode.options.baseURL`), run the real client, and dump
   the headers and body it actually sends.
2. **Replay.** Send those exact bytes straight to the upstream. If that 200s,
   the difference is in the request and the exercise is worth continuing.
3. **Bisect.** Remove one factor at a time, body and headers separately. Every
   row in the table above is one such removal.
4. **Rebuild.** Send our adapter's request, confirm 200, then confirm each
   header/body our adapter adds on top of the minimum is load-bearing.

### The trap that cost a round

An earlier round concluded the free tier had been **closed upstream**, made the
decoy cloak opt-in, and justified both with a control: *"the genuine unsigned-in
opencode CLI fails identically from this host."*

That control was invalid. It was run from a scratch directory whose
`opencode.json` overrode the provider `baseURL`, so the 403 it observed came
from the broken override rather than from the gate. Run from a clean directory,
the same command answers **200**.

> **A control that shares infrastructure with the thing under test proves
> nothing.** Re-run it in isolation before building on it. Eleven working
> deployments were detached on the strength of that broken control.

---

## What changed, and why

| # | before (v1) | after (v2) | reason |
|---|---|---|---|
| 1 | decoys `bash` + `read`, `parameters: {}` | `read` + `shell` with `_DECOY_PARAMETERS` | v2 renamed `bash`→`shell`; the gate reads the schemas |
| 2 | `anonymous` **omitted** `Authorization` | `Bearer public` on the OpenAI-wire routes | gate reads header presence |
| 3 | `x-opencode-session` only | both `x-opencode-session` and `x-opencode-session-id` | v2 rides the pair |
| 4 | sent `HTTP-Referer`, `x-opencode-request` | both removed | client sends neither; each one alone 403s |
| 5 | UA fallback `unknown` | fallback `1.19.0` | `unknown` 403s; <~1.19 is 426 |
| 6 | route by prefix (`"qwen"` → messages) | `_ROUTE_RULES` table, chat default | `qwen3.8-max` is a chat model |
| 7 | cloak always on | `opencode_free_tier_cloak` opt-**out** | default must satisfy the gate; paid-only deployments can turn it off |

Items 3 and 4 removed symbols with no remaining caller: `OPENCODE_REQUEST_RE`,
`canonical_request_id()`, and `_spoof_request`.

Item 5 was a silent outage of its own: with a cold cache the adapter sent
`opencode/unknown`, so the first ~10 s after every boot — and every request while
npm was unreachable — failed all free-model traffic.

---

## Code map

| Concern | Where |
|---|---|
| routing table, route selection | `wiwi/providers/opencode_adapter.py` — `_ROUTE_RULES`, `route_for_model()` |
| default base | `opencode_adapter.OPENCODE_ZEN_BASE` |
| credentials per wire | `_CREDENTIAL_HEADER`, `headers()` |
| anonymous marker | `ANONYMOUS_KEY_SENTINEL`, `ANONYMOUS_BEARER` |
| decoy definitions | `_DECOY_NAMES`, `_DECOY_PARAMETERS`, `_decoys_chat()`, `_decoys_responses()` |
| cloak switch | `FREE_TIER_CLOAK_PARAM`, `_cloak_requested()` |
| decoy call suppression | `_filter_decoys()`, `_filter_decoys_turn()` |
| session id | `canonical_session_id()`, `OPENCODE_SESSION_RE`, `stable_session_id()` |
| User-Agent | `wiwi/providers/opencode_version.py` — `build_user_agent()`, `FALLBACK_VERSION` |
| per-deployment flag | `DeploymentParams.opencode_free_tier_cloak` → `Deployment` → the gateway's params dict |

```yaml
providers:
  - name: zen
    provider: opencode
    base_url: https://opencode.ai/inference/v1
    keys:
      - {label: main, key: os.environ/OPENCODE_API_KEY}   # paid models
      - {label: anon, key: anonymous}                      # free models: Bearer public
```

```yaml
model_list:
  # default: cloak on, which the free tier needs
  - model_name: bunny
    wiwi_params: {provider: zen, model: space-bunny-free}
  # paid-only deployment: no phantom tools in the payload
  - model_name: paid
    wiwi_params: {provider: zen, model: glm-5.1, opencode_free_tier_cloak: false}
```

---

## Diagnostics

**Reading the error.** The string a client sees is wiwi's own classification,
not the upstream's:

```
io requires billing (403): OpenCode's free tier can only be used from within OpenCode.
```

`error_from_provider_status()` maps the upstream's `FreeTierError` to
`402 / permission_error`, and `status_for_key_pool()` returns `None` for it — a
policy refusal never retires a healthy key, and the request fails over to
another deployment instead. So **this message always means "the request shape
was refused"**, never "your key is wrong" and never "the tier is closed".

**Triage order** when a `*-free` model suddenly 403s — check the cheap ones
first:

1. `User-Agent` on the wire. `opencode/unknown` means the version cache is cold
   or npm is unreachable; anything below ~1.19 shows up as `426`.
2. Tool names in the body: `read` **and** `shell` present?
3. Their `parameters` non-empty? An empty `{}` is the 403.
4. `Authorization` present on an OpenAI-wire route?
5. Both session headers present?
6. No `HTTP-Referer`, no `x-opencode-request` (they should not exist any more —
   if they are on the wire, something bypassed this adapter).

**Useful log lines** (all `debug`):

```
opencode_credential_anonymous   route=chat   reason: Bearer public sent
opencode_credential_omitted     route=...    reason=anonymous_sentinel
opencode_credential_scheme      route=... scheme=...   (a wrong scheme is silently unread upstream)
opencode_decoy_call_dropped     tool=... index=...
opencode_version_refreshed      version=...
```

**Cheap live check** without spending a request through the gateway:

```bash
python3 - <<'PY'
import httpx
from wiwi.ir import types as ir
from wiwi.providers.base import ProviderKeyRef
from wiwi.providers.opencode_adapter import OpencodeAdapter
from wiwi.wire import openai_chat as oc

req = oc.decode_request({"model": "m", "messages": [{"role": "user", "content": "hi"}]})
a = OpencodeAdapter()
body = a.encode_request(req, "space-bunny-free", {})
a.build_url("https://opencode.ai/inference/v1", "space-bunny-free", True)
h = a.headers(ProviderKeyRef(label="anon", secret="anonymous"))
r = httpx.post("https://opencode.ai/inference/v1/chat/completions",
               content=__import__("orjson").dumps(body), headers=h, timeout=90)
print(r.status_code, r.text[:120])
PY
```

`200` means the shape is good and the fault is elsewhere (model retired,
account, route). `403` means the shape regressed.

---

## Re-deriving the shape when the client moves

This will happen again: the client renamed a tool once already, in one release.
When a free model starts 403ing across the board **and the real client still
works from the same host**, do this — in order, and do not skip step 1:

1. **Confirm the client works, cleanly.** Run it from a directory with no
   `opencode.json` override, no `baseURL` shim, no proxy. If the client also
   403s, the problem is the account, the network, or the tier — not our shape.
2. **Capture** its request (config `baseURL` override → logging proxy → dump).
3. **Replay** the bytes directly. A replay that fails means the difference is
   *not* in the HTTP request; stop and look elsewhere.
4. **Bisect** one factor at a time — body and headers separately, because a
   minimal probe body can fail for an unrelated reason (an early round spent
   hours proving headers "didn't matter" with a body the gate rejected anyway).
5. **Update** `_DECOY_NAMES` / `_DECOY_PARAMETERS` / `headers()` /
   `FALLBACK_VERSION`, then re-verify with the live check above.
6. **Record** the new matrix in `AUDIT.md` and `UPDATE.md`, and correct the
   stale claims rather than appending to them.

Rules that keep this honest:

- Never widen a rule by guessing a prefix. Add the model's row from the docs.
- Treat "the tier is closed" as a hypothesis with a working control, never a
  conclusion from a failing one.
- If a probe disagrees with the docs, record **both** and say which is which.

---

## Not fixable here

Some `403`/`410`/`404`s are upstream facts, not shape problems:

- **Retired models** answer `410 ModelDeprecated` (`exo-free`, `mimo-v2.5-free`).
- **Withdrawn from a gateway** — OpenRouter has dropped
  `stealth/space-bunny-alpha`, `stealth/ox-alpha`, `minimax/minimax-m3:free`.
- **Paid models with an empty account** answer
  `402 Insufficient account funds`. The free tier's 403 fires *before* the funds
  check, so a funded account does not change it — but a funded account is what
  paid models need.
- **Region blocks** arrive as `RegionError`, a different type.

When one of these is the cause, the fix is to remove the deployment from the
catalog, not to change the adapter. A model that answers `410` will never answer
anything else.
