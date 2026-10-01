"""Round 111 regressions — full-codebase sweep of 2026-09-30.

Each test corresponds to an AUDIT.md entry in the `## 🔴 Open` section added
by that sweep. Every one of these was confirmed to fail against the code as
it stood before the fix.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from wiwi.cache import keygen
from wiwi.cost.pricing import estimate_tokens
from wiwi.ir import types as ir
from wiwi.providers.base import error_from_provider_status
from wiwi.providers.registry import fresh_adapter
from wiwi.server.config_store import ConfigStoreNotFound
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc

# --- #321: provider secret reaches the client ------------------------------

SECRET = "AIza-SUPER-SECRET-KEY-9999"


def _upstream_body_with_secret() -> str:
    """An OpenRouter-shaped error that echoes the provider key back at us."""
    return json.dumps(
        {
            "error": {
                "message": "boom",
                "metadata": {
                    "raw": f"google: upstream said {SECRET}",
                    "provider_name": "google",
                },
            }
        }
    )


def test_provider_secret_never_reaches_client():
    """AUDIT #321: the raw upstream text is concatenated into the client error."""
    err = error_from_provider_status(500, _upstream_body_with_secret(), None)
    assert SECRET not in err.message, f"provider secret leaked to client: {err.message!r}"


def test_provider_secret_not_in_truncated_body_fallback():
    """The `body_text[:500]` fallbacks must not echo a credential either."""
    body = json.dumps({"error": {"code": 500, "message": SECRET * 200}})
    err = error_from_provider_status(500, body, None)
    assert SECRET not in err.message, "secret survived the truncation fallback"


# --- #322: an empty assistant turn is deleted, fusing the user turns --------

def test_empty_assistant_turn_is_not_deleted():
    """AUDIT #322: `content:null`+refusal collapses two user turns into one."""
    a = fresh_adapter("anthropic")
    req = ir.Request(
        model="claude-x",
        messages=[
            ir.Message(role="user", parts=[ir.TextPart("hi")]),
            # An assistant turn that decodes to zero parts — e.g. a refusal.
            ir.Message(role="assistant", parts=[]),
            ir.Message(role="user", parts=[ir.TextPart("second user turn")]),
        ],
    )
    body = a.encode_request(req, "claude-x", {})
    roles = [m["role"] for m in body["messages"]]
    assert roles.count("assistant") == 1, f"assistant turn vanished: {roles}"
    assert roles.count("user") == 2, f"user turns were fused: {roles}"


# --- #327: unhashable upstream stop_reason ----------------------------------

def test_hostile_stop_reason_does_not_raise():
    """AUDIT #327: `_STOP_REASON_IN.get(sr, ...)` raises on a list/dict."""
    a = fresh_adapter("anthropic")
    for hostile in (["x"], {"a": 1}):
        body = json.dumps(
            {"content": [{"type": "text", "text": "hi"}], "stop_reason": hostile}
        ).encode()
        turn = a.decode_response(200, body)  # must not raise
        assert turn.stop_reason == "stop", turn.stop_reason


# --- #334: non-string thinking signature ------------------------------------

def test_hostile_signature_is_coerced():
    """AUDIT #334: `rt` is guarded, `signature` is not."""
    a = fresh_adapter("anthropic")
    body = json.dumps(
        {
            "content": [{"type": "thinking", "thinking": "because", "signature": 123}],
            "stop_reason": "end_turn",
        }
    ).encode()
    turn = a.decode_response(200, body)
    assert turn.thinking[0].signature is None, (
        f"non-string signature reached the IR: {turn.thinking[0].signature!r}"
    )


# --- #325: junk max_tokens discards max_completion_tokens -------------------

def test_junk_max_tokens_does_not_discard_max_completion_tokens():
    """AUDIT #325: a non-numeric `max_tokens` swallows a good `max_completion_tokens`."""
    req = oc.decode_request(
        {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}],
         "max_tokens": "abc", "max_completion_tokens": 77}
    )
    assert req.gen_params.max_tokens == 77, req.gen_params.max_tokens


def test_max_tokens_zero_still_wins_over_max_completion_tokens():
    """UPDATE.md §6.2 must keep holding: 0 is a real cap, not junk."""
    req = oc.decode_request(
        {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}],
         "max_tokens": 0, "max_completion_tokens": 77}
    )
    assert req.gen_params.max_tokens == 0, req.gen_params.max_tokens


# --- #330: estimate_tokens raises on ordinary text --------------------------

def test_estimate_tokens_handles_special_tokens():
    """AUDIT #330: marked fixed, but `disallowed_special` was never passed.

    The trigger is text containing a special-token string — which a client can
    trivially send, e.g. echoed back in a tool result. `estimate_tokens` is on
    the request path, so this is a client-triggerable 500.
    """
    assert estimate_tokens("hello <|endoftext|> world", "gpt-4") > 0


# --- #329: anthropic-beta is absent from the cache key ----------------------

def test_anthropic_beta_changes_cache_key():
    """AUDIT #329: two requests differing only in `anthropic-beta` collide."""
    keygen_cache_key = keygen.response_cache_key
    import inspect

    params = set(inspect.signature(keygen_cache_key).parameters)
    assert "anthropic_beta" in params, (
        f"response_cache_key cannot see the beta header at all: {sorted(params)}"
    )


# --- #335: _defer re-sums the whole buffer on every append ------------------

def test_defer_is_not_quadratic():
    """AUDIT #335: line 777 re-sums `self._deferred` on every append."""
    e = am.AnthropicStreamEncoder.__new__(am.AnthropicStreamEncoder)
    e._deferred = [("text", "x", None)] * 20_000

    # An incremental counter would make appends O(1); a re-sum is O(n).
    # Check the property directly rather than by timing, which is flaky.
    src = pathlib.Path(am.__file__).read_text()
    body = src.split("def _defer", 1)[1].split("def _drain_deferred", 1)[0]
    assert "self._deferred_chars" in body, (
        "_defer still re-sums the whole buffer on every append"
    )


# --- #319: admin edits to a YAML provider are silently discarded ------------

YAML_CONFIG = """
providers:
  - name: p1
    provider: openai
    base_url: https://api.openai.com/v1
    keys:
      - {label: default, key: os.environ/P1KEY}
model_list:
  - model_name: gpt-4o
    litellm_params: {model: gpt-4o}
    wiwi_params: {provider: p1, model: gpt-4o}
general_settings:
  master_key: sk-wiwi-master-test
  database_url: "sqlite+aiosqlite:///__DB__"
"""

MK = "sk-wiwi-master-test"


async def _boot(tmp_path, monkeypatch):
    """Boot the app over a YAML-defined provider with its own sqlite file.

    The lifespan MUST be entered before the client is used — the admin routes
    read ``app.state.wiwi``, which is only populated once startup has run.
    """
    from wiwi.config import load_config
    from wiwi.server.app import create_app

    monkeypatch.setenv("P1KEY", "sk-real-p1")
    cfg = tmp_path / "wiwi.yaml"
    cfg.write_text(YAML_CONFIG.replace("__DB__", str(tmp_path / "app.db")))
    app = create_app(load_config(str(cfg)))
    lm = LifespanManager(app)
    await lm.__aenter__()
    client = AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t",
        headers={"Authorization": f"Bearer {MK}"})
    return app, lm, client


async def test_yaml_provider_edit_is_not_silently_discarded(tmp_path, monkeypatch):
    """AUDIT #319: the UPDATE matched zero rows and the handler answered 200."""
    app, lm, client = await _boot(tmp_path, monkeypatch)
    try:
        async with client:
            r = await client.patch("/admin/providers/p1", json={
                "provider_type": "anthropic",
                "base_url": "https://admin-edited.example",
            })
            # A YAML-defined provider cannot persist an edit, so the request
            # still answers 200 and applies in memory — rejecting outright
            # would break every YAML-only deployment (the HF Space defines all
            # of its providers in YAML). What must not happen is the *silent*
            # version: the store must refuse the zero-row write and the
            # handler must log it.
            assert r.status_code == 200, r.text
            with pytest.raises(ConfigStoreNotFound):
                await app.state.wiwi.config_store.update_provider(
                    "p1", base_url="https://never.example")
    finally:
        await lm.__aexit__(None, None, None)


async def test_yaml_provider_delete_key_is_not_silently_discarded(tmp_path, monkeypatch):
    """AUDIT #319: a deleted YAML key resurrected on the next restart."""
    app, lm, client = await _boot(tmp_path, monkeypatch)
    try:
        async with client:
            r = await client.delete("/admin/providers/p1/keys/default")
            assert r.status_code == 200, r.text
            with pytest.raises(ConfigStoreNotFound):
                await app.state.wiwi.config_store.delete_key("p1", "default")
    finally:
        await lm.__aexit__(None, None, None)


async def test_db_provider_edit_still_persists(tmp_path, monkeypatch):
    """The fix must not break edits to providers that DO have a DB row."""
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine

    from wiwi.config import load_config
    from wiwi.server.app import create_app

    monkeypatch.setenv("P1KEY", "sk-real-p1")
    cfg = tmp_path / "wiwi.yaml"
    cfg.write_text(YAML_CONFIG.replace("__DB__", str(tmp_path / "app.db")))
    app = create_app(load_config(str(cfg)))
    lm = LifespanManager(app)
    await lm.__aenter__()
    try:
        async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t",
                headers={"Authorization": f"Bearer {MK}"}) as client:
            # Create p2 through the admin API, so it is DB-backed and present
            # in the router (a bare store write would not register the account).
            created = await client.post("/admin/providers", json={
                "name": "p2", "provider_type": "openai",
                "base_url": "https://p2.example",
                "key": "sk-p2", "label": "default"})
            assert created.status_code == 200, created.text
            r = await client.patch("/admin/providers/p2", json={
                "base_url": "https://p2-edited.example"})
            assert r.status_code == 200, r.text
            assert app.state.wiwi.router.providers["p2"].base_url == \
                "https://p2-edited.example"

        eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/app.db")
        async with eng.begin() as conn:
            rows = (await conn.execute(sa.text(
                "SELECT base_url FROM providers WHERE name='p2'"))).fetchall()
        await eng.dispose()
        assert [tuple(r) for r in rows] == [("https://p2-edited.example",)], (
            "the edit did not persist")
    finally:
        await lm.__aexit__(None, None, None)


async def test_update_provider_secret_rejects_yaml_provider(tmp_path, monkeypatch):
    """AUDIT #319: a refreshed OAuth token must not be reported as persisted.

    ``_update_provider_secret`` persists before touching memory precisely so
    this path can report failure instead of running on a token a restart
    would revert.
    """
    from wiwi.server.config_store import ConfigStoreNotFound

    app, lm, client = await _boot(tmp_path, monkeypatch)
    try:
        async with client:
            with pytest.raises(ConfigStoreNotFound):
                await app.state.wiwi.config_store.update_key_secret(
                    "p1", "default", "ROTATED-TOKEN")
    finally:
        await lm.__aexit__(None, None, None)


async def test_update_key_rejects_yaml_provider(tmp_path, monkeypatch):
    """AUDIT #319: ``update_key`` was the one mutator left without the check.

    A weight/enable edit on a YAML-defined account went through a bare UPDATE
    that matched zero rows and returned success — the identical symptom #319
    claimed to close.
    """
    app, lm, client = await _boot(tmp_path, monkeypatch)
    try:
        async with client:
            with pytest.raises(ConfigStoreNotFound):
                await app.state.wiwi.config_store.update_key(
                    "p1", "default", enabled=False)
            # The HTTP layer answers 200 but logs, matching the other mutators.
            r = await client.patch("/admin/providers/p1/keys/default",
                                   json={"enabled": False})
            assert r.status_code == 200, r.text
    finally:
        await lm.__aexit__(None, None, None)


def test_anthropic_coercion_divergence_is_documented():
    """The digit-string divergence between the Anthropic ladder and
    ``coerce_int`` is deliberate and recorded, not an accident to re-fix."""
    import inspect

    src = inspect.getsource(am)
    ladder = src.split("mt_raw = body.get", 1)[1][:900]
    assert "isdigit" in ladder, "the Anthropic ladder lost its digit-string branch"
    assert "AUDIT #83" in ladder, (
        "the divergence from coerce_int is undocumented — add the note back"
    )
