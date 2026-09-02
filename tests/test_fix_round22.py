"""Round-22 regression tests: WorkBuddy adapter injects a leading system prompt.

Regression target: the WorkBuddy / CodeBuddy (Tencent Copilot) upstream rejects
any ``/v2/chat/completions`` request whose ``messages[0]`` is not a ``system``
message (business code 11128, "first message is not system prompt"). A client
that opens a conversation with a bare ``user`` turn — no system prompt — was
forwarded verbatim (the IR -> OpenAI encoder preserves message order), so the
request 400'd. Fix: the WorkBuddy adapter prepends a default system message when
``messages[0]`` is not already ``system``, and leaves an already-system-leading
request untouched.
"""

from __future__ import annotations

from wiwi.ir import types as ir
from wiwi.providers.workbuddy_adapter import WorkBuddyAdapter


def _req(**over) -> ir.Request:
    base = {
        "model": "test-model",
        "messages": [ir.Message(role="user", parts=[ir.TextPart(text="hi")])],
        "stream": True,
    }
    base.update(over)
    return ir.Request(**base)


def test_encode_injects_system_prompt_when_first_message_is_user():
    """A request starting with a user turn must get a leading system message,
    so the upstream's code-11128 check passes; the user turn is preserved."""
    a = WorkBuddyAdapter()
    body = a.encode_request(_req(), "glm-5.3", {"max_tokens": 10, "extra_body": {},
                                                "drop_params": True})
    msgs = body["messages"]
    assert msgs and msgs[0]["role"] == "system"
    assert msgs[0]["content"]  # non-empty default
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"] == "hi"
    assert len(msgs) == 2


def test_encode_leaves_existing_leading_system_untouched():
    """A request that already starts with a system message is not duplicated."""
    a = WorkBuddyAdapter()
    sysmsg = ir.Message(role="system", parts=[ir.TextPart(text="original sys")])
    body = a.encode_request(_req(messages=[
        sysmsg,
        ir.Message(role="user", parts=[ir.TextPart(text="q")]),
    ]), "kimi-k3", {"max_tokens": 10, "extra_body": {}, "drop_params": True})
    msgs = body["messages"]
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == "original sys"
    assert sum(1 for m in msgs if m["role"] == "system") == 1
