"""Gateway engine: executes one IR request through router -> adapter -> httpx,
pumping IR deltas to the caller's wire encoder. Surface-agnostic."""

from __future__ import annotations

import asyncio
import contextlib
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
from wiwi.streaming.coalesce import DeltaCoalescer
from wiwi.streaming.resume import StreamTape, build_continuation_messages
from wiwi.streaming.sse import LineSSEParser


class Gateway:
    def __init__(self, router: Router, cost_engine: CostEngine, kind: str = "chat",
                 drop_params: bool = True):
        self.router = router
        self.cost = cost_engine
        self.kind = kind  # "chat"
        self.drop_params = drop_params
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0), http2=True)

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- non-streaming ---------------------------------------------------------
    async def complete(self, ctx: RequestContext) -> ir.AssistantTurn:
        async def call_one(dep: Deployment, key: ProviderKeyRef, c: RequestContext):
            return await self._call(dep, key, c)

        return await execute_with_retries(self.router, ctx, call_one)

    async def _call(self, dep: Deployment, key: ProviderKeyRef,
                    ctx: RequestContext) -> ir.AssistantTurn:
        # Inflight covers the full upstream round-trip here, and for streams the
        # pump owns it until the last delta (see _pump wrapper below).
        dep.inflight += 1
        try:
            return await self._call_once(dep, key, ctx)
        finally:
            dep.inflight -= 1

    async def _call_once(self, dep: Deployment, key: ProviderKeyRef,
                         ctx: RequestContext) -> ir.AssistantTurn:
        adapter = get_adapter(dep.provider.provider_type)
        ctx.deployment = dep
        ctx.provider_key = key
        params: dict[str, Any] = {"max_tokens": dep.max_tokens,
                                  "extra_body": dict(dep.extra_body),
                                  "drop_params": self.drop_params,
                                  "provider_type": dep.provider.provider_type}
        url = _build_url(adapter, dep, key, False, self.kind)
        body = adapter.encode_request(ctx.ir_req, dep.model_id, params)
        headers = {**adapter.headers(key), **dep.provider.extra_headers,
                   **dep.extra_headers}
        t0 = time.monotonic()
        try:
            resp = await self._client.post(url, json=body, headers=headers,
                                           timeout=dep.timeout or dep.provider.timeout_s)
        except httpx.TransportError as e:
            ctx.note_attempt(f"{dep.group}/{dep.model_id}", dep.provider.name, key.label,
                             type(e).__name__, int((time.monotonic() - t0) * 1000))
            raise WiwiError(504 if "Timeout" in type(e).__name__ else 502,
                            "timeout" if "Timeout" in type(e).__name__
                            else "api_connection_error",
                            f"upstream {type(e).__name__}", retryable=True) from e
        latency = int((time.monotonic() - t0) * 1000)
        if resp.status_code != 200:
            ctx.note_attempt(f"{dep.group}/{dep.model_id}", dep.provider.name, key.label,
                             f"http_{resp.status_code}", latency)
            err = error_from_provider_status(resp.status_code, resp.text,
                                             dep.provider.name)
            ra = _parse_retry_after(resp.headers.get("retry-after"))
            if ra is not None:
                err.retry_after = ra
            raise err
        ctx.note_attempt(f"{dep.group}/{dep.model_id}", dep.provider.name, key.label,
                         "ok", latency)
        dep.latencies.append(latency)
        turn = adapter.decode_response(resp.status_code, resp.content)
        self._price(ctx, dep, turn.usage)
        return turn

    # -- streaming ---------------------------------------------------------------
    async def stream(self, ctx: RequestContext) -> AsyncIterator[dl.IRStreamDelta]:
        queue: asyncio.Queue[dl.IRStreamDelta | dl.StreamError] = asyncio.Queue(maxsize=4096)
        pump_task: asyncio.Task | None = None
        tape = StreamTape()
        resume_mode = self.router.settings.stream_resume
        max_resumes = self.router.settings.stream_resume_max_retries
        coalescer = (DeltaCoalescer(
            max_bytes=self.router.settings.stream_coalesce_max_bytes,
            max_ms=self.router.settings.stream_coalesce_max_ms,
        ) if self.router.settings.stream_coalesce else None)

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

        try:
            await execute_with_retries(self.router, ctx, call_one)
        except BaseException:
            # Cancellation (client gone) or exhaustion while waiting for the
            # pump to connect: without this the pump task keeps running and
            # leaks its upstream connection.
            if pump_task and not pump_task.done():
                pump_task.cancel()
            raise
        assert pump_task is not None
        yield dl.StreamStart(model=ctx.ir_req.model, group=ctx.group or "")
        first = True
        content_flowed = False
        try:
            while True:
                d = await queue.get()
                if first and isinstance(d, (dl.TextDelta, dl.ThinkingDelta,
                                             dl.ToolCallOpen)):
                    ctx.first_token_at = time.monotonic()
                    first = False
                if isinstance(d, (dl.TextDelta, dl.ThinkingDelta)):
                    ctx.last_token_at = time.monotonic()
                    content_flowed = True
                if isinstance(d, dl.ToolCallOpen):
                    content_flowed = True
                if isinstance(d, dl.StreamStart):
                    continue  # we emitted our own
                # Record content-bearing deltas to the tape for resume/replay.
                tape.append(d)
                # Mid-stream failover: if the upstream died after content,
                # attempt a resume on a fallback deployment.
                if (isinstance(d, dl.StreamError) and content_flowed
                        and resume_mode != "off" and max_resumes > 0):
                    resumed = await self._attempt_resume(ctx, tape, queue)
                    if resumed:
                        max_resumes -= 1
                        continue  # keep consuming the queue from the new pump
                if coalescer is not None:
                    for cd in coalescer.feed(d, queue.qsize()):
                        yield cd
                else:
                    yield d
                if isinstance(d, (dl.StreamEnd, dl.StreamError)):
                    break
            # Flush any remaining coalesced deltas.
            if coalescer is not None:
                for cd in coalescer.drain():
                    yield cd
        finally:
            if pump_task and not pump_task.done():
                pump_task.cancel()

    async def _attempt_resume(self, ctx: RequestContext, tape: StreamTape,
                              queue: asyncio.Queue) -> bool:
        """Try to resume a failed stream on a fallback deployment.

        Appends the partial assistant output as a continuation message and
        starts a new pump. Returns True if the new pump connected successfully.
        """
        from wiwi.ir import types as ir
        text = tape.replay_text()
        if not text and self.router.settings.stream_resume == "content_only":
            return False
        # Build continuation messages with the partial output prepended.
        cont_msgs = build_continuation_messages(tape, ctx.ir_req.messages)
        resume_req = ir.Request(
            model=ctx.ir_req.model, messages=cont_msgs,
            tools=ctx.ir_req.tools, tool_choice=ctx.ir_req.tool_choice,
            gen_params=ctx.ir_req.gen_params, stream=True,
            stream_options_include_usage=ctx.ir_req.stream_options_include_usage,
            extras=ctx.ir_req.extras)
        # Find a fallback deployment.
        _group, deps = self.router.resolve_group(ctx.group or ctx.ir_req.model)
        if not deps:
            return False
        # Try each fallback group if configured.
        fb_targets = self.router.fallback_targets(ctx.group or "")
        candidates: list[Deployment] = []
        for fb in fb_targets:
            _, fb_deps = self.router.resolve_group(fb)
            candidates.extend(fb_deps)
        # Also try same-group deployments (excluding the one that failed).
        if ctx.deployment:
            candidates.extend(d for d in deps if d is not ctx.deployment)
        else:
            candidates.extend(deps)
        if not candidates:
            return False
        for dep in candidates:
            if not dep.available:
                continue
            key, _ = await dep.provider.pick_key()
            if key is None:
                continue
            # Create a fresh context for the resume attempt.
            resume_ctx = RequestContext(
                surface=ctx.surface, ir_req=resume_req, auth=ctx.auth,
                group=ctx.group, cancel=ctx.cancel)
            ready = asyncio.Event()
            err_box: list[WiwiError | None] = [None]
            pump_task = asyncio.create_task(
                self._pump(dep, ProviderKeyRef(label=key.label, secret=key.secret),
                           resume_ctx, queue, ready, err_box))
            await ready.wait()
            if err_box[0] is None:
                return True
            pump_task.cancel()
        return False

    async def _pump(self, dep: Deployment, key: ProviderKeyRef,
                    ctx: RequestContext, queue: asyncio.Queue,
                    ready: asyncio.Event,
                    err_box: list) -> None:
        # The stream stays in flight — and counts toward dep.inflight — until
        # this pump finishes, not merely until the connection opens.
        dep.inflight += 1
        try:
            await self._pump_once(dep, key, ctx, queue, ready, err_box)
        finally:
            dep.inflight -= 1

    async def _pump_once(self, dep: Deployment, key: ProviderKeyRef,
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
        params: dict[str, Any] = {"max_tokens": dep.max_tokens,
                                  "extra_body": dict(dep.extra_body),
                                  "drop_params": self.drop_params,
                                  "provider_type": dep.provider.provider_type}
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
            except httpx.TransportError as e:
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
                # on_result is called by execute_with_retries' except handler —
                # don't double-count key errors here.
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
            client_gone = False
            grace_drain_s = self.router.settings.stream_grace_drain_s
            idle_s = self.router.settings.stream_idle_timeout_s
            loop_limit = (self.router.settings.stream_loop_limit
                          if self.router.settings.stream_loop_detection else 0)
            loop_last: str | None = None
            loop_count = 0
            line_iter = resp.aiter_lines().__aiter__()
            while True:
                try:
                    line = await asyncio.wait_for(line_iter.__anext__(), timeout=idle_s)
                except TimeoutError:
                    self._note_stream_failure(dep, real_key)
                    self._price_partial(ctx, dep, usage_final, text_len)
                    await queue.put(dl.StreamError(
                        f"upstream idle >{idle_s:.0f}s between chunks", "timeout"))
                    await resp_cm.__aexit__(None, None, None)
                    return
                except StopAsyncIteration:
                    break
                if ctx.cancel.is_set():
                    if grace_drain_s > 0 and not client_gone:
                        # Grace drain: keep pumping upstream for billing accuracy.
                        client_gone = True
                        # Continue draining for up to grace_drain_s.
                        continue
                    elif not client_gone:
                        client_gone = True
                        await queue.put(dl.StreamError("client disconnected", "cancelled"))
                        break
                    else:
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
                            if loop_limit > 0:
                                if d.text == loop_last:
                                    loop_count += 1
                                    if loop_count >= loop_limit:
                                        self._note_stream_failure(dep, real_key)
                                        self._price_partial(ctx, dep, usage_final,
                                                            text_len)
                                        await queue.put(dl.StreamError(
                                            f"model loop detected ({loop_limit} "
                                            f"identical chunks)", "unknown"))
                                        await resp_cm.__aexit__(None, None, None)
                                        return
                                else:
                                    loop_last = d.text
                                    loop_count = 1
                        await queue.put(d)
            await resp_cm.__aexit__(None, None, None)
            # upstream closed; on_result(200) already fired in
            # execute_with_retries when the stream started — don't double count.
            if not client_gone:
                ctx.note_attempt(f"{dep.group}/{dep.model_id}", dep.provider.name,
                                 key.label, "ok", int((time.monotonic() - t0) * 1000))
                dep.latencies.append(int((time.monotonic() - t0) * 1000))
            real_usage = usage_final or dl.UsageFinal()
            est_usage = real_usage
            if real_usage.prompt == 0:
                # Provider sent no usable usage: estimate, keeping any real
                # output / cache counts it did report.
                est_usage = dl.UsageFinal(
                    prompt=estimate_tokens(_flatten(ctx), dep.model_id),
                    cached=real_usage.cached, reasoning=real_usage.reasoning,
                    output=real_usage.output or max(1, text_len // 4),
                    cache_creation=real_usage.cache_creation, estimated=True)
            self._price_stream(ctx, dep, est_usage)
            await queue.put(est_usage)
            if finish is None and usage_final is None and not client_gone:
                self._note_stream_failure(dep, real_key)
                await queue.put(dl.StreamError(
                    "upstream stream ended without completion", "connection"))
                return
            await queue.put(finish or dl.Finish("stop"))
            await queue.put(dl.StreamEnd())
        except asyncio.CancelledError:
            # client went away mid-stream: still release the upstream response,
            # or the pooled socket stays checked out until GC
            if started:
                with contextlib.suppress(Exception):
                    await asyncio.shield(resp_cm.__aexit__(None, None, None))
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
                # Mid-stream error — can't retry; bill partial delivery, feed
                # health stats, send error to client.
                self._note_stream_failure(dep, real_key)
                self._price_partial(ctx, dep, usage_final, text_len)
                await queue.put(dl.StreamError(str(e),
                                               "timeout" if "Timeout" in type(e).__name__
                                               else "connection"))
            try:
                await resp_cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001, S110
                pass

    def _note_stream_failure(self, dep: Deployment, real_key) -> None:
        """Mid-stream failures carry no HTTP status; feed deployment cooldowns
        and the key pool so a provider that keeps dying mid-stream cools off."""
        if real_key is not None:
            real_key.err_count += 1
        dep.record_fail(self.router.settings.allowed_fails,
                        self.router.settings.cooldown_time)

    def _price_partial(self, ctx: RequestContext, dep: Deployment,
                       usage_final: dl.UsageFinal | None, text_len: int) -> None:
        """Price what was delivered even when the stream failed, so virtual-key
        spend reflects tokens actually consumed."""
        u = usage_final or dl.UsageFinal()
        if u.prompt == 0:
            u = dl.UsageFinal(
                prompt=estimate_tokens(_flatten(ctx), dep.model_id),
                cached=u.cached, reasoning=u.reasoning,
                output=u.output or max(1, text_len // 4),
                cache_creation=u.cache_creation, estimated=True)
        self._price_stream(ctx, dep, u)

    def _price(self, ctx: RequestContext, dep: Deployment, u: ir.Usage) -> None:
        model_key = f"{dep.provider.provider_type}/{dep.model_id}"
        ctx.usage = u
        ctx.cost = self.cost.cost(model_key, u.prompt_tokens, u.completion_tokens,
                                  u.cached_tokens)
        ctx.cache_hit = u.cached_tokens > 0
        ctx.metadata["cache_savings"] = self._cache_savings(model_key, u)

    def _cache_savings(self, model_key: str, u: ir.Usage) -> float:
        """Dollars saved by provider-side prompt caching at this model's rates."""
        p = self.cost.prices.get(model_key) or self.cost.prices.get(model_key.split("/")[-1])
        if not p or u.cached_tokens <= 0:
            return 0.0
        input_rate = p["input_cost_per_token"]
        cache_rate = p.get("cache_read_input_cost_per_token", input_rate)
        return round(u.cached_tokens * max(0.0, input_rate - cache_rate), 8)

    def _price_stream(self, ctx: RequestContext, dep: Deployment,
                      u: dl.UsageFinal) -> None:
        model_key = f"{dep.provider.provider_type}/{dep.model_id}"
        ctx.usage = ir.Usage(prompt_tokens=u.prompt, completion_tokens=u.output,
                             cached_tokens=u.cached, reasoning_tokens=u.reasoning,
                             reasoning_estimated=u.estimated,
                             cache_creation_tokens=u.cache_creation)
        ctx.cost = self.cost.cost(model_key, u.prompt, u.output, u.cached)
        ctx.cache_hit = u.cached > 0
        ctx.metadata["cache_savings"] = self._cache_savings(model_key, ctx.usage)


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
    tps = ((u.completion_tokens / stream_secs)
           if u and stream_secs > 0.05 else 0.0)
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
        cache_savings=ctx.metadata.get("cache_savings", 0.0),
        attempts=[{"deployment": a.deployment, "provider": a.provider,
                   "key": a.provider_key_label, "status": a.status,
                   "latency_ms": a.latency_ms} for a in ctx.attempts],
        request_body=ctx.metadata.get("request_body"),
        response_body=ctx.metadata.get("response_body"),
    )
    return evt


def encode_json(obj: Any) -> bytes:
    return orjson.dumps(obj)


def _build_url(adapter, dep: Deployment, key: ProviderKeyRef,
               stream: bool, kind: str) -> str:
    """Build the upstream URL, appending the API key for providers that require it
    in the querystring (e.g. Gemini) rather than headers."""
    url = adapter.build_url(dep.provider.base_url, dep.model_id, stream, kind)
    if dep.provider.provider_type == "gemini" and url.endswith(("?key=", "&key=")):
        url += key.secret
    return url


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
