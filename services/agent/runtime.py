"""Agent runtime lifecycle ownership."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from config.logging import get_logger
from config.settings import Settings
from services.agent.harness.approvals import PendingApprovalStore
from services.agent.harness.audit import flush_audit_writer, start_audit_writer, stop_audit_writer
from services.agent.model_metadata import ModelMetadataService
from services.agent.providers import RuntimeAdapterRegistry

if TYPE_CHECKING:
    from core.conversation import ConversationManager
    from services.agent.core import ChatAgentManager
    from services.agent.mcp import MCPManager
    from services.agent.prompt import PromptManager

logger = get_logger(__name__)


async def _maybe_await(result: Any) -> Any:
    if inspect.isawaitable(result):
        return await result
    return result


@dataclass
class AgentRuntime:
    """Owns process runtime components for agent execution."""

    runtime_adapters: Any = field(default_factory=RuntimeAdapterRegistry)
    chat_agent_manager: ChatAgentManager | None = None
    mcp_manager: MCPManager | None = None
    model_metadata_service: ModelMetadataService | None = None
    prompt_manager: PromptManager | None = None
    conversation_manager: ConversationManager | None = None
    pending_approvals: PendingApprovalStore = field(default_factory=PendingApprovalStore)
    _audit_writer_started: bool = False
    _conversation_broker: Any = None
    _conversation_settings: Settings | None = None

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

    def get_prompt_manager(self) -> PromptManager:
        """Return the runtime-owned PromptManager, creating it lazily."""
        if self.prompt_manager is None:
            from services.agent.prompt import PromptManager

            self.prompt_manager = PromptManager()
        return self.prompt_manager

    def get_conversation_manager(
        self,
        broker: Any,
        settings: Settings | None = None,
    ) -> ConversationManager:
        """
        Return a ConversationManager owned by this runtime.

        Recreates the manager when broker or settings identity changes so
        stop/restart cannot reuse stale references.
        """
        from config.settings import get_settings
        from core.conversation import ConversationManager

        resolved_settings = settings or get_settings()
        if (
            self.conversation_manager is None
            or self._conversation_broker is not broker
            or self._conversation_settings is not resolved_settings
        ):
            self.conversation_manager = ConversationManager(broker, resolved_settings)
            self._conversation_broker = broker
            self._conversation_settings = resolved_settings
        return self.conversation_manager

    def get_pending_approval_store(self, settings: Settings | None = None) -> PendingApprovalStore:
        ttl = float(getattr(settings, "approval_ttl", 120.0) or 120.0) if settings is not None else 120.0
        # 惰性对齐 TTL（不丢已有条目）
        if abs(self.pending_approvals._default_ttl - ttl) > 1e-6:  # noqa: SLF001
            self.pending_approvals._default_ttl = ttl  # noqa: SLF001
        return self.pending_approvals

    async def initialize(self, settings: Settings) -> bool:
        # 审计 writer 生命周期由 runtime 持有
        try:
            start_audit_writer(settings)
            self._audit_writer_started = True
        except Exception as e:  # noqa: BLE001
            logger.warning("audit_writer_start_failed", error=str(e))
        await self.initialize_model_metadata(settings)
        await self.warmup_models(settings)
        mcp_manager = self.get_mcp_manager(settings)
        mcp_connected = await mcp_manager.initialize()
        agent_manager = self.get_agent_manager()
        await agent_manager.initialize(
            settings,
            mcp_toolsets=mcp_manager.get_healthy_toolsets(),
        )
        # Ensure prompt manager exists for the process lifetime.
        self.get_prompt_manager()
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

    async def flush_audit(self, timeout: float = 5.0) -> None:
        """刷新审计队列；失败只告警。"""
        try:
            flush_audit_writer(timeout=timeout)
        except Exception as e:  # noqa: BLE001
            logger.warning("audit_writer_flush_failed", error=str(e))

    def _clear_owned_managers(self) -> None:
        """Drop PromptManager / ConversationManager and broker/settings refs."""
        self.prompt_manager = None
        self.conversation_manager = None
        self._conversation_broker = None
        self._conversation_settings = None

    async def _shutdown_component(self, name: str, action) -> list[tuple[str, str]]:
        """Run one shutdown step; never raise. Returns error tuples for aggregation."""
        try:
            await _maybe_await(action())
            return []
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "runtime_shutdown_component_failed",
                component=name,
                error=str(e),
            )
            return [(name, str(e))]

    async def shutdown(self) -> None:
        """
        Aggregate process teardown.

        Each component is cleaned independently so one failure cannot block the rest.
        """
        errors: list[tuple[str, str]] = []

        # 1) MCP
        mcp_manager = self.mcp_manager
        self.mcp_manager = None
        if mcp_manager is not None:
            errors.extend(
                await self._shutdown_component(
                    "mcp",
                    mcp_manager.shutdown,
                )
            )

        # 2) Agent reset / close
        agent_manager = self.chat_agent_manager
        self.chat_agent_manager = None
        if agent_manager is not None:

            async def _reset_agent() -> None:
                reset = getattr(agent_manager, "reset", None)
                if reset is not None:
                    await _maybe_await(reset())
                close = getattr(agent_manager, "close", None)
                if close is not None:
                    await _maybe_await(close())

            errors.extend(await self._shutdown_component("agent", _reset_agent))

        # 3) Audit writer stop / flush (Task4a path folded in)
        async def _stop_audit() -> None:
            if self._audit_writer_started:
                stop_audit_writer()
            else:
                await self.flush_audit()

        try:
            errors.extend(await self._shutdown_component("audit", _stop_audit))
        finally:
            self._audit_writer_started = False

        # 4) Model metadata cleanup
        metadata_service = self.model_metadata_service
        self.model_metadata_service = None
        if metadata_service is not None:

            async def _close_metadata() -> None:
                closer = getattr(metadata_service, "close", None) or getattr(
                    metadata_service, "shutdown", None
                )
                if closer is not None:
                    await _maybe_await(closer())

            errors.extend(await self._shutdown_component("model_metadata", _close_metadata))

        # 5) Provider HTTP clients
        errors.extend(
            await self._shutdown_component(
                "providers",
                self.runtime_adapters.shutdown,
            )
        )

        # Clear owned session managers so restart cannot reuse stale broker/settings.
        self._clear_owned_managers()
        self.pending_approvals.clear()

        if errors:
            logger.warning(
                "runtime_shutdown_completed_with_errors",
                error_count=len(errors),
                components=[name for name, _ in errors],
                errors=[message for _, message in errors],
            )
        else:
            logger.info("runtime_shutdown_completed")


_default_runtime: AgentRuntime | None = None


def get_agent_runtime() -> AgentRuntime:
    global _default_runtime
    if _default_runtime is None:
        _default_runtime = AgentRuntime()
    return _default_runtime


def set_agent_runtime(runtime: AgentRuntime) -> None:
    global _default_runtime
    _default_runtime = runtime
