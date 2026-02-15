"""MCP 客户端测试"""

import pytest
from config.settings import MCPServerConfig, MCPConfig, Settings


class TestMCPConfig:
    """MCP 配置测试"""

    def test_mcp_server_config_creation(self):
        """测试 MCP 服务器配置创建"""
        config = MCPServerConfig(
            name="test-server",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            env={"DEBUG": "true"},
            auto_start=True,
        )
        assert config.name == "test-server"
        assert config.command == "npx"
        assert len(config.args) == 3
        assert config.auto_start is True

    def test_mcp_server_config_with_url(self):
        """测试带 URL 的 MCP 服务器配置"""
        config = MCPServerConfig(
            name="remote-server",
            command="http",
            url="http://localhost:3000/mcp",
        )
        assert config.name == "remote-server"
        assert config.url == "http://localhost:3000/mcp"

    def test_mcp_config_creation(self):
        """测试 MCP 配置创建"""
        config = MCPConfig(
            enabled=True,
            servers=[
                MCPServerConfig(
                    name="filesystem",
                    command="npx",
                    args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                )
            ],
        )
        assert config.enabled is True
        assert len(config.servers) == 1

    def test_mcp_config_default(self):
        """测试 MCP 默认配置"""
        config = MCPConfig()
        assert config.enabled is False
        assert len(config.servers) == 0


class TestMCPClient:
    """MCP 客户端测试"""

    def test_mcp_client_init(self):
        """测试 MCP 客户端初始化"""
        from services.agent.mcp import MCPClient

        config = MCPServerConfig(
            name="test",
            command="echo",
            args=["hello"],
        )
        client = MCPClient(config)
        assert client._config.name == "test"
        assert client._session is None

    def test_mcp_adapter_init(self):
        """测试 MCP 适配器初始化"""
        from services.agent.mcp import MCPToolAdapter, MCPClient

        config = MCPServerConfig(name="test", command="echo")
        client = MCPClient(config)
        adapter = MCPToolAdapter(client)
        assert adapter._client is client
        assert len(adapter._tools) == 0


class TestMCPManager:
    """MCP 管理器测试"""

    def test_mcp_manager_init(self):
        """测试 MCP 管理器初始化"""
        from services.agent.mcp import MCPManager

        manager = MCPManager()
        assert manager._initialized is False
        assert len(manager._clients) == 0

    def test_mcp_manager_get_adapter(self):
        """测试获取 MCP 适配器"""
        from services.agent.mcp import MCPManager

        manager = MCPManager()
        adapter = manager.get_adapter("nonexistent")
        assert adapter is None

    def test_mcp_manager_get_all_tools(self):
        """测试获取所有工具"""
        from services.agent.mcp import MCPManager

        manager = MCPManager()
        tools = manager.get_all_tools()
        assert isinstance(tools, list)


class TestGetMCPManager:
    """MCP 管理器单例测试"""

    def test_get_mcp_manager_singleton(self):
        """测试获取 MCP 管理器单例"""
        from services.agent.mcp import get_mcp_manager, MCPManager

        manager1 = get_mcp_manager()
        manager2 = get_mcp_manager()
        assert manager1 is manager2
        assert isinstance(manager1, MCPManager)
