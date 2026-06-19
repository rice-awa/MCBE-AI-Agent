"""models.dev 元数据解析与本地缓存。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from _version import __version__
from config.logging import get_logger
from config.settings import ModelMetadataConfig

logger = get_logger(__name__)


def _positive_int(value: Any) -> int | None:
    """仅接受正整数；bool（int 子类）、浮点、字符串等一律返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


class ModelMetadata(BaseModel):
    provider: str
    model: str
    name: str | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None


class ModelMetadataCache(BaseModel):
    models: dict[str, ModelMetadata] = Field(default_factory=dict)

    @staticmethod
    def key(provider: str, model: str) -> str:
        return f"{provider}:{model}"

    def get(self, provider: str, model: str) -> ModelMetadata | None:
        return self.models.get(self.key(provider, model))

    def get_context_window(self, provider: str, model: str) -> int | None:
        metadata = self.get(provider, model)
        return metadata.context_window if metadata else None

    @classmethod
    def from_models_dev_api(cls, payload: Mapping[str, Any]) -> ModelMetadataCache:
        models: dict[str, ModelMetadata] = {}
        if not isinstance(payload, Mapping):
            return cls(models=models)
        for provider_name, provider_data in payload.items():
            if not isinstance(provider_data, Mapping):
                continue
            provider_models = provider_data.get("models")
            if not isinstance(provider_models, Mapping):
                continue
            for model_name, model_data in provider_models.items():
                if not isinstance(model_data, Mapping):
                    continue
                limit = model_data.get("limit")
                context_window: int | None = None
                max_output_tokens: int | None = None
                if isinstance(limit, Mapping):
                    context_window = _positive_int(limit.get("context"))
                    max_output_tokens = _positive_int(limit.get("output"))
                raw_name = model_data.get("name")
                name = raw_name if isinstance(raw_name, str) else None
                models[cls.key(provider_name, model_name)] = ModelMetadata(
                    provider=provider_name,
                    model=model_name,
                    name=name,
                    context_window=context_window,
                    max_output_tokens=max_output_tokens,
                )
        return cls(models=models)


async def load_cache(path: Path) -> ModelMetadataCache:
    """从本地文件加载缓存；文件缺失或损坏时返回空缓存。"""
    if not path.exists():
        return ModelMetadataCache()
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError, ValueError) as e:
        logger.warning("model_metadata_cache_load_failed", error=str(e))
        return ModelMetadataCache()
    if not isinstance(data, dict):
        logger.warning(
            "model_metadata_cache_load_failed",
            error=f"expected json object, got {type(data).__name__}",
        )
        return ModelMetadataCache()
    try:
        return ModelMetadataCache.model_validate(data)
    except (ValidationError, ValueError, TypeError) as e:
        logger.warning("model_metadata_cache_load_failed", error=str(e))
        return ModelMetadataCache()


async def save_cache(path: Path, cache: ModelMetadataCache) -> None:
    """将缓存写入本地文件，自动创建父目录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(cache.model_dump(mode="json"), f, ensure_ascii=False, indent=2)


class ModelMetadataService:
    """启动期加载/刷新 models.dev 元数据，向运行时提供上下文窗口。"""

    def __init__(self, config: ModelMetadataConfig):
        self.config = config
        self._cache = ModelMetadataCache()

    @property
    def cache(self) -> ModelMetadataCache:
        return self._cache

    async def initialize(self) -> None:
        self._cache = await load_cache(self.config.cache_path)
        if self.config.enabled and self.config.refresh_on_startup:
            await self.refresh()

    async def refresh(self) -> None:
        try:
            payload = await self._fetch_payload()
            if not isinstance(payload, Mapping):
                raise TypeError(
                    f"expected mapping payload, got {type(payload).__name__}"
                )
            new_cache = ModelMetadataCache.from_models_dev_api(payload)
            self._cache = new_cache
            await save_cache(self.config.cache_path, new_cache)
            logger.info(
                "model_metadata_refresh_completed",
                model_count=len(new_cache.models),
            )
        except Exception as e:
            logger.warning("model_metadata_refresh_failed", error=str(e))

    async def _fetch_payload(self) -> Mapping[str, Any]:
        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            response = await client.get(
                self.config.source_url,
                headers={
                    f"User-Agent": "mcbe-ai-agent/{__version__} (+https://github.com/rice-awa/mcbe_ai_agent)"
                },
            )
            response.raise_for_status()
            return response.json()

    def get_context_window(self, provider: str, model: str) -> int | None:
        return self._cache.get_context_window(provider, model)
