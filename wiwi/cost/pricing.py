"""Cost engine: pricing table + token accounting."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostState:
    """Result of a pricing lookup. ``unpriced`` is True when the model is
    missing from the pricing table, so callers can log/flag it instead of
    silently treating usage as $0."""

    cost: float
    unpriced: bool


class CostEngine:
    """Costs are USD per token, 8-decimal rounded. Unpriced models cost 0.

    No built-in prices ship — every entry must be added via the admin API
    (``/admin/pricing``) or passed as ``overrides``. Unpriced models cost 0.
    """

    def __init__(self, overrides: dict[str, dict] | None = None):
        self.prices = dict(overrides or {})

    def register(self, model_id: str, input_per_token: float, output_per_token: float) -> None:
        self.prices[model_id] = {
            "input_cost_per_token": input_per_token,
            "output_cost_per_token": output_per_token,
        }

    def cost(self, model_id: str, prompt_tokens: int, completion_tokens: int,
             cached_tokens: int = 0, cache_creation_tokens: int = 0,
             prompt_includes_cached: bool = True) -> float:
        return self.cost_with_status(
            model_id, prompt_tokens, completion_tokens, cached_tokens,
            cache_creation_tokens, prompt_includes_cached).cost

    def cost_with_status(self, model_id: str, prompt_tokens: int,
                         completion_tokens: int, cached_tokens: int = 0,
                         cache_creation_tokens: int = 0,
                         prompt_includes_cached: bool = True) -> CostState:
        """Like :meth:`cost` but also returns whether the model is priced.

        Unpriced models report cost=0.0 (back-compat) and unpriced=True so
        callers can log/flag the missing entry rather than silently treating
        usage as free.

        ``prompt_includes_cached``: True for providers whose ``prompt_tokens``
        is the TOTAL prompt (OpenAI, Gemini, NIM, OpenRouter); False for
        Anthropic, whose ``input_tokens`` already excludes cached tokens.
        """
        p = self._lookup(model_id)
        if not p:
            return CostState(cost=0.0, unpriced=True)
        if prompt_includes_cached:
            uncached_prompt = max(0, prompt_tokens - cached_tokens)
        else:
            uncached_prompt = prompt_tokens
        cached_rate = p.get("cache_read_input_cost_per_token", p["input_cost_per_token"])
        cache_creation_rate = p.get("cache_creation_input_cost_per_token",
                                    p["input_cost_per_token"])
        total = (
            uncached_prompt * p["input_cost_per_token"]
            + cached_tokens * cached_rate
            + cache_creation_tokens * cache_creation_rate
            + completion_tokens * p["output_cost_per_token"]
        )
        return CostState(cost=round(total, 8), unpriced=False)

    def _lookup(self, model_id: str) -> dict | None:
        """Try multiple lookup strategies for a model's pricing entry.

        The gateway calls cost() with ``f"{provider_type}/{model_id}"`` (e.g.
        ``"openrouter/anthropic/claude-sonnet-4-20250514"``).  The pricing
        table keys on the bare model id (e.g. ``"claude-sonnet-4-20250514"``).
        Try in order: the full key, the key without the provider-type prefix,
        then each successive slash-trimmed tail.
        """
        p = self.prices.get(model_id)
        if p:
            return p
        # Try progressively shorter slash-trimmed tails. For
        # "openrouter/anthropic/claude-sonnet-4-20250514" this tries:
        #   "anthropic/claude-sonnet-4-20250514", then "claude-sonnet-4-20250514".
        parts = model_id.split("/")
        for i in range(1, len(parts)):
            tail = "/".join(parts[i:])
            p = self.prices.get(tail)
            if p:
                return p
        return None


def estimate_tokens(text: str, model: str | None = None) -> int:
    """Estimate token count for *text*.

    Uses ``tiktoken`` when available (accurate for OpenAI models); falls back
    to the chars/4 heuristic for unknown models or when tiktoken is not
    installed.
    """
    if not text:
        return 0
    # Try tiktoken for OpenAI-family models.
    if model:
        enc = _get_tiktoken_encoding(model)
        if enc is not None:
            return len(enc.encode(text))
    return max(1, len(text) // 4)


async def estimate_tokens_async(text: str, model: str | None = None) -> int:
    """Async wrapper around :func:`estimate_tokens`.

    Offloads the (potentially blocking) tiktoken import + encoding to a
    worker thread via :func:`asyncio.to_thread` so the event loop is not
    blocked in async stream-pump coroutines.
    """
    import asyncio
    return await asyncio.to_thread(estimate_tokens, text, model)


# Map common model prefixes to tiktoken encoding names.
_MODEL_ENCODING: dict[str, str] = {
    "gpt-4o": "o200k_base",
    "gpt-4-turbo": "cl100k_base",
    "gpt-4": "cl100k_base",
    "gpt-3.5": "cl100k_base",
    "text-embedding": "cl100k_base",
}


def _model_to_encoding(model: str) -> str | None:
    """Return the tiktoken encoding name for *model*, or None if unknown."""
    lower = model.lower()
    for prefix, enc in _MODEL_ENCODING.items():
        if lower.startswith(prefix):
            return enc
    return None


# Cache of tiktoken encoding instances, keyed by encoding name. The tiktoken
# import and get_encoding call are expensive (file I/O + BPE merge-table load);
# caching avoids repeating them on every estimate_tokens call.
_tiktoken_encodings: dict[str, object] = {}
_tiktoken_available: bool | None = None


def _get_tiktoken_encoding(model: str) -> object | None:
    """Return a cached tiktoken encoding for *model*, or None if unavailable.

    Caches the tiktoken import check and each encoding instance so the
    expensive import + ``get_encoding`` work happens at most once per encoding.
    """
    global _tiktoken_available
    enc_name = _model_to_encoding(model)
    if not enc_name:
        return None
    cached = _tiktoken_encodings.get(enc_name)
    if cached is not None:
        return cached
    if _tiktoken_available is None:
        try:
            import tiktoken
            _tiktoken_available = True
        except Exception:  # noqa: BLE001
            _tiktoken_available = False
    if not _tiktoken_available:
        return None
    try:
        import tiktoken
        enc = tiktoken.get_encoding(enc_name)
        _tiktoken_encodings[enc_name] = enc
        return enc
    except Exception:  # noqa: BLE001
        return None
