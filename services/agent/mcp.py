"""MCP 工具集管理 - 使用 PydanticAI 官方 MCP 客户端"""

from typing import Any

from pydantic_ai.mcp import MCPServerStdio, MCPServerStreamableHTTP

from config.logging import get_logger
from config.settings import Settings, MCPServerConfig, get_settings

logger = get_logger(__name__)


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
            return MCPServerStdio(
                command=config.command,
                args=config.args,
                env=config.env if config.env else None,
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


# 为了向后兼容，保留 MCPManager 类的简化版本
class MCPManager:
    """MCP 管理器 - 简化版，主要用于兼容旧代码"""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._toolsets: list[Any] = []
        self._initialized = False

    async def initialize(self) -> bool:
        """初始化 MCP 工具集"""
        if self._initialized:
            return True

        self._toolsets = load_mcp_toolsets(self._settings)
        self._initialized = True
        logger.info(
            "mcp_initialized",
            toolset_count=len(self._toolsets),
        )
        return True

    def get_toolsets(self) -> list[Any]:
        """获取所有 MCP 工具集"""
        return self._toolsets

    async def shutdown(self) -> None:
        """关闭 MCP 连接"""
        # PydanticAI 的 MCP toolsets 会在 agent 运行结束后自动清理
        self._toolsets.clear()
        self._initialized = False
        logger.info("mcp_shutdown")


# 全局单例
_mcp_manager: MCPManager | None = None


def get_mcp_manager() -> MCPManager:
    """获取 MCP 管理器单例"""
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = MCPManager()
    return _mcp_manager
