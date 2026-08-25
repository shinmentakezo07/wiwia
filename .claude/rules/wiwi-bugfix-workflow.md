# wiwi Bug-Fix Workflow

When fixing bugs in the wiwi codebase, follow this workflow:

## Skills to apply
- Load `.claude/skills/systematic-debugging/SKILL.md` before diagnosing any bug: understand root cause before patching symptoms.
- Follow TDD per `.claude/skills/test-driven-development/SKILL.md`: write the failing regression test first, then fix.
- After each fix batch, self-review with the checklist in `.claude/skills/requesting-code-review/SKILL.md`.

## Project rules (from CLAUDE.md — binding)
- Never add dialect/provider-specific branches in `core/`, `router/`, `auth/`. Dialect logic in `wire/`, provider logic in `providers/`.
- Pydantic v2 for config; plain dataclasses for IR / streaming hot paths.
- Async throughout; orjson in hot paths; never print from library code (structlog).
- Ruff only: line-length 100, target py311.
- Tests: bare `async def test_...` (asyncio_mode=auto), no decorators. New bugfix tests go into thematic regression files (`test_fix_roundN.py` — next is `test_fix_round6.py`) rather than topic files.
- Verify before done: run `.venv/bin/python -m pytest tests/ -q` AND `.venv/bin/ruff check wiwi/ tests/`.
- Streaming contract (`wiwi/streaming/deltas.py`): exactly one StreamStart, ToolCallOpen→ArgsDelta*→Close nested per index, exactly one UsageFinal after last content delta, then Finish, then StreamEnd xor StreamError. Adapters guarantee legality.

## Known bug list to fix (from 2026-08-25 Claude Code review)
Fix in this order, one commit per logical fix:

1. **H1+M4+M7 budget race** — `auth/service.py`: atomic conditional UPDATE for spend_to_date; no 60s cache for keys with max_budget; log/flag unpriced models instead of silent $0.
2. **H4 plaintext keys** — `server/app.py:_key_view` (~line 840): remove `"secret": k.secret` from list responses.
3. **H5-H8 streaming hardening** — cap tool-arg size (~1 MiB) in validation.py + partial_json.py buffer; stop logging raw args (log length+fingerprint); cap repair stack depth.
4. **H2+H3 locking** — router on_result under _rr_lock; memory rate limiter check/record_tokens under asyncio.Lock.
5. **H10 Responses parallel tool calls** — wire/openai_responses.py: per-index dict for item state, not single `_tool_n`.
