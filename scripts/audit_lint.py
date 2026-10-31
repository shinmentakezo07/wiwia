"""Validate the AUDIT register so its status markers can be trusted.

Run in CI (`.github/workflows/ci.yml`) and locally before adding an entry:

    python3 scripts/audit_lint.py

Why this exists (AUDIT #340): the register had drifted in both directions —
entries marked open that were fixed, and entries fixed with no marker at all —
so "open" could not be trusted and every sweep re-investigated closed work.
The archive split (`docs/audit-archive/fixed.md`) removed the closed rounds;
this check keeps the live file honest.

Rules
-----
1. Every numbered ``### <n>.`` entry carries its own ``Status:`` line, **or**
   sits under a section that declares one of:
   - a ``✅ Fixed`` header (the archive, and any closed section kept in place);
   - a ``**Section status:**`` line anywhere in the section outside an entry.
2. ``AUDIT.md`` links the archive, and the archive exists.
3. Headings that are not numbered entries (``### Verification method``,
   ``### Cleared seams …``) are sub-headings of the entry above them and are
   not checked.

Entry numbers are **not** required to be unique: the register reused numbers
across sweeps before #340, and renumbering 600 historical entries would rewrite
citations the code comments rely on. New entries should take the next unused
number, but that is a convention, not a check.

Exit code 0 = clean, 1 = violations printed to stderr.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE = ROOT / "AUDIT.md"
ARCHIVE = ROOT / "docs" / "audit-archive" / "fixed.md"
ENTRY_NUM = re.compile(r"^###\s+(\d+)\.")


class Section:
    def __init__(self, header: str, line: int) -> None:
        self.header = header
        self.line = line
        self.loose: list[str] = []  # section-level lines outside any entry
        self.entries: list[tuple[int, str, list[str]]] = []  # line, header, body

    @property
    def closed(self) -> bool:
        if "✅ Fixed" in self.header:
            return True
        return any("Section status:" in line for line in self.loose)


def parse(path: Path) -> list[Section]:
    sections: list[Section] = []
    cur: Section | None = None
    entry: tuple[int, str, list[str]] | None = None
    for i, raw in enumerate(path.read_text().splitlines(), 1):
        if raw.startswith("## "):
            if entry is not None and cur is not None:
                cur.entries.append(entry)
                entry = None
            cur = Section(raw, i)
            sections.append(cur)
        elif raw.startswith("### "):
            if entry is not None and cur is not None:
                cur.entries.append(entry)
            entry = (i, raw, [])
        elif entry is not None:
            entry[2].append(raw)
        elif cur is not None:
            cur.loose.append(raw)
    if entry is not None and cur is not None:
        cur.entries.append(entry)
    return sections


def main() -> int:
    problems: list[str] = []
    checked = 0

    for path in (LIVE, ARCHIVE):
        if not path.exists():
            problems.append(f"{path.relative_to(ROOT)}: missing")
            continue
        for sec in parse(path):
            for line, header, body in sec.entries:
                if not ENTRY_NUM.match(header):
                    continue  # a sub-heading of the entry above, not an entry
                checked += 1
                if not sec.closed and "Status:" not in "\n".join(body):
                    problems.append(
                        f"{path.name}:{line}: {header.strip()} has no `**Status:**` "
                        f"line and its section ({sec.header.lstrip('# ').strip()!r}) "
                        f"declares no `**Section status:**`"
                    )

    if LIVE.exists() and "docs/audit-archive/fixed.md" not in LIVE.read_text():
        problems.append("AUDIT.md: does not link docs/audit-archive/fixed.md")

    if problems:
        print(f"audit lint: {len(problems)} problem(s)", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print(
            "\nEvery numbered entry needs its own `**Status:**` line, or a "
            "section-level `**Section status:**`. See AUDIT #340.",
            file=sys.stderr,
        )
        return 1

    print(f"audit lint: ok ({checked} numbered entries across 2 files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
