"""wiwi CLI entrypoint: wiwi --config wiwi.yaml"""

from __future__ import annotations

import argparse
import sys

import uvicorn

from wiwi.config import ConfigError, load_config


def cli() -> None:
    parser = argparse.ArgumentParser(prog="wiwi",
                                     description="wiwi — unified LLM gateway proxy")
    parser.add_argument("--config", "-c", default="wiwi.yaml", help="path to wiwi.yaml")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"wiwi: config error: {e}", file=sys.stderr)
        sys.exit(1)

    host = args.host or config.wiwi_settings.host
    port = args.port or config.wiwi_settings.port

    from wiwi.server.app import create_app
    app = create_app(config)
    print(f"wiwi listening on http://{host}:{port} "
          f"({len(config.model_list)} deployments, {len(config.providers)} providers)")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    cli()
