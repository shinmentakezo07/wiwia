"""Pydantic v2 request/response schemas for the admin API.

The admin routes used to read their bodies with ``body.get(...)`` and hand
validate each field by hand — which is why there were 35 separate
``json_body`` call sites and 47 hand-rolled 400 branches. A Pydantic model
replaces the field-shape checks with one declarative schema, and the
``field_validator`` hooks carry the *wiwi-specific* rules (the ones the
generic type system cannot express).

**The status code is 400, not 422.** FastAPI's default validation-error
handler answers 422 with ``{"detail": [...]}``, which is neither of wiwi's
dialects and is not a status wiwi has ever returned (``docs/API_REFERENCE.md``
picks 400 for a malformed request). The app installs a
``RequestValidationError`` handler that re-renders validation failures as a
400 in the caller's dialect — see ``server/app.py``.

**Unknown fields are ignored, not rejected.** The admin API is a
public-ish surface whose clients (the SPA, the docs' curl examples, operator
scripts) send more than the schema names. Rejecting extras would turn a
harmless extra key into a 400 on a route that used to accept it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from wiwi.auth.service import AuthService


class AdminModel(BaseModel):
    """Base for the admin request models.

    ``extra="ignore"`` is deliberate: see the module docstring.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=False)


class KeyGenerateRequest(AdminModel):
    """Body of ``POST /admin/keys/generate``.

    Every field is optional except that the caller usually supplies a name.
    The *types* are deliberately loose (``Any``) for the numeric limits: the
    authoritative validation is ``AuthService._coerce_limit`` (which owns the
    "0 is not a cap" rule and the bool rejection), and duplicating it here
    would create a second convention that can drift from the first.

    The model exists to reject the shapes that are wrong *structurally* —
    ``models`` not being a list of strings, ``priority`` not being a string —
    before the service layer runs.
    """

    name: str | None = None
    alias: str | None = None
    custom_key: str | None = None
    models: list[str] | None = None
    max_budget: Any = None
    rpm: Any = None
    tpm: Any = None
    ttl_seconds: Any = None
    priority: Any = None

    @field_validator("models", mode="before")
    @classmethod
    def _models_are_strings(cls, value: Any) -> Any:
        """``models`` must be a list of strings.

        A bare string used to be iterated per character by
        ``AuthService._coerce_models`` (``"abc"`` became ``["a","b","c"]``), so
        the shape is pinned here where the caller can see a 400.

        ``ValueError``, not ``TypeError``: Pydantic v2 only converts
        ``ValueError``/``AssertionError`` into a validation error. A
        ``TypeError`` raised here escapes the validator and surfaces as an
        unhandled 500 instead of the 400 the caller should see.
        """
        if value is None:
            return None
        if not isinstance(value, list) or not all(
                isinstance(item, str) for item in value):
            raise ValueError("'models' must be a list of strings")
        return value

    @field_validator("priority", mode="before")
    @classmethod
    def _priority_is_a_string(cls, value: Any) -> Any:
        """``priority`` names a lane, so it must be a string.

        A number would become a lane named ``"3"`` that matches no configured
        lane and silently degrades to ``default_lane``.

        ``ValueError``, not ``TypeError`` — see the note above.
        """
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("'priority' must be a string")  # noqa: TRY004
        return value.strip() or None


class KeyPatchRequest(AdminModel):
    """Body of ``PATCH /admin/keys/{key_id}``.

    ``extra="ignore"`` matters most here: the route projects the body onto
    ``AuthService.UPDATABLE_FIELDS`` and ignores everything else, so a client
    sending a stray field is not an error. The model keeps the *known* fields
    typed and lets the rest through.
    """

    max_budget: Any = None
    rpm: Any = None
    tpm: Any = None
    models: list[str] | None = None
    expires_at: Any = None
    ttl_seconds: Any = None
    priority: Any = None

    @field_validator("models", mode="before")
    @classmethod
    def _models_are_strings(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, list) or not all(
                isinstance(item, str) for item in value):
            raise ValueError("'models' must be a list of strings")
        return value

    @field_validator("priority", mode="before")
    @classmethod
    def _priority_is_a_string(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("'priority' must be a string")  # noqa: TRY004
        return value.strip() or None


class KeyDisableRequest(AdminModel):
    """Body of ``POST /admin/keys/{key_id}/disable``.

    ``disabled`` defaults to True (the route's historical default: a disable
    call with no body disables). It must be a real ``bool`` — ``bool("false")``
    is True in Python, so a caller asking to *enable* a key with the string
    ``"false"`` would silently revoke it instead (AUDIT #82).
    """

    disabled: bool = True

    @field_validator("disabled", mode="before")
    @classmethod
    def _disabled_is_a_bool(cls, value: Any) -> Any:
        if not isinstance(value, bool):
            raise ValueError("disabled must be a boolean")  # noqa: TRY004
        return value


class VirtualKeyResponse(BaseModel):
    """A virtual key as the admin API returns it.

    Mirrors ``AuthService.list_keys``' projection.

    ``owner_id`` is a genuinely *absent* field on the owner-scoped listing
    (``list_keys_for_owner`` omits it — the WHERE clause already fixed it),
    while it is present on the admin listing. The route is mounted with
    ``response_model_exclude_unset=True`` so an absent field stays absent:
    without it the model would ADD ``"owner_id": null`` to the owner-scoped
    response, which the old inline handler never did.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    alias: str
    models: list[str] = Field(default_factory=list)
    max_budget: float | None = None
    spend_to_date: float = 0.0
    rpm: float | None = None
    tpm: float | None = None
    expires_at: float | None = None
    disabled: bool = False
    owner_id: str | None = None
    budget_reserved: float = 0.0
    priority: str | None = None


class KeyListResponse(BaseModel):
    """Body of ``GET /admin/keys``."""

    keys: list[VirtualKeyResponse]


class KeyGenerateResponse(BaseModel):
    """Body of ``POST /admin/keys/generate``.

    The plaintext is returned here and NOWHERE else — it is stored hashed.
    """

    key: str
    id: str
    note: str = "store this key now; it is not shown again"


class KeyPatchResponse(BaseModel):
    """Body of ``PATCH /admin/keys/{key_id}``."""

    key: VirtualKeyResponse


class KeyDeleteResponse(BaseModel):
    """Body of ``DELETE /admin/keys/{key_id}``."""

    deleted: bool


class KeyDisableResponse(BaseModel):
    """Body of ``POST /admin/keys/{key_id}/disable``."""

    key_id: str
    disabled: bool


#: The fields ``PATCH /admin/keys/{key_id}`` projects onto. Re-exported from
#: the owning module rather than restated here, so the two cannot drift.
UPDATABLE_FIELDS: tuple[str, ...] = AuthService.UPDATABLE_FIELDS
