"""Round 187 — a malformed ``top_k`` is reported as malformed, not unsupported.

``OpenAIAdapter.supports_top_k`` lets a vLLM-backed upstream (IFM) receive a
top-level ``top_k``. The drop warning was emitted for two different facts —
"this wire has no such field" and "the value could not be coerced" — so an IFM
caller that sent ``top_k: "20"`` was told the provider has no Chat Completions
equivalent, which is false: IFM carries the field; the value was simply not an
integer.

These pin the split: an uncoercible value names the value, and the
"no equivalent" wording is reserved for adapters that do not set
``supports_top_k``.
"""

from __future__ import annotations

from wiwi.ir import types as ir
from wiwi.providers.ifm_adapter import IFMAdapter
from wiwi.providers.openai_adapter import OpenAIAdapter

MODEL = "IFM/K2-Horizon-375B-A23B"


def _req(top_k) -> ir.Request:
    return ir.Request(
        model=MODEL,
        messages=[ir.Message(role="user", parts=[ir.TextPart("hi")])],
        gen_params=ir.GenParams(top_k=top_k),
        extras={},
        stream=False,
    )


def test_ifm_names_the_value_when_top_k_cannot_be_coerced():
    adapter = IFMAdapter()
    body = adapter.encode_request(_req("20"), MODEL, {})
    assert "top_k" not in body
    warnings = " ".join(adapter.translation_warnings)
    assert "not a usable integer" in warnings
    assert "no Chat Completions equivalent" not in warnings


def test_an_adapter_without_supports_top_k_still_calls_it_unsupported():
    """Control: the "no equivalent" wording still belongs to the adapters
    whose wire genuinely lacks the field."""
    adapter = OpenAIAdapter()
    body = adapter.encode_request(_req(20), MODEL, {})
    assert "top_k" not in body
    assert any("no Chat Completions equivalent" in w
               for w in adapter.translation_warnings)
