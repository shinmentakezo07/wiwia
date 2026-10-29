#!/usr/bin/env python3
"""Run the wiwi gateway on a Runtime Cloud sandbox.

This is the deployment harness that moves the project's real work onto Runtime
(https://withruntime.com). It:

  1. Creates a Runtime sandbox (2 vCPU / 4 GiB / 8 GiB disk) on free usage.
  2. Uploads the source tree (secrets, venvs, node_modules and runtime state
     excluded) plus a self-contained config, so the sandbox needs no keys.
  3. Installs the project (pip install -e '.[dev,redis,otel]').
  4. Runs the project's own gate: `ruff check` and the pytest suite.
  5. Starts the real job -- the wiwi gateway server -- in the background via
     `spawn`, then proves it is live: /health returns 200 and /v1/models
     answers with the configured providers.
  6. Deletes the sandbox so nothing is left billed.

Every step is fatal: a red gate exits non-zero instead of being printed and
skipped, so this can be wired into CI. Exit codes are 1 (gate red), 2 (server
never became healthy) and 3 (server did not answer /v1/models).

Usage:
    RUNTIME_API_KEY=... python3 deploy/runtime_deploy.py
    # or reuse the machine's saved Runtime connection (no key needed)

Requirements: Python 3.11+ with `withruntime` installed (`pip install withruntime`).
"""

from __future__ import annotations

import io
import re
import sys
import tarfile
import time
from pathlib import Path

from withruntime import Sandbox

REPO = Path(__file__).resolve().parent.parent
SANDBOX_NAME = "wiwi-runtime-move"
LABELS = {"app": "wiwi", "job": "runtime-move"}
REMOTE = "/workspace/wiwi"
MASTER_KEY = "sk-test-master"

# Never ship: secrets, caches, nested repos, runtime state.
EXCLUDE_TOP = {
    ".git", "node_modules", ".venv", "__pycache__", ".pytest_cache",
    ".hypothesis", ".mimocode", ".playwright-mcp", ".agents", ".perch",
    ".freebuff", ".claude", ".ruff_cache", ".wiwi", ".verify", ".env",
    "deepseek-web2api-free", "wiwi.db", "web/node_modules",
}
# ``EXCLUDE_RE`` matches a *directory component* anywhere in the path; the
# compiled / object files are matched as suffixes. (The old pattern anchored on
# ``$``, so a file inside ``web/node_modules/pkg/index.js`` was never excluded
# — the whole tree shipped.)
EXCLUDE_RE = re.compile(r"(^|/)(__pycache__|\.pyc$|\.git|node_modules|\.venv)(/|$)")

# The harness must not depend on the operator's wiwi.yaml (gitignored, carries
# live keys) nor on .env (which it deliberately does not upload). This config
# binds one placeholder credential, so the provider and its ``smoke`` group
# exist and /health reports ``ok`` instead of ``degraded`` regardless of what
# the box has configured. ``database_url`` is left unset on purpose:
# DATABASE_URL is not forwarded, so the server takes its SQLite default.
#
# Shape matters: ``model_list`` entries nest their fields under
# ``wiwi_params`` (``provider`` + ``model``). A flat entry is dropped silently
# by ``WiwiConfig._validate``, leaving a gateway with no routes that still
# answers /health with 200 — which is why the health gate checks the body.
CONFIG_YAML = """\
general_settings:
  master_key: os.environ/WIWI_MASTER_KEY

providers:
  - name: local-ollama
    provider: openai-compatible
    base_url: http://localhost:11434/v1
    keys:
      - {label: local, key: "ollama"}

model_list:
  - model_name: smoke
    wiwi_params:
      provider: local-ollama
      model: smoke

wiwi_settings:
  port: 4000
"""


def make_tar(root: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path in root.rglob("*"):
            rel = path.relative_to(root)
            if rel.parts and rel.parts[0] in EXCLUDE_TOP:
                continue
            if EXCLUDE_RE.search(str(rel)):
                continue
            if path.is_file() and path.exists():
                tf.add(path, arcname=f"wiwi-source/{rel}")
    return buf.getvalue()


def main() -> int:
    print(":: building source archive", flush=True)
    tarball = make_tar(REPO)
    print(f":: archive ~{len(tarball) / 1e6:.1f} MB", flush=True)

    print(f":: creating sandbox '{SANDBOX_NAME}'", flush=True)
    sbx = Sandbox.create(
        name=SANDBOX_NAME,
        labels=LABELS,
        vcpu=2,
        memory_mib=4096,
        disk_mib=8192,
        timeout_seconds=3600,
        on_lease_end="stop",
    )
    print(f":: sandbox {sbx.id} ready", flush=True)
    try:
        print(":: uploading source", flush=True)
        sbx.files.write("/workspace/wiwi-source.tar.gz", tarball)

        def run(cmd: str, timeout: int = 900) -> str:
            """Run ``cmd`` in the sandbox, echo its output, return stdout+stderr.

            ``cmd | tail`` exits with ``tail``'s status, not the command's, so
            callers check the text (a marker line) instead of an exit code.
            The exit status is also folded into the returned text as a
            ``[exit N]`` line, which is what :func:`run` shares with callers.
            """
            print(f"\n$ {cmd}\n" + "=" * 60, flush=True)
            r = sbx.exec(cmd, timeout_ms=timeout * 1000,
                         on_stdout=sys.stdout.write, on_stderr=sys.stderr.write)
            marker = f"\n[exit {r.exit_code}]"
            print(marker, flush=True)
            return (r.stdout or "") + (r.stderr or "") + marker

        run("mkdir -p /workspace/wiwi && "
            "tar -xzf /workspace/wiwi-source.tar.gz -C /workspace/wiwi "
            "--strip-components=1")

        sbx.files.write(f"{REMOTE}/wiwi.yaml", CONFIG_YAML)

        # Unpiped: the install's real exit code is the gate.
        if "[exit 0]" not in run("cd /workspace/wiwi && "
                                  "python3 -m pip install -q -e '.[dev,redis,otel]'",
                                  timeout=1500):
            print(":: pip install failed", flush=True)
            return 1

        print(":: running the project's lint gate", flush=True)
        if "All checks passed" not in run("cd /workspace/wiwi && "
                                          "python3 -m ruff check wiwi/ tests/ "
                                          "2>&1 | tail -25", timeout=600):
            print(":: ruff gate is red", flush=True)
            return 1

        print(":: running the project's pytest suite (full). This may take several minutes.",
              flush=True)
        if not re.search(r"\d+ passed", run(
                "cd /workspace/wiwi && python3 -m pytest tests/ -q "
                "--no-header -p no:cacheprovider 2>&1 | tail -25", timeout=2400)):
            print(":: pytest gate is red", flush=True)
            return 1

        print(":: starting the real job: wiwi gateway server", flush=True)
        prof = sbx.spawn(
            f"cd {REMOTE} && "
            f"WIWI_MASTER_KEY={MASTER_KEY} python3 -m wiwi.main --config wiwi.yaml"
        )
        print(f":: server process {prof.id} spawned", flush=True)

        def http_status(url: str, extra: str = "") -> str:
            try:
                r = sbx.exec(f"curl -s -o /tmp/body.json -w '%{{http_code}}' {extra} {url}",
                             timeout_ms=20_000)
                return r.stdout.strip()
            except Exception as e:  # noqa: BLE001 - remote sandbox call can raise many SDK errors
                print(f":: probe error: {e}", file=sys.stderr, flush=True)
                return ""

        deadline = time.time() + 180
        ok = False
        while time.time() < deadline and not ok:
            # A 200 alone is not proof: /health answers 200 while the gateway
            # has no deployments and reports ``degraded`` (measured — a
            # model_list that fails the ``wiwi_params`` shape check is dropped
            # silently). Require a healthy body AND a resolvable route.
            ok = (http_status("http://127.0.0.1:4000/health") == "200"
                  and '"status":"ok"' in sbx.files.read_text("/tmp/body.json")
                  and http_status("http://127.0.0.1:4000/public/models") == "200"
                  and "smoke" in sbx.files.read_text("/tmp/body.json"))
            if not ok:
                time.sleep(3)
        if not ok:
            print(":: gateway never became healthy with the 'smoke' group", flush=True)
            return 2
        print(":: /health healthy, /public/models serves 'smoke'", flush=True)

        code = http_status("http://127.0.0.1:4000/v1/models",
                           f"-H 'Authorization: Bearer {MASTER_KEY}'")
        print(f":: /v1/models HTTP {code}", flush=True)
        body = sbx.files.read_text("/tmp/body.json")[:500]
        print(f":: /v1/models body: {body}", flush=True)
        # Clean up in ``finally`` too: a failed probe used to leave the server
        # running until the sandbox was deleted.
        prof.kill("SIGTERM")
        if code != "200" or "smoke" not in body:
            print(":: /v1/models did not serve the configured model", flush=True)
            return 3

        print(":: DONE — wiwi gateway verified running on Runtime", flush=True)
        return 0
    finally:
        print(f":: deleting sandbox {sbx.id}", flush=True)
        sbx.delete()
        print(":: sandbox deleted", flush=True)


if __name__ == "__main__":
    sys.exit(main())
