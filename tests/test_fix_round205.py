"""Round 205 — AUDIT #597: a per-key quota 429 ended the deployment.

`execute_with_retries` put the deployment into `tried_dep_ids` on its first
failure, whatever the failure was. Since AUDIT #469 a `429` that states a
`Retry-After` cools only the *credential*; the deployment is still healthy and
its second key is untouched — but the loop had already excluded it, so `fresh`
was `False` and the request ended with the `429` from the first key. With
`num_retries: 0` (one attempt) the healthy sibling key was never contacted at
all.

The tests drive the real app through `run_chat_like`: two upstream keys where
the first is quota-limited and the second works, one deployment, no fallbacks.
"""

import httpx
import respx
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from wiwi.config import load_config_from_string
from wiwi.server.app import create_app

URL = "https://up.test/v1/chat/completions"
MASTER = "sk-wiwi-master-test"
OK_BODY = {
    "id": "cmpl-1",
    "object": "chat.completion",
    "created": 1,
    "model": "gpt",
    "choices": [{"index": 0,
                 "message": {"role": "assistant", "content": "ok"},
                 "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


def _config(num_retries: int) -> str:
    return f"""
general_settings:
  master_key: {MASTER}
  database_url: "sqlite+aiosqlite:///:memory:"
providers:
  - name: p
    provider: openai
    base_url: https://up.test/v1
    keys: [{{label: k1, key: sk-a}}, {{label: k2, key: sk-b}}]
router_settings:
  num_retries: {num_retries}
model_list:
  - model_name: m
    wiwi_params: {{provider: p, model: gpt}}
"""


async def _chat(c, content: str) -> httpx.Response:
    return await c.post("/v1/chat/completions",
                        json={"model": "m",
                              "messages": [{"role": "user", "content": content}]})


async def _with_client(num_retries: int, upstream, body):
    """Run `body(client)` against a real app whose upstream is `upstream`."""
    @respx.mock
    async def _run():
        respx.post(URL).mock(side_effect=upstream)
        app = create_app(load_config_from_string(_config(num_retries)))
        async with LifespanManager(app), AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://t",
                headers={"Authorization": f"Bearer {MASTER}"}) as c:
            return await body(c)

    return await _run()


async def test_quota_429_fails_over_to_a_sibling_key_without_retries():
    """`num_retries: 0` must still use the second key of the same deployment.

    The first key is over quota (`429` + `Retry-After`), which cools the key
    only. Reporting that 429 to the caller while a healthy key of the same
    deployment sits idle is the #597 defect.
    """
    hits: list[str] = []

    def _upstream(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        hits.append(auth)
        if auth == "Bearer sk-b":
            return httpx.Response(200, json=OK_BODY)
        return httpx.Response(429, headers={"retry-after": "5"},
                              json={"error": {"message": "quota exhausted"}})

    status = await _with_client(0, _upstream, lambda c: _chat(c, "hi"))
    assert status.status_code == 200, hits
    assert hits == ["Bearer sk-a", "Bearer sk-b"]


async def test_quota_429_fails_over_with_retries_configured():
    """The same failover must not depend on retries being turned on."""
    hits: list[str] = []

    def _upstream(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        hits.append(auth)
        if auth == "Bearer sk-b":
            return httpx.Response(200, json=OK_BODY)
        return httpx.Response(429, headers={"retry-after": "30"},
                              json={"error": {"message": "quota exhausted"}})

    r = await _with_client(2, _upstream, lambda c: _chat(c, "hi"))
    assert r.status_code == 200, hits
    assert hits[:2] == ["Bearer sk-a", "Bearer sk-b"]


async def test_a_second_request_starts_on_the_cooled_key_pool():
    """The failover must not be bought by spending the deployment.

    After the first request only `k1` is cooling — the deployment is still
    available — so a following request is served instead of answering the #469
    "no healthy deployment" 503.
    """

    def _upstream(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") == "Bearer sk-a":
            return httpx.Response(429, headers={"retry-after": "5"},
                                  json={"error": {"message": "quota exhausted"}})
        return httpx.Response(200, json=OK_BODY)

    async def _both(c):
        first = await _chat(c, "a")
        second = await _chat(c, "b")
        return first.status_code, second.status_code

    assert await _with_client(0, _upstream, _both) == (200, 200)


async def test_bare_429_still_cools_the_deployment():
    """Fix #597 must not narrow AUDIT #469's rule back down to "always retry".

    A `429` with no `Retry-After` is an availability signal about the
    *deployment*: the key is deliberately left in rotation, so the retry loop is
    the only thing keeping the next request away from a model that just said
    "at capacity". The second request must fail rather than hit the same
    deployment again (no fallback group exists in this fixture).
    """

    def _upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "at capacity"}})

    async def _both(c):
        first = await _chat(c, "a")
        second = await _chat(c, "b")
        return first.status_code, second.status_code

    assert await _with_client(0, _upstream, _both) == (429, 429)
