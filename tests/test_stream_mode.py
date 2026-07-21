"""流式输出模式测试（基于 agent.iter() 官方推荐方式）"""

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from pydantic_ai import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPartDelta,
)
from pydantic_ai.messages import TextPart, ThinkingPart, ThinkingPartDelta, ToolCallPart, ToolReturnPart

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from models.agent import AgentDependencies
from services.agent import core


async def _noop(_: str) -> None:
    return None


# ============== Mock 事件类（使用 PydanticAI 真实事件类型） ==============
# 使用真实事件对象避免全局 monkeypatch builtins.isinstance，同时保持测试聚焦 stream mode 行为。


def make_text_part(content: str = "") -> TextPart:
    """创建模拟 TextPart"""
    return TextPart(content)


def make_text_part_delta(content_delta: str) -> TextPartDelta:
    """创建模拟 TextPartDelta"""
    return TextPartDelta(content_delta)


def make_part_start_event(index: int, content: str = "") -> PartStartEvent:
    """创建模拟 PartStartEvent"""
    return PartStartEvent(index=index, part=make_text_part(content))


def make_part_delta_event(index: int, content_delta: str) -> PartDeltaEvent:
    """创建模拟 PartDeltaEvent"""
    return PartDeltaEvent(index=index, delta=make_text_part_delta(content_delta))


def make_thinking_part(content: str = "") -> ThinkingPart:
    """创建模拟 ThinkingPart"""
    return ThinkingPart(content)


def make_thinking_part_delta(content_delta: str) -> ThinkingPartDelta:
    """创建模拟 ThinkingPartDelta"""
    return ThinkingPartDelta(content_delta=content_delta)


def make_thinking_part_start_event(index: int, content: str = "") -> PartStartEvent:
    """创建模拟 ThinkingPart 的 PartStartEvent"""
    return PartStartEvent(index=index, part=make_thinking_part(content))


def make_thinking_part_delta_event(index: int, content_delta: str) -> PartDeltaEvent:
    """创建模拟 ThinkingPartDelta 的 PartDeltaEvent"""
    return PartDeltaEvent(index=index, delta=make_thinking_part_delta(content_delta))


def make_tool_part(
    tool_name: str = "run_minecraft_command", args: dict | None = None
) -> ToolCallPart:
    """创建模拟 ToolCallPart"""
    return ToolCallPart(
        tool_name=tool_name,
        tool_call_id="test_call_id",
        args=args or {"command": "give @s diamond"},
    )


def make_tool_result(
    tool_call_id: str = "test_call_id", content: str = "命令执行成功"
) -> ToolReturnPart:
    """创建模拟工具结果"""
    return ToolReturnPart(
        tool_name="run_minecraft_command",
        content=content,
        tool_call_id=tool_call_id,
    )


def make_function_tool_call_event(
    tool_name: str = "run_minecraft_command", args: dict | None = None
) -> FunctionToolCallEvent:
    """创建模拟 FunctionToolCallEvent"""
    return FunctionToolCallEvent(make_tool_part(tool_name, args))


def make_function_tool_result_event(
    tool_call_id: str = "test_call_id", result_content: str = "命令执行成功"
) -> FunctionToolResultEvent:
    """创建模拟 FunctionToolResultEvent"""
    return FunctionToolResultEvent(part=make_tool_result(tool_call_id, result_content))


# ============== Mock 节点流 ==============


class MockRequestStream:
    """模拟 ModelRequestNode.stream() 返回的流"""

    def __init__(self, events: list[Any]):
        self._events = events

    async def __aenter__(self) -> "MockRequestStream":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def __aiter__(self):
        for event in self._events:
            yield event

    async def stream_text(self, delta: bool = True) -> AsyncIterator[str]:
        """模拟 stream_text 方法"""
        for event in self._events:
            if hasattr(event, "delta") and hasattr(event.delta, "content_delta"):
                yield event.delta.content_delta


class MockHandleStream:
    """模拟 CallToolsNode.stream() 返回的流"""

    def __init__(self, events: list[Any]):
        self._events = events

    async def __aenter__(self) -> "MockHandleStream":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def __aiter__(self):
        for event in self._events:
            yield event


class MockModelRequestNode:
    """模拟 ModelRequestNode"""

    def __init__(self, events: list[Any]):
        self._events = events

    def stream(self, ctx: Any) -> MockRequestStream:
        return MockRequestStream(self._events)


class MockCallToolsNode:
    """模拟 CallToolsNode"""

    def __init__(self, events: list[Any]):
        self._events = events

    def stream(self, ctx: Any) -> MockHandleStream:
        return MockHandleStream(self._events)


class MockUserPromptNode:
    """模拟 UserPromptNode"""

    def __init__(self, prompt: str):
        self.user_prompt = prompt


class MockEndNode:
    """模拟 EndNode"""

    def __init__(self, output: str):
        self.data = SimpleNamespace(output=output)


# ============== Mock AgentRun ==============


class MockAgentRun:
    """模拟 agent.iter() 返回的 AgentRun"""

    def __init__(
        self,
        nodes: list[Any],
        result_output: str = "",
        result_messages: list[Any] = None,
    ):
        self._nodes = nodes
        self.result = SimpleNamespace(
            output=result_output,
            usage=lambda: {"total_tokens": 100},
            all_messages=lambda: result_messages or [],
            new_messages=lambda: result_messages or [],
        )
        self.ctx = SimpleNamespace()

    async def __aiter__(self):
        for node in self._nodes:
            yield node


class MockAgentRunContext:
    """模拟 agent.iter() 的上下文管理器"""

    def __init__(self, run: MockAgentRun):
        self._run = run

    async def __aenter__(self) -> MockAgentRun:
        return self._run

    async def __aexit__(self, *args: Any) -> None:
        pass


# ============== Mock Agent ==============


class MockAgent:
    """模拟 Agent 类，支持 iter() 和 run() 方法"""

    @classmethod
    def __class_getitem__(cls, _item: Any) -> type["MockAgent"]:
        """支持泛型下标 Agent[...]，兼容新版 pydantic-ai。"""
        return cls

    @staticmethod
    def system_prompt(func: Any) -> Any:
        """模拟 @agent.system_prompt 装饰器，直接返回原函数。"""
        return func

    @staticmethod
    def tool(func: Any) -> Any:
        """模拟 @agent.tool 装饰器，直接返回原函数。"""
        return func

    # 类方法，用于节点类型判断
    @staticmethod
    def is_user_prompt_node(node: Any) -> bool:
        return isinstance(node, MockUserPromptNode)

    @staticmethod
    def is_model_request_node(node: Any) -> bool:
        return isinstance(node, MockModelRequestNode)

    @staticmethod
    def is_call_tools_node(node: Any) -> bool:
        return isinstance(node, MockCallToolsNode)

    @staticmethod
    def is_end_node(node: Any) -> bool:
        return isinstance(node, MockEndNode)

    def __init__(
        self,
        text_chunks: list[str] = None,
        tool_events: list[Any] = None,
        result_output: str = "",
        result_messages: list[Any] = None,
        request_node_events: list[list[Any]] = None,
        **_: Any,
    ):

        """
        Args:
            text_chunks: 文本增量列表（用于流式模式）
            tool_events: 工具事件列表（用于 CallToolsNode）
            result_output: 最终输出文本
            result_messages: 最终消息列表
            request_node_events: 多个 ModelRequestNode 的事件列表（用于测试跨节点缓冲）
        """
        self._text_chunks = text_chunks or []
        self._tool_events = tool_events or []
        self._result_output = result_output
        self._result_messages = result_messages or []
        self._request_node_events = request_node_events
        self.run_called = False
        self.run_call_count = 0
        self.iter_called = False
        self.last_usage_limits = None
        self.last_kwargs: dict[str, Any] = {}

    def iter(
        self,
        prompt: str,
        deps: Any = None,
        model: Any = None,
        message_history: list[Any] = None,
        usage_limits: Any = None,
        **kwargs: Any,
    ) -> MockAgentRunContext:
        """模拟 iter() 方法"""
        self.iter_called = True
        self.last_usage_limits = usage_limits
        self.last_kwargs = kwargs
        # 构建节点序列
        nodes = []

        # 1. UserPromptNode
        nodes.append(MockUserPromptNode(prompt))

        # 2. ModelRequestNode
        if self._request_node_events is not None:
            for events in self._request_node_events:
                nodes.append(MockModelRequestNode(events))
        elif self._text_chunks or self._tool_events:
            events = []
            # PartStartEvent（可能有初始内容）
            events.append(make_part_start_event(0, ""))
            # PartDeltaEvent（文本增量）
            for i, chunk in enumerate(self._text_chunks):
                events.append(make_part_delta_event(0, chunk))
            nodes.append(MockModelRequestNode(events))

        # 3. CallToolsNode（如果有工具事件）
        if self._tool_events:
            nodes.append(MockCallToolsNode(self._tool_events))

        # 4. EndNode
        nodes.append(MockEndNode(self._result_output))

        return MockAgentRunContext(
            MockAgentRun(nodes, self._result_output, self._result_messages)
        )

    async def run(
        self,
        prompt: str,
        deps: Any = None,
        model: Any = None,
        message_history: list[Any] = None,
        usage_limits: Any = None,
        **kwargs: Any,
    ) -> SimpleNamespace:
        """模拟 run() 方法（用于非流式模式）"""
        self.run_called = True
        self.run_call_count += 1
        self.last_usage_limits = usage_limits
        self.last_kwargs = kwargs
        return SimpleNamespace(
            output=self._result_output,
            usage=lambda: {"total_tokens": 100},
            all_messages=lambda: self._result_messages,
            new_messages=lambda: self._result_messages,
        )


# ============== 测试辅助函数 ==============


async def _collect_events(prompt: str, deps: AgentDependencies, model: str) -> list[Any]:
    return [event async for event in core.stream_chat(prompt, deps, model=model, agent=core.chat_agent)]


async def _collect_stream_chat_events(
    prompt: str,
    deps: AgentDependencies,
    model: object,
    agent: MockAgent,
) -> list[Any]:
    return [event async for event in core.stream_chat(prompt, deps, model=model, agent=agent)]


def _build_deps(stream_sentence_mode: bool) -> AgentDependencies:
    return AgentDependencies(
        connection_id=uuid4(),
        player_name="Tester",
        settings=SimpleNamespace(
            stream_sentence_mode=stream_sentence_mode,
            request_limit=8,
            tool_calls_limit=8,
            input_tokens_limit=None,
            output_tokens_limit=None,
            total_tokens_limit=None,
            run_timeout=90.0,
            max_tool_concurrency=4,
            context_output_reserve_tokens=1024,
        ),  # type: ignore[arg-type]
        http_client=None,  # type: ignore[arg-type]
        send_to_game=_noop,
        run_command=_noop,
        run_id="test-run-id",
        conversation_id="test-conv",
    )


def _patch_agent_node_adapter(monkeypatch) -> None:
    """Patch core.Agent only so mock nodes use the same node-class adapter as production."""

    monkeypatch.setattr(core, "Agent", MockAgent)

    # Patch ChatAgentManager.get_agent so that tests can inject mock_agent via core.chat_agent.
    original_get_agent = core.ChatAgentManager.get_agent

    def patched_get_agent(self: core.ChatAgentManager) -> Any:
        if isinstance(core.chat_agent, MockAgent):
            return core.chat_agent
        return original_get_agent(self)

    monkeypatch.setattr(core.ChatAgentManager, "get_agent", patched_get_agent)

    original_isinstance = isinstance

    def custom_isinstance(obj, classinfo):
        # Handle PartStartEvent check
        if classinfo.__name__ == "PartStartEvent" if hasattr(classinfo, "__name__") else False:
            return hasattr(obj, "index") and hasattr(obj, "part")
        # Handle PartDeltaEvent check
        if classinfo.__name__ == "PartDeltaEvent" if hasattr(classinfo, "__name__") else False:
            return hasattr(obj, "index") and hasattr(obj, "delta")
        # Handle TextPartDelta check
        if classinfo.__name__ == "TextPartDelta" if hasattr(classinfo, "__name__") else False:
            return hasattr(obj, "content_delta")
        # Handle FunctionToolCallEvent check
        if classinfo.__name__ == "FunctionToolCallEvent" if hasattr(classinfo, "__name__") else False:
            return hasattr(obj, "part") and hasattr(obj.part, "tool_name") and hasattr(obj.part, "args")
        # Handle FunctionToolResultEvent check
        if classinfo.__name__ == "FunctionToolResultEvent" if hasattr(classinfo, "__name__") else False:
            return hasattr(obj, "tool_call_id") and hasattr(obj, "result") and not hasattr(obj.part, "args")
        return original_isinstance(obj, classinfo)

    monkeypatch.setattr("builtins.isinstance", custom_isinstance)


# ============== 测试用例 ==============


def test_stream_sentence_mode_true_should_stream_by_sentence(monkeypatch) -> None:
    """流式模式：按完整句子发送"""
    _patch_agent_node_adapter(monkeypatch)

    mock_agent = MockAgent(
        text_chunks=["你好", "，世界。", "再见", "！"],
        result_output="你好，世界。再见！",
    )
    monkeypatch.setattr(core, "chat_agent", mock_agent)

    events = asyncio.run(_collect_events("hi", _build_deps(True), model="fake"))
    content_events = [event for event in events if event.content]

    assert [event.content for event in content_events] == ["你好，世界。", "再见！"]
    assert events[-1].metadata is not None
    assert events[-1].metadata.get("is_complete") is True


def test_stream_sentence_mode_false_should_batch_after_complete(monkeypatch) -> None:
    """非流式模式：完整响应后分批发送"""
    _patch_agent_node_adapter(monkeypatch)

    sentence_1 = ("甲" * 50) + "。"
    sentence_2 = ("乙" * 50) + "。"
    sentence_3 = ("丙" * 50) + "。"
    full_response = sentence_1 + sentence_2 + sentence_3

    mock_agent = MockAgent(result_output=full_response)
    monkeypatch.setattr(core, "chat_agent", mock_agent)

    events = asyncio.run(_collect_events("hi", _build_deps(False), model="fake"))
    content_events = [event for event in events if event.content]

    # 总长度 > 150，将分成两个批次发送
    assert len(content_events) == 2
    assert "".join(event.content for event in content_events) == full_response
    assert events[-1].metadata is not None
    assert events[-1].metadata.get("is_complete") is True


def test_stream_chat_tool_result_uses_tool_name_from_call_event(monkeypatch) -> None:
    _patch_agent_node_adapter(monkeypatch)

    agent = MockAgent(
        tool_events=[
            make_function_tool_call_event(
                "run_minecraft_command",
                {"command": "give @s diamond"},
            ),
            make_function_tool_result_event("test_call_id", "已执行命令: /give @s diamond"),
        ],
        result_output="已给你钻石。",
    )

    events = asyncio.run(
        _collect_stream_chat_events("hello", _build_deps(True), object(), agent)
    )
    result_events = [event for event in events if event.event_type == "tool_result"]

    assert len(result_events) == 1
    assert result_events[0].metadata is not None
    assert result_events[0].metadata["tool_call_id"] == "test_call_id"
    assert result_events[0].metadata["tool_name"] == "run_minecraft_command"


def test_tool_call_should_be_recorded_in_tool_events(monkeypatch) -> None:
    """工具调用事件应被记录在 tool_events 中"""
    _patch_agent_node_adapter(monkeypatch)

    tool_events = [
        make_function_tool_call_event("run_minecraft_command", {"command": "give @s diamond"}),
        make_function_tool_result_event("test_call_id", "已执行命令: /give @s diamond"),
    ]

    mock_agent = MockAgent(
        text_chunks=["我来给你钻石。"],
        tool_events=tool_events,
        result_output="已给你一颗钻石！",
    )
    monkeypatch.setattr(core, "chat_agent", mock_agent)

    events = asyncio.run(_collect_events("给我钻石", _build_deps(True), model="fake"))

    # 检查完成事件中的 tool_events
    assert events[-1].metadata is not None
    assert events[-1].metadata.get("is_complete") is True
    tool_events_list = events[-1].metadata.get("tool_events", [])
    assert len(tool_events_list) == 1
    assert tool_events_list[0]["tool_name"] == "run_minecraft_command"
    assert events[-1].metadata.get("run_id") == "test-run-id"
    assert events[-1].metadata.get("player_name") == "Tester"
    assert events[-1].metadata.get("conversation_id") == "test-conv"
    assert "tool_calls" not in events[-1].metadata
    assert "tool_returns" not in events[-1].metadata
    assert mock_agent.last_usage_limits is not None
    assert mock_agent.last_usage_limits.request_limit == 8
    assert mock_agent.last_usage_limits.tool_calls_limit == 8


def test_tool_chain_no_longer_needs_manual_fallback(monkeypatch) -> None:
    """工具链不再需要手动回退（框架自动处理）"""
    _patch_agent_node_adapter(monkeypatch)

    tool_events = [
        make_function_tool_call_event("run_minecraft_command", {"command": "give @s diamond"}),
        make_function_tool_result_event("test_call_id", "已执行命令: /give @s diamond"),
    ]

    mock_agent = MockAgent(
        text_chunks=["好的，"],
        tool_events=tool_events,
        result_output="好的，已给你一颗钻石！",
    )
    monkeypatch.setattr(core, "chat_agent", mock_agent)

    events = asyncio.run(_collect_events("给我钻石", _build_deps(True), model="fake"))
    content_events = [event for event in events if event.content]

    # 工具调用由框架自动处理，不再需要手动回退
    # 最终输出应包含完整的响应
    assert len(content_events) > 0
    assert events[-1].metadata is not None
    # tool_fallback_used 字段已被移除
    assert "tool_fallback_used" not in events[-1].metadata


def test_iter_sentence_batches_should_fallback_for_long_sentence() -> None:
    """长句子应被切分"""
    long_sentence = ("A" * 20) + "。"
    batches = list(core._iter_sentence_batches(long_sentence, max_chars=10))

    assert len(batches) == 3
    assert "".join(batches) == long_sentence


def test_iter_sentence_batches_default_reads_from_settings(monkeypatch) -> None:
    """未显式传入 max_chars 时，应从 Settings.flow_control.non_stream_batch_max_chars 读取默认值。"""
    from unittest.mock import patch

    fake_settings = SimpleNamespace(
        flow_control=SimpleNamespace(non_stream_batch_max_chars=5, non_stream_send_delay=0.0)
    )

    with patch.object(core, "get_settings", return_value=fake_settings):
        batches = list(core._iter_sentence_batches("AAAAA。BBBBB。"))

    # 每句 6 字符 > max_chars=5，退化为按 5 字符切分，每句切成 2 段
    assert len(batches) == 4
    assert "".join(batches) == "AAAAA。BBBBB。"


def test_send_content_event_uses_non_stream_send_delay_from_settings(monkeypatch) -> None:
    """_send_content_event 的 add_delay 应使用 Settings.flow_control.non_stream_send_delay。"""
    from unittest.mock import patch

    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    fake_settings = SimpleNamespace(
        flow_control=SimpleNamespace(non_stream_batch_max_chars=150, non_stream_send_delay=0.25)
    )

    ctx = core._HandlerContext()

    async def run() -> None:
        await core._send_content_event(ctx, "hello。", add_delay=True)

    with patch.object(core, "get_settings", return_value=fake_settings), \
         patch.object(core.asyncio, "sleep", fake_sleep):
        asyncio.run(run())

    assert slept == [0.25]


def test_non_stream_constants_removed_from_module() -> None:
    """旧的模块级常量应已被移除，避免遗留的硬编码来源。"""
    assert not hasattr(core, "NON_STREAM_BATCH_MAX_CHARS")
    assert not hasattr(core, "NON_STREAM_SEND_DELAY")


def test_empty_text_chunks_should_not_yield_content_events(monkeypatch) -> None:
    """空文本列表不应产生内容事件"""
    _patch_agent_node_adapter(monkeypatch)

    mock_agent = MockAgent(text_chunks=[], result_output="")
    monkeypatch.setattr(core, "chat_agent", mock_agent)

    events = asyncio.run(_collect_events("hi", _build_deps(True), model="fake"))
    content_events = [event for event in events if event.content]

    # 没有文本内容，只有完成事件
    assert len(content_events) == 0
    assert events[-1].metadata is not None
    assert events[-1].metadata.get("is_complete") is True


def test_non_stream_mode_uses_run_method(monkeypatch) -> None:
    """非流式模式应使用 run() 方法，并传入 UsageLimits。"""
    _patch_agent_node_adapter(monkeypatch)

    mock_agent = MockAgent(result_output="这是一个测试响应。")
    monkeypatch.setattr(core, "chat_agent", mock_agent)

    events = asyncio.run(_collect_events("hi", _build_deps(False), model="fake"))

    # 非流式模式调用 run()
    assert mock_agent.run_called is True
    assert mock_agent.last_usage_limits is not None
    assert mock_agent.last_usage_limits.request_limit == 8
    assert events[-1].metadata is not None
    assert events[-1].metadata.get("is_complete") is True
    assert events[-1].metadata.get("run_id") == "test-run-id"
    assert "tool_events" in events[-1].metadata
    assert "tool_calls" not in events[-1].metadata
    assert "tool_returns" not in events[-1].metadata


def test_multiple_tool_calls_should_all_be_recorded(monkeypatch) -> None:
    """多个工具调用事件都应被记录"""
    _patch_agent_node_adapter(monkeypatch)

    tool_events = [
        make_function_tool_call_event("run_minecraft_command", {"command": "give @s diamond"}),
        make_function_tool_result_event("test_call_id_1", "已执行命令: /give @s diamond"),
        make_function_tool_call_event("send_game_message", {"message": "你好"}),
        make_function_tool_result_event("test_call_id_2", "消息已发送"),
    ]

    mock_agent = MockAgent(
        tool_events=tool_events,
        result_output="已完成所有操作！",
    )
    monkeypatch.setattr(core, "chat_agent", mock_agent)

    events = asyncio.run(_collect_events("执行多个操作", _build_deps(True), model="fake"))

    assert events[-1].metadata is not None
    tool_events_list = events[-1].metadata.get("tool_events", [])
    assert len(tool_events_list) == 2
    assert tool_events_list[0]["tool_name"] == "run_minecraft_command"
    assert tool_events_list[1]["tool_name"] == "send_game_message"


def test_stream_sentence_mode_should_not_flush_incomplete_tail_between_request_nodes(monkeypatch) -> None:
    """流式模式：跨 ModelRequestNode 的半句不应提前发送"""
    _patch_agent_node_adapter(monkeypatch)

    request_node_events = [
        [make_part_start_event(0, ""), make_part_delta_event(0, "你好")],
        [make_part_start_event(0, ""), make_part_delta_event(0, "，世界。")],
    ]

    mock_agent = MockAgent(
        request_node_events=request_node_events,
        result_output="你好，世界。",
    )
    monkeypatch.setattr(core, "chat_agent", mock_agent)

    events = asyncio.run(_collect_events("hi", _build_deps(True), model="fake"))
    content_events = [event for event in events if event.content]

    assert [event.content for event in content_events] == ["你好，世界。"]


def test_non_stream_mode_should_populate_tool_events_from_messages(monkeypatch) -> None:
    """非流式模式：应从结果消息中回填工具调用元数据"""
    _patch_agent_node_adapter(monkeypatch)

    result_messages = [
        SimpleNamespace(parts=[make_tool_part("run_minecraft_command", {"command": "time set day"})])
    ]

    mock_agent = MockAgent(
        result_output="已将时间设置为白天。",
        result_messages=result_messages,
    )
    monkeypatch.setattr(core, "chat_agent", mock_agent)

    events = asyncio.run(_collect_events("把时间调成白天", _build_deps(False), model="fake"))

    assert events[-1].metadata is not None
    tool_events_list = events[-1].metadata.get("tool_events", [])
    assert len(tool_events_list) == 1
    assert tool_events_list[0]["tool_name"] == "run_minecraft_command"
    assert tool_events_list[0]["args"] == {"command": "time set day"}
    assert events[-1].metadata.get("run_id") == "test-run-id"
    assert "tool_calls" not in events[-1].metadata
    assert "tool_returns" not in events[-1].metadata


def test_chat_agent_manager_caches_fallback_agent_after_mcp_failure(monkeypatch) -> None:
    manager = core.ChatAgentManager()
    created = []

    def fake_create_agent(toolsets=None):
        agent = SimpleNamespace(toolsets=toolsets)
        created.append(agent)
        return agent

    monkeypatch.setattr(manager, "_create_agent", fake_create_agent)

    manager.mark_mcp_failed()
    first = manager.get_active_agent(connection_id="connection", mode="stream")
    second = manager.get_active_agent(connection_id="connection", mode="non_stream")

    assert first is second
    assert first.toolsets is None
    assert created == [first]


def test_chat_agent_manager_explicit_agent_overrides_fallback(monkeypatch) -> None:
    manager = core.ChatAgentManager()
    explicit_agent = SimpleNamespace(name="explicit")

    def fail_create_agent(toolsets=None):
        raise AssertionError("fallback should not be created")

    monkeypatch.setattr(manager, "_create_agent", fail_create_agent)

    manager.mark_mcp_failed()

    assert manager.get_active_agent(explicit_agent) is explicit_agent


# ============== 思考模式（reasoning）测试 ==============


def test_thinking_part_start_should_yield_reasoning_not_content(monkeypatch) -> None:
    """ThinkingPart 的 PartStartEvent 应作为 reasoning 事件，不应混入 content。"""
    _patch_agent_node_adapter(monkeypatch)

    request_node_events = [
        [
            make_thinking_part_start_event(0, "玩家"),
            make_thinking_part_delta_event(0, "打了个招呼"),
            make_part_start_event(1, "嗨"),
            make_part_delta_event(1, "！"),
        ]
    ]

    mock_agent = MockAgent(
        request_node_events=request_node_events,
        result_output="嗨！",
    )
    monkeypatch.setattr(core, "chat_agent", mock_agent)

    events = asyncio.run(_collect_events("hi", _build_deps(True), model="fake"))
    reasoning_events = [e for e in events if e.event_type == "reasoning" and e.content]
    content_events = [e for e in events if e.event_type == "content" and e.content]

    assert "".join(e.content for e in reasoning_events) == "玩家打了个招呼"
    assert "".join(e.content for e in content_events) == "嗨！"


def test_thinking_part_delta_after_text_should_not_merge_into_content(monkeypatch) -> None:
    """思考增量在前、文本在后的场景，两者不应互相串入。"""
    _patch_agent_node_adapter(monkeypatch)

    request_node_events = [
        [
            make_thinking_part_start_event(0, "思考。"),
            make_part_start_event(1, "回答。"),
        ]
    ]

    mock_agent = MockAgent(
        request_node_events=request_node_events,
        result_output="回答。",
    )
    monkeypatch.setattr(core, "chat_agent", mock_agent)

    events = asyncio.run(_collect_events("hi", _build_deps(True), model="fake"))
    reasoning_events = [e for e in events if e.event_type == "reasoning" and e.content]
    content_events = [e for e in events if e.event_type == "content" and e.content]

    assert "".join(e.content for e in reasoning_events) == "思考。"
    assert "".join(e.content for e in content_events) == "回答。"


def test_non_stream_mode_should_yield_reasoning_from_thinking_parts(monkeypatch) -> None:
    """非流式模式：应从结果消息的 ThinkingPart 提取 reasoning 并在 content 之前 yield。"""
    _patch_agent_node_adapter(monkeypatch)

    result_messages = [
        SimpleNamespace(parts=[make_thinking_part("先想一想。"), make_text_part("最终答案。")]),
    ]

    mock_agent = MockAgent(
        result_output="最终答案。",
        result_messages=result_messages,
    )
    monkeypatch.setattr(core, "chat_agent", mock_agent)

    events = asyncio.run(_collect_events("hi", _build_deps(False), model="fake"))
    typed = [(e.event_type, e.content) for e in events if e.content]
    reasoning = [c for t, c in typed if t == "reasoning"]
    content = [c for t, c in typed if t == "content"]

    assert reasoning == ["先想一想。"]
    assert "".join(content) == "最终答案。"
    # reasoning 必须在 content 之前
    first_reasoning_idx = next(i for i, (t, _) in enumerate(typed) if t == "reasoning")
    first_content_idx = next(i for i, (t, _) in enumerate(typed) if t == "content")
    assert first_reasoning_idx < first_content_idx
