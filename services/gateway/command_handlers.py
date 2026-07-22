"""Host command business logic for the SDK gateway (migrated from WebSocketServer)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from mcbe_ws_sdk import FlowControlSettings, McbeOutboundDelivery
from mcbe_ws_sdk.addon import AddonBridgeService
from mcbe_ws_sdk.gateway.connection import ConnectionState

from config.logging import get_logger
from config.settings import Settings
from core.queue import MessageBroker, normalize_conversation_id
from core.session import DEFAULT_CONVERSATION_ID
from models.constants import DEFAULT_PLAYER_DISPLAY_NAME
from models.messages import ChatRequest
from services.auth.jwt_handler import JWTHandler
from services.gateway.session_store import HostConnectionSession, HostSessionStore
from services.gateway.ws_command_runner import WsCommandRunner
from mcbe_ws_sdk import MinecraftProtocolHandler
from mcbe_ws_sdk.gateway.handler import TellrawMessage
from services.agent.trace import TraceContext, get_trace_recorder

logger = get_logger(__name__)


class CommandHandlers:
    """In-game command handlers using host session store + SDK delivery."""

    def __init__(
        self,
        broker: MessageBroker,
        settings: Settings,
        jwt_handler: JWTHandler,
        sessions: HostSessionStore,
        ws_commands: WsCommandRunner,
        addon: AddonBridgeService,
        protocol: MinecraftProtocolHandler,
        flow: FlowControlSettings,
        *,
        log_raw: bool = False,
    ) -> None:
        self.broker = broker
        self.settings = settings
        self.jwt_handler = jwt_handler
        self.sessions = sessions
        self.ws_commands = ws_commands
        self.addon = addon
        self.protocol = protocol
        self.flow = flow
        self.log_raw = log_raw
        self.dev_mode = settings.dev_mode

    # -- session / delivery helpers ------------------------------------------------

    def _host(self, state: ConnectionState) -> HostConnectionSession | None:
        return self.sessions.get(state.id)

    def _require_host(self, state: ConnectionState) -> HostConnectionSession:
        host = self._host(state)
        if host is None:
            host = self.sessions.create(
                state.id,
                authenticated=self.dev_mode,
                ai_broadcast_all=self.settings.minecraft.ai_broadcast_default,
            )
        return host

    def _delivery(self, state: ConnectionState) -> McbeOutboundDelivery | None:
        if state.send_payload is None:
            return None
        return McbeOutboundDelivery(
            connection_id=state.id,
            send_payload=state.send_payload,
            settings=self.flow,
            log_raw_payloads=self.log_raw,
        )

    def _reply_target(self, state: ConnectionState, player_name: str | None) -> str:
        return player_name or state._player_name or "@a"

    async def _send_player_reply(
        self,
        state: ConnectionState,
        msg: TellrawMessage,
        *,
        source: str,
        player_name: str | None = None,
    ) -> None:
        delivery = self._delivery(state)
        if delivery is None:
            return
        target = self._reply_target(state, player_name)
        await delivery.send_tellraw(
            msg.text,
            color=msg.color,
            source=source,
            target=target,
        )

    def _build_chat_request(
        self,
        state: ConnectionState,
        content: str,
        *,
        delivery: str,
        player_name: str | None,
        conversation_id: str,
    ) -> ChatRequest:
        host = self._require_host(state)
        sender = player_name or state._player_name
        session = host.get_player_session(sender)
        resolved_conversation = conversation_id or DEFAULT_CONVERSATION_ID
        # 一次玩家意图：trace_id == run_id（迁移期）；首次 attempt 新生成。
        trace_id = str(uuid4())
        attempt_id = str(uuid4())
        return ChatRequest(
            connection_id=state.id,
            content=content,
            player_name=sender,
            use_context=session.context_enabled,
            provider=session.current_provider,
            delivery=delivery or "tellraw",
            conversation_id=resolved_conversation,
            auto_approve_tools=host.should_auto_approve_tools(
                sender, resolved_conversation
            ),
            run_id=trace_id,
            trace_id=trace_id,
            attempt_id=attempt_id,
        )

    def _trace_context_for_request(self, chat_req: ChatRequest) -> TraceContext | None:
        """从已填充 correlation 的 ChatRequest 构建 TraceContext。"""
        trace_id = chat_req.trace_id or chat_req.run_id
        attempt_id = chat_req.attempt_id
        if not trace_id or not attempt_id:
            return None
        return TraceContext(
            trace_id=trace_id,
            run_id=chat_req.run_id or trace_id,
            attempt_id=attempt_id,
            message_id=str(chat_req.id),
            connection_id=str(chat_req.connection_id),
            player_name=chat_req.player_name or DEFAULT_PLAYER_DISPLAY_NAME,
            conversation_id=chat_req.conversation_id or DEFAULT_CONVERSATION_ID,
        )

    def _emit_request_rejected(
        self,
        *,
        state: ConnectionState,
        player_name: str | None,
        reason: str,
        chat_req: ChatRequest | None = None,
        command_type: str | None = None,
    ) -> None:
        """Emit request.rejected with metadata only (no message content)."""
        try:
            recorder = get_trace_recorder()
            if chat_req is not None:
                context = self._trace_context_for_request(chat_req)
            else:
                # Auth rejection 等无 ChatRequest 路径：生成 ephemeral correlation。
                ephemeral_id = str(uuid4())
                context = TraceContext(
                    trace_id=ephemeral_id,
                    run_id=ephemeral_id,
                    attempt_id=str(uuid4()),
                    message_id="",
                    connection_id=str(state.id),
                    player_name=player_name or DEFAULT_PLAYER_DISPLAY_NAME,
                    conversation_id=DEFAULT_CONVERSATION_ID,
                )
            if context is None:
                return
            attributes: dict[str, Any] = {"reason": reason}
            if command_type is not None:
                attributes["command_type"] = command_type
            if chat_req is not None and chat_req.delivery:
                attributes["delivery"] = chat_req.delivery
            recorder.emit(
                "request.rejected",
                context,
                status="rejected",
                attributes=attributes,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("trace_emit_failed", event="request.rejected", error=str(exc))

    def _emit_trace_events(
        self,
        chat_req: ChatRequest,
        *events: tuple[str, str, dict[str, Any]],
    ) -> None:
        """Emit one or more trace events; never raises into the chat path."""
        try:
            context = self._trace_context_for_request(chat_req)
            if context is None:
                return
            recorder = get_trace_recorder()
            for event_name, status, attributes in events:
                recorder.emit(
                    event_name,
                    context,
                    status=status,
                    attributes=attributes,
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("trace_emit_failed", error=str(exc))

    # -- routing -------------------------------------------------------------------

    async def handle_command(
        self,
        state: ConnectionState,
        cmd_type: str,
        content: str,
        player_name: str | None = None,
    ) -> None:
        if player_name:
            state._player_name = player_name
        if cmd_type == "login":
            await self.handle_login(state, content, player_name=player_name)
            return

        if not self.dev_mode and not await self.check_auth(state):
            error_msg = self.protocol.create_error_message("请先登录")
            await self._send_player_reply(
                state, error_msg, source="auth", player_name=player_name
            )
            self._emit_request_rejected(
                state=state,
                player_name=player_name,
                reason="auth_required",
                command_type=cmd_type,
            )
            return

        command_handlers = {
            "chat": lambda: self._handle_chat_command(state, content, player_name),
            "chat_script": lambda: self.handle_chat(
                state, content, delivery="scriptevent", player_name=player_name
            ),
            "context": lambda: self.handle_context(
                state, content, player_name=player_name
            ),
            "conversation": lambda: self.handle_conversation(
                state, content, player_name=player_name
            ),
            "template": lambda: self.handle_template(
                state, content, player_name=player_name
            ),
            "setting": lambda: self.handle_setting(
                state, content, player_name=player_name
            ),
            "mcp": lambda: self.handle_mcp(state, content, player_name=player_name),
            "ai_broadcast": lambda: self.handle_ai_broadcast(
                state, content, player_name=player_name
            ),
            "tool_approve": lambda: self.handle_tool_approval(
                state, content, approved=True, player_name=player_name
            ),
            "tool_deny": lambda: self.handle_tool_approval(
                state, content, approved=False, player_name=player_name
            ),
            "switch_model": lambda: self.handle_switch_model(
                state, content, player_name=player_name
            ),
            "help": lambda: self.handle_help(state, player_name=player_name),
            "save": lambda: self.handle_save(state, player_name=player_name),
            "run_command": lambda: self.handle_run_command(
                state, content, player_name=player_name
            ),
        }
        if handler := command_handlers.get(cmd_type):
            await handler()

    async def _handle_chat_command(
        self,
        state: ConnectionState,
        content: str,
        player_name: str | None,
    ) -> None:
        await self.handle_chat(
            state, content, delivery="tellraw", player_name=player_name
        )
        await self.broker.send_response(
            state.id,
            {
                "type": "ai_response_sync",
                "player_name": player_name
                or state._player_name
                or DEFAULT_PLAYER_DISPLAY_NAME,
                "role": "user",
                "text": content,
            },
        )

    # -- auth ----------------------------------------------------------------------

    async def handle_login(
        self,
        state: ConnectionState,
        password: str,
        player_name: str | None = None,
    ) -> None:
        host = self._require_host(state)
        if self.dev_mode:
            host.authenticated = True
            msg = self.protocol.create_info_message(
                "开发模式: 无需登录，已自动认证"
            )
            await self._send_player_reply(
                state, msg, source="login", player_name=player_name
            )
            return

        if self.jwt_handler.verify_password(password):
            if self.jwt_handler.is_token_valid(str(state.id)):
                host.authenticated = True
                msg = self.protocol.create_info_message("您已登录")
                await self._send_player_reply(
                    state, msg, source="login", player_name=player_name
                )
                return

            token = self.jwt_handler.generate_token()
            self.jwt_handler.save_token(str(state.id), token)
            host.authenticated = True

            msg = self.protocol.create_success_message("登录成功！")
            await self._send_player_reply(
                state, msg, source="login", player_name=player_name
            )
            logger.info("user_authenticated", connection_id=str(state.id))
        else:
            msg = self.protocol.create_error_message("密码错误")
            await self._send_player_reply(
                state, msg, source="login", player_name=player_name
            )

    async def check_auth(self, state: ConnectionState) -> bool:
        host = self._require_host(state)
        if host.authenticated:
            return True
        stored_token = self.jwt_handler.get_stored_token(str(state.id))
        if stored_token and self.jwt_handler.verify_token(stored_token):
            host.authenticated = True
            return True
        return False

    # -- chat ----------------------------------------------------------------------

    async def handle_chat(
        self,
        state: ConnectionState,
        content: str,
        delivery: str,
        player_name: str | None = None,
    ) -> None:
        if not content:
            msg = self.protocol.create_error_message("请输入聊天内容")
            await self._send_player_reply(
                state, msg, source="chat", player_name=player_name
            )
            return

        host = self._require_host(state)
        active_conversation_id = self.broker.get_active_conversation_id(
            state.id, player_name
        )
        chat_req = self._build_chat_request(
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
        chat_req.conversation_invalidation_epoch = (
            self.broker.get_conversation_invalidation_epoch(
                state.id, chat_req.player_name, chat_req.conversation_id
            )
        )
        chat_req.broadcast_ai_chat = (
            delivery == "tellraw"
            and host.should_broadcast_ai_chat(chat_req.player_name)
        )

        try:
            await self.broker.submit_request(state.id, chat_req, priority=0)
            logger.debug(
                "chat_request_queued",
                connection_id=str(state.id),
                player=player_name,
                content_length=len(content),
            )
            attributes = {
                "delivery": chat_req.delivery,
                "use_context": chat_req.use_context,
                "provider": chat_req.provider,
            }
            self._emit_trace_events(
                chat_req,
                ("trace.started", "started", attributes),
                ("request.accepted", "accepted", attributes),
            )
        except asyncio.QueueFull:
            self._emit_request_rejected(
                state=state,
                player_name=player_name,
                reason="queue_full",
                chat_req=chat_req,
            )
            msg = self.protocol.create_error_message("服务器繁忙，请稍后重试")
            await self._send_player_reply(
                state, msg, source="chat", player_name=player_name
            )

    async def handle_ui_chat(
        self,
        state: ConnectionState,
        player_name: str,
        message: str,
    ) -> None:
        if player_name:
            state._player_name = player_name

        logger.info(
            "ui_chat_received",
            connection_id=str(state.id),
            player=player_name,
            content_length=len(message),
        )

        if not self.dev_mode and not await self.check_auth(state):
            error_msg = self.protocol.create_error_message("请先登录")
            await self._send_player_reply(
                state, error_msg, source="auth", player_name=player_name
            )
            self._emit_request_rejected(
                state=state,
                player_name=player_name,
                reason="auth_required",
                command_type="ui_chat",
            )
            return

        await self.broker.send_response(
            state.id,
            {
                "type": "ai_response_sync",
                "player_name": player_name or DEFAULT_PLAYER_DISPLAY_NAME,
                "role": "user",
                "text": message,
            },
        )
        await self.handle_chat(
            state, message, delivery="tellraw", player_name=player_name
        )

    # -- context / conversation ----------------------------------------------------

    async def handle_context(
        self, state: ConnectionState, option: str, player_name: str | None = None
    ) -> None:
        host = self._require_host(state)
        session = host.get_player_session(player_name)

        parts = option.strip().split(None, 1) if option.strip() else []
        action = parts[0] if parts else "状态"

        if action in ("启用", "开启", "on", "enable"):
            session.context_enabled = True
            msg = self.protocol.create_success_message("上下文已启用")
        elif action in ("关闭", "off", "disable"):
            session.context_enabled = False
            msg = self.protocol.create_success_message(
                "上下文已关闭；现有对话历史不会被清除，如需清除请使用 AGENT 对话 clear"
            )
        elif action in ("状态", "status", ""):
            status = "启用" if session.context_enabled else "关闭"
            conversation_id = self.broker.get_active_conversation_id(
                state.id, player_name
            )
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
                    usage_percent = (
                        estimated_tokens / provider_config.context_window
                    ) * 100
                    context_usage = (
                        f"\n上下文使用: {usage_percent:.1f}% "
                        f"({estimated_tokens}/{provider_config.context_window} tokens)"
                    )
                else:
                    context_usage = (
                        f"\n上下文使用: ~{estimated_tokens} tokens (模型上下文窗口未知)"
                    )
            except Exception:
                pass

            msg = self.protocol.create_info_message(
                f"上下文状态: {status}"
                f"\n当前对话: {conversation_id}"
                f"\n当前对话轮数: {turns}/{self.settings.max_history_turns}"
                f"{context_usage}"
            )
        elif action in (
            "clear",
            "清除",
            "压缩",
            "compress",
            "保存",
            "save",
            "恢复",
            "restore",
            "列表",
            "list",
            "删除",
            "delete",
        ):
            await self.handle_conversation(state, option, player_name=player_name)
            return
        else:
            msg = self.protocol.create_error_message(
                "上下文命令仅管理是否携带历史。用法: AGENT 上下文 <启用/关闭/状态>\n"
                "对话的新建/切换/清除/保存/恢复请使用: AGENT 对话 <new/switch/clear/...>"
            )

        await self._send_player_reply(
            state, msg, source="context", player_name=player_name
        )

    async def handle_conversation(
        self, state: ConnectionState, option: str, player_name: str | None = None
    ) -> None:
        parts = option.strip().split(None, 1) if option.strip() else []
        action = parts[0].lower() if parts else "状态"
        if action in ("状态", "status", "", "list", "列表", "已保存", "saved"):
            msg = await self._handle_conversation(state, option, player_name)
        else:
            lock = self.broker.get_session_lock(state.id, player_name)
            async with lock:
                msg = await self._handle_conversation(state, option, player_name)

        await self._send_player_reply(
            state, msg, source="conversation", player_name=player_name
        )

    async def _handle_conversation(
        self, state: ConnectionState, option: str, player_name: str | None = None
    ) -> TellrawMessage:
        from core.conversation import get_conversation_manager

        conv_manager = get_conversation_manager(self.broker, self.settings)
        host = self._require_host(state)
        session = host.get_player_session(player_name)
        actor = player_name or session.player_name

        parts = option.strip().split(None, 1) if option.strip() else []
        action = parts[0].lower() if parts else "状态"
        arg = parts[1].strip() if len(parts) > 1 else ""
        conversation_id = self.broker.get_active_conversation_id(state.id, actor)

        if action in ("new", "新建", "创建"):
            new_id = self._generate_unique_conversation_id(state.id, actor, arg)
            if new_id is None:
                target_id = normalize_conversation_id(arg)
                return self.protocol.create_error_message(
                    f"对话 {target_id} 已存在，请使用 AGENT 对话 switch {target_id} 切换"
                )
            self.broker.bump_conversation_invalidation_epoch(
                state.id, actor, conversation_id
            )
            self.broker.set_active_conversation_id(state.id, actor, new_id)
            self.broker.set_conversation_history(state.id, actor, [], new_id)
            metadata = self.broker.ensure_conversation_metadata(state.id, actor, new_id)
            return self.protocol.create_success_message(
                f"已新建并切换到对话: #{metadata.short_id} {new_id}"
            )
        if action in ("switch", "切换"):
            if not arg:
                return self.protocol.create_error_message(
                    "请指定要切换的对话 ID\n用法: AGENT 对话 switch <ID>"
                )
            resolved_id = self.broker.resolve_conversation_short_id(
                state.id, actor, arg
            )
            target_id = normalize_conversation_id(resolved_id or arg)
            was_existing = self.broker.conversation_exists(state.id, actor, target_id)
            self.broker.bump_conversation_invalidation_epoch(
                state.id, actor, conversation_id
            )
            if target_id != conversation_id:
                self.broker.bump_conversation_invalidation_epoch(
                    state.id, actor, target_id
                )
            if not was_existing:
                self.broker.set_conversation_history(state.id, actor, [], target_id)
            self.broker.set_active_conversation_id(state.id, actor, target_id)
            history = self.broker.get_conversation_history(state.id, actor, target_id)
            turns = self._count_conversation_turns(history)
            metadata = self.broker.ensure_conversation_metadata(
                state.id, actor, target_id
            )
            display_id = f"#{metadata.short_id} {target_id}"
            message = (
                f"已切换到对话: {display_id}（{turns}轮）"
                if was_existing
                else f"已创建并切换到新会话: {display_id}"
            )
            return self.protocol.create_success_message(message)
        if action in ("clear", "清除"):
            self.broker.clear_conversation_history(state.id, actor, conversation_id)
            self.broker.set_conversation_history(state.id, actor, [], conversation_id)
            return self.protocol.create_success_message(
                f"对话 {conversation_id} 的历史已清除"
            )
        if action in ("状态", "status", ""):
            history = self.broker.get_conversation_history(
                state.id, actor, conversation_id
            )
            turns = self._count_conversation_turns(history)
            context_status = "启用" if session.context_enabled else "关闭"
            return self.protocol.create_info_message(
                f"当前对话: {conversation_id}"
                f"\n对话轮数: {turns}/{self.settings.max_history_turns}"
                f"\n压缩触发: {self._conversation_compression_status_text()}"
                f"\n上下文携带: {context_status}"
            )
        if action in ("list", "列表"):
            live_conversations = dict(
                self.broker.list_player_conversations(state.id, actor)
            )
            live_conversations.setdefault(
                conversation_id,
                len(
                    self.broker.get_conversation_history(
                        state.id, actor, conversation_id
                    )
                ),
            )
            for conv_id in live_conversations:
                self.broker.ensure_conversation_metadata(state.id, actor, conv_id)
            lines = ["当前连接内对话:"]
            for metadata in self.broker.list_player_conversation_metadata(
                state.id, actor
            ):
                conv_id = metadata.conversation_id
                message_count = live_conversations.get(
                    conv_id,
                    len(
                        self.broker.get_conversation_history(
                            state.id, actor, conv_id
                        )
                    ),
                )
                marker = " *" if conv_id == conversation_id else ""
                title = metadata.title or "未命名"
                lines.append(
                    f"• #{metadata.short_id} {conv_id}{marker} - {title} - {message_count} 条消息"
                )
            return self.protocol.create_info_message("\n".join(lines))
        if action in ("压缩", "compress"):
            success, result = await conv_manager.check_and_compress(
                state.id,
                actor,
                force=True,
                conversation_id=conversation_id,
                provider_name=session.current_provider or self.settings.default_provider,
            )
            return (
                self.protocol.create_success_message(result)
                if success
                else self.protocol.create_info_message(result)
            )
        if action in ("保存", "save"):
            success, result = await conv_manager.save_conversation(
                connection_id=state.id,
                player_name=actor,
                provider=session.current_provider or self.settings.default_provider,
                template=session.current_template,
                custom_variables=session.custom_variables,
                conversation_id=conversation_id,
            )
            return (
                self.protocol.create_success_message(f"对话已保存: {result}")
                if success
                else self.protocol.create_error_message(result)
            )
        if action in ("恢复", "restore"):
            if not arg:
                return self.protocol.create_error_message(
                    "请指定要恢复的会话 ID\n用法: AGENT 对话 restore <保存ID>"
                )
            success, result = await conv_manager.restore_conversation(
                state.id, arg, player_name=actor, conversation_id=conversation_id
            )
            return (
                self.protocol.create_success_message(result)
                if success
                else self.protocol.create_error_message(result)
            )
        if action in ("已保存", "saved"):
            conversations = await conv_manager.list_conversations(player_name=actor)
            list_text = conv_manager.format_conversation_list(conversations)
            return self.protocol.create_info_message(list_text)
        if action in ("删除", "delete"):
            if not arg:
                return self.protocol.create_error_message(
                    "请指定要删除的保存会话 ID\n用法: AGENT 对话 delete <保存ID>"
                )
            success, result = await conv_manager.delete_conversation(
                arg, player_name=actor
            )
            return (
                self.protocol.create_success_message(result)
                if success
                else self.protocol.create_error_message(result)
            )

        return self.protocol.create_error_message(
            "无效选项，请使用: new [ID]/switch <ID>/clear/status/list/"
            "compress/save/restore <保存ID>/saved/delete <保存ID>"
        )

    def _generate_unique_conversation_id(
        self,
        connection_id: UUID,
        player_name: str | None,
        requested_id: str = "",
    ) -> str | None:
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
        return (
            "chat-"
            + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            + "-"
            + uuid4().hex[:6]
        )

    def _count_conversation_turns(self, history: list) -> int:
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
            int(
                self.settings.max_history_turns
                * self.settings.compression_trigger_ratio
            ),
        )
        return (
            f"{threshold}轮，保留最近{self.settings.compression_keep_recent_turns}轮"
        )

    # -- model / template / settings -----------------------------------------------

    async def handle_switch_model(
        self, state: ConnectionState, provider: str, player_name: str | None = None
    ) -> None:
        available = self.settings.list_available_providers()
        if provider.lower() not in available:
            msg = self.protocol.create_error_message(
                f"不可用的提供商。可用: {', '.join(available)}"
            )
            await self._send_player_reply(
                state, msg, source="switch_model", player_name=player_name
            )
            return

        lock = self.broker.get_session_lock(state.id, player_name)
        async with lock:
            msg = await self._handle_switch_model(state, provider, player_name)

        await self._send_player_reply(
            state, msg, source="switch_model", player_name=player_name
        )

    async def _handle_switch_model(
        self, state: ConnectionState, provider: str, player_name: str | None = None
    ) -> TellrawMessage:
        host = self._require_host(state)
        session = host.get_player_session(player_name)
        session.current_provider = provider.lower()
        self.broker.clear_player_conversation_histories(state.id, player_name)
        config = self.settings.get_provider_config(provider.lower())
        return self.protocol.create_success_message(
            f"已切换到 {provider}/{config.model}，该玩家的运行时对话历史已清理"
        )

    async def handle_template(
        self, state: ConnectionState, content: str, player_name: str | None = None
    ) -> None:
        from services.agent.prompt import get_prompt_manager

        manager = get_prompt_manager()
        connection_id = str(state.id)
        host = self._require_host(state)
        session = host.get_player_session(player_name)
        actor = player_name or session.player_name

        if not content or content.strip() == "":
            current = manager.get_session_template(connection_id, actor)
            template = manager.get_template(current)
            if template:
                msg = self.protocol.create_info_message(
                    f"当前模板: {template.name} - {template.description}"
                )
            else:
                msg = self.protocol.create_info_message(f"当前模板: {current}")
        elif content.strip() == "list":
            templates = manager.list_templates()
            current = manager.get_session_template(connection_id, actor)
            lines = ["可用模板:"]
            for name in templates:
                template = manager.get_template(name)
                marker = " *" if name == current else ""
                desc = template.description if template else ""
                lines.append(f"• {name}{marker} - {desc}")
            msg = self.protocol.create_info_message("\n".join(lines))
        else:
            template_name = content.strip()
            if manager.set_session_template(connection_id, actor, template_name):
                session.current_template = template_name
                session.custom_variables = manager.get_session_variables(
                    connection_id, actor
                )
                template = manager.get_template(template_name)
                desc = template.description if template else ""
                msg = self.protocol.create_success_message(
                    f"已切换到模板: {template_name} ({desc})"
                )
            else:
                templates = ", ".join(manager.list_templates())
                msg = self.protocol.create_error_message(
                    f"模板不存在: {template_name}\n可用: {templates}"
                )

        await self._send_player_reply(
            state, msg, source="template", player_name=player_name
        )

    async def handle_setting(
        self, state: ConnectionState, content: str, player_name: str | None = None
    ) -> None:
        from services.agent.prompt import get_prompt_manager

        manager = get_prompt_manager()
        connection_id = str(state.id)
        host = self._require_host(state)
        session = host.get_player_session(player_name)
        actor = player_name or session.player_name

        if not content:
            msg = self.protocol.create_error_message("请输入设置项")
            await self._send_player_reply(
                state, msg, source="setting", player_name=player_name
            )
            return

        parts = content.strip().split(None, 1)
        if len(parts) < 2:
            msg = self.protocol.create_error_message(
                "用法: AGENT 设置 变量 <名称> <值>\n      AGENT 设置 别名 <名称> <别名>"
            )
            await self._send_player_reply(
                state, msg, source="setting", player_name=player_name
            )
            return

        setting_type = parts[0]
        rest = parts[1]

        if setting_type == "变量":
            if "=" in rest:
                name_val = rest.split("=", 1)
                name = name_val[0].strip()
                value = name_val[1].strip()
            else:
                var_parts = rest.split(None, 1)
                if len(var_parts) < 2:
                    msg = self.protocol.create_error_message(
                        "用法: AGENT 设置 变量 <名称> <值>"
                    )
                    await self._send_player_reply(
                        state, msg, source="setting", player_name=player_name
                    )
                    return
                name = var_parts[0]
                value = var_parts[1]

            manager.set_session_variable(connection_id, actor, name, value)
            session.custom_variables = manager.get_session_variables(
                connection_id, actor
            )
            display_name = f"custom_{name}" if not name.startswith("custom_") else name
            msg = self.protocol.create_success_message(
                f"已设置变量: {display_name} = {value}"
            )
        elif setting_type == "别名":
            msg = self.protocol.create_info_message("别名管理功能开发中")
        else:
            msg = self.protocol.create_error_message("未知的设置类型，请使用: 变量")

        await self._send_player_reply(
            state, msg, source="setting", player_name=player_name
        )

    async def handle_help(
        self, state: ConnectionState, player_name: str | None = None
    ) -> None:
        help_text = self.protocol.get_help_text()
        msg = self.protocol.create_info_message(help_text)
        await self._send_player_reply(
            state, msg, source="help", player_name=player_name
        )

    async def handle_save(
        self, state: ConnectionState, player_name: str | None = None
    ) -> None:
        await self.handle_conversation(state, "save", player_name=player_name)

    async def handle_tool_approval(
        self,
        state: ConnectionState,
        content: str,
        *,
        approved: bool,
        player_name: str | None = None,
    ) -> None:
        """处理 `AGENT 同意|拒绝 [approval_id|对话|永远]`。

        - 无参数：智能选择当前对话下唯一待审批批次的最早一项
        - 显式 id：按 id 决策
        - `对话` / `永远`（仅同意）：打开自动批准，并尽量把当前待审批一并批掉
          - 对话：仅当前对话生效，切换对话后需重新开启
          - 永远：本连接该玩家所有对话生效（切换对话仍生效，断线清零）
        """
        from services.agent.runtime import get_agent_runtime

        source = "tool_approve" if approved else "tool_deny"
        raw = content.strip()
        token = raw.split(None, 1)[0] if raw else ""
        token_lower = token.casefold()

        host = self._require_host(state)
        owner = player_name or state._player_name or DEFAULT_PLAYER_DISPLAY_NAME
        conversation_id = self.broker.get_active_conversation_id(state.id, owner)
        store = get_agent_runtime().get_pending_approval_store(self.settings)

        # `AGENT 同意 对话|永远` / `AGENT 拒绝 对话|永远`
        scope_tokens = {
            "对话": "conversation",
            "conv": "conversation",
            "conversation": "conversation",
            "永远": "forever",
            "always": "forever",
            "forever": "forever",
            "永久": "forever",
        }
        scope = scope_tokens.get(token_lower) if token else None
        if scope is not None:
            if not approved:
                if scope == "forever":
                    host.clear_auto_approve_tools(owner, forever=True)
                    msg = self.protocol.create_success_message(
                        "已关闭本连接的自动同意（永远）"
                    )
                else:
                    host.clear_auto_approve_tools(
                        owner, conversation_id=conversation_id
                    )
                    msg = self.protocol.create_success_message(
                        f"已关闭当前对话的自动同意（对话={conversation_id}）"
                    )
                await self._send_player_reply(
                    state, msg, source=source, player_name=player_name
                )
                return

            if scope == "forever":
                host.enable_auto_approve_tools_forever(owner)
                scope_label = "本连接（永远，切换对话仍生效）"
            else:
                host.enable_auto_approve_tools_conversation(owner, conversation_id)
                scope_label = f"当前对话（{conversation_id}）"
            msg = self.protocol.create_success_message(
                f"已开启工具自动同意：{scope_label}"
            )
            await self._send_player_reply(
                state, msg, source=source, player_name=player_name
            )
            # 若当前有待审批，尽量整批自动批掉（多批时只处理最早一批）
            await self._auto_approve_pending_batches(
                state,
                store=store,
                owner=owner,
                conversation_id=conversation_id,
                player_name=player_name,
                source=source,
            )
            return

        # 省略 id → 智能解析；显式 id 走原路径
        approval_id, resolve_reason = store.resolve_target_approval_id(
            connection_id=str(state.id),
            player_name=owner,
            conversation_id=conversation_id,
            approval_id=token or None,
        )
        if approval_id is None:
            verb = "同意" if approved else "拒绝"
            hint = resolve_reason or f"用法: AGENT {verb} [approval_id|对话|永远]"
            msg = self.protocol.create_error_message(hint)
            await self._send_player_reply(
                state, msg, source=source, player_name=player_name
            )
            return

        await self._apply_approval_decision(
            state,
            store=store,
            owner=owner,
            conversation_id=conversation_id,
            approval_id=approval_id,
            approved=approved,
            player_name=player_name,
            source=source,
        )

    async def _auto_approve_pending_batches(
        self,
        state: ConnectionState,
        *,
        store: Any,
        owner: str,
        conversation_id: str,
        player_name: str | None,
        source: str,
    ) -> None:
        """开启自动同意后，把当前对话最早一批待审批全部批掉并 resume。"""
        pending_items = store.list_pending_for_owner(
            connection_id=str(state.id),
            player_name=owner,
            conversation_id=conversation_id,
        )
        if not pending_items:
            return
        # 只处理最早一批，避免多批交叉 resume
        first_batch = pending_items[0].batch_id
        batch_items = [
            item for item in pending_items if item.batch_id == first_batch
        ]
        completed_batch: list[Any] | None = None
        last_pending = None
        for item in batch_items:
            pending, reason, completed = store.record_decision(
                connection_id=str(state.id),
                player_name=owner,
                conversation_id=conversation_id,
                approval_id=item.approval_id,
                approved=True,
            )
            if pending is None:
                logger.warning(
                    "auto_approve_batch_item_failed",
                    approval_id=item.approval_id,
                    reason=reason,
                )
                continue
            last_pending = pending
            if completed is not None:
                completed_batch = completed
        if last_pending is None:
            return
        info = self.protocol.create_info_message(
            f"已自动批准当前批次 {first_batch}（{len(batch_items)} 项）"
        )
        await self._send_player_reply(
            state, info, source=source, player_name=player_name
        )
        if completed_batch is None:
            return
        await self._resume_from_completed_batch(
            state,
            completed_batch=completed_batch,
            pending=last_pending,
            owner=owner,
            conversation_id=conversation_id,
            player_name=player_name,
            source=source,
        )

    async def _apply_approval_decision(
        self,
        state: ConnectionState,
        *,
        store: Any,
        owner: str,
        conversation_id: str,
        approval_id: str,
        approved: bool,
        player_name: str | None,
        source: str,
    ) -> None:
        pending, reject_reason, completed_batch = store.record_decision(
            connection_id=str(state.id),
            player_name=owner,
            conversation_id=conversation_id,
            approval_id=approval_id,
            approved=approved,
        )
        if pending is None:
            msg = self.protocol.create_error_message(reject_reason or "审批不可用")
            await self._send_player_reply(
                state, msg, source=source, player_name=player_name
            )
            return

        if not approved:
            msg = self.protocol.create_info_message(
                f"已拒绝工具调用 [{approval_id}] {pending.tool_name}"
            )
        else:
            msg = self.protocol.create_success_message(
                f"已批准工具调用 [{approval_id}] {pending.tool_name}"
            )
        await self._send_player_reply(
            state, msg, source=source, player_name=player_name
        )

        if completed_batch is None:
            wait_msg = self.protocol.create_info_message(
                f"已记录 [{approval_id}] 的决策；同批还有待审批项，"
                f"全部决策后才会继续执行（batch={pending.batch_id}）"
            )
            await self._send_player_reply(
                state, wait_msg, source=source, player_name=player_name
            )
            return

        await self._resume_from_completed_batch(
            state,
            completed_batch=completed_batch,
            pending=pending,
            owner=owner,
            conversation_id=conversation_id,
            player_name=player_name,
            source=source,
        )

    async def _resume_from_completed_batch(
        self,
        state: ConnectionState,
        *,
        completed_batch: list[Any],
        pending: Any,
        owner: str,
        conversation_id: str,
        player_name: str | None,
        source: str,
    ) -> None:
        host = self._require_host(state)
        approvals_payload: dict[str, Any] = {}
        for item in completed_batch:
            if item.decision is True:
                approvals_payload[item.tool_call_id] = True
            else:
                approvals_payload[item.tool_call_id] = {
                    "kind": "tool-denied",
                    "message": f"玩家拒绝了工具调用 {item.tool_name}",
                }

        resume_msg = self.protocol.create_success_message(
            f"批次 {pending.batch_id} 决策完成（{len(completed_batch)} 项），继续执行..."
        )
        await self._send_player_reply(
            state, resume_msg, source=source, player_name=player_name
        )

        generation = self.broker.get_conversation_generation(
            state.id, owner, conversation_id
        )
        invalidation_epoch = self.broker.get_conversation_invalidation_epoch(
            state.id, owner, conversation_id
        )
        session = host.get_player_session(owner)
        # 审批恢复：保留原始 trace/run_id（ingress 时二者相等），生成新 attempt_id。
        original_trace_id = pending.run_id
        resume_attempt_id = str(uuid4())
        chat_req = ChatRequest(
            connection_id=state.id,
            content="",
            player_name=owner,
            use_context=pending.use_context,
            provider=pending.provider or session.current_provider,
            delivery=pending.delivery,  # type: ignore[arg-type]
            conversation_id=conversation_id,
            conversation_generation=generation,
            conversation_invalidation_epoch=invalidation_epoch,
            broadcast_ai_chat=pending.broadcast_ai_chat,
            auto_approve_tools=host.should_auto_approve_tools(
                owner, conversation_id
            ),
            run_id=original_trace_id,
            trace_id=original_trace_id,
            attempt_id=resume_attempt_id,
            resume_approval_id=pending.batch_id,
            deferred_tool_results={"approvals": approvals_payload},
            resume_message_history=pending.messages,
        )
        try:
            await self.broker.submit_request(state.id, chat_req, priority=0)
            attributes = {
                "delivery": chat_req.delivery,
                "resume": True,
                "batch_id": pending.batch_id,
            }
            self._emit_trace_events(
                chat_req,
                ("request.accepted", "accepted", attributes),
                ("request.resumed", "resumed", attributes),
            )
        except asyncio.QueueFull:
            self._emit_request_rejected(
                state=state,
                player_name=player_name,
                reason="queue_full",
                chat_req=chat_req,
            )
            msg = self.protocol.create_error_message("服务器繁忙，请稍后重试")
            await self._send_player_reply(
                state, msg, source=source, player_name=player_name
            )

    async def handle_run_command(
        self,
        state: ConnectionState,
        command: str,
        player_name: str | None = None,
    ) -> None:
        if not command:
            msg = self.protocol.create_error_message("请输入命令")
            await self._send_player_reply(
                state, msg, source="run_command", player_name=player_name
            )
            return

        delivery = self._delivery(state)
        if delivery is None:
            msg = self.protocol.create_error_message("连接不可用")
            await self._send_player_reply(
                state, msg, source="run_command", player_name=player_name
            )
            return

        try:
            await delivery.send_raw_command(command, source="run_command")
        except ValueError as exc:
            msg = self.protocol.create_error_message(str(exc))
            await self._send_player_reply(
                state, msg, source="run_command", player_name=player_name
            )
            return

        logger.info(
            "command_executed",
            connection_id=str(state.id),
            command=command,
        )

    async def handle_ai_broadcast(
        self,
        state: ConnectionState,
        content: str,
        player_name: str | None = None,
    ) -> None:
        msg = self._handle_ai_broadcast(state, content)
        await self._send_player_reply(
            state, msg, source="ai_broadcast", player_name=player_name
        )

    def _handle_ai_broadcast(
        self, state: ConnectionState, content: str
    ) -> TellrawMessage:
        host = self._require_host(state)
        parts = content.strip().split()
        action = parts[0].lower() if parts else "状态"

        if action in ("状态", "status", ""):
            mode = "开启" if host.ai_broadcast_all else "关闭"
            players = "、".join(sorted(host.ai_broadcast_players)) or "无"
            return self.protocol.create_info_message(
                f"AI 广播状态:\n全服广播: {mode}\n指定玩家: {players}"
            )

        if action in ("关闭", "off", "disable"):
            host.ai_broadcast_all = False
            host.ai_broadcast_players.clear()
            return self.protocol.create_success_message("AI 广播已关闭")

        if action in ("全服", "all"):
            if len(parts) < 2:
                return self.protocol.create_error_message(
                    "用法: AGENT 广播 全服 <开启|关闭>"
                )
            toggle = parts[1].lower()
            if toggle in ("开启", "启用", "on", "enable"):
                host.ai_broadcast_all = True
                return self.protocol.create_success_message("AI 全服广播已开启")
            if toggle in ("关闭", "off", "disable"):
                host.ai_broadcast_all = False
                return self.protocol.create_success_message("AI 全服广播已关闭")
            return self.protocol.create_error_message(
                "用法: AGENT 广播 全服 <开启|关闭>"
            )

        if action in ("玩家", "player"):
            if len(parts) < 3:
                return self.protocol.create_error_message(
                    "用法: AGENT 广播 玩家 <玩家名> <开启|关闭>"
                )
            target_player = parts[1]
            toggle = parts[2].lower()
            if toggle in ("开启", "启用", "on", "enable"):
                host.ai_broadcast_players.add(target_player)
                return self.protocol.create_success_message(
                    f"已开启 {target_player} 的 AI 聊天广播"
                )
            if toggle in ("关闭", "off", "disable"):
                host.ai_broadcast_players.discard(target_player)
                return self.protocol.create_success_message(
                    f"已关闭 {target_player} 的 AI 聊天广播"
                )
            return self.protocol.create_error_message(
                "用法: AGENT 广播 玩家 <玩家名> <开启|关闭>"
            )

        return self.protocol.create_error_message(
            "用法: AGENT 广播 <状态|关闭|全服 开启|关闭|玩家 <玩家名> 开启|关闭>"
        )

    async def handle_mcp(
        self,
        state: ConnectionState,
        content: str,
        player_name: str | None = None,
    ) -> None:
        from services.agent.mcp import MCPConnectionStatus, get_mcp_manager

        manager = get_mcp_manager(self.settings)
        parts = content.strip().split(None, 1) if content.strip() else []
        action = parts[0] if parts else ""
        arg = parts[1] if len(parts) > 1 else ""

        if action == "list":
            if not self.settings.mcp.enabled:
                msg = self.protocol.create_info_message("MCP 功能未启用")
            elif not self.settings.mcp.servers:
                msg = self.protocol.create_info_message("未配置 MCP 服务器")
            else:
                lines = [f"MCP 服务器 ({len(self.settings.mcp.servers)}):"]
                for name, _config in self.settings.mcp.servers.items():
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
                msg = self.protocol.create_info_message("\n".join(lines))
        elif action == "status":
            if not manager.is_initialized:
                msg = self.protocol.create_info_message(
                    "MCP 管理器尚未初始化\n请等待服务启动完成"
                )
            else:
                status = manager.get_status_summary()
                lines = [
                    "MCP 状态概览:",
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
                msg = self.protocol.create_info_message("\n".join(lines))
        elif action == "reload":
            if not manager.is_initialized:
                msg = self.protocol.create_error_message("MCP 管理器尚未初始化")
            elif arg:
                if arg not in manager.servers:
                    msg = self.protocol.create_error_message(f"未找到服务器: {arg}")
                else:
                    success = manager.reload_toolset(arg)
                    if success:
                        from services.agent.runtime import get_agent_runtime

                        get_agent_runtime().refresh_mcp_tools(self.settings)
                        msg = self.protocol.create_success_message(
                            f"服务器 {arg} 配置已重新加载"
                        )
                    else:
                        msg = self.protocol.create_error_message(
                            f"服务器 {arg} 配置重载失败"
                        )
            else:
                msg = self.protocol.create_error_message(
                    "请指定服务器名称: AGENT MCP reload <名称>"
                )
        else:
            msg = self.protocol.create_info_message(
                "MCP 管理命令:\n"
                "  list - 列出所有服务器\n"
                "  status - 显示详细状态\n"
                "  reload <名称> - 重新加载服务器配置"
            )

        await self._send_player_reply(state, msg, source="mcp", player_name=player_name)
