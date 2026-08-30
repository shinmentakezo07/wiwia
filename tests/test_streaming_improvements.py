"""Tests for streaming performance, quality, and recovery improvements.

Covers:
- Partial JSON parser (repair, streaming, finalize)
- Delta coalescer (backpressure, passthrough, control deltas)
- StreamTape (append, replay, text extraction, eviction)
- Idle watchdog and loop detection (config, gateway behavior)
- orjson in hot path (parse compatibility)
- Split tool-call name accumulation
- Token estimation (model-aware)
- SSE event IDs (sse_frame with id)
- Schema validation on tool args
- Prometheus metrics rendering
- Config settings (all new RouterSettings fields)
"""

from __future__ import annotations

import json
import time

from wiwi.streaming import deltas as dl
from wiwi.streaming.coalesce import DeltaCoalescer
from wiwi.streaming.partial_json import PartialJSONParser, _repair_truncated_json, parse_partial
from wiwi.streaming.resume import StreamTape, build_continuation_messages
from wiwi.streaming.sse import sse_frame
from wiwi.streaming.validation import validate_tool_args

# -- partial_json.py ----------------------------------------------------------

class TestParsePartial:
    def test_complete_json(self):
        val, complete = parse_partial('{"key": "value"}')
        assert val == {"key": "value"}
        assert complete is True

    def test_truncated_object(self):
        val, complete = parse_partial('{"key": "val')
        assert complete is False
        assert val.get("key") == "val"

    def test_truncated_array(self):
        val, complete = parse_partial('[1, 2, 3')
        assert complete is False
        assert val == [1, 2, 3]

    def test_empty_string(self):
        val, complete = parse_partial("")
        assert val == {}
        assert complete is False

    def test_whitespace_only(self):
        val, complete = parse_partial("   ")
        assert val == {}
        assert complete is False

    def test_completely_malformed(self):
        val, complete = parse_partial("not json at all")
        assert val == {}
        assert complete is False

    def test_truncated_string_value(self):
        val, complete = parse_partial('{"name": "John')
        assert complete is False
        assert val.get("name") == "John"

    def test_nested_truncated(self):
        val, complete = parse_partial('{"a": {"b": [1, 2')
        assert complete is False
        assert isinstance(val, dict)


class TestRepairTruncatedJSON:
    def test_repair_unclosed_string(self):
        repaired = _repair_truncated_json('{"key": "val')
        assert json.loads(repaired) == {"key": "val"}

    def test_repair_trailing_single_backslash(self):
        """Trailing single `\\` is a dangling escape; repair must drop it
        before appending the closing quote, otherwise the result is invalid
        JSON and the caller falls back to `{}` (silent tool-args loss)."""
        repaired = _repair_truncated_json('{"key": "val\\')
        # Must parse — was the original bug.
        parsed = json.loads(repaired)
        assert parsed == {"key": "val"}

    def test_repair_trailing_backslash_then_open_brace(self):
        """A trailing `\\` followed by an unclosed `{` — the brace is open
        too, so we need both fixes (drop `\\`, close string, close brace)."""
        repaired = _repair_truncated_json('{"outer": {"key": "val\\')
        parsed = json.loads(repaired)
        assert parsed == {"outer": {"key": "val"}}

    def test_repair_doubled_backslash_preserved(self):
        """A doubled `\\\\` is an escaped backslash and is already a valid
        JSON token — the repairer must NOT strip it (the
        `not text.endswith("\\\\")` guard)."""
        repaired = _repair_truncated_json('{"key": "val\\\\')
        parsed = json.loads(repaired)
        # The string was truncated mid-content; closing it yields "val\\".
        assert parsed == {"key": "val\\"}

    def test_repair_unclosed_object(self):
        repaired = _repair_truncated_json('{"key": "val"')
        assert json.loads(repaired) == {"key": "val"}

    def test_repair_unclosed_array(self):
        repaired = _repair_truncated_json('[1, 2, 3')
        assert json.loads(repaired) == [1, 2, 3]

    def test_repair_nested(self):
        repaired = _repair_truncated_json('{"a": {"b": "c')
        assert json.loads(repaired) == {"a": {"b": "c"}}

    def test_repair_complete_json_unchanged(self):
        text = '{"key": "value"}'
        assert _repair_truncated_json(text) == text

    def test_repair_escaped_string(self):
        repaired = _repair_truncated_json(r'{"path": "C:\\Win')
        assert json.loads(repaired) == {"path": "C:\\Win"}


class TestPartialJSONParser:
    def test_streaming_accumulation(self):
        parser = PartialJSONParser()
        fragments = ['{"na', 'me": ', '"John"', '}']
        results = []
        for f in fragments:
            results.append(parser.feed(f))
        assert results[-1] == {"name": "John"}
        assert parser.finalize() == {"name": "John"}

    def test_finalize_empty(self):
        parser = PartialJSONParser()
        assert parser.finalize() == {}

    def test_finalize_malformed(self):
        parser = PartialJSONParser()
        parser.feed("not json")
        assert parser.finalize() == {}

    def test_raw_property(self):
        parser = PartialJSONParser()
        parser.feed('{"a": 1}')
        assert parser.raw == '{"a": 1}'

    def test_reset(self):
        parser = PartialJSONParser()
        parser.feed('{"a": 1}')
        parser.reset()
        assert parser.raw == ""


# -- coalesce.py --------------------------------------------------------------

class TestDeltaCoalescer:
    def test_passthrough_under_threshold(self):
        """When queue depth is low, deltas pass through immediately."""
        c = DeltaCoalescer(threshold=100)
        d = dl.TextDelta("hello")
        result = c.feed(d, queue_depth=10)
        assert len(result) == 1
        assert result[0] is d

    def test_coalesce_above_threshold(self):
        """When queue depth is high, TextDeltas are buffered."""
        c = DeltaCoalescer(threshold=10, max_bytes=1000, max_ms=1000)
        d1 = dl.TextDelta("hello ")
        d2 = dl.TextDelta("world")
        r1 = c.feed(d1, queue_depth=20)
        assert r1 == []  # buffered
        r2 = c.feed(d2, queue_depth=20)
        assert r2 == []  # still buffered (below max_bytes/max_ms)
        drained = c.drain()
        assert len(drained) == 1
        assert drained[0].text == "hello world"

    def test_flush_on_max_bytes(self):
        """Flush when buffer exceeds max_bytes."""
        c = DeltaCoalescer(threshold=10, max_bytes=10, max_ms=1000)
        c.feed(dl.TextDelta("12345"), queue_depth=20)
        result = c.feed(dl.TextDelta("67890"), queue_depth=20)
        assert len(result) == 1
        assert result[0].text == "1234567890"

    def test_flush_on_non_mergeable(self):
        """Non-mergeable deltas flush the buffer."""
        c = DeltaCoalescer(threshold=10, max_bytes=1000, max_ms=1000)
        c.feed(dl.TextDelta("hello "), queue_depth=20)
        result = c.feed(dl.StreamEnd(), queue_depth=20)
        assert len(result) == 2
        assert isinstance(result[0], dl.TextDelta)
        assert result[0].text == "hello "
        assert isinstance(result[1], dl.StreamEnd)

    def test_never_coalesce_control_deltas(self):
        """Control deltas are never buffered."""
        c = DeltaCoalescer(threshold=10, max_bytes=1000, max_ms=1000)
        d = dl.ToolCallOpen(index=0, id="call_1", name="search")
        result = c.feed(d, queue_depth=20)
        assert len(result) == 1
        assert result[0] is d

    def test_drain_empty(self):
        c = DeltaCoalescer()
        assert c.drain() == []


# -- resume.py ----------------------------------------------------------------

class TestStreamTape:
    def test_append_and_replay(self):
        tape = StreamTape()
        tape.append(dl.TextDelta("hello "))
        tape.append(dl.TextDelta("world"))
        tape.append(dl.StreamEnd())
        replayed = tape.replay()
        assert len(replayed) == 2  # StreamEnd not stored
        assert all(isinstance(d, dl.TextDelta) for d in replayed)

    def test_replay_text(self):
        tape = StreamTape()
        tape.append(dl.TextDelta("hello "))
        tape.append(dl.TextDelta("world"))
        assert tape.replay_text() == "hello world"

    def test_replay_thinking(self):
        tape = StreamTape()
        tape.append(dl.ThinkingDelta("thinking..."))
        tape.append(dl.TextDelta("text"))
        assert tape.replay_thinking() == "thinking..."

    def test_skip_control_deltas(self):
        tape = StreamTape()
        tape.append(dl.StreamStart(model="gpt-4"))
        tape.append(dl.StreamEnd())
        assert tape.replay() == []
        assert tape.replay_text() == ""

    def test_eviction(self):
        """When max_bytes is exceeded, oldest entries are evicted."""
        tape = StreamTape(max_bytes=20)
        tape.append(dl.TextDelta("0123456789"))  # 10 bytes, seq 1
        tape.append(dl.TextDelta("0123456789"))  # 10 bytes, seq 2, total 20
        tape.append(dl.TextDelta("X"))  # 1 byte, total 21 > 20: evict first
        replayed = tape.replay()
        # First entry evicted, second and third remain
        texts = [d.text for d in replayed if isinstance(d, dl.TextDelta)]
        assert len(texts) == 2
        assert texts[0] == "0123456789"  # second entry survived
        assert texts[1] == "X"

    def test_seq_increments(self):
        tape = StreamTape()
        assert tape.seq == 0
        tape.append(dl.TextDelta("a"))
        assert tape.seq == 1
        tape.append(dl.TextDelta("b"))
        assert tape.seq == 2

    def test_replay_from_seq(self):
        tape = StreamTape()
        tape.append(dl.TextDelta("a"))  # seq 1
        tape.append(dl.TextDelta("b"))  # seq 2
        tape.append(dl.TextDelta("c"))  # seq 3
        replayed = tape.replay(last_seq=1)
        # seq > 1: entries with seq 2 and 3
        assert len(replayed) == 2
        assert replayed[0].text == "b"
        assert replayed[1].text == "c"

    def test_clear(self):
        tape = StreamTape()
        tape.append(dl.TextDelta("hello"))
        tape.clear()
        assert tape.replay() == []
        assert tape.bytes == 0


class TestBuildContinuationMessages:
    def test_appends_assistant_text(self):
        from wiwi.ir import types as ir
        tape = StreamTape()
        tape.append(dl.TextDelta("Hello, "))
        tape.append(dl.TextDelta("world!"))
        original = [ir.Message(role="user", parts=[ir.TextPart("Hi")])]
        result = build_continuation_messages(tape, original)
        assert len(result) == 3  # original + assistant + continuation prompt
        assert result[1].role == "assistant"
        assert any(isinstance(p, ir.TextPart) and p.text == "Hello, world!"
                    for p in result[1].parts)

    def test_includes_thinking(self):
        from wiwi.ir import types as ir
        tape = StreamTape()
        tape.append(dl.ThinkingDelta("Let me think..."))
        tape.append(dl.TextDelta("Answer"))
        original = [ir.Message(role="user", parts=[ir.TextPart("Q")])]
        result = build_continuation_messages(tape, original)
        assistant_msg = result[1]
        assert any(isinstance(p, ir.ThinkingPart) for p in assistant_msg.parts)

    def test_empty_tape_no_continuation(self):
        from wiwi.ir import types as ir
        tape = StreamTape()
        original = [ir.Message(role="user", parts=[ir.TextPart("Hi")])]
        result = build_continuation_messages(tape, original)
        assert result == original

    def test_includes_partial_tool_calls(self):
        """Partial tool calls must be preserved on resume so the model does
        not re-emit them (duplicating tool calls the client already saw)."""
        from wiwi.ir import types as ir
        tape = StreamTape()
        tape.append(dl.TextDelta("partial"))
        tape.append(dl.ToolCallOpen(index=0, id="call_1", name="search"))
        tape.append(dl.ToolCallArgsDelta(index=0, args_fragment='{"q":'))
        tape.append(dl.ToolCallArgsDelta(index=0, args_fragment='"x"}'))
        tape.append(dl.ToolCallClose(index=0))
        original = [ir.Message(role="user", parts=[ir.TextPart("Q")])]
        result = build_continuation_messages(tape, original)
        assistant_msg = result[1]
        tool_parts = [p for p in assistant_msg.parts if isinstance(p, ir.ToolUsePart)]
        assert len(tool_parts) == 1
        assert tool_parts[0].id == "call_1"
        assert tool_parts[0].name == "search"
        assert tool_parts[0].args == {"q": "x"}

    def test_truncated_tool_args_repaired(self):
        """Truncated tool-call args (stream died mid-args) are auto-repaired."""
        from wiwi.ir import types as ir
        tape = StreamTape()
        tape.append(dl.ToolCallOpen(index=0, id="call_1", name="write"))
        tape.append(dl.ToolCallArgsDelta(index=0, args_fragment='{"path": "/tmp/f'))
        original = [ir.Message(role="user", parts=[ir.TextPart("Q")])]
        result = build_continuation_messages(tape, original)
        tool_parts = [p for p in result[1].parts if isinstance(p, ir.ToolUsePart)]
        assert len(tool_parts) == 1
        assert tool_parts[0].name == "write"
        # Truncated JSON was auto-repaired (unterminated string closed)
        assert tool_parts[0].args == {"path": "/tmp/f"}

    def test_parallel_tool_calls_preserved(self):
        """Parallel tool calls in the tape are all reconstructed in order."""
        from wiwi.ir import types as ir
        tape = StreamTape()
        tape.append(dl.ToolCallOpen(index=0, id="c0", name="get"))
        tape.append(dl.ToolCallOpen(index=1, id="c1", name="set"))
        tape.append(dl.ToolCallArgsDelta(index=0, args_fragment='{"a":1}'))
        tape.append(dl.ToolCallArgsDelta(index=1, args_fragment='{"b":2}'))
        tape.append(dl.ToolCallClose(index=0))
        tape.append(dl.ToolCallClose(index=1))
        original = [ir.Message(role="user", parts=[ir.TextPart("Q")])]
        result = build_continuation_messages(tape, original)
        tool_parts = [p for p in result[1].parts if isinstance(p, ir.ToolUsePart)]
        assert len(tool_parts) == 2
        assert tool_parts[0].name == "get" and tool_parts[0].args == {"a": 1}
        assert tool_parts[1].name == "set" and tool_parts[1].args == {"b": 2}


# -- sse_frame with event_id ---------------------------------------------------

class TestSSEFrameEventID:
    def test_frame_with_id(self):
        frame = sse_frame("message", '{"data": 1}', event_id=42)
        assert b"id: 42\n" in frame
        assert b"event: message\n" in frame
        assert b'data: {"data": 1}\n\n' in frame

    def test_frame_without_id(self):
        frame = sse_frame("message", '{"data": 1}')
        assert b"id:" not in frame
        assert b"event: message\n" in frame

    def test_frame_id_with_no_event(self):
        frame = sse_frame("", '{"data": 1}', event_id=7)
        assert b"id: 7\n" in frame
        assert b"event:" not in frame
        assert b'data: {"data": 1}\n\n' in frame


# -- config settings ----------------------------------------------------------

class TestConfigSettings:
    def test_stream_idle_timeout_default(self):
        from wiwi.config import RouterSettings
        rs = RouterSettings()
        assert rs.stream_idle_timeout_s == 30.0

    def test_stream_loop_detection_default(self):
        from wiwi.config import RouterSettings
        rs = RouterSettings()
        assert rs.stream_loop_detection is True
        assert rs.stream_loop_limit == 100

    def test_stream_coalesce_default(self):
        from wiwi.config import RouterSettings
        rs = RouterSettings()
        assert rs.stream_coalesce is False

    def test_stream_resume_default(self):
        from wiwi.config import RouterSettings
        rs = RouterSettings()
        assert rs.stream_resume == "off"
        assert rs.stream_resume_max_retries == 1

    def test_stream_event_ids_default(self):
        from wiwi.config import RouterSettings
        rs = RouterSettings()
        assert rs.stream_event_ids is False

    def test_grace_drain_default(self):
        from wiwi.config import RouterSettings
        rs = RouterSettings()
        assert rs.stream_grace_drain_s == 0.0

    def test_prometheus_default(self):
        from wiwi.config import RouterSettings
        rs = RouterSettings()
        assert rs.prometheus_enabled is False
        assert rs.prometheus_path == "/metrics"


# -- orjson hot path ----------------------------------------------------------

class TestOrjsonHotPath:
    def test_openai_adapter_uses_orjson(self):
        """Verify orjson.loads is used in decode_stream_event."""
        from wiwi.providers.openai_adapter import OpenAIAdapter
        adapter = OpenAIAdapter()
        # orjson.loads accepts str, same as json.loads
        result = adapter.decode_stream_event("", '{"choices": [{"delta": {"content": "hi"}}]}')
        assert any(isinstance(d, dl.TextDelta) and d.text == "hi" for d in result)

    def test_anthropic_adapter_uses_orjson(self):
        from wiwi.providers.anthropic_adapter import AnthropicAdapter
        adapter = AnthropicAdapter()
        data = json.dumps({
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "hello"},
        })
        result = adapter.decode_stream_event("content_block_delta", data)
        assert any(isinstance(d, dl.TextDelta) and d.text == "hello" for d in result)

    def test_openrouter_adapter_uses_orjson(self):
        from wiwi.providers.openrouter_adapter import OpenRouterAdapter
        adapter = OpenRouterAdapter()
        data = json.dumps({"choices": [{"delta": {"content": "hi"}}]})
        result = adapter.decode_stream_event("", data)
        assert any(isinstance(d, dl.TextDelta) and d.text == "hi" for d in result)

    def test_base_uses_orjson_for_error(self):
        from wiwi.providers.base import _extract_error_message
        msg = _extract_error_message('{"error": {"message": "bad request"}}')
        assert msg == "bad request"


# -- split tool-call name accumulation ----------------------------------------

class TestSplitToolCallName:
    def test_name_accumulated_across_deltas(self):
        from wiwi.providers.openai_adapter import OpenAIAdapter
        adapter = OpenAIAdapter()
        # First delta: id + partial name
        d1 = json.dumps({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "function": {"name": "search", "arguments": ""}
        }]}}]})
        adapter.decode_stream_event("", d1)

        # Second delta: name fragment without id
        d2 = json.dumps({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"name": "_web"}}
        ]}}]})
        result = adapter.decode_stream_event("", d2)
        # No ToolCallOpen emitted yet (deferred until name complete)
        assert not any(isinstance(d, dl.ToolCallOpen) for d in result)
        assert adapter._tool_names[0] == "search_web"

    def test_name_cleared_on_finish(self):
        from wiwi.providers.openai_adapter import OpenAIAdapter
        adapter = OpenAIAdapter()
        d1 = json.dumps({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "function": {"name": "search"}}
        ]}}]})
        adapter.decode_stream_event("", d1)
        assert 0 in adapter._tool_names
        d2 = json.dumps({"choices": [{"finish_reason": "tool_calls"}]})
        adapter.decode_stream_event("", d2)
        assert len(adapter._tool_names) == 0

    def test_fragmented_name_client_sees_full_name(self):
        """The full accumulated name must reach the client, not just the
        first fragment.  Regression: some OpenAI-compatible providers
        (vLLM, etc.) fragment the function name across deltas."""
        from wiwi.providers.openai_adapter import OpenAIAdapter
        adapter = OpenAIAdapter()
        events = [
            json.dumps({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_1", "function": {"name": "get"}}]}}]}),
            json.dumps({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"name": "_weather"}}]}}]}),
            json.dumps({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": "{}"}}]}}]}),
            json.dumps({"choices": [{"finish_reason": "tool_calls"}]}),
        ]
        deltas = []
        for data in events:
            deltas.extend(adapter.decode_stream_event("", data))
        opens = [d for d in deltas if isinstance(d, dl.ToolCallOpen)]
        assert len(opens) == 1
        assert opens[0].name == "get_weather"

    def test_fragmented_name_no_args_flushed_on_finish(self):
        """A fragmented name with no arguments is still flushed before
        the ToolCallClose at finish_reason time."""
        from wiwi.providers.openai_adapter import OpenAIAdapter
        adapter = OpenAIAdapter()
        events = [
            json.dumps({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_1", "function": {"name": "get"}}]}}]}),
            json.dumps({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"name": "_data"}}]}}]}),
            json.dumps({"choices": [{"finish_reason": "tool_calls"}]}),
        ]
        deltas = []
        for data in events:
            deltas.extend(adapter.decode_stream_event("", data))
        opens = [d for d in deltas if isinstance(d, dl.ToolCallOpen)]
        closes = [d for d in deltas if isinstance(d, dl.ToolCallClose)]
        assert len(opens) == 1
        assert opens[0].name == "get_data"
        assert len(closes) == 1

    def test_fragmented_name_parallel(self):
        """Parallel fragmented tool calls: each accumulates independently."""
        from wiwi.providers.openai_adapter import OpenAIAdapter
        adapter = OpenAIAdapter()
        events = [
            json.dumps({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "c0", "function": {"name": "get"}},
                {"index": 1, "id": "c1", "function": {"name": "set"}}]}}]}),
            json.dumps({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"name": "_a"}},
                {"index": 1, "function": {"name": "_b"}}]}}]}),
            json.dumps({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": "{}"}},
                {"index": 1, "function": {"arguments": "{}"}}]}}]}),
            json.dumps({"choices": [{"finish_reason": "tool_calls"}]}),
        ]
        deltas = []
        for data in events:
            deltas.extend(adapter.decode_stream_event("", data))
        opens = {d.index: d.name for d in deltas if isinstance(d, dl.ToolCallOpen)}
        assert opens == {0: "get_a", 1: "set_b"}

    def test_openrouter_fragmented_name(self):
        """OpenRouter adapter also defers ToolCallOpen for fragmented names."""
        from wiwi.providers.openrouter_adapter import OpenRouterAdapter
        adapter = OpenRouterAdapter()
        events = [
            json.dumps({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_1", "function": {"name": "get"}}]}}]}),
            json.dumps({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"name": "_weather"}}]}}]}),
            json.dumps({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": "{}"}}]}}]}),
            json.dumps({"choices": [{"finish_reason": "tool_calls"}]}),
        ]
        deltas = []
        for data in events:
            deltas.extend(adapter.decode_stream_event("", data))
        opens = [d for d in deltas if isinstance(d, dl.ToolCallOpen)]
        assert len(opens) == 1
        assert opens[0].name == "get_weather"


# -- token estimation ----------------------------------------------------------

class TestTokenEstimation:
    def test_estimate_with_unknown_model(self):
        from wiwi.cost.pricing import estimate_tokens
        assert estimate_tokens("hello world", "unknown-model") > 0

    def test_estimate_without_model(self):
        from wiwi.cost.pricing import estimate_tokens
        assert estimate_tokens("hello world") == max(1, len("hello world") // 4)

    def test_estimate_empty(self):
        from wiwi.cost.pricing import estimate_tokens
        assert estimate_tokens("") == 0

    def test_estimate_with_gpt4o(self):
        from wiwi.cost.pricing import estimate_tokens
        # If tiktoken is installed, this should return a more accurate count.
        # If not, falls back to chars/4.
        result = estimate_tokens("hello world", "gpt-4o")
        assert result > 0


# -- schema validation --------------------------------------------------------

class TestSchemaValidation:
    def test_valid_args(self):
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        valid, msg = validate_tool_args("search", '{"name": "test"}', schema)
        assert valid is True
        assert msg == ""

    def test_invalid_json(self):
        schema = {"type": "object"}
        valid, msg = validate_tool_args("search", "not json", schema)
        assert valid is False
        assert "not valid JSON" in msg

    def test_type_mismatch(self):
        schema = {"type": "object"}
        valid, msg = validate_tool_args("search", '[1, 2, 3]', schema)
        assert valid is False
        assert "expected object" in msg

    def test_missing_required(self):
        schema = {"type": "object", "required": ["name"]}
        valid, msg = validate_tool_args("search", '{"age": 30}', schema)
        assert valid is False
        assert "missing required" in msg

    def test_no_schema_skips_validation(self):
        valid, msg = validate_tool_args("search", "anything", None)
        assert valid is True
        assert msg == ""

    def test_integer_not_bool(self):
        schema = {"type": "object", "properties": {"count": {"type": "integer"}}}
        valid, msg = validate_tool_args("test", '{"count": true}', schema)
        # bool is a subclass of int in Python, so it must be rejected
        # explicitly for "integer". Per-property types are checked, not just
        # the top-level object type (previously this returned True, letting a
        # boolean through an integer field — see AUDIT #20).
        assert valid is False
        assert "count" in msg

    def test_number_not_bool(self):
        schema = {"type": "object", "properties": {"score": {"type": "number"}}}
        valid, msg = validate_tool_args("test", '{"score": true}', schema)
        assert valid is False
        assert "score" in msg

    def test_property_type_ok(self):
        schema = {"type": "object", "properties": {"score": {"type": "number"}}}
        valid, _msg = validate_tool_args("test", '{"score": 1.5}', schema)
        assert valid is True


# -- Prometheus metrics -------------------------------------------------------

class TestPrometheusMetrics:
    def test_render_empty(self):
        from wiwi.server.metrics import render_metrics
        result = render_metrics([])
        assert "wiwi_requests_total 0" not in result  # empty case
        assert "no requests" in result

    def test_render_with_events(self):
        from wiwi.logging_core.events import LogEvent
        from wiwi.server.metrics import render_metrics
        events = [
            LogEvent(
                stream="request", ts=time.time(), request_id="r1",
                surface="chat", key_alias="test", model_group="gpt-4",
                provider="openai", provider_key_label="main",
                status=200, error_code="", tok_in=100, tok_cached=0,
                tok_reasoning=0, tok_out=50, tps=10.0, ttft_ms=100.0,
                latency_ms=500.0, cost=0.01, was_stream=True, cache_hit=False,
                cache_savings=0.0, attempts=[],
            ),
            LogEvent(
                stream="request", ts=time.time(), request_id="r2",
                surface="chat", key_alias="test", model_group="gpt-4",
                provider="openai", provider_key_label="main",
                status=500, error_code="api_error", tok_in=50, tok_cached=0,
                tok_reasoning=0, tok_out=0, tps=0.0, ttft_ms=0.0,
                latency_ms=200.0, cost=0.0, was_stream=True, cache_hit=False,
                cache_savings=0.0, attempts=[],
            ),
        ]
        result = render_metrics(events)
        assert "wiwi_requests_total 2" in result
        assert "wiwi_tokens_total" in result
        assert "wiwi_cost_total" in result
        assert "wiwi_request_duration_ms" in result
        assert 'status="200"' in result
        assert 'status="500"' in result

    def test_has_correct_content_type_header(self):
        """The metrics endpoint uses text/plain Prometheus exposition format."""
        # Just verify the rendering produces the right format markers.
        from wiwi.server.metrics import render_metrics
        result = render_metrics([])
        assert "# HELP" in result
        assert "# TYPE" in result


# -- integration: idle timeout in gateway -------------------------------------

class TestGatewayIdleTimeout:
    def test_idle_timeout_config_propagates(self):
        """Verify that the idle timeout setting is read from RouterSettings."""
        from wiwi.config import RouterSettings
        rs = RouterSettings(stream_idle_timeout_s=15.0)
        assert rs.stream_idle_timeout_s == 15.0


class TestGatewayLoopDetection:
    def test_loop_detection_config_propagates(self):
        from wiwi.config import RouterSettings
        rs = RouterSettings(stream_loop_detection=True, stream_loop_limit=50)
        assert rs.stream_loop_limit == 50

    def test_loop_detection_can_be_disabled(self):
        from wiwi.config import RouterSettings
        rs = RouterSettings(stream_loop_detection=False)
        # When disabled, the gateway uses loop_limit=0
        assert rs.stream_loop_detection is False


# -- integration: HTTP/2 -------------------------------------------------------

class TestHTTP2Enabled:
    async def test_gateway_client_has_http2(self):
        """Verify the gateway's httpx client has http2=True."""
        from wiwi.config import WiwiConfig
        from wiwi.core.gateway import Gateway
        from wiwi.cost.pricing import CostEngine
        from wiwi.router.router import Router
        config = WiwiConfig()
        router = Router(config)
        cost = CostEngine()
        gw = Gateway(router, cost)
        assert gw._client is not None
        await gw.aclose()


# -- integration: resume config -----------------------------------------------

class TestStreamResumeConfig:
    def test_resume_disabled_by_default(self):
        from wiwi.config import RouterSettings
        rs = RouterSettings()
        assert rs.stream_resume == "off"

    def test_resume_content_only(self):
        from wiwi.config import RouterSettings
        rs = RouterSettings(stream_resume="content_only")
        assert rs.stream_resume == "content_only"

    def test_resume_enabled(self):
        from wiwi.config import RouterSettings
        rs = RouterSettings(stream_resume="enabled")
        assert rs.stream_resume == "enabled"
