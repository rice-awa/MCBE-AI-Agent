"""命令行接口 - 应用主入口"""

import asyncio
import json
import signal
import sys
from pathlib import Path
from typing import Any

import click

from config.settings import get_settings
from config.logging import setup_logging, get_logger
from core.queue import MessageBroker
from services.agent.worker import AgentWorker
from services.agent.runtime import get_agent_runtime
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
            self.broker,
            self.settings,
            self.jwt_handler,
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

        # 注入流控中间件运行时配置
        from services.websocket.flow_control import FlowControlMiddleware
        FlowControlMiddleware.configure(
            max_content_length=self.settings.max_chunk_content_length,
            sentence_mode=self.settings.chunk_sentence_mode,
        )

        agent_runtime = get_agent_runtime()
        mcp_connected = await agent_runtime.initialize(self.settings)
        mcp_status = agent_runtime.get_mcp_manager(self.settings).get_status_summary()
        logger.info(
            "agent_runtime_initialized",
            mcp_enabled=mcp_status["enabled"],
            mcp_connected=mcp_status["active_servers"],
            mcp_total=mcp_status["total_servers"],
            mcp_has_active_server=mcp_connected,
            mcp_toolsets_count=len(agent_runtime.get_agent_manager().mcp_toolsets),
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

        # 关闭 Agent runtime 维护的 MCP 和 Provider 资源
        await get_agent_runtime().shutdown()

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
    try:
        settings = get_settings()
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(
            f"配置未完成: {exc}\n请先运行 python cli.py init，编辑 .env 和 config.json 后再启动服务。"
        )

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
    config_file = Path("config.json")
    config_example = Path("config.example.json")

    if env_file.exists():
        click.confirm(".env 已存在，是否覆盖?", abort=True)
    if config_file.exists():
        click.confirm("config.json 已存在，是否覆盖?", abort=True)

    if not env_example.exists():
        click.echo(f"❌ 找不到模板文件: {env_example.absolute()}", err=True)
        sys.exit(1)
    if not config_example.exists():
        click.echo(f"❌ 找不到模板文件: {config_example.absolute()}", err=True)
        sys.exit(1)

    try:
        env_file.write_text(env_example.read_text(encoding="utf-8"), encoding="utf-8")
        config_file.write_text(config_example.read_text(encoding="utf-8"), encoding="utf-8")
        click.echo(f"✅ 敏感配置文件已创建: {env_file.absolute()}")
        click.echo(f"✅ 应用配置文件已创建: {config_file.absolute()}")
        click.echo("\n请编辑 .env 填入密钥，并按需编辑 config.json 调整普通配置")
    except Exception as e:
        click.echo(f"❌ 创建配置文件失败: {e}", err=True)
        sys.exit(1)


@cli.group("runtime-harness")
def runtime_harness():
    """运行时 Harness 工具。"""
    pass


@runtime_harness.command("analyze")
@click.option("--recent", default=None, type=click.IntRange(min=1), help="分析最近 N 条审计记录")
@click.option("--json", "json_output", is_flag=True, default=False, help="输出机器可读 JSON")
@click.option("--no-llm", is_flag=True, default=False, help="仅输出规则聚合建议")
def runtime_harness_analyze(recent: int | None, json_output: bool, no_llm: bool):
    """分析运行时 Harness 审计记录并给出反馈建议。"""
    from services.agent.harness.analyze import analyze_records, apply_llm_suggestions, read_recent_records

    settings = get_settings()
    recent_count = recent if recent is not None else settings.runtime_harness_audit_max_records
    records = read_recent_records(settings.runtime_harness_audit_path, recent_count)
    analysis = analyze_records(records)
    analysis["recent"] = recent_count
    analysis["audit_path"] = settings.runtime_harness_audit_path
    if not no_llm:
        analysis = asyncio.run(apply_llm_suggestions(analysis, settings))

    if json_output:
        click.echo(json.dumps(analysis, ensure_ascii=False, sort_keys=True))
        return

    _echo_runtime_harness_analysis(analysis, no_llm=no_llm)


def _echo_runtime_harness_analysis(analysis: dict[str, Any], *, no_llm: bool) -> None:
    totals = analysis["totals"]
    click.echo("=== Runtime Harness 审计分析 ===")
    click.echo(f"审计文件: {analysis['audit_path']}")
    click.echo(f"分析范围: 最近 {analysis['recent']} 条")
    click.echo(f"总调用: {totals['calls']}")
    click.echo(f"失败率: {analysis['failure_rate']:.2%} ({totals['failures']} 次失败)")
    click.echo(f"平均耗时: {analysis['average_duration_ms']:.2f} ms")
    click.echo(f"风险分布: {_format_distribution(analysis['risk_distribution'])}")

    click.echo("\n重点问题工具:")
    if analysis["top_issue_tools"]:
        for issue in analysis["top_issue_tools"]:
            reasons = ", ".join(issue["reasons"])
            click.echo(
                f"- {issue['tool_name']}: 调用 {issue['calls']}，"
                f"失败率 {issue['failure_rate']:.2%}，平均 {issue['average_duration_ms']:.2f} ms，"
                f"原因 {reasons}"
            )
    else:
        click.echo("- 暂无明显问题工具")

    click.echo("\n反馈建议:")
    for suggestion in analysis["suggestions"]:
        click.echo(f"- {suggestion}")
    if no_llm:
        click.echo("- 已按 --no-llm 跳过 LLM 建议。")
    elif analysis.get("llm_suggestions_used"):
        click.echo("- 已使用默认 Provider 生成 LLM 建议。")
    else:
        fallback = analysis.get("llm_suggestions_fallback")
        if fallback:
            click.echo(f"- LLM 建议生成失败，已回退规则模板输出: {fallback}")
        else:
            click.echo("- 未启用 LLM 建议，本次使用规则模板输出。")


def _format_distribution(distribution: dict[str, int]) -> str:
    if not distribution:
        return "无"
    return ", ".join(f"{risk}={count}" for risk, count in sorted(distribution.items()))


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
        click.echo("提示: 在 config.json 中设置 mcp.enabled=true 启用 MCP")
        return

    if not settings.mcp.servers:
        click.echo("⚠️  未配置任何 MCP 服务器")
        click.echo("提示: 在 config.json 的 mcp.servers 中配置服务器")
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

        manager = get_mcp_manager(settings)

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

        manager = get_mcp_manager(settings)

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
