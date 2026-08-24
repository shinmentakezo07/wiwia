"""wiwi CLI entrypoint: wiwi --config wiwi.yaml"""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn

from wiwi.config import ConfigError, load_config, load_config_from_string


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

    try:
        # Precedence: --config flag > WIWI_CONFIG env var > default wiwi.yaml
        config_yaml = os.environ.get("WIWI_CONFIG")
        if args.config:
            config = load_config(args.config)
        elif config_yaml:
            config = load_config_from_string(config_yaml)
        else:
            config = load_config("wiwi.yaml")
    except ConfigError as e:
        print(f"wiwi: config error: {e}", file=sys.stderr)
        sys.exit(1)

    host = args.host or config.wiwi_settings.host
    port = args.port or config.wiwi_settings.port

    if args.reload:
        # Uvicorn reload requires the app as a string import path, not an object,
        # because it re-imports the app in a fresh subprocess on each restart.
        reload_dirs = args.reload_dir or ["wiwi"]
        print(f"wiwi dev mode (reload) listening on http://{host}:{port} "
              f"watching: {', '.join(reload_dirs)}")
        uvicorn.run(
            "wiwi.server.app:create_app_from_config_path",
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
