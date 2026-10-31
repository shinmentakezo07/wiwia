"""Egress-scoped refusals carry a machine-readable marker (AUDIT #557).

A refusal that names the *network* the request left from is the one class of
upstream error an operator-supplied egress pool can answer: retiring the exit
that carried it and drawing another is the only retry that can succeed. The
classifier already renders it as ``403 permission_error`` so the key pool
leaves the credential alone; this round adds ``WiwiError.egress_scoped`` so a
caller does not have to re-parse the message to know that.
"""

from __future__ import annotations

import json

from wiwi.providers.base import WiwiError, error_from_provider_status

# Verbatim from OpenCode's free tier (see AUDIT #557 and docs/OPENCODE.md).
# Note the absence of a ``type``: the live refusal carries only the message,
# which is why the network arm is reachable at all — a body typed
# ``FreeTierError`` is classified as a billing refusal before the markers run
# (round 184 covers that precedence).
_NETWORK_BLOCK = json.dumps({
    "error": {
        "message": (
            "OpenCode's free models aren't available from your current network "
            "because we've detected abusive traffic from this IP address. "
            "This isn't related to your account."
        ),
    }
})


def test_network_block_is_egress_scoped():
    err = error_from_provider_status(403, _NETWORK_BLOCK, "opencode")
    assert isinstance(err, WiwiError)
    assert err.status == 403
    assert err.etype == "permission_error"
    assert err.retryable is True
    assert err.egress_scoped is True


def test_network_block_leaves_the_key_pool_alone():
    from wiwi.providers.base import status_for_key_pool

    err = error_from_provider_status(403, _NETWORK_BLOCK, "opencode")
    assert status_for_key_pool(err) is None


def test_request_shape_refusal_is_not_egress_scoped():
    """A FreeTierError that names the *shape* is about the client, not the exit."""
    body = json.dumps({
        "error": {
            "type": "FreeTierError",
            "message": "OpenCode's free tier can only be used from within OpenCode.",
        }
    })
    err = error_from_provider_status(403, body, "opencode")
    assert err.status == 402
    assert err.egress_scoped is False


def test_quota_refusal_is_not_egress_scoped():
    """The free allowance is accounted per account/workspace/network.

    It is deliberately *not* marked egress-scoped: the allowance is shared, so
    rotating the exit cannot be assumed to buy anything, and treating it as an
    exit problem would churn tunnels for a spent quota.
    """
    body = json.dumps({
        "error": {
            "type": "FreeUsageLimitError",
            "message": "Free usage limit reached. Subscribe to OpenCode Go to keep going.",
        }
    })
    err = error_from_provider_status(429, body, "opencode")
    assert err.status == 402
    assert err.egress_scoped is False


def test_credential_rejection_is_not_egress_scoped():
    body = json.dumps({"error": {"type": "AuthError", "message": "Invalid API key."}})
    err = error_from_provider_status(401, body, "opencode")
    assert err.status == 401
    assert err.egress_scoped is False


def test_default_is_false():
    """The flag defaults off, so every pre-existing construction is unchanged."""
    assert WiwiError(500, "api_error", "boom").egress_scoped is False
