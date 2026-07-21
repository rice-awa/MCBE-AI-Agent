import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from config.settings import LLMProviderConfig, Settings
from core.queue import MessageBroker
from models.agent import StreamEvent
from models.messages import ChatRequest
from services.agent.core import ChatAgentManager
from services.agent.model_metadata import ModelMetadataCache
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

        def get_healthy_toolsets(self):
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
    assert runtime.prompt_manager is not None


def test_agent_runtime_refresh_mcp_tools_passes_latest_toolsets():
    calls = []
    settings = SimpleNamespace()

    class FakeMCPManager:
        def get_healthy_toolsets(self):
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

        def get_healthy_toolsets(self):
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
    runtime.prompt_manager = object()  # type: ignore[assignment]
    runtime.conversation_manager = object()  # type: ignore[assignment]
    runtime._conversation_broker = object()
    runtime._conversation_settings = settings  # type: ignore[assignment]

    assert asyncio.run(runtime.initialize(settings)) is True
    asyncio.run(runtime.shutdown())

    assert "mcp_shutdown" in calls
    assert "agent_reset" in calls
    assert "adapters_shutdown" in calls
    assert runtime.mcp_manager is None
    assert runtime.chat_agent_manager is None
    assert runtime.prompt_manager is None
    assert runtime.conversation_manager is None
    assert runtime._conversation_broker is None
    assert runtime._conversation_settings is None


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


def test_agent_runtime_owns_prompt_and_conversation_managers(monkeypatch):
    from core.conversation import ConversationManager
    from services.agent.prompt import PromptManager, get_prompt_manager
    from core.conversation import get_conversation_manager

    original = get_agent_runtime()
    runtime = AgentRuntime()
    broker = object()
    settings = Settings(default_provider="ollama", dev_mode=True)

    try:
        set_agent_runtime(runtime)

        prompt1 = runtime.get_prompt_manager()
        prompt2 = get_prompt_manager()
        assert isinstance(prompt1, PromptManager)
        assert prompt1 is prompt2
        assert runtime.prompt_manager is prompt1

        conv1 = runtime.get_conversation_manager(broker, settings)
        conv2 = get_conversation_manager(broker, settings)
        assert isinstance(conv1, ConversationManager)
        assert conv1 is conv2
        assert runtime.conversation_manager is conv1
        assert runtime._conversation_broker is broker
        assert runtime._conversation_settings is settings

        # New broker/settings identity must not reuse stale manager.
        other_broker = object()
        other_settings = Settings(default_provider="ollama", dev_mode=True)
        conv3 = runtime.get_conversation_manager(other_broker, other_settings)
        assert conv3 is not conv1
        assert runtime.conversation_manager is conv3
        assert runtime._conversation_broker is other_broker
        assert runtime._conversation_settings is other_settings
    finally:
        set_agent_runtime(original)


def test_agent_runtime_shutdown_aggregates_errors_and_continues(monkeypatch):
    """MCP shutdown failure must not prevent agent/audit/provider cleanup."""
    calls = []

    class FakeRuntimeAdapters:
        async def shutdown(self):
            calls.append("providers_close")

    class FailingMCPManager:
        async def shutdown(self):
            calls.append("mcp_shutdown")
            raise RuntimeError("mcp boom")

    class FakeChatAgentManager:
        def reset(self):
            calls.append("agent_reset")

    class FakeMetadataService:
        async def close(self):
            calls.append("metadata_close")

    runtime = AgentRuntime(
        runtime_adapters=FakeRuntimeAdapters(),
        mcp_manager=FailingMCPManager(),  # type: ignore[arg-type]
        chat_agent_manager=FakeChatAgentManager(),  # type: ignore[arg-type]
        model_metadata_service=FakeMetadataService(),  # type: ignore[arg-type]
    )
    runtime._audit_writer_started = True
    runtime.prompt_manager = object()  # type: ignore[assignment]
    runtime.conversation_manager = object()  # type: ignore[assignment]
    runtime._conversation_broker = object()
    runtime._conversation_settings = Settings(default_provider="ollama", dev_mode=True)

    def fake_stop_audit_writer(timeout: float = 5.0) -> None:
        calls.append("audit_stop")

    monkeypatch.setattr(
        "services.agent.runtime.stop_audit_writer",
        fake_stop_audit_writer,
    )

    asyncio.run(runtime.shutdown())

    assert calls == [
        "mcp_shutdown",
        "agent_reset",
        "audit_stop",
        "metadata_close",
        "providers_close",
    ]
    assert runtime.mcp_manager is None
    assert runtime.chat_agent_manager is None
    assert runtime.model_metadata_service is None
    assert runtime.prompt_manager is None
    assert runtime.conversation_manager is None
    assert runtime._audit_writer_started is False
    assert runtime._conversation_broker is None
    assert runtime._conversation_settings is None


# ---------------------------------------------------------------------------
# Task 4: 运行时接入模型元数据服务
# ---------------------------------------------------------------------------


def _metadata_namespace(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        enabled=False,
        refresh_on_startup=False,
        cache_path=tmp_path / "cache.json",
    )


def test_agent_runtime_initializes_model_metadata_before_warmup(tmp_path):
    calls = []

    class FakeSettings:
        def __init__(self):
            self.model_metadata = _metadata_namespace(tmp_path)

        def attach_model_metadata_cache(self, cache):
            calls.append("metadata_attach")

    settings = FakeSettings()

    class FakeRuntimeAdapters:
        async def warmup_models(self, received_settings):
            calls.append("warmup")

    class FakeMCPManager:
        async def initialize(self):
            calls.append("mcp_initialize")
            return True

        def get_healthy_toolsets(self):
            calls.append("mcp_toolsets")
            return ["toolset"]

    class FakeChatAgentManager:
        async def initialize(self, received_settings, mcp_toolsets):
            calls.append("agent_initialize")

    runtime = AgentRuntime(
        runtime_adapters=FakeRuntimeAdapters(),
        mcp_manager=FakeMCPManager(),
        chat_agent_manager=FakeChatAgentManager(),
    )

    assert asyncio.run(runtime.initialize(settings)) is True
    assert calls == [
        "metadata_attach",
        "warmup",
        "mcp_initialize",
        "mcp_toolsets",
        "agent_initialize",
    ]
    assert runtime.get_model_metadata_service() is not None


def test_agent_runtime_metadata_init_failure_does_not_break_init(tmp_path, monkeypatch):
    calls = []

    class FakeSettings:
        def __init__(self):
            self.model_metadata = _metadata_namespace(tmp_path)

        def attach_model_metadata_cache(self, cache):
            calls.append("metadata_attach")

    class FailingService:
        def __init__(self, config):
            calls.append("service_created")

        async def initialize(self):
            raise RuntimeError("metadata boom")

        @property
        def cache(self):
            return ModelMetadataCache()

    import services.agent.runtime as runtime_mod

    monkeypatch.setattr(runtime_mod, "ModelMetadataService", FailingService)

    settings = FakeSettings()

    class FakeRuntimeAdapters:
        async def warmup_models(self, received_settings):
            calls.append("warmup")

    class FakeMCPManager:
        async def initialize(self):
            calls.append("mcp_initialize")
            return True

        def get_healthy_toolsets(self):
            calls.append("mcp_toolsets")
            return ["toolset"]

    class FakeChatAgentManager:
        async def initialize(self, received_settings, mcp_toolsets):
            calls.append("agent_initialize")

    runtime = AgentRuntime(
        runtime_adapters=FakeRuntimeAdapters(),
        mcp_manager=FakeMCPManager(),
        chat_agent_manager=FakeChatAgentManager(),
    )

    assert asyncio.run(runtime.initialize(settings)) is True
    assert "metadata_attach" not in calls
    assert runtime.get_model_metadata_service() is None
    assert calls == [
        "service_created",
        "warmup",
        "mcp_initialize",
        "mcp_toolsets",
        "agent_initialize",
    ]


def test_runtime_attaches_metadata_cache_to_settings_before_worker(tmp_path):
    calls = []

    class FakeSettings:
        def __init__(self):
            self.model_metadata = _metadata_namespace(tmp_path)
            self._model_metadata_cache = None

        def attach_model_metadata_cache(self, cache):
            self._model_metadata_cache = cache
            calls.append("metadata_attach")

    settings = FakeSettings()

    class FakeRuntimeAdapters:
        async def warmup_models(self, received_settings):
            calls.append("warmup")
            assert received_settings._model_metadata_cache is not None

    class FakeMCPManager:
        async def initialize(self):
            calls.append("mcp_initialize")
            return True

        def get_healthy_toolsets(self):
            calls.append("mcp_toolsets")
            return ["toolset"]

    class FakeChatAgentManager:
        async def initialize(self, received_settings, mcp_toolsets):
            calls.append("agent_initialize")

    runtime = AgentRuntime(
        runtime_adapters=FakeRuntimeAdapters(),
        mcp_manager=FakeMCPManager(),
        chat_agent_manager=FakeChatAgentManager(),
    )

    asyncio.run(runtime.initialize(settings))

    assert settings._model_metadata_cache is not None
    assert calls[0] == "metadata_attach"
    assert calls.index("metadata_attach") < calls.index("warmup")


# ---------------------------------------------------------------------------
# Task1: Provider 配置参数确实传入（无真实网络）
# ---------------------------------------------------------------------------


def test_deepseek_custom_base_url_uses_openai_provider(monkeypatch):
    registry = RuntimeAdapterRegistry()
    captured = {}

    class FakeOpenAIChatModel:
        def __init__(self, model, provider=None):
            captured["model"] = model
            captured["provider"] = provider

    class FakeOpenAIProvider:
        def __init__(self, **kwargs):
            captured["openai_provider_kwargs"] = kwargs

    class FakeDeepSeekProvider:
        def __init__(self, **kwargs):
            captured["deepseek_provider_kwargs"] = kwargs

    monkeypatch.setattr("services.agent.providers.OpenAIChatModel", FakeOpenAIChatModel)
    monkeypatch.setattr("services.agent.providers.OpenAIProvider", FakeOpenAIProvider)
    monkeypatch.setattr("services.agent.providers.DeepSeekProvider", FakeDeepSeekProvider)
    monkeypatch.setattr(
        registry,
        "_get_or_create_http_client",
        lambda config, name: "http-client",
    )

    registry._create_deepseek_model(
        provider_config(base_url="https://custom.deepseek.test/v1", api_key="k")
    )

    assert captured["model"] == "deepseek-chat"
    assert "openai_provider_kwargs" in captured
    assert captured["openai_provider_kwargs"]["base_url"] == "https://custom.deepseek.test/v1"
    assert captured["openai_provider_kwargs"]["http_client"] == "http-client"
    assert "deepseek_provider_kwargs" not in captured


def test_deepseek_default_base_url_uses_deepseek_provider(monkeypatch):
    registry = RuntimeAdapterRegistry()
    captured = {}

    class FakeOpenAIChatModel:
        def __init__(self, model, provider=None):
            captured["provider"] = provider

    class FakeDeepSeekProvider:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

    monkeypatch.setattr("services.agent.providers.OpenAIChatModel", FakeOpenAIChatModel)
    monkeypatch.setattr("services.agent.providers.DeepSeekProvider", FakeDeepSeekProvider)
    monkeypatch.setattr(
        registry,
        "_get_or_create_http_client",
        lambda config, name: "http-client",
    )

    registry._create_deepseek_model(provider_config(base_url="https://api.deepseek.com", api_key="k"))

    assert captured["kwargs"]["api_key"] == "k"
    assert captured["kwargs"]["http_client"] == "http-client"


def test_anthropic_model_receives_timeout_via_http_client(monkeypatch):
    registry = RuntimeAdapterRegistry()
    captured = {}

    class FakeAnthropicModel:
        def __init__(self, model, provider=None):
            captured["model"] = model
            captured["provider"] = provider

    class FakeAnthropicProvider:
        def __init__(self, **kwargs):
            captured["provider_kwargs"] = kwargs

    import services.agent.providers as providers_mod

    # 延迟 import 路径在函数内部；patch 目标模块
    import pydantic_ai.models.anthropic as anthropic_models
    import pydantic_ai.providers.anthropic as anthropic_providers

    monkeypatch.setattr(anthropic_models, "AnthropicModel", FakeAnthropicModel)
    monkeypatch.setattr(anthropic_providers, "AnthropicProvider", FakeAnthropicProvider)

    def fake_http(config, name):
        captured["http_timeout"] = config.timeout
        captured["provider_name"] = name
        return "anthropic-http"

    monkeypatch.setattr(registry, "_get_or_create_http_client", fake_http)

    registry._create_anthropic_model(
        provider_config(name="anthropic", model="claude-test", api_key="k", timeout=33)
    )

    assert captured["model"] == "claude-test"
    assert captured["http_timeout"] == 33
    assert captured["provider_name"] == "anthropic"
    assert captured["provider_kwargs"]["http_client"] == "anthropic-http"


def test_ollama_model_receives_base_url_and_timeout(monkeypatch):
    registry = RuntimeAdapterRegistry()
    captured = {}

    class FakeOllamaModel:
        def __init__(self, model, provider=None):
            captured["model"] = model
            captured["provider"] = provider

    class FakeOllamaProvider:
        def __init__(self, **kwargs):
            captured["provider_kwargs"] = kwargs

    import pydantic_ai.models.ollama as ollama_models
    import pydantic_ai.providers.ollama as ollama_providers

    monkeypatch.setattr(ollama_models, "OllamaModel", FakeOllamaModel)
    monkeypatch.setattr(ollama_providers, "OllamaProvider", FakeOllamaProvider)

    def fake_http(config, name):
        captured["http_timeout"] = config.timeout
        return "ollama-http"

    monkeypatch.setattr(registry, "_get_or_create_http_client", fake_http)

    registry._create_ollama_model(
        provider_config(
            name="ollama",
            model="llama3",
            api_key=None,
            base_url="http://ollama.local:11434",
            timeout=17,
        )
    )

    assert captured["model"] == "llama3"
    assert captured["http_timeout"] == 17
    assert captured["provider_kwargs"]["base_url"] == "http://ollama.local:11434"
    assert captured["provider_kwargs"]["http_client"] == "ollama-http"


def test_llm_http_client_skips_raw_hooks_when_disabled(monkeypatch):
    registry = RuntimeAdapterRegistry()
    monkeypatch.setattr(
        RuntimeAdapterRegistry,
        "_is_llm_raw_log_enabled",
        staticmethod(lambda: False),
    )

    created = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr("services.agent.providers.httpx.AsyncClient", FakeAsyncClient)

    client = registry._create_llm_http_client(provider_config(timeout=12), "deepseek")
    assert isinstance(client, FakeAsyncClient)
    assert created["timeout"] == 12
    assert "event_hooks" not in created


def test_llm_http_client_installs_hooks_without_aread_when_enabled(monkeypatch):
    registry = RuntimeAdapterRegistry()
    monkeypatch.setattr(
        RuntimeAdapterRegistry,
        "_is_llm_raw_log_enabled",
        staticmethod(lambda: True),
    )

    created = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr("services.agent.providers.httpx.AsyncClient", FakeAsyncClient)

    registry._create_llm_http_client(provider_config(timeout=9), "openai")
    assert "event_hooks" in created
    # 确保源码不再强制 aread
    import inspect
    from services.agent.providers import RuntimeAdapterRegistry as R

    source = inspect.getsource(R._create_llm_http_client)
    assert "response.aread" not in source
    assert "await response.aread" not in source


def test_default_pytest_collection_excludes_live_marker():
    """Default pytest.ini addopts exclude live tests from collection/selection."""
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "tests/test_agent_multi_tool_live.py",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    output = (result.stdout or "") + (result.stderr or "")
    # With addopts = -m 'not live', live tests must not be selected by default.
    assert "test_deepseek_chat_should_support_multi_turn_tool_chain" not in result.stdout
    assert "test_deepseek_reasoner_should_support_multi_turn_tool_chain" not in result.stdout
    assert (
        result.returncode == 0
        or "deselected" in output.lower()
        or "no tests collected" in output.lower()
    )
