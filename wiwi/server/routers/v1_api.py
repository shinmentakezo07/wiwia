"""The inbound /v1 surfaces: chat, responses, messages, legacy completions.

Extracted verbatim from ``server/app.py`` (round 206). Every route is a thin
wrapper over ``run_chat_like`` — the shared execution pipeline that owns
admission, routing, retries, budgets, logging and billing; the only thing
that differs per route is the wire codec. The Playground variant admits the
caller by session cookie instead of a bearer key and injects the account's
playground virtual key server-side (policy in
``wiwi/server/services/playground.py``).

The handlers close over per-app callables handed in by ``create_app``
(``authenticate``, ``run_chat_like``, ``json_body``, ``_err``), so the
router is built once per app; the Playground variant's session-cookie
admission is the module-level ``CurrentUser`` dependency from
``wiwi.server.dependencies``.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from wiwi.core.context import RequestContext
from wiwi.core.gateway import estimate_request_tokens
from wiwi.server.dependencies import CurrentUser, ORJSONResponse
from wiwi.server.services.playground import playground_bearer
from wiwi.wire import anthropic_messages as am
from wiwi.wire import openai_chat as oc
from wiwi.wire import openai_completions as ocmpl
from wiwi.wire import openai_responses as orp


def make_router(state, *, authenticate, run_chat_like, json_body, _err):
    """Build the /v1 router bound to the per-app execution helpers."""
    api = APIRouter(prefix="/v1", tags=["v1"])

    @api.post("/chat/completions")
    async def chat_completions(request: Request):
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        return await run_chat_like(request, "chat", body, oc.decode_request,
                                   oc.encode_response)

    @api.post("/playground/completions")
    async def playground_completions(request: Request, actor: CurrentUser):
        """Playground chat completions — the /v1/chat/completions pipeline
        behind the browser session instead of a bearer key.

        Same decoder, same encoder, same ``run_chat_like`` execution — the only
        difference is admission: the ``wiwi_session`` cookie identifies the
        caller, and the user's playground virtual key is resolved server-side
        and injected as the bearer. That key is minted once on first use and
        reused for every later request, and the material never reaches the
        browser, so a key that expired or was revoked (the old "401 invalid API
        key" in a perfectly logged-in tab) is replaced transparently instead.
        """
        if state.auth is None:
            return _err(500, "api_error", "gateway not initialized", request)
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        try:
            pg_bearer = await playground_bearer(state, actor)
        except Exception as e:  # noqa: BLE001 — mint/DB failure is a 500, not a crash
            import structlog as _sl

            _sl.get_logger("wiwi.playground").error(
                "playground_bearer_unavailable", user_id=actor.id,
                error=type(e).__name__, detail=str(e))
            return _err(500, "api_error",
                        "could not prepare the playground key", request)
        return await run_chat_like(request, "chat", body, oc.decode_request,
                                   oc.encode_response, bearer_token=pg_bearer)

    @api.post("/responses")
    async def responses_api(request: Request):
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        return await run_chat_like(request, "responses", body, orp.decode_request,
                                   orp.encode_response,
                                   codec_history=orp.with_history,
                                   store_response=True)

    @api.get("/responses/{response_id}")
    async def responses_get(request: Request, response_id: str):
        """Replay a stored response object. Key-scoped: a foreign id is a 404."""
        st = state.response_store
        info, err_resp = await authenticate(request, "", "responses")
        if err_resp:
            return err_resp
        row = (await st.get(response_id, info.key_id)) if st is not None else None
        if row is None:
            return _err(404, "not_found_error", f"response '{response_id}' not found",
                        request, "responses")
        return ORJSONResponse(row.output)

    @api.delete("/responses/{response_id}")
    async def responses_delete(request: Request, response_id: str):
        st = state.response_store
        info, err_resp = await authenticate(request, "", "responses")
        if err_resp:
            return err_resp
        deleted = ((await st.delete(response_id, info.key_id)) if st is not None
                   else False)
        if not deleted:
            return _err(404, "not_found_error", f"response '{response_id}' not found",
                        request, "responses")
        return {"id": response_id, "object": "response.deleted", "deleted": True}

    @api.post("/completions")
    async def completions_api(request: Request):
        """Legacy OpenAI text completions: one prompt in, ``choices[].text`` out.

        Served through ``run_chat_like`` like every other surface, so auth,
        routing, retries, budgets, logging and billing are identical. The
        dialect has no tool protocol and no roles; the codec maps ``prompt``
        onto one user message and refuses the parameters the IR cannot carry.
        """
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        return await run_chat_like(request, "completions", body,
                                   ocmpl.decode_request, ocmpl.encode_response)

    @api.post("/messages")
    async def messages_api(request: Request):
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        return await run_chat_like(request, "messages", body, am.decode_request,
                                   am.encode_response)

    @api.post("/messages/count_tokens")
    async def count_tokens(request: Request):
        body, jerr = await json_body(request)
        if jerr:
            return jerr
        try:
            ir_req = am.decode_request(body)
        except (oc.DialectError, ValueError) as e:
            return _err(400, "invalid_request_error", str(e), request, "messages")
        _, err_resp = await authenticate(request, ir_req.model, "messages")
        if err_resp:
            return err_resp
        # Count through the same estimator the gateway's streaming fallback
        # uses, so the number reported to the client agrees with the number
        # wiwi itself bills against when upstream omits usage. Walking only
        # TextPart ignored tool schemas, tool_use/tool_result and thinking
        # blocks — the bulk of an agentic prompt — and the private
        # ``len(text) // 4 + 1`` heuristic disagreed with ``estimate_tokens``
        # (tiktoken where available) for identical text (AUDIT #130).
        # ``estimate_request_tokens`` also adds binary media, which a text
        # walk cannot represent: a 300 KB screenshot used to count 7 tokens
        # instead of ~1500, so Claude Code's auto-compact never fired and the
        # session died on an upstream context error (AUDIT #156).
        ctx = RequestContext(surface="messages", ir_req=ir_req)
        total = await estimate_request_tokens(ctx, ir_req.model)
        return ORJSONResponse({"input_tokens": max(1, total)})

    @api.get("/models")
    async def list_models(request: Request):
        _, err_resp = await authenticate(request, model="*")
        if err_resp:
            return err_resp
        data = []
        for name in sorted(state.router.groups.keys()):
            data.append({"id": name, "object": "model", "owned_by": "wiwi"})
        return ORJSONResponse({"object": "list", "data": data})

    return api
