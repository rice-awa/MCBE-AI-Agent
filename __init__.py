"""MCBE AI Agent - 现代化异步 AI Agent 框架"""

from _version import __version__

from config.settings import Settings, get_settings
from core.queue import MessageBroker
from services.agent.core import chat_agent
from services.gateway.server import HostGatewayServer

__all__ = [
    "Settings",
    "get_settings",
    "MessageBroker",
    "chat_agent",
    "HostGatewayServer",
    "__version__",
]
