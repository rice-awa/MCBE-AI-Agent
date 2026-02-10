"""消息队列上下文管理测试"""

from uuid import uuid4

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

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
