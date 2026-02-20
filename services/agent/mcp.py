"""MCP 工具集管理 - 使用 PydanticAI 官方 MCP 客户端"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic_ai.mcp import MCPServerStdio, MCPServerStreamableHTTP

from config.logging import get_logger
from config.settings import Settings, MCPServerConfig, get_settings

logger = get_logger(__name__)


class MCPConnectionStatus(str, Enum):
    """MCP 连接状态

    注意：Pydantic AI 的 MCP 连接是懒加载的，实际连接发生在 agent.run() 时。
    这里的状态主要用于跟踪配置是否有效和最近一次运行的结果。
    """

    PENDING = "pending"  # 待连接（配置有效，尚未在运行中使用）
    ACTIVE = "active"  # 活跃（最近一次运行成功）
    ERROR = "error"  # 错误（最近一次运行失败）
    DISABLED = "disabled"  # 禁用（配置无效）


@dataclass
class MCPServerInfo:
    """MCP 服务器信息"""

    name: str
    config: MCPServerConfig
    toolset: Any | None = None
    status: MCPConnectionStatus = MCPConnectionStatus.PENDING
    last_error: str | None = None
    last_run_time: datetime | None = None
    last_run_success: bool | None = None
    tools: list[str] = field(default_factory=list)


class MCPManager:
    """
    MCP 管理器

    功能：
    - 管理 MCP 服务器配置和工具集
    - 跟踪服务器运行状态
    - 支持优雅降级

    重要说明：
    Pydantic AI 的 MCP 连接是懒加载的，实际连接发生在 agent.run() 时。
    MCPManager 主要负责配置管理和状态跟踪，而非连接管理。
    """

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._servers: dict[str, MCPServerInfo] = {}
        self._initialized = False
        self._init_lock = asyncio.Lock()

    @property
    def servers(self) -> dict[str, MCPServerInfo]:
        """获取所有服务器信息"""
        return self._servers

    @property
    def toolsets(self) -> list[Any]:
        """获取所有有效的工具集（状态非 DISABLED）"""
        return [
            info.toolset
            for info in self._servers.values()
            if info.status != MCPConnectionStatus.DISABLED and info.toolset
        ]

    @property
    def is_initialized(self) -> bool:
        """是否已初始化"""
        return self._initialized

    def get_server_info(self, name: str) -> MCPServerInfo | None:
        """获取指定服务器的信息"""
        return self._servers.get(name)

    def get_status_summary(self) -> dict[str, Any]:
        """获取MCP状态摘要"""
        servers_summary = {}
        for name, info in self._servers.items():
            servers_summary[name] = {
                "status": info.status.value,
                "last_error": info.last_error,
                "last_run_time": info.last_run_time.isoformat() if info.last_run_time else None,
                "last_run_success": info.last_run_success,
                "tools_count": len(info.tools),
            }

        active_count = sum(
            1 for info in self._servers.values()
            if info.status in (MCPConnectionStatus.PENDING, MCPConnectionStatus.ACTIVE)
        )

        return {
            "enabled": self._settings.mcp.enabled,
            "initialized": self._initialized,
            "total_servers": len(self._servers),
            "active_servers": active_count,
            "servers": servers_summary,
        }

    async def initialize(self, test_connections: bool = False) -> bool:
        """
        初始化 MCP 管理器

        创建 MCP 工具集配置。注意：实际的 MCP 连接发生在 agent.run() 时。

        Args:
            test_connections: 是否在初始化时测试连接（默认 False，避免阻塞启动）

        Returns:
            是否至少有一个有效的服务器配置
        """
        async with self._init_lock:
            if self._initialized:
                return self._has_active_server()

            if not self._settings.mcp.enabled:
                logger.info("mcp_disabled")
                self._initialized = True
                return False

            logger.info(
                "mcp_initializing",
                servers_count=len(self._settings.mcp.servers),
            )

            # 创建所有服务器的工具集
            for server_name, server_config in self._settings.mcp.servers.items():
                toolset = self._create_toolset(server_name, server_config)
                status = MCPConnectionStatus.PENDING if toolset else MCPConnectionStatus.DISABLED

                self._servers[server_name] = MCPServerInfo(
                    name=server_name,
                    config=server_config,
                    toolset=toolset,
                    status=status,
                )

                # 可选：在初始化时测试连接
                if test_connections and toolset:
                    await self._test_server_connection(server_name)

            self._initialized = True

            active_count = sum(
                1 for info in self._servers.values()
                if info.status != MCPConnectionStatus.DISABLED
            )

            logger.info(
                "mcp_initialized",
                total=len(self._servers),
                active=active_count,
            )

            return active_count > 0

    async def _test_server_connection(self, server_name: str) -> bool:
        """
        测试指定服务器的连接

        Args:
            server_name: 服务器名称

        Returns:
            连接是否成功
        """
        info = self._servers.get(server_name)
        if not info or not info.toolset:
            return False

        try:
            logger.debug("mcp_testing_connection", server=server_name)
            # 使用较短超时测试连接
            async with info.toolset:
                pass  # 只是测试连接是否可建立
            info.status = MCPConnectionStatus.ACTIVE
            info.last_run_success = True
            logger.info("mcp_connection_test_success", server=server_name)
            return True
        except TimeoutError as e:
            info.status = MCPConnectionStatus.ERROR
            info.last_error = f"连接超时: {str(e)}"
            logger.warning(
                "mcp_connection_test_timeout",
                server=server_name,
                error=str(e),
            )
            return False
        except Exception as e:
            info.status = MCPConnectionStatus.ERROR
            info.last_error = str(e)
            logger.warning(
                "mcp_connection_test_failed",
                server=server_name,
                error=str(e),
            )

    def _create_toolset(
        self,
        server_name: str,
        config: MCPServerConfig,
    ) -> Any | None:
        """
        创建 MCP 工具集

        Args:
            server_name: 服务器名称
            config: MCP 服务器配置

        Returns:
            MCP 工具集实例，配置无效时返回 None
        """
        try:
            # HTTP 模式（通过 URL 配置）
            if config.url:
                # 判断是 SSE 还是 Streamable HTTP
                if config.url.endswith("/sse"):
                    from pydantic_ai.mcp import MCPServerSSE
                    logger.info(
                        "mcp_toolset_created",
                        server=server_name,
                        mode="sse",
                        url=config.url,
                    )
                    return MCPServerSSE(config.url)
                else:
                    logger.info(
                        "mcp_toolset_created",
                        server=server_name,
                        mode="streamable-http",
                        url=config.url,
                    )
                    return MCPServerStreamableHTTP(config.url)

            # Stdio 模式（通过命令配置）
            if config.command:
                logger.info(
                    "mcp_toolset_created",
                    server=server_name,
                    mode="stdio",
                    command=config.command,
                    args=config.args,
                )
                # 合并环境变量：继承父进程环境 + 用户配置的环境变量
                merged_env = dict(os.environ)
                if config.env:
                    merged_env.update(config.env)
                
                return MCPServerStdio(
                    command=config.command,
                    args=config.args,
                    env=merged_env,
                    timeout=config.timeout,
                )

            logger.warning(
                "mcp_toolset_config_invalid",
                server=server_name,
                message="既没有配置 URL 也没有配置命令",
            )
            return None

        except Exception as e:
            logger.error(
                "mcp_toolset_creation_failed",
                server=server_name,
                error=str(e),
            )
            return None

    def update_server_status(
        self,
        server_name: str,
        success: bool,
        error: str | None = None,
    ) -> None:
        """
        更新服务器运行状态

        在 agent.run() 完成后调用，用于跟踪服务器健康状态。

        Args:
            server_name: 服务器名称
            success: 是否成功
            error: 错误信息（如果有）
        """
        info = self._servers.get(server_name)
        if not info:
            return

        info.last_run_time = datetime.now()
        info.last_run_success = success
        info.last_error = error

        if success:
            info.status = MCPConnectionStatus.ACTIVE
        else:
            info.status = MCPConnectionStatus.ERROR

        logger.debug(
            "mcp_server_status_updated",
            server=server_name,
            status=info.status.value,
            success=success,
            error=error,
        )

    def reset_server_status(self, server_name: str) -> None:
        """
        重置服务器状态为待连接

        Args:
            server_name: 服务器名称
        """
        info = self._servers.get(server_name)
        if info and info.status != MCPConnectionStatus.DISABLED:
            info.status = MCPConnectionStatus.PENDING
            info.last_error = None
            logger.info("mcp_server_status_reset", server=server_name)

    def _has_active_server(self) -> bool:
        """检查是否有活跃的服务器"""
        return any(
            info.status in (MCPConnectionStatus.PENDING, MCPConnectionStatus.ACTIVE)
            for info in self._servers.values()
        )

    async def shutdown(self) -> None:
        """关闭 MCP 管理器"""
        logger.info("mcp_shutdown_starting")

        # Pydantic AI 的 MCP 工具集会在 agent.run() 结束时自动清理
        # 这里只需要清理内部状态
        for info in self._servers.values():
            if info.status != MCPConnectionStatus.DISABLED:
                info.status = MCPConnectionStatus.PENDING

        self._initialized = False
        logger.info("mcp_shutdown_complete")

    def get_toolsets_for_agent(self) -> list[Any]:
        """
        获取用于 Agent 的工具集列表

        如果没有有效的工具集，返回空列表（优雅降级）

        Returns:
            MCP 工具集列表
        """
        toolsets = self.toolsets
        if not toolsets:
            logger.debug("mcp_no_active_toolsets")
        return toolsets

    def get_healthy_toolsets(self) -> list[Any]:
        """
        获取健康的 MCP 工具集列表

        只返回状态为 PENDING 或 ACTIVE 的工具集，
        排除已标记为 ERROR 或 DISABLED 的服务器。

        Returns:
            健康的 MCP 工具集列表
        """
        healthy_toolsets = []
        for info in self._servers.values():
            if info.status in (MCPConnectionStatus.PENDING, MCPConnectionStatus.ACTIVE):
                if info.toolset:
                    healthy_toolsets.append(info.toolset)
            elif info.status == MCPConnectionStatus.ERROR:
                # 记录跳过的错误服务器
                logger.debug(
                    "mcp_skipping_unhealthy_server",
                    server=info.name,
                    status=info.status.value,
                    error=info.last_error,
                )

        if not healthy_toolsets:
            logger.debug("mcp_no_healthy_toolsets_available")

        return healthy_toolsets

    def mark_server_failed(self, server_name: str, error: str) -> None:
        """
        标记服务器为失败状态

        当检测到某个 MCP 服务器连接失败时调用，
        避免后续请求继续尝试使用该服务器。

        Args:
            server_name: 服务器名称
            error: 错误信息
        """
        info = self._servers.get(server_name)
        if info and info.status != MCPConnectionStatus.DISABLED:
            info.status = MCPConnectionStatus.ERROR
            info.last_error = error
            info.last_run_success = False
            logger.warning(
                "mcp_server_marked_failed",
                server=server_name,
                error=error,
            )

    def disable_unhealthy_servers(self) -> list[str]:
        """
        禁用所有不健康的服务器

        将状态为 ERROR 的服务器标记为 DISABLED，
        防止它们被继续使用。

        Returns:
            被禁用的服务器名称列表
        """
        disabled_servers = []
        for name, info in self._servers.items():
            if info.status == MCPConnectionStatus.ERROR:
                info.status = MCPConnectionStatus.DISABLED
                disabled_servers.append(name)
                logger.info("mcp_server_disabled", server=name, error=info.last_error)
        return disabled_servers

    def get_toolset_by_name(self, name: str) -> Any | None:
        """
        根据名称获取工具集

        Args:
            name: 服务器名称

        Returns:
            工具集实例，如果不存在或已禁用则返回 None
        """
        info = self._servers.get(name)
        if info and info.status != MCPConnectionStatus.DISABLED and info.toolset:
            return info.toolset
        return None

    def reload_toolset(self, server_name: str) -> bool:
        """
        重新加载指定服务器的工具集

        Args:
            server_name: 服务器名称

        Returns:
            是否成功
        """
        info = self._servers.get(server_name)
        if not info:
            logger.warning("mcp_reload_server_not_found", server=server_name)
            return False

        # 重新创建工具集
        new_toolset = self._create_toolset(server_name, info.config)
        if new_toolset:
            info.toolset = new_toolset
            info.status = MCPConnectionStatus.PENDING
            info.last_error = None
            logger.info("mcp_toolset_reloaded", server=server_name)
            return True
        else:
            info.status = MCPConnectionStatus.DISABLED
            logger.warning("mcp_toolset_reload_failed", server=server_name)
            return False


# 全局单例
_mcp_manager: MCPManager | None = None


def get_mcp_manager() -> MCPManager:
    """获取 MCP 管理器单例"""
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = MCPManager()
    return _mcp_manager


# ============ 兼容旧接口 ============


def create_mcp_toolset(
    server_name: str,
    config: MCPServerConfig,
) -> MCPServerStdio | MCPServerStreamableHTTP | None:
    """
    根据配置创建 MCP 工具集

    Args:
        server_name: 服务器名称
        config: MCP 服务器配置

    Returns:
        MCP 工具集实例，配置无效时返回 None
    """
    try:
        # HTTP 模式（通过 URL 配置）
        if config.url:
            # 判断是 SSE 还是 Streamable HTTP
            if config.url.endswith("/sse"):
                from pydantic_ai.mcp import MCPServerSSE
                logger.info(
                    "mcp_server_created",
                    server=server_name,
                    mode="sse",
                    url=config.url,
                )
                return MCPServerSSE(config.url)
            else:
                logger.info(
                    "mcp_server_created",
                    server=server_name,
                    mode="streamable-http",
                    url=config.url,
                )
                return MCPServerStreamableHTTP(config.url)

        # Stdio 模式（通过命令配置）
        if config.command:
            logger.info(
                "mcp_server_created",
                server=server_name,
                mode="stdio",
                command=config.command,
                args=config.args,
            )
            # 合并环境变量：继承父进程环境 + 用户配置的环境变量
            merged_env = dict(os.environ)
            if config.env:
                merged_env.update(config.env)
            
            return MCPServerStdio(
                command=config.command,
                args=config.args,
                env=merged_env,
                timeout=config.timeout,
            )

        logger.warning(
            "mcp_server_config_invalid",
            server=server_name,
            message="既没有配置 URL 也没有配置命令",
        )
        return None

    except Exception as e:
        logger.error(
            "mcp_server_creation_failed",
            server=server_name,
            error=str(e),
        )
        return None


def load_mcp_toolsets(settings: Settings | None = None) -> list[Any]:
    """
    从配置加载 MCP 工具集

    Args:
        settings: 应用配置，不传则使用全局配置

    Returns:
        MCP 工具集列表，可直接传给 Agent 的 toolsets 参数
    """
    settings = settings or get_settings()
    toolsets: list[Any] = []

    mcp_config = settings.mcp
    if not mcp_config.enabled:
        logger.info("mcp_disabled")
        return toolsets

    for server_name, server_config in mcp_config.servers.items():
        toolset = create_mcp_toolset(server_name, server_config)
        if toolset:
            toolsets.append(toolset)

    logger.info(
        "mcp_toolsets_loaded",
        count=len(toolsets),
        servers=list(mcp_config.servers.keys()),
    )
    return toolsets


def load_mcp_toolsets_from_dict(config_dict: dict[str, Any]) -> list[Any]:
    """
    从字典配置加载 MCP 工具集

    支持两种格式：
    1. 官方格式: {"mcpServers": {"server-name": {...}}}
    2. 简化格式: {"server-name": {...}}

    Args:
        config_dict: MCP 配置字典

    Returns:
        MCP 工具集列表
    """
    toolsets: list[Any] = []

    # 提取 mcpServers 部分
    servers_data = config_dict.get("mcpServers", config_dict)
    if not isinstance(servers_data, dict):
        logger.warning("mcp_config_invalid_format")
        return toolsets

    for server_name, server_config in servers_data.items():
        if not isinstance(server_config, dict):
            continue

        config = MCPServerConfig(**server_config)
        toolset = create_mcp_toolset(server_name, config)
        if toolset:
            toolsets.append(toolset)

    logger.info("mcp_toolsets_loaded_from_dict", count=len(toolsets))
    return toolsets
