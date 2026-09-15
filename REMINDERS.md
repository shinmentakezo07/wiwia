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

- [ ] `docs/API_REFERENCE.md:79` — lists `wiwi_provider_cooldowns`, which is rendered
      nowhere, and documents the three quantile metrics as histograms.
- [ ] `docs/API_REFERENCE.md:203` / `docs/ADMIN.md:98` / `docs/ARCHITECTURE.md:58` — pin
      budget-cap semantics to `402`; confirm they still match the C1 fix.
- [x] `wiwi.yaml.example` — done this round: the comment now states the real precedence
      (`wiwi_params.timeout` > provider `timeout_s` > `router_settings.timeout`).
- [ ] `docs/CORE.md` / `docs/ARCHITECTURE.md` — list `raw_body_bytes` and `log_buffer` as
      part of `RequestContext`'s contract; those fields were removed.
- [ ] `UPDATE.md` — **binding** changelog for translation-layer fixes. Any change under
      `wiwi/wire/` or `wiwi/providers/` needs an entry here.

## 4. Test files the agents were forbidden to touch

All four were deliberately **kept** this round, so these test files need no edit:

- [x] `StreamTape.replay_thinking` — kept (test-pinned). Still has no production caller.
- [ ] `ClineAdapter.set_header_context` — kept (test-pinned, no production caller). To make
      `X-Task-ID` actually reach Cline, add
      `adapter.set_header_context({"task_id": request.headers.get("x-task-id")})` beside the
      three `set_tool_context` call sites in `wiwi/core/gateway.py` (~226, ~329, ~772).
      There is no inbound-header path into `RequestContext` today, so this needs a codec
      change too — that is why it was not done.
- [x] `DBSink.invalidate_cache` — kept and now **called** from `write_requests`/`write_audit`,
      so a freshly logged request is immediately visible to `/admin/stats/*` and
      `/admin/logs/*` instead of for up to 5 s.
- [x] `LoggingSubsystem.dropped_request_logs` — kept and now surfaced on `/metrics`
      (`wiwi_request_logs_dropped_total`) and `/health`.

## 5. Product decisions left open

- [ ] **`log_requests` / `header_allowlist`** — both config fields were **deleted** this
      round as dead (nothing read either). The docs in §3 still describe them and must be
      updated.
- [x] **`excluded_providers`** — the dead disjunct was removed this round (the set was
      declared and read but never populated, so `pname in excluded_providers` was always
      `False`); the surviving cadence still works through `provider_consec`.
- [x] **Audit log surface** — `DBSink.read_audit()` + `LoggingSubsystem.read_audit()` and the
      `/admin/logs/audit` route now exist and are verified live (200 with rows for a master
      bearer, 401 anonymously). Only a **UI page** remains undecided.
- [ ] **`POST /admin/cline/oauth/auto-connect`** is implemented but unreachable from the
      SPA (both Cline UIs use the paste-code flow deliberately). Wire it or drop it.
- [ ] **31 unimported `web/src` modules** (~6.8k LOC of an abandoned `llmgateway.io` port).
      Deleted, or kept as reference?

## 6. Environment

- [ ] **`import wiwi` outside the repo root resolves to a stale checkout** at
      `/teamspace/studios/this_studio/Fionn` — the editable install points there. Reinstall
      from this checkout (`pip install -e .`) or export
      `PYTHONPATH=/teamspace/studios/this_studio/wiwia` in your shell profile. Until then,
      any ad-hoc script run from elsewhere exercises different code.

## 7. Verify before you commit

```bash
cd /teamspace/studios/this_studio/wiwia
python3 -m pytest tests/ -q          # the binding gate
ruff check wiwi/ tests/              # the binding gate
cd web && bun run build && bun run lint
```

The pre-existing baseline was **1672 passed, ruff clean**. After this round's fixes the
suite is **1733 passed, ruff clean** — the increase is the 61 new regressions in
`tests/test_fix_round55.py`, not a relaxed gate. Anything below 1733 is a regression;
`AUDIT_REPORT.md`'s verification log records what each finding looked like before the fix.

`bun run build` was verified green this round (tsc strict + vite, 2394 modules). `bun run
lint` was not run.

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
