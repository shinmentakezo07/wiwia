# wiwi Bug-Fix Workflow

When fixing bugs in the wiwi codebase, follow this workflow:

## Skills to apply
- Load `.claude/skills/systematic-debugging/SKILL.md` before diagnosing any bug: understand root cause before patching symptoms.
- Follow TDD per `.claude/skills/test-driven-development/SKILL.md`: write the failing regression test first, then fix.
- After each fix batch, self-review with the checklist in `.claude/skills/requesting-code-review/SKILL.md`.

## Project rules
Binding project rules (arch layering, ruff, async, streaming contract, test conventions) live canonically in `CLAUDE.md` — do not restate them here. Key operational reminders:

- Use the ambient interpreter for tests and lint: `python3 -m pytest tests/ -q` and `ruff check wiwi/ tests/`. **Never** `.venv/bin/python` — the `.venv` symlink has no site-packages (see CLAUDE.md).
- New bugfix tests go into thematic regression files (`test_fix_roundN.py`) rather than topic files — find the next unused N with `ls tests/test_fix_round*.py`, never assume one.
- Verify before done: full pytest + ruff, both green, before claiming work finished or committing.

## Known bug list
The 2026-08-25 review list (H1 budget race, H4 plaintext keys, H5–H8 streaming hardening, H2/H3 locking, H10 Responses parallel tool calls) is **fully fixed** as of 2026-08-31 — verify against `tests/` if in doubt, and do not re-fix. Before starting a new bugfix, read `AUDIT.md` for currently open issues.
