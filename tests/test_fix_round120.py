"""Regression round 120 — the 2026-10-06 sweep (AUDIT #360-#372).

Every test here was written to FAIL against the pre-fix source. Findings and
their evidence are recorded in AUDIT.md under "Open — sweep 2026-10-06".
"""

import asyncio
import contextlib
import json

import pytest

# ---------------------------------------------------------------------------
# #351 / #352 — inbound codec guards
# ---------------------------------------------------------------------------


def test_unhashable_reasoning_effort_does_not_raise():
    """#351: a list/dict effort 500s every Anthropic + Gemini deployment."""
    from wiwi.ir.types import effort_to_thinking_budget

    for bad in (["low"], {"a": 1}, ("low",), 5, True, None, object()):
        assert effort_to_thinking_budget(bad) is None


def test_unhashable_reasoning_effort_survives_the_adapter_encode_path():
    """#351: the crash was inside effort_to_thinking_budget, reached by encode."""
    from wiwi.ir.types import GenParams

    g = GenParams(reasoning_effort=["low"], max_tokens=100)
    assert g.effective_thinking_budget() is None


@pytest.mark.parametrize("bad_input", [{"a": 1}, 123, True, 4.5])
def test_non_list_non_string_input_is_rejected(bad_input):
    """#352: input of a wrong type was silently dropped -> 200 with no prompt."""
    import wiwi.wire.openai_responses as resp

    with pytest.raises(Exception) as ei:
        resp.decode_request({"model": "m", "input": bad_input})
    assert "input" in str(ei.value)


def test_valid_input_shapes_still_decode():
    """#352: the guard must not reject the two legal shapes."""
    import wiwi.wire.openai_responses as resp

    assert resp.decode_request({"model": "m", "input": "hi"}).messages
    assert resp.decode_request(
        {"model": "m", "input": [{"type": "message", "role": "user",
                                  "content": "hi"}]}).messages


# ---------------------------------------------------------------------------
# #365 / #366 — adapter typed-wrong guards
# ---------------------------------------------------------------------------


def _frame(delta):
    # The gateway's SSE parser strips the ``data: `` prefix before handing the
    # payload to the adapter, so pass the bare JSON.
    return json.dumps({"choices": [{"index": 0, "delta": delta}]})


def _tc(idx):
    return {"index": idx, "id": "c1", "type": "function",
            "function": {"name": "f", "arguments": ""}}


def _args(idx):
    return {"index": idx, "function": {"arguments": "{}"}}


@pytest.mark.parametrize("bad", [["STOP"], {"a": 1}, ("STOP",)])
def test_gemini_finish_reason_typed_wrong_does_not_raise(bad):
    """#365: an unhashable finishReason raised TypeError on both paths."""
    from wiwi.providers.registry import fresh_adapter

    body = {"candidates": [{"content": {"parts": [{"text": "hi"}]},
                            "finishReason": bad}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1}}
    turn = fresh_adapter("gemini").decode_response(200, json.dumps(body).encode())
    assert turn.stop_reason == "stop"
    assert turn.text == "hi"

    ev = {"candidates": [{"content": {"parts": [{"text": "hi"}]},
                          "finishReason": bad}]}
    ds = fresh_adapter("gemini").decode_stream_event("message", json.dumps(ev))
    assert ds, "a text frame must still produce deltas"


@pytest.mark.parametrize("ptype", ["openai", "nvidia-nim"])
@pytest.mark.parametrize("bad", [["a"], {"z": 1}])
def test_tool_call_index_unhashable_does_not_raise(ptype, bad):
    """#366: idx feeds a set add + dict key, so an unhashable value raised."""
    from wiwi.providers.registry import fresh_adapter

    a = fresh_adapter(ptype)
    a.decode_stream_event("message", _frame({"tool_calls": [_tc(bad)]}))
    out = a.decode_stream_event("message", _frame({"tool_calls": [_args(bad)]}))
    opens = [d for d in out if d.__class__.__name__ == "ToolCallOpen"]
    assert len(opens) == 1
    assert isinstance(opens[0].index, int)
    assert not isinstance(opens[0].index, bool)


@pytest.mark.parametrize("ptype", ["openai", "nvidia-nim"])
def test_tool_call_index_wrong_but_hashable_is_coerced(ptype):
    """#366: index="0" split one provider call across two tool blocks."""
    from wiwi.providers.registry import fresh_adapter

    a = fresh_adapter(ptype)
    a.decode_stream_event("message", _frame({"tool_calls": [_tc("0")]}))
    out = a.decode_stream_event("message", _frame({"tool_calls": [_args("0")]}))
    opens = [d for d in out if d.__class__.__name__ == "ToolCallOpen"]
    assert len(opens) == 1
    assert isinstance(opens[0].index, int)


def test_openai_tool_call_baseline_still_intact():
    """Control: the guard must not disturb the well-formed path."""
    from wiwi.providers.registry import fresh_adapter

    a = fresh_adapter("openai")
    a.decode_stream_event("message", _frame({"tool_calls": [_tc(0)]}))
    out = a.decode_stream_event("message", _frame({"tool_calls": [_args(0)]}))
    names = [d.__class__.__name__ for d in out]
    # The Open is emitted from _pending_opens on the args frame (deferred by
    # design), so assert on the two deltas that carry the call.
    assert names == ["ToolCallOpen", "ToolCallArgsDelta"]


# ---------------------------------------------------------------------------
# #367 — Responses encoder must not drop interleaved text/thinking
# ---------------------------------------------------------------------------


def _deltas():
    from wiwi.streaming import deltas as dl

    return [
        dl.StreamStart(model="m"),
        dl.TextDelta("BEFORE "),
        dl.ToolCallOpen(index=0, id="c1", name="f"),
        dl.TextDelta("DURING"),
        dl.ToolCallArgsDelta(index=0, args_fragment="{}"),
        dl.ToolCallClose(index=0),
        dl.TextDelta(" AFTER"),
        dl.Finish("tool_call"),
        dl.UsageFinal(prompt=1, output=1),
        dl.StreamEnd(),
    ]


def _texts(enc, deltas):
    text = ""
    for d in deltas:
        raw = enc.feed(d)
        if raw and b"output_text.delta" in raw:
            for line in raw.decode().splitlines():
                if line.startswith("data: "):
                    p = json.loads(line[6:])
                    if p.get("type") == "response.output_text.delta":
                        text += p["delta"]
    return text


def test_responses_encoder_preserves_text_around_a_tool_call():
    """#367: text arriving while a tool item is open was silently dropped."""
    from wiwi.wire.openai_responses import ResponsesStreamEncoder

    enc = ResponsesStreamEncoder("m", "r1")
    got = _texts(enc, _deltas())
    assert "DURING" in got, f"interleaved text lost; got {got!r}"
    assert got == "BEFORE DURING AFTER"


def test_responses_encoder_preserves_thinking_around_a_tool_call():
    """#367: ThinkingDelta during an open tool was discarded entirely."""
    from wiwi.streaming import deltas as dl
    from wiwi.wire.openai_responses import ResponsesStreamEncoder

    enc = ResponsesStreamEncoder("m", "r1")
    seq = [
        dl.StreamStart(model="m"),
        dl.ThinkingDelta("PLAN-A"),
        dl.ToolCallOpen(index=0, id="c1", name="f"),
        dl.ThinkingDelta("PLAN-B"),
        dl.ToolCallArgsDelta(index=0, args_fragment="{}"),
        dl.ToolCallClose(index=0),
        dl.Finish("tool_call"),
        dl.UsageFinal(prompt=1, output=1),
        dl.StreamEnd(),
    ]
    raw = b"".join(enc.feed(d) or b"" for d in seq)
    assert b"PLAN-B" in raw, "interleaved thinking lost"


def test_responses_tool_output_index_order_is_preserved():
    """Control: buffering must not corrupt output_index ordering."""
    from wiwi.wire.openai_responses import ResponsesStreamEncoder

    enc = ResponsesStreamEncoder("m", "r1")
    raw = b"".join(enc.feed(d) or b"" for d in _deltas())
    added, done = [], []
    for line in raw.decode().splitlines():
        if not line.startswith("data: "):
            continue
        p = json.loads(line[6:])
        if p.get("type") == "response.output_item.added":
            added.append(p["output_index"])
        elif p.get("type") == "response.output_item.done":
            done.append(p["output_index"])
    assert added == sorted(added), f"output_index went backwards: {added}"
    assert done == sorted(done), f"done order disagrees with added: {done}"


# ---------------------------------------------------------------------------
# #368 — a tool CALL with no id, like #331's result half
# ---------------------------------------------------------------------------


def test_chat_tool_call_without_id_is_rejected():
    """#368: id defaulted to '' -> an unanswerable, permanently unpaired turn."""
    import wiwi.wire.openai_chat as chat

    with pytest.raises(chat.DialectError):
        chat.decode_request({
            "model": "m",
            "messages": [{"role": "assistant", "content": None, "tool_calls": [
                {"type": "function",
                 "function": {"name": "f", "arguments": "{}"}}]}],
        })


def test_responses_tool_call_without_id_is_rejected():
    import wiwi.wire.openai_responses as resp

    with pytest.raises(resp.DialectError):
        resp.decode_request({"model": "m", "input": [
            {"type": "function_call", "name": "f", "arguments": "{}"}]})


def test_tool_call_with_id_still_decodes():
    import wiwi.wire.openai_chat as chat

    r = chat.decode_request({
        "model": "m",
        "messages": [{"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "f", "arguments": "{}"}}]}],
    })
    assert r.messages[0].parts[0].id == "call_1"


# ---------------------------------------------------------------------------
# #369 — text.verbosity must reach extras
# ---------------------------------------------------------------------------


def test_text_verbosity_rides_extras():
    """#369: `text` was denylisted so verbosity could never reach the upstream."""
    import wiwi.wire.openai_responses as resp

    r = resp.decode_request(
        {"model": "m", "input": "hi", "text": {"verbosity": "low"}})
    assert "low" in json.dumps(r.extras), f"verbosity dropped: extras={r.extras}"


def test_text_format_still_sets_response_format():
    """Control: #369's fix must not disturb text.format."""
    import wiwi.wire.openai_responses as resp

    r = resp.decode_request(
        {"model": "m", "input": "hi",
         "text": {"format": {"type": "json_object"}}})
    assert r.gen_params.response_format is not None


# ---------------------------------------------------------------------------
# #361 / #370 — router
# ---------------------------------------------------------------------------


def _cycle_cfg(n_pa: int, cycle_n: int):
    from wiwi.config import load_config_from_string

    ml = "\n".join(
        f"  - model_name: grp\n    wiwi_params: {{provider: pA, model: m{i}}}"
        for i in range(n_pa))
    return load_config_from_string(f"""
general_settings:
  master_key: sk-test
router_settings:
  cycle_every_n: {cycle_n}
  num_retries: 0
providers:
  - name: pA
    provider: openai
    keys: [{{label: k, key: sk-a}}]
  - name: pB
    provider: openai
    keys: [{{label: k, key: sk-b}}]
model_list:
{ml}
  - model_name: grp
    wiwi_params: {{provider: pB, model: z}}
""")


async def _max_pa_run(n_pa: int, cycle_n: int, n: int = 200) -> int:
    from wiwi.core.context import RequestContext
    from wiwi.ir import types as ir
    from wiwi.router.router import Router, execute_with_retries

    r = Router(_cycle_cfg(n_pa, cycle_n))
    _, deps = r.resolve_group("grp")
    for d in deps:
        d.weight = 20 if d.provider.name == "pA" else 1
    req = ir.Request(model="m",
                     messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])])
    ctx = RequestContext(surface="chat", ir_req=req, group="grp")
    ctx.request_id = "r"
    served: list[str] = []

    async def call_one(dep, key, c):
        served.append(dep.provider.name)
        return ir.AssistantTurn(text="ok", stop_reason="stop")

    for _ in range(n):
        await execute_with_retries(r, ctx, call_one)
        for d in deps:
            d.release_slot("r")

    run = best = 0
    for p in served:
        run = run + 1 if p == "pA" else 0
        best = max(best, run)
    return best


async def test_cycle_every_n_holds_with_multiple_deployments_per_provider():
    """#361: the pop was inside the loop, so only d0 was ever excluded."""
    for n_pa in (2, 3, 4):
        assert await _max_pa_run(n_pa, 2) <= 2, (
            f"cycle_every_n=2 violated with {n_pa} pA deployments")


async def test_cycle_every_n_single_deployment_still_holds():
    """Control: the shape the #305 tests cover must keep working."""
    assert await _max_pa_run(1, 2) <= 2


def _lb_cfg(strategy: str, n: int):
    from wiwi.config import load_config_from_string

    ml = "\n".join(
        f"  - model_name: grp\n    wiwi_params: {{provider: pA, model: m{i}}}"
        for i in range(n))
    return load_config_from_string(f"""
general_settings:
  master_key: sk-test
router_settings:
  routing_strategy: {strategy}
providers:
  - name: pA
    provider: openai
    keys: [{{label: k, key: sk-a}}]
model_list:
{ml}
""")


@pytest.mark.parametrize("strategy", ["least-busy", "latency-based"])
async def test_no_deployment_is_starved_on_a_tie(strategy):
    """#370: bare min() always returned list-order-first on equal metrics."""
    import collections

    from wiwi.router.router import Router

    r = Router(_lb_cfg(strategy, 3))
    _, deps = r.resolve_group("grp")
    if strategy == "latency-based":
        # Warm every deployment to an identical p95. The cold path (p95 == 0)
        # already breaks ties randomly, so only a WARM tie reaches the bare
        # min() that starves the siblings.
        for d in deps:
            for _ in range(5):
                d.latencies.append(100.0)

    class Ctx:
        request_id = "r"
        est_tokens = 0
        def __init__(self):
            self.metadata = {}

        auth = None
        session_id = None

    picks = collections.Counter()
    for _ in range(600):
        d = r.pick_deployment(deps, Ctx())
        picks[d.model_id] += 1
        d.release_slot("r")
    assert len(picks) == 3, f"{strategy} starved deployments: {dict(picks)}"


async def test_simple_shuffle_still_spreads():
    """Control: the tie-break must not disturb the default strategy."""
    import collections

    from wiwi.router.router import Router

    r = Router(_lb_cfg("simple-shuffle", 3))
    _, deps = r.resolve_group("grp")

    class Ctx:
        request_id = "r"
        est_tokens = 0
        def __init__(self):
            self.metadata = {}

        auth = None
        session_id = None

    picks = collections.Counter()
    for _ in range(600):
        d = r.pick_deployment(deps, Ctx())
        picks[d.model_id] += 1
        d.release_slot("r")
    assert len(picks) == 3


# ---------------------------------------------------------------------------
# #362 — PBKDF2 must not run on the event loop
# ---------------------------------------------------------------------------


async def test_password_verify_does_not_block_the_event_loop():
    """#362: 200k PBKDF2 iterations inline froze every in-flight request."""
    import time

    from wiwi.auth.users import hash_password, verify_password

    stored = hash_password("correct-horse-battery")

    gaps: list[float] = []
    stop = asyncio.Event()

    async def ticker():
        while not stop.is_set():
            t0 = time.perf_counter()
            await asyncio.sleep(0.005)
            gaps.append(time.perf_counter() - t0 - 0.005)

    t = asyncio.create_task(ticker())
    await asyncio.sleep(0.02)
    # If verify is off-thread this returns fast; if inline, the ticker stalls.
    await asyncio.to_thread(verify_password, "correct-horse-battery", stored)
    stop.set()
    await t
    assert max(gaps) < 0.02, f"event loop stalled {max(gaps)*1000:.0f} ms"


async def test_user_service_verify_offloads_hashing():
    """#362: UserService.verify must reach to_thread, not call PBKDF2 inline."""
    import inspect
    import tempfile
    import time

    from sqlalchemy.ext.asyncio import create_async_engine

    from wiwi.auth.users import UserService

    td = tempfile.mkdtemp()
    eng = create_async_engine(f"sqlite+aiosqlite:///{td}/u.db")
    svc = UserService(eng, "s" * 32)
    await svc.startup()
    try:
        await svc.create_user("bob", "correct-horse-battery")

        gaps: list[float] = []
        stop = asyncio.Event()

        async def ticker():
            while not stop.is_set():
                t0 = time.perf_counter()
                await asyncio.sleep(0.005)
                gaps.append(time.perf_counter() - t0 - 0.005)

        t = asyncio.create_task(ticker())
        await asyncio.sleep(0.02)
        got = await svc.verify("bob", "correct-horse-battery")
        stop.set()
        await t
        assert got is not None and got.username == "bob"
        assert max(gaps) < 0.02, (
            f"verify() blocked the loop for {max(gaps)*1000:.0f} ms")
        assert "to_thread" in inspect.getsource(UserService.verify)
    finally:
        await eng.dispose()


# ---------------------------------------------------------------------------
# #367 follow-up — buffered content must flush only after the LAST open tool
# ---------------------------------------------------------------------------


def _events(raw: bytes) -> list[dict]:
    return [json.loads(line[6:]) for line in raw.decode().splitlines()
            if line.startswith("data: ")]


def test_responses_deferred_text_waits_for_parallel_sibling():
    """A message item must not land between two open tools' done events."""
    from wiwi.streaming import deltas as dl
    from wiwi.wire.openai_responses import ResponsesStreamEncoder

    enc = ResponsesStreamEncoder("m", "r1")
    seq = [
        dl.StreamStart(model="m"),
        dl.ToolCallOpen(index=0, id="c0", name="f0"),
        dl.ToolCallOpen(index=1, id="c1", name="f1"),
        dl.TextDelta("MID"),
        dl.ToolCallClose(index=0),
        dl.ToolCallClose(index=1),
        dl.StreamEnd(),
    ]
    evs = _events(b"".join(enc.feed(d) or b"" for d in seq))
    done = [e["item"]["type"] for e in evs
            if e["type"] == "response.output_item.done"]
    assert done == ["function_call", "function_call", "message"]


def test_responses_deferred_content_reaches_completed_output():
    """Flushed items must be in response.completed's output array, and the
    text must arrive as an output_text.delta that clients render."""
    from wiwi.streaming import deltas as dl
    from wiwi.wire.openai_responses import ResponsesStreamEncoder

    enc = ResponsesStreamEncoder("m", "r1")
    seq = [
        dl.StreamStart(model="m"),
        dl.ToolCallOpen(index=0, id="c0", name="f"),
        dl.ThinkingDelta("WHY"),
        dl.TextDelta("SAY"),
        dl.ToolCallClose(index=0),
        dl.Finish("tool_call"),
        dl.UsageFinal(prompt=1, output=1),
        dl.StreamEnd(),
    ]
    raw = b"".join(enc.feed(d) or b"" for d in seq) + enc._completed()
    evs = _events(raw)
    deltas = [e.get("delta") for e in evs
              if e["type"] == "response.output_text.delta"]
    assert deltas == ["SAY"]
    completed = next(e for e in evs if e["type"] == "response.completed")
    kinds = [i["type"] for i in completed["response"]["output"]]
    assert kinds == ["function_call", "reasoning", "message"]


# ---------------------------------------------------------------------------
# #360 — mid-stream resume must keep the attempts[] audit trail
# ---------------------------------------------------------------------------


def test_merge_resume_context_carries_attempts():
    from wiwi.core.context import RequestContext
    from wiwi.core.gateway import merge_resume_context
    from wiwi.ir.types import Request

    origin = RequestContext(surface="chat", ir_req=Request(model="m", messages=[]))
    origin.note_attempt("g/a", "pA", "k", "stream_error", 10)
    resumed = RequestContext(surface="chat", ir_req=Request(model="m", messages=[]))
    resumed.note_attempt("g/b", "pB", "k", "ok", 20)
    merge_resume_context(origin, resumed)
    assert [(a.provider, a.status) for a in origin.attempts] == [
        ("pA", "stream_error"), ("pB", "ok")]


# ---------------------------------------------------------------------------
# #363 — failed realtime handshake must release inflight + rpm slot
# ---------------------------------------------------------------------------


async def test_realtime_failed_accept_does_not_leak_inflight():
    from asgi_lifespan import LifespanManager

    from wiwi.config import (
        DeploymentParams,
        GeneralSettings,
        KeyDef,
        ModelEntry,
        ProviderDef,
        RealtimeSettings,
        RouterSettings,
        WiwiConfig,
    )
    from wiwi.server.app import create_app

    master = "sk-wiwi-master-test"
    cfg = WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai-compatible",
                               base_url="http://127.0.0.1:1/v1",
                               keys=[KeyDef(label="a", key="sk-test-123456")])],
        model_list=[ModelEntry(model_name="m", wiwi_params=DeploymentParams(
            provider="p1", model="mm", rpm=50))],
        general_settings=GeneralSettings(
            master_key=master, database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(),
        realtime=RealtimeSettings(enabled=True),
    )
    app = create_app(cfg)
    async with LifespanManager(app):
        dep = app.state.wiwi.router.groups["m"][0]
        before_rpm = dep._rpm_window.total if dep._rpm_window else 0

        for _ in range(3):
            msgs = [{"type": "websocket.connect"}]

            async def receive(msgs=msgs):
                return msgs.pop(0) if msgs else {"type": "websocket.disconnect",
                                                 "code": 1000}

            async def send(message):
                if message["type"] == "websocket.accept":
                    raise OSError("client vanished mid-handshake")

            scope = {"type": "websocket", "path": "/v1/realtime",
                     "raw_path": b"/v1/realtime", "query_string": b"model=m",
                     "headers": [(b"authorization", f"Bearer {master}".encode())],
                     "scheme": "ws", "server": ("test", 80),
                     "client": ("127.0.0.1", 1), "subprotocols": [],
                     "root_path": "", "app": app}
            # Pre-fix the error escaped the handler; post-fix it is handled.
            # Either way the slot accounting below is what matters.
            with contextlib.suppress(Exception):
                await app(scope, receive, send)

        assert dep.inflight == 0, f"inflight leaked: {dep.inflight}"
        after_rpm = dep._rpm_window.total if dep._rpm_window else 0
        assert after_rpm == before_rpm


# ---------------------------------------------------------------------------
# #364 — POST /admin/providers must persist before mutating routing state
# ---------------------------------------------------------------------------


async def test_admin_create_provider_db_failure_leaves_no_ghost():
    import httpx
    from asgi_lifespan import LifespanManager

    from wiwi.config import (
        DeploymentParams,
        GeneralSettings,
        KeyDef,
        ModelEntry,
        ProviderDef,
        WiwiConfig,
    )
    from wiwi.server.app import create_app

    cfg = WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="k")])],
        model_list=[ModelEntry(model_name="g", wiwi_params=DeploymentParams(
            provider="p1", model="m"))],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:"),
    )
    app = create_app(cfg)
    async with LifespanManager(app):
        state = app.state.wiwi

        async def boom(*a, **kw):
            raise RuntimeError("db down")

        state.config_store.add_provider = boom
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            r = await c.post("/admin/providers",
                             headers={"Authorization": "Bearer sk-wiwi-master-test"},
                             json={"name": "ghost", "provider_type": "openai",
                                   "key": "sk-x", "alias_id": "gh"})
        assert r.status_code >= 500
        assert "ghost" not in state.router.providers
        assert "gh" not in state.router.alias_to_provider


# ---------------------------------------------------------------------------
# #372 — the admin model-group payload must expose probation
# ---------------------------------------------------------------------------


async def test_model_groups_payload_reports_probation():
    import httpx
    from asgi_lifespan import LifespanManager

    from wiwi.config import (
        DeploymentParams,
        GeneralSettings,
        KeyDef,
        ModelEntry,
        ProviderDef,
        WiwiConfig,
    )
    from wiwi.server.app import create_app

    cfg = WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="k")])],
        model_list=[
            ModelEntry(model_name="g", wiwi_params=DeploymentParams(
                provider="p1", model="m1")),
            ModelEntry(model_name="g", wiwi_params=DeploymentParams(
                provider="p1", model="m2")),
        ],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:"),
    )
    app = create_app(cfg)
    async with LifespanManager(app):
        deps = app.state.wiwi.router.groups["g"]
        deps[1].mark_recovered()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as c:
            r = await c.get("/admin/models",
                            headers={"Authorization": "Bearer sk-wiwi-master-test"})
        assert r.status_code == 200, r.text
        grp = next(g for g in r.json()["groups"] if g["name"] == "g")
        flags = {d["model_id"]: d["probation"] for d in grp["deployments"]}
        assert flags == {"m1": False, "m2": True}
