#!/usr/bin/env bash
# Deploy the wiwi gateway to the HuggingFace Docker Space `shimen/yapapa`.
#
#   ./deploy/hf_space.sh              # publish committed HEAD to the Space
#   ./deploy/hf_space.sh --dry-run    # list exactly what would be uploaded
#
# The Space is a deploy target, not a source of truth: this exports the repo's
# committed tree into a scratch clone of the Space and pushes one commit. The
# gateway's own git history is untouched, and nothing is ever force-pushed.
#
# Auth: HF_TOKEN from the environment, else from the repo's gitignored .env.
# The token is handed to git through a transient credential helper, so it never
# lands in the scratch clone's .git/config or in this process's argv.
#
# Why `git archive` and not `hf upload`: this Space is PUBLIC, and `hf upload`
# walks the working tree directly — it would ship .env, wiwi.yaml and wiwi.db
# if they happen to sit in the checkout. `git archive` exports tracked files
# only, so anything gitignored is structurally unable to be uploaded.
set -euo pipefail

SPACE_ID="${SPACE_ID:-shimen/yapapa}"
BRANCH="${BRANCH:-main}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DRY_RUN=0
case "${1:-}" in
  --dry-run|-n) DRY_RUN=1 ;;
  "") ;;
  *) echo "usage: $0 [--dry-run]" >&2; exit 2 ;;
esac

# --- token -------------------------------------------------------------------
if [[ -z "${HF_TOKEN:-}" && -f "$ROOT/.env" ]]; then
  # Last assignment wins, matching dotenv semantics; comments are skipped.
  HF_TOKEN="$(sed -n 's/^[[:space:]]*HF_TOKEN[[:space:]]*=[[:space:]]*//p' "$ROOT/.env" | tail -1)"
fi
if [[ -z "${HF_TOKEN:-}" ]]; then
  echo "error: HF_TOKEN is not set and not found in $ROOT/.env" >&2
  echo "       create a write token at https://huggingface.co/settings/tokens" >&2
  exit 1
fi

# The helper is expanded by git's shell, so the value stays out of argv/config.
# The leading `credential.helper=` resets the helper list: without it the global
# `credential.helper store` (and any other configured helper) is consulted
# first, and a stale stored token for huggingface.co shadows the one we pass.
# Helpers are asked in order until a username+password pair is complete.
GIT_AUTH=(
  -c "credential.helper="
  -c "credential.helper=!f() { echo username=${SPACE_ID%%/*}; echo password=\$HF_TOKEN; }; f"
)

REV="$(git -C "$ROOT" rev-parse --short HEAD)"
echo "==> Space: https://huggingface.co/spaces/$SPACE_ID (branch $BRANCH)"
echo "==> source revision: $REV"

if [[ -n "$(git -C "$ROOT" status --porcelain)" ]]; then
  echo "    note: working tree is dirty — the Space gets committed HEAD ($REV), not local edits"
fi

# --- payload -----------------------------------------------------------------
# Tracked files at HEAD. Anything gitignored (.env, wiwi.yaml, wiwi.db,
# opencode.json, key.md, .verify/, .wiwi/, *.har) is absent by construction.
echo "==> exporting $REV"
if [[ $DRY_RUN -eq 1 ]]; then
  echo "==> files that would be uploaded to the Space:"
  git -C "$ROOT" archive --format=tar HEAD | tar -tf - | sed 's/^/    /'
  echo "==> dry run: nothing pushed"
  exit 0
fi

# --- scratch clone -----------------------------------------------------------
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "==> cloning Space into $WORK"
# Skip LFS smudge: the Space holds no LFS objects we need locally, and a smudge
# would try to authenticate a download before the push helper is in play.
env HF_TOKEN="$HF_TOKEN" GIT_LFS_SKIP_SMUDGE=1 git "${GIT_AUTH[@]}" clone --depth 1 --branch "$BRANCH" \
  "https://huggingface.co/spaces/$SPACE_ID" "$WORK/space" >/dev/null 2>&1 || {
    echo "error: could not clone the Space. Check HF_TOKEN (needs repo.write on '${SPACE_ID%%/*}')." >&2
    exit 1
  }

# Clear the previous deploy so removals propagate. `.git` is the clone's own,
# and `.gitattributes` carries HF's LFS rules — both must survive.
shopt -s dotglob nullglob
for entry in "$WORK/space"/*; do
  base="$(basename "$entry")"
  [[ "$base" == ".git" || "$base" == ".gitattributes" ]] && continue
  rm -rf "$entry"
done
shopt -u dotglob nullglob

echo "==> unpacking into the Space tree"
git -C "$ROOT" archive --format=tar HEAD | tar -x -C "$WORK/space"

# The Space's README.md is its manifest (sdk: docker, app_port: 4000); the
# repo's README is the project's front page. They are different documents, so
# the manifest is written last and wins.
cp "$ROOT/deploy/hf-space/README.md" "$WORK/space/README.md"

# --- commit + push -----------------------------------------------------------
cd "$WORK/space"

# HF rejects a push containing binaries that are not in LFS/xet ("Your push was
# rejected because it contains binary files ... use xet to store binary files").
# The tracked tree carries the UI's PNG logos, so they must go through LFS. The
# Space's own .gitattributes covers model weights (*.bin, *.safetensors, ...) but
# not *.png, hence the explicit track. git-lfs uploads the objects to HF's LFS
# endpoint during push, authenticating through the same helper.
git lfs install --local >/dev/null
git lfs track "*.png" >/dev/null

MSG="Deploy wiwi $REV"

git add -A
if git diff --cached --quiet; then
  echo "==> Space already matches $REV — nothing to push"
  exit 0
fi

git -c user.name="wiwi deploy" -c user.email="deploy@wiwi.invalid" \
  commit -q -m "$MSG"

echo "==> pushing $MSG"
env HF_TOKEN="$HF_TOKEN" git "${GIT_AUTH[@]}" push origin "HEAD:$BRANCH"

echo "==> done: https://huggingface.co/spaces/$SPACE_ID"
echo "    build logs: https://huggingface.co/spaces/$SPACE_ID/logs"
