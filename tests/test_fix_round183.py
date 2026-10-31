"""Round 183 — a free-tier quota 429 is a quota, not a rate limit (AUDIT #556).

OpenCode's free allowance is accounted against the account, the workspace and
the network; once it is spent the upstream answers ``429 FreeUsageLimitError``
("Free usage limit reached … subscribe to OpenCode Go") — *usually with* a
``Retry-After``. Before this round that body took the ordinary rate-limit path:
``error_from_provider_status`` rendered it ``429 rate_limit_error`` and
``status_for_key_pool`` reported it to the pool, so the single ``anonymous``
sentinel every free Zen deployment shares was cooled for the stated window and
one exhausted allowance answered ``503 no healthy deployment`` for the whole
tier. That is the same outage AUDIT #469 fixed for a 429 with *no* window; the
quota case slipped through because it does state one.

The classification now reads the body: a 429 that names a quota/billing
condition is an entitlement refusal (402 ``permission_error``), which the key
pool ignores, and a refusal that arrives *inside* a 200 event stream takes the
same classification so it is charged identically.

What this round deliberately does not do is rotate the session id or the source
address on that error. The vendor counts the allowance per
account/workspace/network and flags "abusive traffic from this IP address" as
its own refusal, so a rotation would be quota evasion, not a fix — and the
adapter cannot change the gateway's source address anyway.
"""

from __future__ import annotations

import orjson
import pytest

from wiwi.providers.base import (
    error_from_provider_status,
    is_quota_refusal,
    status_for_key_pool,
)
from wiwi.providers.opencode_adapter import OpencodeAdapter, _envelope_stream_error
from wiwi.router.router import ProviderAccount, ProviderKey, deployment_scoped_failure
from wiwi.streaming import deltas as dl

ZEN = "io"

# The body the live gateway logged on 2026-10-30 (shape as Zen sends it).
FREE_QUOTA_429 = (
    '{"type":"error","error":{"type":"FreeUsageLimitError",'
    '"message":"io rate limited: Free usage limit reached. Free models work in '
    'OpenCode (https://opencode.ai); subscribe to OpenCode Go to keep going: '
    'https://opencode.ai/go"}}'
)


# -- classification ----------------------------------------------------------


def test_quota_429_is_an_entitlement_refusal_not_a_rate_limit():
    err = error_from_provider_status(429, FREE_QUOTA_429, ZEN)

    assert err.status == 402
    assert err.etype == "permission_error"
    assert err.retryable is True, "it must still fail over to another deployment"
    assert err.upstream_type == "FreeUsageLimitError", "the vendor's own label survives"
    assert "quota exhausted" in err.message
    assert "Free usage limit reached" in err.message, "the vendor wording is preserved"


def test_quota_429_is_recognised_from_the_wording_alone():
    """Console sometimes drops the type and keeps only the human message."""
    body = ('{"error":{"message":"Free usage limit reached. Subscribe to OpenCode '
            'Go to keep going: https://opencode.ai/go"}}')

    err = error_from_provider_status(429, body, ZEN)

    assert (err.status, err.etype) == (402, "permission_error")
    assert is_quota_refusal(body) is True


def test_a_plain_429_is_still_a_rate_limit():
    """Control: the fix keys on the body, never on the status alone."""
    body = '{"error":{"message":"Rate limit exceeded. Please retry after 1s."}}'

    err = error_from_provider_status(429, body, "openai")

    assert (err.status, err.etype) == (429, "rate_limit_error")
    assert status_for_key_pool(err) == 429, "an ordinary rate limit still cools the key"


def test_a_quota_429_reports_nothing_to_the_key_pool():
    err = error_from_provider_status(429, FREE_QUOTA_429, ZEN)

    assert status_for_key_pool(err) is None


@pytest.mark.parametrize("failover_mode", ["any_error", "standard"])
def test_a_quota_429_leaves_the_shared_sentinel_in_rotation(failover_mode):
    """One exhausted free allowance must not retire the tier's only credential.

    The router charges the pool only when ``status_for_key_pool`` names a
    status; a quota refusal names none, so the sentinel stays active and the
    other free deployments keep serving (AUDIT #469's contract, quota edition).
    """
    err = error_from_provider_status(429, FREE_QUOTA_429, ZEN)
    acct = ProviderAccount(
        name="zen", provider_type="opencode", base_url="https://zen.example",
        keys=[ProviderKey(label="anonymous", secret="anonymous")])
    key = acct.keys[0]

    # Exactly what ``execute_with_retries`` does with the pool status.
    status = status_for_key_pool(err)
    acct.on_result(key, status, err.retry_after, failover_mode=failover_mode)

    assert status is None
    assert key.status == "active"
    assert key.available
    assert key.err_count == 0


def test_a_quota_429_is_not_a_deployment_fault_either():
    """It is a statement about an allowance, so it cools neither scope."""
    err = error_from_provider_status(429, FREE_QUOTA_429, ZEN)

    assert deployment_scoped_failure(status_for_key_pool(err), err.retry_after) is False


def test_a_quota_429_stating_a_window_still_does_not_cool_the_key():
    """The Retry-After is a reset hint for the caller, not a credential verdict."""
    err = error_from_provider_status(429, FREE_QUOTA_429, ZEN)
    err.retry_after = 32217.0  # measured reset window (GitHub #58205)

    assert status_for_key_pool(err) is None
    assert err.retry_after == 32217.0


# -- the mid-stream envelope -------------------------------------------------


def test_a_quota_refusal_inside_the_stream_is_classified_too():
    """A refusal after the 200 has no HTTP status to classify — tag it here."""
    d = _envelope_stream_error(FREE_QUOTA_429)

    assert d is not None
    assert (d.status, d.etype) == (402, "permission_error")
    assert dl.stream_error_type(d) == "permission_error"


def test_a_bare_string_quota_envelope_is_recognised():
    d = _envelope_stream_error(
        '{"error":"Free usage limit reached. Subscribe to OpenCode Go."}')

    assert d is not None
    assert (d.status, d.etype) == (402, "permission_error")


def test_a_plain_stream_error_keeps_its_generic_classification():
    """Control: an ordinary in-stream rate limit is not turned into a quota."""
    d = _envelope_stream_error('{"error":"rate limited"}')

    assert d is not None
    assert d.status is None and d.etype is None
    assert dl.stream_error_type(d) == "api_error"


def test_the_responses_failed_event_carries_the_quota_classification():
    a = OpencodeAdapter()
    a._last_route = "responses"
    payload = orjson.dumps({
        "type": "response.failed",
        "response": {"status": "failed",
                     "error": {"type": "FreeUsageLimitError",
                               "message": "Free usage limit reached"}},
    }).decode()

    out = a.decode_stream_event("response.failed", payload)

    errors = [d for d in out if isinstance(d, dl.StreamError)]
    assert len(errors) == 1
    assert (errors[0].status, errors[0].etype) == (402, "permission_error")


def test_the_responses_failed_control_stays_generic():
    a = OpencodeAdapter()
    a._last_route = "responses"
    payload = orjson.dumps({
        "type": "response.failed",
        "response": {"status": "failed", "error": {"message": "boom"}},
    }).decode()

    out = a.decode_stream_event("response.failed", payload)

    errors = [d for d in out if isinstance(d, dl.StreamError)]
    assert len(errors) == 1
    assert errors[0].status is None and errors[0].etype is None
