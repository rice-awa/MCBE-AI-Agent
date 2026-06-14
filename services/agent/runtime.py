"""Agent runtime lifecycle ownership."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from config.settings import Settings
from services.agent.providers import RuntimeAdapterRegistry

if TYPE_CHECKING:
    from services.agent.core import ChatAgentManager


@dataclass
class AgentRuntime:
    """Owns process runtime components for agent execution."""

    runtime_adapters: Any = field(default_factory=RuntimeAdapterRegistry)
    chat_agent_manager: ChatAgentManager | None = None

    def get_agent_manager(self) -> ChatAgentManager:
        if self.chat_agent_manager is None:
            from services.agent.core import ChatAgentManager

            self.chat_agent_manager = ChatAgentManager()
        return self.chat_agent_manager

    async def warmup_models(self, settings: Settings) -> None:
        await self.runtime_adapters.warmup_models(settings)

    async def shutdown(self) -> None:
        await self.runtime_adapters.shutdown()


_default_runtime: AgentRuntime | None = None


def get_agent_runtime() -> AgentRuntime:
    global _default_runtime
    if _default_runtime is None:
        _default_runtime = AgentRuntime()
    return _default_runtime


def set_agent_runtime(runtime: AgentRuntime) -> None:
    global _default_runtime
    _default_runtime = runtime
