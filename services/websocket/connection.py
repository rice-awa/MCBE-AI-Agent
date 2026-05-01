"""WebSocket 连接管理"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from websockets.server import WebSocketServerProtocol

from core.queue import MessageBroker
from models.messages import StreamChunk
from models.minecraft import MinecraftCommand
from models.agent import MCColor, MCPrefix
from services.websocket.flow_control import FlowControlMiddleware
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
    current_template: str = "default"  # 当前使用的提示词模板
    custom_variables: dict[str, str] = field(default_factory=dict)  # 自定义变量


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
            await self._send_game_message_with_color(state, message, color)

    def _log_ws_send(
        self, state: ConnectionState, payload: str, source: str
    ) -> None:
        """记录 ws 发送，附带 commandLine 字节长度便于观察分片命中率。"""
        command_line_bytes = 0
        try:
            data = json.loads(payload)
            command_line_bytes = len(
                data.get("body", {}).get("commandLine", "").encode("utf-8")
            )
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass

        ws_raw_logger.info(
            "websocket_response_sent",
            connection_id=str(state.id),
            source=source,
            payload=payload,
            command_line_bytes=command_line_bytes,
        )

    async def _send_game_message(
        self, state: ConnectionState, message: str
    ) -> None:
        """向游戏发送消息（使用默认绿色）"""
        await self._send_game_message_with_color(state, message, MCColor.GREEN)

    async def _send_game_message_with_color(
        self, state: ConnectionState, message: str, color: str
    ) -> None:
        """向游戏发送消息（使用指定颜色）"""
        if not state.websocket:
            return

        try:
            payloads = FlowControlMiddleware.chunk_tellraw(message, color=color)
            chunk_delay = FlowControlMiddleware.chunk_delay_for("tellraw")
            for payload in payloads:
                await state.websocket.send(payload)
                self._log_ws_send(state, payload, source="stream_tellraw")
                # 分片间插入微小延迟，避免命令洪泛触发看门狗
                if len(payloads) > 1:
                    await asyncio.sleep(chunk_delay)

            logger.debug(
                "game_message_sent",
                connection_id=str(state.id),
                message_length=len(message),
                color=color,
                chunk_count=len(payloads),
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
                request_id = cmd.header.requestId
                state.pending_command_futures[request_id] = result_future

                def _cleanup_cancelled_future(
                    future: asyncio.Future[str],
                ) -> None:
                    if future.cancelled():
                        state.pending_command_futures.pop(request_id, None)

                result_future.add_done_callback(_cleanup_cancelled_future)

            await state.websocket.send(payload)
            self._log_ws_send(state, payload, source="stream_run_command")

            logger.debug(
                "command_executed",
                connection_id=str(state.id),
                command=command,
                request_id=cmd.header.requestId,
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
        player_name = response.get("player_name", "Player")
        role = response.get("role", "assistant")
        text = response.get("text", "")

        if not text:
            return

        try:
            # assistant 响应：等 tellraw 流式发送完毕后再发 scriptevent
            if role == "assistant":
                await asyncio.sleep(
                    FlowControlMiddleware.chunk_delay_for("ai_resp_prelude")
                )

            payloads = FlowControlMiddleware.chunk_ai_response(
                player_name, role, text
            )
            chunk_delay = FlowControlMiddleware.chunk_delay_for("ai_resp")
            for payload in payloads:
                await state.websocket.send(payload)
                self._log_ws_send(state, payload, source="ai_response_sync")
                # 分片间延迟，避免看门狗超时
                if len(payloads) > 1:
                    await asyncio.sleep(chunk_delay)

            logger.debug(
                "ai_response_sync_sent",
                connection_id=str(state.id),
                player_name=player_name,
                role=role,
                chunk_count=len(payloads),
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
            payloads = FlowControlMiddleware.chunk_scriptevent(
                message, message_id
            )
            chunk_delay = FlowControlMiddleware.chunk_delay_for("scriptevent")
            for payload in payloads:
                await state.websocket.send(payload)
                self._log_ws_send(state, payload, source="stream_scriptevent")
                # 分片间插入微小延迟，避免命令洪泛触发看门狗
                if len(payloads) > 1:
                    await asyncio.sleep(chunk_delay)

            logger.debug(
                "script_event_sent",
                connection_id=str(state.id),
                message_length=len(message),
                chunk_count=len(payloads),
            )
        except Exception as e:
            logger.error(
                "send_script_event_error",
                connection_id=str(state.id),
                error=str(e),
            )
