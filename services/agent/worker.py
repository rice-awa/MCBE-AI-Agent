"""Agent Worker - 从队列消费请求并处理"""

import asyncio
import copy
import time
from uuid import UUID

import httpx
from pydantic_ai.messages import ModelMessage, ModelRequest

from core.queue import MessageBroker
from services.agent.core import stream_chat
from services.agent.providers import ProviderRegistry
from models.messages import ChatRequest, StreamChunk
from models.agent import AgentDependencies
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

        # 构建依赖
        deps = AgentDependencies(
            connection_id=connection_id,
            player_name=request.player_name or "Player",
            settings=self.settings,
            http_client=self._http_client,  # type: ignore
            send_to_game=self._create_send_callback(connection_id),
            run_command=self._create_command_callback(connection_id),
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
        try:
            async for event in stream_chat(
                request.content,
                deps,
                model,
                message_history=message_history,
            ):
                event_count += 1
                if event.event_type == "content" and event.content:
                    response_parts.append(event.content)
                elif event.event_type == "reasoning" and event.content:
                    reasoning_parts.append(event.content)

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

                chunk = StreamChunk(
                    connection_id=connection_id,
                    chunk_type=event.event_type,  # type: ignore
                    content=event.content,
                    sequence=sequence,
                    delivery=request.delivery,
                )

                await self.broker.send_response(connection_id, chunk)
                sequence += 1

        except Exception as e:
            logger.error(
                "stream_processing_error",
                worker_id=self.worker_id,
                connection_id=str(connection_id),
                error=str(e),
                exc_info=True,
            )
            await self._send_error_chunk(connection_id, str(e), sequence)

    @staticmethod
    def _trim_history(
        messages: list[ModelMessage],
        max_turns: int,
    ) -> list[ModelMessage]:
        """按“用户轮次”裁剪历史，保留最近 N 轮对话。"""
        if max_turns <= 0:
            return []

        user_turns = 0
        for idx in range(len(messages) - 1, -1, -1):
            message = messages[idx]
            if isinstance(message, ModelRequest):
                has_user_prompt = any(
                    getattr(part, "part_kind", None) == "user-prompt"
                    for part in message.parts
                )
                if has_user_prompt:
                    user_turns += 1
                    if user_turns > max_turns:
                        start_idx = idx + 1
                        while start_idx < len(messages):
                            next_message = messages[start_idx]
                            if isinstance(next_message, ModelRequest):
                                has_next_prompt = any(
                                    getattr(part, "part_kind", None) == "user-prompt"
                                    for part in next_message.parts
                                )
                                if has_next_prompt:
                                    break
                            start_idx += 1
                        return list(messages[start_idx:])

        return list(messages)

    @classmethod
    def _strip_reasoning_content(
        cls,
        messages: list[ModelMessage],
    ) -> tuple[list[ModelMessage], int]:
        """深拷贝并清空历史中的 reasoning_content，减少后续请求带宽。"""
        sanitized_messages = copy.deepcopy(messages)
        cleared_count = 0
        for message in sanitized_messages:
            cleared_count += cls._clear_reasoning_content_in_object(message)
        return sanitized_messages, cleared_count

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

        async def run_command(command: str) -> None:
            # 发送到响应队列，由 WebSocket Handler 处理
            await self.broker.send_response(
                connection_id,
                {"type": "run_command", "command": command},
            )

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
