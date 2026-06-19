"""models.dev 元数据解析与缓存测试"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config.settings import ModelMetadataConfig
from services.agent.model_metadata import (
    ModelMetadata,
    ModelMetadataCache,
    ModelMetadataService,
    load_cache,
    save_cache,
)


def test_parse_models_dev_api_context_and_output_limits():
    payload = {
        "deepseek": {
            "models": {
                "deepseek-chat": {
                    "id": "deepseek-chat",
                    "name": "DeepSeek Chat",
                    "limit": {"context": 1000000, "output": 384000},
                }
            }
        }
    }

    cache = ModelMetadataCache.from_models_dev_api(payload)

    metadata = cache.get("deepseek", "deepseek-chat")
    assert metadata is not None
    assert metadata.provider == "deepseek"
    assert metadata.model == "deepseek-chat"
    assert metadata.name == "DeepSeek Chat"
    assert metadata.context_window == 1000000
    assert metadata.max_output_tokens == 384000


def test_get_returns_none_when_provider_missing():
    cache = ModelMetadataCache.from_models_dev_api(
        {"deepseek": {"models": {}}}
    )
    assert cache.get("unknown", "x") is None


def test_get_returns_none_when_model_missing():
    cache = ModelMetadataCache.from_models_dev_api(
        {"deepseek": {"models": {"deepseek-chat": {"name": "x"}}}}
    )
    assert cache.get("deepseek", "deepseek-chat-v2") is None


def test_get_returns_none_for_empty_payload():
    cache = ModelMetadataCache.from_models_dev_api({})
    assert cache.get("deepseek", "deepseek-chat") is None


def test_key_format_is_provider_colon_model():
    assert ModelMetadataCache.key("deepseek", "deepseek-chat") == "deepseek:deepseek-chat"


def test_get_uses_exact_match_without_case_normalization():
    payload = {
        "DeepSeek": {
            "models": {
                "DeepSeek-Chat": {
                    "name": "x",
                    "limit": {"context": 100, "output": 50},
                }
            }
        }
    }
    cache = ModelMetadataCache.from_models_dev_api(payload)
    assert cache.get("DeepSeek", "DeepSeek-Chat") is not None
    assert cache.get("deepseek", "deepseek-chat") is None
    assert cache.get("deepseek", "DeepSeek-Chat") is None
    assert cache.get("DeepSeek", "deepseek-chat") is None


def test_parse_model_without_limit_keeps_none():
    payload = {"p": {"models": {"m": {"name": "M"}}}}
    cache = ModelMetadataCache.from_models_dev_api(payload)
    metadata = cache.get("p", "m")
    assert metadata is not None
    assert metadata.name == "M"
    assert metadata.context_window is None
    assert metadata.max_output_tokens is None


@pytest.mark.parametrize(
    "context_value",
    [-1, 0, "1000000", 1.5, True, False, None],
)
def test_context_window_none_when_limit_context_not_positive_int(context_value):
    payload = {
        "p": {
            "models": {
                "m": {
                    "limit": {"context": context_value, "output": 100},
                }
            }
        }
    }
    cache = ModelMetadataCache.from_models_dev_api(payload)
    metadata = cache.get("p", "m")
    assert metadata is not None
    assert metadata.context_window is None
    assert metadata.max_output_tokens == 100


@pytest.mark.parametrize(
    "output_value",
    [-1, 0, "384000", 1.5, True, False, None],
)
def test_max_output_tokens_none_when_limit_output_not_positive_int(output_value):
    payload = {
        "p": {
            "models": {
                "m": {
                    "limit": {"context": 100, "output": output_value},
                }
            }
        }
    }
    cache = ModelMetadataCache.from_models_dev_api(payload)
    metadata = cache.get("p", "m")
    assert metadata is not None
    assert metadata.context_window == 100
    assert metadata.max_output_tokens is None


def test_context_window_none_when_limit_context_key_absent():
    payload = {"p": {"models": {"m": {"limit": {"output": 100}}}}}
    cache = ModelMetadataCache.from_models_dev_api(payload)
    metadata = cache.get("p", "m")
    assert metadata is not None
    assert metadata.context_window is None
    assert metadata.max_output_tokens == 100


def test_max_output_tokens_none_when_limit_output_key_absent():
    payload = {"p": {"models": {"m": {"limit": {"context": 100}}}}}
    cache = ModelMetadataCache.from_models_dev_api(payload)
    metadata = cache.get("p", "m")
    assert metadata is not None
    assert metadata.context_window == 100
    assert metadata.max_output_tokens is None


def test_parse_skips_non_dict_provider():
    payload = {"deepseek": "not-a-dict"}
    cache = ModelMetadataCache.from_models_dev_api(payload)
    assert cache.get("deepseek", "any") is None


def test_parse_skips_when_models_not_dict():
    payload = {"deepseek": {"models": "not-a-dict"}}
    cache = ModelMetadataCache.from_models_dev_api(payload)
    assert cache.get("deepseek", "any") is None


def test_parse_skips_when_models_key_absent():
    payload = {"deepseek": {"other": 1}}
    cache = ModelMetadataCache.from_models_dev_api(payload)
    assert cache.get("deepseek", "any") is None


def test_parse_skips_when_model_not_dict():
    payload = {"deepseek": {"models": {"deepseek-chat": "not-a-dict"}}}
    cache = ModelMetadataCache.from_models_dev_api(payload)
    assert cache.get("deepseek", "deepseek-chat") is None


def test_parse_handles_mixed_valid_and_malformed_without_raising():
    payload = {
        "deepseek": {
            "models": {
                "deepseek-chat": {
                    "name": "DeepSeek Chat",
                    "limit": {"context": 1000000, "output": 384000},
                },
                "bad-model": "not-a-dict",
            }
        },
        "broken-provider": "not-a-dict",
        "no-models": {"other": 1},
    }
    cache = ModelMetadataCache.from_models_dev_api(payload)
    metadata = cache.get("deepseek", "deepseek-chat")
    assert metadata is not None
    assert metadata.context_window == 1000000
    assert metadata.max_output_tokens == 384000
    assert cache.get("deepseek", "bad-model") is None
    assert cache.get("broken-provider", "any") is None
    assert cache.get("no-models", "any") is None


def test_get_context_window_helper_returns_value():
    payload = {"p": {"models": {"m": {"limit": {"context": 4096, "output": 100}}}}}
    cache = ModelMetadataCache.from_models_dev_api(payload)
    assert cache.get_context_window("p", "m") == 4096


def test_get_context_window_helper_returns_none_when_missing():
    cache = ModelMetadataCache.from_models_dev_api({})
    assert cache.get_context_window("p", "m") is None


def test_positive_int_does_not_coerce_bool_to_int():
    # bool 是 int 子类，必须被排除：True 不应被当作 context=1
    payload = {"p": {"models": {"m": {"limit": {"context": True, "output": False}}}}}
    cache = ModelMetadataCache.from_models_dev_api(payload)
    metadata = cache.get("p", "m")
    assert metadata is not None
    assert metadata.context_window is None
    assert metadata.max_output_tokens is None


@pytest.mark.asyncio
async def test_load_cache_returns_empty_when_file_absent(tmp_path: Path):
    cache = await load_cache(tmp_path / "missing.json")
    assert isinstance(cache, ModelMetadataCache)
    assert cache.models == {}


@pytest.mark.asyncio
async def test_load_cache_returns_empty_on_corrupt_json(tmp_path: Path):
    path = tmp_path / "corrupt.json"
    path.write_text("{not valid json", encoding="utf-8")
    cache = await load_cache(path)
    assert cache.models == {}


@pytest.mark.asyncio
async def test_load_cache_returns_empty_on_non_dict_json(tmp_path: Path):
    path = tmp_path / "list.json"
    path.write_text('[1, 2, 3]', encoding="utf-8")
    cache = await load_cache(path)
    assert cache.models == {}


@pytest.mark.asyncio
async def test_load_cache_returns_empty_on_invalid_structure(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text('{"models": "not-a-dict"}', encoding="utf-8")
    cache = await load_cache(path)
    assert cache.models == {}


@pytest.mark.asyncio
async def test_save_then_load_roundtrip_creates_parent_dir(tmp_path: Path):
    cache = ModelMetadataCache.from_models_dev_api(
        {
            "deepseek": {
                "models": {
                    "deepseek-chat": {
                        "name": "DeepSeek Chat",
                        "limit": {"context": 1000000, "output": 384000},
                    }
                }
            }
        }
    )
    path = tmp_path / "subdir" / "nested" / "cache.json"
    assert not path.parent.exists()

    await save_cache(path, cache)
    assert path.parent.exists()
    assert path.exists()

    loaded = await load_cache(path)
    metadata = loaded.get("deepseek", "deepseek-chat")
    assert metadata is not None
    assert metadata.name == "DeepSeek Chat"
    assert metadata.context_window == 1000000
    assert metadata.max_output_tokens == 384000


# ---------------------------------------------------------------------------
# Task 3: ModelMetadataService 启动刷新
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metadata_service_initialize_disabled_skips_refresh(tmp_path, monkeypatch):
    config = ModelMetadataConfig(
        enabled=False,
        refresh_on_startup=True,
        cache_path=tmp_path / "cache.json",
    )
    service = ModelMetadataService(config)

    async def _must_not_call():
        raise AssertionError("_fetch_payload should not be called when disabled")

    monkeypatch.setattr(service, "_fetch_payload", _must_not_call)
    await service.initialize()
    assert len(service.cache.models) == 0


@pytest.mark.asyncio
async def test_metadata_service_initialize_refresh_on_startup_false_skips_refresh(
    tmp_path, monkeypatch
):
    config = ModelMetadataConfig(
        enabled=True,
        refresh_on_startup=False,
        cache_path=tmp_path / "cache.json",
    )
    service = ModelMetadataService(config)

    async def _must_not_call():
        raise AssertionError(
            "_fetch_payload should not be called when refresh_on_startup is False"
        )

    monkeypatch.setattr(service, "_fetch_payload", _must_not_call)
    await service.initialize()
    assert len(service.cache.models) == 0


@pytest.mark.asyncio
async def test_metadata_service_refresh_updates_in_memory_cache(tmp_path, monkeypatch):
    config = ModelMetadataConfig(
        enabled=True,
        refresh_on_startup=True,
        cache_path=tmp_path / "cache.json",
    )
    service = ModelMetadataService(config)

    async def _fake_fetch():
        return {
            "openai": {
                "models": {
                    "gpt-4o": {
                        "name": "GPT-4o",
                        "limit": {"context": 128000, "output": 16384},
                    }
                }
            }
        }

    monkeypatch.setattr(service, "_fetch_payload", _fake_fetch)
    await service.refresh()

    assert service.cache.get_context_window("openai", "gpt-4o") == 128000


@pytest.mark.asyncio
async def test_metadata_service_initialize_refresh_failure_preserves_loaded_cache(
    tmp_path, monkeypatch
):
    cache_path = tmp_path / "cache.json"
    preloaded = ModelMetadataCache.from_models_dev_api(
        {"openai": {"models": {"preserved-model": {"limit": {"context": 999}}}}}
    )
    await save_cache(cache_path, preloaded)

    config = ModelMetadataConfig(
        enabled=True,
        refresh_on_startup=True,
        cache_path=cache_path,
    )
    service = ModelMetadataService(config)

    async def _failing_fetch():
        raise RuntimeError("network down")

    monkeypatch.setattr(service, "_fetch_payload", _failing_fetch)
    await service.initialize()

    assert service.cache.get_context_window("openai", "preserved-model") == 999


@pytest.mark.asyncio
async def test_metadata_service_initialize_refresh_failure_no_local_cache_no_raise(
    tmp_path, monkeypatch
):
    config = ModelMetadataConfig(
        enabled=True,
        refresh_on_startup=True,
        cache_path=tmp_path / "missing.json",
    )
    service = ModelMetadataService(config)

    async def _failing_fetch():
        raise RuntimeError("network down")

    monkeypatch.setattr(service, "_fetch_payload", _failing_fetch)
    await service.initialize()
    assert len(service.cache.models) == 0


@pytest.mark.asyncio
async def test_metadata_service_refresh_writes_cache_file(tmp_path, monkeypatch):
    cache_path = tmp_path / "subdir" / "cache.json"
    config = ModelMetadataConfig(
        enabled=True,
        refresh_on_startup=True,
        cache_path=cache_path,
    )
    service = ModelMetadataService(config)

    async def _fake_fetch():
        return {"openai": {"models": {"gpt-4o": {"limit": {"context": 128000}}}}}

    monkeypatch.setattr(service, "_fetch_payload", _fake_fetch)
    await service.refresh()

    assert cache_path.exists()
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    assert "openai:gpt-4o" in data["models"]
    assert data["models"]["openai:gpt-4o"]["context_window"] == 128000


@pytest.mark.asyncio
async def test_metadata_service_get_context_window_delegates_to_cache(tmp_path):
    config = ModelMetadataConfig(cache_path=tmp_path / "cache.json")
    service = ModelMetadataService(config)
    service._cache = ModelMetadataCache.from_models_dev_api(
        {"openai": {"models": {"gpt-4o": {"limit": {"context": 128000}}}}}
    )
    assert service.get_context_window("openai", "gpt-4o") == 128000
    assert service.get_context_window("openai", "missing") is None
