# Fionn / wiwi — End-to-End Bug Audit

**Date:** 2026-08-26
**Baseline:** 532 tests pass, ruff clean. Bugs below exist in paths not covered by the thematic regression suite.

Each finding verified against source by reading the cited lines. Severities: 🔴 critical · 🟠 high · 🟡 medium · ⚪ low.


---

## How to read this register

**Live entries only.** Every closed *round* lives in
[`docs/audit-archive/fixed.md`](docs/audit-archive/fixed.md) — 73 sections,
preserved verbatim with the round and regression test that closed each one.
The most recent rounds stay here instead of moving, carrying their own
`**Status: fixed**` under a section header marked ✅ — a section's badge states
what its *entries* say; the last round to close a section is named in the
`**Section status:**` line under the header, not in a rewritten history.

**Oops-proofing the header rule.** A section header is prose and drifts; the
badges inside it do not. So the grid below is the authority, and the text
under each header restates it. After any fix that empties a section, update the
header line and the `**Section status:**` line in the same edit.

**Status markers are binding** (`scripts/audit_lint.py`, run in CI). Every
`###` entry must carry its own `**Status:**` line, or sit under a section that
declares a `**Section status:**`. That rule exists because #340 found the
register had drifted in both directions: entries marked open that were fixed,
and entries fixed with no marker at all. An entry with no status is now a
build failure, not a judgment call.

**Header badges obey the same rule, checked** (`scripts/audit_lint.py`, 2026-11-09).
A section header may no longer claim `🔴/🟠/🟡/⚪ Open` while every entry under it
is fixed — that is #596, the drift that left eight "Open" headers over closed
sections. Headers now use ✅ Fixed (code done, nothing live) or 🟢 Partial
(something under it is still open); any entry that is not fixed must carry a
`**Status: open` or `**Status: partial` line, so a badge can never lie again.
Old `(new)` / `Addendum` sections predate the rule and are exempt.

**"Unverified" is not "open".** Sections headed `🟠/🟡/🔴 … (new)` or
`Addendum — …` are historical audit records from 2026-08/09. Their entries were
triaged by the rounds that followed, but not every one carries a marker, so the
section says so explicitly. Re-check the source (and the matching
`tests/test_fix_roundN.py`) before acting on one; do not assume it is live.

**Adding a finding:** give it the next number, a severity badge, `file:line`
citations, the trigger, and a one-line fix sketch. Add `**Status: open**` if it
is not fixed in the same change.

---

## Archive index (fixed, newest first)

## ✅ Fixed — the retry loop gave up on a deployment after one credential hit its quota (2026-11-09)

**Section status:** fixed — #597 (round 205).

### 597. A per-key 429 ended the deployment for the whole request, so `num_retries: 0` returned a 429 for a key the pool still had

**Severity:** 🟠 High.
**Status: fixed** — round 205. Test: `tests/test_fix_round205.py`.

`wiwi/router/router.py:1724` (pre-fix) treated **every** `WiwiError` the same
way: the first thing the `except` arm did was

```python
tried_dep_ids.add(id(dep))
```

which excludes the deployment from the rest of this request. That is right for a
`5xx` and for an auth refusal, and it was right for every `429` before AUDIT
#469. #469 changed the accounting so that a `429` **with** a `Retry-After`
cools only the *key* (`ProviderAccount.on_result`, `router.py:289`) and leaves
the deployment healthy — but the retry loop still blacklisted the deployment.
The two fixes therefore cancelled each other out for the most common shape of a
rate limit: an upstream that names a window per credential.

**Trigger** (reproduced end-to-end, two-key pool, one deployment, `m` → `gpt`):

1. `k1` answers `429` with `Retry-After: 5` (quota). The key is cooled, the
   deployment stays `available`.
2. The loop's `fresh` test (`router.py:1796`) asks for a deployment that is
   available **and** not in `tried_dep_ids`. The only one is excluded, so
   `fresh` is `False`.
3. With `num_retries: 0` the range holds exactly one attempt and the loop ends;
   `first_error` is the `429`.

Measured: `429` to the caller, **one** upstream hit, `k2` — which works — never
contacted, and no fallback group configured anywhere can see it. The same request
against a pool whose first pick is `k2` succeeds, so the failure is pure
retry-loop bookkeeping: one spent credential is reported as a spent model.

**Fix:** a `429` that carries a positive `Retry-After` no longer adds the
deployment to `tried_dep_ids` (`router.py:1740-1741`). The key is already cooling,
so the next `pick_key` cannot choose it again — the retry lands on a sibling
credential of the same deployment, which is what the cooldown was for. A `429`
with **no** window stays deployment-scoped (it is #469's availability signal),
and a `5xx` or auth refusal still excludes the deployment, so the existing
fallback and cooldown behaviour is untouched.

**Second half — the attempt count.** Excluding a deployment was also the only
thing making `num_retries: 0` mean "never retry". With the backlist removed, a
second attempt of the *same* deployment on a *different* key is not an extra
try, it is the failover the setting is advertised to provide, so the loop now
always takes at least two attempts (`router.py:1562-1569`,
`max(num_retries + 1, 2)`). The documented meaning of `0` — "try once, never
retry" (`wiwi/config.py`, `_resilience_knobs_must_not_be_negative`) — still
holds for the deployment: no deployment is contacted twice on the same key, and
no sibling *group* is entered. The clamp is about credentials, not deployments.

## ✅ Fixed — implicit state machines: three were real but unenforced (2026-11-09)

**Section status:** fixed — #595 (round 204).

### 595. The delta-stream contract, the key lifecycle and the encoder turn state were implicit — now declared and checkable

**Severity:** 🟡 Medium (no single crash; each is a class of latent defect).
**Status: fixed** — round 204. Tests: `tests/test_fix_round204.py`.

`docs/CORE.md` and `wiwi/streaming/deltas.py` both state the streaming contract
as a binding rule — "adapters guarantee legality; encoders never defend" — and
three separate machines were implemented to it. None of them was *checkable*:

1. **`wiwi/streaming/deltas.py`** declared the ordering contract in prose only.
   A violation (an `ArgsDelta` naming a closed index, a `Finish` before
   `StreamStart`, content after `StreamEnd`) produced a subtly malformed client
   stream and **no log line said so**. `wiwi/core/gateway.py:1569` even carries
   a comment naming one such case "contract violation" and silently repairs it.
   Because nothing could be asserted, the guarantee was trusted for all 13
   provider types rather than tested.
2. **`wiwi/router/router.py` `ProviderKey.status`** — five states
   (`active|cooling|invalid|disabled|probation`) mutated from six call sites.
   Two of those states are both `status == "invalid"` and differ only by
   `cooldown_until` (timed retirement vs terminal dead credential), so reading
   a key's state meant knowing the interaction between four fields. Six sites
   (`server/app.py` ×2, `cline/hermes/workbuddy_auto_refresh.py` ×3) reset the
   key by hand —

   ```python
   key.secret = secret
   key.status = "active"
   key.cooldown_until = 0.0
   ```

   — while `ProviderKey.recover(force=True)` does exactly this **plus** the
   `err_count`/`current_weight` reset that makes the revival survive the next
   non-200. That omission is precisely AUDIT #199, whose fix was applied to the
   admin path only; the five OAuth-grant paths kept the broken form.
3. **The four stream encoders** held the same conceptual state (open block,
   live tool calls, deferred prose, buffered usage) under three different
   private vocabularies — `_open_block`/`_tool_blocks`, `_item_open`/`_tools`,
   `_open_calls`/`_deferred`. ~52 lines of tests reach into those private names,
   so renaming a field broke unrelated assertions.

**Fix sketch:** `wiwi/streaming/machine.py` turns the prose contract into a
`DeltaStreamState` observer (`observe()` never raises; it returns the first
problem) plus `validate_stream()` for tests. It is wired into the live stream
loop in `server/app.py` as `_watch_delta`, which only ever emits a
`stream_contract_violation` warning — output is unchanged, so the observation
cannot break a response. The key lifecycle is declared once as `KEY_STATUSES` +
a transition table in `router.py`, gains `ProviderKey.explain()` (timed and
terminal `invalid` are now distinguishable in one line), and the five OAuth
sites route through `recover(force=True)`. All four encoders gain a uniform
`explain()` built on `streaming.machine.explain_encoder`, giving logs and tests
one stable vocabulary.

Two defects were found in the new validator itself while writing the tests and
are fixed: `validate_stream()` demanded a `StreamEnd` even for a `StreamError`
terminal (reporting every errored stream as truncated), and a repeated
`StreamStart` was reported as "after the stream began" when the stream had in
fact already ended.

- ✅ Fixed — `wiwi/core/` sweep (2026-10-29)
- ✅ Fixed — round 180: the all-time timeseries bucketing (2026-10-29)
- ✅ Fixed — round 179: three latent IR defects found by reading the module against its consumers (2026-10-29)
- ✅ Fixed — round 175: a configured-but-broken Redis response cache failed silently (2026-10-29)
- ✅ Fixed — round 176: tape accounting let logprobs deltas grow the tape unbounded (2026-10-29)
- ✅ Fixed — round 170: four IR carriers the translation was inferring or dropping (2026-10-22)
- ✅ Fixed — log-table responsiveness at 375px, and the transparent overlays (2026-10-18)
- ✅ Fixed — Cline/WorkBuddy OAuth + backup/restore audit (2026-10-14)
- ✅ Fixed — multi-account Hermes + realtime follow-up (2026-10-13)
- ✅ Fixed — round 68: legacy model-id recovery corrupts slash-bearing groups (2026-09-16)
- ✅ Fixed — round 66: rate-limiter RPM refunds and the unbounded sweep (2026-09-16)
- ✅ Fixed — round 65: retroactive pricing keyed on a truncated model id (2026-09-16)
- ✅ Fixed — DB-layer audit (2026-09-15)
- ✅ Fixed — Cline live version fingerprint
- ✅ Fixed — round 46
- ✅ Fixed — round 45
- ✅ Fixed — round 44
- ✅ Fixed — round 42
- ✅ Fixed — round 41
- ✅ Fixed
- ✅ Fixed — round 91 (2026-09-20)
- ✅ Fixed — round 92 (2026-09-21)
- ✅ Fixed — round 93 (2026-09-21)
- ✅ Fixed — round 93 (2026-09-21)
- ✅ Fixed — round 96 (2026-09-24)
- ✅ Fixed — combos editor UI (2026-09-24)
- ✅ Fixed — round 100 (2026-09-25)
- ✅ Fixed — round 97 (2026-09-25)
- ✅ Fixed — round 98 (2026-09-25)
- ✅ Fixed — playground workbench UI (2026-09-25)
- ✅ Fixed — round 101 (2026-09-26)
- ✅ Fixed — round 104 (2026-09-27)
- ✅ Fixed — round 106 (2026-09-28)
- ✅ Fixed — round 105 (2026-09-28)
- ✅ Fixed — round 107 (2026-09-29)
- ✅ Fixed — round 108 (2026-09-29)
- ✅ Fixed — round 109 (2026-09-29)
- ✅ Fixed — round 110 (2026-09-29)
- ✅ Fixed — round 111 (2026-09-29)
- ✅ Fixed — docs page touch targets (2026-09-30)
- ✅ Fixed — round 117 (2026-10-01)
- ✅ Fixed — round 119 (2026-10-05)
- ✅ Fixed — round 122 (2026-10-08) — container deployments
- ✅ Fixed — sweep 2026-10-06 (gateway, streaming, codecs, adapters, router, auth, server)
- ✅ Fixed — round 133 (2026-10-12): tool-call-only streams reported no TPS
- ✅ Fixed — /console/oauth redesign (2026-10-11)
- ✅ Fixed — round 129: whole-gateway backup / restore (2026-10-11)
- ✅ Fixed — round 127/128: Anthropic translation sweep (2026-10-11)
- ✅ Fixed — round 134: Hermes OAuth auto-refresh (2026-10-12)
- ✅ Fixed — round 136: multi-account Hermes (2026-10-13)
- ✅ Fixed — provider delete cascades its deployments (2026-10-14)
- ✅ Fixed — Zen relayed "Client metadata is not supported." for the caller's `metadata` (2026-10-15)
- ✅ Fixed — usage and stats export: the window, the file, and the picker (2026-10-15)
- ✅ Fixed — round 153: the Hermes worker cache (2026-10-15)
- ✅ Fixed — round 154: five `wiwi/wire/` defects (2026-10-16)
- ✅ Fixed — rounds 155/156: the TPS meter, and the tool-call churn behind it (2026-10-16)
- ✅ Fixed — rounds 157: an upstream 529 reached the caller as an opaque `api_error` (2026-10-18)
- ✅ Fixed — round 158: an inbound-surface audit sweep across eight divergences (2026-10-18)
- ✅ Fixed — round 159: WorkBuddy accounts could not be created without an out-of-band auth file (2026-10-18)
- ✅ Fixed — round 160: WorkBuddy refused every Codex / Claude Code request (2026-10-18)
- ✅ Fixed — round 161: the /console/settings Import merge (2026-10-19)
- ✅ Fixed — round 162: the delta / chunk flow (2026-10-19)
- ✅ Fixed — round 163: the replay cursor's unit (2026-10-19)
- ✅ Fixed — round 165: the SSE layer's ingest and replay (2026-10-20)
- ✅ Fixed — round 164: the translation layer's loss points (2026-10-19)
- ✅ Fixed — round 166: the README's copy-paste config did not load (2026-10-20)
- ✅ Fixed — round 167: DeepSeek's thinking-mode round-trip was missing on the OpenCode route (2026-10-21)
- ✅ Fixed — round 168: the /admin/keys routes move to a router + service + schemas (2026-10-21)
- ✅ Fixed — round 169: the /console/settings Import panel (2026-10-22)
- ✅ Fixed — the Playground empty-state scroll fought the user (2026-10-23)
- ✅ Fixed — round 170: the Playground mobile history drawer (2026-10-25)
- ✅ Fixed — round 173: journal reads off the event loop, the Gemini system register, and the aiosqlite warning leaker (2026-10-25)
- ✅ Fixed — round 180: the 2026-10-29 sweep (2026-10-29)

---

## 🟠 Awaiting visual confirmation — the Playground beam backdrop invalidated its composite every frame during scroll (2026-10-24)

**Section status:** fixed on the code side (scroll-gesture freeze + dropped `mix-blend-mode`/`mask-image` from `.pg-workbench-beams` as the #518 escalation); the only open item is the visual confirmation on the reporting display.

### 518. Scrolling a Playground chat flickers/glitches because the fixed animated SVG is blend-composited over the scroll

**Severity:** 🟠 High on software-rendered displays (the flicker is visible; the
empty-chat snap of #517 is related but separate) · **Status: fixed (scroll-
gesture freeze + #518 escalation applied), pending visual confirmation on the reporting display**

**Where:** `web/src/components/HeroBeamBackdrop.tsx` (SMIL `<animate
attributeName="offset">` gradients on every beam stroke) rendered by
`web/src/pages/Playground.tsx:1415` inside `.pg-atmosphere`, and the layering in
`web/src/styles.css:3547` — `.pg-workbench-beams` is `position: fixed` with
`mix-blend-mode: screen` **and** a `mask-image: linear-gradient(...)`, sitting
directly under `.pg-message-scroll` (the scrolling chat).

**Trigger.** Scroll any Playground chat — empty launcher or populated
conversation — on a display where Chromium raster-s composites (no GPU path):
this VM's X11 desktop, the dashboard Computer-tab view, or any machine without GPU accelerated
compositing. Every scroll frame re-rasterises the scrolled layer,
which forces Chromium to re-run the `screen` blend against the *entire* fixed
SVG; simultaneously the beams' SMIL gradient offsets invalidate that same fixed
layer once per frame. The scroll content and the animated backdrop re-blend
against each other in the same frames → transient partial-tile artefacts read
as flicker/glitch.

**Evidence (live Chrome 156 in this VM's headed desktop, new-chat empty state):**
- The scroll *logic* is clean: `scrollTop = 180` holds over 1 s, rAF timing
  during 6 s of trusted CDP `mouse.wheel` scrolling stays at a rock-solid
  `16.8 ms/frame` with zero frames above 40 ms (355 sampled, `-jank 0`), body is
  overflow-free, and only one scrollable element exists. So the repaint defect
  is in the compositing interplay, not the JS.
- The only continuously animating fixed element under the scroll container is
  the beam SVG; pausing its SMIL clock removes the per-frame invalidation.

**Fix.** During an active scroll gesture, freeze the SVG's SMIL animation clock
(`svg.pauseAnimations()`) and resume it (`unpauseAnimations()`) 250 ms after the
gesture goes idle — implemented around `handleScroll` in `Playground.tsx` via an
`atmosphereRef` wrapper around `.pg-atmosphere`. Zero visual change while idle;
the beam animation resumes where it left off after scroll. `prefers-reduced-
motion` still hides the beams entirely (existing CSS). If the flicker persists
on the reporting screen after this, the next escalation is dropping
`mix-blend-mode: screen`/`mask-image` from `.pg-workbench-beams` (a cheaper
layer overall), which is deliberately *not* bundled now — one mechanism at a
time.

---

## ✅ Fixed — aiosqlite workers outlived their event loop, and pytest blamed an innocent test (2026-10-17)

**Section status:** fixed — #475 (round 173).

### 475. Undisposed test-side `aiosqlite` connections raise `RuntimeError: Event loop is closed` on a worker thread, mis-attributed to the *next* test

**Severity:** ⚪ Low (test hygiene — the default gate stays green and nothing in production is affected, but the warning class cannot be escalated and its reported nodeid is not the culprit) · **Status: fixed — round 173**: the leaker was `tests/test_fix_round59.py` (15 `_engine()` calls, never disposed; localized by running the suite with `-W error::pytest.PytestUnhandledThreadExceptionWarning` — the errors land on round 59's own cache tests). Its engines now come from a tracked `engine` fixture that disposes in `finally`, and the escalated run over the affected files is clean. **Files:** `tests/test_fix_round59.py`
**Files:** the leaker is unnamed (see below); the production shutdown path is *not* it — `wiwi/server/app.py:1041` creates exactly one async engine, `:1085` hands the same object to `DBSink` (`wiwi/logging_core/db_sink.py:412` stores it as `self.engine`), and `:1252` disposes it, so the sole engine is disposed on shutdown.
**Trigger:** any full `python3 -m pytest tests/ -q` run. Result on 2026-10-17: `3383 passed, 6 skipped, 7 warnings in 217.64s`. Several of those warnings are `PytestUnhandledThreadExceptionWarning: Exception in thread Thread-NNN (_connection_worker_thread)`, each a stack of `aiosqlite/core.py:75 future.get_loop().call_soon_threadsafe(...)` → `RuntimeError: Event loop is closed`; at least four distinct thread ids appear (`Thread-464`, `-466`, `-1188`, `-1189`). An `aiosqlite` connection that is never closed outlives the loop it was bound to, and its worker thread then tries to publish a result onto a loop that is already closed. (The run's other warnings are unrelated deprecations — Starlette's `httpx`-based `TestClient` at `tests/test_fix_round139.py:49`, and SQLAlchemy's `StaticPool`-from-`mode=memory` selection at `wiwi/server/app.py:1041` — so read the 7 as a mix, and count the thread exceptions by their `_connection_worker_thread` frames rather than by the total.)
**Why the reported nodeid is useless:** `_pytest/threadexception.py:147` collects pending thread exceptions inside `pytest_runtest_call`, i.e. at the *start* of a test's call phase, so whatever thread died during the previous teardown is charged to the next test. Escalating with `-W error::pytest.PytestUnhandledThreadExceptionWarning` proves it: a stop-at-first run (`-x`) charged the very first occurrence to `tests/test_fix_round126.py::test_an_unknown_provider_type_is_rejected_by_the_registry` — a sync registry test that opens no database — while a full escalation run (`1 failed, 3381 passed, 6 skipped, 2 warnings, 1 error`) charged `tests/test_fix_round59.py::test_query_cache_keeps_live_entries` (FAILED) and `tests/test_fix_round126.py::test_forget_owner_drops_a_dropped_journals_intent` (ERROR), the latter a pure `JournalStore` test with no DB either. Three runs, three different nodeids, all innocent.
**Consequence:** `-W error::pytest.PytestUnhandledThreadExceptionWarning` cannot be used as a strict gate or a warning-free baseline until this is fixed — it turns a green suite red at an unrelated test, which reads as a regression in whatever change happens to be in flight. Distinct from #463/#464, which are real failures (an hour-boundary bucket split and the realtime connection race).
**Fix sketch:** name the leaker, then dispose it. Every `create_async_engine` in a test must be paired with `await engine.dispose()` (or the app must be driven through `LifespanManager` so `AppState.shutdown` runs) — pytest's own hint (`--tracemalloc`) or bisecting a file at a time with `-W error::…` will localise it. Keep a session-end assertion that no `aiosqlite` worker thread survives, so the class cannot come back silently.


---

## ✅ Fixed — a shared anonymous Zen key turned one model's 429 into a whole-tier 503 (2026-10-15)

**Section status:** fixed — #469 (round 180).

### 469. One upstream 429 cools the `anonymous` key and retires every free Zen deployment

**Severity:** 🟡 Medium (a single flawed upstream model becomes a whole-tier outage for the length of the key's cooldown) · **Status: fixed** — round 180 (`tests/test_fix_round180.py`)
**Files:** `wiwi/router/router.py` — `if status == 429: key.mark_cooling(retry_after if retry_after and retry_after > 0 else 30.0)` (standard mode, ~line 263); `wiwi/providers/base.py:67-68` — `status_for_key_pool()` reports 429 for any 429; `wiwi/providers/opencode_adapter.py` — the `anonymous` sentinel is one credential shared by every free deployment.
**Trigger:** any free Zen model whose upstream answers 429 while the pool declares a single `anonymous` key entry — the shape `wiwi.yaml.example` recommends. Measured live 2026-10-15 through `create_app` (one key): `step-5-preview-free` answered `429 … Upstream request failed: Endpoint is unavailable.` and the **next 16 consecutive requests** — nine other free models across all three inbound dialects — answered `503 no healthy deployment for '<group>'`. A second run lost 25 the same way after `cat-jam-free` 429'd. Declaring two `anonymous` entries cut the same blip to three requests. The sentinel is not an account, so cooling "the key" retires the single path every free deployment shares, and this 429 is an upstream-availability blip rather than a quota signal about the credential.
**Fix sketch:** decide the cooldown's scope from the error, not the status: a 429 carrying no `Retry-After` and no quota wording should cool the *deployment* (as `record_fail` already does for 5xx) and leave the key in rotation — or let an adapter declare a credential non-coolable when it is a shared anonymous marker. Regression to write: two deployments on one sentinel key, one answers 429, the other must still serve without a cold window.
**Repro + workaround:** recorded in `docs/OPENCODE.md` (the free-tier section's "one 429 can read as a whole-tier outage" paragraph). No code change yet.

---

## ✅ Fixed — full-suite flakes unrelated to any pending change (2026-10-14)

**Section status:** fixed — #463 (anchored bucket), #464 (round 156, with #414).

Both were observed failing during one full-suite run and confirmed to pass in
isolation and on the pristine committed baseline (3108 passed). Neither is
caused by the working tree; both are recorded so the next agent does not
mistake a red gate for their own regression.

### 463. `test_repeated_sweeps_merge_instead_of_overwrite` splits its bucket when the two sweeps straddle an hour boundary

**Severity:** 🟡 Medium (hourly gate flake) · **Status: fixed** — the test now
anchors its timestamps inside one hour bucket (re-verified 2026-10-19)
**File:** `tests/test_hist_percentiles.py` (`test_repeated_sweeps_merge_instead_of_overwrite`)
**Trigger:** full-suite run that happens to cross an hour boundary between the
two `rollup_and_prune` calls. Both sweeps write `now - i` for `i in range(50)`,
and the rollup bucket is an hour index, so the `assert len(rows) == 1` fails
roughly once per hour (~1.4% of runs). Reproduced once on 2026-10-14; passes
13/13 in isolation.
**Fix:** derive the timestamps from a frozen `now` captured before the first
write (or subtract a fixed span instead of a per-index second), so the two
sweeps are always in the same bucket regardless of when the test runs.

**Fix applied (2026-10-19).** The timestamps are now anchored 1800 seconds
into the current hour, so the whole 50-event run (1800 down to 1750) lands in
one bucket whatever the wall clock says:

```python
now = (int(time.time() // 3600) * 3600) + 1800
```

Verified against the boundary case the old scheme failed on — with `now` 20
seconds past an hour edge, the old timestamps produced **two** buckets and the
anchored ones produce exactly **one**.

### 464. `test_realtime` opens two relay connections under full-suite ordering — see #414

Already recorded as #414 (reproduced 2 of 2 full runs at that time). Re-observed
2026-10-14: `tests/test_realtime.py::test_a_relayed_frame_arrives_upstream_byte_identical`
failed in 1 of 2 identical runs of a 4-file subset and passed 13/13 in
isolation. Not re-counted as a new finding; #414 stands as the entry to fix.

Re-observed 2026-10-15 (full-suite run, `connections == 2`), with one new detail:
the test also fails when run as the **single node id** — 2 of 2 runs — while
`tests/test_realtime.py` as a whole passes 13/13 twice. That points away from
suite ordering and at the assertion itself: this test opens two connections to
the fake server by construction (`tests/test_realtime.py:198` connects the client
directly, `_run_relay` at `:217` opens the relay's own upstream socket), and
`FakeRealtimeServer.connections` only increments once the server's handler
coroutine is scheduled (`:44`). `assert upstream.connections == 1` therefore
races the fake server's scheduling rather than testing relay behaviour.

**Status: fixed** — round 156, with #414. The diagnosis above was right and the
fix was the assertion: the test now waits for the relay's connection to be
accepted and requires 2. 6/6 passes where it previously failed about half the
time in isolation.

---

## ✅ Fixed — Hermes integration sweep (2026-10-12)

**Section status:** fixed — #424 (round 180), #425/#426 (round 134 follow-up).

Found by reading the whole Hermes surface end to end (`hermes_adapter.py`,
`hermes_oauth.py`, the `/admin/hermes/oauth/*` routes, the connect/refresh/
disconnect handlers, and the catalog/registry wiring) and comparing each against
how Cline and WorkBuddy handle the same shape. Registry dispatch, the OpenAI
wire reuse, the adapter's `hermes:`-prefix stripping, the device-code connect
flow, and the round-134 auto-refresh were all verified correct; the entries
below are what the sweep found still wrong. Entries #424-#426.

### 424. Hermes OAuth sessions reuse the Cline pending map and share its eviction, so a Cline sweep can drop a live Hermes login

**Severity:** 🟠 Medium · **Status: fixed** — round 180 (`tests/test_fix_round180.py`)
**Files:** `wiwi/server/app.py` (`AppState.oauth_pending` + `evict_expired_pending`), the three connect flows

The Hermes device-code session used to be stashed in `state.cline_pending[token]` with
`kind: "hermes_device_code"`, and the eviction sweep iterates every entry in
that map with a single `created_at > 600s` rule. Functionally correct today (the
10-minute window matches Nous's `expires_in`, and the `kind` discriminator keeps
the poll from cross-using a Cline session), but it couples two unrelated OAuth
providers to one dict whose comment still says "Pending Cline OAuth sessions."
The next provider added will land here too, and any Cline-specific change to the
TTL or eviction silently changes Hermes's. This is the "no second convention
beside an existing one" rule applied in reverse — the shared map is the
*Cline* convention, and Hermes is piggybacking on it.

**Fix sketch:** give Hermes its own `state.hermes_pending` dict (or a generic
`state.oauth_pending: dict[str, dict]` keyed by a `kind` field that every OAuth
provider uses), and have the sweep iterate it generically. Low urgency; a
latent-coupling bug, not a live failure.

### 425. A `poll`/`refresh` that returns no `expires_in` writes `expires_at = now`, so the round-134 sweeper immediately sees the token as due and re-refreshes on the next tick

**Severity:** 🟡 Medium · **Status: fixed** — round 134 follow-up (`tests/test_fix_round134.py`; fixed 2026-10-12)
**Files:** `wiwi/providers/hermes_oauth.py` (`compute_expires_at`), `wiwi/server/app.py` (poll + manual refresh), `wiwi/providers/hermes_auto_refresh.py:_do_refresh`

All three write `expires_at = time.time() + <expires_in>` with `0` as the
default. Nous always returns `expires_in` today, but if it ever omits it (or
returns `0`), `expires_at` lands in the past, and `expires_within_lead(past,
now)` is `True` — so the round-134 sweeper treats the token as permanently due
and re-refreshes it every 60 s, burning the single-use refresh grant until
`invalid_grant` marks the provider dead.

Fix: a shared `hermes_oauth.compute_expires_at(expires_in, now)` resolves a
missing / zero / non-numeric / bool `expires_in` to a forward window strictly
beyond the lead (`2 * REFRESH_LEAD_S`), so the next `expires_within_lead` read
is `False`. A usable value is honoured verbatim. All three sites call it.

A second defect found while testing this one and fixed in the same pass: on a
YAML-defined provider, `_do_refresh` called `_update_secret` (which raises
`ConfigStoreNotFound` — no DB key row) *before* `set_setting`, so the exception
aborted the rotation before the fresh `expires_at` was ever persisted; the
record stayed in the past and the sweeper spun anyway. The order is now
persist-record-first, then rotate the pool key, with the `set_setting` failure
contained and logged rather than aborting the live-token update.

### 426. `hermes_oauth_refresh` and `poll` call the synchronous `refresh_access_token`/`poll_token` on the event loop, stalling every concurrent request for the portal round-trip

**Severity:** 🟡 Medium · **Status: fixed** — round 134 follow-up (`tests/test_fix_round134.py`; fixed 2026-10-12)
**Files:** `wiwi/server/app.py:6013` (poll), `:6058` (refresh)

The admin endpoints called `hermes_oauth`'s functions directly, and those use a
synchronous `httpx.Client` (they are shared with the CLI-facing connect flow).
The round-134 auto-refresh runs them via `asyncio.to_thread`, but these two
handlers did not. A `POST /admin/hermes/oauth/refresh` blocked the uvicorn event
loop for the full Nous round-trip (up to `_TIMEOUT_S = 30s`), stalling every
in-flight completion and stream on the same worker.

Fix: both calls are wrapped in `await asyncio.to_thread(...)`, matching
`hermes_auto_refresh._do_refresh`. Covered by the existing round-134 admin/flow
tests, which now exercise the threaded path.

---

## ✅ Fixed — sweep 2026-10-11 (upstream SSE reading path)

**Section status:** fixed — #419 (float case 2026-10-19, typed-wrong half round 186), #420 (round 162).

Found by executing the real `LineSSEParser` + `Gateway.stream` pump against
malformed-but-plausible upstream wire data (Anthropic inbound `/v1/messages`,
mocked Anthropic/OpenAI upstreams). Entries #419-#420. The parsing layer itself
held up under chunk-splitting, CRLF, multi-line `data:`, non-dict JSON and
600 KB frames — both findings below are in what the pump does with a frame that
*parsed* cleanly.

### 419. A float `content_block_delta.index` is coerced to `0`, silently dropping or misrouting a tool-call args fragment

**Severity:** 🟠 high · **Status: fixed** — float case 2026-10-19; the
typed-wrong half closed in round 186 (`tests/test_fix_round186.py`)

The float case is normalized, not coerced: `_block_index` returns `int(idx)`
for a numerically whole float, so `1.0` reaches the SAME block as `1` instead
of being rewritten to `0` (a different tool call's index).
**Files:** `wiwi/providers/anthropic_adapter.py:825-876` (`_block_index`),
`:1043-1062` (`input_json_delta` arm), `:968-1000` (`content_block_start`
tool arms), `:1063-1081` (`content_block_stop`), landing in
`wiwi/wire/anthropic_messages.py:1042-1049` (`_tool_blocks.get(d.index)`)

**Round 186 closed the remaining half.** A typed-wrong value (list/dict/str/
bool/non-integral float) used to fall back to `0` *and keep the frame*; when a
call was live on block 0 that appended the fragment to a **different** call's
arguments, and the stream still terminated cleanly, so nothing downstream could
detect the corruption (the gateway folds by index; every wire encoder trusts
the decoder's Open -> ArgsDelta* -> Close contract). `_block_index` now returns
`None` for an unrecoverable index, `input_json_delta` also gates on
`idx in self._tool_indices`, and the start/result arms drop the frame. A
`content_block_stop` with an unreadable index still closes the single open call
when there is exactly one (the only block it can refer to) and is otherwise
dropped, so a tool block is never orphaned by a malformed stop.
**Fix:** reject rather than coerce — in the `input_json_delta` arm, gate on
`idx in self._tool_indices` exactly as the `content_block_stop` arm does, and
have `_block_index` return `None` (frame dropped) instead of `0` for a
typed-wrong index.

**Why not #384/#416:** those were *crashes* (`TypeError` escaping the decoder) and
are fixed. The `_block_index` guard added in `3adf761` **converted the crash into
silent corruption**: a non-int index no longer raises, it is quietly rewritten to
`0`, which is a *different tool call's* index. Same for `openai_adapter.py:615`
and `nim_adapter.py:427` (`idx = i`).

```python
706:  idx = payload.get("index", 0)
707:  if not isinstance(idx, int) or isinstance(idx, bool):
708:      return 0          # <- lossy: 1.0 becomes block 0
...
821:  index=self._block_index(payload),   # no `idx in self._tool_indices` gate
```

JSON has no int/float distinction and `json.dumps(1.0)` emits `1.0`, so a
re-serializing proxy or any upstream that computes the index as a float hits
this. Note `hash(1) == hash(1.0)` is `True`, so a set/dict membership test alone
would *not* catch it — the value must be rejected by type before it is used as a key.

**Probe** (executed, `fresh_adapter("anthropic")`, block 1 opened, delta with `"index": 1.0`):

```
index=1 opened; delta with index=1.0 -> [ToolCallArgsDelta(index=0, args_fragment='X')]
tool_indices={1}
```

**Consequence, executed end-to-end** (real app, `POST /v1/messages`, respx upstream,
raw wire frames dumped). HTTP **200**, a clean `message_stop`, and **no error frame
at all** in either shape:

*Single tool at upstream index 1, no block 0 — fragment silently DROPPED:*
```
CONTROL  index=1   -> wire block 0 name='beta' args='{"city":"SF"}'   VALID
TEST     index=1.0 -> wire block 0 name='beta' args='{"city":}'       *** INVALID JSON ***
```
The client is handed a truncated `input_json_delta` run and a normally-terminated
stream, so it executes the tool with unparseable arguments.

*Two tools at index 0 and 1 — fragment MISROUTED into the other tool call:*
```
CONTROL  index=1   -> alpha='{"who":"a"}'          beta='{"x":"LEAK"1}'  (attributed to beta)
TEST     index=1.0 -> alpha='{"who":"a"}"LEAK"'   beta='{"x":1}'        (fragment went to alpha)
```
The raw wire dump shows `content_block_delta` for block **0** emitted *after* block 1
was opened — the strictly-sequential block violation the comment at
`anthropic_messages.py:925-933` says Claude Code rejects outright. Both tool
argument streams are wrong.

The OpenAI-wire family is louder: `openai_adapter.py:615` emits
`ToolCallClose(index=0)` followed by a second `ToolCallOpen(index=0)`, splitting
one provider tool call into two blocks.

---

### 420. `[DONE]` on an `anthropic`-typed upstream is dropped, producing a mid-stream `error` frame and cooling a healthy deployment

**Severity:** 🟠 high · **Status: fixed** — round 162 (`tests/test_fix_round162.py`)

The Anthropic decoder now has the terminator arm its OpenAI-wire siblings
already had: on `data == "[DONE]"` it flushes the still-open tool calls
(`ToolCallClose` per index), emits `Finish("tool_call")` when a call was
delivered, and terminates with `StreamEnd`. The body now ends cleanly instead
of being mistaken for a truncation.
**Files:** `wiwi/providers/anthropic_adapter.py:711-715` (`orjson.JSONDecodeError ->
return []` swallows the sentinel), consumed by `wiwi/core/gateway.py:2083-2093`
(`if finish is None and not saw_terminal:` → `_fail_stream(..., price=False)`) and
`:2245-2247` (`health_status == 502` → `dep.record_fail`)
**Fix:** give the Anthropic decoder the terminator arm its OpenAI-wire siblings
already have (`openai_adapter.py:514`, `nim_adapter.py:302`, `openrouter_adapter.py:320`,
`opencode_adapter.py:514`) — on `data == "[DONE]"` flush open tool state and emit
`Finish("stop") + StreamEnd` — so a `[DONE]`-terminated body ends cleanly instead of
being mistaken for a truncation.

**Why not #76:** #76 is the identical consequence shape (decoder returns `[]` for
the terminal frame → `StreamError("upstream stream ended without completion")` plus
`record_fail`) but for a **Gemini** candidate missing `finishReason`. This is the
Anthropic decoder and a different frame class. `anthropic_adapter.py` has **no**
`data == "[DONE]"` arm at all — grep confirms 0 occurrences, versus 1 each in the
four OpenAI-wire decoders.

**Probe** (executed, real app + respx, `message_start` + a text block + `"hi there"`,
then the tail varies):

```
T1. normal: message_delta + message_stop -> error frames: 0
T2. [DONE] INSTEAD of message_stop       -> error frames: 1
     data: {"type":"error","error":{"type":"api_error",
          "message":"upstream stream ended without completion"}}
T3. message_delta only (no message_stop) -> error frames: 0   (message_delta carries the finish)
T4. [DONE] appended AFTER a proper tail  -> error frames: 0
```

T2 is a byte-for-byte match for what Claude Code renders as **"API Error: Server
error mid-response. The response above may be incomplete."** — an Anthropic `error`
frame arriving *after* content was already delivered.

**Trigger:** a provider entry typed `provider: anthropic` whose upstream emits
OpenAI-shaped SSE — the common self-hosted-relay case (newapi/one-api/LiteLLM-style
proxies that normalise every upstream to OpenAI shape, and append `[DONE]`
universally). This is a config-shape mismatch, not a hypothetical.

**Consequence, executed with a spy on `Deployment.record_fail`:**
```
CONTROL  proper anthropic tail : client_saw_error=False  record_fail calls=[]
TEST     [DONE] instead of tail: client_saw_error=True   record_fail calls=[('claude-sonnet-4-5', 1)]
```
Every such response costs the deployment a fail; past `allowed_fails` the
deployment is cooled and a perfectly healthy credential is retired, so a
misconfigured relay produces a self-sustaining outage rather than one bad turn.

---

## ✅ Fixed — sweep 2026-10-09 (config, adapters)

**Section status:** fixed — #382-#386 (rounds 128, 162, 180; #385 re-verified 2026-10-13).

New findings from a fresh eight-subsystem sweep. Every one below was confirmed by
**executing code** against the real adapters/`load_config_from_string`, not by reading
alone. Findings #382-#386.

### 382. `router_settings` retry/failover knobs have no positivity validator; `num_retries: -1` makes every request a 503

**Severity:** 🔴 critical (single-typo total outage) · **Status: fixed** — round 162 (`tests/test_fix_round162.py`)

The validator now rejects a NEGATIVE value for all five knobs. **Zero is not
rejected**, and that distinction is load-bearing: these knobs are COUNTS of
attempts and failures, not LIMITS. `num_retries: 0` means "try once, never
retry" (the loop's `range(0 + 1)` is exactly one attempt) and is the common
test configuration; `stream_loop_limit: 0` disables loop detection by the
detector's own documented contract. The sibling validators on
`DeploymentParams`/`KeyDef` reject zero because those knobs are LIMITS, where
zero silently means "no cap" (AUDIT #101) — the opposite rule.
**Files:** `wiwi/config.py:236-243` (`RouterSettings`, no `field_validator`), consumed at
`wiwi/router/router.py:1384` (`for attempt in range(router.settings.num_retries + 1)`)
**Fix:** a `_positive`-style validator on `num_retries`, `allowed_fails`,
`key_max_consecutive_fails`, `stream_resume_max_retries`, `stream_loop_limit`, mirroring
`config.py:185-197` (`_limits_must_be_positive`) and `:198-212` (`_weight_positive`).

`DeploymentParams` validates `weight`, `rpm`, `tpm`, `max_inflight` positive
(`config.py:185-197`), and `KeyDef.weight` too (`:198-212`) — but the `RouterSettings`
resilience knobs that feed the *same* retry/cooldown control loop have no validator at
all. This config loads cleanly (verified with `load_config_from_string`):

```
ACCEPTED: num_retries=-5 allowed_fails=0 key_max_fails=-1 cycle=-3 resume=-2 loop_limit=0
range(num_retries+1) = []
```

**Probe** (executed): the config above parses with no error; `list(range(num_retries+1))`
is `[]`.

**Consequence chain, per knob:**
- `num_retries: -1` → the retry loop at `router.py:1384` iterates **zero** times, so no
  request is ever attempted and every request falls through to
  `raise first_error or WiwiError(503, ...)` (`router.py:1626`). One typo turns the
  gateway into a permanent 503 for every model. This is strictly worse than the `rpm: 0`
  case the code explicitly guards against (`config.py:190-193`, "a zero slipped through as
  an unlimited deployment"): zero *attempts* is a total outage, not a silent no-op.
- `allowed_fails: 0` → `if len(recent) >= allowed_fails` (`router.py:608`) is true on the
  *first* failure, so every deployment cools on a single transient 500.
- `key_max_consecutive_fails: -1` → any key with ≥1 failure is retired on the first error.
- `stream_loop_limit: 0` → loop detection fires on chunk zero.
- `stream_resume_max_retries: -2` → mid-stream resume never runs.

`cycle_every_n` and `cooldown_time` happen to be defended at their use sites
(`max(0, ...)` at `router.py:1348`, `max(cooldown_time, 1.0)` at `router.py:605`) — the
exact pattern that makes the missing validation easy to miss: two siblings are defended
at the call site, five are not defended anywhere.

**Why not a known finding:** AUDIT #306 is `KeyDef`/`DeploymentParams` `weight`; #101 is
deployment `rpm`. Neither covers `RouterSettings`.

### 383. Healer settings that gate real provider spend are unvalidated; `tick_s: 0` makes the probe loop a busy-spin

**Severity:** 🟠 High (unbounded real-money probe spend) · **Status: fixed** — round 180 (`tests/test_fix_round180.py`)
**Files:** `wiwi/config.py:408-436` (`HealerSettings` — only `probation_weight` is
validated), consumers `wiwi/core/recovery.py:330-342`, `:356`, `:392`, `:456`

`HealerSettings` has exactly one validator, on `probation_weight` (`config.py:429-436`),
with a comment arguing for clamping because "at `0` the key … can never be picked". Every
field that controls **how much money the healer spends** is unvalidated. Verified — all
accepted with no error:

```
tick_s=0.0 max_probes=1000000 min_interval=0.0 backoff_base=0.0 probes_to_restore=0
```

**Consequences:**
- `tick_s: 0` → `await asyncio.wait_for(self._stop.wait(), timeout=self._s.tick_s)`
  (`recovery.py:339-340`) returns immediately every iteration, so the sweep becomes a
  **tight busy-loop** that saturates a core and hammers every cooling deployment with
  1-token completions (real provider money). The initial `asyncio.sleep(self._s.tick_s)`
  (`recovery.py:332`) also becomes a no-op, so probes start the instant the process boots.
- `max_probes_per_sweep: 1000000` removes the documented per-tick blast-radius cap.
- `min_probe_interval_s: 0` + `probe_backoff_base_s: 0` → `CircuitBreaker.trip`'s
  `window = min(cap, base * 2 ** (streak-1))` = 0 (`recovery.py:78`) and `ok()`'s interval
  check (`recovery.py:392`) always passes, so both independent circuit brakes are disabled
  and every target is re-probed every tick.
- `probes_to_restore: 0` → `if dst.streak >= self._s.probes_to_restore`
  (`recovery.py:456`) is true after the first healthy probe, restoring an intermittently-
  401'd key into rotation on a single lucky probe — the premature-restore the streak
  exists to prevent.

`healer.enabled` is off by default, which protects a default deployment but not an
operator who turns it on and typos `tick_s`.

**Why not a known finding:** #238 covers only `probation_weight`; #97 covers premature
restore via the `CREDS_VALID_MODEL_BAD` verdict, not a zero `probes_to_restore`.

### 384. Anthropic streaming decoder never guards `content_block_*.index`; unhashable value crashes the pump

**Severity:** 🟠 High · **Status: fixed** — round 128 (`tests/test_fix_round128.py`; found
2026-10-10, fixed 2026-10-11). The four cited reads now route through a new
`AnthropicAdapter._block_index(payload)` helper carrying the same rule as the
openai/nim/openrouter decoders, including rejecting `bool` (`isinstance(True, int)`
is True, so `True` would collide with `1` as a set member). See #416 for the
second instance of the same class, at a site this entry did not cover.
**Files:** `wiwi/providers/anthropic_adapter.py:729, 741, 789, 794-795`
**Fix:** lift the OpenAI-base/OpenRouter guard verbatim — `if not isinstance(idx, int) or
isinstance(idx, bool): idx = 0` — before `self._tool_indices.add(idx)` at `:741` and the
read at `:789`.

```python
729:  idx = payload.get("index", 0)
...
741:  self._tool_indices.add(idx)
...
789:  index=payload.get("index", 0),
```

`payload.get("index", 0)` defaults only a *missing* key. An Anthropic frame whose `index`
is a list/dict (a re-serializing proxy, or a malformed upstream) reaches
`self._tool_indices.add(idx)` at `:741` and raises `TypeError: unhashable type`.

**Probe** (executed, `fresh_adapter("anthropic")`, `decode_stream_event("content_block_start", …)`):

```
start index=list -> TypeError: unhashable type: 'list'
start index=dict -> TypeError: unhashable type: 'dict'
start index="0"  -> OK, _tool_indices={'0'}
start index=null -> OK, _tool_indices={None}
```

**Consequence:** the `TypeError` escapes `decode_stream_event` into the pump's mid-stream
handler, which converts it to a `StreamError`, bills the partial, fires
`dep.record_fail`, and moves a healthy key to `cooling` — a frame with zero semantics
penalizes a healthy credential and cools a healthy deployment.

**Why not a known finding:** this is the exact class #366 fixed in `openai_adapter.py` and
`nim_adapter.py` (both now carry `if not isinstance(idx, int) or isinstance(idx, bool):
idx = i`), and the OpenRouter twin inherited the base guard. Anthropic is now the sole
unguarded streaming decoder. #153's crash sweep of this adapter added dict guards at
`:715, :727, :774, :800` but not `index`.

**Disproved sub-claim (recorded so it is not re-filed):** a `null` index does **not**
split one call in two. `content_block_start` stores `None`, `content_block_stop` reads
`payload.get("index", 0)` → `0`, but the `in` test at `:795` is `0 in {None}` → False…
**yet the executed probe shows `ToolCallClose` IS still emitted** (the `None` default and
the stored `None` agree when the key is present-but-null on both frames). Verified:
`start(index=null)` then `stop(index=null)` → `['ToolCallClose']`. The split only occurs
on a *missing* key at open vs present-`null` at close, which no observed provider emits.
Only the unhashable crash is a live defect.

### 385. `openai_adapter` / `nim_adapter` do not coerce a JSON `null` tool-call `name`, emitting `ToolCallOpen(name="")`

**Severity:** 🟡 Medium · **Status: fixed** — re-verified 2026-10-13 (sweep).
Both adapters now read the name through the existing `as_str` boundary guard
(`openai_adapter.py:~622`, `nim_adapter.py:~435`), whose comments cite this
entry: a JSON `null` / list / dict name collapses or coerces instead of
poisoning `_tool_names` or emitting a non-str `ToolCallOpen.name`. Body
preserved unchanged.
**Files:** `wiwi/providers/openai_adapter.py:612`, `wiwi/providers/nim_adapter.py:430`
**Fix:** `name_fragment = as_str(fn.get("name"))` — exactly the OpenRouter sibling
(`openrouter_adapter.py:450`).

```python
612:  name_fragment = fn.get("name", "")     # openai + nvidia-nim
450:  name_fragment = as_str(fn.get("name")) # openrouter — guarded
```

`fn.get("name", "")` defaults only a *missing* key, so an explicit `null` name flows
through as `None`, then into `self._tool_names[idx] = name_fragment or ""` and the
deferred open `self._pending_opens[idx] = (tc["id"], "")` → `ToolCallOpen(name="")`. The
client receives a `tool_use` block with an empty name it cannot dispatch.

**Probe** (executed, a single `finish_reason` chunk carrying one tool call):

```
openai     name=null -> [('ToolCallOpen', ''), ('ToolCallArgsDelta', None), ('ToolCallClose', None), ('Finish', None)]
nvidia-nim name=null -> [('ToolCallOpen', ''), ...]
openai     name=str  -> [('ToolCallOpen', 'get_weather'), ...]
```

**Why not a known finding:** #232 fixed exactly this shape for Gemini and Anthropic
(`as_str` guards) and states "and 10 similar `.get(k, "")` sites across the adapters", but
the OpenRouter `as_str` fix was the only one applied to the OpenAI family. The divergence
is now direct: OpenRouter guards, its two siblings do not.

### 386. `gemini_adapter.py:470` re-implements the dict gate as `(cand.get("content") or {}).get("parts")`, which a truthy non-dict turns into an `AttributeError`

**Severity:** 🟡 Medium · **Status: fixed** — round 162 (`tests/test_fix_round162.py`)

The gate now reads the field through `as_dict` (the same helper the sync twin
and the sibling `usageMetadata` arm already use), so a truthy non-dict yields
`{}` instead of raising. A corrupt frame is additionally dropped WHOLE, so it
cannot terminate the stream on the usage-without-finishReason arm.
**Files:** `wiwi/providers/gemini_adapter.py:470`
**Fix:** `not as_dict(cand.get("content")).get("parts")` — the sync twin at `:302`
already uses `as_dict(cand.get("content"))`, and `:419` uses `if not isinstance(u, dict):
u = None` for the sibling `usageMetadata` in the same method.

```python
470:  elif u and not self._saw_tail and not (cand.get("content") or {}).get("parts"):
```

The `or {}` default fires only on a *falsy* `content`. A truthy non-dict (`5`, `true`,
`"x"`, `[]`) passes straight into `.get("parts")`.

**Probe** (executed, `fresh_adapter("gemini")`):

```
content=int   -> AttributeError: 'int' object has no attribute 'get'
content=bool  -> AttributeError: 'bool' object has no attribute 'get'
content=str   -> AttributeError: 'str' object has no attribute 'get'
content=list  -> OK      (falsy-when-empty, hits the `or {}` default)
content=null  -> OK      (falsy, hits the default)
content=dict  -> OK, deltas=['StreamStart','TextDelta']   (control)
```

**Consequence:** escapes `decode_stream_event` → pump cooldown + key penalty on a frame
carrying no semantics.

**Why not a known finding:** #247 established `as_dict` for nested reads and the same
file already applies it correctly one line above (`:419`, citing #154 for "a truthy
non-dict `usageMetadata` must not reach `u.get`"); `:302` is the sync twin. #194/#195
covered usage counters, not this container gate.

---

## ✅ Fixed — sweep 2026-10-10 (ir)

**Section status:** fixed — #387, #388 (round 124).

Two findings from a sweep of `wiwi/ir/` (`types.py`, `translation.py`, `builtin_tools.py`),
both confirmed by **executing code** against the real adapters rather than reading alone.
Findings #387-#388.

### 387. `is_builtin_name` is not total — a non-str `ToolUsePart.name` raises `TypeError` on replay

**Severity:** 🟠 High · **Status: fixed** — round 124
(`tests/test_fix_round124.py`; found 2026-10-10, fixed same day)
**Files:** `wiwi/ir/builtin_tools.py:136-138` (`is_builtin_name`), reached from
`wiwi/providers/anthropic_adapter.py:373`

> **HTTP-reachable sibling: see #390.** Verifying this fix end to end over the real
> request path found that a *fragmented* container name raises earlier, in the
> adapter's name-accumulating branch. #387 is the downstream read; #390 is the one a
> client can actually trigger.
**Fix:** `return isinstance(name, str) and name in BUILTIN_TOOL_TYPES` — the guard the
sibling `canonical_for` already carries (`builtin_tools.py:104-105`) and that #126 asks for.

```python
136:  def is_builtin_name(name: str) -> bool:
137:      """True when a tool-call name (ToolUsePart.name) names a canonical builtin."""
138:      return name in BUILTIN_TOOL_TYPES
```

`name in <dict>` hashes the key, so a `list`/`dict` name raises `TypeError`. Every other
entry point in this module is total (`canonical_for` guards its input, and the `wire/`
callers pre-guard `ttype`); this is the one unguarded read.

**Probe** (executed against the real adapter; reachable through the unguarded
`name_fragment = fn.get("name", "")` at `openai_adapter.py:612` / `nim_adapter.py:430`
that open #385 records — a JSON `null` is the *weakest* case of that missing coercion,
and a list/dict name is the one that crashes):

```
openai     name=['web_search']  -> chunk holds _pending_opens={0: ('c1', ['web_search'])}
openai     name=['web_search']  -> [DONE]: ToolCallOpen(name=['web_search']), ToolCallClose, Finish, StreamEnd
anthropic  block_type='tool_use' -> OK, upstream body carries {"type":"tool_use","name":["web_search"]}
anthropic  block_type='' / None  -> RAISE TypeError: unhashable type: 'list'
```

**Consequence chain.** The gateway folds the delta into `ir.ToolUsePart`
(`core/gateway.py:812`, `streaming/resume.py:170`), whose `__post_init__` coerces the
*id* but not the *name*. So a list name reaches the Anthropic adapter's replay path
verbatim and is emitted upstream as `{"type":"tool_use","name":["web_search"]}`, which
Anthropic 400s. When `block_type` is falsy — an IR part built without it, the fallback
#156 documents — the `bt.is_builtin_name(p.name)` call at `anthropic_adapter.py:373`
raises `TypeError` instead, which the pump turns into a `StreamError`: the request fails
and a healthy deployment is cooled.

**Why not a known finding:** #126 and #154 fix `canonical_for`'s guard and the `wire/`
callers; #232/#385 cover name *coercion* in the adapters. None covers `is_builtin_name`
itself, which is the read that turns a bad name into a 500 instead of a clean 400.

### 388. `thinking_budget_to_effort` is not total — a non-int budget raises `TypeError`

**Severity:** 🟡 Medium · **Status: fixed** — round 124
(`tests/test_fix_round124.py`; found 2026-10-10, fixed same day)
**Files:** `wiwi/ir/types.py:328-349` (`thinking_budget_to_effort`), consumed by
`GenParams.effective_reasoning_effort` (`:251`)
**Fix:** return `"none"` for a non-numeric budget, mirroring the guard #351 added to
`effort_to_thinking_budget` directly above it.

```python
328:  def thinking_budget_to_effort(budget: int) -> str:
...
341:      if budget <= 0:      # str/None/list -> TypeError
```

**Probe** (executed):

```
thinking_budget_to_effort('8000')  -> RAISE TypeError: '<=' not supported between 'str' and 'int'
thinking_budget_to_effort(None)    -> RAISE TypeError
thinking_budget_to_effort([8000])  -> RAISE TypeError
thinking_budget_to_effort(8000.0)  -> 'medium'   (float accepted, so the type is loose by design)
GenParams(thinking_budget='8000').effective_reasoning_effort() -> RAISE TypeError
```

`effort_to_thinking_budget` (`:288`) was made total by #351 with the reasoning "The type
is not enforced at the IR boundary … so the guard belongs here", and its docstring says
so. The inverse function below never got the mirror guard, so the class #351 closed is
only half closed.

**Reachability (recorded so the severity is not over-rated).** No current wire decoder
reaches this: `anthropic_messages.py:415-427` coerces `budget_tokens` to `int` (verified —
a digit-string `"8000"` decodes to `8000`, and all four adapters then encode cleanly), and
the other three surfaces never set `thinking_budget`. It is reachable from any adapter or
admin path that sets the field without coercion. Medium rather than high because the live
entry point is guarded, not because the helper is safe.

**Consequence when reached:** `effective_reasoning_effort()` runs inside `encode_request`
on six adapters (`openai`, `bai`, `nim`, `opencode`, `openrouter`, `workbuddy`), so the
`TypeError` surfaces as a 500 before any upstream call.

**Why not a known finding:** #351 covers only the forward direction; #83/#187 cover
`coerce_int` at the wire boundary.

### 389. `effective_thinking_budget` forwarded the raw field, so fixing #388's helper was not enough

**Severity:** 🟠 High · **Status: fixed** — round 124
(`tests/test_fix_round124.py::test_broken_thinking_budget_never_raises_in_any_adapter`)
**Files:** `wiwi/ir/types.py` (`GenParams.effective_thinking_budget`),
`wiwi/providers/openrouter_adapter.py:179` (`max(g.thinking_budget, 1024)`)

Found while verifying the #388 fix end to end, not in the original sweep: with only
`thinking_budget_to_effort` guarded, three of the eleven provider types still raised
`TypeError` inside `encode_request` on a non-numeric budget.

**Probe** (executed, post-#388-fix, pre-#389-fix):

```
budget='8000'  -> openai:ok bai:ok nvidia-nim:ok opencode:ok
                  openrouter:TypeError workbuddy:ok anthropic:TypeError gemini:ok
```

Two independent reads of the same unvalidated field:

```
ir/types.py  effective_thinking_budget:  if self.thinking_budget is not None:
                                             return self.thinking_budget     # raw
openrouter   179:  budget = max(g.thinking_budget, 1024)                      # raw
anthropic    530:  budget = max(budget, MIN_THINKING_BUDGET)                  # via accessor
```

`effective_thinking_budget` was typed `int | None` but returned the field verbatim, so
`coerce_int` never ran on it — the exact "second convention beside an existing one"
shape `AGENTS.md` prohibits, since `coerce_int` already existed for this job and its
sibling accessor used it. OpenRouter additionally bypassed the accessor entirely and
read the raw field, so it would have raised even with the accessor fixed.

**Fix:** `effective_thinking_budget` routes the field through the existing
`ir.coerce_int` and returns `None` for an unusable value — the value every caller
already handles as "no resolvable budget, leave thinking OFF", so a malformed budget
degrades exactly like an unknown effort instead of 500ing. A float still passes through
(`coerce_int` accepts an integral one, and the callers only compare and add), and `0`
still passes through as the documented thinking-off signal.

**Near-miss while fixing, recorded because it bit.** Routing OpenRouter's read through
`effective_thinking_budget` broke two existing tests (`test_openrouter_adapter.py::test_reasoning_effort_maps_to_reasoning_effort`

## 🟠 Fixed — sweep 2026-11-09 (chat stream encoder emitted text mid-tool-call)

One finding from driving a real Anthropic SSE stream through
`AnthropicAdapter.decode_stream_event` into `ChatStreamEncoder`: the Chat surface
was the only one of the three that did not defer prose while a tool call was open.
The sibling encoders have deferred since #156 (Anthropic) and #367 (Responses);
the Chat wire had no tool-open tracking at all.

### 594. `ChatStreamEncoder` emitted text between two `tool_calls` fragments

**Severity:** 🟠 High · **Status: fixed** — round 203
(`tests/test_fix_round203.py`)
**Files:** `wiwi/wire/openai_chat.py` (`ChatStreamEncoder`: `_open_calls`,
`_deferred`/`_defer`/`_flush_deferred`, the `TextDelta`/`ThinkingDelta`/
`ToolCallClose` feed arms, `final_frame`)

**Trigger:** the Anthropic upstream can interleave prose with a *still-open*
parallel tool call — index 0 opens, index 1 opens and closes, the model writes
text under index 0, then index 0's `content_block_stop` arrives. The decoder
forwards exactly `ToolCallOpen(0)`, `ToolCallOpen(1)`, `ToolCallClose(1)`,
`TextDelta`, `ToolCallArgsDelta(0)`, `ToolCallClose(0)`. Because
`ChatStreamEncoder` tracked no open call, the text went out as a `content`
chunk **between** two `tool_calls` fragments of the same turn.

**Consequence:** an OpenAI client reads a `content` chunk arriving after a
`tool_calls` fragment as prose from a turn that has not finished, and several
agent loops treat any `content` on a tool turn as the end of the assistant's
tool phase — so a parallel call still streaming its arguments is submitted
half-parsed or dropped entirely. Reproduced pre-fix:

```
TEXT REACHED CLIENT BEFORE ALPHA CLOSED: True
  data: {...,"delta":{"content":"Let me also check X."},"finish_reason":null}
```

**Fix:** track the set of client-visible open calls (`_open_calls`; a suppressed
builtin never enters it, since no client-side call exists to hold text back).
Text and thinking buffer into `_deferred` while that set is non-empty and flush
on the close of the **last** sibling. The buffer is capped at
`MAX_DEFERRED_CHARS` (256 KiB) with the sibling encoders' evict-from-the-front
policy, and `final_frame` drains it before the finish chunk — a turn that ends
with a call still open would otherwise lose the model's own words.

---

