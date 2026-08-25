"""DB persistence for admin-added providers, keys, and deployments.

Works with both SQLite (aiosqlite) and PostgreSQL (asyncpg).  DDL and
queries are dialect-portable: auto-increment and upsert syntax are
resolved via a dialect check at startup.

The router is built from ``wiwi.yaml`` at startup.  Admin API mutations
(add/edit/delete providers, provider keys, model-group deployments, alert
rules, routing strategy) modify in-memory state only — without this store
they would be lost on restart.

``ConfigStore`` layers DB-stored entries on top of the YAML-built router
during startup, and persists every admin mutation so changes survive
restarts.  YAML-sourced entries are never written to the DB; they are
always reloaded from the file.  Only admin-created entries are persisted.
"""

from __future__ import annotations

import orjson
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

PROVIDER_DDL = """
CREATE TABLE IF NOT EXISTS providers (
  name TEXT PRIMARY KEY,
  provider_type TEXT NOT NULL,
  base_url TEXT NOT NULL DEFAULT '',
  timeout_s REAL NOT NULL DEFAULT 120.0,
  extra_headers TEXT NOT NULL DEFAULT '{}',
  round_robin INTEGER NOT NULL DEFAULT 1
);
"""

KEY_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS provider_keys (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider_name TEXT NOT NULL,
  label TEXT NOT NULL,
  secret TEXT NOT NULL,
  weight INTEGER NOT NULL DEFAULT 1,
  enabled INTEGER NOT NULL DEFAULT 1,
  UNIQUE(provider_name, label)
);
"""

KEY_DDL_PG = """
CREATE TABLE IF NOT EXISTS provider_keys (
  id SERIAL PRIMARY KEY,
  provider_name TEXT NOT NULL,
  label TEXT NOT NULL,
  secret TEXT NOT NULL,
  weight INTEGER NOT NULL DEFAULT 1,
  enabled INTEGER NOT NULL DEFAULT 1,
  UNIQUE(provider_name, label)
);
"""

DEPLOYMENT_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS deployments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_name TEXT NOT NULL,
  provider_name TEXT NOT NULL,
  model_id TEXT NOT NULL,
  weight INTEGER NOT NULL DEFAULT 1,
  UNIQUE(group_name, provider_name, model_id)
);
"""

DEPLOYMENT_DDL_PG = """
CREATE TABLE IF NOT EXISTS deployments (
  id SERIAL PRIMARY KEY,
  group_name TEXT NOT NULL,
  provider_name TEXT NOT NULL,
  model_id TEXT NOT NULL,
  weight INTEGER NOT NULL DEFAULT 1,
  UNIQUE(group_name, provider_name, model_id)
);
"""

SETTINGS_DDL = """
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

MODEL_PRICES_DDL = """
CREATE TABLE IF NOT EXISTS model_prices (
  model_id TEXT PRIMARY KEY,
  input_cost_per_token REAL NOT NULL DEFAULT 0,
  output_cost_per_token REAL NOT NULL DEFAULT 0,
  cache_read_input_cost_per_token REAL,
  max_input_tokens INTEGER,
  max_output_tokens INTEGER,
  mode TEXT
);
"""


class ConfigStore:
    """Persists admin-added routing state to the database.

    Works with both SQLite and PostgreSQL.  Used by ``AppState.init_db()``
    at startup and by every admin API handler that mutates providers,
    keys, deployments, alert rules, or routing strategy.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self._is_pg = engine.dialect.name == "postgresql"

    async def startup(self) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(sa.text(PROVIDER_DDL))
            key_ddl = KEY_DDL_PG if self._is_pg else KEY_DDL_SQLITE
            dep_ddl = DEPLOYMENT_DDL_PG if self._is_pg else DEPLOYMENT_DDL_SQLITE
            await conn.execute(sa.text(key_ddl))
            await conn.execute(sa.text(dep_ddl))
            await conn.execute(sa.text(SETTINGS_DDL))
            await conn.execute(sa.text(MODEL_PRICES_DDL))
            await self._migrate(conn)

    async def _migrate(self, conn) -> None:
        """Add extra_headers column and indexes if missing (idempotent)."""
        if self._is_pg:
            cols = {r[0] for r in (await conn.execute(sa.text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'providers'"))).all()}
        else:
            cols = {r[1] for r in (await conn.execute(
                sa.text("PRAGMA table_info(providers)"))).all()}
        if "extra_headers" not in cols:
            await conn.execute(sa.text(
                "ALTER TABLE providers ADD COLUMN extra_headers TEXT NOT NULL DEFAULT '{}'"))
        if "round_robin" not in cols:
            await conn.execute(sa.text(
                "ALTER TABLE providers ADD COLUMN round_robin INTEGER NOT NULL DEFAULT 1"))

        # Indexes on FK columns for cascade-delete performance and lookups:
        # - provider_keys.provider_name: FK join + cascade delete
        # - deployments.provider_name: cascade delete when provider is removed
        # - deployments.group_name: lookup deployments by model group
        for idx in [
            "CREATE INDEX IF NOT EXISTS idx_provider_keys_provider ON provider_keys(provider_name)",
            "CREATE INDEX IF NOT EXISTS idx_deployments_provider ON deployments(provider_name)",
            "CREATE INDEX IF NOT EXISTS idx_deployments_group ON deployments(group_name)",
        ]:
            await conn.execute(sa.text(idx))

    # -- providers --------------------------------------------------------------

    async def add_provider(self, name: str, provider_type: str, base_url: str,
                           timeout_s: float = 120.0,
                           extra_headers: dict | None = None,
                           round_robin: bool = True) -> None:
        hdrs = orjson.dumps(extra_headers or {}).decode()
        rr = int(round_robin)
        if self._is_pg:
            sql = ("INSERT INTO providers"
                   " (name, provider_type, base_url, timeout_s, extra_headers, round_robin)"
                   " VALUES (:n,:t,:b,:s,:h,:r)"
                   " ON CONFLICT (name) DO UPDATE SET"
                   " provider_type=EXCLUDED.provider_type,"
                   " base_url=EXCLUDED.base_url,"
                   " timeout_s=EXCLUDED.timeout_s,"
                   " extra_headers=EXCLUDED.extra_headers,"
                   " round_robin=EXCLUDED.round_robin")
        else:
            sql = ("INSERT OR REPLACE INTO providers"
                   " (name, provider_type, base_url, timeout_s, extra_headers, round_robin)"
                   " VALUES (:n,:t,:b,:s,:h,:r)")
        async with self.engine.begin() as conn:
            await conn.execute(sa.text(sql),
                               {"n": name, "t": provider_type, "b": base_url,
                                "s": timeout_s, "h": hdrs, "r": rr})

    async def update_provider(self, name: str, *, provider_type: str | None = None,
                              base_url: str | None = None,
                              round_robin: bool | None = None,
                              new_name: str | None = None) -> None:
        sets: list[str] = []
        params: dict = {"name": name}
        if provider_type is not None:
            sets.append("provider_type = :pt")
            params["pt"] = provider_type
        if base_url is not None:
            sets.append("base_url = :bu")
            params["bu"] = base_url
        if round_robin is not None:
            sets.append("round_robin = :rr")
            params["rr"] = int(round_robin)
        if new_name is not None and new_name != name:
            sets.append("name = :nn")
            params["nn"] = new_name
        if not sets:
            return
        async with self.engine.begin() as conn:
            # When renaming, update child tables FIRST so the FK constraint
            # on provider_keys/deployments is not violated when we change
            # the parent's name.
            if new_name is not None and new_name != name:
                await conn.execute(
                    sa.text("UPDATE provider_keys SET provider_name = :nn"
                            " WHERE provider_name = :name"),
                    {"nn": new_name, "name": name})
                await conn.execute(
                    sa.text("UPDATE deployments SET provider_name = :nn"
                            " WHERE provider_name = :name"),
                    {"nn": new_name, "name": name})
            await conn.execute(
                sa.text(f"UPDATE providers SET {', '.join(sets)} WHERE name = :name"),
                params)

    async def delete_provider(self, name: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(sa.text("DELETE FROM deployments WHERE provider_name = :n"),
                               {"n": name})
            await conn.execute(sa.text("DELETE FROM provider_keys WHERE provider_name = :n"),
                               {"n": name})
            await conn.execute(sa.text("DELETE FROM providers WHERE name = :n"),
                               {"n": name})

    # -- keys -------------------------------------------------------------------

    async def add_key(self, provider_name: str, label: str, secret: str,
                      weight: int = 1, enabled: bool = True) -> None:
        if self._is_pg:
            sql = ("INSERT INTO provider_keys"
                   " (provider_name, label, secret, weight, enabled)"
                   " VALUES (:p,:l,:s,:w,:e)"
                   " ON CONFLICT (provider_name, label) DO UPDATE SET"
                   " secret=EXCLUDED.secret, weight=EXCLUDED.weight,"
                   " enabled=EXCLUDED.enabled")
        else:
            sql = ("INSERT OR REPLACE INTO provider_keys"
                   " (provider_name, label, secret, weight, enabled)"
                   " VALUES (:p,:l,:s,:w,:e)")
        async with self.engine.begin() as conn:
            await conn.execute(sa.text(sql),
                               {"p": provider_name, "l": label, "s": secret,
                                "w": weight, "e": int(enabled)})

    async def update_key(self, provider_name: str, label: str, *,
                         weight: int | None = None,
                         enabled: bool | None = None) -> None:
        sets: list[str] = []
        params: dict = {"p": provider_name, "l": label}
        if weight is not None:
            sets.append("weight = :w")
            params["w"] = weight
        if enabled is not None:
            sets.append("enabled = :e")
            params["e"] = int(enabled)
        if not sets:
            return
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.text(f"UPDATE provider_keys SET {', '.join(sets)}"
                        " WHERE provider_name = :p AND label = :l"),
                params)

    async def delete_key(self, provider_name: str, label: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.text("DELETE FROM provider_keys"
                        " WHERE provider_name = :p AND label = :l"),
                {"p": provider_name, "l": label})

    # -- deployments ------------------------------------------------------------

    async def add_deployment(self, group_name: str, provider_name: str,
                             model_id: str, weight: int = 1) -> None:
        if self._is_pg:
            sql = ("INSERT INTO deployments"
                   " (group_name, provider_name, model_id, weight)"
                   " VALUES (:g,:p,:m,:w)"
                   " ON CONFLICT (group_name, provider_name, model_id)"
                   " DO UPDATE SET weight=EXCLUDED.weight")
        else:
            sql = ("INSERT OR REPLACE INTO deployments"
                   " (group_name, provider_name, model_id, weight)"
                   " VALUES (:g,:p,:m,:w)")
        async with self.engine.begin() as conn:
            await conn.execute(sa.text(sql),
                               {"g": group_name, "p": provider_name,
                                "m": model_id, "w": weight})

    async def update_deployment_weight(self, group_name: str, provider_name: str,
                                       model_id: str, weight: int) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.text("UPDATE deployments SET weight = :w"
                        " WHERE group_name = :g AND provider_name = :p AND model_id = :m"),
                {"w": weight, "g": group_name, "p": provider_name, "m": model_id})

    async def delete_deployment(self, group_name: str, provider_name: str,
                                model_id: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.text("DELETE FROM deployments"
                        " WHERE group_name = :g AND provider_name = :p AND model_id = :m"),
                {"g": group_name, "p": provider_name, "m": model_id})

    # -- model pricing ---------------------------------------------------------

    async def upsert_price(self, model_id: str, entry: dict) -> None:
        """Insert or update a custom model pricing entry."""
        params = {
            "m": model_id,
            "ipt": entry.get("input_cost_per_token", 0),
            "opt": entry.get("output_cost_per_token", 0),
            "cr": entry.get("cache_read_input_cost_per_token"),
            "mi": entry.get("max_input_tokens"),
            "mo": entry.get("max_output_tokens"),
            "mode": entry.get("mode"),
        }
        cols = ("model_id, input_cost_per_token, output_cost_per_token,"
                " cache_read_input_cost_per_token, max_input_tokens,"
                " max_output_tokens, mode")
        ph = ":m,:ipt,:opt,:cr,:mi,:mo,:mode"
        if self._is_pg:
            updates = ("input_cost_per_token=EXCLUDED.input_cost_per_token,"
                       "output_cost_per_token=EXCLUDED.output_cost_per_token,"
                       "cache_read_input_cost_per_token=EXCLUDED.cache_read_input_cost_per_token,"
                       "max_input_tokens=EXCLUDED.max_input_tokens,"
                       "max_output_tokens=EXCLUDED.max_output_tokens,"
                       "mode=EXCLUDED.mode")
            sql = (f"INSERT INTO model_prices ({cols}) VALUES ({ph})"
                   f" ON CONFLICT (model_id) DO UPDATE SET {updates}")
        else:
            sql = f"INSERT OR REPLACE INTO model_prices ({cols}) VALUES ({ph})"
        async with self.engine.begin() as conn:
            await conn.execute(sa.text(sql), params)

    async def delete_price(self, model_id: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.text("DELETE FROM model_prices WHERE model_id = :m"),
                {"m": model_id})

    async def load_prices(self) -> list[dict]:
        """Return all DB-stored custom pricing entries."""
        async with self.engine.connect() as conn:
            rows = (await conn.execute(sa.text(
                "SELECT model_id, input_cost_per_token, output_cost_per_token,"
                " cache_read_input_cost_per_token, max_input_tokens,"
                " max_output_tokens, mode FROM model_prices ORDER BY model_id"))).all()
        out: list[dict] = []
        for r in rows:
            e: dict = {
                "input_cost_per_token": r[1],
                "output_cost_per_token": r[2],
            }
            if r[3] is not None:
                e["cache_read_input_cost_per_token"] = r[3]
            if r[4] is not None:
                e["max_input_tokens"] = r[4]
            if r[5] is not None:
                e["max_output_tokens"] = r[5]
            if r[6] is not None:
                e["mode"] = r[6]
            out.append({"model_id": r[0], **e})
        return out

    # -- settings (alert rules, routing strategy) -------------------------------

    async def set_setting(self, key: str, value) -> None:
        val = orjson.dumps(value).decode()
        if self._is_pg:
            sql = ("INSERT INTO settings (key, value) VALUES (:k,:v)"
                   " ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value")
        else:
            sql = "INSERT OR REPLACE INTO settings (key, value) VALUES (:k,:v)"
        async with self.engine.begin() as conn:
            await conn.execute(sa.text(sql), {"k": key, "v": val})

    async def get_setting(self, key: str, default=None):
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                sa.text("SELECT value FROM settings WHERE key = :k"),
                {"k": key})).first()
        if row is None:
            return default
        return orjson.loads(row[0])

    # -- bulk load at startup ---------------------------------------------------

    async def load_all(self) -> dict:
        """Return all DB-stored config as a plain dict for merging into the router."""
        async with self.engine.connect() as conn:
            prov_rows = (await conn.execute(sa.text(
                "SELECT name, provider_type, base_url, timeout_s, extra_headers,"
                " round_robin FROM providers ORDER BY name"))).all()
            key_rows = (await conn.execute(sa.text(
                "SELECT provider_name, label, secret, weight, enabled"
                " FROM provider_keys ORDER BY id"))).all()
            dep_rows = (await conn.execute(sa.text(
                "SELECT group_name, provider_name, model_id, weight"
                " FROM deployments ORDER BY id"))).all()
        return {
            "providers": [
                {"name": r[0], "provider_type": r[1], "base_url": r[2],
                 "timeout_s": r[3], "extra_headers": orjson.loads(r[4]),
                 "round_robin": bool(r[5])}
                for r in prov_rows
            ],
            "keys": [
                {"provider_name": r[0], "label": r[1], "secret": r[2],
                 "weight": r[3], "enabled": bool(r[4])}
                for r in key_rows
            ],
            "deployments": [
                {"group_name": r[0], "provider_name": r[1], "model_id": r[2],
                 "weight": r[3]}
                for r in dep_rows
            ],
        }
