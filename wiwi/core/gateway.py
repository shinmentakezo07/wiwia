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
from wiwi.streaming.sse import LineSSEParser


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
        ctx.deployment = dep
        ctx.provider_key = key
        params: dict[str, Any] = {"max_tokens": dep.max_tokens, "extra_body": {}}
        url = _build_url(adapter, dep, key, False, self.kind)
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
        queue: asyncio.Queue[dl.IRStreamDelta | dl.StreamError] = asyncio.Queue(maxsize=4096)
        pump_task: asyncio.Task | None = None

        async def call_one(dep: Deployment, key: ProviderKeyRef, c: RequestContext):
            nonlocal pump_task
            ready = asyncio.Event()
            err_box: list[WiwiError | None] = [None]
            pump_task = asyncio.create_task(
                self._pump(dep, key, c, queue, ready, err_box))
            # Wait until the pump either connects successfully or fails before
            # sending any data.  If it fails, raise so execute_with_retries can
            # retry on a different deployment.
            await ready.wait()
            if err_box[0] is not None:
                pump_task.cancel()
                raise err_box[0]
            return pump_task

        await execute_with_retries(self.router, ctx, call_one)
        assert pump_task is not None
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
            if pump_task and not pump_task.done():
                pump_task.cancel()

    async def _pump(self, dep: Deployment, key: ProviderKeyRef,
                    ctx: RequestContext, queue: asyncio.Queue,
                    ready: asyncio.Event,
                    err_box: list) -> None:
        """Stream pump.  Sets *ready* once the upstream connection is established
        (so the caller can begin consuming the queue) or puts a WiwiError into
        *err_box* and sets *ready* if it fails before any data flows."""
        adapter = get_adapter(dep.provider.provider_type)
        ctx.deployment = dep
        ctx.provider_key = key
        real_key = dep.provider.get_key(key.label)  # live pool entry for on_result
        params: dict[str, Any] = {"max_tokens": dep.max_tokens, "extra_body": {}}
        url = _build_url(adapter, dep, key, True, self.kind)
        body = adapter.encode_request(ctx.ir_req, dep.model_id, params)
        headers = {**adapter.headers(key), **dep.provider.extra_headers,
                   **dep.extra_headers}
        t0 = time.monotonic()
        usage_final: dl.UsageFinal | None = None
        finish: dl.Finish | None = None
        text_len = 0
        started = False
        try:
            try:
                resp_cm = self._client.stream("POST", url, json=body, headers=headers,
                                             timeout=dep.timeout
                                             or dep.provider.timeout_s)
                resp = await resp_cm.__aenter__()
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout,
                    httpx.PoolTimeout) as e:
                ctx.note_attempt(f"{dep.group}/{dep.model_id}", dep.provider.name,
                                 key.label, type(e).__name__,
                                 int((time.monotonic() - t0) * 1000))
                err_box[0] = WiwiError(
                    504 if "Timeout" in type(e).__name__ else 502,
                    "timeout" if "Timeout" in type(e).__name__
                    else "api_connection_error",
                    f"upstream {type(e).__name__}", retryable=True)
                ready.set()
                return
            if resp.status_code != 200:
                raw = await resp.aread()
                err = error_from_provider_status(resp.status_code,
                                                 raw.decode(errors="replace"),
                                                 dep.provider.name)
                dep.provider.on_result(real_key, resp.status_code, err.retry_after)
                ctx.note_attempt(f"{dep.group}/{dep.model_id}", dep.provider.name,
                                 key.label, f"http_{resp.status_code}",
                                 int((time.monotonic() - t0) * 1000))
                err_box[0] = err
                ready.set()
                await resp_cm.__aexit__(None, None, None)
                return
            # Connection established — signal the caller to start consuming.
            started = True
            ready.set()
            parser = LineSSEParser()
            async for line in resp.aiter_lines():
                if ctx.cancel.is_set():
                    await queue.put(dl.StreamError("client disconnected", "cancelled"))
                    break
                evt = parser.feed_line(line)
                if evt is None:
                    continue
                for d in adapter.decode_stream_event(evt.event, evt.data):
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
            await resp_cm.__aexit__(None, None, None)
            # upstream closed normally; on_result(200) already fired in
            # execute_with_retries when the stream started — don't double count.
            ctx.note_attempt(f"{dep.group}/{dep.model_id}", dep.provider.name, key.label,
                             "ok", int((time.monotonic() - t0) * 1000))
            u = usage_final or dl.UsageFinal()
            est_usage = u
            if est_usage.prompt == 0:
                est_usage = dl.UsageFinal(
                    prompt=estimate_tokens(_flatten(ctx)),
                    output=max(1, text_len // 4), estimated=True)
            self._price_stream(ctx, dep, est_usage)
            await queue.put(est_usage)
            await queue.put(finish or dl.Finish("stop"))
            await queue.put(dl.StreamEnd())
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            if not started:
                ctx.note_attempt(f"{dep.group}/{dep.model_id}", dep.provider.name,
                                 key.label, f"error:{type(e).__name__}",
                                 int((time.monotonic() - t0) * 1000))
                err_box[0] = WiwiError(502, "api_connection_error",
                                       f"stream pump error: {type(e).__name__}: {e}",
                                       retryable=True)
                ready.set()
            else:
                # Mid-stream error — can't retry; send error to client and
                # record the failure on the live pool entry.
                if real_key is not None:
                    real_key.err_count += 1
                await queue.put(dl.StreamError(str(e),
                                               "timeout" if "Timeout" in type(e).__name__
                                               else "connection"))
            try:
                await resp_cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001, S110
                pass

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


def _build_url(adapter, dep: Deployment, key: ProviderKeyRef,
               stream: bool, kind: str) -> str:
    """Build the upstream URL, appending the API key for providers that require it
    in the querystring (e.g. Gemini) rather than headers."""
    url = adapter.build_url(dep.provider.base_url, dep.model_id, stream, kind)
    if dep.provider.provider_type == "gemini" and "?key=" in url:
        url += key.secret
    return url
