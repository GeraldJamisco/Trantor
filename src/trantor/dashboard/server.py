"""Minimal read-only HTTP server for the Trantor dashboard.

Research views read the immutable Phase E/F artifacts; live views read the
runtime SQLite database opened read-only. Every endpoint is GET-only: the
dashboard cannot submit orders, change settings, or engage/release the
emergency stop (use ``python -m trantor.runtime stop-entries`` for that).
"""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .data import DashboardConfig, DashboardError, ResearchRepository
from .runtime_data import RuntimeRepository, experiments_payload

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/assets/app.css": ("app.css", "text/css; charset=utf-8"),
    "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/assets/live.js": ("live.js", "text/javascript; charset=utf-8"),
    "/assets/trantor-mark.svg": ("trantor-mark.svg", "image/svg+xml"),
}


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """Serve the dashboard shell and read-only JSON endpoints."""

    repository: ResearchRepository
    runtime: RuntimeRepository
    server_version = "TrantorDashboard/0.1"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:
        # Keep the local terminal quiet; errors are returned to the browser.
        return

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        )

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: object) -> None:
        body = (
            json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _query(self) -> dict[str, str]:
        parsed = urlsplit(self.path)
        values = parse_qs(parsed.query, keep_blank_values=False)
        return {key: items[-1] for key, items in values.items() if items}

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlsplit(self.path)
        path = parsed.path
        try:
            if path in STATIC_FILES:
                filename, content_type = STATIC_FILES[path]
                body = (STATIC_DIR / filename).read_bytes()
                self._send_bytes(HTTPStatus.OK, body, content_type)
                return
            if path == "/api/overview":
                self._send_json(HTTPStatus.OK, self.repository.overview())
                return
            if path == "/api/research":
                self._send_json(HTTPStatus.OK, self.repository.research_payload())
                return
            if path == "/api/strategy":
                self._send_json(HTTPStatus.OK, self.repository.strategy_payload())
                return
            if path == "/api/trades":
                self._send_json(
                    HTTPStatus.OK,
                    self.repository.trades_payload(self._query()),
                )
                return
            if path == "/api/diagnostics":
                self._send_json(
                    HTTPStatus.OK,
                    self.repository.diagnostics_payload(),
                )
                return
            if path == "/api/decisions":
                self._send_json(
                    HTTPStatus.OK,
                    self.repository.decisions_payload(self._query()),
                )
                return
            if path.startswith("/api/decisions/"):
                record_hash = unquote(path[len("/api/decisions/") :])
                self._send_json(
                    HTTPStatus.OK,
                    self.repository.decision_detail(record_hash),
                )
                return
            if path == "/api/data":
                self._send_json(HTTPStatus.OK, self.repository.data_page_payload())
                return
            if path == "/api/reports":
                self._send_json(HTTPStatus.OK, self.repository.reports_payload())
                return
            if path == "/api/system":
                self._send_json(HTTPStatus.OK, self.repository.system_payload())
                return
            if path == "/api/config":
                self._send_json(HTTPStatus.OK, self.repository.config_payload())
                return
            if path == "/api/experiments":
                root = self.repository.config.repo_root / "research_results" / "xauusd_p"
                self._send_json(HTTPStatus.OK, experiments_payload(root))
                return
            if path.startswith("/api/live/"):
                self._send_json(HTTPStatus.OK, self._live(path[len("/api/live/") :]))
                return
            if path == "/api/candles":
                self._send_json(
                    HTTPStatus.OK,
                    self.repository.candles_payload(self._query()),
                )
                return
            raise DashboardError("not found", 404)
        except DashboardError as exc:
            self._send_json(exc.status, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            # Do not leak tracebacks or local paths to the browser.
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "dashboard request failed"},
            )

    def _live(self, endpoint: str) -> object:
        query = self._query()
        if endpoint == "status":
            return self.runtime.status()
        if endpoint == "candles":
            return self.runtime.candles(query.get("tf", "15m"))
        if endpoint == "decisions":
            return self.runtime.decisions(query)
        if endpoint.startswith("decisions/"):
            return self.runtime.decision_detail(unquote(endpoint[len("decisions/") :]))
        if endpoint == "positions":
            return self.runtime.positions()
        if endpoint == "risk":
            return self.runtime.risk()
        if endpoint == "health":
            return self.runtime.health(query)
        raise DashboardError("not found", 404)

    def _method_not_allowed(self) -> None:
        # Drain (a bounded amount of) any request body before replying so the
        # client can read the 405; unread data makes Windows reset the socket.
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if 0 < length <= 65_536:
            self.rfile.read(length)
        self.close_connection = True
        self._send_json(
            HTTPStatus.METHOD_NOT_ALLOWED,
            {"error": "the dashboard is read-only; only GET is supported"},
        )

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        self._method_not_allowed()

    def do_PUT(self) -> None:  # noqa: N802 - stdlib handler API
        self._method_not_allowed()

    def do_PATCH(self) -> None:  # noqa: N802 - stdlib handler API
        self._method_not_allowed()

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler API
        self._method_not_allowed()


def build_server(
    config: DashboardConfig | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    runtime_db: Path | None = None,
) -> ThreadingHTTPServer:
    """Build a local server without starting it."""

    repository = ResearchRepository(config)
    resolved = config or DashboardConfig.default()
    state_dir = resolved.repo_root / "state"
    runtime = RuntimeRepository(
        runtime_db or state_dir / "trantor_runtime.sqlite3",
        kill_switch_path=state_dir / "EMERGENCY_STOP",
    )
    handler = type(
        "BoundDashboardRequestHandler",
        (DashboardRequestHandler,),
        {"repository": repository, "runtime": runtime},
    )
    return ThreadingHTTPServer((host, port), handler)
