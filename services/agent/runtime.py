"""Agent runtime lifecycle ownership."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from config.logging import get_logger
from config.settings import Settings
from services.agent.harness.approvals import PendingApprovalStore
from services.agent.model_metadata import ModelMetadataService
from services.agent.providers import RuntimeAdapterRegistry

if TYPE_CHECKING:
    from services.agent.core import ChatAgentManager
    from services.agent.mcp import MCPManager

logger = get_logger(__name__)


@dataclass
class AgentRuntime:
    """Owns process runtime components for agent execution."""

    runtime_adapters: Any = field(default_factory=RuntimeAdapterRegistry)
    chat_agent_manager: ChatAgentManager | None = None
    mcp_manager: MCPManager | None = None
    model_metadata_service: ModelMetadataService | None = None
    pending_approvals: PendingApprovalStore = field(default_factory=PendingApprovalStore)

    def get_agent_manager(self) -> ChatAgentManager:
        if self.chat_agent_manager is None:
            from services.agent.core import ChatAgentManager

            self.chat_agent_manager = ChatAgentManager()
        return self.chat_agent_manager

    def get_mcp_manager(self, settings: Settings | None = None) -> MCPManager:
        if self.mcp_manager is None:
            from services.agent.mcp import MCPManager

            self.mcp_manager = MCPManager(settings)
        return self.mcp_manager

    def get_pending_approval_store(self, settings: Settings | None = None) -> PendingApprovalStore:
        ttl = float(getattr(settings, "approval_ttl", 120.0) or 120.0) if settings is not None else 120.0
        # 惰性对齐 TTL（不丢已有条目）
        if abs(self.pending_approvals._default_ttl - ttl) > 1e-6:  # noqa: SLF001
            self.pending_approvals._default_ttl = ttl  # noqa: SLF001
        return self.pending_approvals

    async def initialize(self, settings: Settings) -> bool:
        await self.initialize_model_metadata(settings)
        await self.warmup_models(settings)
        mcp_manager = self.get_mcp_manager(settings)
        mcp_connected = await mcp_manager.initialize()
        agent_manager = self.get_agent_manager()
        await agent_manager.initialize(
            settings,
            mcp_toolsets=mcp_manager.get_healthy_toolsets(),
        )
        self.get_pending_approval_store(settings)
        return mcp_connected

    async def initialize_model_metadata(self, settings: Settings) -> None:
        """启动期加载 models.dev 元数据并附加到 settings，须在 warmup_models 之前完成。"""
        try:
            service = ModelMetadataService(settings.model_metadata)
            await service.initialize()
            settings.attach_model_metadata_cache(service.cache)
            self.model_metadata_service = service
            logger.info(
                "model_metadata_status",
                enabled=settings.model_metadata.enabled,
                cached_model_count=len(service.cache.models),
                refresh_on_startup=settings.model_metadata.refresh_on_startup,
            )
        except Exception as e:
            logger.warning("model_metadata_init_failed", error=str(e))

    def get_model_metadata_service(self) -> ModelMetadataService | None:
        return self.model_metadata_service

    async def warmup_models(self, settings: Settings) -> None:
        await self.runtime_adapters.warmup_models(settings)

    def refresh_mcp_tools(self, settings: Settings) -> None:
        """Refresh ChatAgentManager with the latest healthy MCP toolsets after MCP reload."""
        mcp_manager = self.get_mcp_manager(settings)
        agent_manager = self.get_agent_manager()
        agent_manager.refresh_mcp_toolsets(mcp_manager.get_healthy_toolsets())

    async def shutdown(self) -> None:
        if self.mcp_manager is not None:
            await self.mcp_manager.shutdown()
            self.mcp_manager = None
        if self.chat_agent_manager is not None:
            reset = getattr(self.chat_agent_manager, "reset", None)
            if reset is not None:
                reset()
            self.chat_agent_manager = None
        self.model_metadata_service = None
        self.pending_approvals.clear()
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
