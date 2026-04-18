"""Agent addon 高层工具测试。"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic_ai import Agent

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from config.settings import get_settings
from models.agent import AgentDependencies
from services.agent.tools import register_agent_tools


class _FakeAddonBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def request(self, capability: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((capability, payload))
        return {"ok": True, "payload": payload}


async def _noop_send(_: str) -> None:
    return None


async def _noop_command(_: str) -> str:
    return "命令执行成功"


def _build_tool_context(fake_addon: _FakeAddonBridge) -> SimpleNamespace:
    settings = get_settings()
    deps = AgentDependencies(
        connection_id=uuid4(),
        player_name="Steve",
        settings=settings,
        http_client=httpx.AsyncClient(),
        send_to_game=_noop_send,
        run_command=_noop_command,
        addon_bridge=fake_addon,
    )
    return SimpleNamespace(deps=deps)


@pytest.mark.asyncio
async def test_agent_tool_get_player_snapshot_uses_addon_bridge() -> None:
    fake_addon = _FakeAddonBridge()
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent)

    tool = agent._function_toolset.tools["get_player_snapshot"]
    ctx = _build_tool_context(fake_addon)
    try:
        result = await tool.function(ctx, target="Steve")
    finally:
        await ctx.deps.http_client.aclose()

    assert "Steve" in result
    assert fake_addon.calls == [("get_player_snapshot", {"target": "Steve"})]


@pytest.mark.asyncio
async def test_agent_tool_find_entities_uses_addon_bridge_payload() -> None:
    fake_addon = _FakeAddonBridge()
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent)

    tool = agent._function_toolset.tools["find_entities"]
    ctx = _build_tool_context(fake_addon)
    try:
        result = await tool.function(ctx, entity_type="minecraft:cow", radius=16, target="@s")
    finally:
        await ctx.deps.http_client.aclose()

    assert "minecraft:cow" in result
    assert fake_addon.calls == [
        (
            "find_entities",
            {"entity_type": "minecraft:cow", "radius": 16, "target": "@s"},
        )
    ]
