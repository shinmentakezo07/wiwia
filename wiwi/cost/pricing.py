"""Cost engine: pricing table + token accounting."""

from __future__ import annotations

import json
from pathlib import Path

BUILTIN_PRICES_PATH = Path(__file__).parent / "model_prices.json"


def _load_builtin() -> dict[str, dict]:
    if BUILTIN_PRICES_PATH.exists():
        return json.loads(BUILTIN_PRICES_PATH.read_text())
    return {}


class CostEngine:
    """Costs are USD per token, 8-decimal rounded. Unpriced models cost 0."""

    def __init__(self, overrides: dict[str, dict] | None = None):
        self.prices = _load_builtin()
        self.prices.update(overrides or {})

    def register(self, model_id: str, input_per_token: float, output_per_token: float) -> None:
        self.prices[model_id] = {
            "input_cost_per_token": input_per_token,
            "output_cost_per_token": output_per_token,
        }

    def cost(self, model_id: str, prompt_tokens: int, completion_tokens: int,
             cached_tokens: int = 0) -> float:
        p = self.prices.get(model_id) or self.prices.get(model_id.split("/")[-1])
        if not p:
            return 0.0
        uncached_prompt = max(0, prompt_tokens - cached_tokens)
        cached_rate = p.get("cache_read_input_cost_per_token", p["input_cost_per_token"])
        total = (
            uncached_prompt * p["input_cost_per_token"]
            + cached_tokens * cached_rate
            + completion_tokens * p["output_cost_per_token"]
        )
        return round(total, 8)


def estimate_tokens(text: str) -> int:
    """chars/4 heuristic fallback."""
    return max(1, len(text) // 4) if text else 0
