"""Agent Worker - 从队列消费请求并处理"""

import asyncio
from uuid import UUID

import httpx

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
                # 从队列获取请求
                item = await self.broker.get_request()

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

        logger.info(
            "processing_chat_request",
            worker_id=self.worker_id,
            connection_id=str(connection_id),
            content_length=len(request.content),
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
        try:
            async for event in stream_chat(request.content, deps, model):
                chunk = StreamChunk(
                    connection_id=connection_id,
                    chunk_type=event.event_type,  # type: ignore
                    content=event.content,
                    sequence=sequence,
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
