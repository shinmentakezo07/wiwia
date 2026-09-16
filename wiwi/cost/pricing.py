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

    A model's entry may carry a ``providers`` sub-map of scoped overrides, so
    the same model id can cost different amounts on different upstreams::

        "gpt-4o": {
            "input_cost_per_token": 3e-6,     # base = all providers
            "output_cost_per_token": 15e-6,
            "providers": {
                "openai":      {"input_cost_per_token": 2.5e-6},  # provider type
                "openai-main": {"input_cost_per_token": 2e-6},    # account
            },
        }

    Account and type share one namespace, so an account named exactly like its
    type (the shipped example config has ``name: openrouter`` /
    ``provider: openrouter``) resolves to the account — deterministic, with
    account precedence, rather than an ambiguous key.
    """

    def __init__(self, overrides: dict[str, dict] | None = None):
        self.prices = dict(overrides or {})

    def register(self, model_id: str, input_per_token: float, output_per_token: float) -> None:
        """Seed a model's base rates, preserving any scoped overrides.

        The scopes are kept deliberately: ``register()`` is the documented way
        to seed a price, and re-registering a model must not silently drop the
        per-provider prices configured for it.
        """
        existing = self.prices.get(model_id) or {}
        entry: dict = {
            "input_cost_per_token": input_per_token,
            "output_cost_per_token": output_per_token,
        }
        if existing.get("providers"):
            entry["providers"] = existing["providers"]
        self.prices[model_id] = entry

    def cost(self, model_id: str, prompt_tokens: int, completion_tokens: int,
             cached_tokens: int = 0, cache_creation_tokens: int = 0,
             prompt_includes_cached: bool = True,
             provider_type: str | None = None,
             provider_name: str | None = None) -> float:
        return self.cost_with_status(
            model_id, prompt_tokens, completion_tokens, cached_tokens,
            cache_creation_tokens, prompt_includes_cached,
            provider_type, provider_name).cost

    def cost_with_status(self, model_id: str, prompt_tokens: int,
                         completion_tokens: int, cached_tokens: int = 0,
                         cache_creation_tokens: int = 0,
                         prompt_includes_cached: bool = True,
                         provider_type: str | None = None,
                         provider_name: str | None = None) -> CostState:
        """Like :meth:`cost` but also returns whether the model is priced.

        Unpriced models report cost=0.0 (back-compat) and unpriced=True so
        callers can log/flag the missing entry rather than silently treating
        usage as free.

        ``prompt_includes_cached``: True for providers whose ``prompt_tokens``
        is the TOTAL prompt (OpenAI, Gemini, NIM, OpenRouter); False for
        Anthropic, whose ``input_tokens`` already excludes cached tokens.

        ``provider_type``/``provider_name`` select a scoped rate (see
        :meth:`resolve`); omitted, the all-providers base rate applies.
        """
        p = self._lookup(model_id, provider_type, provider_name)
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

    def _lookup(self, model_id: str, provider_type: str | None = None,
                provider_name: str | None = None) -> dict | None:
        """Try multiple lookup strategies for a model's pricing entry.

        The gateway calls cost() with ``f"{provider_type}/{model_id}"`` (e.g.
        ``"openrouter/anthropic/claude-sonnet-4-20250514"``).  The pricing
        table keys on the bare model id (e.g. ``"claude-sonnet-4-20250514"``).
        Try in order: the full key, the key without the provider-type prefix,
        then each successive slash-trimmed tail.

        Each candidate key is resolved through :meth:`resolve`, so a scoped
        override applies wherever the entry is found — including on a
        slash-trimmed tail, which would otherwise silently bill the base rate.
        """
        candidates = [model_id]
        # Progressively shorter slash-trimmed tails. For
        # "openrouter/anthropic/claude-sonnet-4-20250514" this tries:
        #   "anthropic/claude-sonnet-4-20250514", then "claude-sonnet-4-20250514".
        parts = model_id.split("/")
        for i in range(1, len(parts)):
            candidates.append("/".join(parts[i:]))
        for key in candidates:
            entry = self.prices.get(key)
            if entry is None:
                continue
            merged = self._merge_scoped(entry, provider_type, provider_name)
            # An entry with no usable rates (a scope-only entry whose scope did
            # not match) is not a price — keep walking rather than returning a
            # truthy dict that the caller would treat as priced.
            if merged is not None:
                return merged
        return None

    def resolve(self, model_id: str, provider_type: str | None = None,
                provider_name: str | None = None) -> dict | None:
        """The effective rate dict for *model_id* on this provider, or None.

        Precedence is most-specific-first: provider account, then provider
        type, then the model's base (all-providers) rates. A scoped override
        may set only some rates — the rest inherit from the base.
        """
        return self._lookup(model_id, provider_type, provider_name)

    @staticmethod
    def _merge_scoped(entry: dict, provider_type: str | None,
                      provider_name: str | None) -> dict | None:
        """Overlay the winning scope onto an entry's base rates.

        Returns None when the result carries no rates at all, so callers can
        treat it as unpriced instead of raising on a missing key.
        """
        scopes = entry.get("providers") or {}
        scoped = None
        # Account beats type. They share one namespace, so an account named
        # exactly like its type resolves to the account.
        if provider_name and provider_name in scopes:
            scoped = scopes[provider_name]
        elif provider_type and provider_type in scopes:
            scoped = scopes[provider_type]
        base = {k: v for k, v in entry.items() if k != "providers"}
        if scoped:
            base = {**base, **scoped}
        if "input_cost_per_token" not in base or "output_cost_per_token" not in base:
            return None
        return base


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
#
# Claude models are listed deliberately: tiktoken is not Anthropic's tokenizer,
# but it is a far closer proxy than chars/4 for the same text — measured within
# ~10% on source code, where chars/4 undercounts by ~8% on prose and by much
# more on punctuation-dense text. Without an entry every claude-* model fell
# through to the chars/4 branch, and Anthropic's own docs note current Claude
# tokenizers produce ~30% MORE tokens than the pre-4.7 ones, so /context and
# the auto-compact threshold read low. cl100k_base is the closest available
# BPE to the Claude family (o200k_base is tuned for GPT-4o and over-splits
# code). This is a documented approximation, not an exact count.
_MODEL_ENCODING: dict[str, str] = {
    "gpt-4o": "o200k_base",
    "gpt-4-turbo": "cl100k_base",
    "gpt-4": "cl100k_base",
    "gpt-3.5": "cl100k_base",
    "text-embedding": "cl100k_base",
    "claude": "cl100k_base",
}


def estimate_image_tokens(width: int, height: int) -> int:
    """Tokens Anthropic bills for an image of *width* x *height*.

    Anthropic's formula: resize so the long edge is at most 1568px, then
    ``(w * h) / 750``. Used when only the pixel dimensions are known.
    """
    if width <= 0 or height <= 0:
        return 0
    long_edge = max(width, height)
    if long_edge > 1568:
        scale = 1568 / long_edge
        width = max(1, int(width * scale))
        height = max(1, int(height * scale))
    return max(1, (width * height) // 750)


def estimate_media_tokens(payload_bytes: int, mime: str = "") -> int:
    """Approximate the prompt tokens an image or document payload costs.

    Neither provider bills base64 media by its encoded length, and the gateway
    has no decoder for every format, so this works from the byte size with
    per-format density factors derived from observed behaviour:

    - **Images**: a base64 payload is ~4/3 the raw bytes. Anthropic bills by
      pixel area (``w*h/750`` after a 1568px long-edge cap), and compressed
      image bytes-per-pixel varies by format — roughly 0.35 for PNG
      (lossless, so large for a given area), ~0.10 for JPEG/WebP. Inverting
      that gives tokens ≈ raw_bytes / 750 / bytes_per_pixel.
    - **PDFs**: Anthropic renders each page as an image; a text page is
      typically 2-6 KB of PDF, so tokens ≈ raw_bytes / 3500 pages times the
      per-page image cost. Floored at one page.

    Deliberately an estimate: it exists so a screenshot is not billed as zero
    (the old behaviour counted only ``url``/``file_id`` and ignored ``b64``
    entirely, so a 300 KB PNG reported 7 tokens instead of ~1500).
    """
    if payload_bytes <= 0:
        return 0
    mime = (mime or "").lower()
    # base64 inflates by 4/3; recover the raw byte count.
    raw = max(1, (payload_bytes * 3) // 4)
    if "pdf" in mime:
        pages = max(1, raw // 3500)
        return pages * 1500
    if "png" in mime:
        return max(1, raw // 260)
    if "jpeg" in mime or "jpg" in mime or "webp" in mime:
        return max(1, raw // 750)
    if "gif" in mime:
        return max(1, raw // 500)
    # Unknown image/document format: assume the JPEG-ish density.
    return max(1, raw // 750)


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
