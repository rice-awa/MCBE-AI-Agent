"""Local HTTP API over agent trace journal (stdlib only).

Uses ``ThreadingHTTPServer`` with GET for reads and DELETE for local journal
mutations (delete one trace / clear journal). Serves JSON under ``/api/*``
and optional static files from ``static_dir`` (``/`` and ``/assets/*``).
Path traversal is rejected. Responses use UTF-8 JSON and ``Cache-Control: no-store``.
"""

from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from services.agent.trace_query import TraceQuery

_MAX_BODY_BYTES = 64 * 1024


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")


class TraceAPIServer:
    """Loopback-friendly trace API + static file server (GET + local DELETE)."""

    def __init__(
        self,
        host: str,
        port: int,
        query: TraceQuery,
        static_dir: Path | str,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.query = query
        self.static_dir = Path(static_dir).resolve()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        server_self = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                # Quiet by default (tests / local tooling)
                return

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                path = unquote(parsed.path or "/")
                query = parse_qs(parsed.query or "")

                if path == "/api/health":
                    self._send_json(200, server_self.query.health())
                    return
                if path == "/api/config":
                    self._send_json(200, server_self._config_payload())
                    return
                if path == "/api/traces":
                    status = _first(query.get("status"))
                    player = _first(query.get("player"))
                    limit_raw = _first(query.get("limit"))
                    try:
                        limit = int(limit_raw) if limit_raw is not None else 50
                    except (TypeError, ValueError):
                        limit = 50
                    traces = server_self.query.list_traces(
                        status=status,
                        player=player,
                        limit=limit,
                    )
                    self._send_json(200, {"traces": traces, "count": len(traces)})
                    return
                if path.startswith("/api/traces/"):
                    trace_id = path[len("/api/traces/") :].strip("/")
                    if not trace_id or "/" in trace_id or ".." in trace_id:
                        self._send_json(404, {"error": "not_found"})
                        return
                    detail = server_self.query.get_trace(trace_id)
                    if detail is None:
                        self._send_json(404, {"error": "not_found", "trace_id": trace_id})
                        return
                    self._send_json(200, detail)
                    return

                # Static routes
                if path == "/" or path == "/index.html":
                    self._serve_static("index.html")
                    return
                if path.startswith("/assets/"):
                    rel = path[len("/assets/") :]
                    self._serve_static(Path("assets") / rel)
                    return

                self._send_json(404, {"error": "not_found"})

            def do_POST(self) -> None:  # noqa: N802
                self._method_not_allowed()

            def do_PUT(self) -> None:  # noqa: N802
                self._method_not_allowed()

            def do_DELETE(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                path = unquote(parsed.path or "/")

                if path == "/api/traces":
                    self._handle_clear_journal()
                    return
                if path.startswith("/api/traces/"):
                    trace_id = path[len("/api/traces/") :].strip("/")
                    if not trace_id or "/" in trace_id or ".." in trace_id:
                        self._send_json(404, {"error": "not_found"})
                        return
                    self._handle_delete_trace(trace_id)
                    return

                self._send_json(404, {"error": "not_found"})

            def do_PATCH(self) -> None:  # noqa: N802
                self._method_not_allowed()

            def _read_json_body(self) -> tuple[Any | None, dict[str, Any] | None]:
                """Return (parsed_body, error_payload). error_payload set on 400 cases."""
                length_raw = self.headers.get("Content-Length") or "0"
                try:
                    length = int(length_raw)
                except (TypeError, ValueError):
                    length = 0
                if length < 0:
                    length = 0
                if length > _MAX_BODY_BYTES:
                    return None, {"error": "confirm_required"}
                raw = self.rfile.read(length) if length else b""
                if not raw or not raw.strip():
                    return None, {"error": "confirm_required"}
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                    return None, {"error": "confirm_required"}
                if not isinstance(body, dict):
                    return None, {"error": "confirm_required"}
                return body, None

            def _handle_delete_trace(self, trace_id: str) -> None:
                body, err = self._read_json_body()
                if err is not None:
                    self._send_json(400, err)
                    return
                assert body is not None
                if body.get("confirm") is not True:
                    self._send_json(400, {"error": "confirm_required"})
                    return

                existing = server_self.query.get_trace(trace_id)
                if existing is None:
                    self._send_json(404, {"error": "not_found", "trace_id": trace_id})
                    return

                try:
                    removed = server_self.query.delete_trace(trace_id)
                except OSError as exc:
                    self._send_json(
                        500,
                        {"error": "write_failed", "message": str(exc)},
                    )
                    return
                except Exception as exc:  # noqa: BLE001
                    self._send_json(
                        500,
                        {"error": "write_failed", "message": str(exc)},
                    )
                    return

                if removed == 0:
                    self._send_json(404, {"error": "not_found", "trace_id": trace_id})
                    return

                self._send_json(
                    200,
                    {
                        "ok": True,
                        "removed_events": removed,
                        "trace_id": trace_id,
                    },
                )

            def _handle_clear_journal(self) -> None:
                body, err = self._read_json_body()
                if err is not None:
                    self._send_json(400, err)
                    return
                assert body is not None
                if body.get("confirm") != "CLEAR_ALL":
                    self._send_json(400, {"error": "confirm_required"})
                    return

                try:
                    server_self.query.clear_journal()
                except OSError as exc:
                    self._send_json(
                        500,
                        {"error": "write_failed", "message": str(exc)},
                    )
                    return
                except Exception as exc:  # noqa: BLE001
                    self._send_json(
                        500,
                        {"error": "write_failed", "message": str(exc)},
                    )
                    return

                self._send_json(200, {"ok": True, "cleared": True})

            def _method_not_allowed(self) -> None:
                body = _json_bytes({"error": "method_not_allowed"})
                self.send_response(405)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Allow", "GET, DELETE")
                self.end_headers()
                self.wfile.write(body)

            def _send_json(self, status: int, payload: Any) -> None:
                body = _json_bytes(payload)
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _serve_static(self, relative: str | Path) -> None:
                root = server_self.static_dir
                try:
                    candidate = (root / relative).resolve()
                except (OSError, RuntimeError):
                    self.send_error(404, "Not Found")
                    return
                # Reject path traversal outside static_dir
                try:
                    candidate.relative_to(root)
                except ValueError:
                    self.send_response(403)
                    body = _json_bytes({"error": "forbidden", "reason": "path_traversal"})
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if not candidate.is_file():
                    self.send_response(404)
                    body = _json_bytes({"error": "not_found"})
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                data = candidate.read_bytes()
                ctype, _ = mimetypes.guess_type(str(candidate))
                if not ctype:
                    ctype = "application/octet-stream"
                if ctype.startswith("text/") or ctype in {
                    "application/javascript",
                    "application/json",
                    "image/svg+xml",
                }:
                    if "charset" not in ctype:
                        ctype = f"{ctype}; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

        return Handler

    def _config_payload(self) -> dict[str, Any]:
        """Expose non-secret audit config for the UI header."""
        enabled = False
        include_content = False
        path = str(self.query.path)
        api_host = self.host
        api_port = self.port
        try:
            from config.settings import get_settings

            settings = get_settings()
            enabled = bool(getattr(settings, "agent_trace_enabled", False))
            include_content = bool(getattr(settings, "agent_trace_include_content", False))
            path = str(getattr(settings, "agent_trace_path", path) or path)
            api_host = str(getattr(settings, "agent_trace_api_host", api_host) or api_host)
            api_port = int(getattr(settings, "agent_trace_api_port", api_port) or api_port)
        except Exception:  # noqa: BLE001
            pass
        if not enabled:
            include_content = False
        return {
            "agent_trace_enabled": enabled,
            "agent_trace_include_content": include_content,
            "agent_trace_path": path,
            "agent_trace_api_host": api_host,
            "agent_trace_api_port": api_port,
            "read_only": False,
            "mutations_enabled": True,
            "mutations": ["delete_trace", "clear_journal"],
            "journal_path": str(self.query.path),
        }

    def create_server(self) -> ThreadingHTTPServer:
        handler = self._make_handler()
        httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self._httpd = httpd
        # If port 0 was requested, record the OS-assigned port
        if self.port == 0:
            self.port = int(httpd.server_address[1])
        return httpd

    def serve_forever(self) -> None:
        httpd = self._httpd or self.create_server()
        httpd.serve_forever()

    def start_background(self) -> ThreadingHTTPServer:
        """Start server in a daemon thread (for tests / optional embedding)."""
        httpd = self.create_server()

        def _run() -> None:
            httpd.serve_forever()

        thread = threading.Thread(target=_run, name="trace-api-server", daemon=True)
        self._thread = thread
        thread.start()
        return httpd

    def shutdown(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None


def _first(values: list[str] | None) -> str | None:
    if not values:
        return None
    value = values[0]
    if value is None or value == "":
        return None
    return value


__all__ = ["TraceAPIServer"]
