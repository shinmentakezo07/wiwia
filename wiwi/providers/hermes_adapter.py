"""Hermes provider adapter (Nous Portal, OpenAI-compatible).

Hermes Nous Portal is an OpenAI Chat Completions-compatible gateway
(``https://inference-api.nousresearch.com/v1``). No wire-format quirks are
needed at request-build time, so this adapter is a specialization of
:class:`~wiwi.providers.openai_adapter.OpenAIAdapter` that opts out of the
OpenAI Realtime WebSocket surface (which Portal does not expose).

Auth: a plain Nous API key or a Portal OAuth access token both work as a bearer.
One provider pool key holds one Nous account: the secret is either a
``hermes:<json>`` credential blob (:func:`wiwi.providers.hermes_oauth.encode_credential`,
carrying the account's access + refresh token for multi-account rotation,
AUDIT #427) or a bare ``hermes:<token>`` paste-a-token. Both resolve to the
account's access token, which the inference API accepts on its own — the CLI's
``agent_key`` is the same JWT as its ``access_token``.
"""


from wiwi.providers import hermes_oauth
from wiwi.providers.base import ProviderKeyRef
from wiwi.providers.openai_adapter import OpenAIAdapter


class HermesAdapter(OpenAIAdapter):
    """OpenAI Chat Completions wire format against Nous Portal."""

    provider_type = "hermes"

    def headers(self, key: ProviderKeyRef) -> dict[str, str]:
        h = super().headers(key)
        # One pool key = one Nous account; the bearer is that account's access
        # token, whether the secret is a JSON blob or a bare hermes:<token>.
        h["Authorization"] = f"Bearer {hermes_oauth.credential_bearer(key.secret)}"
        return h

    def realtime_url(self, base_url: str) -> str | None:
        return None
