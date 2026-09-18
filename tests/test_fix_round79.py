"""Round 79: streaming-internals fixes (#191, #192, #193).

Three caller-reachable defects, all in the streaming package:

1. **#191 — journal paths are non-injective.** ``JournalStore.path_for``
   sanitized by *stripping* disallowed characters, so ``'a/b'``, ``'a.b'``,
   ``'a b'`` and ``'a!b'`` all resolved to ``ab.jsonl`` — and appending a
   single ``.`` to a request id (which every client receives in the
   ``x-wiwi-request-id`` header) resolved to the *victim's* exact journal
   file. ``owner_of`` then read the victim's owner record and the #67
   per-key gate was satisfied by varying only the stripped characters.
   ``path_for`` is now injective: a conforming id (``[A-Za-z0-9_-]{1,64}``,
   which is what ``RequestContext`` generates) keeps its historical
   ``<id>.jsonl`` name, anything else is hashed to ``h<sha256>.jsonl``.

2. **#192 — ``validate_tool_args`` raised on a malformed nested schema.** The
   top-level dict guard existed; the nested keyword reads did not.
   ``schema.get("required")`` was not iterable for a non-list and
   ``properties.get`` raised for a non-dict, and the exception escaped into
   the pump's mid-stream handler — killing the caller's own stream and
   cooling the deployment's key for every other user. Both keywords are now
   coerced at the seam.

3. **#193 — ``_repair_truncated_json`` emitted invalid JSON on an even-length
   backslash run before a ``\\uXXXX``-shaped tail.** The surrogate-strip
   block ran unconditionally, so a *literal* ``\\uD83D`` (an escaped
   backslash followed by the text ``uD83D``) was stripped as though it were
   a real escape, leaving a dangling backslash and losing the whole
   argument object. The block is now gated on the same odd-run parity test
   the partial-escape branch already used.
"""

from __future__ import annotations

import json

import orjson
import pytest

from wiwi.streaming.partial_json import _repair_truncated_json, parse_partial
from wiwi.streaming.tape_store import JournalStore
from wiwi.streaming.validation import validate_tool_args

VICTIM = "0d76f7149cb048dc"  # a real 16-hex request id (core/context.py)


@pytest.fixture
def store(tmp_path):
    return JournalStore(tmp_path / "js", ttl_s=600, max_bytes=1 << 20)


# ---------------------------------------------------------------------------
# #191 — the journal path mapping must be injective
# ---------------------------------------------------------------------------


def test_path_for_is_injective_over_the_collision_set(store):
    """Every id in the register's collision set used to map onto ``ab.jsonl``.

    Distinct ids must map to distinct files, or the #67 owner gate is only as
    strong as the path lookup.
    """
    ids = ["a/b", "a.b", "a b", "a!b", "ab"]
    paths = [store.path_for(i) for i in ids]
    assert len(set(paths)) == len(ids), (
        f"journal paths collide: {[(i, p.name) for i, p in zip(ids, paths)]}"
    )


def test_crafted_id_does_not_alias_the_victims_journal(store):
    """Appending a stripped character to a known request id must not resolve
    to that id's file — the header is public, so this was a cross-key read."""
    assert store.path_for(VICTIM + ".") != store.path_for(VICTIM)
    assert store.path_for(VICTIM + "!") != store.path_for(VICTIM)
    assert store.path_for(VICTIM + " ") != store.path_for(VICTIM)
    # The conforming id keeps its historical file name (existing on-disk
    # journals stay replayable across the upgrade).
    assert store.path_for(VICTIM).name == f"{VICTIM}.jsonl"


def test_path_for_still_rejects_traversal(store):
    """The sanitizer's original purpose — no escaping the journal dir."""
    for evil in ("../evil/../id", "..", "/etc/passwd", "../../x"):
        p = store.path_for(evil)
        assert p.parent == store.dir
        assert "/" not in p.name and ".." not in p.name


async def test_crafted_id_reads_no_owner_and_no_chunks(store):
    """End to end on the #67 gate: a journal owned by key A, addressed by the
    crafted variant of its id, must yield neither the owner nor any chunk."""
    j = await store.open(VICTIM, key_id="kid-A")
    await j.append(1, b"data: secret\n\n")
    await j.finish(1)

    crafted = VICTIM + "."
    assert store.owner_of(VICTIM) == "kid-A"          # control
    assert store.owner_of(crafted) is None, "crafted id read the victim's owner"
    assert store.read_after(crafted, 0) == [], "crafted id read the victim's chunks"
    assert store.is_complete(crafted) is False


async def test_consumers_behave_for_a_valid_id(store):
    """owner_of / is_active / read_after / is_complete keep working for the
    legitimate 16-hex id the server generates and returns to clients."""
    j = await store.open(VICTIM, key_id="kid-A")
    assert store.is_active(VICTIM) is True
    assert store.owner_of(VICTIM) == "kid-A"
    await j.append(1, b"data: a\n\n")
    await j.append(2, b"data: b\n\n")
    assert [s for s, _ in store.read_after(VICTIM, 0)] == [1, 2]
    assert [s for s, _ in store.read_after(VICTIM, 1)] == [2]
    assert store.is_complete(VICTIM) is False
    await j.finish(2)
    assert store.is_complete(VICTIM) is True
    store.release(VICTIM)
    assert store.is_active(VICTIM) is False
    # Ownership survives the release — it lives in the file, not memory.
    assert store.owner_of(VICTIM) == "kid-A"


async def test_crafted_id_cannot_append_into_the_victims_journal(store):
    """The other half of the alias: opening the crafted id used to resolve to
    the victim's file, so the attacker's chunks were appended into it (and
    ``release``/``finish`` operated on the victim's stream)."""
    j = await store.open(VICTIM, key_id="kid-A")
    await j.append(1, b"data: victim\n\n")
    await j.aclose()
    victim_file = store.path_for(VICTIM)
    before = victim_file.read_bytes()

    crafted = VICTIM + "."
    cj = await store.open(crafted, key_id="kid-B")
    await cj.append(1, b"data: attacker\n\n")
    await cj.aclose()

    assert store.path_for(crafted) != victim_file
    assert victim_file.read_bytes() == before, (
        "a crafted id appended into the victim's journal"
    )
    # The victim's records are still exactly its own.
    assert [c for _, c in store.read_after(VICTIM, 0)] == [b"data: victim\n\n"]


# ---------------------------------------------------------------------------
# #192 — a malformed nested schema is skipped, never raised on
# ---------------------------------------------------------------------------

# Shapes that carry no validation semantics at all: pre-fix each one raised
# out of ``validate_tool_args`` (a non-list ``required`` is not iterable, a
# non-dict ``properties`` has no ``.get``, an unhashable ``required`` member
# blows up the ``in`` lookup). All must be skipped, exactly like the
# top-level non-dict schema the H4 guard already covers.
MALFORMED_SCHEMAS = [
    {"type": "object", "properties": ["a"]},       # list of property objects
    {"type": "object", "properties": "nope"},      # str
    {"type": "object", "properties": 7},           # int
    {"type": "object", "required": None},          # null
    {"type": "object", "required": 3},             # int
    {"type": "object", "required": [{"a": 1}]},    # unhashable member
    {"type": "object", "required": [["a"]]},       # unhashable member
]


@pytest.mark.parametrize("schema", MALFORMED_SCHEMAS,
                         ids=[repr(s) for s in MALFORMED_SCHEMAS])
def test_malformed_nested_schema_is_skipped_not_raised(schema):
    """Pre-fix: AttributeError/TypeError escaped into the pump's mid-stream
    handler — the caller's stream died and the deployment's key was cooled."""
    assert validate_tool_args("t", '{"x": 1}', schema) == (True, "")


@pytest.mark.parametrize("schema", [
    {"type": "object", "required": [None], "properties": ["a"]},
    {"type": "object", "required": [None, {"a": 1}], "properties": ["a"]},
    {"type": "object", "required": [{"a": 1}, "y"]},
    {"type": "object", "required": 3.5, "properties": "x"},
    {"type": "object", "required": {"a": 1}},
])
def test_malformed_nested_schema_never_raises(schema):
    """A hashable junk member still takes the ordinary reject path (its
    historical behaviour) — the contract is that nothing raises, not that
    every malformed shape validates clean."""
    result = validate_tool_args("t", '{"x": 1}', schema)
    assert isinstance(result, tuple) and len(result) == 2
    ok, msg = result
    assert isinstance(ok, bool) and isinstance(msg, str)


def test_malformed_nested_schema_does_not_suppress_real_violations():
    """Control: coercion must not turn validation into a no-op. A well-formed
    schema still rejects the same payload."""
    schema = {"type": "object", "required": ["y"], "properties": {"y": {"type": "string"}}}
    ok, msg = validate_tool_args("t", '{"x": 1}', schema)
    assert ok is False and "y" in msg
    ok, _ = validate_tool_args("t", '{"y": 1}', schema)
    assert ok is False  # property type still enforced
    assert validate_tool_args("t", '{"y": "s"}', schema) == (True, "")


# ---------------------------------------------------------------------------
# #193 — repair must stay parseable for every trailing backslash-run parity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n", range(1, 7))
def test_repair_of_backslash_run_before_a_surrogate_shaped_tail(n):
    """n backslashes then ``uD83D``, cut mid-string.

    An even run is a *literal* ``\\uD83D``; pre-fix the surrogate block
    stripped it and left a dangling backslash, so the repaired text was not
    JSON at all and the whole argument object was lost (``parse_partial`` ->
    ``({}, False)``). An odd run really does open the escape, so the dangling
    high surrogate is dropped (the #139 guarantee) while the complete escaped
    pairs before it survive as literal backslashes.
    """
    text = '{"a": "' + "\\" * n + "uD83D"
    repaired = _repair_truncated_json(text)
    parsed = json.loads(repaired)  # pre-fix: JSONDecodeError for n = 2, 4, 6
    pairs = "\\" * (n // 2)
    if n % 2:
        expected = pairs          # the fresh backslash + escape are dropped
    else:
        expected = pairs + "uD83D"  # the tail is literal text, not an escape
    assert parsed == {"a": expected}, (
        f"n={n}: repaired {repaired!r} decoded to {parsed!r}, want {expected!r}"
    )


@pytest.mark.parametrize("n", range(1, 7))
def test_repair_of_backslash_run_before_a_low_surrogate_tail(n):
    """Same parity rule for ``uDE00`` (the low half)."""
    text = '{"a": "' + "\\" * n + "uDE00"
    parsed = json.loads(_repair_truncated_json(text))
    pairs = "\\" * (n // 2)
    assert parsed == {"a": pairs if n % 2 else pairs + "uDE00"}


def test_parse_partial_keeps_the_object_for_an_even_run():
    """The public entry point degraded to ``{}`` — the args silently vanished."""
    value, complete = parse_partial('{"a": "' + "\\" * 2 + "uD83D")
    assert value == {"a": "\\uD83D"}, "the whole argument object was lost"
    assert complete is False


def test_real_surrogate_escape_is_still_stripped():
    """Control: an odd run really does open an escape, so a dangling high
    surrogate must still be dropped (the #139 guarantee is unchanged)."""
    assert json.loads(_repair_truncated_json('{"a": "\\uD83D')) == {"a": ""}
    assert json.loads(_repair_truncated_json('{"a": "\\uDE00')) == {"a": ""}
    # Complete pair survives untouched.
    assert json.loads(_repair_truncated_json('{"a": "\\uD83D\\uDE00')) == {
        "a": "\U0001f600"}


def test_literal_surrogate_text_is_not_stripped_when_escaped():
    """An even run followed by a *real* low-surrogate escape: the predecessor
    is literal text, not the pair's high half, so the lone low surrogate must
    still be dropped rather than kept as an unencodable code point."""
    text = '{"a": "' + "\\" * 4 + "uD83D" + "\\" + "uDE00"
    parsed = json.loads(_repair_truncated_json(text))
    for value in parsed.values():
        for ch in value:
            assert not (0xD800 <= ord(ch) <= 0xDFFF), (
                f"surrogate U+{ord(ch):04X} survived the repair"
            )
    orjson.dumps(parsed)  # must be encodable (pre-fix: TypeError)
