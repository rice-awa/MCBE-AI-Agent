"""消息队列上下文管理测试"""

from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

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
