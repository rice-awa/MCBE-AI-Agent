import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools_mcbe_simulator import (
    AddonBridgeSimulator,
    CommandRequestDispatcher,
    McbeCommandSimulator,
    build_player_message,
    inspect_record,
    load_scenario,
    load_scenario_messages,
    resolve_player_name,
)


def _command_request(command_line: str, request_id: str = "req-1") -> dict:
    return {
        "header": {
            "requestId": request_id,
            "messagePurpose": "commandRequest",
            "version": 1,
            "EventName": "commandRequest",
        },
        "body": {
            "origin": {"type": "player"},
            "commandLine": command_line,
            "version": 17039360,
        },
    }


def test_build_player_message_uses_mcbe_event_shape() -> None:
    message = build_player_message("fantong7038", "AI chat hi")
    data = json.loads(message)

    assert data["header"]["messagePurpose"] == "event"
    assert data["header"]["eventName"] == "PlayerMessage"
    assert data["body"] == {
        "message": "AI chat hi",
        "receiver": "",
        "sender": "fantong7038",
        "type": "chat",
    }


def test_dispatcher_returns_command_response_with_same_request_id() -> None:
    dispatcher = CommandRequestDispatcher(McbeCommandSimulator(player_name="fantong7038"))

    responses = dispatcher.handle(_command_request("scriptevent mcbeai:ai_resp {}", "req-script"))
    data = json.loads(responses[0])

    assert data["header"]["messagePurpose"] == "commandResponse"
    assert data["header"]["requestId"] == "req-script"
    assert data["body"]["statusCode"] == 0
    assert data["body"]["statusMessage"] == "Script event mcbeai:ai_resp has been sent"


def test_give_command_response_matches_recorded_mcbe_shape() -> None:
    simulator = McbeCommandSimulator(player_name="tester")

    responses = simulator.handle_command("give tester diamond 64", "req-give")
    data = json.loads(responses[0])

    assert data["header"]["requestId"] == "req-give"
    assert data["body"]["statusCode"] == 0
    assert data["body"]["itemAmount"] == 64
    assert data["body"]["itemName"] == "钻石"
    assert data["body"]["playerName"] == ["tester"]
    assert data["body"]["statusMessage"].startswith("给予 tester")


def test_say_command_emits_command_response_and_player_message() -> None:
    simulator = McbeCommandSimulator(player_name="tester")

    responses = simulator.handle_command("say hi", "req-say")
    command_response = json.loads(responses[0])
    event = json.loads(responses[1])

    assert command_response["body"] == {"message": "hi", "statusCode": 0}
    assert event["header"]["eventName"] == "PlayerMessage"
    assert event["body"]["sender"] == "外部"
    assert event["body"]["message"] == "[外部] hi"


def test_common_mc_commands_return_successful_responses() -> None:
    simulator = McbeCommandSimulator(player_name="tester")
    commands = [
        "summon sheep ~ ~1 ~",
        "tp tester ~ ~1 ~",
        "teleport tester 0 80 0",
        "time set day",
        "weather clear",
        "effect tester speed 30 1",
        "gamemode creative tester",
        "setblock ~ ~-1 ~ stone",
        "fill ~ ~ ~ ~1 ~1 ~1 air",
        "kill @e[type=sheep,c=1]",
        "clear tester",
        "title tester title hello",
        "playsound random.orb tester",
        "particle minecraft:basic_flame_particle ~ ~1 ~",
        "locate structure village",
    ]

    for command in commands:
        response = json.loads(simulator.handle_command(command, "req-common")[0])
        assert response["body"]["statusCode"] == 0, command
        assert "Simulated command failed" not in response["body"].get("statusMessage", "")


def test_load_scenario_reads_player_and_messages(tmp_path: Path) -> None:
    scenario = tmp_path / "scenario.json"
    scenario.write_text(
        json.dumps(
            {
                "player": "tester",
                "simulation": {"command_response_delay_ms": 50},
                "steps": [
                    {"send_player_message": "AI chat hi"},
                    {"message": "帮助"},
                    "运行命令 say hi",
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    loaded = load_scenario(scenario)

    assert loaded.player == "tester"
    assert loaded.messages == ["AI chat hi", "帮助", "运行命令 say hi"]
    assert loaded.command_response_delay == 0.05
    assert load_scenario_messages(scenario) == loaded.messages


def test_resolve_player_name_prefers_scenario_player() -> None:
    assert resolve_player_name(cli_player="fantong7038", scenario_player="tester") == "tester"
    assert resolve_player_name(cli_player="tester", scenario_player=None) == "tester"


def test_inspect_record_pairs_command_requests_and_responses(tmp_path: Path) -> None:
    input_file = tmp_path / "packets.jsonl"
    rows = [
        {
            "seq": 1,
            "direction": "server_to_client",
            "binary": False,
            "raw": "{}",
            "message_purpose": "commandRequest",
            "request_id": "req-1",
            "body": {"commandLine": "say hi"},
        },
        {
            "seq": 2,
            "direction": "client_to_server",
            "binary": False,
            "raw": "{}",
            "message_purpose": "commandResponse",
            "request_id": "req-1",
            "body": {"statusCode": 0},
        },
    ]
    input_file.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")

    stats = inspect_record(input_file)

    assert stats["command_requests"] == 1
    assert stats["command_responses"] == 1
    assert stats["matched_request_ids"] == 1
    assert stats["unmatched_request_ids"] == []
    assert stats["command_kinds"] == {"say": 1}


def test_bridge_snapshot_capabilities_return_success_payloads() -> None:
    bridge = AddonBridgeSimulator(player_name="tester")
    capabilities = [
        ("get_inventory_snapshot", {"target": "tester"}, "inventory"),
        ("get_player_snapshot", {"target": "tester"}, "players"),
        ("find_entities", {"entity_type": "sheep", "radius": 16}, "entities"),
    ]

    for capability, capability_payload, expected_key in capabilities:
        response = bridge.handle_request(
            {
                "request_id": f"addon-{capability}",
                "capability": capability,
                "payload": capability_payload,
            }
        )
        event = json.loads(response)
        encoded_payload = event["body"]["message"].split("|", 4)[4]
        payload = json.loads(encoded_payload)

        assert payload["ok"] is True
        assert expected_key in payload["payload"]
    simulator = McbeCommandSimulator(player_name="tester", addon_bridge=AddonBridgeSimulator())
    bridge_payload = {
        "request_id": "addon-req-1",
        "capability": "run_world_command",
        "payload": {"command": "summon sheep ~ ~1 ~"},
    }

    responses = simulator.handle_command(
        "scriptevent mcbeai:bridge_request " + json.dumps(bridge_payload, ensure_ascii=False),
        "req-bridge",
    )
    command_response = json.loads(responses[0])
    bridge_event = json.loads(responses[1])

    assert command_response["header"]["requestId"] == "req-bridge"
    assert command_response["body"]["statusCode"] == 0
    assert bridge_event["body"]["sender"] == "MCBEAI_TOOL"
    assert bridge_event["body"]["message"].startswith("MCBEAI|RESP|addon-req-1|1/1|")
    encoded_payload = bridge_event["body"]["message"].split("|", 4)[4]
    payload = json.loads(encoded_payload)
    assert payload["ok"] is True
    assert payload["payload"]["statusCode"] == 0
    assert payload["payload"]["statusMessage"].startswith("已模拟执行命令: summon")
