import asyncio
from types import SimpleNamespace
from uuid import uuid4

from config.settings import LLMProviderConfig, Settings
from core.queue import MessageBroker
from models.agent import StreamEvent
from models.messages import ChatRequest
from services.agent.core import ChatAgentManager
from services.agent.providers import ProviderRegistry, RuntimeAdapterRegistry
from services.agent.runtime import AgentRuntime, get_agent_runtime, set_agent_runtime


def provider_config(**overrides):
    values = {
        "name": "deepseek",
        "api_key": "test-key",
        "base_url": None,
        "model": "deepseek-chat",
        "enabled": True,
        "timeout": 60,
    }
    values.update(overrides)
    return LLMProviderConfig(**values)


def test_runtime_adapter_registry_caches_models_per_config(monkeypatch):
    registry = RuntimeAdapterRegistry()
    created = []

    def fake_create(config):
        model = object()
        created.append((config, model))
        return model

    monkeypatch.setattr(registry, "_create_deepseek_model", fake_create)

    config = provider_config()
    first = registry.get_model(config)
    second = registry.get_model(config)

    assert first is second
    assert len(created) == 1


def test_runtime_adapter_registry_separates_models_by_api_key(monkeypatch):
    registry = RuntimeAdapterRegistry()
    created = []

    def fake_create(config):
        model = object()
        created.append((config.api_key, model))
        return model

    monkeypatch.setattr(registry, "_create_deepseek_model", fake_create)

    first = registry.get_model(provider_config(api_key="old-key"))
    second = registry.get_model(provider_config(api_key="new-key"))

    assert first is not second
    assert [key for key, _model in created] == ["old-key", "new-key"]


def test_runtime_adapter_registry_separates_http_clients_by_api_key(monkeypatch):
    registry = RuntimeAdapterRegistry()
    created = []

    def fake_create(config, provider_name):
        client = object()
        created.append((config.api_key, provider_name, client))
        return client

    monkeypatch.setattr(registry, "_create_llm_http_client", fake_create)

    first = registry._get_or_create_http_client(provider_config(api_key="old-key"), "deepseek")
    second = registry._get_or_create_http_client(provider_config(api_key="new-key"), "deepseek")

    assert first is not second
    assert [(key, provider) for key, provider, _client in created] == [
        ("old-key", "deepseek"),
        ("new-key", "deepseek"),
    ]


def test_runtime_adapter_registry_reuses_http_clients_per_config(monkeypatch):
    registry = RuntimeAdapterRegistry()
    created = []

    def fake_create(config, provider_name):
        client = object()
        created.append((config, provider_name, client))
        return client

    monkeypatch.setattr(registry, "_create_llm_http_client", fake_create)

    config = provider_config()
    first = registry._get_or_create_http_client(config, "deepseek")
    second = registry._get_or_create_http_client(config, "deepseek")

    assert first is second
    assert len(created) == 1



def test_worker_stream_chat_does_not_pass_primary_agent(monkeypatch):
    async def _run() -> None:
        broker = MessageBroker()
        connection_id = uuid4()
        broker.register_connection(connection_id)
        worker = __import__("services.agent.worker", fromlist=["AgentWorker"]).AgentWorker(
            broker,
            Settings(default_provider="ollama", dev_mode=True),
        )
        seen_kwargs = {}

        async def fake_stream_chat(*_args, **kwargs):
            seen_kwargs.update(kwargs)
            yield StreamEvent(
                event_type="content",
                content="ok",
                sequence=0,
                metadata={"is_complete": True, "all_messages": []},
            )

        class FakeAgentManager:
            def get_agent(self):
                raise AssertionError("worker should not request the primary agent")

        monkeypatch.setattr("services.agent.worker.stream_chat", fake_stream_chat)
        monkeypatch.setattr("services.agent.providers.ProviderRegistry.get_model", lambda _config: object())
        monkeypatch.setattr("services.agent.core.get_agent_manager", lambda: FakeAgentManager())
        monkeypatch.setattr("services.agent.mcp.get_mcp_manager", lambda _settings: None)
        monkeypatch.setattr(
            worker,
            "_schedule_title_generation",
            lambda *_args, **_kwargs: asyncio.sleep(0),
        )

        await worker._process_request_locked(
            ChatRequest(
                connection_id=connection_id,
                content="hello",
                player_name="alice",
            ),
            connection_id,
        )

        assert "agent" not in seen_kwargs

    asyncio.run(_run())


def test_chat_agent_manager_uses_fallback_after_mcp_failure(monkeypatch):
    manager = ChatAgentManager()
    created = []

    def fake_create_agent(toolsets=None):
        agent = {"toolsets": toolsets}
        created.append(agent)
        return agent

    monkeypatch.setattr(manager, "_create_agent", fake_create_agent)

    asyncio.run(manager.initialize(Settings(default_provider="ollama", dev_mode=True), mcp_toolsets=["toolset"]))
    primary = manager.get_agent()
    manager.mark_mcp_failed()
    active = manager.get_active_agent(connection_id="conn", mode="stream")

    assert active is not primary
    assert active["toolsets"] is None


def test_chat_agent_manager_refresh_mcp_toolsets_restores_primary_after_failure(monkeypatch):
    manager = ChatAgentManager()
    created = []

    def fake_create_agent(toolsets=None):
        agent = {"toolsets": toolsets}
        created.append(agent)
        return agent

    monkeypatch.setattr(manager, "_create_agent", fake_create_agent)

    asyncio.run(manager.initialize(Settings(default_provider="ollama", dev_mode=True), mcp_toolsets=["old-toolset"]))
    manager.mark_mcp_failed()
    fallback = manager.get_active_agent(connection_id="conn", mode="stream")

    manager.refresh_mcp_toolsets(["new-toolset"])
    active = manager.get_active_agent(connection_id="conn", mode="stream")

    assert fallback["toolsets"] is None
    assert manager.is_mcp_available is True
    assert manager.mcp_toolsets == ["new-toolset"]
    assert active is manager.get_agent()
    assert active is not fallback
    assert active["toolsets"] == ["new-toolset"]


def test_runtime_adapter_registry_shutdown_closes_clients_and_clears_caches(monkeypatch):
    registry = RuntimeAdapterRegistry()
    closed = []

    class FakeClient:
        async def aclose(self):
            closed.append("closed")

    fake_model = object()
    registry._http_client_cache["client"] = FakeClient()
    registry._model_cache["model"] = fake_model

    asyncio.run(registry.shutdown())

    assert closed == ["closed"]
    assert registry._http_client_cache == {}
    assert registry._model_cache == {}


def test_provider_registry_facade_delegates_to_runtime_adapter():
    original = ProviderRegistry.get_runtime_adapters()
    runtime_adapters = SimpleNamespace(
        get_model=lambda config: ("model", config.name),
        list_providers=lambda: ["fake"],
        warmup_models=lambda settings: None,
        shutdown=lambda: None,
        get_model_string=lambda config: f"fake:{config.model}",
    )
    try:
        ProviderRegistry.set_runtime_adapters(runtime_adapters)  # type: ignore[arg-type]
        config = provider_config(name="fake", model="fake-model")

        assert ProviderRegistry.get_model(config) == ("model", "fake")
        assert ProviderRegistry.list_providers() == ["fake"]
        assert ProviderRegistry.get_model_string(config) == "fake:fake-model"
    finally:
        ProviderRegistry.set_runtime_adapters(original)


def test_agent_runtime_owns_provider_registry_facade():
    original = get_agent_runtime()
    runtime_adapters = SimpleNamespace(
        get_model=lambda config: ("runtime-model", config.name),
        list_providers=lambda: ["runtime"],
        warmup_models=lambda settings: None,
        shutdown=lambda: None,
        get_model_string=lambda config: f"runtime:{config.model}",
    )
    runtime = AgentRuntime(runtime_adapters=runtime_adapters)
    try:
        set_agent_runtime(runtime)
        config = provider_config(name="runtime", model="runtime-model")

        assert ProviderRegistry.get_runtime_adapters() is runtime_adapters
        assert ProviderRegistry.get_model(config) == ("runtime-model", "runtime")
        assert ProviderRegistry.list_providers() == ["runtime"]
        assert ProviderRegistry.get_model_string(config) == "runtime:runtime-model"
    finally:
        set_agent_runtime(original)


def test_agent_runtime_caches_chat_agent_manager(monkeypatch):
    created = []

    class FakeChatAgentManager:
        def __init__(self):
            created.append(self)

    import services.agent.core as agent_core

    monkeypatch.setattr(agent_core, "ChatAgentManager", FakeChatAgentManager)

    runtime = AgentRuntime()
    first = runtime.get_agent_manager()
    second = runtime.get_agent_manager()

    assert first is second
    assert created == [first]


def test_agent_runtime_initializes_adapters_mcp_and_agent_manager():
    calls = []
    settings = SimpleNamespace()

    class FakeRuntimeAdapters:
        async def warmup_models(self, received_settings):
            calls.append(("warmup", received_settings))

    class FakeMCPManager:
        async def initialize(self):
            calls.append(("mcp_initialize",))
            return True

        def get_toolsets_for_agent(self):
            calls.append(("mcp_toolsets",))
            return ["toolset"]

    class FakeChatAgentManager:
        async def initialize(self, received_settings, mcp_toolsets):
            calls.append(("agent_initialize", received_settings, mcp_toolsets))

    runtime = AgentRuntime(
        runtime_adapters=FakeRuntimeAdapters(),
        mcp_manager=FakeMCPManager(),
        chat_agent_manager=FakeChatAgentManager(),
    )

    assert asyncio.run(runtime.initialize(settings)) is True
    assert calls == [
        ("warmup", settings),
        ("mcp_initialize",),
        ("mcp_toolsets",),
        ("agent_initialize", settings, ["toolset"]),
    ]


def test_agent_runtime_refresh_mcp_tools_passes_latest_toolsets():
    calls = []
    settings = SimpleNamespace()

    class FakeMCPManager:
        def get_toolsets_for_agent(self):
            calls.append("mcp_toolsets")
            return ["latest-toolset"]

    class FakeChatAgentManager:
        def refresh_mcp_toolsets(self, mcp_toolsets):
            calls.append(("agent_refresh", mcp_toolsets))

    runtime = AgentRuntime(
        mcp_manager=FakeMCPManager(),
        chat_agent_manager=FakeChatAgentManager(),
    )

    runtime.refresh_mcp_tools(settings)

    assert calls == ["mcp_toolsets", ("agent_refresh", ["latest-toolset"])]



def test_agent_runtime_shutdown_resets_managers_for_reinitialize():
    calls = []
    settings = SimpleNamespace()

    class FakeRuntimeAdapters:
        async def warmup_models(self, received_settings):
            calls.append(("warmup", received_settings))

        async def shutdown(self):
            calls.append("adapters_shutdown")

    class FakeMCPManager:
        async def initialize(self):
            calls.append("mcp_initialize")
            return True

        def get_toolsets_for_agent(self):
            calls.append("mcp_toolsets")
            return ["toolset"]

        async def shutdown(self):
            calls.append("mcp_shutdown")

    class FakeChatAgentManager:
        async def initialize(self, received_settings, mcp_toolsets):
            calls.append(("agent_initialize", received_settings, mcp_toolsets))

        def reset(self):
            calls.append("agent_reset")

    runtime = AgentRuntime(
        runtime_adapters=FakeRuntimeAdapters(),
        mcp_manager=FakeMCPManager(),
        chat_agent_manager=FakeChatAgentManager(),
    )

    assert asyncio.run(runtime.initialize(settings)) is True
    asyncio.run(runtime.shutdown())

    assert "mcp_shutdown" in calls
    assert "agent_reset" in calls
    assert runtime.mcp_manager is None
    assert runtime.chat_agent_manager is None


def test_agent_runtime_shutdown_closes_mcp_before_adapters():
    calls = []

    class FakeRuntimeAdapters:
        async def shutdown(self):
            calls.append("adapters_shutdown")

    class FakeMCPManager:
        async def shutdown(self):
            calls.append("mcp_shutdown")

    runtime = AgentRuntime(
        runtime_adapters=FakeRuntimeAdapters(),
        mcp_manager=FakeMCPManager(),
    )

    asyncio.run(runtime.shutdown())

    assert calls == ["mcp_shutdown", "adapters_shutdown"]
