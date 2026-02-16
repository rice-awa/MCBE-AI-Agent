"""消息队列上下文管理测试"""

from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    UserPromptPart,
)

from core.queue import MessageBroker
from services.agent.worker import AgentWorker


def _build_turn(index: int) -> list[ModelRequest | ModelResponse]:
    return [
        ModelRequest(parts=[UserPromptPart(content=f"user-{index}")]),
        ModelResponse(parts=[TextPart(content=f"assistant-{index}")]),
    ]


def test_trim_history_keep_recent_turns() -> None:
    messages = []
    for i in range(1, 4):
        messages.extend(_build_turn(i))

    trimmed = AgentWorker._trim_history(messages, max_turns=2)

    assert len(trimmed) == 4
    assert isinstance(trimmed[0], ModelRequest)
    assert isinstance(trimmed[2], ModelRequest)
    assert trimmed[0].parts[0].content == "user-2"
    assert trimmed[2].parts[0].content == "user-3"


def test_message_broker_history_lifecycle() -> None:
    broker = MessageBroker()
    connection_id = uuid4()

    broker.register_connection(connection_id)
    history = _build_turn(1)
    broker.set_conversation_history(connection_id, history)

    stored = broker.get_conversation_history(connection_id)
    assert len(stored) == 2

    broker.unregister_connection(connection_id)
    assert broker.get_conversation_history(connection_id) == []

class _ReasoningNode:
    def __init__(self, reasoning_content: str) -> None:
        self.reasoning_content = reasoning_content
        self.children: list[object] = []


class _Container:
    def __init__(self, items: list[object]) -> None:
        self.items = items


def test_strip_reasoning_content_for_history() -> None:
    node = _ReasoningNode("hidden-thought")
    nested = {"reasoning_content": "hidden-json", "child": node}
    message = _Container([nested, node])

    sanitized, cleared_count = AgentWorker._strip_reasoning_content([message])

    assert cleared_count == 2
    assert node.reasoning_content == "hidden-thought"

    sanitized_message = sanitized[0]
    assert isinstance(sanitized_message, _Container)
    sanitized_nested = sanitized_message.items[0]
    assert isinstance(sanitized_nested, dict)
    assert sanitized_nested["reasoning_content"] == ""

    sanitized_node = sanitized_message.items[1]
    assert isinstance(sanitized_node, _ReasoningNode)
    assert sanitized_node.reasoning_content == ""


def test_strip_reasoning_content_for_thinking_part() -> None:
    message = ModelResponse(
        parts=[ThinkingPart(content="hidden-thought"), TextPart(content="visible")]
    )

    sanitized, cleared_count = AgentWorker._strip_reasoning_content([message])

    assert cleared_count == 1
    assert message.parts[0].content == "hidden-thought"

    sanitized_message = sanitized[0]
    assert isinstance(sanitized_message, ModelResponse)
    assert isinstance(sanitized_message.parts[0], ThinkingPart)
    assert sanitized_message.parts[0].content == ""
    assert isinstance(sanitized_message.parts[1], TextPart)
    assert sanitized_message.parts[1].content == "visible"


def test_worker_tool_events_should_not_be_sent_twice(monkeypatch) -> None:
    """工具事件应仅发送格式化消息一次，避免重复发送原始事件。"""

    import asyncio
    from types import SimpleNamespace

    from config.settings import Settings
    from models.messages import ChatRequest

    broker = MessageBroker()
    connection_id = uuid4()
    broker.register_connection(connection_id)

    worker = AgentWorker(broker=broker, settings=Settings(), worker_id=1)

    class _FakeManager:
        def get_agent(self):
            return object()

    async def _fake_stream_chat(*args, **kwargs):
        yield SimpleNamespace(
            event_type="tool_call",
            content='{"command":"say hi"}',
            metadata={"tool_name": "run_minecraft_command", "args": {"command": "say hi"}},
        )
        yield SimpleNamespace(
            event_type="tool_result",
            content="ok",
            metadata={"tool_name": "run_minecraft_command"},
        )
        yield SimpleNamespace(
            event_type="content",
            content="完成",
            metadata={"is_complete": False},
        )

    monkeypatch.setattr("services.agent.worker.stream_chat", _fake_stream_chat)
    monkeypatch.setattr("services.agent.core.get_agent_manager", lambda: _FakeManager())
    monkeypatch.setattr("services.agent.worker.ProviderRegistry.get_model", lambda *_: object())

    request = ChatRequest(connection_id=connection_id, content="test", use_context=False)
    asyncio.run(worker._process_request_locked(request, connection_id))

    queue = broker.get_response_queue(connection_id)
    assert queue is not None

    chunks = []
    while not queue.empty():
        chunks.append(queue.get_nowait())

    assert [chunk.chunk_type for chunk in chunks] == ["tool_call", "tool_result", "content"]
    assert [chunk.sequence for chunk in chunks] == [0, 1, 2]
