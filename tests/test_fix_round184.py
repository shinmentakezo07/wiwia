"""Round 184 — the flagged-network 403 was charged to the credential (AUDIT #557).

OpenCode's free tier refuses a request from an address it has flagged with a
403 that says so in the body:

    OpenCode's free models aren't available from your current network because
    we've detected abusive traffic from this IP address. This isn't related to
    your account.

Before this round the body matched no entitlement marker, so the 403 fell
through to the credential branch: `authentication_error`, `status_for_key_pool`
returned 403, and `on_result` marked a perfectly healthy key invalid for 600 s —
while the caller was told to re-authenticate a key that was never the problem
and the operator went looking for a credential or a request-shape bug.

The refusal is an account/exit-level one, so it is classified with the other
entitlement refusals: 403 `permission_error`, retryable (a fallback group on a
different provider can serve it), and the key is left alone. What it is *not*
is an invitation to retry the same egress or to rotate the source address —
that is the vendor's abuse control (AUDIT #556).

Coverage here is the classification and its accounting; the wording variants are
the ones transcribed from the live refusal and from another gateway that proxies
the same tier (opencode2api, `internal/gateway/abuse.go`).
"""

from __future__ import annotations

import pytest

from wiwi.providers.base import (
    error_from_provider_status,
    status_for_key_pool,
)
from wiwi.router.router import ProviderAccount, ProviderKey

ZEN = "io"

LIVE_NETWORK_403 = (
    '{"error":{"message":"OpenCode\'s free models aren\'t available from your '
    'current network because we\'ve detected abusive traffic from this IP '
    'address. This isn\'t related to your account. If you\'re on a VPN or proxy, '
    'turn it off and try again, or subscribe to OpenCode Go at '
    'https://opencode.ai/go to keep building."}}'
)


# -- classification ----------------------------------------------------------


@pytest.mark.parametrize("body", [
    LIVE_NETWORK_403,
    ('{"error":{"message":"Forbidden, OpenCode Free Model Gateway Detect '
     'Abuse Traffic"}}'),
    '{"error":{"message":"Abuse of traffic"}}',
    # The wording without the abuse phrase: the network is still the subject.
    ('{"error":{"message":"Free models are not available from your current '
     'network."}}'),
])
def test_a_flagged_network_is_an_entitlement_refusal(body):
    err = error_from_provider_status(403, body, ZEN)

    assert (err.status, err.etype) == (403, "permission_error")
    assert err.retryable is True, "a fallback on another provider can still serve"
    assert "network" in err.message, "the message must name the real cause"
    assert status_for_key_pool(err) is None, "the key is not the problem"


def test_a_flagged_network_does_not_retire_the_key():
    err = error_from_provider_status(403, LIVE_NETWORK_403, ZEN)
    acct = ProviderAccount(
        name="zen", provider_type="opencode", base_url="https://zen.example",
        keys=[ProviderKey(label="anonymous", secret="anonymous")])
    key = acct.keys[0]

    # Exactly what ``execute_with_retries`` does with the pool status.
    status = status_for_key_pool(err)
    acct.on_result(key, status, None, failover_mode="standard")

    assert status is None
    assert key.status == "active"
    assert key.available
    assert key.err_count == 0


def test_a_genuine_auth_failure_is_unchanged():
    """Control: a 403 that does not name the network is still a credential."""
    err = error_from_provider_status(
        403, '{"error":{"message":"Invalid API key provided."}}', "openai")

    assert err.etype == "authentication_error"
    assert status_for_key_pool(err) == 403


def test_the_shape_refusal_is_still_a_billing_refusal():
    """Control: FreeTierError keeps its 402 rendering (AUDIT #71/#150)."""
    err = error_from_provider_status(
        403, '{"error":{"type":"FreeTierError","message":"OpenCode\'s free tier '
        'can only be used from within OpenCode."}}', ZEN)

    assert (err.status, err.etype) == (402, "permission_error")
    assert "requires billing" in err.message
    assert status_for_key_pool(err) is None
