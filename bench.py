#!/usr/bin/env python3
"""bench.py — stress / latency / TPS tester for wiwi and any OpenAI-compatible proxy.

Measures per request: TTFT (time to first token), total latency, input/output
tokens, output TPS (tokens/sec of the streaming window). Aggregates p50/p95,
success rate, and throughput per concurrency level, then compares targets.

Usage:
  .venv/bin/python bench.py                     # default sweep
  .venv/bin/python bench.py -n 10 -c 1,4,16 --max-tokens 100
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time

import httpx

TARGETS = {
    "wiwi(4000)": {"base": "http://localhost:4000/v1",
                   "key": "sk-wiwi-master-fionn-2026"},
    "other(8317)": {"base": "http://localhost:8317/v1", "key": "123"},
}
MODEL = "stealth/ox-alpha"
PROMPT = ("You are being benchmarked. Reply with exactly this sentence and "
          "nothing else: The quick brown fox jumps over the lazy dog.")


async def one_request(client: httpx.AsyncClient, base: str, key: str,
                      max_tokens: int, stream: bool) -> dict:
    t0 = time.perf_counter()
    ttft = None
    text_chars = 0
    usage = None
    try:
        async with client.stream(
            "POST", f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={"model": MODEL, "stream": stream, "max_tokens": max_tokens,
                  "messages": [{"role": "user", "content": PROMPT}]},
        ) as r:
            if r.status_code != 200:
                body = (await r.aread()).decode(errors="replace")[:150]
                return {"ok": False, "err": f"HTTP {r.status_code}: {body}"}
            if stream:
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or [{}]
                    delta = choices[0].get("delta") or {}
                    if delta.get("content"):
                        if ttft is None:
                            ttft = time.perf_counter() - t0
                        text_chars += len(delta["content"])
                    if chunk.get("usage"):
                        usage = chunk["usage"]
            else:
                obj = json.loads((await r.aread()))
                msg = (obj.get("choices") or [{}])[0].get("message") or {}
                text_chars = len(msg.get("content") or "")
                usage = obj.get("usage")
                ttft = time.perf_counter() - t0
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "err": f"{type(e).__name__}: {e}"}

    total = time.perf_counter() - t0
    if ttft is None:
        ttft = total  # non-streaming or no content delta observed
    out_toks = (usage or {}).get("completion_tokens") or max(1, text_chars // 4)
    in_toks = (usage or {}).get("prompt_tokens") or 0
    cached = ((usage or {}).get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    gen_secs = max(total - ttft, 0.001)
    return {"ok": True, "ttft": ttft, "total": total, "in": in_toks,
            "out": out_toks, "cached": cached, "tps": out_toks / gen_secs}


def pct(values: list[float], p: float) -> float:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return 0.0
    return vals[min(len(vals) - 1, int(len(vals) * p))]


async def sweep(name: str, cfg: dict, n: int, concurrencies: list[int],
                max_tokens: int, stream: bool) -> list[dict]:
    rows = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0),
                                 limits=httpx.Limits(max_connections=64)) as client:
        for c in concurrencies:
            results, i = [], 0
            while i < n:
                batch = min(c, n - i)
                t0 = time.perf_counter()
                results += await asyncio.gather(*[
                    one_request(client, cfg["base"], cfg["key"], max_tokens, stream)
                    for _ in range(batch)])
                wall = time.perf_counter() - t0
                i += batch
            oks = [r for r in results if r["ok"]]
            errs = [r for r in results if not r["ok"]]
            row = {
                "target": name, "conc": c, "reqs": n,
                "ok": len(oks), "fail": len(errs),
                "ttft_p50": pct([r["ttft"] for r in oks], .50),
                "ttft_p95": pct([r["ttft"] for r in oks], .95),
                "lat_p50": pct([r["total"] for r in oks], .50),
                "lat_p95": pct([r["total"] for r in oks], .95),
                "tps_avg": statistics.mean([r["tps"] for r in oks]) if oks else 0,
                "tok_out_total": sum(r["out"] for r in oks),
                "tok_in_avg": statistics.mean([r["in"] for r in oks]) if oks else 0,
                "cached_avg": statistics.mean([r["cached"] for r in oks]) if oks else 0,
                "wall_s": wall,
                "rps": len(oks) / wall if wall > 0 else 0,
                "err_sample": errs[0]["err"][:80] if errs else "",
            }
            rows.append(row)
    return rows


def print_table(all_rows: list[dict]) -> None:
    hdr = (f"{'target':<13}{'conc':>5}{'ok/fail':>9}{'TTFT p50':>10}{'TTFT p95':>10}"
           f"{'LAT p50':>10}{'LAT p95':>10}{'TPS avg':>9}{'RPS':>7}{'tok_in':>8}{'cached':>8}")
    print(hdr)
    print("-" * len(hdr))
    for r in all_rows:
        print(f"{r['target']:<13}{r['conc']:>5}{f'{r[chr(111)+chr(107)]}/{r[chr(102)+chr(97)+chr(105)+chr(108)]}':>9}"
              f"{r['ttft_p50']:>9.2f}s{r['ttft_p95']:>9.2f}s"
              f"{r['lat_p50']:>9.2f}s{r['lat_p95']:>9.2f}s"
              f"{r['tps_avg']:>8.1f}{r['rps']:>7.2f}"
              f"{r['tok_in_avg']:>8.0f}{r['cached_avg']:>8.0f}")
    fails = [r for r in all_rows if r["fail"]]
    if fails:
        print("\nerrors:")
        for r in fails:
            print(f"  {r['target']} c={r['conc']}: {r['err_sample']}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=6, help="requests per target per wave")
    ap.add_argument("-c", default="1,4", help="concurrency levels, comma-sep")
    ap.add_argument("--max-tokens", type=int, default=60)
    ap.add_argument("--no-stream", action="store_true")
    ap.add_argument("--targets", default=",".join(TARGETS.keys()))
    args = ap.parse_args()

    concs = [int(x) for x in args.c.split(",")]
    wanted = [t for t in args.targets.split(",") if t.strip()]
    all_rows = []
    for name in wanted:
        cfg = TARGETS.get(name)
        if cfg is None:
            print(f"unknown target {name!r}; choose from {list(TARGETS)}", file=sys.stderr)
            sys.exit(1)
        print(f">>> sweeping {name}  (n={args.n}, c={concs}, "
              f"max_tokens={args.max_tokens}, stream={not args.no_stream})")
        rows = await sweep(name, cfg, args.n, concs, args.max_tokens,
                           not args.no_stream)
        all_rows += rows
    print()
    print_table(all_rows)


if __name__ == "__main__":
    asyncio.run(main())
