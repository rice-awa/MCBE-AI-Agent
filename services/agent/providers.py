"""LLM Provider 注册表"""

import json
from typing import Any

import httpx
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.openai import OpenAIProvider

from config.logging import get_logger
from config.settings import LLMProviderConfig, Settings
from core.exceptions import ProviderNotConfiguredError, ProviderNotFoundError

logger = get_logger(__name__)
llm_raw_logger = get_logger("llm.raw")


class RuntimeAdapterRegistry:
    """Owns LLM model adapters and their HTTP client lifecycle."""

    _raw_body_max_chars = 20_000

    def __init__(self) -> None:
        self._model_cache: dict[str, Model] = {}
        self._http_client_cache: dict[str, httpx.AsyncClient] = {}

    def get_model(self, config: LLMProviderConfig) -> Model:
        """
        根据配置获取模型实例

        Args:
            config: LLM 提供商配置

        Returns:
            PydanticAI Model 实例

        Raises:
            ProviderNotFoundError: 未知的提供商
            ProviderNotConfiguredError: 提供商未正确配置
        """
        provider_name = config.name.lower()
        cache_key = self._build_model_cache_key(config)

        if not config.enabled:
            raise ProviderNotConfiguredError(
                provider_name,
                details={"reason": "提供商未启用"},
            )

        if cached_model := self._model_cache.get(cache_key):
            return cached_model

        try:
            if provider_name == "deepseek":
                model = self._create_deepseek_model(config)
            elif provider_name == "openai":
                model = self._create_openai_model(config)
            elif provider_name == "anthropic":
                model = self._create_anthropic_model(config)
            elif provider_name == "ollama":
                model = self._create_ollama_model(config)
            else:
                raise ProviderNotFoundError(provider_name)

            self._model_cache[cache_key] = model
            return model

        except Exception as e:
            logger.error(
                "provider_creation_failed",
                provider=provider_name,
                error=str(e),
            )
            raise

    def _create_deepseek_model(self, config: LLMProviderConfig) -> Model:
        """创建 DeepSeek 模型"""
        if not config.api_key:
            raise ProviderNotConfiguredError(
                "deepseek",
                details={"reason": "缺少 API Key"},
            )

        logger.info("creating_deepseek_model", model=config.model)

        http_client = self._get_or_create_http_client(config, "deepseek")

        return OpenAIChatModel(
            config.model,
            provider=DeepSeekProvider(
                api_key=config.api_key,
                http_client=http_client,
            ),
        )

    def _create_openai_model(self, config: LLMProviderConfig) -> Model:
        """创建 OpenAI 模型"""
        if not config.api_key:
            raise ProviderNotConfiguredError(
                "openai",
                details={"reason": "缺少 API Key"},
            )

        logger.info("creating_openai_model", model=config.model)

        provider_kwargs: dict[str, Any] = {
            "api_key": config.api_key,
            "http_client": self._get_or_create_http_client(config, "openai"),
        }
        if config.base_url:
            provider_kwargs["base_url"] = config.base_url

        return OpenAIChatModel(
            config.model,
            provider=OpenAIProvider(**provider_kwargs),
        )

    def _create_anthropic_model(self, config: LLMProviderConfig) -> Model:
        """创建 Anthropic 模型"""
        if not config.api_key:
            raise ProviderNotConfiguredError(
                "anthropic",
                details={"reason": "缺少 API Key"},
            )

        logger.info("creating_anthropic_model", model=config.model)

        from pydantic_ai.models.anthropic import AnthropicModel

        return AnthropicModel(config.model, api_key=config.api_key)

    def _create_ollama_model(self, config: LLMProviderConfig) -> Model:
        """创建 Ollama 模型"""
        logger.info(
            "creating_ollama_model",
            model=config.model,
            base_url=config.base_url,
        )

        from pydantic_ai.models.ollama import OllamaModel

        return OllamaModel(
            config.model,
            base_url=config.base_url or "http://localhost:11434",
        )

    @staticmethod
    def list_providers() -> list[str]:
        """列出所有支持的提供商"""
        return ["deepseek", "openai", "anthropic", "ollama"]

    async def warmup_models(self, settings: Settings) -> None:
        """
        预热 LLM 模型，提前创建默认 provider 的模型实例

        Args:
            settings: 应用配置
        """
        if not settings.llm_warmup_enabled:
            logger.info("llm_warmup_disabled")
            return

        logger.info(
            "llm_warmup_starting",
            default_provider=settings.default_provider,
        )

        try:
            provider_config = settings.get_provider_config(settings.default_provider)
            self.get_model(provider_config)

            logger.info(
                "llm_warmup_completed",
                provider=settings.default_provider,
                model=provider_config.model,
            )

        except Exception as e:
            logger.warning(
                "llm_warmup_failed",
                provider=settings.default_provider,
                error=str(e),
                exc_info=True,
            )

    async def shutdown(self) -> None:
        """关闭 registry 维护的 HTTP 客户端。"""
        for cache_key, client in list(self._http_client_cache.items()):
            try:
                await client.aclose()
            except Exception as e:
                logger.warning(
                    "provider_http_client_close_failed",
                    cache_key=cache_key,
                    error=str(e),
                )

        self._http_client_cache.clear()
        self._model_cache.clear()

    @staticmethod
    def get_model_string(config: LLMProviderConfig) -> str:
        """
        获取模型字符串（用于 Agent 初始化）

        Args:
            config: 提供商配置

        Returns:
            模型字符串，格式如 "deepseek:deepseek-chat"
        """
        provider_name = config.name.lower()
        if provider_name in ["deepseek", "openai"]:
            return f"{provider_name}:{config.model}"
        if provider_name == "anthropic":
            return f"anthropic:{config.model}"
        if provider_name == "ollama":
            return f"ollama:{config.model}"
        return config.model

    @staticmethod
    def _build_model_cache_key(config: LLMProviderConfig) -> str:
        """根据 provider 配置构建模型缓存键。"""
        return ":".join(
            [
                config.name.lower(),
                config.model,
                config.base_url or "",
                str(config.timeout),
            ]
        )

    @staticmethod
    def _build_http_client_cache_key(
        config: LLMProviderConfig,
        provider_name: str,
    ) -> str:
        return ":".join(
            [
                provider_name,
                config.model,
                config.base_url or "",
                str(config.timeout),
            ]
        )

    def _get_or_create_http_client(
        self,
        config: LLMProviderConfig,
        provider_name: str,
    ) -> httpx.AsyncClient:
        cache_key = self._build_http_client_cache_key(config, provider_name)
        if cached_client := self._http_client_cache.get(cache_key):
            return cached_client

        client = self._create_llm_http_client(config, provider_name)
        self._http_client_cache[cache_key] = client
        return client

    def _create_llm_http_client(
        self,
        config: LLMProviderConfig,
        provider_name: str,
    ) -> httpx.AsyncClient:
        """创建带 LLM 原始请求/响应日志的 HTTP 客户端。"""

        async def on_request(request: httpx.Request) -> None:
            llm_raw_logger.info(
                "llm_raw_request",
                provider=provider_name,
                model=config.model,
                method=request.method,
                url=str(request.url),
                headers=self._sanitize_headers(dict(request.headers)),
                body=self._format_raw_body(request.content),
            )

        async def on_response(response: httpx.Response) -> None:
            try:
                await response.aread()
                body = self._format_raw_body(response.content)
            except Exception as e:
                body = f"<response_read_error: {str(e)}>"

            llm_raw_logger.info(
                "llm_raw_response",
                provider=provider_name,
                model=config.model,
                status_code=response.status_code,
                url=str(response.request.url),
                headers=self._sanitize_headers(dict(response.headers)),
                body=body,
            )

        return httpx.AsyncClient(
            timeout=config.timeout,
            event_hooks={"request": [on_request], "response": [on_response]},
        )

    def _format_raw_body(self, content: bytes | str | None) -> str | None:
        if content is None:
            return None

        if isinstance(content, bytes):
            text = content.decode("utf-8", errors="replace")
        else:
            text = str(content)

        text = text.strip()
        if not text:
            return None

        try:
            parsed = json.loads(text)
            text = json.dumps(parsed, ensure_ascii=False)
        except Exception:
            pass

        if len(text) > self._raw_body_max_chars:
            return (
                text[: self._raw_body_max_chars]
                + f"...<truncated:{len(text) - self._raw_body_max_chars}>"
            )

        return text

    @staticmethod
    def _sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
        sensitive_keys = {"authorization", "api-key", "x-api-key"}
        sanitized: dict[str, str] = {}
        for key, value in headers.items():
            if key.lower() in sensitive_keys:
                sanitized[key] = "***"
            else:
                sanitized[key] = value
        return sanitized


class ProviderRegistry:
    """Compatibility facade for the process default runtime adapter registry."""

    @classmethod
    def get_runtime_adapters(cls) -> RuntimeAdapterRegistry:
        from services.agent.runtime import get_agent_runtime

        return get_agent_runtime().runtime_adapters

    @classmethod
    def set_runtime_adapters(cls, runtime_adapters: RuntimeAdapterRegistry) -> None:
        from services.agent.runtime import get_agent_runtime

        get_agent_runtime().runtime_adapters = runtime_adapters

    @classmethod
    def get_model(cls, config: LLMProviderConfig) -> Model:
        return cls.get_runtime_adapters().get_model(config)

    @classmethod
    def list_providers(cls) -> list[str]:
        return cls.get_runtime_adapters().list_providers()

    @classmethod
    async def warmup_models(cls, settings: Settings) -> None:
        await cls.get_runtime_adapters().warmup_models(settings)

    @classmethod
    async def shutdown(cls) -> None:
        await cls.get_runtime_adapters().shutdown()

    @classmethod
    def get_model_string(cls, config: LLMProviderConfig) -> str:
        return cls.get_runtime_adapters().get_model_string(config)
