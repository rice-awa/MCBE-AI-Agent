"""命令行接口 - 应用主入口"""

import asyncio
import signal
import sys
from pathlib import Path
from typing import Any

import click

from config.settings import get_settings
from config.logging import setup_logging, get_logger
from core.queue import MessageBroker
from services.agent.worker import AgentWorker
from services.agent.providers import ProviderRegistry
from services.websocket.server import WebSocketServer
from services.auth.jwt_handler import JWTHandler

logger = get_logger(__name__)


class Application:
    """MCBE AI Agent 应用"""

    def __init__(self):
        self.settings = get_settings()
        self.broker = MessageBroker(max_size=self.settings.queue_max_size)
        self.jwt_handler = JWTHandler(self.settings)
        self.ws_server = WebSocketServer(
            self.broker, self.settings, self.jwt_handler
        )
        self.workers: list[AgentWorker] = []
        self._shutdown_event = asyncio.Event()

    async def start(self) -> None:
        """启动应用"""
        logger.info(
            "application_starting",
            version="2.2.0",
            host=self.settings.host,
            port=self.settings.port,
            default_provider=self.settings.default_provider,
            worker_count=self.settings.llm_worker_count,
            dev_mode=self.settings.dev_mode,
        )

        # 预热 LLM 模型
        await ProviderRegistry.warmup_models(self.settings)

        # 异步初始化 MCP 管理器
        from services.agent.mcp import get_mcp_manager
        mcp_manager = get_mcp_manager()
        mcp_connected = await mcp_manager.initialize()

        mcp_status = mcp_manager.get_status_summary()
        logger.info(
            "mcp_initialization_complete",
            enabled=mcp_status["enabled"],
            connected=mcp_status["connected_servers"],
            total=mcp_status["total_servers"],
        )

        # 初始化 Agent 管理器（使用已连接的 MCP 工具集）
        from services.agent.core import get_agent_manager
        agent_manager = get_agent_manager()
        await agent_manager.initialize(
            self.settings,
            mcp_toolsets=mcp_manager.get_toolsets_for_agent(),
        )
        logger.info(
            "agent_manager_initialized",
            mcp_toolsets_count=len(agent_manager.mcp_toolsets),
            mcp_connected=mcp_connected,
        )

        # 启动 Agent Workers
        for i in range(self.settings.llm_worker_count):
            worker = AgentWorker(self.broker, self.settings, worker_id=i)
            await worker.start()
            self.workers.append(worker)

        # 启动 WebSocket 服务器
        await self.ws_server.start()

        logger.info("application_started")

        # 等待关闭信号
        await self._shutdown_event.wait()

    async def stop(self) -> None:
        """停止应用"""
        logger.info("application_stopping")

        # 停止 WebSocket 服务器
        await self.ws_server.stop()

        # 停止所有 Workers
        for worker in self.workers:
            await worker.stop()

        # 关闭 MCP 管理器
        from services.agent.mcp import get_mcp_manager
        mcp_manager = get_mcp_manager()
        await mcp_manager.shutdown()

        # 关闭 Provider 维护的 HTTP 客户端
        await ProviderRegistry.shutdown()

        logger.info("application_stopped")

    def handle_shutdown(self, sig: Any) -> None:
        """处理关闭信号"""
        logger.info("shutdown_signal_received", signal=sig)
        self._shutdown_event.set()


async def run_application() -> None:
    """运行应用主逻辑"""
    # 加载设置
    settings = get_settings()

    # 配置日志
    setup_logging(
        log_level=settings.log_level,
        enable_file_logging=settings.enable_file_logging,
        enable_ws_raw_log=settings.enable_ws_raw_log,
        enable_llm_raw_log=settings.enable_llm_raw_log,
    )

    # 创建应用
    app = Application()

    # 注册信号处理器
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda s=sig: app.handle_shutdown(s))
        except NotImplementedError:
            signal.signal(sig, lambda *_: app.handle_shutdown(sig))

    try:
        # 启动应用
        await app.start()
    except KeyboardInterrupt:
        logger.info("keyboard_interrupt")
    finally:
        # 停止应用
        await app.stop()


@click.group()
@click.version_option(version="2.2.0", prog_name="mcbe-AI-agent")
def cli():
    """MCBE AI Agent - Minecraft Bedrock Edition AI 聊天服务器"""
    pass


@cli.command()
@click.option("--host", default=None, help="服务器地址")
@click.option("--port", default=None, type=int, help="服务器端口")
@click.option("--log-level", default=None, help="日志级别 (DEBUG/INFO/WARNING/ERROR)")
@click.option("--dev", is_flag=True, default=False, help="启用开发模式 (跳过身份验证，仅用于本地调试)")
def serve(host: str | None, port: int | None, log_level: str | None, dev: bool):
    """启动 WebSocket 服务器"""
    # 加载设置
    settings = get_settings()

    # 覆盖配置
    if host:
        settings.host = host
    if port:
        settings.port = port
    if log_level:
        settings.log_level = log_level  # type: ignore
    if dev:
        settings.dev_mode = True

    # 开发模式警告
    if settings.dev_mode:
        click.echo("⚠️  警告: 开发模式已启用 - 身份验证已跳过，仅用于本地开发调试！\n")

    # 运行应用
    try:
        asyncio.run(run_application())
    except KeyboardInterrupt:
        click.echo("\n服务器已停止")


@cli.command()
def info():
    """显示配置信息"""
    settings = get_settings()

    click.echo("=== MCBE AI Agent 配置 ===\n")
    click.echo(f"服务器地址: {settings.host}:{settings.port}")
    click.echo(f"默认 LLM: {settings.default_provider}")
    click.echo(f"Worker 数量: {settings.llm_worker_count}")
    click.echo(f"队列大小: {settings.queue_max_size}")
    click.echo(f"JWT 过期时间: {settings.jwt_expiration}s")
    click.echo(f"开发模式: {'已启用 ⚠️' if settings.dev_mode else '未启用'}")
    click.echo(f"\n可用的 LLM 提供商:")

    for provider in settings.list_available_providers():
        config = settings.get_provider_config(provider)
        status = "✓" if config.enabled else "✗"
        click.echo(f"  {status} {provider}: {config.model}")


@cli.command()
@click.argument("provider", type=click.Choice(["deepseek", "openai", "anthropic", "ollama"]))
def test_provider(provider: str):
    """测试 LLM 提供商连接"""
    import httpx
    from services.agent.providers import ProviderRegistry

    settings = get_settings()

    click.echo(f"正在测试 {provider} 连接...\n")

    try:
        config = settings.get_provider_config(provider)

        if not config.enabled:
            click.echo(f"❌ {provider} 未配置或未启用", err=True)
            sys.exit(1)

        # 创建模型
        model = ProviderRegistry.get_model(config)

        click.echo(f"✓ 提供商: {config.name}")
        click.echo(f"✓ 模型: {config.model}")
        click.echo(f"✓ Base URL: {config.base_url or 'default'}")
        click.echo(f"\n✅ {provider} 配置正确")

    except Exception as e:
        click.echo(f"❌ 测试失败: {e}", err=True)
        sys.exit(1)


@cli.command()

def init():
    """初始化配置文件"""
    env_file = Path(".env")
    env_example = Path(".env.example")

    if env_file.exists():
        click.confirm("配置文件已存在，是否覆盖?", abort=True)

    if not env_example.exists():
        click.echo(f"❌ 找不到模板文件: {env_example.absolute()}", err=True)
        sys.exit(1)

    try:
        # 复制 .env.example 到 .env
        env_content = env_example.read_text(encoding="utf-8")
        env_file.write_text(env_content, encoding="utf-8")
        click.echo(f"✅ 配置文件已创建: {env_file.absolute()}")
        click.echo("\n请编辑 .env 文件并填入您的 API 密钥")
    except Exception as e:
        click.echo(f"❌ 复制配置文件失败: {e}", err=True)
        sys.exit(1)


@cli.group()
def mcp():
    """MCP 服务器管理"""
    pass


@mcp.command("list")
def mcp_list():
    """列出所有 MCP 服务器及其状态"""
    settings = get_settings()

    click.echo("=== MCP 服务器列表 ===\n")

    if not settings.mcp.enabled:
        click.echo("⚠️  MCP 功能未启用")
        click.echo("提示: 设置 MCP_ENABLED=true 启用 MCP")
        return

    if not settings.mcp.servers:
        click.echo("⚠️  未配置任何 MCP 服务器")
        click.echo("提示: 通过 MCP_SERVERS 环境变量配置服务器")
        return

    click.echo(f"MCP 状态: 已启用")
    click.echo(f"服务器数量: {len(settings.mcp.servers)}\n")

    for server_name, config in settings.mcp.servers.items():
        click.echo(f"📦 {server_name}")

        if config.url:
            click.echo(f"   模式: HTTP ({config.url})")
        elif config.command:
            cmd_str = f"{config.command} {' '.join(config.args)}" if config.args else config.command
            click.echo(f"   模式: Stdio")
            click.echo(f"   命令: {cmd_str}")
        else:
            click.echo(f"   模式: 未配置")

        click.echo(f"   超时: {config.timeout}s")

        if config.env:
            click.echo(f"   环境变量: {list(config.env.keys())}")

        click.echo()


@mcp.command("status")
def mcp_status():
    """显示 MCP 服务器的详细状态（需要运行中的服务）"""
    settings = get_settings()

    click.echo("=== MCP 服务状态 ===\n")

    if not settings.mcp.enabled:
        click.echo("❌ MCP 功能未启用")
        return

    # 尝试获取运行中的 MCP 管理器状态
    try:
        from services.agent.mcp import get_mcp_manager, MCPConnectionStatus

        manager = get_mcp_manager()

        if not manager.is_initialized:
            click.echo("⚠️  MCP 管理器尚未初始化")
            click.echo("提示: 启动服务后可查看实时状态")
            return

        status = manager.get_status_summary()

        click.echo(f"已初始化: {'是' if status['initialized'] else '否'}")
        click.echo(f"活跃服务器: {status['active_servers']}/{status['total_servers']}\n")

        for server_name, server_info in status["servers"].items():
            server_status = server_info["status"]
            status_icon = {
                MCPConnectionStatus.PENDING.value: "⏳",
                MCPConnectionStatus.ACTIVE.value: "✅",
                MCPConnectionStatus.ERROR.value: "❌",
                MCPConnectionStatus.DISABLED.value: "⛔",
            }.get(server_status, "❓")

            click.echo(f"{status_icon} {server_name}")
            click.echo(f"   状态: {server_status}")

            if server_info.get("last_error"):
                click.echo(f"   错误: {server_info['last_error']}")

            if server_info.get("last_run_time"):
                click.echo(f"   上次运行: {server_info['last_run_time']}")

            if server_info.get("last_run_success") is not None:
                result = "成功" if server_info["last_run_success"] else "失败"
                click.echo(f"   运行结果: {result}")

            click.echo()

    except Exception as e:
        click.echo(f"❌ 获取状态失败: {e}", err=True)


@mcp.command("test")
@click.argument("server_name", required=False)
def mcp_test(server_name: str | None):
    """测试 MCP 服务器配置"""
    settings = get_settings()

    click.echo("=== MCP 配置测试 ===\n")

    if not settings.mcp.enabled:
        click.echo("❌ MCP 功能未启用")
        return

    if not settings.mcp.servers:
        click.echo("❌ 未配置任何 MCP 服务器")
        return

    async def _test_config():
        from services.agent.mcp import get_mcp_manager, MCPConnectionStatus

        manager = get_mcp_manager()

        # 如果未初始化，先初始化
        if not manager.is_initialized:
            click.echo("正在初始化 MCP 管理器...\n")
            await manager.initialize()

        if server_name:
            # 测试指定服务器
            if server_name not in manager.servers:
                click.echo(f"❌ 未找到服务器: {server_name}")
                return

            info = manager.servers[server_name]
            click.echo(f"服务器: {server_name}")
            click.echo(f"状态: {info.status.value}")

            if info.status == MCPConnectionStatus.DISABLED:
                click.echo(f"❌ 配置无效")
                if info.last_error:
                    click.echo(f"   错误: {info.last_error}")
            else:
                click.echo(f"✅ 配置有效")

                # 显示配置详情
                if info.config.url:
                    click.echo(f"   模式: HTTP ({info.config.url})")
                elif info.config.command:
                    cmd = f"{info.config.command} {' '.join(info.config.args)}" if info.config.args else info.config.command
                    click.echo(f"   模式: Stdio ({cmd})")

        else:
            # 测试所有服务器
            click.echo("检查所有 MCP 服务器配置...\n")

            for name, info in manager.servers.items():
                if info.status == MCPConnectionStatus.DISABLED:
                    click.echo(f"⛔ {name}: 配置无效")
                    if info.last_error:
                        click.echo(f"   错误: {info.last_error}")
                else:
                    click.echo(f"✅ {name}: 配置有效")

            active_count = sum(
                1 for info in manager.servers.values()
                if info.status != MCPConnectionStatus.DISABLED
            )
            click.echo(f"\n活跃服务器: {active_count}/{len(manager.servers)}")

        await manager.shutdown()

    try:
        asyncio.run(_test_config())
    except KeyboardInterrupt:
        click.echo("\n测试已取消")


def main():
    """CLI 入口点"""
    cli()


if __name__ == "__main__":
    main()
