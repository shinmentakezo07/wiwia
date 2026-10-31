"""IFM adapter: MBZUAI Institute of Foundation Models (api.ifm.ai).

IFM's hosted API is an OpenAI Chat Completions endpoint (K2 Horizon and
Jais 3, 512K context), so this adapter specializes
:class:`~wiwi.providers.openai_adapter.OpenAIAdapter` and only carries the
request-shaping the IFM chat templates require. The rules themselves are
documented — with citations — in :mod:`wiwi.providers.ifm`; in short:

- every assistant history message replays ``reasoning_content`` (``""``
  included), or turn 2 is rejected with a 400;
- message content is a plain string, text only (parts arrays are flattened
  and non-text parts are reported);
- ``reasoning_effort`` is sent through ``chat_template_kwargs`` and clamped
  to ``low`` / ``medium`` / ``high``;
- ``X-Session-ID`` pins a conversation to one worker (best-effort latency
  optimisation);
- ``stop`` is capped at four sequences, and ``top_k`` is forwarded because
  the upstream honours it (``supports_top_k``).

Reasoning traces arrive on ``reasoning_content`` (with a ``reasoning``
mirror) in both the non-streaming body and stream deltas, which the base
adapter already decodes into ``ThinkingPart`` / ``ThinkingDelta``.
"""

from __future__ import annotations

from typing import Any

import structlog

from wiwi.ir import types as ir
from wiwi.providers.base import ProviderKeyRef
from wiwi.providers.ifm import (
    IFM_MAX_STOP,
    conversation_session_id,
    normalize_reasoning_effort,
    shape_messages,
    unsupported_platform_params,
)
from wiwi.providers.openai_adapter import OpenAIAdapter

log = structlog.get_logger("wiwi.ifm_adapter")


class IFMAdapter(OpenAIAdapter):
    """OpenAI Chat Completions wire format against https://api.ifm.ai/v1."""

    provider_type = "ifm"
    # IFM is vLLM-backed and accepts top_k as a native sampling parameter.
    supports_top_k = True

    def __init__(self) -> None:
        super().__init__()
        # Set by encode_request() and read by headers(): the gateway always
        # encodes before it builds headers, on every path (non-streaming,
        # streaming, 401-refresh retry), and a fresh instance per request
        # means no other request can overwrite it.
        self._session_id: str | None = None

    def reset(self) -> None:
        super().reset()
        self._session_id = None

    def realtime_url(self, base_url: str) -> str | None:
        """IFM exposes no Realtime-style WebSocket surface."""
        return None

    def headers(self, key: ProviderKeyRef) -> dict[str, str]:
        h = super().headers(key)
        if self._session_id:
            # Session affinity (docs.ifm.ai/#/session-routing): same value ->
            # same worker -> the prefix's KV cache is reused instead of
            # recomputed. Purely a latency optimisation; omitting it is legal.
            h["X-Session-ID"] = self._session_id
        return h

    def encode_request(self, req: ir.Request, model_id: str,
                       deployment_params: dict[str, Any]) -> dict[str, Any]:
        # Force the "native OpenAI-shaped" encoding path so per-message
        # reasoning_content is emitted for the turns that have a trace; the
        # turns that do not are padded below, because IFM validates the
        # field's presence rather than its content.
        params = dict(deployment_params)
        params["provider_type"] = "ifm"
        body = super().encode_request(req, model_id, params)

        self._apply_reasoning_effort(req, body)

        # OpenAI-platform parameters IFM does not document. They ride in from
        # client extras via the base adapter's `_STANDARD` set; an unknown
        # top-level field is at best ignored and at worst a 400, so it goes
        # with a caller-visible warning (same policy as NIM).
        for key in unsupported_platform_params():
            if key in body:
                self.warn(f"'{key}' is not supported by IFM and was dropped")
                body.pop(key, None)

        # `stop` is capped at four sequences upstream; truncating keeps the
        # first four the caller listed (the ones a generation is most likely
        # to hit) instead of failing the whole request.
        stop = body.get("stop")
        if isinstance(stop, list) and len(stop) > IFM_MAX_STOP:
            dropped = len(stop) - IFM_MAX_STOP
            body["stop"] = stop[:IFM_MAX_STOP]
            self.warn(
                f"IFM accepts at most {IFM_MAX_STOP} stop sequences; "
                f"{dropped} were dropped")

        messages = body.get("messages")
        if isinstance(messages, list):
            shape_messages(messages, self.warn)
            self._session_id = conversation_session_id(
                messages, model_id, deployment_params.get("session_id"))
        return body

    def _apply_reasoning_effort(self, req: ir.Request,
                                body: dict[str, Any]) -> None:
        """Move the resolved effort into ``chat_template_kwargs``.

        The base adapter puts ``reasoning_effort`` at the body top level when
        it believes it is talking to native OpenAI, which it does for an
        unknown provider type. IFM documents the knob as a chat-template
        argument, so the top-level spelling is removed rather than left to be
        ignored or rejected.
        """
        body.pop("reasoning_effort", None)
        raw = req.gen_params.effective_reasoning_effort()
        effort = normalize_reasoning_effort(raw)
        if effort is not None:
            chat = body.get("chat_template_kwargs")
            if not isinstance(chat, dict):
                chat = {}
                body["chat_template_kwargs"] = chat
            # The caller's level outranks a deployment-level default; other
            # template kwargs (a deployment's own tuning) are preserved.
            chat["reasoning_effort"] = effort
            return
        if raw == "none":
            self.warn(
                "IFM always reasons and has no documented switch to disable "
                "thinking; the 'none' effort was dropped")
        elif isinstance(raw, str) and raw:
            self.warn(
                f"reasoning effort '{raw}' is not an IFM level "
                f"(low/medium/high) and was dropped")
