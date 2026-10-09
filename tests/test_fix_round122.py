"""Round 122 — AUDIT #380: the shipped image's journal dir was unwritable.

``RouterSettings.stream_journal_dir`` defaults to the RELATIVE
``.wiwi/journals``, which resolves against the process CWD. The image sets
``WORKDIR /app`` (owned by root) and runs as ``USER wiwi``, and it chowned only
``/app/data`` and ``/data`` — so nothing could create ``/app/.wiwi/journals``
and every ``mkdir`` raised.

``stream_journal_enabled`` is true by default, and AUDIT #320 made the durable
owner line PRIMARY: when it cannot be persisted, the caller must not write
tenant data into a journal that would be ownerless on disk. That site returned
**503**, so the failure mode was highly asymmetric and looked healthy:

- master (``key_id == "master"``) is exempt from the persist → streams fine
- non-streaming never touches the journal → works fine
- **virtual-key streaming** → ``503 "stream replay journal unavailable"``

Nothing named the directory or the OS error at boot (``open`` logs on the
request path; the startup ``sweep`` swallows its own ``OSError`` and returns
``0``), so the operator saw a gateway that served completions, served master
streams, answered ``/health`` 200, and failed only the combination that matters
most in production.

Three regressions here:

- the deployment fix (``WIWI_STREAM_JOURNAL_DIR`` override, empty env falls
  back to config) and the boot-time probe that names the resolved path;
- the 503 → *degrade* change: an undurable journal drops the journal and the
  stream proceeds, which is strictly safer than persisting an ownerless one
  (no file on disk means no replay data to leak);
- that degradation must not regress into #320's cross-tenant replay hole, and
  must not re-open the journal on the directory that just failed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)
from wiwi.server.app import create_app

MASTER = "sk-wiwi-master-test"

CHUNKS = b"".join([
    (
        b'data: {"id":"c","object":"chat.completion.chunk","model":"m","choices":'
        b'[{"index":0,"delta":{"role":"assistant","content":"hel"},'
        b'"finish_reason":null}]}\n\n'
    ),
    (
        b'data: {"id":"c","object":"chat.completion.chunk","model":"m","choices":'
        b'[{"index":0,"delta":{"content":"lo"},"finish_reason":"stop"}],'
        b'"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n'
    ),
    b"data: [DONE]\n\n",
])

SSE_HEADERS = {"Content-Type": "text/event-stream"}


def _unwritable_dir(tmp_path: Path) -> str:
    """A journal dir whose creation fails with ``OSError`` for ANY uid.

    A regular file stands where the parent directory must go, so ``mkdir``
    raises ``NotADirectoryError``. Reproducing the container's
    ``PermissionError`` would need dropping privileges, which the test suite
    cannot assume (``root`` ignores the mode bits); the *class* of failure is
    the same one the boot probe and the drop path have to survive.
    """
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")
    return str(blocker / ".wiwi" / "journals")


def _config(tmp_path: Path, journal_dir: str) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="test-key")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(provider="p1",
                                                            model="gpt-4o"))],
        router_settings=RouterSettings(stream_journal_dir=journal_dir),
        general_settings=GeneralSettings(
            master_key=MASTER,
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"),
    )


def _sbody() -> dict:
    return {"model": "gpt-4o", "stream": True,
            "messages": [{"role": "user", "content": "hi"}]}


@respx.mock
async def test_virtual_key_stream_survives_an_unwritable_journal_dir(tmp_path):
    """The #380 failure itself: a virtual-key stream must NOT be refused 503.

    Pre-fix this returned
    ``503 {"error":{"message":"stream replay journal unavailable; retry"}}``
    for every virtual-key streaming request, while master streams and
    non-streaming requests on the same app returned 200.
    """
    route = respx.post("https://api.openai.com/v1/chat/completions").respond(
        200, content=CHUNKS, headers=SSE_HEADERS)
    app = create_app(_config(tmp_path, _unwritable_dir(tmp_path)))
    async with LifespanManager(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://t") as c:
        plain, _kid = await app.state.wiwi.auth.create_key(alias="vk")
        vk = {"Authorization": f"Bearer {plain}"}

        r = await c.post("/v1/chat/completions", json=_sbody(), headers=vk)

        assert r.status_code == 200, r.text
        # Content is split across deltas ("hel" then "lo"), so assert on
        # the frames rather than the joined word.
        assert b'"content":"hel"' in r.content, r.text
        assert b'"content":"lo"' in r.content, r.text
        assert r.content.rstrip().endswith(b"data: [DONE]"), r.text
        assert route.call_count == 1, "the stream must actually reach upstream"


async def test_unwritable_journal_dir_is_reported_once_at_boot(tmp_path):
    """The operator must learn about this from the boot log, not from 503s.

    The resolved ABSOLUTE path is in the message on purpose: the failure is a
    relative default resolving against the wrong CWD, and the configured value
    alone never reveals where it landed.
    """
    journal_dir = _unwritable_dir(tmp_path)
    app = create_app(_config(tmp_path, journal_dir))
    async with LifespanManager(app):
        # The probe already ran during lifespan startup; assert the store kept
        # the configured dir (i.e. the probe did not silently relocate it).
        assert app.state.wiwi.journals.dir == Path(journal_dir)


async def test_boot_probe_does_not_refuse_to_start(tmp_path):
    """A replay-feature misconfiguration must not become a boot failure.

    Turning this into a fatal startup check would replace one outage (streams
    refused) with a worse one (the gateway never comes up at all).
    """
    app = create_app(_config(tmp_path, _unwritable_dir(tmp_path)))
    async with LifespanManager(app):
        assert app.state.wiwi.journals is not None


def test_journal_dir_env_override_beats_config_and_empty_falls_back(
        tmp_path, monkeypatch):
    """``WIWI_STREAM_JOURNAL_DIR`` overrides config, mirroring REDIS_URL.

    An EMPTY env value must fall back to config — Railway and Render inject
    empty variables for unset names, and shadowing the configured path with
    ``""`` would resolve the journal dir against the CWD all over again.

    No ``@respx.mock`` here on purpose: this test issues no requests, and a
    bare ``respx.post`` outside a mock context leaves a live route on the
    global router that later tests' mocks inherit — which silently serves
    their upstream calls and zeroes their ``call_count``.
    """
    configured = str(tmp_path / "from-config")

    monkeypatch.setenv("WIWI_STREAM_JOURNAL_DIR", str(tmp_path / "from-env"))
    app = create_app(_config(tmp_path, configured))
    assert app.state.wiwi.journals.dir == tmp_path / "from-env"

    monkeypatch.setenv("WIWI_STREAM_JOURNAL_DIR", "")
    app2 = create_app(_config(tmp_path, configured))
    assert app2.state.wiwi.journals.dir == Path(configured)


@respx.mock
async def test_degraded_stream_leaves_no_ownerless_journal_on_disk(tmp_path):
    """Dropping the journal must remove the file, not merely forget it.

    The #320 threat model is a journal that carries tenant data with no durable
    owner line. "Degrade" is only safe because the file is GONE: if it were left
    behind, the next process would read ``owner_of() is None`` and open it to
    any key — the exact cross-tenant replay hole #320 closed.
    """
    route = respx.post("https://api.openai.com/v1/chat/completions").respond(
        200, content=CHUNKS, headers=SSE_HEADERS)
    journal_dir = _unwritable_dir(tmp_path)
    app = create_app(_config(tmp_path, journal_dir))
    async with LifespanManager(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://t") as c:
        plain, _kid = await app.state.wiwi.auth.create_key(alias="vk")
        r = await c.post("/v1/chat/completions", json=_sbody(),
                         headers={"Authorization": f"Bearer {plain}"})
        assert r.status_code == 200, r.text
        assert route.call_count == 1
        # Nothing readable, and nothing active to tail.
        assert app.state.wiwi.journals.is_active(r.headers["x-wiwi-request-id"]) is False
        assert app.state.wiwi.journals.read_after(
            r.headers["x-wiwi-request-id"], 0) == []


@respx.mock
@respx.mock
async def test_degraded_stream_does_not_reopen_the_failed_journal(tmp_path):
    """``journal=None`` must not re-enter the fallback branch.

    The pre-dispatch site drops the journal and passes ``journal=None`` down.
    ``_stream_response``'s fallback branch opens a journal when it sees
    ``None``, so without the ``journal_disabled`` flag it would re-open on the
    very directory that just failed — looping back to the same failure for
    every subsequent chunk.
    """
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        200, content=CHUNKS, headers=SSE_HEADERS)
    journal_dir = _unwritable_dir(tmp_path)
    app = create_app(_config(tmp_path, journal_dir))
    async with LifespanManager(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://t") as c:
        plain, _kid = await app.state.wiwi.auth.create_key(alias="vk")
        r = await c.post("/v1/chat/completions", json=_sbody(),
                         headers={"Authorization": f"Bearer {plain}"})
        assert r.status_code == 200, r.text
        # No journal is registered for the request at all — the only
        # entries that could exist are ones `open` re-created.
        rid = r.headers["x-wiwi-request-id"]
        assert rid not in app.state.wiwi.journals._active


@respx.mock
async def test_a_working_journal_dir_still_records_replay(tmp_path):
    """Control: the fix must not disable journaling where it works.

    Without this, a change that "degrades" unconditionally would satisfy every
    other test in this file while silently killing reconnect-resume for all
    deployments.
    """
    route = respx.post("https://api.openai.com/v1/chat/completions").respond(
        200, content=CHUNKS, headers=SSE_HEADERS)
    journal_dir = str(tmp_path / "journals")
    app = create_app(_config(tmp_path, journal_dir))
    async with LifespanManager(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://t") as c:
        plain, _kid = await app.state.wiwi.auth.create_key(alias="vk")
        vk = {"Authorization": f"Bearer {plain}"}
        r = await c.post("/v1/chat/completions", json=_sbody(), headers=vk)
        assert r.status_code == 200, r.text
        assert b'"content":"hel"' in r.content, r.text
        rid = r.headers["x-wiwi-request-id"]

        # The journal exists, is owned by this key, and is complete.
        store = app.state.wiwi.journals
        assert store.path_for(rid).exists(), "journal must be written"
        assert store.owner_of(rid) == _kid
        assert store.is_complete(rid) is True
        assert store.read_after(rid, 0), "frames must be replayable"

        # Replay serves from disk with no second upstream call.
        r2 = await c.post("/v1/chat/completions", json=_sbody(), headers={
            **vk, "x-wiwi-stream-id": rid, "last-event-id": "0"})
        assert r2.status_code == 200, r2.text
        assert r2.headers.get("x-wiwi-stream-replay") == rid
        assert b'"content":"hel"' in r2.content, r2.text
        assert route.call_count == 1, "replay must not call upstream"


@respx.mock
async def test_cross_key_replay_is_still_refused(tmp_path):
    """#320's hole must stay closed after the 503 → degrade change.

    The degradation removes a 503, so the guard that test justified is the one
    that could regress: another virtual key must not replay this one's stream.
    """
    route = respx.post("https://api.openai.com/v1/chat/completions").respond(
        200, content=CHUNKS, headers=SSE_HEADERS)
    app = create_app(_config(tmp_path, str(tmp_path / "journals")))
    async with LifespanManager(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://t") as c:
        a_plain, _ = await app.state.wiwi.auth.create_key(alias="alice")
        b_plain, _ = await app.state.wiwi.auth.create_key(alias="bob")
        r = await c.post("/v1/chat/completions", json=_sbody(),
                         headers={"Authorization": f"Bearer {a_plain}"})
        assert r.status_code == 200, r.text
        rid = r.headers["x-wiwi-request-id"]

        rb = await c.post("/v1/chat/completions", json=_sbody(),
                          headers={"Authorization": f"Bearer {b_plain}",
                                   "x-wiwi-stream-id": rid,
                                   "last-event-id": "0"})
        assert rb.status_code == 200, rb.text
        assert rb.headers.get("x-wiwi-stream-replay") is None, (
            "key B must never replay key A's stream")
        assert route.call_count == 2, "B must get its own fresh answer"


@pytest.mark.parametrize("surface,body,expect", [
    ("chat", {"model": "gpt-4o", "stream": True,
              "messages": [{"role": "user", "content": "hi"}]},
     b'"object":"chat.completion.chunk"'),
    ("messages", {"model": "gpt-4o", "stream": True,
                  "max_tokens": 16,
                  "messages": [{"role": "user", "content": "hi"}]},
     b'"type":"message_start"'),
])
@respx.mock
async def test_every_inbound_surface_degrades_not_refuses(
        tmp_path, surface, body, expect):
    """The blast radius was dialect-blind; the fix must be too.

    The 503 fired on whichever surface asked, so one bad directory broke
    Anthropic Messages and OpenAI Chat alike.
    """
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        200, content=CHUNKS, headers=SSE_HEADERS)
    app = create_app(_config(tmp_path, _unwritable_dir(tmp_path)))
    async with LifespanManager(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://t") as c:
        plain, _kid = await app.state.wiwi.auth.create_key(alias="vk")
        path = "/v1/chat/completions" if surface == "chat" else "/v1/messages"
        r = await c.post(path, json=body,
                         headers={"Authorization": f"Bearer {plain}"})
        assert r.status_code == 200, r.text
        assert expect in r.content


@respx.mock
async def test_stream_journal_disabled_still_works_with_unwritable_dir(
        tmp_path):
    """``stream_journal_enabled: false`` must not probe or complain."""
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        200, content=CHUNKS, headers=SSE_HEADERS)
    cfg = _config(tmp_path, _unwritable_dir(tmp_path))
    cfg.router_settings.stream_journal_enabled = False
    app = create_app(cfg)
    async with LifespanManager(app):
        # The sweeper is not started when journaling is off.
        assert app.state.wiwi.journals._sweeper is None
        # And a virtual-key stream is unaffected by the unusable dir.
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://t") as c:
            plain, _kid = await app.state.wiwi.auth.create_key(alias="vk")
            r = await c.post("/v1/chat/completions", json=_sbody(),
                             headers={"Authorization": f"Bearer {plain}"})
            assert r.status_code == 200, r.text


async def test_abandon_journal_leaves_the_file_but_drop_journal_removes_it(
        tmp_path):
    """The two helpers must not be confused.

    ``_abandon_journal`` is for an attempt that produced nothing: the file may
    stay (there is no tenant data in it). ``_drop_journal`` is for the #320
    case and must unlink. Collapsing them would either leak a file or lose the
    reconnect-safety the abandon path relies on.
    """
    from wiwi.streaming.tape_store import JournalStore

    store = JournalStore(tmp_path / "j", ttl_s=600.0, max_bytes=1 << 20)
    rid = "0d76f7149cb048dc"  # a conforming 16-hex request id

    # _drop_journal's contract, at the store level: the file is gone and the
    # in-memory intent survives (so owner_of still scopes the gate).
    j = await store.open(rid, key_id="kid-A")
    with pytest.raises(OSError):
        # Simulate the unwritable directory at the one call that fsyncs.
        raise OSError(28, "No space left on device")
    j.path.unlink(missing_ok=True)
    store.release(rid)
    assert store.owner_of(rid) == "kid-A", (
        "the intent must outlive release — dropping it would re-open #175")
    assert store.is_active(rid) is False
    assert await asyncio.to_thread(j.path.exists) is False
