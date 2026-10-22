"""Service layer: business logic behind the HTTP routes.

The service modules own the rules (authorization, validation, invariants) and
raise :class:`wiwi.server.errors.APIError`; the router modules own the HTTP
shape. Neither imports FastAPI's request/response machinery, so the rules can
be exercised without an HTTP client.
"""
