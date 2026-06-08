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
    broker.set_conversation_history(connection_id, "alice", history)

    stored = broker.get_conversation_history(connection_id, "alice")
    assert len(stored) == 2

    # 不同玩家的历史应当互相隔离
    assert broker.get_conversation_history(connection_id, "bob") == []

    broker.unregister_connection(connection_id)
    assert broker.get_conversation_history(connection_id, "alice") == []


def test_message_broker_history_per_player_isolation() -> None:
    """同一连接、不同玩家的历史互不串扰。"""
    broker = MessageBroker()
    connection_id = uuid4()

    alice_history = _build_turn(1)
    bob_history = _build_turn(2)

    broker.set_conversation_history(connection_id, "alice", alice_history)
    broker.set_conversation_history(connection_id, "bob", bob_history)

    assert broker.get_conversation_history(connection_id, "alice")[0].parts[0].content == "user-1"
    assert broker.get_conversation_history(connection_id, "bob")[0].parts[0].content == "user-2"

    broker.clear_conversation_history(connection_id, "alice")
    assert broker.get_conversation_history(connection_id, "alice") == []
    # bob 的历史不受影响
    assert broker.get_conversation_history(connection_id, "bob") != []



def test_message_broker_history_per_conversation_isolation() -> None:
    """同一玩家的不同对话历史应互相隔离。"""
    broker = MessageBroker()
    connection_id = uuid4()

    broker.set_conversation_history(connection_id, "alice", _build_turn(1), "default")
    broker.set_conversation_history(connection_id, "alice", _build_turn(2), "build-plan")

    assert (
        broker.get_conversation_history(connection_id, "alice", "default")[0].parts[0].content
        == "user-1"
    )
    assert (
        broker.get_conversation_history(connection_id, "alice", "build-plan")[0].parts[0].content
        == "user-2"
    )

    broker.clear_conversation_history(connection_id, "alice", "default")

    assert broker.get_conversation_history(connection_id, "alice", "default") == []
    assert broker.get_conversation_history(connection_id, "alice", "build-plan") != []


def test_conversation_metadata_assigns_stable_short_ids_per_player() -> None:
    broker = MessageBroker()
    connection_id = uuid4()

    alice_default = broker.ensure_conversation_metadata(connection_id, "alice", "default")
    alice_build = broker.ensure_conversation_metadata(connection_id, "alice", "build-plan")
    alice_default_again = broker.ensure_conversation_metadata(connection_id, "alice", "default")
    bob_default = broker.ensure_conversation_metadata(connection_id, "bob", "default")

    assert alice_default.short_id == 1
    assert alice_build.short_id == 2
    assert alice_default_again is alice_default
    assert alice_default_again.short_id == 1
    assert bob_default.short_id == 1


def test_resolve_conversation_short_id_accepts_hash_and_plain_number() -> None:
    broker = MessageBroker()
    connection_id = uuid4()

    broker.ensure_conversation_metadata(connection_id, "alice", "default")
    build_metadata = broker.ensure_conversation_metadata(connection_id, "alice", "build-plan")

    assert broker.resolve_conversation_short_id(connection_id, "alice", "#2") == "build-plan"
    assert broker.resolve_conversation_short_id(connection_id, "alice", "2") == "build-plan"
    assert broker.resolve_conversation_short_id(connection_id, "alice", "abc") is None
    assert broker.resolve_conversation_short_id(connection_id, "alice", "#999") is None
    assert broker.resolve_conversation_short_id(connection_id, "bob", str(build_metadata.short_id)) is None


def test_list_player_conversation_metadata_includes_short_ids_and_titles() -> None:
    broker = MessageBroker()
    connection_id = uuid4()

    broker.get_session_lock(connection_id, "alice")
    broker.set_conversation_history(connection_id, "alice", _build_turn(1), "build-plan")
    broker.set_conversation_title(connection_id, "alice", "build-plan", "Build Plan")
    broker.set_conversation_history(connection_id, "alice", _build_turn(2), "redstone")
    broker.set_conversation_title(connection_id, "alice", "redstone", "Redstone")
    broker.ensure_conversation_metadata(connection_id, "alice", "metadata-only")
    broker.set_conversation_history(connection_id, "bob", _build_turn(3), "bob-chat")

    metadata = broker.list_player_conversation_metadata(connection_id, "alice")

    assert [(item.conversation_id, item.short_id, item.title) for item in metadata] == [
        ("build-plan", 1, "Build Plan"),
        ("redstone", 2, "Redstone"),
        ("metadata-only", 3, None),
        ("default", 4, None),
    ]
    assert metadata[0].title_status == "ready"
    assert metadata[1].title_status == "ready"
    assert metadata[2].title_status == "pending"
    assert metadata[3].title_status == "pending"


def test_clear_connection_sessions_removes_conversation_metadata() -> None:
    broker = MessageBroker()
    connection_id = uuid4()

    broker.set_conversation_history(connection_id, "alice", _build_turn(1), "build-plan")
    broker.set_conversation_title(connection_id, "alice", "build-plan", "Build Plan")
    assert broker.resolve_conversation_short_id(connection_id, "alice", "#1") == "build-plan"

    broker.clear_connection_sessions(connection_id)

    assert broker.list_player_conversation_metadata(connection_id, "alice") == []
    assert broker.resolve_conversation_short_id(connection_id, "alice", "#1") is None


def test_session_lock_per_player_across_conversations() -> None:
    """同一玩家跨不同对话使用同一把锁；不同玩家使用不同锁，可并行。"""
    broker = MessageBroker()
    connection_id = uuid4()

    default_lock = broker.get_session_lock(connection_id, "alice", "default")
    other_conversation_lock = broker.get_session_lock(connection_id, "alice", "build-plan")
    bob_lock = broker.get_session_lock(connection_id, "bob", "default")

    assert default_lock is other_conversation_lock
    assert default_lock is not bob_lock

def test_session_lock_per_player() -> None:
    """同一玩家拿到同一把锁；不同玩家拿到不同锁，可并行。"""
    broker = MessageBroker()
    connection_id = uuid4()

    lock_alice = broker.get_session_lock(connection_id, "alice")
    lock_alice_again = broker.get_session_lock(connection_id, "alice")
    lock_bob = broker.get_session_lock(connection_id, "bob")

    assert lock_alice is lock_alice_again
    assert lock_alice is not lock_bob

def test_conversation_generation_blocks_stale_history_write() -> None:
    broker = MessageBroker()
    connection_id = uuid4()

    generation = broker.get_conversation_generation(connection_id, "alice", "default")
    broker.clear_conversation_history(connection_id, "alice", "default")

    updated = broker.set_conversation_history(
        connection_id,
        "alice",
        _build_turn(1),
        "default",
        expected_generation=generation,
    )

    assert updated is False
    assert broker.get_conversation_history(connection_id, "alice", "default") == []


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

    worker = AgentWorker(
        broker=broker,
        settings=Settings(tool_response_verbose=True),
        worker_id=1,
    )

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


def test_worker_generates_title_after_first_completed_turn(monkeypatch) -> None:
    """首轮完成并写回历史后，应生成并保存对话标题。"""

    import asyncio
    from types import SimpleNamespace

    from config.settings import Settings
    from models.messages import ChatRequest

    broker = MessageBroker()
    connection_id = uuid4()
    broker.register_connection(connection_id)

    worker = AgentWorker(
        broker=broker,
        settings=Settings(max_history_turns=5),
        worker_id=1,
    )
    worker.run_title_generation_inline = True

    completed_messages = _build_turn(1)
    model = object()

    class _FakeManager:
        def get_agent(self):
            return object()

    async def _fake_stream_chat(*args, **kwargs):
        yield SimpleNamespace(
            event_type="content",
            content="完成",
            metadata={"is_complete": True, "all_messages": completed_messages},
        )

    async def _fake_generate_title(first_user_message, actual_model):
        assert first_user_message == "first prompt"
        assert actual_model is model
        return "First Prompt"

    async def _fake_check_and_compress(*args, **kwargs):
        return False, "not compressed"

    monkeypatch.setattr("services.agent.worker.stream_chat", _fake_stream_chat)
    monkeypatch.setattr("services.agent.core.get_agent_manager", lambda: _FakeManager())
    monkeypatch.setattr("services.agent.worker.ProviderRegistry.get_model", lambda *_: model)
    monkeypatch.setattr("services.agent.worker.generate_conversation_title", _fake_generate_title)
    monkeypatch.setattr(
        "core.conversation.ConversationManager.check_and_compress",
        _fake_check_and_compress,
    )

    request = ChatRequest(
        connection_id=connection_id,
        player_name="alice",
        content="first prompt",
        use_context=True,
        conversation_id="current-conversation",
    )
    asyncio.run(worker._process_request_locked(request, connection_id))

    metadata = broker.get_conversation_metadata(
        connection_id,
        "alice",
        "current-conversation",
    )
    assert metadata.title == "First Prompt"
    assert metadata.title_status == "ready"


def test_worker_does_not_regenerate_title_after_later_turn(monkeypatch) -> None:
    """已有标题或后续轮次不应再次触发标题生成。"""

    import asyncio
    from types import SimpleNamespace

    from config.settings import Settings
    from models.messages import ChatRequest

    broker = MessageBroker()
    connection_id = uuid4()
    broker.register_connection(connection_id)
    broker.set_conversation_title(
        connection_id,
        "alice",
        "current-conversation",
        "Existing Title",
    )

    worker = AgentWorker(
        broker=broker,
        settings=Settings(max_history_turns=5),
        worker_id=1,
    )
    worker.run_title_generation_inline = True

    completed_messages = []
    completed_messages.extend(_build_turn(1))
    completed_messages.extend(_build_turn(2))

    class _FakeManager:
        def get_agent(self):
            return object()

    async def _fake_stream_chat(*args, **kwargs):
        yield SimpleNamespace(
            event_type="content",
            content="完成",
            metadata={"is_complete": True, "all_messages": completed_messages},
        )

    async def _fake_generate_title(*args, **kwargs):
        raise AssertionError("title generator should not be called")

    async def _fake_check_and_compress(*args, **kwargs):
        return False, "not compressed"

    monkeypatch.setattr("services.agent.worker.stream_chat", _fake_stream_chat)
    monkeypatch.setattr("services.agent.core.get_agent_manager", lambda: _FakeManager())
    monkeypatch.setattr("services.agent.worker.ProviderRegistry.get_model", lambda *_: object())
    monkeypatch.setattr("services.agent.worker.generate_conversation_title", _fake_generate_title)
    monkeypatch.setattr(
        "core.conversation.ConversationManager.check_and_compress",
        _fake_check_and_compress,
    )

    request = ChatRequest(
        connection_id=connection_id,
        player_name="alice",
        content="second prompt",
        use_context=True,
        conversation_id="current-conversation",
    )
    asyncio.run(worker._process_request_locked(request, connection_id))

    metadata = broker.get_conversation_metadata(
        connection_id,
        "alice",
        "current-conversation",
    )
    assert metadata.title == "Existing Title"
    assert metadata.title_status == "ready"


def test_worker_does_not_write_title_after_connection_cleanup(monkeypatch) -> None:
    """标题后台任务完成时连接已注销，不应重建已清理的元数据。"""

    import asyncio

    from config.settings import Settings

    broker = MessageBroker()
    connection_id = uuid4()
    broker.register_connection(connection_id)
    broker.mark_conversation_title_generating(connection_id, "alice", "current-conversation")
    broker.unregister_connection(connection_id)

    worker = AgentWorker(
        broker=broker,
        settings=Settings(max_history_turns=5),
        worker_id=1,
    )

    async def _fake_generate_title(*args, **kwargs):
        return "Late Title"

    monkeypatch.setattr("services.agent.worker.generate_conversation_title", _fake_generate_title)

    asyncio.run(
        worker._generate_title_for_conversation(
            connection_id,
            "alice",
            "current-conversation",
            "first prompt",
            object(),
        )
    )

    assert broker.list_player_conversation_metadata(connection_id, "alice") == []


def test_worker_does_not_mark_title_failed_after_connection_cleanup(monkeypatch) -> None:
    """标题生成失败时连接已注销，不应重建已清理的元数据。"""

    import asyncio

    from config.settings import Settings

    broker = MessageBroker()
    connection_id = uuid4()
    broker.register_connection(connection_id)
    broker.mark_conversation_title_generating(connection_id, "alice", "current-conversation")
    broker.unregister_connection(connection_id)

    worker = AgentWorker(
        broker=broker,
        settings=Settings(max_history_turns=5),
        worker_id=1,
    )

    async def _fake_generate_title(*args, **kwargs):
        raise RuntimeError("late failure")

    monkeypatch.setattr("services.agent.worker.generate_conversation_title", _fake_generate_title)

    asyncio.run(
        worker._generate_title_for_conversation(
            connection_id,
            "alice",
            "current-conversation",
            "first prompt",
            object(),
        )
    )

    assert broker.list_player_conversation_metadata(connection_id, "alice") == []


def test_worker_persists_completed_history_when_context_disabled(monkeypatch) -> None:
    """use_context=False 时不读取历史，但完成事件仍应写回当前 conversation 历史。"""

    import asyncio
    from types import SimpleNamespace

    from config.settings import Settings
    from models.messages import ChatRequest

    broker = MessageBroker()
    connection_id = uuid4()
    broker.register_connection(connection_id)
    broker.set_conversation_history(
        connection_id,
        "alice",
        _build_turn(1),
        "old-conversation",
    )

    worker = AgentWorker(
        broker=broker,
        settings=Settings(max_history_turns=5),
        worker_id=1,
    )

    captured_message_history = object()
    completed_messages = _build_turn(2)

    class _FakeManager:
        def get_agent(self):
            return object()

    async def _fake_stream_chat(*args, **kwargs):
        nonlocal captured_message_history
        captured_message_history = kwargs.get("message_history")
        yield SimpleNamespace(
            event_type="content",
            content="完成",
            metadata={"is_complete": True, "all_messages": completed_messages},
        )

    async def _fake_check_and_compress(*args, **kwargs):
        return False, "not compressed"

    monkeypatch.setattr("services.agent.worker.stream_chat", _fake_stream_chat)
    monkeypatch.setattr("services.agent.core.get_agent_manager", lambda: _FakeManager())
    monkeypatch.setattr("services.agent.worker.ProviderRegistry.get_model", lambda *_: object())
    monkeypatch.setattr(
        "core.conversation.ConversationManager.check_and_compress",
        _fake_check_and_compress,
    )

    request = ChatRequest(
        connection_id=connection_id,
        player_name="alice",
        content="test",
        use_context=False,
        conversation_id="current-conversation",
    )
    asyncio.run(worker._process_request_locked(request, connection_id))

    assert captured_message_history is None
    current_history = broker.get_conversation_history(
        connection_id,
        "alice",
        "current-conversation",
    )
    old_history = broker.get_conversation_history(
        connection_id,
        "alice",
        "old-conversation",
    )
    assert len(current_history) == 2
    assert current_history[0].parts[0].content == "user-2"
    assert old_history[0].parts[0].content == "user-1"
