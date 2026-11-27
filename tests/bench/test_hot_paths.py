"""Hot-path micro-benchmarks. Excluded from the normal run; use
`python3 -m pytest tests/bench --benchmark-only -p no:randomly`.
"""

from wiwi.streaming.partial_json import parse_partial
from wiwi.streaming.sse import split_sse_lines, sse_frame
from wiwi.wire.openai_chat import decode_request

_CHAT_BODY = {
    "model": "gpt-4o",
    "stream": True,
    "max_tokens": 512,
    "messages": [
        {"role": "system", "content": "You are helpful."},
        *[
            {"role": "user" if i % 2 == 0 else "assistant", "content": "hello " * 50}
            for i in range(20)
        ],
    ],
    "tools": [
        {
            "type": "function",
            "function": {
                "name": f"tool_{i}",
                "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
            },
        }
        for i in range(10)
    ],
}

_PARTIAL = '{"query": "search term", "filters": {"lang": "en", "tags": ["a", "b", "c'
_SSE = "".join(f'data: {{"i": {i}, "text": "token"}}\n\n' for i in range(200))


def test_bench_chat_decode(benchmark):
    benchmark(decode_request, _CHAT_BODY)


def test_bench_partial_json(benchmark):
    benchmark(parse_partial, _PARTIAL)


def test_bench_sse_frame(benchmark):
    benchmark(sse_frame, "message", '{"delta": "hello world"}')


def test_bench_split_sse_lines(benchmark):
    benchmark(split_sse_lines, _SSE)
