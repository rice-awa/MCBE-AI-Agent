"""PydanticAI Agent 工具定义"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import quote

from pydantic_ai import Agent, RunContext
from pydantic import BaseModel, Field

from config.logging import get_logger
from models.agent import AgentDependencies
from models.minecraft import MinecraftCommand

logger = get_logger(__name__)


def register_agent_tools(chat_agent: Agent[AgentDependencies, str]) -> None:
    """注册 Agent 工具到指定的 Agent 实例"""

    class McWikiSearchParams(BaseModel):
        """MCWiki 搜索参数"""

        query: str = Field(..., description="搜索关键词")
        limit: int = Field(default=5, ge=1, le=50, description="结果数量限制")
        namespaces: str | None = Field(default=None, description="命名空间，逗号分隔")
        pretty: bool = Field(default=False, description="是否返回格式化 JSON")

    class McWikiPageParams(BaseModel):
        """MCWiki 页面参数"""

        page_name: str = Field(..., description="页面名称")
        format: Literal["html", "markdown", "both", "wikitext"] = Field(
            default="markdown",
            description="内容格式",
        )
        use_cache: bool = Field(default=True, description="是否使用缓存")
        include_metadata: bool = Field(default=False, description="是否包含元数据")
        max_chars: int = Field(default=2000, ge=200, le=8000, description="最大返回字符数")

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
            await ctx.deps.run_command(command)
            return f"已执行命令: /{command}"
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

    @chat_agent.tool
    async def mcwiki_search(
        ctx: RunContext[AgentDependencies],
        params: McWikiSearchParams,
    ) -> str:
        """
        搜索 MCWiki 内容

        Args:
            ctx: 运行上下文
            params: 搜索参数

        Returns:
            搜索结果摘要
        """
        logger.info(
            "agent_tool_call",
            tool="mcwiki_search",
            query=params.query,
            connection_id=str(ctx.deps.connection_id),
        )

        base_url = ctx.deps.settings.mcwiki_base_url.rstrip("/")
        try:
            response = await ctx.deps.http_client.get(
                f"{base_url}/api/search",
                params={
                    "q": params.query,
                    "limit": params.limit,
                    "namespaces": params.namespaces,
                    "pretty": "true" if params.pretty else "false",
                },
            )
            response.raise_for_status()
            payload = response.json()
            if not payload.get("success"):
                return f"搜索失败: {payload.get('error', {}).get('message', '未知错误')}"

            data = payload.get("data", {})
            results = data.get("results", [])
            if not results:
                return f"未找到与“{params.query}”相关的页面。"

            lines = [f"搜索“{params.query}”结果（共 {data.get('pagination', {}).get('totalHits', 0)} 条）:"]
            for item in results:
                title = item.get("title", "未知标题")
                url = item.get("url", "")
                snippet = (item.get("snippet") or "").strip()
                if snippet:
                    lines.append(f"- {title}：{snippet} {url}")
                else:
                    lines.append(f"- {title}：{url}")
            return "\n".join(lines)
        except Exception as e:
            logger.error(
                "agent_tool_error",
                tool="mcwiki_search",
                error=str(e),
            )
            return f"搜索请求失败: {str(e)}"

    @chat_agent.tool
    async def mcwiki_get_page(
        ctx: RunContext[AgentDependencies],
        params: McWikiPageParams,
    ) -> str:
        """
        获取 MCWiki 页面内容

        Args:
            ctx: 运行上下文
            params: 页面参数

        Returns:
            页面内容摘要
        """
        logger.info(
            "agent_tool_call",
            tool="mcwiki_get_page",
            page_name=params.page_name,
            format=params.format,
            connection_id=str(ctx.deps.connection_id),
        )

        base_url = ctx.deps.settings.mcwiki_base_url.rstrip("/")
        page_name = quote(params.page_name)
        try:
            response = await ctx.deps.http_client.get(
                f"{base_url}/api/page/{page_name}",
                params={
                    "format": params.format,
                    "useCache": "true" if params.use_cache else "false",
                    "includeMetadata": "true" if params.include_metadata else "false",
                },
            )
            response.raise_for_status()
            payload = response.json()
            if not payload.get("success"):
                return f"页面获取失败: {payload.get('error', {}).get('message', '未知错误')}"

            page = payload.get("data", {}).get("page", {})
            title = page.get("title") or page.get("pageName") or params.page_name
            content = page.get("content", {})
            text = ""
            if params.format == "wikitext":
                text = content.get("wikitext", "")
            elif params.format == "html":
                text = content.get("html", "")
            elif params.format == "both":
                text = content.get("markdown") or content.get("html") or ""
            else:
                text = content.get("markdown", "")

            text = text.strip()
            if not text:
                return f"页面“{title}”没有返回可用内容。"

            if len(text) > params.max_chars:
                text = text[: params.max_chars].rstrip() + "..."
            return f"《{title}》内容摘录：\n{text}"
        except Exception as e:
            logger.error(
                "agent_tool_error",
                tool="mcwiki_get_page",
                error=str(e),
            )
            return f"页面请求失败: {str(e)}"


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
