"""Round-26 regression tests: cache-token accounting gaps.

Regression targets (found during the cache-layer / durable-stream-recovery
round, see AUDIT.md addendum):

- ``Gateway._complete_via_stream`` dropped ``UsageFinal.cache_creation`` when
  folding stream deltas into an ``AssistantTurn`` for force_stream providers
  (Cline / WorkBuddy). Anthropic cache-write tokens therefore never reached
  pricing or the request log for those providers.
- ``build_log_event`` never persisted ``cache_creation_tokens``: the LogEvent,
  the ``request_logs`` column, and every aggregate derived from them had no
  cache-write visibility even though the cost engine priced it.
"""

from __future__ import annotations

import httpx
import respx

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    WiwiConfig,
)
from wiwi.core.context import RequestContext
from wiwi.core.gateway import Gateway, build_log_event
from wiwi.cost.pricing import CostEngine
from wiwi.ir import types as ir
from wiwi.providers.base import ProviderKeyRef
from wiwi.router.router import Deployment, ProviderAccount, Router
from wiwi.streaming import deltas as dl


def _req(text: str = "hi") -> ir.Request:
    return ir.Request(model="m", messages=[ir.Message(role="user",
                                                      parts=[ir.TextPart(text)])])


class _ScriptedForceStreamAdapter:
    """Minimal force_stream adapter: one scripted delta per SSE event."""

    force_stream = True

    def __init__(self, script: list[dl.IRStreamDelta]) -> None:
        self._script = list(script)

    def build_url(self, base_url: str, model_id: str, stream: bool) -> str:
        return base_url.rstrip("/") + "/chat/completions"

    def encode_request(self, ir_req, model_id, params):
        return {}

    def headers(self, key) -> dict[str, str]:
        return {}

    def decode_stream_event(self, event: str, data: str):
        if data == "[DONE]":
            return []
        return [self._script.pop(0)] if self._script else []


@respx.mock
async def test_complete_via_stream_preserves_cache_creation_tokens():
    """UsageFinal.cache_creation must survive the stream→turn fold."""
    script: list[dl.IRStreamDelta] = [
        dl.TextDelta("hel"), dl.TextDelta("lo"),
        dl.UsageFinal(prompt=10, cached=2, output=5, cache_creation=77),
        dl.Finish("stop"),
    ]
    cfg = WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               base_url="https://force.example/v1",
                               keys=[KeyDef(label="k1", key="sk-1")])],
        model_list=[ModelEntry(model_name="gpt-x",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-x"))],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
    )
    gw = Gateway(Router(cfg), CostEngine())
    try:
        body = b"data: 1\n\n" * 4 + b"data: [DONE]\n\n"
        respx.post("https://force.example/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=body))
        acct = ProviderAccount(name="p1", provider_type="openai",
                               base_url="https://force.example/v1")
        dep = Deployment(group="gpt-x", provider=acct, model_id="gpt-x")
        key = ProviderKeyRef(label="k1", secret="sk-1")
        ctx = RequestContext(surface="chat", ir_req=_req("hi"), group="gpt-x")
        turn = await gw._complete_via_stream(dep, key, ctx,
                                             _ScriptedForceStreamAdapter(script))
        assert turn.text == "hello"
        assert turn.usage.cache_creation_tokens == 77
        assert turn.usage.cached_tokens == 2
        evt = build_log_event(ctx)
        assert evt.tok_cache_creation == 77
    finally:
        await gw.aclose()


def test_build_log_event_carries_cache_creation():
    ctx = RequestContext(surface="chat", ir_req=_req(), group="g",
                         request_id="reqx")
    ctx.usage = ir.Usage(prompt_tokens=50, completion_tokens=10,
                         cached_tokens=5, cache_creation_tokens=88)
    evt = build_log_event(ctx)
    assert evt.tok_cache_creation == 88
    assert evt.tok_cached == 5
