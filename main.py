"""应用主入口"""

import asyncio
import signal
from typing import Any

from config.settings import get_settings
from config.logging import setup_logging, get_logger
from core.queue import MessageBroker
from services.agent.worker import AgentWorker
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

        logger.info("application_stopped")

    def handle_shutdown(self, sig: Any) -> None:
        """处理关闭信号"""
        logger.info("shutdown_signal_received", signal=sig)
        self._shutdown_event.set()


async def main() -> None:
    """主函数"""
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


if __name__ == "__main__":
    asyncio.run(main())
