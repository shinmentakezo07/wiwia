"""B.AI adapter: unified LLM gateway (api.b.ai) over the OpenAI Chat wire format.

B.AI (docs.b.ai/llmservice) exposes one API key across three protocols —
OpenAI Chat Completions (``/v1/chat/completions``), OpenAI Responses
(``/v1/responses``), and Anthropic Messages (``/v1/messages``). wiwi always
speaks Chat Completions to it, so this adapter is a specialization of
:class:`~wiwi.providers.openai_adapter.OpenAIAdapter`.

B.AI quirks handled here:

- Like most OpenAI-compatible gateways, B.AI must not be sent
  ``reasoning_content`` on *history* assistant messages (native OpenAI
  accepts it; compatible gateways reject the unrecognized field). Routing
  through the ``openai-compatible`` encoding path handles that.
- Reasoning effort IS forwarded: B.AI hosts reasoning models (its usage
  reports ``reasoning_tokens`` and streams ``reasoning_content`` deltas),
  so ``reasoning_effort`` (and the Anthropic-dialect ``thinking_budget``
  already normalized to an effort level by the IR) maps to the standard
  Chat Completions ``reasoning_effort`` parameter.

DeepSeek quirks (models whose id contains ``deepseek``, e.g.
``deepseek-v4-pro``, ``deepseek-reasoner``) — per DeepSeek's thinking-mode
docs (api-docs.deepseek.com/guides/thinking_mode):

- Thinking mode is ON by default with effort ``high``. ``reasoning_effort``
  is accepted (``low``/``medium``/``high``/``xhigh``/``max``; the server maps
  ``medium``→``high`` internally). Effort ``none`` (Anthropic
  ``thinking.type=disabled`` / Responses ``reasoning.effort=none``) must be
  expressed as ``{"thinking": {"type": "disabled"}}`` instead.
- **Tool-call round-trip (the common 400):** when the request carries
  ``tools``, the ``reasoning_content`` of all previous assistant turns must
  be passed back — even for turns where the model made no tool call — or the
  API rejects the request with
  ``The "reasoning_content" in the thinking mode must be passed back to the API``.
  So for DeepSeek + tools, history reasoning is emitted per-message (and
  assistant messages that carry none get an empty string, the router-side
  re-attach pattern used by claude-code-router / CodeRouter fixes).
- Without ``tools``, history ``reasoning_content`` is ignored by the API (and
  older R1-era deployments 400'd on it), so it is stripped as for any other
  OpenAI-compatible gateway.
- ``temperature``/``top_p``/``presence_penalty``/``frequency_penalty`` are
  silently ignored in thinking mode (no error) — no special handling needed.
"""

from __future__ import annotations

from typing import Any

from wiwi.ir import types as ir
from wiwi.providers.openai_adapter import OpenAIAdapter


def _is_deepseek(model_id: str) -> bool:
    """True for DeepSeek models (deepseek-reasoner, deepseek-v3.x/v4, R1 ids,
    optionally vendor-prefixed like ``deepseek/deepseek-v4``)."""
    return "deepseek" in model_id.lower()


class BAIAdapter(OpenAIAdapter):
    """OpenAI Chat Completions wire format against https://api.b.ai/v1."""

    provider_type = "bai"

    def encode_request(self, req: ir.Request, model_id: str,
                       deployment_params: dict[str, Any]) -> dict[str, Any]:
        deepseek = _is_deepseek(model_id)
        effort = req.gen_params.effective_reasoning_effort()
        # Per-message history reasoning is wanted exactly when DeepSeek's
        # thinking-mode round-trip rule requires it: DeepSeek model + tools
        # + thinking not explicitly disabled. Everything else takes the
        # strict compatible-gateway path (history reasoning stripped).
        deepseek_roundtrip = deepseek and bool(req.tools) and effort != "none"
        params = dict(deployment_params)
        params["provider_type"] = "openai" if deepseek_roundtrip else "openai-compatible"
        body = super().encode_request(req, model_id, params)
        # "none" is NOT a valid reasoning_effort value on any OpenAI-compatible
        # surface (B.AI is not native OpenAI). Whether DeepSeek or not, the
        # "disable thinking" intent must use B.AI's thinking toggle rather than
        # forwarding an unsupported "none" string (which would 400 upstream).
        # A disabled state never triggers the round-trip rule (which requires
        # effort != "none"), so it always returns.
        if effort == "none":
            body.pop("reasoning_effort", None)
            body["thinking"] = {"type": "disabled"}
            return body
        if not deepseek:
            # Plain B.AI model: named effort levels accepted, nothing else needed.
            if effort:
                body["reasoning_effort"] = effort
            return body
        # DeepSeek: normalize the reasoning controls.
        if effort:
            body["reasoning_effort"] = effort
        if deepseek_roundtrip:
            # The thinking-mode tool round-trip: EVERY history assistant
            # message must carry reasoning_content once tools are present,
            # even turns without thinking. Clients that stripped the field
            # (the widespread 400) get an empty string — presence of the
            # field is what the API validates; content is best-effort.
            for m in body["messages"]:
                if m.get("role") == "assistant" and "reasoning_content" not in m:
                    m["reasoning_content"] = ""
        return body
