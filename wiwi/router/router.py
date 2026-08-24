"""Router: model groups of deployments, provider key pools with smooth weighted
round-robin, cooldowns, retries, fallbacks (docs/ADMIN.md §2, ARCHITECTURE.md §4.3)."""

from __future__ import annotations

import asyncio
import math
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from wiwi.config import PROVIDER_TYPES, RouterSettings, WiwiConfig
from wiwi.core.context import RequestContext
from wiwi.providers.base import (
    RETRYABLE_STATUS,
    ProviderKeyRef,
    WiwiError,
)


@dataclass
class ProviderKey:
    label: str
    secret: str
    weight: int = 1
    enabled: bool = True
    status: str = "active"          # active | cooling | invalid | disabled
    cooldown_until: float = 0.0
    last_used: float = 0.0
    current_weight: float = 0.0     # smooth WRR state
    req_count: int = 0
    err_count: int = 0

    @property
    def available(self) -> bool:
        return (self.enabled and self.status in ("active", "cooling")
                and not (self.status == "cooling" and time.monotonic() < self.cooldown_until))

    def mark_cooling(self, seconds: float) -> None:
        self.status = "cooling"
        self.cooldown_until = time.monotonic() + seconds

    def mark_invalid(self) -> None:
        self.status = "invalid"

    def recover(self) -> None:
        if self.status == "cooling" and time.monotonic() >= self.cooldown_until:
            self.status = "active"


@dataclass
class ProviderAccount:
    name: str
    provider_type: str
    base_url: str
    timeout_s: float = 120.0
    extra_headers: dict[str, str] = field(default_factory=dict)
    round_robin: bool = True
    keys: list[ProviderKey] = field(default_factory=list)
    _rr_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _seq_idx: int = 0  # sequential key cursor (when round_robin=False)

    @property
    def healthy(self) -> bool:
        return any(k.available for k in self.keys)

    def get_key(self, label: str) -> ProviderKey | None:
        """Resolve the live pool entry for a key reference (by label)."""
        for k in self.keys:
            if k.label == label:
                return k
        return None

    async def pick_key(self) -> tuple[ProviderKey | None, float]:
        """Pick the next key to use.

        When ``round_robin`` is True (default): smooth weighted round-robin
        over available keys (nginx algorithm).
        When ``round_robin`` is False: sequential selection — the first
        available key in list order, advancing the cursor only when the
        current key is unavailable (cooldown/disabled).
        """
        async with self._rr_lock:
            for k in self.keys:
                k.recover()
            avail = [k for k in self.keys if k.available]
            if not avail:
                soonest = min((k.cooldown_until for k in self.keys
                               if k.status == "cooling"), default=None)
                return None, (soonest - time.monotonic() if soonest else 5.0)

            if not self.round_robin:
                # Sequential: find the first available key at/after the cursor.
                # This keeps using the same key until it becomes unavailable,
                # then advances to the next one in list order.
                n = len(self.keys)
                for offset in range(n):
                    idx = (self._seq_idx + offset) % n
                    k = self.keys[idx]
                    if k.available:
                        self._seq_idx = idx
                        k.last_used = time.monotonic()
                        return k, 0.0
                # Fallback (should not reach here since avail is non-empty)
                k = avail[0]
                k.last_used = time.monotonic()
                return k, 0.0

            # Smooth WRR (nginx algorithm)
            total = sum(k.weight for k in avail)
            for k in avail:
                k.current_weight += k.weight
            best = max(avail, key=lambda k: k.current_weight)
            best.current_weight -= total
            best.last_used = time.monotonic()
            return best, 0.0

    def on_result(self, key: ProviderKey | None, status: int | None,
                  retry_after: float | None) -> None:
        if key is None or status is None:
            return
        if status == 200:
            key.req_count += 1
        elif status == 429:
            key.err_count += 1
            key.mark_cooling(retry_after if retry_after and retry_after > 0 else 30.0)
        elif status in (401, 403):
            key.err_count += 1
            key.mark_invalid()


@dataclass
class Deployment:
    group: str
    provider: ProviderAccount
    model_id: str
    weight: int = 1
    rpm: int | None = None
    tpm: int | None = None
    timeout: float | None = None
    max_tokens: int | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)
    # cooldown / health
    fails: list[float] = field(default_factory=list)
    cooldown_until: float = 0.0
    inflight: int = 0
    latencies: deque = field(default_factory=lambda: deque(maxlen=50))

    @property
    def available(self) -> bool:
        return self.provider.healthy and time.monotonic() >= self.cooldown_until

    def record_fail(self, allowed_fails: int, cooldown_time: float) -> None:
        now = time.monotonic()
        self.fails.append(now)
        recent = [t for t in self.fails if now - t < 60]
        self.fails = recent
        if len(recent) >= allowed_fails:
            self.cooldown_until = now + cooldown_time
            self.fails.clear()

    def p95_latency(self) -> float:
        if not self.latencies:
            return 0.0
        s = sorted(self.latencies)
        return s[max(0, min(len(s) - 1, math.ceil(len(s) * 0.95) - 1))]


class Router:
    def __init__(self, config: WiwiConfig):
        self.settings: RouterSettings = config.router_settings
        self.providers: dict[str, ProviderAccount] = {}
        self.groups: dict[str, list[Deployment]] = {}
        self._build(config)

    def _build(self, config: WiwiConfig) -> None:
        for p in config.providers:
            acct = ProviderAccount(
                name=p.name, provider_type=p.provider,
                base_url=p.base_url or _default_base_url(p.provider),
                timeout_s=p.timeout_s, extra_headers=dict(p.extra_headers),
                round_robin=p.round_robin,
                keys=[ProviderKey(label=k.label, secret=k.key, weight=k.weight,
                                  enabled=k.enabled) for k in p.keys],
            )
            self.providers[p.name] = acct
        for entry in config.model_list:
            wp = entry.wiwi_params
            acct = self.providers.get(wp.provider)
            if acct is None:
                raise ValueError(f"model {entry.model_name!r} references unknown provider"
                                 f" {wp.provider!r}")
            dep = Deployment(group=entry.model_name, provider=acct, model_id=wp.model,
                             weight=wp.weight, rpm=wp.rpm, tpm=wp.tpm,
                             timeout=wp.timeout, max_tokens=wp.max_tokens,
                             extra_headers=dict(wp.extra_headers),
                             extra_body=dict(wp.extra_body))
            self.groups.setdefault(entry.model_name, []).append(dep)
        # alias resolution happens at route(); aliases may point to any group name

    def resolve_group(self, requested: str) -> tuple[str | None, list[Deployment]]:
        name = requested
        for _ in range(8):  # bounded walk: aliases may chain, never cycle
            nxt = self.settings.model_group_alias.get(name)
            if nxt is None or nxt == name:
                break
            name = nxt
        deps = self.groups.get(name, [])
        return (name, deps) if deps else (None, [])

    def pick_deployment(self, deps: list[Deployment], ctx: RequestContext,
                        exclude: set[int] | None = None) -> Deployment | None:
        """Pick a healthy deployment. `exclude` holds id()s of deployments that
        already failed this request, so retries land on a *different* deployment
        when one exists (LiteLLM semantics)."""
        exclude = exclude or set()
        avail = [d for d in deps if d.available and id(d) not in exclude]
        if not avail:
            avail = [d for d in deps if d.available]  # nothing fresh left: reuse allowed
        if not avail:
            return None
        strategy = self.settings.routing_strategy
        if strategy == "least-busy":
            return min(avail, key=lambda d: d.inflight)
        if strategy == "latency-based":
            # p95 == 0 means "no samples yet": let cold deployments win so they
            # get explored instead of starving behind warmed-up peers
            return min(avail, key=lambda d: d.p95_latency())
        # simple-shuffle: weight-weighted random
        total = sum(d.weight for d in avail)
        r = random.uniform(0, total)
        upto = 0.0
        for d in avail:
            upto += d.weight
            if r <= upto:
                return d
        return avail[-1]

    def fallback_targets(self, failed_group: str, ctx_kind: str = "fallbacks") -> list[str]:
        table = getattr(self.settings, ctx_kind)
        return table.get(failed_group, [])


def _default_base_url(provider_type: str) -> str:
    """Look up the default base URL from the built-in provider catalog.

    Derived from BUILTIN_PROVIDER_TYPES so there is a single source of truth —
    adding a provider type to the catalog automatically makes its default URL
    available here. Returns "" for types with no fixed default (e.g.
    openai-compatible) or unknown types.
    """
    for p in BUILTIN_PROVIDER_TYPES:
        if p["provider_type"] == provider_type:
            return p["default_base_url"]
    return ""


# Built-in provider types that ship with wiwi and can be selected by name
# in the admin UI. Mirrors registry.get_adapter's recognized types.
# Metadata sourced from each provider's official API docs as of Aug 2026.
BUILTIN_PROVIDER_TYPES: list[dict[str, str | list[str]]] = [
    {
        "provider_type": "openai",
        "label": "OpenAI",
        "default_base_url": "https://api.openai.com/v1",
        "description": (
            "GPT-5.6 family models via the OpenAI Chat Completions and "
            "Responses APIs. Supports reasoning effort, tool use, "
            "structured outputs, and vision."
        ),
        "latest_models": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
        "context_window": "1.05M tokens",
        "docs_url": "https://developers.openai.com/api/docs/models",
    },
    {
        "provider_type": "anthropic",
        "label": "Anthropic",
        "default_base_url": "https://api.anthropic.com/v1",
        "description": (
            "Claude models via the Anthropic Messages API. Features adaptive "
            "thinking, tool use, vision, and 1M-token context windows on "
            "Opus and Sonnet."
        ),
        "latest_models": [
            "claude-fable-5",
            "claude-opus-5",
            "claude-sonnet-5",
            "claude-haiku-4-5",
        ],
        "context_window": "1M tokens",
        "docs_url": "https://platform.claude.com/docs/en/about-claude/models/overview",
    },
    {
        "provider_type": "gemini",
        "label": "Google Gemini",
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta",
        "description": (
            "Gemini models via the Google Generative Language API. "
            "Multimodal input (text, images, video, audio), structured "
            "output, function calling, and the Interactions API."
        ),
        "latest_models": [
            "gemini-3.1-pro-preview",
            "gemini-3.7-flash",
            "gemini-3.5-flash-lite",
        ],
        "context_window": "1M+ tokens",
        "docs_url": "https://ai.google.dev/gemini-api/docs",
    },
    {
        "provider_type": "openrouter",
        "label": "OpenRouter",
        "default_base_url": "https://openrouter.ai/api/v1",
        "description": (
            "Unified gateway to 400+ models from 60+ providers via a single "
            "OpenAI-compatible endpoint. Automatic fallbacks, reasoning "
            "parameter translation, and a latest-alias system."
        ),
        "latest_models": [
            "~openai/gpt-latest",
            "~anthropic/claude-sonnet-latest",
            "google/gemini-3.1-pro-preview",
            "minimax/minimax-m3",
        ],
        "context_window": "varies per model",
        "docs_url": "https://openrouter.ai/docs/quickstart",
    },
    {
        "provider_type": "openai-compatible",
        "label": "OpenAI-compatible",
        "default_base_url": "",
        "description": (
            "Any endpoint that speaks the OpenAI Chat Completions wire "
            "format (e.g. vLLM, Ollama, Together, Groq, DeepSeek). "
            "Provide a custom base URL."
        ),
        "latest_models": [],
        "context_window": "varies by endpoint",
        "docs_url": "",
    },
    {
        "provider_type": "gmicloud",
        "label": "GMI Cloud",
        "default_base_url": "https://api.gmi-serving.com/v1",
        "description": (
            "Serverless and dedicated GPU inference for 70+ open-source "
            "and frontier models (DeepSeek, GLM, Llama, Qwen, Claude, "
            "GPT, Gemini, Kimi) via an OpenAI-compatible Chat Completions "
            "API. Supports streaming, tool use, vision, and reasoning "
            "models with reasoning_content."
        ),
        "latest_models": [
            "deepseek-ai/DeepSeek-V3.2",
            "zai-org/GLM-5-FP8",
            "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
            "Qwen/Qwen3.6-Plus",
            "openai/gpt-5.5",
            "google/gemini-3.1-pro-preview",
            "anthropic/claude-opus-4.7",
            "moonshotai/Kimi-K2.6",
        ],
        "context_window": "varies per model",
        "docs_url": "https://docs.gmicloud.ai/quickstart",
    },
]

# Sanity check: every catalog entry must be a recognized provider type, and
# every provider type in PROVIDER_TYPES should have a catalog card. This fails
# at import time so a new provider type added to config.py without a matching
# catalog entry (or vice-versa) is caught immediately, not silently at runtime.
_catalog_types = {p["provider_type"] for p in BUILTIN_PROVIDER_TYPES}
assert _catalog_types == set(PROVIDER_TYPES), (
    f"BUILTIN_PROVIDER_TYPES catalog ({_catalog_types}) is out of sync with "
    f"wiwi.config.PROVIDER_TYPES ({set(PROVIDER_TYPES)}) — update both"
)


async def execute_with_retries(router: Router, ctx: RequestContext,
                               call_one) -> Any:
    """call_one(dep, key) -> result; raises WiwiError on failure.

    Walks: primary group deployments (retries per settings), then fallback groups.
    """
    first_error: WiwiError | None = None
    queue: list[str] = []
    if ctx.group:
        queue.append(ctx.group)
    seen: set[str] = set(queue)

    while queue:
        group_name = queue.pop(0)
        _, deps = router.resolve_group(group_name)
        if not deps:
            continue
        last_err: WiwiError | None = None
        group_first_err: WiwiError | None = None
        tried_dep_ids: set[int] = set()
        for attempt in range(router.settings.num_retries + 1):
            dep = router.pick_deployment(deps, ctx, exclude=tried_dep_ids)
            if dep is not None:
                tried_dep_ids.add(id(dep))
            if dep is None:
                last_err = WiwiError(503, "service_unavailable",
                                     f"no healthy deployment for '{group_name}'",
                                     retryable=True)
                break
            key, retry_in = await dep.provider.pick_key()
            if key is None:
                last_err = WiwiError(429, "rate_limit_error",
                                     f"all keys cooling for provider"
                                     f" '{dep.provider.name}'", retry_after=max(1.0, retry_in))
                fresh = any(d.available and id(d) not in tried_dep_ids for d in deps)
                if not fresh and attempt < router.settings.num_retries:
                    await asyncio.sleep(min(5.0, max(1.0, retry_in)))
                continue  # dep already excluded above; siblings may have live keys
            # inflight/latency accounting lives in the gateway: for streams the
            # request stays in flight until the pump finishes, not until
            # execute_with_retries returns (which happens at connect time).
            try:
                result = await call_one(
                    dep, ProviderKeyRef(label=key.label, secret=key.secret), ctx)
                dep.provider.on_result(key, 200, None)
                return result
            except WiwiError as e:
                if group_first_err is None:
                    group_first_err = e
                last_err = e
                status = _status_of(e)
                dep.provider.on_result(key, status, e.retry_after)
                if status in (408, 500, 502, 503, 504, 529):
                    dep.record_fail(router.settings.allowed_fails,
                                    router.settings.cooldown_time)
                if not e.retryable:
                    raise
                fresh = any(d.available and id(d) not in tried_dep_ids for d in deps)
                if not fresh and attempt < router.settings.num_retries:
                    ra = e.retry_after or 0.0
                    await asyncio.sleep(min(5.0, max(ra, 0.5 * (2 ** attempt)))
                                        + random.uniform(0.0, 0.25))
        if first_error is None:
            first_error = group_first_err or last_err
        # enqueue fallbacks for this group
        for fb in router.fallback_targets(group_name):
            if fb not in seen:
                seen.add(fb)
                queue.append(fb)

    raise first_error or WiwiError(503, "service_unavailable", "no deployment could serve request")


def _status_of(e: WiwiError) -> int | None:
    if e.status == 429:
        return 429
    if e.status in (401, 403):
        return e.status
    if e.status in RETRYABLE_STATUS:
        return e.status
    return None
