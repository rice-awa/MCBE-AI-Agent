"""PydanticAI Agent 工具定义"""

from __future__ import annotations

import json
from typing import Any, Protocol, cast

from pydantic_ai import Agent, RunContext
from pydantic_ai.toolsets import FunctionToolset

from config.logging import get_logger
from models.agent import AgentDependencies, MCColor
from models.minecraft import MinecraftCommand, sanitize_tellraw_target
from services.agent.harness.prompting import render_schema_description_prefix
from services.agent.tool_results import CommandResult, ToolResult
from services.agent.mcwiki import (
    build_mcwiki_url,
    build_page_url,
    build_search_params,
    normalize_limit,
)

logger = get_logger(__name__)

BUILTIN_TOOLSET_ID = "mcbe-builtin"


class ToolRegistrationSettings(Protocol):
    runtime_harness_enabled: bool
    runtime_harness_schema_enabled: bool
    runtime_harness_audit_enabled: bool
    runtime_harness_audit_path: str
    runtime_harness_audit_max_records: int


class AgentToolSettings(Protocol):
    mcwiki_base_url: str

    def list_available_providers(self) -> list[str]:
        ...


def _runtime_harness_schema_enabled(settings: ToolRegistrationSettings | None) -> bool:
    if settings is None:
        return True
    return settings.runtime_harness_enabled and settings.runtime_harness_schema_enabled


def get_agent_tools_container(
    chat_agent: Agent[AgentDependencies, str],
) -> FunctionToolset[Any] | None:
    """通过公开 `agent.toolsets` 定位内置工具容器，不访问私有字段。"""
    for toolset in chat_agent.toolsets:
        if isinstance(toolset, FunctionToolset):
            return toolset
        tools = getattr(toolset, "tools", None)
        if isinstance(tools, dict) and tools:
            # 兼容公开 toolsets 中的工具容器
            return cast(FunctionToolset[Any], toolset)
    return None


def iter_registered_tools(chat_agent: Agent[AgentDependencies, str]) -> dict[str, Any]:
    """返回 agent 上已注册的工具名 -> Tool 映射（仅公开 toolsets）。"""
    collected: dict[str, Any] = {}
    for toolset in chat_agent.toolsets:
        tools = getattr(toolset, "tools", None)
        if isinstance(tools, dict):
            collected.update(tools)
    return collected


def _enhance_registered_tool_descriptions(chat_agent: Agent[AgentDependencies, str]) -> None:
    for tool_name, tool in iter_registered_tools(chat_agent).items():
        description = tool.description or ""
        if description.startswith("[运行时 Harness]"):
            continue
        tool.description = render_schema_description_prefix(tool_name) + description.strip()


def _stringify_tool_results(chat_agent: Agent[AgentDependencies, str]) -> None:
    for tool in iter_registered_tools(chat_agent).values():
        function = getattr(tool, "function", None)
        if function is None or getattr(function, "_tool_result_stringified", False):
            continue

        async def wrapper(*args: Any, _function=function, **kwargs: Any) -> str:
            result = await _function(*args, **kwargs)
            return str(result)

        setattr(wrapper, "_tool_result_stringified", True)
        tool.function = wrapper
        function_schema = getattr(tool, "function_schema", None)
        if function_schema is not None and hasattr(function_schema, "function"):
            function_schema.function = wrapper


def _tool_success(text: str) -> ToolResult:
    return ToolResult.ok(text)


def _tool_failure(
    text: str,
    *,
    error_kind: str = "PERMANENT",
    retryable: bool = False,
    external_state_unknown: bool = False,
    diagnostic_summary: str | None = None,
) -> ToolResult:
    return ToolResult.failure(
        text,
        error_kind=error_kind,  # type: ignore[arg-type]
        retryable=retryable,
        external_state_unknown=external_state_unknown,
        diagnostic_summary=diagnostic_summary,
    )


async def _run_command_result(
    ctx: RunContext[AgentDependencies],
    command: str,
) -> CommandResult:
    """调用 deps.run_command，兼容结构化 CommandResult 与旧字符串回调。"""
    raw = await ctx.deps.run_command(command)
    if isinstance(raw, CommandResult):
        return raw
    # 兼容测试/旧回调返回 str
    text = str(raw) if raw is not None else ""
    if text.startswith("命令执行失败: 连接") or text.startswith("命令执行失败: 连接不存在"):
        return CommandResult.connection_unavailable(text)
    if "超时" in text and ("未收到" in text or "外部状态" in text or text.startswith("命令执行超时")):
        return CommandResult.timeout_unknown(text)
    if text.startswith("命令执行失败"):
        return CommandResult.failed(text)
    return CommandResult.ok(text or "命令执行成功")


def register_agent_tools(
    chat_agent: Agent[AgentDependencies, str],
    settings: ToolRegistrationSettings | None = None,
) -> None:
    """注册 Agent 工具到指定的 Agent 实例"""

    @chat_agent.tool
    async def run_minecraft_command(
        ctx: RunContext[AgentDependencies],
        command: str,
    ) -> str:
        """
        执行 Minecraft 命令

        Args:
            ctx: 运行上下文
            command: 要执行的命令（不包括前导斜杠）

        Returns:
            执行结果描述
        """
        logger.info(
            "agent_tool_call",
            tool="run_minecraft_command",
            command=command,
            connection_id=str(ctx.deps.connection_id),
            run_id=ctx.deps.run_id,
            player_name=ctx.deps.player_name,
        )

        try:
            result = await _run_command_result(ctx, command)
            return ToolResult.from_command_result(result)
        except Exception as e:
            logger.error(
                "agent_tool_error",
                tool="run_minecraft_command",
                error=str(e),
                run_id=ctx.deps.run_id,
            )
            return _tool_failure(
                f"命令执行失败: {str(e)}",
                error_kind="INTERNAL",
                diagnostic_summary=str(e),
            )

    @chat_agent.tool
    async def run_minecraft_commands(
        ctx: RunContext[AgentDependencies],
        commands: list[str],
    ) -> str:
        """
        批量执行 Minecraft 命令，注意一定要遵循MCBE的语法

        Args:
            ctx: 运行上下文
            commands: 要执行的命令数组（不包括前导斜杠）

        Returns:
            每条命令的执行结果
        """
        logger.info(
            "agent_tool_call",
            tool="run_minecraft_commands",
            command_count=len(commands),
            connection_id=str(ctx.deps.connection_id),
            run_id=ctx.deps.run_id,
        )

        results: list[str] = []
        any_failure = False
        any_unknown = False
        any_transient = False
        for index, command in enumerate(commands, start=1):
            try:
                result = await _run_command_result(ctx, command)
                tool_result = ToolResult.from_command_result(result)
                results.append(f"{index}. {command}: {tool_result.output}")
                if not tool_result.is_success:
                    any_failure = True
                    if tool_result.external_state_unknown:
                        any_unknown = True
                    if tool_result.retryable:
                        any_transient = True
            except Exception as e:
                any_failure = True
                logger.error(
                    "agent_tool_error",
                    tool="run_minecraft_commands",
                    command=command,
                    error=str(e),
                )
                results.append(f"{index}. {command}: 命令执行失败: {str(e)}")
        text = "\n".join(results) if results else "没有可执行的命令"
        if any_failure:
            return _tool_failure(
                text,
                error_kind="TRANSIENT" if (any_transient or any_unknown) else "PERMANENT",
                retryable=any_transient and not any_unknown,
                external_state_unknown=any_unknown,
            )
        return _tool_success(text)

    @chat_agent.tool
    async def send_game_message(
        ctx: RunContext[AgentDependencies],
        message: str,
        broadcast: bool = False,
    ) -> str:
        """
        向游戏发送消息

        Args:
            ctx: 运行上下文
            message: 要发送的消息
            broadcast: 是否广播给全服；默认只发送给触发玩家

        Returns:
            发送结果
        """
        logger.info(
            "agent_tool_call",
            tool="send_game_message",
            connection_id=str(ctx.deps.connection_id),
            run_id=ctx.deps.run_id,
        )

        try:
            target = "@a" if broadcast else ctx.deps.player_name
            command = build_tellraw_command(message, MCColor.GREEN, target)
            result = await _run_command_result(ctx, command)
            return ToolResult.from_command_result(
                result,
                success_text="消息已发送到游戏",
                failure_prefix="消息发送失败",
            )
        except Exception as e:
            logger.error(
                "agent_tool_error",
                tool="send_game_message",
                error=str(e),
            )
            return _tool_failure(
                f"消息发送失败: {str(e)}",
                error_kind="INTERNAL",
                diagnostic_summary=str(e),
            )

    @chat_agent.tool
    async def send_colored_message(
        ctx: RunContext[AgentDependencies],
        message: str,
        color: str = "§a",
        broadcast: bool = False,
    ) -> str:
        """
        发送带颜色的聊天消息

        Args:
            ctx: 运行上下文
            message: 消息内容
            color: Minecraft 颜色代码，例如 "§a"
            broadcast: 是否广播给全服；默认只发送给触发玩家

        Returns:
            发送结果
        """
        logger.info(
            "agent_tool_call",
            tool="send_colored_message",
            color=color,
            connection_id=str(ctx.deps.connection_id),
            run_id=ctx.deps.run_id,
        )

        try:
            target = "@a" if broadcast else ctx.deps.player_name
            command = build_tellraw_command(message, color, target)
            result = await _run_command_result(ctx, command)
            return ToolResult.from_command_result(
                result,
                success_text="彩色消息已发送",
                failure_prefix="彩色消息发送失败",
            )
        except Exception as e:
            logger.error(
                "agent_tool_error",
                tool="send_colored_message",
                error=str(e),
            )
            return _tool_failure(
                f"彩色消息发送失败: {str(e)}",
                error_kind="INTERNAL",
                diagnostic_summary=str(e),
            )

    @chat_agent.tool
    async def send_title_message(
        ctx: RunContext[AgentDependencies],
        title: str,
        subtitle: str | None = None,
        fade_in: int = 10,
        stay: int = 70,
        fade_out: int = 20,
        broadcast: bool = False,
    ) -> str:
        """
        发送标题消息

        Args:
            ctx: 运行上下文
            title: 主标题
            subtitle: 副标题
            fade_in: 淡入时间（ticks）
            stay: 停留时间（ticks）
            fade_out: 淡出时间（ticks）
            broadcast: 是否广播给全服；默认只发送给触发玩家

        Returns:
            发送结果
        """
        logger.info(
            "agent_tool_call",
            tool="send_title_message",
            connection_id=str(ctx.deps.connection_id),
            run_id=ctx.deps.run_id,
        )

        try:
            target = "@a" if broadcast else ctx.deps.player_name
            commands = build_title_commands(title, subtitle, fade_in, stay, fade_out, target)
            last: CommandResult | None = None
            for command in commands:
                last = await _run_command_result(ctx, command)
                if not last.is_success:
                    return ToolResult.from_command_result(
                        last,
                        failure_prefix="标题发送失败",
                    )
            return ToolResult.from_command_result(
                last or CommandResult.ok(),
                success_text="标题消息已发送",
                failure_prefix="标题发送失败",
            )
        except Exception as e:
            logger.error(
                "agent_tool_error",
                tool="send_title_message",
                error=str(e),
            )
            return _tool_failure(
                f"标题发送失败: {str(e)}",
                error_kind="INTERNAL",
                diagnostic_summary=str(e),
            )

    @chat_agent.tool
    async def send_actionbar_message(
        ctx: RunContext[AgentDependencies],
        message: str,
        broadcast: bool = False,
    ) -> str:
        """
        发送 Actionbar 消息

        Args:
            ctx: 运行上下文
            message: Actionbar 内容
            broadcast: 是否广播给全服；默认只发送给触发玩家

        Returns:
            发送结果
        """
        logger.info(
            "agent_tool_call",
            tool="send_actionbar_message",
            connection_id=str(ctx.deps.connection_id),
            run_id=ctx.deps.run_id,
        )

        try:
            target = "@a" if broadcast else ctx.deps.player_name
            command = build_actionbar_command(message, target)
            result = await _run_command_result(ctx, command)
            return ToolResult.from_command_result(
                result,
                success_text="Actionbar 消息已发送",
                failure_prefix="Actionbar 发送失败",
            )
        except Exception as e:
            logger.error(
                "agent_tool_error",
                tool="send_actionbar_message",
                error=str(e),
            )
            return _tool_failure(
                f"Actionbar 发送失败: {str(e)}",
                error_kind="INTERNAL",
                diagnostic_summary=str(e),
            )

    @chat_agent.tool
    async def send_script_event(
        ctx: RunContext[AgentDependencies],
        content: str,
        message_id: str = "server:data",
    ) -> str:
        """
        发送脚本事件

        Args:
            ctx: 运行上下文
            content: 事件内容
            message_id: 事件 ID，默认 server:data

        Returns:
            发送结果
        """
        logger.info(
            "agent_tool_call",
            tool="send_script_event",
            message_id=message_id,
            connection_id=str(ctx.deps.connection_id),
            run_id=ctx.deps.run_id,
        )

        try:
            command = MinecraftCommand.create_scriptevent(content, message_id).body.commandLine
            result = await _run_command_result(ctx, command)
            return ToolResult.from_command_result(
                result,
                success_text="脚本事件已发送",
                failure_prefix="脚本事件发送失败",
            )
        except ValueError as e:
            return _tool_failure(
                f"脚本事件发送失败: {str(e)}",
                error_kind="INVALID_ARGUMENT",
                diagnostic_summary=str(e),
            )
        except Exception as e:
            logger.error(
                "agent_tool_error",
                tool="send_script_event",
                error=str(e),
            )
            return _tool_failure(
                f"脚本事件发送失败: {str(e)}",
                error_kind="INTERNAL",
                diagnostic_summary=str(e),
            )

    @chat_agent.tool
    async def fetch_url_text(
        ctx: RunContext[AgentDependencies],
        url: str,
        max_chars: int = 2000,
    ) -> str:
        """
        拉取网页文本内容（仅支持 http/https）

        Args:
            ctx: 运行上下文
            url: 请求地址
            max_chars: 最大返回字符数

        Returns:
            文本内容
        """
        logger.info(
            "agent_tool_call",
            tool="fetch_url_text",
            url=url,
            connection_id=str(ctx.deps.connection_id),
            run_id=ctx.deps.run_id,
        )

        if not url.startswith(("http://", "https://")):
            return _tool_failure(
                "仅支持 http 或 https URL",
                error_kind="INVALID_ARGUMENT",
            )

        try:
            response = await ctx.deps.http_client.get(url)
            response.raise_for_status()
            text = response.text.strip()
            if len(text) > max_chars:
                return _tool_success(text[:max_chars] + "...")
            return _tool_success(text)
        except Exception as e:
            logger.error(
                "agent_tool_error",
                tool="fetch_url_text",
                error=str(e),
            )
            return _tool_failure(
                f"请求失败: {str(e)}",
                error_kind="TRANSIENT",
                retryable=True,
                diagnostic_summary=str(e),
            )

    @chat_agent.tool
    async def mcwiki_search(
        ctx: RunContext[AgentDependencies],
        query: str,
        limit: int = 5,
        namespaces: list[int] | None = None,
        use_cache: bool = True,
        pretty: bool = False,
    ) -> str:
        """
        搜索 Minecraft Wiki 内容，尽量不要使用多个搜索关键词。

        Args:
            ctx: 运行上下文
            query: 搜索关键词
            limit: 返回结果数量（1-50）
            namespaces: 命名空间列表
            use_cache: 是否使用缓存
            pretty: 是否格式化 JSON

        Returns:
            搜索结果摘要
        """
        logger.info(
            "agent_tool_call",
            tool="mcwiki_search",
            query=query,
            connection_id=str(ctx.deps.connection_id),
            run_id=ctx.deps.run_id,
        )

        tool_settings = cast(AgentToolSettings, ctx.deps.settings)
        base_url = tool_settings.mcwiki_base_url
        search_limit = normalize_limit(limit, default=5)
        params = build_search_params(query, search_limit, namespaces, use_cache, pretty)
        url = build_mcwiki_url(base_url, "api/search")

        try:
            response = await ctx.deps.http_client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        except Exception as e:
            logger.error(
                "agent_tool_error",
                tool="mcwiki_search",
                error=str(e),
            )
            return _tool_failure(
                f"搜索请求失败: {str(e)}",
                error_kind="TRANSIENT",
                retryable=True,
                diagnostic_summary=str(e),
            )

        if not payload.get("success"):
            error = payload.get("error", {})
            message = error.get("message", "搜索失败")
            return _tool_failure(
                f"搜索失败: {message}",
                error_kind="PERMANENT",
            )

        results = payload.get("data", {}).get("results", [])
        if not results:
            return _tool_success("未找到相关条目。")

        lines = ["搜索结果："]
        for index, item in enumerate(results, start=1):
            title = item.get("title", "未知标题")
            url_item = item.get("url", "")
            snippet = item.get("snippet", "")
            line = f"{index}. {title}"
            if url_item:
                line += f" - {url_item}"
            if snippet:
                line += f"\n   摘要: {snippet}"
            lines.append(line)
        return _tool_success("\n".join(lines))

    @chat_agent.tool
    async def mcwiki_get_page(
        ctx: RunContext[AgentDependencies],
        page_name: str,
        format: str = "markdown",
        use_cache: bool = True,
        include_metadata: bool = True,
        pretty: bool = False,
        max_chars: int = 2000,
    ) -> str:
        """
        获取 Minecraft Wiki 页面内容，优先使用wikitext格式

        Args:
            ctx: 运行上下文
            page_name: 页面名称
            format: 输出格式（html/markdown/both/wikitext）
            use_cache: 是否使用缓存
            include_metadata: 是否包含元数据
            pretty: 是否格式化 JSON
            max_chars: 最大返回字符数

        Returns:
            页面内容摘要
        """
        logger.info(
            "agent_tool_call",
            tool="mcwiki_get_page",
            page_name=page_name,
            connection_id=str(ctx.deps.connection_id),
            run_id=ctx.deps.run_id,
        )

        tool_settings = cast(AgentToolSettings, ctx.deps.settings)
        base_url = tool_settings.mcwiki_base_url
        url = build_page_url(base_url, page_name)
        params = {
            "format": format,
            "useCache": "true" if use_cache else "false",
            "includeMetadata": "true" if include_metadata else "false",
        }
        if pretty:
            params["pretty"] = "true"

        try:
            response = await ctx.deps.http_client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
        except Exception as e:
            logger.error(
                "agent_tool_error",
                tool="mcwiki_get_page",
                error=str(e),
            )
            return _tool_failure(
                f"页面请求失败: {str(e)}",
                error_kind="TRANSIENT",
                retryable=True,
                diagnostic_summary=str(e),
            )

        if not payload.get("success"):
            error = payload.get("error", {})
            message = error.get("message", "页面获取失败")
            return _tool_failure(
                f"页面获取失败: {message}",
                error_kind="PERMANENT",
            )

        page = payload.get("data", {}).get("page", {})
        title = page.get("title", page_name)
        url_item = page.get("url", "")
        content = page.get("content", {})
        text_content = ""

        if format == "wikitext":
            text_content = content.get("wikitext", "")
        elif format == "html":
            text_content = content.get("html", "")
        elif format == "markdown":
            text_content = content.get("markdown", "")
        else:
            text_content = content.get("markdown") or content.get("html") or ""

        if not text_content:
            return _tool_failure(
                f"页面 {title} 内容为空或解析失败。",
                error_kind="PERMANENT",
            )

        text_content = text_content.strip()
        if len(text_content) > max_chars:
            text_content = text_content[:max_chars] + "..."

        header = f"页面: {title}"
        if url_item:
            header += f" - {url_item}"
        return _tool_success(f"{header}\n{text_content}")

    @chat_agent.tool
    async def list_available_providers(
        ctx: RunContext[AgentDependencies],
    ) -> str:
        """
        获取可用的 LLM Provider 列表

        Args:
            ctx: 运行上下文

        Returns:
            Provider 列表描述
        """
        providers = cast(AgentToolSettings, ctx.deps.settings).list_available_providers()
        return _tool_success("可用 Provider: " + ", ".join(providers))

    @chat_agent.tool
    async def get_player_snapshot(
        ctx: RunContext[AgentDependencies],
        target: str = "@a",
    ) -> str:
        """通过 addon 桥接获取玩家快照。"""
        if ctx.deps.addon_bridge is None:
            return _tool_failure("Addon 桥接不可用", error_kind="TRANSIENT", retryable=True)

        try:
            result = await ctx.deps.addon_bridge.request(
                "get_player_snapshot",
                {"target": target},
            )
            return _tool_success(json.dumps(result.get("payload", result), ensure_ascii=False))
        except Exception as e:
            logger.error("agent_tool_error", tool="get_player_snapshot", error=str(e))
            return _tool_failure(
                f"获取玩家快照失败: {str(e)}",
                error_kind="TRANSIENT",
                retryable=True,
                diagnostic_summary=str(e),
            )

    @chat_agent.tool
    async def get_inventory_snapshot(
        ctx: RunContext[AgentDependencies],
        target: str = "@a",
    ) -> str:
        """通过 addon 桥接获取背包快照。"""
        if ctx.deps.addon_bridge is None:
            return _tool_failure("Addon 桥接不可用", error_kind="TRANSIENT", retryable=True)

        try:
            result = await ctx.deps.addon_bridge.request(
                "get_inventory_snapshot",
                {"target": target},
            )
            return _tool_success(json.dumps(result.get("payload", result), ensure_ascii=False))
        except Exception as e:
            logger.error("agent_tool_error", tool="get_inventory_snapshot", error=str(e))
            return _tool_failure(
                f"获取背包快照失败: {str(e)}",
                error_kind="TRANSIENT",
                retryable=True,
                diagnostic_summary=str(e),
            )

    @chat_agent.tool
    async def find_entities(
        ctx: RunContext[AgentDependencies],
        entity_type: str,
        radius: int = 32,
        target: str = "@s",
    ) -> str:
        """通过 addon 桥接查询实体快照。"""
        if ctx.deps.addon_bridge is None:
            return _tool_failure("Addon 桥接不可用", error_kind="TRANSIENT", retryable=True)

        try:
            result = await ctx.deps.addon_bridge.request(
                "find_entities",
                {
                    "entity_type": entity_type,
                    "radius": radius,
                    "target": target,
                },
            )
            return _tool_success(json.dumps(result.get("payload", result), ensure_ascii=False))
        except Exception as e:
            logger.error("agent_tool_error", tool="find_entities", error=str(e))
            return _tool_failure(
                f"查询实体失败: {str(e)}",
                error_kind="TRANSIENT",
                retryable=True,
                diagnostic_summary=str(e),
            )

    @chat_agent.tool
    async def run_world_command(
        ctx: RunContext[AgentDependencies],
        command: str,
    ) -> str:
        """通过 addon 桥接受控执行世界命令。(仅当run_minecraft_command工具无法使用才用)"""
        if ctx.deps.addon_bridge is None:
            return _tool_failure("Addon 桥接不可用", error_kind="TRANSIENT", retryable=True)

        try:
            result = await ctx.deps.addon_bridge.request(
                "run_world_command",
                {"command": command},
            )
            return _tool_success(json.dumps(result.get("payload", result), ensure_ascii=False))
        except Exception as e:
            logger.error("agent_tool_error", tool="run_world_command", error=str(e))
            return _tool_failure(
                f"执行世界命令失败: {str(e)}",
                error_kind="TRANSIENT",
                retryable=True,
                diagnostic_summary=str(e),
            )

    if _runtime_harness_schema_enabled(settings):
        _enhance_registered_tool_descriptions(chat_agent)
    # 策略/幂等/审批/审计统一由 harness.execution.HarnessToolset 入口处理。
    # 禁止在生产路径修改已注册函数对象（不调用 wrap_registered_tools）。
    _stringify_tool_results(chat_agent)


def escape_command_text(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("\"", "\\\"")
        .replace("\n", "\\n")
    )


def build_title_commands(
    title: str,
    subtitle: str | None,
    fade_in: int,
    stay: int,
    fade_out: int,
    target: str,
) -> list[str]:
    safe_target = sanitize_tellraw_target(target)
    commands = [f'title {safe_target} title "{escape_command_text(title)}"']
    if subtitle:
        commands.append(f'title {safe_target} subtitle "{escape_command_text(subtitle)}"')
    commands.append(f"title {safe_target} times {fade_in} {stay} {fade_out}")
    return commands


def build_actionbar_command(message: str, target: str) -> str:
    safe_target = sanitize_tellraw_target(target)
    return f'title {safe_target} actionbar "{escape_command_text(message)}"'


def build_tellraw_command(message: str, color: str, target: str) -> str:
    return MinecraftCommand.create_tellraw(message, color=color, target=target).body.commandLine
