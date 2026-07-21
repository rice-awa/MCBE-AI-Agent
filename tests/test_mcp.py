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

        manager = MCPManager(Settings())
        assert manager.is_initialized is False
        assert len(manager.servers) == 0

    def test_mcp_manager_servers_property(self):
        """测试 MCP 管理器 servers 属性"""
        from services.agent.mcp import MCPManager

        manager = MCPManager(Settings())
        servers = manager.servers
        assert isinstance(servers, dict)

    def test_mcp_manager_toolsets_property(self):
        """测试 MCP 管理器 toolsets 属性"""
        from services.agent.mcp import MCPManager

        manager = MCPManager(Settings())
        toolsets = manager.toolsets
        assert isinstance(toolsets, list)

    def test_mcp_manager_get_status_summary(self):
        """测试获取 MCP 状态摘要"""
        from services.agent.mcp import MCPManager

        manager = MCPManager(Settings())
        summary = manager.get_status_summary()

        assert "enabled" in summary
        assert "initialized" in summary
        assert "total_servers" in summary
        assert "active_servers" in summary
        assert "servers" in summary

    def test_mcp_manager_get_server_info_not_found(self):
        """测试获取不存在的服务器信息"""
        from services.agent.mcp import MCPManager

        manager = MCPManager(Settings())
        info = manager.get_server_info("non-existent")
        assert info is None

    def test_mcp_manager_get_toolsets_for_agent(self):
        """测试获取用于 Agent 的工具集"""
        from services.agent.mcp import MCPManager

        manager = MCPManager(Settings())
        toolsets = manager.get_toolsets_for_agent()
        assert isinstance(toolsets, list)

    def test_mcp_manager_get_toolset_by_name(self):
        """测试根据名称获取工具集"""
        from services.agent.mcp import MCPManager

        manager = MCPManager(Settings())
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

        manager = MCPManager(Settings())
        await manager.shutdown()

        assert manager.is_initialized is False

    def test_mcp_manager_update_server_status(self):
        """测试更新服务器状态"""
        from services.agent.mcp import MCPManager, MCPConnectionStatus, MCPServerInfo

        manager = MCPManager(Settings())
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

        manager = MCPManager(Settings())
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

        manager1 = get_mcp_manager(Settings())
        manager2 = get_mcp_manager(Settings())
        assert manager1 is manager2
        assert isinstance(manager1, MCPManager)


class TestSettingsMCPIntegration:
    """Settings MCP 集成测试"""

    def test_mcp_servers_official_format_from_config_json(self, tmp_path, monkeypatch):
        """测试官方格式 MCP servers JSON 文件配置"""
        import json

        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.json").write_text(
            json.dumps(
                {
                    "mcp": {
                        "enabled": True,
                        "servers": {
                            "filesystem": {
                                "command": "npx",
                                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                            }
                        },
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        settings = Settings()

        assert settings.mcp.enabled is True
        assert "filesystem" in settings.mcp.servers
        assert settings.mcp.servers["filesystem"].command == "npx"

    def test_mcp_servers_simple_format_from_config_json(self, tmp_path, monkeypatch):
        """测试简化格式 MCP servers JSON 文件配置"""
        import json

        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.json").write_text(
            json.dumps(
                {
                    "mcp": {
                        "enabled": True,
                        "servers": {
                            "filesystem": {
                                "command": "npx",
                                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                            }
                        },
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        settings = Settings()

        assert settings.mcp.enabled is True
        assert "filesystem" in settings.mcp.servers

    def test_minecraft_commands_from_config_json(self, tmp_path, monkeypatch):
        """测试 Minecraft 命令可通过 JSON 文件配置"""
        import json

        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.json").write_text(
            json.dumps(
                {
                    "minecraft": {
                        "commands": {
                            "AGENT MCP": {
                                "type": "mcp",
                                "aliases": ["AGENT mcp", "AI MCP", "AI mcp"],
                                "description": "MCP 服务器管理",
                                "usage": "<list/status/reload>",
                            },
                            "测试": "help",
                        }
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        settings = Settings()

        assert "AGENT MCP" in settings.minecraft.commands
        assert settings.minecraft.commands["测试"] == "help"
        mcp_cmd = settings.minecraft.commands["AGENT MCP"]
        assert isinstance(mcp_cmd, dict)
        assert mcp_cmd["type"] == "mcp"
        assert "AGENT mcp" in mcp_cmd["aliases"]

    def test_plain_mcp_environment_json_is_ignored(self, tmp_path, monkeypatch):
        """测试旧 MCP_SERVERS 环境变量不会再配置 MCP"""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(
            "MCP_SERVERS",
            '{"filesystem": {"command": "npx", "args": ["-y", "server"]}}',
        )
        monkeypatch.setenv("MCP_ENABLED", "true")

        settings = Settings()

        assert settings.mcp.enabled is False
        assert settings.mcp.servers == {}

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


class TestMCPFailureInvariants:
    """MCP 故障不变量：局部失败不拖垮健康 server；不整轮重放。"""

    def test_one_server_failure_does_not_remove_healthy(self):
        from services.agent.mcp import MCPManager, MCPConnectionStatus, MCPServerInfo

        manager = MCPManager(Settings())
        healthy = object()
        failed = object()
        manager._servers["good"] = MCPServerInfo(
            name="good",
            config=MCPServerConfig(command="echo"),
            toolset=healthy,
            status=MCPConnectionStatus.ACTIVE,
        )
        manager._servers["bad"] = MCPServerInfo(
            name="bad",
            config=MCPServerConfig(command="echo"),
            toolset=failed,
            status=MCPConnectionStatus.ACTIVE,
        )

        manager.mark_server_failed("bad", "timeout")
        toolsets = manager.get_healthy_toolsets()

        assert manager._servers["bad"].status == MCPConnectionStatus.ERROR
        assert manager._servers["good"].status == MCPConnectionStatus.ACTIVE
        assert healthy in toolsets
        assert failed not in toolsets

    def test_get_toolsets_for_agent_uses_healthy_only(self):
        from services.agent.mcp import MCPManager, MCPConnectionStatus, MCPServerInfo

        manager = MCPManager(Settings())
        a = object()
        b = object()
        manager._servers["a"] = MCPServerInfo(
            name="a",
            config=MCPServerConfig(command="echo"),
            toolset=a,
            status=MCPConnectionStatus.PENDING,
        )
        manager._servers["b"] = MCPServerInfo(
            name="b",
            config=MCPServerConfig(command="echo"),
            toolset=b,
            status=MCPConnectionStatus.ERROR,
            last_error="boom",
        )
        assert manager.get_toolsets_for_agent() == [a]


    @pytest.mark.asyncio
    async def test_mcp_timeout_does_not_replay_whole_run(self, monkeypatch):
        """行为测试：本地工具已执行后 MCP timeout 返回结构化错误，且不重跑整轮。

        用 non_stream + FunctionModel：先执行本地工具，再在下一轮 model 请求抛
        含 mcp/timeout 的 TimeoutError，触发 no-replay TRANSIENT 路径。
        """
        from types import SimpleNamespace
        from uuid import uuid4

        from pydantic_ai import Agent, RunContext
        from pydantic_ai.messages import ModelResponse, ToolCallPart
        from pydantic_ai.models.function import FunctionModel
        from pydantic_ai.models.test import TestModel
        from pydantic_ai.tools import DeferredToolRequests

        from models.agent import AgentDependencies
        from services.agent.core import non_stream_response_handler
        from services.agent.harness.execution import HarnessCapability, PolicyEngine
        from services.agent.tool_results import ToolResult

        class _Settings:
            runtime_harness_enabled = True
            runtime_harness_audit_enabled = False
            runtime_harness_audit_path = "logs/unused.jsonl"
            runtime_harness_audit_max_records = 100
            hard_deny_tools: list[str] = []
            hard_deny_command_roots: list[str] = []
            max_batch_commands = 10
            mcp_tool_allowlist: list[str] = []
            tool_policy_version = "2026-07-21.1"
            approval_ttl = 120.0
            stream_sentence_mode = False
            request_limit = 8
            tool_calls_limit = 8
            input_tokens_limit = None
            output_tokens_limit = None
            total_tokens_limit = None
            run_timeout = 30.0
            max_tool_concurrency = 4
            context_output_reserve_tokens = 1024

            def get_provider_config(self, provider_name: str | None = None):
                return SimpleNamespace(context_window=None)

        local_calls = {"count": 0}
        handler_invocations = {"count": 0}

        policy = PolicyEngine.from_settings(_Settings())
        agent: Agent[AgentDependencies, str | DeferredToolRequests] = Agent(
            "test",
            deps_type=AgentDependencies,
            output_type=[str, DeferredToolRequests],
            capabilities=[HarnessCapability(policy=policy)],
        )

        @agent.tool
        async def list_available_providers(ctx: RunContext[AgentDependencies]) -> str:
            local_calls["count"] += 1
            return ToolResult.ok("providers: deepseek")

        step = {"n": 0}

        def model_fn(messages, info):
            step["n"] += 1
            if step["n"] == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="list_available_providers",
                            args={},
                            tool_call_id="tc-local-1",
                        )
                    ]
                )
            # 本地工具已执行后，模拟 MCP 连接/调用超时（不递归重放）
            raise TimeoutError("mcp server demo-mcp timeout: deadline exceeded")

        async def _noop_send(msg: str) -> None:
            return None

        async def _noop_cmd(cmd: str):
            from services.agent.tool_results import CommandResult

            return CommandResult.ok("ok")

        deps = AgentDependencies(
            connection_id=uuid4(),
            player_name="Steve",
            settings=_Settings(),
            http_client=SimpleNamespace(),
            send_to_game=_noop_send,
            run_command=_noop_cmd,
            provider="test",
            run_id="run-mcp-timeout",
            conversation_id="conv-mcp",
        )

        from services.agent import core as core_mod

        class _FakeManager:
            def get_active_agent(self, agent_arg=None, **kwargs):
                return agent_arg or agent

        monkeypatch.setattr(core_mod, "get_agent_manager", lambda: _FakeManager())

        events = []
        handler_invocations["count"] += 1
        async for event in non_stream_response_handler(
            "call tools",
            deps,
            model=FunctionModel(model_fn),
            agent=agent,
        ):
            events.append(event)

        error_events = [e for e in events if e.event_type == "error"]
        assert error_events, f"expected structured error, got {[e.event_type for e in events]}"
        err = error_events[0]
        assert (err.metadata or {}).get("error_kind") == "TRANSIENT"
        assert (err.metadata or {}).get("run_id") == "run-mcp-timeout"

        # 本地工具已执行（最多一次）；不得因 timeout 再起一轮导致二次执行
        assert local_calls["count"] == 1
        assert handler_invocations["count"] == 1

        # 再跑一次独立请求可以再次执行本地工具（证明只影响当前 run，不递归重放）
        local_before = local_calls["count"]
        events2 = []
        handler_invocations["count"] += 1
        async for event in non_stream_response_handler(
            "list only",
            deps,
            model=TestModel(call_tools=["list_available_providers"]),
            agent=agent,
        ):
            events2.append(event)
        assert local_calls["count"] == local_before + 1
        assert handler_invocations["count"] == 2
        assert not any(e.event_type == "error" for e in events2)
