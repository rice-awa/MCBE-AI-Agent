import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools_mcbe_ws_recorder
from tools_mcbe_ws_recorder import PacketRecord, ProxyRecorder, RecorderLogger, SessionRecorder, load_replay_packets, replay_delay


def test_packet_record_preserves_json_text_frame() -> None:
    raw = json.dumps(
        {
            "header": {
                "requestId": "req-1",
                "messagePurpose": "event",
                "eventName": "PlayerMessage",
            },
            "body": {"sender": "Steve", "message": "hello"},
        },
        ensure_ascii=False,
    )

    record = PacketRecord.from_message(
        seq=1,
        started_at=100.0,
        now=100.25,
        direction="client_to_server",
        path="mcbe->proxy->server",
        message=raw,
    )

    assert record.raw == raw
    assert record.binary is False
    assert record.bytes == len(raw.encode("utf-8"))
    assert record.json_valid is True
    assert record.header["requestId"] == "req-1"
    assert record.body["sender"] == "Steve"
    assert record.request_id == "req-1"
    assert record.message_purpose == "event"
    assert record.event_name == "PlayerMessage"
    assert record.elapsed_ms == 250


def test_packet_record_accepts_result_handshake_json() -> None:
    record = PacketRecord.from_message(
        seq=1,
        started_at=0.0,
        now=0.0,
        direction="server_to_client",
        path="server->proxy->mcbe",
        message='{"Result": "true"}',
    )

    assert record.json_valid is True
    assert record.header is None
    assert record.body == {"Result": "true"}
    assert record.message_purpose is None
    assert record.error is None


def test_packet_record_accepts_command_response_json() -> None:
    raw = json.dumps(
        {
            "header": {"requestId": "req-2", "messagePurpose": "commandResponse"},
            "body": {"statusCode": 0},
        }
    )

    record = PacketRecord.from_message(
        seq=1,
        started_at=0.0,
        now=0.0,
        direction="client_to_server",
        path="mcbe->proxy->server",
        message=raw,
    )

    assert record.json_valid is True
    assert record.header == {"requestId": "req-2", "messagePurpose": "commandResponse"}
    assert record.body == {"statusCode": 0}
    assert record.request_id == "req-2"
    assert record.message_purpose == "commandResponse"
    assert record.error is None
    record = PacketRecord.from_message(
        seq=1,
        started_at=10.0,
        now=10.0,
        direction="client_to_server",
        path="mcbe->proxy->server",
        message="not-json",
    )

    assert record.raw == "not-json"
    assert record.binary is False
    assert record.bytes == len(b"not-json")
    assert record.json_valid is False
    assert record.header is None
    assert record.body is None
    assert "JSON" in record.error


def test_load_replay_packets_selects_client_text_packets_only(tmp_path: Path) -> None:
    input_file = tmp_path / "packets.jsonl"
    rows = [
        {"seq": 1, "direction": "server_to_client", "binary": False, "raw": "server"},
        {"seq": 2, "direction": "client_to_server", "binary": True, "raw": None},
        {"seq": 3, "direction": "client_to_server", "binary": False, "raw": "first"},
        {"seq": 4, "direction": "client_to_server", "binary": False, "raw": "second"},
    ]
    input_file.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    packets = load_replay_packets(input_file)

    assert [packet.seq for packet in packets] == [3, 4]
    assert [packet.raw for packet in packets] == ["first", "second"]


def test_replay_delay_respects_speed() -> None:
    previous = PacketRecord.from_message(
        seq=1,
        started_at=0.0,
        now=0.0,
        direction="client_to_server",
        path="mcbe->proxy->server",
        message="first",
    )
    current = PacketRecord.from_message(
        seq=2,
        started_at=0.0,
        now=1.5,
        direction="client_to_server",
        path="mcbe->proxy->server",
        message="second",
    )

    assert replay_delay(previous, current, speed=1.0) == pytest.approx(1.5)
    assert replay_delay(previous, current, speed=2.0) == pytest.approx(0.75)
    assert replay_delay(previous, current, speed=0.0) == 0.0


@pytest.mark.asyncio
async def test_session_recorder_writes_jsonl_json_and_summary(tmp_path: Path) -> None:
    recorder = SessionRecorder(base_dir=tmp_path, mode="record", session_id="session-a")
    await recorder.record_message(
        direction="client_to_server",
        path="mcbe->proxy->server",
        message='{"header":{"requestId":"req-1"},"body":{}}',
    )
    await recorder.record_message(
        direction="server_to_client",
        path="server->proxy->mcbe",
        message=b"\x01\x02",
    )

    await recorder.close()

    session_dir = tmp_path / "session-a"
    jsonl_rows = [json.loads(line) for line in (session_dir / "packets.jsonl").read_text(encoding="utf-8").splitlines()]
    json_rows = json.loads((session_dir / "packets.json").read_text(encoding="utf-8"))
    summary = (session_dir / "SUMMARY.md").read_text(encoding="utf-8")

    assert [row["seq"] for row in jsonl_rows] == [1, 2]
    assert json_rows == jsonl_rows
    assert "client_to_server: 1" in summary
    assert "server_to_client: 1" in summary
    assert "总包数: 2" in summary


def test_recorder_logger_prints_timestamp_level_event_and_fields(capsys: pytest.CaptureFixture[str]) -> None:
    logger = RecorderLogger("record")

    logger.info("target_connect_failed", session="session-a", target="ws://127.0.0.1:8080", error="refused")

    output = capsys.readouterr().out.strip()
    assert output.startswith("[")
    assert "] [INFO] [record] target_connect_failed" in output
    assert "session=session-a" in output
    assert "target=ws://127.0.0.1:8080" in output


def test_recorder_logger_allows_protocol_event_field(capsys: pytest.CaptureFixture[str]) -> None:
    logger = RecorderLogger("record")

    logger.info("packet_forwarded", session="session-a", event="PlayerMessage")

    output = capsys.readouterr().out.strip()
    assert "packet_forwarded" in output
    assert "event=PlayerMessage" in output


@pytest.mark.asyncio
async def test_proxy_recorder_start_uses_websockets_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {}

    class FakeServer:
        async def __aenter__(self) -> "FakeServer":
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    def fake_serve(handler: object, host: str, port: int, ping_interval: object) -> FakeServer:
        called["host"] = host
        called["port"] = port
        called["ping_interval"] = ping_interval
        return FakeServer()

    class StopAfterStartFuture:
        def __await__(self):
            raise asyncio.CancelledError
            yield

    monkeypatch.setattr(tools_mcbe_ws_recorder.websockets, "serve", fake_serve)
    monkeypatch.setattr(tools_mcbe_ws_recorder.asyncio, "Future", StopAfterStartFuture)

    with pytest.raises(asyncio.CancelledError):
        await ProxyRecorder("127.0.0.1", 18080, "ws://127.0.0.1:8080", "out").start()

    assert called == {"host": "127.0.0.1", "port": 18080, "ping_interval": None}
