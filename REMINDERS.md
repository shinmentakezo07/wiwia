# REMINDERS — manual follow-ups

**Nothing here has been committed or pushed.** The working tree holds every fix from
this round plus your pre-existing uncommitted work (`AGENTS.md`, `UPDATE.md`,
`wiwi/providers/workbuddy_adapter.py`, `tests/test_fix_round53.py`), which was left
untouched throughout.

Update this file as items are done. Delete a line when it is finished; the list is the
source of truth for what is still outstanding.

---

## 1. Commit (yours to do)

```bash
cd /teamspace/studios/this_studio/wiwia
git status                      # review everything first
git diff                        # read the whole diff before staging
```

Suggested split — one logical change per commit, imperative present tense, capitalized,
no prefix tags (per `AGENTS.md`):

| # | Message | Scope |
|---|---|---|
| 1 | `Enforce budget caps on both response paths` | `wiwi/server/app.py`, `wiwi/auth/service.py` |
| 2 | `Scope the response cache to the full request` | `wiwi/cache/keygen.py` |
| 3 | `Accept admin sessions on every admin route` | `wiwi/server/app.py` |
| 4 | `Repair provider aliases on rename` | `wiwi/server/app.py` |
| 5 | `Harden the streaming and forced-stream decode paths` | `wiwi/core/gateway.py`, `wiwi/wire/`, `wiwi/providers/` |
| 6 | `Evict expired keys from the auth cache` | `wiwi/auth/service.py` |
| 7 | `Fix the admin console defects found in audit` | `web/src/` |
| 8 | `Remove dead code found in audit` | mixed |
| 9 | `Record the deep audit findings` | `AUDIT_REPORT.md`, `AUDIT.md` |

Your four pre-existing files belong in their own commit(s) — do not fold them into the
fix commits.

## 2. Do NOT commit these

Per `AGENTS.md`: `wiwi.yaml`, `wiwi.db`, `.env`, `key.md`, `opencode.json(c)`, `*.har`,
anything under `.wiwi/` or `.verify/`. `AUDIT_REPORT.md` is intentionally tracked; the
throwaway harnesses the fixers wrote are not.

## 3. Documentation the agents could not edit

Each fixer was scoped to code only, so these doc lines still describe the old behaviour:

- [x] `docs/API_REFERENCE.md:79` — **done this round.** `wiwi_provider_cooldowns` (rendered
      nowhere) is gone; the line now lists the series actually exported and states that the
      three quantile families are `summary`, not `histogram`.
- [x] `docs/API_REFERENCE.md:203` / `docs/ADMIN.md:98` / `docs/ARCHITECTURE.md:58` — **verified
      this round**: budget caps are still reported as `402` on the refusal path, and the
      post-response semantics ("applied post-response; exceeding a cap yields `402` on
      subsequent requests") match the shipped behaviour. No edit needed.
- [x] `wiwi.yaml.example` — done this round: the comment now states the real precedence
      (`wiwi_params.timeout` > provider `timeout_s` > `router_settings.timeout`).
- [x] `docs/CORE.md` / `docs/ARCHITECTURE.md` — **done this round.** `raw_body_bytes` and
      `log_buffer` are removed from the `RequestContext` field lists; `est_tokens` and
      `forward_headers` (both live) are named instead.
- [x] `UPDATE.md` — **done this round.** The round-75/76/78 entry records the codec, adapter
      and gateway fixes (AUDIT #159-#211) across `wiwi/wire/`, `wiwi/providers/` and
      `wiwi/core/gateway.py`.

## 4. Test files the agents were forbidden to touch

All four were deliberately **kept** this round, so these test files need no edit:

- [x] `StreamTape.replay_thinking` — kept (test-pinned). Still has no production caller.
- [x] `ClineAdapter.set_header_context` — **KEPT, decision recorded (round 85).** The
      wiring was investigated and deliberately NOT added. `X-Task-ID` is *optional*
      client-identity context: `headers()` already emits it whenever `_context["task_id"]`
      is set, and the adapter's module docstring lists the headers Cline actually
      *requires* (`HTTP-Referer`, `X-Title`, `X-CLIENT-*`, `X-PLATFORM*`) — `X-Task-ID` is
      not among them. Nothing in `docs/`, `AUDIT.md` or the Cline upstream contract
      indicates it is needed for a request to succeed, and `AUDIT_REPORT.md` records the
      method as "tests only" without a demonstrated failure from its absence. Wiring it
      would also require a codec change (no inbound-header path into `RequestContext`
      exists — `forward_headers` is allowlisted to `anthropic-beta` only), so it is a
      feature, not a fix. Left as-is: test-pinned, no production caller, no user-visible
      defect. Revisit only if a real Cline request is observed to fail without it.
- [x] `DBSink.invalidate_cache` — kept and now **called** from `write_requests`/`write_audit`,
      so a freshly logged request is immediately visible to `/admin/stats/*` and
      `/admin/logs/*` instead of for up to 5 s.
- [x] `LoggingSubsystem.dropped_request_logs` — kept and now surfaced on `/metrics`
      (`wiwi_request_logs_dropped_total`) and `/health`.

## 5. Product decisions left open

- [x] **`log_requests` / `header_allowlist`** — both config fields were **deleted** as dead
      (nothing read either). **Verified this round**: `grep -rn "log_requests\|header_allowlist"
      docs/ wiwi.yaml.example README.md` returns no hits, so no documentation describes them.
- [x] **`excluded_providers`** — the dead disjunct was removed this round (the set was
      declared and read but never populated, so `pname in excluded_providers` was always
      `False`); the surviving cadence still works through `provider_consec`.
- [x] **Audit log surface** — `DBSink.read_audit()` + `LoggingSubsystem.read_audit()` and the
      `/admin/logs/audit` route now exist and are verified live (200 with rows for a master
      bearer, 401 anonymously). Only a **UI page** remains undecided.
- [x] **`POST /admin/cline/oauth/auto-connect`** — **KEPT as dormant (decision round 85).**
      It is implemented and tested (5 tests in `tests/test_admin_cline_oauth.py`), and the
      route is correct; it simply cannot complete end-to-end today because Cline's Google
      OAuth ignores the `callback_url` parameter, so the redirect never carries the `?code=`
      back. Both Cline UIs therefore drive the paste-code flow deliberately
      (`web/src/api/client.ts:432-435` documents this at the call site). Kept rather than
      deleted because it needs no maintenance and becomes live the moment Cline honours
      `callback_url` — at which point it is the better UX (no copy-paste). No UI is wired to
      it. If Cline never fixes it, deleting is a one-commit change.
- [x] **31 unimported `web/src` modules** — **RESOLVED (verified round 85).** An import
      sweep over all 74 `web/src` `.ts`/`.tsx` files finds **0** modules whose basename is
      referenced by no other file: the abandoned `llmgateway.io` port has already been
      cleaned out. Nothing to delete; the item was stale. (`bun run build` compiles 2394
      modules and `bun run lint` reports 0 errors, so nothing is orphaned.)

## 6. Environment

- [x] **`import wiwi` outside the repo root resolves to a stale checkout** — **FIXED
      (round 85).** The editable install (`_editable_impl_wiwi.pth`) pointed at
      `/teamspace/studios/this_studio/Fionn`, whose last commit was 2026-09-03. Reinstalled
      from this checkout with `uv pip install -e . --no-deps` (plain `pip` fails here:
      `hatchling.build` is not installed in the env, so `--no-build-isolation` cannot work
      either). Verified from `/tmp` and from the repo root: both now resolve to
      `/teamspace/studios/this_studio/wiwia/wiwi/__init__.py`. The Fionn checkout is dormant
      (no writes in 7 days) and was left untouched, so this is reversible.

## 7. Verify before you commit

```bash
cd /teamspace/studios/this_studio/wiwia
python3 -m pytest tests/ -q          # the binding gate
ruff check wiwi/ tests/              # the binding gate
cd web && bun run build && bun run lint
```

The pre-existing baseline was **1672 passed, ruff clean**. After this round's fixes the
suite is **2264 passed, ruff clean** — the increase is the new `test_fix_round68/75-84`
regressions plus the round-69-74 files, not a relaxed gate. Anything below 2264 is a
regression; `AUDIT_REPORT.md`'s verification log records what each finding looked like
before the fix.

`bun run build` was verified green this round (tsc strict + vite, 2394 modules) and
`bun run lint` reports **0 errors** (20 pre-existing warnings, none on changed lines). The
six web findings (#205-#210) are verified in real Chromium by
`.verify/webconsole/redgreen.py` — **29/29 checks pass on the fixed bundle** and the
discriminating subset fails on a pre-fix build.

## 8. Console lag at 24h+ ranges (reported 2026-09-15, fixed)

**Symptom:** the console became sluggish after selecting a range wider than ~1 hour.

**Cause (measured, not inferred).** Two compounding problems:

1. `GET /admin/logs/requests?limit=10000` returns a fixed 10,000-row / 5.46 MB JSON
   body, and the frontend applies the time window **client-side**. The payload is
   the same for every range — but the number of rows that survive the filter is not:
   at "Last hour" ~350 rows remain, at "Last 24 hours" ~9,000 do.
2. Both `Usage.tsx` and `RequestLogs.tsx` rendered **every** surviving row with no
   pagination or virtualization (~15 DOM nodes per row).

Measured in headless Chrome against 10,000 seeded rows:

| Range | Rows rendered | DOM nodes |
|---|---|---|
| Last hour | 352 | 6,212 |
| Last 24 hours | 9,025 | **136,309** |

A 22x DOM jump at 24h. The `Usage` range is also persisted in `localStorage`
(`wiwi.usage.range`), so once 24h is picked the page stays slow on every visit.

**Fix:** render at most `ROWS_PER_PAGE = 250` rows on both pages, with a
"Show 250 more" button and a `showing X of Y` count. Aggregates (totals row,
summary chips, chart) still use the full filtered set, so no number changed.

**Verified:** 24h now renders 250 rows / 6,539 nodes instead of 9,025 / 136,309 —
a 21x reduction — and "Show 250 more" expands to 502 rows while the Totals row
still reports all 9,350 requests. `bun run build` and `bun run lint` clean
(0 errors); the 126-row 30m view is unaffected.

**Still open (the deeper fix, not done):** the API contract itself is wrong for a
wide window — the client should ask for the rows it needs instead of pulling 10,000
and discarding 99%. Adding `minutes` / `offset` parameters to
`/admin/logs/requests` (the sink already takes `limit`) would cut the payload from
5.46 MB to a few KB and remove the client-side window filter entirely. That touches
the endpoint, `read_requests`, and six call sites; it is a separate change.

**DONE (round 85).** `GET /admin/logs/requests` now takes `minutes` (0 = all-time, the
same convention as `/admin/stats/*`) and `offset`, passed through `read_requests` /
`_read_requests_uncached` and applied to the ring fallback too, so both paths agree.
Both are clamped at the endpoint (`minutes = max(0, minutes)`, likewise `offset`), and
the query cache keys on them — without that, two windows would have served each other's
rows. The zero-argument call is byte-identical to before, so all six existing callers
keep today's behaviour.

`Usage.tsx` was migrated: every consumer of its rows already derived from the same
`l.ts >= now - range*60` predicate the server now applies, so the switch is provably
equivalent (the client-side filter is kept as a guard for the placeholder window while a
new range loads). **`RequestLogs.tsx` was deliberately NOT migrated** — its unfiltered
set feeds three non-time-filtered surfaces (the model/provider/surface dropdowns via
`distinctOptions`, the two distinct empty states, and the footer total), so windowing the
fetch would silently shrink all three. That page keeps the full fetch.

Verified live: all-time 10 rows, `minutes=10` → 5, `offset=2` → 3, negative values clamp
to the no-op, and `offset` returns a strict suffix of the unlimited query.
`tests/test_fix_round86.py` (10).

## 9. Request-log storage cap (implemented)

`request_logs` is now bounded by row count as well as age, with the removed rows
preserved as aggregates:

- `log_max_rows: 10000` (default) — keep at most N raw rows
- `log_retention_days: 30` — age limit, unchanged
- `log_prune_interval_s: 3600` — sweep interval (0 = startup only; it used to be
  startup-only always, so a long-running server never pruned at all)

Both paths aggregate into the new `request_rollups` table (hourly, grouped by
`key_id`/`model_group`/`provider`) inside the SAME transaction as the delete, so
a crash cannot lose rows or double-count them. Every read that consumes
`request_logs` — overview, timeseries, per-key scoping — unions the rollups back
in, so totals, token counts, cost, cache stats and percentiles are unchanged by
pruning. Verified live: 120 requests, cap 50 → 50 raw rows, `requests=120` and
`tok_in=1320` still reported.

**Known approximation:** percentiles. p95 cannot be summed, so each rollup row
stores its own bucket's p95 and reads combine it with raw samples by weighting
each side by sample count. Exact when a window is entirely raw or entirely
rolled up (the common cases); approximate in the mixed band. The alternative was
keeping every sample forever, which is the growth this exists to stop.

**Not done:** the rollup only records `tps`/`ttft_ms`/`latency_ms` percentiles —
the same three the dashboard shows. If a new percentile metric is added, it needs
a column in `request_rollups` and a pass in `rollup_and_prune`.

## 8. New regressions added this round

`tests/test_fix_round55.py` — 46 tests, one per fixed finding, all behavioral (they assert
the contract a consumer observes, not the implementation). They were RED against the
pre-fix tree and are GREEN now. Also corrected in this round, because they pinned
pre-fix behaviour rather than the contract:

- `tests/test_fix_round45.py::test_cache_never_serves_over_budget_payload` asserted
  `route.call_count == 2`; the C1 fix makes it **1**, because the second request is now
  refused at admission instead of being re-dispatched upstream. Updated with a comment.
- `tests/test_stats_db.py::test_cache_invalidated_by_invalidate_cache` seeded its second
  row *through* `write_requests` and asserted the stale cache still showed one row — i.e.
  it pinned the bug the `invalidate_cache` wiring fixes. The setup now inserts through raw
  SQL instead.
