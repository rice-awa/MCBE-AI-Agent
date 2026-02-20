"""PydanticAI Agent 工具定义"""

from __future__ import annotations

from typing import Any

from pydantic_ai import Agent, RunContext

from config.logging import get_logger
from models.agent import AgentDependencies
from models.minecraft import MinecraftCommand
from services.agent.mcwiki import (
    build_mcwiki_url,
    build_page_url,
    build_search_params,
    normalize_limit,
)

logger = get_logger(__name__)


def register_agent_tools(chat_agent: Agent[AgentDependencies, str]) -> None:
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
        )

        try:
            result = await ctx.deps.run_command(command)
            # 简化返回：仅返回游戏原始响应，避免重复显示命令
            # 工具调用名称和参数已通过 tool_call 事件显示
            return result if result else "命令执行成功"
        except Exception as e:
            logger.error(
                "agent_tool_error",
                tool="run_minecraft_command",
                error=str(e),
            )
            return f"命令执行失败: {str(e)}"

    @chat_agent.tool
    async def send_game_message(
        ctx: RunContext[AgentDependencies],
        message: str,
    ) -> str:
        """
        向游戏发送消息

        Args:
            ctx: 运行上下文
            message: 要发送的消息

        Returns:
            发送结果
        """
        logger.info(
            "agent_tool_call",
            tool="send_game_message",
            connection_id=str(ctx.deps.connection_id),
        )

        try:
            await ctx.deps.send_to_game(message)
            return "消息已发送到游戏"
        except Exception as e:
            logger.error(
                "agent_tool_error",
                tool="send_game_message",
                error=str(e),
            )
            return f"消息发送失败: {str(e)}"

    @chat_agent.tool
    async def send_colored_message(
        ctx: RunContext[AgentDependencies],
        message: str,
        color: str = "§a",
    ) -> str:
        """
        发送带颜色的聊天消息

        Args:
            ctx: 运行上下文
            message: 消息内容
            color: Minecraft 颜色代码，例如 "§a"

        Returns:
            发送结果
        """
        logger.info(
            "agent_tool_call",
            tool="send_colored_message",
            color=color,
            connection_id=str(ctx.deps.connection_id),
        )

        try:
            command = build_tellraw_command(message, color)
            await ctx.deps.run_command(command)
            return "彩色消息已发送"
        except Exception as e:
            logger.error(
                "agent_tool_error",
                tool="send_colored_message",
                error=str(e),
            )
            return f"彩色消息发送失败: {str(e)}"

    @chat_agent.tool
    async def send_title_message(
        ctx: RunContext[AgentDependencies],
        title: str,
        subtitle: str | None = None,
        fade_in: int = 10,
        stay: int = 70,
        fade_out: int = 20,
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

        Returns:
            发送结果
        """
        logger.info(
            "agent_tool_call",
            tool="send_title_message",
            connection_id=str(ctx.deps.connection_id),
        )

        try:
            commands = build_title_commands(title, subtitle, fade_in, stay, fade_out)
            for command in commands:
                await ctx.deps.run_command(command)
            return "标题消息已发送"
        except Exception as e:
            logger.error(
                "agent_tool_error",
                tool="send_title_message",
                error=str(e),
            )
            return f"标题发送失败: {str(e)}"

    @chat_agent.tool
    async def send_actionbar_message(
        ctx: RunContext[AgentDependencies],
        message: str,
    ) -> str:
        """
        发送 Actionbar 消息

        Args:
            ctx: 运行上下文
            message: Actionbar 内容

        Returns:
            发送结果
        """
        logger.info(
            "agent_tool_call",
            tool="send_actionbar_message",
            connection_id=str(ctx.deps.connection_id),
        )

        try:
            command = build_actionbar_command(message)
            await ctx.deps.run_command(command)
            return "Actionbar 消息已发送"
        except Exception as e:
            logger.error(
                "agent_tool_error",
                tool="send_actionbar_message",
                error=str(e),
            )
            return f"Actionbar 发送失败: {str(e)}"

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
        )

        try:
            command = MinecraftCommand.create_scriptevent(content, message_id).body.commandLine
            await ctx.deps.run_command(command)
            return "脚本事件已发送"
        except Exception as e:
            logger.error(
                "agent_tool_error",
                tool="send_script_event",
                error=str(e),
            )
            return f"脚本事件发送失败: {str(e)}"

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
        )

        if not url.startswith(("http://", "https://")):
            return "仅支持 http 或 https URL"

        try:
            response = await ctx.deps.http_client.get(url)
            response.raise_for_status()
            text = response.text.strip()
            if len(text) > max_chars:
                return text[:max_chars] + "..."
            return text
        except Exception as e:
            logger.error(
                "agent_tool_error",
                tool="fetch_url_text",
                error=str(e),
            )
            return f"请求失败: {str(e)}"

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
        搜索 Minecraft Wiki 内容

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
        )

        base_url = ctx.deps.settings.mcwiki_base_url
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
            return f"搜索请求失败: {str(e)}"

        if not payload.get("success"):
            error = payload.get("error", {})
            message = error.get("message", "搜索失败")
            return f"搜索失败: {message}"

        results = payload.get("data", {}).get("results", [])
        if not results:
            return "未找到相关条目。"

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
        return "\n".join(lines)

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
        获取 Minecraft Wiki 页面内容

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
        )

        base_url = ctx.deps.settings.mcwiki_base_url
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
            return f"页面请求失败: {str(e)}"

        if not payload.get("success"):
            error = payload.get("error", {})
            message = error.get("message", "页面获取失败")
            return f"页面获取失败: {message}"

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
            return f"页面 {title} 内容为空或解析失败。"

        text_content = text_content.strip()
        if len(text_content) > max_chars:
            text_content = text_content[:max_chars] + "..."

        header = f"页面: {title}"
        if url_item:
            header += f" - {url_item}"
        return f"{header}\n{text_content}"

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
        providers = ctx.deps.settings.list_available_providers()
        return "可用 Provider: " + ", ".join(providers)


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
) -> list[str]:
    commands = [f'title @a title "{escape_command_text(title)}"']
    if subtitle:
        commands.append(f'title @a subtitle "{escape_command_text(subtitle)}"')
    commands.append(f"title @a times {fade_in} {stay} {fade_out}")
    return commands


def build_actionbar_command(message: str) -> str:
    return f'title @a actionbar "{escape_command_text(message)}"'


def build_tellraw_command(message: str, color: str) -> str:
    return MinecraftCommand.create_tellraw(message, color=color).body.commandLine
