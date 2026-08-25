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


def test_cost_lookup_strips_provider_prefix():
    """The gateway calls cost() with f'{provider_type}/{model_id}'.
    For nested model IDs like 'openrouter/anthropic/claude-sonnet-4-20250514',
    the lookup must find the pricing entry keyed on 'claude-sonnet-4-20250514'."""
    ce = CostEngine()
    ce.register("claude-sonnet-4-20250514",
                input_per_token=0.000003, output_per_token=0.000015)
    c = ce.cost("anthropic/claude-sonnet-4-20250514", 1000, 500)
    assert c > 0
    # Two-level prefix: openrouter wraps anthropic
    c2 = ce.cost("openrouter/anthropic/claude-sonnet-4-20250514", 1000, 500)
    assert abs(c - c2) < 1e-9


def test_cost_lookup_glm_nested_id():
    """Model 'zai/glm-5.2' arriving as 'openai-compatible/zai/glm-5.2'
    must find the pricing entry keyed on 'glm-5.2'."""
    ce = CostEngine()
    ce.register("glm-5.2", input_per_token=0.0000014, output_per_token=0.0000044)
    c = ce.cost("openai-compatible/zai/glm-5.2", 1000, 500)
    assert c > 0
    # Also works with just the model id
    c2 = ce.cost("zai/glm-5.2", 1000, 500)
    assert abs(c - c2) < 1e-9


def test_cost_lookup_unknown_returns_zero():
    ce = CostEngine()
    assert ce.cost("openai-compatible/unknown-model", 100, 100) == 0.0
    assert ce.cost("openrouter/unknown/org/model", 100, 100) == 0.0


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
