"""Round 207 — AUDIT #600: a spent OpenCode free allowance must wrap, not error.

Two defects, both on the path a caller walks into when the OpenCode free tier
runs out of allowance:

1. **The wrap.** ``execute_with_retries`` treated the refusal like any other
   failure: it spent the whole retry budget re-asking the same exhausted
   deployment (three upstream hits and two backoff sleeps) and only then
   entered the fallback group — and with no fallback group configured the
   caller got the upstream's billing state as its answer. A refusal about an
   *allowance* is not a transient fault: the same deployment answers the same
   way, so the retry budget must buy a different provider instead.

2. **The reset window.** The upstream states when the allowance resets in the
   BODY (``retryAfterMs``, measured ~9 h) and never in a header, so wiwi read
   no window at all: the caller was told its allowance was spent with no idea
   when to come back, and the proxy log could not name the window either.

The tests drive the real app through the OpenCode adapter — the free-tier
fingerprint, the forced upstream stream, the shared ``anonymous`` sentinel —
so the assertions are about the shipped behaviour and not about a mocked shape.
"""

from __future__ import annotations

import json
import sys

import httpx
import respx
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

import wiwi.main
from wiwi.config import load_config_from_string
from wiwi.providers.base import error_from_provider_status
from wiwi.server.app import create_app

ZEN_URL = "https://opencode.ai/inference/v1/chat/completions"
BACKUP_URL = "https://backup.test/v1/chat/completions"
MASTER = "sk-wiwi-master-test"

# The body of a spent free allowance, in the upstream's own shape. The reset
# window rides in the BODY: Zen sends no ``Retry-After`` header at all.
QUOTA_429 = {
    "type": "error",
    "error": {
        "type": "FreeUsageLimitError",
        "message": (
            "Free usage limit reached. Free models work in OpenCode "
            "(https://opencode.ai); subscribe to OpenCode Go to keep going: "
            "https://opencode.ai/go"
        ),
        "retryAfterMs": 32217000,
    },
}

OK_BODY = {
    "id": "cmpl-1",
    "object": "chat.completion",
    "created": 1,
    "model": "gpt-4o",
    "choices": [{"index": 0,
                 "message": {"role": "assistant", "content": "ok"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


def _config(*, with_fallback: bool = True, shared_group: bool = False) -> str:
    """A Zen free deployment, a paid backup, and optionally a fallback link.

    ``shared_group`` puts both deployments in ONE model group (the multi-provider
    shape); otherwise the backup is its own group and reachable only through
    ``router_settings.fallbacks``.
    """
    fallbacks = ("  fallbacks:\n    deepseek-v3-free: [gpt-4o]\n"
                 if with_fallback else "")
    if shared_group:
        model_list = (
            "  - model_name: deepseek-v3-free\n"
            "    wiwi_params: {provider: zen, model: deepseek-v3-free}\n"
            "  - model_name: deepseek-v3-free\n"
            "    wiwi_params: {provider: backup, model: gpt-4o}\n"
        )
    else:
        model_list = (
            "  - model_name: deepseek-v3-free\n"
            "    wiwi_params: {provider: zen, model: deepseek-v3-free}\n"
            "  - model_name: gpt-4o\n"
            "    wiwi_params: {provider: backup, model: gpt-4o}\n"
        )
    return f"""
general_settings:
  master_key: {MASTER}
  database_url: "sqlite+aiosqlite:///:memory:"
providers:
  - name: zen
    provider: opencode
    base_url: https://opencode.ai/inference/v1
    keys: [{{label: anonymous, key: anonymous}}]
  - name: backup
    provider: openai
    base_url: https://backup.test/v1
    keys: [{{label: default, key: sk-backup}}]
router_settings:
  num_retries: 2
{fallbacks}model_list:
{model_list}"""


async def _chat(c: AsyncClient, model: str = "deepseek-v3-free") -> httpx.Response:
    return await c.post("/v1/chat/completions",
                        json={"model": model,
                              "messages": [{"role": "user", "content": "hi"}]})


async def _run(config: str, body):
    """Run ``body(client, zen_hits, backup_hits)`` against a real app."""
    @respx.mock
    async def _inner():
        zen = respx.post(ZEN_URL).respond(status_code=429, json=QUOTA_429)
        backup = respx.post(BACKUP_URL).respond(status_code=200, json=OK_BODY)
        app = create_app(load_config_from_string(config))
        async with LifespanManager(app), AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://t",
                headers={"Authorization": f"Bearer {MASTER}"}) as c:
            return await body(c, zen, backup, app)

    return await _inner()


# -- the wrap ---------------------------------------------------------------

async def test_a_spent_allowance_wraps_to_the_fallback_group():
    """The exhausted free allowance must reach the backup provider.

    The old path re-asked the spent deployment until the retry budget ran out
    (three upstream hits, two backoff sleeps) and only then entered the
    fallback group — so the caller waited for nothing and the upstream saw
    three refusals it had already given.
    """

    async def _body(c, zen, backup, app):
        r = await _chat(c)
        return r, zen.call_count, backup.call_count

    r, zen_hits, backup_hits = await _run(_config(), _body)
    assert r.status_code == 200, r.text
    assert zen_hits == 1, f"the spent deployment was re-asked {zen_hits} times"
    assert backup_hits == 1, backup_hits


async def test_a_spent_allowance_wraps_to_a_sibling_provider_in_the_group():
    """A group that spans two providers must fail over inside the group.

    The backup lives in the same model group here, so there is no fallback
    table in play: the wrap is the group failover itself.
    """

    async def _body(c, zen, backup, app):
        r = await _chat(c)
        return r, zen.call_count, backup.call_count

    r, zen_hits, backup_hits = await _run(
        _config(with_fallback=False, shared_group=True), _body)
    assert r.status_code == 200, r.text
    assert zen_hits == 1, zen_hits
    assert backup_hits == 1, backup_hits


async def test_the_spent_allowance_does_not_cool_the_shared_sentinel():
    """The ``anonymous`` sentinel is shared by every free Zen deployment.

    One spent allowance must not retire the tier (AUDIT #469): the credential
    stays in rotation and the deployment stays available, because the
    allowance resets on its own schedule rather than by our cooldown.
    """

    async def _body(c, zen, backup, app):
        await _chat(c)
        router = app.state.wiwi.router
        key = router.providers["zen"].keys[0]
        dep = router.groups["deepseek-v3-free"][0]
        return key, dep

    key, dep = await _run(_config(with_fallback=False), _body)
    assert key.available, "the shared anonymous sentinel was cooled"
    assert key.status == "active", key.status
    assert dep.available, "the whole free tier was retired"


async def test_the_reset_window_reaches_the_caller():
    """With nowhere to fail over to, the caller still learns when to come back.

    The upstream states the window in the body and sends no header, so wiwi
    read none — the caller was told its allowance was spent with no idea when
    it resets.
    """

    async def _body(c, zen, backup, app):
        return await _chat(c)

    r = await _run(_config(with_fallback=False), _body)
    assert r.status_code == 402, r.text
    assert r.json()["error"]["type"] == "permission_error"
    assert "quota exhausted" in r.json()["error"]["message"]
    assert r.headers.get("Retry-After") == "32217", dict(r.headers)


async def test_the_reset_window_reaches_a_streaming_caller():
    """A streaming request that is refused before the stream starts.

    The refusal arrives at connect time, so the response is still a normal
    error response and the window can ride in a header.
    """

    async def _body(c, zen, backup, app):
        return await c.post("/v1/chat/completions",
                            json={"model": "deepseek-v3-free",
                                  "messages": [{"role": "user", "content": "hi"}],
                                  "stream": True})

    r = await _run(_config(with_fallback=False), _body)
    assert r.status_code == 402, r.text
    assert r.headers.get("Retry-After") == "32217", dict(r.headers)


# -- the window itself ------------------------------------------------------

def test_the_reset_window_is_read_from_the_error_body():
    err = error_from_provider_status(429, json.dumps(QUOTA_429), "zen")
    assert err.retry_after == 32217.0, err.retry_after
    assert err.status == 402
    assert err.etype == "permission_error"


def test_a_body_without_a_window_reads_none():
    err = error_from_provider_status(
        429, json.dumps({"error": {"message": "slow down"}}), "zen")
    assert err.retry_after is None


def test_a_window_in_milliseconds_is_converted_to_seconds():
    err = error_from_provider_status(
        429, json.dumps({"error": {"retryAfterMs": 1500}}), "zen")
    assert err.retry_after == 1.5


def test_a_window_in_seconds_is_read_as_is():
    err = error_from_provider_status(
        429, json.dumps({"error": {"retryAfter": 90}}), "zen")
    assert err.retry_after == 90.0


def test_a_non_numeric_window_is_ignored():
    err = error_from_provider_status(
        429, json.dumps({"error": {"retryAfterMs": "soon"}}), "zen")
    assert err.retry_after is None


# -- the platform port (Railway) --------------------------------------------

def _run_cli(tmp_path, monkeypatch, argv, port_env):
    """Run the CLI with uvicorn stubbed out; return the kwargs it was given."""
    config = tmp_path / "wiwi.yaml"
    config.write_text(
        "general_settings:\n  master_key: sk-x\n"
        "database_url: \"sqlite+aiosqlite:///:memory:\"\n"
        "wiwi_settings:\n  port: 4000\n"
    )
    seen: dict = {}
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(wiwi.main, "load_env", lambda: None)
    monkeypatch.setattr(wiwi.main.uvicorn, "run",
                        lambda app, **kw: seen.update(kw))
    if port_env is None:
        monkeypatch.delenv("PORT", raising=False)
    else:
        monkeypatch.setenv("PORT", port_env)
    wiwi.main.cli()
    return seen


def test_the_cli_listens_on_the_injected_platform_port(tmp_path, monkeypatch):
    """Railway injects ``PORT`` and talks to it.

    Without this the gateway answers on 4000 while the platform's edge proxy
    and healthcheck talk to the injected port, and the deployment is reported
    unhealthy.
    """
    seen = _run_cli(tmp_path, monkeypatch,
                   ["wiwi", "--config", str(tmp_path / "wiwi.yaml")], "8123")
    assert seen["port"] == 8123, seen


def test_the_port_flag_still_beats_the_platform_port(tmp_path, monkeypatch):
    seen = _run_cli(tmp_path, monkeypatch,
                   ["wiwi", "--config", str(tmp_path / "wiwi.yaml"),
                    "--port", "9000"], "8123")
    assert seen["port"] == 9000, seen


def test_an_empty_platform_port_does_not_shadow_the_configured_port(
        tmp_path, monkeypatch):
    """Railway injects an EMPTY value for a variable that is not set.

    An empty value must fall back to the configured port, not to port 0.
    """
    seen = _run_cli(tmp_path, monkeypatch,
                   ["wiwi", "--config", str(tmp_path / "wiwi.yaml")], "")
    assert seen["port"] == 4000, seen


def test_a_garbage_platform_port_does_not_shadow_the_configured_port(
        tmp_path, monkeypatch):
    seen = _run_cli(tmp_path, monkeypatch,
                   ["wiwi", "--config", str(tmp_path / "wiwi.yaml")], "http")
    assert seen["port"] == 4000, seen
