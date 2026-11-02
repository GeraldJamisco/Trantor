"""Minimal read-only HTTP server for the Trantor research dashboard."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .data import DashboardConfig, DashboardError, ResearchRepository

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/assets/app.css": ("app.css", "text/css; charset=utf-8"),
    "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/assets/trantor-mark.svg": ("trantor-mark.svg", "image/svg+xml"),
}


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """Serve the dashboard shell and read-only JSON endpoints."""

    repository: ResearchRepository
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

    def _method_not_allowed(self) -> None:
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
) -> ThreadingHTTPServer:
    """Build a local server without starting it."""

    repository = ResearchRepository(config)
    handler = type(
        "BoundDashboardRequestHandler",
        (DashboardRequestHandler,),
        {"repository": repository},
    )
    return ThreadingHTTPServer((host, port), handler)
