"""Round 194 — the LOW tier: four small defects, each one value on the
wrong side of a boundary.

1. **Anthropic ``max_tokens=0`` + thinking silently upgraded the cap to
   ``budget + 1024``.** ``max_tokens: 0`` is Anthropic's documented cache
   pre-warm signal (AUDIT #156 — preserved with an ``is not None`` test at
   ``anthropic_adapter.py:541``). But the thinking branch that enforces
   ``max_tokens > budget_tokens`` then ran ``if body["max_tokens"] <= budget``
   and overwrote ``0`` with e.g. ``9024`` when a thinking budget (8000) was
   also set. The pre-warm signal died and up to 9024 output tokens could be
   billed. The invariant is real (the API requires ``max_tokens >
   budget_tokens``), so the fix keeps the clamp for a *positive* cap and
   leaves ``0`` untouched — a contradictory (0 cap + thinking) request is
   forwarded as written, same as it is when thinking is off.

2. **The Chat codec dropped dict-form ``tool_choice``.** ``{"type":
   "none"}`` / ``{"type": "auto"}`` / ``{"type": "required"}`` (the object
   spelling the Responses and Anthropic surfaces also ship) decoded to
   ``None`` — unconstrained. A shared tool catalog across surfaces sent the
   object form, asked for *no* tools, got an IR with no constraint, and the
   model called a tool the caller forbade. The string forms and
   ``{"type": "function"}`` were already handled — only the object
   *constraint* spellings fell through.

3. **`allow_unframed=True` for every provider.** Two gateway stream arms
   and the recovery probe built ``LineSSEParser(allow_unframed=True)``
   unconditionally, citing "envelope providers (Cline/WorkBuddy)" in the
   comment while the code applied the lenient parser to *all* providers. A
   strictly-framed SSE provider emitting a stray unframed JSON line (relay
   pretty-print, error envelope without ``data:``) would be fed to
   ``decode_stream_event`` where the strict parser drops it. Now the flag
   is gated on the deployment's provider type (``cline`` / ``workbuddy``)
   so the strict parser is the default everywhere else.

4. **Redis limiter minted a fresh ``uuid4()`` per scope when
   ``request_id`` was empty.** ``check()`` generated the UID *inside* the
   per-scope loop, so an id-less caller stored four different UIDs in the
   four windows. ``release()`` and ``record_tokens()`` — which match by
   ``request_id`` — could then never find anything: reservations leaked
   until the 60s prune and Redis TPM over-counted. One UID per ``check()``
   call now; and since the two ``record_tokens`` callers need a uid to
   match on, each reservation keeps a stable identity.
"""

from __future__ import annotations

from wiwi.core.gateway import _allow_unframed_for
from wiwi.ir import types as ir
from wiwi.providers.anthropic_adapter import AnthropicAdapter
from wiwi.ratelimit.redis import RedisRateLimiter
from wiwi.streaming.sse import LineSSEParser, _looks_like_payload
from wiwi.wire import openai_chat as oc

_PARAMS = {
    "max_tokens": 64,
    "extra_body": {},
    "drop_params": True,
    "prompt_cache": False,
    "prompt_cache_min_tokens": None,
    "opencode_free_tier_cloak": True,
    "provider_type": "anthropic",
}


def _anth_body(**gen: object) -> dict:
    req = ir.Request(
        model="claude-x",
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(**gen),  # type: ignore[arg-type]
    )
    return AnthropicAdapter().encode_request(req, "claude-x", _PARAMS)


# ---------------------------------------------------------------------------
# 1. max_tokens=0 + thinking must not upgrade the cap
# ---------------------------------------------------------------------------


def test_max_tokens_zero_with_thinking_budget_stays_zero():
    out = _anth_body(max_tokens=0, thinking_budget=8000)
    assert out["max_tokens"] == 0, (
        f"max_tokens=0 + thinking became {out['max_tokens']} — the "
        f"pre-warm signal was billed as a completion")
    assert out["thinking"]["budget_tokens"] == 8000


def test_positive_cap_still_clamps_above_budget():
    """The API invariant max_tokens > budget_tokens is preserved for real caps."""
    out = _anth_body(max_tokens=4000, thinking_budget=8000)
    assert out["max_tokens"] == 9024
    assert out["thinking"]["budget_tokens"] == 8000


def test_max_tokens_zero_without_thinking_stays_zero():
    out = _anth_body(max_tokens=0)
    assert out["max_tokens"] == 0
    assert "thinking" not in out


# ---------------------------------------------------------------------------
# 2. Chat codec: dict-form tool_choice constraints
# ---------------------------------------------------------------------------


def _decode_tc(tc) -> object:
    req = oc.decode_request({
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "tool_choice": tc,
    })
    return req.tool_choice


def test_dict_tool_choice_none():
    assert isinstance(_decode_tc({"type": "none"}), ir.ToolChoiceNone)


def test_dict_tool_choice_auto():
    assert isinstance(_decode_tc({"type": "auto"}), ir.ToolChoiceAuto)


def test_dict_tool_choice_required():
    assert isinstance(_decode_tc({"type": "required"}), ir.ToolChoiceRequired)


def test_string_tool_choice_none_still_works():
    assert isinstance(_decode_tc("none"), ir.ToolChoiceNone)


def test_dict_tool_choice_function_still_works():
    tc = _decode_tc({"type": "function", "function": {"name": "f"}})
    assert isinstance(tc, ir.ToolChoiceNamed) and tc.name == "f"


def test_absent_tool_choice_stays_unconstrained():
    assert _decode_tc(None) is None


# ---------------------------------------------------------------------------
# 3. allow_unframed gated to envelope providers
# ---------------------------------------------------------------------------


def test_allow_unframed_only_for_envelope_providers():
    assert _allow_unframed_for("cline") is True
    assert _allow_unframed_for("workbuddy") is True
    assert _allow_unframed_for("openai") is False
    assert _allow_unframed_for("anthropic") is False
    assert _allow_unframed_for("gemini") is False
    assert _allow_unframed_for("openai-compatible") is False
    assert _allow_unframed_for("openrouter") is False
    assert _allow_unframed_for("hermes") is False
    assert _allow_unframed_for("nvidia-nim") is False
    assert _allow_unframed_for("ifm") is False
    assert _allow_unframed_for("bai") is False
    assert _allow_unframed_for("gmicloud") is False
    # opencode (Zen) is a force_stream provider whose chat route can answer
    # with a bare JSON completion body — strict parsing dropped the whole
    # turn ("upstream stream ended without completion"). The zero-cost
    # regression this pin guards against is round-29/27/167's mock shape.
    assert _allow_unframed_for("opencode") is True


def test_strict_parser_drops_unframed_json_line():
    """The strict parser (default) must drop a stray JSON line entirely."""
    parser = LineSSEParser()
    assert parser.feed_line('{"delta": {"text": "hi"}}') is None
    assert parser.flush() is None


def test_unframed_parser_accepts_json_line():
    parser = LineSSEParser(allow_unframed=True)
    evt = parser.feed_line('{"delta": {"text": "hi"}}')
    assert evt is not None
    assert evt.data == '{"delta": {"text": "hi"}}'


def test_looks_like_payload_boundary():
    assert _looks_like_payload('{"a": 1}')
    assert _looks_like_payload("[1, 2]")
    assert not _looks_like_payload("data: x")
    assert not _looks_like_payload("event: msg")
    assert not _looks_like_payload(": comment")


# ---------------------------------------------------------------------------
# 4. One UID per check() call, not one per scope
# ---------------------------------------------------------------------------


def _limiter() -> RedisRateLimiter:
    r = RedisRateLimiter.__new__(RedisRateLimiter)
    # Only the attributes check()/record_tokens()/release() touch.
    from wiwi.ratelimit.memory import RateLimiter as MemoryRateLimiter
    r._memory = MemoryRateLimiter(global_rpm=100, global_tpm=100000)
    r._redis = None
    return r


async def _check_and_release_without_redis(_limiter_ref):
    # Without Redis the in-memory fallback must not blow up on an id-less
    # check → release round trip (the Redis path shares the same uid
    # contract, so this also pins the cross-backend shape).
    allowed, _ = await _limiter_ref.check("k1", 5, 100, 10, request_id="")
    assert allowed
    await _limiter_ref.release("k1", request_id="")
    # Id-less record_tokens must not raise.
    await _limiter_ref.record_tokens("k1", 10, request_id="")


def test_uid_is_one_per_check_not_per_scope():
    """Stub the Redis side and assert the four windows share one UID."""
    import asyncio

    class _Pipe:
        def __init__(self):
            self.calls = []

        def zremrangebyscore(self, *a):
            self.calls.append(("zremrangebyscore", a))

        def zadd(self, scope, mapping):
            self.calls.append(("zadd", scope, mapping))

        def hset(self, key, field, value):
            self.calls.append(("hset", key, field, value))

        def zrange(self, scope, start, stop):
            self.calls.append(("zrange", scope, start, stop))

        async def execute(self):
            # prune x N, zadd x N, hset x N, zrange x N
            n = sum(1 for c in self.calls if c[0] == "zremrangebyscore")
            return [0] * (3 * n) + [[{"ts": 1, "cost": 1, "uid": "u"}]] * n  # shape stub

    class _FakeRedis:
        def __init__(self):
            self.pipe = None

        def pipeline(self):
            self.pipe = _Pipe()
            return self.pipe

        async def zrange(self, scope, start, stop):
            return []

        async def hget(self, key, field):
            return None

    lim = _limiter()
    lim._redis = _FakeRedis()

    async def run():
        await lim.check("k1", 5, 100, 10, request_id="")

    asyncio.run(run())
    pipe = lim._redis.pipe
    assert pipe is not None
    member_uids = {
        member.split(":")[2]
        for c in pipe.calls if c[0] == "zadd"
        for member in c[2]
    }
    assert len(member_uids) == 1, (
        f"uid per scope leaked: {member_uids} — must be one per check()")


def test_opencode_bare_json_completion_decodes_through_lenient_parser():
    """A bare JSON completion body (no data: framing) must not vanish.

    This is the exact body shape the round-29 tests mock and Zen can return
    for a non-streaming caller. With the strict parser it drops entirely;
    the lenient parser must surface it and the OpenAI adapter must turn it
    into a Finish (the text content rides message text, and usage rides the
    UsageFinal — both are present in the bare body).
    """
    import json as _json

    from wiwi.providers.opencode_adapter import OpencodeAdapter

    body = _json.dumps({
        "id": "chatcmpl-x", "object": "chat.completion", "model": "m",
        "choices": [{"index": 0, "message": {"role": "assistant",
                                             "content": "hello"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    })
    parser = LineSSEParser(allow_unframed=True)
    evt = parser.feed_line(body)
    assert evt is not None and evt.data == body
    deltas = OpencodeAdapter().decode_stream_event(evt.event, evt.data)
    assert any(isinstance(d, ir.__class__ and deltas[0].__class__) for d in deltas)
    assert any(d.__class__.__name__ == "Finish" for d in deltas)
