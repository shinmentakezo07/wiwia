"""Regression tests for shinway-style alias schema + ForceMapping (round 19).

Covers: ``ModelAliasEntry`` schema (``target`` / ``force_mapping`` /
``display_name`` / ``fork``), per-alias response-model rewrite (echo the
client's alias or show the resolved group), admin CRUD with rich values
including persistence across restart, validation of rich values, and
back-compat (plain-string YAML aliases still work).
"""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
import respx
from asgi_lifespan import LifespanManager

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelAliasEntry,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)
from wiwi.server.app import create_app

# -- fixtures ----------------------------------------------------------------

def _config(
    *,
    yaml_aliases: dict | None = None,
    group_a_model: str = "m-a",
    group_b_model: str = "m-b",
    group_c_model: str = "m-c",
    group_d_model: str = "m-d",
) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="k1")])],
        model_list=[
            ModelEntry(model_name="group-a",
                       wiwi_params=DeploymentParams(provider="p1", model=group_a_model)),
            ModelEntry(model_name="group-b",
                       wiwi_params=DeploymentParams(provider="p1", model=group_b_model)),
            ModelEntry(model_name="group-c",
                       wiwi_params=DeploymentParams(provider="p1", model=group_c_model)),
            ModelEntry(model_name="group-d",
                       wiwi_params=DeploymentParams(provider="p1", model=group_d_model)),
        ],
        general_settings=GeneralSettings(master_key="sk-wiwi-master-test",
                                         database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(model_group_alias=yaml_aliases or {}),
    )


@pytest_asyncio.fixture
async def client():
    app = create_app(_config())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, app


@pytest_asyncio.fixture
async def client_with_alias():
    """Pre-configured with one rich-form alias `fast -> group-a, force_mapping=false`."""
    app = create_app(_config(yaml_aliases={
        "fast": ModelAliasEntry(target="group-a", force_mapping=False),
    }))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, app


H = {"Authorization": "Bearer sk-wiwi-master-test"}


OPENAI_CHAT_BODY = {
    "id": "chatcmpl-x", "object": "chat.completion", "model": "m-a",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2,
              "prompt_tokens_details": {"cached_tokens": 0},
              "completion_tokens_details": {"reasoning_tokens": 0}},
}

OPENAI_RESPONSES_BODY = {
    "id": "resp_x", "object": "response", "model": "m-a",
    "output": [{"type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "hello"}]}],
    "usage": {"input_tokens": 5, "output_tokens": 2,
              "input_tokens_details": {"cached_tokens": 0},
              "output_tokens_details": {"reasoning_tokens": 0}},
}

ANTHROPIC_BODY = {
    "id": "msg_x", "type": "message", "model": "m-a", "role": "assistant",
    "content": [{"type": "text", "text": "hello"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 5, "output_tokens": 2,
              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
}


# -- schema tests ------------------------------------------------------------

def test_alias_entry_schema_round_trip():
    """ModelAliasEntry accepts the rich form and round-trips via model_dump."""
    e = ModelAliasEntry(target="group-a", force_mapping=False,
                        display_name="Fast")
    assert e.model_dump() == {
        "target": "group-a", "force_mapping": False, "display_name": "Fast",
        "fork": False,
    }


def test_alias_entry_rejects_unknown_keys():
    """Typos in YAML must fail loudly, not be silently ignored."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ModelAliasEntry(target="group-a", forcemapping=False)  # type: ignore[call-arg]


def test_router_settings_accepts_plain_string_aliases():
    """Back-compat: plain-string values are still legal."""
    s = RouterSettings(model_group_alias={"fast": "group-a"})
    assert s.model_group_alias["fast"] == "group-a"


def test_router_settings_accepts_mixed_aliases():
    s = RouterSettings(model_group_alias={
        "plain": "group-a",
        "rich": ModelAliasEntry(target="group-b", force_mapping=False,
                                display_name="R"),
    })
    assert isinstance(s.model_group_alias["rich"], ModelAliasEntry)
    assert s.model_group_alias["plain"] == "group-a"


# -- response rewrite: non-streaming -----------------------------------------

@respx.mock
async def test_force_mapping_false_rewrites_response_to_group_chat(client_with_alias):
    """`force_mapping: false` → response `model` shows the resolved group, not
    the alias the client sent."""
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        json=OPENAI_CHAT_BODY)
    c, _app = client_with_alias
    r = await c.post("/v1/chat/completions", json={
        "model": "fast",
        "messages": [{"role": "user", "content": "hi"}],
    }, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "group-a"


@respx.mock
async def test_force_mapping_false_rewrites_response_responses(client_with_alias):
    c, _app = client_with_alias
    # The OpenAI provider always calls /v1/chat/completions upstream; the
    # inbound Responses / Anthropic codecs are decoded to IR and re-encoded
    # to OpenAI before the request leaves. Mock the actual upstream URL.
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        json=OPENAI_CHAT_BODY)
    r = await c.post("/v1/responses", json={
        "model": "fast", "input": "hi",
    }, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "group-a"


@respx.mock
async def test_force_mapping_false_rewrites_response_anthropic(client_with_alias):
    c, _app = client_with_alias
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        json=OPENAI_CHAT_BODY)
    r = await c.post("/v1/messages", json={
        "model": "fast", "max_tokens": 100,
        "messages": [{"role": "user", "content": "hi"}],
    }, headers={"x-api-key": "sk-wiwi-master-test",
                "anthropic-version": "2023-06-01"})
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "group-a"


# -- response rewrite: streaming ---------------------------------------------

@respx.mock
async def test_force_mapping_false_rewrites_streaming_chat(client_with_alias):
    c, _app = client_with_alias
    respx.post("https://api.openai.com/v1/chat/completions").respond(text=(
        'data: {"choices":[{"delta":{"role":"assistant","content":"He"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"y"}}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
        '"usage":{"prompt_tokens":3,"completion_tokens":2,'
        '"prompt_tokens_details":{"cached_tokens":0},'
        '"completion_tokens_details":{"reasoning_tokens":0}}}\n\n'
        "data: [DONE]\n\n"))
    r = await c.post("/v1/chat/completions", json={
        "model": "fast", "stream": True,
        "messages": [{"role": "user", "content": "hi"}],
    }, headers=H)
    assert r.status_code == 200, r.text
    body = r.text
    # Every chat.completion.chunk frame must carry the rewritten model.
    assert body.count('"model":"group-a"') >= 2
    assert '"model":"fast"' not in body


# -- echo behavior (default + explicit force_mapping=true) -------------------

@respx.mock
async def test_plain_string_alias_echoes_alias_in_response(client):
    """Back-compat: YAML `fast: group-a` (str) → response model == 'fast'."""
    c, app = client
    # mutate live map directly (mirrors the round-18 admin endpoint effect)
    app.state.wiwi.router.settings.model_group_alias["fast"] = "group-a"
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        json=OPENAI_CHAT_BODY)
    r = await c.post("/v1/chat/completions", json={
        "model": "fast",
        "messages": [{"role": "user", "content": "hi"}],
    }, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "fast"


@respx.mock
async def test_force_mapping_true_explicit_echoes_alias(client):
    """`force_mapping: true` (default) → response model == alias."""
    c, app = client
    app.state.wiwi.router.settings.model_group_alias["fast"] = ModelAliasEntry(
        target="group-a", force_mapping=True)
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        json=OPENAI_CHAT_BODY)
    r = await c.post("/v1/chat/completions", json={
        "model": "fast",
        "messages": [{"role": "user", "content": "hi"}],
    }, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "fast"


# -- chain semantics ---------------------------------------------------------

@respx.mock
async def test_first_hop_flag_wins_in_chain(client):
    """If the client types `a` and the chain is a→b→group, the *first-hop*
    entry's `force_mapping` controls the response name."""
    c, app = client
    # b is also an alias so a→b→group works.
    app.state.wiwi.router.settings.model_group_alias = {
        "a": ModelAliasEntry(target="b", force_mapping=False),
        "b": ModelAliasEntry(target="group-a", force_mapping=True),
    }
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        json=OPENAI_CHAT_BODY)
    r = await c.post("/v1/chat/completions", json={
        "model": "a",
        "messages": [{"role": "user", "content": "hi"}],
    }, headers=H)
    assert r.status_code == 200, r.text
    # first hop is force_mapping=false → response model == final resolved group
    assert r.json()["model"] == "group-a"


# -- admin CRUD with rich values ---------------------------------------------

async def test_admin_post_aliases_accepts_rich_value(client):
    c, _app = client
    r = await c.post("/admin/aliases", headers=H, json={"set": {
        "fast": {"target": "group-a", "force_mapping": False,
                 "display_name": "Fast"},
    }, "unset": []})
    assert r.status_code == 200, r.text
    listing = (await c.get("/admin/models", headers=H)).json()
    assert "fast" in listing["aliases"]
    fast = listing["aliases"]["fast"]
    assert isinstance(fast, dict)
    assert fast["target"] == "group-a"
    assert fast["force_mapping"] is False
    assert fast["display_name"] == "Fast"


async def test_admin_post_aliases_round_trip_persistence(tmp_path):
    db = tmp_path / "wiwi.db"
    cfg1 = _config(yaml_aliases={"yaml-only": "group-a"})
    cfg1.general_settings.database_url = f"sqlite+aiosqlite:///{db}"
    app1 = create_app(cfg1)
    async with LifespanManager(app1):
        transport = httpx.ASGITransport(app=app1)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/admin/aliases", headers=H, json={"set": {
                "fast": {"target": "group-b", "force_mapping": False},
            }, "unset": []})
            assert r.status_code == 200, r.text
    cfg2 = _config(yaml_aliases={"yaml-only": "group-a"})
    cfg2.general_settings.database_url = f"sqlite+aiosqlite:///{db}"
    app2 = create_app(cfg2)
    async with LifespanManager(app2):
        transport = httpx.ASGITransport(app=app2)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            listing = (await c.get("/admin/models", headers=H)).json()
            # YAML plain-string alias survives
            assert listing["aliases"]["yaml-only"] == "group-a"
            # Rich alias survived and is the dict form
            assert listing["aliases"]["fast"]["target"] == "group-b"
            assert listing["aliases"]["fast"]["force_mapping"] is False


async def test_admin_post_aliases_unset_rich_value(client):
    c, _app = client
    # Set, then unset.
    r = await c.post("/admin/aliases", headers=H, json={"set": {
        "fast": {"target": "group-a", "force_mapping": False},
    }, "unset": []})
    assert r.status_code == 200, r.text
    r = await c.post("/admin/aliases", headers=H, json={"set": {}, "unset": ["fast"]})
    assert r.status_code == 200, r.text
    listing = (await c.get("/admin/models", headers=H)).json()
    assert "fast" not in listing["aliases"]


async def test_admin_post_aliases_rejects_fork_true(client):
    """`fork` is accepted in the schema for parity, but `fork: true` is
    rejected by the admin endpoint (not implemented yet)."""
    c, _app = client
    r = await c.post("/admin/aliases", headers=H, json={"set": {
        "fast": {"target": "group-a", "fork": True},
    }, "unset": []})
    assert r.status_code == 400
    assert "fork" in r.json()["error"]["message"].lower()


async def test_admin_post_aliases_rejects_invalid_rich_value(client):
    c, _app = client
    # missing target
    r = await c.post("/admin/aliases", headers=H, json={"set": {
        "fast": {"force_mapping": False},
    }, "unset": []})
    assert r.status_code == 400
    # non-bool force_mapping
    r = await c.post("/admin/aliases", headers=H, json={"set": {
        "fast": {"target": "group-a", "force_mapping": "yes"},
    }, "unset": []})
    assert r.status_code == 400
    # unknown key
    r = await c.post("/admin/aliases", headers=H, json={"set": {
        "fast": {"target": "group-a", "bogus": 1},
    }, "unset": []})
    assert r.status_code == 400


# -- display_name round-trip via /admin/models and /public/models ------------

async def test_display_name_appears_in_admin_models(client):
    c, _app = client
    await c.post("/admin/aliases", headers=H, json={"set": {
        "fast": {"target": "group-a", "display_name": "Fast (cheap)"},
    }, "unset": []})
    listing = (await c.get("/admin/models", headers=H)).json()
    assert listing["aliases"]["fast"]["display_name"] == "Fast (cheap)"


async def test_display_name_appears_in_public_models(client):
    c, _app = client
    await c.post("/admin/aliases", headers=H, json={"set": {
        "fast": {"target": "group-a", "display_name": "Fast (cheap)"},
    }, "unset": []})
    r = await c.get("/public/models")
    assert r.status_code == 200
    body = r.json()
    assert body["aliases"]["fast"]["display_name"] == "Fast (cheap)"
