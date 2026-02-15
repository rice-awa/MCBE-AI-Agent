"""MCP 客户端 - 使用官方 Model Context Protocol Python SDK"""

from contextlib import AsyncExitStack
import os
from typing import Any

import mcp.types as types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from config.logging import get_logger
from config.settings import MCPServerConfig, Settings, get_settings

logger = get_logger(__name__)


class MCPClient:
    """MCP 协议客户端 - 使用官方 SDK"""

    def __init__(self, config: MCPServerConfig):
        self._config = config
        self._session: ClientSession | None = None
        self._read: Any = None
        self._write: Any = None
        self._exit_stack: AsyncExitStack | None = None

    async def start(self) -> bool:
        """启动 MCP 服务器连接

        Returns:
            是否启动成功
        """
        try:
            if self._session:
                return True

            self._exit_stack = AsyncExitStack()

            if self._config.url:
                # 远程 MCP 服务器 - 使用 streamable_http
                # 注意: 需要 mcp.client.streamable_http
                from mcp.client.streamable_http import streamable_http_client

                self._read, self._write = await self._exit_stack.enter_async_context(
                    streamable_http_client(self._config.url)
                )
                self._session = ClientSession(self._read, self._write)
                await self._session.initialize()
                logger.info("mcp_client_started", server=self._config.name, mode="http")
            else:
                # 本地 MCP 服务器 - 使用 stdio
                server_params = StdioServerParameters(
                    command=self._config.command,
                    args=self._config.args,
                    env={**os.environ, **self._config.env},
                )
                self._read, self._write = await self._exit_stack.enter_async_context(
                    stdio_client(server_params)
                )
                self._session = ClientSession(self._read, self._write)
                await self._session.initialize()
                logger.info(
                    "mcp_client_started",
                    server=self._config.name,
                    mode="stdio",
                )
            return True
        except Exception as e:
            await self.stop()
            logger.error(
                "mcp_client_start_failed",
                server=self._config.name,
                error=str(e),
            )
            return False

    async def stop(self) -> None:
        """停止 MCP 服务器连接"""
        if self._session:
            await self._session.close()
            self._session = None

        if self._exit_stack:
            await self._exit_stack.aclose()
            self._exit_stack = None

        self._read = None
        self._write = None
        logger.info("mcp_client_stopped", server=self._config.name)

    async def list_tools(self) -> list[types.Tool]:
        """获取可用工具列表

        Returns:
            工具列表
        """
        if not self._session:
            raise RuntimeError(f"MCP client not started: {self._config.name}")

        result = await self._session.list_tools()
        return result.tools

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> types.CallToolResult:
        """调用 MCP 工具

        Args:
            name: 工具名称
            arguments: 工具参数

        Returns:
            工具调用结果
        """
        if not self._session:
            raise RuntimeError(f"MCP client not started: {self._config.name}")

        result = await self._session.call_tool(name, arguments)
        return result

    async def list_prompts(self) -> list[types.Prompt]:
        """获取可用提示模板

        Returns:
            提示模板列表
        """
        if not self._session:
            raise RuntimeError(f"MCP client not started: {self._config.name}")

        result = await self._session.list_prompts()
        return result.prompts

    async def list_resources(self) -> list[types.Resource]:
        """获取可用资源

        Returns:
            资源列表
        """
        if not self._session:
            raise RuntimeError(f"MCP client not started: {self._config.name}")

        result = await self._session.list_resources()
        return result.resources


class MCPToolAdapter:
    """MCP 工具适配器 - 转换为 PydanticAI 工具"""

    def __init__(self, client: MCPClient):
        self._client = client
        self._tools: list[types.Tool] = []

    async def load_tools(self) -> list[types.Tool]:
        """加载 MCP 工具

        Returns:
            工具列表
        """
        self._tools = await self._client.list_tools()
        logger.info(
            "mcp_tools_loaded",
            server=self._client._config.name,
            count=len(self._tools),
        )
        return self._tools

    def get_tools(self) -> list[types.Tool]:
        """获取已加载的工具列表"""
        return self._tools


class MCPManager:
    """MCP 管理器 - 管理多个 MCP 服务器连接"""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._clients: dict[str, MCPClient] = {}
        self._adapters: dict[str, MCPToolAdapter] = {}
        self._initialized = False

    async def initialize(self) -> bool:
        """初始化 MCP 服务器连接

        Returns:
            是否初始化成功
        """
        if self._initialized:
            return True

        mcp_config = self._settings.mcp
        if not mcp_config.enabled:
            logger.info("mcp_disabled")
            return True

        success = True
        for server_config in mcp_config.servers:
            client = MCPClient(server_config)
            adapter = MCPToolAdapter(client)

            if server_config.auto_start:
                started = await client.start()
                if started:
                    # 加载工具
                    try:
                        await adapter.load_tools()
                    except Exception as e:
                        logger.warning(
                            "mcp_tool_load_failed",
                            server=server_config.name,
                            error=str(e),
                        )
                else:
                    success = False

            self._clients[server_config.name] = client
            self._adapters[server_config.name] = adapter

        self._initialized = True
        logger.info(
            "mcp_initialized",
            server_count=len(self._clients),
            success=success,
        )
        return success

    async def shutdown(self) -> None:
        """关闭所有 MCP 服务器连接"""
        for client in self._clients.values():
            await client.stop()
        self._clients.clear()
        self._adapters.clear()
        self._initialized = False
        logger.info("mcp_shutdown")

    def get_adapter(self, server_name: str) -> MCPToolAdapter | None:
        """获取指定服务器的适配器"""
        return self._adapters.get(server_name)

    def get_all_adapters(self) -> dict[str, MCPToolAdapter]:
        """获取所有服务器适配器"""
        return self._adapters.copy()

    def get_all_tools(self) -> list[types.Tool]:
        """获取所有 MCP 服务器的工具"""
        tools = []
        for adapter in self._adapters.values():
            tools.extend(adapter.get_tools())
        return tools


# 全局单例
_mcp_manager: MCPManager | None = None


def get_mcp_manager() -> MCPManager:
    """获取 MCP 管理器单例"""
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = MCPManager()
    return _mcp_manager
