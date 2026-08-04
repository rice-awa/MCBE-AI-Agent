"""Offline tests for TraceAPIServer (stdlib HTTP, in-thread)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from services.agent.trace_api import TraceAPIServer
from services.agent.trace_query import TraceQuery


def _event(
    *,
    event_name: str,
    trace_id: str,
    sequence: int = 0,
    status: str = "info",
    player: str = "alex",
    timestamp: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attrs = {
        "player_name": player,
        "connection_id": "conn-1",
        "conversation_id": "default",
        "message_id": f"msg-{trace_id}",
    }
    if attributes:
        attrs.update(attributes)
    return {
        "schema_version": 1,
        "event_id": f"ev-{trace_id}-{sequence}",
        "event_name": event_name,
        "timestamp": timestamp
        or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "sequence": sequence,
        "trace_id": trace_id,
        "run_id": trace_id,
        "attempt_id": "attempt-1",
        "status": status,
        "attributes": attrs,
    }


def _write_journal(path: Path) -> None:
    base = datetime(2026, 7, 22, 9, 0, 0, tzinfo=UTC)
    lines = [
        _event(
            event_name="trace.started",
            trace_id="t-failed",
            sequence=0,
            status="started",
            player="steve",
            timestamp=base.isoformat().replace("+00:00", "Z"),
        ),
        _event(
            event_name="trace.failed",
            trace_id="t-failed",
            sequence=1,
            status="failed",
            player="steve",
            timestamp=(base + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
        ),
        _event(
            event_name="trace.started",
            trace_id="t-ok",
            sequence=0,
            status="started",
            player="alex",
            timestamp=(base + timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
        ),
        _event(
            event_name="trace.completed",
            trace_id="t-ok",
            sequence=1,
            status="completed",
            player="alex",
            timestamp=(base + timedelta(seconds=6)).isoformat().replace("+00:00", "Z"),
        ),
    ]
    path.write_text("\n".join(json.dumps(e) for e in lines) + "\n", encoding="utf-8")


class _ApiClient:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def get(self, path: str) -> "_Response":
        url = self.base + path
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read()
                headers = {k.lower(): v for k, v in resp.headers.items()}
                return _Response(resp.status, body, headers)
        except urllib.error.HTTPError as exc:
            body = exc.read()
            headers = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
            return _Response(exc.code, body, headers)

    def delete(self, path: str, payload: dict[str, Any] | None = None) -> "_Response":
        url = self.base + path
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="DELETE")
        if data is not None:
            req.add_header("Content-Type", "application/json")
            req.add_header("Content-Length", str(len(data)))
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read()
                headers = {k.lower(): v for k, v in resp.headers.items()}
                return _Response(resp.status, body, headers)
        except urllib.error.HTTPError as exc:
            body = exc.read()
            headers = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
            return _Response(exc.code, body, headers)


class _Response:
    def __init__(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.status = status
        self.body = body
        self.headers = headers
        self._json: Any | None = None

    @property
    def json(self) -> Any:
        if self._json is None:
            self._json = json.loads(self.body.decode("utf-8"))
        return self._json

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")


@pytest.fixture
def api_bundle(tmp_path: Path):
    journal = tmp_path / "agent_traces.jsonl"
    _write_journal(journal)
    static_dir = tmp_path / "web" / "trace"
    assets = static_dir / "assets"
    assets.mkdir(parents=True)
    (static_dir / "index.html").write_text(
        "<!doctype html><html><body>trace ui</body></html>",
        encoding="utf-8",
    )
    (assets / "app.js").write_text("console.log('ok');", encoding="utf-8")

    query = TraceQuery(journal)
    server = TraceAPIServer(
        host="127.0.0.1",
        port=0,
        query=query,
        static_dir=static_dir,
    )
    httpd = server.start_background()
    port = server.port
    client = _ApiClient(f"http://127.0.0.1:{port}")
    try:
        yield client, server, static_dir, journal
    finally:
        server.shutdown()


def test_api_returns_filtered_trace_list(api_bundle) -> None:
    client, *_ = api_bundle
    response = client.get("/api/traces?status=failed&limit=10")
    assert response.status == 200
    assert response.headers.get("cache-control") == "no-store"
    assert "application/json" in response.headers.get("content-type", "")
    assert all(item["status"] == "failed" for item in response.json["traces"])
    assert response.json["count"] == 1
    assert response.json["traces"][0]["trace_id"] == "t-failed"


def test_api_filter_by_player(api_bundle) -> None:
    client, *_ = api_bundle
    response = client.get("/api/traces?player=alex")
    assert response.status == 200
    assert all(item["player_name"] == "alex" for item in response.json["traces"])
    assert response.json["count"] == 1


def test_api_get_trace_detail(api_bundle) -> None:
    client, *_ = api_bundle
    response = client.get("/api/traces/t-ok")
    assert response.status == 200
    body = response.json
    assert body["summary"]["status"] == "completed"
    assert "events" in body
    assert "attempts" in body
    assert "models" in body
    assert "tools" in body
    assert "approvals" in body
    assert "delivery" in body


def test_api_get_trace_missing_404(api_bundle) -> None:
    client, *_ = api_bundle
    response = client.get("/api/traces/does-not-exist")
    assert response.status == 404
    assert response.json["error"] == "not_found"


def test_api_health_and_config(api_bundle) -> None:
    client, *_ = api_bundle
    health = client.get("/api/health")
    assert health.status == 200
    assert "parsed_lines" in health.json
    assert "malformed_lines" in health.json
    assert health.headers.get("cache-control") == "no-store"

    config = client.get("/api/config")
    assert config.status == 200
    assert config.json["read_only"] is False
    assert config.json["mutations_enabled"] is True
    assert config.json["mutations"] == ["delete_trace", "clear_journal"]
    assert "agent_trace_enabled" in config.json
    assert "agent_trace_include_content" in config.json


def test_api_serves_static_index_and_assets(api_bundle) -> None:
    client, *_ = api_bundle
    index = client.get("/")
    assert index.status == 200
    assert "trace ui" in index.text
    assert index.headers.get("cache-control") == "no-store"

    asset = client.get("/assets/app.js")
    assert asset.status == 200
    assert "console.log" in asset.text


def test_api_path_traversal_rejected(api_bundle) -> None:
    client, server, static_dir, _journal = api_bundle
    # Place a secret outside static_dir
    secret = static_dir.parent / "secret.txt"
    secret.write_text("top-secret", encoding="utf-8")

    response = client.get("/assets/../../secret.txt")
    # urllib may normalize; also try encoded traversal
    assert response.status in {403, 404}
    assert b"top-secret" not in response.body

    response2 = client.get("/assets/%2e%2e/%2e%2e/secret.txt")
    assert response2.status in {403, 404}
    assert b"top-secret" not in response2.body


def test_api_missing_static_404(tmp_path: Path) -> None:
    journal = tmp_path / "agent_traces.jsonl"
    journal.write_text("", encoding="utf-8")
    empty_static = tmp_path / "empty_static"
    empty_static.mkdir()
    server = TraceAPIServer(
        host="127.0.0.1",
        port=0,
        query=TraceQuery(journal),
        static_dir=empty_static,
    )
    server.start_background()
    try:
        client = _ApiClient(f"http://127.0.0.1:{server.port}")
        r = client.get("/")
        assert r.status == 404
        r2 = client.get("/assets/missing.js")
        assert r2.status == 404
    finally:
        server.shutdown()


def test_api_rejects_post(api_bundle) -> None:
    client, server, *_ = api_bundle
    url = f"http://127.0.0.1:{server.port}/api/traces"
    req = urllib.request.Request(url, data=b"{}", method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        urllib.request.urlopen(req, timeout=5)
        raised = False
    except urllib.error.HTTPError as exc:
        raised = True
        assert exc.code == 405
        body = json.loads(exc.read().decode("utf-8"))
        assert body["error"] == "method_not_allowed"
        allow = (exc.headers.get("Allow") or "") if exc.headers else ""
        assert "GET" in allow
        assert "DELETE" in allow
    assert raised


def test_api_list_all(api_bundle) -> None:
    client, *_ = api_bundle
    response = client.get("/api/traces?limit=50")
    assert response.status == 200
    assert response.json["count"] == 2
    # newest-first: t-ok ends later
    assert response.json["traces"][0]["trace_id"] == "t-ok"


def test_api_delete_trace_without_confirm_400(api_bundle) -> None:
    client, *_ = api_bundle
    response = client.delete("/api/traces/t-ok", {"confirm": False})
    assert response.status == 400
    assert response.json["error"] == "confirm_required"

    empty = client.delete("/api/traces/t-ok")
    assert empty.status == 400
    assert empty.json["error"] == "confirm_required"

    wrong = client.delete("/api/traces/t-ok", {"confirm": "yes"})
    assert wrong.status == 400
    assert wrong.json["error"] == "confirm_required"


def test_api_delete_trace_success_and_subsequent_get_404(api_bundle) -> None:
    client, *_ = api_bundle
    before = client.get("/api/traces/t-ok")
    assert before.status == 200

    response = client.delete("/api/traces/t-ok", {"confirm": True})
    assert response.status == 200
    assert response.json["ok"] is True
    assert response.json["trace_id"] == "t-ok"
    assert response.json["removed_events"] >= 1

    after = client.get("/api/traces/t-ok")
    assert after.status == 404
    assert after.json["error"] == "not_found"
    assert after.json["trace_id"] == "t-ok"

    listed = client.get("/api/traces?limit=50")
    assert listed.status == 200
    assert listed.json["count"] == 1
    assert listed.json["traces"][0]["trace_id"] == "t-failed"


def test_api_delete_unknown_trace_404(api_bundle) -> None:
    client, *_ = api_bundle
    response = client.delete("/api/traces/does-not-exist", {"confirm": True})
    assert response.status == 404
    assert response.json["error"] == "not_found"
    assert response.json["trace_id"] == "does-not-exist"


def test_api_delete_path_traversal_404(api_bundle) -> None:
    client, *_ = api_bundle
    response = client.delete("/api/traces/../etc/passwd", {"confirm": True})
    assert response.status == 404
    assert response.json["error"] == "not_found"


def test_api_clear_journal_requires_clear_all(api_bundle) -> None:
    client, *_ = api_bundle
    wrong = client.delete("/api/traces", {"confirm": True})
    assert wrong.status == 400
    assert wrong.json["error"] == "confirm_required"

    empty = client.delete("/api/traces")
    assert empty.status == 400
    assert empty.json["error"] == "confirm_required"

    still_there = client.get("/api/traces?limit=50")
    assert still_there.status == 200
    assert still_there.json["count"] == 2


def test_api_clear_journal_success(api_bundle) -> None:
    client, *_ = api_bundle
    response = client.delete("/api/traces", {"confirm": "CLEAR_ALL"})
    assert response.status == 200
    assert response.json["ok"] is True
    assert response.json["cleared"] is True

    listed = client.get("/api/traces?limit=50")
    assert listed.status == 200
    assert listed.json["count"] == 0

    missing = client.get("/api/traces/t-ok")
    assert missing.status == 404
