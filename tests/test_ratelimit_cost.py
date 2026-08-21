"""Rate limiter + cost engine unit tests."""

from wiwi.cost.pricing import CostEngine, estimate_tokens
from wiwi.ratelimit.memory import RateLimiter


def test_rate_limit_rpm():
    rl = RateLimiter(global_rpm=3)
    ok = True
    for _ in range(3):
        allowed, _ = rl.check("k1", None, None)
        ok = ok and allowed
    assert ok
    allowed, retry_after = rl.check("k1", None, None)
    assert not allowed and retry_after >= 1


def test_rate_limit_per_key():
    rl = RateLimiter()
    for _ in range(5):
        allowed, _ = rl.check("kA", key_rpm=5, key_tpm=None)
        assert allowed
    allowed, _ = rl.check("kA", key_rpm=5, key_tpm=None)
    assert not allowed
    allowed, _ = rl.check("kB", key_rpm=5, key_tpm=None)  # other key unaffected
    assert allowed


def test_cost_engine():
    ce = CostEngine()
    ce.register("my-model", input_per_token=0.000001, output_per_token=0.000002)
    c = ce.cost("openai/my-model", prompt_tokens=1000, completion_tokens=500)
    assert abs(c - (1000 * 0.000001 + 500 * 0.000002)) < 1e-9
    assert ce.cost("unknown-model", 100, 100) == 0.0


def test_cost_cached_discount():
    ce = CostEngine()
    ce.register("m", input_per_token=0.000001, output_per_token=0.000001,
                )
    ce.prices["m"]["cache_read_input_cost_per_token"] = 0.0000001
    full = ce.cost("m", 1000, 0)
    cached = ce.cost("m", 1000, 0, cached_tokens=900)
    assert cached < full


def test_estimate_tokens():
    assert estimate_tokens("abcd" * 10) == 10
    assert estimate_tokens("") == 0
