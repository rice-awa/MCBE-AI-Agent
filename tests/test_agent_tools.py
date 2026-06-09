"""Agent 工具函数测试"""

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

import pytest
from pydantic_ai import Agent

from config.settings import Settings
from models.agent import AgentDependencies
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


async def _noop_send_to_game(message: str) -> None:
    return None


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


def test_escape_command_text() -> None:
    assert escape_command_text('Hello "MC"') == 'Hello \\"MC\\"'
    assert escape_command_text("Line1\nLine2") == "Line1\\nLine2"


def test_build_title_commands() -> None:
    commands = build_title_commands("主标题", "副标题", 10, 70, 20)
    assert commands[0] == 'title @a title "主标题"'
    assert commands[1] == 'title @a subtitle "副标题"'
    assert commands[2] == "title @a times 10 70 20"


def test_build_actionbar_command() -> None:
    command = build_actionbar_command("提示")
    assert command == 'title @a actionbar "提示"'


def test_build_tellraw_command() -> None:
    command = build_tellraw_command("测试", "§b")
    assert command.startswith("tellraw @a")
    assert "§b" in command
