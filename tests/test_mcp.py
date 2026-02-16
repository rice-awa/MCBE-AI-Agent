"""MCP 客户端测试"""

import pytest
from config.settings import MCPServerConfig, MCPConfig, Settings


class TestMCPConfig:
    """MCP 配置测试"""

    def test_mcp_server_config_creation_stdio(self):
        """测试 MCP 服务器配置创建（stdio 模式）"""
        config = MCPServerConfig(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            env={"DEBUG": "true"},
        )
        assert config.command == "npx"
        assert len(config.args) == 3
        assert config.env == {"DEBUG": "true"}
        assert config.url is None
        assert config.timeout == 10

    def test_mcp_server_config_creation_http(self):
        """测试 MCP 服务器配置创建（HTTP 模式）"""
        config = MCPServerConfig(
            url="http://localhost:3000/mcp",
            timeout=30,
        )
        assert config.url == "http://localhost:3000/mcp"
        assert config.command is None
        assert config.timeout == 30

    def test_mcp_config_creation(self):
        """测试 MCP 配置创建（字典格式）"""
        config = MCPConfig(
            enabled=True,
            servers={
                "filesystem": MCPServerConfig(
                    command="npx",
                    args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                )
            },
        )
        assert config.enabled is True
        assert "filesystem" in config.servers
        assert len(config.servers) == 1

    def test_mcp_config_default(self):
        """测试 MCP 默认配置"""
        config = MCPConfig()
        assert config.enabled is False
        assert len(config.servers) == 0


class TestMCPToolsetCreation:
    """MCP 工具集创建测试"""

    def test_create_stdio_toolset(self):
        """测试创建 stdio 工具集"""
        from services.agent.mcp import create_mcp_toolset

        config = MCPServerConfig(
            command="echo",
            args=["hello"],
        )
        toolset = create_mcp_toolset("test-server", config)
        assert toolset is not None

    def test_create_http_toolset(self):
        """测试创建 HTTP 工具集"""
        from services.agent.mcp import create_mcp_toolset

        config = MCPServerConfig(
            url="http://localhost:8000/mcp",
        )
        toolset = create_mcp_toolset("test-server", config)
        assert toolset is not None

    def test_create_sse_toolset(self):
        """测试创建 SSE 工具集"""
        from services.agent.mcp import create_mcp_toolset

        config = MCPServerConfig(
            url="http://localhost:3001/sse",
        )
        toolset = create_mcp_toolset("test-server", config)
        assert toolset is not None


class TestLoadMCPToolsets:
    """MCP 工具集加载测试"""

    def test_load_mcp_toolsets_disabled(self):
        """测试 MCP 禁用时不加载工具集"""
        from services.agent.mcp import load_mcp_toolsets

        settings = Settings()
        settings.mcp.enabled = False
        toolsets = load_mcp_toolsets(settings)
        assert len(toolsets) == 0

    def test_load_mcp_toolsets_from_dict(self):
        """测试从字典加载 MCP 工具集"""
        from services.agent.mcp import load_mcp_toolsets_from_dict

        config = {
            "mcpServers": {
                "test-server": {
                    "command": "echo",
                    "args": ["hello"],
                }
            }
        }
        toolsets = load_mcp_toolsets_from_dict(config)
        assert len(toolsets) == 1

    def test_load_mcp_toolsets_simple_format(self):
        """测试简化格式加载 MCP 工具集"""
        from services.agent.mcp import load_mcp_toolsets_from_dict

        config = {
            "test-server": {
                "command": "echo",
                "args": ["hello"],
            }
        }
        toolsets = load_mcp_toolsets_from_dict(config)
        assert len(toolsets) == 1


class TestMCPManager:
    """MCP 管理器测试"""

    def test_mcp_manager_init(self):
        """测试 MCP 管理器初始化"""
        from services.agent.mcp import MCPManager

        manager = MCPManager()
        assert manager._initialized is False
        assert len(manager._toolsets) == 0

    def test_mcp_manager_get_toolsets(self):
        """测试获取 MCP 工具集"""
        from services.agent.mcp import MCPManager

        manager = MCPManager()
        toolsets = manager.get_toolsets()
        assert isinstance(toolsets, list)


class TestGetMCPManager:
    """MCP 管理器单例测试"""

    def test_get_mcp_manager_singleton(self):
        """测试获取 MCP 管理器单例"""
        from services.agent.mcp import get_mcp_manager, MCPManager

        manager1 = get_mcp_manager()
        manager2 = get_mcp_manager()
        assert manager1 is manager2
        assert isinstance(manager1, MCPManager)


class TestSettingsMCPIntegration:
    """Settings MCP 集成测试"""

    def test_invalid_minecraft_commands_json_should_not_raise(caplog):
        """测试非法命令 JSON 只记录警告不抛异常"""
        settings = Settings(minecraft_commands_json="{invalid")
        assert settings.minecraft.commands

    def test_invalid_mcp_servers_json_should_not_raise(caplog):
        """测试非法 MCP servers JSON 只记录警告不抛异常"""
        settings = Settings(mcp_servers_json="{invalid")
        assert settings.mcp.enabled is False

    def test_mcp_servers_json_official_format(self):
        """测试官方格式 MCP servers JSON"""
        import json
        settings = Settings(
            mcp_servers_json=json.dumps({
                "mcpServers": {
                    "filesystem": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                    }
                }
            })
        )
        assert settings.mcp.enabled is True
        assert "filesystem" in settings.mcp.servers
        assert settings.mcp.servers["filesystem"].command == "npx"

    def test_mcp_servers_json_simple_format(self):
        """测试简化格式 MCP servers JSON"""
        import json
        settings = Settings(
            mcp_servers_json=json.dumps({
                "filesystem": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                }
            })
        )
        assert settings.mcp.enabled is True
        assert "filesystem" in settings.mcp.servers
