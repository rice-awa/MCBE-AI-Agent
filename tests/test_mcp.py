"""MCP 客户端测试"""

import asyncio
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


class TestMCPConnectionStatus:
    """MCP 连接状态枚举测试"""

    def test_status_values(self):
        """测试状态枚举值"""
        from services.agent.mcp import MCPConnectionStatus

        assert MCPConnectionStatus.PENDING.value == "pending"
        assert MCPConnectionStatus.ACTIVE.value == "active"
        assert MCPConnectionStatus.ERROR.value == "error"
        assert MCPConnectionStatus.DISABLED.value == "disabled"


class TestMCPServerInfo:
    """MCP 服务器信息测试"""

    def test_server_info_creation(self):
        """测试服务器信息创建"""
        from services.agent.mcp import MCPServerInfo, MCPConnectionStatus

        config = MCPServerConfig(command="echo")
        info = MCPServerInfo(
            name="test-server",
            config=config,
        )

        assert info.name == "test-server"
        assert info.config == config
        assert info.status == MCPConnectionStatus.PENDING
        assert info.toolset is None
        assert info.last_error is None
        assert info.last_run_time is None
        assert info.last_run_success is None
        assert info.tools == []


class TestMCPManager:
    """MCP 管理器测试"""

    def test_mcp_manager_init(self):
        """测试 MCP 管理器初始化"""
        from services.agent.mcp import MCPManager

        manager = MCPManager()
        assert manager.is_initialized is False
        assert len(manager.servers) == 0

    def test_mcp_manager_servers_property(self):
        """测试 MCP 管理器 servers 属性"""
        from services.agent.mcp import MCPManager

        manager = MCPManager()
        servers = manager.servers
        assert isinstance(servers, dict)

    def test_mcp_manager_toolsets_property(self):
        """测试 MCP 管理器 toolsets 属性"""
        from services.agent.mcp import MCPManager

        manager = MCPManager()
        toolsets = manager.toolsets
        assert isinstance(toolsets, list)

    def test_mcp_manager_get_status_summary(self):
        """测试获取 MCP 状态摘要"""
        from services.agent.mcp import MCPManager

        manager = MCPManager()
        summary = manager.get_status_summary()

        assert "enabled" in summary
        assert "initialized" in summary
        assert "total_servers" in summary
        assert "active_servers" in summary
        assert "servers" in summary

    def test_mcp_manager_get_server_info_not_found(self):
        """测试获取不存在的服务器信息"""
        from services.agent.mcp import MCPManager

        manager = MCPManager()
        info = manager.get_server_info("non-existent")
        assert info is None

    def test_mcp_manager_get_toolsets_for_agent(self):
        """测试获取用于 Agent 的工具集"""
        from services.agent.mcp import MCPManager

        manager = MCPManager()
        toolsets = manager.get_toolsets_for_agent()
        assert isinstance(toolsets, list)

    def test_mcp_manager_get_toolset_by_name(self):
        """测试根据名称获取工具集"""
        from services.agent.mcp import MCPManager

        manager = MCPManager()
        toolset = manager.get_toolset_by_name("non-existent")
        assert toolset is None

    @pytest.mark.asyncio
    async def test_mcp_manager_initialize_disabled(self):
        """测试 MCP 禁用时的初始化"""
        from services.agent.mcp import MCPManager

        settings = Settings()
        settings.mcp.enabled = False

        manager = MCPManager(settings)
        result = await manager.initialize()

        assert result is False
        assert manager.is_initialized is True

    @pytest.mark.asyncio
    async def test_mcp_manager_shutdown(self):
        """测试 MCP 管理器关闭"""
        from services.agent.mcp import MCPManager

        manager = MCPManager()
        await manager.shutdown()

        assert manager.is_initialized is False

    def test_mcp_manager_update_server_status(self):
        """测试更新服务器状态"""
        from services.agent.mcp import MCPManager, MCPConnectionStatus, MCPServerInfo

        manager = MCPManager()
        # 手动添加一个服务器信息
        config = MCPServerConfig(command="echo")
        manager._servers["test-server"] = MCPServerInfo(
            name="test-server",
            config=config,
            toolset=None,
        )

        manager.update_server_status("test-server", success=True)
        assert manager._servers["test-server"].status == MCPConnectionStatus.ACTIVE

        manager.update_server_status("test-server", success=False, error="test error")
        assert manager._servers["test-server"].status == MCPConnectionStatus.ERROR
        assert manager._servers["test-server"].last_error == "test error"

    def test_mcp_manager_reset_server_status(self):
        """测试重置服务器状态"""
        from services.agent.mcp import MCPManager, MCPConnectionStatus, MCPServerInfo

        manager = MCPManager()
        config = MCPServerConfig(command="echo")
        manager._servers["test-server"] = MCPServerInfo(
            name="test-server",
            config=config,
            toolset=None,
            status=MCPConnectionStatus.ERROR,
            last_error="some error",
        )

        manager.reset_server_status("test-server")
        assert manager._servers["test-server"].status == MCPConnectionStatus.PENDING
        assert manager._servers["test-server"].last_error is None


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

    def test_invalid_minecraft_commands_json_should_not_raise(self, caplog):
        """测试非法命令 JSON 只记录警告不抛异常"""
        settings = Settings(minecraft_commands_json="{invalid")
        assert settings.minecraft.commands

    def test_invalid_mcp_servers_json_should_not_raise(self, caplog):
        """测试非法 MCP servers JSON 只记录警告不抛异常"""
        settings = Settings(mcp_servers_json="{invalid")
        assert settings.mcp.enabled is False

    def test_mcp_servers_json_official_format(self):
        """测试官方格式 MCP servers JSON"""
        import json
        # 使用环境变量别名来设置 mcp_enabled
        settings = Settings(
            MCP_ENABLED=True,
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
            MCP_ENABLED=True,
            mcp_servers_json=json.dumps({
                "filesystem": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                }
            })
        )
        assert settings.mcp.enabled is True
        assert "filesystem" in settings.mcp.servers

    def test_mcp_command_in_minecraft_config(self):
        """测试 MCP 命令在 Minecraft 配置中"""
        settings = Settings()
        assert "AGENT MCP" in settings.minecraft.commands
        mcp_cmd = settings.minecraft.commands["AGENT MCP"]
        assert isinstance(mcp_cmd, dict)
        assert mcp_cmd["type"] == "mcp"
        assert "AGENT mcp" in mcp_cmd["aliases"]


class TestMCPManagerAsyncOperations:
    """MCP 管理器异步操作测试"""

    @pytest.mark.asyncio
    async def test_initialize_with_servers(self):
        """测试带服务器配置的初始化"""
        from services.agent.mcp import MCPManager

        settings = Settings()
        settings.mcp.enabled = True
        settings.mcp.servers = {
            "test-server": MCPServerConfig(
                command="echo",
                args=["test"],
            )
        }

        manager = MCPManager(settings)
        result = await manager.initialize()

        assert manager.is_initialized is True
        assert "test-server" in manager.servers

        # 清理
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_reload_toolset(self):
        """测试重新加载工具集"""
        from services.agent.mcp import MCPManager, MCPConnectionStatus

        settings = Settings()
        settings.mcp.enabled = True
        settings.mcp.servers = {
            "test-server": MCPServerConfig(
                command="echo",
                args=["test"],
            )
        }

        manager = MCPManager(settings)
        await manager.initialize()

        # 重新加载存在的服务器
        result = manager.reload_toolset("test-server")
        assert result is True
        assert manager._servers["test-server"].status == MCPConnectionStatus.PENDING

        # 重新加载不存在的服务器
        result = manager.reload_toolset("non-existent")
        assert result is False

        await manager.shutdown()
