"""对话管理器测试"""

import asyncio
import json
import sys
import os
from pathlib import Path
from uuid import uuid4
from datetime import datetime

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
    """模拟 MessageBroker"""

    def __init__(self):
        self._histories = {}

    def get_conversation_history(self, connection_id):
        return list(self._histories.get(str(connection_id), []))

    def set_conversation_history(self, connection_id, history):
        self._histories[str(connection_id)] = list(history)

    def clear_conversation_history(self, connection_id):
        self._histories.pop(str(connection_id), None)


def test_conversation_manager_init():
    """测试 ConversationManager 初始化"""
    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    assert manager.settings is settings
    assert manager.broker is broker
    assert manager._storage_dir == Path("data/conversations")
    assert manager._get_compression_threshold() == 16  # 20 * 0.8


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
    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    # 构建3轮对话
    messages = _build_multi_turn(3)

    # 提取摘要（保留2轮，所以1轮应该被摘要）
    summary = manager._extract_summary(messages, keep_turns=2)
    assert "user-1" in summary or "assistant-1" in summary


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
    success, msg = await manager.compress_history(connection_id)

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

    success, msg = await manager.check_and_compress(connection_id)

    assert success is False
    assert "未达到压缩阈值" in msg


@pytest.mark.asyncio
async def test_check_and_compress_force():
    """测试强制压缩"""
    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    connection_id = uuid4()

    # 只构建5轮对话
    messages = _build_multi_turn(5)
    broker.set_conversation_history(connection_id, messages)

    # 强制压缩
    success, msg = await manager.check_and_compress(connection_id, force=True)

    assert success is True
    assert "压缩完成" in msg


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
    broker.set_conversation_history(connection_id, messages)

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
    broker.clear_conversation_history(connection_id)

    # 加载对话
    success, loaded_messages = await manager.load_conversation(session_id)

    assert success is True
    assert len(loaded_messages) == 6  # 3轮 * 2条消息

    # 清理测试文件
    test_file = manager._storage_dir / f"{session_id}.json"
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
    assert "Player1" in result
    assert "Player2" in result


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
    broker.set_conversation_history(connection_id, messages)

    success, session_id = await manager.save_conversation(
        connection_id=connection_id,
        player_name=player_name,
    )
    assert success is True

    # 清除当前历史
    broker.clear_conversation_history(connection_id)

    # 恢复对话
    success, msg = await manager.restore_conversation(connection_id, session_id)

    assert success is True
    assert "已恢复" in msg

    # 验证历史已恢复
    restored_history = broker.get_conversation_history(connection_id)
    assert len(restored_history) > 0

    # 清理
    test_file = manager._storage_dir / f"{session_id}.json"
    if test_file.exists():
        test_file.unlink()


def test_compression_threshold():
    """测试压缩阈值计算"""
    settings = Settings(max_history_turns=20)
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    assert manager._get_compression_threshold() == 16  # 20 * 0.8

    # 测试不同的阈值
    settings = Settings(max_history_turns=10)
    manager = ConversationManager(broker, settings)
    assert manager._get_compression_threshold() == 8  # 10 * 0.8


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
