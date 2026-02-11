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
            version="2.0.0",
            host=self.settings.host,
            port=self.settings.port,
            default_provider=self.settings.default_provider,
            worker_count=self.settings.llm_worker_count,
        )

        # 预热 LLM 模型
        await ProviderRegistry.warmup_models(self.settings)

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
@click.version_option(version="2.0.0", prog_name="mcbe-AI-agent")
def cli():
    """MCBE AI Agent - Minecraft Bedrock Edition AI 聊天服务器"""
    pass


@cli.command()
@click.option("--host", default=None, help="服务器地址")
@click.option("--port", default=None, type=int, help="服务器端口")
@click.option("--log-level", default=None, help="日志级别 (DEBUG/INFO/WARNING/ERROR)")
def serve(host: str | None, port: int | None, log_level: str | None):
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


def main():
    """CLI 入口点"""
    cli()


if __name__ == "__main__":
    main()
