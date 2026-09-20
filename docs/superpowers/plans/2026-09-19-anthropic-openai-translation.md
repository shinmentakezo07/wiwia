# Anthropic Messages ↔ OpenAI-Compatible Chat Completions Translation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing hub-and-spoke translation path between the **Anthropic Messages** surface (`POST /v1/messages` ↔ `providers/anthropic_adapter.py`) and the **OpenAI Chat Completions** surface (`POST /v1/chat/completions` ↔ `providers/openai_adapter.py`) correct, lossless and *fail-loud* in **both** directions, and close the one confirmed probe-classification defect that lets a dead Anthropic deployment be declared HEALTHY and restored into rotation.

**Architecture:** No new endpoint, no new provider type, no pairwise converters. Everything routes **dialect → wire codec → canonical IR → provider adapter → provider API** and back. The work is (a) a small set of *shared canonical helpers* in `wiwi/ir/` that both `wire/` and `providers/` may import, (b) hardening the two codecs and the two adapters against the specific lossy paths listed in the Spec, (c) an **adapter-owned probe validator** so the HealthHealer asks the adapter that owns the provider dialect how to classify a 200 body — keeping provider knowledge in `providers/` and out of `core/recovery.py`, and (d) regression coverage that asserts the round trip end-to-end.

**Tech Stack:** Python 3.12 (`python3` on PATH), asyncio, httpx, orjson, structlog, Pydantic v2 (config only), frozen `@dataclass` IR/streaming types, pytest 9 + pytest-asyncio (`asyncio_mode = "auto"`), respx (decorator form), hypothesis for round-trip invariants.

**Spec:** This plan is the spec for the batch. `docs/ARCHITECTURE.md` and `docs/CORE.md` are design docs that intentionally run ahead of the code — trust the code where they disagree.

**Out of scope (do NOT do in this plan):**
- The Kaggle dataset directory and every dataset-validation/filtering concern. Unrelated to this repository's translation path; do not inspect, restore, modify or report on it.
- **True** OpenAI Responses API support on either side (inbound `openai_responses` codec already exists and is *not* touched) and **true** provider-side OpenAI `/v1/responses` outbound. Both are separate future work.
- Any new public route, any new entry in `PROVIDER_TYPES`, any `web/` or admin-UI change, any commit or push.

---

## Spec

### The two directions

| # | Client dialect | Provider adapter | Status today |
|---|---|---|---|
| A | Anthropic Messages | `AnthropicAdapter` | native passthrough; **not** in scope for behaviour change beyond the dialect-correctness fixes below |
| B | OpenAI Chat | `OpenAIAdapter` | native passthrough; **not** in scope for behaviour change beyond the dialect-correctness fixes below |
| C | **Anthropic Messages** | **`OpenAIAdapter`** | forward direction, integration-tested (`test_anthropic_surface_to_openai_backend`), gaps listed below |
| D | **OpenAI Chat** | **`AnthropicAdapter`** | reverse direction; no integration test at all, gaps listed below |

C and D are the deliverable. A and B are covered only insofar as a fix in a shared helper touches them — every such touch must be proven by the existing suites staying green.

### Confirmed defects and gaps this plan closes

1. **HealthHealer declares a dead Anthropic deployment HEALTHY (🔴).** `wiwi/core/recovery.py:126-135` `probe_verdict()` treats any HTTP 200 whose body is not the WorkBuddy `{"code": N, "msg": …}` envelope as `HEALTHY`. `_body_is_error_envelope()` (`recovery.py:139-181`) recognizes exactly that one shape. Anthropic rides overload/rate conditions on **200 with an error envelope** — `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}`. Verified by source read: a probe of a cooling Anthropic key that returns that body today yields `HEALTHY`, and `HealthHealer._probe_pair` (`recovery.py:391`) then restores the key **and** clears the deployment's cooldown into probation — restoring a target that is provably not serving. The same class applies to any provider whose 200-body is a structured error; the fix is a **provider-owned** validator, not a second hardcoded branch in `core/`.
2. **`ProviderAdapter` has no probe hook at all** (`wiwi/providers/base.py:274-288`, five methods). There is no seam by which a provider can say "this 200 body is an error", which is *why* the WorkBuddy shape had to be hardcoded in `core/` in the first place.
3. **Antonomasia of `extras` direction is asymmetric.** `wiwi/wire/anthropic_messages.py:452` uses an **allowlist** (`_PASSTHROUGH_KEYS`) while `wiwi/wire/openai_chat.py:305` uses a **denylist** (`_KNOWN_KEYS`). A client param named `thinking`/`budget_tokens`-again, or any future Anthropic param added to the Messages surface but not to the allowlist, is silently dropped when the call is routed to an **OpenAI provider** — and symmetrically, an OpenAI-only param reaching an **Anthropic provider** is dropped without a diagnostic. Silent drops are the failure mode; the plan makes the drop *visible* (structlog at debug) and covers the params clients actually set.
4. **`stop_reason` / `finish_reason` mapping is lossy in one direction.** `wiwi/providers/openai_adapter.py` maps `finish_reason` with an inline dict (`{"stop","length","tool_calls"→"tool_call","function_call"→"tool_call","content_filter"}`) defaulting to `"stop"`; `wiwi/wire/anthropic_messages.py:_STOP_REASON_OUT:461` and `wiwi/providers/anthropic_adapter.py:_STOP_REASON_IN` own the Anthropic side. A provider that reports a **non-OpenAI** `finish_reason` (e.g. `"error"`, `"content_filter"` variants, `"stop_sequence"`) is silently coerced to `"stop"`, and an Anthropic client cannot tell a `max_tokens` truncation from a natural end. This plan centralizes the map and makes unmapped values observable.
5. **Thinking/reasoning fidelity across the seam.** `reasoning_effort` (OpenAI spelling) ↔ `thinking.budget_tokens` (Anthropic spelling) already goes through `ir/types.py:_EFFORT_BUDGETS` / `effort_to_thinking_budget()` / `thinking_budget_to_effort()`, and `wiwi/providers/openai_adapter.py` already gates `reasoning_effort` behind `is_native_openai`. What is **not** covered: emitting reasoning back to an Anthropic client when the OpenAI provider used `reasoning` (not `reasoning_content`); **redacted thinking** (`ThinkingPart(block_type="redacted_thinking", data=…)`) replaying verbatim through the OpenAI adapter (which must drop it rather than emit a bogus `content` string) and surviving into the next turn on the Anthropic side; and `thinking.signature` round-tripping.
6. **`cache_hit` vs `response_cache_hit` conflation risk.** `wiwi/cache/` sets `LogEvent.response_cache_hit`; `cache_hit` is the *provider prompt-cache* hit and feeds `wiwi_prompt_cache_hits_total`. A translation change that starts populating `Usage.cached_tokens` from a new place must not touch `cache_hit`. Called out as an explicit invariant, not a change.
7. **The reverse direction has no end-to-end test.** `tests/test_integration.py` covers C (`test_anthropic_surface_to_openai_backend`) only. D — an OpenAI Chat client, backed by an Anthropic provider, including streaming — has no integration coverage, so nothing today would catch a regression that breaks it.

### Non-negotiable invariants

- **Streaming contract** (`wiwi/streaming/deltas.py`) — exactly one `StreamStart` first; `ToolCallOpen → ToolCallArgsDelta* → ToolCallClose` nested per index (parallel calls are siblings, so several indices may be open at once and closed in a batch); `UsageFinal` **at least once**, after content, and *may repeat* (encoders buffer last-write-wins); then `Finish`; then exactly one of `StreamEnd | StreamError`. `StreamError` may terminate at **any** point and needs no preceding `Finish`. Every delta is `@dataclass(frozen=True)`; per-stream state lives on the **adapter instance**, never on a delta.
- **`fresh_adapter(type)` on every hot path.** Adapters accumulate per-stream decode state (open tool indices, name fragments, deferred opens, NIM aliases) across awaits. `get_adapter(type)` is a `reset()`-on-hand-out singleton and is safe only for synchronous, non-await-held use.
- **No dialect/provider branching outside `wiwi/wire/` and `wiwi/providers/`.** In particular `core/recovery.py` must not gain an `if provider_type == "anthropic"`. The only carve-out is the generic contracts in `providers/base.py` + `providers/registry.py`.
- **Shared helpers importable by both layers live in `wiwi/ir/`** (its `__init__.py` is empty; `wiwi/ir/types.py` is imported by `wire/` and `providers/` alike). Putting a shared helper in `wiwi/wire/` would force `providers/` to import `wiwi.wire`, which is forbidden. Verified by `grep -rn "from wiwi.wire\|import wiwi.wire" wiwi/providers/ wiwi/core/ wiwi/router/ wiwi/streaming/ wiwi/ir/` → **no matches**.
- **`tests/test_recovery.py::TestProbeVerdict.test_table` calls `probe_verdict(200)` with NO body and asserts `HEALTHY`.** Any body-aware change must keep that call site green.
- **`UPDATE.md` is binding.** Read it before editing any of `wiwi/wire/{openai_chat,openai_responses,anthropic_messages}.py` or `wiwi/providers/{openai,anthropic,openrouter}_adapter.py`; add an entry for every fix that lands in those files.
- **`AUDIT.md` is binding.** Defect #1 above is a real, source-verified finding: it gets an entry **before or alongside** the fix, with the `file:line` citations, the trigger, and a one-line fix sketch, and the entry is marked fixed in place when Task 4 lands.

## Global Constraints

- Use the ambient `python3` (3.12) / `pytest` (9.1.1) / `ruff` (0.16.4) on `PATH`. **NEVER** `.venv/bin/python` — there is no usable `.venv` in this checkout.
- Gate, both green, fresh output, before any completion claim:
  `python3 -m pytest tests/ -q && ruff check wiwi/ tests/`
- Ruff: `line-length = 100`, target `py311`, `EXE002` ignored. No mypy/pyright.
- Tests: bare `async def test_*` (no `@pytest.mark.asyncio` — `asyncio_mode = "auto"`); **no `conftest.py` anywhere**, so each file builds its own `_config()` factory and its own `LifespanManager + httpx.ASGITransport` client fixture inline; upstream mocking with **respx decorator form** (`@respx.mock`) — the context-manager form is broken in respx 0.23 + httpx 0.28.
- New bug-fix regressions go into the next unused `tests/test_fix_roundN.py`. `ls tests/test_fix_round*.py | sort -V | tail` currently ends at **round 90**, so the next number is **91** — but peer sessions share this tree, so **re-run that `ls` immediately before the `Write`** and never assume the number.
- Thematic translation regressions belong in `tests/test_translation_enhancements.py`; codec round-trips in `tests/test_codecs.py`; end-to-end surface routing in `tests/test_integration.py`; recovery/probe classification in `tests/test_recovery.py`.
- IR and streaming types are **frozen dataclasses**. Do not add fields to `Request`, `AssistantTurn`, `Usage`, `GenParams`, or any `IRStreamDelta` variant in this plan. New state belongs on the adapter instance.
- Pydantic v2 for config and admin schemas only. `orjson` in hot paths. `structlog` everywhere — **never `print`** from library code.
- Admin auth for any test hitting `/admin/*`: `Authorization: Bearer sk-wiwi-master-test`.
- **No commit steps in this plan.** The repository forbids committing or pushing unless the user gives a direct instruction in that turn. Leave the tree dirty; run the gate; report; stop.
- **No branches, no worktrees.** Work directly on `main` in the main checkout.
- **Never touch** `wiwi.yaml`, `wiwi.db`, `key.md`, `.env`, `opencode.json(c)`, `.verify/`, `.wiwi/`, `*.har` — they hold live provider keys and runtime state and are all gitignored.
- Anything UI/UX (there is none in scope here) would additionally require desktop **and** ~375px mobile verification per the binding UI/UX rule. Not applicable — but do not add a `web/` change and claim exemption.

---

### Task 1: Shared canonical translation helpers in `wiwi/ir/`

**Why:** Four separate places need to agree on the same mapping, and today they do not (`openai_adapter` inline dict, `anthropic_messages._STOP_REASON_OUT`, `anthropic_adapter._STOP_REASON_IN`, `ir/types._EFFORT_BUDGETS`). One owner, imported by both layers, is the only way to keep `wire/` and `providers/` from drifting. `wiwi/ir/` is the one package both may import.

**Files:**
- Create: `wiwi/ir/translation.py`
- Create: `tests/test_translation_helpers.py`

**Interfaces:**
- Produces (later tasks rely on these exact names):
  - `OPENAI_FINISH_TO_IR: dict[str, ir.StopReason]` — the single OpenAI-`finish_reason` → IR map, covering `stop`, `length`, `tool_calls`, `function_call`, `content_filter`, and the non-standard spellings seen in the wild (`tool_use`, `max_tokens`, `end_turn`, `stop_sequence`, `error`).
  - `ir_to_openai_finish(stop_reason: str) -> str` — IR → OpenAI `finish_reason`; the inverse, used by the OpenAI wire encoder and the OpenAI adapter's stream encoder.
  - `normalize_finish_reason(raw: Any) -> ir.StopReason` — total, never raises: non-`str` and unmapped values return `"stop"` **and** emit `log.debug("finish_reason_unmapped", raw=…)` exactly once per distinct unmapped value per process.
  - `carry_extras(source: dict[str, Any], known: frozenset[str]) -> dict[str, Any]` — returns the sub-dict of `source` whose keys are **not** in `known` (the denylist shape `openai_chat` already uses), logging at debug which keys were dropped only when at least one key of interest is present. Total; never raises on typed-wrong input.
  - `merge_extras(*layers: dict[str, Any]) -> dict[str, Any]` — left-to-right merge, later wins, skipping non-dict layers. Replaces ad-hoc `{**a, **b}` on the extras path so a `None`/list layer cannot poison the body.
  - Module logger: `log = structlog.get_logger("wiwi.ir.translation")`
- Consumes: `wiwi.ir.types` only. **No** import of `wiwi.wire`, **no** concrete adapter import.

- [ ] **Step 1: Write the failing tests** — create `tests/test_translation_helpers.py` with exactly:

```python
"""Shared canonical translation helpers: finish_reason maps and extras merging.

These helpers live in ``wiwi/ir/`` because both ``wiwi/wire/`` (inbound codecs)
and ``wiwi/providers/`` (outbound adapters) import them. Anything placed in
``wiwi/wire/`` would force the providers package to import a dialect module,
which the layering rule forbids.
"""
import pytest

from wiwi.ir import translation as tr


class TestFinishReason:
    def test_openai_to_ir_covers_standard_spellings(self):
        assert tr.normalize_finish_reason("stop") == "stop"
        assert tr.normalize_finish_reason("length") == "length"
        assert tr.normalize_finish_reason("tool_calls") == "tool_call"
        assert tr.normalize_finish_reason("function_call") == "tool_call"
        assert tr.normalize_finish_reason("content_filter") == "content_filter"

    def test_openai_to_ir_covers_nonstandard_spellings(self):
        assert tr.normalize_finish_reason("tool_use") == "tool_call"
        assert tr.normalize_finish_reason("max_tokens") == "length"
        assert tr.normalize_finish_reason("end_turn") == "stop"
        assert tr.normalize_finish_reason("stop_sequence") == "stop_sequence"

    def test_normalize_is_total(self):
        # Never raise, always a legal StopReason.
        assert tr.normalize_finish_reason(None) == "stop"
        assert tr.normalize_finish_reason(5) == "stop"
        assert tr.normalize_finish_reason(["length"]) == "stop"
        assert tr.normalize_finish_reason("who_knows") == "stop"

    def test_ir_to_openai_is_the_inverse_on_the_shared_vocabulary(self):
        assert tr.ir_to_openai_finish("stop") == "stop"
        assert tr.ir_to_openai_finish("length") == "length"
        assert tr.ir_to_openai_finish("tool_call") == "tool_calls"
        assert tr.ir_to_openai_finish("content_filter") == "content_filter"

    def test_ir_to_openai_is_total_for_anthropic_only_reasons(self):
        # Anthropic-only IR reasons have no OpenAI spelling; they must not
        # crash and must land on something an OpenAI client accepts.
        for sr in ("pause_turn", "stop_sequence", "context_window_exceeded",
                   "compaction", "nope"):
            assert isinstance(tr.ir_to_openai_finish(sr), str)

    def test_roundtrip_through_ir_is_stable(self):
        for raw in ("stop", "length", "tool_calls", "function_call",
                    "content_filter", "tool_use", "max_tokens"):
            once = tr.normalize_finish_reason(raw)
            assert tr.normalize_finish_reason(tr.ir_to_openai_finish(once)) == once


class TestCarryExtras:
    def test_denylist_shape(self):
        known = frozenset({"model", "messages"})
        assert tr.carry_extras({"model": "m", "messages": [], "top_k": 3},
                               known) == {"top_k": 3}

    def test_typed_wrong_source_is_empty_not_a_crash(self):
        known = frozenset({"model"})
        assert tr.carry_extras({}, known) == {}
        assert tr.carry_extras({"model": 1}, known) == {}

    def test_empty_known_returns_everything(self):
        assert tr.carry_extras({"a": 1}, frozenset()) == {"a": 1}


class TestMergeExtras:
    def test_later_wins(self):
        assert tr.merge_extras({"a": 1, "b": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_skips_non_dict_layers(self):
        assert tr.merge_extras(None, {"a": 1}, [1, 2], "x", {}) == {"a": 1}

    def test_none_layer_does_not_erase(self):
        # `{**None}` raises; this must not.
        assert tr.merge_extras({"a": 1}, None) == {"a": 1}


def test_helpers_are_importable_from_the_providers_layer():
    """Layering guard: `wiwi/ir/translation.py` is the only place both
    `wiwi/wire/` and `wiwi/providers/` may import a shared translation helper
    from. This test lives beside the helpers so a future move is caught."""
    import importlib
    mod = importlib.import_module("wiwi.ir.translation")
    assert mod is not None
    assert not hasattr(mod, "_WIRE_ONLY_MARKER")
```

- [ ] **Step 2: Run the tests and confirm RED**

```bash
python3 -m pytest tests/test_translation_helpers.py -q
```
Expected: `ModuleNotFoundError: No module named 'wiwi.ir.translation'` (collection error). Confirm the failure is the missing module, **not** a typo in the test file.

- [ ] **Step 3: Write `wiwi/ir/translation.py`** — implement to the interfaces above. Constraints: no `wiwi.wire` import; no concrete adapter import; `structlog` for the debug log; `Any` from `typing`; `ir.StopReason` from `wiwi.ir.types`. Log an unmapped `finish_reason` **once per distinct value** using a module-level `set` guard so a hot loop cannot flood the log.

- [ ] **Step 4: Run the tests and confirm GREEN**

```bash
python3 -m pytest tests/test_translation_helpers.py -q
```
Expected: all pass.

- [ ] **Step 5: Prove the new module does not break the layering rule**

```bash
grep -rn "wiwi.wire" wiwi/ir/
```
Expected: **no output**. If anything matches, the helper was placed wrong — move it, do not add an exemption.

- [ ] **Step 6: Prove nothing regressed**

```bash
python3 -m pytest tests/test_codecs.py tests/test_translation_enhancements.py -q
ruff check wiwi/ir/ tests/test_translation_helpers.py
```
Expected: green. This task adds a module and imports nothing existing, so a failure here means the test file itself is wrong.

---

### Task 2: OpenAI Chat Completions side — mapping through the shared helpers

**Why:** `wiwi/providers/openai_adapter.py` carries the inline `finish_reason` dict and the `is_native_openai` / `emit_per_message_reasoning` provider gates; `wiwi/wire/openai_chat.py` carries the `_KNOWN_KEYS` denylist and the `ChatStreamEncoder`. Both must delegate the *mapping* to Task 1's helpers so the two Anthropic-facing paths cannot drift, and both must keep their existing behaviour on every input the current suites cover.

**Files:**
- Modify: `wiwi/providers/openai_adapter.py` (the `finish_reason` mapping in `decode_stream_event`, and the sync `decode_response` mapping if present)
- Modify: `wiwi/wire/openai_chat.py` (`extras` construction at ~:305; `Finish` emission in `ChatStreamEncoder`)
- Modify: `tests/test_translation_enhancements.py` (append regression tests)
- Modify: `UPDATE.md` (new entry — see Task 6)

**Interfaces:**
- Consumes: `wiwi.ir.translation.normalize_finish_reason`, `ir_to_openai_finish`, `carry_extras`, `merge_extras` (Task 1).
- Produces: no new public names. Behaviour change is limited to (a) non-standard `finish_reason` values now map to their real IR reason instead of collapsing to `"stop"`, (b) an unmapped value is logged at debug instead of vanishing.

- [ ] **Step 1: Write the failing regression tests** — append to `tests/test_translation_enhancements.py`:

```python
# -- finish_reason fidelity on the OpenAI side (shared canonical map) ----------

def test_openai_adapter_maps_nonstandard_finish_reason():
    """A non-standard finish_reason must reach the IR as its real reason.

    Before: the inline dict defaulted every unknown value to "stop", so an
    Anthropic client could not tell a truncation from a natural end.
    """
    from wiwi.streaming import deltas as dl
    ad = OpenAIAdapter()
    out = []
    for ev in [
        json.dumps({"choices": [{"delta": {"content": "x"}}]}),
        json.dumps({"choices": [{"delta": {}, "finish_reason": "max_tokens"}]}),
    ]:
        out.extend(ad.decode_stream_event("", ev))
    finish = [d for d in out if isinstance(d, dl.Finish)]
    assert len(finish) == 1
    assert finish[0].stop_reason == "length"


def test_openai_adapter_maps_tool_use_spelling_to_tool_call():
    from wiwi.streaming import deltas as dl
    ad = OpenAIAdapter()
    out = ad.decode_stream_event(
        "", json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_use"}]}))
    finish = [d for d in out if isinstance(d, dl.Finish)]
    assert finish and finish[0].stop_reason == "tool_call"


def test_openai_adapter_unknown_finish_reason_still_terminates():
    """Totality: an unknown reason must still produce exactly one Finish."""
    from wiwi.streaming import deltas as dl
    ad = OpenAIAdapter()
    out = ad.decode_stream_event(
        "", json.dumps({"choices": [{"delta": {}, "finish_reason": "wat"}]}))
    finish = [d for d in out if isinstance(d, dl.Finish)]
    assert len(finish) == 1
    assert finish[0].stop_reason == "stop"


def test_chat_encoder_emits_openai_spelling_for_tool_call():
    from wiwi.streaming import deltas as dl
    enc = oc.ChatStreamEncoder("gpt-4o", "abc")
    frames = [enc.feed(d) for d in
              [dl.Finish("tool_call"), dl.StreamEnd()]]
    frames.append(enc.final_frame())
    blob = b"".join(f for f in frames if f).decode()
    assert "tool_calls" in blob
    assert '"tool_call"' not in blob  # the IR spelling must not leak out
```

- [ ] **Step 2: Run and confirm RED**

```bash
python3 -m pytest tests/test_translation_enhancements.py -q -k "finish_reason or openai_spelling"
```
Expected: the `max_tokens` and `tool_use` cases fail on `stop_reason == "stop"` (the current collapse); the encoder case may already pass — record which, do not assume.

- [ ] **Step 3: Change `wiwi/providers/openai_adapter.py`** — replace the inline dict with `ir.translation.normalize_finish_reason(fr)`. Keep the surrounding close-all-open-tool-calls logic and the `self._synthesized_opens.clear()` exactly as they are; this is a one-expression substitution plus the import. Then read `wiwi/providers/openai_adapter.py`'s **sync** `decode_response` for a second copy of the same dict and route it through the same helper.

- [ ] **Step 4: Change `wiwi/wire/openai_chat.py`** — route the `Finish`-emission spelling through `ir_to_openai_finish`, and construct `extras` with `carry_extras(body, _KNOWN_KEYS)` instead of the inline comprehension. Behaviour on the existing corpus must be identical (`carry_extras` **is** the denylist).

- [ ] **Step 5: Run and confirm GREEN**

```bash
python3 -m pytest tests/test_translation_enhancements.py tests/test_codecs.py -q
```
Expected: all pass, including the four new tests.

- [ ] **Step 6: Prove the other dialects did not move**

```bash
python3 -m pytest tests/test_integration.py tests/test_openai_responses.py -q
```
Expected: green. If `test_openai_responses.py` does not exist, use
`ls tests/ | grep -i respons` to find the right file — do not skip this step.

---

### Task 3: Anthropic Messages side — mapping, extras and reasoning replay

**Why:** `wiwi/wire/anthropic_messages.py` uses an **allowlist** (`_PASSTHROUGH_KEYS`) where the OpenAI codecs use a denylist, and `wiwi/providers/anthropic_adapter.py` owns `_STOP_REASON_IN`, the `thinking` block construction, and the redacted-thinking decode. This task makes the two stop-reason directions use Task 1's single map, makes a dropped `extras` key visible instead of silent, and closes the reasoning-replay gaps in the Spec (items 3, 4, 5).

**Files:**
- Modify: `wiwi/wire/anthropic_messages.py` (`_PASSTHROUGH_KEYS` usage at ~:446, `_STOP_REASON_OUT` at ~:461)
- Modify: `wiwi/providers/anthropic_adapter.py` (`_STOP_REASON_IN`, thinking block construction in `encode_request`, redacted-thinking handling in `decode_response`/`decode_stream_event`)
- Modify: `tests/test_translation_enhancements.py` (append regressions)
- Modify: `tests/test_codecs.py` (append codec round-trips)
- Modify: `UPDATE.md`

**Interfaces:**
- Consumes: `wiwi.ir.translation` (Task 1).
- Produces: no new public names. `_STOP_REASON_OUT` and `_STOP_REASON_IN` keep their names (both are module-private but referenced by tests) and become thin lookups built from the shared map.

- [ ] **Step 1: Write the failing regression tests** — append to `tests/test_translation_enhancements.py`:

```python
# -- Anthropic-side stop reason and reasoning fidelity -------------------------

def test_anthropic_stop_reason_out_covers_the_ir_vocabulary():
    """Every IR StopReason an OpenAI provider can produce must have an
    Anthropic spelling, so the client sees max_tokens vs end_turn."""
    for sr, expected in [("stop", "end_turn"), ("length", "max_tokens"),
                         ("tool_call", "tool_use"),
                         ("content_filter", "refusal"),
                         ("pause_turn", "pause_turn"),
                         ("stop_sequence", "stop_sequence")]:
        assert am._STOP_REASON_OUT[sr] == expected


def test_anthropic_adapter_roundtrips_a_max_tokens_stop():
    turn = ir.AssistantTurn(text="cut off", stop_reason="length")
    body = am.encode_response(ctx=None, turn=turn, model="claude", req_id="r")
    assert body["stop_reason"] == "max_tokens"


def test_redacted_thinking_survives_anthropic_decode_and_reencode():
    """A redacted thinking block must replay verbatim and never become text."""
    data = "EroBCkYIBxgCIkA..."
    resp = {"id": "m", "type": "message", "role": "assistant", "model": "claude",
            "stop_reason": "end_turn",
            "content": [{"type": "redacted_thinking", "data": data},
                        {"type": "text", "text": "answer"}],
            "usage": {"input_tokens": 1, "output_tokens": 2}}
    turn = AnthropicAdapter().decode_response(200, json.dumps(resp).encode())
    assert turn.thinking[0].block_type == "redacted_thinking"
    assert turn.thinking[0].data == data
    assert turn.thinking[0].text == ""
    out = am.encode_response(ctx=None, turn=turn, model="claude", req_id="r")
    blocks = {b["type"]: b for b in out["content"]}
    assert blocks["redacted_thinking"]["data"] == data


def test_redacted_thinking_is_not_replayed_as_openai_content():
    """Routing a redacted-thinking turn to an OpenAI provider must drop the
    block, not stringify its opaque payload into the conversation."""
    resp = {"id": "m", "type": "message", "role": "assistant", "model": "claude",
            "stop_reason": "end_turn",
            "content": [{"type": "redacted_thinking", "data": "OPAQUE"},
                        {"type": "text", "text": "answer"}],
            "usage": {"input_tokens": 1, "output_tokens": 2}}
    turn = AnthropicAdapter().decode_response(200, json.dumps(resp).encode())
    req = ir.Request(model="gpt-4o", messages=[
        ir.Message(role="assistant", parts=[
            ir.TextPart(text=turn.text), *turn.thinking])])
    body = OpenAIAdapter().encode_request(req, "gpt-4o", {})
    blob = json.dumps(body)
    assert "OPAQUE" not in blob


def test_anthropic_client_thinking_budget_reaches_openai_provider():
    req = am.decode_request({
        "model": "o3",
        "messages": [{"role": "user", "content": "think"}],
        "thinking": {"type": "enabled", "budget_tokens": 32000},
        "max_tokens": 100000,
    })
    body = OpenAIAdapter().encode_request(req, "o3", {})
    assert body["reasoning_effort"] == "high"


def test_anthropic_thinking_disabled_reaches_openai_provider_as_none():
    req = am.decode_request({
        "model": "o3",
        "messages": [{"role": "user", "content": "quick"}],
        "thinking": {"type": "disabled"},
        "max_tokens": 100,
    })
    body = OpenAIAdapter().encode_request(req, "o3", {})
    assert body.get("reasoning_effort") in (None, "none")
```

- [ ] **Step 2: Run and confirm RED**

```bash
python3 -m pytest tests/test_translation_enhancements.py -q -k "anthropic and (stop_reason or redacted or budget or disabled)"
```
Expected: at minimum `test_redacted_thinking_is_not_replayed_as_openai_content` fails (the payload currently reaches the OpenAI body as text). Record the exact failure of each test before changing code — several may already pass.

- [ ] **Step 3: Change `wiwi/wire/anthropic_messages.py`** — build `_STOP_REASON_OUT` from `wiwi.ir.translation` so there is one map, keeping the module-level name. Add an explicit debug log when a client param is present in the body but absent from `_PASSTHROUGH_KEYS`, listing the dropped key names — the allowlist stays an allowlist (it is deliberate: these are the params the Messages API accepts), but the drop becomes observable.

- [ ] **Step 4: Change `wiwi/providers/anthropic_adapter.py`** — build `_STOP_REASON_IN` from the shared map; confirm the redacted-thinking path emits `ThinkingPart(text="", block_type="redacted_thinking", data=…)` on **both** the sync (`decode_response`, ~:600) and streaming (`decode_stream_event`) paths, and that `encode_request` replays it verbatim inside the `thinking` array rather than under `content`.

- [ ] **Step 5: Change `wiwi/providers/openai_adapter.py`'s assistant-turn rendering** so a `ThinkingPart` with `block_type == "redacted_thinking"` is **dropped** rather than rendered as a text/reasoning fragment. Read `_role_parts_to_content()` first and place the guard where `ThinkingPart` is already handled — do not add a second code path for parts.

- [ ] **Step 6: Run and confirm GREEN**

```bash
python3 -m pytest tests/test_translation_enhancements.py tests/test_codecs.py -q
```

- [ ] **Step 7: Prove the native Anthropic path is unchanged**

```bash
python3 -m pytest tests/test_anthropic_surface.py tests/test_integration.py -q
```
Expected: green. If `tests/test_anthropic_surface.py` does not exist, `ls tests/ | grep -i anthropic` first and run whatever is there.

---

### Task 4: Adapter-owned probe validation (HealthHealer) + `AUDIT.md`

**Why:** This is the confirmed 🔴. `probe_verdict()` in `wiwi/core/recovery.py` knows exactly one 200-error-envelope shape (WorkBuddy's `{"code": N, "msg": …}`) and calls everything else `HEALTHY`. An Anthropic `{"type":"error","error":{"type":"overloaded_error"}}` on a 200 makes the healer restore a dead key **and** resume a cooled deployment. The fix must not hardcode a second shape in `core/` — that is the layering violation the current code already skirts. Instead the adapter that owns the dialect decides.

**Files:**
- Modify: `wiwi/providers/base.py` — add an **optional** protocol method
- Implement: `probe_error` (name fixed) on `wiwi/providers/anthropic_adapter.py` and on the OpenAI-wire family (`wiwi/providers/openai_adapter.py`, inherited by `openai-compatible`/`gmicloud`/`bai` through `registry.get_adapter`'s `_OPENAI_WIRE_TYPES` fallback)
- Modify: `wiwi/core/recovery.py` — `probe_verdict` consults the adapter hook via a `Callable` passed in, **not** via a provider-type branch
- Modify: `AUDIT.md` — add the finding, then mark it fixed in place
- Modify: `tests/test_recovery.py` (probe classification) and the next `tests/test_fix_roundN.py` (round 91, re-checked)

**Interfaces:**
- `ProviderAdapter.probe_error` — an **optional** protocol member: `probe_error(self, status: int, body: bytes | str | None) -> str | None`. Returns a short reason string when the body *is* an error envelope that the HTTP status does not express, else `None`. Optional so the eleven existing adapters are not all forced to implement it; `recovery.py` reaches it with `getattr(adapter, "probe_error", None)`.
- `probe_verdict(status, body=None, error_probe: Callable[[int, bytes | str | None], str | None] | None = None) -> ProbeVerdict` — the third parameter is new and **optional**, so `probe_verdict(200) is ProbeVerdict.HEALTHY` (asserted by `tests/test_recovery.py::TestProbeVerdict.test_table`) keeps working unchanged. When `error_probe` is provided and `status == 200` and it returns a truthy reason, the verdict is `UNREACHABLE` (the existing class for "we cannot use this target"), and `HealthHealer._probe` surfaces the reason as `detail`.
- `HealthHealer._probe` builds the callable from `fresh_adapter(dep.provider.provider_type)`, which it already constructs — no new construction site, no new import in `recovery.py`.

- [ ] **Step 1: Report the defect in `AUDIT.md` FIRST** — add an entry at the top of the live section, using the existing format exactly: a severity badge (🔴), a short title, `wiwi/core/recovery.py:126-135` (the `probe_verdict` 200 branch) and `wiwi/core/recovery.py:139-181` (`_body_is_error_envelope`, WorkBuddy-only), the trigger (a cooling Anthropic key whose 200 probe body is `{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}` is classified `HEALTHY` and restored into rotation with its deployment's cooldown cleared), and the one-line fix sketch (adapter-owned `probe_error` consulted by `probe_verdict`). Search `AUDIT.md` for the same symptom first — if an equivalent entry already exists, extend it rather than adding a duplicate.
  > Note for the implementer: `AUDIT.md`'s `Status: fixed` markers are known to lag the code (see project memory). Do not trust a marker; verify against source. This particular finding was verified by direct source read and is **live**.

- [ ] **Step 2: Write the failing tests** — append to `tests/test_recovery.py`:

```python
class TestProbeEnvelopeAcrossProviders:
    """A 200 whose body is a provider-shaped error must not read HEALTHY.

    Regression: only WorkBuddy's `{"code": N, "msg": …}` envelope was
    recognized, so an Anthropic `overloaded_error` on a 200 had the healer
    restore a dead key and clear its deployment's cooldown (AUDIT #<entry>).
    """

    def test_bare_200_still_healthy(self):
        # Preserves the documented no-body contract.
        assert probe_verdict(200) is ProbeVerdict.HEALTHY

    def test_anthropic_200_overload_is_not_healthy(self):
        body = (b'{"type":"error","error":{"type":"overloaded_error",'
                b'"message":"Overloaded"}}')
        assert probe_verdict(
            200, body, error_probe=AnthropicAdapter().probe_error
        ) is ProbeVerdict.UNREACHABLE

    def test_anthropic_200_real_completion_is_healthy(self):
        body = (b'{"id":"msg_1","type":"message","role":"assistant",'
                b'"content":[{"type":"text","text":"pong"}],'
                b'"stop_reason":"end_turn","usage":{"input_tokens":1,'
                b'"output_tokens":1}}')
        assert probe_verdict(
            200, body, error_probe=AnthropicAdapter().probe_error
        ) is ProbeVerdict.HEALTHY

    def test_openai_200_error_envelope_is_not_healthy(self):
        body = b'{"error":{"message":"insufficient_quota","type":"server_error"}}'
        assert probe_verdict(
            200, body, error_probe=OpenAIAdapter().probe_error
        ) is ProbeVerdict.UNREACHABLE

    def test_openai_200_real_completion_is_healthy(self):
        body = json.dumps(PROBE_OK_BODY).encode()
        assert probe_verdict(
            200, body, error_probe=OpenAIAdapter().probe_error
        ) is ProbeVerdict.HEALTHY

    def test_workbuddy_envelope_still_detected_without_a_hook(self):
        body = b'{"code": 4001, "msg": "session expired"}'
        assert probe_verdict(200, body) is ProbeVerdict.UNREACHABLE

    def test_hook_is_optional(self):
        # Any adapter lacking probe_error must not raise.
        assert probe_verdict(200, b'{"choices":[]}') is ProbeVerdict.HEALTHY

    def test_hook_that_raises_does_not_cool_anything(self):
        def boom(status, body):
            raise RuntimeError("adapter bug")
        # A broken validator must degrade to the old behaviour, not crash the
        # healer sweep.
        assert probe_verdict(200, b"{}", error_probe=boom) is ProbeVerdict.HEALTHY
```

- [ ] **Step 3: Run and confirm RED**

```bash
python3 -m pytest tests/test_recovery.py -q -k "Envelope"
```
Expected: `TypeError: probe_verdict() got an unexpected keyword argument 'error_probe'` for most cases — that is the RED. Confirm `test_bare_200_still_healthy` passes **before** the change too, so it is a genuine invariant and not a coincidence.

- [ ] **Step 4: Add the hook to `wiwi/providers/base.py`** — extend the `ProviderAdapter` Protocol with the optional `probe_error` member and document that it is optional and reached via `getattr`.

- [ ] **Step 5: Implement `probe_error` on the two adapters** — `AnthropicAdapter.probe_error` returns a reason for a JSON body whose `type == "error"` with a non-empty `error.type`/`error.message`; `OpenAIAdapter.probe_error` returns a reason for a JSON body carrying a top-level `error` dict. Both must be **total**: an unparseable / non-dict / missing-field body returns `None`, never raises. Both must also inspect SSE `data:` frames the same way `_body_is_error_envelope` does, since `force_stream` providers return 200 + SSE. Note: `AnthropicAdapter` is **not** used by `force_stream` today, but the OpenAI-wire family includes one, so the OpenAI implementation needs the SSE path.

- [ ] **Step 6: Change `wiwi/core/recovery.py`** — add the optional `error_probe` parameter to `probe_verdict`, keep `_body_is_error_envelope` as the fallback (WorkBuddy still has no adapter hook and must keep working), and have `_probe` pass `getattr(adapter, "probe_error", None)`. Confirmed by source read: `core/recovery.py` may import `wiwi.providers.base` and `wiwi.providers.registry` (the documented carve-out) — **do not** import a concrete adapter module, and do not add a `provider_type == "anthropic"` branch.

- [ ] **Step 7: Run and confirm GREEN**

```bash
python3 -m pytest tests/test_recovery.py -q
```
Expected: green, including the pre-existing `TestProbeVerdict.test_table`.

- [ ] **Step 8: Prove the healer end-to-end still restores a genuinely healthy target** — the existing healer tests in `tests/test_recovery.py` mock `PROBE_OK_BODY` (OpenAI-shaped) against an OpenAI provider. Add one healer-level test in the next `tests/test_fix_roundN.py` that points the healer at an **Anthropic** provider returning a 200 error envelope and asserts the key stays cooling and the deployment stays cooled:

```bash
ls tests/test_fix_round*.py | sort -V | tail -3   # re-check the next number NOW
```

  > The `ls` above is not decorative: peer sessions share this tree and a round number can be claimed between reading it and writing the file. Write to whatever the listing shows as next.

- [ ] **Step 9: Mark the `AUDIT.md` entry fixed in place** — add `**Status: fixed**` with a description of the change and the name of the regression test added in Step 8. Never delete the finding.

---

### Task 5: End-to-end coverage for the reverse direction

**Why:** `tests/test_integration.py` proves Anthropic-client → OpenAI-provider. The reverse — an OpenAI Chat client, backed by an Anthropic provider, streaming — has no integration test, so nothing would catch a regression that breaks it. This is the test that proves the batch's headline claim.

**Files:**
- Modify: `tests/test_integration.py`

**Interfaces:**
- Consumes: the existing `_config()` factory and `client` fixture in that file, plus `respx` in decorator form. Read the file first — its `_config()` is built for an OpenAI provider; a second factory for an Anthropic deployment is expected and should follow the same shape rather than being bolted onto the existing one.

- [ ] **Step 1: Write the failing integration tests** — append to `tests/test_integration.py`, mirroring the existing `test_anthropic_surface_to_openai_backend` structure:

```python
@pytest.mark.parametrize("stream", [False, True])
@respx.mock
async def test_openai_surface_to_anthropic_backend(stream):
    """An OpenAI Chat client, routed to an Anthropic provider, must get
    OpenAI-shaped output — including streaming tool calls."""
    ...
```

  The non-streaming case asserts: `choices[0].message.content`, `finish_reason` mapped from `end_turn` → `stop`, `usage.prompt_tokens`/`completion_tokens` from `input_tokens`/`output_tokens`, and `usage.completion_tokens_details.reasoning_tokens` from `output_tokens_details.thinking_tokens`. The streaming case asserts the emitted SSE is OpenAI `chat.completion.chunk` frames carrying `reasoning_content` for the thinking block and a `tool_calls` delta for a `tool_use` block, terminated by `finish_reason` and `[DONE]`.

  Also add the **error** case: the Anthropic provider returns 429 with an Anthropic-shaped body, and the OpenAI client must receive an **OpenAI-shaped** error (`{"error": {...}}`) with the status preserved — proving `error_body` selection is driven by the inbound surface, not the outbound provider.

- [ ] **Step 2: Run and confirm RED**

```bash
python3 -m pytest tests/test_integration.py -q -k anthropic_backend
```
Expected: the new tests fail on the fixture/config (no Anthropic deployment) before they fail on behaviour. Record the first real failure.

- [ ] **Step 3: Fix whatever the test exposes** — if the failure is genuine translation loss (not fixture setup), fix it in the owning module: dialect loss in `wiwi/wire/openai_chat.py`, provider loss in `wiwi/providers/anthropic_adapter.py`. **Do not** add branching in `wiwi/core/gateway.py` or `wiwi/server/app.py` to make the test pass; if the fix seems to need that, stop and report it — it means the seam is in the wrong place.

- [ ] **Step 4: Run and confirm GREEN**

```bash
python3 -m pytest tests/test_integration.py -q
```

- [ ] **Step 5: Property-based round-trip invariant** — the true invariant is *not* "adapter A's encode then adapter B's decode is the identity" (the two providers carry different vocabularies, so it cannot be). It is this: for a generated `ir.AssistantTurn`, encoding it in the **OpenAI** dialect (`openai_adapter.decode_response` of the body the OpenAI encoder would send) and separately in the **Anthropic** dialect yields two turns whose `text` and `stop_reason` agree, and whose `tool_calls` agree on `id` and `name`. Generate small turns with `hypothesis` (`text` from a small alphabet to keep the database useful, 0–2 tool calls with ids from a small pool, `stop_reason` from the IR vocabulary). Assert on the **IR**, never on byte equality — `orjson` key order and the providers' extra fields make byte comparison meaningless. This test catches the class of bug where one dialect silently loses a field the other preserves.

---

### Task 6: `UPDATE.md` — the binding changelog for the translation layer

**Why:** `UPDATE.md` is binding: any agent touching `wiwi/wire/{openai_chat,openai_responses,anthropic_messages}.py` or `wiwi/providers/{openai,anthropic,openrouter}_adapter.py` must read it first and record what changed, or a later agent rediscovers and re-fixes the same thing.

**Files:**
- Modify: `UPDATE.md`

**Interfaces:** none — this is the record.

- [ ] **Step 1: Add one round heading** in the file's existing style. The latest heading is `## Round 89 — … — 2026-09-19`; use the next round number **and re-check it against the file immediately before writing** (`grep -n "^## Round" UPDATE.md | tail -5`) — rounds 90+ may already exist from a peer session.

- [ ] **Step 2: One entry per fix**, in the file's established format: `### N.N Title` / `**File**:` / `**Before**:` (a real code snippet of the old behaviour) / `**After**:` (the new snippet) / `**Tests**:` (the test names added). Cover, at minimum: the `finish_reason` collapse, the redacted-thinking drop on the OpenAI route, and the extras-drop visibility. The probe fix belongs in `UPDATE.md` **only if** it changed one of the listed files — it changes `providers/base.py`, `providers/anthropic_adapter.py` and `providers/openai_adapter.py`, so it does.

- [ ] **Step 3: Cross-check both docs**

```bash
grep -n "^## Round" UPDATE.md | tail -3
grep -n "Status: fixed" AUDIT.md | head -5
```
Confirm the new round is present exactly once and the `AUDIT.md` entry from Task 4 carries its fixed marker.

---

### Task 7: Verification, review, and the gate

**Why:** The repository's pre-completion gate is binding: a completion claim without fresh command output in the same message is a false claim.

**Files:** none modified.

- [ ] **Step 1: Full gate, fresh output**

```bash
python3 -m pytest tests/ -q
ruff check wiwi/ tests/
```
Both must be green. Do **not** report a pinned pass-count from memory — report what this run printed.

- [ ] **Step 2: Confirm the layering invariants by command, not by belief**

```bash
grep -rn "wiwi.wire" wiwi/ir/ wiwi/core/ wiwi/router/ wiwi/streaming/ wiwi/providers/ | grep -v "wiwi/providers/__init__"
grep -rn "from wiwi.providers\.\|import wiwi.providers\." wiwi/core/recovery.py
```
The first must show no `wiwi.ir/`/`core/`/`router/`/`streaming/` matches; the second must show only `wiwi.providers.base` and `wiwi.providers.registry`.

- [ ] **Step 3: Confirm no commit-path or secret file was touched**

```bash
git status --porcelain
git diff --stat
```
Expected: only the intended source/test/doc files. No `wiwi.yaml`, `wiwi.db`, `.env`, `key.md`, `opencode.json(c)`, `.verify/`, `.wiwi/`, `*.har`. Leave the tree **dirty** — do not commit, do not push, do not branch.

- [ ] **Step 4: Run the code-review skill** — `superpowers:requesting-code-review` (and the ECC reviewers for the touched stack: `ecc:python-reviewer`, plus `/ecc:security-review` if any credential-handling line moved — `probe_error` reads error bodies, so run it). Act on the findings or decline each with reasoning; a self-review of the diff is **not** a substitute.

- [ ] **Step 5: Run `superpowers:verification-before-completion`** and report with the fresh Step 1 output in the same message.

- [ ] **Step 6: Report, then stop.** State which tasks are done, which are not, the exact gate output, and any finding declined with reasoning. **Do not commit or push** — that requires a direct instruction in the turn it happens, and approval for one commit never extends to the next.

---

## Self-review

**Spec coverage.** Defect 1 (probe misclassification) → Task 4. Defect 2 (no adapter probe hook) → Task 4 Steps 4–5. Defect 3 (extras asymmetry) → Tasks 2 and 3. Defect 4 (stop-reason loss) → Tasks 1, 2, 3. Defect 5 (thinking/redacted fidelity) → Task 3. Defect 6 (`cache_hit` invariant) → stated as an invariant in the Spec; no task changes it, which is the correct treatment. Defect 7 (no reverse integration test) → Task 5. `UPDATE.md` → Task 6. `AUDIT.md` → Task 4 Steps 1 and 9. Gate and layering proof → Task 7.

**Placeholder scan.** No `TODO`, no `...` standing in for a decision, no "handle errors appropriately". Task 5 Step 1 is deliberately described in prose rather than written out, because the correct assertions depend on what the existing `_config()` factory in `tests/test_integration.py` supports — the shape is specified, the code is not guessed.

**Type consistency.** `normalize_finish_reason(raw: Any) -> ir.StopReason` and `ir_to_openai_finish(stop_reason: str) -> str` are used by name in Tasks 2 and 3. `carry_extras(source, known)` and `merge_extras(*layers)` are used by name in Task 2. `probe_error(self, status, body) -> str | None` is used by name in Task 4 Steps 2, 4, 5 and 6, and the `error_probe` keyword used in the Task 4 tests matches the `probe_verdict` signature in the Interfaces block. `PROBE_OK_BODY` referenced in Task 4 Step 2 already exists at `tests/test_recovery.py:261`.

**Scope discipline.** No new route, no new `PROVIDER_TYPES` entry, no `web/` change, no Kaggle/dataset work, no true Responses support, no commit steps, no branch, no worktree.
