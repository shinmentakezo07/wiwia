"""Round 118 regressions — AUDIT #331.

A tool-result message missing its call id must be rejected at the codec
boundary (400), not forwarded upstream with an empty/placeholder id.
"""

from __future__ import annotations

import pytest

from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_responses as orr
from wiwi.wire.openai_chat import DialectError


def test_chat_tool_message_missing_tool_call_id_rejected():
    body = {"model": "m", "messages": [{"role": "tool", "content": "ok"}]}
    with pytest.raises(DialectError, match="tool_call_id"):
        oc.decode_request(body)


def test_chat_tool_message_empty_tool_call_id_rejected():
    body = {"model": "m", "messages": [{"role": "tool", "tool_call_id": "", "content": "ok"}]}
    with pytest.raises(DialectError, match="tool_call_id"):
        oc.decode_request(body)


def test_chat_tool_message_non_string_tool_call_id_rejected():
    body = {"model": "m", "messages": [{"role": "tool", "tool_call_id": 42, "content": "ok"}]}
    with pytest.raises(DialectError, match="tool_call_id"):
        oc.decode_request(body)


def test_chat_tool_message_valid_id_still_accepted():
    body = {"model": "m", "messages": [{"role": "tool", "tool_call_id": "call_1", "content": "ok"}]}
    req = oc.decode_request(body)
    assert req.messages[0].parts[0].tool_use_id == "call_1"


def test_anthropic_tool_result_missing_tool_use_id_rejected():
    body = {"model": "m", "max_tokens": 1,
            "messages": [{"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}]}
    with pytest.raises(DialectError, match="tool_use_id"):
        am.decode_request(body)


def test_anthropic_tool_result_empty_tool_use_id_rejected():
    body = {"model": "m", "max_tokens": 1,
            "messages": [{"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "", "content": "ok"}]}]}
    with pytest.raises(DialectError, match="tool_use_id"):
        am.decode_request(body)


def test_anthropic_tool_result_valid_id_still_accepted():
    body = {"model": "m", "max_tokens": 1,
            "messages": [{"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}]}]}
    req = am.decode_request(body)
    assert req.messages[0].parts[0].tool_use_id == "toolu_1"


def test_responses_function_call_output_missing_call_id_rejected():
    body = {"model": "m", "input": [{"type": "function_call_output", "output": "ok"}]}
    with pytest.raises(DialectError, match="call_id"):
        orr.decode_request(body)


def test_responses_function_call_output_empty_call_id_rejected():
    body = {"model": "m", "input": [
        {"type": "function_call_output", "call_id": "", "output": "ok"}]}
    with pytest.raises(DialectError, match="call_id"):
        orr.decode_request(body)


def test_responses_function_call_output_valid_id_still_accepted():
    body = {"model": "m", "input": [
        {"type": "function_call_output", "call_id": "call_1", "output": "ok"}]}
    req = orr.decode_request(body)
    assert req.messages[0].parts[0].tool_use_id == "call_1"
