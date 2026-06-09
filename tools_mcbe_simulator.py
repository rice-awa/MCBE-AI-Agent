"""MCBE WebSocket 与 Addon bridge 模拟客户端。"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import websockets

from services.addon.protocol import BRIDGE_TOOL_PLAYER_NAME
from tools_mcbe_ws_recorder import PacketRecord, RecorderLogger, SessionRecorder, load_replay_packets, replay_delay

MCBE_VERSION = 17104896
COMMAND_VERSION = 17039360


ITEM_NAME_MAP = {
    "diamond": "钻石",
}


def json_dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"


def build_player_message(
    sender: str,
    message: str,
    *,
    receiver: str = "",
    message_type: str = "chat",
) -> str:
    return json_dumps(
        {
            "body": {
                "message": message,
                "receiver": receiver,
                "sender": sender,
                "type": message_type,
            },
            "header": {
                "eventName": "PlayerMessage",
                "messagePurpose": "event",
                "version": MCBE_VERSION,
            },
        }
    )


def build_command_response(request_id: str, body: dict[str, Any]) -> str:
    return json_dumps(
        {
            "header": {
                "messagePurpose": "commandResponse",
                "requestId": request_id,
                "version": MCBE_VERSION,
            },
            "body": body,
        }
    )


def command_kind(command_line: str) -> str:
    if command_line.startswith("scriptevent mcbeai:bridge_request "):
        return "scriptevent_bridge_request"
    if command_line.startswith("scriptevent mcbeai:ai_resp "):
        return "scriptevent_ai_resp"
    if command_line.startswith("scriptevent "):
        return "scriptevent"
    return command_line.split(maxsplit=1)[0] if command_line.strip() else "empty"


def load_packet_records(input_path: str | Path) -> list[PacketRecord]:
    path = Path(input_path)
    if path.suffix == ".jsonl":
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        rows = json.loads(path.read_text(encoding="utf-8"))
    return [PacketRecord.from_dict(row) for row in rows]


def inspect_record(input_path: str | Path) -> dict[str, Any]:
    packets = load_packet_records(input_path)
    requests: dict[str, PacketRecord] = {}
    responses: dict[str, PacketRecord] = {}
    command_kinds: dict[str, int] = {}

    for packet in packets:
        if packet.message_purpose == "commandRequest" and packet.request_id:
            requests[packet.request_id] = packet
            command_line = packet.body.get("commandLine") if isinstance(packet.body, dict) else ""
            kind = command_kind(command_line) if isinstance(command_line, str) else "unknown"
            command_kinds[kind] = command_kinds.get(kind, 0) + 1
        elif packet.message_purpose == "commandResponse" and packet.request_id:
            responses[packet.request_id] = packet

    matched = sorted(set(requests) & set(responses))
    unmatched = sorted(set(requests) - set(responses))
    return {
        "command_requests": len(requests),
        "command_responses": len(responses),
        "matched_request_ids": len(matched),
        "unmatched_request_ids": unmatched,
        "command_kinds": command_kinds,
    }


def load_scenario_messages(scenario_path: str | Path) -> list[str]:
    data = json.loads(Path(scenario_path).read_text(encoding="utf-8"))
    steps = data.get("steps", []) if isinstance(data, dict) else []
    messages: list[str] = []
    for step in steps:
        if isinstance(step, str):
            messages.append(step)
        elif isinstance(step, dict):
            message = step.get("send_player_message", step.get("message"))
            if isinstance(message, str):
                messages.append(message)
    return messages


class AddonBridgeSimulator:
    def handle_request(self, payload: dict[str, Any]) -> str:
        request_id = str(payload.get("request_id") or "")
        capability = payload.get("capability")
        capability_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}

        if capability == "run_world_command":
            command = str(capability_payload.get("command") or "")
            response_payload = self._handle_run_world_command(command)
        else:
            response_payload = {
                "ok": False,
                "error": f"Unsupported capability: {capability}",
            }

        encoded_payload = json.dumps(response_payload, ensure_ascii=False, separators=(",", ":"))
        return build_player_message(
            BRIDGE_TOOL_PLAYER_NAME,
            f"MCBEAI|RESP|{request_id}|1/1|{encoded_payload}",
            message_type="chat",
        )

    def _handle_run_world_command(self, command: str) -> dict[str, Any]:
        body = McbeCommandSimulator.command_result_for(command)
        return {"ok": body.get("statusCode") == 0, "payload": body}


class McbeCommandSimulator:
    def __init__(
        self,
        *,
        player_name: str,
        addon_bridge: AddonBridgeSimulator | None = None,
        emit_tellraw_echo: bool = True,
        emit_say_event: bool = True,
    ) -> None:
        self.player_name = player_name
        self.addon_bridge = addon_bridge or AddonBridgeSimulator()
        self.emit_tellraw_echo = emit_tellraw_echo
        self.emit_say_event = emit_say_event

    def handle_command(self, command_line: str, request_id: str) -> list[str]:
        if command_line.startswith("scriptevent mcbeai:bridge_request "):
            return self._handle_bridge_request(command_line, request_id)
        if command_line.startswith("scriptevent mcbeai:ai_resp "):
            return [
                build_command_response(
                    request_id,
                    {"statusCode": 0, "statusMessage": "Script event mcbeai:ai_resp has been sent"},
                )
            ]
        if command_line.startswith("scriptevent "):
            return [
                build_command_response(
                    request_id,
                    {"statusCode": 0, "statusMessage": "Script event has been sent"},
                )
            ]
        if command_line.startswith("tellraw "):
            responses = [
                build_command_response(
                    request_id,
                    {"recipient": [self.player_name, BRIDGE_TOOL_PLAYER_NAME], "statusCode": 0},
                )
            ]
            if self.emit_tellraw_echo:
                rawtext = command_line.split(" ", 2)[2] if len(command_line.split(" ", 2)) == 3 else ""
                responses.append(build_player_message("外部", rawtext + "\n", receiver=self.player_name, message_type="tell"))
            return responses
        if command_line.startswith("say "):
            text = command_line.removeprefix("say ")
            responses = [build_command_response(request_id, {"message": text, "statusCode": 0})]
            if self.emit_say_event:
                responses.append(build_player_message("外部", f"[外部] {text}", message_type="say"))
            return responses

        return [build_command_response(request_id, self.command_result_for(command_line))]

    def _handle_bridge_request(self, command_line: str, request_id: str) -> list[str]:
        raw_payload = command_line.removeprefix("scriptevent mcbeai:bridge_request ")
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            return [
                build_command_response(
                    request_id,
                    {"statusCode": -1, "statusMessage": "Invalid bridge request payload"},
                )
            ]
        return [
            build_command_response(
                request_id,
                {"statusCode": 0, "statusMessage": "Script event mcbeai:bridge_request has been sent"},
            ),
            self.addon_bridge.handle_request(payload),
        ]

    @staticmethod
    def command_result_for(command_line: str) -> dict[str, Any]:
        if command_line.startswith("give "):
            return McbeCommandSimulator._give_result(command_line)
        return {"statusCode": -1, "statusMessage": f"Simulated command failed: unsupported command: {command_line}"}

    @staticmethod
    def _give_result(command_line: str) -> dict[str, Any]:
        parts = command_line.split()
        if len(parts) < 4:
            return {"statusCode": -1, "statusMessage": "Simulated command failed: invalid give command"}
        _, player_name, item_id, amount_text = parts[:4]
        try:
            amount = int(amount_text)
        except ValueError:
            return {"statusCode": -1, "statusMessage": "Simulated command failed: invalid item amount"}
        item_name = ITEM_NAME_MAP.get(item_id, item_id)
        return {
            "itemAmount": amount,
            "itemName": item_name,
            "playerName": [player_name],
            "statusCode": 0,
            "statusMessage": f"给予 {player_name}  {item_name} * {amount} 效果",
        }


class CommandRequestDispatcher:
    def __init__(self, simulator: McbeCommandSimulator) -> None:
        self.simulator = simulator

    def handle(self, message: dict[str, Any]) -> list[str]:
        header = message.get("header") if isinstance(message, dict) else None
        body = message.get("body") if isinstance(message, dict) else None
        if not isinstance(header, dict) or not isinstance(body, dict):
            return []
        if header.get("messagePurpose") != "commandRequest":
            return []
        request_id = header.get("requestId")
        command_line = body.get("commandLine")
        if not isinstance(request_id, str) or not isinstance(command_line, str):
            return []
        return self.simulator.handle_command(command_line, request_id)


class SimulatedMcbeClient:
    def __init__(
        self,
        *,
        target: str,
        out_dir: str | Path,
        player_name: str,
        emit_tellraw_echo: bool = True,
        emit_say_event: bool = True,
    ) -> None:
        self.target = target
        self.out_dir = Path(out_dir)
        self.player_name = player_name
        self.logger = RecorderLogger("simulate")
        self.dispatcher = CommandRequestDispatcher(
            McbeCommandSimulator(
                player_name=player_name,
                emit_tellraw_echo=emit_tellraw_echo,
                emit_say_event=emit_say_event,
            )
        )

    async def run_messages(self, messages: list[str], speed: float = 1.0, wait_after: float = 1.0) -> None:
        recorder = SessionRecorder(self.out_dir, mode="simulation")
        receiver: asyncio.Task[None] | None = None
        self.logger.info(
            "simulation_started",
            session=recorder.session_id,
            target=self.target,
            player=self.player_name,
            messages=len(messages),
            dir=recorder.session_dir,
        )
        try:
            async with websockets.connect(self.target, ping_interval=None) as websocket:
                receiver = asyncio.create_task(self._receive_and_respond(websocket, recorder))
                for message in messages:
                    payload = build_player_message(self.player_name, message)
                    await websocket.send(payload)
                    record = await recorder.record_message(
                        direction="client_to_server",
                        path="sim->server",
                        message=payload,
                    )
                    self.logger.info(
                        "player_message_sent",
                        session=recorder.session_id,
                        seq=record.seq if record else None,
                        message=message,
                    )
                    if speed > 0:
                        await asyncio.sleep(0.2 / speed)
                await asyncio.sleep(max(wait_after, 0.0))
        except Exception as exc:
            await recorder.record_error(f"模拟异常: {exc}")
            self.logger.error("simulation_failed", session=recorder.session_id, error=exc)
        finally:
            if receiver is not None:
                receiver.cancel()
                await asyncio.gather(receiver, return_exceptions=True)
            await recorder.close()
            self.logger.info(
                "simulation_closed",
                session=recorder.session_id,
                packets=len(recorder.records),
                errors=len(recorder.errors),
                dir=recorder.session_dir,
            )

    async def replay_record(self, input_path: str | Path, speed: float, wait_after: float = 1.0) -> None:
        packets = load_replay_packets(input_path)
        messages = [str(packet.body.get("message")) for packet in packets if isinstance(packet.body, dict)]
        await self.run_messages(messages, speed=speed, wait_after=wait_after)

    async def _receive_and_respond(self, websocket: Any, recorder: SessionRecorder) -> None:
        async for message in websocket:
            await recorder.record_message(direction="server_to_replay_client", path="server->sim", message=message)
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue
            for response in self.dispatcher.handle(data):
                await websocket.send(response)
                record = await recorder.record_message(direction="client_to_server", path="sim->server", message=response)
                self.logger.info(
                    "sim_response_sent",
                    session=recorder.session_id,
                    seq=record.seq if record else None,
                )


async def async_main(args: argparse.Namespace) -> None:
    if args.command == "inspect-record":
        print(json.dumps(inspect_record(args.input), ensure_ascii=False, indent=2))
        return

    client = SimulatedMcbeClient(
        target=args.target,
        out_dir=args.out,
        player_name=args.player,
        emit_tellraw_echo=not args.no_tellraw_echo,
        emit_say_event=not args.no_say_event,
    )
    if args.command == "simulate-client":
        messages = list(args.message or [])
        if args.scenario:
            messages.extend(load_scenario_messages(args.scenario))
        await client.run_messages(messages, speed=args.speed, wait_after=args.wait_after)
    elif args.command == "replay-record-with-simulation":
        await client.replay_record(args.input, speed=args.speed, wait_after=args.wait_after)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MCBE WebSocket 与 Addon bridge 模拟客户端")
    subparsers = parser.add_subparsers(dest="command", required=True)

    simulate = subparsers.add_parser("simulate-client", help="发送玩家消息并模拟 MCBE/Add-on 响应")
    simulate.add_argument("--target", default="ws://127.0.0.1:8080")
    simulate.add_argument("--player", default="fantong7038")
    simulate.add_argument("--message", action="append", default=[])
    simulate.add_argument("--scenario")
    simulate.add_argument("--speed", type=float, default=1.0)
    simulate.add_argument("--wait-after", type=float, default=1.0)
    simulate.add_argument("--out", default="test_logs/ws_simulations")
    simulate.add_argument("--no-tellraw-echo", action="store_true")
    simulate.add_argument("--no-say-event", action="store_true")

    replay = subparsers.add_parser("replay-record-with-simulation", help="回放真实玩家输入并模拟命令响应")
    replay.add_argument("--input", required=True)
    replay.add_argument("--target", default="ws://127.0.0.1:8080")
    replay.add_argument("--player", default="fantong7038")
    replay.add_argument("--speed", type=float, default=1.0)
    replay.add_argument("--wait-after", type=float, default=1.0)
    replay.add_argument("--out", default="test_logs/ws_simulations")
    replay.add_argument("--no-tellraw-echo", action="store_true")
    replay.add_argument("--no-say-event", action="store_true")

    inspect = subparsers.add_parser("inspect-record", help="分析录制文件中的 commandRequest/commandResponse 配对")
    inspect.add_argument("--input", required=True)
    inspect.set_defaults(target="", out="test_logs/ws_simulations", player="fantong7038", no_tellraw_echo=False, no_say_event=False)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
