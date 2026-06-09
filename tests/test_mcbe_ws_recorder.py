import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools_mcbe_ws_recorder import PacketRecord, RecorderLogger, SessionRecorder, load_replay_packets, replay_delay


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


def test_packet_record_keeps_invalid_json_text_frame() -> None:
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


def test_recorder_logger_prints_timestamp_level_event_and_fields(capsys: pytest.CaptureFixture[str]) -> None:
    logger = RecorderLogger("record")

    logger.info("target_connect_failed", session="session-a", target="ws://127.0.0.1:8080", error="refused")

    output = capsys.readouterr().out.strip()
    assert output.startswith("[")
    assert "] [INFO] [record] target_connect_failed" in output
    assert "session=session-a" in output
    assert "target=ws://127.0.0.1:8080" in output
    assert "error=refused" in output
