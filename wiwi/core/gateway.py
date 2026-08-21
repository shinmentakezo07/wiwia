"""Gateway engine: executes one IR request through router -> adapter -> httpx,
pumping IR deltas to the caller's wire encoder. Surface-agnostic."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import orjson

from wiwi.core.context import RequestContext
from wiwi.cost.pricing import CostEngine, estimate_tokens
from wiwi.ir import types as ir
from wiwi.logging_core.events import LogEvent
from wiwi.providers.base import ProviderKeyRef, WiwiError, error_from_provider_status
from wiwi.providers.registry import get_adapter
from wiwi.router.router import Deployment, Router, execute_with_retries
from wiwi.streaming import deltas as dl


class Gateway:
    def __init__(self, router: Router, cost_engine: CostEngine, kind: str = "chat"):
        self.router = router
        self.cost = cost_engine
        self.kind = kind  # "chat" | "embeddings"
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- non-streaming ---------------------------------------------------------
    async def complete(self, ctx: RequestContext) -> ir.AssistantTurn:
        async def call_one(dep: Deployment, key: ProviderKeyRef, c: RequestContext):
            return await self._call(dep, key, c)

        return await execute_with_retries(self.router, ctx, call_one)

    async def _call(self, dep: Deployment, key: ProviderKeyRef,
                    ctx: RequestContext) -> ir.AssistantTurn:
        adapter = get_adapter(dep.provider.provider_type)
        params: dict[str, Any] = {"max_tokens": dep.max_tokens, "extra_body": {}}
        url = adapter.build_url(dep.provider.base_url, dep.model_id, False, self.kind)
        body = adapter.encode_request(ctx.ir_req, dep.model_id, params)
        headers = {**adapter.headers(key), **dep.provider.extra_headers,
                   **dep.extra_headers}
        t0 = time.monotonic()
        try:
            resp = await self._client.post(url, json=body, headers=headers,
                                           timeout=dep.timeout or dep.provider.timeout_s)
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout,
                httpx.PoolTimeout) as e:
            ctx.note_attempt(f"{dep.group}/{dep.model_id}", dep.provider.name, key.label,
                             type(e).__name__, int((time.monotonic() - t0) * 1000))
            raise WiwiError(504 if "Timeout" in type(e).__name__ else 502,
                            "timeout" if "Timeout" in type(e).__name__
                            else "api_connection_error",
                            f"upstream {type(e).__name__}", retryable=True) from e
        latency = int((time.monotonic() - t0) * 1000)
        if resp.status_code != 200:
            retry_after = None
            ra = resp.headers.get("retry-after")
            if ra:
                try:
                    retry_after = float(ra)
                except ValueError:
                    retry_after = None
            ctx.note_attempt(f"{dep.group}/{dep.model_id}", dep.provider.name, key.label,
                             f"http_{resp.status_code}", latency)
            raise error_from_provider_status(resp.status_code, resp.text,
                                             dep.provider.name) if not retry_after else \
                WiwiError(429 if resp.status_code == 429 else 502,
                          "rate_limit_error" if resp.status_code == 429
                          else "api_connection_error",
                          resp.text[:300], retryable=True, retry_after=retry_after)
        ctx.note_attempt(f"{dep.group}/{dep.model_id}", dep.provider.name, key.label,
                         "ok", latency)
        turn = adapter.decode_response(resp.status_code, resp.content)
        self._price(ctx, dep, turn.usage)
        return turn

    # -- streaming ---------------------------------------------------------------
    async def stream(self, ctx: RequestContext) -> AsyncIterator[dl.IRStreamDelta]:
        queue: asyncio.Queue[dl.IRStreamDelta] = asyncio.Queue(maxsize=4096)

        async def call_one(dep: Deployment, key: ProviderKeyRef, c: RequestContext):
            task = asyncio.create_task(
                self._pump(dep, key, c, queue))
            return task

        task = await execute_with_retries(self.router, ctx, call_one)
        assert isinstance(task, asyncio.Task)
        yield dl.StreamStart(model=ctx.ir_req.model, group=ctx.group or "")
        first = True
        try:
            while True:
                d = await queue.get()
                if first and isinstance(d, (dl.TextDelta, dl.ThinkingDelta,
                                            dl.ToolCallOpen)):
                    ctx.first_token_at = time.monotonic()
                    first = False
                if isinstance(d, (dl.TextDelta, dl.ThinkingDelta)):
                    ctx.last_token_at = time.monotonic()
                if isinstance(d, dl.StreamStart):
                    continue  # we emitted our own
                yield d
                if isinstance(d, (dl.StreamEnd, dl.StreamError)):
                    break
        finally:
            task.cancel()

    async def _pump(self, dep: Deployment, key: ProviderKeyRef,
                    ctx: RequestContext, queue: asyncio.Queue) -> None:
        adapter = get_adapter(dep.provider.provider_type)
        params: dict[str, Any] = {"max_tokens": dep.max_tokens, "extra_body": {}}
        url = adapter.build_url(dep.provider.base_url, dep.model_id, True, self.kind)
        body = adapter.encode_request(ctx.ir_req, dep.model_id, params)
        headers = {**adapter.headers(key), **dep.provider.extra_headers,
                   **dep.extra_headers}
        usage_final: dl.UsageFinal | None = None
        finish: dl.Finish | None = None
        text_len = 0
        try:
            async with self._client.stream("POST", url, json=body, headers=headers,
                                           timeout=dep.timeout
                                           or dep.provider.timeout_s) as resp:
                if resp.status_code != 200:
                    raw = await resp.aread()
                    err = error_from_provider_status(resp.status_code,
                                                     raw.decode(errors="replace"),
                                                     dep.provider.name)
                    dep.provider.on_result(key, resp.status_code, err.retry_after)
                    await queue.put(dl.StreamError(err.message, "status",
                                                   resp.status_code))
                    return
                async for line in resp.aiter_lines():
                    if ctx.cancel.is_set():
                        await queue.put(dl.StreamError("client disconnected",
                                                       "cancelled"))
                        return
                    if not line or line.startswith(":"):
                        continue
                    event = ""
                    data = ""
                    if line.startswith("event:"):
                        event = line[6:].strip()
                        continue
                    if line.startswith("data:"):
                        data = line[5:].strip()
                    else:
                        continue
                    for d in adapter.decode_stream_event(event, data):
                        if isinstance(d, dl.UsageFinal):
                            usage_final = d
                        elif isinstance(d, dl.Finish):
                            finish = d
                        elif isinstance(d, dl.StreamEnd):
                            continue
                        else:
                            if isinstance(d, dl.TextDelta):
                                text_len += len(d.text)
                            await queue.put(d)
            # upstream closed normally
            u = usage_final or dl.UsageFinal()
            if u.output == 0 and finish is None:
                pass
            est_usage = u
            if est_usage.prompt == 0:
                est_usage = dl.UsageFinal(
                    prompt=estimate_tokens(_flatten(ctx)),
                    output=max(1, text_len // 4), estimated=True)
            self._price_stream(ctx, dep, est_usage)
            await queue.put(est_usage)
            await queue.put(finish or dl.Finish("stop"))
            await queue.put(dl.StreamEnd())
        except (httpx.ConnectError, httpx.ReadTimeout) as e:
            await queue.put(dl.StreamError(str(e),
                                           "timeout" if "Timeout" in type(e).__name__
                                           else "connection"))
        except asyncio.CancelledError:
            raise

    def _price(self, ctx: RequestContext, dep: Deployment, u: ir.Usage) -> None:
        model_key = f"{dep.provider.provider_type}/{dep.model_id}"
        ctx.usage = u
        ctx.cost = self.cost.cost(model_key, u.prompt_tokens, u.completion_tokens,
                                  u.cached_tokens)

    def _price_stream(self, ctx: RequestContext, dep: Deployment,
                      u: dl.UsageFinal) -> None:
        model_key = f"{dep.provider.provider_type}/{dep.model_id}"
        ctx.usage = ir.Usage(prompt_tokens=u.prompt, completion_tokens=u.output,
                             cached_tokens=u.cached, reasoning_tokens=u.reasoning,
                             reasoning_estimated=u.estimated,
                             cache_creation_tokens=u.cache_creation)
        ctx.cost = self.cost.cost(model_key, u.prompt, u.output, u.cached)


def _flatten(ctx: RequestContext) -> str:
    out = []
    for m in ctx.ir_req.messages:
        for p in m.parts:
            if isinstance(p, ir.TextPart):
                out.append(p.text)
    return " ".join(out)


def build_log_event(ctx: RequestContext) -> LogEvent:
    latency_ms = (time.monotonic() - ctx.started) * 1000
    ttft = ((ctx.first_token_at - ctx.started) * 1000
            if ctx.first_token_at else 0.0)
    stream_secs = ((ctx.last_token_at - ctx.first_token_at)
                   if ctx.first_token_at and ctx.last_token_at else 0.0)
    u = ctx.usage
    tps = (u.completion_tokens / stream_secs) if stream_secs > 0.05 else 0.0
    auth = ctx.auth
    evt = LogEvent(
        stream="request", ts=time.time(), request_id=ctx.request_id,
        surface=ctx.surface, key_alias=getattr(auth, "alias", ""),
        model_group=ctx.group or "", provider=(ctx.deployment.provider.name
                                               if ctx.deployment else ""),
        provider_key_label=(getattr(ctx.provider_key, "label", "") if ctx.provider_key else ""),
        status=ctx.status, error_code=(ctx.error.etype if ctx.error else ""),
        tok_in=u.prompt_tokens if u else 0,
        tok_cached=u.cached_tokens if u else 0,
        tok_reasoning=u.reasoning_tokens if u else 0,
        tok_out=u.completion_tokens if u else 0,
        tps=round(tps, 2), ttft_ms=round(ttft, 1), latency_ms=round(latency_ms, 1),
        cost=ctx.cost, was_stream=ctx.ir_req.stream, cache_hit=ctx.cache_hit,
        attempts=[{"deployment": a.deployment, "provider": a.provider,
                   "key": a.provider_key_label, "status": a.status,
                   "latency_ms": a.latency_ms} for a in ctx.attempts],
    )
    return evt


def encode_json(obj: Any) -> bytes:
    return orjson.dumps(obj)
