"""流式输出模式测试（基于 agent.iter() 官方推荐方式）"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from models.agent import AgentDependencies
from services.agent import core


async def _noop(_: str) -> None:
    return None


# ============== Mock 事件类（使用 SimpleNamespace） ==============
# 使用 SimpleNamespace 创建对象，在测试中通过 monkeypatch isinstance 来通过类型检查


def make_text_part(content: str = "") -> SimpleNamespace:
    """创建模拟 TextPart"""
    return SimpleNamespace(content=content)


def make_text_part_delta(content_delta: str) -> SimpleNamespace:
    """创建模拟 TextPartDelta"""
    return SimpleNamespace(content_delta=content_delta)


def make_part_start_event(index: int, content: str = "") -> SimpleNamespace:
    """创建模拟 PartStartEvent"""
    return SimpleNamespace(index=index, part=make_text_part(content))


def make_part_delta_event(index: int, content_delta: str) -> SimpleNamespace:
    """创建模拟 PartDeltaEvent"""
    return SimpleNamespace(index=index, delta=make_text_part_delta(content_delta))


def make_tool_part(
    tool_name: str = "run_minecraft_command", args: dict = None
) -> SimpleNamespace:
    """创建模拟 ToolCallPart"""
    return SimpleNamespace(
        tool_name=tool_name,
        tool_call_id="test_call_id",
        args=args or {"command": "give @s diamond"},
    )


def make_tool_result(content: str = "命令执行成功") -> SimpleNamespace:
    """创建模拟工具结果"""
    return SimpleNamespace(content=content)


def make_function_tool_call_event(
    tool_name: str = "run_minecraft_command", args: dict = None
) -> SimpleNamespace:
    """创建模拟 FunctionToolCallEvent"""
    return SimpleNamespace(part=make_tool_part(tool_name, args))


def make_function_tool_result_event(
    tool_call_id: str = "test_call_id", result_content: str = "命令执行成功"
) -> SimpleNamespace:
    """创建模拟 FunctionToolResultEvent"""
    return SimpleNamespace(tool_call_id=tool_call_id, result=make_tool_result(result_content))


# ============== Mock 节点流 ==============


class MockRequestStream:
    """模拟 ModelRequestNode.stream() 返回的流"""

    def __init__(self, events: list[Any]):
        self._events = events

    async def __aiter__(self):
        for event in self._events:
            yield event

    async def stream_text(self, delta: bool = True) -> AsyncIterator[str]:
        """模拟 stream_text 方法"""
        for event in self._events:
            if isinstance(event, MockPartDeltaEvent):
                yield event.delta.content_delta


class MockHandleStream:
    """模拟 CallToolsNode.stream() 返回的流"""

    def __init__(self, events: list[Any]):
        self._events = events

    async def __aiter__(self):
        for event in self._events:
            yield event


class MockModelRequestNode:
    """模拟 ModelRequestNode"""

    def __init__(self, events: list[Any]):
        self._events = events

    async def stream(self, ctx: Any) -> MockRequestStream:
        return MockRequestStream(self._events)


class MockCallToolsNode:
    """模拟 CallToolsNode"""

    def __init__(self, events: list[Any]):
        self._events = events

    async def stream(self, ctx: Any) -> MockHandleStream:
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
    ):
        """
        Args:
            text_chunks: 文本增量列表（用于流式模式）
            tool_events: 工具事件列表（用于 CallToolsNode）
            result_output: 最终输出文本
            result_messages: 最终消息列表
        """
        self._text_chunks = text_chunks or []
        self._tool_events = tool_events or []
        self._result_output = result_output
        self._result_messages = result_messages or []
        self.run_called = False
        self.run_call_count = 0

    def iter(
        self,
        prompt: str,
        deps: Any = None,
        model: Any = None,
        message_history: list[Any] = None,
    ) -> MockAgentRunContext:
        """模拟 iter() 方法"""
        # 构建节点序列
        nodes = []

        # 1. UserPromptNode
        nodes.append(MockUserPromptNode(prompt))

        # 2. ModelRequestNode（如果有文本或工具）
        if self._text_chunks or self._tool_events:
            events = []
            # PartStartEvent（可能有初始内容）
            events.append(MockPartStartEvent(0, ""))
            # PartDeltaEvent（文本增量）
            for i, chunk in enumerate(self._text_chunks):
                events.append(MockPartDeltaEvent(0, chunk))
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
    ) -> SimpleNamespace:
        """模拟 run() 方法（用于非流式模式）"""
        self.run_called = True
        self.run_call_count += 1
        return SimpleNamespace(
            output=self._result_output,
            usage=lambda: {"total_tokens": 100},
            all_messages=lambda: self._result_messages,
            new_messages=lambda: self._result_messages,
        )


# ============== 测试辅助函数 ==============


async def _collect_events(prompt: str, deps: AgentDependencies, model: str) -> list[Any]:
    return [event async for event in core.stream_chat(prompt, deps, model=model)]


def _build_deps(stream_sentence_mode: bool) -> AgentDependencies:
    return AgentDependencies(
        connection_id=uuid4(),
        player_name="Tester",
        settings=SimpleNamespace(stream_sentence_mode=stream_sentence_mode),  # type: ignore[arg-type]
        http_client=None,  # type: ignore[arg-type]
        send_to_game=_noop,
        run_command=_noop,
    )


def _patch_isinstance_for_events(monkeypatch):
    """Patch isinstance to make mock events pass type checks"""

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
            return hasattr(obj, "part") and hasattr(obj.part, "tool_name")
        # Handle FunctionToolResultEvent check
        if classinfo.__name__ == "FunctionToolResultEvent" if hasattr(classinfo, "__name__") else False:
            return hasattr(obj, "tool_call_id") and hasattr(obj, "result")
        return original_isinstance(obj, classinfo)

    monkeypatch.setattr("builtins.isinstance", custom_isinstance)


# ============== 测试用例 ==============


def test_stream_sentence_mode_true_should_stream_by_sentence(monkeypatch) -> None:
    """流式模式：按完整句子发送"""
    _patch_isinstance_for_events(monkeypatch)
    
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
    _patch_isinstance_for_events(monkeypatch)
    
    sentence_1 = ("甲" * 50) + "。"
    sentence_2 = ("乙" * 50) + "。"
    sentence_3 = ("丙" * 50) + "。"
    full_response = sentence_1 + sentence_2 + sentence_3

    mock_agent = MockAgent(result_output=full_response)
    monkeypatch.setattr(core, "chat_agent", mock_agent)

    events = asyncio.run(_collect_events("hi", _build_deps(False), model="fake"))
    content_events = [event for event in events if event.content]

    # 总长度 < 150，作为一个批次发送
    assert len(content_events) == 1
    assert content_events[0].content == full_response
    assert events[-1].metadata is not None
    assert events[-1].metadata.get("is_complete") is True


def test_tool_call_should_be_recorded_in_tool_events(monkeypatch) -> None:
    """工具调用事件应被记录在 tool_events 中"""
    _patch_isinstance_for_events(monkeypatch)
    
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


def test_tool_chain_no_longer_needs_manual_fallback(monkeypatch) -> None:
    """工具链不再需要手动回退（框架自动处理）"""
    _patch_isinstance_for_events(monkeypatch)
    
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


def test_empty_text_chunks_should_not_yield_content_events(monkeypatch) -> None:
    """空文本列表不应产生内容事件"""
    _patch_isinstance_for_events(monkeypatch)
    
    mock_agent = MockAgent(text_chunks=[], result_output="")
    monkeypatch.setattr(core, "chat_agent", mock_agent)

    events = asyncio.run(_collect_events("hi", _build_deps(True), model="fake"))
    content_events = [event for event in events if event.content]

    # 没有文本内容，只有完成事件
    assert len(content_events) == 0
    assert events[-1].metadata is not None
    assert events[-1].metadata.get("is_complete") is True


def test_non_stream_mode_uses_run_method(monkeypatch) -> None:
    """非流式模式应使用 run() 方法"""
    _patch_isinstance_for_events(monkeypatch)
    
    mock_agent = MockAgent(result_output="这是一个测试响应。")
    monkeypatch.setattr(core, "chat_agent", mock_agent)

    events = asyncio.run(_collect_events("hi", _build_deps(False), model="fake"))

    # 非流式模式调用 run()
    assert mock_agent.run_called is True
    assert events[-1].metadata is not None
    assert events[-1].metadata.get("is_complete") is True


def test_multiple_tool_calls_should_all_be_recorded(monkeypatch) -> None:
    """多个工具调用事件都应被记录"""
    _patch_isinstance_for_events(monkeypatch)
    
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
