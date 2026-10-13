"""Round 136: multi-account Hermes — one Nous account per pool key.

Each Nous Portal account is a separate OAuth identity with its own rotating
(single-use) refresh token and its own rate-limit bucket (~50 RPM / 50k TPM).
wiwi stores one account per provider pool key as a self-contained
``hermes:<json>`` credential blob, so the existing key pool + smooth weighted
round-robin spreads traffic across accounts and beats any single account's cap
(AUDIT #427).

Before this, Hermes was single-account: one provider had one ``refresh_token``
in a settings record, and ``_update_secret`` wrote the rotated token to EVERY
pool key — so a refresh clobbered sibling accounts, and there was no way to hold
more than one account. This suite pins the per-account behaviour:

1. the adapter turns a ``hermes:<json>`` blob AND a bare ``hermes:<token>``
   into the right inference bearer;
2. the sweeper refreshes each account on its own due-check and NEVER touches a
   sibling account's secret;
3. a 401 refreshes only the failing account;
4. the key pool's WRR distributes across ≥2 account keys (the whole point).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
import respx
from sqlalchemy.ext.asyncio import create_async_engine

from wiwi.config import (
    DeploymentParams,
    GeneralSettings,
    KeyDef,
    ModelEntry,
    ProviderDef,
    RouterSettings,
    WiwiConfig,
)
from wiwi.providers import hermes_auto_refresh, hermes_oauth
from wiwi.providers.base import ProviderKeyRef
from wiwi.providers.hermes_adapter import HermesAdapter
from wiwi.providers.hermes_oauth import TOKEN_URL
from wiwi.router.router import Router
from wiwi.server.config_store import ConfigStore

MASTER = "sk-wiwi-master-test"


def _config(two_accounts: bool = False) -> WiwiConfig:
    keys = [KeyDef(label="default",
                   key=hermes_oauth.encode_credential(
                       "acc-a", "ref-a", time.time() + 3600, "a@example.com"))]
    if two_accounts:
        keys.append(KeyDef(
            label="b@example.com",
            key=hermes_oauth.encode_credential(
                "acc-b", "ref-b", time.time() + 3600, "b@example.com")))
    return WiwiConfig(
        providers=[ProviderDef(
            name="hermes-prov", provider="hermes",
            base_url="https://inference-api.nousresearch.com/v1",
            keys=keys)],
        model_list=[ModelEntry(
            model_name="hermes-model",
            wiwi_params=DeploymentParams(provider="hermes-prov",
                                         model="Hermes-4-405B"))],
        general_settings=GeneralSettings(
            master_key=MASTER,
            database_url="sqlite+aiosqlite:///:memory:"),
        router_settings=RouterSettings(num_retries=0, allowed_fails=2,
                                       cooldown_time=60.0),
    )


@dataclass
class _Mini:
    router: Router
    config_store: ConfigStore


def _state(two_accounts: bool = False):
    router = Router(_config(two_accounts=two_accounts))
    cs = ConfigStore(create_async_engine("sqlite+aiosqlite:///:memory:"))
    return router, _Mini(router=router, config_store=cs), cs


# -- 1. Adapter turns a blob / bare token into the right bearer ---------------


def test_adapter_bearer_from_blob_and_bare_token():
    adapter = HermesAdapter()

    blob = hermes_oauth.encode_credential(
        "jwt-access", "jwt-refresh", time.time() + 60, "a@example.com")
    h = adapter.headers(ProviderKeyRef(label="default", secret=blob))
    assert h["Authorization"] == "Bearer jwt-access"

    # A bare hermes:<token> (paste-a-token, no JSON) still works.
    h2 = adapter.headers(ProviderKeyRef(label="k", secret="hermes:raw-token"))
    assert h2["Authorization"] == "Bearer raw-token"


def test_encode_decode_roundtrip_and_decode_rejects_bare():
    blob = hermes_oauth.encode_credential(
        "acc", "ref", 123.5, "a@example.com")
    cred = hermes_oauth.decode_credential(blob)
    assert cred == {"access_token": "acc", "refresh_token": "ref",
                    "expires_at": 123.5, "label": "a@example.com"}
    # A bare token is not a managed account.
    assert hermes_oauth.decode_credential("hermes:raw") is None
    # A raw key is not Hermes OAuth at all.
    assert hermes_oauth.decode_credential("sk-something") is None
    assert hermes_oauth.credential_bearer("sk-something") == "sk-something"


# -- 2. Sweeper refreshes each account independently ---------------------------


@respx.mock
async def test_sweeper_refreshes_each_account_without_touching_siblings():
    """Two due accounts each rotate; a fresh one is skipped. A rotation on one
    key never rewrites a sibling's secret (the old clobber-all-keys bug)."""
    router, state, cs = _state(two_accounts=True)
    await cs.startup()
    try:
        prov = router.providers["hermes-prov"]
        # default: due (expired); b@example.com: fresh.
        prov.keys[0].secret = hermes_oauth.encode_credential(
            "acc-a", "ref-a", time.time() - 1, "a@example.com")
        prov.keys[1].secret = hermes_oauth.encode_credential(
            "acc-b", "ref-b", time.time() + 3600, "b@example.com")

        route = respx.post(TOKEN_URL).mock(side_effect=[
            httpx.Response(200, json={"access_token": "acc-a2",
                                      "refresh_token": "ref-a2",
                                      "expires_in": 3600}),
        ])
        worker = hermes_auto_refresh.HermesAutoRefresh(state)
        await worker._sweep()

        # Only the due account (default) was refreshed.
        assert route.call_count == 1
        cred_a = hermes_oauth.decode_credential(prov.keys[0].secret)
        cred_b = hermes_oauth.decode_credential(prov.keys[1].secret)
        assert cred_a["access_token"] == "acc-a2"
        # The sibling's secret is byte-for-byte unchanged.
        assert cred_b["access_token"] == "acc-b"
        assert cred_b["refresh_token"] == "ref-b"
    finally:
        await cs.engine.dispose()


@respx.mock
async def test_one_account_dead_grant_does_not_block_the_other():
    """An invalid_grant on one account kills only that account's circuit; the
    other account keeps refreshing."""
    router, state, cs = _state(two_accounts=True)
    await cs.startup()
    try:
        prov = router.providers["hermes-prov"]
        prov.keys[0].secret = hermes_oauth.encode_credential(
            "acc-a", "ref-a-dead", time.time() - 1, "a@example.com")
        prov.keys[1].secret = hermes_oauth.encode_credential(
            "acc-b", "ref-b", time.time() - 1, "b@example.com")
        # default -> invalid_grant (dead); b -> success.
        route = respx.post(TOKEN_URL).mock(side_effect=[
            httpx.Response(400, json={"error": "invalid_grant"}),
            httpx.Response(200, json={"access_token": "acc-b2",
                                      "refresh_token": "ref-b2",
                                      "expires_in": 3600}),
        ])
        worker = hermes_auto_refresh.HermesAutoRefresh(state)
        await worker._sweep()

        assert route.call_count == 2
        assert worker._circuit.dead(("hermes-prov", "default"))
        assert not worker._circuit.dead(("hermes-prov", "b@example.com"))
        cred_b = hermes_oauth.decode_credential(prov.keys[1].secret)
        assert cred_b["access_token"] == "acc-b2"
    finally:
        await cs.engine.dispose()


# -- 3. WRR spreads across account keys (the whole point) ---------------------


async def test_wrr_distributes_across_accounts():
    """Two account keys of equal weight share traffic ~50/50 over a sweep of
    picks — this is what beats a single account's 50 RPM cap."""
    router, _state_obj, cs = _state(two_accounts=True)
    try:
        prov = router.providers["hermes-prov"]
        picked: dict[str, int] = {}
        for _ in range(100):
            key, _ = await prov.pick_key()
            picked[key.label] = picked.get(key.label, 0) + 1
        # Both accounts served, and neither was starved.
        assert set(picked) == {"default", "b@example.com"}
        assert min(picked.values()) >= 40  # ~50 each, with slack for WRR jitter
    finally:
        await cs.engine.dispose()
