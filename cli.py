"""命令行接口"""

import asyncio
import sys
from pathlib import Path

import click

from config.settings import get_settings
from main import main as app_main


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
        asyncio.run(app_main())
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

    if env_file.exists():
        click.confirm("配置文件已存在，是否覆盖?", abort=True)

    env_template = """# MCBE AI Agent 配置

# 服务器配置
HOST=0.0.0.0
PORT=8080

# 认证配置
SECRET_KEY=change-me-in-production
WEBSOCKET_PASSWORD=123456

# DeepSeek 配置
DEEPSEEK_API_KEY=your-api-key-here

# OpenAI 配置 (可选)
# OPENAI_API_KEY=your-api-key-here

# Anthropic 配置 (可选)
# ANTHROPIC_API_KEY=your-api-key-here

# 默认 LLM 提供商
DEFAULT_PROVIDER=deepseek

# Worker 配置
LLM_WORKER_COUNT=2
QUEUE_MAX_SIZE=100

# 日志配置
LOG_LEVEL=INFO
ENABLE_FILE_LOGGING=true
"""

    env_file.write_text(env_template, encoding="utf-8")
    click.echo(f"✅ 配置文件已创建: {env_file.absolute()}")
    click.echo("\n请编辑 .env 文件并填入您的 API 密钥")


def main():
    """CLI 入口点"""
    cli()


if __name__ == "__main__":
    main()
