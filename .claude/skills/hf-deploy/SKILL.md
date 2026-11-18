---
name: hf-deploy
description: Runbook for deploying this gateway to the HuggingFace Docker Space shimen/yapapa — the deploy script, the git-lfs prerequisite, the secret-free export rule, and the Space's runtime configuration. Read before changing the deploy path or shipping.
---

# Deployment — HuggingFace Space (`shimen/yapapa`)

[`shimen/yapapa`](https://huggingface.co/spaces/shimen/yapapa) — live at <https://shimen-yapapa.hf.space> — is a Docker Space that runs this gateway. It is a **deploy target, not a source of truth**: never edit it by hand, it is built from this repo.

```bash
./deploy/hf_space.sh --dry-run    # list the exact file set that would ship
./deploy/hf_space.sh              # export committed HEAD into the Space and push
```

`git archive HEAD` exports the committed tree into a scratch clone of the Space, `deploy/hf-space/README.md` is written over the Space's `README.md` (the Space manifest — `sdk: docker`, `app_port: 4000`), and a single `Deploy wiwi <sha>` commit is pushed. Nothing is force-pushed and the gateway's own git history is untouched.

**`git-lfs` is a hard prerequisite of the deploy.** The tracked tree carries PNGs, HF rejects a push containing binaries that are not in LFS/xet, and the Space's own `.gitattributes` covers only model weights — so the script tracks `*.png` and pushes those objects through LFS. Without git-lfs the failure lands *after* the clone and the commit, so the Space is left untouched and the error reads as a bare `git: 'lfs' is not a git command`. The script now preflights it and prints install instructions (including the no-root path: drop the official binary in `~/.local/bin`, which is already on the default PATH on this box).

Three things to know before touching the deploy path:

- **The Space is public, so the export must stay secret-free.** `git archive` exports *tracked* files only — that is exactly why the script uses it instead of `hf upload`, which walks the working tree and would ship `.env`, `wiwi.yaml` and `wiwi.db` whenever they sit in the checkout. Do not "simplify" it back to a working-tree copy.
- **The repo README and the Space README are different documents.** The repo root `README.md` is the project front page; the Space's `README.md` is its YAML manifest. GitHub renders YAML front matter as a nested table, so the manifest cannot live at the repo root — it lives in `deploy/hf-space/README.md` and is copied in at deploy time.
- **Only committed work deploys.** Local edits are silently left behind (the script warns). Commit first, then deploy.

Auth is `HF_TOKEN` — a write token with `repo.write` on `shimen` — taken from the environment, else from the gitignored `.env`. It reaches git through a transient credential helper, so it never lands in the scratch clone's `.git/config` or in argv.

Runtime configuration lives in the Space's **Settings → Variables and secrets**, injected as environment variables — which is precisely how `wiwi.yaml.example` resolves provider keys (`os.environ/NAME`). `WIWI_MASTER_KEY` is **required and already set**: the gateway fails closed without a session secret, so a Space whose secret is missing builds fine and then dies at `RUNTIME_ERROR`. `DATABASE_URL` is pre-set to SQLite under `/data`, the only writable path in a Space container and ephemeral across rebuilds; durable state means pointing it at an external Postgres. Both are live at <https://shimen-yapapa.hf.space>; the master key is mirrored into the gitignored `.env` as `WIWI_MASTER_KEY_SPACE`.

One deploy-path detail worth keeping: HF rejects a push containing binaries that are not in LFS, and the tracked UI carries PNG logos, so the script runs `git lfs track "*.png"` in the scratch clone. Removing that turns a successful deploy into `Your push was rejected because it contains binary files`.
