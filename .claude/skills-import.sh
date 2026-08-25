#!/bin/bash
# Sync relevant Hermes skills into .claude/skills/ for Claude Code
set -e
SRC=/teamspace/studios/this_studio/.hermes/skills
DST=.claude/skills
mkdir -p "$DST"
for s in software-development/systematic-debugging software-development/test-driven-development software-development/requesting-code-review autonomous-ai-agents/claude-code; do
  name=$(basename "$s")
  rm -rf "$DST/$name"
  cp -r "$SRC/$s" "$DST/$name"
  echo "installed: $name"
done
