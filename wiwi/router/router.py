"""Router: model groups of deployments, provider key pools with smooth weighted
round-robin, cooldowns, retries, fallbacks (docs/ADMIN.md §2, ARCHITECTURE.md §4.3)."""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from wiwi.config import PROVIDER_TYPES, ModelAliasEntry, RouterSettings, WiwiConfig
from wiwi.core.context import RequestContext
from wiwi.providers.base import (
    ProviderKeyRef,
    WiwiError,
    status_for_key_pool,
)
from wiwi.server.stats import percentile


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
            # Reset WRR weight so the recovered key isn't starved by the
            # deficit it accumulated while cooling.
            self.current_weight = 0.0

@dataclass
class ProviderAccount:
    name: str
    provider_type: str
    base_url: str
    timeout_s: float = 120.0
    extra_headers: dict[str, str] = field(default_factory=dict)
    round_robin: bool = True
    keys: list[ProviderKey] = field(default_factory=list)
    # Optional caller-facing alias id.  Mirrors ProviderDef.alias_id from
    # wiwi.yaml; admin API mutations keep it in sync.  Used by the router's
    # alias-to-provider registry so request bodies may name the alias.
    alias_id: str | None = None
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

    async def pick_key(self, exclude_labels: set[str] | None = None) -> tuple[ProviderKey | None, float]:
        """Pick the next key to use.

        When ``round_robin`` is True (default): smooth weighted round-robin
        over available keys (nginx algorithm).
        When ``round_robin`` is False: sequential selection — the first
        available key in list order, advancing the cursor only when the
        current key is unavailable (cooldown/disabled).

        ``exclude_labels`` lets cycle-3 / any-error failover skip a specific
        key (e.g. the one that just served N consecutive requests) without
        having to temporarily mark it unavailable.
        """
        async with self._rr_lock:
            for k in self.keys:
                k.recover()
            exclude_labels = exclude_labels or set()
            avail = [k for k in self.keys if k.available and k.label not in exclude_labels]
            if not avail:
                # fall back to the full available list if every key is excluded
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
                    if k.available and k.label not in exclude_labels:
                        self._seq_idx = idx
                        k.last_used = time.monotonic()
                        return k, 0.0
                # Fallback (should not reach here since avail is non-empty)
                k = avail[0]
                k.last_used = time.monotonic()
                return k, 0.0

            # Smooth WRR (nginx algorithm) over the (possibly excluded) avail list
            total = sum(k.weight for k in avail)
            for k in avail:
                k.current_weight += k.weight
            best = max(avail, key=lambda k: k.current_weight)
            best.current_weight -= total
            best.last_used = time.monotonic()
            return best, 0.0

    def on_result(self, key: ProviderKey | None, status: int | None,
                  retry_after: float | None,
                  failover_mode: str = "any_error",
                  key_max_consecutive_fails: int = 5) -> None:
        """Update a key's health counters after a request completes.

        Synchronous callers (e.g. retry paths inside the same task) use this
        directly.  Async paths that can race with :meth:`pick_key` should
        call :meth:`on_result_locked` so the mutation serializes under the
        same lock.

        ``failover_mode`` is one of:

        - "standard": historical behaviour: 429 -> cooldown, 5xx -> let
          ``Deployment.record_fail`` handle it, 401/403 -> mark_invalid.
        - "any_error": every non-200 applies a short cooling window so the
          next pick rotates to a different key.  Auth errors (401/403)
          count as 2 consecutive failures; only when err_count reaches
          ``key_max_consecutive_fails`` is the key permanently retired.
        """
        if key is None or status is None:
            return
        if status == 200:
            key.req_count += 1
            # any consecutive-fail streak is broken on success
            key.err_count = 0
            return
        if failover_mode == "any_error":
            key.err_count += 2 if status in (401, 403) else 1
            if key.err_count >= key_max_consecutive_fails:
                key.mark_invalid()
            else:
                # short cooldown so the next request rotates to a different key
                # (5xx retry-after honored when present, else a default).
                ra = retry_after if (retry_after and retry_after > 0) else 5.0
                key.mark_cooling(min(ra, 30.0))
            return
        # standard mode
        if status == 429:
            key.err_count += 1
            key.mark_cooling(retry_after if retry_after and retry_after > 0 else 30.0)
        elif status in (401, 403):
            key.err_count += 1
            key.mark_invalid()

    async def on_result_locked(self, key: ProviderKey | None, status: int | None,
                               retry_after: float | None,
                               failover_mode: str = "any_error",
                               key_max_consecutive_fails: int = 5) -> None:
        """Async variant that takes ``_rr_lock`` so it cannot interleave with
        :meth:`pick_key`'s read of the same state.  Use this from any path
        that may run concurrently with a fresh pick_key for the same account.
        """
        async with self._rr_lock:
            self.on_result(key, status, retry_after,
                           failover_mode=failover_mode,
                           key_max_consecutive_fails=key_max_consecutive_fails)

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
        # The window must outlast the interval at which a chronically failing
        # deployment fails, otherwise each failure is pruned before the next
        # arrives and `allowed_fails` is never reached. At the shipped
        # cooldown_time of 30s the old max(60, 2*30) == 60s window was
        # identical to the original 60s, so anything failing less often than
        # once a minute never cooled down at all. The floor keeps a
        # near-zero cooldown from making the window uselessly small; the
        # ceiling bounds how many timestamps we retain.
        window = max(300.0, min(6.0 * max(cooldown_time, 1.0), 3600.0))
        recent = [t for t in self.fails if now - t < window]
        self.fails = recent
        if len(recent) >= allowed_fails:
            self.cooldown_until = now + cooldown_time
            self.fails.clear()

    def p95_latency(self) -> float:
        return percentile(self.latencies, 0.95)


def _alias_target(v: str | ModelAliasEntry) -> str:
    """Extract the next-hop group name from a ``model_group_alias`` value.

    Plain-string values pass through; rich entries (shinway-style
    ``ModelAliasEntry``) expose their ``target`` field. This lets the alias
    chain walk in ``Router.resolve_group`` stay agnostic to the value form.
    """
    if isinstance(v, ModelAliasEntry):
        return v.target
    return v


class Router:
    def __init__(self, config: WiwiConfig):
        self.settings: RouterSettings = config.router_settings
        self.providers: dict[str, ProviderAccount] = {}
        self.groups: dict[str, list[Deployment]] = {}
        # Proxy-log emitter for gateway-op events (upstream 5xx, key cooldown,
        # retries, fallback switches). Wired to the LoggingSubsystem by the app
        # at startup. No-op by default so Router works standalone (tests/fakes).
        self.log_proxy = self._noop_log_proxy
        # alias_id -> provider_name (per-provider alias registry, distinct
        # from router_settings.model_group_alias which is a string->string
        # group name rewrite).  Built from config.providers[*].alias_id
        # so admins can expose a provider account under a stable alias.
        self.alias_to_provider: dict[str, str] = {}
        # group_name -> per-provider WRR cursor for cross-provider rotation.
        # Only populated for groups whose deployments span 2+ providers;
        # single-provider groups keep their original shuffle semantics.
        self._group_provider_rr: dict[str, _CrossProviderWRR] = {}
        self._build(config)

    def _noop_log_proxy(self, level: str, message: str, request_id: str = "",
                        **kw: object) -> None:
        """Default proxy-log emitter: does nothing. Overridden at startup."""
        return

    def _build(self, config: WiwiConfig) -> None:
        for p in config.providers:
            acct = ProviderAccount(
                name=p.name, provider_type=p.provider,
                base_url=p.base_url or _default_base_url(p.provider),
                timeout_s=p.timeout_s, extra_headers=dict(p.extra_headers),
                round_robin=p.round_robin,
                keys=[ProviderKey(label=k.label, secret=k.key, weight=k.weight,
                                  enabled=k.enabled) for k in p.keys],
                alias_id=p.alias_id,
            )
            self.providers[p.name] = acct
            if p.alias_id:
                # Pre-existing alias_id wins; config validator already rejects
                # duplicates so this assignment is unambiguous.
                self.alias_to_provider.setdefault(p.alias_id, p.name)
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
        # Cross-provider WRR is only meaningful for groups whose deployments
        # span at least two distinct provider accounts.  Single-provider
        # groups keep their original pick_deployment shuffle semantics.
        self.rebuild_cross_provider_pools()
        # alias resolution happens at route(); aliases may point to any group name

    def resolve_group(self, requested: str) -> tuple[str | None, list[Deployment]]:
        # First, see if `requested` is a provider alias_id.  If so, the call
        # is asking "give me a model that this provider can serve" — return
        # every deployment whose provider matches.  Empty list means the
        # alias exists but the provider serves no models in model_list, and
        # the gateway surface treats that as 404 like any other unknown group.
        pname = self.alias_to_provider.get(requested)
        if pname is not None:
            deps = [d for d in self.groups.values() for d in d
                    if d.provider.name == pname]
            return (requested, deps) if deps else (None, [])
        name = requested
        for _ in range(8):  # bounded walk: aliases may chain, never cycle
            nxt = _alias_target(self.settings.model_group_alias.get(name))
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
            # p95 == 0 means "no samples yet": among cold deployments,
            # break ties randomly so they get explored instead of all
            # traffic pinning to the first one in list order.
            cold = [d for d in avail if d.p95_latency() == 0.0]
            if cold and len(cold) == len(avail):
                return random.choice(cold)
            return min(avail, key=lambda d: d.p95_latency())
        # Cross-provider weighted round-robin: when this group has
        # deployments on 2+ providers, rotate across providers (provider-then-key)
        # instead of weighted-shuffling every pick.  This means each provider
        # gets a contiguous burst of key rotations before we move on, which
        # matches the user's "round robin over key plus provider" requirement.
        rr = self._group_provider_rr.get(deps[0].group)
        if rr is not None and len({d.provider.name for d in avail}) >= 2:
            return rr.pick(avail)
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

    def set_provider_alias(self, name: str, alias_id: str | None) -> None:
        """Update the alias id on a provider account and keep the
        alias-to-provider map consistent.

        Setting an empty/None alias removes the entry.  Raises ValueError
        if ``alias_id`` is already claimed by a different provider.
        """
        acct = self.providers.get(name)
        if acct is None:
            raise ValueError(f"unknown provider {name!r}")
        # Remove any prior mapping pointing at this provider so a rename
        # of just the alias is a clean swap.
        for k, v in list(self.alias_to_provider.items()):
            if v == name:
                del self.alias_to_provider[k]
        if alias_id:
            prior = self.alias_to_provider.get(alias_id)
            if prior is not None and prior != name:
                raise ValueError(
                    f"alias_id {alias_id!r} already used by provider {prior!r}")
            self.alias_to_provider[alias_id] = name
        acct.alias_id = alias_id

    def rebuild_cross_provider_pools(self) -> None:
        """Recompute which groups need a cross-provider WRR cursor.

        Called by the admin API after a deployment is added/removed so the
        pool layer tracks the live set of multi-provider groups exactly.
        """
        self._group_provider_rr.clear()
        for gname, deps in self.groups.items():
            providers = {d.provider.name for d in deps}
            if len(providers) >= 2:
                self._group_provider_rr[gname] = _CrossProviderWRR(deps)


@dataclass
class _CrossProviderWRR:
    """Smooth weighted round-robin cursor over provider accounts within one
    model group.  Each provider appears once with weight = sum of its
    deployments' weights in the group.  When a provider is picked, the
    per-provider key WRR (in ProviderAccount.pick_key) picks the actual key.

    The nginx smooth-WRR algorithm keeps deficits so a temporarily-unhealthy
    provider doesn't get starved after it recovers.
    """
    deps: list[Deployment] = field(default_factory=list)
    _state: dict[str, float] = field(default_factory=dict)

    def _weights(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for d in self.deps:
            out[d.provider.name] = out.get(d.provider.name, 0) + d.weight
        return out

    def pick(self, avail: list[Deployment]) -> Deployment | None:
        # Only consider providers with at least one available deployment.
        avail_providers: dict[str, int] = {}
        for d in avail:
            avail_providers[d.provider.name] = (
                avail_providers.get(d.provider.name, 0) + d.weight)
        if not avail_providers:
            return None
        # nginx smooth WRR over the available providers.
        total = sum(avail_providers.values())
        for pname, weight in avail_providers.items():
            self._state[pname] = self._state.get(pname, 0.0) + weight
        best = max(avail_providers, key=lambda p: self._state.get(p, 0.0))
        self._state[best] = self._state.get(best, 0.0) - total
        # Within the chosen provider, pick the first available deployment
        # for the requested model.  Multi-deployment-per-provider in the
        # same group is rare; if it happens, fall back to the first match.
        for d in avail:
            if d.provider.name == best:
                return d
        return None


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
        "provider_type": "cline",
        "label": "Cline",
        "default_base_url": "https://api.cline.bot/api/v1",
        "description": (
            "Cline's provider gateway (api.cline.bot) via an OpenAI-compatible "
            "Chat Completions API with streaming-only responses. Authenticates "
            "with a Cline account OAuth token (WorkOS) and requires Cline "
            "client-identification headers, which wiwi sends automatically."
        ),
        "latest_models": ["z-ai/glm-5.2", "claude-sonnet-5", "gpt-5.5"],
        "context_window": "varies per model",
        "docs_url": "https://cline.bot",
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
    {
        "provider_type": "bai",
        "label": "B.AI",
        "default_base_url": "https://api.b.ai/v1",
        "description": (
            "B.AI's unified LLM service (api.b.ai): one API key across the "
            "OpenAI Chat Completions, OpenAI Responses, and Anthropic "
            "Messages protocols. wiwi speaks Chat Completions and re-encodes "
            "any inbound dialect. Hosts DeepSeek thinking-mode models — the "
            "adapter replays reasoning_content on tool-call turns so their "
            "documented 400 round-trip requirement never fires."
        ),
        "latest_models": ["deepseek-v4-flash-vision-exp", "deepseek-v4-flash"],
        "context_window": "varies per model",
        "docs_url": "https://docs.b.ai/llmservice/api/",
    },
    {
        "provider_type": "workbuddy",
        "label": "WorkBuddy",
        "default_base_url": "https://copilot.tencent.com",
        "description": (
            "WorkBuddy / CodeBuddy (Tencent copilot.tencent.com, workbuddy.ai) "
            "via an OpenAI-compatible Chat Completions API. Authenticates with "
            "an OAuth access token + account uid headers, is streaming-only "
            "upstream, requires string tool_choice, and wraps errors in a "
            "{code,msg,data} envelope. wiwi sends the account headers and "
            "handles token refresh automatically."
        ),
        "latest_models": [
            "deepseek-v4-flash",
            "glm-5.3",
            "kimi-k3",
            "hy3",
        ],
        "context_window": "varies per model",
        "docs_url": "https://www.codebuddy.cn",
    },
    {
        "provider_type": "nvidia-nim",
        "label": "NVIDIA NIM",
        "default_base_url": "https://integrate.api.nvidia.com/v1",
        "description": (
            "NVIDIA NIM hosts 50+ open and frontier models (Nemotron, "
            "DeepSeek, GLM, Llama, Qwen, Kimi, MiniMax, StepFun) on "
            "optimized GPU infrastructure via an OpenAI-compatible Chat "
            "Completions API. Supports streaming, tool use, reasoning "
            "via chat_template_kwargs, and reasoning_content."
        ),
        "latest_models": [
            "nvidia/nemotron-3-super-120b-a12b",
            "nvidia/nemotron-3.5-lightning-30b-a3b",
            "deepseek-ai/deepseek-v4-pro",
            "zai-org/glm-5.2",
            "moonshotai/kimi-k2.6",
            "minimaxai/minimax-m3",
            "stepfun-ai/step-3.7-flash",
        ],
        "context_window": "varies per model",
        "docs_url": "https://docs.nvidia.com/nim/large-language-models/latest/",
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

    Cycle-3 + any-error failover: when ``router.settings.cycle_every_n > 0``,
    after a key has served N consecutive successful requests the next pick
    excludes it (forces the WRR cursor to advance).  Same for the
    cross-provider cursor: after a provider has served N consecutive
    requests the next pick prefers a different provider.  When
    ``failover_mode == "any_error"`` (default), any non-200 applies a short
    cooldown so the next pick rotates to a different key — keys are only
    permanently retired after ``key_max_consecutive_fails`` consecutive
    failures (auth errors count double).
    """
    cycle_n = max(0, router.settings.cycle_every_n)
    failover_mode = router.settings.failover_mode
    key_max_fails = router.settings.key_max_consecutive_fails
    # per-request cycle counters.  Use ``getattr`` so legacy test fakes
    # (plain classes with a ``group`` attribute) keep working.
    md = getattr(ctx, "metadata", None)
    if not isinstance(md, dict):
        md = {}
        try:
            ctx.metadata = md  # type: ignore[attr-defined]
        except AttributeError:
            # legacy read-only fake — fall back to a local dict (cycle
            # credit won't survive past this request, which is fine)
            pass
    provider_consec: dict[str, int] = md.setdefault("wiwi_cycle_provider", {})
    key_consec: dict[tuple[str, str], int] = md.setdefault("wiwi_cycle_key", {})
    first_error: WiwiError | None = None
    queue: list[str] = []
    if ctx.group:
        queue.append(ctx.group)
    seen: set[str] = set(queue)

    # Proxy-log emitter for gateway-op events. `log_proxy` defaults to a no-op
    # on Router; the app wires it to the LoggingSubsystem at startup.
    proxy_log = router.log_proxy
    req_id = getattr(ctx, "request_id", "")

    def _proxy(level: str, message: str) -> None:
        proxy_log(level, message, req_id)

    while queue:
        group_name = queue.pop(0)
        _, deps = router.resolve_group(group_name)
        if not deps:
            continue
        last_err: WiwiError | None = None
        group_first_err: WiwiError | None = None
        tried_dep_ids: set[int] = set()
        tried_key_labels: set[tuple[str, str]] = set()
        excluded_providers: set[str] = set()
        for attempt in range(router.settings.num_retries + 1):
            # cycle-3: if the chosen provider has served N consecutive
            # requests already, prefer a different one this round.
            prefer_exclude: set[int] = set(tried_dep_ids)
            if cycle_n > 0:
                for d in deps:
                    pname = d.provider.name
                    if (pname in excluded_providers
                            or provider_consec.get(pname, 0) >= cycle_n):
                        prefer_exclude.add(id(d))
            dep = router.pick_deployment(deps, ctx, exclude=prefer_exclude)
            if dep is None:
                # relax cycle exclusion and try again with just the tried dep set
                dep = router.pick_deployment(deps, ctx, exclude=tried_dep_ids)
                if dep is None:
                    last_err = WiwiError(503, "service_unavailable",
                                         f"no healthy deployment for '{group_name}'",
                                         retryable=True)
                    break
            key, retry_in = await dep.provider.pick_key(
                exclude_labels={lbl for (pn, lbl) in tried_key_labels if pn == dep.provider.name}
            )
            if key is None:
                tried_dep_ids.add(id(dep))
                tried_key_labels.add((dep.provider.name, "*"))
                last_err = WiwiError(429, "rate_limit_error",
                                     f"all keys cooling for provider"
                                     f" '{dep.provider.name}'", retry_after=max(1.0, retry_in))
                fresh = any(d.available and id(d) not in tried_dep_ids for d in deps)
                if not fresh:
                    # All keys cooling and no fresh deployments: wait for a
                    # key to recover, then clear exclusions so the deployment
                    # can be retried instead of breaking with a 503.
                    if attempt < router.settings.num_retries:
                        await asyncio.sleep(min(5.0, max(1.0, retry_in)))
                        tried_dep_ids.clear()
                        tried_key_labels.clear()
                    continue
                continue  # siblings may have live keys
            tried_key_labels.add((dep.provider.name, key.label))
            # inflight/latency accounting lives in the gateway: for streams the
            # request stays in flight until the pump finishes, not until
            # execute_with_retries returns (which happens at connect time).
            try:
                result = await call_one(
                    dep, ProviderKeyRef(label=key.label, secret=key.secret), ctx)
                # Success: account the key — except for streaming, where
                # `call_one` returns at *connect* time, long before we know
                # whether the stream will actually deliver anything. Crediting
                # here resets err_count to 0, so a key that connects and then
                # dies mid-stream never accumulates a retirement streak and
                # keeps getting picked first (AUDIT #6). The pump credits the
                # key itself once the stream completes cleanly.
                if not getattr(ctx, "_defer_key_credit", False):
                    await dep.provider.on_result_locked(key, 200, None,
                                                        failover_mode=failover_mode,
                                                        key_max_consecutive_fails=key_max_fails)
                # bump cycle counters
                if cycle_n > 0:
                    provider_consec[dep.provider.name] = (
                        provider_consec.get(dep.provider.name, 0) + 1)
                    key_consec[(dep.provider.name, key.label)] = (
                        key_consec.get((dep.provider.name, key.label), 0) + 1)
                return result
            except WiwiError as e:
                tried_dep_ids.add(id(dep))
                if group_first_err is None:
                    group_first_err = e
                last_err = e
                status = _status_of(e)
                await dep.provider.on_result_locked(key, status, e.retry_after,
                                                    failover_mode=failover_mode,
                                                    key_max_consecutive_fails=key_max_fails)
                # any error: clear this key/provider's cycle credit so the
                # rotation cadence doesn't shield a flapping key from being
                # re-picked.
                key_consec.pop((dep.provider.name, key.label), None)
                provider_consec.pop(dep.provider.name, None)
                if status is not None:
                    _proxy("warn",
                           f"upstream {status} on {dep.group}/{dep.model_id} "
                           f"[{dep.provider.name}/{key.label}]: {e.message}")
                if status in (408, 500, 502, 503, 504, 529):
                    dep.record_fail(router.settings.allowed_fails,
                                    router.settings.cooldown_time)
                if not e.retryable:
                    # Non-retryable, but context-window errors should still
                    # try context_window_fallbacks (e.g. a model with a larger
                    # context window) before giving up.
                    if e.etype == "context_window_exceeded":
                        for fb in router.fallback_targets(group_name,
                                                          "context_window_fallbacks"):
                            if fb not in seen:
                                seen.add(fb)
                                queue.append(fb)
                        break  # let the while-loop process the fallback queue
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
                _proxy("info",
                       f"failing over '{group_name}' to fallback group '{fb}'")
        # Context-window fallbacks: when the failure was a context-window
        # exceeded error, also enqueue groups from context_window_fallbacks
        # (e.g. a model with a larger context window).
        if last_err is not None and last_err.etype == "context_window_exceeded":
            for fb in router.fallback_targets(group_name, "context_window_fallbacks"):
                if fb not in seen:
                    seen.add(fb)
                    queue.append(fb)
                    _proxy("info",
                           f"context-window fallback for '{group_name}' -> '{fb}'")

    raise first_error or WiwiError(503, "service_unavailable", "no deployment could serve request")


def _status_of(e: WiwiError) -> int | None:
    return status_for_key_pool(e)
