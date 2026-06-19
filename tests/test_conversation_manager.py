"""对话管理器测试"""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from config.settings import Settings
from core.queue import MessageBroker
from core.conversation import ConversationManager


def _build_turn(index: int) -> list:
    """构建一个用户-AI对话轮次"""
    return [
        ModelRequest(parts=[UserPromptPart(content=f"user-{index}")]),
        ModelResponse(parts=[TextPart(content=f"assistant-{index}")]),
    ]


def _build_multi_turn(count: int) -> list:
    """构建多个对话轮次"""
    messages = []
    for i in range(1, count + 1):
        messages.extend(_build_turn(i))
    return messages


class MockBroker:
    """模拟 MessageBroker（按 (connection_id, player_name) 分桶）"""

    def __init__(self):
        self._histories = {}
        self._generations = {}
        self._titles = {}

    @staticmethod
    def _key(connection_id, player_name, conversation_id="default"):
        return (str(connection_id), player_name or "__anonymous__", conversation_id or "default")

    def get_conversation_history(self, connection_id, player_name=None, conversation_id="default"):
        return list(self._histories.get(self._key(connection_id, player_name, conversation_id), []))

    def get_conversation_generation(self, connection_id, player_name=None, conversation_id="default"):
        return self._generations.get(self._key(connection_id, player_name, conversation_id), 0)

    def set_conversation_history(
        self, connection_id, player_name, history=None, conversation_id="default", expected_generation=None
    ):
        # 兼容历史调用：若只传两个参数则视为旧 API，第二个参数即历史
        if history is None and isinstance(player_name, list):
            history = player_name
            player_name = None
        key = self._key(connection_id, player_name, conversation_id)
        current_generation = self._generations.get(key, 0)
        if expected_generation is not None and expected_generation != current_generation:
            return False
        self._histories[key] = list(history or [])
        self._generations[key] = current_generation + 1
        return True

    def get_conversation_metadata(self, connection_id, player_name=None, conversation_id="default"):
        conversation_id = conversation_id or "default"
        title = self._titles.get(self._key(connection_id, player_name, conversation_id))
        return SimpleNamespace(
            title=title,
            short_id=1,
            title_status="ready" if title else "pending",
            conversation_id=conversation_id,
        )

    def set_conversation_title(self, connection_id, player_name, conversation_id, title):
        self._titles[self._key(connection_id, player_name, conversation_id)] = title

    def clear_conversation_history(self, connection_id, player_name=None, conversation_id="default"):
        key = self._key(connection_id, player_name, conversation_id)
        self._histories.pop(key, None)
        self._generations[key] = self._generations.get(key, 0) + 1


def test_conversation_manager_init():
    """测试 ConversationManager 初始化"""
    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    assert manager.settings is settings
    assert manager.broker is broker
    assert manager._storage_dir == Path("data/conversations")
    assert manager._get_compression_threshold() == 16  # 20 * 0.8


def test_conversation_manager_uses_configured_storage_dir(tmp_path):
    """ConversationManager 应从 settings.storage.conversations_dir 读取存储目录。"""
    from config.settings import StorageConfig

    storage_dir = tmp_path / "custom_convs"
    settings = Settings(storage=StorageConfig(conversations_dir=storage_dir))
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    assert manager._storage_dir == storage_dir
    assert storage_dir.exists()


def test_jwt_handler_uses_configured_tokens_file(tmp_path):
    """JWTHandler 应从 settings.storage.tokens_file 读取令牌文件路径。"""
    from config.settings import StorageConfig
    from services.auth.jwt_handler import JWTHandler

    tokens_file = tmp_path / "custom_tokens.json"
    settings = Settings(storage=StorageConfig(tokens_file=tokens_file))
    handler = JWTHandler(settings)

    assert handler.token_file == tokens_file
    handler.save_token("uuid-1", "tok-1")
    assert tokens_file.exists()
    data = json.loads(tokens_file.read_text(encoding="utf-8"))
    assert data == [{"uuid": "uuid-1", "token": "tok-1"}]


def test_jwt_handler_uses_configured_algorithm(tmp_path):
    """JWTHandler 应使用 settings.jwt_algorithm 编码与校验令牌。"""
    import jwt as jwt_lib

    from config.settings import StorageConfig
    from services.auth.jwt_handler import JWTHandler

    tokens_file = tmp_path / "tokens.json"
    settings = Settings(
        jwt_secret="test-secret",
        jwt_algorithm="HS512",
        storage=StorageConfig(tokens_file=tokens_file),
    )
    handler = JWTHandler(settings)

    token = handler.generate_token()
    assert handler.verify_token(token) is True

    decoded_header = jwt_lib.get_unverified_header(token)
    assert decoded_header["alg"] == "HS512"

    forged = jwt_lib.encode({"exp": 9999999999, "iat": 0}, "test-secret", algorithm="HS256")
    assert handler.verify_token(forged) is False


def test_jwt_handler_default_algorithm_is_hs256(tmp_path):
    """默认 jwt_algorithm 应为 HS256，保持与历史行为兼容。"""
    from config.settings import StorageConfig
    from services.auth.jwt_handler import JWTHandler

    settings = Settings(storage=StorageConfig(tokens_file=tmp_path / "tokens.json"))
    assert settings.jwt_algorithm == "HS256"
    handler = JWTHandler(settings)

    token = handler.generate_token()
    assert handler.verify_token(token) is True


def test_count_turns():
    """测试计算对话轮次"""
    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    # 构建3轮对话
    messages = _build_multi_turn(3)
    assert manager._count_turns(messages) == 3

    # 构建10轮对话
    messages = _build_multi_turn(10)
    assert manager._count_turns(messages) == 10


def test_truncate_to_turns():
    """测试截断对话到指定轮次"""
    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    # 构建10轮对话，截取到5轮
    messages = _build_multi_turn(10)
    truncated = manager._truncate_to_turns(messages, 5)

    assert manager._count_turns(truncated) == 5
    # 验证保留的是最近的对话
    # 最后一轮是 assistant-10 (ModelResponse)
    assert truncated[-1].parts[0].content == "assistant-10"


def test_split_history_for_compression_keeps_recent_turns():
    settings = Settings(compression_keep_recent_turns=3)
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    messages = _build_multi_turn(10)
    older, recent = manager.compressor.split_history(messages)

    assert manager._count_turns(older) == 7
    assert manager._count_turns(recent) == 3
    assert recent[0].parts[0].content == "user-8"
    assert recent[-1].parts[0].content == "assistant-10"

    short_messages = _build_multi_turn(2)
    older, recent = manager.compressor.split_history(short_messages)
    assert older == []
    assert recent == short_messages

    manager = ConversationManager(broker, Settings(compression_keep_recent_turns=0))
    older, recent = manager.compressor.split_history(messages)
    assert older == messages
    assert recent == []


def test_create_summary_message_uses_history_summary_marker():
    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    message = manager.compressor.create_summary_message("事实: 玩家在建房子")

    assert isinstance(message, ModelRequest)
    assert message.parts[0].part_kind == "user-prompt"
    assert "[历史摘要]" in message.parts[0].content
    assert "事实: 玩家在建房子" in message.parts[0].content


def test_serialize_messages_for_summary_skips_thinking_parts():
    from pydantic_ai.messages import ThinkingPart

    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)
    messages = [
        ModelRequest(parts=[UserPromptPart(content="玩家想建一个城堡")]),
        ModelResponse(parts=[ThinkingPart(content="hidden"), TextPart(content="建议先规划地基")]),
    ]

    text = manager.compressor.serialize_messages_for_summary(messages)

    assert "用户: 玩家想建一个城堡" in text
    assert "AI: 建议先规划地基" in text
    assert "hidden" not in text


def test_local_summary_is_structured_and_limited():
    settings = Settings(compression_summary_max_chars=120)
    broker = MockBroker()
    manager = ConversationManager(broker, settings)
    messages = _build_multi_turn(6)

    summary = manager.compressor.build_local_summary(messages)

    assert summary.startswith("事实:")
    assert "user-1" in summary
    assert len(summary) <= 120

    for max_chars in (1, 2, 3):
        manager = ConversationManager(broker, Settings(compression_summary_max_chars=max_chars))
        summary = manager.compressor.build_local_summary(messages)
        assert summary == "事实:"[:max_chars]
        assert len(summary) <= max_chars


@pytest.mark.asyncio
async def test_llm_summary_uses_plain_agent_without_tools(monkeypatch):
    settings = Settings(compression_summary_max_chars=2000)
    broker = MockBroker()
    manager = ConversationManager(broker, settings)
    captured = {}

    class FakeResult:
        output = "事实: 玩家正在建城堡\n偏好: 喜欢石砖"

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            captured["agent_kwargs"] = kwargs

        async def run(self, prompt, model=None):
            captured["prompt"] = prompt
            captured["model"] = model
            return FakeResult()

    def fake_get_model(config):
        captured["provider_model"] = config.model
        return "fake-model"

    monkeypatch.setattr("core.conversation.Agent", FakeAgent)
    monkeypatch.setattr("core.conversation.ProviderRegistry.get_model", fake_get_model)

    summary = await manager.compressor.build_llm_summary(
        messages=_build_multi_turn(2),
        provider_name="deepseek",
    )

    assert summary == "事实: 玩家正在建城堡\n偏好: 喜欢石砖"
    assert captured["model"] == "fake-model"
    assert captured["provider_model"] == settings.deepseek_model
    for key in ("toolsets", "tools", "deps_type"):
        assert key not in captured["agent_kwargs"] or captured["agent_kwargs"][key] is None
    assert "请总结以下较早的 Minecraft AI 对话" in captured["prompt"]


@pytest.mark.asyncio
async def test_compress_history_writes_llm_summary_and_recent_turns(monkeypatch):
    settings = Settings(
        max_history_turns=20,
        compression_enabled=True,
        compression_keep_recent_turns=4,
    )
    broker = MockBroker()
    manager = ConversationManager(broker, settings)
    connection_id = uuid4()
    broker.set_conversation_history(connection_id, "alice", _build_multi_turn(12), "build")

    async def fake_llm_summary(messages, provider_name=None):
        return "事实: 玩家在建城堡"

    monkeypatch.setattr(manager.compressor, "build_llm_summary", fake_llm_summary)

    success, msg = await manager.compress_history(
        connection_id,
        "alice",
        conversation_id="build",
        provider_name="deepseek",
    )

    history = broker.get_conversation_history(connection_id, "alice", "build")
    assert success is True
    assert "LLM" in msg
    assert manager._count_turns(history) == 4
    assert "[历史摘要]" in history[0].parts[0].content
    assert "玩家在建城堡" in history[0].parts[0].content
    assert history[1].parts[0].content == "user-9"


@pytest.mark.asyncio
async def test_compression_only_updates_target_player_conversation(monkeypatch):
    settings = Settings(compression_keep_recent_turns=2)
    broker = MockBroker()
    manager = ConversationManager(broker, settings)
    connection_id = uuid4()
    broker.set_conversation_history(connection_id, "alice", _build_multi_turn(8), "build")
    broker.set_conversation_history(connection_id, "bob", _build_multi_turn(4), "build")
    broker.set_conversation_history(connection_id, "alice", _build_multi_turn(5), "redstone")

    async def fake_llm_summary(messages, provider_name=None):
        return "事实: alice build 被压缩"

    monkeypatch.setattr(manager.compressor, "build_llm_summary", fake_llm_summary)

    success, _msg = await manager.compress_history(
        connection_id,
        "alice",
        conversation_id="build",
        provider_name="deepseek",
    )

    assert success is True
    assert (
        "alice build 被压缩"
        in broker.get_conversation_history(connection_id, "alice", "build")[0].parts[0].content
    )
    assert broker.get_conversation_history(connection_id, "bob", "build")[0].parts[0].content == "user-1"
    assert broker.get_conversation_history(connection_id, "alice", "redstone")[0].parts[0].content == "user-1"


@pytest.mark.asyncio
async def test_check_and_compress_limits_keep_turns_below_low_threshold(monkeypatch):
    settings = Settings(max_history_turns=5, compression_keep_recent_turns=8)
    broker = MockBroker()
    manager = ConversationManager(broker, settings)
    connection_id = uuid4()
    broker.set_conversation_history(connection_id, "alice", _build_multi_turn(5), "build")

    async def fake_llm_summary(messages, provider_name=None):
        return "事实: 低阈值压缩成功"

    monkeypatch.setattr(manager.compressor, "build_llm_summary", fake_llm_summary)

    success, msg = await manager.check_and_compress(
        connection_id,
        "alice",
        conversation_id="build",
        provider_name="deepseek",
    )

    history = broker.get_conversation_history(connection_id, "alice", "build")
    assert success is True
    assert "压缩完成" in msg
    assert manager._count_turns(history) == 3
    assert "低阈值压缩成功" in history[0].parts[0].content
    assert history[1].parts[0].content == "user-3"


@pytest.mark.asyncio
async def test_compress_history_falls_back_to_local_summary(monkeypatch):
    settings = Settings(
        max_history_turns=20,
        compression_enabled=True,
        compression_keep_recent_turns=2,
    )
    broker = MockBroker()
    manager = ConversationManager(broker, settings)
    connection_id = uuid4()
    broker.set_conversation_history(connection_id, "alice", _build_multi_turn(8), "build")

    async def failing_llm_summary(messages, provider_name=None):
        raise TimeoutError("summary timeout")

    monkeypatch.setattr(manager.compressor, "build_llm_summary", failing_llm_summary)

    success, msg = await manager.compress_history(
        connection_id,
        "alice",
        conversation_id="build",
        provider_name="deepseek",
    )

    history = broker.get_conversation_history(connection_id, "alice", "build")
    assert success is True
    assert "本地回退" in msg
    assert manager._count_turns(history) == 2
    assert "事实:" in history[0].parts[0].content


@pytest.mark.asyncio
async def test_compression_skips_stale_generation_write(monkeypatch):
    settings = Settings(
        max_history_turns=20,
        compression_enabled=True,
        compression_keep_recent_turns=4,
    )
    broker = MockBroker()
    manager = ConversationManager(broker, settings)
    connection_id = uuid4()
    broker.set_conversation_history(connection_id, "alice", _build_multi_turn(12), "build")

    async def fake_llm_summary(messages, provider_name=None):
        broker.clear_conversation_history(connection_id, "alice", "build")
        broker.set_conversation_history(connection_id, "alice", _build_multi_turn(1), "build")
        return "事实: 这是一份过期摘要"

    monkeypatch.setattr(manager.compressor, "build_llm_summary", fake_llm_summary)

    success, msg = await manager.compress_history(
        connection_id,
        "alice",
        conversation_id="build",
        provider_name="deepseek",
    )

    history = broker.get_conversation_history(connection_id, "alice", "build")
    assert success is False
    assert "跳过过期压缩结果" in msg
    assert manager._count_turns(history) == 1
    assert history[0].parts[0].content == "user-1"
    assert "[历史摘要]" not in history[0].parts[0].content


def test_extract_keywords():
    """测试提取关键词"""
    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    # 短文本
    short_text = "如何建造房子"
    assert manager._extract_keywords(short_text) == "如何建造房子"

    # 长文本（使用英文避免编码问题）
    long_text = "This is a very long text content that contains a lot of detailed information and explanatory details"
    keywords = manager._extract_keywords(long_text)
    assert len(keywords) <= 53  # 50 + "..."
    assert keywords.endswith("...")


def test_summarize_text():
    """测试文本摘要"""
    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    # 短文本
    short_text = "这是简短的回答"
    assert manager._summarize_text(short_text) == "这是简短的回答"

    # 长文本
    long_text = "这是一个非常长的回答，" * 20
    summary = manager._summarize_text(long_text, max_length=50)
    assert len(summary) <= 53  # 50 + "..."


def test_extract_summary():
    """测试提取摘要"""
    settings = Settings(compression_keep_recent_turns=20)
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    messages = _build_multi_turn(3)

    summary = manager._extract_summary(messages, keep_turns=2)
    assert summary.startswith("事实:")
    assert "user-1" in summary
    assert "assistant-1" in summary
    assert "user-2" not in summary
    assert "assistant-2" not in summary


@pytest.mark.asyncio
async def test_compress_history():
    """测试对话历史压缩"""
    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    connection_id = uuid4()

    # 构建25轮对话（超过阈值16轮）
    messages = _build_multi_turn(25)
    broker.set_conversation_history(connection_id, messages)

    assert manager._count_turns(messages) == 25

    # 执行压缩
    success, msg = await manager.compress_history(connection_id, None)

    assert success is True
    assert "压缩完成" in msg

    # 验证压缩后的轮次
    compressed_history = broker.get_conversation_history(connection_id)
    turns = manager._count_turns(compressed_history)
    assert turns <= 20  # 保留最近20轮


@pytest.mark.asyncio
async def test_check_and_compress_not_needed():
    """测试检查压缩 - 不需要压缩"""
    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    connection_id = uuid4()

    # 只构建5轮对话（不超过阈值16轮）
    messages = _build_multi_turn(5)
    broker.set_conversation_history(connection_id, messages)

    success, msg = await manager.check_and_compress(connection_id, None)

    assert success is False
    assert "未达到压缩阈值" in msg


@pytest.mark.asyncio
async def test_check_and_compress_force():
    """测试强制压缩"""
    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    connection_id = uuid4()

    # 构建10轮对话，超过默认保留的最近8轮，强制压缩可产生旧历史摘要
    messages = _build_multi_turn(10)
    broker.set_conversation_history(connection_id, messages)

    # 强制压缩
    success, msg = await manager.check_and_compress(connection_id, None, force=True)

    assert success is True
    assert "压缩完成" in msg


@pytest.mark.asyncio
async def test_check_and_compress_disabled_without_force():
    settings = Settings(compression_enabled=False)
    broker = MockBroker()
    manager = ConversationManager(broker, settings)
    connection_id = uuid4()
    broker.set_conversation_history(connection_id, _build_multi_turn(25))

    success, msg = await manager.check_and_compress(connection_id, None, force=False)

    assert success is False
    assert "已禁用" in msg


@pytest.mark.asyncio
async def test_save_and_load_conversation():
    """测试保存和加载对话"""
    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    connection_id = uuid4()
    player_name = "TestPlayer"

    # 构建3轮对话
    messages = _build_multi_turn(3)
    broker.set_conversation_history(connection_id, player_name, messages)

    # 保存对话
    success, session_id = await manager.save_conversation(
        connection_id=connection_id,
        player_name=player_name,
        provider="deepseek",
        model="deepseek-chat",
    )

    assert success is True
    assert session_id is not None

    # 清除当前历史
    broker.clear_conversation_history(connection_id, player_name)

    # 加载对话
    success, loaded_messages = await manager.load_conversation(session_id)

    assert success is True
    assert len(loaded_messages) == 6  # 3轮 * 2条消息

    # 清理测试文件
    test_file = manager._storage_dir / f"{session_id}.json"
    if test_file.exists():
        test_file.unlink()


@pytest.mark.asyncio
async def test_save_and_restore_compressed_summary_message(monkeypatch):
    settings = Settings(compression_keep_recent_turns=2)
    broker = MockBroker()
    manager = ConversationManager(broker, settings)
    connection_id = uuid4()
    player_name = "Alice"
    broker.set_conversation_history(connection_id, player_name, _build_multi_turn(6), "build")

    async def fake_llm_summary(messages, provider_name=None):
        return "事实: 保存前已压缩"

    monkeypatch.setattr(manager.compressor, "build_llm_summary", fake_llm_summary)
    await manager.compress_history(connection_id, player_name, conversation_id="build")

    success, session_id = await manager.save_conversation(
        connection_id=connection_id,
        player_name=player_name,
        conversation_id="build",
    )
    assert success is True

    broker.clear_conversation_history(connection_id, player_name, "build")
    success, msg = await manager.restore_conversation(
        connection_id,
        session_id,
        player_name=player_name,
        conversation_id="build",
    )

    restored = broker.get_conversation_history(connection_id, player_name, "build")
    assert success is True
    assert "已恢复" in msg
    assert "保存前已压缩" in restored[0].parts[0].content

    test_file = manager._storage_dir / f"{session_id}.json"
    if test_file.exists():
        test_file.unlink()


@pytest.mark.asyncio
async def test_save_conversation_persists_runtime_title():
    """测试保存对话时持久化运行时标题"""
    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    connection_id = uuid4()
    player_name = "Builder"
    conversation_id = "default"

    messages = _build_multi_turn(1)
    broker.set_conversation_history(connection_id, player_name, messages, conversation_id)
    broker.set_conversation_title(connection_id, player_name, conversation_id, "建筑计划")

    success, session_id = await manager.save_conversation(
        connection_id=connection_id,
        player_name=player_name,
        conversation_id=conversation_id,
    )

    assert success is True

    test_file = manager._storage_dir / f"{session_id}.json"
    try:
        data = json.loads(test_file.read_text(encoding="utf-8"))
        assert data["title"] == "建筑计划"
        assert data["metadata"]["title"] == "建筑计划"
    finally:
        if test_file.exists():
            test_file.unlink()


@pytest.mark.asyncio
async def test_delete_conversation():
    """测试删除对话"""
    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    connection_id = uuid4()

    # 构建并保存对话
    messages = _build_multi_turn(1)
    broker.set_conversation_history(connection_id, messages)

    success, session_id = await manager.save_conversation(connection_id)
    assert success is True

    # 删除对话
    success, msg = await manager.delete_conversation(session_id)
    assert success is True
    assert "已删除" in msg


@pytest.mark.asyncio
async def test_format_conversation_list():
    """测试格式化对话列表"""
    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    # 空列表
    result = manager.format_conversation_list([])
    assert "暂无" in result

    # 有对话
    conversations = [
        {
            "session_id": "test_session_1",
            "player_name": "Player1",
            "conversation_id": "default",
            "title": "建筑计划",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "created_at": "2026-02-15T10:00:00",
            "updated_at": "2026-02-15T10:30:00",
            "message_count": 10,
        },
        {
            "session_id": "test_session_2",
            "player_name": "Player2",
            "provider": "openai",
            "model": "gpt-4",
            "created_at": "2026-02-15T09:00:00",
            "updated_at": "2026-02-15T09:30:00",
            "message_count": 20,
        },
    ]

    result = manager.format_conversation_list(conversations)
    assert "建筑计划" in result
    assert "未命名" in result
    assert "Player1" in result
    assert "Player2" in result


@pytest.mark.asyncio
async def test_list_conversations_reads_legacy_metadata_title():
    """测试旧保存文件没有顶层标题时仍读取 metadata.title。"""
    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)
    session_id = f"legacy_{uuid4().hex}"
    test_file = manager._storage_dir / f"{session_id}.json"
    test_file.write_text(
        json.dumps(
            {
                "player_name": "Player1",
                "provider": "deepseek",
                "model": "deepseek-chat",
                "created_at": "2026-02-15T10:00:00",
                "updated_at": "2026-02-15T10:30:00",
                "message_count": 10,
                "metadata": {"title": "旧标题", "conversation_id": "legacy"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    try:
        conversations = await manager.list_conversations()
        legacy = next(item for item in conversations if item["session_id"] == session_id)
        assert legacy["title"] == "旧标题"
        assert legacy["conversation_id"] == "legacy"
    finally:
        if test_file.exists():
            test_file.unlink()


@pytest.mark.asyncio
async def test_restore_conversation():
    """测试恢复对话"""
    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    connection_id = uuid4()
    player_name = "TestPlayer"

    # 构建并保存对话
    messages = _build_multi_turn(3)
    broker.set_conversation_history(connection_id, player_name, messages)

    success, session_id = await manager.save_conversation(
        connection_id=connection_id,
        player_name=player_name,
    )
    assert success is True

    # 清除当前历史
    broker.clear_conversation_history(connection_id, player_name)

    # 恢复对话
    success, msg = await manager.restore_conversation(connection_id, session_id, player_name=player_name)

    assert success is True
    assert "已恢复" in msg

    # 验证历史已恢复
    restored_history = broker.get_conversation_history(connection_id, player_name)
    assert len(restored_history) > 0

    # 清理
    test_file = manager._storage_dir / f"{session_id}.json"
    if test_file.exists():
        test_file.unlink()


def test_compression_threshold():
    settings = Settings(max_history_turns=20, compression_trigger_ratio=0.8)
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    assert manager._get_compression_threshold() == 16

    settings = Settings(max_history_turns=10, compression_trigger_ratio=0.6)
    manager = ConversationManager(broker, settings)
    assert manager._get_compression_threshold() == 6


def test_truncate_edge_cases():
    """测试边界情况"""
    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    # 空历史
    assert manager._truncate_to_turns([], 5) == []

    # 截取到0轮
    messages = _build_multi_turn(3)
    truncated = manager._truncate_to_turns(messages, 0)
    assert truncated == []

    # 截取轮数大于实际轮数
    truncated = manager._truncate_to_turns(messages, 10)
    assert truncated == messages


def run_async_tests():
    """运行异步测试"""
    asyncio.run(test_compress_history())
    asyncio.run(test_check_and_compress_not_needed())
    asyncio.run(test_check_and_compress_force())
    asyncio.run(test_save_and_load_conversation())
    asyncio.run(test_delete_conversation())
    asyncio.run(test_format_conversation_list())
    asyncio.run(test_restore_conversation())
    print("所有异步测试通过!")


if __name__ == "__main__":
    # 运行同步测试
    test_conversation_manager_init()
    print("test_conversation_manager_init 通过")

    test_count_turns()
    print("test_count_turns 通过")

    test_truncate_to_turns()
    print("test_truncate_to_turns 通过")

    test_extract_keywords()
    print("test_extract_keywords 通过")

    test_summarize_text()
    print("test_summarize_text 通过")

    test_extract_summary()
    print("test_extract_summary 通过")

    test_compression_threshold()
    print("test_compression_threshold 通过")

    test_truncate_edge_cases()
    print("test_truncate_edge_cases 通过")

    # 运行异步测试
    run_async_tests()

    print("\n所有测试通过!")


def test_session_path_rejects_path_traversal():
    """测试会话 ID 路径穿越防护"""
    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    with pytest.raises(ValueError):
        manager._get_session_file_path("../escape")


@pytest.mark.asyncio
async def test_delete_conversation_rejects_invalid_session_id():
    """测试删除会话时拒绝非法会话 ID"""
    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    success, msg = await manager.delete_conversation("../escape")
    assert success is False
    assert "非法会话 ID" in msg
