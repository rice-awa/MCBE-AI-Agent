"""MCBE AI Agent - 现代化异步 AI Agent 框架"""

__version__ = "2.0.0"

from config.settings import Settings, get_settings
from core.queue import MessageBroker
from services.agent.core import chat_agent
from services.websocket.server import WebSocketServer

__all__ = [
    "Settings",
    "get_settings",
    "MessageBroker",
    "chat_agent",
    "WebSocketServer",
    "__version__",
]
