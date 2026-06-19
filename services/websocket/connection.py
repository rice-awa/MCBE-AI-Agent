"""WebSocket 连接管理"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from websockets.server import WebSocketServerProtocol

from core.queue import MessageBroker
from core.session import DEFAULT_PLAYER_KEY
from models.constants import DEFAULT_PLAYER_DISPLAY_NAME
from models.messages import StreamChunk, SystemNotification
from models.agent import MCColor, MCPrefix
from services.agent.prompt import get_prompt_manager
from services.websocket.delivery import AiResponseSync, McbeOutboundDelivery
from config.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PlayerSession:
    """单个玩家在某连接上的会话状态（按玩家隔离的可变设置）。"""

    player_name: str
    context_enabled: bool = True
    current_provider: str | None = None
    current_template: str = "default"
    custom_variables: dict[str, str] = field(default_factory=dict)


@dataclass
class ConnectionState:
    """连接状态。

    单条 WebSocket 连接被多个玩家共享（MCBE 服务器模型决定）。
    所有"单值"字段（context_enabled / current_provider / current_template /
    custom_variables）需要按玩家隔离，集中存放在 ``_player_sessions`` 中；
    顶层的 ``player_name`` 仅作为"最近一次发言者"的便捷指针。
    """

    id: UUID = field(default_factory=uuid4)
    websocket: WebSocketServerProtocol | None = None
    authenticated: bool = False
    # 最近一次产生消息的玩家名，仅用于日志和兼容旧路径，禁止在多人逻辑里读它做分支。
    player_name: str | None = None
    connected_at: datetime = field(default_factory=datetime.now)
    response_queue: asyncio.Queue | None = None
    pending_command_futures: dict[str, asyncio.Future[str]] = field(default_factory=dict)
    ai_broadcast_all: bool = False
    ai_broadcast_players: set[str] = field(default_factory=set)
    # 按玩家隔离的会话状态
    _player_sessions: dict[str, PlayerSession] = field(default_factory=dict)

    def get_player_session(self, player_name: str | None) -> PlayerSession:
        """获取指定玩家的会话状态；不存在则按默认值创建。"""
        key = player_name or DEFAULT_PLAYER_KEY
        session = self._player_sessions.get(key)
        if session is None:
            session = PlayerSession(player_name=key)
            self._player_sessions[key] = session
        return session

    def should_broadcast_ai_chat(self, player_name: str | None) -> bool:
        if self.ai_broadcast_all:
            return True
        return bool(player_name and player_name in self.ai_broadcast_players)

    def all_player_sessions(self) -> list[PlayerSession]:
        """快照式列出连接下所有玩家会话。"""
        return list(self._player_sessions.values())


class ConnectionManager:
    """
    WebSocket 连接管理器

    职责:
    - 管理活跃连接
    - 注册/注销连接
    - 启动独立的响应发送协程
    """

    def __init__(self, broker: MessageBroker, dev_mode: bool = False):
        self.broker = broker
        self.dev_mode = dev_mode
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
            dev_mode=self.dev_mode,
        )

        return state

    async def unregister(self, connection_id: UUID) -> None:
        """
        注销连接

        Args:
            connection_id: 连接 ID
        """
        if state := self._connections.pop(connection_id, None):
            self._fail_pending_command_futures(state)
            self._fail_queued_command_futures(state)

            # 取消响应发送任务
            if task := self._sender_tasks.pop(connection_id, None):
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            self._fail_pending_command_futures(state)
            self._fail_queued_command_futures(state)

            # 清理提示词管理器中该连接下所有玩家的模板和变量状态
            get_prompt_manager().clear_connection(str(connection_id))

            # 从消息代理注销
            self.broker.unregister_connection(connection_id)

            logger.info(
                "connection_unregistered",
                connection_id=str(connection_id),
                remaining_connections=len(self._connections),
            )

    def _fail_pending_command_futures(self, state: ConnectionState) -> None:
        """完成已登记但未收到 commandResponse 的命令 future。"""
        for future in state.pending_command_futures.values():
            if not future.done():
                future.set_result("命令执行失败: 连接已关闭")
        state.pending_command_futures.clear()

    def _fail_queued_command_futures(self, state: ConnectionState) -> None:
        """完成仍滞留在响应队列中的 run_command future。"""
        queue = state.response_queue
        if queue is None:
            return

        while not queue.empty():
            try:
                response = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            if (
                isinstance(response, dict)
                and response.get("type") == "run_command"
                and isinstance(response.get("result_future"), asyncio.Future)
            ):
                result_future = response["result_future"]
                if not result_future.done():
                    result_future.set_result("命令执行失败: 连接已关闭")

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

        for state in list(self._connections.values()):
            await self.unregister(state.id)
            if state.websocket:
                try:
                    await state.websocket.close()
                except Exception as e:
                    logger.error(
                        "error_closing_websocket",
                        connection_id=str(state.id),
                        error=str(e)
                    )

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
            response = None
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
                self._resolve_run_command_future(
                    response,
                    failure_message="命令执行失败: 连接已关闭",
                )
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

    def _resolve_run_command_future(
        self, response: any, failure_message: str
    ) -> None:
        """从 run_command 响应对象中提取并完成 result_future。"""
        if (
            isinstance(response, dict)
            and response.get("type") == "run_command"
            and isinstance(response.get("result_future"), asyncio.Future)
        ):
            result_future = response["result_future"]
            if not result_future.done():
                result_future.set_result(failure_message)

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
        elif isinstance(response, SystemNotification):
            await self._send_system_notification(state, response)
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
            elif msg_type == "ai_response_sync":
                await self._sync_response_to_addon(state, response)
        else:
            logger.warning(
                "unknown_response_type",
                connection_id=str(state.id),
                response_type=type(response).__name__,
            )

    async def _send_stream_chunk(
        self, state: ConnectionState, chunk: StreamChunk
    ) -> None:
        """发送流式响应块到 Minecraft（根据类型应用颜色和前缀）"""
        # 根据不同的 chunk_type 确定颜色和消息格式
        if chunk.chunk_type == "thinking_start":
            message = f"{MCColor.GRAY}{MCPrefix.THINKING}思考中..."
            color = MCColor.GRAY
        elif chunk.chunk_type == "thinking_end":
            message = ""  # 不发送结束标记
            color = MCColor.GRAY
        elif chunk.chunk_type == "content":
            # LLM 主要输出内容 - 绿色
            message = chunk.content
            color = MCColor.GREEN
        elif chunk.chunk_type == "reasoning":
            # DeepSeek reasoning content - 灰色带前缀
            message = f"{MCPrefix.THINKING}{chunk.content}"
            color = MCColor.GRAY
        elif chunk.chunk_type == "tool_call":
            # 工具调用信息 - 黄色
            message = chunk.content
            color = MCColor.YELLOW
        elif chunk.chunk_type == "tool_result":
            # 工具返回结果 - 黄色（或可用不同颜色区分）
            message = chunk.content
            color = MCColor.YELLOW
        elif chunk.chunk_type == "error":
            # 错误信息 - 红色
            message = f"{MCPrefix.ERROR}{chunk.content}"
            color = MCColor.RED
        else:
            message = chunk.content
            color = MCColor.GREEN

        if not message:
            return

        if chunk.delivery == "scriptevent":
            await self._send_script_event(state, message)
        else:
            await self._send_game_message_with_color(
                state,
                message,
                color,
                player_name=chunk.target or chunk.player_name,
            )

    async def _send_system_notification(
        self, state: ConnectionState, notification: SystemNotification
    ) -> None:
        color = {
            "info": MCColor.AQUA,
            "warning": MCColor.YELLOW,
            "error": MCColor.RED,
        }[notification.level]
        await self._send_game_message_with_color(
            state,
            notification.message,
            color,
            player_name=notification.player_name,
        )

    def _delivery(self, state: ConnectionState) -> McbeOutboundDelivery:
        return McbeOutboundDelivery(
            connection_id=state.id,
            send_payload=state.websocket.send,
        )

    async def _send_game_message(
        self,
        state: ConnectionState,
        message: str,
        player_name: str | None = None,
    ) -> None:
        """向游戏发送消息（使用默认绿色）"""
        await self._send_game_message_with_color(
            state,
            message,
            MCColor.GREEN,
            player_name=player_name,
        )

    async def _send_game_message_with_color(
        self,
        state: ConnectionState,
        message: str,
        color: str,
        player_name: str | None = None,
    ) -> None:
        """向游戏发送消息（使用指定颜色）"""
        if not state.websocket:
            return

        try:
            target = player_name or "@a"
            chunk_count = await self._delivery(state).send_tellraw(
                message,
                color=color,
                source="stream_tellraw",
                target=target,
            )

            logger.debug(
                "game_message_sent",
                connection_id=str(state.id),
                message_length=len(message),
                color=color,
                chunk_count=chunk_count,
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
            def _register_pending_future(request_id: str) -> None:
                if result_future:
                    state.pending_command_futures[request_id] = result_future

            request_id = await self._delivery(state).send_raw_command(
                command,
                source="stream_run_command",
                before_send=_register_pending_future if result_future else None,
            )
            if result_future:
                def _cleanup_cancelled_future(
                    future: asyncio.Future[str],
                ) -> None:
                    if future.cancelled():
                        state.pending_command_futures.pop(request_id, None)

                result_future.add_done_callback(_cleanup_cancelled_future)

            logger.debug(
                "command_executed",
                connection_id=str(state.id),
                command=command,
                request_id=request_id,
            )
        except Exception as e:
            if result_future and not result_future.done():
                request_id = next(
                    (
                        req_id
                        for req_id, future in state.pending_command_futures.items()
                        if future is result_future
                    ),
                    None,
                )
                if request_id is not None:
                    state.pending_command_futures.pop(request_id, None)
                result_future.set_result(f"命令执行失败: {str(e)}")
            logger.error(
                "run_command_error",
                connection_id=str(state.id),
                command=command,
                error=str(e),
            )

    async def _sync_response_to_addon(
        self, state: ConnectionState, response: dict
    ) -> None:
        """将 AI 响应/用户消息同步到 Addon UI 历史记录。

        为避免看门狗踢出，assistant 响应先等 tellraw 流式发送完毕，
        各分片之间插入延迟，避免命令洪泛。
        """
        player_name = response.get("player_name", DEFAULT_PLAYER_DISPLAY_NAME)
        role = response.get("role", "assistant")
        text = response.get("text", "")

        if not text:
            return

        try:
            chunk_count = await self._delivery(state).send_ai_response(
                AiResponseSync(
                    player_name=player_name,
                    role=role,
                    text=text,
                ),
                source="ai_response_sync",
            )

            logger.debug(
                "ai_response_sync_sent",
                connection_id=str(state.id),
                player_name=player_name,
                role=role,
                chunk_count=chunk_count,
            )
        except Exception as e:
            logger.error(
                "ai_response_sync_error",
                connection_id=str(state.id),
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
            chunk_count = await self._delivery(state).send_scriptevent(
                message,
                message_id=message_id,
                source="stream_scriptevent",
            )

            logger.debug(
                "script_event_sent",
                connection_id=str(state.id),
                message_length=len(message),
                chunk_count=chunk_count,
            )
        except Exception as e:
            logger.error(
                "send_script_event_error",
                connection_id=str(state.id),
                error=str(e),
            )
