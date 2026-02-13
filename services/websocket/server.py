"""WebSocket 服务器"""

import asyncio
import json
from typing import Any

import websockets
from websockets.server import WebSocketServerProtocol, serve

from core.queue import MessageBroker
from services.websocket.connection import ConnectionManager
from services.websocket.minecraft import MinecraftProtocolHandler
from services.auth.jwt_handler import JWTHandler
from config.settings import Settings
from config.logging import get_logger

logger = get_logger(__name__)
ws_raw_logger = get_logger("websocket.raw")


class WebSocketServer:
    """
    WebSocket 服务器

    职责:
    - 接受 Minecraft 客户端连接
    - 处理玩家消息和命令
    - 将聊天请求提交到消息队列
    - 响应发送由 ConnectionManager 独立处理
    """

    def __init__(
        self,
        broker: MessageBroker,
        settings: Settings,
        jwt_handler: JWTHandler,
    ):
        self.broker = broker
        self.settings = settings
        self.jwt_handler = jwt_handler
        self.connection_manager = ConnectionManager(broker)
        self.protocol_handler = MinecraftProtocolHandler()
        self._server: Any = None

    async def start(self) -> None:
        """启动 WebSocket 服务器"""
        ws_config = self.settings.websocket

        self._server = await serve(
            self.handle_connection,
            self.settings.host,
            self.settings.port,
            ping_interval=ws_config.ping_interval,
            ping_timeout=ws_config.ping_timeout,
            close_timeout=ws_config.close_timeout,
            max_size=ws_config.max_size,
            max_queue=ws_config.max_queue,
        )

        logger.info(
            "websocket_server_started",
            host=self.settings.host,
            port=self.settings.port,
        )

    async def stop(self) -> None:
        """停止 WebSocket 服务器"""
        logger.info("stopping_websocket_server")

        # 先关闭所有活跃连接
        await self.connection_manager.shutdown_all()

        # 再关闭服务器
        if self._server:
            self._server.close()
            await self._server.wait_closed()

        logger.info("websocket_server_stopped")

    async def handle_connection(self, websocket: WebSocketServerProtocol) -> None:
        """
        处理单个 WebSocket 连接

        Args:
            websocket: WebSocket 连接
        """
        # 注册连接
        state = await self.connection_manager.register(websocket)

        try:
            # 发送初始化消息
            await self._send_ws_payload(
                state,
                json.dumps({"Result": "true"}),
                source="connection_init",
            )

            # 订阅玩家消息事件
            subscribe_msg = self.protocol_handler.create_subscribe_message()
            await self._send_ws_payload(state, subscribe_msg, source="subscribe")

            # 发送欢迎消息
            welcome_text = self.protocol_handler.create_welcome_message(
                connection_id=str(state.id),
                model=self.settings.get_provider_config().model,
                provider=self.settings.default_provider,
                context_enabled=state.context_enabled,
            )
            welcome_cmd = self.protocol_handler.create_info_message(welcome_text)
            await self._send_ws_payload(state, welcome_cmd, source="welcome")

            logger.info(
                "client_connected",
                connection_id=str(state.id),
            )

            # 处理消息循环
            async for message in websocket:
                await self.handle_message(state, message)

        except websockets.exceptions.ConnectionClosed:
            logger.info(
                "client_disconnected",
                connection_id=str(state.id),
            )
        except Exception as e:
            logger.error(
                "connection_error",
                connection_id=str(state.id),
                error=str(e),
                exc_info=True,
            )
        finally:
            # 注销连接
            await self.connection_manager.unregister(state.id)

    async def handle_message(self, state: Any, message: str) -> None:
        """
        处理接收到的消息

        Args:
            state: 连接状态
            message: 消息内容（JSON 字符串）
        """
        try:
            ws_raw_logger.info(
                "websocket_request_received",
                connection_id=str(state.id),
                payload=message,
            )

            data = json.loads(message)

            # 去重逻辑：排除 sender 为"外部"且 eventName 为 "PlayerMessage" 的消息
            if self._is_external_duplicate_message(data):
                return

            if self._handle_command_response(state, data):
                return

            # 解析玩家消息
            player_event = self.protocol_handler.parse_player_message(data)
            if not player_event:
                return

            # 更新玩家名称
            if player_event.sender and not state.player_name:
                state.player_name = player_event.sender

            # 解析命令
            cmd_type, content = self.protocol_handler.parse_command(
                player_event.message
            )

            if not cmd_type:
                return  # 不是命令，忽略

            logger.info(
                "command_received",
                connection_id=str(state.id),
                command=cmd_type,
                player=player_event.sender,
            )

            # 处理命令
            await self.handle_command(state, cmd_type, content)

        except json.JSONDecodeError:
            logger.warning(
                "invalid_json",
                connection_id=str(state.id),
            )
        except Exception as e:
            logger.error(
                "message_handling_error",
                connection_id=str(state.id),
                error=str(e),
                exc_info=True,
            )

    def _handle_command_response(self, state: Any, data: dict[str, Any]) -> bool:
        """处理 Minecraft commandResponse，回传给等待中的 Agent 工具。"""
        header = data.get("header", {})
        if header.get("messagePurpose") != "commandResponse":
            return False

        request_id = header.get("requestId")
        if not request_id:
            return True

        future = state.pending_command_futures.pop(request_id, None)
        if not future:
            logger.debug(
                "command_response_untracked",
                connection_id=str(state.id),
                request_id=request_id,
            )
            return True

        body = data.get("body", {})
        status_code = body.get("statusCode")
        status_message = body.get("statusMessage") or ""

        if status_code == 0:
            result = status_message or "命令执行成功"
        else:
            result = (
                f"命令执行失败(statusCode={status_code}): {status_message}"
                if status_message
                else f"命令执行失败(statusCode={status_code})"
            )

        if not future.done():
            future.set_result(result)

        logger.info(
            "command_response_tracked",
            connection_id=str(state.id),
            request_id=request_id,
            status_code=status_code,
        )
        return True

    def _is_external_duplicate_message(self, data: dict[str, Any]) -> bool:
        """
        检查是否为需要排除的外部重复消息

        当 sender 为"外部"且 eventName 为"PlayerMessage"时，
        该消息是由 send_game_message 产生的重复响应，需要排除
        """
        # 检查去重功能是否启用
        if not self.settings.dedup_external_messages:
            return False

        header = data.get("header", {})
        event_name = header.get("eventName")

        # 只关注 PlayerMessage 事件
        if event_name != "PlayerMessage":
            return False

        body = data.get("body", {})
        sender = body.get("sender")

        # 排除 sender 为"外部"的消息
        if sender == "外部":
            logger.debug(
                "external_duplicate_message_filtered",
                connection_id=data.get("header", {}).get("requestId", "unknown"),
                sender=sender,
                event_name=event_name,
            )
            return True

        return False

    async def handle_command(
        self, state: Any, cmd_type: str, content: str
    ) -> None:
        """
        处理命令

        Args:
            state: 连接状态
            cmd_type: 命令类型
            content: 命令内容
        """
        # 登录命令不需要认证
        if cmd_type == "login":
            await self.handle_login(state, content)
            return

        # 其他命令需要认证
        if not await self.check_auth(state):
            error_msg = self.protocol_handler.create_error_message("请先登录")
            await self._send_ws_payload(state, error_msg, source="auth")
            return

        # 路由到具体处理器
        if cmd_type == "chat":
            await self.handle_chat(state, content, delivery="tellraw")
        elif cmd_type == "chat_script":
            await self.handle_chat(state, content, delivery="scriptevent")
        elif cmd_type == "context":
            await self.handle_context(state, content)
        elif cmd_type == "switch_model":
            await self.handle_switch_model(state, content)
        elif cmd_type == "help":
            await self.handle_help(state)
        elif cmd_type == "save":
            await self.handle_save(state)
        elif cmd_type == "run_command":
            await self.handle_run_command(state, content)

    async def handle_login(self, state: Any, password: str) -> None:
        """处理登录"""
        if self.jwt_handler.verify_password(password):
            # 检查是否已有有效 token
            if self.jwt_handler.is_token_valid(str(state.id)):
                msg = self.protocol_handler.create_info_message("您已登录")
                await self._send_ws_payload(state, msg, source="login")
                return

            # 生成新 token
            token = self.jwt_handler.generate_token()
            self.jwt_handler.save_token(str(state.id), token)
            state.authenticated = True

            msg = self.protocol_handler.create_success_message("登录成功！")
            await self._send_ws_payload(state, msg, source="login")

            logger.info("user_authenticated", connection_id=str(state.id))
        else:
            msg = self.protocol_handler.create_error_message("密码错误")
            await self._send_ws_payload(state, msg, source="login")

    async def check_auth(self, state: Any) -> bool:
        """检查认证状态"""
        stored_token = self.jwt_handler.get_stored_token(str(state.id))
        if stored_token and self.jwt_handler.verify_token(stored_token):
            state.authenticated = True
            return True
        return False

    async def handle_chat(
        self, state: Any, content: str, delivery: str
    ) -> None:
        """处理聊天请求（非阻塞）"""
        if not content:
            msg = self.protocol_handler.create_error_message("请输入聊天内容")
            await self._send_ws_payload(state, msg, source="chat")
            return

        # 创建聊天请求
        chat_req = self.protocol_handler.create_chat_request(
            state, content, delivery=delivery
        )

        # 提交到消息队列（非阻塞！）
        try:
            await self.broker.submit_request(state.id, chat_req, priority=0)
            logger.debug(
                "chat_request_queued",
                connection_id=str(state.id),
                content_length=len(content),
            )
        except asyncio.QueueFull:
            msg = self.protocol_handler.create_error_message(
                "服务器繁忙，请稍后重试"
            )
            await self._send_ws_payload(state, msg, source="chat")

    async def handle_context(self, state: Any, option: str) -> None:
        """处理上下文管理"""
        if option == "启用":
            state.context_enabled = True
            msg = self.protocol_handler.create_success_message("上下文已启用")
        elif option == "关闭":
            state.context_enabled = False
            self.broker.clear_conversation_history(state.id)
            msg = self.protocol_handler.create_success_message("上下文已关闭")
        elif option == "状态":
            status = "启用" if state.context_enabled else "关闭"
            msg = self.protocol_handler.create_info_message(f"上下文状态: {status}")
        else:
            msg = self.protocol_handler.create_error_message(
                "无效选项，请使用: 启用/关闭/状态"
            )

        await self._send_ws_payload(state, msg, source="context")

    async def handle_switch_model(self, state: Any, provider: str) -> None:
        """处理模型切换"""
        available = self.settings.list_available_providers()

        if provider.lower() in available:
            state.current_provider = provider.lower()
            self.broker.clear_conversation_history(state.id)
            config = self.settings.get_provider_config(provider.lower())
            msg = self.protocol_handler.create_success_message(
                f"已切换到 {provider}/{config.model}"
            )
        else:
            msg = self.protocol_handler.create_error_message(
                f"不可用的提供商。可用: {', '.join(available)}"
            )

        await self._send_ws_payload(state, msg, source="switch_model")

    async def handle_help(self, state: Any) -> None:
        """显示帮助"""
        help_text = self.protocol_handler.get_help_text()
        msg = self.protocol_handler.create_info_message(help_text)
        await self._send_ws_payload(state, msg, source="help")

    async def handle_save(self, state: Any) -> None:
        """保存对话"""
        # TODO: 实现对话历史保存
        msg = self.protocol_handler.create_info_message("对话保存功能开发中")
        await self._send_ws_payload(state, msg, source="save")

    async def handle_run_command(self, state: Any, command: str) -> None:
        """执行游戏命令"""
        if not command:
            msg = self.protocol_handler.create_error_message("请输入命令")
            await self._send_ws_payload(state, msg, source="run_command")
            return

        from models.minecraft import MinecraftCommand

        cmd = MinecraftCommand.create_raw(command)
        await self._send_ws_payload(
            state,
            cmd.model_dump_json(exclude_none=True),
            source="run_command",
        )

        logger.info(
            "command_executed",
            connection_id=str(state.id),
            command=command,
        )

    async def _send_ws_payload(self, state: Any, payload: str, source: str) -> None:
        """统一发送 WebSocket 响应并记录原始输出。"""
        await state.websocket.send(payload)
        ws_raw_logger.info(
            "websocket_response_sent",
            connection_id=str(state.id),
            source=source,
            payload=payload,
        )
