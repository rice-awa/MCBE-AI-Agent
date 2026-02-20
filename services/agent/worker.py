"""Agent Worker - 从队列消费请求并处理"""

import asyncio
import copy
import dataclasses
import time
from uuid import UUID

import httpx
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ThinkingPart

from core.queue import MessageBroker
from services.agent.core import stream_chat, _extract_exception_details
from services.agent.providers import ProviderRegistry
from models.messages import ChatRequest, StreamChunk
from models.agent import (
    AgentDependencies,
    ContextInfo,
    MCColor,
    MCPrefix,
    truncate_text,
    format_tool_call_message,
    format_tool_result_message,
)
from config.settings import Settings
from config.logging import get_logger

logger = get_logger(__name__)


class AgentWorker:
    """
    Agent 工作协程 - 从队列消费请求并处理

    架构:
    - 从 MessageBroker 的请求队列获取任务
    - 调用 PydanticAI Agent 进行流式处理
    - 将响应发送到对应连接的响应队列
    """

    def __init__(
        self,
        broker: MessageBroker,
        settings: Settings,
        worker_id: int = 0,
    ):
        self.broker = broker
        self.settings = settings
        self.worker_id = worker_id
        self._running = False
        self._http_client: httpx.AsyncClient | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """启动 Worker"""
        if self._running:
            logger.warning("worker_already_running", worker_id=self.worker_id)
            return

        self._running = True
        self._http_client = httpx.AsyncClient(timeout=60)

        logger.info("worker_started", worker_id=self.worker_id)

        # 创建后台任务
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """停止 Worker"""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self._http_client:
            await self._http_client.aclose()

        logger.info("worker_stopped", worker_id=self.worker_id)

    async def _run(self) -> None:
        """Worker 主循环"""
        while self._running:
            try:
                # 从队列获取请求（带超时，避免无限期阻塞）
                try:
                    item = await asyncio.wait_for(
                        self.broker.get_request(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    # 超时后继续循环，检查 _running 状态
                    continue

                # 处理请求
                await self._process_request(item)

                # 标记任务完成
                self.broker.request_done()

            except asyncio.CancelledError:
                logger.info("worker_cancelled", worker_id=self.worker_id)
                break
            except Exception as e:
                logger.error(
                    "worker_error",
                    worker_id=self.worker_id,
                    error=str(e),
                    exc_info=True,
                )
                # 继续运行，不因单个错误而停止

    async def _process_request(self, item: any) -> None:
        """
        处理单个请求

        Args:
            item: QueueItem 包含 connection_id 和 payload (ChatRequest)
        """
        request: ChatRequest = item.payload
        connection_id: UUID = item.connection_id

        # 同一连接请求串行处理，避免多 worker 并发导致上下文乱序。
        connection_lock = self.broker.get_connection_lock(connection_id)
        async with connection_lock:
            await self._process_request_locked(request, connection_id)

    async def _process_request_locked(
        self,
        request: ChatRequest,
        connection_id: UUID,
    ) -> None:
        """处理单个请求（已持有连接锁）"""

        logger.info(
            "processing_chat_request",
            worker_id=self.worker_id,
            connection_id=str(connection_id),
            content_length=len(request.content),
        )

        message_history: list[ModelMessage] | None = None
        if request.use_context:
            raw_history = self.broker.get_conversation_history(connection_id)
            message_history, cleared_count = self._strip_reasoning_content(raw_history)
            logger.debug(
                "chat_history_loaded",
                worker_id=self.worker_id,
                connection_id=str(connection_id),
                history_message_count=len(message_history),
                cleared_reasoning_content_count=cleared_count,
            )

        # 构建获取上下文信息的回调
        def get_context_info() -> ContextInfo | None:
            """获取当前对话的上下文使用信息"""
            if not request.use_context:
                return None

            history = self.broker.get_conversation_history(connection_id)
            message_count = len(history) if history else 0
            # 简单估算：平均每条消息 100 tokens
            estimated_tokens = message_count * 100
            # 获取模型最大上下文
            provider_name = request.provider or self.settings.default_provider
            provider_config = self.settings.get_provider_config(provider_name)
            max_tokens = provider_config.context_window

            return ContextInfo(
                message_count=message_count,
                estimated_tokens=estimated_tokens,
                max_tokens=max_tokens,
            )

        # 构建依赖
        deps = AgentDependencies(
            connection_id=connection_id,
            player_name=request.player_name or "Player",
            settings=self.settings,
            http_client=self._http_client,  # type: ignore
            send_to_game=self._create_send_callback(connection_id),
            run_command=self._create_command_callback(connection_id),
            provider=request.provider or self.settings.default_provider,
            get_context_info=get_context_info,
        )

        # 获取模型
        try:
            provider_name = request.provider or self.settings.default_provider
            provider_config = self.settings.get_provider_config(provider_name)
            model = ProviderRegistry.get_model(provider_config)

            logger.debug(
                "using_provider",
                provider=provider_name,
                model=provider_config.model,
            )

        except Exception as e:
            logger.error(
                "provider_error",
                provider=provider_name,
                error=str(e),
            )
            # 发送错误消息
            await self._send_error_chunk(connection_id, str(e), 0)
            return

        # 流式处理
        sequence = 0
        event_count = 0
        response_parts: list[str] = []
        reasoning_parts: list[str] = []
        start_time = time.monotonic()

        # 获取已初始化的 Agent 实例（带有 MCP 工具集）
        from services.agent.core import get_agent_manager, _is_mcp_timeout_error
        from services.agent.mcp import get_mcp_manager

        agent_manager = get_agent_manager()
        agent = agent_manager.get_agent()

        # 尝试获取 MCP 管理器以跟踪服务器状态
        mcp_manager = get_mcp_manager()

        try:
            async for event in stream_chat(
                request.content,
                deps,
                model,
                message_history=message_history,
                agent=agent,
            ):
                event_count += 1
                if event.event_type == "content" and event.content:
                    response_parts.append(event.content)
                elif event.event_type == "reasoning" and event.content:
                    reasoning_parts.append(event.content)

                # 处理工具调用事件 - 发送到游戏显示
                elif event.event_type == "tool_call":
                    tool_name = event.metadata.get("tool_name", "unknown") if event.metadata else "unknown"
                    tool_args = event.metadata.get("args") if event.metadata else None
                    # 确保 tool_args 是字典类型（Pydantic AI 有时会返回 JSON 字符串）
                    if isinstance(tool_args, str):
                        import json
                        try:
                            tool_args = json.loads(tool_args)
                        except json.JSONDecodeError:
                            tool_args = {"raw": tool_args}
                    tool_msg = format_tool_call_message(tool_name, tool_args)
                    tool_chunk = StreamChunk(
                        connection_id=connection_id,
                        chunk_type="tool_call",
                        content=tool_msg,
                        sequence=sequence,
                        delivery=request.delivery,
                        tool_name=tool_name,
                        tool_args=tool_args,
                    )
                    await self.broker.send_response(connection_id, tool_chunk)
                    sequence += 1

                # 处理工具返回事件 - 根据配置决定是否发送到游戏显示
                elif event.event_type == "tool_result":
                    # 只有在 tool_response_verbose 为 True 时才显示工具返回结果
                    if self.settings.tool_response_verbose:
                        tool_name = event.metadata.get("tool_name") if event.metadata else None
                        result_content = event.content
                        tool_result_msg = format_tool_result_message(
                            tool_name or "tool",
                            result_content,
                            max_length=80,
                        )
                        result_chunk = StreamChunk(
                            connection_id=connection_id,
                            chunk_type="tool_result",
                            content=tool_result_msg,
                            sequence=sequence,
                            delivery=request.delivery,
                            tool_name=tool_name,
                            tool_result_preview=truncate_text(result_content, 80),
                        )
                        await self.broker.send_response(connection_id, result_chunk)
                        sequence += 1

                if event.metadata and event.metadata.get("is_complete"):
                    all_messages = event.metadata.get("all_messages")
                    if request.use_context and isinstance(all_messages, list):
                        if self.broker.get_response_queue(connection_id) is not None:
                            trimmed_history = self._trim_history(
                                all_messages,
                                self.settings.max_history_turns,
                            )
                            trimmed_history, cleared_count = self._strip_reasoning_content(
                                trimmed_history
                            )
                            self.broker.set_conversation_history(
                                connection_id,
                                trimmed_history,
                            )
                            logger.debug(
                                "chat_history_updated",
                                worker_id=self.worker_id,
                                connection_id=str(connection_id),
                                history_message_count=len(trimmed_history),
                                cleared_reasoning_content_count=cleared_count,
                            )

                            # 自动压缩检查：当对话历史超过阈值的 80% 时自动压缩
                            from core.conversation import get_conversation_manager

                            conv_manager = get_conversation_manager(self.broker, self.settings)
                            compressed, msg = await conv_manager.check_and_compress(connection_id)
                            if compressed:
                                logger.debug(
                                    "auto_compression_triggered",
                                    worker_id=self.worker_id,
                                    connection_id=str(connection_id),
                                    message=msg,
                                )

                    response_text = "".join(response_parts)
                    reasoning_text = "".join(reasoning_parts)
                    duration_ms = int((time.monotonic() - start_time) * 1000)
                    logger.info(
                        "chat_response_complete",
                        worker_id=self.worker_id,
                        connection_id=str(connection_id),
                        response=response_text,
                        response_length=len(response_text),
                        reasoning_length=len(reasoning_text),
                        chunk_count=event_count,
                        duration_ms=duration_ms,
                        usage=event.metadata.get("usage"),
                        tool_calls=event.metadata.get("tool_calls"),
                        tool_returns=event.metadata.get("tool_returns"),
                        tool_fallback_used=event.metadata.get("tool_fallback_used"),
                    )
                elif event.event_type == "error":
                    response_text = "".join(response_parts)
                    logger.error(
                        "chat_response_error",
                        worker_id=self.worker_id,
                        connection_id=str(connection_id),
                        error=event.content,
                        response=response_text,
                        response_length=len(response_text),
                    )
                    # 错误事件需要发送到游戏
                    chunk = StreamChunk(
                        connection_id=connection_id,
                        chunk_type=event.event_type,  # type: ignore
                        content=event.content,
                        sequence=sequence,
                        delivery=request.delivery,
                    )
                    await self.broker.send_response(connection_id, chunk)
                    sequence += 1

                # content 和 reasoning 事件需要发送到游戏
                elif event.event_type in ("content", "reasoning"):
                    if event.content:
                        chunk = StreamChunk(
                            connection_id=connection_id,
                            chunk_type=event.event_type,  # type: ignore
                            content=event.content,
                            sequence=sequence,
                            delivery=request.delivery,
                        )
                        await self.broker.send_response(connection_id, chunk)
                        sequence += 1
                # tool_call 事件已在上面处理并发送
                # tool_result 事件已根据配置决定是否发送
                # is_complete 事件不需要发送到游戏

        except Exception as e:
            error_detail = _extract_exception_details(e)

            # 检查是否是 MCP 超时错误，如果是则更新 MCP 服务器状态
            if _is_mcp_timeout_error(e):
                logger.warning(
                    "mcp_timeout_detected_in_worker",
                    worker_id=self.worker_id,
                    connection_id=str(connection_id),
                    error=error_detail,
                )
                # 标记所有 MCP 服务器为失败（因为我们不知道具体是哪个）
                for server_name in mcp_manager.servers.keys():
                    mcp_manager.mark_server_failed(server_name, f"连接超时: {error_detail}")
                # 注意：core.py 中的降级机制会自动重试，这里不需要额外处理

            logger.error(
                "stream_processing_error",
                worker_id=self.worker_id,
                connection_id=str(connection_id),
                error=error_detail,
                exc_info=True,
            )
            await self._send_error_chunk(connection_id, error_detail, sequence)

    @staticmethod
    def _trim_history(
        messages: list[ModelMessage],
        max_turns: int,
    ) -> list[ModelMessage]:
        """按"用户轮次"裁剪历史，保留最近 N 轮对话。

        注意：确保工具调用链的完整性，不从中间切断 tool-call/tool-return 对。
        """
        if max_turns <= 0:
            return []

        if not messages:
            return []

        # 从后向前查找用户轮次
        user_turns = 0
        cut_idx = 0

        for idx in range(len(messages) - 1, -1, -1):
            message = messages[idx]
            if isinstance(message, ModelRequest):
                has_user_prompt = any(
                    getattr(part, "part_kind", None) == "user-prompt"
                    for part in message.parts
                )
                if has_user_prompt:
                    user_turns += 1
                    if user_turns == max_turns:
                        # 找到第 N 个用户轮次的起始位置
                        cut_idx = idx
                        break

        # 如果没有找到足够的轮次，保留全部
        if user_turns < max_turns:
            return list(messages)

        # 确保不从 tool-call/tool-return 链中间切断
        # 向前查找，确保包含完整的工具调用链
        while cut_idx > 0:
            prev_message = messages[cut_idx - 1]
            # 检查前一个消息是否包含未完成的工具调用
            if isinstance(prev_message, ModelResponse):
                has_tool_call = any(
                    getattr(part, "part_kind", None) == "tool-call"
                    for part in prev_message.parts
                )
                if has_tool_call:
                    # 需要包含这个响应，因为当前请求可能是对其的 tool-return
                    cut_idx -= 1
                    continue

            # 检查是否是系统提示词
            if isinstance(prev_message, ModelRequest):
                has_system_prompt = any(
                    getattr(part, "part_kind", None) == "system-prompt"
                    for part in prev_message.parts
                )
                if has_system_prompt:
                    cut_idx -= 1
                    continue

            break

        return list(messages[cut_idx:])

    @classmethod
    def _strip_reasoning_content(
        cls,
        messages: list[ModelMessage],
    ) -> tuple[list[ModelMessage], int]:
        """清空历史中的推理内容（ThinkingPart/content 与 reasoning_content），减少后续请求带宽。"""
        sanitized_messages: list[ModelMessage] = []
        cleared_count = 0

        for message in messages:
            sanitized_message, message_cleared = cls._sanitize_model_message(message)
            if sanitized_message is None:
                sanitized_message = copy.deepcopy(message)
                message_cleared = cls._clear_reasoning_content_in_object(sanitized_message)

            sanitized_messages.append(sanitized_message)
            cleared_count += message_cleared

        return sanitized_messages, cleared_count

    @classmethod
    def _sanitize_model_message(
        cls,
        message: ModelMessage,
    ) -> tuple[ModelMessage | None, int]:
        """优先走轻量路径处理标准 ModelMessage，避免整条历史深拷贝。"""
        if not isinstance(message, ModelResponse):
            return None, 0

        updated_parts: list[object] | None = None
        cleared_count = 0

        for index, part in enumerate(message.parts):
            updated_part = part

            if isinstance(part, ThinkingPart) and part.content:
                updated_part = dataclasses.replace(part, content="")
                cleared_count += 1

            if hasattr(updated_part, "reasoning_content"):
                reasoning_content = getattr(updated_part, "reasoning_content", None)
                if isinstance(reasoning_content, str) and reasoning_content:
                    if dataclasses.is_dataclass(updated_part):
                        updated_part = dataclasses.replace(updated_part, reasoning_content="")
                    else:
                        updated_part = copy.copy(updated_part)
                        setattr(updated_part, "reasoning_content", "")
                    cleared_count += 1

            if updated_part is not part:
                if updated_parts is None:
                    updated_parts = list(message.parts)
                updated_parts[index] = updated_part

        if updated_parts is None:
            return message, 0

        return dataclasses.replace(message, parts=updated_parts), cleared_count

    @classmethod
    def _clear_reasoning_content_in_object(
        cls,
        obj: object,
        visited: set[int] | None = None,
    ) -> int:
        """递归清空对象图中的 reasoning_content 字段。"""
        if visited is None:
            visited = set()

        obj_id = id(obj)
        if obj_id in visited:
            return 0
        visited.add(obj_id)

        cleared_count = 0

        if isinstance(obj, dict):
            for key, value in obj.items():
                if key == "reasoning_content" and isinstance(value, str) and value:
                    obj[key] = ""
                    cleared_count += 1
                else:
                    cleared_count += cls._clear_reasoning_content_in_object(value, visited)
            return cleared_count

        if isinstance(obj, list):
            for item in obj:
                cleared_count += cls._clear_reasoning_content_in_object(item, visited)
            return cleared_count

        if isinstance(obj, tuple):
            for item in obj:
                cleared_count += cls._clear_reasoning_content_in_object(item, visited)
            return cleared_count

        if hasattr(obj, "reasoning_content"):
            value = getattr(obj, "reasoning_content", None)
            if isinstance(value, str) and value:
                try:
                    setattr(obj, "reasoning_content", "")
                    cleared_count += 1
                except (AttributeError, TypeError, ValueError):
                    pass

        if hasattr(obj, "__dict__"):
            for value in vars(obj).values():
                cleared_count += cls._clear_reasoning_content_in_object(value, visited)

        return cleared_count

    def _create_send_callback(self, connection_id: UUID):
        """创建发送消息到游戏的回调"""

        async def send_to_game(message: str) -> None:
            # 发送到响应队列，由 WebSocket Handler 处理
            await self.broker.send_response(
                connection_id,
                {"type": "game_message", "content": message},
            )

        return send_to_game

    def _create_command_callback(self, connection_id: UUID):
        """创建执行命令的回调"""

        async def run_command(command: str) -> str:
            # 发送到响应队列，由 WebSocket Handler 处理并等待命令结果
            loop = asyncio.get_running_loop()
            future: asyncio.Future[str] = loop.create_future()
            sent = await self.broker.send_response(
                connection_id,
                {
                    "type": "run_command",
                    "command": command,
                    "result_future": future,
                },
            )

            if not sent:
                return "命令执行失败: 连接不存在"

            try:
                return await asyncio.wait_for(future, timeout=10.0)
            except asyncio.TimeoutError:
                return "命令执行超时: 未收到游戏侧 commandResponse"

        return run_command

    async def _send_error_chunk(
        self, connection_id: UUID, error: str, sequence: int
    ) -> None:
        """发送错误消息块"""
        chunk = StreamChunk(
            connection_id=connection_id,
            chunk_type="error",
            content=f"错误: {error}",
            sequence=sequence,
        )
        await self.broker.send_response(connection_id, chunk)
