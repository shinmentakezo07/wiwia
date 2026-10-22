"""Request/response schemas for wiwi's HTTP API.

Kept in one package so an importing module has a single place to look for
"what does this route accept and return". Each module owns one surface; the
schemas are Pydantic v2 models (the project already depends on Pydantic v2
for its config models — see ``wiwi/config.py``).
"""
