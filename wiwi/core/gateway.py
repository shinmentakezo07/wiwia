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
import structlog

from wiwi.core.context import RequestContext
from wiwi.cost.pricing import CostEngine, estimate_tokens_async
from wiwi.ir import types as ir
from wiwi.logging_core.events import LogEvent
from wiwi.providers.base import (
    ProviderKeyRef,
    WiwiError,
    error_from_provider_status,
    status_for_key_pool,
)
from wiwi.providers.registry import fresh_adapter
from wiwi.router.router import Deployment, Router, execute_with_retries
from wiwi.streaming import deltas as dl
from wiwi.streaming.coalesce import DeltaCoalescer
from wiwi.streaming.loopdetect import LoopDetector
from wiwi.streaming.resume import StreamTape, build_continuation_messages
from wiwi.streaming.sse import LineSSEParser, SSEEvent

log = structlog.get_logger(__name__)

# How long the consumer waits, after setting `ctx.cancel`, for the pump to
# notice the flag and release the upstream connection itself before falling
# back to cancelling it. Long enough to cover a socket teardown, short enough
# that a client disconnect is not held up.
_PUMP_CANCEL_GRACE_S = 1.0


class Gateway:
    def __init__(self, router: Router, cost_engine: CostEngine,
                 drop_params: bool = True):
        self.router = router
        self.cost = cost_engine
        self.drop_params = drop_params
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0), http2=True)
        # Optional on-demand Cline token refresh hook. Set by the app at
        # startup when a Cline provider is configured. Signature:
        #   hook(provider_name, key_label) -> bool
        # Returns True when the access token was rotated and the caller
        # should retry; False when no rotation happened (caller surfaces
        # the original 401).
        self._on_demand_cline_refresh = None
        # On-demand refresh hooks per provider type (e.g. "workbuddy"), set
        # by the app at startup. Cline keeps its legacy attribute above for
        # backward compatibility with existing wiring/tests.
        self._on_demand_refresh_hooks: dict[str, Any] = {}

    def _resolve_refresh_hook(self, dep: Deployment):
        """Return the on-demand 401-refresh hook for a deployment's provider.

        Cline's hook wins when set (legacy seam); otherwise the
        provider-type-keyed registry supplies the hook.
        """
        if dep.provider.provider_type == "cline":
            return self._on_demand_cline_refresh
        return self._on_demand_refresh_hooks.get(dep.provider.provider_type)

    async def aclose(self) -> None:
        await self._client.aclose()

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
        # Private adapter, same rationale as the stream pump below: adapters
        # stash per-request decode state (e.g. NIM's tool schemas and argument
        # aliases, set by `set_tool_context` after encode_request) that is read
        # back after the request await. A concurrent acquisition of the shared
        # singleton resets that state in between.
        adapter = fresh_adapter(dep.provider.provider_type)
        # Streaming-only upstream (Cline): the upstream has no non-streaming
        # mode, so use the streaming pump and reassemble the SSE deltas into
        # an AssistantTurn for the non-streaming caller.
        if getattr(adapter, "force_stream", False):
            return await self._complete_via_stream(dep, key, ctx, adapter)
        ctx.deployment = dep
        ctx.provider_key = key
        params: dict[str, Any] = {"max_tokens": dep.max_tokens,
                                  "extra_body": dict(dep.extra_body),
                                  "drop_params": self.drop_params,
                                  "provider_type": dep.provider.provider_type}
        url = _build_url(adapter, dep, key, False)
        body = adapter.encode_request(ctx.ir_req, dep.model_id, params)
        if hasattr(adapter, "set_tool_context"):
            adapter.set_tool_context(body)
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
            # On-demand token refresh: 401 from an OAuth-backed provider
            # (Cline, WorkBuddy) usually means the access token was rotated
            # upstream. Refresh and retry once before surfacing the error.
            refresh_hook = self._resolve_refresh_hook(dep)
            if resp.status_code == 401 and refresh_hook is not None:
                rotated = await refresh_hook(dep.provider.name, key.label)
                if rotated:
                    # The on-demand refresh rotated every pool key's
                    # secret; rebuild the headers so the retry uses the
                    # freshly-issued access token instead of the snapshot
                    # held by this request's ProviderKeyRef.
                    live_key = dep.provider.get_key(key.label)
                    retry_key = (ProviderKeyRef(label=key.label,
                                                secret=live_key.secret)
                                 if live_key is not None else key)
                    retry_headers = {**adapter.headers(retry_key),
                                     **dep.provider.extra_headers,
                                     **dep.extra_headers}
                    try:
                        retry_resp = await self._client.post(
                            url, json=body, headers=retry_headers,
                            timeout=dep.timeout or dep.provider.timeout_s)
                    except httpx.TransportError as e:
                        raise WiwiError(502, "api_connection_error",
                                        f"upstream {type(e).__name__}",
                                        retryable=True) from e
                    if retry_resp.status_code == 200:
                        ctx.note_attempt(f"{dep.group}/{dep.model_id}",
                                         dep.provider.name, key.label,
                                         "ok_after_refresh",
                                         int((time.monotonic() - t0) * 1000))
                        dep.latencies.append(int((time.monotonic() - t0) * 1000))
                        turn = adapter.decode_response(retry_resp.status_code,
                                                      retry_resp.content)
                        self._price(ctx, dep, turn.usage)
                        return turn
            raise err
        ctx.note_attempt(f"{dep.group}/{dep.model_id}", dep.provider.name, key.label,
                         "ok", latency)
        dep.latencies.append(latency)
        turn = adapter.decode_response(resp.status_code, resp.content)
        self._price(ctx, dep, turn.usage)
        return turn

    async def _complete_via_stream(self, dep: Deployment, key: ProviderKeyRef,
                                   ctx: RequestContext, adapter) -> ir.AssistantTurn:
        """Reassemble a streaming-only upstream (force_stream=True) into a
        non-streaming AssistantTurn.

        Opens a streaming HTTP request, pumps SSE through the adapter's
        ``decode_stream_event``, folds the deltas into an AssistantTurn, and
        prices the result — the same outcome ``_call_once`` would produce
        for a normal JSON upstream.  Mirrors ``_pump_once``'s connect/error
        handling so retryable failures propagate to ``execute_with_retries``
        the same way.
        """
        import json

        from wiwi.ir import types as ir
        from wiwi.streaming.partial_json import _repair_truncated_json

        ctx.deployment = dep
        ctx.provider_key = key
        params: dict[str, Any] = {"max_tokens": dep.max_tokens,
                                  "extra_body": dict(dep.extra_body),
                                  "drop_params": self.drop_params,
                                  "provider_type": dep.provider.provider_type}
        url = _build_url(adapter, dep, key, True)
        body = adapter.encode_request(ctx.ir_req, dep.model_id, params)
        if hasattr(adapter, "set_tool_context"):
            adapter.set_tool_context(body)
        headers = {**adapter.headers(key), **dep.provider.extra_headers,
                   **dep.extra_headers}
        t0 = time.monotonic()
        try:
            resp_cm = self._client.stream("POST", url, json=body, headers=headers,
                                          timeout=dep.timeout or dep.provider.timeout_s)
            resp = await resp_cm.__aenter__()
        except httpx.TransportError as e:
            ctx.note_attempt(f"{dep.group}/{dep.model_id}", dep.provider.name, key.label,
                             type(e).__name__, int((time.monotonic() - t0) * 1000))
            raise WiwiError(504 if "Timeout" in type(e).__name__ else 502,
                            "timeout" if "Timeout" in type(e).__name__
                            else "api_connection_error",
                            f"upstream {type(e).__name__}", retryable=True) from e
        if resp.status_code != 200:
            raw = await resp.aread()
            await resp_cm.__aexit__(None, None, None)
            ctx.note_attempt(f"{dep.group}/{dep.model_id}", dep.provider.name, key.label,
                             f"http_{resp.status_code}",
                             int((time.monotonic() - t0) * 1000))
            err = error_from_provider_status(resp.status_code,
                                             raw.decode(errors="replace"),
                                             dep.provider.name)
            ra = _parse_retry_after(resp.headers.get("retry-after"))
            if ra is not None:
                err.retry_after = ra
            # On-demand token refresh on a streaming connect: 401 means
            # the access token was rotated upstream. Reconnect once with
            # the freshly-issued token before surfacing the error.
            refresh_hook = self._resolve_refresh_hook(dep)
            if resp.status_code == 401 and refresh_hook is not None:
                rotated = await refresh_hook(dep.provider.name, key.label)
                if rotated:
                    # Rebuild headers from the live (post-refresh) key
                    # secret so the reconnect carries the fresh token.
                    live_key = dep.provider.get_key(key.label)
                    retry_key = (ProviderKeyRef(label=key.label,
                                                secret=live_key.secret)
                                 if live_key is not None else key)
                    retry_headers = {**adapter.headers(retry_key),
                                     **dep.provider.extra_headers,
                                     **dep.extra_headers}
                    try:
                        retry_cm = self._client.stream(
                            "POST", url, json=body, headers=retry_headers,
                            timeout=dep.timeout or dep.provider.timeout_s)
                        retry_resp = await retry_cm.__aenter__()
                    except httpx.TransportError as e:
                        raise WiwiError(502, "api_connection_error",
                                        f"upstream {type(e).__name__}",
                                        retryable=True) from e
                    if retry_resp.status_code == 200:
                        # Swap the failed response for the fresh one and
                        # fall through to the pump loop below.
                        resp = retry_resp
                        resp_cm = retry_cm
                    else:
                        await retry_cm.__aexit__(None, None, None)
                        raise err
                else:
                    raise err
            else:
                raise err
        # Connection OK — pump the SSE stream into an AssistantTurn.
        try:
            parser = LineSSEParser()
            text = ""
            thinking = ""
            tool_calls: list[ir.ToolUsePart] = []
            open_calls: dict[int, ir.ToolUsePart] = {}
            arg_bufs: dict[int, str] = {}
            usage = ir.Usage()
            stop_reason: ir.StopReason = "stop"

            def _apply_event(evt: SSEEvent) -> None:
                nonlocal text, thinking, usage, stop_reason
                for d in adapter.decode_stream_event(evt.event, evt.data):
                    if isinstance(d, dl.TextDelta):
                        text += d.text
                    elif isinstance(d, dl.ThinkingDelta):
                        thinking += d.text
                    elif isinstance(d, dl.ToolCallOpen):
                        open_calls[d.index] = ir.ToolUsePart(
                            id=d.id, name=d.name, args={}, raw_args="")
                        arg_bufs[d.index] = ""
                    elif isinstance(d, dl.ToolCallArgsDelta):
                        if d.index in arg_bufs:
                            arg_bufs[d.index] += d.args_fragment
                    elif isinstance(d, dl.ToolCallClose):
                        tc = open_calls.pop(d.index, None)
                        raw = arg_bufs.pop(d.index, "")
                        if tc is not None:
                            if raw:
                                try:
                                    tc.args = json.loads(_repair_truncated_json(raw))
                                except (json.JSONDecodeError, ValueError):
                                    tc.raw_args = raw
                            else:
                                tc.raw_args = raw
                            tool_calls.append(tc)
                    elif isinstance(d, dl.UsageFinal):
                        usage = ir.Usage(
                            prompt_tokens=d.prompt,
                            completion_tokens=d.output,
                            cached_tokens=d.cached,
                            reasoning_tokens=d.reasoning,
                        )
                    elif isinstance(d, dl.Finish):
                        stop_reason = d.stop_reason
                    elif isinstance(d, dl.StreamError):
                        raise WiwiError(502, "api_error", d.message,
                                        retryable=d.kind != "status")

            line_iter = resp.aiter_lines().__aiter__()
            while True:
                try:
                    line = await asyncio.wait_for(
                        line_iter.__anext__(),
                        timeout=self.router.settings.stream_idle_timeout_s)
                except TimeoutError:
                    raise WiwiError(504, "timeout",
                                    f"upstream idle >{self.router.settings.stream_idle_timeout_s:.0f}s",
                                    retryable=True)
                except StopAsyncIteration:
                    break
                evt = parser.feed_line(line)
                if evt is not None:
                    _apply_event(evt)
            # Flush any frame buffered without a trailing blank line
            # (DeepSeek/B.A.I close with "data: [DONE]\n"). Without this the
            # final content delta is silently dropped.
            _flushed = parser.flush()
            if _flushed is not None:
                _apply_event(_flushed)
            # Flush still-open tool calls (stream ended mid-tool-call).
            for idx in sorted(open_calls):
                tc = open_calls[idx]
                raw = arg_bufs.get(idx, "")
                if raw:
                    try:
                        tc.args = json.loads(_repair_truncated_json(raw))
                    except (json.JSONDecodeError, ValueError):
                        tc.raw_args = raw
                else:
                    tc.raw_args = raw
                tool_calls.append(tc)
        finally:
            await resp_cm.__aexit__(None, None, None)
        latency = int((time.monotonic() - t0) * 1000)
        ctx.note_attempt(f"{dep.group}/{dep.model_id}", dep.provider.name, key.label,
                         "ok", latency)
        dep.latencies.append(latency)
        turn = ir.AssistantTurn(text=text, tool_calls=tool_calls,
                                stop_reason=stop_reason, usage=usage)
        if thinking:
            turn.thinking.append(ir.ThinkingPart(text=thinking))
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
                # Do NOT cancel the pump here: it has already recorded the
                # error and is on its way out, but it may still be closing the
                # upstream response (`ready` is set *before* that await).
                # Cancelling mid-close leaves the httpx response un-released
                # and leaks the pooled connection — on every retried 429/500/
                # 401 connect.  Await it instead so cleanup completes.
                try:
                    await asyncio.wait_for(pump_task, timeout=5.0)
                except asyncio.CancelledError:
                    # The client went away while we waited for cleanup.
                    # `wait_for` has already cancelled the pump; re-raise so
                    # the disconnect propagates. Suppressing it here (as
                    # suppress(BaseException) did) reported the upstream error
                    # to a caller that no longer exists and hid the
                    # cancellation from the ASGI disconnect handler.
                    raise
                except Exception as e:  # noqa: BLE001
                    # A wedged close (TimeoutError) or a residual teardown
                    # error. The result is already known to be a failure, so
                    # neither carries information the caller needs — but
                    # neither should pass entirely unremarked.
                    log.debug("stream pump cleanup did not finish cleanly",
                              error=type(e).__name__, detail=str(e))
                raise err_box[0]
            return pump_task

        # Streaming: hold back the key credit until the pump reports a clean
        # completion. See RequestContext._defer_key_credit / AUDIT #6.
        ctx._defer_key_credit = True
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
                # Skipped when resume is off (the default): the tape is only
                # read by _attempt_resume, so recording it is pure waste.
                if resume_mode != "off":
                    tape.append(d)
                # Mid-stream failover: if the upstream died after content,
                # attempt a resume on a fallback deployment.
                if (isinstance(d, dl.StreamError) and content_flowed
                        and resume_mode != "off" and max_resumes > 0):
                    resumed, new_pump = await self._attempt_resume(ctx, tape, queue)
                    if resumed:
                        # Update the outer pump_task reference so the finally
                        # below cancels the *active* pump (the resume pump),
                        # not the original. Without this, the resume pump
                        # leaks its upstream connection.
                        pump_task = new_pump
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
            # Signal the pump that the consumer is gone *before* cancelling it.
            # The pump polls `ctx.cancel` between upstream chunks so it can stop
            # pulling, run the grace drain (billing the tokens the client did
            # receive), and release the upstream connection cleanly. Without
            # this the pump only ever learns via CancelledError, which skips
            # that path entirely.
            ctx.cancel.set()
            if pump_task and not pump_task.done():
                # Setting the flag is not enough on its own: this is a
                # synchronous `finally`, so cancelling on the next line would
                # deliver CancelledError before the pump is ever rescheduled to
                # observe the flag. Yield first, bounded, so the pump can run
                # its own teardown; cancel only if it doesn't finish.
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(asyncio.shield(pump_task),
                                           timeout=_PUMP_CANCEL_GRACE_S)
            if pump_task and not pump_task.done():
                pump_task.cancel()

    async def _attempt_resume(self, ctx: RequestContext, tape: StreamTape,
                              queue: asyncio.Queue) -> tuple[bool, asyncio.Task | None]:
        """Try to resume a failed stream on a fallback deployment.

        Appends the partial assistant output as a continuation message and
        starts a new pump. Returns ``(success, new_pump_task)``: the caller
        must update its outer ``pump_task`` reference to the returned task,
        otherwise the resume pump leaks its upstream connection when the
        consumer closes (the outer finally only cancels the original task).
        """
        from wiwi.ir import types as ir
        text = tape.replay_text()
        if not text and self.router.settings.stream_resume == "content_only":
            return False, None
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
            return False, None
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
            return False, None
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
            new_pump_task = asyncio.create_task(
                self._pump(dep, ProviderKeyRef(label=key.label, secret=key.secret),
                           resume_ctx, queue, ready, err_box))
            await ready.wait()
            if err_box[0] is None:
                # Connection succeeded — report success so the key's
                # req_count increments and any cooldown is cleared.
                await dep.provider.on_result_locked(key, 200, None)
                # Return the new pump task so the caller updates its outer
                # reference — otherwise the resume pump leaks its connection
                # when the consumer closes (the outer finally only cancels
                # whatever pump_task currently points to).
                return True, new_pump_task
            # Connection failed — feed the key pool and deployment cooldown
            # so a provider that keeps failing on resume cools off.
            status = status_for_key_pool(err_box[0])
            await dep.provider.on_result_locked(key, status, err_box[0].retry_after)
            if status in (408, 500, 502, 503, 504, 529):
                dep.record_fail(self.router.settings.allowed_fails,
                                self.router.settings.cooldown_time)
            new_pump_task.cancel()
        return False, None

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
        # Private adapter: this pump holds the adapter across every await for
        # the whole stream, accumulating per-stream decode state (deferred tool
        # opens, name fragments, open indices). The shared singleton is reset on
        # each acquisition, so a concurrent request that acquires the same
        # provider type mid-stream wipes this stream's state — losing
        # ToolCallOpen/Close. A fresh instance makes ownership exclusive.
        adapter = fresh_adapter(dep.provider.provider_type)
        ctx.deployment = dep
        ctx.provider_key = key
        real_key = dep.provider.get_key(key.label)  # live pool entry for on_result
        params: dict[str, Any] = {"max_tokens": dep.max_tokens,
                                  "extra_body": dict(dep.extra_body),
                                  "drop_params": self.drop_params,
                                  "provider_type": dep.provider.provider_type}
        url = _build_url(adapter, dep, key, True)
        try:
            body = adapter.encode_request(ctx.ir_req, dep.model_id, params)
            if hasattr(adapter, "set_tool_context"):
                adapter.set_tool_context(body)
            headers = {**adapter.headers(key), **dep.provider.extra_headers,
                       **dep.extra_headers}
        except Exception as e:  # noqa: BLE001
            ctx.note_attempt(f"{dep.group}/{dep.model_id}", dep.provider.name,
                             key.label, "encode_error", 0)
            err_box[0] = WiwiError(400, "invalid_request_error",
                                   f"failed to encode request: {e}")
            ready.set()
            return
        t0 = time.monotonic()
        usage_final: dl.UsageFinal | None = None
        finish: dl.Finish | None = None
        text_len = 0
        started = False
        saw_terminal = False
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
                ra = _parse_retry_after(resp.headers.get("retry-after"))
                if ra is not None:
                    err.retry_after = ra
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
            grace_deadline: float | None = None  # monotonic deadline for grace drain
            idle_s = self.router.settings.stream_idle_timeout_s
            loop_limit = (self.router.settings.stream_loop_limit
                          if self.router.settings.stream_loop_detection else 0)
            # O(1) per token: tracks repetition runs for short periods only,
            # rather than rescanning the whole window every token.
            loop_detector = LoopDetector(loop_limit)
            line_iter = resp.aiter_lines().__aiter__()
            closed = False  # True once resp_cm.__aexit__ has been called

            async def _close_upstream() -> None:
                nonlocal closed
                if not closed:
                    closed = True
                    with contextlib.suppress(Exception):
                        await resp_cm.__aexit__(None, None, None)

            async def _apply_delta(deltas: list[dl.IRStreamDelta]) -> bool:
                """Route one decoded event's deltas; returns True = abort pump."""
                nonlocal text_len, usage_final, finish, saw_terminal
                for d in deltas:
                    if isinstance(d, dl.UsageFinal):
                        usage_final = d
                    elif isinstance(d, dl.Finish):
                        finish = d
                    elif isinstance(d, dl.StreamEnd):
                        saw_terminal = True
                        continue
                    else:
                        if isinstance(d, dl.TextDelta):
                            text_len += len(d.text)
                            if loop_detector.feed(d.text):
                                await self._note_stream_failure(
                                    dep, real_key)
                                await self._price_partial(
                                    ctx, dep, usage_final, text_len)
                                await queue.put(dl.StreamError(
                                    f"model loop detected ({loop_limit} "
                                    f"repeating chunks)", "unknown"))
                                await _close_upstream()
                                return True
                        if not client_gone:
                            await queue.put(d)
                return False

            while True:
                try:
                    line = await asyncio.wait_for(line_iter.__anext__(), timeout=idle_s)
                except TimeoutError:
                    await self._note_stream_failure(dep, real_key)
                    await self._price_partial(ctx, dep, usage_final, text_len)
                    await queue.put(dl.StreamError(
                        f"upstream idle >{idle_s:.0f}s between chunks", "timeout"))
                    await _close_upstream()
                    return
                except StopAsyncIteration:
                    break
                if ctx.cancel.is_set():
                    if not client_gone:
                        client_gone = True
                        if grace_drain_s > 0:
                            # Grace drain: keep pumping upstream for billing
                            # accuracy. Set a deadline so we stop after
                            # grace_drain_s seconds, not after one line.
                            grace_deadline = time.monotonic() + grace_drain_s
                            continue
                        await queue.put(dl.StreamError("client disconnected", "cancelled"))
                        break
                    else:
                        # Already in grace drain: stop if the deadline passed.
                        if grace_deadline is not None and time.monotonic() >= grace_deadline:
                            break
                        continue
                evt = parser.feed_line(line)
                if evt is None:
                    continue
                aborted = await _apply_delta(adapter.decode_stream_event(evt.event, evt.data))
                if aborted:
                    return
            # Flush any frame buffered without a trailing blank line
            # (DeepSeek/B.A.I close with "data: [DONE]\n"). Without this the
            # final [DONE] stays buffered, is never seen, and the stream is
            # misread as a mid-stream drop.
            _flushed = parser.flush()
            if _flushed is not None:
                aborted = await _apply_delta(adapter.decode_stream_event(_flushed.event, _flushed.data))
                if aborted:
                    return
            await _close_upstream()
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
                est_prompt = await estimate_tokens_async(_flatten(ctx), dep.model_id)
                est_usage = dl.UsageFinal(
                    prompt=est_prompt,
                    cached=real_usage.cached, reasoning=real_usage.reasoning,
                    output=real_usage.output or max(1, text_len // 4),
                    cache_creation=real_usage.cache_creation, estimated=True)
            self._price_stream(ctx, dep, est_usage)
            if not client_gone:
                await queue.put(est_usage)
                if finish is None and not saw_terminal:
                    # Ended with no finish_reason and no [DONE]: the body just
                    # stopped. That is a truncation whether or not usage
                    # arrived — the client is billed for tokens it never got
                    # a finish_reason for and the key still looks healthy.
                    # [DONE] is the only clean end-of-stream marker that
                    # legitimately replaces a finish_reason (DeepSeek/B.A.I).
                    await self._note_stream_failure(dep, real_key)
                    await queue.put(dl.StreamError(
                        "upstream stream ended without completion", "connection"))
                    return
                if finish is None:
                    # DeepSeek/B.AI and other OpenAI-compatible servers signal
                    # completion purely with [DONE], omitting a trailing
                    # finish_reason/usage chunk. Treat a [DONE]-terminated
                    # stream as a clean "stop" so the client's OpenAI SDK sees
                    # a finish_reason instead of a truncated stream.
                    finish = dl.Finish("stop")
                await queue.put(finish or dl.Finish("stop"))
                await queue.put(dl.StreamEnd())
                # AUDIT #6: credit the key only now that the stream actually
                # completed. `execute_with_retries` used to record on_result(200)
                # at *connect* time, which reset err_count to 0 — so a key that
                # connects and then dies mid-stream never accumulated a
                # retirement streak and kept getting picked first.
                await dep.provider.on_result_locked(
                    real_key, 200, None,
                    failover_mode=self.router.settings.failover_mode,
                    key_max_consecutive_fails=(
                        self.router.settings.key_max_consecutive_fails))
        except asyncio.CancelledError:
            # client went away mid-stream: still release the upstream response,
            # or the pooled socket stays checked out until GC.  Price the
            # partial delivery so the log and virtual-key spend reflect
            # tokens actually consumed before the disconnect.
            if started:
                # Shielded: we are already being cancelled, so an unshielded
                # await would be interrupted immediately and skip billing.
                await asyncio.shield(self._price_partial(
                    ctx, dep, usage_final, text_len))
                await asyncio.shield(asyncio.wait_for(_close_upstream(), timeout=5.0))
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
                await self._note_stream_failure(dep, real_key)
                await self._price_partial(ctx, dep, usage_final, text_len)
                await queue.put(dl.StreamError(str(e),
                                               "timeout" if "Timeout" in type(e).__name__
                                               else "connection"))
            await _close_upstream()

    async def _note_stream_failure(self, dep: Deployment, real_key) -> None:
        """Mid-stream failures carry no HTTP status; feed deployment cooldowns
        and the key pool so a provider that keeps dying mid-stream cools off.

        Routing through ``on_result_locked`` (rather than bumping
        ``err_count`` directly) is what actually rotates traffic: it applies a
        cooldown window so the next ``pick_key`` skips this key, and retires
        it outright once it crosses ``key_max_consecutive_fails``.  Streaming
        attempts already recorded ``on_result(200)`` at connect time, so
        without the cooldown here a key could die mid-stream indefinitely and
        still be picked first on every subsequent request.
        """
        dep.record_fail(self.router.settings.allowed_fails,
                        self.router.settings.cooldown_time)
        if real_key is None:
            return
        # In "standard" failover mode `on_result` handles only 429/401/403 and
        # silently discards a 5xx, so the penalty below would vanish entirely.
        # Count it here for that mode only: in "any_error" (default)
        # `on_result` already increments err_count, and doing both would
        # double-count and retire keys at half the configured threshold.
        if self.router.settings.failover_mode == "standard":
            real_key.err_count += 1
        await dep.provider.on_result_locked(
            real_key, 502, None,
            failover_mode=self.router.settings.failover_mode,
            key_max_consecutive_fails=self.router.settings.key_max_consecutive_fails)

    async def _price_partial(self, ctx: RequestContext, dep: Deployment,
                             usage_final: dl.UsageFinal | None,
                             text_len: int) -> None:
        """Price what was delivered even when the stream failed, so virtual-key
        spend reflects tokens actually consumed.

        Async because the estimator runs tiktoken, which blocks; this is called
        from the stream pump, so the sync variant stalled the event loop for
        every concurrent request (AUDIT #33).
        """
        u = usage_final or dl.UsageFinal()
        if u.prompt == 0:
            u = dl.UsageFinal(
                prompt=await estimate_tokens_async(_flatten(ctx), dep.model_id),
                cached=u.cached, reasoning=u.reasoning,
                output=u.output or max(1, text_len // 4),
                cache_creation=u.cache_creation, estimated=True)
        self._price_stream(ctx, dep, u)

    def _price(self, ctx: RequestContext, dep: Deployment, u: ir.Usage) -> None:
        model_key = f"{dep.provider.provider_type}/{dep.model_id}"
        ctx.usage = u
        includes_cached = dep.provider.provider_type != "anthropic"
        state = self.cost.cost_with_status(
            model_key, u.prompt_tokens, u.completion_tokens, u.cached_tokens,
            u.cache_creation_tokens, includes_cached)
        ctx.cost = state.cost
        ctx.cache_hit = u.cached_tokens > 0
        ctx.metadata["cache_savings"] = self._cache_savings(model_key, u)
        if state.unpriced:
            ctx.metadata["unpriced_model"] = True
            ctx.metadata["unpriced_model_id"] = model_key

    def _cache_savings(self, model_key: str, u: ir.Usage) -> float:
        """Dollars saved by provider-side prompt caching at this model's rates."""
        p = self.cost._lookup(model_key)
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
        includes_cached = dep.provider.provider_type != "anthropic"
        state = self.cost.cost_with_status(
            model_key, u.prompt, u.output, u.cached, u.cache_creation,
            includes_cached)
        ctx.cost = state.cost
        ctx.cache_hit = u.cached > 0
        ctx.metadata["cache_savings"] = self._cache_savings(model_key, ctx.usage)
        if state.unpriced:
            ctx.metadata["unpriced_model"] = True
            ctx.metadata["unpriced_model_id"] = model_key


def _flatten(ctx: RequestContext) -> str:
    out = []
    for m in ctx.ir_req.messages:
        for p in m.parts:
            if isinstance(p, ir.TextPart):
                out.append(p.text)
            elif isinstance(p, ir.ToolResultPart):
                out.append(p.content)
            elif isinstance(p, ir.ThinkingPart):
                out.append(p.text)
            elif isinstance(p, ir.ToolUsePart):
                # Include the tool name and JSON-serialized args so tool-use
                # turns contribute to the prompt-token estimate.
                out.append(p.name)
                out.append(p.raw_args or orjson.dumps(p.args).decode())
    return " ".join(out)


def build_log_event(ctx: RequestContext) -> LogEvent:
    latency_ms = (time.monotonic() - ctx.started) * 1000
    ttft = ((ctx.first_token_at - ctx.started) * 1000
            if ctx.first_token_at else 0.0)
    stream_secs = ((ctx.last_token_at - ctx.first_token_at)
                   if ctx.first_token_at and ctx.last_token_at else 0.0)
    u = ctx.usage
    # Throughput (output tokens/sec) — like OpenRouter's "throughput" metric:
    # for streaming, generation-phase speed (completion_tokens / stream_secs);
    # for non-streaming or streams too short to time meaningfully, fall back
    # to total round-trip latency so throughput is always reported when we
    # have output tokens, not just for streaming requests.
    tps = 0.0
    if u and u.completion_tokens > 0:
        if stream_secs > 0.05:
            tps = u.completion_tokens / stream_secs
        elif latency_ms > 50:
            tps = u.completion_tokens / (latency_ms / 1000)
    auth = ctx.auth
    evt = LogEvent(
        stream="request", ts=time.time(), request_id=ctx.request_id,
        surface=ctx.surface, key_alias=getattr(auth, "alias", ""),
        key_id=getattr(auth, "key_id", ""),
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



def _build_url(adapter, dep: Deployment, key: ProviderKeyRef, stream: bool) -> str:
    """Build the upstream URL, appending the API key for providers that require it
    in the querystring (e.g. Gemini) rather than headers. Adapters may declare
    ``build_url_for_key(base_url, model_id, stream, key)`` when the credential
    itself routes the URL (e.g. WorkBuddy CN vs global account domains)."""
    build = getattr(adapter, "build_url_for_key", None)
    if build is not None:
        return build(dep.provider.base_url, dep.model_id, stream, key)
    url = adapter.build_url(dep.provider.base_url, dep.model_id, stream)
    if dep.provider.provider_type == "gemini" and url.endswith(("?key=", "&key=")):
        url += key.secret
    return url


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    # RFC 7231 also allows an HTTP-date (e.g. "Wed, 21 Oct 2026 07:28:00 GMT").
    # Parse it and compute seconds from now; clamp to >= 0.
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(value)
        if dt is not None:
            delta = dt.timestamp() - time.time()
            return max(0.0, delta)
    except (TypeError, ValueError):
        pass
    return None


