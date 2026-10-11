"""Round 126 — four follow-ups from the 2026-10-10 sweep (#392, #394, #395, #393).

Each finding was reproduced by executing code before being fixed. These tests
pin the fixed behaviour, and each carries a control proving the fix did not
disturb the path it sits beside.

#392 ``load_config`` folded every read failure into ``ConfigError``, so an
     unreadable path (a directory, a root-owned file read by the non-root
     container user) no longer escapes as a raw traceback past the CLI's
     ``except ConfigError``.

#394 ``_load_db_config`` re-validates a DB provider's ``provider_type`` against
     ``PROVIDER_TYPES``, so a row written by an older build or a foreign dump no
     longer reaches ``registry.fresh_adapter`` and raises a bare ``ValueError``
     past the router's ``WiwiError`` handler as an unhandled 500.

#395 ``JournalStore.forget_owner`` lets ``_drop_journal`` drop the ownership
     bookkeeping for a journal it has unlinked, instead of leaking one
     ``_owner_intent`` entry per streamed request for the process lifetime in the
     degraded-journal state.

#393 the HF deploy extract excludes ``.gitattributes``, so the Space's own LFS
     weight rules survive a deploy instead of being overwritten by the repo's
     copy — which is what the skip guard one block above already intended.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

import pytest

from wiwi.config import PROVIDER_TYPES, ConfigError, load_config
from wiwi.streaming.tape_store import JournalStore

# ---------------------------------------------------------------------------
# #392 — unreadable config paths must surface as ConfigError
# ---------------------------------------------------------------------------

async def _config_from(tmp_path, name: str) -> object:
    return load_config(str(tmp_path / name))

async def test_config_pointed_at_a_directory_is_a_clean_config_error(tmp_path):
    """``wiwi --config <a directory>`` must not raise IsADirectoryError.

    ``p.read_text()`` sat inside a ``try`` that caught only ``yaml.YAMLError``,
    so the directory's ``IsADirectoryError`` propagated past ``main.py``'s
    ``except ConfigError`` as a raw traceback (AUDIT #392).
    """
    d = tmp_path / "adir"
    d.mkdir()
    with pytest.raises(ConfigError) as exc:
        await _config_from(tmp_path, "adir")
    assert "cannot read config file" in str(exc.value)

@pytest.mark.skipif(
    os.geteuid() == 0,
    reason="root ignores file permissions, so the unreadable case cannot be built",
)
async def test_an_unreadable_config_file_is_a_clean_config_error(tmp_path):
    """A permission error must be reported, not dumped as a traceback.

    The real-world trigger: the container runs as the non-root ``wiwi`` user and
    a root-owned ``wiwi.yaml`` is bind-mounted over it.
    """
    p = tmp_path / "locked.yaml"
    p.write_text("providers: []\n")
    p.chmod(0)
    try:
        with pytest.raises(ConfigError) as exc:
            await _config_from(tmp_path, "locked.yaml")
        assert "cannot read config file" in str(exc.value)
    finally:
        # Restore so tmp_path cleanup can remove it.
        p.chmod(stat.S_IRUSR | stat.S_IWUSR)

async def test_a_missing_config_file_keeps_its_own_message(tmp_path):
    """Control: the missing-file case stays distinct and still names itself.

    It is the likelier cause, so it must not be folded into the generic
    cannot-read message the fix introduced.
    """
    with pytest.raises(ConfigError) as exc:
        await _config_from(tmp_path, "nope.yaml")
    assert "config file not found" in str(exc.value)

async def test_malformed_yaml_is_still_an_invalid_yaml_error(tmp_path):
    """Control: the YAMLError arm keeps its own, more specific message."""
    p = tmp_path / "bad.yaml"
    p.write_text("providers: [oops\n")
    with pytest.raises(ConfigError) as exc:
        await _config_from(tmp_path, "bad.yaml")
    assert "invalid YAML" in str(exc.value)

# ---------------------------------------------------------------------------
# #394 — provider_type re-validation
# ---------------------------------------------------------------------------

def test_provider_types_are_the_validation_source():
    """The registry and the config model must agree on the accepted set.

    The fix keys off ``PROVIDER_TYPES``, so the constant must stay the single
    source of truth ``CLAUDE.md`` claims it is.
    """
    from wiwi.providers import registry

    assert len(PROVIDER_TYPES) > 0
    assert all(isinstance(t, str) for t in PROVIDER_TYPES)
    # Every accepted type must resolve to an adapter — otherwise the fix would
    # admit a provider the gateway cannot actually call.
    for t in PROVIDER_TYPES:
        registry.fresh_adapter(t)

def test_an_unknown_provider_type_is_rejected_by_the_registry():
    """Control: the consequence the #394 fix avoids is still real.

    Confirms the finding was not theoretical — an unvalidated type reaching the
    gateway raises a bare ``ValueError``, which ``router.py`` re-raises past its
    ``WiwiError`` handler instead of degrading into a retryable failure.
    """
    from wiwi.providers import registry
    from wiwi.providers.base import WiwiError
    with pytest.raises(ValueError) as exc:
        registry.fresh_adapter("not-a-real-type")
    assert "unsupported provider type" in str(exc.value)
    assert not isinstance(exc.value, WiwiError)

# ---------------------------------------------------------------------------
# #395 — the dropped journal's intent is forgotten
# ---------------------------------------------------------------------------

async def test_forget_owner_drops_a_dropped_journals_intent(tmp_path):
    """A journal whose file was unlinked must not keep its intent.

    ``release`` keeps it on purpose (the file stays replayable for its TTL), and
    ``_reclaim_intent`` only reclaims intents whose file the sweep itself
    unlinked — so a *dropped* journal's intent was never reclaimed and the map
    grew one entry per streamed request for the process lifetime (AUDIT #395).
    """
    store = JournalStore(tmp_path / "j", ttl_s=600.0, max_bytes=1 << 20)
    rid = "0d76f7149cb048dc"

    j = await store.open(rid, key_id="kid-A")
    assert store.owner_of(rid) == "kid-A"
    store.release(rid)
    # Still owned after release alone: the replayable file needs its gate.
    assert store.owner_of(rid) == "kid-A"

    # The #320 drop path: file gone, so the intent has no job left.
    j.path.unlink(missing_ok=True)
    store.forget_owner(rid)
    assert store.owner_of(rid) is None
    assert store.has_owner_intent(rid) is False

async def test_forget_owner_is_a_noop_for_an_unknown_request(tmp_path):
    """Calling it on a request with no intent must not raise.

    ``_drop_journal`` runs on paths where the journal may never have been opened
    successfully, so the cleanup must be unconditional.
    """
    store = JournalStore(tmp_path / "j", ttl_s=600.0, max_bytes=1 << 20)
    store.forget_owner("never-opened")
    store.forget_owner("")  # the empty-id edge the release path also guards
    assert store.owner_of("never-opened") is None

async def test_a_finished_journals_intent_survives_forget_of_another(tmp_path):
    """Control: forgetting one journal must not disturb another's ownership.

    The map is shared, so a bulk cleanup that dropped more than it was asked to
    would re-open #175 for every concurrent stream.
    """
    store = JournalStore(tmp_path / "j", ttl_s=600.0, max_bytes=1 << 20)
    keep, drop = "0d76f7149cb048dc", "1a2b3c4d5e6f7081"
    await store.open(keep, key_id="kid-A")
    jd = await store.open(drop, key_id="kid-B")

    jd.path.unlink(missing_ok=True)
    store.release(drop)
    store.forget_owner(drop)

    assert store.owner_of(drop) is None
    assert store.owner_of(keep) == "kid-A", (
        "forgetting one journal's intent cleared a live stream's ownership")

# ---------------------------------------------------------------------------
# #393 — the deploy extract must not overwrite the Space's .gitattributes
# ---------------------------------------------------------------------------

def test_deploy_extract_excludes_gitattributes():
    """The archive step must not unpack the repo's ``.gitattributes``.

    The cleanup loop above it deliberately preserves the Space's copy ("carries
    HF's LFS rules"), and the repo tracks its own ``.gitattributes`` (the
    ``docs/assets/shots/*.png`` rule) — so a plain extract overwrote the Space's
    and discarded HF's weight rules, pre-loading the exact "push rejected because
    it contains binary files" failure the ``git lfs track`` below exists to
    prevent (AUDIT #393).
    """
    script = Path(__file__).resolve().parents[1] / "deploy" / "hf_space.sh"
    text = script.read_text()

    # The extract line must carry the exclude.
    extract = re.search(r"^git -C \"\$ROOT\" archive.*?\n(?:\s*\|.*\n)*", text,
                        re.MULTILINE)
    assert extract, "could not find the archive|tar extract pipeline"
    assert "--exclude=.gitattributes" in extract.group(0), (
        "the deploy extract overwrites the Space's .gitattributes with the "
        "repo's copy, discarding HF's LFS weight rules (#393)")

    # And the guard it exists to protect must still be there.
    assert '[[ "$base" == ".git" || "$base" == ".gitattributes" ]] && continue' \
        in text, "the preserve guard was removed; the exclude depends on it"
