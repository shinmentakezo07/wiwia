"""Round-83 regression tests — router/config/cost defects from the round-68/75
sweeps of AUDIT.md: #190, #199, #172, #182, #203.

Each regression test below fails against the pre-fix source and passes after
the fix; the ``Control:`` tests pin the behaviour a naive fix would break.

#190 — ``Deployment.settle_tokens`` resolved its reservation with
``_DepWindow.find_estimated(request_id)``, documented as "preferring an exact
request-id match" but falling back to the newest estimated event. Because
``settle_tokens`` called it FIRST, the fallback won before the id-based
``find_event`` was ever consulted: a request whose own reservation had aged out
of the 60 s window while it was still streaming wrote its real usage onto a
DIFFERENT in-flight request's event. Three cascades: the window under-counted
(admission let traffic past the cap), the rightful owner's event was flipped to
confirmed so ``release_slot`` no longer refunded it, and the rpm window settled
the wrong event. The fix mirrors ``ratelimit/memory.py``: ``id`` → any event for
that id → **append**, with the newest-estimated arm reachable only for id-less
callers.

#199 — ``ProviderKey.recover()`` resets ``err_count`` but early-returns unless
the cooldown window already elapsed, so the admin "reset this key" path (which
set ``status``/``cooldown_until`` by hand in ``server/app.py``) left the streak
at its retirement value and the next non-200 re-retired the key.
``recover(force=True)`` is the operator reset.

#172 — ``@app.get(config.router_settings.prometheus_path)`` runs inside
``create_app``, so a path parameter or a missing leading slash crashed route
registration (total outage on a config typo) or silently registered a host
pattern. Validated at config-parse time now.

#182 — a typoed ``os.environ/NAME`` resolved to ``""`` and the empty-key filter
deleted the provider and every model it served, with no warning.

#203 — ``CostEngine.cost_with_status`` priced negative token counts into a
negative charge (a credit), which ``record_spend`` then silently swallowed.
"""

from __future__ import annotations

import time
from pathlib import Path
from textwrap import dedent

import pytest
import structlog

from wiwi.config import (
    ConfigError,
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
    load_config,
    load_config_from_string,
)
from wiwi.cost.pricing import CostEngine
from wiwi.router.router import ProviderAccount, ProviderKey, Router

MASTER = "sk-wiwi-master-test"


def _cfg(**deployment_params) -> WiwiConfig:
    return WiwiConfig(
        providers=[ProviderDef(name="p1", provider="openai",
                               keys=[KeyDef(label="a", key="k")])],
        model_list=[ModelEntry(model_name="gpt-4o",
                               wiwi_params=DeploymentParams(
                                   provider="p1", model="gpt-4o",
                                   **deployment_params))],
        general_settings=GeneralSettings(master_key=MASTER,
                                         database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(num_retries=0),
    )


# ---------------------------------------------------------------------------
# #190 — a late settle must never adopt another request's reservation
# ---------------------------------------------------------------------------

def test_late_settle_does_not_adopt_a_live_reservation():
    """The first request's reservation aged out; its real usage must land on
    its own new event, leaving the in-flight request's reservation intact.

    RED: ``settle_tokens("long", 700)`` falls through ``find_estimated``'s
    newest-estimated arm and overwrites ``live``'s 300-token estimate with
    long's 700, flipping it to confirmed. The window then reads 700 while the
    truth is 700 actual + 300 still in flight, so the next 300-token request is
    admitted against a cap that is already full.
    """
    dep = Router(_cfg(tpm=1000, rpm=2)).groups["gpt-4o"][0]
    now = time.monotonic()
    dep.reserve_slot("long", 600)
    dep.reserve_slot("live", 300)

    # 'long' has been streaming for over a minute: both of its reservations
    # aged out of the 60 s window while it was still in flight.
    dep._window(True).events[0].ts -= 120.0
    dep._window(False).events[0].ts -= 120.0
    assert not dep.rate_limited(now, 0)  # prunes the aged-out events
    tpm = dep._window(True)
    assert [e.request_id for e in tpm.events] == ["live"], "setup: long aged out"

    # It now completes and settles its real usage.
    dep.settle_tokens("long", 700)

    assert tpm.total == 1000, (
        f"window holds {tpm.total}; expected 700 (long's actual) + 300 (live, "
        "still in flight) — the late settle adopted live's reservation")
    live = [e for e in tpm.events if e.request_id == "live"]
    assert len(live) == 1 and live[0].tokens == 300 and live[0].estimated, (
        "the in-flight request lost its own reservation: "
        f"{[(e.request_id, e.tokens, e.estimated) for e in tpm.events]}")

    # The cap must still bind: 700 + 300 == the cap, so a further 300 is over.
    assert dep.rate_limited(now, 300), (
        "admitted past the cap — the window under-counted the in-flight request"
    )
    # Control: an estimate that still fits is admitted.
    assert not dep.rate_limited(now, 0)


def test_rightful_owner_is_still_refundable_after_a_late_settle():
    """The over-admission's second half: the swallowed refund is a permanent
    leak for the rest of the window.

    RED: the mis-attributed event is flipped to ``estimated=False``, so
    ``release_slot`` (which refunds estimated events only) finds nothing for the
    in-flight request and silently no-ops.
    """
    dep = Router(_cfg(tpm=1000, rpm=2)).groups["gpt-4o"][0]
    now = time.monotonic()
    dep.reserve_slot("long", 600)
    dep.reserve_slot("live", 300)
    dep._window(True).events[0].ts -= 120.0
    dep._window(False).events[0].ts -= 120.0
    dep.rate_limited(now, 0)
    dep.settle_tokens("long", 700)

    # 'live' now fails upstream and is refunded.
    dep.release_slot("live")

    tpm = dep._window(True)
    assert not any(e.request_id == "live" for e in tpm.events), (
        "'live' still holds a reservation after its own refund — the late "
        "settle had already consumed it, so the refund no-op'd")
    assert [(e.request_id, e.tokens, e.estimated) for e in tpm.events] == [
        ("long", 700, False)], "only long's real usage should remain"
    assert tpm.total == 700, tpm.total
    assert list(dep._window(False).events) == [], (
        "the rpm slots must both be gone: long's aged out, live's was refunded")


def test_repeated_settle_of_one_request_still_adjusts_in_place():
    """Control: the append arm must not double-charge a request settled twice.

    The pump prices a completed stream and can then be cancelled while blocking
    on the output queue, so its cancellation handler prices the same request
    again.
    """
    dep = Router(_cfg(tpm=1000)).groups["gpt-4o"][0]
    dep.reserve_slot("r1", 500)
    dep.settle_tokens("r1", 200)
    dep.settle_tokens("r1", 200)
    tpm = dep._window(True)
    assert tpm.total == 200, "one request settled twice must occupy 200, not 400"
    assert len(tpm.events) == 1


def test_idless_settle_still_reconciles_the_newest_estimate():
    """Control: an id-less caller keeps the lenient newest-estimated arm."""
    dep = Router(_cfg(tpm=1000)).groups["gpt-4o"][0]
    dep.reserve_slot("", 900)
    dep.settle_tokens("", 50)
    tpm = dep._window(True)
    assert tpm.total == 50, "an id-less settle must replace its own estimate"
    assert len(tpm.events) == 1 and not tpm.events[0].estimated


def test_settle_without_any_reservation_still_records_the_usage():
    """Control: a resume attempt on a deployment that never admitted the
    original request must still be charged against the cap."""
    dep = Router(_cfg(tpm=1000)).groups["gpt-4o"][0]
    dep.settle_tokens("never-admitted", 400)
    tpm = dep._window(True)
    assert tpm.total == 400
    assert [(e.request_id, e.tokens) for e in tpm.events] == [("never-admitted", 400)]


# ---------------------------------------------------------------------------
# #199 — the admin key reset must clear the failure streak
# ---------------------------------------------------------------------------

def _key() -> ProviderKey:
    return ProviderKey(label="a", secret="k")


def test_forced_recover_clears_the_failure_streak():
    """RED: ``recover()`` early-returns while the cooldown window is running
    (and for a terminal ``invalid``), so the admin reset left ``err_count`` at
    its retirement value and the very next non-200 re-retired the key."""
    key = _key()
    key.err_count = 5
    key.mark_invalid(600.0)

    key.recover(force=True)

    assert key.status == "active", key.status
    assert key.cooldown_until == 0.0
    assert key.err_count == 0, "the streak survived the operator reset"
    assert key.available, "a reset key must be usable immediately"


def test_forced_recover_revives_a_terminal_invalid():
    """Control for the reset's scope: a terminal ``invalid`` is otherwise only
    revivable by the healer, but the admin reset is the operator saying so."""
    key = _key()
    key.err_count = 7
    key.mark_invalid(None)  # terminal: cooldown_until == 0.0
    assert not key.available

    key.recover(force=True)

    assert key.status == "active" and key.available
    assert key.err_count == 0


def test_plain_recover_still_refuses_to_resurrect_a_terminal_invalid():
    """Control: the non-forced path keeps AUDIT #115's contract — pick_key's
    sweep must not resurrect a genuinely dead credential."""
    key = _key()
    key.err_count = 5
    key.mark_invalid(None)

    key.recover()

    assert key.status == "invalid" and not key.available
    assert key.err_count == 5


def test_one_failure_after_a_forced_recover_does_not_re_retire():
    """The observable consequence: the reset must actually stick."""
    acct = ProviderAccount(name="p", provider_type="openai", base_url="http://x",
                           keys=[_key()])
    key = acct.keys[0]
    key.err_count = 5
    key.mark_invalid(600.0)
    key.recover(force=True)

    # One 401 counts as two consecutive failures in any_error mode.
    acct.on_result(key, 401, None, failover_mode="any_error",
                   key_max_consecutive_fails=5)

    assert key.status != "invalid", (
        "a single failure after the reset re-retired the key — the reset "
        "cleared status but not the streak")
    assert key.status == "cooling", key.status


# ---------------------------------------------------------------------------
# #172 — an invalid prometheus_path must be rejected at config-parse time
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "/metrics/{job}",        # path parameter: FastAPI would capture /metrics/x
    "metrics",               # no leading slash: registered as a HOST pattern
    "/metrics/{job:int}",
    "/metrics/{job}/{job}",  # duplicate param -> ValueError at registration
    "/metrics/{job:foo}",    # unknown convertor -> AssertionError
])
def test_invalid_prometheus_path_is_rejected(bad):
    """RED: the value reached ``@app.get(...)`` inside ``create_app``, so the
    typo either failed route registration (the gateway never boots) or silently
    registered something that is not ``/metrics``."""
    raw = dedent(f"""
        router_settings:
          prometheus_enabled: true
          prometheus_path: "{bad}"
    """)
    with pytest.raises(ConfigError) as ei:
        load_config_from_string(raw)
    assert "prometheus_path" in str(ei.value)


@pytest.mark.parametrize("good", ["/metrics", "/internal/metrics", "/m"])
def test_valid_prometheus_path_loads(good):
    """Control: a literal path still loads and still registers its route."""
    from wiwi.server.app import create_app

    raw = dedent(f"""
        general_settings:
          master_key: {MASTER}
        router_settings:
          prometheus_enabled: true
          prometheus_path: "{good}"
    """)
    cfg = load_config_from_string(raw)
    assert cfg.router_settings.prometheus_path == good

    app = create_app(cfg)
    assert any(getattr(r, "path", None) == good for r in app.routes), (
        f"no route registered at {good!r}")


def test_invalid_prometheus_path_rejected_from_a_file(tmp_path):
    """The file loader takes the same path, so a typo in wiwi.yaml is caught
    before uvicorn ever imports the app."""
    p = tmp_path / "wiwi.yaml"
    p.write_text(dedent("""
        router_settings:
          prometheus_enabled: true
          prometheus_path: "metrics"
    """))
    with pytest.raises(ConfigError, match="prometheus_path"):
        load_config(p)


# ---------------------------------------------------------------------------
# #182 — a dropped provider must be visible
# ---------------------------------------------------------------------------

def test_typoed_env_var_warns_and_names_provider_and_variable():
    """RED: the provider (and its models) vanished with no warning at all, so
    the resulting ``404 model not found`` read as a routing problem."""
    raw = dedent("""
        providers:
          - name: p-typo
            provider: openai
            keys:
              - {label: main, key: os.environ/WIWI_TYPO_KEY_XYZ}
          - name: p-real
            provider: anthropic
            keys:
              - {label: main, key: "sk-real"}
        model_list:
          - model_name: m-typo
            wiwi_params: {provider: p-typo, model: gpt-4o}
          - model_name: m-real
            wiwi_params: {provider: p-real, model: claude-x}
    """)
    with structlog.testing.capture_logs() as logs:
        cfg = load_config_from_string(raw)

    # The filter stays lenient: the config still loads with the real provider.
    assert [p.name for p in cfg.providers] == ["p-real"]
    assert [m.model_name for m in cfg.model_list] == ["m-real"]

    hits = [e for e in logs if e.get("event") == "provider_dropped_no_key"]
    assert len(hits) == 1, logs
    assert hits[0]["provider"] == "p-typo"
    assert hits[0]["env_vars"] == ["WIWI_TYPO_KEY_XYZ"], (
        "the warning must name the env var the key resolved from, or the typo "
        "is still invisible")


def test_no_warning_when_every_provider_survives(monkeypatch):
    """Control: the warning must not fire for the legitimate optional-provider
    case — a provider whose key is present, literal, or simply not declared in
    this config at all."""
    monkeypatch.setenv("WIWI_ROUND83_PRESENT", "sk-present")
    raw = dedent("""
        providers:
          - name: p-env
            provider: openai
            keys:
              - {label: main, key: os.environ/WIWI_ROUND83_PRESENT}
          - name: p-literal
            provider: anthropic
            keys:
              - {label: main, key: "sk-literal"}
        model_list:
          - model_name: m
            wiwi_params: {provider: p-env, model: gpt-4o}
    """)
    with structlog.testing.capture_logs() as logs:
        cfg = load_config_from_string(raw)

    assert [p.name for p in cfg.providers] == ["p-env", "p-literal"]
    assert not [e for e in logs if e.get("event") == "provider_dropped_no_key"], logs


def test_partial_key_drop_is_not_reported_as_a_dropped_provider(monkeypatch):
    """Control: only *fully* empty providers are dropped (and warned about); a
    provider that keeps one real key survives silently."""
    raw = dedent("""
        providers:
          - name: p-partial
            provider: openai
            keys:
              - {label: main, key: os.environ/WIWI_ROUND83_ABSENT}
              - {label: backup, key: "sk-backup"}
    """)
    with structlog.testing.capture_logs() as logs:
        cfg = load_config_from_string(raw)

    assert [p.name for p in cfg.providers] == ["p-partial"]
    assert [k.label for k in cfg.providers[0].keys] == ["backup"]
    assert not [e for e in logs if e.get("event") == "provider_dropped_no_key"], logs


def test_provider_with_no_keys_declared_is_not_reported_as_dropped():
    """Control: a provider that never declared a ``keys`` entry is untouched by
    the empty-key filter — nothing resolved to an empty value, so there is no
    env var to name and no warning to emit. (An explicit ``keys: []`` is a
    separate, hard validation error.)"""
    raw = dedent("""
        providers:
          - name: p-keyless
            provider: openai
    """)
    with structlog.testing.capture_logs() as logs:
        cfg = load_config_from_string(raw)

    assert [p.name for p in cfg.providers] == ["p-keyless"], (
        "a provider that never declared a key must survive the filter")
    assert not [e for e in logs if e.get("event") == "provider_dropped_no_key"], logs


def test_shipped_example_config_still_loads(monkeypatch):
    """Integration control: the shipped ``wiwi.yaml.example`` declares optional
    providers whose keys are normally absent. The filter must stay lenient there
    — each one drops, none of them errors — while every drop is now reported."""
    for var in ("OPENAI_API_KEY", "OPENAI_API_KEY_2", "ANTHROPIC_API_KEY",
                "OPENROUTER_API_KEY", "GMI_API_KEY", "BAI_API_KEY",
                "NVIDIA_NIM_API_KEY", "OPENCODE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    example = Path(__file__).resolve().parent.parent / "wiwi.yaml.example"
    with structlog.testing.capture_logs() as logs:
        cfg = load_config(example)

    # Only the provider with a literal key survives; the rest are dropped, not
    # rejected — which is the whole point of the lenient filter.
    assert [p.name for p in cfg.providers] == ["local-ollama"]
    hits = [e for e in logs if e.get("event") == "provider_dropped_no_key"]
    assert {h["provider"] for h in hits} == {
        "openai-main", "anthropic-main", "openrouter", "gmicloud", "bai",
        "nvidia-nim", "opencode-zen"}
    assert all(h["env_vars"] for h in hits), (
        "every drop must name the env var its keys resolved from: "
        f"{[(h['provider'], h['env_vars']) for h in hits]}")


# ---------------------------------------------------------------------------
# #203 — negative token counts must never price into a negative charge
# ---------------------------------------------------------------------------

def _priced() -> CostEngine:
    ce = CostEngine()
    ce.prices["m"] = {
        "input_cost_per_token": 1e-6,
        "output_cost_per_token": 2e-6,
        "cache_read_input_cost_per_token": 1e-7,
        "cache_creation_input_cost_per_token": 3e-6,
    }
    return ce


@pytest.mark.parametrize("kwargs,expected", [
    # Only the prompt term was floored, and only when
    # ``prompt_includes_cached`` was True; every case below priced a *credit*
    # before the fix (or inflated the prompt term by subtracting a negative
    # cache count), so each asserts the exact clamped charge.
    ({"prompt_tokens": 0, "completion_tokens": -100}, 0.0),
    ({"prompt_tokens": -50, "completion_tokens": -100}, 0.0),
    # cached_tokens=-100 made uncached_prompt 200 instead of 100.
    ({"prompt_tokens": 100, "completion_tokens": 0, "cached_tokens": -100}, 1e-4),
    # cache_creation_tokens=-100 was billed at 3e-6 -> a negative total.
    ({"prompt_tokens": 100, "completion_tokens": 0,
      "cache_creation_tokens": -100}, 1e-4),
    ({"prompt_tokens": -50, "completion_tokens": -100, "cached_tokens": -10,
      "cache_creation_tokens": -10}, 0.0),
])
def test_negative_token_counts_are_clamped_to_zero(kwargs, expected):
    """RED: ``cost('m', 0, -100)`` returned ``-0.0002``; ``record_spend`` then
    dropped the credit on the floor (``add_cost <= 0``), leaving
    ``spend_to_date`` untouched while the row logged a negative cost."""
    state = _priced().cost_with_status("m", **kwargs)
    assert state.unpriced is False
    assert state.cost >= 0.0, f"negative charge {state.cost} for {kwargs}"
    assert state.cost == pytest.approx(expected), kwargs


def test_negative_counts_with_excluded_cached_prompt_are_clamped():
    """Anthropic's shape: ``prompt_includes_cached=False`` took the unclamped
    ``prompt_tokens`` branch, so the negative prompt term was billed."""
    state = _priced().cost_with_status("m", -50, -100, cached_tokens=-10,
                                       prompt_includes_cached=False)
    assert state.cost == 0.0, state.cost


def test_positive_pricing_is_unchanged_by_the_clamp():
    """Control: the four terms still price exactly as before."""
    state = _priced().cost_with_status("m", 1000, 500, cached_tokens=200,
                                       cache_creation_tokens=100)
    expected = (800 * 1e-6) + (200 * 1e-7) + (100 * 3e-6) + (500 * 2e-6)
    assert state.cost == pytest.approx(expected)
    # All-zero usage stays free rather than unpriced.
    zero = _priced().cost_with_status("m", 0, 0)
    assert zero.cost == 0.0 and zero.unpriced is False
