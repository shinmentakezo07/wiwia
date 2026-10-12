"""Hermes provider adapter (Nous Portal, OpenAI-compatible).

Hermes Nous Portal is an OpenAI Chat Completions-compatible gateway
(``https://inference-api.nousresearch.com/v1``). No wire-format quirks are
needed at request-build time, so this adapter is a specialization of
:class:`~wiwi.providers.openai_adapter.OpenAIAdapter` that opts out of the
OpenAI Realtime WebSocket surface (which Portal does not expose).

Auth: a plain Nous API key or a Portal OAuth access token both work as a bearer.
An OAuth token stored by the connect flow lands on the key pool prefixed
``hermes:`` (so it is distinguishable from a raw key); the prefix is stripped
here so the upstream sees ``Authorization: Bearer <token>``.
"""


from wiwi.providers.base import ProviderKeyRef
from wiwi.providers.openai_adapter import OpenAIAdapter

_OAUTH_PREFIX = "hermes:"


class HermesAdapter(OpenAIAdapter):
    """OpenAI Chat Completions wire format against Nous Portal."""

    provider_type = "hermes"

    def headers(self, key: ProviderKeyRef) -> dict[str, str]:
        h = super().headers(key)
        secret = key.secret
        if secret.startswith(_OAUTH_PREFIX):
            h["Authorization"] = f"Bearer {secret[len(_OAUTH_PREFIX):]}"
        return h

    def realtime_url(self, base_url: str) -> str | None:
        return None
