"""MCBE WebSocket 数据包录制与回放工具。"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import websockets

Direction = Literal["client_to_server", "server_to_client", "server_to_replay_client"]


class RecorderLogger:
    def __init__(self, component: str) -> None:
        self.component = component

    def info(self, log_event: str, **fields: Any) -> None:
        self._emit("INFO", log_event, fields)

    def warning(self, log_event: str, **fields: Any) -> None:
        self._emit("WARN", log_event, fields)

    def error(self, log_event: str, **fields: Any) -> None:
        self._emit("ERROR", log_event, fields)

    def _emit(self, level: str, log_event: str, fields: dict[str, Any]) -> None:
        timestamp = datetime.now().isoformat(timespec="milliseconds")
        details = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
        suffix = f" {details}" if details else ""
        print(f"[{timestamp}] [{level}] [{self.component}] {log_event}{suffix}")


@dataclass
class PacketRecord:
    seq: int
    timestamp: str
    elapsed_ms: int
    direction: str
    path: str
    raw: str | None
    binary: bool
    bytes: int
    json_valid: bool
    header: dict[str, Any] | None
    body: dict[str, Any] | None
    request_id: str | None
    message_purpose: str | None
    event_name: str | None
    error: str | None = None

    @classmethod
    def from_message(
        cls,
        *,
        seq: int,
        started_at: float,
        now: float,
        direction: str,
        path: str,
        message: str | bytes,
        error: str | None = None,
    ) -> "PacketRecord":
        binary = isinstance(message, bytes)
        raw = None if binary else message
        size = len(message) if binary else len(message.encode("utf-8"))
        json_valid = False
        header: dict[str, Any] | None = None
        body: dict[str, Any] | None = None
        request_id: str | None = None
        message_purpose: str | None = None
        event_name: str | None = None
        parse_error = error

        if not binary:
            try:
                data = json.loads(message)
                json_valid = True
                if isinstance(data, dict):
                    raw_header = data.get("header")
                    raw_body = data.get("body", data)
                    header = raw_header if isinstance(raw_header, dict) else None
                    body = raw_body if isinstance(raw_body, dict) else {"value": raw_body}
                    if header is not None:
                        request_id = header.get("requestId")
                        message_purpose = header.get("messagePurpose")
                        event_name = header.get("eventName") or header.get("EventName")
                else:
                    body = {"value": data}
            except json.JSONDecodeError as exc:
                parse_error = error or f"JSON parse failed: {exc}"

        return cls(
            seq=seq,
            timestamp=datetime.fromtimestamp(now).isoformat(timespec="milliseconds"),
            elapsed_ms=round((now - started_at) * 1000),
            direction=direction,
            path=path,
            raw=raw,
            binary=binary,
            bytes=size,
            json_valid=json_valid,
            header=header,
            body=body,
            request_id=request_id,
            message_purpose=message_purpose,
            event_name=event_name,
            error=parse_error,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PacketRecord":
        return cls(
            seq=data["seq"],
            timestamp=data.get("timestamp", ""),
            elapsed_ms=data.get("elapsed_ms", 0),
            direction=data["direction"],
            path=data.get("path", ""),
            raw=data.get("raw"),
            binary=data.get("binary", False),
            bytes=data.get("bytes", 0),
            json_valid=data.get("json_valid", False),
            header=data.get("header"),
            body=data.get("body"),
            request_id=data.get("request_id"),
            message_purpose=data.get("message_purpose"),
            event_name=data.get("event_name"),
            error=data.get("error"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SessionRecorder:
    def __init__(self, base_dir: str | Path, mode: str, session_id: str | None = None) -> None:
        self.base_dir = Path(base_dir)
        self.mode = mode
        self.session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_dir = self.base_dir / self.session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.started_at = time.time()
        self.started_iso = datetime.fromtimestamp(self.started_at).isoformat(timespec="seconds")
        self.records: list[PacketRecord] = []
        self.direction_counts: Counter[str] = Counter()
        self.total_bytes = 0
        self.errors: list[str] = []
        self._jsonl_file = (self.session_dir / "packets.jsonl").open("a", encoding="utf-8")

    async def record_message(
        self,
        *,
        direction: str,
        path: str,
        message: str | bytes,
        error: str | None = None,
    ) -> PacketRecord:
        record = PacketRecord.from_message(
            seq=len(self.records) + 1,
            started_at=self.started_at,
            now=time.time(),
            direction=direction,
            path=path,
            message=message,
            error=error,
        )
        await self.record_packet(record)
        return record

    async def record_packet(self, record: PacketRecord) -> None:
        self.records.append(record)
        self.direction_counts[record.direction] += 1
        self.total_bytes += record.bytes
        if record.error:
            self.errors.append(f"#{record.seq} {record.direction}: {record.error}")
        self._jsonl_file.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        self._jsonl_file.flush()

    async def record_error(self, message: str) -> None:
        self.errors.append(message)

    async def close(self) -> None:
        ended_at = time.time()
        self._jsonl_file.close()
        packets = [record.to_dict() for record in self.records]
        (self.session_dir / "packets.json").write_text(
            json.dumps(packets, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.session_dir / "SUMMARY.md").write_text(
            self._build_summary(ended_at),
            encoding="utf-8",
        )

    def _build_summary(self, ended_at: float) -> str:
        lines = [
            f"# MCBE WebSocket {self.mode} Session Summary",
            "",
            f"会话 ID: {self.session_id}",
            f"开始时间: {self.started_iso}",
            f"结束时间: {datetime.fromtimestamp(ended_at).isoformat(timespec='seconds')}",
            f"持续时间: {ended_at - self.started_at:.3f}s",
            f"总包数: {len(self.records)}",
            f"总字节数: {self.total_bytes}",
            "",
            "## 方向统计",
            "",
        ]
        if self.direction_counts:
            lines.extend(f"- {direction}: {count}" for direction, count in sorted(self.direction_counts.items()))
        else:
            lines.append("- 无")

        lines.extend(["", "## 错误", ""])
        if self.errors:
            lines.extend(f"- {item}" for item in self.errors)
        else:
            lines.append("- 无")
        lines.append("")
        return "\n".join(lines)


def load_replay_packets(input_path: str | Path) -> list[PacketRecord]:
    path = Path(input_path)
    if path.suffix == ".jsonl":
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        rows = json.loads(path.read_text(encoding="utf-8"))

    packets = [PacketRecord.from_dict(row) for row in rows]
    return [
        packet
        for packet in packets
        if packet.direction == "client_to_server" and not packet.binary and packet.raw is not None
    ]


def replay_delay(previous: PacketRecord | None, current: PacketRecord, speed: float) -> float:
    if previous is None or speed <= 0:
        return 0.0
    elapsed_delta = max(current.elapsed_ms - previous.elapsed_ms, 0) / 1000
    return elapsed_delta / speed


class ProxyRecorder:
    def __init__(self, listen_host: str, listen_port: int, target: str, out_dir: str | Path) -> None:
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.target = target
        self.out_dir = Path(out_dir)
        self.logger = RecorderLogger("record")

    async def start(self) -> None:
        async with websockets.serve(
            self._handle_client,
            self.listen_host,
            self.listen_port,
            ping_interval=None,
        ):
            self.logger.info(
                "proxy_started",
                listen=f"ws://{self.listen_host}:{self.listen_port}",
                target=self.target,
                out=self.out_dir,
            )
            await asyncio.Future()

    async def _handle_client(self, client_ws: WebSocketServerProtocol) -> None:
        recorder = SessionRecorder(self.out_dir, mode="record")
        client_remote = getattr(client_ws, "remote_address", None)
        self.logger.info(
            "client_connected",
            session=recorder.session_id,
            client=client_remote,
            target=self.target,
            dir=recorder.session_dir,
        )
        try:
            self.logger.info("target_connecting", session=recorder.session_id, target=self.target)
            async with websockets.connect(self.target, ping_interval=None) as server_ws:
                self.logger.info("target_connected", session=recorder.session_id, target=self.target)
                client_to_server = asyncio.create_task(
                    self._forward(
                        source=client_ws,
                        target=server_ws,
                        recorder=recorder,
                        direction="client_to_server",
                        path="mcbe->proxy->server",
                    )
                )
                server_to_client = asyncio.create_task(
                    self._forward(
                        source=server_ws,
                        target=client_ws,
                        recorder=recorder,
                        direction="server_to_client",
                        path="server->proxy->mcbe",
                    )
                )
                done, pending = await asyncio.wait(
                    {client_to_server, server_to_client},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    exc = task.exception()
                    if exc is not None:
                        await recorder.record_error(str(exc))
                        self.logger.error("forward_task_failed", session=recorder.session_id, error=exc)
        except Exception as exc:
            await recorder.record_error(f"目标服务端连接失败或会话异常: {exc}")
            self.logger.error(
                "target_connect_failed",
                session=recorder.session_id,
                target=self.target,
                error=exc,
            )
        finally:
            await recorder.close()
            self.logger.info(
                "session_closed",
                session=recorder.session_id,
                packets=len(recorder.records),
                bytes=recorder.total_bytes,
                errors=len(recorder.errors),
                dir=recorder.session_dir,
            )

    async def _forward(
        self,
        *,
        source: Any,
        target: Any,
        recorder: SessionRecorder,
        direction: Direction,
        path: str,
    ) -> None:
        try:
            async for message in source:
                record = await recorder.record_message(direction=direction, path=path, message=message)
                self.logger.info(
                    "packet_forwarded",
                    session=recorder.session_id,
                    seq=record.seq,
                    direction=direction,
                    bytes=record.bytes,
                    json=record.json_valid,
                    purpose=record.message_purpose,
                    event=record.event_name,
                    request_id=record.request_id,
                )
                await target.send(message)
        except websockets.exceptions.ConnectionClosed as exc:
            self.logger.info(
                "endpoint_closed",
                session=recorder.session_id,
                direction=direction,
                code=getattr(exc, "code", None),
                reason=getattr(exc, "reason", None),
            )
            return
        except Exception as exc:
            await recorder.record_error(f"{direction} 转发失败: {exc}")
            self.logger.error("packet_forward_failed", session=recorder.session_id, direction=direction, error=exc)
            raise


class ReplayClient:
    def __init__(self, input_path: str | Path, target: str, speed: float, out_dir: str | Path) -> None:
        self.input_path = Path(input_path)
        self.target = target
        self.speed = speed
        self.out_dir = Path(out_dir)
        self.logger = RecorderLogger("replay")

    async def run(self) -> None:
        packets = load_replay_packets(self.input_path)
        recorder = SessionRecorder(self.out_dir, mode="replay")
        self.logger.info(
            "replay_started",
            session=recorder.session_id,
            input=self.input_path,
            target=self.target,
            speed=self.speed,
            packets=len(packets),
            dir=recorder.session_dir,
        )
        receiver: asyncio.Task[None] | None = None
        try:
            async with websockets.connect(self.target, ping_interval=None) as websocket:
                self.logger.info("target_connected", session=recorder.session_id, target=self.target)
                receiver = asyncio.create_task(self._receive_responses(websocket, recorder))
                previous: PacketRecord | None = None
                for packet in packets:
                    delay = replay_delay(previous, packet, self.speed)
                    if delay > 0:
                        await asyncio.sleep(delay)
                    await websocket.send(packet.raw)
                    record = await recorder.record_message(
                        direction="client_to_server",
                        path="replay->server",
                        message=packet.raw or "",
                    )
                    self.logger.info(
                        "packet_replayed",
                        session=recorder.session_id,
                        source_seq=packet.seq,
                        seq=record.seq,
                        bytes=record.bytes,
                        delay=f"{delay:.3f}s",
                        purpose=record.message_purpose,
                        event=record.event_name,
                        request_id=record.request_id,
                    )
                    previous = packet
                await asyncio.sleep(0.2)
        except Exception as exc:
            await recorder.record_error(f"回放异常: {exc}")
            self.logger.error("replay_failed", session=recorder.session_id, target=self.target, error=exc)
        finally:
            if receiver is not None:
                receiver.cancel()
                await asyncio.gather(receiver, return_exceptions=True)
            await recorder.close()
            self.logger.info(
                "replay_closed",
                session=recorder.session_id,
                packets=len(recorder.records),
                bytes=recorder.total_bytes,
                errors=len(recorder.errors),
                dir=recorder.session_dir,
            )

    async def _receive_responses(self, websocket: Any, recorder: SessionRecorder) -> None:
        try:
            async for message in websocket:
                record = await recorder.record_message(
                    direction="server_to_replay_client",
                    path="server->replay",
                    message=message,
                )
                self.logger.info(
                    "response_received",
                    session=recorder.session_id,
                    seq=record.seq,
                    bytes=record.bytes,
                    json=record.json_valid,
                    purpose=record.message_purpose,
                    event=record.event_name,
                    request_id=record.request_id,
                )
        except websockets.exceptions.ConnectionClosed as exc:
            self.logger.info(
                "target_closed",
                session=recorder.session_id,
                code=getattr(exc, "code", None),
                reason=getattr(exc, "reason", None),
            )
            return


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MCBE WebSocket 数据包录制与回放工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser("record", help="启动透明 WebSocket 代理并记录双向数据包")
    record.add_argument("--listen-host", default="0.0.0.0")
    record.add_argument("--listen-port", type=int, default=18080)
    record.add_argument("--target", default="ws://127.0.0.1:8080")
    record.add_argument("--out", default="test_logs/ws_records")

    replay = subparsers.add_parser("replay-server", help="回放录制文件中的客户端侧数据包")
    replay.add_argument("--input", required=True)
    replay.add_argument("--target", default="ws://127.0.0.1:8080")
    replay.add_argument("--speed", type=float, default=1.0)
    replay.add_argument("--out", default="test_logs/ws_replays")

    return parser


async def async_main(args: argparse.Namespace) -> None:
    if args.command == "record":
        await ProxyRecorder(args.listen_host, args.listen_port, args.target, args.out).start()
    elif args.command == "replay-server":
        await ReplayClient(args.input, args.target, args.speed, args.out).run()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
