"""Config loading: wiwi.yaml -> typed models, os.environ/ interpolation, fail-fast validation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ConfigError(Exception):
    """Raised with file/line context when the config is invalid."""


def load_env(path: str | Path = ".env") -> None:
    """Load .env file into os.environ if it exists.

    Called early so that DATABASE_URL, WIWI_MASTER_KEY, provider keys, and
    WIWI_CONFIG are available before config parsing.  Existing environment
    variables are never overwritten — .env only fills gaps.
    """
    p = Path(path)
    if p.is_file():
        load_dotenv(p, override=False)


# Every provider type that ships with wiwi. This is the single source of truth
# — the router (catalog), the admin API (add/patch validation), and the Pydantic
# config schema all reference this list so a new provider type can't be added in
# one place without the others noticing. Mirrors registry.get_adapter().
PROVIDER_TYPES: tuple[str, ...] = (
    "openai",
    "anthropic",
    "gemini",
    "openai-compatible",
    "openrouter",
    "gmicloud",
    "bai",
    "nvidia-nim",
    "cline",
    "workbuddy",
)


def _interpolate(value: Any) -> Any:
    """Recursively resolve `os.environ/NAME` strings.

    Missing env vars resolve to an empty string rather than crashing, so the
    example config (which references many optional provider keys) can load in
    a fresh container.  Providers with empty keys are filtered out in _validate.
    """
    if isinstance(value, str) and value.startswith("os.environ/"):
        var = value[len("os.environ/"):]
        return os.getenv(var, "")
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


class KeyDef(BaseModel):
    label: str = "default"
    key: str
    weight: int = 1
    enabled: bool = True

    @field_validator("key")
    @classmethod
    def _key_required(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("provider key entry needs a non-empty 'key'")
        return v


class ProviderDef(BaseModel):
    name: str
    provider: Literal[PROVIDER_TYPES]  # type: ignore[valid-type]
    base_url: str | None = None
    timeout_s: float = 120.0
    extra_headers: dict[str, str] = Field(default_factory=dict)
    # When True (default), keys are selected via smooth weighted round-robin.
    # When False, keys are used sequentially (first available, in label order).
    round_robin: bool = True
    keys: list[KeyDef] = Field(default_factory=list)
    # Optional caller-facing alias for this account.  When set, clients can
    # request a model by the alias id and the router will resolve it to a
    # model_name the same way ``model_group_alias`` does.  Multiple providers
    # that share an alias automatically pool into a single cross-provider
    # weighted round-robin for any model_name they both serve.
    alias_id: str | None = None

    @field_validator("keys")
    @classmethod
    def _need_keys(cls, v: list[KeyDef]) -> list[KeyDef]:
        if not v:
            raise ValueError("provider needs at least one key entry")
        return v

    @field_validator("alias_id")
    @classmethod
    def _alias_id_format(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        if any(c.isspace() for c in v):
            raise ValueError("alias_id must not contain whitespace")
        return v


class DeploymentParams(BaseModel):
    provider: str  # provider account name from providers:
    model: str     # provider-native model id, e.g. "gpt-4o" or "anthropic/claude-..." style id
    weight: int = 1
    max_tokens: int | None = None
    rpm: int | None = None
    tpm: int | None = None
    timeout: float | None = None
    extra_headers: dict[str, str] = Field(default_factory=dict)
    # Extra JSON fields merged into the upstream request body at encode time.
    # Used for provider-specific routing knobs, e.g. OpenRouter's ``provider``
    # object: ``extra_body: {provider: {only: ["gmicloud"]}}``.
    extra_body: dict[str, Any] = Field(default_factory=dict)


class ModelEntry(BaseModel):
    model_name: str
    wiwi_params: DeploymentParams


class ModelAliasEntry(BaseModel):
    """Rich-form entry in ``model_group_alias``. Modelled on shinway's
    ``OAuthModelAlias``: ``target`` is the resolved group, ``force_mapping``
    controls whether the response echoes the client alias (``True`` — the
    wiwi/LiteLLM default) or reveals the resolved group (``False``),
    ``display_name`` is an optional human label surfaced by the admin UI
    and the public catalog, and ``fork`` is accepted for config parity but
    is rejected by the admin endpoint until implemented.
    """
    model_config = ConfigDict(extra="forbid")
    target: str
    force_mapping: bool = True
    display_name: str | None = None
    fork: bool = False


class RouterSettings(BaseModel):
    routing_strategy: Literal["simple-shuffle", "least-busy", "latency-based"] = "simple-shuffle"
    num_retries: int = 2
    timeout: float = 120.0
    allowed_fails: int = 3
    cooldown_time: float = 30.0
    fallbacks: dict[str, list[str]] = Field(default_factory=dict)
    context_window_fallbacks: dict[str, list[str]] = Field(default_factory=dict)
    model_group_alias: dict[str, str | ModelAliasEntry] = Field(default_factory=dict)
    global_rpm: int | None = None
    global_tpm: int | None = None
    # Streaming resilience
    stream_idle_timeout_s: float = 30.0  # max seconds between upstream chunks
    stream_loop_detection: bool = True
    stream_loop_limit: int = 100  # identical consecutive chunks before abort
    stream_coalesce: bool = False  # coalesce TextDeltas under backpressure
    stream_coalesce_max_bytes: int = 8192
    stream_coalesce_max_ms: float = 50.0
    # Mid-stream failover: when an upstream dies after content has flowed,
    # retry on a fallback deployment with the partial output prepended.
    stream_resume: Literal["off", "content_only", "enabled"] = "off"
    stream_resume_max_retries: int = 1  # how many mid-stream resume attempts
    # SSE Last-Event-ID: assign monotonic event ids for client-side resumption.
    stream_event_ids: bool = False
    # Client-gone grace drain: on disconnect, keep pumping upstream for
    # accurate billing. 0 = cancel immediately (current behavior).
    stream_grace_drain_s: float = 0.0
    # Prometheus /metrics endpoint
    prometheus_enabled: bool = False
    prometheus_path: str = "/metrics"
    # Cycle rotation: every N successful requests served by the same provider
    # or the same key, force the WRR cursor to advance so traffic actually
    # rotates, not just spreads by weight.  Set to 0 to disable the cadence
    # and keep weight-driven WRR only.
    cycle_every_n: int = 3
    # Failover policy: "any_error" rotates to the next key on any non-200
    # (counting consecutive fails so we can still permanently retire a key
    # after a sustained auth failure); "standard" keeps the historical
    # 429/5xx-only cooldown behaviour.
    failover_mode: Literal["any_error", "standard"] = "any_error"
    # Consecutive failures before a key is permanently retired.  Only
    # relevant in "any_error" mode.  401/403 errors count twice.
    key_max_consecutive_fails: int = 5

class GeneralSettings(BaseModel):
    master_key: str = ""
    database_url: str = "sqlite+aiosqlite:///wiwi.db"
    redis_url: str = ""
    # Ceiling on live virtual keys per non-admin owner. Without it a user can
    # mint unbounded keys and rotate around any per-key budget or rate limit,
    # making those controls advisory. Admins are exempt.
    max_keys_per_user: int = 50

    @field_validator("max_keys_per_user")
    @classmethod
    def _key_cap_sane(cls, v: int) -> int:
        if v < 1:
            raise ValueError("max_keys_per_user must be >= 1")
        return v


    @field_validator("database_url")
    @classmethod
    def _db_url_default(cls, v: str) -> str:
        """Treat empty string as unset so the SQLite default applies."""
        return v or "sqlite+aiosqlite:///wiwi.db"


class WiwiSettings(BaseModel):
    drop_params: bool = True
    max_request_body_mb: int = 50
    log_requests: bool = True
    store_prompts_in_spend_logs: bool = False
    """Prune request_logs older than this many days at startup. 0 = keep forever."""
    log_retention_days: int = 30
    host: str = "0.0.0.0"
    port: int = 4000
    # Absolute public base URL (scheme + host, no trailing slash) when the
    # gateway sits behind a proxy — e.g. "https://wiwi.example.com". When set,
    # it is the ONLY origin used to build OAuth callback URLs and
    # X-Forwarded-* headers are ignored. Unset falls back to the request's own
    # Host header (never X-Forwarded-Host) so an attacker cannot point an
    # OAuth callback at their own origin.
    public_url: str = ""
    header_allowlist: list[str] = Field(
        default_factory=lambda: [
            "anthropic-version", "anthropic-beta",
            "openai-organization", "openai-project", "openai-beta",
        ]
    )


class WiwiConfig(BaseModel):
    providers: list[ProviderDef] = Field(default_factory=list)
    model_list: list[ModelEntry] = Field(default_factory=list)
    router_settings: RouterSettings = Field(default_factory=RouterSettings)
    general_settings: GeneralSettings = Field(default_factory=GeneralSettings)
    wiwi_settings: WiwiSettings = Field(default_factory=WiwiSettings)

    @model_validator(mode="after")
    def _model_refs_exist(self) -> WiwiConfig:
        names = {p.name for p in self.providers}
        # alias_id must be unique across providers so cross-provider pool
        # resolution is unambiguous.
        seen_alias: dict[str, str] = {}
        for p in self.providers:
            if p.alias_id is None:
                continue
            prior = seen_alias.get(p.alias_id)
            if prior is not None:
                raise ValueError(
                    f"alias_id {p.alias_id!r} is used by both provider"
                    f" {prior!r} and {p.name!r}")
            seen_alias[p.alias_id] = p.name
        for entry in self.model_list:
            if entry.wiwi_params.provider not in names:
                raise ValueError(
                    f"model {entry.model_name!r} references unknown provider"
                    f" {entry.wiwi_params.provider!r}")
        return self


def load_config(path: str | Path) -> WiwiConfig:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {p}: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"{p} must contain a YAML mapping at top level")
    return _validate(raw)

def load_config_from_string(raw_yaml: str) -> WiwiConfig:
    """Load config from inline YAML (for WIWI_CONFIG env var in containers)."""
    try:
        raw = yaml.safe_load(raw_yaml) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in WIWI_CONFIG: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError("WIWI_CONFIG must contain a YAML mapping at top level")
    return _validate(raw)


def _validate(raw: dict) -> WiwiConfig:
    try:
        data = _interpolate(raw)
        # Filter out providers whose keys resolved to empty strings (env vars
        # not set) and model entries that reference those removed providers.
        # This lets the example config load in a fresh container where most
        # provider API keys are absent — only providers with real keys activate.
        # Providers explicitly declared with keys: [] still raise a validation
        # error — only keys that were present but resolved to "" are filtered.
        providers = data.get("providers", [])
        if isinstance(providers, list):
            survivors: list[dict] = []
            removed_names: set[str] = set()
            for p in providers:
                if not isinstance(p, dict):
                    continue
                keys = p.get("keys", [])
                pname = str(p.get("name", ""))
                if not isinstance(keys, list) or len(keys) == 0:
                    # keys: [] or missing — let Pydantic raise the validation error
                    survivors.append(p)
                    continue
                real_keys = [
                    k for k in keys
                    if isinstance(k, dict) and str(k.get("key", "")).strip()
                ]
                if real_keys:
                    p["keys"] = real_keys
                    survivors.append(p)
                else:
                    removed_names.add(pname)
            data["providers"] = survivors
            model_list = data.get("model_list", [])
            if isinstance(model_list, list):
                data["model_list"] = [
                    m for m in model_list
                    if isinstance(m, dict)
                    and isinstance(m.get("wiwi_params"), dict)
                    and m["wiwi_params"].get("provider", "") not in removed_names
                ]
        return WiwiConfig.model_validate(data)
    except ConfigError:
        raise
    except Exception as e:  # pydantic.ValidationError and friends
        loc = getattr(e, "errors", list)()
        first = loc[0] if loc else {}
        where = ".".join(str(x) for x in first.get("loc", []))
        raise ConfigError(f"invalid config at '{where or '<root>'}': {first.get('msg', e)}") from e
