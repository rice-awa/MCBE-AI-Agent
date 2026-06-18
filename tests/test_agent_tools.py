"""Agent 工具函数测试"""

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest
from pydantic_ai import Agent

from config.settings import LLMProviderConfig, Settings
from models.agent import AgentDependencies
from models.minecraft import MinecraftCommand
from services.agent.harness.prompting import render_schema_description_prefix
from services.agent.tools import (
    build_actionbar_command,
    build_tellraw_command,
    build_title_commands,
    escape_command_text,
    register_agent_tools,
)


def test_schema_description_prefix_contains_catalog_fields() -> None:
    prefix = render_schema_description_prefix("run_minecraft_command")

    assert "[运行时 Harness]" in prefix
    assert "意图: 改变世界" in prefix
    assert "风险: 高" in prefix
    assert "适用:" in prefix
    assert "禁用:" in prefix
    assert "参数:" in prefix


def test_registered_tool_description_includes_runtime_harness_prefix() -> None:
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent, settings=Settings())

    description = agent._function_toolset.tools["run_minecraft_command"].description

    assert description is not None
    assert description.startswith("[运行时 Harness]")
    assert "执行 Minecraft 命令" in description


def test_registered_tool_description_can_skip_runtime_harness_prefix() -> None:
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    settings = Settings(runtime_harness_schema_enabled=False)
    register_agent_tools(agent, settings=settings)

    description = agent._function_toolset.tools["run_minecraft_command"].description

    assert description is not None
    assert not description.startswith("[运行时 Harness]")
    assert "执行 Minecraft 命令" in description


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeHttpClient:
    def __init__(self, payload: dict):
        self.payload = payload
        self.requests: list[tuple[str, dict | None]] = []

    async def get(self, url: str, params: dict | None = None):
        self.requests.append((url, params))
        return _FakeResponse(self.payload)


class _NarrowRuntimeSettings:
    default_provider = "deepseek"
    system_prompt = "test prompt"
    stream_sentence_mode = True
    mcwiki_base_url = "https://wiki.example.test"
    runtime_harness_enabled = True
    runtime_harness_prompt_enabled = True
    runtime_harness_schema_enabled = False
    runtime_harness_audit_enabled = False
    runtime_harness_audit_path = "unused.jsonl"
    runtime_harness_audit_max_records = 1

    def get_provider_config(self, provider_name: str | None = None) -> LLMProviderConfig:
        return LLMProviderConfig(
            name=provider_name or self.default_provider,
            api_key="test-key",
            model="test-model",
            enabled=True,
        )

    def list_available_providers(self) -> list[str]:
        return ["deepseek", "ollama"]


async def _noop_send_to_game(message: str) -> None:
    return None


async def _noop_command(_: str) -> str:
    return "ok"


@pytest.mark.asyncio
async def test_agent_tools_accept_narrow_runtime_settings() -> None:
    settings = _NarrowRuntimeSettings()
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent, settings=settings)
    http_client = _FakeHttpClient(
        {
            "success": True,
            "data": {
                "results": [
                    {
                        "title": "Stone",
                        "url": "https://wiki.example.test/Stone",
                        "snippet": "block",
                    }
                ]
            },
        }
    )
    deps = AgentDependencies(
        connection_id=uuid4(),
        player_name="Alex",
        settings=settings,
        http_client=http_client,
        send_to_game=_noop_send_to_game,
        run_command=_noop_command,
    )
    ctx = SimpleNamespace(deps=deps)

    providers = await agent._function_toolset.tools["list_available_providers"].function(ctx)
    search = await agent._function_toolset.tools["mcwiki_search"].function(ctx, "stone")

    assert providers == "可用 Provider: deepseek, ollama"
    assert "Stone" in search
    assert http_client.requests[0][0] == "https://wiki.example.test/api/search"


@pytest.mark.asyncio
async def test_registered_tool_function_writes_audit_jsonl(tmp_path) -> None:
    audit_path = tmp_path / "tools.jsonl"
    settings = Settings(runtime_harness_audit_path=str(audit_path))
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent, settings=settings)

    async def run_command(command: str) -> str:
        return "ok"

    deps = AgentDependencies(
        connection_id=uuid4(),
        player_name="Alex",
        settings=settings,
        http_client=SimpleNamespace(),
        send_to_game=_noop_send_to_game,
        run_command=run_command,
        provider="ollama",
    )
    ctx = SimpleNamespace(deps=deps)

    result = await agent._function_toolset.tools["run_minecraft_command"].function(ctx, "say hi")

    assert result == "ok"
    records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["tool_name"] == "run_minecraft_command"
    assert records[0]["player_name"] == "Alex"
    assert records[0]["parameters"] == {"command": "say hi"}

@pytest.mark.asyncio
async def test_registered_tool_audit_uses_structured_failure(tmp_path) -> None:
    audit_path = tmp_path / "tools.jsonl"
    settings = Settings(runtime_harness_audit_path=str(audit_path))
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent, settings=settings)

    async def run_command(command: str) -> str:
        return "ok"

    deps = AgentDependencies(
        connection_id=uuid4(),
        player_name="Alex",
        settings=settings,
        http_client=SimpleNamespace(),
        send_to_game=_noop_send_to_game,
        run_command=run_command,
        provider="ollama",
    )
    ctx = SimpleNamespace(deps=deps)

    result = await agent._function_toolset.tools["fetch_url_text"].function(ctx, "ftp://example.test")

    assert result == "仅支持 http 或 https URL"
    records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["tool_name"] == "fetch_url_text"
    assert records[0]["result"]["success"] == "failure"
    assert records[0]["result"]["failure_reason"] == "仅支持 http 或 https URL"

@pytest.mark.asyncio
async def test_message_tools_default_to_triggering_player() -> None:
    settings = Settings(runtime_harness_audit_enabled=False)
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent, settings=settings)
    commands: list[str] = []

    async def run_command(command: str) -> str:
        commands.append(command)
        return "ok"

    deps = AgentDependencies(
        connection_id=uuid4(),
        player_name="Alex",
        settings=settings,
        http_client=SimpleNamespace(),
        send_to_game=_noop_send_to_game,
        run_command=run_command,
    )
    ctx = SimpleNamespace(deps=deps)

    await agent._function_toolset.tools["send_game_message"].function(ctx, "plain")
    await agent._function_toolset.tools["send_colored_message"].function(ctx, "hello")
    await agent._function_toolset.tools["send_title_message"].function(ctx, "title")
    await agent._function_toolset.tools["send_actionbar_message"].function(ctx, "hint")

    assert commands[0].startswith("tellraw Alex")
    assert commands[1].startswith("tellraw Alex")
    assert commands[2] == 'title Alex title "title"'
    assert commands[3] == "title Alex times 10 70 20"
    assert commands[4] == 'title Alex actionbar "hint"'


@pytest.mark.asyncio
async def test_message_tools_use_all_players_only_when_broadcast_true() -> None:
    settings = Settings(runtime_harness_audit_enabled=False)
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent, settings=settings)
    commands: list[str] = []

    async def run_command(command: str) -> str:
        commands.append(command)
        return "ok"

    deps = AgentDependencies(
        connection_id=uuid4(),
        player_name="Alex",
        settings=settings,
        http_client=SimpleNamespace(),
        send_to_game=_noop_send_to_game,
        run_command=run_command,
    )
    ctx = SimpleNamespace(deps=deps)

    await agent._function_toolset.tools["send_game_message"].function(ctx, "plain", broadcast=True)
    await agent._function_toolset.tools["send_colored_message"].function(ctx, "hello", broadcast=True)
    await agent._function_toolset.tools["send_title_message"].function(ctx, "title", broadcast=True)
    await agent._function_toolset.tools["send_actionbar_message"].function(ctx, "hint", broadcast=True)

    assert commands[0].startswith("tellraw @a")
    assert commands[1].startswith("tellraw @a")
    assert commands[2] == 'title @a title "title"'
    assert commands[3] == "title @a times 10 70 20"
    assert commands[4] == 'title @a actionbar "hint"'


@pytest.mark.asyncio
async def test_send_script_event_rejects_invalid_message_id() -> None:
    settings = Settings(runtime_harness_audit_enabled=False)
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent, settings=settings)
    commands: list[str] = []

    async def run_command(command: str) -> str:
        commands.append(command)
        return "ok"

    deps = AgentDependencies(
        connection_id=uuid4(),
        player_name="Alex",
        settings=settings,
        http_client=SimpleNamespace(),
        send_to_game=_noop_send_to_game,
        run_command=run_command,
    )
    ctx = SimpleNamespace(deps=deps)

    result = await agent._function_toolset.tools["send_script_event"].function(
        ctx,
        "payload",
        "server:data; say hacked",
    )

    assert "脚本事件发送失败" in result
    assert commands == []


def test_create_scriptevent_rejects_uppercase_message_id() -> None:
    for message_id in ("Server:Data", "MCBEAI:AI_RESP"):
        with pytest.raises(ValueError):
            MinecraftCommand.create_scriptevent("payload", message_id)


def test_escape_command_text() -> None:
    assert escape_command_text('Hello "MC"') == 'Hello \\"MC\\"'
    assert escape_command_text("Line1\nLine2") == "Line1\\nLine2"


def test_build_title_commands() -> None:
    commands = build_title_commands("主标题", "副标题", 10, 70, 20, "Alex")
    assert commands[0] == 'title Alex title "主标题"'
    assert commands[1] == 'title Alex subtitle "副标题"'
    assert commands[2] == "title Alex times 10 70 20"


def test_build_title_commands_broadcast_target() -> None:
    commands = build_title_commands("主标题", None, 10, 70, 20, "@a")
    assert commands[0] == 'title @a title "主标题"'
    assert commands[1] == "title @a times 10 70 20"


def test_build_actionbar_command() -> None:
    command = build_actionbar_command("提示", "Alex")
    assert command == 'title Alex actionbar "提示"'


def test_build_tellraw_command() -> None:
    command = build_tellraw_command("测试", "§b", "Alex")
    assert command.startswith("tellraw Alex")
    assert "§b" in command
