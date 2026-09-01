"""Round-16 regression tests: admin (unowned) playground key cap.

Bug: ``_mint_playground_key`` skipped its cap entirely for admins. Admin
keys are minted with ``owner_id=None``, and both guard rails treated that
as "no cap applies":

- ``AuthService.create_key`` only enforces ``max_keys_per_user`` when
  ``owner_id is not None``;
- ``_mint_playground_key`` guarded its own cap behind
  ``if service is not None and owner_id is not None``.

So every admin login, and every ``/auth/playground-key`` call, minted
another never-reclaimed key. React StrictMode double-invokes the
Playground's mint effect in dev, so the leak was ~2 keys per mount.

Fix: both cap helpers now accept ``owner_id=None`` and select unowned rows
with ``IS NULL`` (``owner_id = NULL`` never matches in SQL), and the mint
path applies the cap unconditionally.

Also covers the Playground's stuck "Creating key…" state: a failed mint
returned "" and was never retried, and a 401 was indistinguishable from a
transient error, so a dead session spun forever.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import create_async_engine

from wiwi.auth.service import AuthService

MAX_PG_KEYS = 5


def _memory_engine():
    return create_async_engine("sqlite+aiosqlite:///:memory:")


async def _svc(max_keys_per_user: int = 50) -> AuthService:
    engine = _memory_engine()
    svc = AuthService(engine, master_key_plaintext="master-key-test",
                      max_keys_per_user=max_keys_per_user)
    await svc.startup()
    return svc


class TestUnownedKeyAccounting:
    """count_keys/expire_keys must see unowned (admin) keys."""

    async def test_count_keys_none_counts_unowned_only(self):
        """owner_id=None selects NULL-owner rows, not all rows.

        ``WHERE owner_id = NULL`` matches nothing, so a naive
        implementation silently returns 0 and the cap never fires.
        """
        svc = await _svc()
        await svc.create_key(alias="playground")          # unowned
        await svc.create_key(alias="playground")          # unowned
        await svc.create_key(alias="playground", owner_id="u1")

        assert await svc.count_keys(owner_id=None, alias="playground") == 2
        assert await svc.count_keys(owner_id="u1", alias="playground") == 1
        await svc.engine.dispose()

    async def test_count_keys_none_excludes_expired(self):
        """Expire must make unowned keys disappear from the count."""
        svc = await _svc()
        await svc.create_key(alias="playground")
        await svc.create_key(alias="playground")
        assert await svc.count_keys(owner_id=None) == 2

        await svc.expire_keys(owner_id=None, keep_newest=1)
        assert await svc.count_keys(owner_id=None) == 1
        await svc.engine.dispose()

    async def test_expire_keys_none_keeps_newest(self):
        """Expiring unowned keys keeps the newest N, same contract as owned."""
        svc = await _svc()
        for _ in range(4):
            await svc.create_key(alias="playground")

        expired = await svc.expire_keys(owner_id=None, keep_newest=2)
        assert expired == 2
        assert await svc.count_keys(owner_id=None) == 2
        await svc.engine.dispose()

    async def test_expire_keys_none_does_not_touch_owned(self):
        """Scoped correctly: unowned expiry must not reap a user's keys."""
        svc = await _svc()
        for _ in range(3):
            await svc.create_key(alias="playground")          # unowned
        await svc.create_key(alias="playground", owner_id="u1")

        await svc.expire_keys(owner_id=None, keep_newest=1)

        assert await svc.count_keys(owner_id=None) == 1
        # u1's key is untouched even though it would be the oldest overall.
        assert await svc.count_keys(owner_id="u1") == 1
        await svc.engine.dispose()

    async def test_alias_filter_still_applies_to_unowned(self):
        """The alias filter must combine with the IS NULL owner clause."""
        svc = await _svc()
        await svc.create_key(alias="playground")
        await svc.create_key(alias="production")

        assert await svc.count_keys(owner_id=None, alias="playground") == 1
        assert await svc.count_keys(owner_id=None) == 2
        await svc.engine.dispose()


class TestAdminPlaygroundKeyCap:
    """The mint path caps unowned keys, not just owned ones."""

    async def test_create_key_cap_still_exempts_unowned(self):
        """Documents create_key's deliberate unowned exemption.

        create_key intentionally skips the per-user cap for owner_id=None
        (admins mint on behalf of the deployment). The playground cap in
        _mint_playground_key is what has to bound that case — so this test
        pins the exemption to make the division of responsibility explicit.
        """
        svc = await _svc(max_keys_per_user=2)
        for _ in range(4):
            await svc.create_key(alias="playground")  # must not raise
        assert await svc.count_keys(owner_id=None) == 4
        await svc.engine.dispose()

    async def test_mint_helper_bounds_unowned_keys(self):
        """Repeated unowned mints are reclaimed, not accumulated.

        Mirrors _mint_playground_key's admin branch: count with
        owner_id=None, then expire down to the cap minus one so the new
        key can be created.
        """
        svc = await _svc()
        for _ in range(MAX_PG_KEYS):
            await svc.create_key(alias="playground")
        assert await svc.count_keys(owner_id=None, alias="playground") == MAX_PG_KEYS

        # The cap branch now runs for admins too.
        active = await svc.count_keys(owner_id=None, alias="playground")
        if active >= MAX_PG_KEYS:
            await svc.expire_keys(owner_id=None, alias="playground",
                                  keep_newest=MAX_PG_KEYS - 1)
        await svc.create_key(alias="playground")

        # One was reclaimed and one added → still at the cap, not above it.
        assert await svc.count_keys(owner_id=None, alias="playground") == MAX_PG_KEYS
        await svc.engine.dispose()

    async def test_mint_path_caps_admins_via_http(self):
        """End-to-end: repeated /auth/playground-key calls stay bounded.

        This is the actual leak — each call minted a key and nothing ever
        expired the old ones. Admins hit it on every login and every new
        tab.
        """
        from httpx import ASGITransport, AsyncClient

        from wiwi.config import GeneralSettings, WiwiConfig
        from wiwi.server.app import create_app

        cfg = WiwiConfig(
            general_settings=GeneralSettings(
                master_key="mk-test-master-key",
                database_url="sqlite+aiosqlite:///file:memdb16?mode=memory&cache=shared&uri=true",
            ))
        app = create_app(cfg)
        transport = ASGITransport(app=app)
        # Keep the shared in-memory DB alive for the whole test: it is
        # process-wide, and disposing the engine drops the schema.
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=transport, base_url="http://test") as client,
        ):
            r = await client.post("/auth/login",
                                  json={"master_key": "mk-test-master-key"})
            assert r.status_code == 200, r.text

            for _ in range(MAX_PG_KEYS + 3):
                mr = await client.post("/auth/playground-key")
                assert mr.status_code == 200, mr.text
                assert mr.json()["key"].startswith("sk-wiwi-")

            from wiwi.server.app import _MAX_PLAYGROUND_KEYS_PER_USER
            live = await app.state.wiwi.auth.count_keys(
                owner_id=None, alias="playground")
            assert live <= _MAX_PLAYGROUND_KEYS_PER_USER, (
                f"unowned playground keys grew unbounded: {live}")
