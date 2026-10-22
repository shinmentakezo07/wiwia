"""API errors with a dialect-correct body.

wiwi serves three inbound dialects (OpenAI Chat, OpenAI Responses, Anthropic
Messages) and each expects a different error envelope:

* OpenAI-shaped dialects want ``{"error": {"message", "type", "code"}}``.
* Anthropic's Messages API wants ``{"type": "error", "error": {...}}``.

Before this module every handler built its error body inline through the
``_err`` closure in ``server/app.py``, which meant the dialect knowledge and
the handler knowledge were entangled in one 7k-line function. A handler could
only report a failure by *returning* a response, because there was no
exception type to raise — so every early return had to remember to check for
``None`` before continuing.

:class:`APIError` gives handlers the other shape: raise it, and the
application-level exception handler renders it in the right dialect. The
status code, the error type and the message stay the same values the inline
``_err`` calls used, so this is a structural change with no contract change.

The rendering is deliberately NOT FastAPI's default ``HTTPException``
handler. FastAPI renders ``{"detail": ...}``, which is neither dialect, and
wiwi's clients (Claude Code, Codex CLI, the OpenAI SDKs) all match on the
dialect shape.
"""

from __future__ import annotations

from typing import Any

from starlette.exceptions import HTTPException


class APIError(HTTPException):
    """An error with a wiwi status + error type, rendered per dialect.

    Subclasses Starlette's ``HTTPException`` so the existing
    ``except HTTPException`` path in the app keeps working, but it carries
    the two extra fields wiwi's error contract needs:

    * ``etype`` — wiwi's error vocabulary (``invalid_request_error``,
      ``authentication_error``, …). The Anthropic renderer maps it onto
      Anthropic's own vocabulary; the OpenAI renderer passes it through.
    * ``surface`` — which dialect to answer in. ``"messages"`` means
      Anthropic; ``"responses"`` and ``"chat"`` are OpenAI-shaped.

    ``detail`` is kept in sync with ``message`` because Starlette's
    ``HTTPException`` and any middleware that inspects it read ``detail``.
    """

    def __init__(self, status: int, etype: str, message: str,
                 surface: str = "chat") -> None:
        super().__init__(status_code=status, detail=message)
        self.etype = etype
        self.message = message
        self.surface = surface


def error_body_for(surface: str):
    """The per-dialect error-body builder for *surface*.

    Returns the codec's own ``error_body`` function so the dialect stays the
    single source of truth for its envelope. Importing the codecs here (rather
    than re-deriving the shapes) is what keeps ``/v1/messages`` and
    ``/v1/chat/completions`` from drifting apart.

    ``"messages"`` is Anthropic; everything else is OpenAI-shaped. The
    Responses dialect shares the OpenAI envelope.

    The codec imports are function-local on purpose. ``errors.py`` is
    imported by ``dependencies.py``, which ``server/app.py`` imports early;
    a module-level codec import would make the whole wire layer load before
    the app factory does. The cost is a dict lookup per error rendered, which
    is nothing next to the request itself.
    """
    if surface == "messages":
        from wiwi.wire import anthropic_messages as am

        return am.error_body
    if surface == "responses":
        from wiwi.wire import openai_responses as orp

        return orp.error_body
    from wiwi.wire import openai_chat as oc

    return oc.error_body


def render(exc: APIError, request_id: str = "") -> dict[str, Any]:
    """Render *exc* into the body its dialect expects.

    The Anthropic renderer takes the request id (the real Messages API echoes
    it in the body) while the OpenAI-shaped ones carry it as a header only.
    """
    if exc.surface == "messages":
        return error_body_for("messages")(exc.status_code, exc.etype,
                                          exc.message, request_id=request_id)
    return error_body_for(exc.surface)(exc.status_code, exc.etype, exc.message)


def validation_message(exc: Any) -> str:
    """A one-line message for a FastAPI request-validation failure.

    FastAPI's ``RequestValidationError`` carries a list of per-field errors.
    The dialect body has a single ``message`` string, so the first error is
    the one reported — the rest are usually consequences of it (a missing
    field *and* its type error).

    **A validator's own message is used verbatim.** The request models raise
    ``ValueError("'models' must be a list of strings")`` — the exact string
    the hand-written handlers used to return — and Pydantic reports it as
    ``"Value error, 'models' must be a list of strings"``. The prefix comes
    off, so the client sees the same text it always did.

    **Pydantic's built-in errors get the field name.** Their messages are
    terse ("Input should be a valid integer"), and naming the field is what
    makes them actionable. A validator's message already names its own
    field, so prefixing it would only repeat it.
    """
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return "request validation failed"
    try:
        details = errors()
    except Exception:  # noqa: BLE001 - never let error reporting raise
        return "request validation failed"
    if not details:
        return "request validation failed"
    first = details[0]
    if not isinstance(first, dict):
        return "request validation failed"
    loc = first.get("loc") or ()
    # ``loc`` is (body, field, ...) for a body field and (query, name, …) for
    # a query parameter. Drop the leading "body"/"query" marker.
    parts = [str(p) for p in loc if p not in ("body", "query", "path")]
    field = ".".join(parts)
    msg = str(first.get("msg") or "invalid value")
    # Pydantic prefixes a validator's ValueError with "Value error, ".
    msg = msg.removeprefix("Value error, ")
    # A custom validator wrote a complete, user-facing message.
    if first.get("type") == "value_error":
        return msg
    return f"{field}: {msg}" if field else msg
