"""Normalized-IR hashing for the exact-match response cache (docs/CORE.md §6).

Two requests that decode to the same canonical IR must hit the same cache
entry regardless of inbound dialect — so the key is SHA-256 over a normalized
JSON projection of the IR request plus the routing context that changes the
answer (model group, surface, auth key). Auth key scoping keeps one virtual
key's cached responses from being served to another.
"""

from __future__ import annotations

import hashlib
from dataclasses import is_dataclass
from typing import Any

import orjson

from wiwi.ir import types as ir


def _encode(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        d: dict[str, Any] = {"__t": type(obj).__name__}
        for f in obj.__dataclass_fields__:
            d[f] = _encode(getattr(obj, f))
        return d
    if isinstance(obj, (list, tuple)):
        return [_encode(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _encode(v) for k, v in sorted(obj.items())}
    return obj


def response_cache_key(ir_req: ir.Request, group: str, surface: str,
                       key_id: str) -> str:
    """SHA-256 hex digest over the normalized IR request + routing scope."""
    payload = {
        "group": group,
        "surface": surface,
        "key_id": key_id,
        "model": ir_req.model,
        "messages": _encode(ir_req.messages),
        "tools": _encode(ir_req.tools),
        "tool_choice": _encode(ir_req.tool_choice),
        "gen_params": _encode(ir_req.gen_params),
        "stream": False,
    }
    blob = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
    return hashlib.sha256(blob).hexdigest()
