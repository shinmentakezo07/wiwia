"""Explicit, observational state machines for the gateway's implicit lifecycles.

The code already encodes several state machines as scattered flags (a key's
``status``, a deployment's ``cooldown_until``, a budget reservation, an OAuth
pending session, a stored response). This module declares those states and
transitions once, so they can be validated and inspected without changing any
serving behaviour:

* ``fire`` records a transition and logs an illegal event — it never raises and
  never mutates the authoritative fields.
* ``explain`` returns the current state plus the recent transition history.
* ``snapshot`` exposes every machine's tracked instances for diagnostics.

Only low-frequency authorities are tracked per instance here (currently the
response store). The rest are declared for validation and derived-State
inspection; see ``docs/STATE_MACHINES.md`` for the inventory and the reason each
one is (or is not yet) wired into its hot path.
"""

from __future__ import annotations

import contextlib
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from threading import Lock
from typing import Any

import structlog

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Transition:
    src: str
    event: str
    dst: str


class StateMachine:
    """A declarative, observational finite state machine.

    Transitions are keyed by ``(source_state, event)``. An unknown pair is
    recorded as illegal and leaves the state unchanged; callers can treat the
    machine as a validator/observer and ignore the return value.
    """

    def __init__(self, name: str, states: Iterable[str], initial: str,
                 transitions: Iterable[Transition], *, history: int = 8,
                 max_instances: int = 4096) -> None:
        self.name = name
        self.states = frozenset(states)
        self.initial = initial
        self.transitions = tuple(transitions)
        self.max_instances = max(0, max_instances)
        self._by_event = {(t.src, t.event): t.dst for t in self.transitions}
        self._states: dict[str, str] = {}
        self._history: dict[str, deque[tuple[str, str, str]]] = {}
        self._lock = Lock()
        self.illegal = 0

    def state(self, instance: str = "default") -> str:
        with self._lock:
            return self._states.get(instance, self.initial)

    def fire(self, event: str, instance: str = "default", **fields: Any) -> str:
        with self._lock:
            current = self._states.get(instance, self.initial)
            nxt = self._by_event.get((current, event))
            if nxt is None:
                self.illegal += 1
                illegal = True
            else:
                illegal = False
                if instance not in self._states and len(self._states) >= self.max_instances:
                    self._states.pop(next(iter(self._states)), None)
                    self._history.pop(next(iter(self._history), ""), None)
                self._states[instance] = nxt
                self._history.setdefault(instance, deque(maxlen=8)).append(
                    (current, event, nxt))
        payload = {
            "machine": self.name, "instance": instance, "transition": event,
            "state": current, "to": nxt, **fields,
        }
        with contextlib.suppress(Exception):
            if illegal:
                log.warning("state_machine.illegal", **payload)
            else:
                log.debug("state_machine.transition", **payload)
        return current if illegal else nxt

    def explain(self, instance: str = "default") -> dict[str, Any]:
        with self._lock:
            return {
                "machine": self.name,
                "instance": instance,
                "state": self._states.get(instance, self.initial),
                "history": [
                    {"from": a, "event": b, "to": c}
                    for a, b, c in self._history.get(instance, ())
                ],
                "states": sorted(self.states),
                "transitions": [
                    {"from": t.src, "event": t.event, "to": t.dst}
                    for t in self.transitions
                ],
                "illegal": self.illegal,
            }

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return dict(self._states)


# -- declarations -----------------------------------------------------------

DEPLOYMENT = StateMachine(
    "deployment",
    ["absent", "ready", "degraded", "cooling", "probation", "retired"],
    "absent",
    [
        Transition("absent", "add", "ready"),
        Transition("ready", "fail", "degraded"),
        Transition("degraded", "fail", "cooling"),
        Transition("degraded", "success", "ready"),
        Transition("cooling", "expire", "probation"),
        Transition("cooling", "success", "ready"),
        Transition("probation", "success", "ready"),
        Transition("probation", "fail", "cooling"),
        Transition("ready", "retire", "retired"),
        Transition("degraded", "retire", "retired"),
        Transition("cooling", "retire", "retired"),
        Transition("probation", "retire", "retired"),
    ],
)

# Key lifecycle already has an explicit status field and diagram in
# wiwi/router/router.py; this declaration is the same graph.
KEY = StateMachine(
    "key",
    ["active", "cooling", "invalid", "disabled", "probation"],
    "active",
    [
        Transition("active", "cool", "cooling"),
        Transition("active", "invalidate", "invalid"),
        Transition("active", "disable", "disabled"),
        Transition("cooling", "recover", "active"),
        Transition("cooling", "invalidate", "invalid"),
        Transition("invalid", "recover", "probation"),
        Transition("probation", "success", "active"),
        Transition("probation", "fail", "cooling"),
        Transition("disabled", "enable", "active"),
    ],
)

BUDGET = StateMachine(
    "budget",
    ["idle", "reserved", "settled", "released", "refused"],
    "idle",
    [
        Transition("idle", "reserve", "reserved"),
        Transition("idle", "refuse", "refused"),
        Transition("reserved", "settle", "settled"),
        Transition("reserved", "release", "released"),
        Transition("reserved", "stale", "released"),
        Transition("settled", "reserve", "reserved"),
        Transition("released", "reserve", "reserved"),
        Transition("refused", "reserve", "reserved"),
    ],
)

OAUTH = StateMachine(
    "oauth",
    ["idle", "pending", "connected", "error", "expired"],
    "idle",
    [
        Transition("idle", "start", "pending"),
        Transition("pending", "callback_ok", "connected"),
        Transition("pending", "callback_error", "error"),
        Transition("pending", "expire", "expired"),
        Transition("connected", "disconnect", "idle"),
        Transition("error", "start", "pending"),
        Transition("expired", "start", "pending"),
    ],
)

RESPONSE = StateMachine(
    "response",
    ["absent", "stored", "expired", "deleted"],
    "absent",
    [
        Transition("absent", "put", "stored"),
        Transition("stored", "put", "stored"),
        Transition("stored", "expire", "expired"),
        Transition("expired", "expire", "expired"),
        Transition("stored", "delete", "deleted"),
        Transition("expired", "delete", "deleted"),
        Transition("deleted", "put", "stored"),
    ],
    max_instances=4096,
)

JOURNAL = StateMachine(
    "journal",
    ["absent", "recording", "complete", "abandoned"],
    "absent",
    [
        Transition("absent", "begin", "recording"),
        Transition("recording", "complete", "complete"),
        Transition("recording", "abort", "abandoned"),
        Transition("complete", "replay", "complete"),
        Transition("abandoned", "replay", "abandoned"),
        Transition("complete", "begin", "recording"),
    ],
)

HEALER = StateMachine(
    "healer_probe",
    ["idle", "probing", "ready", "backing_off"],
    "idle",
    [
        Transition("idle", "probe", "probing"),
        Transition("probing", "ok", "ready"),
        Transition("probing", "fail", "backing_off"),
        Transition("backing_off", "tick", "probing"),
        Transition("ready", "fail", "backing_off"),
    ],
)

SESSION = StateMachine(
    "session",
    ["anonymous", "authenticated", "expired"],
    "anonymous",
    [
        Transition("anonymous", "login", "authenticated"),
        Transition("authenticated", "logout", "anonymous"),
        Transition("authenticated", "expire", "expired"),
        Transition("expired", "login", "authenticated"),
    ],
)

CONFIG_OVERLAY = StateMachine(
    "config_overlay",
    ["absent", "loaded"],
    "absent",
    [
        Transition("absent", "load", "loaded"),
        Transition("loaded", "update", "loaded"),
        Transition("loaded", "clear", "absent"),
    ],
)

MACHINES: dict[str, StateMachine] = {
    m.name: m for m in (
        DEPLOYMENT, KEY, BUDGET, OAUTH, RESPONSE, JOURNAL, HEALER,
        SESSION, CONFIG_OVERLAY,
    )
}


def snapshot_all() -> dict[str, dict[str, Any]]:
    return {
        name: {"current": m.snapshot(), "illegal": m.illegal}
        for name, m in MACHINES.items()
    }


# -- derived states (no tracking, safe for hot paths) -----------------------

def deployment_state(dep: Any, now: float | None = None) -> str:
    """Derive a deployment's lifecycle state from its existing fields."""
    import time

    now = time.time() if now is None else now
    if getattr(dep, "cooldown_until", 0.0) > now:
        return "cooling"
    if getattr(dep, "probation", False):
        return "probation"
    fails = getattr(dep, "fails", ())
    if fails and float(fails[-1]) > now - 60.0:
        return "degraded"
    return "ready"


def budget_state(info: Any) -> str:
    """Derive a key's reservation state from an AuthInfo snapshot."""
    if getattr(info, "disabled", False):
        return "disabled"
    if getattr(info, "budget_reserved", 0.0) > 0:
        return "reserved"
    max_budget = getattr(info, "max_budget", None)
    if (max_budget is not None
            and getattr(info, "spend_to_date", 0.0) >= max_budget):
        return "exhausted"
    return "idle"


def oauth_state(session: dict[str, Any] | None, now: float | None = None) -> str:
    """Derive a pending OAuth session's state (pending/expired/absent)."""
    import time

    if not session:
        return "absent"
    now = time.time() if now is None else now
    created = float(session.get("created_at", now))
    ttl = float(session.get("ttl_s", 600.0))
    return "pending" if now - created <= ttl else "expired"


def response_state(created_at: float, ttl_s: float,
                   now: float | None = None) -> str:
    """Derive a stored response's readability (stored/expired)."""
    import time

    now = time.time() if now is None else now
    if ttl_s > 0 and now - created_at > ttl_s:
        return "expired"
    return "stored"
