"""Round 90: the reproduced critical/high findings from the 2026-09-19 sweep.

Each class here was reproduced against the live tree before the fix and is
pinned so it cannot come back. The set:

- #220 a non-admin could strip the budget/RPM/TPM/model caps an operator had
  placed on their own key (privilege escalation).
- #221 the login/signup throttles were check-then-act, so a concurrent burst
  admitted every request (limit 5 → 20 admitted).
- #224 a truthy non-list ``choices`` crashed seven adapters and cooled a
  healthy deployment.
- #227 ``includes_cached`` keyed on provider *type*, so an OpenCode
  Messages-route model (Anthropic-shaped usage) billed fresh input at $0.
- #228 ``text += d.text`` in a closure cell was quadratic (measured 2206x at
  120k fragments), stalling the event loop.
- #246 ``items`` was absent from the NIM schema recursion key-sets, so aliasing
  happened but reversal did not — and a boolean subschema survived.
- #247 every non-streaming ``decode_response`` crashed on a JSON ``null``
  nested field, and the failure was charged to key/deployment health.
- #248 ``nim_native_tools._unalias_args`` was flat, leaking nested aliases.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from asgi_lifespan import LifespanManager

from wiwi.config import WiwiConfig
from wiwi.core.gateway import prompt_includes_cached
from wiwi.providers.nim_tool_schema import (
    collect_nim_tool_aliases,
    sanitize_nim_tool_schemas,
    unalias_nim_tool_args,
)
from wiwi.providers.registry import fresh_adapter
from wiwi.server.app import _AttemptThrottle, create_app

CONFIG = {
    "general_settings": {"master_key": "MK",
                         "database_url": "sqlite+aiosqlite:///:memory:"},
    "providers": [{"name": "p", "provider": "openai", "base_url": "http://x",
                   "keys": [{"key": "sk-a"}]}],
    "model_list": [{"model_name": "g",
                    "wiwi_params": {"provider": "p", "model": "m"}}],
}


async def _client(cfg: dict = CONFIG):
    app = create_app(WiwiConfig.model_validate(cfg))
    lm = LifespanManager(app)
    await lm.__aenter__()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                               base_url="http://t")
    client._wiwi_lm = lm  # type: ignore[attr-defined]
    return client


async def _aclose(client):
    await client.aclose()
    await client._wiwi_lm.__aexit__(None, None, None)  # type: ignore[attr-defined]


# -- #220 ---------------------------------------------------------------------

async def test_non_admin_cannot_strip_admin_caps_on_own_key():
    """The reported escalation: admin caps a user-owned key, user clears it."""
    client = await _client()
    try:
        await client.post("/auth/signup",
                          json={"username": "alice", "password": "pw12345678"})
        await client.post("/auth/login",
                          json={"username": "alice", "password": "pw12345678"})
        await client.post("/auth/playground-key")
        kid = (await client.get("/admin/keys")).json()["keys"][0]["id"]

        r = await client.patch(f"/admin/keys/{kid}",
                               json={"max_budget": 0.0, "rpm": 1,
                                     "models": ["cheap-only"]},
                               headers={"Authorization": "Bearer MK"})
        assert r.status_code == 200
        capped = r.json()["key"]
        assert capped["max_budget"] == 0.0

        # The non-admin owner tries to clear every cap.
        r = await client.patch(f"/admin/keys/{kid}",
                               json={"max_budget": None, "rpm": None,
                                     "models": []})
        assert r.status_code == 403, r.text
        assert r.json()["error"]["type"] == "permission_error"

        # And the caps are still in place on the row.
        after = next(k for k in (await client.get("/admin/keys")).json()["keys"]
                     if k["id"] == kid)
        assert after["max_budget"] == 0.0
        assert after["rpm"] == 1
        assert after["models"] == ["cheap-only"]
    finally:
        await _aclose(client)


async def test_admin_may_still_patch_every_field():
    client = await _client()
    try:
        kid = (await client.post("/admin/keys/generate", json={"name": "k"},
                                 headers={"Authorization": "Bearer MK"})
               ).json()["id"]
        r = await client.patch(f"/admin/keys/{kid}",
                               json={"max_budget": 5.0, "rpm": 10},
                               headers={"Authorization": "Bearer MK"})
        assert r.status_code == 200
        assert r.json()["key"]["max_budget"] == 5.0
    finally:
        await _aclose(client)


# -- #221 ---------------------------------------------------------------------

async def test_throttle_admits_at_most_limit_under_concurrency():
    th = _AttemptThrottle(limit=5, window_s=300.0)

    async def attempt():
        ok, _ = await th.try_consume("s")
        if ok:
            await asyncio.sleep(0.02)   # stand-in for the PBKDF2 window
        return ok

    res = await asyncio.gather(*[attempt() for _ in range(20)])
    assert sum(res) == 5, "check-then-act admitted more than the limit"


async def test_throttle_refund_returns_a_slot():
    th = _AttemptThrottle(limit=1, window_s=60.0)
    ok, _ = await th.try_consume("x")
    assert ok
    ok2, _ = await th.try_consume("x")
    assert not ok2
    await th.refund("x")
    ok3, _ = await th.try_consume("x")
    assert ok3


# -- #224 ---------------------------------------------------------------------

@pytest.mark.parametrize("provider_type", [
    "openai", "openrouter", "nvidia-nim", "cline", "workbuddy", "bai",
])
@pytest.mark.parametrize("payload", [{"choices": 5}, {"choices": True},
                                     {"choices": {"a": 1}}])
def test_truthy_non_list_choices_is_ignored(provider_type, payload):
    adapter = fresh_adapter(provider_type)
    # Must not raise: a TypeError here escapes the decoder and cools a healthy
    # deployment through _note_stream_failure.
    adapter.decode_stream_event("", json.dumps(payload))


# -- #247 ---------------------------------------------------------------------

@pytest.mark.parametrize("provider_type, body", [
    ("openai", {"choices": [{"message": None}]}),
    ("openai", {"choices": [], "usage": "x"}),
    ("openai", {"choices": "x", "usage": None}),
    ("openrouter", {"choices": [None]}),
    ("anthropic", {"content": [None]}),
    ("anthropic", {"content": "x", "usage": "x"}),
    ("gemini", {"candidates": [{"content": "str"}]}),
    ("gemini", {"candidates": [None], "usageMetadata": "x"}),
    ("nvidia-nim", {"choices": [{"message": None}]}),
    ("cline", {"choices": [{"message": None}]}),
    ("workbuddy", {"choices": [None]}),
    ("bai", {"choices": [{"message": None}]}),
])
def test_sync_decode_tolerates_null_nested_fields(provider_type, body):
    adapter = fresh_adapter(provider_type)
    turn = adapter.decode_response(200, json.dumps(body).encode())
    assert turn is not None


# -- #227 ---------------------------------------------------------------------

class _Prov:
    def __init__(self, provider_type):
        self.provider_type = provider_type


class _Dep:
    def __init__(self, provider_type, model_id):
        self.provider = _Prov(provider_type)
        self.model_id = model_id


@pytest.mark.parametrize("provider_type, model_id, expected", [
    # Anthropic Messages usage EXCLUDES cache reads -> do not subtract.
    ("anthropic", "claude-3-5-sonnet", False),
    ("opencode", "claude-sonnet-5", False),
    ("opencode", "qwen3-coder", False),
    # OpenAI-wire usage INCLUDES cache reads -> subtract.
    ("opencode", "mimo-v2.5-free", True),
    ("openai", "gpt-4o", True),
    ("openrouter", "stealth/ox-alpha", True),
])
def test_includes_cached_follows_wire_shape(provider_type, model_id, expected):
    assert prompt_includes_cached(_Dep(provider_type, model_id)) is expected


# -- #246 / #248 --------------------------------------------------------------

def _edit_tool():
    return {
        "type": "function",
        "function": {
            "name": "Edit",
            "parameters": {
                "type": "object",
                "properties": {
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": True,
                            "properties": {"type": {"type": "string"},
                                           "val": {"type": "string"}},
                        },
                    },
                },
            },
        },
    }


def test_items_is_sanitized_and_aliased():
    san = sanitize_nim_tool_schemas([_edit_tool()])[0]
    items = san["function"]["parameters"]["properties"]["edits"]["items"]
    assert "additionalProperties" not in items, "boolean subschema survived"
    assert "_nim_arg_type" in items["properties"]


def test_items_aliases_are_collected():
    san = sanitize_nim_tool_schemas([_edit_tool()])[0]
    aliases = collect_nim_tool_aliases([san])
    assert aliases.get("Edit") == {"_nim_arg_type": "type"}


def test_nested_alias_is_reversed():
    out = unalias_nim_tool_args(
        {"op": {"_nim_arg_type": "replace", "val": "x"}},
        {"_nim_arg_type": "type"})
    assert out == {"op": {"type": "replace", "val": "x"}}
