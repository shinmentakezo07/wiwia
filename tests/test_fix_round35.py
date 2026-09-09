"""Round-35 regression tests: automatic Anthropic prompt-cache breakpoints.

Scope (all reproduced against source before fixing):

``wiwi`` only ever *passed through* ``cache_control``. A client that did not
send it got no prompt caching at all and paid full input price on every
request, even when the deployment had a large static system prompt plus
stable tool definitions — exactly the shape prompt caching exists for.

This round adds opt-in breakpoint injection. The design is driven by two
behaviours in the Anthropic docs that make the naive approach actively
harmful:

1. **A breakpoint on a block that changes every request never gets a read.**
   Cache writes happen *only at the breakpoint*, and reads walk back looking
   for entries prior requests wrote. Marking the final user message (which
   differs per request) means every request writes a new entry and none ever
   reads one — you pay the 1.25x write premium forever. The docs call this
   out as the "common mistake". Top-level *automatic* caching has the same
   flaw for a static-system + varying-message prompt, because it places the
   breakpoint on the last cacheable block.

   So injection marks the **stable prefix only**: the last tool definition
   and the last system block. Never the trailing user turn.

2. **Below a model-specific minimum the prompt is silently not cached**
   (512-4096 tokens depending on model). Marking a short prefix is not just
   useless, it is a write that will never be read. So injection estimates
   the prefix token count and skips when under the threshold.

Caller-supplied ``cache_control`` always wins: we never overwrite a marker
the client placed deliberately.
"""

from __future__ import annotations

import pytest

from wiwi.ir import types as ir
from wiwi.providers import anthropic_adapter as aa

EPHEMERAL = {"type": "ephemeral"}


def _req(text: str = "hi", **gen) -> ir.Request:
    return ir.Request(
        model="m",
        messages=[ir.Message(role="user", parts=[ir.TextPart(text)])],
        gen_params=ir.GenParams(**gen),
    )


def _long(n_chars: int = 8000) -> str:
    """A stable prefix comfortably over any model's cacheable minimum."""
    return "x" * n_chars


def _with_system(req: ir.Request, text: str) -> ir.Request:
    req.messages.insert(0, ir.Message(role="system", parts=[ir.TextPart(text)]))
    return req


def _encode(req, **params):
    return aa.AnthropicAdapter().encode_request(req, "claude-sonnet-4-5", params)


# ---------------------------------------------------------------------------
# 1. Off by default
# ---------------------------------------------------------------------------

def test_no_breakpoints_without_optin():
    req = _with_system(_req(), _long())
    body = _encode(req)
    assert "cache_control" not in body
    assert isinstance(body["system"], str)


def test_no_breakpoints_when_disabled_explicitly():
    req = _with_system(_req(), _long())
    body = _encode(req, prompt_cache=False)
    assert "cache_control" not in body


# ---------------------------------------------------------------------------
# 2. Marks the stable prefix, never the varying tail
# ---------------------------------------------------------------------------

def test_marks_system_block_when_enabled():
    req = _with_system(_req(), _long())
    body = _encode(req, prompt_cache=True)
    system = body["system"]
    assert isinstance(system, list), "block form is required to carry markers"
    assert system[-1]["cache_control"] == EPHEMERAL


def test_never_marks_the_trailing_user_message():
    """The whole point: the last user turn varies, marking it kills reads."""
    req = _with_system(_req("what time is it?"), _long())
    body = _encode(req, prompt_cache=True)
    last = body["messages"][-1]
    assert last["role"] == "user"
    for blk in last["content"]:
        assert "cache_control" not in blk


def test_marks_last_tool_definition():
    short = _long(200)
    req = _with_system(_req(), short)
    req.tools = [ir.Tool(name="a", description="d1"),
                 ir.Tool(name="b", description="d2")]
    req.tools[0].parameters_json_schema = {"type": "object",
                                           "properties": {"p" + "x" * 4000: {}}}
    body = _encode(req, prompt_cache=True)
    tools = body["tools"]
    assert tools[-1]["cache_control"] == EPHEMERAL
    # Earlier tools are inside the prefix, not breakpoints themselves.
    assert "cache_control" not in tools[0]


# ---------------------------------------------------------------------------
# 3. Minimum-token threshold
# ---------------------------------------------------------------------------

def test_skips_when_prefix_under_minimum():
    req = _with_system(_req(), "tiny system prompt")
    body = _encode(req, prompt_cache=True)
    assert isinstance(body["system"], str)
    assert "cache_control" not in body


def test_skips_when_no_system_and_no_tools():
    body = _encode(_req(), prompt_cache=True)
    assert "cache_control" not in body


@pytest.mark.parametrize("n_chars,expect_marked", [
    (100, False),      # ~25 tokens, far under any minimum
    (200_000, True),   # ~50k tokens, over every model's minimum
])
def test_threshold_boundary(n_chars, expect_marked):
    req = _with_system(_req(), _long(n_chars))
    body = _encode(req, prompt_cache=True)
    marked = isinstance(body["system"], list) and bool(
        body["system"][-1].get("cache_control"))
    assert marked is expect_marked


def test_custom_threshold_respected():
    req = _with_system(_req(), _long(10_000))
    # Absurdly high minimum: nothing qualifies.
    body = _encode(req, prompt_cache=True, prompt_cache_min_tokens=10_000_000)
    assert isinstance(body["system"], str)


# ---------------------------------------------------------------------------
# 4. Caller-supplied markers always win
# ---------------------------------------------------------------------------

def test_existing_system_cache_control_preserved_not_duplicated():
    req = _with_system(_req(), _long())
    req.messages[0].parts[0].cache_control = {"type": "ephemeral", "ttl": "1h"}
    body = _encode(req, prompt_cache=True)
    system = body["system"]
    assert system[-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_existing_tool_cache_control_preserved():
    req = _with_system(_req(), _long(200))
    req.tools = [ir.Tool(name="a")]
    req.tools[0].cache_control = {"type": "ephemeral", "ttl": "1h"}
    req.tools[0].parameters_json_schema = {"type": "object"}
    body = _encode(req, prompt_cache=True)
    assert body["tools"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_client_top_level_cache_control_still_forwarded():
    req = _with_system(_req(), _long())
    req.extras["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
    body = _encode(req, prompt_cache=True)
    assert body["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


# ---------------------------------------------------------------------------
# 5. Breakpoint budget
# ---------------------------------------------------------------------------

def test_at_most_two_injected_breakpoints():
    """Tools + system. Leaving room for the caller's own markers."""
    req = _with_system(_req(), _long())
    tools = []
    for i in range(3):
        t = ir.Tool(name=f"t{i}")
        t.parameters_json_schema = {"type": "object"}
        tools.append(t)
    req.tools = tools
    body = _encode(req, prompt_cache=True)
    markers = 0
    for t in body.get("tools", []):
        markers += "cache_control" in t
    system = body["system"]
    if isinstance(system, list):
        markers += sum("cache_control" in b for b in system)
    assert markers <= 2


def test_client_with_breakpoints_never_gets_ours_added():
    """Invariant behind the 4-breakpoint budget: if the caller marked
    anything, we add nothing — so total can never exceed 4."""
    req = _with_system(_req(), _long())
    req.messages[0].parts[0].cache_control = EPHEMERAL
    body = _encode(req, prompt_cache=True)
    count = sum("cache_control" in b for b in body["system"])
    assert count == 1, "only the caller's marker, none injected"


def test_no_injection_when_client_used_all_slots():
    """4 client breakpoints already: adding more would 400."""
    req = _with_system(_req(), _long())
    req.messages[0].parts[0].cache_control = EPHEMERAL
    tools = []
    for i in range(3):
        t = ir.Tool(name=f"t{i}")
        t.cache_control = EPHEMERAL
        t.parameters_json_schema = {"type": "object"}
        tools.append(t)
    req.tools = tools
    body = _encode(req, prompt_cache=True)
    # Client's 4 markers intact, none of ours added.
    count = sum("cache_control" in t for t in body["tools"])
    count += sum("cache_control" in b for b in body["system"])
    assert count == 4


# ---------------------------------------------------------------------------
# 6. Response-format instruction must not break the prefix
# ---------------------------------------------------------------------------

def test_json_instruction_appended_after_breakpoint():
    """The injected instruction is per-schema, so it must sit outside the
    cached prefix rather than being merged into the marked block."""
    req = _with_system(_req(), _long())
    req.gen_params.response_format = ir.ResponseFormat(type="json_object")
    body = _encode(req, prompt_cache=True)
    system = body["system"]
    assert isinstance(system, list)
    # The marked block is the caller's original text, unmodified.
    assert system[0]["cache_control"] == EPHEMERAL
    assert system[0]["text"] == _long()
    assert len(system) == 2
    assert "cache_control" not in system[1]


# ---------------------------------------------------------------------------
# 7. Prefix size estimation
# ---------------------------------------------------------------------------

def test_prefix_token_estimate_counts_tools_and_system():
    req = _with_system(_req(), _long(4000))
    t = ir.Tool(name="a", description=_long(4000))
    t.parameters_json_schema = {"type": "object"}
    req.tools = [t]
    est = aa._prefix_token_estimate(req)
    assert est > 1500  # ~8000 chars / 4


def test_prefix_token_estimate_ignores_user_turns():
    a = _with_system(_req("short"), _long(4000))
    b = _with_system(_req("a much longer user message here" * 100), _long(4000))
    assert aa._prefix_token_estimate(a) == aa._prefix_token_estimate(b)
