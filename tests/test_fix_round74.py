"""Round 74: the router's proxy log must surface entitlement refusals.

Round 71 classified Zen's ``403 FreeTierError`` as a ``permission_error``
billing refusal (402) and made ``status_for_key_pool`` return ``None`` so the
key pool is never charged. But ``execute_with_retries`` derived its proxy-log
gate from that same value — ``if status is not None: _proxy("warn", ...)`` —
so the moment the classification landed, the ONE line that told the operator
*why* a request failed over vanished from the log exactly for these errors.
An operator tailing the proxy log saw requests fail with no upstream-status
line at all: the self-inflicted-outage regression was fixed but rendered
invisible.

Pinned here:

- a ``permission_error`` on 401/402/403 produces a proxy log line naming the
  provider, group, key label, the account-entitlement status and the upstream
  message ("account entitlement (402): ... requires billing ...")
- the key pool still receives NOTHING for that error (``err_count`` stays 0,
  key stays active) — the round-71 guarantee is unchanged
- a genuine credential rejection (``authentication_error``) keeps the
  historical ``upstream <status> on ...`` warn shape and still charges the key
- a 400 invalid_request_error (``status_for_key_pool`` → None) still produces
  no warn line, matching the pre-existing contract for caller-side errors
- a retryable upstream 5xx keeps the historical ``upstream <status>`` shape
"""

from __future__ import annotations

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)
from wiwi.core.context import RequestContext
from wiwi.ir import types as ir
from wiwi.providers.base import (
    WiwiError,
    error_from_provider_status,
    status_for_key_pool,
)
from wiwi.router.router import Router, execute_with_retries

_ZEN_FREE_TIER_403 = (
    '{"type":"error","error":{"type":"FreeTierError",'
    '"message":"Error from provider (Console): OpenCode\'s free tier can only '
    'be used from within OpenCode"}}'
)


def _router(num_retries: int = 0) -> Router:
    cfg = WiwiConfig(
        providers=[ProviderDef(
            name="io", provider="opencode",
            base_url="https://opencode.ai/zen/v1",
            keys=[KeyDef(label="main", key="sk-zen-test")]),
        ],
        model_list=[ModelEntry(
            model_name="zen", wiwi_params=DeploymentParams(
                provider="io", model="muse-spark-1.3-contributor-free")),
        ],
        general_settings=GeneralSettings(
            master_key="sk-wiwi-master-test",
            database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(num_retries=num_retries),
    )
    return Router(cfg)


def _ctx() -> RequestContext:
    return RequestContext(
        surface="chat",
        ir_req=ir.Request(model="zen",
                          messages=[ir.Message(role="user", parts=[])]),
        group="zen")


def _capture(router: Router) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    router.log_proxy = lambda level, message, req_id: lines.append((level,
                                                                    message))
    return lines


async def _run(router: Router, err: WiwiError) -> None:
    async def call_one(dep, key, ctx):
        raise err

    try:
        await execute_with_retries(router, _ctx(), call_one)
    except WiwiError as e:
        assert e is err


# -- the fix -------------------------------------------------------------------

async def test_entitlement_refusal_is_logged_as_account_entitlement():
    """FreeTierError (classified 402 permission_error) must still warn —
    naming the provider, deployment and the account-entitlement cause."""
    r = _router()
    lines = _capture(r)
    err = error_from_provider_status(403, _ZEN_FREE_TIER_403, "io")
    assert err.status == 402 and err.etype == "permission_error"
    assert status_for_key_pool(err) is None

    await _run(r, err)

    entitlement = [m for lvl, m in lines if "account entitlement (402)" in m]
    assert entitlement, lines
    assert "refused" in entitlement[0]
    assert "io" in entitlement[0]
    assert "zen/muse-spark-1.3-contributor-free" in entitlement[0]
    assert "[main]" in entitlement[0]
    assert "requires billing" in entitlement[0]
    # level must be warn: it is an operational warning, not an info note
    assert any(lvl == "warn" and m is entitlement[0] for lvl, m in lines)


async def test_policy_refusal_403_is_logged_as_account_entitlement():
    """Same visibility for the policy half (stays 403 permission_error)."""
    r = _router()
    lines = _capture(r)
    err = error_from_provider_status(
        401,
        '{"type":"error","error":{"type":"RegionError",'
        '"message":"not available in your country"}}',
        "io")
    assert err.status == 403 and err.etype == "permission_error"

    await _run(r, err)

    assert any("account entitlement (403)" in m and "denied access" in m
               for _lvl, m in lines), lines


async def test_entitlement_refusal_never_charges_key_pool():
    """The round-71 guarantee must survive the logging fix untouched."""
    r = _router(num_retries=3)
    _capture(r)
    err = error_from_provider_status(403, _ZEN_FREE_TIER_403, "io")

    await _run(r, err)

    key = r.providers["io"].keys[0]
    assert key.err_count == 0
    assert key.status == "active"


# -- historical shapes preserved ------------------------------------------------

async def test_credential_rejection_keeps_historical_log_shape():
    """A genuine bad-key 401 keeps `upstream <status> on ...` and charges the
    key exactly as before the entitlement classification existed."""
    r = _router()
    lines = _capture(r)
    err = error_from_provider_status(
        401,
        '{"type":"error","error":{"type":"AuthError","message":"Invalid API key."}}',
        "io")
    assert err.etype == "authentication_error"
    assert status_for_key_pool(err) == 401

    await _run(r, err)

    historical = [m for _lvl, m in lines if "upstream 401" in m]
    assert historical, lines
    assert "zen/muse-spark-1.3-contributor-free" in historical[0]
    assert "[io/main]" in historical[0]
    # and the pool IS charged (any_error: auth counts double)
    assert r.providers["io"].keys[0].err_count >= 2


async def test_caller_400_stays_silent_in_proxy_log():
    """A non-retryable caller-side 400 says nothing about key health and has
    never produced a warn line — keep that contract."""
    r = _router()
    lines = _capture(r)

    await _run(r, WiwiError(400, "invalid_request_error", "bad body"))

    assert not lines, lines


async def test_server_5xx_keeps_historical_log_shape():
    r = _router()
    lines = _capture(r)
    err = error_from_provider_status(500, '{"error":{"message":"boom"}}', "io")
    assert err.status == 502  # normalized

    await _run(r, err)

    assert any("upstream 502" in m for _lvl, m in lines), lines
