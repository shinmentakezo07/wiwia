"""Round 150: a refusal log must say why the *vendor* refused.

An OpenCode Zen free-model outage produced one log line — ``http_403`` — and a
client message reading ``io requires billing (403)``. Both pushed the
investigation at the account and the keys, when the upstream's own body said
``FreeTierError``: *this request does not look like our client*. A whole round
was spent on keys, quotas and regions before the request shape was suspected.

The cause was that ``WiwiError`` kept only wiwi's own classification. That
classification is deliberately coarse — it is what decides key-pool accounting
and what the caller's dialect renders — and it is lossy exactly where it hurts:
``FreeTierError`` and ``CreditsError`` both arrive as ``403`` and both become
``permission_error`` + 402, so "your account is wrong" and "our request is wrong"
become indistinguishable from outside.

``WiwiError.upstream_type`` now carries the vendor's label verbatim, and the
gateway logs it on every refusal path (``upstream_refused``) alongside the
rendered etype, so the diagnostic starts from what the vendor said. The healer's
verdict detail carries it too, because that is what an operator reads when a
provider looks unhealthy.

Pinned below: the label survives classification on every branch (not just the
entitlement one), the mapping is unchanged everywhere else, and a body with no
type reports ``None`` rather than a guess.
"""

from __future__ import annotations

from wiwi.providers.base import error_from_provider_status, status_for_key_pool

ZEN = 'zen'
UPSTREAM_TYPE = "FreeTierError"
ZEN_BODY = (
    '{"type":"error","error":{"type":"FreeTierError","message":"OpenCode\'s '
    'free tier can only be used from within OpenCode."}}'
)


# -- the label survives every branch ------------------------------------------


def test_billing_refusal_keeps_the_vendor_label():
    err = error_from_provider_status(403, ZEN_BODY, ZEN)
    assert err.upstream_type == UPSTREAM_TYPE
    # And the existing rendering is untouched — this field is additive.
    assert err.status == 402
    assert err.etype == "permission_error"
    assert err.retryable is True
    assert "requires billing" in err.message


def test_the_label_is_carried_on_the_credential_branch_too():
    # The classification branches are independent of the tagging: a 401 that
    # happens to name a type must keep it, or the gap reopens on the next branch.
    err = error_from_provider_status(401, '{"error":{"type":"AuthError"}}', ZEN)
    assert err.upstream_type == "AuthError"
    assert err.etype == "authentication_error"
    assert status_for_key_pool(err) == 401, "unchanged accounting"


def test_the_label_is_carried_on_rate_limit_and_server_branches():
    assert error_from_provider_status(
        429, '{"error":{"type":"FreeUsageLimitError"}}', ZEN).upstream_type == (
        "FreeUsageLimitError")
    assert error_from_provider_status(
        500, '{"error":{"type":"server_error"}}', ZEN).upstream_type == "server_error"
    assert error_from_provider_status(
        400, '{"error":{"type":"ModelDeprecated"}}', ZEN).upstream_type == "ModelDeprecated"


def test_a_body_with_no_type_reports_none_rather_than_guessing():
    # An empty body is the common case for a proxy-level 403; inventing a label
    # would put a fabricated cause in the log.
    err = error_from_provider_status(403, "", ZEN)
    assert err.upstream_type is None
    err = error_from_provider_status(403, "plain text gateway page", ZEN)
    assert err.upstream_type is None


def test_a_wrongly_typed_body_does_not_break_classification():
    # `error` is sometimes a bare string and sometimes a structure; neither may
    # raise on the way to the label.
    for body in ('{"error":"rate limited"}', '{"error":{"type":123}}',
                 '{"error":{"type":"FreeTierError"}}', "not json at all"):
        err = error_from_provider_status(403, body, ZEN)
        assert isinstance(err.upstream_type, (str, type(None))), body


# -- it did not need a rendering change --------------------------------------


def test_key_pool_accounting_is_unchanged():
    # The whole point of the coarse etype was that a policy refusal must not
    # retire a healthy key. Adding the vendor label must not disturb that.
    billing = error_from_provider_status(403, ZEN_BODY, ZEN)
    assert status_for_key_pool(billing) is None
    auth = error_from_provider_status(401, '{"error":{"type":"AuthError"}}', ZEN)
    assert status_for_key_pool(auth) == 401


def test_the_error_message_still_hides_credentials():
    # The label is a type name from the body; it must not become a channel for
    # echoing a key the upstream quoted back at us.
    err = error_from_provider_status(401,
                                     '{"error":{"type":"AuthError",'
                                     '"message":"bad key sk-live-ABCDEFGHIJK"}}', ZEN)
    assert err.upstream_type == "AuthError"
    assert "sk-live-ABCDEFGHIJK" not in err.message
