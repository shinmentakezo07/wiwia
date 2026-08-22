"""Config loading: wiwi.yaml -> typed models, os.environ/ interpolation, fail-fast validation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class ConfigError(Exception):
    """Raised with file/line context when the config is invalid."""


def _interpolate(value: Any) -> Any:
    """Recursively resolve `os.environ/NAME` strings."""
    if isinstance(value, str) and value.startswith("os.environ/"):
        var = value[len("os.environ/"):]
        resolved = os.getenv(var)
        if resolved is None:
            raise ConfigError(f"environment variable '{var}' is not set (referenced as {value!r})")
        return resolved
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
    provider: Literal["openai", "anthropic", "gemini", "openai-compatible"]
    base_url: str | None = None
    timeout_s: float = 120.0
    extra_headers: dict[str, str] = Field(default_factory=dict)
    keys: list[KeyDef] = Field(default_factory=list)

    @field_validator("keys")
    @classmethod
    def _need_keys(cls, v: list[KeyDef]) -> list[KeyDef]:
        if not v:
            raise ValueError("provider needs at least one key entry")
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


class ModelEntry(BaseModel):
    model_name: str
    wiwi_params: DeploymentParams


class RouterSettings(BaseModel):
    routing_strategy: Literal["simple-shuffle", "least-busy", "latency-based"] = "simple-shuffle"
    num_retries: int = 2
    timeout: float = 120.0
    allowed_fails: int = 3
    cooldown_time: float = 30.0
    fallbacks: dict[str, list[str]] = Field(default_factory=dict)
    context_window_fallbacks: dict[str, list[str]] = Field(default_factory=dict)
    model_group_alias: dict[str, str] = Field(default_factory=dict)
    global_rpm: int | None = None
    global_tpm: int | None = None


class GeneralSettings(BaseModel):
    master_key: str = ""
    database_url: str = "sqlite+aiosqlite:///wiwi.db"
    redis_url: str = ""


class WiwiSettings(BaseModel):
    drop_params: bool = True
    max_request_body_mb: int = 50
    log_requests: bool = True
    host: str = "0.0.0.0"
    port: int = 4000
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
    try:
        data = _interpolate(raw)
        return WiwiConfig.model_validate(data)
    except ConfigError:
        raise
    except Exception as e:  # pydantic.ValidationError and friends
        loc = getattr(e, "errors", list)()
        first = loc[0] if loc else {}
        where = ".".join(str(x) for x in first.get("loc", []))
        raise ConfigError(f"invalid config at '{where or '<root>'}': {first.get('msg', e)}") from e
