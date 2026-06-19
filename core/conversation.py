"""对话管理器 - 管理对话历史、压缩和持久化存储"""

import asyncio
import inspect
import json
import re
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import aiofiles
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    UserPromptPart,
)

from config.logging import get_logger
from config.settings import Settings
from services.agent.providers import ProviderRegistry

logger = get_logger(__name__)


class CompressionResult(BaseModel):
    compressed: bool
    used_llm: bool = False
    fallback_used: bool = False
    original_turns: int = 0
    new_turns: int = 0
    summary: str = ""
    message: str = ""


class ConversationMetadata(BaseModel):
    """对话元数据"""

    connection_id: str
    player_name: str | None = None
    conversation_id: str = "default"
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    message_count: int = 0
    title: str | None = None
    template: str = "default"
    custom_variables: dict[str, str] = Field(default_factory=dict)


class SavedConversation(BaseModel):
    """保存的对话"""

    connection_id: str
    player_name: str | None = None
    conversation_id: str = "default"
    provider: str
    model: str
    created_at: datetime
    updated_at: datetime
    message_count: int
    title: str | None = None
    messages: list[dict] = Field(default_factory=list)
    metadata: ConversationMetadata


class ConversationCompressor:
    """对话压缩器：分离历史、生成摘要、构造压缩后的 message_history。"""

    def __init__(self, settings: Settings):
        self.settings = settings

    def count_turns(self, history: list[ModelMessage]) -> int:
        turns = 0
        for message in history:
            if self.is_summary_message(message):
                continue
            for part in getattr(message, "parts", []):
                if getattr(part, "part_kind", None) == "user-prompt":
                    turns += 1
                    break
        return turns

    def split_history(
        self,
        history: list[ModelMessage],
        keep_turns: int | None = None,
    ) -> tuple[list[ModelMessage], list[ModelMessage]]:
        return self._split_history_by_keep_turns(
            history,
            self.settings.compression_keep_recent_turns if keep_turns is None else keep_turns,
        )

    @staticmethod
    def _split_history_by_keep_turns(
        history: list[ModelMessage],
        keep_turns: int,
    ) -> tuple[list[ModelMessage], list[ModelMessage]]:
        if keep_turns <= 0:
            return list(history), []

        user_turns = 0
        cut_idx = len(history)
        for idx in range(len(history) - 1, -1, -1):
            message = history[idx]
            if any(getattr(part, "part_kind", None) == "user-prompt" for part in getattr(message, "parts", [])):
                user_turns += 1
                if user_turns == keep_turns:
                    cut_idx = idx
                    break

        if user_turns < keep_turns:
            return [], list(history)
        return list(history[:cut_idx]), list(history[cut_idx:])

    def serialize_messages_for_summary(self, messages: list[ModelMessage]) -> str:
        lines: list[str] = []
        for message in messages:
            role = self._message_role(message)
            text_parts: list[str] = []
            for part in getattr(message, "parts", []):
                part_kind = getattr(part, "part_kind", None)
                if part_kind == "thinking":
                    continue
                if part_kind in {"user-prompt", "text"}:
                    content = str(getattr(part, "content", "") or "").strip()
                    if content:
                        text_parts.append(re.sub(r"\s+", " ", content))
                elif part_kind == "tool-call":
                    tool_name = getattr(part, "tool_name", "tool")
                    text_parts.append(f"调用工具 {tool_name}")
                elif part_kind == "tool-return":
                    content = str(getattr(part, "content", "") or "").strip()
                    if content:
                        normalized_content = re.sub(r"\s+", " ", content)
                        text_parts.append(f"工具返回 {normalized_content[:200]}")
            if text_parts:
                lines.append(f"{role}: {' '.join(text_parts)}")
        return "\n".join(lines)

    @staticmethod
    def _message_role(message: ModelMessage) -> str:
        if isinstance(message, ModelRequest):
            return "用户"
        if isinstance(message, ModelResponse):
            return "AI"
        return "消息"

    def build_local_summary(self, messages: list[ModelMessage]) -> str:
        text = self.serialize_messages_for_summary(messages)
        if not text:
            return ""

        max_chars = self.settings.compression_summary_max_chars
        if max_chars <= 0:
            return ""

        compact = re.sub(r"\s+", " ", text).strip()
        summary = f"事实: 以下为较早对话的本地压缩摘要。{compact}"
        if len(summary) <= max_chars:
            return summary
        if max_chars <= 3:
            return "事实:"[:max_chars]
        return summary[: max_chars - 3].rstrip() + "..."

    async def build_llm_summary(
        self,
        messages: list[ModelMessage],
        provider_name: str | None = None,
    ) -> str:
        source_text = self.serialize_messages_for_summary(messages)
        if not source_text:
            return ""

        provider = provider_name or self.settings.default_provider
        provider_config = self.settings.get_provider_config(provider)
        model = ProviderRegistry.get_model(provider_config)
        agent = Agent(
            "openai:gpt-4o-mini",
            instructions=self._summary_instructions(),
            retries=1,
        )
        prompt = self._summary_prompt(source_text)

        result = await asyncio.wait_for(
            agent.run(prompt, model=model),
            timeout=self.settings.compression_timeout,
        )
        output = self._extract_output_text(result).strip()
        return output[: self.settings.compression_summary_max_chars].strip()

    def _summary_instructions(self) -> str:
        return (
            "你是 Minecraft Bedrock AI 助手的对话压缩器。"
            "只总结对后续回答有用的信息，不要编造。"
            "输出中文，使用以下小标题中实际有内容的项：事实、玩家偏好、未完成任务、重要约束。"
            "不要包含推理过程，不要输出 Markdown 表格。"
        )

    def _summary_prompt(self, source_text: str) -> str:
        max_chars = self.settings.compression_summary_max_chars
        return (
            "请总结以下较早的 Minecraft AI 对话，供后续轮次作为历史摘要使用。\n"
            f"摘要上限 {max_chars} 字。\n\n"
            f"较早对话:\n{source_text}"
        )

    @staticmethod
    def _extract_output_text(result: object) -> str:
        for field_name in ("output", "data"):
            if hasattr(result, field_name):
                value = getattr(result, field_name)
                return "" if value is None else str(value)
        return str(result)

    def create_summary_message(self, summary: str) -> ModelMessage | None:
        if not summary.strip():
            return None

        return ModelRequest(parts=[UserPromptPart(content=f"[历史摘要]\n{summary.strip()}", part_kind="user-prompt")])

    @staticmethod
    def is_summary_message(message: ModelMessage) -> bool:
        return any(
            getattr(part, "part_kind", None) == "user-prompt"
            and str(getattr(part, "content", "") or "").lstrip().startswith("[历史摘要]")
            for part in getattr(message, "parts", [])
        )


class ConversationManager:
    """
    对话管理器 - 管理对话历史的压缩和持久化存储

    功能:
    - 自动压缩：当对话历史超过阈值时自动压缩
    - 手动压缩：支持手动触发智能压缩
    - 持久化存储：保存和恢复对话历史
    - 列表/删除：管理已保存的对话
    """

    def __init__(
        self,
        broker,
        settings: Settings,
    ):
        self.broker = broker
        self.settings = settings
        self.compressor = ConversationCompressor(settings)
        self._storage_dir = Path("data/conversations")
        self._storage_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "conversation_manager_initialized",
            storage_dir=str(self._storage_dir),
            max_history_turns=settings.max_history_turns,
        )

    def _get_compression_threshold(self) -> int:
        """获取压缩触发阈值"""
        return max(1, int(self.settings.max_history_turns * self.settings.compression_trigger_ratio))

    def _get_compression_keep_recent_turns(self, threshold: int | None = None) -> int:
        threshold = threshold or self._get_compression_threshold()
        configured_keep_turns = self.settings.compression_keep_recent_turns
        if threshold <= 1:
            return 0
        return min(configured_keep_turns, threshold - 1)

    async def check_and_compress(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | bool | None = None,
        force: bool = False,
        provider_name: str | None = None,
    ) -> tuple[bool, str]:
        """
        检查是否需要压缩，必要时执行压缩

        Args:
            connection_id: 连接 ID
            player_name: 玩家名（多人会话隔离）
            force: 是否强制压缩

        Returns:
            (是否执行了压缩, 描述信息)
        """
        if isinstance(conversation_id, bool):
            force = conversation_id
            conversation_id = None

        history = self.broker.get_conversation_history(connection_id, player_name, conversation_id)
        threshold = self._get_compression_threshold()

        if not history:
            return False, "对话历史为空"

        if not self.settings.compression_enabled and not force:
            return False, "对话自动压缩已禁用"

        # 计算当前轮次
        turns = self._count_turns(history)

        if force:
            # 强制模式下，尝试提取摘要（即使轮数少于阈值）
            return await self.compress_history(
                connection_id,
                player_name,
                force=True,
                conversation_id=conversation_id,
                provider_name=provider_name,
            )
        elif turns >= threshold:
            return await self.compress_history(
                connection_id,
                player_name,
                conversation_id=conversation_id,
                provider_name=provider_name,
            )

        return False, f"当前 {turns} 轮，未达到压缩阈值 {threshold} 轮"

    async def compress_history(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | bool | None = None,
        force: bool = False,
        provider_name: str | None = None,
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
            player_name: 玩家名（多人会话隔离）
            force: 是否强制压缩（强制模式下即使轮数少于阈值也会提取摘要）

        Returns:
            (是否成功, 描述信息)
        """
        if isinstance(conversation_id, bool):
            force = conversation_id
            conversation_id = None

        history = self.broker.get_conversation_history(connection_id, player_name, conversation_id)
        generation = self._get_conversation_generation(connection_id, player_name, conversation_id)

        if not history:
            return False, "对话历史为空"

        current_turns = self._count_turns(history)

        older_messages, recent_messages = self.compressor.split_history(
            history,
            keep_turns=self._get_compression_keep_recent_turns(),
        )
        if not older_messages and not force:
            return False, f"当前 {current_turns} 轮，无需压缩"
        if not older_messages:
            return False, f"当前 {current_turns} 轮，无可压缩的旧历史"

        summary = ""
        used_llm = False
        fallback_used = False

        if self.settings.compression_enabled and older_messages:
            try:
                summary = await self.compressor.build_llm_summary(
                    older_messages,
                    provider_name=provider_name,
                )
                used_llm = bool(summary)
            except Exception as e:
                fallback_used = True
                logger.warning(
                    "conversation_llm_compression_failed",
                    connection_id=str(connection_id),
                    player=player_name,
                    conversation_id=conversation_id,
                    error=str(e),
                )

        if older_messages and not summary:
            summary = self.compressor.build_local_summary(older_messages)
            fallback_used = True

        compressed_history = list(recent_messages)
        summary_message = self.compressor.create_summary_message(summary)
        if summary_message:
            compressed_history.insert(0, summary_message)

        write_success = self._set_conversation_history(
            connection_id,
            player_name,
            compressed_history,
            conversation_id,
            expected_generation=generation,
        )
        if write_success is False:
            logger.warning(
                "conversation_compression_stale_generation",
                connection_id=str(connection_id),
                player=player_name,
                conversation_id=conversation_id,
                expected_generation=generation,
            )
            return False, "对话历史已更新，跳过过期压缩结果"

        new_turns = self._count_turns(compressed_history)
        logger.info(
            "conversation_compressed",
            connection_id=str(connection_id),
            player=player_name,
            conversation_id=conversation_id,
            original_turns=current_turns,
            new_turns=new_turns,
            summary_length=len(summary),
            used_llm=used_llm,
            fallback_used=fallback_used,
        )

        mode = "LLM摘要" if used_llm else "本地回退摘要"
        return True, f"压缩完成({mode}): {current_turns}轮 -> {new_turns}轮"

    def _get_conversation_generation(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | None,
    ) -> int | None:
        get_generation = getattr(self.broker, "get_conversation_generation", None)
        if get_generation is None:
            return None

        return get_generation(connection_id, player_name, conversation_id)

    def _set_conversation_history(
        self,
        connection_id: UUID,
        player_name: str | None,
        history: list[ModelMessage],
        conversation_id: str | None,
        expected_generation: int | None = None,
    ) -> bool | None:
        set_history = self.broker.set_conversation_history
        if self._supports_expected_generation(set_history):
            return set_history(
                connection_id,
                player_name,
                history,
                conversation_id,
                expected_generation=expected_generation,
            )

        return set_history(connection_id, player_name, history, conversation_id)

    @staticmethod
    def _supports_expected_generation(set_history) -> bool:
        try:
            signature = inspect.signature(set_history)
        except (TypeError, ValueError):
            return False

        return (
            "expected_generation" in signature.parameters
            or any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values())
        )

    def _count_turns(self, history: list[ModelMessage]) -> int:
        """计算对话轮次（用户提问次数）"""
        return self.compressor.count_turns(history)

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
        older, _recent = self.compressor._split_history_by_keep_turns(history, keep_turns)
        return self.compressor.build_local_summary(older)

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
            return self.compressor.create_summary_message(summary)
        except Exception as e:
            logger.warning("create_summary_message_failed", error=str(e))
            return None

    async def save_conversation(
        self,
        connection_id: UUID,
        player_name: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        conversation_id: str | None = None,
        template: str = "default",
        custom_variables: dict[str, str] | None = None,
    ) -> tuple[bool, str]:
        """
        保存当前对话到持久化存储

        Args:
            connection_id: 连接 ID
            player_name: 玩家名称（同时是会话分桶键）
            provider: LLM 提供商
            model: 模型名称
            template: 使用的模板
            custom_variables: 自定义变量

        Returns:
            (是否成功, 消息/会话ID)
        """
        history = self.broker.get_conversation_history(connection_id, player_name, conversation_id)

        if not history:
            return False, "对话历史为空，无法保存"

        try:
            # 序列化消息：使用 dump_json 序列化为 JSON 字节
            messages_json_bytes = ModelMessagesTypeAdapter.dump_json(history)
            messages_json_str = messages_json_bytes.decode("utf-8")

            # 生成会话ID
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            session_id = f"{connection_id}_{timestamp}_{uuid4().hex[:8]}"

            metadata = self.broker.get_conversation_metadata(
                connection_id, player_name, conversation_id
            )
            title = metadata.title or "未命名"

            # 构建保存数据
            saved_data = {
                "connection_id": str(connection_id),
                "player_name": player_name,
                "conversation_id": conversation_id or "default",
                "title": title,
                "provider": provider or self.settings.default_provider,
                "model": model or self.settings.get_provider_config(
                    provider or self.settings.default_provider
                ).model,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "message_count": len(history),
                "messages": json.loads(messages_json_str),
                "metadata": {
                    "title": title,
                    "template": template,
                    "conversation_id": conversation_id or "default",
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
        player_name: str | None = None,
        conversation_id: str | None = None,
    ) -> tuple[bool, str]:
        """
        恢复对话到指定连接

        Args:
            connection_id: 连接 ID
            session_id: 会话 ID
            player_name: 玩家名（恢复到该玩家的桶里）

        Returns:
            (是否成功, 消息)
        """
        success, result = await self.load_conversation(session_id)

        if not success:
            return False, result

        messages = result if isinstance(result, list) else []

        bump_epoch = getattr(self.broker, "bump_conversation_invalidation_epoch", None)
        if bump_epoch is not None:
            bump_epoch(connection_id, player_name, conversation_id)

        # 设置到 broker（按 player_name 分桶）
        self.broker.set_conversation_history(connection_id, player_name, messages, conversation_id)

        logger.info(
            "conversation_restored",
            connection_id=str(connection_id),
            player=player_name,
            conversation_id=conversation_id,
            session_id=session_id,
            message_count=len(messages),
        )

        return True, f"已恢复会话 {session_id} 到对话 {conversation_id or 'default'}，共 {len(messages)} 条消息"

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
                        "conversation_id": data.get("conversation_id", data.get("metadata", {}).get("conversation_id", "default")),
                        "title": data.get("title", data.get("metadata", {}).get("title")),
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
        删除指定的已保存对话文件；不影响当前连接内的运行时对话。

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
            conversation_id = conv.get("conversation_id", "default")
            title = conv.get("title") or "未命名"
            provider = conv.get("provider", "deepseek")
            model = conv.get("model", "")
            count = conv.get("message_count", 0)
            updated = conv.get("updated_at", "")[:16]  # 只取日期时间

            lines.append(
                f"{i + 1}. [{session_id}] {title} | {player} | {provider}/{model} | "
                f"对话:{conversation_id} | {count}条消息 | {updated}"
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
