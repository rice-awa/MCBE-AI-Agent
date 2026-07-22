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

from config.settings import Settings
from models.agent import AgentDependencies
from services.agent.tools import iter_registered_tools, register_agent_tools
from services.agent.tool_results import CommandResult


class _FakeAddonBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def request(self, capability: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((capability, payload))
        return {"ok": True, "payload": payload}


async def _noop_send(_: str) -> None:
    return None


async def _noop_command(_: str) -> CommandResult:
    return CommandResult.ok("命令执行成功")


def _build_tool_context(fake_addon: _FakeAddonBridge) -> SimpleNamespace:
    settings = Settings()
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

    tool = iter_registered_tools(agent)["get_player_snapshot"]
    ctx = _build_tool_context(fake_addon)
    try:
        result = await tool.function(ctx, target="Steve")
    finally:
        await ctx.deps.http_client.aclose()

    assert "Steve" in result
    assert fake_addon.calls == [("get_player_snapshot", {"target": "Steve"})]


class _LookBlockAddonBridge(_FakeAddonBridge):
    async def request(self, capability: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((capability, payload))
        return {
            "ok": True,
            "payload": {
                "player": str(payload.get("target") or "Steve"),
                "hit": True,
                "block": {
                    "typeId": "minecraft:stone",
                    "location": {"x": 1, "y": 64, "z": 2},
                    "dimension": "minecraft:overworld",
                },
                "face": "North",
                "faceLocation": {"x": 0.5, "y": 0.5, "z": 0.0},
            },
        }


@pytest.mark.asyncio
async def test_agent_tool_get_look_block_defaults_to_current_player() -> None:
    fake_addon = _LookBlockAddonBridge()
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent)

    tool = iter_registered_tools(agent)["get_look_block"]
    ctx = _build_tool_context(fake_addon)
    try:
        result = await tool.function(ctx)
    finally:
        await ctx.deps.http_client.aclose()

    # 默认只返回 typeId + 坐标，不含 player / face 等详情
    assert "minecraft:stone" in result
    assert "face" not in result
    assert "faceLocation" not in result
    assert "dimension" not in result
    assert "Steve" not in result
    assert fake_addon.calls == [
        (
            "get_look_block",
            {
                "target": "Steve",
                "max_distance": 8,
                "include_liquid_blocks": False,
                "include_passable_blocks": False,
            },
        )
    ]


@pytest.mark.asyncio
async def test_agent_tool_get_look_block_resolves_selector_to_current_player() -> None:
    fake_addon = _LookBlockAddonBridge()
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent)

    tool = iter_registered_tools(agent)["get_look_block"]
    ctx = _build_tool_context(fake_addon)
    try:
        result = await tool.function(
            ctx,
            target="@s",
            max_distance=16,
            include_liquid_blocks=True,
        )
    finally:
        await ctx.deps.http_client.aclose()

    assert "minecraft:stone" in result
    assert "face" not in result
    assert fake_addon.calls == [
        (
            "get_look_block",
            {
                "target": "Steve",
                "max_distance": 16,
                "include_liquid_blocks": True,
                "include_passable_blocks": False,
            },
        )
    ]


@pytest.mark.asyncio
async def test_agent_tool_get_look_block_include_details_returns_full_payload() -> None:
    fake_addon = _LookBlockAddonBridge()
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent)

    tool = iter_registered_tools(agent)["get_look_block"]
    ctx = _build_tool_context(fake_addon)
    try:
        result = await tool.function(ctx, include_details=True)
    finally:
        await ctx.deps.http_client.aclose()

    assert "minecraft:stone" in result
    assert "face" in result
    assert "faceLocation" in result
    assert "dimension" in result
    assert "Steve" in result
    assert fake_addon.calls == [
        (
            "get_look_block",
            {
                "target": "Steve",
                "max_distance": 8,
                "include_liquid_blocks": False,
                "include_passable_blocks": False,
            },
        )
    ]


@pytest.mark.asyncio
async def test_agent_tool_find_entities_uses_addon_bridge_payload() -> None:
    fake_addon = _FakeAddonBridge()
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent)

    tool = iter_registered_tools(agent)["find_entities"]
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


@pytest.mark.asyncio
async def test_agent_tool_ok_false_maps_to_failure_string() -> None:
    class _FailBridge(_FakeAddonBridge):
        async def request(self, capability: str, payload: dict[str, object]) -> dict[str, object]:
            self.calls.append((capability, payload))
            return {
                "ok": False,
                "payload": {"code": "INTERNAL_ERROR", "message": "addon boom"},
            }

    fake_addon = _FailBridge()
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent)
    tool = iter_registered_tools(agent)["get_player_snapshot"]
    ctx = _build_tool_context(fake_addon)
    try:
        result = await tool.function(ctx, target="Steve")
    finally:
        await ctx.deps.http_client.aclose()

    text = result if isinstance(result, str) else str(result)
    assert "addon boom" in text or "INTERNAL_ERROR" in text
    # Must not look like a bare success payload of the request args
    assert '"ok": false' in text.lower() or "ok\": false" in text or "INTERNAL_ERROR" in text
