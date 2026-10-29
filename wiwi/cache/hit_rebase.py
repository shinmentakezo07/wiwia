"""Rebase a cached response body onto the request that is being served.

A cache hit replays the stored bytes, which were encoded for the request that
*produced* the entry. Its envelope identity — the response ``id`` and the
creation timestamp — therefore belongs to that earlier request, while the
caller is a different one (its own ``x-wiwi-request-id`` is in the headers and
in its log row). On the OpenAI Chat and Anthropic Messages surfaces the ``id``
is an opaque handle, so the mismatch is cosmetic. On the Responses surface it
is not: ``resp_<request_id>`` is dereferenced by ``GET /v1/responses/{id}`` and
by ``previous_response_id``, so a hit handed the caller an id naming *someone
else's* stored object (AUDIT #523).

The body is already dialect-shaped at this point and this module has no
business branching on dialects, so the rebase is mechanical: keep everything
except the envelope identity, and regenerate that from the current request id
using the prefix the cached body itself carries.

Streaming bodies are never cached, so only the non-streaming envelopes appear
here. Each field is rewritten only when present and shaped as expected — a
backend that stored something unexpected yields the original value rather than
a mangled one.
"""

from __future__ import annotations

import time
from typing import Any


def _rebased_id(stored: Any, req_id: str) -> Any:
    """``chatcmpl-<old>`` -> ``chatcmpl-<req_id>``.

    The separator is whatever the cached id used (``-`` or ``_``), so this does
    not need a per-dialect prefix table and cannot drift when a new surface
    picks a new one. An id with no separator is left alone: guessing would be
    worse than the stale value.
    """
    if not isinstance(stored, str) or not req_id:
        return stored
    for sep in ("-", "_"):
        if sep in stored:
            prefix = stored.split(sep, 1)[0]
            return f"{prefix}{sep}{req_id}"
    return stored


def rebase_cached_body(body: dict[str, Any], req_id: str) -> dict[str, Any]:
    """Return *body* with the envelope identity of the current request.

    Mutates in place: the caller owns the freshly ``orjson.loads``-ed cache
    payload, which is not shared with the cache entry (that is stored as
    bytes), so no copy is allocated on this path.
    """
    if "id" in body:
        body["id"] = _rebased_id(body["id"], req_id)
    # Chat uses ``created`` (epoch seconds), Responses ``created_at``; both
    # describe when the answered response was produced, which is now.
    now = int(time.time())
    if "created" in body:
        body["created"] = now
    if "created_at" in body:
        body["created_at"] = now
    return body
