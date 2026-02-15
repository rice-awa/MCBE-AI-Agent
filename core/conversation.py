"""对话管理器 - 管理对话历史、压缩和持久化存储"""

import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import aiofiles
from pydantic import BaseModel, Field
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from config.logging import get_logger
from config.settings import Settings

logger = get_logger(__name__)


class ConversationMetadata(BaseModel):
    """对话元数据"""

    connection_id: str
    player_name: str | None = None
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    message_count: int = 0
    template: str = "default"
    custom_variables: dict[str, str] = Field(default_factory=dict)


class SavedConversation(BaseModel):
    """保存的对话"""

    connection_id: str
    player_name: str | None = None
    provider: str
    model: str
    created_at: datetime
    updated_at: datetime
    message_count: int
    messages: list[dict] = Field(default_factory=list)
    metadata: ConversationMetadata


class ConversationManager:
    """
    对话管理器 - 管理对话历史的压缩和持久化存储

    功能:
    - 自动压缩：当对话历史超过阈值时自动压缩
    - 手动压缩：支持手动触发智能压缩
    - 持久化存储：保存和恢复对话历史
    - 列表/删除：管理已保存的对话
    """

    # 压缩触发阈值比例
    COMPRESSION_THRESHOLD_RATIO = 0.8

    def __init__(
        self,
        broker,
        settings: Settings,
    ):
        self.broker = broker
        self.settings = settings
        self._storage_dir = Path("data/conversations")
        self._storage_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "conversation_manager_initialized",
            storage_dir=str(self._storage_dir),
            max_history_turns=settings.max_history_turns,
        )

    def _get_compression_threshold(self) -> int:
        """获取压缩触发阈值"""
        return int(self.settings.max_history_turns * self.COMPRESSION_THRESHOLD_RATIO)

    async def check_and_compress(
        self,
        connection_id: UUID,
        force: bool = False,
    ) -> tuple[bool, str]:
        """
        检查是否需要压缩，必要时执行压缩

        Args:
            connection_id: 连接 ID
            force: 是否强制压缩

        Returns:
            (是否执行了压缩, 描述信息)
        """
        history = self.broker.get_conversation_history(connection_id)
        threshold = self._get_compression_threshold()

        if not history:
            return False, "对话历史为空"

        # 计算当前轮次
        turns = self._count_turns(history)

        if force:
            # 强制模式下，尝试提取摘要（即使轮数少于阈值）
            return await self.compress_history(connection_id, force=True)
        elif turns >= threshold:
            return await self.compress_history(connection_id)

        return False, f"当前 {turns} 轮，未达到压缩阈值 {threshold} 轮"

    async def compress_history(
        self,
        connection_id: UUID,
        force: bool = False,
    ) -> tuple[bool, str]:
        """
        压缩对话历史 - 保留关键信息，删除冗余内容

        压缩策略:
        1. 保留最近 N 轮完整对话 (N = max_history_turns)
        2. 提取并保留用户问题的核心关键词
        3. 保留 AI 回答的摘要
        4. 删除推理过程和冗余表达

        Args:
            connection_id: 连接 ID
            force: 是否强制压缩（强制模式下即使轮数少于阈值也会提取摘要）

        Returns:
            (是否成功, 描述信息)
        """
        history = self.broker.get_conversation_history(connection_id)
        max_turns = self.settings.max_history_turns
        threshold = self._get_compression_threshold()

        if not history:
            return False, "对话历史为空"

        current_turns = self._count_turns(history)

        # 强制模式下可以处理少于阈值的情况，用于提取摘要
        if not force and current_turns <= threshold:
            return False, f"当前 {current_turns} 轮，无需压缩"

        # 保留最近 threshold 轮对话
        truncated = self._truncate_to_turns(history, threshold)

        # 提取关键词摘要
        summary = self._extract_summary(history, threshold)

        # 如果有摘要，添加到历史开头
        if summary:
            # 创建一个带有摘要的系统消息
            summary_message = self._create_summary_message(summary)
            if summary_message:
                truncated.insert(0, summary_message)

        self.broker.set_conversation_history(connection_id, truncated)

        new_turns = self._count_turns(truncated)
        logger.info(
            "conversation_compressed",
            connection_id=str(connection_id),
            original_turns=current_turns,
            new_turns=new_turns,
            summary_length=len(summary),
        )

        return True, f"压缩完成: {current_turns}轮 -> {new_turns}轮"

    def _count_turns(self, history: list[ModelMessage]) -> int:
        """计算对话轮次（用户提问次数）"""
        turns = 0
        for message in history:
            # 检查是否是用户请求消息
            for part in message.parts:
                if getattr(part, "part_kind", None) == "user-prompt":
                    turns += 1
                    break
        return turns

    def _truncate_to_turns(
        self,
        history: list[ModelMessage],
        max_turns: int,
    ) -> list[ModelMessage]:
        """截断到指定轮次，保留完整的用户-AI对话"""
        if max_turns <= 0:
            return []

        user_turns = 0
        cut_idx = 0

        for idx in range(len(history) - 1, -1, -1):
            message = history[idx]
            for part in message.parts:
                if getattr(part, "part_kind", None) == "user-prompt":
                    user_turns += 1
                    if user_turns == max_turns:
                        cut_idx = idx
                        break
            if user_turns == max_turns:
                break

        if user_turns < max_turns:
            return list(history)

        return list(history[cut_idx:])

    def _extract_summary(self, history: list[ModelMessage], keep_turns: int) -> str:
        """从历史中提取摘要信息"""
        summary_parts = []

        # 获取需要摘要的旧对话（排除保留的最近 N 轮）
        old_history = self._truncate_to_turns(history, keep_turns)
        if len(old_history) >= len(history):
            return ""

        older_messages = history[: len(history) - len(old_history)]

        for message in older_messages:
            # 提取用户问题关键词
            if hasattr(message, "parts"):
                for part in message.parts:
                    if getattr(part, "part_kind", None) == "user-prompt":
                        content = getattr(part, "content", "") or ""
                        keywords = self._extract_keywords(content)
                        if keywords:
                            summary_parts.append(f"用户问: {keywords}")
                    elif getattr(part, "part_kind", None) == "text":
                        # AI 回复
                        content = getattr(part, "content", "") or ""
                        summary = self._summarize_text(content)
                        if summary:
                            summary_parts.append(f"AI答: {summary}")

        return " | ".join(summary_parts[:10])  # 限制摘要长度

    def _extract_keywords(self, text: str) -> str:
        """提取文本关键词"""
        if not text:
            return ""

        # 简单提取：取前50个字符作为关键词
        keywords = text[:50].strip()
        if len(text) > 50:
            keywords += "..."
        return keywords

    def _summarize_text(self, text: str, max_length: int = 100) -> str:
        """摘要文本"""
        if not text:
            return ""

        # 移除多余空白
        text = re.sub(r"\s+", " ", text).strip()

        if len(text) <= max_length:
            return text

        return text[:max_length].strip() + "..."

    def _create_summary_message(self, summary: str) -> ModelMessage | None:
        """创建摘要消息"""
        try:
            # 使用 PydanticAI 的 ModelRequest 创建摘要消息
            from pydantic_ai.messages import ModelRequest, UserPromptPart

            return ModelRequest(
                parts=[
                    UserPromptPart(
                        content=f"[历史摘要] {summary}",
                        part_kind="user-prompt",
                    )
                ]
            )
        except Exception as e:
            logger.warning("create_summary_message_failed", error=str(e))
            return None

    async def save_conversation(
        self,
        connection_id: UUID,
        player_name: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        template: str = "default",
        custom_variables: dict[str, str] | None = None,
    ) -> tuple[bool, str]:
        """
        保存当前对话到持久化存储

        Args:
            connection_id: 连接 ID
            player_name: 玩家名称
            provider: LLM 提供商
            model: 模型名称
            template: 使用的模板
            custom_variables: 自定义变量

        Returns:
            (是否成功, 消息/会话ID)
        """
        history = self.broker.get_conversation_history(connection_id)

        if not history:
            return False, "对话历史为空，无法保存"

        try:
            # 序列化消息：使用 dump_json 序列化为 JSON 字节
            messages_json_bytes = ModelMessagesTypeAdapter.dump_json(history)
            messages_json_str = messages_json_bytes.decode("utf-8")

            # 生成会话ID
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_id = f"{connection_id}_{timestamp}"

            # 构建保存数据
            saved_data = {
                "connection_id": str(connection_id),
                "player_name": player_name,
                "provider": provider or self.settings.default_provider,
                "model": model or self.settings.get_provider_config(
                    provider or self.settings.default_provider
                ).model,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "message_count": len(history),
                "messages": json.loads(messages_json_str),
                "metadata": {
                    "template": template,
                    "custom_variables": custom_variables or {},
                },
            }

            # 保存到文件
            file_path = self._storage_dir / f"{session_id}.json"
            async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(saved_data, ensure_ascii=False, indent=2))

            logger.info(
                "conversation_saved",
                session_id=session_id,
                connection_id=str(connection_id),
                message_count=len(history),
            )

            return True, session_id

        except Exception as e:
            logger.error(
                "save_conversation_failed",
                connection_id=str(connection_id),
                error=str(e),
            )
            return False, f"保存失败: {str(e)}"

    def _get_session_file_path(self, session_id: str) -> Path:
        """安全解析会话文件路径，防止路径穿越。"""
        normalized = Path(session_id)
        if normalized.name != session_id or normalized.suffix:
            raise ValueError(f"非法会话 ID: {session_id}")

        file_path = (self._storage_dir / f"{session_id}.json").resolve()
        storage_root = self._storage_dir.resolve()
        if storage_root not in file_path.parents:
            raise ValueError(f"非法会话 ID: {session_id}")

        return file_path

    async def load_conversation(
        self,
        session_id: str,
    ) -> tuple[bool, list[ModelMessage] | str]:
        """
        从持久化存储加载对话

        Args:
            session_id: 会话 ID

        Returns:
            (是否成功, 对话历史或错误消息)
        """
        try:
            file_path = self._get_session_file_path(session_id)
        except ValueError as e:
            return False, str(e)

        if not file_path.exists():
            return False, f"会话不存在: {session_id}"

        try:
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                content = await f.read()
                data = json.loads(content)

            # 反序列化消息
            messages = ModelMessagesTypeAdapter.validate_json(
                json.dumps(data["messages"])
            )

            logger.info(
                "conversation_loaded",
                session_id=session_id,
                message_count=len(messages),
            )

            return True, messages

        except Exception as e:
            logger.error(
                "load_conversation_failed",
                session_id=session_id,
                error=str(e),
            )
            return False, f"加载失败: {str(e)}"

    async def restore_conversation(
        self,
        connection_id: UUID,
        session_id: str,
    ) -> tuple[bool, str]:
        """
        恢复对话到指定连接

        Args:
            connection_id: 连接 ID
            session_id: 会话 ID

        Returns:
            (是否成功, 消息)
        """
        success, result = await self.load_conversation(session_id)

        if not success:
            return False, result

        messages = result if isinstance(result, list) else []

        # 设置到 broker
        self.broker.set_conversation_history(connection_id, messages)

        logger.info(
            "conversation_restored",
            connection_id=str(connection_id),
            session_id=session_id,
            message_count=len(messages),
        )

        return True, f"已恢复会话 {session_id}，共 {len(messages)} 条消息"

    async def list_conversations(self) -> list[dict]:
        """
        列出所有已保存的对话

        Returns:
            对话列表（按更新时间排序）
        """
        conversations = []

        for file_path in self._storage_dir.glob("*.json"):
            try:
                async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                    content = await f.read()
                    data = json.loads(content)

                conversations.append(
                    {
                        "session_id": file_path.stem,
                        "player_name": data.get("player_name"),
                        "provider": data.get("provider"),
                        "model": data.get("model"),
                        "created_at": data.get("created_at"),
                        "updated_at": data.get("updated_at"),
                        "message_count": data.get("message_count", 0),
                    }
                )
            except Exception as e:
                logger.warning(
                    "list_conversation_read_error",
                    file=str(file_path),
                    error=str(e),
                )

        # 按更新时间排序
        conversations.sort(
            key=lambda x: x.get("updated_at", ""),
            reverse=True,
        )

        return conversations

    async def delete_conversation(self, session_id: str) -> tuple[bool, str]:
        """
        删除指定的对话

        Args:
            session_id: 会话 ID

        Returns:
            (是否成功, 消息)
        """
        try:
            file_path = self._get_session_file_path(session_id)
        except ValueError as e:
            return False, str(e)

        if not file_path.exists():
            return False, f"会话不存在: {session_id}"

        try:
            file_path.unlink()

            logger.info(
                "conversation_deleted",
                session_id=session_id,
            )

            return True, f"已删除会话: {session_id}"

        except Exception as e:
            logger.error(
                "delete_conversation_failed",
                session_id=session_id,
                error=str(e),
            )
            return False, f"删除失败: {str(e)}"

    def format_conversation_list(
        self,
        conversations: list[dict],
        limit: int = 10,
    ) -> str:
        """格式化对话列表为可读字符串"""
        if not conversations:
            return "暂无保存的对话"

        lines = ["已保存的对话:"]
        for i, conv in enumerate(conversations[:limit]):
            session_id = conv.get("session_id", "unknown")
            player = conv.get("player_name", "未知玩家")
            provider = conv.get("provider", "deepseek")
            model = conv.get("model", "")
            count = conv.get("message_count", 0)
            updated = conv.get("updated_at", "")[:16]  # 只取日期时间

            # 简化 session_id 显示
            display_id = session_id.split("_")[1] if "_" in session_id else session_id[:8]

            lines.append(
                f"{i + 1}. [{display_id}] {player} | {provider}/{model} | "
                f"{count}条消息 | {updated}"
            )

        if len(conversations) > limit:
            lines.append(f"... 共 {len(conversations)} 个会话")

        return "\n".join(lines)


# 全局管理器实例
_conversation_manager: ConversationManager | None = None


def get_conversation_manager(
    broker,
    settings: Settings | None = None,
) -> ConversationManager:
    """获取对话管理器单例"""
    global _conversation_manager

    if _conversation_manager is None:
        from config.settings import get_settings

        settings = settings or get_settings()
        _conversation_manager = ConversationManager(broker, settings)

    return _conversation_manager
