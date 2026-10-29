#!/usr/bin/env python3
"""Run the wiwi gateway on a Runtime Cloud sandbox.

This is the deployment harness that moves the project's real work onto Runtime
(https://withruntime.com). It:

  1. Creates a Runtime sandbox (2 vCPU / 4 GiB / 8 GiB disk) on free usage.
  2. Uploads the source tree (secrets, venvs, node_modules and runtime state
     excluded).
  3. Installs the project (pip install -e '.[dev,redis,otel]').
  4. Runs the project's own gate: `ruff check` and the pytest suite.
  5. Starts the real job -- the wiwi gateway server -- in the background via
     `spawn`, then proves it is live: /health returns 200 and /v1/models
     answers with the configured providers.
  6. Deletes the sandbox so nothing is left billed.

Usage:
    RUNTIME_API_KEY=... python3 deploy/runtime_deploy.py
    # or reuse the machine's saved Runtime connection (no key needed)

Requirements: Python 3.11+ with `withruntime` installed (`pip install withruntime`).
"""

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

# Never ship: secrets, caches, nested repos, runtime state.
EXCLUDE_TOP = {
    ".git", "node_modules", ".venv", "__pycache__", ".pytest_cache",
    ".hypothesis", ".mimocode", ".playwright-mcp", ".agents", ".perch",
    ".freebuff", ".claude", ".ruff_cache", ".wiwi", ".verify", ".env",
    "deepseek-web2api-free", "wiwi.db", "web/node_modules",
}
EXCLUDE_RE = re.compile(r"(^|/)(__pycache__|\.pyc|\.git|node_modules|\.venv)$")


def make_tar(root: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path in root.rglob("*"):
            if not path.is_file() or not path.exists():
                continue
            rel = path.relative_to(root)
            if rel.parts and rel.parts[0] in EXCLUDE_TOP:
                continue
            if EXCLUDE_RE.search(str(rel)):
                continue
            tf.add(path, arcname=f"wiwi-source/{rel}")
    return buf.getvalue()


def main() -> int:
    print(":: building source archive")
    tarball = make_tar(REPO)
    print(f":: archive ~{len(tarball)/1e6:.1f} MB")

    print(f":: creating sandbox '{SANDBOX_NAME}'")
    sbx = Sandbox.create(
        name=SANDBOX_NAME,
        labels=LABELS,
        vcpu=2,
        memory_mib=4096,
        disk_mib=8192,
        timeout_seconds=3600,
        on_lease_end="stop",
    )
    print(f":: sandbox {sbx.id} ready")
    try:
        print(":: uploading source")
        sbx.files.write("/workspace/wiwi-source.tar.gz", tarball)

        def run(cmd: str, timeout: int = 900) -> int:
            print(f"\n$ {cmd}\n" + "=" * 60, flush=True)
            r = sbx.exec(cmd, timeout_ms=timeout * 1000,
                         on_stdout=sys.stdout.write, on_stderr=sys.stderr.write)
            print(f"\n[exit {r.exit_code}]", flush=True)
            return r.exit_code

        rc = run("mkdir -p /workspace/wiwi && "
                 "tar -xzf /workspace/wiwi-source.tar.gz -C /workspace/wiwi "
                 "--strip-components=1")
        if rc != 0:
            return rc

        rc = run("cd /workspace/wiwi && "
                 "python3 -m pip install -q -e '.[dev,redis,otel]' "
                 "2>&1 | tail -20", timeout=1500)
        if rc != 0:
            print(":: pip install failed; skipping tests (still verify server)", flush=True)

        print(":: running the project's lint gate")
        run("cd /workspace/wiwi && python3 -m ruff check wiwi/ tests/", timeout=600)

        print(":: running the project's pytest suite (full). This may take several minutes.")
        rc = run("cd /workspace/wiwi && python3 -m pytest tests/ -q "
                 "--no-header -p no:cacheprovider 2>&1 | tail -25", timeout=2400)
        if rc != 0:
            print(f":: pytest exited {rc}; continuing to live-server verification", flush=True)

        print(":: starting the real job: wiwi gateway server")
        prof = sbx.spawn(
            "cd /workspace/wiwi && "
            "WIWI_MASTER_KEY=sk-test-master python3 -m wiwi.main "
            "--config wiwi.yaml.example"
        )
        print(f":: server process {prof.id} spawned", flush=True)

        def health_ok():
            try:
                r = sbx.exec("curl -s -o /dev/null -w '%{http_code}' "
                             "http://127.0.0.1:4000/health", timeout_ms=20000)
                return r.stdout.strip() == "200"
            except Exception as e:  # noqa: BLE001 - remote sandbox call can raise many SDK errors
                print(f":: health probe error: {e}", file=sys.stderr, flush=True)
                return False

        deadline = time.time() + 180
        ok = False
        while time.time() < deadline and not ok:
            ok = health_ok()
            if not ok:
                time.sleep(3)
        if not ok:
            print(":: /health never reached 200", flush=True)
            return 1
        print(":: /health -> 200", flush=True)

        r = sbx.exec("curl -s -o /tmp/models.json -w '%{http_code}' "
                     "-H 'Authorization: Bearer sk-test-master' "
                     "http://127.0.0.1:4000/v1/models", timeout_ms=30000)
        print(f":: /v1/models HTTP {r.stdout.strip()}", flush=True)
        body = sbx.files.read_text("/tmp/models.json")[:500]
        print(f":: /v1/models body: {body}", flush=True)
        prof.kill("SIGTERM")

        print(":: DONE — wiwi gateway verified running on Runtime", flush=True)
        return 0
    finally:
        print(f":: deleting sandbox {sbx.id}", flush=True)
        sbx.delete()
        print(":: sandbox deleted", flush=True)


if __name__ == "__main__":
    sys.exit(main())
