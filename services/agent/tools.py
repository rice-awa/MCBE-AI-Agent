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
    build_health_url,
    build_mcwiki_url,
    build_namespaces_url,
    build_page_exists_url,
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
    async def mcwiki_search(
        ctx: RunContext[AgentDependencies],
        query: str,
        limit: int = 10,
        namespaces: list[int] | None = None,
        use_cache: bool = True,
        pretty: bool = False,
    ) -> str:
        """搜索 Minecraft 中文 Wiki。

        用 1-3 个游戏名词查找匹配页面。多关键词会缩小范围（所有词须同时出现），
        如需多角度搜索应分批调用，不要一次堆砌大量关键词。

        Args:
            ctx: 运行上下文
            query: 搜索关键词，1-3 个游戏名词为佳。例：'信标 激活'，而不是
                '信标 激活 怎么做 教程 步骤'。这不是搜索引擎，不要堆砌关键词。
            limit: 返回结果数量，默认 10，最大 50
            namespaces: 限定命名空间数字 ID 列表。0=Main 10=Template 14=Category
                9994=Module。空则使用默认值。可用 mcwiki_list_namespaces 查询完整映射。
            use_cache: 是否使用缓存，默认 true
            pretty: 是否格式化 JSON，默认 false

        Returns:
            搜索结果摘要（标题、URL、片段）
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
        search_limit = normalize_limit(limit, default=10)
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
                f"搜索失败: API 服务不可用 ({e})",
                error_kind="TRANSIENT",
                retryable=True,
                diagnostic_summary=str(e),
            )

        if not payload.get("success"):
            error = payload.get("error", {})
            if isinstance(error, dict):
                message = error.get("message") or error.get("code") or "未知错误"
            else:
                message = error or "未知错误"
            return _tool_failure(
                f"搜索失败: {message}",
                error_kind="PERMANENT",
            )

        results = payload.get("data", {}).get("results", [])
        if not results:
            return _tool_success(f"未找到与 '{query}' 相关的页面。")

        lines = [f"搜索 '{query}' 的结果 ({len(results)} 条):\n"]
        for item in results:
            title = item.get("title", "未知标题")
            namespace = item.get("namespace", "")
            url_item = item.get("url", "")
            snippet = str(item.get("snippet", "")).replace("\n", " ")[:150]
            ns_part = f" ({namespace})" if namespace else ""
            lines.append(f"- **{title}**{ns_part}")
            if url_item:
                lines.append(f"  {url_item}")
            if snippet:
                lines.append(f"  {snippet}")
            lines.append("")
        return _tool_success("\n".join(lines))

    @chat_agent.tool
    async def mcwiki_get_page(
        ctx: RunContext[AgentDependencies],
        page_name: str,
        format: str = "wikitext",
        use_cache: bool = True,
        include_metadata: bool = True,
        pretty: bool = False,
        max_chars: int = 2000,
    ) -> str:
        """获取 Minecraft 中文 Wiki 页面的内容。

        根据页面名称获取完整内容。

        格式选择：
        - wikitext（默认）：Wiki 原始标记，{{Template}} 完整保留，信息最全。
          **非必要不要改 format，默认 wikitext 就是最好的。**
        - html：清洗后的正文 HTML。仅当 wikitext 中某个模板确实无法理解时才使用。
        - markdown / both：仅在调用方显式需要时传入。

        wikitext 中遇到不认识的 {{Template}} 时，可用 mcwiki_search 传
        namespaces=[10] 去模板命名空间搜索该模板文档。

        Args:
            ctx: 运行上下文
            page_name: 页面名称，支持中文。例如 '钻石'、'工作台'、'命令'
            format: 输出格式。默认 wikitext（优先，无需声明）；html 仅在 wikitext
                模板确实无法理解时使用；markdown/both 需显式声明
            use_cache: 是否使用缓存，默认 true
            include_metadata: 是否包含元数据，默认 true
            pretty: 是否格式化 JSON，默认 false
            max_chars: 最大返回字符数，默认 2000

        Returns:
            页面内容摘要
        """
        logger.info(
            "agent_tool_call",
            tool="mcwiki_get_page",
            page_name=page_name,
            format=format,
            connection_id=str(ctx.deps.connection_id),
            run_id=ctx.deps.run_id,
        )

        tool_settings = cast(AgentToolSettings, ctx.deps.settings)
        base_url = tool_settings.mcwiki_base_url
        url = build_page_url(base_url, page_name)
        # 未识别的 format 回退到 wikitext，避免默认/脏值走到易崩的 HTML 解析路径
        requested_format = (format or "wikitext").strip().lower()
        if requested_format not in {"wikitext", "html", "markdown", "both"}:
            requested_format = "wikitext"
        params = {
            "format": requested_format,
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
                f"页面获取失败: API 服务不可用 ({e})",
                error_kind="TRANSIENT",
                retryable=True,
                diagnostic_summary=str(e),
            )

        if not payload.get("success"):
            error = payload.get("error", {})
            code = error.get("code", "") if isinstance(error, dict) else ""
            if code == "PAGE_NOT_FOUND":
                return _tool_failure(
                    f"页面 '{page_name}' 不存在。可尝试使用 mcwiki_search 搜索正确的页面名称。",
                    error_kind="PERMANENT",
                )
            if isinstance(error, dict):
                message = error.get("message") or code or "未知错误"
            else:
                message = error or "未知错误"
            return _tool_failure(
                f"获取页面失败: {message}",
                error_kind="PERMANENT",
            )

        page = payload.get("data", {}).get("page", {})
        title = page.get("title") or page.get("pageName") or page_name
        url_item = page.get("url", "")
        content = page.get("content", {})
        text_content = ""

        if requested_format == "wikitext":
            text_content = content.get("wikitext", "")
        elif requested_format == "html":
            text_content = content.get("html", "")
        elif requested_format == "markdown":
            text_content = content.get("markdown", "")
        else:
            text_content = (
                content.get("markdown")
                or content.get("html")
                or content.get("wikitext")
                or ""
            )

        if not text_content:
            return _tool_failure(
                f"页面 {title} 内容为空或解析失败。",
                error_kind="PERMANENT",
            )

        text_content = text_content.strip()
        if len(text_content) > max_chars:
            text_content = text_content[:max_chars] + "..."

        info_lines = [f"# {title}"]
        if url_item:
            info_lines.append(f"URL: {url_item}")

        if include_metadata:
            meta = page.get("meta", {})
            info_lines.append(
                f"格式: {requested_format}  |  字数: {meta.get('wordCount', 'N/A')}  "
                f"|  段落: {meta.get('sectionCount', 'N/A')}"
            )
        else:
            info_lines.append(f"格式: {requested_format}")

        info_lines.extend(["", text_content])
        return _tool_success("\n".join(info_lines))

    @chat_agent.tool
    async def mcwiki_check_page_exists(
        ctx: RunContext[AgentDependencies],
        page_name: str,
    ) -> str:
        """检查 Minecraft 中文 Wiki 页面是否存在。

        在不确定页面名是否准确、又不想拉取全文时使用。
        若不存在，可改用 mcwiki_search 搜索正确名称。

        Args:
            ctx: 运行上下文
            page_name: 页面名称，支持中文

        Returns:
            存在性结果；存在时可能包含页面 ID、长度、最后修改、重定向信息
        """
        logger.info(
            "agent_tool_call",
            tool="mcwiki_check_page_exists",
            page_name=page_name,
            connection_id=str(ctx.deps.connection_id),
            run_id=ctx.deps.run_id,
        )

        tool_settings = cast(AgentToolSettings, ctx.deps.settings)
        url = build_page_exists_url(tool_settings.mcwiki_base_url, page_name)

        try:
            response = await ctx.deps.http_client.get(url)
            response.raise_for_status()
            payload = response.json()
        except Exception as e:
            logger.error(
                "agent_tool_error",
                tool="mcwiki_check_page_exists",
                error=str(e),
            )
            return _tool_failure(
                f"检查失败: API 服务不可用 ({e})",
                error_kind="TRANSIENT",
                retryable=True,
                diagnostic_summary=str(e),
            )

        if not payload.get("success"):
            error = payload.get("error", {})
            if isinstance(error, dict):
                message = error.get("message") or error.get("code") or "未知错误"
            else:
                message = error or "未知错误"
            return _tool_failure(
                f"检查失败: {message}",
                error_kind="PERMANENT",
            )

        info = payload.get("data", {})
        if info.get("exists"):
            page_info = info.get("pageInfo", {}) or {}
            lines = [f"页面 '{page_name}' 存在。"]
            if page_info:
                if page_info.get("pageid") is not None:
                    lines.append(f"页面ID: {page_info.get('pageid')}")
                if page_info.get("length") is not None:
                    lines.append(f"长度: {page_info.get('length')} 字节")
                if page_info.get("touched"):
                    lines.append(f"最后修改: {page_info.get('touched')}")
            if info.get("redirected"):
                actual_title = page_info.get("title", page_name)
                lines.append(f"重定向到: {actual_title}")
            return _tool_success("\n".join(lines))
        return _tool_success(f"页面 '{page_name}' 不存在。")

    @chat_agent.tool
    async def mcwiki_check_health(
        ctx: RunContext[AgentDependencies],
    ) -> str:
        """检查 Minecraft Wiki API 服务健康状态。

        在 Wiki 工具连续失败、怀疑上游不可用时使用。

        Args:
            ctx: 运行上下文

        Returns:
            服务状态、运行时间、环境与内存摘要
        """
        logger.info(
            "agent_tool_call",
            tool="mcwiki_check_health",
            connection_id=str(ctx.deps.connection_id),
            run_id=ctx.deps.run_id,
        )

        tool_settings = cast(AgentToolSettings, ctx.deps.settings)
        url = build_health_url(tool_settings.mcwiki_base_url)

        try:
            response = await ctx.deps.http_client.get(url)
            response.raise_for_status()
            payload = response.json()
        except Exception as e:
            logger.error(
                "agent_tool_error",
                tool="mcwiki_check_health",
                error=str(e),
            )
            return _tool_failure(
                f"API 服务不可用: {e}",
                error_kind="TRANSIENT",
                retryable=True,
                diagnostic_summary=str(e),
            )

        if not payload.get("status"):
            return _tool_failure(
                f"API 返回异常: {payload}",
                error_kind="TRANSIENT",
                retryable=True,
            )

        mem = payload.get("memory", {}) or {}
        svc = payload.get("service", {}) or {}
        uptime = payload.get("uptime", {}) or {}
        lines = [
            f"状态: {payload.get('status', 'unknown')}",
            f"运行时间: {uptime.get('human', 'N/A')}",
            f"环境: {svc.get('environment', 'N/A')}",
            (
                f"内存: {mem.get('used', '?')} / {mem.get('total', '?')} "
                f"(系统 {mem.get('system', '?')})"
            ),
        ]
        return _tool_success("\n".join(lines))

    @chat_agent.tool
    async def mcwiki_list_namespaces(
        ctx: RunContext[AgentDependencies],
    ) -> str:
        """获取 Minecraft Wiki 的命名空间映射表。

        返回所有可用命名空间的数字 ID 与名称。
        结合 mcwiki_search 的 namespaces 参数使用，例如 namespaces=[10] 搜索模板文档。

        Args:
            ctx: 运行上下文

        Returns:
            命名空间 ID → 名称映射
        """
        logger.info(
            "agent_tool_call",
            tool="mcwiki_list_namespaces",
            connection_id=str(ctx.deps.connection_id),
            run_id=ctx.deps.run_id,
        )

        tool_settings = cast(AgentToolSettings, ctx.deps.settings)
        url = build_namespaces_url(tool_settings.mcwiki_base_url)

        try:
            response = await ctx.deps.http_client.get(url)
            response.raise_for_status()
            payload = response.json()
        except Exception as e:
            logger.error(
                "agent_tool_error",
                tool="mcwiki_list_namespaces",
                error=str(e),
            )
            return _tool_failure(
                f"获取命名空间失败: {e}",
                error_kind="TRANSIENT",
                retryable=True,
                diagnostic_summary=str(e),
            )

        if not payload.get("success"):
            error = payload.get("error", {})
            if isinstance(error, dict):
                message = error.get("message") or error.get("code") or "未知错误"
            else:
                message = error or "未知错误"
            return _tool_failure(
                f"获取命名空间失败: {message}",
                error_kind="PERMANENT",
            )

        namespaces = payload.get("data", {}).get("namespaces", {}) or {}
        lines = ["命名空间映射表：\n"]
        for key, value in namespaces.items():
            lines.append(f"  {key} → {value}")
        return _tool_success("\n".join(lines))

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
    async def get_look_block(
        ctx: RunContext[AgentDependencies],
        target: str = "",
        max_distance: int = 8,
        include_liquid_blocks: bool = False,
        include_passable_blocks: bool = False,
    ) -> str:
        """
        获取玩家视线正对着的方块（射线检测 getBlockFromViewDirection）。

        用于回答「我在看什么 / 前面是什么方块」。默认查询当前对话玩家；
        不要传 @s/@a 等选择器（会自动回退到当前玩家）。

        Args:
            ctx: 运行上下文
            target: 玩家名；留空则使用当前对话玩家。不要使用选择器。
            max_distance: 最大检测距离（格），默认 8，有效范围 1–64
            include_liquid_blocks: 是否把液体（水、岩浆）当作挡射线方块；默认 False
            include_passable_blocks: 是否把可穿过方块（花、藤蔓等）当作命中；默认 False

        Returns:
            JSON 字符串：命中时含 typeId、坐标、维度、命中面 face、faceLocation；
            未命中时 hit=false；玩家不在线时返回错误信息
        """
        if ctx.deps.addon_bridge is None:
            return _tool_failure("Addon 桥接不可用", error_kind="TRANSIENT", retryable=True)

        resolved_target = (target or "").strip()
        if not resolved_target or resolved_target.startswith("@"):
            # 选择器（@s/@a 等）在 Script API getPlayers({name}) 中无效，回退到对话玩家。
            resolved_target = ctx.deps.player_name

        try:
            result = await ctx.deps.addon_bridge.request(
                "get_look_block",
                {
                    "target": resolved_target,
                    "max_distance": max_distance,
                    "include_liquid_blocks": include_liquid_blocks,
                    "include_passable_blocks": include_passable_blocks,
                },
            )
            return _tool_success(json.dumps(result.get("payload", result), ensure_ascii=False))
        except Exception as e:
            logger.error("agent_tool_error", tool="get_look_block", error=str(e))
            return _tool_failure(
                f"获取视线方块失败: {str(e)}",
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
