"""DeepSeek 在线多轮工具调用集成测试。"""

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from config.settings import LLMProviderConfig, Settings
from models.agent import AgentDependencies
from pydantic_ai.messages import ModelMessage
from services.agent.core import stream_chat
from services.agent.providers import ProviderRegistry


class _ToolRecorder:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.game_messages: list[str] = []

    async def run_command(self, command: str) -> None:
        self.commands.append(command)

    async def send_to_game(self, message: str) -> None:
        self.game_messages.append(message)


def _require_api_key() -> str:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        pytest.skip("未设置 DEEPSEEK_API_KEY，跳过在线集成测试")
    return api_key


async def _run_turn(
    prompt: str,
    deps: AgentDependencies,
    model: object,
    message_history: list[ModelMessage] | None,
) -> dict:
    events = [
        event
        async for event in stream_chat(
            prompt,
            deps,
            model=model,
            message_history=message_history,
        )
    ]
    metadata = [event.metadata for event in events if event.metadata]
    assert metadata, "未收到完成事件元数据"
    return metadata[-1] or {}


async def _run_live_multi_turn_tool_chain(model_name: str) -> tuple[list[dict], list[str]]:
    api_key = _require_api_key()

    provider_config = LLMProviderConfig(
        name="deepseek",
        api_key=api_key,
        base_url="https://api.deepseek.com",
        model=model_name,
        enabled=True,
        timeout=120,
    )
    model = ProviderRegistry.get_model(provider_config)

    recorder = _ToolRecorder()
    settings = Settings(
        deepseek_api_key=api_key,
        stream_sentence_mode=True,
        llm_warmup_enabled=False,
    )

    deps = AgentDependencies(
        connection_id=uuid4(),
        player_name="IntegrationTester",
        settings=settings,
        http_client=httpx.AsyncClient(timeout=30),
        send_to_game=recorder.send_to_game,
        run_command=recorder.run_command,
    )

    first_meta = await _run_turn(
        "请调用 run_minecraft_command 执行命令 say tool_chain_step_1，然后简短回复。",
        deps,
        model,
        message_history=None,
    )

    history = first_meta.get("all_messages")
    assert isinstance(history, list)

    second_meta = await _run_turn(
        "继续上一轮，再调用 run_minecraft_command 执行命令 say tool_chain_step_2，然后回复 OK。",
        deps,
        model,
        message_history=history,
    )

    await deps.http_client.aclose()
    await ProviderRegistry.shutdown()

    return [first_meta, second_meta], recorder.commands


def test_deepseek_chat_should_support_multi_turn_tool_chain() -> None:
    metadata_list, _commands = asyncio.run(_run_live_multi_turn_tool_chain("deepseek-chat"))

    assert len(metadata_list) == 2
    assert metadata_list[0].get("is_complete") is True
    assert metadata_list[1].get("is_complete") is True
    assert metadata_list[0].get("tool_calls", 0) >= 1
    assert metadata_list[0].get("tool_returns", 0) >= 1
    assert metadata_list[1].get("tool_calls", 0) >= 1
    assert metadata_list[1].get("tool_returns", 0) >= 1



def test_deepseek_reasoner_should_support_multi_turn_tool_chain() -> None:
    metadata_list, _commands = asyncio.run(_run_live_multi_turn_tool_chain("deepseek-reasoner"))

    assert len(metadata_list) == 2
    assert metadata_list[0].get("is_complete") is True
    assert metadata_list[1].get("is_complete") is True
    assert metadata_list[0].get("tool_calls", 0) >= 1
    assert metadata_list[0].get("tool_returns", 0) >= 1
    assert metadata_list[1].get("tool_calls", 0) >= 1
    assert metadata_list[1].get("tool_returns", 0) >= 1

