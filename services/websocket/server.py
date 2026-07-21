"""WebSocket 服务器"""

import asyncio
import json
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import websockets
from websockets.server import WebSocketServerProtocol, serve

from core.queue import MessageBroker, normalize_conversation_id
from services.addon.service import get_addon_bridge_service
from services.websocket.connection import ConnectionManager
from services.websocket.delivery import McbeOutboundDelivery
from services.websocket.minecraft import MinecraftProtocolHandler, TellrawMessage
from services.auth.jwt_handler import JWTHandler
from models.constants import DEFAULT_PLAYER_DISPLAY_NAME
from models.messages import ChatRequest
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
        self.dev_mode = settings.dev_mode
        self.connection_manager = ConnectionManager(broker, dev_mode=self.dev_mode)
        self.protocol_handler = MinecraftProtocolHandler(settings.minecraft)
        self.addon_bridge_service = get_addon_bridge_service()
        self.addon_bridge_service.set_ui_chat_callback(self._handle_ui_chat_message)
        self._server: Any = None

    async def start(self) -> None:
        """启动 WebSocket 服务器"""
        ws_config = self.settings.websocket

        # 开发模式警告
        if self.dev_mode:
            logger.warning(
                "dev_mode_enabled",
                message="⚠️  开发模式已启用 - 身份验证已跳过，仅用于本地开发调试！",
            )

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
            dev_mode=self.dev_mode,
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

        # 开发模式下自动认证
        if self.dev_mode:
            state.authenticated = True
            logger.info(
                "dev_mode_auto_auth",
                connection_id=str(state.id),
                message="开发模式: 自动认证",
            )

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

            # 发送欢迎消息（连接刚建立时还没有具体玩家身份，用默认上下文开关）
            welcome_text = self.protocol_handler.create_welcome_message(
                connection_id=str(state.id),
                model=self.settings.get_provider_config().model,
                provider=self.settings.default_provider,
                context_enabled=True,
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
            self.addon_bridge_service.close_connection(state.id)
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

            # 检查是否为 addon 桥接消息（RESP 或 UI_CHAT）
            if self.addon_bridge_service.is_bridge_chat_message(
                player_event.sender,
                player_event.message,
            ) or self.addon_bridge_service.is_ui_chat_message(
                player_event.sender,
                player_event.message,
            ):
                handled = self.addon_bridge_service.handle_player_message(
                    state.id,
                    player_event.sender,
                    player_event.message,
                )
                logger.debug(
                    "addon_bridge_message_handled",
                    connection_id=str(state.id),
                    sender=player_event.sender,
                    message_prefix=player_event.message[:30] if player_event.message else "",
                    handled=handled,
                )
                return

            # 记录最近一次发言者（仅作便捷指针；多人路由必须用 player_event.sender）
            if player_event.sender:
                state.player_name = player_event.sender

            # 解析命令
            command = self.protocol_handler.parse_typed_command(player_event.message)

            if command is None:
                return  # 不是命令，忽略

            logger.info(
                "command_received",
                connection_id=str(state.id),
                command=command.type,
                player=player_event.sender,
            )

            # 处理命令（player_name 直传，避免连接级状态串扰）
            await self.handle_command(
                state, command.type, command.content, player_name=player_event.sender
            )

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

    def _reply_target(self, state: Any, player_name: str | None) -> str:
        """返回玩家命令响应目标；state.player_name 仅作为旧调用路径的回退。"""
        if player_name:
            return player_name
        legacy_player_name = getattr(state, "player_name", None)
        return legacy_player_name or "@a"

    async def _send_player_reply(
        self,
        state: Any,
        msg: TellrawMessage,
        source: str,
        player_name: str | None,
    ) -> None:
        """发送由玩家消息触发的命令响应，默认只发给触发玩家。"""
        target = self._reply_target(state, player_name)
        await self._send_ws_payload(state, replace(msg, target=target), source=source)

    async def handle_command(
        self,
        state: Any,
        cmd_type: str,
        content: str,
        player_name: str | None = None,
    ) -> None:
        """
        处理命令

        Args:
            state: 连接状态
            cmd_type: 命令类型
            content: 命令内容
            player_name: 触发本次命令的玩家名（多人会话隔离的关键参数）
        """
        # 登录命令不需要认证
        if player_name:
            state.player_name = player_name
        if cmd_type == "login":
            await self.handle_login(state, content, player_name=player_name)
            return

        # 其他命令需要认证（开发模式下跳过）
        if not self.dev_mode and not await self.check_auth(state):
            error_msg = self.protocol_handler.create_error_message("请先登录")
            await self._send_player_reply(state, error_msg, source="auth", player_name=player_name)
            return

        # 路由到具体处理器
        command_handlers = {
            "chat": lambda: self._handle_chat_command(state, content, player_name),
            "chat_script": lambda: self.handle_chat(
                state, content, delivery="scriptevent", player_name=player_name
            ),
            "context": lambda: self.handle_context(state, content, player_name=player_name),
            "conversation": lambda: self.handle_conversation(state, content, player_name=player_name),
            "template": lambda: self.handle_template(state, content, player_name=player_name),
            "setting": lambda: self.handle_setting(state, content, player_name=player_name),
            "mcp": lambda: self.handle_mcp(state, content, player_name=player_name),
            "ai_broadcast": lambda: self.handle_ai_broadcast(state, content, player_name=player_name),
            "tool_approval": lambda: self.handle_tool_approval(state, content, player_name=player_name),
            "switch_model": lambda: self.handle_switch_model(state, content, player_name=player_name),
            "help": lambda: self.handle_help(state, player_name=player_name),
            "save": lambda: self.handle_save(state, player_name=player_name),
            "run_command": lambda: self.handle_run_command(state, content, player_name=player_name),
        }
        if handler := command_handlers.get(cmd_type):
            await handler()

    async def _handle_chat_command(
        self,
        state: Any,
        content: str,
        player_name: str | None,
    ) -> None:
        await self.handle_chat(
            state, content, delivery="tellraw", player_name=player_name
        )
        await self.broker.send_response(state.id, {
            "type": "ai_response_sync",
            "player_name": player_name or state.player_name or DEFAULT_PLAYER_DISPLAY_NAME,
            "role": "user",
            "text": content,
        })

    async def handle_login(
        self,
        state: Any,
        password: str,
        player_name: str | None = None,
    ) -> None:
        """处理登录"""
        # 开发模式下跳过登录验证
        if self.dev_mode:
            msg = self.protocol_handler.create_info_message(
                "开发模式: 无需登录，已自动认证"
            )
            await self._send_player_reply(state, msg, source="login", player_name=player_name)
            return

        if self.jwt_handler.verify_password(password):
            # 检查是否已有有效 token
            if self.jwt_handler.is_token_valid(str(state.id)):
                msg = self.protocol_handler.create_info_message("您已登录")
                await self._send_player_reply(state, msg, source="login", player_name=player_name)
                return

            # 生成新 token
            token = self.jwt_handler.generate_token()
            self.jwt_handler.save_token(str(state.id), token)
            state.authenticated = True

            msg = self.protocol_handler.create_success_message("登录成功！")
            await self._send_player_reply(state, msg, source="login", player_name=player_name)

            logger.info("user_authenticated", connection_id=str(state.id))
        else:
            msg = self.protocol_handler.create_error_message("密码错误")
            await self._send_player_reply(state, msg, source="login", player_name=player_name)

    async def check_auth(self, state: Any) -> bool:
        """检查认证状态"""
        stored_token = self.jwt_handler.get_stored_token(str(state.id))
        if stored_token and self.jwt_handler.verify_token(stored_token):
            state.authenticated = True
            return True
        return False

    async def handle_chat(
        self,
        state: Any,
        content: str,
        delivery: str,
        player_name: str | None = None,
    ) -> None:
        """处理聊天请求（非阻塞）"""
        if not content:
            msg = self.protocol_handler.create_error_message("请输入聊天内容")
            await self._send_player_reply(state, msg, source="chat", player_name=player_name)
            return

        # 创建聊天请求（使用本次事件的真实 sender，而非缓存的 state.player_name）
        active_conversation_id = self.broker.get_active_conversation_id(state.id, player_name)
        chat_req = self.protocol_handler.create_chat_request(
            state,
            content,
            delivery=delivery,
            player_name=player_name,
            conversation_id=active_conversation_id,
        )
        self.broker.ensure_conversation(
            state.id, chat_req.player_name, chat_req.conversation_id
        )
        chat_req.conversation_generation = self.broker.get_conversation_generation(
            state.id, chat_req.player_name, chat_req.conversation_id
        )
        chat_req.conversation_invalidation_epoch = self.broker.get_conversation_invalidation_epoch(
            state.id, chat_req.player_name, chat_req.conversation_id
        )
        chat_req.broadcast_ai_chat = delivery == "tellraw" and state.should_broadcast_ai_chat(chat_req.player_name)

        # 提交到消息队列（非阻塞！）
        try:
            await self.broker.submit_request(state.id, chat_req, priority=0)
            logger.debug(
                "chat_request_queued",
                connection_id=str(state.id),
                player=player_name,
                content_length=len(content),
            )
        except asyncio.QueueFull:
            msg = self.protocol_handler.create_error_message(
                "服务器繁忙，请稍后重试"
            )
            await self._send_player_reply(state, msg, source="chat", player_name=player_name)

    async def handle_context(
        self, state: Any, option: str, player_name: str | None = None
    ) -> None:
        """处理上下文开关与状态（按玩家分桶，不再承担对话管理职责）。"""
        session = state.get_player_session(player_name)

        parts = option.strip().split(None, 1) if option.strip() else []
        action = parts[0] if parts else "状态"

        if action in ("启用", "开启", "on", "enable"):
            session.context_enabled = True
            msg = self.protocol_handler.create_success_message("上下文已启用")
        elif action in ("关闭", "off", "disable"):
            session.context_enabled = False
            msg = self.protocol_handler.create_success_message(
                "上下文已关闭；现有对话历史不会被清除，如需清除请使用 AGENT 对话 clear"
            )
        elif action in ("状态", "status", ""):
            status = "启用" if session.context_enabled else "关闭"
            conversation_id = self.broker.get_active_conversation_id(state.id, player_name)
            history = self.broker.get_conversation_history(
                state.id, player_name, conversation_id
            )
            turns = self._count_conversation_turns(history)

            context_usage = ""
            try:
                provider = session.current_provider or self.settings.default_provider
                provider_config = self.settings.get_provider_config(provider)
                message_count = len(history)
                estimated_tokens = message_count * 100
                if provider_config.context_window:
                    usage_percent = (estimated_tokens / provider_config.context_window) * 100
                    context_usage = (
                        f"\n上下文使用: {usage_percent:.1f}% "
                        f"({estimated_tokens}/{provider_config.context_window} tokens)"
                    )
                else:
                    context_usage = f"\n上下文使用: ~{estimated_tokens} tokens (模型上下文窗口未知)"
            except Exception:
                pass

            msg = self.protocol_handler.create_info_message(
                f"上下文状态: {status}"
                f"\n当前对话: {conversation_id}"
                f"\n当前对话轮数: {turns}/{self.settings.max_history_turns}"
                f"{context_usage}"
            )
        elif action in (
            "clear", "清除", "压缩", "compress", "保存", "save",
            "恢复", "restore", "列表", "list", "删除", "delete",
        ):
            await self.handle_conversation(state, option, player_name=player_name)
            return
        else:
            msg = self.protocol_handler.create_error_message(
                "上下文命令仅管理是否携带历史。用法: AGENT 上下文 <启用/关闭/状态>\n"
                "对话的新建/切换/清除/保存/恢复请使用: AGENT 对话 <new/switch/clear/...>"
            )

        await self._send_player_reply(state, msg, source="context", player_name=player_name)

    async def handle_conversation(
        self, state: Any, option: str, player_name: str | None = None
    ) -> None:
        """处理对话管理：新建、切换、清除、压缩、保存、恢复、列表和删除。"""
        parts = option.strip().split(None, 1) if option.strip() else []
        action = parts[0].lower() if parts else "状态"
        if action in ("状态", "status", "", "list", "列表", "已保存", "saved"):
            msg = await self._handle_conversation(state, option, player_name)
        else:
            lock = self.broker.get_session_lock(state.id, player_name)
            async with lock:
                msg = await self._handle_conversation(state, option, player_name)

        await self._send_player_reply(state, msg, source="conversation", player_name=player_name)

    async def _handle_conversation(
        self, state: Any, option: str, player_name: str | None = None
    ) -> TellrawMessage:
        from core.conversation import get_conversation_manager

        conv_manager = get_conversation_manager(self.broker, self.settings)
        session = state.get_player_session(player_name)
        actor = player_name or session.player_name

        parts = option.strip().split(None, 1) if option.strip() else []
        action = parts[0].lower() if parts else "状态"
        arg = parts[1].strip() if len(parts) > 1 else ""
        conversation_id = self.broker.get_active_conversation_id(state.id, actor)

        if action in ("new", "新建", "创建"):
            new_id = self._generate_unique_conversation_id(state.id, actor, arg)
            if new_id is None:
                target_id = normalize_conversation_id(arg)
                return self.protocol_handler.create_error_message(
                    f"对话 {target_id} 已存在，请使用 AGENT 对话 switch {target_id} 切换"
                )
            self.broker.bump_conversation_invalidation_epoch(state.id, actor, conversation_id)
            self.broker.set_active_conversation_id(state.id, actor, new_id)
            self.broker.set_conversation_history(state.id, actor, [], new_id)
            metadata = self.broker.ensure_conversation_metadata(state.id, actor, new_id)
            return self.protocol_handler.create_success_message(
                f"已新建并切换到对话: #{metadata.short_id} {new_id}"
            )
        elif action in ("switch", "切换"):
            if not arg:
                return self.protocol_handler.create_error_message(
                    "请指定要切换的对话 ID\n用法: AGENT 对话 switch <ID>"
                )
            resolved_id = self.broker.resolve_conversation_short_id(state.id, actor, arg)
            target_id = normalize_conversation_id(resolved_id or arg)
            was_existing = self.broker.conversation_exists(state.id, actor, target_id)
            self.broker.bump_conversation_invalidation_epoch(state.id, actor, conversation_id)
            if target_id != conversation_id:
                self.broker.bump_conversation_invalidation_epoch(state.id, actor, target_id)
            if not was_existing:
                self.broker.set_conversation_history(state.id, actor, [], target_id)
            self.broker.set_active_conversation_id(state.id, actor, target_id)
            history = self.broker.get_conversation_history(state.id, actor, target_id)
            turns = self._count_conversation_turns(history)
            metadata = self.broker.ensure_conversation_metadata(state.id, actor, target_id)
            display_id = f"#{metadata.short_id} {target_id}"
            message = (
                f"已切换到对话: {display_id}（{turns}轮）"
                if was_existing
                else f"已创建并切换到新会话: {display_id}"
            )
            return self.protocol_handler.create_success_message(message)
        elif action in ("clear", "清除"):
            self.broker.clear_conversation_history(state.id, actor, conversation_id)
            self.broker.set_conversation_history(state.id, actor, [], conversation_id)
            return self.protocol_handler.create_success_message(
                f"对话 {conversation_id} 的历史已清除"
            )
        elif action in ("状态", "status", ""):
            history = self.broker.get_conversation_history(state.id, actor, conversation_id)
            turns = self._count_conversation_turns(history)
            context_status = "启用" if session.context_enabled else "关闭"
            return self.protocol_handler.create_info_message(
                f"当前对话: {conversation_id}"
                f"\n对话轮数: {turns}/{self.settings.max_history_turns}"
                f"\n压缩触发: {self._conversation_compression_status_text()}"
                f"\n上下文携带: {context_status}"
            )
        elif action in ("list", "列表"):
            live_conversations = dict(self.broker.list_player_conversations(state.id, actor))
            live_conversations.setdefault(conversation_id, len(
                self.broker.get_conversation_history(state.id, actor, conversation_id)
            ))
            for conv_id in live_conversations:
                self.broker.ensure_conversation_metadata(state.id, actor, conv_id)
            lines = ["当前连接内对话:"]
            for metadata in self.broker.list_player_conversation_metadata(state.id, actor):
                conv_id = metadata.conversation_id
                message_count = live_conversations.get(conv_id, len(
                    self.broker.get_conversation_history(state.id, actor, conv_id)
                ))
                marker = " *" if conv_id == conversation_id else ""
                title = metadata.title or "未命名"
                lines.append(
                    f"• #{metadata.short_id} {conv_id}{marker} - {title} - {message_count} 条消息"
                )
            return self.protocol_handler.create_info_message("\n".join(lines))
        elif action in ("压缩", "compress"):
            success, result = await conv_manager.check_and_compress(
                state.id,
                actor,
                force=True,
                conversation_id=conversation_id,
                provider_name=session.current_provider or self.settings.default_provider,
            )
            return (
                self.protocol_handler.create_success_message(result)
                if success else self.protocol_handler.create_info_message(result)
            )
        elif action in ("保存", "save"):
            success, result = await conv_manager.save_conversation(
                connection_id=state.id,
                player_name=actor,
                provider=session.current_provider or self.settings.default_provider,
                template=session.current_template,
                custom_variables=session.custom_variables,
                conversation_id=conversation_id,
            )
            return (
                self.protocol_handler.create_success_message(f"对话已保存: {result}")
                if success else self.protocol_handler.create_error_message(result)
            )
        elif action in ("恢复", "restore"):
            if not arg:
                return self.protocol_handler.create_error_message(
                    "请指定要恢复的会话 ID\n用法: AGENT 对话 restore <保存ID>"
                )
            success, result = await conv_manager.restore_conversation(
                state.id, arg, player_name=actor, conversation_id=conversation_id
            )
            return (
                self.protocol_handler.create_success_message(result)
                if success else self.protocol_handler.create_error_message(result)
            )
        elif action in ("已保存", "saved"):
            conversations = await conv_manager.list_conversations(player_name=actor)
            list_text = conv_manager.format_conversation_list(conversations)
            return self.protocol_handler.create_info_message(list_text)
        elif action in ("删除", "delete"):
            if not arg:
                return self.protocol_handler.create_error_message(
                    "请指定要删除的保存会话 ID\n用法: AGENT 对话 delete <保存ID>"
                )
            success, result = await conv_manager.delete_conversation(
                arg, player_name=actor
            )
            return (
                self.protocol_handler.create_success_message(result)
                if success else self.protocol_handler.create_error_message(result)
            )

        return self.protocol_handler.create_error_message(
            "无效选项，请使用: new [ID]/switch <ID>/clear/status/list/"
            "compress/save/restore <保存ID>/saved/delete <保存ID>"
        )

    def _generate_unique_conversation_id(
        self,
        connection_id: UUID,
        player_name: str | None,
        requested_id: str = "",
    ) -> str | None:
        """生成不覆盖已有运行时对话的 ID；显式 ID 已存在时返回 None。"""
        if requested_id:
            new_id = normalize_conversation_id(requested_id)
            if self.broker.conversation_exists(connection_id, player_name, new_id):
                return None
            return new_id

        for _ in range(10):
            new_id = self._generate_conversation_id()
            if not self.broker.conversation_exists(connection_id, player_name, new_id):
                return new_id
        raise RuntimeError("无法生成唯一对话 ID")

    @staticmethod
    def _generate_conversation_id() -> str:
        """生成简短运行时对话 ID。"""
        return "chat-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + uuid4().hex[:6]

    def _count_conversation_turns(self, history: list) -> int:
        """计算对话轮次"""
        turns = 0
        for message in history:
            if hasattr(message, "parts"):
                for part in message.parts:
                    if getattr(part, "part_kind", None) == "user-prompt":
                        turns += 1
                        break
        return turns

    def _conversation_compression_status_text(self) -> str:
        if not self.settings.compression_enabled:
            return "关闭"
        threshold = max(
            1,
            int(self.settings.max_history_turns * self.settings.compression_trigger_ratio),
        )
        return f"{threshold}轮，保留最近{self.settings.compression_keep_recent_turns}轮"

    async def handle_switch_model(
        self, state: Any, provider: str, player_name: str | None = None
    ) -> None:
        """处理模型切换（按玩家分桶）"""
        available = self.settings.list_available_providers()
        if provider.lower() not in available:
            msg = self.protocol_handler.create_error_message(
                f"不可用的提供商。可用: {', '.join(available)}"
            )
            await self._send_player_reply(state, msg, source="switch_model", player_name=player_name)
            return

        lock = self.broker.get_session_lock(state.id, player_name)
        async with lock:
            msg = await self._handle_switch_model(state, provider, player_name)

        await self._send_player_reply(state, msg, source="switch_model", player_name=player_name)

    async def _handle_switch_model(
        self, state: Any, provider: str, player_name: str | None = None
    ) -> TellrawMessage:
        session = state.get_player_session(player_name)

        session.current_provider = provider.lower()
        self.broker.clear_player_conversation_histories(state.id, player_name)
        config = self.settings.get_provider_config(provider.lower())
        return self.protocol_handler.create_success_message(
            f"已切换到 {provider}/{config.model}，该玩家的运行时对话历史已清理"
        )

    async def handle_template(
        self, state: Any, content: str, player_name: str | None = None
    ) -> None:
        """处理模板管理（按玩家分桶）"""
        from services.agent.prompt import get_prompt_manager

        manager = get_prompt_manager()
        connection_id = str(state.id)
        session = state.get_player_session(player_name)
        actor = player_name or session.player_name

        if not content or content.strip() == "":
            # 显示当前模板
            current = manager.get_session_template(connection_id, actor)
            template = manager.get_template(current)
            if template:
                msg = self.protocol_handler.create_info_message(
                    f"当前模板: {template.name} - {template.description}"
                )
            else:
                msg = self.protocol_handler.create_info_message(f"当前模板: {current}")
        elif content.strip() == "list":
            # 列出所有模板
            templates = manager.list_templates()
            current = manager.get_session_template(connection_id, actor)
            lines = ["可用模板:"]
            for name in templates:
                template = manager.get_template(name)
                marker = " *" if name == current else ""
                desc = template.description if template else ""
                lines.append(f"• {name}{marker} - {desc}")
            msg = self.protocol_handler.create_info_message("\n".join(lines))
        else:
            # 切换模板
            template_name = content.strip()
            if manager.set_session_template(connection_id, actor, template_name):
                # 同时更新 PlayerSession 的模板状态
                session.current_template = template_name
                session.custom_variables = manager.get_session_variables(connection_id, actor)
                template = manager.get_template(template_name)
                desc = template.description if template else ""
                msg = self.protocol_handler.create_success_message(
                    f"已切换到模板: {template_name} ({desc})"
                )
            else:
                templates = ", ".join(manager.list_templates())
                msg = self.protocol_handler.create_error_message(
                    f"模板不存在: {template_name}\n可用: {templates}"
                )

        await self._send_player_reply(state, msg, source="template", player_name=player_name)

    async def handle_setting(
        self, state: Any, content: str, player_name: str | None = None
    ) -> None:
        """处理设置管理（按玩家分桶）"""
        from services.agent.prompt import get_prompt_manager

        manager = get_prompt_manager()
        connection_id = str(state.id)
        session = state.get_player_session(player_name)
        actor = player_name or session.player_name

        if not content:
            msg = self.protocol_handler.create_error_message("请输入设置项")
            await self._send_player_reply(state, msg, source="setting", player_name=player_name)
            return

        parts = content.strip().split(None, 1)
        if len(parts) < 2:
            msg = self.protocol_handler.create_error_message(
                "用法: AGENT 设置 变量 <名称> <值>\n      AGENT 设置 别名 <名称> <别名>"
            )
            await self._send_player_reply(state, msg, source="setting", player_name=player_name)
            return

        setting_type = parts[0]
        rest = parts[1]

        if setting_type == "变量":
            # 设置自定义变量
            # 支持格式: "变量 <名称> <值>" 或 "变量 <名称>=<值>"
            if "=" in rest:
                # 格式: 变量 name=value
                name_val = rest.split("=", 1)
                name = name_val[0].strip()
                value = name_val[1].strip()
            else:
                # 格式: 变量 name value
                var_parts = rest.split(None, 1)
                if len(var_parts) < 2:
                    msg = self.protocol_handler.create_error_message(
                        "用法: AGENT 设置 变量 <名称> <值>"
                    )
                    await self._send_player_reply(state, msg, source="setting", player_name=player_name)
                    return
                name = var_parts[0]
                value = var_parts[1]

            # PromptManager.set_session_variable 会自动添加 custom_ 前缀
            manager.set_session_variable(connection_id, actor, name, value)
            session.custom_variables = manager.get_session_variables(connection_id, actor)

            # 显示带前缀的名称
            display_name = f"custom_{name}" if not name.startswith("custom_") else name
            msg = self.protocol_handler.create_success_message(
                f"已设置变量: {display_name} = {value}"
            )
        elif setting_type == "别名":
            # 别名管理（暂时未实现）
            msg = self.protocol_handler.create_info_message("别名管理功能开发中")
        else:
            msg = self.protocol_handler.create_error_message(
                "未知的设置类型，请使用: 变量"
            )

        await self._send_player_reply(state, msg, source="setting", player_name=player_name)

    async def handle_help(self, state: Any, player_name: str | None = None) -> None:
        """显示帮助"""
        help_text = self.protocol_handler.get_help_text()
        msg = self.protocol_handler.create_info_message(help_text)
        await self._send_player_reply(state, msg, source="help", player_name=player_name)

    async def handle_tool_approval(
        self,
        state: Any,
        content: str,
        player_name: str | None = None,
    ) -> None:
        """处理 `AGENT 工具审批 <approval_id> <允许|拒绝>`。

        UX (approach C)：同一 DeferredToolRequests 批内，每条命令只记录一项决策；
        待 batch 全部决策后，用完整 DeferredToolResults 恢复 run，避免 partial results。
        """
        from services.agent.runtime import get_agent_runtime

        parts = content.strip().split()
        if len(parts) < 2:
            msg = self.protocol_handler.create_error_message(
                "用法: AGENT 工具审批 <approval_id> <允许|拒绝>"
            )
            await self._send_player_reply(state, msg, source="tool_approval", player_name=player_name)
            return

        approval_id = parts[0]
        decision_raw = parts[1].lower()
        if decision_raw in ("允许", "allow", "approve", "yes", "y", "true", "1"):
            approved = True
        elif decision_raw in ("拒绝", "deny", "reject", "no", "n", "false", "0"):
            approved = False
        else:
            msg = self.protocol_handler.create_error_message(
                "第二参数必须是 允许 或 拒绝"
            )
            await self._send_player_reply(state, msg, source="tool_approval", player_name=player_name)
            return

        owner = player_name or state.player_name or DEFAULT_PLAYER_DISPLAY_NAME
        conversation_id = self.broker.get_active_conversation_id(state.id, owner)
        store = get_agent_runtime().get_pending_approval_store(self.settings)

        pending, reject_reason, completed_batch = store.record_decision(
            connection_id=str(state.id),
            player_name=owner,
            conversation_id=conversation_id,
            approval_id=approval_id,
            approved=approved,
        )
        if pending is None:
            msg = self.protocol_handler.create_error_message(reject_reason or "审批不可用")
            await self._send_player_reply(state, msg, source="tool_approval", player_name=player_name)
            return

        if not approved:
            msg = self.protocol_handler.create_info_message(
                f"已拒绝工具调用 [{approval_id}] {pending.tool_name}"
            )
        else:
            msg = self.protocol_handler.create_success_message(
                f"已批准工具调用 [{approval_id}] {pending.tool_name}"
            )
        await self._send_player_reply(state, msg, source="tool_approval", player_name=player_name)

        if completed_batch is None:
            # 仍有同批未决策项：只确认记录，不 resume
            wait_msg = self.protocol_handler.create_info_message(
                f"已记录 [{approval_id}] 的决策；同批还有待审批项，"
                f"全部决策后才会继续执行（batch={pending.batch_id}）"
            )
            await self._send_player_reply(state, wait_msg, source="tool_approval", player_name=player_name)
            return

        # batch 齐套：构造完整 DeferredToolResults 并恢复同一调用链
        approvals_payload: dict[str, Any] = {}
        for item in completed_batch:
            if item.decision is True:
                approvals_payload[item.tool_call_id] = True
            else:
                approvals_payload[item.tool_call_id] = {
                    "kind": "tool-denied",
                    "message": f"玩家拒绝了工具调用 {item.tool_name}",
                }

        resume_msg = self.protocol_handler.create_success_message(
            f"批次 {pending.batch_id} 决策完成（{len(completed_batch)} 项），继续执行..."
        )
        await self._send_player_reply(state, resume_msg, source="tool_approval", player_name=player_name)

        generation = self.broker.get_conversation_generation(
            state.id, owner, conversation_id
        )
        invalidation_epoch = self.broker.get_conversation_invalidation_epoch(
            state.id, owner, conversation_id
        )
        chat_req = ChatRequest(
            connection_id=state.id,
            content="",  # 不把批准文本作为新 prompt
            player_name=owner,
            use_context=pending.use_context,
            provider=pending.provider or state.get_player_session(owner).current_provider,
            delivery=pending.delivery,  # type: ignore[arg-type]
            conversation_id=conversation_id,
            conversation_generation=generation,
            conversation_invalidation_epoch=invalidation_epoch,
            broadcast_ai_chat=pending.broadcast_ai_chat,
            run_id=pending.run_id,
            resume_approval_id=pending.batch_id,
            deferred_tool_results={"approvals": approvals_payload},
            resume_message_history=pending.messages,
        )
        await self.broker.submit_request(state.id, chat_req, priority=0)

    async def handle_save(self, state: Any, player_name: str | None = None) -> None:
        """兼容旧保存命令：保存当前活动对话。"""
        await self.handle_conversation(state, "save", player_name=player_name)

    async def handle_run_command(
        self,
        state: Any,
        command: str,
        player_name: str | None = None,
    ) -> None:
        """执行游戏命令"""
        if not command:
            msg = self.protocol_handler.create_error_message("请输入命令")
            await self._send_player_reply(state, msg, source="run_command", player_name=player_name)
            return

        try:
            await self._delivery(state).send_raw_command(command, source="run_command")
        except ValueError as exc:
            msg = self.protocol_handler.create_error_message(str(exc))
            await self._send_player_reply(state, msg, source="run_command", player_name=player_name)
            return

        logger.info(
            "command_executed",
            connection_id=str(state.id),
            command=command,
        )

    async def handle_ai_broadcast(
        self,
        state: Any,
        content: str,
        player_name: str | None = None,
    ) -> None:
        """处理 AI 聊天广播策略。"""
        msg = self._handle_ai_broadcast(state, content)
        await self._send_player_reply(state, msg, source="ai_broadcast", player_name=player_name)

    def _handle_ai_broadcast(self, state: Any, content: str) -> TellrawMessage:
        parts = content.strip().split()
        action = parts[0].lower() if parts else "状态"

        if action in ("状态", "status", ""):
            mode = "开启" if state.ai_broadcast_all else "关闭"
            players = "、".join(sorted(state.ai_broadcast_players)) or "无"
            return self.protocol_handler.create_info_message(
                f"AI 广播状态:\n全服广播: {mode}\n指定玩家: {players}"
            )

        if action in ("关闭", "off", "disable"):
            state.ai_broadcast_all = False
            state.ai_broadcast_players.clear()
            return self.protocol_handler.create_success_message("AI 广播已关闭")

        if action in ("全服", "all"):
            if len(parts) < 2:
                return self.protocol_handler.create_error_message(
                    "用法: AGENT 广播 全服 <开启|关闭>"
                )
            toggle = parts[1].lower()
            if toggle in ("开启", "启用", "on", "enable"):
                state.ai_broadcast_all = True
                return self.protocol_handler.create_success_message("AI 全服广播已开启")
            if toggle in ("关闭", "off", "disable"):
                state.ai_broadcast_all = False
                return self.protocol_handler.create_success_message("AI 全服广播已关闭")
            return self.protocol_handler.create_error_message(
                "用法: AGENT 广播 全服 <开启|关闭>"
            )

        if action in ("玩家", "player"):
            if len(parts) < 3:
                return self.protocol_handler.create_error_message(
                    "用法: AGENT 广播 玩家 <玩家名> <开启|关闭>"
                )
            target_player = parts[1]
            toggle = parts[2].lower()
            if toggle in ("开启", "启用", "on", "enable"):
                state.ai_broadcast_players.add(target_player)
                return self.protocol_handler.create_success_message(
                    f"已开启 {target_player} 的 AI 聊天广播"
                )
            if toggle in ("关闭", "off", "disable"):
                state.ai_broadcast_players.discard(target_player)
                return self.protocol_handler.create_success_message(
                    f"已关闭 {target_player} 的 AI 聊天广播"
                )
            return self.protocol_handler.create_error_message(
                "用法: AGENT 广播 玩家 <玩家名> <开启|关闭>"
            )

        return self.protocol_handler.create_error_message(
            "用法: AGENT 广播 <状态|关闭|全服 开启|关闭|玩家 <玩家名> 开启|关闭>"
        )

    async def handle_mcp(
        self,
        state: Any,
        content: str,
        player_name: str | None = None,
    ) -> None:
        """处理 MCP 服务器管理命令"""
        from services.agent.mcp import get_mcp_manager, MCPConnectionStatus

        manager = get_mcp_manager(self.settings)
        parts = content.strip().split(None, 1) if content.strip() else []
        action = parts[0] if parts else ""
        arg = parts[1] if len(parts) > 1 else ""

        if action == "list":
            # 列出所有 MCP 服务器
            if not self.settings.mcp.enabled:
                msg = self.protocol_handler.create_info_message("MCP 功能未启用")
            elif not self.settings.mcp.servers:
                msg = self.protocol_handler.create_info_message("未配置 MCP 服务器")
            else:
                lines = [f"MCP 服务器 ({len(self.settings.mcp.servers)}):"]
                for name, config in self.settings.mcp.servers.items():
                    info = manager.get_server_info(name)
                    if info:
                        status_icon = {
                            MCPConnectionStatus.PENDING: "⏳",
                            MCPConnectionStatus.ACTIVE: "✅",
                            MCPConnectionStatus.ERROR: "❌",
                            MCPConnectionStatus.DISABLED: "⛔",
                        }.get(info.status, "❓")
                        lines.append(f"  {status_icon} {name}: {info.status.value}")
                    else:
                        lines.append(f"  ⚪ {name}: 未初始化")
                msg = self.protocol_handler.create_info_message("\n".join(lines))

        elif action == "status":
            # 显示详细状态
            if not manager.is_initialized:
                msg = self.protocol_handler.create_info_message(
                    "MCP 管理器尚未初始化\n请等待服务启动完成"
                )
            else:
                status = manager.get_status_summary()
                lines = [
                    f"MCP 状态概览:",
                    f"  已启用: {'是' if status['enabled'] else '否'}",
                    f"  活跃服务器: {status['active_servers']}/{status['total_servers']}",
                ]
                for name, info in status["servers"].items():
                    lines.append(f"\n  {name}:")
                    lines.append(f"    状态: {info['status']}")
                    if info.get("last_error"):
                        lines.append(f"    错误: {info['last_error']}")
                    if info.get("last_run_time"):
                        lines.append(f"    上次运行: {info['last_run_time']}")
                msg = self.protocol_handler.create_info_message("\n".join(lines))

        elif action == "reload":
            # 重新加载指定服务器的配置
            if not manager.is_initialized:
                msg = self.protocol_handler.create_error_message(
                    "MCP 管理器尚未初始化"
                )
            elif arg:
                # 重载指定服务器
                if arg not in manager.servers:
                    msg = self.protocol_handler.create_error_message(
                        f"未找到服务器: {arg}"
                    )
                else:
                    success = manager.reload_toolset(arg)
                    if success:
                        from services.agent.runtime import get_agent_runtime

                        get_agent_runtime().refresh_mcp_tools(self.settings)
                        msg = self.protocol_handler.create_success_message(
                            f"服务器 {arg} 配置已重新加载"
                        )
                    else:
                        msg = self.protocol_handler.create_error_message(
                            f"服务器 {arg} 配置重载失败"
                        )
            else:
                msg = self.protocol_handler.create_error_message(
                    "请指定服务器名称: AGENT MCP reload <名称>"
                )

        else:
            # 显示帮助
            msg = self.protocol_handler.create_info_message(
                "MCP 管理命令:\n"
                "  list - 列出所有服务器\n"
                "  status - 显示详细状态\n"
                "  reload <名称> - 重新加载服务器配置"
            )

        await self._send_player_reply(state, msg, source="mcp", player_name=player_name)

    async def _handle_ui_chat_message(
        self, connection_id: UUID, player_name: str, message: str
    ) -> None:
        """处理从 addon UI 发来的聊天消息。"""
        state = self.connection_manager.get_connection(connection_id)
        if not state:
            logger.warning(
                "ui_chat_connection_not_found",
                connection_id=str(connection_id),
            )
            return

        # 记录最近发言者（仅用于日志）；多人路由统一使用 player_name 形参。
        if player_name:
            state.player_name = player_name

        logger.info(
            "ui_chat_received",
            connection_id=str(state.id),
            player=player_name,
            content_length=len(message),
        )

        # 与普通聊天命令保持一致：非开发模式必须先通过登录认证
        if not self.dev_mode and not await self.check_auth(state):
            error_msg = self.protocol_handler.create_error_message("请先登录")
            await self._send_player_reply(state, error_msg, source="auth", player_name=player_name)
            return

        # 把 UI 玩家发出的"用户消息"回流到 Addon 历史（与聊天框命令保持一致）
        await self.broker.send_response(state.id, {
            "type": "ai_response_sync",
            "player_name": player_name or DEFAULT_PLAYER_DISPLAY_NAME,
            "role": "user",
            "text": message,
        })

        await self.handle_chat(
            state, message, delivery="tellraw", player_name=player_name
        )

    async def _send_ws_payload(
        self,
        state: Any,
        payload: "str | TellrawMessage",
        source: str,
    ) -> None:
        """统一发送 WebSocket 响应并记录原始输出。

        - TellrawMessage: 走出站投递 Adapter 自动分片，
          从源头消除"反向解析自家产物"的反模式。
        - str: 用于订阅消息、初始化等已序列化 payload，原样发送。
        """
        if isinstance(payload, TellrawMessage):
            await self._delivery(state).send_tellraw(
                payload.text,
                color=payload.color,
                source=source,
                target=payload.target,
            )
            return

        await self._delivery(state).send_payload(payload, source=source)

    def _delivery(self, state: Any) -> McbeOutboundDelivery:
        return McbeOutboundDelivery(
            connection_id=state.id,
            send_payload=state.websocket.send,
        )
