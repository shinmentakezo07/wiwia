"""Round 214: IR gaps — refusal slot (AUDIT #618), tool_result block_type guard (#619)."""

import orjson
import pytest

from wiwi.ir import types as ir
from wiwi.wire import openai_chat, openai_responses


def _adapter():
    from wiwi.providers.openai_adapter import OpenAIAdapter
    return OpenAIAdapter.__new__(OpenAIAdapter)


def _refusal_body(content=None):
    return orjson.dumps({"choices": [{"message": {
        "role": "assistant", "content": content, "refusal": "I can't help with that."},
        "finish_reason": "stop"}]})


def test_adapter_keeps_refusal_separate_and_mirrors_into_text():
    turn = _adapter().decode_response(200, _refusal_body())
    assert turn.refusal == "I can't help with that."
    assert turn.text == "I can't help with that."


def test_adapter_ignores_non_string_refusal():
    body = orjson.dumps({"choices": [{"message": {"content": "hi", "refusal": 5}}]})
    turn = _adapter().decode_response(200, body)
    assert turn.refusal is None and turn.text == "hi"


def test_chat_encoder_emits_refusal_slot_not_content():
    turn = ir.AssistantTurn(text="no", refusal="no")
    out = openai_chat.encode_response(None, turn, "m", "r")
    msg = out["choices"][0]["message"]
    assert msg["refusal"] == "no" and msg["content"] is None


def test_chat_encoder_without_refusal_has_no_refusal_key():
    out = openai_chat.encode_response(None, ir.AssistantTurn(text="hi"), "m", "r")
    assert "refusal" not in out["choices"][0]["message"]


def test_responses_encoder_emits_refusal_content_part():
    turn = ir.AssistantTurn(text="no", refusal="no")
    out = openai_responses.encode_response(None, turn, "m", "r")
    msg = next(i for i in out["output"] if i["type"] == "message")
    assert msg["content"] == [{"type": "refusal", "refusal": "no"}]


@pytest.mark.parametrize("bad", ["junk", 5, None, ""])
def test_tool_result_block_type_degrades(bad):
    assert ir.ToolResultPart("t", "c", block_type=bad).block_type == "tool_result"


@pytest.mark.parametrize("good", ["tool_result", "web_search_tool_result",
                                  "mcp_tool_result", "future_x_tool_result"])
def test_tool_result_block_type_open_family_kept(good):
    assert ir.ToolResultPart("t", "c", block_type=good).block_type == good
