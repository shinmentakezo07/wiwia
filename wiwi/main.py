"""wiwi CLI entrypoint: wiwi --config wiwi.yaml"""

from __future__ import annotations

import argparse
import os
import sys
from typing import TYPE_CHECKING

import uvicorn

from wiwi.config import (
    ConfigError,
    WiwiConfig,
    load_config,
    load_config_from_string,
    load_env,
)

if TYPE_CHECKING:
    from fastapi import FastAPI


def _resolve_config(config_path: str | None) -> WiwiConfig:
    """Load the config with the documented precedence.

    ``--config`` path > ``WIWI_CONFIG`` (raw YAML, for containers) >
    ``wiwi.yaml`` in the CWD. Shared by the CLI and the reload app factory so
    the reloaded server resolves the same source the developer started.
    """
    if config_path:
        return load_config(config_path)
    config_yaml = os.environ.get("WIWI_CONFIG")
    if config_yaml:
        return load_config_from_string(config_yaml)
    return load_config("wiwi.yaml")


def _reload_app_factory() -> FastAPI:
    """App factory for uvicorn reload mode (see :func:`cli`).

    Uvicorn re-imports the app in a fresh subprocess on every restart and can
    only be given an import string, so the parsed config cannot cross the
    boundary. ``cli`` publishes the resolved ``--config`` path through
    ``WIWI_CONFIG_PATH``; the child inherits ``os.environ`` and re-resolves
    from there, which keeps one precedence rule for both processes.
    """
    # The child inherits the parent's environment, but load .env anyway so this
    # factory also works when uvicorn imports it directly (same reason
    # wiwi.server.app.create_app_from_config_path does).
    load_env()
    try:
        config = _resolve_config(os.environ.get("WIWI_CONFIG_PATH"))
    except ConfigError as e:
        print(f"wiwi: config error: {e}", file=sys.stderr)
        sys.exit(1)
    from wiwi.server.app import create_app
    return create_app(config)


def cli() -> None:
    parser = argparse.ArgumentParser(prog="wiwi",
                                     description="wiwi — unified LLM gateway proxy")
    parser.add_argument("--config", "-c", default=None, help="path to wiwi.yaml")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--reload", action="store_true",
                        help="auto-reload on code changes (dev mode)")
    parser.add_argument("--reload-dir", action="append", default=None,
                        metavar="DIR",
                        help="directory to watch for reload (default: wiwi/); "
                             "may be repeated for multiple dirs")
    args = parser.parse_args()

    # Load .env before anything else so DATABASE_URL, WIWI_MASTER_KEY, provider
    # keys, and WIWI_CONFIG are all available during config parsing.
    load_env()

    try:
        config = _resolve_config(args.config)
    except ConfigError as e:
        print(f"wiwi: config error: {e}", file=sys.stderr)
        sys.exit(1)

    host = args.host or config.wiwi_settings.host
    port = args.port or config.wiwi_settings.port

    if args.reload:
        # Uvicorn reload requires the app as a string import path, not an object,
        # because it re-imports the app in a fresh subprocess on each restart.
        # That subprocess cannot be handed the parsed config, so publish the
        # resolved --config path and let wiwi.main:_reload_app_factory re-resolve
        # it there. Without this the child silently fell back to ./wiwi.yaml —
        # wrong providers, wrong master key, wrong database. Assigned on both
        # branches: a stale value inherited from an outer shell would otherwise
        # make the child resolve a different source than the parent just did.
        # Absolute so a relative path cannot be re-resolved against a new cwd.
        os.environ["WIWI_CONFIG_PATH"] = (
            os.path.abspath(args.config) if args.config else "")
        reload_dirs = args.reload_dir or ["wiwi"]
        print(f"wiwi dev mode (reload) listening on http://{host}:{port} "
              f"watching: {', '.join(reload_dirs)}")
        uvicorn.run(
            "wiwi.main:_reload_app_factory",
            factory=True,
            host=host,
            port=port,
            log_level="info",
            reload=True,
            reload_dirs=reload_dirs,
            reload_includes=["*.py"],
            timeout_graceful_shutdown=5,
        )
    else:
        from wiwi.server.app import create_app
        app = create_app(config)
        print(f"wiwi listening on http://{host}:{port} "
              f"({len(config.model_list)} deployments, {len(config.providers)} providers)")
        uvicorn.run(app, host=host, port=port, log_level="info",
                    timeout_graceful_shutdown=5)


if __name__ == "__main__":
    cli()
