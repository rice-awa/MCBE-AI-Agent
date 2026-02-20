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
        self.dev_mode = settings.dev_mode
        self.connection_manager = ConnectionManager(broker, dev_mode=self.dev_mode)
        self.protocol_handler = MinecraftProtocolHandler()
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

        # 其他命令需要认证（开发模式下跳过）
        if not self.dev_mode and not await self.check_auth(state):
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
        elif cmd_type == "template":
            await self.handle_template(state, content)
        elif cmd_type == "setting":
            await self.handle_setting(state, content)
        elif cmd_type == "mcp":
            await self.handle_mcp(state, content)
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
        # 开发模式下跳过登录验证
        if self.dev_mode:
            msg = self.protocol_handler.create_info_message(
                "开发模式: 无需登录，已自动认证"
            )
            await self._send_ws_payload(state, msg, source="login")
            return

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
        # 导入对话管理器
        from core.conversation import get_conversation_manager

        conv_manager = get_conversation_manager(self.broker, self.settings)

        # 解析选项
        parts = option.strip().split(None, 1) if option.strip() else []
        action = parts[0] if parts else ""
        arg = parts[1] if len(parts) > 1 else ""

        if action == "启用":
            state.context_enabled = True
            msg = self.protocol_handler.create_success_message("上下文已启用")
        elif action == "关闭":
            state.context_enabled = False
            self.broker.clear_conversation_history(state.id)
            msg = self.protocol_handler.create_success_message("上下文已关闭")
        elif action == "状态":
            status = "启用" if state.context_enabled else "关闭"
            history = self.broker.get_conversation_history(state.id)
            turns = self._count_conversation_turns(history)

            # 获取上下文使用信息
            context_usage = ""
            try:
                # 使用当前提供商或默认提供商
                provider = state.current_provider or self.settings.default_provider
                provider_config = self.settings.get_provider_config(provider)
                message_count = len(history)
                estimated_tokens = message_count * 100  # 简单估算
                if provider_config.context_window:
                    usage_percent = (estimated_tokens / provider_config.context_window) * 100
                    context_usage = f"\n上下文使用: {usage_percent:.1f}% ({estimated_tokens}/{provider_config.context_window} tokens)"
                else:
                    context_usage = f"\n上下文使用: ~{estimated_tokens} tokens (模型上下文窗口未知)"
            except Exception:
                pass

            msg = self.protocol_handler.create_info_message(
                f"上下文状态: {status}\n当前对话轮数: {turns}/{self.settings.max_history_turns}{context_usage}"
            )
            await self._send_ws_payload(state, msg, source="context")
            return
        elif action == "压缩":
            # 手动触发压缩
            success, result = await conv_manager.check_and_compress(state.id, force=True)
            if success:
                msg = self.protocol_handler.create_success_message(result)
            else:
                msg = self.protocol_handler.create_info_message(result)
        elif action == "保存":
            # 保存当前对话
            success, result = await conv_manager.save_conversation(
                connection_id=state.id,
                player_name=state.player_name,
                provider=state.current_provider or self.settings.default_provider,
                template=state.current_template,
                custom_variables=state.custom_variables,
            )
            if success:
                msg = self.protocol_handler.create_success_message(f"对话已保存: {result}")
            else:
                msg = self.protocol_handler.create_error_message(result)
        elif action == "恢复":
            # 恢复对话
            if not arg:
                msg = self.protocol_handler.create_error_message(
                    "请指定要恢复的会话 ID\n用法: AGENT 上下文 恢复 <ID>"
                )
            else:
                success, result = await conv_manager.restore_conversation(state.id, arg)
                if success:
                    msg = self.protocol_handler.create_success_message(result)
                else:
                    msg = self.protocol_handler.create_error_message(result)
        elif action == "列表":
            # 列出保存的对话
            conversations = await conv_manager.list_conversations()
            list_text = conv_manager.format_conversation_list(conversations)
            msg = self.protocol_handler.create_info_message(list_text)
        elif action == "删除":
            # 删除对话
            if not arg:
                msg = self.protocol_handler.create_error_message(
                    "请指定要删除的会话 ID\n用法: AGENT 上下文 删除 <ID>"
                )
            else:
                success, result = await conv_manager.delete_conversation(arg)
                if success:
                    msg = self.protocol_handler.create_success_message(result)
                else:
                    msg = self.protocol_handler.create_error_message(result)
        elif action == "清除":
            # 清除对话历史
            self.broker.clear_conversation_history(state.id)
            msg = self.protocol_handler.create_success_message("对话历史已清除")
        else:
            msg = self.protocol_handler.create_error_message(
                "无效选项，请使用: 启用/关闭/状态/压缩/保存/恢复 <ID>/列表/删除 <ID>/清除"
            )

        await self._send_ws_payload(state, msg, source="context")

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

    async def handle_template(self, state: Any, content: str) -> None:
        """处理模板管理"""
        from services.agent.prompt import get_prompt_manager

        manager = get_prompt_manager()
        connection_id = str(state.id)

        if not content or content.strip() == "":
            # 显示当前模板
            current = manager.get_connection_template(connection_id)
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
            current = manager.get_connection_template(connection_id)
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
            if manager.set_connection_template(connection_id, template_name):
                # 同时更新 ConnectionState 的模板状态
                state.current_template = template_name
                state.custom_variables = manager.get_connection_variables(connection_id)
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

        await self._send_ws_payload(state, msg, source="template")

    async def handle_setting(self, state: Any, content: str) -> None:
        """处理设置管理"""
        from services.agent.prompt import get_prompt_manager

        manager = get_prompt_manager()
        connection_id = str(state.id)

        if not content:
            msg = self.protocol_handler.create_error_message("请输入设置项")
            await self._send_ws_payload(state, msg, source="setting")
            return

        parts = content.strip().split(None, 1)
        if len(parts) < 2:
            msg = self.protocol_handler.create_error_message(
                "用法: AGENT 设置 变量 <名称> <值>\n      AGENT 设置 别名 <名称> <别名>"
            )
            await self._send_ws_payload(state, msg, source="setting")
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
                    await self._send_ws_payload(state, msg, source="setting")
                    return
                name = var_parts[0]
                value = var_parts[1]

            # PromptManager.set_connection_variable 会自动添加 custom_ 前缀
            manager.set_connection_variable(connection_id, name, value)
            state.custom_variables = manager.get_connection_variables(connection_id)

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

        await self._send_ws_payload(state, msg, source="setting")

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

    async def handle_mcp(self, state: Any, content: str) -> None:
        """处理 MCP 服务器管理命令"""
        from services.agent.mcp import get_mcp_manager, MCPConnectionStatus

        manager = get_mcp_manager()
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

        await self._send_ws_payload(state, msg, source="mcp")

    async def _send_ws_payload(self, state: Any, payload: str, source: str) -> None:
        """统一发送 WebSocket 响应并记录原始输出。"""
        await state.websocket.send(payload)
        ws_raw_logger.info(
            "websocket_response_sent",
            connection_id=str(state.id),
            source=source,
            payload=payload,
        )
