"""WebSocket 连接管理"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from websockets.server import WebSocketServerProtocol

from core.queue import MessageBroker
from models.messages import StreamChunk
from models.minecraft import MinecraftCommand
from config.logging import get_logger

logger = get_logger(__name__)
ws_raw_logger = get_logger("websocket.raw")


@dataclass
class ConnectionState:
    """连接状态"""

    id: UUID = field(default_factory=uuid4)
    websocket: WebSocketServerProtocol | None = None
    authenticated: bool = False
    player_name: str | None = None
    connected_at: datetime = field(default_factory=datetime.now)
    context_enabled: bool = True
    response_queue: asyncio.Queue | None = None
    current_provider: str | None = None
    pending_command_futures: dict[str, asyncio.Future[str]] = field(default_factory=dict)


class ConnectionManager:
    """
    WebSocket 连接管理器

    职责:
    - 管理活跃连接
    - 注册/注销连接
    - 启动独立的响应发送协程
    """

    def __init__(self, broker: MessageBroker):
        self.broker = broker
        self._connections: dict[UUID, ConnectionState] = {}
        self._sender_tasks: dict[UUID, asyncio.Task] = {}

    async def register(
        self, websocket: WebSocketServerProtocol
    ) -> ConnectionState:
        """
        注册新连接

        Args:
            websocket: WebSocket 连接

        Returns:
            连接状态对象
        """
        state = ConnectionState(websocket=websocket)
        state.response_queue = self.broker.register_connection(state.id)
        self._connections[state.id] = state

        # 启动响应发送任务
        task = asyncio.create_task(self._response_sender(state))
        self._sender_tasks[state.id] = task

        logger.info(
            "connection_registered",
            connection_id=str(state.id),
            total_connections=len(self._connections),
        )

        return state

    async def unregister(self, connection_id: UUID) -> None:
        """
        注销连接

        Args:
            connection_id: 连接 ID
        """
        if state := self._connections.pop(connection_id, None):
            for future in state.pending_command_futures.values():
                if not future.done():
                    future.set_result("命令执行失败: 连接已关闭")
            state.pending_command_futures.clear()

            # 取消响应发送任务
            if task := self._sender_tasks.pop(connection_id, None):
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            # 从消息代理注销
            self.broker.unregister_connection(connection_id)

            logger.info(
                "connection_unregistered",
                connection_id=str(connection_id),
                remaining_connections=len(self._connections),
            )

    def get_connection(self, connection_id: UUID) -> ConnectionState | None:
        """获取连接状态"""
        return self._connections.get(connection_id)

    def get_all_connections(self) -> list[ConnectionState]:
        """获取所有连接"""
        return list(self._connections.values())

    @property
    def connection_count(self) -> int:
        """活跃连接数"""
        return len(self._connections)

    async def shutdown_all(self) -> None:
        """
        关闭所有连接

        在服务器关闭时调用，以确保所有连接都被正常关闭
        """
        logger.info(
            "shutting_down_all_connections",
            connection_count=len(self._connections)
        )

        # 取消所有响应发送任务
        for connection_id, task in list(self._sender_tasks.items()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # 关闭所有 WebSocket 连接
        for state in list(self._connections.values()):
            if state.websocket:
                try:
                    await state.websocket.close()
                except Exception as e:
                    logger.error(
                        "error_closing_websocket",
                        connection_id=str(state.id),
                        error=str(e)
                    )

        # 清空所有连接
        self._connections.clear()
        self._sender_tasks.clear()

        logger.info("all_connections_closed")

    async def _response_sender(self, state: ConnectionState) -> None:
        """
        响应发送协程 - 独立于 LLM 请求

        这个协程持续从响应队列读取消息并发送到 Minecraft
        关键设计: 不阻塞在 LLM 请求上，保持 WebSocket 通信流畅
        """
        logger.info(
            "response_sender_started",
            connection_id=str(state.id),
        )

        while state.id in self._connections:
            try:
                # 使用超时避免永久阻塞（缩短超时时间以便更快响应关闭）
                response = await asyncio.wait_for(
                    state.response_queue.get(),  # type: ignore
                    timeout=0.5,  # 从 1.0 秒减少到 0.5 秒
                )

                await self._handle_response(state, response)

            except asyncio.TimeoutError:
                # 超时是正常的，继续循环
                continue
            except asyncio.CancelledError:
                logger.info(
                    "response_sender_cancelled",
                    connection_id=str(state.id),
                )
                break
            except Exception as e:
                logger.error(
                    "response_sender_error",
                    connection_id=str(state.id),
                    error=str(e),
                    exc_info=True,
                )
                # 发生错误但不中断，继续处理

        logger.info(
            "response_sender_stopped",
            connection_id=str(state.id),
        )

    async def _handle_response(
        self, state: ConnectionState, response: any
    ) -> None:
        """
        处理响应消息

        Args:
            state: 连接状态
            response: 响应数据
        """
        if isinstance(response, StreamChunk):
            await self._send_stream_chunk(state, response)
        elif isinstance(response, dict):
            # 处理特殊消息类型
            msg_type = response.get("type")
            if msg_type == "game_message":
                await self._send_game_message(state, response["content"])
            elif msg_type == "run_command":
                await self._run_command(
                    state,
                    response["command"],
                    response.get("result_future"),
                )
        else:
            logger.warning(
                "unknown_response_type",
                connection_id=str(state.id),
                response_type=type(response).__name__,
            )

    async def _send_stream_chunk(
        self, state: ConnectionState, chunk: StreamChunk
    ) -> None:
        """发送流式响应块到 Minecraft"""
        if chunk.chunk_type == "thinking_start":
            message = "|think-start|\n"
        elif chunk.chunk_type == "thinking_end":
            message = "|think-end|\n"
        elif chunk.chunk_type == "content":
            message = chunk.content
        elif chunk.chunk_type == "reasoning":
            # DeepSeek reasoning content
            message = chunk.content
        elif chunk.chunk_type == "error":
            message = f"❌ {chunk.content}"
        else:
            message = chunk.content

        if not message:
            return

        if chunk.delivery == "scriptevent":
            await self._send_script_event(state, message)
        else:
            await self._send_game_message(state, message)

    async def _send_game_message(
        self, state: ConnectionState, message: str
    ) -> None:
        """向游戏发送消息（使用 tellraw）"""
        if not state.websocket:
            return

        try:
            command = MinecraftCommand.create_tellraw(message, color="§a")
            payload = command.model_dump_json(exclude_none=True)
            await state.websocket.send(payload)
            ws_raw_logger.info(
                "websocket_response_sent",
                connection_id=str(state.id),
                source="stream_tellraw",
                payload=payload,
            )

            logger.debug(
                "game_message_sent",
                connection_id=str(state.id),
                message_length=len(message),
            )
        except Exception as e:
            logger.error(
                "send_game_message_error",
                connection_id=str(state.id),
                error=str(e),
            )

    async def _run_command(
        self,
        state: ConnectionState,
        command: str,
        result_future: asyncio.Future[str] | None = None,
    ) -> None:
        """执行 Minecraft 命令"""
        if not state.websocket:
            if result_future and not result_future.done():
                result_future.set_result("命令执行失败: WebSocket 未连接")
            return

        try:
            cmd = MinecraftCommand.create_raw(command)
            payload = cmd.model_dump_json(exclude_none=True)
            if result_future:
                state.pending_command_futures[cmd.header.requestId] = result_future

            await state.websocket.send(payload)
            ws_raw_logger.info(
                "websocket_response_sent",
                connection_id=str(state.id),
                source="stream_run_command",
                payload=payload,
            )

            logger.debug(
                "command_executed",
                connection_id=str(state.id),
                command=command,
                request_id=cmd.header.requestId,
            )
        except Exception as e:
            if result_future and not result_future.done():
                result_future.set_result(f"命令执行失败: {str(e)}")
            logger.error(
                "run_command_error",
                connection_id=str(state.id),
                command=command,
                error=str(e),
            )

    async def _send_script_event(
        self,
        state: ConnectionState,
        message: str,
        message_id: str = "server:data",
    ) -> None:
        """通过 scriptevent 发送消息"""
        if not state.websocket:
            return

        try:
            command = MinecraftCommand.create_scriptevent(message, message_id)
            payload = command.model_dump_json(exclude_none=True)
            await state.websocket.send(payload)
            ws_raw_logger.info(
                "websocket_response_sent",
                connection_id=str(state.id),
                source="stream_scriptevent",
                payload=payload,
            )

            logger.debug(
                "script_event_sent",
                connection_id=str(state.id),
                message_length=len(message),
            )
        except Exception as e:
            logger.error(
                "send_script_event_error",
                connection_id=str(state.id),
                error=str(e),
            )
