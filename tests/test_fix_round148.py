"""Round 148: Zen/Console routing is a table, not a prefix guess.

Console publishes one base URL and **four** upstream wires, selected per model
(https://opencode.ai/docs/zen, "Endpoints"). The adapter used to pick by
prefix:

    _RESPONSES_PREFIXES = ("gpt-", "grok-", "muse-spark-")
    _MESSAGES_PREFIXES  = ("claude-", "qwen", "union-alpha")
    _GEMINI_PREFIXES    = ("gemini-",)

which is wrong on the current catalog, because **the qwen family straddles two
wires**. Published table: `qwen3.8-max` is on `/chat/completions`, while
`qwen3.8-flash` and the qwen3.7 / 3.6 / 3.5 lines are on `/messages`. The single
`"qwen"` prefix therefore sent `qwen3.8-max` to the Anthropic Messages endpoint,
where it cannot answer. Nothing machine-readable catches this: models.dev lists
the `opencode` provider as `@ai-sdk/openai-compatible` with **no** per-model
overrides, so the split exists only on that page.

Two further corrections came out of the same pass:

- **Base root.** `models.dev`'s `api` and every row of the docs table use
  `https://opencode.ai/inference/v1`; the adapter defaulted to `/zen/v1`. The
  legacy root still routes (probed 2026-10-14: a paid model there returns the
  account's `402 Insufficient account funds`, i.e. it authenticates and reaches
  the API), so an existing config pointing at `zen/v1` keeps working — only the
  default moved.
- **`jev-*` has no chat wire.** It is a System One model on `/systemone`, a typed
  `state` + `questions` → values/probabilities protocol, not a completion API.
  Documented as not served here rather than silently mis-routed.

Routing could not be confirmed per-endpoint by probing: the account's funds
check fires *before* route validation, so every route answers `402` for a paid
model. The table is therefore transcribed from the docs and asserted against
that transcription here — widen a rule only from the page, never by guessing a
prefix.

Pinned below: every row of the published table (responses / messages / gemini /
chat, including both halves of the qwen split), the fall-through to chat, the
default base root, and that a configured `base_url` still wins.
"""

from __future__ import annotations

import pytest

from wiwi.providers.opencode_adapter import (
    _ROUTE_BASES,
    OPENCODE_ZEN_BASE,
    OpencodeAdapter,
    route_for_model,
)

# Transcribed from the docs "Endpoints" table, 2026-11-19
# (https://opencode.ai/v2/docs/console/models/).
DOCS_TABLE: list[tuple[str, str]] = [
    # responses — OpenAI Responses wire
    ("gpt-6.2-astra", "responses"), ("gpt-6.2-sol", "responses"),
    ("gpt-6-astra", "responses"), ("gpt-6-sol", "responses"),
    ("gpt-6.1-sol", "responses"), ("gpt-6-luna", "responses"),
    ("gpt-5.6-sol", "responses"), ("gpt-5.6-terra", "responses"),
    ("gpt-5.6-luna", "responses"), ("gpt-5.5", "responses"),
    ("gpt-5.5-pro", "responses"), ("gpt-5.4", "responses"),
    ("gpt-5.4-pro", "responses"), ("gpt-5.4-mini", "responses"),
    ("gpt-5.4-nano", "responses"), ("gpt-5.3-codex", "responses"),
    ("gpt-5.3-codex-spark", "responses"), ("gpt-5.2", "responses"),
    ("gpt-5.2-codex", "responses"), ("gpt-5.1", "responses"),
    ("gpt-5.1-codex", "responses"), ("gpt-5.1-codex-max", "responses"),
    ("gpt-5.1-codex-mini", "responses"), ("gpt-5", "responses"),
    ("gpt-5-codex", "responses"), ("gpt-5-nano", "responses"),
    ("grok-4.7", "responses"), ("grok-4.6", "responses"),
    ("grok-4.5", "responses"), ("grok-build-0.1", "responses"),
    ("grok-code", "responses"),
    ("muse-spark-1.3", "responses"), ("muse-spark-1.2", "responses"),
    ("muse-spark-1.3-contributor-free", "responses"),
    ("muse-spark-1.2-contributor-free", "responses"),
    ("muse-ember-1.0", "responses"), ("muse-wonder-1.0", "responses"),
    # messages — Anthropic Messages wire
    ("claude-fable-5.2", "messages"), ("claude-fable-5.1", "messages"),
    ("claude-fable-5", "messages"), ("claude-opus-5.5", "messages"),
    ("claude-opus-5", "messages"), ("claude-opus-4.8", "messages"),
    ("claude-opus-4.7", "messages"), ("claude-opus-4.6", "messages"),
    ("claude-opus-4.5", "messages"), ("claude-sonnet-5.5", "messages"),
    ("claude-sonnet-5", "messages"), ("claude-sonnet-4.6", "messages"),
    ("claude-sonnet-4.5", "messages"), ("claude-haiku-5.5", "messages"),
    ("claude-haiku-4.5", "messages"),
    ("qwen3.8-flash", "messages"), ("qwen3.6-plus", "messages"),
    ("qwen3.5-plus", "messages"),
    # gemini wire
    ("gemini-4-carbon", "gemini"), ("gemini-4-argon", "gemini"),
    ("gemini-4-hydrogen", "gemini"),
    ("gemini-3.8-flash", "gemini"), ("gemini-3.7-flash", "gemini"),
    ("gemini-3.6-flash", "gemini"), ("gemini-3.5-flash", "gemini"),
    ("gemini-3.5-flash-lite", "gemini"),
    # chat — everything else
    ("qwen3.8-max", "chat"), ("qwen3-coder", "chat"),
    ("deepseek-v4.1-flash", "chat"), ("deepseek-v4.1-flash-free", "chat"),
    ("deepseek-v4-pro", "chat"),
    ("minimax-m3", "chat"), ("minimax-m2.7", "chat"), ("minimax-m2.5", "chat"),
    ("glm-5.3-flash", "chat"), ("glm-5.3", "chat"), ("glm-5.2", "chat"),
    ("glm-5.1", "chat"), ("glm-5", "chat"),
    ("kimi-k2.5", "chat"), ("kimi-k2.6", "chat"), ("kimi-k2.5-free", "chat"),
    ("kimi-k2.7-code", "chat"), ("kimi-k3", "chat"), ("mistral-large-4", "chat"),
    ("big-pickle", "chat"), ("iceman-free", "chat"),
    ("inkling-2-small-free", "chat"), ("beam-free", "chat"),
    ("flint-40b-free", "chat"), ("longcat-2.5-free", "chat"),
    ("hy4-flash-free", "chat"), ("mimo-v2.6-flash-free", "chat"),
    ("nemotron-3.5-lightning-free", "chat"),
    ("nemotron-3.5-super-vl-free", "chat"),
]


@pytest.mark.parametrize("model,expected", DOCS_TABLE)
def test_every_row_of_the_published_table(model: str, expected: str):
    assert route_for_model(model) == expected, model


def test_the_qwen_family_is_split_across_two_wires():
    # The regression: a bare "qwen" prefix sent qwen3.8-max to /messages.
    assert route_for_model("qwen3.8-max") == "chat"
    assert route_for_model("qwen3.8-flash") == "messages"
    # A future qwen release with no row must land on the busy default, not on a
    # wire it was never published for.
    assert route_for_model("qwen9-9-max") == "chat"


def test_unknown_and_empty_models_fall_through_to_chat():
    for model in ("", "   ", "some-future-model", "qwen-unreleased-thing"):
        assert route_for_model(model) == "chat", model
    # A *named family* still routes on its prefix even for a version the table
    # never listed — that is the point of the prefix rules.
    assert route_for_model("gpt-5.5-unknown-suffix") == "responses"


def test_routing_is_case_and_whitespace_insensitive():
    assert route_for_model("  GPT-5.5  ") == "responses"
    assert route_for_model("Claude-Sonnet-5") == "messages"
    assert route_for_model("GEMINI-3-FLASH") == "gemini"


def test_the_default_bases_are_the_per_wire_canonical_roots():
    # Re-fetched 2026-11-19: the docs table and models.dev `api` publish four
    # different canonical roots; a deployment-set base_url overrides them all.
    assert OPENCODE_ZEN_BASE == "https://opencode.ai/inference/openai/v1"
    assert _ROUTE_BASES == {
        "responses": "https://opencode.ai/inference/openai/v1",
        "messages": "https://opencode.ai/inference/anthropic/v1",
        "gemini": "https://opencode.ai/inference/google/v1beta",
        "chat": "https://opencode.ai/inference/openai/v1",
    }


def test_default_urls_hit_the_canonical_root_of_each_wire():
    a = OpencodeAdapter()
    assert a.build_url("", "gpt-5.5", False) == (
        "https://opencode.ai/inference/openai/v1/responses")
    assert a.build_url("", "claude-sonnet-5", True) == (
        "https://opencode.ai/inference/anthropic/v1/messages")
    assert a.build_url("", "gemini-4-carbon", True) == (
        "https://opencode.ai/inference/google/v1beta/models/gemini-4-carbon"
        ":streamGenerateContent?alt=sse")
    assert a.build_url("", "gemini-4-carbon", False) == (
        "https://opencode.ai/inference/google/v1beta/models/gemini-4-carbon"
        ":generateContent")
    assert a.build_url("", "deepseek-v4-pro", True) == (
        "https://opencode.ai/inference/openai/v1/chat/completions")


def test_a_configured_base_url_still_wins():
    a = OpencodeAdapter()
    assert a.build_url("https://opencode.ai/zen/v1", "glm-5.1", True) == (
        "https://opencode.ai/zen/v1/chat/completions")
    # Trailing slashes are normalised, and an empty base falls back to the default.
    assert a.build_url("https://opencode.ai/inference/v1/", "gpt-5.5", True) == (
        "https://opencode.ai/inference/v1/responses")
    assert a.build_url("", "glm-5.1", True) == f"{OPENCODE_ZEN_BASE}/chat/completions"


def test_build_url_hits_the_route_the_table_chose():
    a = OpencodeAdapter()
    cases = {
        "gpt-5.5": "/responses",
        "muse-spark-1.3": "/responses",
        "claude-sonnet-5": "/messages",
        "qwen3.7-max": "/messages",
        "qwen3.8-max": "/chat/completions",
        "deepseek-v4.1-flash": "/chat/completions",
    }
    for model, suffix in cases.items():
        url = a.build_url("https://example.invalid/v1", model, True)
        assert url.endswith(suffix), f"{model} -> {url}"


def test_the_gemini_route_still_selects_its_wire_from_the_url():
    a = OpencodeAdapter()
    assert a.build_url("https://example.invalid/v1", "gemini-3-flash", True) == (
        "https://example.invalid/v1/models/gemini-3-flash:streamGenerateContent?alt=sse")
    assert a.build_url("https://example.invalid/v1", "gemini-3-flash", False) == (
        "https://example.invalid/v1/models/gemini-3-flash:generateContent")


def test_the_messages_route_still_names_the_anthropic_version():
    a = OpencodeAdapter()
    from wiwi.providers.base import ProviderKeyRef
    a.build_url("https://example.invalid/v1", "claude-sonnet-5", True)
    headers = a.headers(ProviderKeyRef(label="k", secret="sk-x"))
    assert headers["anthropic-version"] == "2023-06-01"
    # And only there.
    a.build_url("https://example.invalid/v1", "glm-5.1", True)
    assert "anthropic-version" not in a.headers(ProviderKeyRef(label="k", secret="sk-x"))
