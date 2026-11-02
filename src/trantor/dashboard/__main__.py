"""Start the read-only Trantor dashboard server."""

from __future__ import annotations

import argparse
from pathlib import Path

from .data import DEFAULT_ARTIFACT_REL, DashboardConfig
from .server import build_server


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Serve the read-only Trantor research dashboard. This does not "
            "connect to a broker, run a backtest, or submit orders."
        )
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=DEFAULT_ARTIFACT_REL,
        help="repository-relative authoritative Phase E artifact directory",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    default = DashboardConfig.default()
    artifact_dir = args.artifacts
    if not artifact_dir.is_absolute():
        artifact_dir = default.repo_root / artifact_dir
    config = DashboardConfig(
        repo_root=default.repo_root,
        artifact_dir=artifact_dir,
        settings_path=default.settings_path,
        settings_example_path=default.settings_example_path,
        app_version=default.app_version,
    )
    server = build_server(config, host=args.host, port=args.port)
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/"
    print("Trantor Research Dashboard")
    print(f"Read-only local URL: {url}")
    print("Press Ctrl+C to stop. No broker connection or order route exists.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
