# AUDIT archive — fixed entries

Moved out of [`AUDIT.md`](../../AUDIT.md) on 2026-10-30 so the live register
stays readable (AUDIT #340). Everything in this file is closed: each section is
the round that fixed it, and most entries name the regression test that pins the
fix. The content is preserved verbatim.

Entry numbers are historical: the register reused a few of them across sweeps
before #340, so a number here may not match the same number in `AUDIT.md`.

Return to [`AUDIT.md`](../../AUDIT.md).

---

## ✅ Fixed — round 180: the 2026-10-29 sweep (2026-10-29)

Eleven entries closed in one pass, plus the drift corrections the sweep turned
up. Regression coverage: `tests/test_fix_round180.py` (22 tests), with rounds
98, 111 and 142 updated where the *contract* changed rather than a test detail.

| # | Severity | What was wrong |
|---|---|---|
| 319 | 🔴 Critical | Admin edits to a YAML-defined provider applied in memory only: renames were lost, deleted keys resurrected, and an OAuth rotation (which goes through `update_key_secret`) never persisted. The startup merge now layers DB rows **over** YAML for providers and keys, `providers`/`provider_keys` carry `detached` tombstones for a rename or a key delete, `update_key_secret` inserts the override row with the live routing fields, and `update_key`/`delete_key` write an override/tombstone instead of refusing. The PATCH handler's `ConfigStoreNotFound` fallback writes the operator's full desired state (plus a tombstone for the old name on a rename) |
| 383 | 🟠 High | `HealerSettings` validated only `probation_weight`, so `tick_s: 0` made the paid probe loop a tight busy-spin, `max_probes_per_sweep` had no floor, and `probes_to_restore: 0` restored a key on one lucky probe. The spend knobs now reject the degenerate values (`tick_s`/`probe_timeout_s` > 0, counts ≥ 1, intervals not negative) while small cadences and a `0` minimum interval stay legal |
| 373 | 🟠 High | Two concurrent `POST /admin/providers` for one name both answered 200 — the duplicate guard ran before the first `await` and nothing reserved the name, so the DB kept the first key and the router the last. Guard + persist + insert are now one critical section under `AppState.provider_create_lock` |
| 469 | 🟡 Medium | Any upstream 429 cooled the *key*, and the OpenCode free tier shares one `anonymous` sentinel, so one model's blip retired every free deployment for the cooldown. A 429 with no `Retry-After` is now a deployment failure (`deployment_scoped_failure`), charged through `record_fail`, and the credential stays in rotation; a 429 **with** a stated window is still a quota statement and still cools the key — *except* when the body names a quota marker, which is #556 |
| 424 | 🟡 Medium | Hermes device-code sessions were stashed in the Cline pending map and swept by Cline's single TTL. One generic `AppState.oauth_pending` now holds all three browser flows with a per-session `ttl_s`, swept by `evict_expired_pending()` |
| 461 | 🟡 Medium | Per-deployment `rpm`/`tpm`/`timeout`/`max_tokens`/`prompt_cache` had no column, so they never survived a backup/restore or the DB overlay. Both DDLs and the `_migrate` ALTER list gained the five columns; `add_deployment` takes them as keyword-only `_UNSET`-defaulted values (a weight edit cannot wipe them, `None` clears one); `load_all`, the startup merge and the backup export/apply all carry them. `delete_deployment` now UPDATEs the tombstone so a detach preserves weight and limits |
| 462 | 🟠 Medium | A failed Cline OAuth *record* write dropped the replacement single-use grant: the next tick presented the consumed token, got `invalid_grant`, and retired the provider. The rotation is now held in `_pending` and re-persisted by `flush_pending` (sweep) or `_refresh_locked` (401 hook / admin refresh) **before** any new rotation is attempted |
| 532 | 🟡 Medium | All-time `stats.timeseries` bucketing was wrong for any chart with more than one populated bucket: `starts` held one entry per event, so `bisect_right` returned the index after the last equal element and an event in bucket 0 was attributed to bucket N (clamped onto the last day). The index is arithmetic again over the contiguous grid (a deduped `bisect` would compress the gaps) — full entry at the end of this file |
| 486 | 🟡 Medium | `.admin-btn`/`.admin-input` rendered at ~36/38 px and the shell's sidebar/sign-out controls at 32-42 px, under the binding 44 px touch floor. Raised in the unlayered coarse-pointer block (and `pointer-coarse:` variants on the shell controls); desktop density is unchanged |
| 377 | 🟠 High | On touch the 88 px stepper painted over the 72 px weight field (its `padding-right` lived in `@layer components` and lost to the caller's `px-*` utility), and a suffixed `NumberInput` lost its `pr-16` because `{...rest}` re-applied the caller's `className` after it. The stepper is now a **sibling** of the input on coarse pointers (unlayered rule), so no padding has to win a layer fight, and `NumberInput` merges the two class names |
| 378 | ⚪ Low | The weight editor PATCHed twice on Enter (the 500 ms debounce stayed armed) and stuck on a typed decimal (`parseInt("2.5")` saved 2 while `dirty` kept comparing "2.5"). `commit` disarms the timer, `save` normalizes the field to the committed integer, and a `savedRef` makes a duplicate commit a no-op |

**Also corrected in this pass (register drift, #340):**

* #411 (Cline OAuth status) was marked open but is fixed — `cline_oauth_status`
  falls back to decoding the pool key's JWT (`app.py:6252-6279`).
* #351 carried a second, stale `**Status: open**` line under its "fixed"
  marker; the guard exists at `wiwi/ir/types.py:412`.
* #350 was already marked fixed and the code confirms it (`app.py:2238-2246`).

---


## ✅ Fixed — round 173: journal reads off the event loop, the Gemini system register, and the aiosqlite warning leaker (2026-10-25)

Closes the three open register items the 2026-10-25 delta-flow sweep left
flagged. Regression: `tests/test_fix_round173.py`.

| # | Severity | What was wrong |
|---|---|---|
| 237 | 🟡 Medium | The journal's **read** paths did blocking FS work on the event loop. #105 fixed the writes, #282 fixed the 50 ms tail poll, but the reconnect gate still ran `owner_of` + `read_after` + `is_complete` — three whole-file `Path.read_bytes()` calls — synchronously per reconnect, and `open()`'s `mkdir`/`touch` were on-loop too. The gate now answers from `JournalStore.replay_state()` (one frozen `ReplayState` built off-loop; the owner scan is an unfiltered read because the owner record is `seq == 0` and the frames pass filters `seq <= last_seq` — the first cut of the consolidated read reused the filtered records and read "no owner", the exact legacy-and-open state #320/#317 exist to distinguish, caught by round 112's cross-tenant test). The tail loop's `read_next`/`truncated`/`is_expired` gained `*_async` twins, `open()` touches off-loop, and `owner_of_async`/`read_after_async`/`is_complete_async` exist for future async callers. The sync spellings stay: the sweeper runs on a worker thread, and offline tooling owns no loop |
| 328 | 🟠 High (register-only) | The Gemini mid-conversation-system fix was **in the code and correct** (`gemini_adapter.py`: leading system → `systemInstruction`; a later system message folds in place as `[system]`-marked user text, merging into the adjacent user entry or opening its own after a model turn) but no test pinned it — round 64's mid-conversation test covers the Anthropic adapter only — and the entry still read `Status: open`. Four pins added (`tests/test_fix_round173.py`); the entry is corrected in place below |
| 475 | ⚪ Low | The aiosqlite `Event loop is closed` worker-thread warnings that made `-W error::pytest.PytestUnhandledThreadExceptionWarning` misattribute failures: the leaker was **`tests/test_fix_round59.py`**, whose 15 `_engine()` calls never disposed their engines. Reproduced by escalation (errors land on round 59's own cache tests), fixed with a tracked `engine` fixture that disposes in `finally`; the escalated run over the affected files is now clean |

**Also re-verified during this round:** #237's write half and tail half were
already fixed (`append`/`aclose`/owner writes via `asyncio.to_thread`;
`JournalTail` incremental cursor) — only the read half and `open()`'s touch
remained.

---


## ✅ Fixed — round 170: the Playground mobile history drawer (2026-10-25)

### 519. On <768px viewports the history drawer opened onto the collapsed rail and could never be dismissed

**Severity:** 🟠 High on mobile (the whole chat-history surface was unusable
there) · **Status: fixed** — same session, no regression-test file (pure
frontend layout logic; verified live in Chromium at 375×812 and 1440×900 —
the repo's pytest suite covers the Python side only)

**Where:** `web/src/pages/Playground.tsx` — `ChatSidebar` computed
`const isOverlay = collapsed && window.innerWidth < 768` while the render
already used `sidebarCollapsed = window.innerWidth < 768` at mount, and
`onToggle` was `setSidebarCollapsed((v) => !v)`.

**Trigger.** Open `/playground` at any viewport under 768px (default
`sidebarCollapsed = true`): the rail's "Expand sidebar" tap set
`sidebarCollapsed = false`, which rendered the **desktop** column
(`hidden … md:flex` → display:none below md) while `isOverlay` flipped true
and attached the overlay's Escape/scrim handlers to that hidden element.
The drawer never appeared and could never be dismissed; the flag was also
stale across resizes (read once at mount, never updated).

**Fix (as landed).**
- `ChatSidebar` now takes an explicit `variant: "rail" | "column" | "overlay"`
  prop; the parent derives it from a live
  `matchMedia("(max-width: 767px)")` listener plus an explicit
  `drawerOpen` state — no more inferring the form from the collapse flag.
- Stacking fix uncovered while verifying: the drawer's `z-30` was trapped
  inside `.pg-body` (`relative z-10`, under the root wrapper's own
  `relative z-0` stacking context), so the top bar (`z-30`, outside) painted
  **above** the open drawer and swallowed its collapse button. The body row
  now lifts to `z-[40]` while the drawer is open, and the drawer/scrim carry
  `z-[70]`/`z-[60]`.
- While the drawer is open, the page-level "Escape stops the stream"
  handler yields (`drawerHoldsKey`), so Escape closes the drawer instead of
  killing an in-flight turn.
- The same pass also fixed: composer auto-grow on Firefox/Safari
  (`field-sizing` is Chromium-only; JS fallback added), no auto-scroll to
  the new turn when the user had scrolled up (send now re-asserts
  `atBottom`), sub-44px tap targets (banner Retry/dismiss, search-clear ✕,
  clear-all confirm row), hero tabs gained the roving-tabindex +
  Arrow/Home/End contract, the status pill is now an announced live region
  instead of `aria-hidden`, the model selector surfaces a retryable load
  error instead of an empty list, and `--admin-text-dim` was raised
  `#6b7280 → #848c99` (4.0:1 → 6.0:1 on the near-black surfaces) so the
  caption text it drives meets WCAG AA.

**Verification.** Live Chromium via CDP: drawer opens from the rail and closes
via its button, the scrim tap, Escape, and chat selection; `elementFromPoint`
confirms the collapse button hit-tests above the top bar; no horizontal
overflow at 375px; `axe-core` scoped to `main`: 0 violations (the remaining
"incomplete" color-contrast nodes are gradient/blend backgrounds axe cannot
resolve; spot-checked ratios 6:1–21:1). Desktop 1440px unchanged.

---


## ✅ Fixed — the Playground empty-state scroll fought the user (2026-10-23)

### 517. Scrolling a new/empty Playground chat snapped back to the top, flickering

**Severity:** 🟡 Medium (the launcher was effectively unscrollable on any viewport
where it overflows, so the last suggestions were unreachable) · **Status: fixed**

**Where:** `web/src/pages/Playground.tsx` — the `useLayoutEffect` that keeps a
conversation pinned to the newest message (the empty-state branch added by the
earlier "mobile empty workbench" fix).

**Trigger.** Open the Playground on a new (empty) chat and scroll the message
area — any wheel, trackpad, or touch gesture. Reproduced at 400×700, where the
empty launcher overflows its viewport (`scrollHeight 687 / clientHeight 415`).

**Consequence.** The effect's dependency list includes `atBottom`, and every
scroll event runs `handleScroll` → `setAtBottom`. Each flip re-ran the layout
effect, whose empty branch unconditionally executed `el.scrollTop = 0`. So the
user's scroll was undone on the very next frame: setting `scrollTop = 180`
measured `0` a few ticks later, and dragging to the bottom `(272)` snapped back
to `0`. The result was a visible flicker/glitch and an unreachable lower half of
the launcher. The one-time top-align the branch was added for still worked, so
the defect was invisible to the earlier fix's own check.

**Fix.** Track the chat id whose empty state was already top-aligned in a ref
(`emptyScrollChat`) and reset `scrollTop` only when that id changes; clear it
once the conversation is non-empty. A new empty chat still starts at the top,
and the non-empty stick-to-bottom behavior is untouched.

**Verification (live browser, no web test runner in this repo):**
- Post-fix, `scrollTop = 180` held at 180 across 800 ms and 5 samples (was 0
  after ~400 ms), and a six-step 40 px drag produced a strictly increasing
  `0 → 40 → 80 → 120 → 160 → 200 → 240` with no snap-backs.
- Initial load of an empty chat still measured `scrollTop === 0` (top-aligned).
- `tsc -b && vite build` green; `eslint` 0 errors (29 pre-existing warnings,
  none in `Playground.tsx`).

---


## ✅ Fixed — round 169: the /console/settings Import panel (2026-10-22)

Found by reading `web/src/pages/Backup.tsx`, `web/src/pages/Settings.tsx` and
`wiwi/server/backup.py`, then verifying each claim against a live gateway
(in-memory SQLite, `create_app` + `httpx.ASGITransport`). Every finding below
was reproduced, not inferred. Regression tests:
`tests/test_fix_round169.py` (12 tests).

### 509. A no-secrets export still writes Hermes OAuth refresh tokens in clear text

**Severity:** 🔴 Critical (a credential the operator explicitly opted out of
lands in a downloaded file) · **Status: fixed** — round 169
(`tests/test_fix_round169.py`)

**Files:** `wiwi/server/backup.py:131` (`_SECRET_SETTING_PREFIXES` — was
`_SECRET_SETTING_PREFIX`), `wiwi/server/backup.py:452` (`_export_settings`),
`wiwi/server/backup.py:479` (`_redact_oauth`),
`wiwi/server/app.py:6314` (`_hermes_oauth_setting_key`)

**Trigger.** `GET /admin/backup` **without** `include_secrets=1`. The export
sweeps the whole `settings` table and redacts any key starting with
`_SECRET_SETTING_PREFIX`, which is the single string `"cline_oauth:"`. The
Hermes OAuth record is stored under a *different* prefix,
`"hermes_oauth:<provider>"` (`wiwi/server/app.py:6314`), so it never matches
the rule and is written verbatim — refresh token and all.

Reproduced against a live gateway with a synthetic Hermes record:

```
include_secrets flag in doc: False
cline_oauth:mycline   -> {'connected': True, 'email': 'a@b.c', 'redacted': True}
hermes_oauth:myhermes -> {'connected': True, 'email': 'a@b.c',
                          'refresh_token': 'REFRESH-SECRET-abc123',
                          'access_token': 'ACCESS-SECRET-def456'}
```

**Why it matters.** The console's own wording for the toggle is
*"Off. The file will list key labels and their weights but carry no secrets."*
— and the "What a backup carries" card says *"OAuth accounts ride in the keys
section… they only appear when plaintext secrets are included."* Both claims
are false for Hermes: the record never rides in the keys section at all, and
its secret rides in the **settings** section regardless of the toggle.

**Fix sketch.** Replace the single-prefix rule with a set of credential-bearing
prefixes (`cline_oauth:`, `hermes_oauth:`, and any future provider added
alongside them) — or, better, mark a record secret at *write* time (a
`secret: True` flag on the record) and redact on that flag, so a new OAuth
provider is covered the day it is added rather than the day someone
remembers to extend the tuple. Note the Hermes record is currently written by
no live code path (`_hermes_oauth_setting_key` has exactly two references,
both in `app.py`, and neither writes the row — the Hermes credential is
actually stored on the *pool key* as a `hermes:` blob, `wiwi/server/app.py:6524`),
so the leak is reachable only via a row left behind by an older build or
written out of band. It is still a live leak of a live secret, and the
defence must not depend on the writer staying absent.

**The fix.** Two layers, because the prefix tuple alone would leave the same
hole open for the *next* provider:

1. `_SECRET_SETTING_PREFIX` became `_SECRET_SETTING_PREFIXES`, a tuple
   naming every credential-bearing prefix (`cline_oauth:`, `hermes_oauth:`),
   matched through a new `_is_secret_setting_key` helper.
2. `_redact_oauth` now strips by **substring** match against
   `_SECRET_RECORD_FIELDS` rather than by equality against three exact
   names. The old equality list was a second enumeration of the same
   fragility: a record that spelled the field differently (`refreshToken`,
   `token_value`) kept it. The safe direction for a function whose whole
   job is to guarantee that a *no-secrets* file contains no secret is to
   strip anything that looks like a credential and keep only the fields the
   console displays.

The `include_secrets=1` path is unchanged — a credential backup still
carries the refresh token, so the fix does not silently break it
(`test_secrets_export_still_carries_the_hermes_refresh_token`).

**Not done, deliberately.** The structural fix — marking a record secret at
*write* time — is not implemented, because it would change the write shape
of every OAuth provider's record and the tuple is the smaller, safer
change. The tuple's maintenance burden is now carried by a test: the
whole-document sweep in
`test_no_secrets_export_leaks_no_secret_anywhere_in_the_document` fails if
a new secret-bearing key appears in the settings table unlisted.

### 510. The import's `?sections=` filter is ignored — every section is merged

**Severity:** 🟠 High (the operator's explicit scope choice is silently
discarded) · **Status: fixed** — round 169
(`tests/test_fix_round169.py`)

**Files:** `wiwi/server/backup.py:795` (`validate_backup`),
`wiwi/server/backup.py:820` (the plan's `"sections"` key),
`wiwi/server/backup.py:1396` (`apply_backup`),
`wiwi/server/backup.py` (`parse_import_sections` — NEW),
`wiwi/server/backup.py` (`summarize`),
`wiwi/server/app.py:4686` (`admin_import_backup`),
`web/src/pages/Backup.tsx:262` (`previewBackup` / `importBackup`)

**Trigger.** Tick "Request log" in the export, then **untick it** before
merging — the console sends `POST /admin/backup/import?sections=providers,keys,aliases`
with a document that still carries the `logs` section. The server validates
every section and applies every section regardless of the filter.

`validate_backup` stores the requested sections in `plan["sections"]` and
`summarize()` reports them, but **no `_apply_*` function consults
`plan["sections"]`**. `apply_backup` calls all of them unconditionally.

Reproduced against a live gateway — the operator asked to merge three
sections, and the request log was merged anyway:

```
file sections: ['providers', 'keys', 'aliases', 'logs']
file log rows: 1
import status: 200
logs result: 1
TARGET request_logs ROWS: 1
```

**Why it matters.** The section filter is the console's *only* scope control
on the import side, and the "Merge only" card tells the operator *"anything the
file does not mention is left alone"* — implying that what the file *does*
mention, and what they selected, is what lands. The dry-run preview is worse
than useless here: it reports the counts of the *document*, not of the
*selection*, so the operator sees a preview they believe matches the merge and
it does not.

**Fix sketch.** Gate each section's apply on membership in `plan["sections"]`
— the plan already carries the tuple. The bulk sections need the same gate
(`_apply_bulk` currently writes all three). The dry-run summary should
report the *selected* sections' counts, so the preview and the merge agree.

**The fix.** Four parts:

1. **`apply_backup` gates every section** on `plan["sections"]`. A section
   that was not selected is *absent* from the result rather than reported as
   a zero — the console renders these counts as prose ("merged 3 providers,
   1 alias…"), so a deselected section must not appear in it at all.
2. **`_apply_bulk` gates each bulk section individually** (`logs`,
   `rollups`, `responses`), since the picker can select one without the
   others and they share one transaction.
3. **`summarize` reports the selection**, so the dry-run preview counts what
   the merge will do rather than what the document contains.
4. **`parse_import_sections` (NEW)** resolves the selection when the caller
   sent no `?sections=`: it is the sections the document **actually
   carries**, not the ones it declares.

Point 4 is the one that needed a second pass. The first implementation used
the document's declared `sections` list, and that broke
`tests/test_fix_round142.py::test_a_failed_rollup_row_is_counted_and_the_rest_survives`
— that test hand-builds a document carrying a `rollups` section that its
declared list does not mention, and the rollup was silently skipped. A
document's `sections` field is a *claim*, not a fact: a file exported with
`?sections=logs` and then hand-edited, or merged from a file that carried
more, can hold a section it does not list. The content is the truth. The
declared list is only a hint, and a hint must not decide what gets written.

One deliberate exception: the `users` section is applied when `vkeys` was
selected, even if `users` was not. A vkey's `owner_id` references a user,
and a key whose owner does not exist is rejected by the auth check, which
fails CLOSED — so creating the referenced user is part of restoring the key,
not a separate scope the operator has to know about. It is *not* applied
for an unrelated selection: merging a providers-only backup must not create
accounts.

### 511. The About tab states facts the code contradicts

**Severity:** 🟡 Medium (operator-facing documentation is wrong) ·
**Status: fixed** — round 169
(`tests/test_fix_round169.py`)

**Files:** `web/src/pages/Settings.tsx:381` (`AboutTab`),
`web/src/pages/Settings.tsx:389` (the "Data retention" card),
`web/src/pages/Settings.tsx:437` (the "Models" info tile),
`wiwi/logging_core/subsystem.py:51` (the ring size),
`wiwi/server/app.py:1143` (the DB sink)

**Trigger.** Open Settings → About. Three claims are false:

1. *"Request and proxy log rings hold ~500 events in memory. / Stats windows
   are computed from that ring. / **DB persistence lands post-MVP.**"* — the DB
   sink is not post-MVP. `AppState.init_db` creates a `DBSink` unconditionally
   (`wiwi/server/app.py:1143`), and `/admin/stats/overview` reads from it
   whenever it exists (`wiwi/server/app.py:5280-5285`), precisely so that
   *"usage stats silently reset on every redeploy"* stops happening. The 500
   event ring is a fallback path, not the storage.
2. *"Inbound surfaces: OpenAI Chat / OpenAI Responses / Anthropic Messages"*
   — the legacy `POST /v1/completions` surface and the cookie-gated
   `POST /v1/playground/completions` are absent, as is the OpenAI Completions
   *dialect* the repository ships a codec for.
3. The "Models" info tile links to `/models`, which is the **public catalog**
   page (`web/src/main.tsx:127` → `ModelsCatalogPage`). The admin page that
   configures deployments is `/console/models` (`web/src/main.tsx:195` →
   `ModelsPage`). The tile is titled "Models" and describes it as
   *"configure deployments"*, so it sends the operator to the wrong page.

**Fix sketch.** Rewrite the retention card against the real storage model
(DB sink primary, 500-event ring as fallback), list all five inbound
surfaces, and point the tile at `/console/models`.

**The fix.** The retention card now names the two real knobs and their
defaults — `log_retention_days` (30) and `log_max_rows` (10 000, from
`wiwi/config.py`) — states that pruned rows are rolled into hourly usage
rollups first so totals survive the delete, and describes the ~500-event
ring as the fallback for when the database sink is unavailable. The
inbound-surface list gained "OpenAI Completions" and "Playground". The
Models tile now points at `/console/models`.

**A responsive defect found while verifying the fix.** The two new badges
made a pre-existing mobile problem visible: `.admin-badge` had no
`white-space` rule, so on a 375px viewport the flex item broke a label
*mid-word* — "OPENAI RESPONSE / S". The fix is `white-space: nowrap` on
`.admin-badge` (`web/src/styles.css`), so each badge stays whole and the
container wraps *between* badges instead.

The first attempt also added `max-width: 100%` + `overflow: hidden` +
`text-overflow: ellipsis`, and that was wrong: a badge inside a narrow
table cell is *wider* than its cell, and clipping it hid the label
entirely ("openai-compatible" became "openai-compatibl…"). Those three
properties were removed — overflowing the cell is the lesser evil, the
text stays readable, and the table scrolls. Verified by driving eight
console pages at 375px and 1440px and measuring every `.admin-badge` for
a two-line box or a clipped label: none.

### 512. The General tab's "Last checked" ignores the 24-hour clock preference

**Severity:** ⚪ Low · **Status: fixed** — round 169
(`tests/test_fix_round169.py`)

**Files:** `web/src/pages/Settings.tsx:133`

**Trigger.** Settings → Appearance → turn **off** "24-hour clock", then look
at Settings → General. The "Last checked" row is hardcoded
`toLocaleTimeString("en-US", { hour12: false })` — always 24-hour, whatever
the preference says. The topbar clock honours the pref
(`web/src/components/Layout.tsx:162`), so the console shows two different
formats for the same clock.

**Fix sketch.** Read `prefs.clock24h` (the `useClientPrefs` hook is already
imported in this file) and pass `hour12: !prefs.clock24h`.

**The fix.** `GeneralTab` now reads `useClientPrefs()` and passes
`hour12: !prefs.clock24h`, matching the topbar.

---

## ✅ Fixed — round 168: the /admin/keys routes move to a router + service + schemas (2026-10-21)

### 508. A per-app FastAPI dependency was silently demoted to a body field

**Severity:** 🟠 High · **Status: fixed** — round 168
**Files:** `wiwi/server/routers/admin_keys.py` (the router),
`wiwi/server/dependencies.py` (the guard), `wiwi/server/app.py` (the mount),
`wiwi/server/schemas/admin_keys.py` (the request models),
`wiwi/server/services/key_admin.py` (the rules),
`wiwi/server/errors.py` (NEW — `APIError` + the dialect-correct renderer)

**Trigger.** The five `/admin/keys` routes moved out of `create_app`'s closure
into a router module. The first cut of that move passed the auth guard as a
**per-app closure**:

```python
app.include_router(admin_keys_router.make_router(
    KeyAdminService(state, config.router_settings.priority_lanes),
    make_require_user_dependency(state)),          # ← closure, not a name
)
```

with the handler annotated `actor: Annotated[UserInfo, Depends(require_user)]`.

**Cause.** `from __future__ import annotations` makes every annotation a
*string*, and FastAPI evaluates those strings against the **module globals** of
the file that declares the handler — not the enclosing function's locals. The
closure-local `require_user` was therefore invisible to the resolver, so the
`Depends` marker was never seen and `actor` was treated as an ordinary
**body field**. Every request to the five routes failed:

```
400 invalid_request_error: actor: Field required
```

The failure is silent in the sense that matters: the app boots, the routes
are registered, the paths answer, and the error is a 400 that looks like a
client mistake. Nothing says "your dependency did not resolve". A guard that
never runs is worse than no guard — the routes looked protected and were not.

**The fix.** The guard is module-level and reads the app state the standard
FastAPI way:

```python
async def require_user(request: Request) -> UserInfo:
    actor = await resolve_current_user(request, app_state(request))
    if actor is None:
        raise APIError(401, "authentication_error", "authentication required")
    return actor
```

The `CurrentUser` / `AdminUser` aliases live in `wiwi/server/dependencies.py`
next to the function they name, so the annotation resolves against the module
that defines it.

**Two further corrections the move forced, both now pinned by
`tests/test_fix_round168.py`:**

* **422 → 400.** FastAPI's default request-validation handler answers *422*
  with `{"detail": [...]}`. wiwi's contract is **400** with a dialect-shaped
  body (`docs/API_REFERENCE.md`), and 422 was a status the gateway had never
  returned. A `RequestValidationError` handler was installed in
  `server/app.py` to re-render validation failures as a 400 in the caller's
  dialect.
* **`TypeError` → `ValueError` in the Pydantic validators.** Pydantic v2
  converts only `ValueError`/`AssertionError` into a validation error; a
  `TypeError` raised in a `field_validator` escapes the validator and
  surfaces as an unhandled **500**. The validators raise `ValueError`
  (with `# noqa: TRY004`, the same reasoning as
  `wiwi/auth/service.py`).

**Three fidelity defects the review caught and the move introduced** (all
fixed, all pinned by `tests/test_fix_round168.py`):

* **The error message changed.** Pydantic reports a validator's `ValueError`
  as `"Value error, 'models' must be a list of strings"`, and the field name
  in front of it. The old handler returned exactly
  `"'models' must be a list of strings"`. A client matching on the message
  would have broken, and the existing tests only checked a substring, so
  nothing caught it. `validation_message()` in `wiwi/server/errors.py` now
  strips the prefix and uses a custom validator's message verbatim; Pydantic's
  *built-in* errors still get the field name, because those are terse
  ("Input should be a valid string") and the field is what makes them
  actionable.
* **The response model ADDED a field.** `list_keys_for_owner` omits
  `owner_id` (the WHERE clause already fixed it) while `list_keys` includes
  it. The typed response model would have added `"owner_id": null` to the
  owner-scoped listing — a shape the old handler never produced. The two
  routes that return key dicts are mounted with
  `response_model_exclude_unset=True`, so an absent field stays absent.
* **The `priority` coercion moved.** The old `_key_priority` helper turned
  `""` and `"   "` into `None` and trimmed the rest. The schema's validator
  reproduces that exactly, so a blank lane still means "no lane".

**Regression coverage.** `tests/test_fix_round168.py` (30 tests) pins the
route table, the response shapes, the 400-not-422 rule, the verbatim error
messages, the `owner_id` presence/absence split, the authorization rules
(401 / 403 "not your key" / 403 "may only be set by an admin"), the audit
trail, and `test_actor_is_a_dependency_not_a_body_field` — the defect
above, so a future "make it per-app again" change cannot land silently.

**Also fixed in the same move:** the guard's resolution logic was duplicated
between `server/app.py`'s closures and the new module (two copies of the
master-bearer → session-cookie → anonymous chain). There is now one
definition, in `wiwi/server/dependencies.py`, and `app.py`'s closures
delegate to it.


## ✅ Fixed — round 167: DeepSeek's thinking-mode round-trip was missing on the OpenCode route (2026-10-21)

### 507. A DeepSeek model on the OpenCode adapter was rejected with `The reasoning_content in the thinking mode must be passed back to the API`

**Severity:** 🟠 High · **Status: fixed** — round 167
**Files:** `wiwi/providers/opencode_adapter.py` (`encode_request`, the chat
route), `wiwi/providers/deepseek.py` (NEW — the shared rule),
`wiwi/providers/bai_adapter.py` (the same rule, now shared rather than copied)

**Trigger:** route any `deepseek-*` model through the **OpenCode** provider
(Zen) with a request that carries tools, and replay a history in which any
assistant turn has no `reasoning_content` — the ordinary shape of a transcript
a router rebuilt from the client's own messages. The upstream answers:

```
400 invalid_request_error: The reasoning_content in the thinking mode must be
passed back to the API.
```

Through Zen the client sees the Console wrapper
(`io: Upstream request failed: [invalid_request_error] …`), which reads like a
gateway fault rather than a request-shape one.

**Cause.** DeepSeek's thinking mode documents one hard rule
(api-docs.deepseek.com/guides/thinking_mode): when a request carries
`tools`, the `reasoning_content` of **all** previous assistant turns must be
passed back — *even for turns where the model made no tool call*. What the
API validates is the field's **presence** on every history assistant message.

The OpenCode adapter's chat route delegates to the OpenAI adapter with
`provider_type="openai"`, and that adapter writes `reasoning_content` only on
the history turns that *have* thinking
(`wiwi/providers/openai_adapter.py`, `_role_parts_to_content`). A turn whose
reasoning the client did not echo therefore arrived with the field missing —
precisely the turn DeepSeek rejects. The B.A.I adapter had already solved this
for its own route (`wiwi/providers/bai_adapter.py`), but the OpenCode route
never received the rule: the two adapters were two copies of a provider fact
that only one of them had.

**Fix.** The rule moved into one module, `wiwi/providers/deepseek.py`, and
both adapters apply it:

- `is_deepseek_model()` — the model-id test (substring, so
  `deepseek-ai/DeepSeek-V3.2` and `DeepSeek-R1` are matched too).
- `deepseek_thinking_enabled()` — DeepSeek thinks **by default**, so only an
  explicit disable (`reasoning_effort="none"`, which is what the Anthropic
  codec's `thinking.type="disabled"` normalises to) turns the rule off.
- `deepseek_roundtrip_required()` — model + tools + thinking on.
- `apply_deepseek_reasoning_roundtrip()` — pads every history assistant
  message that lacks the field with `""`, and returns whether it changed the
  body.

Two details the fix had to get right, both of which a naive port of the B.A.I
copy would have missed:

1. **"Does the upstream see tools?" is the caller's to answer.** The rule keys
   on the tools the *upstream* will see, and the two adapters answer that
   differently — correctly so. B.A.I adds no tools of its own, so the
   *declared* tools are the tools on the wire, which is what its route has
   always keyed on. The OpenCode adapter's free-tier cloak injects
   `read`/`shell` into the body, so there the *wire* is the answer — a
   caller that declared no tools still requests tool-carrying upstream. The
   shared helper takes the answer as an argument (`has_tools`) rather than
   deciding it, so neither adapter is forced into the other's reading and
   the B.A.I route stays byte-for-byte unchanged.
2. **The padding is additive.** A turn that *has* real reasoning keeps it; the
   helper only adds the field to the turns that lack it, so the reasoning
   text a client sent is never overwritten with an empty string.

**Blast radius (measured, not asserted).** The pre-fix and post-fix adapters
were loaded side by side — the real `HEAD` versions out of git, not a
reconstruction — and their encoded bodies compared byte for byte over 960
OpenCode and 180 B.A.I request bodies (16 models x 2 inbound dialects x tools
x history shape x effort). Result: **35 OpenCode differences, every one of
them a DeepSeek model in the `history=bare` shape — the bug's own shape —
and 0 non-DeepSeek differences; 0 B.A.I differences at all.** The eight other
adapters do not import the module, so their behaviour cannot change.

**Regression:** `tests/test_fix_round167.py` (23 tests) — the empty-string
pad, the reasoning text surviving, the rule not firing without tools / with
thinking disabled / on a non-DeepSeek model, the cloak ordering, the
`has_tools` split between the two adapters, the Anthropic-dialect (Claude
Code) and OpenAI-dialect callers, and the messages route staying untouched.


---

## ✅ Fixed — round 166: the README's copy-paste config did not load (2026-10-20)

A sweep of the **README's own YAML** — every ```yaml block in `README.md`,
extracted and fed to wiwi's own loader (`wiwi.config.load_config`) — for
blocks a reader copying them verbatim could not actually use. The README is
the project's front page and its Quickstart tells the reader to
`cp wiwi.yaml.example wiwi.yaml` and edit; the annotated config block is
presented as *the* reference to copy. It has to load.

**Method.** Every ```yaml block in `README.md` was extracted and passed
through `load_config` with the provider-key environment variables set. A
block that names a provider account, or a `router_settings` key, that the
schema or the validator rejects is a copy-paste failure.

**Result.** Two of the five blocks failed. Both are fixed below; all five
now load.

### 505. The annotated config's `stream_resume: off` is YAML's boolean, and the schema rejects it

**Severity:** 🟠 High · **Status: fixed** — round 166
**Files:** `README.md` (the annotated `wiwi.yaml` block), `wiwi/config.py:330`
(`RouterSettings.stream_resume`)

**Trigger:** copy the README's annotated config into `wiwi.yaml` and start
the gateway.

**Cause.** `stream_resume` is typed
`Literal["off", "content_only", "enabled"]` (`wiwi/config.py:330`). YAML 1.1
parses a bare `off` as the **boolean** `False`, not the string `"off"` — so
the README's `stream_resume: off` reached the validator as `False` and was
rejected outright:

```
ConfigError: invalid config at 'router_settings.stream_resume':
Input should be 'off', 'content_only' or 'enabled'
```

The gateway refuses to start on that line. The same trap applies to `no`
and `yes`. `docs/CONFIG.md:151` already documented the value correctly,
as the quoted string `"off"`.

**Fix:** quote the value in the README — `stream_resume: "off"` — and say
why in the comment, since a reader "simplifying" the quotes re-breaks it.

**Regression:** the five README YAML blocks are now validated against
`load_config` as part of this round's verification (see below).

### 506. The annotated config referenced a provider account it never defined

**Severity:** 🟠 High · **Status: fixed** — round 166
**Files:** `README.md` (the annotated `wiwi.yaml` block's `model_list`)

**Trigger:** copy the README's annotated config into `wiwi.yaml` and start
the gateway.

**Cause.** The block's `model_list` routed `claude-sonnet` at the provider
account `anthropic-main`, but the block's `providers:` list defined only
`openai-main` and `local-ollama`. wiwi's validator requires every
`wiwi_params.provider` to resolve to a `providers:` entry, so the config
was rejected:

```
ConfigError: invalid config at '<root>': Value error,
model 'claude-sonnet' references unknown provider 'anthropic-main'
```

This is the single most-copied block in the file — it is the config the
Quickstart points at — and it could not start a gateway.

**Fix:** add the missing `anthropic-main` account to the block's
`providers:` list, so the block is self-contained and loadable on its own.

**Regression:** all five of the README's YAML blocks now load through
`wiwi.config.load_config`. The new "Recipes" section was written against
the same bar: each of its four recipes is a standalone fragment that loads
on its own (the fourth needed its two provider accounts added for the
same reason as #506).

---

## ✅ Fixed — round 164: the translation layer's loss points (2026-10-19)

A sweep of the **decode → IR → re-encode** path — every inbound codec
(`wiwi/wire/`), every outbound adapter (`wiwi/providers/`), the IR itself
(`wiwi/ir/`) and the delta taxonomy (`wiwi/streaming/deltas.py`) — for
information that is lost on the way through, and for losses the caller
cannot see.

**Method.** One maximally-rich IR request (every part type, every tool
flag, every generation parameter) encoded into every adapter, and one rich
`AssistantTurn` encoded into every inbound dialect; each feature's survival
probed with a sentinel value rather than a substring, so a probe could not
report a false positive. The matrix is reproduced in the test module's
docstring.

**Result.** The IR is not the problem — it models the intersection of the
dialects correctly, and the lossless paths (Anthropic→Anthropic, OpenAI→
OpenAI) are lossless. The loss was in three places: a *carrier* gap (the IR
had no field for something), a *silence* gap (the carrier existed but the
caller was not told), and a *billed-but-unreturned* gap (the upstream charged
for a feature the client never received).

### 496. `json_schema` was dropped entirely on the Gemini adapter

**Severity:** 🟠 High · **Status: fixed** — round 164 (`tests/test_fix_round164.py`)
**Files:** `wiwi/providers/gemini_adapter.py` (`encode_request`, `generationConfig`)

The adapter handled only `response_format.type == "json_object"`, which
Gemini spells `responseMimeType: "application/json"`. A caller asking for
`json_schema` — the constrained-output form, with an actual schema — got
**nothing**: no `responseMimeType`, no schema, no error. The model answered
in prose, the caller had no way to know its constraint was ignored, and the
request looked successful.

Gemini has a native spelling for this: `responseSchema` in
`generationConfig`. It was simply never emitted.

**Fix:** `json_schema` now emits both `responseMimeType: "application/json"`
and `responseSchema` (with the schema's `name` as `title` when present).
A schema that is not a JSON object is dropped with a caller-visible warning
rather than forwarded as a malformed `responseSchema`.

**Test:** `tests/test_fix_round164.py::test_response_format_reaches_a_gemini_upstream`

### 497. `stop_sequence` was populated on the IR and read by no OpenAI-shaped encoder

**Severity:** 🟡 Medium · **Status: fixed** — round 164 (`tests/test_fix_round164.py`)
**Files:** `wiwi/ir/types.py` (`AssistantTurn.stop_sequence`), `wiwi/providers/anthropic_adapter.py` (populates it), `wiwi/wire/openai_chat.py`, `wiwi/wire/openai_responses.py`

`AssistantTurn.stop_sequence` exists precisely to carry "which caller-supplied
stop sequence fired" — a distinction `StopReason` gained the `stop_sequence`
member for. The Anthropic adapter populates it (`anthropic_adapter.py:676`).
No OpenAI-shaped encoder ever read it: OpenAI's `finish_reason` vocabulary
has no `stop_sequence` value, so the information was dropped at the encoder.

The caller of an OpenAI-shaped surface learned that the turn stopped but
never *why*, and the caller of a Responses surface learned the same.

**Fix:** `openai_chat.encode_response` emits `choice["stop_sequence"]` and
`openai_responses.encode_response` emits `response["stop_sequence"]`, both
only when the value is present. The Anthropic encoder already carried it.

**Test:** `tests/test_fix_round164.py::test_stop_sequence_reaches_an_openai_chat_caller` (+ 2 siblings)

### 498. The caller-visible translation-warning channel was wired into one adapter out of ten

**Severity:** 🟠 High · **Status: fixed** — round 164 (`tests/test_fix_round164.py`)
**Files:** `wiwi/providers/base.py` (new `WarningMixin`), `wiwi/providers/{openai,openrouter,anthropic,gemini,opencode}_adapter.py`, `wiwi/server/app.py` (`_with_warnings`)

`providers/base.py` documents the mechanism it owns:

> Adapters that want to surface such warnings set `self.translation_warnings`
> to a list during `encode_request`; the gateway drains it into
> `ctx.metadata["translation_warnings"]` via this helper.

and explains why it exists: "an agent that serializes tool calls for
correctness (file edits, shell state) would never learn it is getting
concurrency it forbade."

**Only the Gemini adapter used it** (for `strict` and
`disable_parallel_tool_use`). Every other drop was visible only to the
*operator* in the proxy log. The mechanism drained into `ctx.metadata` and
from there into the spend log — so even the one adapter that used it never
told the *caller*; the caller had no channel at all.

Confirmed silent drops (all reachable from a normal client request):

| Drop | Adapters |
|---|---|
| provider-hosted builtin tool | OpenAI, OpenRouter, Gemini, NIM, OpenCode |
| `top_k` | OpenAI, OpenRouter, NIM |
| `seed` | Anthropic, Gemini |
| `strict` (already warned) | Gemini |
| `disable_parallel_tool_use` (already warned) | Gemini |
| audio attachment | Anthropic, Gemini, OpenCode |
| document title/context | Gemini |

**Fix, in three parts:**

1. **`providers/base.py` gains `WarningMixin`** — the attribute, its reset,
   and one `warn()` method, so every adapter phrases its warnings the same
   way and no adapter hand-rolls the list. It lives in `base.py` because it
   is a generic contract with no provider-specific logic, which is the only
   part of `providers/` the layering rule lets `core/` import.
2. **Every adapter now warns** on the drop it performs, and the OpenCode
   adapter forwards its three sub-adapters' warnings up to itself (they
   would otherwise be collected and thrown away, so the caller learned
   nothing on those routes).
3. **`server/app.py` gains `_with_warnings`**, which puts the messages in an
   `x-wiwi-translation-warnings` response header. A header is the only
   channel available: the response body is dialect-owned, and an
   OpenAI-shaped body cannot carry an Anthropic-shaped note without
   breaking the client's schema expectations. The header is absent when
   there are no warnings, so a clean request's response is unchanged. The
   full list always remains in the request log.

**Test:** `tests/test_fix_round164.py` — 8 warning tests plus two end-to-end
ones (`test_the_caller_learns_that_a_builtin_tool_was_dropped`,
`test_a_caller_defined_function_tool_is_not_reported_as_dropped`, which
pins the flag-not-name rule that AUDIT #156 established).

### 499. Two part types fell through the adapter loops with no branch and no warning

**Severity:** 🟠 High · **Status: fixed** — round 164 (`tests/test_fix_round164.py`)
**Files:** `wiwi/providers/anthropic_adapter.py`, `wiwi/providers/gemini_adapter.py`, `wiwi/providers/opencode_adapter.py`

`AudioPart` had **no branch at all** on the Anthropic and Gemini adapters:
the part simply vanished from the `for p in m.parts` loop — not even a log
line. A caller that attached audio got no indication that the model would
never hear it. (The OpenCode adapter logged, but only for the operator.)

`DocumentPart.name` and `DocumentPart.context` — a PDF's title and the
caller's description of it — were dropped on every non-Anthropic adapter
with no report. A caller attaching `report.pdf` with the context `"Q3
revenue"` got a document with neither, and two differently-named
attachments became indistinguishable to the model.

**Fix:** explicit branches. Anthropic and Gemini now report the dropped
audio part (the drop itself is unavoidable — the Messages API has no audio
content block, and Gemini's `inline_data` audio shape differs from the
inbound dialects'). Gemini and the OpenAI-wire family (OpenAI, OpenRouter,
NIM, BAI, Cline, WorkBuddy, Hermes) report the dropped document
title/context. Anthropic is exempt from the latter: it has native `title`
and `context` fields, and a test pins that.

**Test:** `tests/test_fix_round164.py::test_anthropic_adapter_does_not_silently_drop_an_audio_part` (+ 4 siblings, one pinning the Anthropic document-title case)

### 500. An unsigned thinking block was sent to Anthropic, which rejects it on replay

**Severity:** 🟠 High · **Status: fixed** — round 164 (`tests/test_fix_round164.py`)
**Files:** `wiwi/providers/anthropic_adapter.py` (`encode_request`, `ThinkingPart` branch)

`RESEARCH.md:67`: "Anthropic signatures MUST round-trip unmodified or 400."

The adapter sent `{"type": "thinking", "thinking": ...}` with **no
`signature`** whenever the source had none — so the block was rejected by
the upstream on replay, naming nothing the caller sent.

Two reachable paths:

- A Responses `reasoning.encrypted_content` blob (which no Anthropic-shaped
  source signs) replayed to an Anthropic upstream. AUDIT #355 added the
  `encrypted_content` carrier; its fix sketch also said "have
  `anthropic_adapter` skip (or refuse) a `ThinkingPart` with no signature
  rather than send it unsigned" — **only the `encrypted_content` half of
  that landed.**
- An OpenAI-shaped client's `reasoning_content` echoed back as history: the
  Chat codec lifts it into a `ThinkingPart` (`openai_chat.py`, the
  `reasoning_content` arm), and the OpenAI dialect has no notion of a
  signature to sign it with.

**Fix:** the block is dropped rather than sent, and the caller is warned. A
dropped thinking block is a history the API still accepts — the model just
loses the visible reasoning it produced. An unsigned replay is a
guaranteed 400. The `redacted_thinking` blob is unaffected: it IS the
signed material, and it still replays verbatim.

**Test:** `tests/test_fix_round164.py::test_anthropic_upstream_never_receives_an_unsigned_thinking_block` (+ 2 siblings, one pinning the signed case and one pinning the redacted blob)

### 501. Twelve OpenAI-2026 platform params are honoured and billed by the upstream but invisible to the caller

**Severity:** 🟡 Medium · **Status: fixed — round 164 (`tests/test_fix_round164.py`)**
**Files:** `wiwi/providers/base.py` (new `OPENAI_PLATFORM_PARAMS`, `unhonoured_platform_params`), `wiwi/providers/anthropic_adapter.py`, `wiwi/providers/gemini_adapter.py`, `wiwi/providers/nim_adapter.py`, `wiwi/providers/opencode_adapter.py`

`user`, `frequency_penalty`, `presence_penalty`, `logit_bias`,
`service_tier`, `store`, `prediction`, `modalities`, `verbosity`,
`safety_identifier`, `prompt_cache_key`, `web_search_options` are forwarded
verbatim to an OpenAI-shaped upstream and dropped by every other provider.
They are *request-side* knobs: unlike `logprobs` (#326) they have no
response-side value, so there is nothing to carry back.

The upstream honours and bills for them, so the request is not wasted — but
a caller that set `logit_bias` against an Anthropic or Gemini deployment
learns nothing about it. #498 closed the silence for the drops the gateway
*can* see; these are forwarded rather than dropped, so no drop is visible
to warn about.

Also unrepresentable in the IR: assistant **audio output** and
`image_url.detail`.

**Fixed in round 164 — by warning, not by suppressing.**

The decision the original fix sketch left open — "forwarding-and-warning
keeps a strict OpenAI-compatible gateway's behaviour, while
not-forwarding-and-warning is honest about what the backend did" — was
resolved in favour of **warning**: the parameter is still forwarded (so a
deployment that talks to an OpenAI-shaped backend behind a non-OpenAI-typed
provider entry keeps working), and the caller is told it did not apply to
this backend. Suppressing it would have been a silent behaviour change for
any deployment whose *typed* provider understates what it actually talks to
— and the provider type is configuration, not a fact about the endpoint.

- `providers/base.py` gains `OPENAI_PLATFORM_PARAMS` and
  `unhonoured_platform_params(extras)`. The table lives in `base.py` rather
  than in one adapter because BOTH layers need it: the OpenAI-shaped
  adapters forward these (they CAN honour them) and the Anthropic/Gemini
  adapters must report that they cannot. A single table keeps the two
  sides from drifting — a param added to the forward list without a row
  here would be silently dropped on every non-OpenAI backend, which is the
  exact defect this table exists to prevent.
- The Anthropic and Gemini adapters report each such param by name.
- The OpenAI-shaped adapters do **not**: they honour the params, so a
  warning there would be a false alarm on every ordinary request that sets
  `logprobs` or `service_tier`. A test pins that.
- Two providers strip these for a *different* reason and were silent on it
  too: **NIM** strict-validates its body ("Unsupported parameter(s):
  `prompt_cache_key`") and 400s, so the strip is mandatory — and **the
  OpenCode Responses route** never forwards extras at all. Both now report
  what they removed. NIM's strip predates this finding and was correct in
  what it did; what it lacked was the report.

**Test:** `tests/test_fix_round164.py::test_anthropic_adapter_reports_the_platform_params_it_cannot_honour` (+ 4 siblings, one pinning that the OpenAI adapter stays silent, one for NIM's mandatory strip, one for the OpenCode Responses route)

---

## ✅ Fixed — round 165: the SSE layer's ingest and replay (2026-10-20)

A sweep of the **SSE** layer — the upstream ingest parser
(`wiwi/streaming/sse.py`), the gateway pump (`wiwi/core/gateway.py`), the
journal (`wiwi/streaming/tape_store.py`), the client-facing writer
(`wiwi/server/app.py`) and the four wire encoders (`wiwi/wire/`) — against
the streaming contract in `docs/STREAMING.md` and the reconnect contract in
`docs/API_REFERENCE.md`.

**Method.** Probes against the real app (`create_app` + a `respx`-mocked
upstream) and against the real parser, driven both through `httpx` and
directly at the ASGI app so that *when* a frame was sent could be observed
rather than inferred. Each finding below was reproduced against the current
tree before being written down.

> **A note on the method, recorded because it cost the sweep a wrong
> finding.** #502 was first written as a 🔴 "the client loses the frame's
> content" defect on the strength of an end-to-end probe that showed the
> content missing. The probe's fixture was invalid: it put **two** JSON
> documents in **one** frame, which the SSE spec joins into a single
> multi-line `data` payload, and two concatenated JSON documents are not
> valid JSON — so the frame was *correctly* dropped. The decisive control
> (the same frame with the `id:` line removed) loses the content
> identically, exonerating the `id:` line. #502 below is the **corrected**
> entry, with the correction and the control recorded in place. The lesson
> is the one the register already teaches elsewhere: a probe that shows a
> symptom must also show a **control** where the suspected cause is
> absent, or the finding is a guess.

### 502. An SSE field line (`id:`, `retry:`) on the upstream stream is read as a data payload

**Severity:** 🟡 Medium · **Status: fixed** — round 165 (`tests/test_fix_round165.py`)

**Where:** `wiwi/streaming/sse.py:36-49` (`LineSSEParser.feed_line`),
`wiwi/core/gateway.py:838` and `:1955` (both pumps build the parser with
`allow_unframed=True`).

**Trigger:** any upstream that emits an `id:` or `retry:` field line.
This is legal SSE — those are *fields* of the frame, not payloads.

**Cause.** `allow_unframed` exists so the envelope providers (Cline,
WorkBuddy), which answer with one whole JSON object per line, are not
silently discarded. Its implementation, however, was a catch-all: **any**
line that is not a comment, not `event:` and not `data:` was emitted as an
immediate data event — including `id:`, `retry:` and any other legal SSE
field. The parser therefore handed the adapters a second, spurious event
whose payload was the literal text `id: 42`.

**Consequence.** Every adapter opens `decode_stream_event` with
`orjson.loads(data)` and returns `[]` on a decode error, so the spurious
event is dropped — the frame's *own* payload is untouched. The observable
defect is therefore **a wasted event, not lost content**: the pump decodes
one extra frame per field line, and the healer's probe classifier
(`core/recovery.py`) scans one extra payload per field line. The
`id:`-line-carrying frame itself is decoded normally.

> **Correction.** This entry was first written as a 🔴 "the frame's real
> content is lost" finding, on the strength of an end-to-end probe that
> showed the content missing. That probe was **invalid**: its fixture put
> *two* JSON documents in one frame, which the SSE spec joins into one
> `data` payload — and two concatenated JSON documents are not valid JSON,
> so the frame was correctly dropped. The decisive control (the same frame
> with the `id:` line REMOVED) loses the content identically, exonerating
> the `id:` line. The corrected probe, one JSON document per frame:
>
> ```
> id: 1 / data: {"c":"Hello"}   ->  PRE: ['id: 1', '{"c":"Hello"}']
>                                   POST: ['{"c":"Hello"}']
> data: {"c":"Hello"} (no id)   ->  PRE: ['{"c":"Hello"}']
>                                   POST: ['{"c":"Hello"}']
> ```
>
> The `id:` line's only real effect was the extra junk event, which the
> pre-fix and post-fix parsers both emitted. The fix is still right — the
> field line should never have been read as a payload — but the severity is
> 🟡, not 🔴, and no content was ever lost.

**Fix:** `LineSSEParser.feed_line` now recognises `id:` and `retry:`
explicitly and treats them as fields (ignored — wiwi re-numbers every
frame it emits, so the upstream's id is not the client's `Last-Event-ID`).
The unframed branch additionally requires the line to *look* like a
payload (it starts with `{` or `[`), so no unknown field line can be
mistaken for one. The envelope providers' shape is unchanged.

**Regression:** `tests/test_fix_round165.py` —
`test_parser_does_not_emit_an_id_line_as_its_own_data_event`,
`test_parser_still_accepts_a_bare_json_object_per_line`, and
`test_an_upstream_id_line_does_not_cost_the_client_its_answer` (the
end-to-end case, with the fixture corrected to one JSON document per
frame).

### 503. The reconnect-replay response omits `x-accel-buffering: no`, so a proxy buffers the replayed stream into one burst

**Severity:** 🟠 High · **Status: fixed** — round 165 (`tests/test_fix_round165.py`)

**Where:** `wiwi/server/app.py:2422-2428` (the replay `StreamingResponse`),
versus `wiwi/server/app.py:2557-2563` (the live inference response) and
`wiwi/server/app.py:3683` (the admin stream).

**Trigger:** any client that reconnects with `x-wiwi-stream-id` behind a
buffering reverse proxy (nginx, ALB, Cloudflare).

**Cause.** The live path sets `x-accel-buffering: no` — with the comment
"nginx and friends buffer an SSE body unless told not to, which turns a
stream into one burst at the end and can trip a proxy write timeout on a
long turn" — and the admin stream sets it too. The **replay** path builds
its `StreamingResponse` with a separate header dict that does not include
it. The header is the only signal the proxy has: `Cache-Control: no-cache`
does not disable proxy buffering.

**Why it matters more on replay than on a live stream.** A live stream
emits frames as they are produced, so a buffering proxy still forwards
everything promptly *if the turn is short*; the burst appears at the end.
A replay is a different shape: for an in-flight stream the replay **tails**
the journal, and the tail loop yields frames as they are appended — possibly
one frame every few seconds for the life of the original turn. A buffering
proxy holds all of it until the response closes, which is exactly the
"reconnect appears hung, then dumps everything at once" symptom the live
path's header was added to prevent. The client asked to resume and gets
silence followed by a burst, or a proxy write timeout.

**Probe** (executed, real app, reconnect with `x-wiwi-stream-id`):

```
LIVE   x-accel-buffering = no
REPLAY headers:
    cache-control = no-cache
    x-wiwi-request-id = 2a367662d0eb4b7a
    x-wiwi-stream-replay = d7a0a6f290a04f9a
    content-type = text/event-stream; charset=utf-8
    x-wiwi-latency-ms = 0.8
                                      <- no x-accel-buffering
```

**Fix:** add `"x-accel-buffering": "no"` to the replay response's
headers. The three SSE responses should carry the same set — the header is
about the transport, not about the route.

**Regression:** `tests/test_fix_round165.py` —
`test_the_replay_response_carries_the_anti_buffering_header` (the live and
replay responses carry the same anti-buffering header).

### 504. The replay tail loop sends no keep-alive, so a reconnecting client sits in silence for the whole journal TTL

**Severity:** 🟡 Medium · **Status: fixed** — round 165 (`tests/test_fix_round165.py`)

**Where:** `wiwi/server/app.py:2386-2402` (`_replay_iter`'s tail loop).

**Trigger:** a reconnect to a stream that is still running in this process,
on a turn whose upstream is slow.

**Cause.** The tail loop polls the journal every 50 ms and yields the
frames it finds. When the original turn is between chunks — the long
thinking phase, a slow tool call — there is nothing to yield, so the replay
connection emits **nothing at all**: no frame, no comment, no ping. The loop
runs until the journal's done record lands or the TTL expires
(`stream_journal_ttl_s`, default 600 s).

**Why it matters.** The live path has a keep-alive for exactly this
situation: the gateway emits an SSE keep-alive frame when the upstream goes
quiet for `stream_ping_interval_s` (default 15 s), so an idle proxy/ALB
does not reap the connection mid-turn. The replay path has no equivalent,
so the reconnecting client — which is *by definition* waiting on someone
else's turn — is the one connection with no protection. An idle proxy
reaps it, the client reconnects, and the cycle repeats.

**Probe** (executed, real app, an active journal with no done record):
the replay delivered its two buffered frames within 0.03 s and then emitted
**nothing** for the rest of the observation window, while the tail loop was
still open.

**Fix:** emit an SSE comment (`: keep-alive\n\n`) from the tail loop
on the same cadence as the live path's ping. A comment is ignored by every
conforming SSE parser, so it is safe on any dialect — unlike the live
path's ``ping``, which is Anthropic's *named* event and does not exist on
the other three surfaces.

**Regression:** `tests/test_fix_round165.py` —
`test_a_tailing_replay_emits_a_keepalive` (a tailing replay emits a
keep-alive within the ping interval of going idle).

---

## ✅ Fixed — round 163: the replay cursor's unit (2026-10-19)

A sweep of the SSE **replay** path — the journal (`wiwi/streaming/tape_store.py`),
the writer (`wiwi/server/app.py:_stream_response` / `_emit`), the wire format
(`wiwi/streaming/sse.py`) and the reconnect documentation — against the contract
in `docs/API_REFERENCE.md` §"Streaming headers & reconnect".

**Method.** The real journal and the real app (`create_app` + a `respx`-mocked
upstream) driven through a reconnect: the first stream captured frame by frame,
then a second request with `x-wiwi-stream-id` + `Last-Event-ID` compared
against what the client had already received. Seven regressions in
`tests/test_fix_round163.py`; all seven fail against the pre-fix tree.

### 493. The replay cursor counted frames while the journal counted chunks, so a reconnect re-delivered the frames before the cursor

**Severity:** 🟠 High · **Status: fixed** — round 163 (`tests/test_fix_round163.py`)

**Where:** `wiwi/server/app.py:2781-2806` (`_emit`), `wiwi/streaming/tape_store.py`
(`_encode_record`, `append`, `read_after`, `JournalTail.read_next`).

**Trigger:** any reconnect whose cursor lands *inside* a chunk that carries
several SSE frames — which the encoders produce routinely: chat's
`final_frame()` is the finish chunk **plus** the usage chunk (with
`stream_options.include_usage`), Anthropic's is `content_block_stop` **plus**
`message_delta`, Responses' `_completed()` is several frames.

**Cause.** #283 gave every sub-frame its own SSE `id:`, so the client's
`Last-Event-ID` names a single **frame**. But the journal wrote **one record
per chunk**, keyed by the chunk's *last* frame id, and `read_after` filtered
`seq > cursor` — a **chunk** comparison. A cursor landing mid-chunk therefore
re-delivered the chunk from its first frame: the client received a frame it
had already applied.

#283's fix note asserted the two sides agreed — "the journal records the same
numbering, so `Last-Event-ID` means one thing to both sides" (entry #283
below). That claim was never checked, and it is false: the wire numbers
frames, the journal numbers chunks. #283's own trigger — "the client's
connection dropping between sub-frames" — was fixed on the wire side only, and
the same scenario now *duplicates* frames instead of losing them.

**Probe** (executed, real app, chat + `include_usage`, `stream_event_ids: true`):

```
ORIGINAL STREAM            RECONNECT with Last-Event-ID: 4
  frame0 ['id: 1']           frame0 ['id: 4']   <- the client already had this
  frame1 ['id: 2']           frame1 ['id: 5']
  frame2 ['id: 3']           frame2 ['id: 6']
  frame3 ['id: 4']  <- cursor
  frame4 ['id: 5']
  frame5 ['id: 6']
```

**Fix:** the sequence counter now counts **frames**, the journal record gains
the chunk's first-frame number (`"fs"`), and both readers slice a chunk at
frame granularity against the cursor (`_frames_after` in `tape_store.py`).
A record written before the field existed reads `fs = seq` (a single frame).

**Regression:** `tests/test_fix_round163.py` —
`test_journal_replay_slices_a_multi_frame_chunk`,
`test_journal_replay_keeps_a_whole_chunk_when_the_cursor_is_below_it`,
`test_reconnect_does_not_re_deliver_a_frame_the_client_already_had`.

### 494. `stream_event_ids` defaulted off while the journal defaulted on, so the client could not express a cursor at all

**Severity:** 🟠 High · **Status: fixed** — round 163 (`tests/test_fix_round163.py`)

**Where:** `wiwi/config.py:332-339` (`RouterSettings.stream_event_ids`),
`wiwi/server/app.py:2500` (the consumer), `wiwi/server/app.py:2291-2297`
(the replay gate).

**Trigger:** every reconnect in the shipped configuration.

**Cause.** `stream_journal_enabled` defaults to `true` and
`stream_event_ids` defaulted to `false`. With no `id:` line on the wire the
client cannot name the frame it stopped at, so `replay_after` stayed `0` and
`read_after(replay_id, 0)` re-served the **whole** response — the client
applied the content it already had a second time. The documentation promised
the opposite: "a client reconnecting with `x-wiwi-stream-id` +
`Last-Event-ID` replays missed frames" (`docs/API_REFERENCE.md`,
`docs/STREAMING.md`).

**Probe** (executed, real app, default config):

```
=== ORIGINAL STREAM (default config) ===   role / "Hello" / " world" / finish / [DONE]
-> any 'id:' line?  False

=== RECONNECT with x-wiwi-stream-id, NO Last-Event-ID ===
    role / "Hello" / " world" / finish / [DONE]   <- the whole stream again
-> upstream calls: 1                              <- no double billing, full duplication
```

**Fix:** `stream_event_ids` now defaults to `true` (~8 bytes a frame). The
journal was always on; this makes its cursor reachable. `docs/CONFIG.md`,
`README.md` and the pinned default in `tests/test_streaming_improvements.py`
were updated with it.

**Regression:** `tests/test_fix_round163.py` —
`test_event_ids_are_on_by_default`,
`test_default_config_emits_a_cursor_on_every_frame`,
`test_reconnect_with_a_cursor_replays_only_the_missed_frames`.

### 495. The API reference called the request id a field on every SSE frame

**Severity:** ⚪ Low (documentation) · **Status: fixed** — round 163

**Where:** `docs/API_REFERENCE.md` §"Streaming headers & reconnect".

**Cause.** The row read "`x-wiwi-request-id` | Request ID, echoed on every SSE
frame's `id:` field". It is a **response header** (`wiwi/server/app.py:2269`);
the frame `id:` field carries a monotonic per-frame sequence number, and only
when `stream_event_ids` is on. The row now says what the header is, and the
`Last-Event-ID` row says what the cursor is.

**Regression:** `tests/test_fix_round163.py` —
`test_api_reference_does_not_call_the_request_id_a_frame_id`.

### Also noted, not fixed (no defect, but dead weight)

`wiwi/streaming/sse.py`: `sse_frame`'s `event_id` parameter is never passed by
any caller — the `id:` line is injected by `_inject_id` in `server/app.py`
instead, so two conventions exist for one thing; and `iter_sse_events` has no
caller at all (not even a test). `StreamTape.replay` has no production caller
either, which `docs/STREAMING.md` already states honestly.

---

## ✅ Fixed — round 162: the delta / chunk flow (2026-10-19)

A sweep of the streaming delta path — the wire encoders
(`wiwi/wire/openai_chat.py`, `openai_responses.py`, `anthropic_messages.py`,
`openai_completions.py`), the pump (`wiwi/core/gateway.py`), the coalescer
(`wiwi/streaming/coalesce.py`) and the streaming adapters — against the
contract in `wiwi/streaming/deltas.py`.

**Method.** Adversarial delta sequences fed through every encoder and every
adapter decoder; end-to-end replays through `create_app` with a `respx`-mocked
upstream for the findings that need the *client-visible* behaviour rather than
an adapter-level guess. Thirteen defects were found and fixed, with 30
regressions in `tests/test_fix_round162.py`.

**Fixed in round 162:**

| # | Severity | Summary |
|---|---|---|
| 350 | 🔴 Critical | `previous_response_id` dropped the entire user-side history — three chained `/v1/responses` turns reached the model as `assistant:A2 \| user:Q3`, with Q1 and Q2 gone. The store recorded both halves; only the output was read |
| 382 | 🔴 Critical | `router_settings` resilience knobs accepted negative values — `num_retries: -1` made `range(num_retries + 1)` empty, so every request was a 503. Now negative is rejected; **zero is not** (it is a legitimate "try once") |
| 420 | 🟠 High | `[DONE]` on an `anthropic`-typed upstream was dropped, so a relay that normalises to OpenAI shape produced a mid-stream `error` frame AND failed a healthy deployment. The decoder now has the terminator arm its OpenAI-wire siblings had |
| 347 | 🟠 High | The 402 budget refusal leaked the rate-limit slot it had reserved — in the GLOBAL scope, so three refusals from a capped key locked an unrelated uncapped key out with a 429 |
| 348 | 🟠 High | The lifespan teardown was twelve unguarded `await X.stop()` calls: the first to raise skipped every later one and the engine disposal, so `shutdown_event` was never set and seven workers were orphaned |
| 353 | 🟠 High | `audit_logs` was written on every admin mutation and never pruned — no age prune, no cap, no rollup. Now swept alongside `request_logs` |
| 354 | 🟠 High | The retention sweep had no cross-process lock, so two gateway processes swept the same table and double-counted the rollup. Now takes a database-level lock |
| 355 | 🟠 High | A Responses `reasoning` item replayed with no `encrypted_content` — the field did not exist anywhere in the tree, so Codex's turn-2 history was rejected. Now carried end to end through the IR and the delta |
| 349 | 🟡 Medium | `/metrics` returned the SPA's `index.html` (HTTP 200) when Prometheus was disabled — the default. The route is now always registered and answers a truthful 404 when the exporter is off |
| 356 | 🟡 Medium | A tool item's `item_id` used the IR stream index, so the streamed and sync ids disagreed for the same turn. Now keyed on the output index, like the sync path |
| 357 | 🟡 Medium | `ToolCallClose` out of open order misordered `response.completed.output` — the contract permits batch closes in any order, so close order cannot be display order |
| 358 | 🟡 Medium | `stop_reason: context_window_exceeded` reported `status: "completed"`, removing the client's only auto-compact signal. Now `incomplete`, like `length` and `content_filter` |
| 386 | 🟡 Medium | Gemini's `(cand.get("content") or {}).get("parts")` gate raised `AttributeError` on a truthy non-dict `content` — the `or {}` default fires only on a falsy value |

**Also re-verified fixed** (the status marker was stale): **#419** — a float
`content_block_delta.index` is normalized to its integer block rather than
coerced to `0`, so the args fragment reaches the right tool call.

**Also checked and found already handled** (no new entry needed — the code is
correct, and any report claiming otherwise would be a false positive): the
interleaved text/thinking buffer in all three streaming encoders (text
arriving while a tool call is open is deferred, never dropped, and flushed
only once the *last* sibling tool closes); parallel tool-call index routing in
the Anthropic and Responses encoders; the coalescer's `max_ms` deadline
(`flush_due` is polled against the wall clock, not against upstream traffic);
the loop detector's period/repeat arithmetic; the `_saw_tail` exactly-once
guard in the Gemini adapter; the Anthropic decoder's `null` `partial_json`
coercion; the NIM adapter's args-before-id synthesize branch (#143) and its
reused-index close (#196); the OpenAI adapter's in-band error frame (#212);
the Anthropic encoder's deferred-buffer cap (#219).

---

## ✅ Fixed — round 161: the /console/settings Import merge (2026-10-19)

Found by *driving* the merge against a live gateway and then reading the
database back — export from one instance, merge into another, then read the
target's tables. Not by reading the code. The pattern is identical in all
five: the merge answers `200` with a per-section count, the console renders
"merged N …", and the value is either gone on the next restart or never
worked in the first place.

Covered by `tests/test_fix_round161.py` (9 tests; all 9 failed before the
fix, each for its own reason).

### 488. An imported model alias was applied in memory and never written to the database

**Severity:** 🟠 High (an alias the operator merged is gone on the next restart, and the console reported the merge as successful) · **Status: fixed** — round 161 (`tests/test_fix_round161.py`)
**Files:** `wiwi/server/backup.py` (`_apply_aliases` — pre-fix it merged the overlay into `router.settings.model_group_alias` and returned), compared against `wiwi/server/app.py:5110-5127` (`admin_update_aliases`, which persists the same overlay)

`_apply_aliases` applied the imported overlay to the live router and stopped
there. Nothing called `config_store.set_setting("model_group_alias", …)`, so
the alias lived exactly as long as the process: the next startup re-read the
`settings` table, found no overlay row, and the alias was gone.

`POST /admin/aliases` persists the overlay, so the two writers of one
feature disagreed about whether an alias survives a restart — and the import
path was the one that did not.

**Probe** (executed — real export → merge → new `AppState` over the same DB):

```
merge: 200 {'aliases': 1, …}
settings rows: []                     ← the overlay was never written
after restart: {}                     ← the alias is gone
```

**Fix:** `_apply_aliases` now persists the overlay. The persisted value is
the **overlay**, not the merged map — the merged map also contains the
YAML-defined aliases, and writing those would pin aliases the operator never
created. The overlay is recomputed as `diff(live vs YAML base)` by the new
`_alias_overlay` helper, mirroring the admin route: a YAML alias the import
removed is a `None` tombstone, a YAML alias it restored is its value.

### 489. An imported virtual key's `owner_id` pointed at the source's user id, orphaning the key

**Severity:** 🔴 Critical (a key that authenticates on the source is **dead** on the target, while the merge reports it as applied) · **Status: fixed** — round 161 (`tests/test_fix_round161.py`)
**Files:** `wiwi/server/backup.py` (`_apply_users` — it preserves the target's id for an existing username; `_apply_vkeys` — pre-fix it wrote the document's `owner_id` verbatim), `wiwi/auth/service.py:386-397` (`_lookup_db`'s `EXISTS` subquery, which fails CLOSED on a dangling owner)

Users are keyed by `username`, and the document carries the **source's** user
ids. The target may already have that username under a different id.
`_apply_users` handles this correctly — an existing username keeps the
target's id — but `_apply_vkeys` wrote the source's `owner_id` unchanged, so
the key referenced a `users` row that does not exist here.

The auth check fails closed on that: `EXISTS (SELECT 1 FROM users u WHERE
u.id = v.owner_id AND …)` is false for a missing owner, so the key is
rejected. A tenant's key that worked on the source gateway was dead on the
target — and `vkeys: 1` in the response said it had been restored.

**Probe** (executed — source and target both have `alice`, different ids):

```
source: user=u21b88a807cc0b500  key authenticates=True
target: user=u230d0ff3f4dced12
target vkeys: [('playground', 'u230d0ff3f4dced12'), ('playground', 'u21b88a807cc0b500')]
ORPHANED keys: [('playground', 'u21b88a807cc0b500')]
target: source key authenticates = False
```

**Fix:** `_apply_users` now returns the source→target id mapping, and
`_apply_vkeys` re-points `owner_id` through it. An owner the mapping does
not cover and that does not exist on the target is dropped rather than left
dangling — an un-owned key still authenticates (`auth/service.py` exempts
`owner_id IS NULL`), while a dangling one does not.

### 490. A secret-less backup overwrote a live `cline_oauth:*` record with the redacted placeholder

**Severity:** 🟠 High (restoring a backup without secrets disconnected the target's working Cline accounts) · **Status: fixed** — round 161 (`tests/test_fix_round161.py`)
**Files:** `wiwi/server/backup.py` (`_apply_settings` — pre-fix it wrote every setting verbatim; `_redact_oauth`, whose docstring already promised the correct behaviour)

The export redacts credential-bearing settings and marks the result
`{"redacted": True}` (plus a `connected` flag). `_redact_oauth`'s own
docstring says import "skips a redacted record rather than writing the
placeholder over a live token" — but nothing skipped it. `_apply_settings`
wrote the redacted record verbatim, so the target's working refresh token
was replaced by a record carrying no token at all.

**Probe** (executed — target holds `LIVE-TOKEN`, the file carries the
redacted placeholder):

```
exported: {'connected': True, 'expires_at': 1.0, 'redacted': True}
target after merge: {'connected': True, 'expires_at': 1.0, 'redacted': True}
```

**Fix:** `_apply_settings` skips a record marked `redacted` (new
`_is_redacted_setting` helper) and logs `backup_setting_redacted_skipped`. A
redacted record also carries no information worth storing — every field but
the marker was stripped — so writing it would replace a real record with an
empty shell even where the target had none.

### 491. A provider key imported from a secret-less backup was never persisted

**Severity:** 🟠 High (the provider silently loses a member of its key pool on the next restart, and the operator's weight edit goes with it) · **Status: fixed** — round 161 (`tests/test_fix_round161.py`)
**Files:** `wiwi/server/backup.py` (`_apply_keys` — pre-fix it wrote the DB row only `if k["secret"]`), `wiwi/server/config_store.py` (the new `upsert_key_routing`)

The DB row was written only when the document carried a secret. The
rationale was sound — a backup exported without secrets must not blank a
working credential — but it also meant a key the target did **not** have was
never created: it existed in memory with an empty secret and was gone on the
next restart, and its `weight`/`enabled` edits were lost with it. The
console reported the key as merged.

**Probe** (executed — source exports without secrets, target merges):

```
merge: 200 {'providers': 1, 'keys': 1, …}
provider_keys rows: []
in-memory keys of pX: [('k1', '')]
-- restart --
pX keys after restart: []
```

**Fix:** the distinction that matters is *does the target already have this
key*, not *does the document carry a secret*. A key the target lacks is now
created (with the empty secret the document implies); an existing key keeps
its credential and only its routing fields move. The latter needed a new
`ConfigStore.upsert_key_routing` — `add_key` takes the secret as a value
and overwrites it, and `update_key` raises `ConfigStoreNotFound` when the
row is missing, which is the normal case for a YAML-defined provider (the
AUDIT #319 trap).

### 492. A virtual key whose id collided with a live row was dropped but counted as applied

**Severity:** 🟡 Medium (the console reports a virtual key that is not in the database) · **Status: fixed** — round 161 (`tests/test_fix_round161.py`)
**Files:** `wiwi/server/backup.py` (`_apply_vkeys` — pre-fix the insert was `INSERT OR IGNORE` / `ON CONFLICT (id) DO NOTHING` and the counter incremented regardless)

`vkeys.id` is the primary key, so a document whose key id collides with a
live row made the insert a no-op rather than an error — and the counter
said "applied" anyway. The colliding row is a *different* key, so it must
not be clobbered either, but the imported key was simply not restored.

**Probe** (executed — the target holds a different key under the same id):

```
merge result: {…, 'vkeys': 1}
target vkeys: [('kc729c5487712ae41', 'target-key-collision')]
```

**Fix:** the collision is detected before the insert and the import is
**re-keyed** under a fresh id (`bk<hash prefix>`), so the key is restored,
the pre-existing row is untouched, and the applied count matches what is
on disk. Logged as `backup_vkey_id_collision_rekeyed`.

---

## ✅ Fixed — round 160: WorkBuddy refused every Codex / Claude Code request (2026-10-18)

Reported verbatim from a Claude Code session routed at a `workbuddy`
deployment::

    API Error: 400 {"error":{"message":"WorkBuddy AI: Illegal API invocation
    from an unapproved channel — WorkBuddy refuses chats from Codex and
    Claude Code (their system prompt); use it from Hermes, OpenCode or Pi, or
    add another provider to this group","type":"invalid_request_error"}}

Two independent defects, either of which alone produces that failure. 17
regressions in `tests/test_fix_round160.py`.

### 487. The fingerprint rewrite covered one sentence out of seven, and the 400 it caused was terminal

**Severity:** 🟠 High (the provider is unusable from the two clients it is most likely to be bought for, and the gateway had no way to route around it) · **Status: fixed** — round 160 (`tests/test_fix_round160.py`)
**Files:** `wiwi/providers/workbuddy_adapter.py` (`_SANITIZE_FEATURES` :53, `_IDENTITY_REWRITES` :84, `_sanitize_text` :110 — pre-fix the rewrite was one `_REWRITES` entry and the gate listed four literals), `wiwi/providers/base.py` (`_CHANNEL_REFUSAL_MARKERS` :242, the 400 branch :339 in `_classify_provider_status`)

`_sanitize_text` exists to break the *verbatim template match* the upstream
moderation runs against an unapproved client's system prompt. Pre-fix it rewrote
exactly one string — `"You are Claude Code, Anthropic's official CLI for
Claude."` — while the detection gate `_has_fingerprint` listed four literals of
which only one was a Claude Code identity marker. Everything else the clients
actually send went through byte-identical, which is precisely the request the
upstream refuses.

Measured with `strings` over the clients installed on this machine (Claude Code
2.1.288, Codex 0.160.0) rather than written from memory, seven identity strings
reach the wire and only the first was rewritten: the plain CLI opener; the
Agent-SDK opener (`…running within the Claude Agent SDK.`); the subagent and
file-search specialist openers (`You are an agent for Claude Code, …` /
`You are a file search specialist for Claude Code, …`); the personality-prompt
opener (`You are an interactive CLI tool that helps users with software
engineering tasks.`); the self-description (`Claude Code is an agentic coding
CLI based on the Anthropic API.`); and Codex's `OpenAI's command-line coding
agent`.

The fix keeps the ported list's *shape* — it is a verbatim-template denylist,
so a list is what the upstream gate keys on — but derives each entry from the
**identity clause** instead of one frozen sentence, so a wording drift in a
client release no longer re-opens the hole. Two properties of that change are
load-bearing:

- **Gate and rewrite table must agree.** A marker with no matching rewrite, or a
  rewrite with no matching marker, passes the text through untouched — the
  pre-fix state, and it is silent in both directions. The personality-prompt
  and agentic-CLI prompts name the product nowhere, so they are matched on
  their own clause (`You are an interactive CLI tool`, `is an agentic coding
  CLI`) rather than on `Claude Code`. `test_every_client_identity_prompt_is_neutralised`
  asserts *both* halves — `_has_fingerprint(prompt)` and
  `_sanitize_text(prompt) != prompt` — precisely because the first caught the
  mismatch the second would have missed. Each replacement alters the smallest
  span that makes the text differ: the upstream needs it changed, not replaced,
  and the model still needs its own instructions intact.
- **The classifier branch is keyed on the vendor's wording, not the provider
  name.** `unapproved channel` / `illegal api invocation` is a phrase a second
  vendor can reuse; keying on `workbuddy` would have left the next one broken.

Second defect: even with a clean request shape, an unapproved-channel refusal is
per-*deployment*, so another deployment in the same model group can serve the
very same body. `_classify_provider_status` mapped a bare 400 to a
**non-retryable** `invalid_request_error`, and the router re-raises a
non-retryable error *before* it drains its fallback queue — so the upstream's
own advice ("add another provider to this group") was impossible to follow. A
channel refusal is a *shape* refusal, the same class as the 403 entitlement
branch above it, and must fail over. It is marked `permission_error` rather
than `authentication_error` so `status_for_key_pool` leaves the key alone —
nothing is wrong with the credential — and the upstream wording is preserved
verbatim in the message, because it is the only evidence of what was objected
to (`docs/UPSTREAM_DIAGNOSIS.md`, step 0). The branch sits *after* the
context-window check, so a 400 that is both a channel refusal and a
too-long-context keeps its own more specific `context_window_exceeded`.

## ✅ Fixed — round 159: WorkBuddy accounts could not be created without an out-of-band auth file (2026-10-18)

Reported as "the WorkBuddy login URL is wrong". It was not wrong — there was no
login URL. The `workbuddy` provider had no browser-login flow at all, so the only
way to create an account was `/import` with a `workbuddy2api`-style `auths/` JSON
that the operator had to obtain from a *different* client, on another machine.
`cline` (`build_auth_url`) and `hermes` (`request_device_code`) both ship a login
flow; WorkBuddy was the one OAuth-ish provider that did not. 20 regressions in
`tests/test_fix_round159.py`.

### 485. The `workbuddy` provider shipped no browser-login flow, so `authUrl` had nowhere to come from

**Severity:** 🟠 High (a fresh operator cannot connect the provider from wiwi at all — the credential must be harvested out-of-band first) · **Status: fixed** — round 159 (`tests/test_fix_round159.py`)
**Files:** `wiwi/providers/workbuddy_auth.py` (`accepted_auth_url` :372, `start_login` :425, `poll_login` :508), `wiwi/server/app.py` (`/admin/workbuddy/login/start` :6921, `/admin/workbuddy/login/poll` :6975), `web/src/api/client.ts` (`workbuddyLoginStart` :621, `workbuddyLoginPoll` :628), `web/src/api/types.ts` (login types :440), `web/src/pages/WorkBuddyAccounts.tsx` (`LoginDialog` :206)

`workbuddy_auth.py` could already *refresh* a WorkBuddy credential and could
parse one, but nothing in it could *obtain* one. There was no `start_login`, no
`poll_login`, no auth-URL allowlist, and no admin route — the three legs that
Cline and Hermes each own. The operator-visible symptom is a `docs_url`
(`wiwi/router/router.py`, `https://www.codebuddy.cn`) that looks like a login
link in the provider row but is the CN *web* Origin, not something a CLI login
can be driven against.

The flow was found by probing the live upstream, not by reading the client's
source: `POST {base}/v2/plugin/auth/state?platform=CLI` mints a `state` and
returns the `authUrl` the operator must open; `GET {base}/v2/plugin/auth/token?state=…`
yields the tokens once approved (and answers HTTP **200** with
`{"code":11217,"msg":"11217:login ing..."}` while still pending — a business
code inside a 200, the same envelope quirk the chat leg has);
`GET {base}/v2/plugin/login/account?state=…` with the access token as bearer
gives uid/nickname/enterpriseId. Verified live in both realms: the login page is
on the **API host itself** — `https://copilot.tencent.com/login?platform=CLI&state=…`
(CN) and `https://www.workbuddy.ai/login?platform=CLI&state=…` (global) — so
`www.codebuddy.cn` is never the URL to hand an operator.

Three properties of the fix are load-bearing, because each is somewhere the
naive version would have gone wrong:

- **The `authUrl` is relayed from the upstream, never composed.** It carries the
  upstream's own `state`, and a pinned string breaks the day Tencent moves the
  page. `accepted_auth_url` host-allowlists the answer (`copilot.tencent.com`,
  `www.workbuddy.ai`, `www.codebuddy.cn`; https only) so a hostile or misrouted
  reply cannot turn the admin console into an open redirector — the SPA opens
  whatever comes back.
- **The SPA polls with a wiwi-side token, not the upstream `state`.** The
  upstream identifier is the one the token leg authenticates with, so it stays
  server-side; the pending session lives in the shared `oauth_pending` store
  (renamed from `cline_pending` in round 180, #424) as
  `{"kind": "workbuddy_login", "provider", "realm", "state", "created_at",
  "ttl_s"}` and is told apart from the Cline/Hermes entries by `"kind"`.
- **A poll that finds the login still pending does not consume the session**
  (almost every first poll lands before the human has typed a password), while
  success and every terminal error do — and the provider *type* is checked
  before the single-use token leg is spent, so polling into a provider that can
  never accept the key does not burn a real login. The account is created by
  `/login/poll`, not by `/login/start`: a login nobody approves must leave no
  trace.

**Consequence:** an operator now connects WorkBuddy from the console the way
they connect Cline and Hermes, with no out-of-band file.

### 486. Every `.admin-btn` / `.admin-input` in the console renders under the binding 44 px touch target

**Severity:** 🟡 Medium (a binding house rule is violated console-wide; cost is mis-taps on a phone, not data loss) · **Status: fixed** — round 180 (`web/src/styles.css`, `web/src/components/Layout.tsx`)
**Files:** `web/src/styles.css` (`.admin-input` :452 `padding: 8px 12px` → ~38 px, `.admin-btn` :580 `padding: 7px 14px` → ~36 px); unfixed examples in the app shell around `web/src/pages/WorkBuddyAccounts.tsx` (`Close sidebar` 36×36, sidebar nav 237×42, `Sign out` 40×32, `Open sidebar` 40×40)

Measured, not eyeballed, on the running console at 375×812 with
`getBoundingClientRect()`: the two shared classes land at ~36 px and ~38 px,
against the UI/UX Universal Compatibility Rule's ≥44×44 px. The house remedy
already exists and is what `.admin-number-stepper button` uses
(`min-height: 44px`, `web/src/styles.css`), and call-site `min-h-11` is the same
idea applied from Tailwind — 80 call sites already do this. The round-159
surface was brought to ≥44 px with call-site `min-h-11` (see 485's files); the
app shell around it was deliberately left alone, because raising the base
classes changes every admin page's density and that is a console-wide call, not
a side effect of one page.

**Fix sketch:** add `min-height: 44px` to `.admin-btn` and `.admin-input`
mediately in `@media (pointer: coarse)` so mouse density is unchanged on
desktop, or sweep call sites to `min-h-11`; then re-measure the shell.

## ✅ Fixed — round 158: an inbound-surface audit sweep across eight divergences (2026-10-18)

Found while auditing each inbound surface and each path of a surface against its
siblings for missing logic. Every defect here has the same shape: two surfaces,
or two paths of one surface, disagreeing about the same event — so which dialect
the caller happened to use decided what they were told. 57 regressions in
`tests/test_fix_round158.py`.

### 477. A content-filtered turn announced `response.completed` around a payload that said `"status": "incomplete"`

**Severity:** 🟠 High (a client switching on the event type never learns the answer was cut) · **Status: fixed** — round 158 (`tests/test_fix_round158.py`)
**Files:** `wiwi/wire/openai_responses.py` (`_INCOMPLETE_REASONS` :469, `_response_obj` :490, `ResponsesStreamEncoder._completed` :1037)

`_response_obj` decides the payload's `status` from `_INCOMPLETE_REASONS` —
`{"length": "max_output_tokens", "content_filter": "content_filter"}`. The
streaming terminal *event* keyed off one entry of that table instead:

```python
incomplete = self._stop == "length"
```

An OpenAI upstream that content-filters a turn reports
`finish_reason: "content_filter"`, so wiwi emitted `response.completed` wrapping
`"status": "incomplete"` with `incomplete_details: {"reason": "content_filter"}`
— the two copies of one turn contradicting each other inside one frame. The
non-streaming encoder consults the table and described the same upstream answer
correctly, so the same turn was reported correctly or incorrectly depending only
on `"stream": true`.

**Consequence:** the OpenAI SDK and Codex CLI branch on the event type, so a
filtered answer arrived as a clean completion; only a client that *also* read the
payload's `status` could tell. `length` was handled correctly, which is exactly
why the gap survived an audit of the same function.

**Fix:** the event type consults the same table the payload does —
`incomplete = self._stop in _INCOMPLETE_REASONS` — so the two cannot disagree for
any stop the table knows. A stop the table has never seen still completes, which
preserves the old behaviour for reasons wiwi does not classify.

### 478. A mid-stream failure lost its classification on the OpenAI-shaped surfaces

**Severity:** 🟠 High (a mid-stream 429/529/timeout reached an OpenAI client as an opaque `api_error` while the identical failure reached Claude Code correctly classified) · **Status: fixed** — round 158
**Files:** `wiwi/streaming/deltas.py:147` (new `stream_error_type`), `wiwi/wire/openai_chat.py:510`, `wiwi/wire/openai_completions.py:190`, `wiwi/wire/openai_responses.py:1003`, `wiwi/wire/anthropic_messages.py:1238,1257`

A mid-stream `error` frame carries no HTTP status, so the classification a client
retries on has to be recovered from the `StreamError` itself. The Anthropic
encoder has done that since AUDIT #156; both OpenAI-shaped stream encoders and
the Responses encoder's `response.failed` payload hardcoded one constant:

```python
err = {"error": {"message": d.message, "type": "api_error"}}
```

The classification was present in the IR (`StreamError.kind`/`.etype`/`.status`)
and thrown away at exactly the surfaces whose sync `error_body` renders the same
`etype` verbatim — so the OpenAI dialect disagreed with itself between its
streaming and non-streaming paths, and with the Anthropic dialect about one
upstream event.

**Consequence:** an OpenAI-surface client could not distinguish "slow down" from
"something is broken", so it failed fast on a transient upstream 429/529 instead
of backing off, while the same upstream failure told Claude Code what to do.

**Fix:** one classifier beside the delta it interprets —
`streaming.deltas.stream_error_type` — which returns the vendor's own type when
the upstream named one, else the observed status, else the failure kind, in the
vocabulary `providers/base.error_from_provider_status` already puts on
`WiwiError.etype`: 529 → `overloaded_error`, 429 → `rate_limit_error`, 504 or
`kind == "timeout"` → `timeout`, 408/500/502/503 → `api_connection_error`, else
`api_error`. Each dialect spells the result in its own vocabulary — the Anthropic
encoder maps `timeout` onto its `timeout_error` through `_ANTHROPIC_STREAM_TYPES`
(:1257) — and the OpenAI surfaces pass it straight into `error.type`/`error.code`,
exactly what their sync `error_body` does with the same etype.

### 479. An Anthropic server-tool `id` that is not a string reached the IR, and a fallback result named a call it did not ship

**Severity:** 🟡 Medium (a 400 on replay, blamed on a field the caller never wrote) · **Status: fixed** — round 158
**Files:** `wiwi/wire/anthropic_messages.py:175-196` (decode), `:1129-1143` (encoder)

Two halves of one identity contract, both on the server-tool path.

**Decode.** `id=b.get("id", "")` defaults only a MISSING key: an explicit JSON
`null`, a number, a list all passed through untouched into `ToolUsePart.id`, so
replay re-emitted them (`"None"`, `123`) on a `*_tool_result` pair naming nothing.
The client-dispatched `tool_use` arm one branch above already refuses a non-string
id with `DialectError` (AUDIT #368), which is why this defect could only live on
the `server_tool_use`/`mcp_tool_use` side — the two spellings share one arm and
had no such guard. The regression test initially targeted the arm that was
already safe and so could not have caught it.

**Encode.** `_take_server_call` (:804) falls back to the lone buffered call when a
result names an id the adapter never saw, specifically so the result is not
emitted alone and unpaired — but the result block then shipped carrying the
upstream's unrecognized id while the call shipped its real one. Two ids for one
pair is precisely the unpaired-on-replay shape the buffering exists to prevent.

**Fix:** guard the decode read like `name` one line up (`id=raw_id if
isinstance(raw_id, str) else ""`), and rewrite the fallback result's `tool_use_id`
to the call it is actually paired with. Both the block type (`mcp_tool_use` must
go back as `mcp_tool_use`) and the builtin tag survive the guard.

### 480. The stored copy of a streamed hosted search claimed an empty query

**Severity:** 🟡 Medium (a client continuing by `previous_response_id` reads back a different turn than the one it saw streamed) · **Status: fixed** — round 158
**Files:** `wiwi/server/app.py:2022` (`_streamed_response_payload`), `wiwi/wire/openai_responses.py:572` (`_builtin_query`), `:683-696` (`_close_tool`)

A streamed hosted call reaches the client through the encoder's `_close_tool`,
which extracts the query from the accumulated raw argument fragments with
`_builtin_query`. The stored copy is rebuilt *after* the stream from the same
accumulators and read a key no accumulator ever writes:

```python
out.append(orp._builtin_call_item(tc["id"], tc.get("query", ""), tc["builtin"]))
```

The accumulator records only the raw fragments (`_args`), so `tc.get("query", "")`
always stored `""` — the stored item claimed a hosted search had run with an empty
query, in the same response whose live SSE frame had carried the real one. The
extraction existed in the very module the function was calling into.

**Fix:** extract from the same fragments with the same helper —
`orp._builtin_query("".join(tc.get("_args") or []))` — so the stored turn and the
streamed one are the same turn.

### 481. One coincidental match aborted a healthy stream at a small `stream_loop_limit`

**Severity:** 🟠 High (a healthy answer truncated mid-stream) · **Status: fixed** — round 158
**Files:** `wiwi/streaming/loopdetect.py:53` (`MIN_LOOP_REPEATS`), `:106` (`_threshold`)

`_threshold(period)` is `max(1, limit - period)` — the repeat count that makes a
window of `limit` chunks exactly periodic, which is the condition the original
full-window scan raised on. At the documented knob `stream_loop_limit: 33` a
period of 32 gives `max(1, 1) = 1`: a SINGLE matching lag. Two chunks that merely
coincide 32 apart are ordinary prose — a sentence reused a paragraph later — and
the stream was aborted there.

Dormant at the shipped default of 100, where every period ≤ `MAX_LOOP_PERIOD` (32)
needs 68+ repeats, but the knob is documented as deciding *how many repeats* are
required, and a smaller one silently converted it into "a coincidence is enough".

**Fix:** floor at `MIN_LOOP_REPEATS = 3`. Three repeats means the periodic pattern
has been traversed four times, which is the shape a real loop shows. A 3-chunk
cycle repeated to the threshold still fires, and a period at or above the limit
still requires three confirming repetitions rather than one.

### 482. Closing a half-written token lost every argument in the object

**Severity:** 🟠 High (the tool ran with no arguments at the boundaries that occur most) · **Status: fixed** — round 158
**Files:** `wiwi/streaming/partial_json.py` (`_scan_json` :37, `_PARTIAL_LITERALS` :89, `_DANGLING_NUMBER` :99, `_trim_incomplete_value` :107, `_repair_truncated_json` :173)

The repair appended the closing characters the nesting owed and nothing else,
which is sufficient only when the cut landed *between* tokens. A real token stream
cuts wherever the output limit or a client disconnect lands: mid-separator, mid-key,
mid-literal, mid-number. Closing those produced `{"a": 1,}` / `{"b": }` /
`{"a": tru}` / `{"n": 1.}` — none of which is JSON — so `json.loads` raised in
every consumer and the ENTIRE argument object was lost, at exactly the boundaries
that happen most, while `parse_partial` (the streamed partial view, no production
consumer — see `docs/STREAMING.md:114`) showed the values fine.

**Fix:** a token-aware trim before the closers are appended. A half-written literal
is *completed* — the prefix is unambiguous (`t`, `tr`, `tru`, … `nul`) and the value
is real. A trailing separator, a lone sign, a dangling key, and a number tail that
cannot terminate (`.`, `e`, `e+`) are *dropped*, because there is no value to
invent. A cut inside a *key* drops the pair rather than closing a key that can never
be given a value.

Two precision notes, both measured and both now pinned by tests:

- The number trimmer matches the WHOLE token, not the offending `.e+`. A first form
  using a lookbehind removed only those characters, repairing `{"n": 1.` to
  `{"n": 1}` — asserting a number the model never wrote. That matters because the
  repaired text is what the call is executed with: `core/gateway.py`'s
  `_apply_event` (:871) assigns `json.loads(...)` to `tc.args`, as do the
  decode-side repairs in `wiwi/wire/openai_chat.py:182` and
  `wiwi/wire/openai_responses.py:47`.
- The literal completion must be tried AFTER the number tail, because an exponent's
  `e` is a letter: `{"n": 1e` would otherwise look like a half-written literal and
  be returned as-is.

**One pre-existing test retargeted, not deleted:**
`tests/test_fix_round20.py::test_stream_pump_validates_tool_args_on_close` fed the
validator `{"broken":` — reachable as "unrepairable" only because the repair could
not handle a dangling key, i.e. the very incapacity this entry fixes. Under the fix
that input is the honest `{}`, and `wiwi/streaming/validation.py`'s documented
contract is that a repairable payload is not a violation, so the case moved to
`{"broken": }` (a key with no value that the *model* wrote, in full). The case still
pins the same behaviour — unrepairable args are flagged, the stream does not fail —
and truncated-but-repairable args are now covered as "no violation, whole object
preserved" in `tests/test_fix_round158.py`.

### 483. `StreamTape` evicted the entry it had just appended

**Severity:** 🟠 High (a refused resume silently became a partial one) · **Status: fixed** — round 158
**Files:** `wiwi/streaming/resume.py:203` (`StreamTape._evict`)

```python
while self._bytes > self._max_bytes and self._entries:
```

A single delta larger than the tape's whole budget was appended and then evicted by
its own append. The tape was left EMPTY: `bytes == 0`, no head, and `head_evicted`
answers False for the vacuous empty case *by design*. AUDIT #68's continuation guard
reads exactly that predicate, so a one-delta oversized stream turned a refused resume
into a silently partial one — the fallback rebuilt the assistant prefix from an empty
tape and replayed nothing, while the client already had the content.

**Fix:** never evict the last entry — `len(self._entries) > 1`. Keeping the oversized
newest entry costs nothing (it is in memory regardless) and keeps the tape's answer to
"did anything get dropped?" honest. `replay` still returns it, so a continuation is
complete rather than empty.

### 484. The sync Anthropic error body flattened a timeout the streaming path kept

**Severity:** 🟡 Medium (Claude Code cannot back off from a failure reported as `api_error`) · **Status: fixed** — round 158
**Files:** `wiwi/wire/anthropic_messages.py:1278` (`_ANTHROPIC_TYPE_BY_STATUS`), `:1329` (etype table), `:1335` (status fallback)

The mirror of #476, one line down the same table. `providers/base.py` returns
`WiwiError(504, "timeout", …)`; the etype table mapped wiwi's `timeout` onto
`api_error`, while the streaming twin (`_stream_error_type`) mapped the identical
failure onto `timeout_error`. And `_ANTHROPIC_TYPE_BY_STATUS` had no 504 entry, so a
504 whose etype was a name the table had never seen fell to `api_error` too. The two
paths of one surface therefore disagreed about the same failure, with the streaming
one right — the same asymmetry as #476, where the streaming encoder was again the one
that had it right.

**Fix:** the etype table maps `timeout` → `timeout_error`, and 504 gets its own entry
in the status fallback (which is only consulted when the etype table has no entry at
all). The table still takes precedence over the status, deliberately: it carries
wiwi's vocabulary with no one-status-one-answer mapping — `budget_exceeded` arrives as
a 402 and must stay `permission_error`.

---

## ✅ Fixed — rounds 157: an upstream 529 reached the caller as an opaque `api_error` (2026-10-18)

Found while auditing the Anthropic Messages surface for missing logic. The
streaming encoder had this right all along, which is what made the
pre-first-byte path the anomalous one.

### 476. A 529 overload was classified as `502 api_connection_error`, then rendered as `api_error` — so Claude Code had nothing to back off from

**Severity:** 🟠 High (a saturated upstream is presented to the client as an
opaque failure it will not intelligently retry; every Anthropic overload
degrades to "something is broken") · **Status: fixed** — round 157
(`tests/test_fix_round157.py`)
**Files:** `wiwi/providers/base.py` (`_classify_provider_status`), `wiwi/wire/anthropic_messages.py` (`error_body`)

Two defects stacked to produce one symptom.

`_classify_provider_status` folded 529 into the same arm as 500/502/503 and
returned `WiwiError(502, "api_connection_error", …)`. 529 is Anthropic's own
overload signal, not a generic 5xx, and the rewrite destroyed **both** the
status and the vendor's own type. The vendor type did survive on
`err.upstream_type`, but nothing on the error path reads it — grepping showed
it appears only in log lines (`gateway.py:175`, `router.py:1598`,
`recovery.py:556`), never in a response body.

`error_body` then accepted a `status` argument and never read it, deciding the
whole rendering from a static `etype` table. So even once the classifier
stopped collapsing 529, the encoder had no route to `overloaded_error` for any
`etype` it had not already enumerated.

Measured end-to-end through the ASGI app before the fix:

```
UPSTREAM 529 overloaded_error  -> HTTP 502 type=api_error
UPSTREAM 429 rate_limit_error  -> HTTP 429 type=rate_limit_error
```

The 429 arm worked because `error_from_provider_status` passes 429 through
untouched — which is exactly why the defect survived: only the status the
classifier happened to preserve was renderable.

**Consequence:** Claude Code branches on `error.type == "overloaded_error"` to
back off against a saturated upstream. Flattened to `api_error`, a transient
overload is indistinguishable from a broken deployment, so the client fails
fast instead of retrying into a recovered upstream. `billing_error`,
`invalid_tool_use` and `request_too_large` were flattened the same way.

**Fix:** 529 gets its own arm returning `WiwiError(529, "overloaded_error", …)`,
still `retryable=True` — 529 was already in `RETRYABLE_STATUS`, so
`status_for_key_pool` and the deployment-cooldown paths keep behaving exactly
as they did when it arrived rewritten as 502. `error_body` gained a
`_ANTHROPIC_TYPE_BY_STATUS` fallback consulted only when the etype table has no
entry, with the etype table deliberately taking precedence: it carries wiwi's
own vocabulary (`budget_exceeded` arrives as a 402 and must stay
`permission_error`), which has no one-status-one-answer mapping. The vendor
types Claude Code switches on are now in the table rather than flattened.

**Not verified against the live API:** that the current Anthropic upstream
actually rejects these strings. The asymmetry is internal and provable without
it — `_stream_error_type` classified the identical failures correctly all along,
so the two surfaces simply disagreed.


## ✅ Fixed — rounds 155/156: the TPS meter, and the tool-call churn behind it (2026-10-16)

Found while auditing the generation-throughput meter. #474 is the metric bug
proper; #473 is the adapter defect that was quietly carrying the metric, and
which also broke the Anthropic wire for a whole class of upstreams.

### 474. The TPS window excluded the tool-call argument tokens it was divided by

**Severity:** 🟠 High (a reported rate no generation can produce, growing without
bound) · **Status: fixed** — round 155 (`tests/test_fix_round155.py`)
**Files:** `wiwi/core/gateway.py` (`build_log_event`, and the `last_token_at`
stamps in the stream consumer)

`build_log_event` computes `tps = completion_tokens / (last_token_at -
first_token_at)`. The numerator is the provider's count, which includes every
token of a tool call's arguments; the denominator was stamped only on
`TextDelta`/`ThinkingDelta` (round 133) and `ToolCallOpen`. Round 133 left
`ToolCallArgsDelta` out of the bound on the reasoning that each args frame is an
SSE frame rather than a model token — which is true of the *window* and false of
the *ratio*. A stream whose bulk of output is one large tool call therefore
divided 8000 tokens by the few hundred milliseconds up to the open and reported
~26,000 tok/s for a request whose own round-trip rate was ~11,000 tok/s, growing
with args size while the window did not.

**Fix:** stamp `last_token_at` on `ToolCallArgsDelta` too, so the window closes on
the last content-bearing delta of any kind. `ToolCallClose` stays out: providers
batch sibling closes at `finish_reason`, so stamping them would measure the
batch rather than the generation.

### 473. An echoed tool-call `id` closed and reopened the call on every arguments chunk

**Severity:** 🔴 Critical on the Anthropic surface (duplicate `tool_use` ids in
one assistant message are rejected; a lenient client executes the tool N times) ·
**Status: fixed** — round 156 (`tests/test_fix_round156.py`)
**Files:** `wiwi/providers/openai_adapter.py` (`decode_stream_event`, plus the new
`_tool_call_continues` helper and `_open_tool_ids`), `openrouter_adapter.py` and
`nim_adapter.py` (the same branch, both subclasses of `OpenAIAdapter`)

Each OpenAI-shaped adapter treated *any* chunk carrying an `id` on an
already-open index as a new tool call: flush the deferred Open, emit
`ToolCallClose`, then a fresh `ToolCallOpen`, then the arguments. OpenAI itself
sends `id`+`name` only on a call's first chunk, so the branch is reached only by
the OpenAI-compatible upstreams that echo both on *every* arguments chunk.

**Probe** (executed — real `decode_stream_event`, 3 args chunks, one call):

```
OpenAIAdapter      echoed id: opens=3 closes=3
OpenAIAdapter   OpenAI shape: opens=1 closes=1
```

Through `AnthropicStreamEncoder` that became three `content_block_start` events
carrying `id: "call_1"` each — the same id three times in one assistant message,
each block receiving a copy of the arguments:

```
start 1 tool_use call_1 write_file
start 2 tool_use call_1 write_file
start 3 tool_use call_1 write_file
```

It also masked #474: the reopen churn stamped `last_token_at` on every fragment,
so for these providers the window ended in the right place for the wrong reason
— turn the round-155 stamp off and the numbers still looked right, which is why
the churn survived a round of metric work.

**Fix:** `OpenAIAdapter._tool_call_continues(idx, call_id)` — a chunk whose id
matches the call already open on that index continues it. Only a *different* id,
or an index that is not open, takes the close/reopen path, so genuine index reuse
(`test_a_different_id_on_the_same_index_still_starts_a_new_call`) and parallel
indices are unchanged. `_open_tool_ids` is swept wherever `_synthesized_opens`
is, including the two adapter-specific overrides and `reset()` — a leftover id
would make the first chunk of the next request on a reused singleton look like a
continuation.

### 414 / #464 — `test_realtime`'s connection assertion raced the fake server

**Status: fixed** — the assertion, not the relay. `test_realtime.py:208`
cancelled the relay immediately and then required `upstream.connections == 1`, a
value that only held when the relay's handshake had not yet been scheduled on the
fake server's own loop — so it failed about half the time, in isolation as well as
under the full suite. Measured 3/6 passes with the round-156 changes, 4/6 without,
which is the same coin flip in both directions. The test now waits for the relay's
connection to be accepted (`_wait_for_connections`) and asserts the true count of
2; 6/6 passes.

---

## ✅ Fixed — round 154: five `wiwi/wire/` defects (2026-10-16)

Found by a source review of the wire codecs, then reproduced against the real
encoders before fixing. Four were already on this register (#374, #375, #376,
#398) and one is new (#472). All five are covered by
`tests/test_fix_round154.py` — every test in that file failed before its fix.

### 472. The Anthropic `document` decode arm forwards a non-string `title`/`context` into the IR

**Severity:** 🟡 Medium · **Status: fixed** — round 154 (`tests/test_fix_round154.py::test_anthropic_document_non_str_title_and_context_are_dropped`)
**Files:** `wiwi/wire/anthropic_messages.py` (the `btype == "document"` arms, both
the `base64` and `url` branches)

`DocumentPart.name`/`.context` are typed `str | None`, but decode passed
`b.get("title")` / `b.get("context")` straight through, and
`anthropic_adapter.py:359-362` copies them verbatim onto the outbound block. A
client sending `{"type": "document", "source": {...}, "title": 5}` got an
upstream 400 naming a field that looked fine to the caller, and because the junk
was stored in the IR it was re-sent on every later turn of that conversation.

**Probe** (executed — real `decode_request` + real `AnthropicAdapter.encode_request`):

```
IR DocumentPart.name=5  context=['a','b']
OUTBOUND: {'type':'document', ..., 'title': 5, 'context': ['a','b']}
```

This is the Anthropic twin of **#184**: the Responses codec guards the identical
field that way (`openai_responses.py:83-85`), and the `media_type` read two lines
above in this same arm is guarded too. Only this arm was missed.

**Fix:** `isinstance(..., str)` on both reads; junk becomes absent (`None`)
rather than `""`, matching its sibling guards. Legitimate metadata is unaffected
(`test_anthropic_document_real_title_and_context_survive`), and both
`DocumentPart` arms are covered (`test_anthropic_document_url_arm_is_guarded_too`).

---

## ✅ Fixed — round 153: the Hermes worker cache (2026-10-15)

Found by diffing the three OAuth refreshers' `_worker_for` fallbacks against
each other after a report of bugs in the refresh logic. One entry, #471.

### 471. Hermes' `_worker_for` fallback still keys a strong-ref cache on `id(state)` — every AppState leaks, and a recycled id inherits a dead worker's locks

**Severity:** 🟠 High · **Status: fixed** — round 153 (`tests/test_fix_round153.py`; found and fixed 2026-10-15)
**Files:** `wiwi/providers/hermes_auto_refresh.py:366` (`_workers`), `:367-381` (`_worker_for`)

AUDIT #451 moved Cline and WorkBuddy's module-global `_workers` fallback off
raw `id(state)` — Cline to a `weakref`-carrying entry that prunes dead
referents, WorkBuddy to a cache anchored **on the state object** itself. Both
call out why: `id()` is not unique for a process lifetime, so a re-created
`AppState` could be handed a worker bound to a *dead* state and inherit its
locks, dead circuits and stale generation counters.

**Hermes was never given the same treatment** — it was a third OAuth provider
added after #451's fix and kept the original shape:

```python
_workers: dict[int, HermesAutoRefresh] = {}

def _worker_for(state):
    shared = getattr(state, "hermes_refresh", None)
    if shared is not None:
        return shared
    return _workers.setdefault(id(state), HermesAutoRefresh(state))
```

Two distinct failures, both confirmed by execution:

1. **Permanent leak, worse than a cache miss.** `HermesAutoRefresh.__init__`
   stores `self._state = state`, and `_workers` holds the worker, so the module
   global roots every `AppState` that ever took the fallback path. Dropping all
   local references and running `gc.collect()` left every state alive:

   ```
   states still alive after dropping every local ref: [True, True, True]
   _workers[0x774e421852e0] -> worker._state = FakeState (alive=True)
   ```

   The comment on the fallback ("tests, pre-lifespan use") is exactly where this
   bites: the test suite builds a fresh mini-state per test file, so a long
   run accumulates one pinned worker — with its config-store engine reference —
   per state.

2. **Recycled-id inheritance.** An entry is reused purely on numeric address
   equality, with no check that the referent is still the same object, so a new
   state landing on a dead state's address inherits that worker's `_locks`,
   `_generations` and `CircuitBreaker._dead`. For Hermes this is the worst
   instance of the three: a poisoned `mark_dead(("provider", "acct"))` would
   make a *legitimately reconnected* account read as permanently unreloginable,
   and the inherited generation counter is the counter that guards the
   single-use refresh grant against double presentation (AUDIT #169).

Fix mirrors WorkBuddy's (the stronger of the two): anchor the fallback worker on
the state object itself, so it becomes unreachable — and collectable — the
moment the state dies. No module global, no `id()` at all, so neither failure
mode has anything to attach to. `AppState` is a normal class, so `setattr`
succeeds; the `AttributeError` guard keeps a slotted/frozen test stand-in on
the old behaviour (a fresh worker per call), which is a correctness-neutral
fallback rather than a cache bug.

---

## ✅ Fixed — usage and stats export: the window, the file, and the picker (2026-10-15)

### 467. `_read_all` dropped the `log_days` bound after page 1 — a `logs`/`rollups` export shipped rows older than the window it reported

**Severity:** 🟡 Medium (the export *adds* rows rather than losing them, so nothing looks short; the file misstates its own window) · **Status: fixed** — 2026-10-15, `tests/test_fix_round152.py`
**Files:** `wiwi/server/backup.py:708-710` (`_read_all`'s WHERE builder), reached from `_export_logs` (`wiwi/server/backup.py:580`, `ts`/`log_days`) and `_export_rollups` (`:596`, `bucket_ts`/`log_days`)
**Trigger:** any `GET /admin/backup?sections=logs` or `?sections=rollups` whose `log_days` admits enough rows to fill the first page (2 000, `_PAGE`). The predicate `ts >= :cutoff` was appended only when `first` was true; pages 2..n ran `WHERE id > :cursor` alone. Verified against a live SQLite gateway at this commit: 3 000 in-window rows followed by 1 000 rows 30 days old, requested with `log_days=1`, exported **4 000 rows — every one of the 1 000 ancient rows included**. Page 1 has to come back *full* for this to show, which is why it survived #454's fix: the pagination bug and this one hid behind each other, and fixing the cursor alone only made the leak reachable.
**Why it matters beyond a row count:** `rollups` is the surviving usage record once `rollup_and_prune` trims `request_logs` under the retention window, so a window that leaks and does not say so misstates spend on exactly the long-lived instances the section exists for — and the file is the artefact an operator restores onto a second gateway.
**Fix:** the age bound is appended on every page, not just the first. The keyset cursor is `id`-ordered, so a later page is not a continuation of the rows the cutoff admitted — the predicate has to be re-applied with the cursor, not instead of it. Regression: `tests/test_fix_round152.py` (2 tests, one per time column, both asserting on exported rows rather than on the SQL).

### 468. The console's "Export CSV" wrote the newest page and called it the history

**Severity:** 🟡 Medium (silent under-export of usage data; the file looks complete) · **Status: fixed** — 2026-10-15
**Files:** `web/src/pages/Analytics.tsx` (`exportCsv`, which built the CSV from the `logs` the table renders), `web/src/api/client.ts:362` (`getRequestLogs` — one page, `limit=10000`), `wiwi/server/app.py:3351` (`/admin/logs/requests`, page ceiling 50 000)
**Trigger:** click **Export CSV** on Analytics on any gateway holding more than 10 000 request-log rows. The table renders `logsQuery` — a single `/admin/logs/requests?limit=10000` response, capped server-side — and the export serialised exactly that array. So the file contained the newest ≤10 000 rows and nothing said the rest existed; the same one-page array backs the Usage page's aggregates, so the two pages agreed with each other while both disagreed with the database.
**Fix:** `getAllRequestLogs` (`web/src/api/client.ts`) walks `offset` until a short page ends the walk and reports `truncated` when a 200 000-row ceiling stops it; Analytics and Usage both build their CSV from it, apply the same filters the tables apply, and surface a warning instead of a "done" when the file is partial. The CSV header moved to `web/src/lib/csv.ts` so the two pages cannot drift into two dialects of the same file. Note the ceiling is a deliberate bound, not a fix for it: a gateway past 200 000 rows gets a labelled partial file (the export stops and says so) rather than an unbounded fetch.

### 470. A control placed inside `Field` takes the whole field's text as its accessible name

**Severity:** 🟡 Medium (screen-reader users hear a paragraph where a button label belongs; WCAG 2.4.6 / 4.1.2) · **Status: fixed for the new Backup controls** — 2026-10-15; the `Field` primitive itself is unchanged, so every other caller still has it.
**Files:** `web/src/components/ui.tsx:197-209` (`Field` renders `<label className="block">` around `children` and `hint`), `web/src/pages/Backup.tsx` (the "All data — usage & stats" / "Configuration only" buttons, which now sit beside the `Field` rather than inside it)
**Trigger:** render any focusable control inside `Field`. A `<label>` is an ancestor of the control, so it contributes to that control's accessible name — for the backup picker, Playwright computed the button's name as `"Sections Configuration only Providers Provider keys … Usage and stats are NOT in this file: …"`. Focus it with a screen reader (or `getByRole("button", {name: /All data/})`, which returns **0** matches) and the label is unreadable even though the visible text is short. Found by driving the real console, not by reading the module: the button looked right in a screenshot.
**Fix (local):** the quick-select buttons render as siblings of the `Field`, so their accessible names are their own text again — `getByRole("button", {name: /All data/})` now resolves. **The general fix is still open:** `Field` should not wrap arbitrary children in a `<label>` — a `<div>` with `<span id>` + `aria-labelledby` gives the same visual and label semantics without adopting whatever the caller puts inside. Left open deliberately: `Field` is used across the console, and changing it is a sweep of its own, not a rider on this one.

---

## ✅ Fixed — Zen relayed "Client metadata is not supported." for the caller's `metadata` (2026-10-15)

### 466. `metadata` from the inbound request was forwarded to Zen, whose upstreams refuse it

**Severity:** 🟠 High (every chat/messages-route request carrying a client `metadata` block answers `400`) · **Status: fixed** — 2026-10-15
**Files:** `wiwi/providers/opencode_adapter.py` — `_drop_client_metadata()`, called from `encode_request()`
**Trigger:** an inbound request whose body carries a top-level `metadata` (a Claude Code `metadata.user_id`, an OpenAI-Chat client's `metadata`, an Anthropic-dialect `metadata`), routed to an `opencode` deployment on the chat or messages route. Both inbound codecs capture the field into `req.extras` and both delegates forward it (`openai_adapter._STANDARD`, `anthropic_adapter._ANTHROPIC_STANDARD`), so it reached Zen's `/chat/completions` and `/messages`. The provider behind Zen answers `400` with the code `unsupported_metadata` and Console relays it as `Upstream request failed: [unsupported_metadata] Client metadata is not supported.` — the bracketed code is the *upstream's*, preserved because Console drops codes outside its own allow-list (opencode's client pins that format in `packages/ai/test/provider-error.test.ts`) — and wiwi's 400 branch renders it as `<provider account>: <message>`, which is where the `io:`-style prefix in the report comes from.
**Fix:** drop a caller-supplied `metadata` in the adapter before the body is sent, and log `opencode_client_metadata_dropped` (debug) with the route. An operator who wants the field on the wire declares it with `extra_body: {metadata: …}`, which is left alone. Regression: `tests/test_fix_round151.py` (11 tests).
**Caveat — this is not a fix for every model on the account.** The Responses route never forwarded extras, so a failure on the one responses-route free model (`muse-spark-*-contributor-free`) has a different cause; that path needs the raw upstream type before it is touched.
**Checked and *not* the cause, so it does not need re-investigating:** the client fingerprint. Re-derived from the client's own source (`sst/opencode` @ `4397ee4`, 2026-10-15): the `ses_` + 12 hex + 14 base62 id, `x-opencode-project: global`, the `read`/`shell` decoy pair and the absent `x-opencode-request` all still match the current client, and every servable free model answers `200` to the adapter's exact shape — swept 2026-10-15 through the gateway as well as directly, with a caller `metadata` block on every request: 9 of the 11 current free models on all three inbound dialects, while `jev-1.13-free` is the catalog's System One model with no completion wire and `step-5-preview-free` intermittently answers `429 … Endpoint is unavailable.` from its own upstream. Matrix in `docs/OPENCODE.md`.

---

## ✅ Fixed — provider delete cascades its deployments (2026-10-14)

An account feeding one or more model groups could not be deleted at all.

### 462. `DELETE /admin/providers/{name}` refused any account a group referenced, with no way to detach a YAML deployment

**Severity:** 🟠 High · **Status: fixed** — 2026-10-14
**Files:** `wiwi/server/app.py` (`admin_delete_provider`), `web/src/pages/ProviderDetail.tsx`, `docs/API_REFERENCE.md`

The handler scanned every group for a deployment whose provider was this
account and answered `409 invalid_request_error`: `provider still referenced by
groups: stealth/missingno, stepfun/step-5-preview:free — remove those
deployments first`. Two problems made that a dead end rather than a guard:

- A deployment from the YAML `model_list` has no `deployments` row, so the
  delete-deployment route's `detached=1` tombstone could not be written for it
  from the provider-detail UI's per-deployment detach button — only the Combos
  page could. Nothing in the message said so, so the operator had no hint
  which page to visit.
- Even after removing every deployment one at a time, a YAML-sourced one
  returned on the next restart, putting the operator back where they started.

So the account and all its keys were effectively undeletable. Verified live
against a server with two YAML groups pointing at `p1`; the delete now returns
`200` with a `detached_deployments` list, both groups disappear from
`/admin/models`, `combo` (the untouched `p2` group) still routes, and after a
restart the deleted groups stay gone (clean `404` on the old model name).

The fix drops the 409 and cascades: the handler collects the referencing
`(group, model_id)` triples, then after the existing DB cascade deletes the
in-memory deployments (emptied groups are popped, matching
`admin_delete_deployment`), rebuilds the cross-provider pools, and writes one
`delete_deployment` tombstone per triple so YAML-sourced deployments do not
resurrect (AUDIT #297). Order matters: `ConfigStore.delete_provider` already
DELETEs this account's `deployments` rows, so the tombstones must be written
after it or they would be deleted with the rest.

**Coverage:** `tests/test_provider_admin.py` — cascade detaches and reports;
a sibling group is untouched; a two-provider group loses only the deleted
account's entry; a cascaded YAML deployment survives a restart.

---

## ✅ Fixed — round 136: multi-account Hermes (2026-10-13)

Found while investigating how to beat the per-account ~50 RPM cap. Each Nous
Portal account is a separate OAuth identity with its own rotating (single-use)
refresh token and its own rate-limit bucket, so wiwi now stores **one account
per provider pool key** as a self-contained ``hermes:<json>`` credential blob
and lets the existing key pool + smooth weighted round-robin spread traffic
across them. Entry #427.

### 427. Hermes was single-account — one refresh token per provider, and a rotation clobbered every pool key

**Severity:** 🟠 High · **Status: fixed** — round 136 (`tests/test_fix_round136.py`; fixed 2026-10-13)
**Files:** `wiwi/providers/hermes_oauth.py` (`encode_credential`/`decode_credential`/`credential_bearer`), `wiwi/providers/hermes_adapter.py`, `wiwi/providers/hermes_auto_refresh.py`, `wiwi/server/app.py` (device-code/poll/status/refresh/disconnect)

Before this, Hermes held one OAuth identity per provider: a single
``hermes_oauth:<provider>`` settings record with one ``refresh_token``, and
``_update_secret`` wrote the rotated access token to **every** pool key. Two
consequences: a second account could not be added at all, and any refresh
overwrote sibling keys with one account's token. The per-account rate limit
(~50 RPM / 50k TPM) could therefore not be worked around.

The inference endpoint contract was verified against the installed Hermes CLI
and a live call: ``agent_key`` is the **same JWT** as ``access_token``
(``_set_nous_agent_key_from_invoke_jwt`` sets ``agent_key = access_token``), and
``GET /models`` returns 200 with the access token alone — so wiwi's existing
single-token bearer is correct and no ``agent_key`` handling is needed. The gap
was purely account multiplicity.

Fix mirrors the WorkBuddy per-key-credential precedent. Each pool key's secret
is a ``hermes:<json>`` blob (access + refresh + expiry + email label); the
adapter resolves the bearer from it (a bare ``hermes:<token>`` still works for
paste-a-token). ``HermesAutoRefresh`` now keys its locks, generation counters
and circuit breakers on ``(provider, key_label)`` and reads/writes the live
pool key, so each account rotates independently and a rotation never touches a
sibling. The device-code ``/poll`` **appends** the approved account as a new
pool key (labelled by its JWT email, de-duplicated) instead of overwriting key
#0; ``/status`` lists every account; ``/refresh`` and ``/disconnect`` take an
optional ``key_label`` to target one account (disconnect with no label removes
the whole pool). A dead grant marks only that account's circuit dead, so the
rest of the pool keeps serving.

**Coverage:** `tests/test_fix_round136.py` — adapter bearer from blob + bare
token; encode/decode round-trip; sweeper refreshes each account on its own
due-check and leaves siblings byte-for-byte unchanged; one account's
``invalid_grant`` does not block another's refresh; WRR distributes across ≥2
account keys. `tests/test_fix_round132.py` and `test_fix_round134.py` updated to
the per-account model (connect appends a key, status reports `accounts`,
disconnect returns a count, refresh targets `key_label`).

---

## ✅ Fixed — round 134: Hermes OAuth auto-refresh (2026-10-12)

Found by comparing the three OAuth providers' refresh machinery. One entry,
#423.

### 423. Hermes has no automatic token refresh — an expired token hard-401s and retires a healthy key

**Severity:** 🟠 High · **Status: fixed** — round 134 (`tests/test_fix_round134.py`; found and fixed 2026-10-12)**Files:** (new) `wiwi/providers/hermes_auto_refresh.py`; `wiwi/server/app.py:1216` (lifespan wiring), `:1247-1260` (sweeper + 401 hook), `AppState.__init__` + teardown

Cline and WorkBuddy each refresh proactively (a background sweeper that rotates
tokens inside the lead window) **and** on demand (a gateway 401 hook). Hermes
shipped with neither: `wiwi/providers/hermes_oauth.py` exposes a working
`refresh_access_token`, and the admin API calls it from
`/admin/hermes/oauth/refresh` (`app.py:6037`), but nothing rotated a token
automatically. The lifespan block (`app.py:1216`) wires `ClineAutoRefresh`,
`WorkBuddyAutoRefresh`, their 401 hooks (`gw._on_demand_cline_refresh`,
`gw._on_demand_refresh_hooks["workbuddy"]`) — and has no Hermes equivalent.

An access token that expired — or was invalidated upstream, the way Cline
sometimes 401s a still-valid JWT (the AUDIT round-7 trigger) — mid-session
produced a hard 401 to the caller with no rotation. Under the default
`failover_mode == "any_error"` that 401 charged the key's error streak, and a
401 counts double (`router.py:on_result`), so `key_max_consecutive_fails`
(default 5) retires a healthy key after ~3 expired turns. On a Hermes pool with
one key, every request then fails with "all keys cooling".

Fix mirrors the Cline refresher exactly: a 60 s sweeper rotating tokens inside
`hermes_oauth.REFRESH_LEAD_S`, plus a 401 hook rotating unconditionally. Both
funnel through one `HermesAutoRefresh` worker (`_worker_for`) so they share one
per-provider lock and one `CircuitBreaker` — Nous returns a fresh
`refresh_token` per exchange, so a sweeper tick and a client 401 in the same
window must not both present the same token or the loser's `invalid_grant`
marks the provider dead (AUDIT #169). A dead grant (`invalid_grant` /
revoked / reused) is terminal and marks the circuit dead; no retry storm. The
synchronous `httpx.Client` in `hermes_oauth.refresh_access_token` is run via
`asyncio.to_thread` so neither the sweeper nor the 401 hook blocks the event
loop (the AUDIT #33 shape).

**Coverage:** `tests/test_fix_round134.py` — (1) a 401 from Nous triggers the
on-demand refresh and a one-shot retry, with the `hermes:`-prefixed rotated
secret landing on the pool key and the new refresh token persisted; (2) the
sweeper refreshes a token inside the lead window and an unknown expiry but
skips a far-future one and a provider with no stored refresh token; (3) a dead
grant is terminal and the 401 surfaces without a second POST.

---

## ✅ Fixed — round 127/128: Anthropic translation sweep (2026-10-11)

Found by fuzzing `wiwi/ir/` + the Anthropic codec and adapter with executed
probes rather than by reading. Four defects, all confirmed by running the real
encoder/decoder before and after the fix. Entries #415-#418.

Two of them (#417, #418) are typed-wrong-scalar leaks that survive to the
upstream; the other two (#415, #416) corrupt a *stream* mid-flight, and both
share one consequence worth naming: a `TypeError` escaping a decoder is not a
500, it is worse — the pump converts it to a `StreamError`, bills the partial,
records a fail against the deployment, and moves a **healthy** key to `cooling`.
A frame carrying no semantics penalizes a healthy credential.

### 415. Parallel tool calls emitted malformed block ordering — a text block nested inside an open tool block

**Severity:** 🔴 High · **Status: fixed** — round 127 (`tests/test_fix_round127.py`; found and fixed 2026-10-11)
**Files:** `wiwi/wire/anthropic_messages.py:1054` (`ToolCallClose` arm), `:1141-1157` (`final_frame`)

Two independent sites let buffered content drain while a parallel tool block
was still open.

The `ToolCallClose` guard tested `self._open_block != "tool"`, but
`_open_block` names only the **most recently opened** block. The moment
`_close_block(tool_index=…)` cleared it the guard passed — while sibling
indices remained registered in `self._tool_blocks`, which is the actual set of
open tool blocks:

```python
1054:  if self._open_block != "tool" and self._deferred:
1055:      out.extend(self._flush_deferred())
```

And in `final_frame`, `_flush_deferred()` ran **before** the `sorted(self._tool_blocks)`
sweep. The comment there claimed this was safe because "_flush_deferred closes
whatever it needs and the tool indices are tracked separately from _open_block"
— that separation is exactly what breaks.

**Probe** (executed, two `tool_use` blocks open with text buffered between them):

```
before: OPEN 0 tool_use / OPEN 1 tool_use / CLOSE 1 / OPEN 2 text / DELTA 2 / CLOSE 2 / CLOSE 0
after:  OPEN 0 tool_use / OPEN 1 tool_use / CLOSE 1 / CLOSE 0 / OPEN 2 text / DELTA 2 / CLOSE 2
```

Block 0's `content_block_stop` arrived **after** a whole text block had opened,
been filled and closed inside it. Anthropic content blocks are strictly
sequential, so this is SSE the API itself never emits and a strict client cannot
parse. Reproduced end-to-end through the real gateway (`POST /v1/messages`,
`stream:true`) against an Anthropic upstream that omits the parallel siblings'
`content_block_stop` — the AUDIT #111 shape, which #111's own text calls "the
live path".

**Fix:** guard on `not self._tool_blocks` (the open *set*) instead of
`_open_block != "tool"`, and move `_flush_deferred()` to after the sweep in
`final_frame`. This mirrors the Responses encoder's existing
`if not self._tools` guard (`wiwi/wire/openai_responses.py:697`). Both controls
are pinned: the single-tool case and the no-tools case are byte-identical to
their previous output.

**Why not a known finding:** #178 (fixed) was `_emit_server_call` reusing the
flushed text block's index — the same mechanism, single-tool only. #111 (fixed)
added the `final_frame` sweep, but the sweep runs *after* the flush so it never
covered this. #367 fixed this exact class in the *Responses* encoder; the
Anthropic sibling was never given it. The comment at `:1050-1053` already stated
the invariant the code did not implement.

### 416. An unhashable `tool_use_id` in a `*_tool_result` block crashed the stream pump

**Severity:** 🔴 High · **Status: fixed** — round 128 (`tests/test_fix_round128.py`; found and fixed 2026-10-11)
**Files:** `wiwi/providers/anthropic_adapter.py:788`

```python
builtin=self._tool_names_by_id.get(cb.get("tool_use_id", ""))))
```

`.get(k, default)` defaults only a **missing** key. A list/dict
`tool_use_id` was hashed straight into the dict lookup.

**Probe** (executed, `fresh_adapter("anthropic")`):

```
tool_use_id=["x"]   -> TypeError: unhashable type: 'list'
tool_use_id={"a":1} -> TypeError: unhashable type: 'dict'
tool_use_id=5       -> OK (hashable; wrong-typed but not a crash)
```

Every sibling arm in that same branch already coerces — `as_str(cb.get("id"))`
on the `tool_use` arm, `isinstance(cb.get("type"), str)` on the block type —
so this was the one unguarded read.

**Consequence, confirmed over the real app** (`LifespanManager` + `respx`, one
Anthropic SSE stream carrying text + `server_tool_use` + a
`web_search_tool_result` with `tool_use_id: ["x"]`, on `POST /v1/chat/completions`):

```
HTTP 200
  data: {... "delta":{"content":"hi"} ...}
  data: {"error":{"message":"unhashable type: 'list'","type":"api_error"}}
GET /admin/providers -> {"healthy": false, "keys":[{"status":"cooling","err_count":1}]}
```

The client got a 200, partial content and an error frame; the healthy key was
moved to `cooling`.

**Fix:** coerce with the file's existing `as_str`-style guard before the lookup
(`tid if isinstance(tid, str) else ""`).

**Why not a known finding:** #384 (below) covers the `index` reads only and does
not list this line; `_tool_names_by_id` appears nowhere else in this register.
#331 covers the *inbound codec's* `tool_use_id` — a different site, fixed in
round 118. Same defect class as #384, distinct unguarded read.

### 417. `top_k: true` passed the `isinstance(..., int)` guard and reached the upstream

**Severity:** ⚪ Low · **Status: fixed** — round 127 (`tests/test_fix_round127.py`)
**Files:** `wiwi/wire/anthropic_messages.py:461`

`isinstance(True, int)` is `True`, so the raw guard let a JSON `true` through:
IR `top_k=True`, emitted upstream as `{"top_k": true}` (both verified by
execution). The provider 400s naming a type the caller never sent.

**Fix:** `ir.coerce_int(body.get("top_k"))` — the existing helper, which
rejects bools explicitly.

**Why not a known finding:** this is an **unfixed residual of #187**, which is
marked fixed. #187's own fix sketch said `top_k=ir.coerce_int(body.get("top_k"))`;
the implemented guard was a raw `isinstance(..., int)`, so the bool case the
sketch would have closed was left open. #187 is corrected in place below.

### 418. A non-dict `tool_use.input` reached the Anthropic upstream verbatim

**Severity:** ⚪ Low · **Status: fixed** — round 127 (`tests/test_fix_round127.py`)
**Files:** `wiwi/wire/anthropic_messages.py:157` and `:170`

`args=b.get("input") or {}` catches only *falsy* values, so every truthy
non-dict was forwarded. Verified end-to-end:

| client `input` | upstream body |
|---|---|
| `"raw string"` | `{"type":"tool_use","id":"tu1","name":"f","input":"raw string"}` |
| `[1,2]` | `..."input":[1,2]}` |
| `5` | `..."input":5}` |

Anthropic's `tool_use.input` is an **object**, so each produces a structurally
invalid request rejected far from its origin. On the OpenAI path it degrades
differently: `openai_chat.py` emits `"arguments": "\"raw string\""` — a
JSON-encoded string, not an object — so the replayed history carries a tool
call no client can parse.

The identical `or {}` shape **is** guarded in `wiwi/wire/openai_chat.py:160`
(`isinstance(raw_args, dict)`) and in the provider-side decoders, so the
Anthropic inbound path was the outlier.

**Fix:** `isinstance(..., dict)` guard on both arms, mirroring `openai_chat.py:160`.

**Why not a known finding — and a correction to this register.** The
*Disproven on retest* section below asserts that `input`/`args` are "all
correctly guarded". **That is false for this codec.** That line is corrected in
place below rather than left to mislead the next agent.

**Stale statuses corrected in this round** (each re-verified by execution before
editing, per the error-discovery rule): #351, #122, #123, #126, #9, #10 were all
marked open but their code is already fixed. Their bodies are preserved and each
gained a `**Status: fixed**` line naming the guard that fixed it.

---

## ✅ Fixed — round 129: whole-gateway backup / restore (2026-10-11)

Found while building the backup module, and — for #421 — found by *driving* the
two-instance restore, which the unit tests had not done.

### 421. A restored price entry made `GET /admin/pricing` return 500

**Severity:** 🟠 High · **Status: fixed** — round 129 (`tests/test_fix_round129.py`)
**Files:** `wiwi/server/backup.py` (`_validate_prices`), consumed at `wiwi/server/app.py:4966`

The price map is rendered by `_entry_to_per_1m`, which branches on key
**presence** and then multiplies:

```python
if "cache_read_input_cost_per_token" in p:
    entry["cache_read_per_1m"] = round(p["cache_read_input_cost_per_token"] * 1_000_000, 6)
```

`validate_backup` built each parsed price by writing **every** field, filling
absent ones with an explicit `None`. `_apply_prices` merged that into
`state.cost.prices`, so the restored entry carried
`cache_read_input_cost_per_token: None` — a key that is *present*, holding a
value the renderer multiplies. Result: `TypeError: unsupported operand type(s)
for *: 'NoneType' and 'int'`, a 500 on `/admin/pricing`.

The blast radius is wider than the one endpoint. `cost.prices` is read by the
cost engine on every request, so a null-valued rate is the kind of thing that
surfaces later as a billing discrepancy rather than a stack trace. An operator
migrating a gateway would hit the console page first.

Why the surrounding code does not do this: `ConfigStore.load_prices` omits
null columns rather than emitting them (`:544-553`), and `admin_put_pricing`
only sets keys the caller actually supplied (`app.py:4951`). The restore was
the only writer that normalized "absent" to "None".

**Fix:** omit absent price fields instead of storing `None`, matching both
existing conventions. The same applied to the per-scope rates.

**How it was found — worth recording as method.** The 34 unit tests were green;
this only appeared when a *bare* gateway was restored and then walked through
the whole console (`test_a_restored_gateway_is_still_usable` now does exactly
that, hitting eight `/admin/*` reads after a restore). A restore's real test is
"is the result still a working gateway", not "does the data match".

### 420. WorkBuddy account export silently did nothing in Firefox

**Severity:** ⚪ Low · **Status: fixed** — round 129 (`web/src/api/backup.ts`)
**Files:** `web/src/pages/WorkBuddyAccounts.tsx:276-281` (pre-fix)

The export handler built an anchor, set `href`/`download`, and called
`.click()` — but never appended it to the document:

```js
const a = document.createElement("a");
a.href = url;
a.download = `workbuddy-auths-${...}.json`;
a.click();                    // <-- no appendChild, no remove
URL.revokeObjectURL(url);
```

Chrome tolerates a click on a detached element; **Firefox does not**, and
ignores it silently. The button reports success, the network tab shows
nothing, and no file appears. WorkBuddy's OAuth tokens are the one account
type an operator cannot re-derive, so a silently empty export is the worst
possible failure mode for exactly the page that holds them.

The sibling handlers were correct — `Providers.tsx` and `Analytics.tsx` both
did `appendChild` + `remove()` — so this was a hand-copied variant that lost
two lines rather than a shared bug.

**Fix:** all three now call `downloadJson` / `downloadBlob` from
`web/src/api/backup.ts`, which attaches the anchor before clicking. The
blob→anchor→revoke sequence had been duplicated verbatim across the three
pages; extracting it removed the second convention *and* the divergence that
came with it.

---

## ✅ Fixed — /console/oauth redesign (2026-10-11)

Found while redesigning the console's connected-accounts page. Entries #399-#410 were
new (verified against source, not present in this register); #411 is **still open** and
needs backend work that was deliberately out of scope for the UI change.

The page was rewritten across `web/src/pages/OAuthProviders.tsx` (page shell, account
cards, add-account dialog), two new modules `web/src/pages/oauth-shared.tsx` (connect
flow, status, callback banner) and `web/src/pages/oauth-routing.tsx` (routing + default
models), plus additive primitives in `web/src/components/ui.tsx` and `styles.css`.

### 399. Cross-account routing pool deploys to *disconnected* Cline accounts

**Severity:** 🟡 Medium (silent misconfiguration; the deployment 401s on first use) · **Status: fixed**
**Files:** `web/src/pages/OAuthProviders.tsx` (old `:739-741` unfiltered `providers` prop,
`:757-763` `createPool` loop, `:961-968` gate on `providers.length >= 2` rather than
connected count)
**Fix:** statuses hoisted to a page-level `useClineStatusIndex`; `createPool` now iterates
only connected accounts, and the helper copy names how many were skipped.

Deliberately **client-side only**. `connected: false` means only "there is no
`cline_oauth:<name>` row in the settings table", which is also true of a Cline account
provisioned from YAML or import that holds a perfectly valid key — so
`POST /admin/model-groups/{name}/deployments` (`wiwi/server/app.py:4492`) must *not*
reject such a provider. Gating it server-side would break config-only Cline deployments.

### 400. The routing-strategy toggle on the OAuth page mutated **global** router state

**Severity:** 🟠 High (an admin flipping what reads as a pool-local switch silently
re-routes every model group in the gateway) · **Status: fixed**
**Files:** old `web/src/pages/OAuthProviders.tsx:752` (`globalStrategy = modelsQ.data.strategy`),
`:772-781` (`toggleStrategy`), `:852-858` (the `Toggle`); `wiwi/server/app.py:4672-4680`
**Fix:** the toggle and its mutation are deleted. The card now renders the strategy
read-only and links to `/console/models`, which owns the control.

There is no per-group routing strategy anywhere in the system.
`admin_patch_model_group` takes a `{name:path}` parameter and then **ignores it** for the
`strategy` key, writing the single global `state.router.settings.routing_strategy`; there
is no `strategy` field on the `ModelGroup` type at all. The old badge and the old toggle
therefore agreed with each other while both lied about scope — an admin could not have
known the switch was gateway-wide. `web/src/pages/Models.tsx:477` already carries the
comment "strategy is global router state", so the duplicate control was a known hazard
rather than an oversight.

### 401. Refetch silently discarded unsaved default-model selections

**Severity:** 🟡 Medium (silent loss of admin intent) · **Status: fixed**
**Files:** old `web/src/pages/OAuthProviders.tsx:508-512` (the `useEffect` syncing `selected`
from `settingsQ.data`), triggered by the default `refetchOnWindowFocus` at `:484-487`
**Fix:** read-through-until-first-edit — `selected` is now `Set<string> | null` where `null`
means pristine, `current = selected ?? savedSet`, and resync happens only on a genuine change
in the persisted list.

The old effect keyed on `settingsQ.data` identity, which changes on **every** refetch
including the default window-focus one, so ticking models and then switching tabs discarded
the selection with no warning. The same bug class was already recorded unnumbered against
`web/src/pages/Combos.tsx`; that sibling is still unfixed.

### 402. "Save" was disabled at zero selections, and `PUT` cannot remove a default

**Severity:** 🟡 Medium (dead primary button; two same-sounding operations with different
server semantics) · **Status: fixed**
**Files:** old `web/src/pages/OAuthProviders.tsx:687-715`;
`wiwi/server/app.py:4357-4399` (PUT only *adds*, via `_apply_cline_default_models`)
vs `:4404-4451` (DELETE pops the whole `cline:<id>` group)
**Fix:** Save is now enabled whenever the list is dirty, including at zero, with an
`aria-describedby` hint and an explicit `Alert` when zero is the confusing state; the
destructive path is renamed "Remove all defaults" (see #404).

At zero selections with saved ids present the page rendered `dirty === true` next to a
permanently disabled primary button, and the only escape — "Clear all" — is an entirely
different server path. Even clicking it would not have removed the existing deployments,
because `PUT []` only persists an empty list; the UI implied otherwise.

### 403. The Cline callback banner was dropped when `cline_provider` was absent, and could not be dismissed

**Severity:** ⚪ Low (dormant path today, but it is the only UI for `/cline/oauth/callback`)
· **Status: fixed**
**Files:** old `web/src/pages/OAuthProviders.tsx:108` / `:117` (`?? ""`),
`:991-993` (`callbackResult?.provider === p.name ? … : null`), `:308-328` (no dismiss)
**Fix:** rendered once at page level with a dismiss button, so there is no key to mismatch.

Worse than hidden: `_stripClineParams` had already deleted the params from the URL before
the match was attempted, so a missing `cline_provider` destroyed the result rather than
merely failing to show it. The old code also made `cline_provider` a *routing key*, which
is what made it fragile; it is now message text only.

### 404. "Clear all" ran N destructive requests with no confirmation

**Severity:** 🟡 Medium · **Status: fixed**
**Files:** old `web/src/pages/OAuthProviders.tsx:693-706` vs `:444-465` (a single "Disconnect",
which *did* get a modal)
**Fix:** renamed "Remove all defaults", `variant="danger"`, behind a `Dialog` naming the
model count. The serialized `for … await` loop is preserved byte-identically — that is the
#209 fix and must not be "simplified" into `Promise.all`.

The inconsistency was stark: a reversible single-request action got a confirmation modal
while an irreversible multi-request one did not.

### 405. A failed Cline status poll rendered as "not connected"

**Severity:** 🟡 Medium (a gateway blip is indistinguishable from a genuine disconnect)
· **Status: fixed**
**Files:** old `web/src/pages/OAuthProviders.tsx:214-221` (no `isError` branch),
`:294-302` (badge fell through to "not connected"); same defect at
`web/src/pages/ProviderDetail.tsx:1108-1118`
**Fix:** a four-way badge — `checking…` / **`status unavailable` (red)** / `connected` /
`expires soon` / `not connected` — and a Retry control beside it.

This branch fires on a real condition, not just a theoretical one:
`GET /admin/cline/oauth/status` returns 404 for an unknown provider
(`wiwi/server/app.py:5692-5698`), so a provider removed from the router mid-poll reported
itself as "not connected" and offered a full re-connect flow.

### 406. `window.open` for the Cline login had no popup-blocker handling and no copy fallback

**Severity:** ⚪ Low · **Status: fixed**
**Files:** old `web/src/pages/OAuthProviders.tsx:239` and
`web/src/pages/ProviderDetail.tsx:1140`
**Fix:** the login URL is now an `<a target="_blank">` (`Button` gained an `href` variant)
alongside an always-visible `CopyButton`.

The call sat inside a `useMutation` `onSuccess`, i.e. after an `await`, so it was outside
the user-gesture window and routinely blocked. Note that detecting the block is not
possible: `window.open(url, "_blank", "noopener,noreferrer")` returns `null` on **success**
per spec in Chrome and Firefox, so a null-check would warn falsely every time. The fix
inverts the problem — the fallback is always present, so nothing needs detecting.

### 407. The account list load-error state offered no retry

**Severity:** 🟡 Medium (a failing 15s-polling query strands the page) · **Status: fixed**
**Files:** old `web/src/pages/OAuthProviders.tsx:970-977`
**Fix:** `Alert tone="danger"` with a Retry button. #339 added the `isError` branch but not
the recovery affordance.

### 408. Default-model picker rows were ~30px tall with hover-only feedback

**Severity:** ⚪ Low (binding UI/UX rules 2 and 3) · **Status: fixed**
**Files:** old `web/src/pages/OAuthProviders.tsx:652-669`
**Fix:** rows are `min-h-11` with `focus-within` alongside `hover`, and a 16px checkbox.
Cross-ref #261, which lists eight other files but not this one.

### 409. One shared page-level `error` string served four unrelated scopes

**Severity:** ⚪ Low · **Status: fixed**
**Files:** old `web/src/pages/OAuthProviders.tsx:917, 944, 949, 956, 966`
**Fix:** errors are now scoped and dismissible. A failure in "Remove all defaults" used to
paint a red box at the top of the page, above the header's own content, which never cleared
except by triggering another error or reloading.

`ErrorText` also gained `role="alert"` — it is used at 45 call sites and every one of them
was previously silent to assistive technology.

### 410. The page header comment promised an automatic redirect flow that cannot complete

**Severity:** ⚪ Low (documentation defect that hides a real constraint) · **Status: fixed**
**Files:** old `web/src/pages/OAuthProviders.tsx:6-8`, contradicting its own comment at
`:229-233`; `wiwi/providers/cline_oauth.py:41-45`; `REMINDERS.md:99-108`
**Fix:** the new `oauth-shared.tsx` header states plainly that no one-click path exists and
why. The UI now presents the manual three-step flow as the designed path rather than
labelling a button "Connect with Cline" and describing the manual requirement only in body
copy 350 lines below.

`build_auth_url` sends `callback_url` and `redirect_uri`, but Cline's Google OAuth ignores
both and lands the browser on `https://app.cline.bot/auth/callback?code=…`. The gateway's
`GET /cline/oauth/callback` (`wiwi/server/app.py:5610`) therefore never fires, and the
whole `auto-connect` + state-token + banner pipeline is dormant by design.

### 412. `Dialog`'s focus effect re-ran on every parent render, yanking focus to the trigger

**Severity:** 🟠 High (background polling steals focus out of whatever field is being
typed in, every 15s) · **Status: fixed**
**Files:** `web/src/components/ui.tsx` — the focus/Escape effect, previously
`useEffect(..., [props.open, props])`
**Fix:** `onClose` is now read through a ref and the effect depends on `[props.open]`
alone.

`props` is a fresh object on every render, so the effect tore down and re-ran on every
parent render — and its cleanup called `restoreTo?.focus?.()`. Every console page polls
(`getProviders` and `getModels` both at 15s on this one), so a refetch mid-typing moved
focus back to the trigger button. The same churn could capture `restoreTo` as the panel
itself rather than the original trigger, breaking focus restoration on close.

Surfaced while building the connect dialog: pasting a callback URL into step 3 while a
15s poll landed would throw the caret back to step 1. Related: #257.

### 413. `CopyButton` reported success it could not have had

**Severity:** ⚪ Low · **Status: fixed**
**Files:** `web/src/components/ui.tsx` (`CopyButton`)
**Fix:** `navigator.clipboard.writeText` is now awaited in a `try`/`catch`; a rejection
renders a distinct "Copy failed" state instead of "Copied". `CopyButton` also accepts a
`className`, which it previously ignored — callers could not size it to the 44px tap-target
floor without reimplementing it.

The clipboard API rejects on an insecure context or a denied permission. Since the whole
Cline connect flow now depends on this button as the fallback when the login link cannot be
opened, a false "Copied" would be worse than an error.

---

### 411. `/admin/cline/oauth/status` reads a settings row that a credential-provisioned account never creates
**Severity:** 🟠 High (the console reports real, credentialed accounts as disconnected) · **Status: fixed** — re-verified 2026-10-29 (round 180); the fallback exists at `app.py:6252-6279`
**Files:** `wiwi/server/app.py:5692-5715` (`cline_oauth_status`), reading
`config_store` key `cline_oauth:<provider>` minted at `:5465-5466`; credentials actually live
in `provider_keys` (written by `connect` at `:5520`)
**Fix:** have `cline_oauth_status` fall back to inspecting the account's pool keys — decode
the stored JWT's `exp` (and `email` claim) when no `cline_oauth:<name>` row exists, and
report that credential as expired rather than absent.

**Observed on a live install (2026-10-11).** Three Cline accounts each hold a real
WorkOS-issued JWT in `provider_keys`, with distinct `email`/`sub`/`sid` claims, all
`exp`ired 25–32 days earlier. The `settings` table is empty, so every status lookup misses
and all three report `{"connected": false}` — while remaining `healthy: true`, still holding
a pool key, and still serving ten live deployments across two `cline:*` routing groups.
The console has no way to express "expired credential that is still deployed", and this
entry is the reason the redesign distinguishes the states it can actually observe.

This is the one finding the redesign deliberately did **not** fix: it needs a backend change,
and the brief for that work was the UI. It is the highest-value follow-up here because no
visual fix can surface a state the API does not return.

### 414. `test_realtime` opens two relay connections under full-suite ordering

**Severity:** 🟡 Medium (the binding gate is not reproducible-green on `main`) · **Status: fixed** — round 156 (2026-10-16)
**File:** `tests/test_realtime.py:208` (`assert upstream.connections == 1`), with a
cascading teardown error at `:101`
**Trigger:** full-suite `python3 -m pytest tests/ -q`. Reproduced on 2 of 2 full runs;
`tests/test_realtime.py` passes 13/13 in isolation, and
`test_fix_round10.py::TestLoopDetector::test_per_token_cost_is_bounded` is the same
class of flake (failed run 1, passed run 2, 65/65 in isolation).
**Fix:** ~~give each `FakeRealtimeServer` a per-test unique identity (or reset
`connections` in `startup`) so an event-loop/task leaked by an earlier test cannot
reconnect to the next test's server.~~ The diagnosed cause in #464 is the one that
applied: the assertion itself, not a leaked connection. The test cancelled the relay
and then required the count to still be 1 — true only when the relay's handshake had
not been scheduled on the fake server's own loop. It now waits for the handshake
(`_wait_for_connections`) and asserts 2. 6/6 passes, full suite green.

### Verification status (important caveat)

The `/console/oauth` redesign (#399-#413) passes `npx tsc -b --force`, `npm run lint`
(0 errors), `npx vite build`, and `ruff check`. The redesigned page is a pure React
change with no test runner in this repo — `web/` has no vitest/jest/playwright and no
`*.test.*` file, so there is no automated way to assert its behaviour. The Python suite
is **not** green on `main` for reasons unrelated to this work — see #414.

**The binding "verify at 375×812 and desktop" rule has NOT been discharged.** This machine
has no headless browser: no chromium/firefox binary, no `~/.cache/ms-playwright`, and
Python `playwright`/`selenium` are absent. The acceptance criteria below are therefore
**unverified, not passing**:

- horizontal overflow at 375px
- every tap target ≥44×44 (statically: this page's own buttons pass `min-h-11`, but
  `.admin-btn` itself is ~30px and #261's other 7 call sites are still open)
- the focus ring actually painting (the `styles.css` placement fix is reasoned, not seen)
- dialog focus trap / Escape / focus restoration at runtime
- reduced-motion rendering

Also still open and relevant to this page's components: **#257** (Dialog/Drawer declare
`aria-modal` but do not trap focus) and **#377**. The `Dialog` focus-trap, Escape, and
focus-restoration code all exists and was reviewed by reading; what is missing is a way to
*run* it. Someone with a browser should walk the remaining steps of the redesign's
verification plan before this page is considered done.

---

## ✅ Fixed — round 133 (2026-10-12): tool-call-only streams reported no TPS

Found by reading the two lines that bound the TPS generation window, then
reproducing against the real gateway (`POST /v1/chat/completions`, `stream:true`,
a paced tool-call-only SSE upstream). Covered by `tests/test_fix_round133.py`.

### 422. A tool-call-only stream lost its generation-phase TPS

**Severity:** 🟠 high · **Status: fixed** — round 133
(`tests/test_fix_round133.py`)
**Files:** `wiwi/core/gateway.py:1206` (`ToolCallOpen` arm of the stream consumer)

`build_log_event` derives `stream_secs` from `last_token_at - first_token_at`,
and both were stamped in one block. `first_token_at` was set on
`TextDelta`/`ThinkingDelta`/`ToolCallOpen`, but `last_token_at` **only** on
`TextDelta`/`ThinkingDelta`. A stream whose entire output is tool calls (a
function-calling agent that never emits prose) therefore stamped
`first_token_at` on the open and never advanced the other, collapsing
`stream_secs` to `0.0`. The request reported `tps = 0.0` with a full
`tok_out`, so it read as a real measurement rather than a missing one — TTFT
survived, because the open stamp was always fine.

**Probe** (executed, two paced tool calls, 50 completion tokens, ~0.3s apart):

```
before: status=200 tok_out=50 ttft_ms=318.2 tps=0.0
after:  status=200 tok_out=50 ttft_ms=305.1 tps>0  (generation-phase)
```

The zero also fed the aggregates as an excluded sample while still counting in
`tps_sample_ratio`'s denominator, so the coverage figure understated how much
streaming traffic the TPS numbers described — the same silent-exclusion shape
#320 existed to kill.

**Fix:** stamp `last_token_at` on `ToolCallOpen` too, so the window is bounded
by the first and last content-bearing delta of any kind. Args fragments stay
out of the bound deliberately: each is one SSE frame, not one model token, so
timing them would stretch the window across transport time. `ToolCallClose` is
excluded because providers batch the closes of sibling tool calls at
`finish_reason` — stamping the batch's arrival would measure the batch, not the
generation.

**Why not a known finding:** #319-#325 (round 117) fixed the
streaming/non-streaming split and the missing denominator, and its prose
("Requests with no separable generation phase report `tps = 0.0`") reads as
though this case were intended. A tool-call-only stream *has* a separable
generation phase; it was simply never bounded. No entry mentions
`last_token_at` on the tool-call path.

**One shape is still legitimately zero, and is now pinned as such:** the OpenAI
adapter defers `ToolCallOpen` until the name is complete, which the first args
fragment signals. A tool call sent with no args is held and flushed together
with its siblings at `finish_reason`, so both bounds land in the same instant
and the 0.05s floor correctly drops it. That is a stream with no measurable
generation window, not a bug — `test_instant_tool_only_stream_still_reports_no_tps`
keeps it that way.

---

## ✅ Fixed — sweep 2026-10-06 (gateway, streaming, codecs, adapters, router, auth, server)

All thirteen findings below (#360–#372) are fixed; regressions in `tests/test_fix_round120.py`. Review of these fixes found follow-up defects, recorded open as #373–#378.

Six read-only subagent sweeps, one per subsystem. **Every finding below was
re-verified by the reviewer executing code**, not by trusting the subagent
report — two candidate findings were disproved that way and are recorded as
cleared so they are not re-filed. Numbers in "Probe" blocks are real output.

Baseline at this sweep: **2896 passed, 6 skipped**, ruff clean, `tsc -b` +
`vite build` clean.

### 360. A mid-stream resume erases the request log's entire `attempts[]` audit trail

**Severity:** 🟠 High · **Status: fixed**
**Files:** `wiwi/core/gateway.py:1026-1084` (`merge_resume_context`, whole body),
`:1345-1350` (the `finally` that calls it), `wiwi/core/gateway.py:1431-1433`
(`resume_ctx` construction)
**Fix:** fixed — `merge_resume_context` now extends `origin.attempts` with the resumed context's records. Test: `tests/test_fix_round120.py::test_merge_resume_context_carries_attempts`.

`merge_resume_context` folds a resumed attempt back into the originating
context and merges exactly three things: `usage`, `cost`, and `_stream_usage`.
It never touches `attempts` — grep over the whole function body returns **0**
occurrences of the string.

Meanwhile the originating context's `attempts` list is **empty** on this path:
the primary pump died mid-stream through `_fail_stream`, whose failure arm
(`gateway.py:1711-1745`) calls only `_note_stream_failure`. The `"ok"` record at
`:2057` is unreachable once `_fail_stream` has queued a terminal. The fallback's
`AttemptRecord` lands on `resume_ctx.attempts` and is dropped on the floor.

AUDIT #106's fix sketch (`AUDIT.md:2801`) explicitly said "merge the resume
context's usage/cost/**attempts** back into the originating `ctx`". The
attempts half was never implemented; #106 and #281 both closed without noticing,
because `tests/test_fix_round119.py:207,358` assert on `ctx.attempts` only for
**pre-content** failover, which does populate (via `execute_with_retries`).

**Trigger:** `stream_resume != "off"` (non-default) plus any mid-stream
`StreamError` after content has flowed, with a healthy fallback candidate — the
ordinary mid-stream failover the feature exists for.

**Probe** (reproduces the `tests/test_fix_round55.py` fixture with
`stream_resume="enabled"`; control is the same request with `resume="off"` and a
healthy single upstream):

```
resume="enabled":  upstream calls primary=1 fallback=1
                   ctx.attempts : []
                   LOG attempts : []
                   PROXY log saw primary? True
                   PROXY log saw fallback? True
                   LOG status   : 200  tok: 10 2

resume="off" (control):
                   ctx.attempts : [('ok', 'p', 'a')]
                   LOG attempts : [{'deployment': 'm/m', 'provider': 'p', ...}]
```

**Consequence:** a request served by **two** upstreams is persisted as if one
served it, with `status=200` and an empty attempt list. The primary's failure is
invisible in the request log, so per-deployment error rates, key `req_count`
reconciliation and "which provider actually served this" are all wrong for
exactly the requests that were hardest to serve — while the proxy log and the
provider's own billing both show two upstreams consumed tokens.

**Fix sketch:** in the same `finally`, alongside the `merge_resume_context`
loop, `ctx.attempts.extend(rctx.attempts)` — the resumed context's list is
appended after the origin's, so insertion order is already correct.
**Test:** none; belongs in the next `tests/test_fix_roundN.py`.

---

### 361. `cycle_every_n` is a no-op for any provider contributing 2+ deployments

**Severity:** 🟠 High · **Status: fixed**
**Files:** `wiwi/router/router.py:1332-1345` (the provider-consec exclusion
loop in `execute_with_retries`), interacting with `router.py:809-812`
(`pick_deployment`'s relaxed fallback)
**Fix:** fixed — the exclusion loop iterates provider names and excludes every deployment of an over-credit provider before popping. Tests: `test_cycle_every_n_*` in `tests/test_fix_round120.py`.

The round-104 fix for #305 moved the counter pop inside the loop:

```python
for d in deps:
    pname = d.provider.name
    if provider_consec.get(pname, 0) >= cycle_n:
        prefer_exclude.add(id(d))
        provider_consec.pop(pname, None)      # <-- fires on the FIRST match
```

Because the pop is inside `for d in deps`, it clears the credit on the first
matching deployment and breaks out of the effect, not the loop. Only `d0` lands
in `prefer_exclude`; `d1…` of the same provider stay eligible and win the pick.
The credit is consumed with no effect, so it never accumulates to a state that
would exclude the whole provider.

**Probe** (real `execute_with_retries` driver, `call_one` always succeeding,
pA weight 20 : pB weight 1, `cycle_every_n=2`, 200 served requests). The
guarantee is max run ≤ 2:

```
  pA deployments=1: max run =   2   {'pA': 130, 'pB': 70}   OK
  pA deployments=2: max run =  22   {'pA': 191, 'pB': 9}    VIOLATED
  pA deployments=3: max run =  42   {'pA': 195, 'pB': 5}    VIOLATED
  pA deployments=4: max run =  62   {'pA': 197, 'pB': 3}    VIOLATED
```

Deterministic across seeds. `cycle_every_n` 0/1/2/5 with 2 deployments produces
runs of 20/21/22/25 — i.e. the **WRR burst**, not the setting. Same class as
#226/#305, on the axis their tests never covered: all three #305 tests build
their config with **exactly one deployment per provider**, the only shape where
the current code is correct.

**Fix sketch:** hoist the pop out of the loop — iterate provider *names*
(`for pname in {d.provider.name for d in deps}`), and when a provider is
over-credit add `id(d)` for **all** its deployments before popping.

---

### 362. PBKDF2 login/signup runs on the event loop — a 75 ms hard stall per request

**Severity:** 🟠 High · **Status: fixed**
**Files:** `wiwi/auth/users.py:91` (`hash_password`), `:106-108`
(`verify_password`), called synchronously from `:212` (`create_user`) and
`:231` (`verify`); call sites `wiwi/server/app.py:5076` (`auth_signup`) and
`:5208` (`auth_login`)
**Fix:** fixed — `UserService.create_user`/`verify` run `hash_password`/`verify_password`/`burn_dummy_verify` via `asyncio.to_thread`. Tests: `test_password_verify_does_not_block_the_event_loop`, `test_user_service_verify_offloads_hashing`.

`PBKDF2_ITERS = 200_000` and neither call site is wrapped in
`asyncio.to_thread`. AUDIT #33 fixed exactly this shape for `estimate_tokens`
via `estimate_tokens_async`, and #105/#107 fixed blocking FS I/O and tiktoken
loading the same way — PBKDF2 is the one CPU-bound path that was missed.

**Probe** (measured; a ticker task asks for 5 ms and records how late it wakes):

```
single verify_password() wall time : 74.0 ms
worst event-loop stall during it   : 75.4 ms   (ticker asked for 5 ms)
8 concurrent verify() wall time    : 503.4 ms  (fully serialized)
worst event-loop stall             : 498.4 ms
```

**Consequence:** every other request in flight — including in-progress streaming
LLM completions — is frozen for the whole window. Both endpoints are
unauthenticated; the login throttle is 10/300 s per IP and signup 5/3600 s per
IP, so a handful of distinct source addresses sustains the stall indefinitely.
This is the CPU-exhaustion amplifier #221 identified, but as a *loop stall*
rather than a throttle race: the throttle is now correct and the stall survives
it.

**Fix sketch:** `await asyncio.to_thread(verify_password, password, row[1])` in
`UserService.verify` (and `hash_password` in `create_user`) — the async wrapper
is the established precedent in this codebase.

---

### 363. A failed realtime websocket handshake leaks `inflight` and the RPM slot permanently

**Severity:** 🟠 High · **Status: fixed**
**Files:** `wiwi/server/app.py:2832-2833` (`dep.inflight += 1` then
`await ws.accept()`), `:2834-2852` (the `try`/`finally` that owns both releases)
**Fix:** fixed — `ws.accept()` moved inside the `try` whose `finally` releases `inflight` and the RPM slot. Test: `test_realtime_failed_accept_does_not_leak_inflight`.

`dep.inflight += 1` happens **before** `await ws.accept()`, and the accept sits
**outside** the `try:`. Any exception from the accept — client vanished
mid-handshake, TCP RST, a proxy dropping the upgrade — skips the `finally`
entirely. Both the `inflight -= 1` and `_refund_deployment_slot(dep, ctx)` live
only in that `finally`, and nothing else reconciles them: `inflight` is a plain
attribute with no sweeper.

**Probe** (ASGI app driven directly with a `send` that raises on
`websocket.accept`; deployment configured `rpm=3, max_inflight=4`):

```
BEFORE inflight=0/4 rpm_events=0
after 1 failed handshake(s): inflight=1/4 rpm_events=1
after 2 failed handshake(s): inflight=2/4 rpm_events=2
after 3 failed handshake(s): inflight=3/4 rpm_events=3
[warning] realtime refused 503: no healthy deployment for 'rt'
after 4 failed handshake(s): inflight=3/4 rpm_events=3
```

The leak is monotonic and process-lifetime. Once leaked `inflight >= ceiling`,
`_lane_ceiling` sheds every candidate and `pick_deployment` returns `None` →
`503` for **all** realtime sessions on that group; the unfunded RPM event
separately makes `rate_limited()` refuse the group once the window fills. With
the **default** `max_inflight=None` there is no ceiling, so the counter is
silently wrong but not yet gating — it becomes a hard outage the moment an
operator sets the one knob that bounds realtime concurrency.

**Fix sketch:** open the `try:` at `:2832` so the `finally` covers the accept, or
move `dep.inflight += 1` after a successful `ws.accept()`.

---

### 364. `POST /admin/providers` mutates routing state before the DB write

**Severity:** 🟠 High · **Status: fixed**
**Files:** `wiwi/server/app.py:3527-3532` (in-memory mutation),
`:3533-3536` (the DB write); contrast `:3676-3703` (`admin_patch_provider`) and
`:3570-3580` (`admin_delete_provider`), both of which persist first
**Fix:** fixed — the route persists provider + key first (rolling the provider row back if the key insert fails), then mutates `router.providers`/`alias_to_provider`. Test: `test_admin_create_provider_db_failure_leaves_no_ghost`. **Follow-up open:** the reorder opened a concurrent-create race — see #373.

Lines `3527-3532` insert the `ProviderAccount` (with its plaintext
`ProviderKey`) into `state.router.providers` and bind `alias_to_provider`;
**only then** does `:3534` call `config_store.add_provider`. A DB failure raises
out of the handler with no `try` and no rollback, so `log_audit` at `:3545` is
never reached.

**Probe** (`config_store.add_provider` patched to raise `RuntimeError`):

```
providers before: ['p1']
raised out of the ASGI app: RuntimeError
providers after : ['ghost', 'p1']
alias_to_provider: {'gh': 'ghost'}
ghost account is ROUTABLE in this process: True | keys: ['default']
```

**Consequence:** process state and the DB diverge with no audit trace. The
operator sees a 500 and retries, which now hits the `409 already exists` guard,
so the account cannot be re-created; it keeps serving traffic (and exposing its
plaintext secret via the keys/secret route) for the process lifetime, then
vanishes on restart.

**This contradicts AUDIT #181's own fix text**, which asserts "`POST
/admin/providers` … already persist *before* mutating in-memory state". That
claim is false for this route, and #234's file list does not cover it — a false
"fixed" marker, the exact class #340 catalogs.

**Fix sketch:** persist first, then mutate in-memory state, matching
`admin_patch_provider`'s shape.

---

### 365. Gemini's `finishReason` is unguarded on both the sync and stream paths

**Severity:** 🟠 High · **Status: fixed**
**Files:** `wiwi/providers/gemini_adapter.py:319-324` (sync), `:448-455` (stream)
**Fix:** fixed — both Gemini arms route through the total `normalize_finish_reason` path. Test: `test_gemini_finish_reason_typed_wrong_does_not_raise`.

Both arms do an inline `}.get(finish, "stop")` on the upstream's
`candidates[0].finishReason` with no type guard. An unhashable value (list, dict)
raises `TypeError` inside the dict lookup.

**Probe** (`fresh_adapter("gemini")`, direct calls):

```
sync  finishReason=['STOP'] -> TypeError: cannot use 'list' as a dict key
sync  finishReason={'a': 1} -> TypeError: cannot use 'dict' as a dict key
strm  finishReason={'a': 1} -> TypeError: cannot use 'dict' as a dict key
strm  finishReason=['STOP'] -> TypeError: cannot use 'list' as a dict key
```

**Consequence:** sync, the `TypeError` escapes `decode_response` into
`_decode_response_guarded`, becomes a **retryable 502**, and charges
`record_fail` / a key cooldown to a healthy deployment for a semantically fine
frame. Stream, it escapes into the pump's generic handler → `StreamError` to the
client **plus** `_note_stream_failure` → deployment cooldown and key retirement
feed. This is the #153/#287 consequence class.

`tr.normalize_finish_reason` (`ir/translation.py`) was made deliberately
*total* against exactly this (`if not isinstance(raw, str): return "stop"`), and
`anthropic_adapter.py:652,789` and `openai_adapter.py:436` all use it. Gemini is
the one adapter that keeps its own two inline copies — the #270 class
re-instantiated.

**Fix sketch:** route both through the existing total helper.

---

### 366. `tool_calls[].index` is unguarded in the OpenAI base and NIM

**Severity:** 🟠 High · **Status: fixed**
**Files:** `wiwi/providers/openai_adapter.py:599`, `wiwi/providers/nim_adapter.py:421`
**Fix:** fixed — the OpenRouter index guard is lifted into the OpenAI base and NIM. Tests: `test_tool_call_index_*`.

`idx = tc.get("index", i)` feeds a `set` element (`_open_tool_indices.add`) and
a `dict` key (`_tool_names[idx]`), so an unhashable value raises `TypeError`
mid-stream. The sibling `openrouter_adapter.py:441-443` has exactly the right
guard two lines below its own identical read, and the AUDIT #154 comment sits
directly above the two unguarded copies — round 61 guarded `tc` and `fn` but not
`index`.

**Probe** (`decode_stream_event`, then the args frame that closes the call —
the Open is deferred to close by design, so a single frame yields `[]` and is
not evidence of anything):

```
int index (baseline) : [ToolCallOpen(index=0, ...), ToolCallArgsDelta(index=0, ...)]
index=['a'] openai   : TypeError: cannot use 'list' as a set element
index={'z':1} openai : TypeError: cannot use 'dict' as a set element
index=['a'] nim      : TypeError: cannot use 'list' as a set element
index=['a'] openrouter: ok        <- guarded control, no raise
index='0'  openai    : ToolCallOpen(index='0', ...)   <- str where contract says int
```

**Consequence:** affects all four OpenAI-wire types (`openai`,
`openai-compatible`, `gmicloud`, `bai`) plus `cline`, `workbuddy`,
`nvidia-nim`, and the opencode chat route, since all inherit this path. A
hashable-wrong index (`"0"`) is a second, quieter bug: `0` and `"0"` key
different dicts, so one provider call can split across two tool blocks.

**Fix sketch:** lift the OpenRouter guard verbatim into both files.

---

### 367. The Responses encoder silently discards text and thinking that arrive while a tool item is open

**Severity:** 🟠 High · **Status: fixed**
**Files:** `wiwi/wire/openai_responses.py:682` (`TextDelta` arm),
`:710` (`ThinkingDelta` arm); contrast `wiwi/wire/anthropic_messages.py:919,965`
**Fix:** fixed — text/thinking arriving while a tool item is open is buffered (capped like the Anthropic encoder) and flushed as its own item(s) once the **last** open tool closes, with `output_text.delta`/`reasoning_summary_text.*` events and an entry in the terminal `output` array. `tests/test_fix_round38.py::test_responses_interleave_preserves_tool_output_index_routing` updated: it pinned the old discard. Tests: `test_responses_encoder_preserves_*`, `test_responses_deferred_*`. **Follow-ups open:** builtin tools still drop deferred text (#374); chunking and oversized-delta gaps (#375, #376).

Trigger delta sequence — routine on Anthropic-family models, where the adapter
closes a tool block on `content_block_stop` and prose resumes after it:

```
TextDelta("BEFORE ") → ToolCallOpen(0) → TextDelta("DURING")
                    → ToolCallClose(0) → TextDelta(" AFTER")
```

**Probe** (both encoders driven with the same legal sequence):

```
RESPONSES  text deltas: ['BEFORE ', ' AFTER']          <- 'DURING' LOST
ANTHROPIC text deltas: ['BEFORE ', 'DURING', ' AFTER']  <- preserved
thinking deltas: []      # ThinkingDelta during an open tool: entirely discarded
```

`openai_responses.py:679-681` justifies this as *"Interleaved text is suppressed
— the same policy as the Anthropic encoder"*, but the Anthropic encoder calls
`self._defer("text", d.text, None)`, which **buffers and re-emits** it. The
Responses codec fixed the `output_index` corruption (#65, round 38) by
suppressing, and inherited a data-loss side effect the sibling never had. AUDIT
#65 documents only the index fix and repeats the incorrect "mirrors Anthropic"
claim; the loss itself is unrecorded.

**Fix sketch:** port the Anthropic buffer — hold the text in the existing
deferred structure while a tool item is open and flush it as its own
`output_item.added` block on close.

---

### 368. A tool **call** with no id is accepted by all three codecs (the call half of #331)

**Severity:** 🟠 High · **Status: fixed**
**Files:** `wiwi/wire/openai_chat.py:189`, `wiwi/wire/anthropic_messages.py:145,163`,
`wiwi/wire/openai_responses.py:350`
**Fix:** fixed — all three codecs raise `DialectError` on a tool call with a missing/empty/non-string id. Tests: `test_*_tool_call_without_id_is_rejected`.

`id=b.get("id", "")` defaults only a *missing* key, so the IR carries
`ToolUsePart(id="")` and the upstream body carries a nameless-id `tool_use` — the
exact 400-bait shape #331 removed from the **result** side. Worse, the turn is
permanently unanswerable: the result side raises `DialectError` on an empty
`call_id`, so the client can never construct a result that pairs with it.

**Probe:**

```
call id: ''
result w/ empty call_id -> DialectError function_call_output is missing 'call_id'
Anthropic upstream body: {"content":[{"type":"tool_use","id":"","name":"f",...}]}
```

Symmetric across all three surfaces (chat `tool_call`, anthropic `tool_use`,
responses `function_call`).

**Fix sketch:** mirror the result-side guard — read the id raw and raise
`DialectError` when it is missing, empty, or non-string.

---

### 369. `text.verbosity` is silently dropped: `text` is denylisted but only `text.format` is read

**Severity:** 🟡 Medium · **Status: fixed**
**Files:** `wiwi/wire/openai_responses.py:534-538` (`_KNOWN_KEYS`), `:416-419`
(`extras={…}`), `:228-241` (`_decode_response_format`, the only reader of `text`)
**Fix:** fixed — `text.verbosity` is hoisted into `extras`. Test: `test_text_verbosity_rides_extras`.

`{"text":{"verbosity":"low"}}` puts `verbosity` on the adapter's explicit forward
list (`openai_adapter.py:342`, mirrored at `nim_adapter.py:140`) — so the plumbing
exists and would carry it — but the decoder's denylist discards the whole `text`
object before `extras` is built.

**Probe** (real ASGI stack, upstream body captured via respx):

```
client sent  : {"verbosity": "low"}
upstream body: {"model":"gpt-4o","messages":[...],"stream":false}
verbosity reached upstream: False
```

The client gets 200 and silently receives default verbosity. This is the same
silent-drop class the **Anthropic** codec explicitly fixed for `output_config`
by building `_note_unmodelled_params` (`anthropic_messages.py:466-478`) to make
the gap visible; the Responses codec has no equivalent guard.

**Fix sketch:** hoist `verbosity` out of `text` into `extras`, or add the same
`_note_unmodelled_params`-style report for unhandled `text` sub-keys.

---

### 370. `least-busy` and `latency-based` permanently starve every sibling deployment on a tie

**Severity:** 🟡 Medium · **Status: fixed**
**Files:** `wiwi/router/router.py:914-923` (`Router._choose`)
**Fix:** fixed — `_min_with_tiebreak` breaks equal-metric ties at random for `least-busy` and warm `latency-based`. `tests/test_fix_round62.py::test_rpm_saturated_deployment_yields_to_sibling` relied on the list-order bias and now pins order through `inflight`. Test: `test_no_deployment_is_starved_on_a_tie`.

`min(avail, key=...)` has no tie-break, so on equal metrics it always returns the
first element in list order. All-idle (`inflight == 0`) is the normal
low-QPS state, and equal p95 is the normal healthy state.

**Probe** (N equal-weight deployments with identical warm p95, 2000 picks):

```
least-busy      N=2: {'d0': 2000}  starved=['d1']
least-busy      N=5: {'d0': 2000}  starved=['d1','d2','d3','d4']
latency-based   N=5: {'d0': 2000}  starved=['d1','d2','d3','d4']
simple-shuffle  N=5: {'d0':386,'d1':410,'d2':409,'d3':381,'d4':414}  <- control
```

**Consequence:** a configured deployment is permanently starved — 0 of 2000
picks, forever. No weight, probe, or admin action revives it; only a real traffic
difference revives it. Both strategies are non-default, which is why this is
medium rather than high.

The comment at `:917-919` names this exact defect — "instead of all traffic
pinning to the first one in list order" — and fixes it only for the cold
(`p95 == 0`) arm at `:920-922`. The warm tie falls through to the bare `min()`.

**Fix sketch:** add a deterministic tie-break after both `min()` calls,
matching the cold-arm precedent.

---

### 371. `config.yaml` in the repo root is untracked and not gitignored, and holds an API key

**Severity:** 🟠 High (process/hygiene, not code) · **Status: fixed**
**Files:** `.gitignore`, `config.yaml` (untracked, 25 lines)
**Fix:** fixed — `config.yaml` and `config.yaml.bak` added to `.gitignore`.

`git check-ignore config.yaml` exits 1 — the file is **not** ignored. It is not
wiwi's config (it is a `cli-proxy-api` config: `host/port/auth-dir/routing/...`),
but it carries a live-looking credential:

```
openai-compatibility:
- base-url: https://rumiruru.up.railway.app/v1
  api-key-entries:
  - api-key: '123'
```

**Consequence:** `git add -A` — which the repo's own history uses — would commit
it. This is the same class as `key.md` / `code.md` / `.env` / `kye`, all of
which are gitignored precisely because they are untracked plaintext credentials
with no naming convention the ignore file already covers (AUDIT #164, #154).

**Fix sketch:** add `config.yaml` and `config.yaml.bak` to `.gitignore` next to
the existing credential block. Both files are untracked, so nothing is
currently committed — this is preventive, not a remediation.

---

### 372. `Models.tsx` `sharePct()` reports a traffic split the router does not implement

**Severity:** 🟡 Medium · **Status: fixed**
**Files:** `web/src/pages/Models.tsx:99-102` (`sharePct`), `:93-98` (its
docstring), `:105-110` (`depState`); gateway side `wiwi/router/router.py:857-859`
**Fix:** fixed — the admin deployment payloads (`/admin/models`, deployment create) expose `probation`; `Models.tsx` computes share over the router's real candidate set (available, then non-probation when any exists) and shows a probation deployment as a blue "probation" standby state with 0% share. Test: `test_model_groups_payload_reports_probation`.

The uncommitted Models page redesign states its central claim twice:

```
//  * per-deployment effective share of the group's AVAILABLE weight ...
//    computed the way the router computes it so the number matches routing
```
```python
# The router recomputes weights from the *available* candidate set on every
# pick, and its two-level cross-provider WRR composes back down to exactly
# this ratio per deployment — so this matches routing, not the config file.
```

The first half is right: `sharePct()` divides by the sum over
`d.available === true`, which is exactly `Deployment.available`, exactly what the
admin API reports (`app.py`, the `"available": dep.available` field), and the
two-level WRR does compose to `weight/total`. Verified: baseline 1:1:1 gave
33/33/33 from both the UI formula and 600 real router picks; weights 1:5:1 gave
14/71/14 from both.

**But the router applies one more filter the UI does not model** —
`router.py:857-859`:

```python
fresh = [d for d in avail if not d.probation]
if fresh:
    avail = fresh
```

A deployment the HealthHealer restored is `available == True` (its
`cooldown_until` was cleared by `mark_recovered()`), yet it is **excluded from
the candidate set entirely** while any non-probation sibling exists. It does not
receive a reduced share — it receives zero.

**Probe** (600 real `pick_deployment` calls; pA/m-a, pA/m-b, pB/m-c each weight
1, with `pA/m-b` put into probation via `mark_recovered()`):

```
UI sharePct() : {'pA/m-a': 33, 'pA/m-b': 33, 'pB/m-c': 33}
router actual : {'pA/m-a': 50, 'pB/m-c': 50}          (pA/m-b: 0 picks)
MISMATCH      : all three
```

The UI reports a three-way 33/33/33 split; the router sends **100%** of traffic
to the two healthy deployments and **none** to the probation one. The number is
wrong by the full weight of the healed deployment, and its tooltip tells the
operator the opposite of the truth.

`depState()` compounds this: a probation deployment reports `"ok"` (green dot,
"available · N in flight"), so the page actively presents a deployment that is
receiving no traffic as fully healthy.

**Consequence:** the page's stated purpose — "how traffic splits across them",
"matches routing rather than the config file" — is inverted precisely in the
state an operator is most likely to be looking at: right after the healer
revived something. The provider bar has the same denominator bug.

**Fix sketch:** either expose `probation` on the admin `DeploymentInfo` payload
and exclude it from both `sharePct()`'s denominator and `depState()`, or soften
the claim to "share of configured available weight". The first is correct; the
second is honest. `healer.enabled` is off by default, which bounds the blast
radius but not the wrongness.

---

## ✅ Fixed — round 122 (2026-10-08) — container deployments

### 380. The shipped image's journal dir is unwritable, so every virtual-key STREAM is refused with 503

**Severity:** 🔴 critical · **Status: fixed**
**Files:** `wiwi/server/app.py` (`AppState.__init__` journal construction, the
lifespan boot probe, `_persist_journal_owner`, new `_drop_journal`,
`_stream_response`'s new `journal_disabled` flag, the pre-dispatch site),
`Dockerfile`, `docker-compose.yml`, `tests/test_fix_round122.py`

**Trigger:** run the shipped Docker image (Railway, `docker compose up`, the HF
Space) and issue a **streaming** request with a **virtual key**.

`stream_journal_dir` defaults to `.wiwi/journals` — a path **relative to the
process CWD**. The image sets `WORKDIR /app`, so that resolves to
`/app/.wiwi/journals`, but `/app` is owned by `root` (the `COPY` layers) while
the process runs as `USER wiwi` (uid 10001). The Dockerfile `chown`s only
`/app/data` and `/data` — the two paths it documents as writable — and
`docker-compose.yml` mounts a volume on `/app/data` only. Nothing makes
`/app/.wiwi` creatable, so `mkdir` raises `PermissionError` on every request.

The refusal is then *correct code amplifying a deployment defect*. AUDIT #320
made the durable owner line PRIMARY and specified: when it cannot be persisted,
refuse the stream rather than write tenant data into an ownerless journal. That
is exactly what happens — but the blast radius is wrong:

- **master** (`key_id == "master"`) is **exempt** from the persist, so it streams fine.
- **non-streaming** requests never touch the journal, so they work fine.
- **virtual-key streaming** is refused: `503 {"error":{"message":"stream replay journal unavailable; retry"}}`.

So the gateway is up, healthy (`/health` 200), serves completions and serves
master streams, and fails *only* the combination that matters most in
production. Nothing says "permission denied" to the operator: `JournalStore.open`
swallows the `mkdir`/`touch` failure as `journal_touch_failed` and the owner
write as `journal_owner_write_failed` (both at `error`, but on the request path,
interleaved with normal traffic), and the startup `sweep` swallows its own
`OSError` and returns `0` (`tape_store.py:_unlink_expired`) — so the container
comes up reporting nothing wrong. The refusal message names only the journal,
never the directory or the OS error, so the 503 looks like a transient upstream
blip and a client retry loop produces the same 503 forever.

**Verified by execution**, not by reading: a repro driving a real `create_app`
with `stream_journal_dir` pointed at a path whose parent is a regular file (so
`mkdir` fails with `NotADirectoryError` for any uid, no privilege drop needed)
produced exactly this asymmetry —

```
journal_touch_failed        NotADirectoryError: ... /.wiwi/journals
journal_owner_write_failed  key_id=master
master  stream: 200        (SSE frames delivered)
journal_owner_write_failed  key_id=k04ff7f11c2c76516
journal owner persist failed for 42c6838f7e9444a4; refusing the stream ...
vkey    stream: 503 {"error":{"message":"stream replay journal unavailable; retry"}}
vkey nonstream: 200        (body returned)
```

**Fix sketch — three parts, the second and third being what keep this class of
bug from being silent again.**

1. **Make the default resolvable in the shipped image.** Add a
   `WIWI_STREAM_JOURNAL_DIR` env override in `AppState.__init__`, mirroring the
   existing `REDIS_URL`/`DATABASE_URL` convention (empty env falls back to
   config, so platforms that inject empty values do not shadow it), and have
   the image set it to a directory it has already made writable — `/data` is the
   documented writable path in both the Dockerfile and `wiwi.yaml.example`, and
   co-locating journals with the SQLite DB gives them one persistence story.
   Create and `chown` it in the Dockerfile alongside `/app/data`.
2. **Fail loudly at startup, once, with the resolved absolute path.** When
   `stream_journal_enabled`, attempt the directory creation during lifespan and
   log an `error` naming the absolute path and the `OSError` if it fails. The
   operator should learn about this from the boot log, not from a stream of
   503s. Do **not** refuse to start — journaling is a replay convenience, and
   turning a resume-feature misconfiguration into a boot failure would be a
   worse outage than the one being fixed.
3. **Degrade the #320 refusal instead of refusing the request.** When the
   durable owner write fails, drop the journal entirely (unlink, release, and
   mark it unusable so `_stream_response`'s fallback branch does not re-open
   it) and stream the response normally. This is *strictly safer* than what
   happens today with respect to #320's own threat model: with no journal on
   disk there is no ownerless replay data, so the cross-tenant hole #320 closed
   cannot occur — while reconnect-resume is merely unavailable for that
   request. Note that `_stream_response`'s fallback branch already implements
   exactly this degradation (`journal = None`, unlink, release, keep streaming);
   the pre-dispatch site in `run_chat_like` is the outlier that returns 503, so
   the two sites disagree about what an unwritable journal means. Unifying them
   also means a `read-only` mount or a full volume degrades one feature instead
   of taking down all virtual-key streaming.

**Also note for the container story:** the image's journal dir is on the
container filesystem, so on Railway (ephemeral) journals do not survive a
redeploy. That is a separate durability caveat from this bug — mount a Railway
volume on the journal dir if cross-restart resume is wanted — but part 1 is
still required for resume to work at all within a single process lifetime.

**Fix — all three parts implemented.** `WIWI_STREAM_JOURNAL_DIR` overrides the
configured dir in `AppState.__init__` (empty env falls back to config, matching
the `REDIS_URL`/`DATABASE_URL` convention, because Railway and Render inject
empty variables for unset names); the image sets it to `/data/journals` and
`chown`s that path alongside `/app/data`, and `docker-compose.yml` mounts the
existing `wiwi_data` volume at `/data` too so journals survive `down`/`up`
alongside the SQLite DB. The lifespan now probes the dir once and logs
`stream_journal_dir_unwritable` with the **resolved absolute path**, the OS
error, and a remediation hint — non-fatally, since refusing to boot would trade
this outage for a worse one. The pre-dispatch site now calls the new
`_drop_journal` (aclose → release → unlink) and streams on; `_stream_response`
gained a `journal_disabled` flag so the fallback branch cannot re-open a
journal on the directory that just failed, and the two sites now agree.

The security posture is **unchanged or better**: dropping the journal means no
file on disk, so there is no ownerless replay data for #320's cross-tenant hole
to exploit, and `release` deliberately leaves the in-memory intent alive so
`owner_of` still reports the real owner. `tests/test_fix_round122.py` pins all
of it (12 tests), including the two controls that matter most — a working
journal dir still records a complete, replayable, correctly-owned journal with
no second upstream call, and cross-key replay is still refused after the 503 was
removed.

Verified: the repro now returns **200 with full SSE frames** for a virtual-key
stream against an unwritable dir (was 503), the boot log names the absolute
path, and **2944 passed, 11 skipped**, ruff clean.

---

### 381. `_CrossProviderWRR` cursor keys go stale if a group-membership edit forgets to rebuild

**Severity:** 🟡 Medium (latent — no live trigger today) · **Status: fixed**
**Files:** `wiwi/router/router.py` (`_CrossProviderWRR.pick`, `Router._choose`),
`tests/test_fix_round123.py`

`_CrossProviderWRR._dep_cursors` is keyed by `id(deployment)` — deliberately, so
a YAML combo that lists the same `(provider, model_id)` pair twice does not make
the two `Deployment` objects share one deficit and starve the second (the AUDIT
#307 fix; `(provider, model_id)` was the pre-#307 key). An `id()` key is only
valid while the object is alive **and** still in the group. The shipped code
keeps both true only through an implicit contract: every group-membership
mutation calls `Router.rebuild_cross_provider_pools`, which recreates the
`_CrossProviderWRR` object (and so its cursor dict) from scratch.

All **eight** current mutation sites honour that contract — the four admin
deployment endpoints, the Cline default-model reconciler, the startup DB loader,
the provider importer, and `_build` — so there is **no live bug**. But the
contract is unenforced and spread across unrelated modules, and a future
membership edit that forgot to rebuild would leave an orphaned key behind. Two
failures follow: `_dep_cursors` grows by one entry per detached deployment for
the process lifetime, and CPython recycles a freed object's `id()` for the next
allocation, so a brand-new `Deployment` at that address silently inherits a dead
one's deficit and is mis-served.

**Verified by execution** (pruning disabled to simulate the forbidden shape):
after detaching a deployment without rebuilding, the orphaned `id()` key
persisted in `_dep_cursors` with a stale deficit — the exact leak the fix closes:

```
assert 125225817575392 not in {125225817575392: 87.0, 125225817575632: 13.0}
```

**Fix:** `pick` now prunes `_dep_cursors` and `_state` to the group's *live*
membership on every call (`_choose` passes the full group, not just the
available subset). A detached deployment's key is dropped; a merely-*cooling*
deployment keeps its deficit — it is still a member, since a cooldown changes
availability, not membership — so the smooth-WRR "a temporarily-unhealthy peer
is not starved after it recovers" property is preserved. In steady state every
key is already live, so the prune is a no-op and routing behaviour is unchanged
(pinned by `test_steady_state_distribution_unchanged_by_pruning`).

**Regression:** `tests/test_fix_round123.py` (4 tests) — detached-key pruning,
cooling-member deficit retention, unchanged steady-state distribution, and a
reattached deployment starting from a fresh cursor. Confirmed to fail without
the fix and pass with it. Full gate: **2948 passed, 11 skipped**, ruff clean.

---

## ✅ Fixed — round 119 (2026-10-05)

Reported symptom: `Error: upstream idle >30s between chunks`. A model that was
still legitimately working — a reasoning model thinking through a code task, a
cold prompt against a long context — produced no bytes for longer than
`stream_idle_timeout_s` (30 s default) and the gateway declared the turn dead
instead of waiting or failing over. Three distinct defects sat behind that one
message. Covered by `tests/test_fix_round119.py`.

### 356. A long pre-content phase was cut, and never retried
**Severity:** 🟠 high
**Status:** fixed

Two independent bugs, one symptom.

**The cut.** `stream_idle_timeout_s` governed both phases of a stream. Silence
*between* content chunks is diagnostic — that long a gap mid-stream really is a
dead connection. Silence *before* the first content is normal: a reasoning model
queues and thinks, and a cold cache-miss prompt can run for minutes before its
first token. Charging that against a 30 s budget killed healthy long
generations, and the operator's only recourse was raising the watchdog for the
whole stream, which in turn held genuinely wedged connections open far longer.

**No failover.** `_pump_once` set `started = True; ready.set()` the moment the
**200 headers** arrived (`gateway.py:1697-1699`), so `execute_with_retries` had
already returned success before a single body byte was read. Every pre-content
fault was therefore forced down the mid-stream `_fail_stream` arm, and the
`if not started:` err_box branch — the one that raises a retryable `WiwiError`
so the retry loop can pick a different deployment — was **unreachable** for this
entire class of fault. Verified: an upstream that accepts the request and then
goes silent produced `StreamError("upstream idle >30s between chunks")` with a
healthy sibling deployment never contacted, despite `num_retries: 1` and a
configured fallback group.

**Fix:** `started`/`ready` now mark the first *body line*, not the response
headers, which is the single boundary all three of their uses already meant
(`ready` for the retry loop, the err_box-vs-`_fail_stream` split, and partial
billing on cancellation). A new `stream_first_chunk_timeout_s` (default 300 s,
`<= 0` falls back to `stream_idle_timeout_s`) governs the pre-content phase and
`stream_idle_timeout_s` the inter-chunk phase; `saw_content` tracks the boundary
and is deliberately not `started`, because a provider may emit many non-content
frames first (Anthropic's `message_start`/`ping`, OpenRouter's comments) and the
watchdog must tolerate those. The pre-content stall boxes a retryable 504 and is
accounted for by `execute_with_retries` alone — the pump must not also call
`_note_stream_failure`, or the same failure is charged twice (the AUDIT #174
double-count, retiring keys at half the configured threshold).

**Consequence to handle, not to leave:** holding `ready` back until the first
byte means the consumer may not exist for the whole of a long thinking phase,
which is exactly the silence `stream_ping_interval_s` exists to prevent
(AUDIT #177). `stream()`'s wait on the retry driver is now interleaved with the
keep-alive pings instead of blocking on it outright, so an Anthropic client is
still told the connection is alive while the upstream says nothing.

### 357. A read timeout pre-empted the gateway's own watchdog, and reported nothing
**Severity:** 🟠 high
**Status:** fixed

Both stream arms passed a **scalar** timeout to httpx, which applies it to
*every* timeout phase — connect, read, write and pool. So the provider's blanket
request deadline (`router_settings.timeout`, default 120 s) fired as a **read**
timeout, not the gateway's watchdog. Two consequences:

1. A stream legitimately quiet for longer than that deadline was killed by
   httpx rather than by the watchdog, so the retryable watchdog path never ran
   and no failover happened.
2. `httpx.ReadTimeout` carries **no message** — httpx maps httpcore's exceptions
   with `mapped_exc(str(exc))`, and httpcore raises its timeouts with no
   arguments. Both arms passed `str(e)` through, so the client received a
   `StreamError` whose message was the **empty string**: indistinguishable from
   a gateway bug, and useless in a log. The same `f"upstream {type(e).__name__}"`
   that the other call sites use was never applied here.

**Fix:** `stream_read_timeout()` lifts the read deadline on the two
watchdog-armed arms only (the watchdog is the authority for "no progress";
connect/write/pool keep the configured value, which is what a scalar was
actually good for), and keeps the configured deadline when no watchdog is armed
rather than leaving the body unbounded. `_transport_message()` names every
transport failure, falling back to the exception class when there is no text.
`_fmt_secs()` replaces `f"{idle_s:.0f}"`, which rendered a sub-second budget as
`"upstream idle >0s"` — a message naming no duration at all.

**Also fixed while in there:** `_pump_once`'s `finally` now installs a completion
owner on the pump before cancelling it, and so does `stream()`'s pre-consumer
driver wait. Previously that guarantee depended on the consumer happening to
reach the resume branch first, which the extra scheduling hop in the driver wait
made timing-dependent (`test_fix_round98.py::test_consumer_cancellation_keeps_failed_pump_owned`).


---

## ✅ Fixed — round 117 (2026-10-01)

TPS is output **generation** speed. Four defects in how it was measured and
reported. Covered by `tests/test_fix_round117.py`.

### 319. TPS had two different definitions inside one quantile family
**Severity:** 🟠 high
**Status:** fixed

`build_log_event` computed `tps` two ways: for streaming requests
`completion_tokens / (last_token_at - first_token_at)` (generation phase only),
and otherwise `completion_tokens / (latency_ms / 1000)` (whole round trip, so
prefill and queueing included). Both landed in `wiwi_tps` and in the same
`tps_avg`/`tps_p95` admin aggregates, so a single quantile mixed two
incomparable measures. The split was not even within a request type: because
`first_token_at`/`last_token_at` are stamped only in the stream path
(`wiwi/core/gateway.py:1085-1088`), **every** non-streaming request took the
round-trip branch.

The fallback was deliberate — `tests/test_fix_round4.py:58` asserted it, and
its comment claimed "OpenRouter reports throughput for all requests". That
rationale does not hold: OpenRouter's throughput is a generation-phase
measure, and a round-trip figure is not comparable with one.

**Fix:** one definition — `gateway.py:2231-2241` now requires
`stream_secs > 0.05` and has no fallback. Requests with no separable
generation phase report `tps = 0.0`. `test_fix_round4.py:58` and the
short-stream test beside it were **inverted**, with the original expectation
preserved in the docstrings so the change stays traceable.

### 320. Requests with no TPS sample vanished from every aggregate
**Severity:** 🟡 medium
**Status:** fixed

Every TPS consumer filtered on `tps > 0` — `metrics.py:131`, `stats.py`,
`db_sink.py:1499` — with no denominator anywhere. A p50 computed over a tenth
of the window's traffic was indistinguishable from one computed over all of
it, and non-streaming-heavy gateways reported a throughput figure for what was
really a streaming-only statistic.

**Fix:** the exclusion is now published rather than silent.
`wiwi/server/stats.py:tps_sample_ratio` is the single implementation, used by
`stats.overview()`, `stats.timeseries()` (per bucket, via a new `req_buckets`
count), `DBSink.read_overview`, `DBSink.read_timeseries` (new `COUNT(*) AS
req_count` in the raw query and `SUM(requests) AS req_count` in the rollup
query, summed by `_BucketSum`), and a new `wiwi_tps_sample_ratio` gauge.

### 321. `wiwi_tps` exported no p99 while latency and TTFT did
**Severity:** ⚪ low
**Status:** fixed

`metrics.py` rendered p50/p95 for TPS but p50/p95/p99 for
`wiwi_request_duration_ms` and `wiwi_ttft_ms`, and documented the asymmetry in
a comment rather than fixing it. The slow-generation tail is what a throughput
metric is alerted on.

**Fix:** `wiwi_tps` now exports `0.99` as well, matching the other two.

### 322. The nearest-rank percentile formula was duplicated
**Severity:** ⚪ low
**Status:** fixed

`metrics.py:_percentile` (0–100 scale) reimplemented
`stats.percentile` (0–1 scale) in the same repo, each with a comment pointing
at the other as the owner. Either could drift with no test failing.

**Fix:** `stats.percentile_sorted` is the single owner;
`stats.percentile` sorts and delegates, and the exporter calls
`percentile_sorted` directly (it renders three quantiles from one sorted
window, so re-sorting per call would have cost the O(n log n × 3) → O(n log n)
win). `metrics.py:_percentile` deleted; `tests/test_fix_round117.py` asserts
it is gone and that the two entry points agree.

---

## ✅ Fixed — docs page touch targets (2026-09-30)

### 318. Heading deep-link anchors were a 17×17 px target on touch

**Severity:** 🟡 Medium · **Status: fixed**
**Files:** `web/src/pages/Docs.tsx`, `web/src/styles.css`

The `#` deep-link anchor on each docs section heading is a 13 px `Hash`
glyph in `p-0.5`, which measures **17×17 px** — well under the 44 px touch
floor that this repo's UI/UX rule makes binding. The `docs-heading:hover`
reveal and the `@media (hover: none) { opacity: 0.6 }` rule made the anchor
*visible* on touch, which is what made it look compliant: nothing about it
signalled that the thing you could see was too small to hit reliably. A
thumb on a 375 px viewport would miss it most of the time.

Fixed in the same `@media (hover: none)` block that already handled the
touch reveal — `display: inline-flex` plus `min-height/min-width: 44px`,
with `margin-block: -10px` pulling the vertical bleed back so the heading
keeps its existing height. The anchor is the last flex item in the `h2`, so
the extra width falls into the existing `gap-1.5` and shifts nothing.

The 44 px floor is deliberately **not** applied at fine-pointer widths: on a
mouse a 17 px anchor with a visible focus ring is the desktop-docs norm, and
padding it out would put a large invisible box in the heading's text flow.

Three other suspected failures in the same verification pass turned out to be
**test artifacts, not defects**, and are recorded here so they are not
"re-fixed" later:

- **Back-to-top appeared not to scroll.** The smooth scroll from ~5000 px
  takes ~2.5 s; the check slept 900 ms. Fixed by waiting on
  `window.scrollY < 5` with a timeout instead of a fixed sleep.
- **Anchors "not revealed on focus".** Programmatic `.focus()` does not set
  `:focus-visible`, so the reveal CSS correctly did not apply. Fixed by
  driving real `Tab` presses until `document.activeElement` is the anchor.
- **Desktop anchor reported as under-size.** That is the intended behaviour
  described above; the check now asserts on `matchMedia('(hover: none)')` and
  only enforces 44 px where the pointer is coarse.

- **Test:** no `test_fix_roundN.py` added — the defect is a CSS tap-target
  that pytest cannot observe, and `web/` has no test runner. The proof is
  Playwright (`.verify/docs_check.py`, gitignored): 24 checks across 375×812
  touch and 1440×900 desktop — no horizontal overflow, progress rail advances,
  back-to-top returns to `y=0`, all 8 anchors Tab-reachable and focus-revealed,
  anchor hit area 44×44 on touch, navigation works, no page errors. All pass.
- **Verified:** full suite **2729 passed, 5 skipped**, ruff clean, `tsc -b` +
  `vite build` clean, `eslint src` 0 errors.

---

## ✅ Fixed — round 111 (2026-09-29)

### 317. Journal owner-record write failure failed the #67 replay gate OPEN

**Severity:** 🟠 High · **Status: fixed**
**Files:** `wiwi/streaming/tape_store.py`, `tests/test_fix_round110.py`

`JournalStore.open` wrote the per-key ownership record best-effort and
swallowed the error, while the replay gate in `server/app.py` reads
`owner_of() is None` as "a pre-#67 legacy journal, readable by anyone":

```python
try:
    await asyncio.to_thread(_write_owner)
except OSError:
    pass  # best-effort: replay scoping degrades to none
```
```python
jowner = state_.journals.owner_of(replay_id)      # server/app.py:1463
owner_ok = (jowner is None                        # <-- fail-open
            or caller_kid == jowner
            or (jowner == "master" and caller_kid == "master"))
```

The root cause is that **"no owner was ever recorded" and "the owner could not
be recorded" are the same on-disk state.** A transient write failure —
ENOSPC on the Space's `/data` volume, EMFILE under fd pressure, a read-only
mount — produced the same `None` on a brand-new journal as a legacy file does,
so the gate opened to every caller. Key B could reconnect with
`x-wiwi-stream-id` set to key A's request and replay **A's full response
content**: prompt text and tool arguments included.

Two defects, one fix:

1. **Fail-open.** Fixed by recording the ownership *intent* in memory
   (`_owner_intent`) before the write and independently of its outcome.
   `owner_of` consults it, so a lost write returns the intended owner instead
   of `None` — the read is scoped to the real owner and denied to everyone
   else. A journal with neither intent nor owner line is genuinely pre-#67 and
   still reports `None`, preserving the documented restart-replay compat.
2. **Silent.** The `except OSError: pass` gave an operator no signal that
   scoping had degraded on a live stream. It now logs at `error` with the
   request id, key id, and error. The same was done for the eagerly-swallowed
   `mkdir`/`touch` at the top of `open` (double-billing is still avoided —
   `is_active` reads the in-memory set — but the restart-replay fallback was
   being lost silently).

The intent map is bounded by the journals on disk: `sweep` drops an entry
exactly when it unlinks the file it describes. It deliberately **outlives
`release`** — a first attempt cleared it there, and the round-110 test caught
that this re-opens the hole for the entire replay window, because `release`
runs on *every* normal stream completion (`server/app.py:1751`) while the
journal stays replayable for its TTL. That was the common case, not the edge
case.

- **Test:** `tests/test_fix_round110.py` — 5 tests, including the legacy
  compat control, the "real owner can still reconnect" control (failing closed
  must not brick the legitimate owner's own reconnect), and the
  intent-outlives-release / sweep-reclaims invariant.
- **Verified:** repro script pre-fix printed `keyB owner_ok = True`; post-fix
  prints `False` and logs `journal_owner_write_failed`. Full suite **2727
  passed, 5 skipped**, ruff clean.

---

## ✅ Fixed — round 110 (2026-09-29)

### 316. `.env.example` shipped a live-looking `DATABASE_URL` placeholder

**Severity:** 🟡 Medium · **Status: fixed**
**Files:** `.env.example`

The shipped `.env.example` had `DATABASE_URL` **uncommented** under a block of
commented examples:

```
DATABASE_URL=postgresql://user:password@ep-xxx-pooler.c-4.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require
```

The five comment lines directly above it are the alternatives. So
`cp .env.example .env` — the documented bootstrap — inherits the placeholder
as a **real** value, and `init_db` prefers `os.environ["DATABASE_URL"]` over
`general_settings.database_url` (the SQLite default the YAML ships with):

- Local: startup dies inside the lifespan with
  `asyncpg.exceptions.InvalidPasswordError: password authentication failed for
  user 'user'` — every request refused, stack trace on the console.
- Docker: `docker-compose.yml` sets
  `DATABASE_URL: ${DATABASE_URL:-postgresql+asyncpg://wiwi:wiwi@postgres:5432/wiwi}`.
  Because `env_file: [.env]` loads the placeholder, the `${…:-…}` default never
  applies, so `docker compose up --build` connects to the Neon host instead of
  the bundled Postgres service and seeds nothing.

The comment claimed "If unset, defaults to SQLite" — but the file did not leave
it unset.

- **Fix:** `DATABASE_URL` is commented out, with the placeholder template kept
  commented (`postgresql+asyncpg://user:password@host:5432/wiwi`, scheme
  normalized to the async driver) and a note that unset means SQLite / the
  compose default. Verified by `python3 -c 'from wiwi.config import load_env;
  load_env(); print(os.environ.get("DATABASE_URL"))'` → `None`, and by an
  end-to-end `./start.sh` run (`npm ci` → `Application startup complete`,
  Vite ready).

> A personal `.env` copied before this fix keeps the stale line; delete or
> comment line 14 there too.

---

## ✅ Fixed — round 109 (2026-09-29)

Pinned by `tests/test_fix_round109.py` (6 of its 7 tests failed before the fix).

### 315. `trusted_proxies` had no environment override

**Severity:** 🟡 Medium · **Status: fixed**
**Files:** `wiwi/config.py` (`_interpolate`, `GeneralSettings`), `wiwi.yaml.example`

Round 106 gated the session cookie's `Secure` flag and the Cline OAuth callback
scheme on `general_settings.trusted_proxies`. That list was settable **only** by
editing `wiwi.yaml`:

- Every other container-relevant setting has an env override — `DATABASE_URL`,
  `REDIS_URL`, and the whole `general_settings` block is driven by
  `os.environ/NAME` in the shipped example. `trusted_proxies` was the one
  exception.
- The scalar form could not work: `_interpolate` resolves `os.environ/NAME` to a
  **string**, and the field is `list[str]`, so
  `trusted_proxies: os.environ/WIWI_TRUSTED_PROXIES` raised
  `ConfigError: Input should be a valid list` at load.
- The consequence is a deployment-shaped hole. A HuggingFace Space (or any PaaS
  whose image bakes `wiwi.yaml` from the example, as the Dockerfile does) has
  **no config file to edit** and no volume to mount one from, so it could not —
  without abandoning the baked config entirely — reach round 106's fixed
  behaviour: sessions worked, but the cookie was never `Secure`, and OAuth
  callbacks were built as `http://` behind a TLS-terminating ingress.

- **Fix:** `trusted_proxies` accepts a comma- or whitespace-separated string
  (a `mode="before"` validator on `GeneralSettings`; the YAML-list form still
  validates), and `_interpolate` gained a list form so
  `trusted_proxies: [os.environ/WIWI_TRUSTED_PROXIES]` works too.
  `wiwi.yaml.example` now declares it as
  `trusted_proxies: os.environ/WIWI_TRUSTED_PROXIES`, matching the surrounding
  keys. Unset or blank yields `[]` — the fail-closed default (AUDIT #73) — so
  the example stays safe shipped unset.
- **Docs/plumbing:** `WIWI_TRUSTED_PROXIES` documented in `docs/CONFIG.md`,
  `README.md` (Railway table + `WIWI_CONFIG` snippet), `.env.example`,
  `docker-compose.yml`, and the Space manifest `deploy/hf-space/README.md`, so
  the Space can activate the `Secure` flag without publishing `WIWI_MASTER_KEY`.

---

## ✅ Fixed — round 108 (2026-09-29)

Floats on Postgres were 4 bytes wide. Pinned by `tests/test_fix_round108.py`,
which runs only when `WIWI_TEST_POSTGRES_URL` is set — the suite had **no**
real-Postgres fixture, which is why this survived.

### 313. Shared DDL spelled floats `REAL`, which Postgres maps to float4

**Severity:** 🟠 High · **Status: fixed**
**Files:** `wiwi/auth/users.py` (`USERS_DDL`), `wiwi/auth/service.py`
(`CREATE_SQL`), `wiwi/server/config_store.py` (`PROVIDER_DDL`,
`MODEL_PRICES_DDL`, `MODEL_PRICE_SCOPES_DDL`)

The three log tables each have a Postgres DDL variant that says
`DOUBLE PRECISION`, but the five *shared* DDL constants spelled every float
column `REAL`. SQLite maps `REAL` to an 8-byte float; **PostgreSQL maps it to
`float4`**. 16 columns were affected:

- `users`: `created_at`, `updated_at`
- `vkeys`: `max_budget`, `spend_to_date`, `expires_at`, `created_at`, `updated_at`
- `providers`: `timeout_s`
- `model_prices` / `model_price_scopes`: the four `*_cost_per_token` rates

- **Trigger (timestamps).** Every timestamp is `time.time()` ≈ 1.79e9, where a
  float4's ULP is **128 s**. Measured: five playground-style keys minted
  seconds apart stored **one** distinct `created_at` value. `expire_keys` then
  cannot tell them apart, so `ORDER BY created_at DESC … keep_newest` — the
  per-owner key cap that stops a user rotating around per-key budgets — retired
  *arbitrary* keys, including one the owner was actively using. Same input on
  SQLite gave five distinct values.
- **Trigger (sums).** `spend_to_date` is a sum of per-request costs. Measured
  on Postgres: `1000.0` plus twenty recorded `1e-6` charges read back as
  exactly `1000.0` — the increments were below the float4 resolution and
  vanished, so a budget-bound key never reaches its budget. SQLite accumulated
  correctly.
- **Trigger (expiry).** A 0.5 s TTL stored as float4 rounds down by up to 64 s,
  i.e. into the past: the key was born expired. A second `AuthService` (empty
  cache, so the value comes from the column) refused to authenticate it.

- **Fix:** all 16 columns spell `DOUBLE PRECISION`, which is portable (SQLite
  keeps float64 affinity — verified, `typeof` stays `real` and round-trips
  exactly). `widen_pg_floats()` in `wiwi/auth/users.py` widens an **existing**
  Postgres database, called from `UserService.startup`, `AuthService.startup`
  and `ConfigStore._migrate`; it reads `information_schema` and alters only
  columns still typed `real`, so it is idempotent and a no-op on a fresh or
  already-correct database. Verified on a legacy-shaped Postgres database with
  data: 10 `REAL` columns → 0, rows preserved, second startup changed nothing,
  and spend accumulation worked afterwards. SQLite legacy databases (missing
  `owner_id` / `cache_creation_input_cost_per_token`, or with those columns
  added as `REAL`) still migrate cleanly.
- **Test infra:** `tests/test_fix_round108.py` is the first test that runs
  against real Postgres; it skips unless `WIWI_TEST_POSTGRES_URL` is exported,
  so the default gate stays green. Its docstring gives the `docker run` line.

### 314. `delete_deployment` reset a stored weight on SQLite only

**Severity:** ⚪ Low (latent) · **Status: fixed**
**File:** `wiwi/server/config_store.py` — `delete_deployment`

SQLite's `INSERT OR REPLACE` is a DELETE + INSERT: any column the statement
does not name is recreated from its `DEFAULT`, not carried over. Postgres'
`ON CONFLICT … DO UPDATE SET detached=1` touches only `detached`. The tombstone
statement spelled the placeholder `weight` (1) in both branches, so detaching a
deployment whose weight was **7** left `weight=7` on Postgres and `weight=1` on
SQLite. Measured on both dialects.

Not reachable today — `delete_deployment` removes the live deployment from
`router.groups`, and re-attach goes through `add_deployment` (which always
restates `weight`), so no reader observes the reset value. Fixed anyway: the
stored row should not depend on the dialect when the two branches are meant to
express the same intent. The SQLite arm now carries the existing weight across
the replace with a correlated `COALESCE((SELECT weight …), 1)`.

Audited the other five `INSERT OR REPLACE` sites for the same class: every one
of them names **all** columns of its table in both branches (`add_provider`,
`add_key`, `add_deployment`, `upsert_price`, `upsert_price_scope`,
`set_setting`), so no other upsert can drop a stored value this way. The
`providers` table has no `created_at` column, contrary to an earlier note.

---

## ✅ Fixed — round 107 (2026-09-29)

The admin key-generate route kept the round-105 defect on its own path: real
admins minted un-owned keys. Pinned by `tests/test_fix_round107.py`.

### 312. Admin-generated keys were un-owned, uncapped, and shared

**Severity:** 🟡 Medium · **Status: fixed**
**File:** `wiwi/server/app.py` — `/admin/keys/generate` (`owner_id = None if actor.role == "admin" else actor.id`)

**What:** the route resolved `owner_id=None` for every admin, which is the same
shape round 105 fixed on the Playground path. Two consequences:

- `AuthService.create_key` enforces `max_keys_per_user` only when
  `owner_id is not None`, so admin-generated keys were exempt from the ceiling
  the setting exists to provide — a real admin could mint unbounded live
  credentials, each with its own budget/rate limit to rotate around.
- every admin shared the single `owner_id=None` owner id, so `count_keys(None)`
  / `list_keys_for_owner` grouped them together and the keys were
  indistinguishable from one another and from the synthetic master's.

The docs asserted the behaviour as intent ("Admins are exempt",
`README.md`/`config.py`), which contradicted round 105's own note that real
admins now own their keys.

- **Trigger:** promote a real user to admin, call `/admin/keys/generate` past
  `max_keys_per_user` times → all succeed; the owner column is NULL for each.
- **Fix:** `owner_id = _key_owner_id(actor)` — the same helper the playground
  mint uses (renamed from `_pg_owner_id`, since it now serves both mint paths).
  Real accounts, admins included, own their keys; only the synthetic master
  (`_SYNTHETIC_MASTER_ID`) stays un-owned, because `AuthService._lookup_db`'s
  owner check fails closed on a missing owner row and an owned master key would
  therefore never authenticate.
- **Docs:** `README.md`, `wiwi/config.py` and `wiwi/auth/service.py` comments
  no longer claim admins are exempt; `docs/CONFIG.md` gained the missing
  `trusted_proxies` / `max_keys_per_user` / `wiwi_settings` reference (the
  `trusted_proxies` entry is load-bearing for round 106's `Secure` flag and
  round 105's OAuth callback scheme), and its stale `db_url` /
  `session_secret` / `host`-under-`general_settings` entries were corrected.

---

## ✅ Fixed — round 105 (2026-09-28)

Playground wrapper (introduced in round 103), plus the Cline auto-connect
callback URL. Every finding reproduced against the pre-fix source and pinned
by `tests/test_fix_round105.py`.
### 308. The playground bearer cache was never cleared and never bounded

`wiwi/server/app.py` — `_pg_bearer_cache` (round 103).

A `dict[str | None, str]` of owner → **plaintext** virtual key, written at
`_mint_playground_key`, read by `_playground_bearer`, and popped only when its
own revalidation failed. Nothing dropped an entry on logout
(`auth_logout` cleared the cookie and nothing else), on the admin
disable/delete path, or at shutdown, and the dict had no ceiling.

- **Trigger:** N users each use the Playground once and log out → N plaintext
  keys stay resident for the process lifetime, each still matching a live DB
  row, and the dict grows monotonically with distinct owner ids.
- **Fix:** replaced the bare dict with `_PgBearerCache` (bounded LRU, max
  `_PG_BEARER_CACHE_MAX = 256`, recency refreshed on use so an active owner is
  not the one evicted), stored on `AppState.pg_bearers`; dropped on logout and
  on `disabled is True` in the admin user PATCH.

### 309. Every admin shared one playground key, budget, and per-owner cap

`wiwi/server/app.py` — `owner_id = None if actor.role == "admin" else actor.id`
in both `_mint_playground_key` and `_playground_bearer`.

All admins resolved to `owner_id=None`, so they shared a single virtual key, a
single spend/budget, the single `_pg_bearer_cache[None]` slot (each mint
overwrote it), and a single `_MAX_PLAYGROUND_KEYS_PER_USER` count — because
`_mint_playground_key` counts `count_keys(owner_id=None, alias="playground")`
across all admins at once.

- **Trigger:** a sixth admin uses the Playground → the cap check fires
  `expire_keys(owner_id=None, alias="playground", keep_newest=4)`, retiring a
  key another admin is actively using; that admin's next request fails
  revalidation and silently re-mints into the shared slot, so the two accounts
  end up spending from one budget.
- **Fix:** `_pg_owner_id(actor)` gives every real account — admins included —
  its own owned key, so the per-owner cap and budget are counted per admin.
  The synthetic master actor (`UserInfo(id="master")`, no `users` row) keeps an
  un-owned key deliberately: the auth owner check uses `EXISTS` and fails
  closed, so an owned master key would never authenticate. Real user ids are
  `"u" + 16 hex`, so `id == "master"` is an exact, await-free discriminator.
  Verified this does not newly expose admins to `max_keys_per_user` (default
  50, versus a playground alias cap of 5 that is trimmed first).

### 311. The Cline OAuth callback URL was built from the untrusted ASGI scheme

**Severity:** 🟡 Medium · **Status: fixed**
**File:** `wiwi/server/app.py` — `_request_base` (serves `_cline_oauth_auto_connect`)

**What:** `_request_base` composed `f"{request.url.scheme or 'https'}://{host}"`.
That is the same untrusted input as #310: behind an external TLS terminator
(uvicorn only maps `X-Forwarded-Proto` for `forwarded_allow_ips`) the scheme is
http, so the `callback_url` handed to Cline in the auto-connect flow was emitted
as `http://…` and the authorization code came back over plaintext. `public_url`
masked it only where the operator had set it; the default path was wrong.

**Fix:** the scheme now comes from `_request_is_https(request,
config.general_settings.trusted_proxies)` — https, or `X-Forwarded-Proto: https`
from a trusted peer. `Host` handling is unchanged (`X-Forwarded-Host` stays
unhonoured — client controlled, AUDIT #73's neighbourhood).

**Verification:** `tests/test_fix_round105.py` — the auto-connect `auth_url`'s
`callback_url` is `https://…` behind a trusted proxy and `http://…` otherwise.

---

## ✅ Fixed — round 106 (2026-09-28)

Playground wrapper follow-up from the round-105 audit: the session cookie's
`Secure` flag behind a TLS-terminating proxy. Pinned by
`tests/test_fix_round106.py`.

### 310. Session cookie lacked `Secure` behind an external TLS terminator

**Severity:** 🟡 Medium · **Status: fixed**
**File:** `wiwi/server/app.py` (`_set_session_cookie` call sites; new `_request_is_https`)

**What:** all three session-cookie paths (signup, master login, user login) derived
`secure=request.url.scheme == "https"` from the ASGI scheme alone. Uvicorn maps
`X-Forwarded-Proto` into the scheme only for `forwarded_allow_ips` (loopback by
default), so behind an *external* TLS terminator (e.g. the HuggingFace Space's) the
scheme is http and the cookie was set without `Secure` — any same-host plain-HTTP
path would then carry the session cookie in cleartext.

**Fix:** `_request_is_https()` — true when the scheme is https, or when
`X-Forwarded-Proto: https` is presented by a direct peer in
`general_settings.trusted_proxies` (left-most entry honoured, mirroring
`_client_ip`'s reading of `X-Forwarded-For`, AUDIT #73). Header injection from an
untrusted peer stays inert; regressions in `tests/test_fix_round106.py`.

**Not changed:** uvicorn's own `proxy_headers`/`forwarded_allow_ips` handling — an
operator who prefers native mapping can still set `forwarded_allow_ips`, and
direct-TLS deployments keep working through the scheme check.

---

## ✅ Fixed — round 104 (2026-09-27)

Router audit. Every finding below was reproduced against the pre-fix source
(the whole set fails before the fix and passes after); pinned by
`tests/test_fix_round104.py`.

### 303. A full `rpm` window reported its horizon from an unrelated stale `tpm` event

**Severity:** 🟠 High · **Status: fixed**

**Where:** `wiwi/router/router.py` — `Deployment.retry_after_s`.

**Trigger:** a deployment with both `rpm` and `tpm` set, where the `rpm` window
is saturated by requests admitted just now while a cheap request 50 s old is
still inside the `tpm` window (it is then the globally oldest event).

**Consequence:** the scan took the oldest event across *both* windows, not the
saturated one. With `rpm: 3, tpm: 10_000` and three just-admitted requests it
returned **11 s**; the `rpm` window was in fact blocked for the rest of the
minute. The value is what the caller puts in `retry_after` and what the 429
message reports, so every client that honoured it retried straight back into
the cap — a retry storm against a deployment already at its limit.

**Fix:** only a saturated window constrains admission (admission needs *every*
limited window to have room), so the answer is the **latest** of the saturated
windows' clears. Unsaturated case returns `1`. The pre-existing
`wiwi/ratelimit/redis.py:compute_retry_after_seconds` already takes the
saturated-window view; this brings the in-memory window to parity.

**Verification:** `test_retry_after_uses_rpm_window_not_a_stale_tpm_event`
(11 → >30), the symmetric `…_tpm_window_when_tpm_is_the_saturated_one`, and
`test_retry_after_is_one_when_no_window_is_saturated` (60 → 1).

### 304. `resolve_group`'s 8-hop budget truncated legitimate alias chains

**Severity:** 🟡 Medium · **Status: fixed**

**Where:** `wiwi/router/router.py` — `Router.resolve_group`, `for _ in range(8)`.

**Trigger:** `model_group_alias` with 8 or more links — e.g. a provider alias
whose name is itself an alias that hops onward — where the chain is acyclic and
resolves to a real group.

**Consequence:** the walk stopped after 8 hops and fell through to the
provider-alias arm and then to `(None, [])`, so a model the gateway can serve
returned 404. Cycle detection is `seen`'s job, and it already fails closed
(`AUDIT #109`), so the budget only ever cut off valid configurations; config
validation can reject direct cycles but not chained ones.

**Fix:** the loop is now `while True`, terminated by the `seen` cycle guard.
`test_long_alias_cycle_still_fails_closed` pins that removing the budget does
not let a long cycle resolve arbitrarily.

**Verification:** `test_long_alias_chain_resolves[7|8|12]` — 8- and 12-hop
chains go from `(None, [])` to `('real', [1 deployment])`.

### 305. The provider half of `cycle_every_n` was a permanent no-op

**Severity:** 🟠 High · **Status: fixed**

**Where:** `wiwi/router/router.py` — `execute_with_retries`, the
`provider_consec` exclusion block.

**Trigger:** `cycle_every_n > 0` with lopsided provider weights (e.g. 50:1 or
20:1) on a multi-provider group, over more than a few requests.

**Consequence:** the key-level cadence consumes its counters (`AUDIT #226`),
but the provider-level loop only *read* `provider_consec` and never cleared it.
Each success incremented it, so both providers soon sat at `>= cycle_n`, the
exclusion set covered every deployment, `pick_deployment` fell back to its
relaxed pass, and the exclusion was permanently ignored. Measured: max run
**20 in a row** for the heavy provider with `cycle_every_n=2`, and counters
reaching `{'pA': 115, 'pB': 5}`; `cycle_every_n` 0 and 3 produced
*identical* sequences. The setting was indistinguishable from disabled.

**Fix:** pop the provider's credit when its exclusion is applied — the same
consume-on-use shape as the key-level loop directly below it. The counters now
bound themselves at `cycle_n` and the run is capped at `cycle_n`.

**Verification:** `test_provider_cycle_cadence_fires_under_skewed_weights`
(20 → ≤2), `test_provider_cycle_counters_do_not_saturate` (`{pA:115}` → ≤3),
`test_provider_cadence_is_not_a_no_op`.

### 306. `weight: 0` / negative weights were accepted on keys and deployments

**Severity:** 🟡 Medium · **Status: fixed**

**Where:** `wiwi/config.py` — `KeyDef` and `DeploymentParams`.

**Trigger:** `weight: 0` or a negative value in `wiwi.yaml` (or the admin
PATCH/build path) for a provider key or a `model_list` deployment.

**Consequence:** smooth WRR *adds* every candidate's weight to its deficit each
round. `weight: 0` on 2-deployment group handed **100 %** of traffic to the
zero-weight entry and starved the healthy peer to 0; `weight: -1` starved the
peer permanently by driving its deficit negative. `rpm`/`tpm` were already
validated as positive (`AUDIT #101`) and the admin API clamps weight to
`>= 1`, so the YAML path was the inconsistent one and accepted a config that
silently destroys the routing it was meant to configure.

**Fix:** `weight` must be `>= 1` on both models; validated at config load with
a clear `ConfigError` naming the offending path.

**Verification:** `test_nonpositive_key_weight_rejected[0|-1]`,
`test_nonpositive_deployment_weight_rejected[0|-3]`,
`test_default_weights_still_accepted` (the default of 1 is unaffected).

### 307. Duplicate `(provider, model)` pairs in one group starved the second entry

**Severity:** 🟡 Medium · **Status: fixed**

**Where:** `wiwi/router/router.py` — `_CrossProviderWRR.pick`, `_dep_cursors`.

**Trigger:** a multi-provider model group whose `model_list` lists the *same*
provider and model twice (with different weights, or as two identical rows).
The YAML path permits it; only the admin attach endpoint rejects an exact
duplicate.

**Consequence:** the second-level cursor was keyed by `(provider, model_id)`,
so both `Deployment` objects added their weight to **one** shared deficit and
then raced for the same `max`. The first object in list order always won the
tie and the second could never be selected — with weights 3:1 inside provider
`pA`, the 3-weight entry took 160 of 200 picks and the 1-weight entry took
**0**, instead of the intended 150/50. Silent, permanent starvation of a
configured deployment.

**Fix:** the cursor is keyed by the live `Deployment` object (`id`).
`rebuild_cross_provider_pools` recreates the cursor whenever the admin API
changes a group's provider set, and `_build` constructs it after all
deployments exist, so the keys stay valid for the object's lifetime.

**Verification:** `test_duplicate_provider_model_pair_rotates` — the starved
entry goes from 0 → ~40 picks of 200 (3:1 split holds).

---

## ✅ Fixed — round 101 (2026-09-26)

Review of the uncommitted round-97…100 batch. The gateway half is recorded in
#290 (the `resume_mode != "off"` guard was missing, so the shipped default
resumed mid-stream after content on a rebuilt original request); it is now
pinned by `tests/test_fix_round101.py`.

### 301. The Playground key-failure notice was hidden below 1024px

**Severity:** 🟡 Medium · **Status: fixed**

**Where:** `web/src/pages/Playground.tsx` — the `keyFailed` notice in the top bar.

**Trigger:** a key mint that fails four times (or returns a non-auth error) on
any viewport narrower than `lg`, e.g. a phone at 375px.

**Consequence:** the span carried `hidden … lg:flex` and it is the only caller
of `retryKey`, so neither the message nor the Retry button ever rendered. The
mint effect had already exhausted its backoff and left no timer, so `bearer` and
`keyReady` stayed false: the composer, model selector and Send remained disabled
on "Preparing your playground key…" with no visible cause and no recovery short
of reloading the page.

**Fix:** the notice is always rendered; under `lg` it takes its own full-width
row (the top-bar cluster now wraps) and sits inline from `lg` up. Retry is a
44px target with a focus-visible ring.

**Verification:** headless Chromium at 375×812 against the running dev server,
with `**/auth/playground-key` routed to 500 — notice visible, 351px wide, Retry
44px, `scrollWidth` still 375; removing the route and clicking Retry returned
the composer to `enabled` / "Message the model…". Desktop 1440×900: no
horizontal overflow. No automated test (this repo has no web test harness);
`bun run lint` + `bun run build` stay green.

### 302. Playground tap targets, duplicate live region, and label-in-name

**Severity:** ⚪ Low · **Status: fixed**

**Where:** `web/src/pages/Playground.tsx` — empty-state category tabs, the
scroll-to-bottom button, `SessionStatus`, and the Console link.

**Trigger:** touch at 375px; any screen-reader user; voice control.

**Consequence:** the tabs were `min-h-9` (36px) and the scroll button `h-9 w-9`
— under the binding 44px floor while every neighbouring control in the same
patch was raised to 44px. `SessionStatus` emitted a polite live region whose
label is `display:none` below 640px, so phones announced a state whose text the
sighted user cannot see, and it duplicated the message log's own live region.
The Console link carried `aria-label="Open dashboard"` while its visible label
is "Dashboard" (≥`sm`) / "Console" (<`sm`) — neither matches the accessible name
(WCAG 2.5.3).

**Fix:** 44px on both controls. `SessionStatus` is a single decorative element
(`aria-hidden`, no `role`/`aria-live`): the state it shows is already carried by
the message log, the composer placeholder, and the send button's disabled state.
The link's `aria-label` is dropped, so its accessible name is its visible text at
both widths.

**Verification:** measured in the browser at 375×812 and 1440×900 — tabs 44px,
one `.pg-status` element with `aria-hidden="true"`, no `role=status` wrapper
around it, Console link `aria-label` null, `scrollWidth == innerWidth` at both
widths.

---

## ✅ Fixed — playground workbench UI (2026-09-25)

### Mobile empty workbench scrolled its launcher off-screen

**Severity:** ⚪ Low · **Status: fixed**

**Where:** `web/src/pages/Playground.tsx` — the `useLayoutEffect` that keeps a
conversation pinned to the newest message.

**Trigger:** opening an empty Playground on a short phone viewport, where the
prompt launcher is taller than the message viewport.

**Consequence:** the initial empty render was treated as an at-bottom
conversation, so the scroll container jumped to the bottom and hid the title,
model context, and the first suggestions behind the composer.

**Fix:** an empty message list explicitly resets the scroll position to the
top; only non-empty conversations follow the existing stick-to-bottom behavior.
The workbench launcher also top-aligns on phone and short-height layouts.

**Verification:** headless Playwright smoke at 375×812 and 1440×900, including
the model menu and a mocked streaming response; no horizontal overflow and no
console errors.

---

## ✅ Fixed — round 98 (2026-09-25)

### 290. A combo could not fail over when OpenRouter's first SSE event was an error

**Severity:** 🟠 High · **Status: fixed**

**Where:** `wiwi/core/gateway.py::Gateway.stream` mid-stream resume branch.

**Trigger:** `stream_resume: enabled` and the selected combo deployment committed
an HTTP `200`, then OpenRouter sent a valid error event as the first and only SSE
frame (for example `provider_unavailable` / `JSON error injected into SSE stream`)
before any content delta reached the client.

**Consequence:** normal pre-connect retries were already unavailable because the
`200` was committed, while mid-stream resume was gated on `content_flowed`. The
error reached the client and a healthy sibling deployment in the same combo was
never called.

**Fix:** `enabled` resume now also runs for a first-event `StreamError`; an empty
tape rebuilds the unchanged request, so there is no partial response to replay.
`off` and `content_only` keep their existing behavior. That last sentence needs
an explicit guard to stay true: the branch carries `resume_mode != "off"`, which
is the *only* place the setting is enforced — `_attempt_resume` refuses just
`content_only`, and with the default `off` the tape is never recorded, so
dropping the guard made the shipped default resume after content on a rebuilt
original request. The client then received its partial answer followed by a full
regenerated one, and paid for both.

**Regression:** `tests/test_fix_round98.py::test_openrouter_first_event_error_resumes_healthy_combo_deployment`
(no-content, `enabled`), `tests/test_fix_round101.py` (content already flowed:
`off` must not call a fallback; `content_only` still must).

### 291. A failed stream pump could emit a second terminal and be orphaned by resume

**Severity:** 🟠 High · **Status: fixed**

**Where:** `wiwi/core/gateway.py::Gateway._pump_once` and the resume branch in
`Gateway.stream`.

**Trigger:** an adapter emitted `StreamError` (including OpenRouter's committed-
`200` error frame) before the upstream body closed, and mid-stream resume then
selected another deployment.

**Consequence:** the failed pump kept reading and treated the later EOF as a
second truncation, queueing another `StreamError`. The consumer could terminate
on that stale error before the fallback produced content. If the body stayed
open, overwriting the outer pump reference orphaned the old task, retaining its
upstream connection and deployment inflight count.

**Fix:** the pump routes an adapter `StreamError` through its single guarded
terminal path, preserves its status/type for client and key-health semantics,
prices the partial turn, and stops reading. Before the consumer replaces
`pump_task` with the resumed pump, it registers a completion owner, waits
for the failed task to finish, and cancels it after the bounded teardown grace.
This ownership is installed before the first await, so consumer cancellation
cannot bypass it. A task that suppresses cancellation remains explicitly tracked
until its cleanup completes, rather than becoming an unowned task with an
unretrieved exception.

**Regression:** `tests/test_fix_round98.py` covers delayed fallback ordering,
failed-pump settlement, bounded cancellation cleanup, provider error metadata,
one logical start/terminal, and disabled resume modes.

---

## ✅ Fixed — round 97 (2026-09-25)

### 289. Playground displayed client-estimated TPS and TTFT instead of server measurements

**Severity:** 🟡 Medium · **Status: fixed**

**Where:** `web/src/pages/Playground.tsx` (`streamSSE`, `runStream`, and the
response-stat footer).

**Trigger:** a streamed completion with provider/network queueing or a short
output turn. The page timed the first browser-visible delta and divided output
tokens by the browser's full request wall time.

**Consequence:** the footer could show values that disagreed with wiwi's request
log, because it included client transport time and did not use the gateway's
TTFT, generation-phase TPS, or total request latency. Concurrent chats also had
no stable request-id correlation in the UI.

**Fix:** the gateway now exposes an authenticated, owner-scoped exact-ID metrics
lookup at `/admin/logs/requests/{request_id}/metrics`; Playground reads
`x-wiwi-request-id`, polls that record after the stream, and displays the
server-recorded token counts, TTFT, TPS, and total latency. The browser-observed
timing calculation was removed.

**Regression:** `tests/test_fix_round97.py` covers exact ring/DB correlation,
estimated-usage labeling, authentication, and cross-user isolation.

---

## ✅ Fixed — round 100 (2026-09-25)

### 300. Playground 401s on every send once the account passes the playground-key cap

**Severity:** 🟠 High · **Status: fixed**

**Where:** `web/src/pages/Playground.tsx` — the completion `fetch` inside
`runStream` (pre-fix, a single inline `fetch` with no status branch), and
`web/src/api/auth.tsx:ensurePlaygroundKey` (pre-fix, no way to bypass its own
cache). Server side, unchanged: `wiwi/server/app.py:4021`
(`_mint_playground_key`, the `_MAX_PLAYGROUND_KEYS_PER_USER = 5` cap at
`app.py:187`) and `wiwi/auth/service.py:634` (`expire_keys`).

**Trigger:** any account that mints a sixth playground key — five logins is
enough for the oldest, *currently cached* key to be retired. `sessionStorage`
keeps that key for the life of the tab, and `bearer` component state pins it
from then on. Note this is the **cap path**, not the 24h TTL: the DB showed the
first EXPIRED key at 1.1h old, consistent with the cap evicting on the sixth
login.

**Consequence:** the Playground was permanently broken with
`401 {"error":{"message":"invalid API key"}}` on every `/v1/chat/completions`,
while `/admin/models` returned 200 in the same breath — the session cookie is
independent of the virtual key, so the UI looked authenticated while every
completion was rejected. There was no recovery path short of logging out and
back in, which was the only user-visible workaround.

Reproduced against the live gateway: 7 master-key logins, then the first
minted key → 401; the newest key → 404 `model not found` (i.e. auth passed,
routing was the only thing left). With the cap at 5 and `_MAX_KEY_ATTEMPTS`
retries in the mint effect, an ordinary multi-tab session crossed it easily.

**Fix:** the client half. `runStream` now treats a 401 as "my cached bearer is
dead" rather than a terminal error: it re-mints through
`ensurePlaygroundKey(true)` and replays the identical request once, bounded by
a `retried` flag so a genuinely revoked account (where the re-mint itself 401s)
surfaces the real error instead of looping. The new key is written back to
`bearer` so later turns skip the round trip.

`ensurePlaygroundKey` gained a `force` parameter for this. Calling it as before
returns the cached key — so the first attempt at the fix replayed *the same
dead bearer* and 401'd again, which is exactly what the browser run below
caught. A 401 is exactly the evidence that the cache is stale, so the retry
must skip it.

Not changed, deliberately: the cap and the TTL are correct as designed. They are
the mitigation for #57 (unbounded playground keys), and a login returning one
short-lived key is a sound shape — the bug was that nothing on the client
noticed when that key died.

**Verification:** headless browser against the running dev servers. Sign in,
open `/playground`, then mint 7 more keys from the same session to push the
page's cached bearer past the cap. Network trace, before → after:

```
before:  POST /v1/chat/completions  401
         POST /v1/chat/completions  401     (same bearer replayed)
         → error banner "invalid API key", Retry button

after:   POST /v1/chat/completions  401
         POST /auth/playground-key  200
         POST /v1/chat/completions  200     → "pong" rendered, no banner
```

**Regression:** `tests/test_fix_round100.py` — the cap must retire the *oldest*
live key (the retry depends on the freshly minted one being usable), re-minting
at the cap must yield a live key, and the client must carry a bounded
401 → forced re-mint → retry branch (`ensurePlaygroundKey(true)`, not the
cache-reading form).

---

## ✅ Fixed — combos editor UI (2026-09-24)

Found while redesigning the `/console/combos` create/edit dialog. Both were
introduced by that redesign and fixed in the same change, so they carry no
entry number.

### "all selected" ignored the provider when a model_id is aliased

**Severity:** 🟠 High · **Status: fixed**

**Where:** `web/src/pages/Combos.tsx` — the `allOn` computation backing the
pane header's "all selected" badge and its select-all/clear button.

**Trigger:** a search whose results span a model_id registered under more than
one provider — real in this data (`glm-5.3-flash` is deployed under `civ`,
`cov` and `pai`; `cline-free/solar-pro4` under `22`, `3` and `shinm`).

**Consequence:** `allOn` was computed over a flat list of bare `model_id`
strings, so a single provider's copy satisfied `every()` for every occurrence of
that id. Ticking only `civ`'s `glm-5.3-flash` lit "all selected" and flipped
the button to "Deselect", which then cleared the wrong set.

**Fix:** key the check on the full `(provider, model_id)` pair via `depKey`, the
same identity the selection map and the save path already use.

**Regression:** `.verify/combos_ui.py::check_alias_pair` (desktop pass) — picks
a live aliased id from `/admin/models`, asserts 1-of-N ticked does not light the
badge and N-of-N does.

### A providers refetch could wipe in-progress edits in the open dialog

**Severity:** 🟠 High · **Status: fixed**

**Where:** `web/src/pages/Combos.tsx` — the reset `useEffect` that re-initialises
dialog state on open.

**Trigger:** `props.providerOptions` listed as a dependency. It is a `useMemo`
over the `/admin/providers` query, so any refetch returning changed data gives it
a new identity and re-ran the effect, resetting the name field, the selection,
the search box and the active provider.

**Consequence:** silently discarded a half-typed combo name and every ticked
model. `refetchOnWindowFocus` is on by default, so a tab switch was enough. This
also contradicted the page's own comment above `modelsQ` ("background refetches
never clobber in-dialog selections").

**Fix:** read `providerOptions` through a ref, so the effect depends only on
`[props.open, props.editing]` — the dialog opening and the edit target, nothing
else. The active-provider hint still reads the current options at that moment.

**Note:** could not be reproduced under headless Playwright (window-focus
refetch does not fire there), so this is fixed from source reading, not from a
failing browser repro.

---

## ✅ Fixed — round 96 (2026-09-24)

### 286. Repaired OpenRouter tool arguments were replaced by truncated JSON

**Severity:** 🟠 High · **Status: fixed**

**Where:** `wiwi/providers/openai_adapter.py::_role_parts_to_content`,
`wiwi/wire/openai_chat.py::encode_response`,
`wiwi/wire/openai_responses.py::encode_response`,
`wiwi/providers/opencode_adapter.py::_encode_responses_request`.

**Trigger:** an OpenRouter response ended mid tool-argument string, for example
`{"path":"/tmp/notes.txt"`. The decoder repaired the IR `args` to
`{"path": "/tmp/notes.txt"}`, but every outbound boundary preferred the original
`raw_args` and sent the truncated text again.

**Consequence:** the caller received malformed function arguments, and replaying
the assistant turn sent the same malformed string to OpenRouter. This is the
locally reproduced cross-dialect failure; the stored historical OpenRouter
failures only prove upstream 404/402 responses and do not establish that they
used this exact trigger.

**Fix:** all client/upstream wire boundaries now serialize the canonical parsed
`ToolUsePart.args`. `raw_args` remains diagnostic/provider history only.

**Regression:** `tests/test_fix_round96.py::test_truncated_openrouter_response_round_trips_valid_arguments`.

### 287. Typed-wrong OpenRouter frames escaped the decoder as gateway errors

**Severity:** 🟠 High · **Status: fixed**

**Where:** `wiwi/providers/openrouter_adapter.py::decode_response` and
`decode_stream_event`; `wiwi/providers/base.py::_extract_error_message`.

**Trigger:** an upstream frame used a valid HTTP 200 with `choices`,
`usage`, `function`, `reasoning_details`, tool ids/names, or `error.metadata`
in a typed-wrong shape.

**Consequence:** `.get` on a string/list raised inside decoding, which the guarded
provider path converted to a retryable gateway error and could charge against
key/deployment health. Assistant content arrays were also assigned to a
text-only IR field, and malformed reasoning text could reach a later replay.

**Fix:** nested objects use `as_dict`/`as_list`, text and identifiers use
string guards, reasoning details are dropped/coerced to text-only IR values,
array assistant content is flattened, and non-dict error metadata is ignored.

**Regression:** the typed-shape cases in `tests/test_fix_round96.py`.

### 288. Invalid reasoning and hosted tool choices produced bad OpenRouter requests

**Severity:** 🟡 Medium · **Status: fixed**

**Where:** `wiwi/providers/openrouter_adapter.py::encode_request` and
`wiwi/providers/openai_adapter.py::encode_request`.

**Trigger:** unknown/non-string `reasoning_effort`, an Anthropic thinking budget
at or above `max_tokens`, or a hosted Responses choice such as
`tool_choice: {"type":"web_search"}`.

**Consequence:** a list effort could raise `TypeError: unhashable type: 'list'`;
an unknown string/boolean reached OpenRouter despite its closed effort schema;
`max_completion_tokens` could be less than or equal to `reasoning.max_tokens`;
and a hosted choice was encoded as a client function named `web_search`.

**Fix:** the shared encoder accepts only string efforts from its closed set;
OpenRouter emits only documented effort values, raises completion limit to
budget + 1024 when required, and maps a named choice to
`{"type":"openrouter:web_search"}` only when the named tool is actually a
hosted builtin. A client function named `web_search` remains a function choice.

**Regression:** `tests/test_fix_round96.py` reasoning-effort, budget, and
hosted-tool-choice cases. Official schemas:
https://openrouter.ai/docs/guides/best-practices/reasoning-tokens and
https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion.md.

---

## ✅ Fixed — round 93 (2026-09-21)

### 285. The streaming pump silently dropped every line from an envelope provider

**Severity:** 🟠 High · **Status: fixed**

**Where:** `wiwi/core/gateway.py` (`_pump_once`, `_complete_via_stream`),
`wiwi/streaming/sse.py` (`LineSSEParser`), `wiwi/core/recovery.py`
(`_body_is_error_envelope`).

**Trigger:** a streaming client against Cline or WorkBuddy — both
`force_stream`, so the envelope shape reaches every streaming caller — or a
health probe classifying one of their 200 bodies.

**Consequence:** those providers answer a "streaming" request with **one whole
JSON object per line** rather than framed SSE. `LineSSEParser.feed_line` only
recognises blank lines, `:` comments, `event:`, and `data:`, so an unprefixed
line fell through and was discarded. In the pump the effect was total: every
content delta vanished, so the client received an empty turn and a spurious
`"upstream stream ended without completion"` error — reproduced end-to-end
(zero content streamed). #284 fixed the *non-streaming* arm; the streaming pump,
which is the path those providers actually serve, still had the blind spot, so
the fix had been applied to the less-used half. `_body_is_error_envelope` shared
it too, which could classify a 200 error envelope as a healthy probe.

**Fix:** the leniency lives in the parser as an explicit `allow_unframed=True`
opt-in (default unchanged, so a strict caller still drops a malformed line), and
the three call sites that parse upstream bodies pass it. Centralising it
replaced three hand-rolled per-call-site fallbacks — the second-convention
duplication `AGENTS.md` prohibits — with one mechanism.

**Regression:** `tests/test_fix_round93.py` — opt-in is off by default, the
unframed line reaches the adapter and yields its `Finish`, and
`test_all_envelope_call_sites_opt_in` pins *every* call site (verified to fail
when any one drops the flag).

---

## ✅ Fixed — round 93 (2026-09-21)

### 280. Loop detector missed the commonest runaway shape and all reasoning loops

**Severity:** 🟠 High · **Status: fixed**

**Where:** `wiwi/streaming/loopdetect.py`, call site `wiwi/core/gateway.py`
(`_pump_once`).

**Trigger:** a model stuck restating the same sentence or paragraph — the
commonest degenerate loop — or looping inside its reasoning trace.

**Consequence:** three defects, all silent:

1. `MAX_LOOP_PERIOD` was 8, so periods 9..32 were **never detected**. The
   module docstring justified the cap by claiming "a genuine period-40 loop does
   not fit the 'same chunk repeatedly' failure mode" — but a restated one-line
   apology is 10-40 wire chunks, i.e. exactly the class the cap excluded.
2. `_max_period = min(8, limit // 2)` meant a *smaller* configured
   `stream_loop_limit` silently narrowed period coverage (`limit=20` could only
   see periods 1..8) instead of merely raising the repeat count required —
   backwards from the knob's meaning.
3. Only `TextDelta` was fed, so a model looping in its thinking trace was
   invisible while the turn burned to the token limit.

**Fix:** the cap is 32 (covering the restated-sentence class at 32 integer
compares per chunk, far below the per-chunk JSON work it guards); period
coverage is independent of the limit, with the threshold floored at 1 so a
period at or above the limit still needs a confirming repeat; `feed` is
chunk-type-agnostic and ignores empty chunks (so empty keep-alives cannot trip
it); the gateway feeds `ThinkingDelta` too.

**Regression:** `tests/test_fix_round93.py` (params 1..32, limit-independence,
reasoning coverage, empty-chunk immunity, 20k-token false-positive check).
The performance bound in `test_fix_round10.py` moved 8 us → 32 us with the
4x-wider window; its flatness assertion (the real invariant) is unchanged.

### 281. A failed mid-stream resume leaked its pump and dropped its partial usage

**Severity:** 🟡 Medium · **Status: fixed**

**Where:** `wiwi/core/gateway.py` — `_attempt_resume`, connect-failure arm.

**Trigger:** `stream_resume != "off"` and a fallback deployment that fails to
connect, after the failed attempt had already priced a partial delivery.

**Consequence:** `new_pump_task.cancel()` was fire-and-forget, so the pump's
`finally` (which releases the upstream response) might not run before the loop
moved to the next candidate — leaving the socket checked out until GC. Worse,
the failure arm never appended `resume_ctx` to `_pending_resume_ctxs`, so any
usage that attempt had already priced was charged to nobody while the
originating request reported only its own pre-failure tokens.

**Fix:** the failure arm retains the context when it carries usage (mirroring
the success path) and cancels **and awaits** the failed pump under a bounded
`wait_for`, so teardown completes without one bad fallback stalling the loop.

**Regression:** `tests/test_fix_round93.py::test_resume_failure_path_awaits_pump_teardown`.

### 282. Reconnect tail loop re-read and re-parsed the whole journal every 50 ms

**Severity:** 🟠 High · **Status: fixed**

**Where:** `wiwi/streaming/tape_store.py`, `wiwi/server/app.py` (`_replay_iter`).

**Trigger:** any client reconnecting with `x-wiwi-stream-id` while the original
stream is still running (or while the process that owned it died mid-flight).

**Consequence:** each 50 ms tick called `read_after` (full `read_bytes` +
`orjson.loads` of every record) *and* `is_complete` (the same, again). With the
1 MiB per-journal cap that is a sustained full re-parse — measured 2.67 ms +
4.86 ms per tick on a 2000-record journal — of event-loop time, per client,
scaling with journal size, used only to discover nothing new was appended.

**Fix:** `JournalTail`, a byte-offset cursor that reads strictly forward and
advances only past complete lines (a torn tail is re-read next poll). An idle
poll is one `stat` + one empty `read`: measured 0.021 ms vs 2.67 ms, ~125x, and
the removed `is_complete` call was another 4.86 ms. `truncated()` detects the
one case that would corrupt the cursor (file shrunk by a sweep) so the caller
falls back.

**Regression:** `tests/test_fix_round93.py` (equivalence with `read_after`,
incremental append pickup, done-record, ownership skip, torn-line tolerance,
truncation, and an idle-poll cost bound).

### 283. Multi-frame chunks gave every sub-frame the same SSE id

**Severity:** 🟡 Medium · **Status: fixed**

**Where:** `wiwi/server/app.py` — `_inject_id`.

**Trigger:** `stream_event_ids: true` and a chunk carrying more than one SSE
frame (which the encoders do routinely), with the client's connection dropping
between sub-frames.

**Consequence:** every sub-frame in the chunk got the **same** `id:`, so the
client's `Last-Event-ID` could not name a single frame. On reconnect the server
replayed `seq > id`, skipping every sub-frame after the first in the chunk the
client was interrupted on — those frames were silently lost.

**Fix:** `_inject_id` returns `(bytes, last_id)` and assigns each sub-frame its
own monotonically increasing id; `_emit` threads the returned id into `_seq` and
the journal records the same numbering, so `Last-Event-ID` means one thing to
both sides.

**Regression:** `tests/test_fix_round93.py` (distinct increasing ids, the
reconnect-skip scenario, single-frame and no-frame pass-through).

### 284. The non-streaming arm called a truncated upstream a success

**Severity:** 🟠 High · **Status: fixed**

**Where:** `wiwi/core/gateway.py` — `_complete_via_stream`.

**Trigger:** a `force_stream` provider (Cline, WorkBuddy) whose body ends with
no `finish_reason` and no `[DONE]` — an abrupt close mid-turn.

**Consequence:** `aiter_lines` ends cleanly on an abrupt close rather than
raising, so the loop simply exited and the method returned a **200 success**
whose text was silently truncated. The partial tokens were billed as if the turn
had completed, and no error reached `execute_with_retries`, so no failover was
attempted. The streaming arm detects exactly this condition and emits an error;
this arm had no equivalent. Separately, a failure that *did* raise (idle
timeout, transport error) never billed its partial delivery — the streaming
pump's `_price_partial` had no counterpart here.

**Fix:** `saw_terminal` is set on `Finish`/`StreamEnd`; a body ending without
one bills the partial delivery and raises a retryable
`api_connection_error` so failover takes over. `CancelledError` and mid-read
exceptions bill the partial first (the cancel path shielded). Also fixed: a body
line with no SSE field prefix — how the Cline/WorkBuddy envelope providers
answer a "streaming" request — was silently dropped by `LineSSEParser` (not a
comment, `event:`, or `data:`), losing the whole turn; it is now fed to the
adapter as a data frame, which surfaced as a real regression in
`test_fix_round36.py`.

**Regression:** `tests/test_fix_round93.py` (unframed-line handling,
`usage_fallback` contract) plus the pre-existing `test_fix_round36.py`
envelope-provider tests.

---

## ✅ Fixed — round 92 (2026-09-21)

### 277. A full output queue swallows the terminal frame, hanging the client forever

**Severity:** 🔴 Critical · **Status: fixed**

**Where:** `wiwi/core/gateway.py` — `_put_frame` (the pump's only enqueue path)
and the `stream()` consumer loop.

**Trigger:** a consumer more than `_QUEUE_PUT_TIMEOUT_S` (5 s) behind on the
pump's `asyncio.Queue(maxsize=4096)` when the stream terminates — a slow client,
a stalled proxy, a large final `UsageFinal` burst.

**Consequence:** `_put_frame` enqueued with `put_nowait` and fell back to a
bounded `wait_for(queue.put(...))` whose `TimeoutError` was **suppressed**. On
timeout the terminal frame (`StreamEnd`/`StreamError`, and `Finish`) was
discarded. The consumer is parked on `await queue.get()` with no timeout of its
own, so the request never terminated: the client hung until its own socket
timeout, with no error and no stop reason. The docstring justified the
suppression as covering "one case it cannot" — but that case is precisely the
one where the frame is silently lost. A dropped *content* frame is recoverable
backpressure; a dropped *terminal* frame is a hang. `terminal_sent` is set only
after the put, so nothing retried the first terminal.

**Fix:** terminal frames are recognised by `_is_terminal_delta` and enqueued
through a reserved-slot guarantee: the consumer calls `_reserve_terminal_slot`
after each `get()`, keeping one queue slot free so the terminal put always
succeeds; if a slow consumer races into that slot, the terminal put falls back to
an unbounded `await queue.put(d)` — waiting for the consumer is strictly better
than never telling it the stream ended. Content keeps the bounded-wait
backpressure path.

**Regression:** `tests/test_fix_round92.py` — full-queue terminal delivery, slot
reservation/re-opening, content shedding past capacity.

### 278. Coalescer never honours its `max_ms` deadline, hoarding every token when the upstream goes quiet

**Severity:** 🟠 High · **Status: fixed**

**Where:** `wiwi/streaming/coalesce.py` — `feed`; consumer loop in
`wiwi/core/gateway.py`.

**Trigger:** `stream_coalesce: true` (opt-in) with a consumer slow enough to
push queue depth past the threshold (100), then an upstream that stops sending.

**Consequence:** the `max_ms` flush condition was only *evaluated when the next
delta arrived*. With no further upstream traffic the buffer — and every token in
it — was held until a control delta or end-of-stream, so `max_ms` acted as a
maximum lag only for a delta-tight stream and as an *unbounded* lag otherwise.
Reproduced: 300 deltas fed at depth 4096 emitted 0 frames; after 200 ms idle the
buffer was still held. This inverted the feature's purpose — the coalescer exists
to bound latency for slow consumers, not to add unbounded latency.

**Fix:** `flush_due(now=None)` releases the buffer once its deadline has passed,
`buffered_s` exposes the hold time, and the consumer's `queue.get()` wait is
bounded by `_wait_timeout()` — the earliest of the ping interval and the
coalescer's remaining deadline — so the deadline is honoured against the wall
clock instead of against upstream traffic.

**Regression:** `tests/test_fix_round92.py` — release with no further `feed()`,
idempotent flush, `buffered_s` accounting, fast-path passthrough unchanged.

### 279. `_reserve_terminal_slot`'s invariant is not covered by the pump's own tests

**Severity:** ⚪ Low · **Status: fixed**

**Where:** `wiwi/core/gateway.py` — the pump/consumer handshake introduced by
#277.

**Consequence:** the reserved-slot protocol is split across two coroutines (the
producer's `_put_frame`, the consumer's post-`get` reserve call). A future edit
that moved the reserve call before `get()`, or dropped it from the `wait_for`
timeout arm, would silently reintroduce #277 with the suite still green — the
kind of split-invariant drift the round-90 review called out elsewhere.

**Fix:** the reserve is exercised directly (`test_reserve_reopens_the_slot_so_terminals_always_fit`,
`test_reserve_drops_a_content_frame_when_consumer_is_far_behind`,
`test_reserve_is_a_noop_below_capacity`) and the predicate is pinned for every
delta type, so the split invariant has an executable specification.

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

## ✅ Fixed — multi-account Hermes + realtime follow-up (2026-10-13)

Found by reading back round 136 (`/admin/hermes/oauth/*` end to end, the Hermes
credential encode/decode path, the `hermes_auto_refresh` sweeper) and the
`/v1/realtime` WS surface against how every other Hermes deployment is shaped.
The round-136 per-account model is correct at the adapter and sweeper layer, but
the endpoints that *write* pool keys (label derivation, placeholder key,
in-place rotation, whole-pool disconnect), the console that reads `/status`,
and the realtime admission/billing path each still carry the pre-136 or
pre-existing assumptions. Entries #430-#442.

**Resolution (2026-10-14):** all thirteen entries are fixed and each carries an
inline `**Status: fixed**` line naming its round test file — #430/#431/#432/#433/
#434/#436/#441 in `tests/test_fix_round138.py`, #437/#439/#440 in
`tests/test_fix_round139.py`, #438 in `tests/test_fix_round140.py`, #442 in
`tests/test_fix_round141.py`. #435 is a console-only (TypeScript) fix with no
Python regression test. Full gate green at the time of writing: 3218 passed,
11 skipped, ruff clean.

### 430. The Hermes credential blob is stored as the pool-key label — plaintext `access_token` + `refresh_token` in the DB, the API, and every log row

**Severity:** 🔴 Critical · **Status: fixed** — round 138 (`tests/test_fix_round138.py::test_poll_label_is_the_account_label_not_the_credential_blob`; fixed 2026-10-14)

`/poll` now hands `_hermes_account_label(access)` to
`_unique_hermes_key_label` as the `base`, so the
`ProviderKey.label` is the account label and the
`hermes:{"access_token":…}` blob never reaches the label
column, `/status`, the key listing, or
`LogEvent.provider_key_label`. The regression asserts the label is
the email, not the blob.

**Files:** `wiwi/server/app.py:6081-6084` (`_unique_hermes_key_label(state,
provider, secret)`), `wiwi/server/app.py:6129` (`/status`), `wiwi/server/app.py:3409`
(generic key listing), `wiwi/logging_core/events.py:22`
(`LogEvent.provider_key_label`), `wiwi/providers/hermes_auto_refresh.py:248`
(`log.info("hermes_auto_refreshed", ...)`)

`/poll` computes the account's human label on the line above
(`_hermes_account_label(access)`, `:6083`) and passes it only into
`encode_credential`, then hands the *encoded secret blob itself* to the labeler
as its `base`. `_unique_hermes_key_label` treats `base` as a label string and
returns it verbatim when no collision exists, so the resulting
`ProviderKey.label` is the whole `hermes:{"access_token":…,"refresh_token":…}`
JSON.

Unlike `secret`, the label is **not** masked. `mask_key(k.secret)` guards the
secret in the key listing at `:3410`, but `:3409` returns `k.label`
unmasked; `/status` returns it at `:6129`; and `LogEvent.provider_key_label`
(`events.py:22`) carries it onto every request-log row this account serves,
which is rendered in `/console/request-logs` and included in
`GET /admin/backup`. The refresh sweeper's own log line
(`hermes_auto_refresh.py:248`) prints it too. So a long-lived access token and
a single-use refresh token sit in cleartext in the `provider_keys.label` column,
in the request log, and in the proxy log.

The exposure is also *permanent*: `_update_secret`
(`hermes_auto_refresh.py:251-269`) rewrites `k.secret` only, so the label keeps
the original (now dead) tokens after every rotation — the DB never ages it out.

**Fix sketch:** pass `_hermes_account_label(access)` as the labeler's `base`,
not `secret`.

### 431. The `hermes:pending` bootstrap key is created and never removed — it burns a guaranteed-401 request every rotation cycle

**Severity:** 🔴 Critical · **Status: fixed** — round 138 (`tests/test_fix_round138.py`; fixed 2026-10-14)

The bootstrap account is created with an empty key list and `/poll`
adds the first real key, so `hermes:pending` is never minted.
`/disconnect` also drops a lone placeholder by label rather than by
successful decode, so a leftover one is still removable.
`test_placeholder_helper_does_not_scatter_the_literal` pins the
sentinel to a single helper.

**Files:** `wiwi/server/app.py:5965-5979` (bootstrap `ProviderKey`), `wiwi/server/app.py:6089`
(`/poll` append), `wiwi/providers/hermes_oauth.py:276` (`credential_bearer`),
`wiwi/router/router.py:35-37` (`ProviderKey` defaults), `wiwi/server/app.py:6246`
(`/disconnect` filter)

Device-code login bootstraps a fresh provider account with
`ProviderKey(label="default", secret="hermes:pending")`, under a comment
claiming "`/poll` replaces that secret with the real `hermes:<token>` on
approval." `/poll` does not replace anything — it only **appends** a new
`ProviderKey` (`:6089`), so the placeholder survives the entire connect flow.

The placeholder is not inert. `decode_credential("hermes:pending")` returns
`None` (the body after the `hermes:` prefix is not JSON), so
`credential_bearer` falls through to its bare-prefix branch and returns the
literal string `pending` as the bearer. `ProviderKey` defaults to
`enabled=True, status="active"` (`router/router.py:35-37`), so the key sits in
smooth-WRR rotation alongside every real account and sends
`Authorization: Bearer pending` upstream on a share of all traffic — a
guaranteed 401 that trips the auto-refresh hook, which then cannot possibly
succeed for a key with no refresh token. `/disconnect` does not remove it
either: `:6246` only drops keys whose `decode_credential` succeeds, and this
one decodes to `None`.

**Fix sketch:** in `/poll`, drop the placeholder before appending — remove it
from `acct.keys` in memory **and** `await state.config_store.delete_key(
provider, "default")` — or have the bootstrap create the account with an empty
key list and let `/poll` add the first key.

### 432. `/refresh`'s `except AttributeError` fallback calls `_update_secret` on the `None` it just proved — consumes the single-use grant, then 500s

**Severity:** 🟠 High · **Status: fixed** — round 138 (`tests/test_fix_round138.py`; fixed 2026-10-14)

The `except AttributeError:` fallback is gone; the handler branches
explicitly on `state.hermes_refresh is None`. With no lifespan
worker, the threaded exchange writes the new secret to the pool key
directly and returns a relogin-required body instead of 500ing.

**Files:** `wiwi/server/app.py:6180-6212` (the fallback block), `wiwi/server/app.py:890`
(`state.hermes_refresh` initialized `None`), `wiwi/providers/hermes_oauth.py`
(`refresh_access_token`)

The intent of `except AttributeError:` at `:6181` is to catch
`state.hermes_refresh is None` (the attribute is initialized to `None` at
`:890`), and fall back to a direct threaded exchange. The fallback body then
does two things that defeat it: `:6193` calls
`state.hermes_refresh._update_secret(...)` on the object it just established is
`None`, and `:6208` reads `state.hermes_refresh._circuit` unconditionally.

Order matters and it is the wrong order. The threaded exchange at `:6186` runs
**first** and succeeds, rotating Nous's single-use refresh token server-side;
only then does `:6193` raise `AttributeError` on `None` — uncaught, because it
is raised inside an `except` handler. The endpoint 500s. The freshly minted
access token is discarded, the pool key still holds the now-consumed grant, and
the next refresh attempt gets `invalid_grant` → circuit dead → a human re-login,
triggered by one admin click.

Two secondary problems with the same lines: the fallback also masks genuine
`AttributeError`s raised *inside* `_refresh_locked` after a grant was consumed
(they take the same branch and 500 at the same place), and `:6208`'s
`state.hermes_refresh._circuit` is reached on the normal path too, so any
non-`AttributeError` failure from the worker leaves the same latent `None`
dereference.

**Fix sketch:** branch explicitly — `if state.hermes_refresh is None:` → inline
the exchange *and write the new secret directly to the pool key*; `else:` → call
`refresh_key_now(...)` — instead of catching `AttributeError`.

### 433. Whole-pool Hermes disconnect is memory-only — every account comes back after a restart

**Severity:** 🟠 High · **Status: fixed** — round 138 (`tests/test_fix_round138.py::test_whole_pool_disconnect_persists`; fixed 2026-10-14)

Labels removed in memory are collected and each one is passed to
`config_store.delete_key` unconditionally, so the whole-pool path is
as durable as the single-account one.

**Files:** `wiwi/server/app.py:6252-6254`, `wiwi/server/config_store.py:647-680`
(`ConfigStore.load_all`)

`/disconnect` filters `acct.keys` in memory and then calls
`config_store.delete_key` only `if label:`. The whole-pool disconnect — the
path an operator uses to remove *every* Nous account at once, i.e. the one that
should be unambiguous — therefore persists nothing. It clears the legacy
settings record and reports `disconnected: <count>`, so the UI shows the pool as
empty and the admin believes the tokens are gone.

They are not. `ConfigStore.load_all()` reloads every `provider_keys` row for
the account at startup and re-injects them into the router, so the accounts (and
their plaintext credential blobs) reappear on the next process start. The
single-account path does delete its row, so the two paths disagree about whether
disconnect is durable.

**Fix sketch:** collect the labels actually removed in the loop and
`await state.config_store.delete_key(provider, lbl)` for each, unconditionally.

### 434. Re-login appends a duplicate Hermes key instead of rotating that account in place — the stale copy then kills the account's circuit

**Severity:** 🟠 High · **Status: fixed** — round 138 (`tests/test_fix_round138.py::test_relogin_rotates_the_same_account_in_place`; fixed 2026-10-14)

`/poll` scans `acct.keys` for a key whose decoded credential label
matches the incoming account and overwrites that key's secret and DB
row instead of appending.

**Files:** `wiwi/server/app.py:5939-5953` (`_unique_hermes_key_label`),
`wiwi/server/app.py:6084-6089` (`/poll` label + append)

`_unique_hermes_key_label`'s docstring promises: "a second connect for the SAME
account reuses its existing label (so a re-login rotates that account in
place)." Neither half holds. `/poll` unconditionally appends a new
`ProviderKey` (`:6089`) rather than overwriting, and — see #430 — the dedup
`base` was the credential blob, so the base differs on every connect (a new
access token each time) and the "already taken" branch could never match even in
principle. Every connect, including a re-login of one account, silently
duplicates it.

The duplicate is not a harmless extra row. The stale key keeps the *previous*
single-use refresh grant, which the new login has already consumed. The sweeper
eventually presents it, Nous answers `invalid_grant`, and the account's circuit
is marked permanently dead — the AUDIT #169 failure mode, reintroduced through
a path that only appears on the second connect.

**Fix sketch:** before appending, scan `acct.keys` for a key whose
`decode_credential(k.secret).get("label")` equals the new account's label; if
found, overwrite that key's secret (and DB row) instead of appending.

### 435. The admin console was never updated for the multi-account Hermes `/status` — the Refresh button is permanently disabled

**Severity:** 🟠 High · **Status: fixed** (no Python test — console-only; fixed 2026-10-14)

`HermesStatusResponse` now declares the `accounts` array
(`web/src/api/types.ts`), and the new
`web/src/components/HermesAccounts.tsx` renders one row per account
(label, expiry, managed badge, per-row Refresh/Disconnect) with the
whole-pool action kept separate. `web/src/pages/HermesOAuth.tsx` and
`web/src/pages/ProviderDetail.tsx` both mount it via the `@/` alias.
`key_label` now threads through `hermesRefresh`/`hermesDisconnect`
(`web/src/api/client.ts`), and `disconnected` is a count. **There is
no Python regression test for this one** — it is a TypeScript-only
change and is verified by `tsc -b` + eslint at build time, not by
pytest.

**Files:** `web/src/api/types.ts:327-332` (`HermesStatusResponse`),
`web/src/pages/HermesOAuth.tsx:314,350,379`, `web/src/pages/ProviderDetail.tsx:1398-1418`,
`web/src/api/client.ts:484-492` (`hermesRefresh`/`hermesDisconnect`)

`HermesStatusResponse` still declares the pre-round-136 flat shape
`{connected, expires_at, needs_refresh, has_refresh_token}`, but the endpoint
returns `{connected, accounts: [{key_label, label, expires_at, managed, …}]}`
(`app.py:6107-6140`, AUDIT #427). TypeScript does not catch it — the extra
property is simply absent from the declared type, so `statusQ.data.expires_at`
reads `undefined` at runtime and the code compiles clean.

Consequences, all user-visible on the Hermes provider page: the "expires soon"
badge never appears, "Token expires" is always blank, and — because the button's
`disabled` predicate is `!statusQ.data?.has_refresh_token`
(`HermesOAuth.tsx:379`) and that field no longer exists — **the Refresh button
is permanently disabled**. That button is the operator's documented escape hatch
for a dead grant (`hermes_auto_refresh.py:157-159`), so the one control that
recovers a wedged account is unreachable from the console.

`hermesRefresh`/`hermesDisconnect` (`client.ts:484-492`) also send only
`{provider}`, so single-account targeting (`key_label`) is impossible from the
UI, and `disconnected` changed from a `boolean` to a count while the declared
response type still says `boolean`.

**Fix sketch:** update `HermesStatusResponse` to the `accounts` array, render one
row per account (label, expiry, managed badge, per-row Refresh/Disconnect), and
pass `key_label` through `hermesRefresh`/`hermesDisconnect`. Verify per the
project's UI rule at 375px and desktop — the account list must reflow.

### 436. `request_device_code()` is still a blocking sync httpx call on the event loop

**Severity:** 🟠 High · **Status: fixed** — round 138 (`tests/test_fix_round138.py::test_request_device_code_runs_off_the_event_loop`; fixed 2026-10-14)

Wrapped in `asyncio.to_thread` like the two sibling call sites.

**Files:** `wiwi/server/app.py:6001`, `wiwi/providers/hermes_oauth.py` (`_TIMEOUT_S`,
`request_device_code`)

Round 136 threaded the two token-exchange call sites — `/poll` (`:6057`) and
`/refresh` (`:6186`) — but not the third. `request_device_code()` at `:6001` is
a plain synchronous `httpx` call executed directly in the async handler, with
the module's 30 s `_TIMEOUT_S`. It blocks the entire event loop for the
duration of the Nous round-trip: every in-flight completion, every open SSE
stream, and every pending 401 refresh hook stalls behind one admin's connect
click.

It is also the *first* request in the connect flow, so it is the one most likely
to be slow — it is the call that decides whether Nous will show a device code at
all.

**Fix sketch:** `await asyncio.to_thread(hermes_oauth.request_device_code)`,
matching the two sibling sites.

### 437. `/v1/realtime` reimplements the pre-flight and skips the `unpriced_model_policy == "reject"` gate

**Severity:** 🟠 High · **Status: fixed** — round 139 (`tests/test_fix_round139.py`; fixed 2026-10-14)

Fixed by **mirroring** the gate into `/v1/realtime` rather than
extracting the shared pre-flight helper the fix sketch asked for:
`authenticate()` renders its verdict as an HTTP error body, and the
WS path renders a `_ws_refuse` frame, so one function returning one
shape would have meant changing a surface outside this fix's file
region. **This is a weaker guarantee than one shared function** — the
two copies can still drift, and only the tests keep them honest.
`test_the_unpriced_gate_reads_identically_on_both_surfaces` runs the
same key/model/policy through `/v1/chat/completions` and `/v1/realtime`
and asserts identical 503s, and
`test_an_uncapped_key_is_not_refused_by_the_unpriced_gate` pins the
`max_budget is not None` narrowing, so a one-sided edit fails the
suite rather than silently diverging. A future agent who can edit
`authenticate()` should still prefer collapsing the two.

**Files:** `wiwi/server/app.py:2905-2921` (the WS admission block),
`wiwi/server/app.py:1524-1534` (the shared gate inside
`authenticate_and_authorize`), `wiwi/server/app.py:2042` / `:2351` / `:2554`
(the only `enforce_rate_limit` / `_record_tpm_usage` call sites)

The AUDIT #323 503 gate — refusing an unpriced model for a budget-capped key —
exists only inside the shared `authenticate_and_authorize` closure. `/v1/realtime`
never calls that closure: it invokes `state.auth.authenticate(token)` directly
(`:2906`) and re-implements three of the checks inline — disabled/expires
(`:2907-2912`), `over_budget` (`:2913`) and the per-key model allowlist
(`:2917-2921`) — omitting the unpriced-model gate entirely.

So the same key and the same model are refused on `/v1/chat/completions` and
admitted over the WebSocket. `enforce_rate_limit`'s only call site is `:2042`
and `_record_tpm_usage`'s are `:2351`/`:2554`, none of which the realtime path
reaches. With `unpriced_model_policy: reject` and a capped key, an unpriced
model is served over WS at a `CostEngine` price of $0, the cap never trips, and
the operator's `max_budget` silently stops applying to their most expensive
traffic class.

**Fix sketch:** extract the pre-flight into one helper both surfaces call, so
adding a check cannot silently skip one entry point (the repo forbids a second
convention beside an existing one).

### 438. The over-cap spend true-up is still under a bare `contextlib.suppress`

**Severity:** 🟠 High · **Status: fixed** — round 140 (`tests/test_fix_round140.py`; fixed 2026-10-14)

The over-cap true-up now uses the counted sibling's `try`/`except`
(logging `spend_charge_unrecorded` and incrementing
`spend_charge_failures`), and the reservation release is counted too.

**Files:** `wiwi/server/app.py:1628-1630`, `wiwi/server/app.py:1609-1626` (the
counted sibling), commit `8ee5676`

Commit `8ee5676` fixed the realtime copy of this exact pattern and left this
one, so the same method now contains two spend-recording sites with **opposite**
error handling. The sibling at `:1609-1626` wraps its `apply_spend_trueup` in a
`try/except` that logs `spend_charge_unrecorded` and increments
`spend_charge_failures`; the site at `:1628-1630` is the bare
`with contextlib.suppress(Exception):` — verbatim the second half of AUDIT #179.

This is the over-budget *crossing* path (the `if not recorded:` branch), which
is where `update_spend` returned `False` because the key went over its cap and
the charge must still be added. If that true-up raises, the exception is
swallowed: the charge is lost with no log line, and `spend_charge_failures`
stays at 0 so nothing surfaces the drift. `spend_to_date` then sits below
`max_budget` even though real money was spent, and `over_budget` may never trip
for the key that is actually over. The reservation release a few lines above
(`:1605-1607`) is uncounted in the same way.

**Fix sketch:** `try/except` matching `:1621-1626`, and count the reservation
release too.

### 439. A realtime session with no usage peak is charged $0 *and* leaves no request-log row

**Severity:** 🟠 High · **Status: fixed** — round 139 (`tests/test_fix_round139.py`; fixed 2026-10-14)

Every session that clears admission writes a request-log row and
settles tokens regardless of usage, and a no-usage session increments
the new `wiwi_realtime_usage_missing_total` counter
(`wiwi/server/metrics.py`) so the case is measurable rather than
invisible.

**Files:** `wiwi/server/app.py:515-516` (the early return),
`wiwi/server/app.py:525` (`dep.settle_tokens`), `wiwi/server/app.py:550`
(`state.logs.log_request`), `wiwi/server/app.py:2991-2992` (the `if usage is
not None` call site), `wiwi/server/app.py:501` (`_realtime_relay` returning
`None`)

`_bill_realtime_session` returns at `:515-516` — before `dep.settle_tokens`
(`:525`) and before `state.logs.log_request(...)` at `:550` — whenever the
session produced no usable usage. The call site compounds it: `if usage is not
None:` (`:2991-2992`) means a `_realtime_relay` that returns `None` (`:501`,
the handshake-failure path) never enters the billing function at all.

The realistic triggers are all ordinary failures: handshake failure or timeout
(`open_timeout=10.0`), an upstream 401 or 5xx (`:2982-2985`), or
`max_session_s` expiring before any usage-bearing frame arrives. In each case
the session is billed nothing, settles no tokens, and — most importantly —
**writes no row**, so the console shows no trace that a session happened at all
while upstream capacity was consumed. Every HTTP surface logs unconditionally
(`:2396`, `:2404`, `:2416`, `:2553`), which is what makes request-log
completeness a usable invariant; realtime is the one surface that breaks it.

**Fix sketch:** log every session that got past admission regardless of usage,
and add a `realtime_usage_missing` counter so the no-usage case is measurable
rather than invisible.

### 440. A binary frame from upstream terminates the realtime relay

**Severity:** 🟠 High · **Status: fixed** — round 139 (`tests/test_fix_round139.py`; fixed 2026-10-14)

The upstream→client loop branches on `isinstance(frame, (bytes,
bytearray, memoryview))` and forwards with `send_bytes`, so one
binary frame no longer kills the relay. The teardown log level was
also raised so a relay death is not silent.

**Files:** `wiwi/server/app.py:481` (`if '"usage"' in frame`),
`wiwi/server/app.py:486` (`await client_ws.send_text(frame)`),
`wiwi/server/app.py:487-490` (the swallow and the `finally` close),
`wiwi/server/app.py:2973` (`websockets.connect`), `wiwi/server/app.py:464`
(`iter_text`, the correct client→upstream direction)

`websockets.connect` at `:2973` is called without `decode=True`, so `recv()`
yields `str | bytes`. The upstream→client loop at `:481`/`:486` assumes `str`
unconditionally: `'"usage"' in frame` raises `TypeError` on a `bytes` operand,
and `send_text(frame)` raises on one too. That `TypeError` is caught by the bare
`except Exception` at `:487`, logged at `debug` — invisible at default verbosity
— and the `finally` at `:489-490` then closes the client socket. One binary
frame ends the whole session with no client-visible error.

The client→upstream direction is already correct: `:464` uses `iter_text()`,
which decodes. So the relay is asymmetric, and only the direction that can
actually receive binary from a misbehaving or differently-configured upstream is
the broken one.

**Fix sketch:** branch on `isinstance(frame, (bytes, bytearray))` →
`await client_ws.send_bytes(frame)`, else `send_text`; and raise the teardown log
level so a relay death is not silent.

### 441. Per-account Hermes state is never pruned — unbounded growth across reconnects

**Severity:** 🟡 Medium · **Status: fixed** — round 138 (`tests/test_fix_round138.py`; fixed 2026-10-14)

`HermesAutoRefresh.forget(provider, label)` pops the lock, the
generation counter and the breaker's streak/until/dead entries, and
`/disconnect` calls it. The generation counter is what makes it safe
against an in-flight rotation — a popped entry is treated as
"someone else owns this now", not as a restart at zero.

**Files:** `wiwi/providers/hermes_auto_refresh.py:66` (`_locks`),
`wiwi/providers/hermes_auto_refresh.py:70` (`_generations`),
`wiwi/core/recovery.py:67-69` (`CircuitBreaker._streaks` / `_until` / `_dead`)

Round 136 moved the sweeper's per-account bookkeeping onto
`(provider, key_label)` tuples, and all three structures only ever grow:
`_locks` and `_generations` get an entry on first touch of an account, and the
breaker's `_streaks`/`_until`/`_dead` dicts get one per identity it has ever
seen. Nothing pops an entry when `/disconnect` removes a key.

Compounded by #430 and #434: because every connect mints a new label (and today
stores a credential blob as it), the identity space grows without bound over a
deployment's lifetime — a long-lived gateway that has been reconnected
occidentally many times accumulates dead locks, dead generations and dead circuit
state that is never reclaimed.

**Fix sketch:** a `forget(provider, label)` that pops all three and clears the
breaker entry, called from `/disconnect`; the existing generation counter
(`hermes_auto_refresh.py:246`) already gives the race-safety needed to make it
safe against an in-flight refresh.

### 442. Round 137 left the whole-gateway backup export behind — `GET /admin/backup` silently drops `error_message`

**Severity:** 🟡 Medium · **Status: fixed** — round 141 (`tests/test_fix_round141.py`; fixed 2026-10-14)

`"error_message"` added to `_REQUEST_LOG_COLUMNS`, plus the drift
guard `test_request_log_columns_cover_every_written_column`, which
asserts every `db_sink._COLS` entry appears in
`_REQUEST_LOG_COLUMNS` ∪ `BODY_COLUMNS` so the two lists cannot
silently diverge again.

**Files:** `wiwi/server/backup.py:573-580` (`_REQUEST_LOG_COLUMNS`),
`wiwi/server/backup.py:508-521` (`_read_logs`), `wiwi/logging_core/db_sink.py:369-377`
(`_COLS`, the writer-side list that *did* gain the column)

`_REQUEST_LOG_COLUMNS` is an explicit allowlist, and round 137 added
`"error_message"` to the writer-side `_COLS` (`db_sink.py:369-377`) but not here.
`_read_logs` filters the query down to the allowlist (`:517`), so
`GET /admin/backup` omits the column with no error, no warning and no
truncation flag — an operator who exports a backup to preserve a failure's cause
(the entire point of #428) gets request logs with `error_code` and no message.

This is the **second** silent drift between the two lists; nothing asserts they
agree, so the same class of bug will recur on the next column addition.

**Fix sketch:** add `"error_message"` to `_REQUEST_LOG_COLUMNS`, and add a drift
guard test asserting every `db_sink._COLS` entry appears in
`_REQUEST_LOG_COLUMNS` ∪ `BODY_COLUMNS`.

---

## ✅ Fixed — Cline/WorkBuddy OAuth + backup/restore audit (2026-10-14)

Two read-only audits run back to back: the Cline/WorkBuddy OAuth surfaces
(`app.py`'s Cline connect/refresh/disconnect, `cline_oauth.py`,
`cline_auto_refresh.py`, `workbuddy_auth.py`, `workbuddy_auto_refresh.py`,
`workbuddy_import`) read against the WorkBuddy and Hermes equivalents they were
modelled on, and the backup/restore subsystem (`wiwi/server/backup.py`) read
against its own docstrings and the column lists in `logging_core/db_sink.py`.

Both audits compared each surface to a *sibling that already got it right*. The
pattern in almost every entry below is that Cline drifted from the WorkBuddy /
Hermes shape that round 136 and the DB-layer audit established — the shared
refresh lock, the write-ordering rule, the "forget the dead state on disconnect"
rule. The backup entries are the same class of defect one layer down: the
implementation and its documentation disagree, and the code silently does the
opposite of what the docstring claims.

**Citation basis:** line numbers are against the audited tree at commit
`32dc2a4` (pre-fix). The fixes for these entries landed in the working tree
after this sweep was written, so live `file:line` values have shifted; the
function names are the stable anchor. Entries #443-#459.

**Resolution (2026-10-14):** all seventeen entries are fixed and each carries an
inline `**Status: fixed**` line naming its round test file — #443/#444/#446/#447/
#448/#449/#450/#451 in `tests/test_fix_round143.py` (with #450/#451 extended by
`tests/test_fix_round144.py`), #445/#452/#453 in `tests/test_fix_round144.py`, and
#454-#459 in `tests/test_fix_round142.py`. Two are fixed only in part — #458's
config-section half is still open as #460, and #456's honest fix left per-deployment
limits YAML-owned, still open as #461. Full gate green at the time of writing:
3218 passed, 11 skipped, ruff clean.

### 443. The Cline admin refresh bypasses the shared lock and the circuit — one grant, two rotations

**Severity:** 🔴 Critical · **Status: fixed** — round 143 (`tests/test_fix_round143.py`; fixed 2026-10-14)

The hand-rolled refresh is deleted; `cline_oauth_refresh` calls
`ClineAutoRefresh.refresh_now(provider)` →
`_refresh_locked(name, require_due=False, force=True)`, so the admin
route shares the worker's lock, circuit and `mark_dead` decision.
The worker's boolean result maps onto the route's existing
200/`authentication_error` responses, and a transient failure is a
502 rather than a 500.

**Files:** `wiwi/server/app.py:5840-5888` (`cline_oauth_refresh`), specifically
the direct `cline_oauth.refresh_token(record["refresh_token"])` at `:5859`;
`wiwi/providers/cline_auto_refresh.py:132` (`_refresh_locked`, the guarded
entry point it skips); `wiwi/providers/workbuddy_auto_refresh.py:261`
(`refresh_key_now`) and `:153` (`_rotate_locked`); `wiwi/server/app.py:6442`
(WorkBuddy admin refresh → `refresh_key_now`); `wiwi/server/app.py:6179`
(Hermes admin refresh → `state.hermes_refresh._refresh_locked`)

`cline_oauth_refresh` calls `cline_oauth.refresh_token(...)` itself and then
does its own record write at `:5872-5877`. It never acquires
`ClineAutoRefresh._locks[name]`, never checks `self._circuit`, and never bumps
`self._generations` — so it is the **only** one of the three OAuth admin
refresh routes that does not route through the worker. WorkBuddy goes through
`refresh_key_now` → `_rotate_locked(force=True)`; Hermes goes through
`_refresh_locked(require_due=False)`. Cline is the sole outlier.

Because the lock is not held, the 60 s sweeper tick and an operator's Refresh
click can both decide the same provider is due (the sweeper's 5-minute lead
window makes "both land in the same window" the *expected* case, not a race
that needs bad luck) and both POST the same single-use `refreshToken`. Upstream
honours the first and answers `invalid_grant` to the second. Which one loses is
a coin flip; the loser marks the provider dead, and it stays dead until a manual
restart.

The circuit asymmetry makes it worse. On the sweeper/hook side an
`invalid_grant` reaches `_do_refresh`'s
`if result.get("error") == "unrecoverable_refresh_error"` branch and calls
`mark_dead(name)`. On the admin side the same response is turned into a clean
`401 authentication_error` for the operator and **nothing else** — no
`mark_dead`, no audit signal that a grant was burned, no increment of the dead
counter. So the admin path fails silently while the sweeper keeps presenting the
grant it no longer owns, burning one `invalid_grant` per tick until the
provider is finally marked dead by the sweeper. One button click destroys a
working login.

**Fix sketch:** delete the hand-rolled refresh from `cline_oauth_refresh` and
call `state.cline_refresh.refresh_now(provider)` →
`_refresh_locked(name, require_due=False, force=True)` (add the `force`
parameter `_refresh_locked` does not currently have, so the admin route can skip
the due-check without skipping the lock); let the worker own the record write
and the `mark_dead` decision, and map its boolean return onto the
200/`authentication_error` responses the route already returns.

### 444. A Cline refresh response with no `expiresAt` is a permanent refresh storm that kills the grant

**Severity:** 🔴 Critical · **Status: fixed** — round 143 (`tests/test_fix_round143.py`; fixed 2026-10-14)

The conditional expiry write is gone; `_do_refresh` resolves through
`cline_oauth.resolve_expires_at` →
`wiwi/providers/token_expiry.compute_expires_at`, so a payload with
no usable `expiresIn` falls **forward** to `2 * REFRESH_LEAD_S`
rather than preserving the already-due deadline. Both the sweeper and
the admin path go through the worker, so there is one write site.

**Files:** `wiwi/providers/cline_auto_refresh.py:192-193`
(`_do_refresh`'s conditional expiry write), `wiwi/providers/cline_auto_refresh.py:116`
(`_sweep`'s due-check), `wiwi/providers/cline_oauth.py` (`parse_expires_at` /
`resolve_expires_at`), `wiwi/server/app.py:5873-5874` (the same conditional
guard on the admin path)

```python
await self._update_secret(name, result["access_token"])
if result.get("expires_at"):
    record["expires_at"] = result["expires_at"]
```

The expiry is only overwritten when the payload carried something parseable.
When it did not, `record["expires_at"]` keeps its previous value — and we are
*inside* `_do_refresh`, so the previous value is by construction already inside
(or past) the 5-minute lead window that made the provider due. The record is
stored, and the very next `_sweep` tick (60 s) recomputes the same verdict.

Failure sequence: upstream answers `{"accessToken":…,"refreshToken":…}` with no
`expiresAt` → refresh **succeeds** → the rotated pair is written with the stale
expiry → 60 s later the provider is due again → another rotation plus another
`set_setting` write → forever, one rotation per minute per provider, until a
rotation eventually gets `invalid_grant` (upstream retires a grant sooner under
this rate) and `mark_dead` fires. A one-field omission in an upstream response
converts a working provider into a grant-burning loop that ends in a dead
circuit and a re-login.

This is the same defect as AUDIT #425 for Hermes, in the same position in a
sibling file. The Hermes fix established the correct reading — an unparseable
expiry must be resolved to a *fresh* one, not preserved.

**Fix sketch:** resolve the expiry through the shared
`wiwi/providers/token_expiry.compute_expires_at` helper unconditionally (falling
back to `now + REFRESH_LEAD_S` when the payload carries nothing usable), and
delete the `if result.get(...)` guard on both the sweeper and admin paths.

### 445. A WorkBuddy refresh response with no `expiresIn` is the same storm — and the docstring documents the opposite behaviour

**Severity:** 🔴 Critical · **Status: fixed** — round 144 (`tests/test_fix_round144.py`; fixed 2026-10-14)

`workbuddy_auth.refresh_token` resolves the field through
`wiwi/providers/token_expiry.compute_expires_at`, and the docstring
that documented the opposite behaviour was rewritten. Note the
shared helper now backs **all three** OAuth providers (Cline,
WorkBuddy, Hermes), so #425/#444/#445 are one rule in one module.

One pre-existing test was **updated, not deleted**:
`tests/test_workbuddy_adapter.py::test_refresh_token_preserves_expiry_when_missing`
was renamed to
`test_refresh_token_falls_forward_when_expires_in_is_missing` and
inverted — it now asserts the new expiry differs from the old one,
is in the future, and is outside the lead window.

**Files:** `wiwi/providers/workbuddy_auth.py:310-319` (`rotated = WorkBuddyAuth(...,
expires_at=auth.expires_at, …)` then the conditional `expires_in` overwrite),
`wiwi/providers/workbuddy_auth.py:263-270` (the `refresh_token` docstring),
`wiwi/providers/workbuddy_auto_refresh.py:119` (`_check_key`'s due-check)

The rotated auth is built with `expires_at=auth.expires_at` — the caller's
**old** value — and only `:317-319` replaces it, and only when `expiresIn` is a
positive non-bool number. Absent, zero, boolean, or garbage all leave the old
expiry in place.

The docstring above it states the intent as the opposite of the behaviour:

> missing response fields keep their old values — the Go preserveExpiry rule
> that **prevents refresh storms when expiresIn is absent**

It does not prevent refresh storms; it *causes* them. Preserving a stale expiry
means the token is due again on the next tick, forever — the AUDIT #444 loop,
one file over. The comment is load-bearing enough that a future agent reading
it will treat the current code as deliberate and refuse to change it.

**Fix sketch:** resolve via `wiwi/providers/token_expiry.compute_expires_at`
(unconditionally, with the lead-window default when `expiresIn` is unusable) and
rewrite the docstring to say what the code does — a missing `expiresIn` is
treated as "expire at the next refresh", never "keep the old deadline".

### 446. Cline `_do_refresh` persisted the pool key BEFORE the OAuth record, with neither write guarded — a DB error permanently loses the grant

**Severity:** 🟠 High · **Status: fixed** — round 143 (no dedicated regression test; fixed 2026-10-14)

`_do_refresh` writes the OAuth record **first** and the pool key(s)
second, each in its own `try` that logs and returns `False` — neither
write can escape as an exception. Because the guarded writes are the
fix, `core/gateway.py`'s 401-hook call sites were left un-wrapped on
purpose: a hook failure can no longer produce a 500, and the
nearest regression coverage is
`tests/test_fix_round143.py::test_admin_refresh_reports_a_transient_failure_as_502`,
which exercises the guarded path end to end. **There is no test that
fails a `set_setting` mid-rotation and asserts the grant survived** —
that is the gap a future agent should close.

**Files:** `wiwi/providers/cline_auto_refresh.py:190-193` (`_update_secret` then
the record write), `wiwi/core/gateway.py:588` / `:715` / `:1814` (the three
`await refresh_hook(...)` call sites, none wrapped in a `try`),
`wiwi/providers/workbuddy_auto_refresh.py:212` (`_write_secret`, the WorkBuddy
counter-example that gets it right)

`_do_refresh` calls `await self._update_secret(name, …)` at `:190` and only
then writes the settings record. If `set_setting` then raises, the record still
holds the **consumed** refresh token and the old `expires_at`. The next tick
presents that dead grant, gets `invalid_grant`, and calls `mark_dead` — one
transient DB error costs the operator their login.

This is the exact inverse of the ordering rule the codebase states twice
elsewhere (write the irreplaceable single-use grant first; the pool-key write is
derivable from it) and the shape Hermes `_do_refresh` follows. The comment
directly above `_do_refresh` claims the ordering is deliberate, which makes the
reversal a live hazard rather than an oversight.

The second half is the exception path. Both writes are unguarded, so the
`IntegrityError`/operational error escapes `_refresh_locked` uncaught. The
on-demand 401 hook is awaited from `core/gateway.py` at all three sites with no
`try`/`except` around it, so an escaping write converts an upstream **401** into
a gateway **500** — the caller sees a wiwi bug where the provider simply said
"token expired", and the retry/cooldown accounting records the wrong failure.

**Fix sketch:** reorder to record-first, then pool keys; wrap each write in its
own `try`/`except` that logs and returns `False` (never re-raises) so the 401
hook path cannot produce a 500; and add the same guard to the 401-hook call
sites in `gateway.py` so a future hook failure degrades to "no rotation" rather
than an exception.

### 447. The Cline admin refresh/connect paths wrote only `keys[0]` — reintroducing the stale-sibling bug the sweeper's own docstring documents fixing

**Severity:** 🟠 High · **Status: fixed** — round 143 (`tests/test_fix_round143.py`; fixed 2026-10-14)

All three Cline admin paths now route through
`ClineAutoRefresh.store_access_token`, which writes every key the
record owns. The stale-sibling 401 → second-rotation chain is gone.

**Files:** `wiwi/server/app.py:5590-5599` (`_update_provider_secret`,
`key0 = acct.keys[0]`), `wiwi/server/app.py:5663`, `:5760`, `:5868` (its three
callers), `wiwi/providers/cline_auto_refresh.py:200-208` (`_update_secret` and
the docstring below it)

`_update_provider_secret` does `key0 = acct.keys[0]` and rewrites exactly that
one key, in memory and in the DB. The sweeper's `_update_secret` writes **every**
key, and its docstring says exactly why:

> Updating only ``keys[0]`` (the historical behavior) caused on-demand
> 401-refresh retries to pick a still-stale sibling key from the round-robin
> cursor.

That is the fix. `cline_oauth_refresh` and `cline_oauth_connect` still route
through the un-fixed path, so the sweeper and the admin button disagree about
what "refresh this provider" means — and the admin button is the one an operator
reaches for *because* the sweeper is failing.

Trigger: a 2-key Cline pool. The operator clicks Refresh. `keys[0]` is now
fresh; `keys[1]` still holds the pre-rotation access token. Smooth WRR sends the
next request to `keys[1]`, which 401s, which fires the on-demand hook, which
rotates **a second time** — burning the grant the admin click just consumed. If
that rotation lands after a real rotation it gets `invalid_grant` → `mark_dead`.
One admin click: two burned grants, one dead provider. The admin path also never
takes the lock (see #443), so it can compound with a concurrent sweeper tick.

**Fix sketch:** give `_update_provider_secret` the same all-keys semantics as
`ClineAutoRefresh._update_secret` — or, better, delete it and have all three
Cline routes go through the sweeper worker, which now owns both the write and
the ordering (#443, #446).

### 448. `ClineAutoRefresh._update_secret` clobbered EVERY pool key with the OAuth access token — destroying non-OAuth keys

**Severity:** 🟠 High · **Status: fixed** — round 143 (`tests/test_fix_round143.py`; fixed 2026-10-14)

Ownership is fingerprinted: connect stamps
`access_fingerprint` on the OAuth record, and `store_access_token`
overwrites only the keys whose secret matches it, falling back to all
keys when no fingerprint exists yet (first rotation after a connect).
A raw API key the operator added to the pool survives.

**Files:** `wiwi/providers/cline_auto_refresh.py:200-215` (`for k in acct.keys:
k.secret = secret`)

The counter-fix to #447 over-corrects. `_update_secret` loops over
`acct.keys` and assigns the WorkOS bearer to every entry, persisting each one.
The docstring's premise — "a Cline provider's key pool entries all authenticate
with the same OAuth account" — is a convention, not an invariant, and nothing
enforces it: an operator can add a plain API key to a Cline pool through the
normal key-add path.

Trigger: a Cline provider whose pool is `["a", "b"]`, where `a` is
OAuth-managed and `b` is a separately pasted raw API key. One successful refresh
writes the OAuth access token into `b` and persists it. The raw key is gone —
not shadowed, overwritten — and the operator has to re-paste it. Every
subsequent rotation overwrites it again, so the account is permanently
unusable for raw-key traffic and nothing anywhere records that its secret was
replaced.

This is the same class as the Hermes #427 fix (one account's rotation must never
touch a sibling key's credential) arriving through the opposite door: there, one
account wrote to many keys; here, one key's ownership is assumed for all keys.

**Fix sketch:** track which pool keys the OAuth record actually issued — stamp
a fingerprint of the access token on the record when it is written, and have
`_update_secret` overwrite only keys whose secret matches that fingerprint
(falling back to all keys only when no fingerprint exists yet, i.e. the first
rotation after a connect). This is the shape Hermes already uses in
`encode_credential`/`_update_secret`.

### 449. Cline connect overwrote a working refresh grant with `None`

**Severity:** 🟠 High · **Status: fixed** — round 143 (`tests/test_fix_round143.py`; fixed 2026-10-14)

Both connect paths **merge**: a `None` `refresh_token` from a
truncated paste-code exchange keeps the existing working grant
instead of writing null over it.

**Files:** `wiwi/server/app.py:5666-5670` (`cline_oauth_connect`'s record
dict), `wiwi/server/app.py:5763-5767` (`/cline/oauth/callback`'s identical
record dict), `wiwi/providers/cline_oauth.py:232-234` (`exchange_code`'s
return, `refresh_token` is `None` for a truncated code)

Both connect paths build the record unconditionally:

```python
record = {
    "refresh_token": tokens["refresh_token"],   # may be None
    "expires_at": tokens.get("expires_at"),
    "email": tokens.get("email"),
}
```

`cline_oauth.exchange_code` documents at `:214` that `refresh_token` **will be
`None`** when the pasted Google-OAuth code is a truncated token blob — that is a
normal, expected outcome of the paste-code flow, not an error, and the function
returns successfully. The connect handler then persists `refresh_token: null` over
whatever was there.

Trigger: a provider that is already connected and auto-refreshing well. The
operator re-runs connect (to fix an email label, or because the paste-code dialog
is the documented flow) and pastes a code that decodes without a refresh token.
The working grant is replaced with null. `_sweep` then skips the provider
forever — `cline_auto_refresh_refresh` requires a stored `refresh_token` — so it
serves until the access token expires and then hard-401s, with no sweeper and no
recovery except another manual paste. The connect handler returned 200.

**Fix sketch:** treat a `None` `refresh_token` in connect as "this connect
cannot establish auto-refresh": either reject with a 400 explaining that the code
was truncated, or merge — keep the existing `refresh_token` when the new exchange
did not produce one. Merging is the better shape, since `exchange_code`'s `None`
is a legitimate result rather than a failure.

### 450. `mark_dead` is permanent and keyed on `(provider, label)` — a re-created account inherits the corpse

**Severity:** 🟠 High · **Status: fixed** — rounds 143 + 144 (`tests/test_fix_round143.py`, `tests/test_fix_round144.py`; fixed 2026-10-14)

Installing a new credential now clears the identity's breaker entry,
so a dead circuit means "*this* grant is unusable", not "this
account name is forever dead". Wired into Cline connect,
`workbuddy_import`, provider delete, key delete and disconnect.

**Files:** `wiwi/core/recovery.py:81-95` (`clear` / `blocked` / `mark_dead`),
`wiwi/providers/workbuddy_auto_refresh.py:198` (`mark_dead(ident)`),
`wiwi/providers/cline_auto_refresh.py:185` (`mark_dead(name)`)

`CircuitBreaker._dead` is a plain `set` that only `clear()` removes, and
`clear()` runs only at the *end* of a successful rotation
(`workbuddy_auto_refresh.py`, and `cline_auto_refresh._do_refresh`'s tail).
`blocked()` short-circuits on membership at `recovery.py:86-88`, i.e. **before**
any rotation is attempted — so once dead, the only thing that can clear the
state is a rotation, and a rotation requires not being dead. The circuit is
self-locking by construction.

The WorkBuddy identity is `(provider, label)` where `label` is derived from the
account (`nickname or uid`, see the label derivation in `workbuddy_import`). So
the operator's remedy for a dead account — delete the key, import a fresh valid
auth for the same account — **reuses the same identity**, because reusing the
same nickname or uid is the normal case. The new, perfectly valid credential is
born inside a dead circuit. The sweeper reports "blocked by the refresh circuit
breaker" and the 401 hook returns `False`, so the request 401s and the operator
is told to use the force-refresh button — the one control that works.

Cline has the same shape keyed on `(provider_name)`, so it is one reconnect away
from the same lockout.

This makes #451 (state never purged) actively harmful rather than merely leaky:
the corpse outlives the credential that created it.

**Fix sketch:** clear the breaker entry whenever a new credential is installed
for an identity — on `workbuddy_import` of a label, on Cline connect, and on
Hermes `/poll`. A dead circuit should mean "this grant is unusable", and a
*different* grant is not that. Keep `mark_dead` sticky only against the
credential that earned it.

### 451. Per-provider OAuth state is never purged on disconnect or provider delete (Cline and WorkBuddy)

**Severity:** 🟠 High · **Status: fixed** — rounds 143 + 144 (`tests/test_fix_round143.py`, `tests/test_fix_round144.py`; fixed 2026-10-14)

`forget(provider)` / `forget(provider, label)` on both workers pops
the lock, the generation counter and the breaker entries, called from
every teardown path (key delete, provider delete, Cline disconnect,
WorkBuddy disconnect). The module-global `_workers` fallback cache,
keyed on `id(state)`, was also moved off raw `id` so CPython id
recycling cannot hand a fresh `AppState` a worker bound to a dead
one. Hermes got the same `forget` in round 138 (#441).

**Files:** `wiwi/providers/cline_auto_refresh.py:53` (`_locks`), `:57`
(`_generations`), `:185` (`mark_dead`), `wiwi/providers/workbuddy_auto_refresh.py:59-70`
(`_locks` / `_generations` / the breaker's per-identity dicts),
`wiwi/server/app.py:5886` (`DELETE /admin/cline/oauth/disconnect`),
`wiwi/server/app.py:3699-3730` (`admin_delete_provider`), `wiwi/server/app.py:3582`
(`admin_delete_provider_key`)

Three structures per provider — the refresh `asyncio.Lock`, the generation
counter used for the in-flight-rotation race check, and the circuit breaker's
streak/until/dead entries — are created on first touch and never removed. None
of the three teardown paths (key delete, provider delete, Cline disconnect,
WorkBuddy disconnect) pops anything.

Two consequences. The obvious one is unbounded growth: every
connect-then-disconnect cycle leaks a lock, a counter and a breaker entry for a
deployment's whole lifetime. The non-obvious one is #450 — a `dead` circuit
survives the removal of the credential that caused it, so a provider name that
is later reused comes back dead.

**Fix sketch:** a `forget(provider)` / `forget(provider, label)` on each worker
that pops the lock, the generation counter and the breaker entries, called from
every teardown path. The generation counter is what makes this safe against an
in-flight refresh: the in-flight holder captured its generation before entering
and re-checks under the lock, so a popped entry must be treated as "someone
else owns this now" rather than "restart the counter at zero". Hermes already
has the `forget` shape in `hermes_auto_refresh.py` — port it.

### 452. `workbuddy_import` mutated the router BEFORE persisting, with no exception guard — half-imported account and a bare 500

**Severity:** 🟠 High · **Status: fixed** — round 144 (`tests/test_fix_round144.py`; fixed 2026-10-14)

`workbuddy_import` persists first and mutates the in-memory pool only
after every `add_key` lands, rolling the DB back on failure — so a
partial batch leaves neither half applied.

**Files:** `wiwi/server/app.py:6376-6380` (`existing.keys.append(...)` then
`await state.config_store.add_key(...)`), `wiwi/server/app.py:6307-6317` (the
docstring's promise)

The handler's own docstring promises the opposite of what it does:

> Import succeeds only if every entry parses; partial failures are rejected so
> the admin can fix the file rather than end up with a half-imported set.

Parsing *is* all-or-nothing (the `parsed` loop rejects before any mutation), but
persistence is not. The append to `existing.keys` happens first and the
`add_key` call second, inside a plain loop with **no `try`** — so the in-memory
router is mutated optimistically and a DB failure leaves the two permanently
divergent.

Trigger: import 5 accounts where the 3rd `add_key` raises. All 5 keys are live
in memory (so WRR immediately routes traffic through accounts that do not exist
in the DB), only 2 are in the DB, the request 500s with an unhandled traceback,
and **no audit row is written** — `log_audit` is after the loop, so the failed
import leaves no trace at all. On the next restart the provider silently reverts
to 2 keys and the operator has to work out why four accounts vanished. The
docstring's guarantee is checkable only for the parse phase, which is the one
phase that was never at risk.

This is exactly the shape AUDIT #181 fixed in `DELETE /admin/providers`; the
same guard was not applied here.

**Fix sketch:** persist first, mutate second — collect the `add_key` calls, run
them inside a `try` that rolls back the successful ones on failure (or relies on
the fact that nothing has been mutated yet and simply returns a 500 with a clear
message), and only then append to `existing.keys`. Log the audit row in a
`finally`-style path so a failed import is still recorded.

### 453. `workbuddy_import` did not dedupe labels WITHIN one import batch

**Severity:** 🟠 High · **Status: fixed** — round 144 (`tests/test_fix_round144.py`; fixed 2026-10-14)

`taken` is updated as each label is accepted inside the loop, so a
second entry with the same `nickname` in one batch trips the same
409 the error message already advertised.

**Files:** `wiwi/server/app.py:6353-6362` (the `taken` snapshot and the
collision check), `wiwi/server/app.py:6368-6380` (the append/`add_key` loop),
`wiwi/server/config_store.py` (`add_key`, `INSERT OR REPLACE` / `ON CONFLICT
… DO UPDATE`)

```python
taken = {k.label for k in existing.keys} if existing else set()
for label, _ in parsed:
    if label in taken:
        return _err(409, …)
```

`taken` is snapshotted from the existing pool and **never updated inside the
loop**. The handler's own error message ("nothing imported") reads as though
every entry in the batch is checked against every other entry; it is not — only
against the pool as it was before the request. Meanwhile the persistence layer
upserts on label, so two entries with the same `nickname` in one file collapse to
one DB row.

Trigger: one pasted file with two auth objects sharing a `nickname` (easy — a
re-export of the same account, or two accounts from the same device). Both are
appended in memory with identical labels, the DB keeps one, and
`router.get_key(label)` resolves to only the first. Rotation therefore reaches
only one of them; the other holds a credential nothing ever refreshes, and it
401s permanently once its token lapses. The import reported success and listed
the duplicate label twice.

**Fix sketch:** add the label to `taken` as it is accepted inside the loop, so
the second occurrence trips the existing 409 with the same "nothing imported"
guarantee the message already advertises.

### 454. `_read_all` aborts keyset pagination after page 1 — every log and rollup backup exports only the OLDEST 2000 rows

**Severity:** 🔴 Critical · **Status: fixed** — round 142 (`tests/test_fix_round142.py`; fixed 2026-10-14)

`_read_all` derives its cursor from the table rather than the
caller's projection: it selects `id`, advances `id > cursor`, and
strips `id` from the emitted dicts only when it added it. Exports now
return every row beyond the first page for both `logs` and
`rollups`.

**Files:** `wiwi/server/backup.py:592-637` (`_read_all`), specifically
`:630-632`; `wiwi/server/backup.py:573-582` (`_REQUEST_LOG_COLUMNS`),
`:582-591` (`_ROLLUP_COLUMNS`); `wiwi/server/backup.py:508-521` (`_read_logs`),
`:533` (`_read_rollups`)

```python
cursor = rows[-1][usable.index("id")] if "id" in usable else None
if cursor is None:
    return out, truncated
```

`usable` is derived from the **caller's** column allowlist, not from the table.
The SELECT is `SELECT {col_sql} FROM {table} … ORDER BY id LIMIT :limit`, so
`ORDER BY id` works without `id` being selected — but the cursor read needs it.
Neither `_REQUEST_LOG_COLUMNS` (25 names) nor `_ROLLUP_COLUMNS` (24 names)
contains `"id"`. So on the first full page `cursor is None` and the function
returns: `_PAGE` rows, no truncation flag, no warning.

The docstring states the intent that the code does not implement:

> Paging is ``id > cursor`` rather than OFFSET, which re-walks every row already
> read.

Paging never happens. `_read_logs` (`:518`) and `_read_rollups` (`:533`) are the
only two callers, so **every** `logs` and `rollups` section is capped at one page.

Verified empirically against a real SQLite DB at this commit: a 5 000-row
`request_logs` table returned exactly 2 000 rows with `truncated: 0`, first
`request_id='a'`, last `'r1999'`. Adding `"id"` to the projection returned all
5 000.

The real-world consequence is the worst kind of silent failure. `GET
/admin/backup?sections=logs,rollups` on a gateway holding 400 000 `request_logs`
rows ships the **oldest** 2 000 and reports completeness — no error, no
`truncated` key, no count mismatch (see #455). An operator restoring a production
gateway from that archive gets a spend log missing 99.5% of its history and no
indication that anything was dropped. Because the rows are the *oldest*, the
missing data is the settled, long-closed history rather than the recent tail,
which makes the archive look plausible on inspection.

**Fix sketch:** derive the cursor from the table, not the projection — add `"id"`
to the SELECT (and strip it from the emitted dicts), or run a separate
`SELECT id` for the page. Add a regression test that inserts > `_PAGE` rows and
asserts the exported count.

### 455. `truncated` is initialised and never incremented — the documented truncation signal can never fire

**Severity:** 🔴 Critical · **Status: fixed** — round 142 (`tests/test_fix_round142.py`; fixed 2026-10-14)

`truncated` is now emitted as the `{section: count}` mapping
`build_backup` and `web/src/api/types.ts` both already declared, and
the key is omitted entirely when nothing was dropped.

**Files:** `wiwi/server/backup.py:611` (`truncated = 0`), `:631-636` (every
`return out, truncated`), `wiwi/server/backup.py:281-287` (`build_backup`'s
aggregation), `wiwi/server/backup.py:256` (the docstring promising the flag),
`web/src/api/types.ts:592` (`truncated?: Record<string, number>`)

`truncated` is set to `0` on entry and there is no `truncated +=` anywhere in the
file. Every return path hands back the initial `0`. So the only half of the
truncation story that *does* work — the `limit`-clamping branch at `:624-627`,
which shrinks `page` and returns when `len(out) >= limit` — still reports
`truncated: 0`.

The types disagree three ways, which is itself the diagnostic: `build_backup`'s
`truncated: dict[str, int]` (`:281`) expects `_read_all` to return a
**mapping**, `_read_all` returns an **int**, and `BackupDocument.truncated` in
the console (`types.ts:592`) declares `Record<string, number>` — agreeing with
neither. Nothing type-checks across the JS boundary, so the mismatch is silent in
both directions.

Trigger: `GET /admin/backup?log_limit=50000` against a 400 000-row table. The
clamp drops 350 000 rows and the document asserts completeness — which is what
`build_backup`'s docstring promises ("a cap that bites is recorded in
`truncated` so a restore knows the section is partial"). Combined with #454, the
`truncated` flag is the *only* thing that could have surfaced the pagination
bug, and it is inert.

**Fix sketch:** increment by the unexported remainder at the limit branch,
return a `{section: count}` mapping (or rename the field and fix
`build_backup` + `types.ts` together), and add a test that a `log_limit` smaller
than the table produces a non-zero `truncated`.

### 456. `_apply_deployments` restores five per-deployment limits the `deployments` table cannot store — they vanish on the next restart

**Severity:** 🟠 High · **Status: fixed (export side) — round 142 (`tests/test_fix_round142.py`; fixed 2026-10-14)

The chosen fix is the lossy-but-honest one: `_export_deployments` no
longer emits `rpm`/`tpm`/`timeout`/`max_tokens`/`prompt_cache`, and
import leaves a target deployment's limits alone rather than writing
values the table cannot persist. A backup therefore never claims a
limit it cannot restore. **The underlying gap is still open** — the
`deployments` table still has no columns for those five fields, so
per-deployment limits remain YAML-owned and do not survive a
backup/restore round trip. See #461.

**Files:** `wiwi/server/backup.py:1395-1401` (the `Deployment(...)` constructor
with `rpm`/`tpm`/`timeout`/`max_tokens`/`prompt_cache`), `:1403-1409` (the
in-place field assignment on an existing deployment), `:1411-1413` (the
`add_deployment` call that persists only four values), `wiwi/server/config_store.py`
(`add_deployment`, and the `deployments` DDL)

The apply path writes six per-deployment settings into the live `Deployment`
objects, but the single persistence call carries only
`(group_name, provider_name, model_id, weight)`. The `deployments` table's DDL
has no `rpm`, `tpm`, `timeout`, `max_tokens` or `prompt_cache` column, and the
restart path rebuilds every deployment from that table — with defaults.

So the restored instance serves correct limits until the first restart, then
silently reverts to unthrottled defaults for exactly the five fields an operator
was most likely tuning in the source deployment. Trigger: the source instance
sets `gpt-5/prod` to `rpm=20, max_tokens=4000` because it sits behind a rate
limit; after a restore + one restart the new instance serves that group
unthrottled at the default `max_tokens`, and there is no error, no audit row,
and no re-import step that would catch it. The backup's *own export* carries the
values — `_export_deployments` reads them from the live objects — so the format
is complete and only the round-trip is lossy.

**Fix sketch:** either add the five columns to the `deployments` table (the
project has no Alembic, so it is an inline `CREATE TABLE IF NOT EXISTS` plus the
`_migrate` ALTER list), or record the values in the existing settings overlay
that `_load_db_config` already merges. The former is the honest fix.

### 457. `_insert_rows` derives the column set from `rows[0]` alone — a field present on later rows is silently written as the column default

**Severity:** 🟠 High · **Status: fixed** — round 142 (`tests/test_fix_round142.py`; fixed 2026-10-14)

The column set is the union of keys across the batch, so a field only
a later row carries is inserted for that row instead of taking the
column default. Applied to the paged log insert, `_apply_rollups`
and `_apply_responses` alike.

**Files:** `wiwi/server/backup.py:1615` (`cols = [c for c in rows[0] if c in
present]`), `wiwi/server/backup.py:1643` (the paged insert), and the same
`rows[0]`-derived pattern at `:1676` (`_apply_rollups`) and `:1723`
(`_apply_responses`)

The column set for the whole batch is read off the first row. `_coerce_row`
emits a fixed key set per section, so homogeneous documents are fine — but any
heterogeneous one silently loses the field, because the INSERT's column list
omits it and every row takes the column default.

Verified: a two-row document where row 1 carried `error_code="api_error"` and
row 0 did not inserted **both** rows with `error_code = ''`. The failure has no
warning and no non-zero return — the row count says success.

Heterogeneous documents are not hypothetical here: #442 is exactly this class of
drift, and `_coerce_row` keys off the *section's* projection, so any section
whose rows were captured across a schema change (an old archive restored into a
newer build, or a partially-migrated source) hits it.

**Fix sketch:** take the union of keys across the batch —
`cols = [c for c in dict.fromkeys(k for r in rows for k in r) if c in present]` —
so a field missing from row 0 is still inserted as `None` for the rows that
lack it, rather than being dropped for every row that has it.

### 458. The restore path has no rollback — a bulk-section failure leaves earlier pages AND earlier sections committed

**Severity:** 🟠 High · **Status: fixed (bulk sections) — round 142 (`tests/test_fix_round142.py`; fixed 2026-10-14)

`logs`/`rollups`/`responses` are applied **first**, in **one**
transaction, so a malformed row rolls the whole bulk pass back with
nothing else written; each rollup/response row gets its own
SAVEPOINT so one bad row is reported as `failed` rather than taking
the section with it. **The route docstring's blanket "any error
rejects it with nothing applied" still overstates this for the
CONFIG sections**, which persist through `ConfigStore`'s own
transactions — see #460.

**Files:** `wiwi/server/backup.py:1649-1650` (`_exec_insert`, one
`engine.begin()` per call), `wiwi/server/backup.py:1643` (called once per
500-row page), `wiwi/server/backup.py:1259-1284` (`apply_backup`, committing
each section in turn), `wiwi/server/app.py:4288` (the route docstring)

Every unit of work is its own transaction: `_exec_insert` opens a fresh
`engine.begin()`, `_insert_rows` calls it once per 500-row page, and
`apply_backup` commits each section separately. There is no outer transaction, so
a failure at any point leaves everything before it durable.

Verified: a log row with no `ts` raises
`IntegrityError: NOT NULL constraint failed: request_logs.ts` *after* the
preceding pages are already committed.

Trigger: a 50 000-row import in which row 1 200 is malformed. The restore commits
providers, keys, deployments, aliases, settings, prices, users, vkeys **and** the
first 1 000 log rows, then 500s — with no record of how far it got, no partial-
success flag, and no way for the operator to tell which sections are now
half-populated. A retry re-applies the idempotent sections and hits the same row.
Worse, the sections that *did* land (a fresh vkey table, new providers pointing
at credentials the operator has not yet verified) are live immediately.

The route's own docstring over-promises, and the wording is what an operator
relies on:

> any error rejects it with nothing applied

That holds for the **validation** phase only. It does not hold for apply, which
is where the errors that actually happen live.

**Fix sketch:** wrap `apply_backup` in a single outer transaction (or take a
pre-import snapshot and restore it on failure), and make the response carry a
per-section applied/skipped report on partial failure so a retry is safe. At
minimum, correct the docstring to say what is actually guaranteed.

### 459. `_apply_rollups` swallowed every per-row failure; `_apply_responses` had no guard at all

**Severity:** 🟡 Medium · **Status: fixed** — round 142 (`tests/test_fix_round142.py`; fixed 2026-10-14)

Both functions now share one shape: collect failures, count them, and
return them, so the response can say "12 of 4 000 rollup rows
rejected" instead of a bare `0`. The first few log at `error`, the
rest at `warning`, so the signal is visible at default verbosity.

**Files:** `wiwi/server/backup.py:1698-1703` (`_apply_rollups`'s bare
`except Exception: log.warning(...)`), `wiwi/server/backup.py:1711-1735`
(`_apply_responses`, unguarded), `wiwi/server/backup.py:1279-1284`
(`apply_backup`'s section ordering)

`_apply_rollups` wraps each `_exec_insert` in `except Exception`, logs a
warning, and continues — so `written` counts only the successes but nothing
counts the failures, and the function's return value is indistinguishable from
"there was nothing to restore". `_apply_responses` has no `try` at all: one bad
row aborts the section and propagates, after `logs` and `rollups` have already
been committed (which is #458's failure mode, triggered by the smallest possible
input).

Trigger: restoring into a target whose `request_rollups` has a stricter type than
the source (a `TEXT` column that must be numeric on the newer build, or a
non-null column the source never wrote). Every row insert fails. The API answers
`200 {"rollups": 0}` and the operator concludes there was nothing to restore —
when in fact the archive held a full rollup history that was silently discarded.
The `_apply_responses` variant is the mirror image: a single malformed stored
response turns a whole restore into a 500 *after* the useful sections landed.

The asymmetry is itself the defect: two adjacent functions, same shape, opposite
failure policy, neither reporting which rows failed.

**Fix sketch:** give both the same shape — collect failures, count them, and
return them (`{"written": n, "failed": m}`) so the response and the audit row can
say "12 of 4 000 rollup rows rejected" instead of a bare `0`. Log the first few
at `error` and the rest at `warning` so the signal is visible at default
verbosity.

---

## ✅ Fixed — log-table responsiveness at 375px, and the transparent overlays (2026-10-18)

Found while doing the binding UI/UX verification for the request-logs
redesign (`CLAUDE.md` "UI/UX Universal Compatibility Rule" §6: open the page
in a 375px viewport *and* a desktop viewport before claiming done). Both
findings below were reproduced with a headless Chromium harness driving the
real console at 375×812, 768×1024 and 1440×900.

### 466. The proxy-log message cell is capped at a flat 520px, so the table overflows its card at 375px and the message is clipped with no scroll affordance

**Severity:** 🟡 Medium · **Status: fixed**

**Files:** `web/src/pages/ProxyLogs.tsx:252` (the message `<td>`'s
`max-w-[520px]`), `:192` (the `LogsTable` head, which listed four columns with
no breakpoint classes)

The request-log table was given per-column breakpoints so a phone shows four
columns instead of fifteen, but `ProxyLogs.tsx` was left on the old fixed
markup. A 520px cap is wider than a 375px viewport on its own, so the row
overflowed the card: the message ran off the right edge, cut mid-word with no
ellipsis and no indication that the region scrolled. Confirmed by screenshot
and by measurement — `document.scrollWidth` stayed at 375 (the clipping is
inside the `overflow-auto` wrapper), so a naive overflow check does not catch
it.

**Fix:** the cap now steps down with the viewport
(`max-w-[180px] sm:max-w-[320px] lg:max-w-[520px]`), and the request-id column
drops out below `sm` via the `colClass` prop `LogsTable` already supports —
the expanded row already shows the id in full, so nothing is lost. The page
also picks up `admin-table-logs`, the tighter-gutter class the request-log
table uses.

**Verified:** at 375px the message truncates inside the card with an ellipsis;
at 1440px all four columns and the full message are unchanged.

### #429 revisited — `Dialog` was affected too, not just `Drawer`

**Severity:** 🟠 Medium · **Status: fixed** (see the #429 entry above)

The #429 write-up said `Dialog` "happens to survive" because its scrim
hardcodes `bg-black/70`. Its *panel* does not: the dialog panel carries the
same `bg-[var(--admin-surface-elevated)]`, so the "New virtual key" dialog on
`/console/keys` was equally transparent at every width. One `overlayHost()`
helper now serves both overlays, so there is a single portal convention rather
than a second one beside it.

**Verified:** `getComputedStyle(backgroundColor)` on the drawer panel and the
dialog panel both compute `rgb(14, 14, 14)`; Escape still closes the drawer
(`document.querySelectorAll('[role="dialog"]').length === 0`); the dialog
still paints above the page after the portal target changed.

---

## ✅ Fixed — round 170: four IR carriers the translation was inferring or dropping (2026-10-22)

Found by reading `wiwi/ir/types.py` against its consumers. None of these was
found by a failing request, which is why they survived: each one produces a
plausible-looking result rather than an error.

### 513. `Usage` did not say whether `prompt_tokens` includes cache reads, so pricing inferred it from the provider

**Severity:** 🔴 High (money — a wrong answer bills *and* reports a plausible cost) · **Status: fixed** — round 170 (`tests/test_fix_round170.py`)

**Files:** `wiwi/ir/types.py` (`Usage`), `wiwi/streaming/deltas.py` (`UsageFinal`), `wiwi/core/gateway.py` (`_price`, `_price_stream`, `merge_resume_context`, `accumulated_stream_usage`, `usage_fallback`)

**Trigger.** Any adapter whose usage convention disagrees with the deployment's
wire shape. OpenAI totals the prompt (`cached` is a subset); Anthropic's
`input_tokens` excludes cache reads. `prompt_includes_cached(dep)` decided
which by wire, and an `opencode` deployment serving `claude-*` speaks Messages
while an OpenAI-wire test classified it — fresh input was billed at $0 for a
whole Claude Code session (#227; #9 was the same defect one provider narrower).

**The fix.** `Usage.cached_in_prompt: bool | None` records the convention where
the body is decoded; `UsageFinal` carries it for the streaming path. Both
pricing entry points prefer it and fall back to the wire-shape inference only
when it is `None`. Resume merges and the streaming fallback inherit the value
instead of recomputing it. Anthropic records `False`, OpenAI `True`.

### 514. `ToolUsePart` had no `cache_control`, so a breakpoint on a tool call was dropped on replay

**Severity:** 🟠 Medium (cost — a stable prefix re-read at 1x every turn) · **Status: fixed** — round 170 (`tests/test_fix_round170.py`)

**Files:** `wiwi/ir/types.py` (`ToolUsePart`), `wiwi/wire/anthropic_messages.py:162,200`, `wiwi/providers/anthropic_adapter.py`

**Trigger.** A Claude Code turn whose prompt-cache breakpoint sits on a
`tool_use` block (per Anthropic's tool-use caching docs, a marker on any member
of a batch action is accepted). `TextPart`, `ImagePart`, `DocumentPart`,
`ToolResultPart` and `Tool` all carried the field; `ToolUsePart` did not, so the
marker vanished on decode and the replayed history had no breakpoint there.

**The fix.** Field added and decoded on both arms (client-dispatched `tool_use`
and the provider-hosted `server_tool_use`/`mcp_tool_use`), re-emitted by the
Anthropic encoder beside `input`.

### 515. The Chat codec did not decode `top_k`, so `drop_params` discarded it with no signal to the caller

**Severity:** 🟡 Medium (a caller-set constraint silently ignored) · **Status: fixed** — round 170 (`tests/test_fix_round170.py`)

**Files:** `wiwi/wire/openai_chat.py` (`decode_request`, `_KNOWN_KEYS`)

**Trigger.** `POST /v1/chat/completions` with `top_k`. The Anthropic and
Responses codecs put it in `GenParams.top_k`, which makes a non-OpenAI backend
emit the caller-visible "top_k has no Chat Completions equivalent and was
dropped" warning. The Chat codec left it in `Request.extras`, and the OpenAI
adapter's `_STANDARD` set does not list `top_k` — so `drop_params` removed it
before the warning branch, which keys on `g.top_k is not None`, could fire.
Same parameter: warned on two surfaces, silent on the third.

**The fix.** Decode with `ir.coerce_int` (a non-numeric value is rejected, not
forwarded) and add `top_k` to the codec's known keys so it is modelled rather
than passed as an opaque extra.

### 516. `block_type` was an untyped `str` for a value whose exact spelling is a replay requirement

**Severity:** ⚪ Low (latent — no reproduced failure; needs a typo or a junk upstream value) · **Status: fixed** — round 170 (`tests/test_fix_round170.py`)

**Files:** `wiwi/ir/types.py` (`ToolUsePart`, `ThinkingPart`), `wiwi/streaming/deltas.py` (`ToolCallOpen`)

**Trigger.** Any value outside the documented vocabulary. An `mcp_tool_use`
whose paired result is replayed under a different spelling is an unpaired block
and Anthropic rejects the history; `ThinkingPart.block_type` gates the
`redacted_thinking` replay branch the same way. Both were `str` while `Role` and
`StopReason` next to them were `Literal`s.

**The fix.** `ToolUseBlockType` and `ThinkingBlockType` are `Literal`s, and
`__post_init__` normalizes an unknown value to the always-legal default instead
of forwarding it. `ToolResultPart.block_type` stays `str` on purpose: the
decoder accepts any `*_tool_result` spelling so a new server-tool version
replays without an IR change.

### 517. The SPA entry document was served with no `Cache-Control`, so browsers heuristically cached it and pinned stale builds

**Severity:** 🟡 Medium (every redeploy: returning visitors keep the old admin SPA / Playground until a heuristic cache expires) · **Status: fixed** — round 171 (`tests/test_fix_round171.py`)

**Files:** `wiwi/server/app.py` (`SPAStaticFiles`)

**Trigger.** Follow a link to the site or reopen a tab after a redeploy.
`index.html` is the only unhashed file in the Vite build, and
`SPAStaticFiles` served it (and the SPA fallback for client-side routes)
with no freshness information at all. RFC 9111 §4.2.2 lets caches store
such a response heuristically (typically ~10% of its `Last-Modified` age),
so a returning browser reused the old `index.html` — which references the
*previous* build's hashed `/assets/*` filenames. Observed in the wild as
"the Playground mobile-drawer fix isn't there on my phone" when the
served bundle already contained it. A plain reload revalidates anyway;
the broken case is exactly the phone path.

**The fix.** The HTML entry document — `/`, `/index.html`, the SPA
fallback, and any other `.html` — is served `Cache-Control: no-cache`
(revalidate every time; the file's ETag makes that a cheap 304). Hashed
`/assets/*` keep their default caching. Note `StaticFiles` hands the
mount root down as path `"."`, not `""`.

### 518. The IR/codec seam audit: nine fields carried by one path and dropped by its twin (2026-10-25)

**Severity:** 🟠 High (streaming clients — which is every real agent — silently lost data the non-streaming path delivered) · **Status: fixed** — round 172 (`tests/test_fix_round172.py`)

**Files:** `wiwi/ir/types.py`, `wiwi/ir/translation.py`, `wiwi/wire/{openai_chat,openai_completions,anthropic_messages,openai_responses}.py`, `wiwi/providers/{openai,anthropic,gemini,openrouter,nim,opencode}_adapter.py`, `wiwi/server/app.py`

**Trigger.** A read-only audit of every IR field against every producer/consumer
found a recurring defect class: a field decoded and emitted on the non-streaming
path, dropped on the streaming path (or vice versa), or written by adapters and
read by nobody. Items, each pinned by a test:

1. `Finish.stop_sequence` reached only the sync encoders; the Chat and Responses
   stream `Finish` arms read `d.stop_reason` alone (AUDIT #497's missing halves).
   Also: the Chat feed-arm downgrade nulled nothing, so a downgraded tool turn
   carried a stale sequence through `final_frame`.
2. `LogprobsDelta` had no `ResponsesStreamEncoder.feed` arm — streamed callers
   paid for logprobs and never received them (the #326 failure, stream half).
3. Thinking deferred behind an open tool item lost its `encrypted_content`:
   `_defer` no-opped a blob-only delta and `_flush_deferred` built the item with
   no blob — the next Codex turn 400'd (AUDIT #355 re-entering via interleave).
4. `ImagePart.file_id` re-encoded as the corrupt literal
   `"data:image/png;base64,None"` in the OpenAI adapter (`url or data:…,b64`);
   the Chat decoder could not read the `file` part its own adapter emits.
5. `GenParams.thinking_type` was invisible to `effective_reasoning_effort`/
   `effective_thinking_budget` — `thinking: {type:"enabled"}` ran with thinking
   OFF on every non-Anthropic backend, and mode-only requests off on Anthropic.
6. `Usage.cached_in_prompt` was written by every adapter and read only by
   pricing: all four wire encoders re-emitted prompt totals in the upstream's
   convention, so a Messages client behind an OpenAI upstream double-counted
   its cached context and an OpenAI client behind Anthropic under-counted.
   OpenRouter/NIM/opencode/Gemini never recorded the flag at all.
7. `reasoning.summary` was excluded from Responses `extras` by `_KNOWN_KEYS`
   but only `.effort` was read — a real param, neither mapped nor forwarded
   (the `text.verbosity` class, AUDIT #369).
8. `GenParams.metadata` was populated by no decoder; the response-cache gate
   `app.py` keyed on `.get("stream")` could never fire. Dead field removed,
   dead gate condition removed.
9. Sampling-param coercion (`temperature`/`top_p`/`seed`/`parallel_tool_calls`/
   `disable_parallel_tool_use`) existed for `max_tokens`/`top_k` but not the
   rest — typed-wrong values rode verbatim to upstream 400s (AUDIT #83 class).

Also: Anthropic stream errors emitted wiwi's `api_connection_error` (no such
Anthropic type; sync said `api_error`); tool-result `extra_blocks` were both
stringified into `content` AND re-emitted natively (duplicated references);
tool-result image `cache_control` dropped on replay; Gemini silently dropped
`input_examples`, tool-result images, `redacted_thinking` data, and file ids;
the OpenAI adapter forwarded per-message `reasoning_content` to NIM (set drift
vs `is_native_openai`); `parallel_tool_calls=true` was silently dropped on
Anthropic; `tool cache_control`/`defer_loading` silently on the Chat wire.

**The fix.** Every stream encoder now stores `d.stop_sequence` and emits it
(the Chat one nulls it on both downgrade sites); Responses collects
`LogprobsDelta` onto the terminal response object; `_defer` carries the blob
and a blob-only delta buffers an entry; `file_id` renders as the Chat `file`
part everywhere it can and warns where it cannot; `thinking_type`
participates in both effort accessors and the Anthropic enable gate; every
encoder re-bases prompt totals into its own dialect's convention
(Messages: exclusive of cache reads; the three OpenAI surfaces: inclusive);
the flag is recorded by OpenRouter/NIM/opencode (True) and Gemini (True —
promptTokenCount is the whole-prompt counter); `reasoning.summary` rides
extras as `reasoning_summary`; `coerce_float` joins `coerce_int` and all four
decoders coerce; `stream_error_type` output maps to Anthropic vocabulary; the
flatten keeps `content` empty when the blocks ride natively; and every
capability-driven drop names itself to the caller.

## ✅ Fixed — round 176: tape accounting let logprobs deltas grow the tape unbounded (2026-10-29)

### 521. `_delta_size` charged `LogprobsDelta` zero bytes, so the tape's 256 KiB bound never engaged for a `logprobs` stream

**Severity:** 🟠 Medium (unbounded per-stream memory on a client-controlled request field) · **Status: fixed** — round 176 (`tests/test_fix_round176.py`)

**File:** `wiwi/streaming/resume.py` (`_delta_size`)

**Trigger.** A caller sends `logprobs: true` (or `top_logprobs: N`) to an
OpenAI-wire provider. `OpenAIAdapter.decode_stream_event` emits one
`LogprobsDelta` per token (`openai_adapter.py:719`), and `StreamTape.append`
records it. `_delta_size` had no arm for the type, so it fell through to the
final `return 0` and `StreamTape.bytes` never rose. `_evict` only pops while
`self._bytes > self._max_bytes`, so nothing was ever evicted.

**Reproduced** (pre-fix):

```
20000 x LogprobsDelta (a top-5 frame, ~270 JSON bytes)
  -> entries: 20000   tape.bytes: 0   retained: ~5.0 MB
```

The 256 KiB bound advertised in the module docstring (`resume.py:10`) and in
`StreamTape`'s own docstring held for every delta type except this one — and
`ServerToolResultDelta` (AUDIT #133 class), whose zero charge is deliberate
because it never re-enters the continuation.

**Why the existing guards did not catch it.** The zero-charge decision was made
for provider-hosted tool deltas, where charging real size let a `web_search`-heavy
stream evict the client-dispatched deltas a continuation needs — correct for
those, because `build_continuation_messages` filters them out. `LogprobsDelta`
is *not* filtered from the continuation; it simply never feeds it (`replay_text`
and `build_continuation_messages` read text and tool calls only). Copying the
"excluded from the continuation ⇒ charge 0" rationale onto it confused *not
consumed* with *not stored*, and storing is what the budget bounds.

**The fix.** `_delta_size` charges `len(orjson.dumps(delta.logprobs))` for a
populated `LogprobsDelta` (0 when `logprobs` is `None`, the empty case).
Billing/continuation behaviour is unchanged — only the accounting.

**Verified:** the same 20000-frame probe now reports `charged: 224` for one
frame and bounds the tape at 1170 entries / 262080 bytes. Regression test fails
against the pre-fix body (`tape.bytes == 0`) and passes after.

### 522. `validate_tool_args` raised `AttributeError` on a non-`str` payload, breaking its own "never raise" contract

**Severity:** 🟡 Low (latent — no in-repo caller can reach it today) · **Status: fixed** — round 176 (`tests/test_fix_round176.py`)

**File:** `wiwi/streaming/validation.py` (`validate_tool_args`, `_fingerprint`)

**Trigger.** Any caller passing a non-`str` `raw_args`. The oversize check reads
`len(raw_args.encode("utf-8", "replace"))` and both log paths call
`_fingerprint(raw_args)`, which encodes again. `raw_args` is *typed* `str`, and
the docstring opens with "Violations are logged but never raise" — the module
exists so a malformed tool call cannot take down a stream, yet a malformed
*argument type* did exactly that.

**Reproduced** (pre-fix):

```
validate_tool_args("t", None,     schema) -> AttributeError: 'NoneType' object has no attribute 'encode'
validate_tool_args("t", 5,        schema) -> AttributeError: 'int' object has no attribute 'encode'
validate_tool_args("t", [1],      schema) -> AttributeError: 'list' object has no attribute 'encode'
validate_tool_args("t", {"a": 1}, schema) -> AttributeError: 'dict' object has no attribute 'encode'
```

**Why the existing guards did not catch it.** Every guard added here targets a
malformed *schema* (`AUDIT_REPORT H4`, `AUDIT #192`) or a malformed *payload
content* — `schema` is `isinstance`-checked first, and the JSON body is parsed
inside a `try`. The payload's *type* was assumed by the signature and never
checked, so the one input shape the signature excludes is the one shape that
crashes.

**Reachability.** Not reachable from the pump today: `_arg_bufs` is built only
from `dl.ToolCallArgsDelta.args_fragment`, which is contractually `str`, and
every adapter guards its emit site with `isinstance(..., str)` or
`coerce_args_fragment` (`providers/base.py:361`). It is fixed regardless
because the function is a documented public seam whose contract is the reason it
exists — the next caller that skips the coercion must not be the one that
discovers this.

**The fix.** Coerce once at the top: a `dict` becomes its JSON text (so it still
fingerprints and logs meaningfully), anything else becomes `""`. Every later
read then sees the documented type.

**Verified:** the same probe reports `errors: 0`. Regression is parametrized
over `None`/`5`/`1.5`/`[1]`/`{"a": 1}`/`True`/`b"x"` and fails pre-fix with
`AttributeError`.

---

## ✅ Fixed — round 175: a configured-but-broken Redis response cache failed silently (2026-10-29)

Found by running the backend against a live `redis-server` and then against an
unreachable one; the second run produced no output of any kind.

### 520. `RedisResponseCache` swallowed every Redis error with no log and no counter, so an outage was indistinguishable from a cold cache

**Severity:** 🟠 Medium (silent cost + correctness expectation — the operator is billed at the uncached rate while believing a shared cache is serving) · **Status: fixed** — round 175 (`tests/test_fix_round175.py`)

**Files:** `wiwi/cache/redis_cache.py`

**Trigger.** `general_settings.redis_url` (or `REDIS_URL`) set, Redis later
down, credentials wrong, or the server restarted under a different ACL. Every
`get`/`set`/`delete` raised inside the backend, the bare `except Exception`
turned that into `None`/a no-op, and nothing else happened: no `structlog` line,
no counter, no `/health` field, no metric. Every request then missed, so the
gateway silently paid upstream for traffic the operator had configured a shared,
restart-durable cache to absorb. A cold cache and a dead cache looked identical.

**Why the existing guards did not catch it.** `build_response_cache` refuses to
hide exactly this trap for the *missing-package* case (`except ImportError` →
warn + fall back to memory). That guard is import-time only; the runtime path it
is meant to protect — Redis constructed but failing — had no equivalent. Round
34's contract ("a cache must never fail a request") is correct and unchanged,
but it was read as "must also be silent", which is the same class of defect as
`record_spend`'s bare `except` returning `True` (AUDIT #179).

**The fix.** Swallowing stays: a request still never fails and still degrades to
a miss. The failures are now observable:

- `RedisResponseCache.errors` counts every failed `get`/`set`/`delete`;
- one `redis_cache_error` **warning** (with `op`, `url`, exception type and
  message) fires on the healthy → broken transition and *not* per request, so a
  hot path cannot flood the log during an outage;
- the first success afterwards clears the latch, so a later outage warns again
  without a recovery line per request;
- a corrupt/foreign value stays an ordinary miss (`_decode` returns `None`) and
  is deliberately *not* counted as an outage — that is a producer bug, not a
  connection failure.

**Verified:** against live `redis-server` on `127.0.0.1:6379` the healthy path
round-trips bytes/headers/`stored_at` and emits nothing; against
`127.0.0.1:1` it emits exactly one warning per outage. The regression test fails
against the pre-fix body (`errors == 0`, no log) and passes after.

---

### 523. An index-reusing `ToolCallOpen` orphaned the superseded block/item in the Anthropic and Responses encoders

**Severity:** 🟠 High (client-visible truncation on any provider that reuses a tool index) · **Status: fixed** — round 176 (`tests/test_fix_round176.py`)

**Files:** `wiwi/wire/anthropic_messages.py` (`AnthropicStreamEncoder.feed`, `ToolCallOpen` arm), `wiwi/wire/openai_responses.py` (`ResponsesStreamEncoder.feed`, `ToolCallOpen` arm)

**Trigger.** A second `ToolCallOpen` on an index that is still open. Both
encoders keyed their per-block bookkeeping by *IR index* and assigned
unconditionally (`self._tool_blocks[d.index] = self._block_idx`,
`self._tools[n] = {...}`), so the entry identifying the first block was gone
before anything could close it. Round 176 taught the *adapters* to emit a
`ToolCallClose` before re-opening (`AUDIT #243`); the encoders had no equivalent,
so the IR was legal and the wire was not.

**Reproduced** — same 7-delta IR prefix through both encoders:

```
anthropic: content_block_start ×2, content_block_stop ×1   -> block 0 never closed
responses: output_item.added  ×2, output_item.done   ×1   -> fc_r_0 never completed
```

The Anthropic stream then ended with `message_delta`/`end_turn` and an open
`tool_use` block — a client sees the turn complete but the call truncated. The
Responses stream advanced `output_index` while an earlier item was still
`in_progress`.

**Why it matters more on the encode side.** `deltas.py` is the binding contract
and adapters are required to guarantee legality; the encoders are documented as
never defending. That is the right rule for *malformed IR* — but an index reuse
is legal IR (the adapters produce it deliberately, and all four dispose of it
upstream), so the encoder must render it as the two distinct calls it is. The
OpenAI Chat encoder needs no change: it emits a delta stream with no
open/close invariant, and a second Open at the same index merges into one
`tool_calls[0]` with the id replaced.

**The fix.** Close the open entry for that index before overwriting it —
`self._close_block(tool_index=d.index)` / `self._close_tool(n)`. Both helpers
were already parallel-safe and already clear `_open_block`/`_open_tool` when
they match, so the arms below re-establish that state as before.

**Verified:** balanced `start`/`stop` and `added`/`done` at 2/2 for both.
Regressions fail pre-fix (`1 == 2` on the stop and done counts).

---

### 524. A non-positive cache TTL crashed gateway startup on the Redis backend only, and a cache hit was served under the producing request's id

**Severity:** 🟠 Medium (a config the schema declares legal refuses to boot; and on the Responses surface a hit hands the caller an id naming another request's stored object) · **Status: fixed** — round 177 (`tests/test_fix_round177.py`)

**Files:** `wiwi/cache/redis_cache.py`, `wiwi/cache/__init__.py`,
`wiwi/cache/hit_rebase.py` (new), `wiwi/server/app.py` (hit branch)

**Trigger.** Either of:

1. `cache_settings.ttl_s: 0` (or negative) with `general_settings.redis_url`
   set. `RedisResponseCache.__init__` raised `ValueError("ttl_s must be
   positive")`, and that constructor runs inside `create_app` via
   `build_response_cache` — so the process died at startup. The *same* config
   without `redis_url` booted fine, because `MemoryResponseCache` accepts it and
   simply expires everything at once (`time.time() - stored_at > ttl_s` is
   always true). Whether a config was legal depended on which backend happened
   to be selected. `CacheSettings` validates nothing, and `wiwi/config.py` uses
   no numeric constraints anywhere (deliberate), so nothing rejected the value
   earlier.
2. Second and distinct: a cache hit replayed the stored bytes verbatim,
   envelope included. The caller received the **producing** request's `id`
   (`chatcmpl-…`, `msg_…`, `cmpl-…`, `resp_…`) and its frozen
   `created`/`created_at`, while its own `x-wiwi-request-id` — and its own log
   row — carried a different id. Chat/Messages ids are opaque handles, so there
   the mismatch was cosmetic. On the Responses surface `resp_<request_id>` is
   dereferenced by `GET /v1/responses/{id}` and by `previous_response_id`, so
   the hit handed the caller a handle naming *someone else's* stored object.
   Both ids resolve, which is what hid it: resolution alone cannot distinguish
   "my object" from "a different request's object".

**The fix.** Two separate changes, matching the two triggers:

- *TTL.* The constructor no longer raises. `ttl_s <= 0` now means what it
  already meant on the memory backend — **disabled: never serve, never store** —
  via a `disabled` flag that short-circuits `get`/`set` before the client is
  touched (a disabled cache must not even contact Redis, and must not serve a
  value another instance left under the key). `build_response_cache` emits one
  `response_cache_disabled_by_ttl` **warning** before backend selection, so it
  fires for Redis and memory alike: a cache the operator enabled but that can
  never hit is the same silent-cost trap as entry #520, one config field over.
- *Identity.* `wiwi/cache/hit_rebase.py:rebase_cached_body` rewrites only the
  envelope identity onto the serving request: `id` is regenerated from the
  current request id reusing the separator the cached id already carried (`-`
  or `_`), so it needs no per-dialect prefix table and cannot drift when a
  surface picks a new one; `created` and `created_at` are set to now. An id with
  no separator, or any non-`str`, is left untouched — mangling a foreign value
  would be worse than the stale one. Because the body now carries the hit's own
  id, the hit is also **persisted** under `resp_<ctx.request_id>` exactly as the
  upstream path persists a fresh response, otherwise the returned handle would
  dangle; a store failure there is logged (`response_store_put_on_cache_hit_failed`)
  and the hit still serves, the same never-fail-the-request rule as the cache.

Nothing else in the payload changes: the body is otherwise byte-identical, and
`LogEvent` accounting was already correct (own `request_id`, `response_cache_hit`
not `cache_hit`, `tok_in`/`tok_out` zero, reservation refunded).

**Verified:** `ttl_s` of `0` and `-5` with `redis_url` boot and yield a disabled
`RedisResponseCache`; `60` still selects an enabled one. All four surfaces
(`/v1/chat/completions`, `/v1/messages`, `/v1/completions`, `/v1/responses`)
return `id == f"<prefix><own x-wiwi-request-id>"` on a hit, and on Responses
`GET /v1/responses/<hit id>` returns 200 naming that same id and
`previous_response_id` continues (200). Regressions fail against the pre-fix
body (8 of 15, on exactly the intended assertions); `test_fix_round34.py`'s
`test_redis_cache_rejects_nonpositive_ttl`, which pinned the removed
`ValueError`, is replaced by one pinning the disabled semantics, and the two
`r1.json() == r2.json()` assertions that pinned body-verbatim replay now compare
everything except the envelope identity. The round-34 e2e test's `__init__` stub
was rewritten to delegate to the real constructor, because its hand-copied
attribute list silently omitted the new `disabled` field.

### 525. Resume/usage merge lost data when prompt capture was off

🟠 **Medium** — `wiwi/core/gateway.py:_price_stream`, `merge_resume_context`,
`accumulated_stream_usage`

`_stream_usage` — the frame the encoder reports the client's usage block from and
the field `merge_resume_context` sums a resumed attempt into — was written only by
the server's `_capture_delta`, which runs solely when prompt capture is on
(`store_prompts_in_spend_logs` or a Responses store). Under the default config the
field stayed `None`, so after a mid-stream failover the merge had nothing to add:
the client saw the last attempt's tokens while spend billed every attempt (the same
divergence `AUDIT_REPORT` M5 records for `stream_resume_max_retries > 1`, which
recurred on the default config once that retry count was raised). Two further
fields were dropped by the same merge:

- `cache_hit` was never OR-ed from the resumed context, so a failover whose second
  attempt read the provider's prompt cache never reached
  `wiwi_prompt_cache_hits_total`.
- `accumulated_stream_usage` builds the `UsageFinal` that *replaces* the
  per-attempt frame for every encoder, but never set `cost` — while its documented
  twin `merge_resume_context` sums it. The docstring claimed "the same arithmetic";
  it was not.

**Status: fixed** — `_price_stream` publishes the priced frame on the context at the
one choke point every streaming cost routes through (pump, `_price_partial`,
force-stream), so the readers no longer depend on an unrelated capture setting;
`merge_resume_context` ORs `cache_hit`; `accumulated_stream_usage` sums `cost`.

Pinned by `tests/test_fix_round178.py` (3 tests; all 3 fail against the pre-fix
body).

### 526. Resumed attempts started a second trace, and failed spans never exported as errors

🟠 **Medium** — `wiwi/core/gateway.py:_attempt_resume`, `_end_attempt_span`;
`wiwi/core/telemetry.py:Tracer.close`; `wiwi/server/app.py:_err`

Three telemetry gaps, all of which made a failure invisible in exactly the place an
operator looks:

1. `resume_ctx` propagated `cancel`, `forward_headers` and `est_tokens` but not
   `span`. `_attempt_span` parents each `wiwi.upstream` span on `ctx.span`
   *explicitly*, because the pump's task context cannot be relied on to inherit —
   so a resume context without the span parented the failover attempt on nothing
   and emitted it as a second root trace. One client request appeared as two traces,
   and an operator chasing the failure saw only the half that succeeded. `session_id`
   was dropped the same way, which matters most at a failover.
2. `set_status` existed only on `_NoopSpan`: no real span was ever marked ERROR.
   `_end_attempt_span` recorded `wiwi.attempt_status` as a plain attribute, so every
   failed upstream attempt exported as UNSET and APM error rates read zero.
3. `x-wiwi-trace-id` was emitted only on success, though spec C requires it "on
   success and error responses" — a caller reporting a failed request, the case
   where a trace is most wanted, had nothing to quote.

**Status: fixed** — `resume_ctx` carries `span` and `session_id`; `Tracer.close`
takes `status` and marks `StatusCode.ERROR` (description `http_<status>`) for any
4xx/5xx, called from `_end_attempt_span` (non-`ok` attempt) and `_err`;
`_err` adds `x-wiwi-trace-id` from the span it already closes.

**Verified:** live ASGI app with telemetry on — a 404 on `/v1/chat/completions`
returns `x-wiwi-trace-id: d1633f97…` (32 hex); with telemetry off the header is
absent and the 404 is unchanged. Span status pinned against a real
`InMemorySpanExporter` (`ERROR`/`http_500` for a failed attempt, `UNSET` for a
clean one) in `tests/test_fix_round178.py`.

### 527. One bad probe aborted the whole health-healer sweep

🟡 **Low** — `wiwi/core/recovery.py:HealthHealer._sweep`

`_probe` builds the adapter, body, URL and headers *outside* its `try` and catches
only `httpx.TransportError`, and `_sweep` gathered every probe with no
`return_exceptions`. An adapter that raised while encoding therefore propagated out
of the `gather` and aborted the batch — every other sick target in that tick went
unprobed, with only the outer `healer_sweep_error` to show for it. Separately, the
deployment's restore credit was claimed *before* the probe ran, so a probe that
raised still marked its deployment credited and no sibling key could advance the
streak: the deployment could never reach `probes_to_restore`. The healer is off by
default, which is why this never surfaced in production.

**Status: fixed** — each target is isolated (`CancelledError` re-raised, everything
else logged as `healer_probe_error` with deployment/provider/key), the credit is
claimed inside the semaphore and returned with `discard` when the probe raised.

Pinned by `tests/test_fix_round178.py` (2 tests, both fail against the pre-fix
body).

### 528. The force-stream 401 refresh retried without recording the attempt

🟡 **Low** — `wiwi/core/gateway.py:_complete_via_stream`

A 401 from an OAuth-backed provider triggers a token refresh and one reconnect.
`_call_once` and the pump both record `ok_after_refresh` for that second attempt;
`_complete_via_stream` — the arm Cline and WorkBuddy take for every non-streaming
caller — fell through to the pump loop on the retry's 200 without recording
anything, though `_end_attempt_span`'s docstring states "the 401-refresh path
appends `ok_after_refresh` or `http_401`". The request log therefore credited an
OAuth refresh with nothing, and the attempt ended on the failure's record.

**Status: fixed** — the successful reconnect records `ok_after_refresh` in both
`ctx.attempts` and `_log_attempt`, matching the other two arms.

---

## ✅ Fixed — round 179: three latent IR defects found by reading the module against its consumers (2026-10-29)

None of these raised on a live wire path, which is why they survived: each
produced a plausible-looking result rather than an error. Fixed together with
`tests/test_fix_round179.py` (12 tests).

### 529. `effective_thinking_budget` ignored both explicit off-switches

🟡 **Medium** — `wiwi/ir/types.py:GenParams.effective_thinking_budget`

The accessor's docstring has always promised "Returns None when
`reasoning_effort` is 'none' (thinking disabled)", but the code read the direct
budget first and returned it. Two states therefore disagreed with their own
sibling: `GenParams(thinking_type="disabled", thinking_budget=32000)` reported
`effective_reasoning_effort() == "none"` while `effective_thinking_budget()`
returned `32000`, and `reasoning_effort="none"` beside a budget did the same.
Both consumers of the accessor — the Anthropic and Gemini adapters — would turn
thinking ON for a caller who had explicitly turned it off.

Not reachable from a wire today (the Anthropic codec only reads `budget_tokens`
when `type == "enabled"`, and the other three surfaces never set
`thinking_budget`), which is the only reason it is Medium. `GenParams` is a
plain dataclass with no runtime enforcement, and this is the same class of
latent defect as #388/#389.

**Status: fixed** — both off-switches are checked before the budget, so the
accessor agrees with `effective_reasoning_effort`.

### 530. `effective_reasoning_effort`'s documented precedence was not the code's

⚪ **Low** — `wiwi/ir/types.py:GenParams.effective_reasoning_effort`

The docstring described "reasoning_effort, then effort, then budget, then the
`thinking_type` mode as the last fallback". The code checks
`thinking_type == "disabled"` *second*, ahead of `effort` and the budget, so
`GenParams(effort="high", thinking_type="disabled")` returned `"none"` where the
doc promised `"high"`.

The code's order is the correct one — an explicit off switch must not be
re-enabled by a separate "how hard should it think" knob, and the two are
independent wire fields a caller can send together — so the docstring was
corrected to match rather than the behaviour changed.

**Status: fixed** (documentation; behaviour pinned by a test).

### 531. An unknown provider-hosted builtin was dropped even on its own surface

🟠 **High (fidelity)** — `wiwi/providers/anthropic_adapter.py:_encode_tools`,
`wiwi/ir/builtin_tools.py`, `wiwi/wire/{anthropic_messages,openai_responses}.py`

The registry only knows `web_search` and `tool_search_*`, so any other server
tool decodes to a builtin `ir.Tool` carrying its raw wire type under
`builtin_config["_wire_type"]`. Nothing ever read that key: the Anthropic
encoder consulted `wire_type_for()` alone, got `None`, and dropped the tool.

An Anthropic client that sent `code_execution_20250522` or `bash_20250124` to
an Anthropic deployment therefore received a **silently tool-less** request —
the model could not use the tool it had been asked to use — and the only signal
was a server-side `log.warning`, not the caller-visible warning channel
(#498). The behaviour was pinned by
`test_anthropic_encode_drops_unhostable_builtin_with_warning`, whose premise
("unhostable") is wrong for Anthropic's own tools.

**Fix:** the decoders now record the surface that declared the spelling
(`builtin_config["_wire_surface"]`), and the Anthropic encoder re-emits the raw
type only when that surface is its own
(`builtin_tools.passthrough_wire_type`, total against junk). A spelling from
another surface — Responses' `file_search` — is still dropped, so nothing new
can reach a provider that never declared it, and the drop now calls
`self.warn(...)` so the caller sees it.

**Status: fixed** — pinned by `tests/test_fix_round179.py`.

---

## ✅ Fixed — round 180: the all-time timeseries bucketing (2026-10-29)

### 532. `stats.timeseries` attributed every bucket-0 event to the last bucket

🟡 **Medium (wrong numbers on the dashboard's only all-time chart)** —
`wiwi/server/stats.py` (`timeseries`, the all-time branch)

The all-time branch builds a fixed bucket grid (one bucket per day, capped at
30) and located each event with

```python
starts = [int(e.ts // size) * size for e in win]
idx = bisect.bisect_right(starts, int(e.ts // size) * size) - 1
```

`starts` holds **one entry per event**, not one per bucket, so it is full of
duplicates. `bisect_right` returns the position *after* the last element equal
to the key, so an event in the first bucket — whose start is repeated by every
other event in that bucket — resolved to the index of the last duplicate, i.e.
a later bucket, and `max(0, min(idx, len(buckets) - 1))` clamped it onto the
final day.

**Probe** (two events 6 s apart at the start of the window, `buckets=2`):

```
expected [6, 100]   got [0, 106]
```

Every all-time chart therefore showed activity on the *wrong* day, and the
more events a day held the further its count was displaced. The regression came
in with an uncommitted rewrite of the function; the same rewrite carried a
comment that no longer described the code (a note about "private-use
characters", left over from an unrelated change).

**Fix:** the index is arithmetic again — `idx = int((e.ts - first_t) // size)`
— because the buckets are a *contiguous* grid from `first_t` with the empty
buckets included; the `starts` set is only used to size that grid (and is
deduped, since `n_buckets` counts bucket starts, not events). Deduping alone
would not have been enough: a `bisect` over the *populated* starts compresses
the gaps, so a row following a silent day slides into that empty bucket. Both
shapes are pinned by `tests/test_fix_round180.py` — the two-day probe above and
a day-0/day-2 case asserting `[3, 0, 100]` with the middle bucket empty.

**Status: fixed** — round 180.

---

## ✅ Fixed — `wiwi/core/` sweep (2026-10-29)

Read of `core/gateway.py` (2,739 lines), `core/recovery.py`, `core/context.py`
and `core/telemetry.py` against their callers. Five findings; none is a
duplicate of an existing entry (checked by symptom and by file:line). The theme
is the same one #356/#528 came from: **`core/gateway.py` has three hand-written
upstream call paths, and they have drifted.** `_call_once` (non-streaming JSON),
`_complete_via_stream` (streaming-only upstream, non-streaming caller) and
`_pump_once` (streaming caller) each re-implement connect, 401-refresh,
accumulation and accounting; only one of them gets each fix.

### 533. A failed 401-refresh retry is reported as the *stale* 401, and the rotated key is charged for it

🟠 **High (mis-retires a healthy credential)** — `wiwi/core/gateway.py:703`
(`_call_once`), `:843-845` (`_complete_via_stream`); contrast `:1981-1992`
(`_pump_once`)

Both non-streaming arms build `err` from the *first* response and, when the
post-refresh retry also fails, fall through to `raise err` without ever looking
at `retry_resp`. The pump does the opposite and says so in a comment — "surface
the *retry's* error, not the stale 401" — rebuilding the error from the retry's
status and body and parsing its `Retry-After`.

Three consequences, all in the wrong direction:

1. The caller is told `401 authentication_error` when the upstream's real answer
   was, say, 500 or 429. A client that handles 401 by re-authenticating is sent
   to fix a credential that the refresh already fixed.
2. `status_for_key_pool` maps that stale 401 to a **credential** failure
   (`wiwi/providers/base.py:67-73`), so a key that was rotated microseconds
   earlier takes an `err_count` hit and a cooldown. AUDIT #69 records that three
   401/403s retire a key — this path manufactures them out of unrelated 5xx/429
   answers.
3. The retry's `Retry-After` is dropped, so the deployment cooldown is computed
   from nothing, and no `AttemptRecord` is written for the retry (only
   `ok_after_refresh` is, on success — round 178). The request log shows one
   attempt that "returned 401" while the upstream returned two different
   statuses.

**Fix sketch:** mirror `_pump_once:1981-1992` in both arms — on a non-200 retry,
build the error with `error_from_provider_status(retry_resp.status_code,
retry_resp.text, …)`, call `_log_upstream_refusal`, copy `Retry-After` onto it,
`ctx.note_attempt(…, f"http_{retry_resp.status_code}", …)`, and raise *that*.

**Status: fixed** — round 181 (`tests/test_fix_round181.py::test_refresh_retry_failure_surfaces_the_retry_error`,
`::test_refresh_retry_failure_on_the_force_stream_arm_surfaces_the_retry_error`).
Both arms now go through one new helper, `_retry_failure_error(status, body,
headers, dep)`: it builds the error with `error_from_provider_status`, logs the
refusal once, and copies `Retry-After` onto the error. Each arm also
`note_attempt`s the retry's own `http_<status>` before raising, and the
transport-error arms record `transport_error` the same way, so the request log
shows both upstream answers instead of one phantom 401. The rotated key is no
longer charged: the raised status is the retry's, and `status_for_key_pool` only
sees that one.

### 534. `_complete_via_stream` drops three delta kinds and two post-processing steps the pump performs

🟡 **Medium (silent fidelity loss on Cline / WorkBuddy / OpenCode)** —
`wiwi/core/gateway.py:900-960` (`_apply_event`), `:945-946` (the `Finish` arm);
contrast `_pump_once` at `:2018-2019` (loop detector), `:2035-2095` (all arms),
`:2205` (`_validate_leftover_tool_args`)

`streaming/deltas.py` defines thirteen delta variants. `_apply_event` handles
nine. Missing:

* `ServerToolResultDelta` — the provider-hosted tool result (`web_search` hits,
  `tool_reference` entries). It is emitted by
  `wiwi/providers/anthropic_adapter.py:1012`, and `opencode_adapter` delegates
  its Messages route to that adapter (`opencode_adapter.py:532`,
  `self._msg = AnthropicAdapter()`) while declaring `force_stream = True`
  (`:527`). A non-streaming Messages request to an OpenCode `claude-*`
  deployment therefore returns a turn whose `server_blocks` is empty, and the
  encoder has nothing to re-emit — the search hits the model reasoned over are
  gone from the response.
* `LogprobsDelta` — emitted by `wiwi/providers/openai_adapter.py:719`, reachable
  through OpenCode's chat route. `/v1/chat/completions` with `logprobs: true`
  returns tokens with logprobs when streamed and **without** them when not.
* `Finish.stop_sequence` — the arm copies `stop_reason` only (`:946`), so
  `AssistantTurn.stop_sequence` stays `None` on this path. #497 fixed the
  encoder half; the carrier still drops it here.

Plus two steps the pump runs and this arm does not: the loop detector
(`stream_loop_detection` / `stream_loop_limit` are read *only* at `:2018-2019`),
so a repetition loop on a non-streaming request to a `force_stream` provider
burns to `max_tokens` where the streaming path aborts; and
`_validate_closed_tool_args` / `_validate_leftover_tool_args`, so the advisory
tool-arg signal is never produced for these providers.

**Fix sketch:** extend `_apply_event` with the three missing arms
(`ServerToolResultDelta` → append to a `server_blocks` list; `LogprobsDelta` →
accumulate; `Finish` → carry `stop_sequence`), and run the same two post-steps
the pump runs. Better: one shared delta→turn folder used by both arms, so the
next delta variant cannot be added to one and forgotten in the other.

**Status: fixed** — round 181 (`tests/test_fix_round181.py::test_force_stream_reassembly_keeps_server_blocks_logprobs_and_stop_sequence`,
`::test_force_stream_reassembly_aborts_a_repetition_loop`). The three missing
arms are in (`ServerToolResultDelta` → `server_blocks`, `LogprobsDelta` →
`_merge_logprobs` which concatenates the list members instead of keeping the
last frame, `Finish` → `stop_sequence`), and the two post-steps now run:
`ToolCallClose` and the leftover open calls both go through
`self._check_tool_args`, and the read loop feeds `TextDelta`/`ThinkingDelta`
chunks to a `LoopDetector` built from `stream_loop_limit` /
`stream_loop_detection`. A tripped loop raises `WiwiError(400, "api_error", …)`
— 400 is the one status `status_for_key_pool` ignores, and the default
`retryable=False` stops the router re-burning the same prompt, matching the
pump's #108 rationale. The fuller fix (one shared delta→turn folder) is still
open as design work; the arms now agree, which is what this entry tracked.

### 535. `merge_resume_context` folds a resumed attempt's usage but not its `metadata`

🟡 **Medium (the caller is not told a constraint was dropped)** —
`wiwi/core/gateway.py:342-415` (`merge_resume_context`), `:56-67`
(`_record_translation_warnings` writes to *this* context's metadata),
`:2672-2725` (`build_log_event` reads the originating context only)

A mid-stream failover runs the continuation on a fresh `RequestContext`
(`_attempt_resume`), and every advisory signal is written to *that* context:
`translation_warnings`, `tool_args_violations`, `cache_savings`,
`unpriced_model`. The merge folds `attempts`, `usage`, `cost`, `cache_hit` and
`_stream_usage` — and stops there. So a resumed attempt that dropped a
construct (an `anthropic-beta`-gated field, a `strict` mode, a sampling param)
contributes no warning: the `x-wiwi-translation-warnings` header the app builds
from `ctx.metadata` (`app.py:814`) is empty and the request-log column is empty,
which is exactly the "the caller learns nothing" shape #498 was fixed for.

`cache_savings` and `unpriced_model` need a decision rather than a blind merge
(the origin's `cache_savings` is a dollar figure for its own attempt), but
`translation_warnings` and `tool_args_violations` are per-request advisory lists
and should be extended, not overwritten.

**Fix sketch:** in `merge_resume_context`, extend `translation_warnings` and
`tool_args_violations` from the resumed context (create the list if absent), and
decide explicitly whether `unpriced_model`/`cache_savings` propagate — writing
the answer down either way.

**Status: fixed** — round 181 (`tests/test_fix_round181.py::test_merge_resume_context_carries_the_advisory_channels`).
`merge_resume_context` now extends `translation_warnings` and
`tool_args_violations` from the resumed context (deduped, order-preserving) and
**sums** `cache_savings` — the two attempts are billed separately, so the dollar
figures add. The `unpriced_model` half is moot: #536 removed that write
entirely, since its reader was the `x-wiwi-unpriced-model` header, which is
built from the pricing result rather than from metadata.

### 536. Three `ctx.metadata` keys are write-only — nothing reads them

⚪ **Low (dead channel; one of them is documented as reaching the request log)**
— `wiwi/core/gateway.py:53` (`tool_args_violations`), `:2520-2523` and
`:2577-2581` (`unpriced_model`, `unpriced_model_id`), `wiwi/server/app.py:2418-2421`
(same pair, with the comment "Surfaced to the request log via metadata"),
`:2772` (`response_cached`); `wiwi/logging_core/events.py` has no field for any
of them

`build_log_event` (`gateway.py:2672-2725`) reads `cache_savings`,
`response_cache_hit`, `translation_warnings`, `request_body` and `response_body`
— not these three. `_with_warnings` (`app.py:795-822`) forwards only
`translation_warnings`. Repo-wide, `tool_args_violations` appears only in the
gateway and in `tests/test_fix_round20.py`, which asserts the *write*;
`response_cached` appears only at its single write. AUDIT #141 noticed the
`tool_args_violations` half in passing ("a flag nothing reads") but no entry
tracks it.

Consequence: the advisory tool-arg signal reaches an operator only as the
`tool_args_*` proxy-log line from `streaming/validation.py`, never the request
log or the caller; and the `unpriced_model` comment promises a log surface that
does not exist (the response *header* path does work — `app.py:2429`).

**Fix sketch:** either add the three to `LogEvent` (plus the DB column and the
`_with_warnings` merge for the tool-arg list) or delete the writes and correct
the comments. Half a channel is worse than none: it reads as wired.

**Status: fixed** — round 181 (`tests/test_fix_round181.py::test_tool_arg_violations_reach_the_caller_as_a_header`,
`::test_both_advisory_channels_ride_the_same_response`,
`::test_pricing_no_longer_writes_metadata_nothing_reads`). The tool-arg list got
the reader it was missing rather than a new `LogEvent` field: `_with_warnings`
(`app.py`) now emits `x-wiwi-tool-args-warnings` beside the translation list
(shared `_warning_header_value` filter/truncation, both documented in
`docs/API_REFERENCE.md`), and #535 makes the resumed attempt's list reach it.
The request log still has no `tool_args_violations` column — the header is the
caller surface, documented as such, and the operator surface stays the
`tool_args_*` proxy-log lines; adding the column would be a schema change with
no consumer today. `unpriced_model`/`unpriced_model_id` were **deleted** from both `_price` and
`_price_stream` and replaced with a `log.warning("unpriced_model", …)` carrying
model, provider, group and token counts — the caller surface was always the
header, and the metadata copy had no reader. The misleading
"Surfaced to the request log via metadata" comment at `app.py:2418` went with
it. `response_cached` was deleted too: `LogEvent.response_cache_hit` already
carries the fact, so the metadata key was a second convention for one value.

### 537. A deployment the healer marked "dead" is never probed again

⚪ **Low (in-memory only — a restart clears it; traffic is unaffected)** —
`wiwi/core/recovery.py:245` (`MODEL_BAD_DEAD_STREAK`), `:510-517`
(`mark_dead`), `:415-419` and `:446` (`ok()` gates on `blocked()`, which is true
for a dead target), `:486` (the only `clear`)

`_probe_pair` marks the deployment's circuit dead after three consecutive
`CREDS_VALID_MODEL_BAD` verdicts, and `probe_verdict` maps **400 and 404** to
that verdict. `CircuitBreaker.blocked()` returns True for a dead target, so
`_collect_pairs` filters the deployment out of every later sweep, and the only
`clear(did)` sits on the HEALTHY-restore path — which is now unreachable for
that key. A transient 404 (model momentarily unpublished, a bad deploy on the
provider side) or a 400 raised by the *probe's own* shape is therefore enough to
retire the deployment from healing for the life of the process, with one
`healer_deployment_marked_dead` error line and no way to undo it short of a
restart. Nothing in the admin API clears it (no healer route exists).

Note this is the *healer's* circuit, not the deployment's cooldown: traffic
still resumes when `cooldown_until` expires, so the cost is that a recovered
deployment is never restored *early* and never probed again.

**Fix sketch:** treat "dead" as a long backoff rather than a permanent state
(the breaker already has `trip()` for that), or clear the dep circuit when a
real client request through that deployment succeeds (`record_success`), or
expose a reset on the admin healer surface.

**Status: fixed** — round 181 (`tests/test_fix_round181.py::test_model_bad_deployment_is_probed_again_after_the_backoff`;
`tests/test_recovery.py::TestHealthHealer::test_400_on_cooled_dep_escalates_without_retiring_it`,
which pinned the old `dead` assertion and was rewritten). The first of the three
sketches: "dead" is now a long backoff. `_probe_pair`'s
`CREDS_VALID_MODEL_BAD` branch no longer calls `mark_dead` — it trips the dep
circuit, so the capped exponential backoff keeps escalating, and once the window
elapses `_collect_pairs` offers the deployment again. At
`streak >= MODEL_BAD_DEAD_STREAK` it logs `healer_deployment_model_bad` at
error level (the operator signal the old `mark_dead` line carried) and returns;
below that it stays a `healer_probe_model_bad` warning. The `mark_dead` state
itself is left in `CircuitBreaker` for its other callers, and the comment on
`MODEL_BAD_DEAD_STREAK` now says what the constant actually gates.

---

