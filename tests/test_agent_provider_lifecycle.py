import asyncio
from types import SimpleNamespace

from config.settings import LLMProviderConfig
from services.agent.providers import ProviderRegistry, RuntimeAdapterRegistry


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
