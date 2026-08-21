# Design: Deploy the OpenRouter key pool in wiwi

*Date: 2026-08-21 · Status: approved · Scope: configuration + git hygiene, zero code changes*

## Goal

The deployed wiwi instance (port 4000) currently routes all traffic through a single
OpenRouter key (`os.environ/OPENROUTER_API_KEY`). The smooth weighted-round-robin key
pool, per-key cooldown, and invalid-key marking features exist in `wiwi/router/router.py`
and are covered by `tests/test_router.py`, but are unused in production config. Deploy
the 16-key pool from `key.md` so rotation, cooldowns, and per-key failover actually run.

Non-goals (deferred): SSE keepalive pings, `prompt_cache_key` pinning, full
RESEARCH.md §4 checklist audit. Benchmark findings confirmed cached-token relay and
TPS fidelity in wiwi are already correct — no work there.

## Decisions (user-approved)

1. **Key storage: untrack `wiwi.yaml`, keys inline.** `git rm --cached wiwi.yaml`,
   gitignore it. `wiwi.yaml.example` stays as the tracked template. Rationale: zero code
   changes, uses the existing tested `KeyDef` path, and the `.example` file exists
   precisely so the live config can hold secrets untracked.
2. **Rejected alternatives:** per-key env vars (`os.environ/OPENROUTER_KEY_01…16` —
   16 vars to manage everywhere wiwi runs) and a new `keys_file` config option
   (new feature + tests for no added capability over inline entries).

## Config shape (`wiwi.yaml`)

`providers[0]` (`openrouter`, `openai-compatible`, base_url
`https://openrouter.ai/api/v1`) gets its `keys:` list replaced with 16 entries:

```yaml
keys:
  - {label: or-01, key: sk-or-v1-<redacted>, weight: 1, enabled: true}
  - {label: or-02, key: sk-or-v1-<redacted>, weight: 1, enabled: true}
  # … through or-16
```

- Source: `key.md` (28 lines → **16 unique keys**; lines 17–27 duplicate 1–11). Dedupe on paste.
- Labels `or-01`…`or-16` surface in request logs as `provider_key_label`, making
  rotation and per-key failures legible in `/admin/logs/requests`.
- Equal weights; smooth WRR spreads load. The `os.environ/OPENROUTER_API_KEY`
  reference is removed.

## Git hygiene

| Action | Why |
|---|---|
| `git rm --cached wiwi.yaml` + `.gitignore` entry | live config now holds secrets; tracked template is `wiwi.yaml.example` |
| `.gitignore` += `key.md` (then delete the file) | keys live in `wiwi.yaml`; one plaintext copy on disk, not two |
| `.gitignore` += `wiwi.db` | runtime SQLite state, currently untracked-but-committable |
| `Dockerfile` / `docker-compose.yml` | unchanged — image bakes only the example; compose mounts live `wiwi.yaml` read-only |

`wiwi.yaml` already has 0600 permissions.

## Deployment & verification

1. Edit `wiwi.yaml`, delete `key.md`, update `.gitignore`, `git rm --cached wiwi.yaml`.
2. Restart the running instance (currently pid 55737) with the same command:
   `.venv/bin/wiwi --config wiwi.yaml --port 4000`.
3. **Rotation check:** send ~8 streaming chat requests, then
   `GET /admin/logs/requests` (master key) and confirm `provider_key_label` values
   span multiple distinct `or-*` labels.
4. **Regression check:** `pytest tests/ -q` stays green (config schema untouched);
   optional short `bench.py` wave to confirm no latency regression.

## Error handling

No new paths. `config.py` validation fails fast at startup on malformed key entries
(missing key, wrong type); `ProviderDef._need_keys` still requires ≥1 key per provider.
Router cooldown/invalid behavior is unchanged and already unit-tested.

## Testing

No new unit tests — config-only change exercising existing covered behavior
(`tests/test_router.py`: WRR, cooldowns; `tests/test_config.py`: load/validate).
Verification is operational (step 3 above).
