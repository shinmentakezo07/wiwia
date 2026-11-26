"""Round 215: partial-JSON parser was quadratic in tool-argument size (AUDIT #620)."""

import json
import random
import time

from wiwi.streaming import partial_json as pj


def _full_rescan_repair(text):
    # Reference: what the parser must equal — a from-scratch scan each time.
    return pj._repair_truncated_json(text, pj._scan_json(text))


def test_resumed_scan_matches_full_scan_at_every_cut():
    rnd = random.Random(215)
    alpha = '{}[]",:\\u0123456789abcdefeE.+-tfn xD83'
    docs = [json.dumps({"a": {"bb": [1, {"cc": "d"}], "ee": 'f"g'}, "h": True,
                        "u": "\u00e9\U0001F600 C:\\u0f", "n": -12.5e-3})]
    docs += ["".join(rnd.choice(alpha) for _ in range(rnd.randint(0, 30)))
             for _ in range(1500)]
    for d in docs:
        p = pj.PartialJSONParser()
        i = 0
        while i < len(d):
            k = rnd.randint(1, 7)
            frag, i = d[i:i + k], i + k
            got = p.feed(frag)
            assert repr(got) == repr(pj.parse_partial(d[:i])[0]), d[:i]
        if d.strip():
            assert pj._repair_truncated_json(d.strip(), p._scanned()) == \
                _full_rescan_repair(d.strip())


def test_head_trim_resets_scan_state(monkeypatch):
    monkeypatch.setattr(pj, "MAX_BUFFER_BYTES", 32)
    p = pj.PartialJSONParser()
    for frag in ['{"a": "', "x" * 20, '", "b": [1, 2', ", 3]}"]:
        p.feed(frag)
    assert repr(p.finalize()) == repr(pj.PartialJSONParser().finalize() if not p.raw.strip()
                                      else _final(p.raw))


def _final(raw):
    q = pj.PartialJSONParser()
    q.feed(raw)
    return q.finalize()


def test_large_tool_call_streams_in_linear_time():
    args = json.dumps({"content": "x" * 4500, "edits": [{"old": "a", "new": "b"}] * 18})
    t = time.perf_counter()
    p = pj.PartialJSONParser()
    for i in range(0, len(args), 16):
        p.feed(args[i:i + 16])
    assert p.finalize()["content"] == "x" * 4500
    # Was ~1.3 s before round 215; generous bound to stay CI-safe.
    assert time.perf_counter() - t < 0.25
