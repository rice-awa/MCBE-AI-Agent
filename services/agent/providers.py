"""LLM Provider 注册表"""

from typing import Any

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.deepseek import DeepSeekProvider

from mcbe_ai_agent.config.settings import LLMProviderConfig
from mcbe_ai_agent.core.exceptions import ProviderNotFoundError, ProviderNotConfiguredError
from mcbe_ai_agent.config.logging import get_logger

logger = get_logger(__name__)


class ProviderRegistry:
    """LLM Provider 注册表 - 支持多种 LLM"""

    @classmethod
    def get_model(cls, config: LLMProviderConfig) -> Model:
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

        if not config.enabled:
            raise ProviderNotConfiguredError(
                provider_name,
                details={"reason": "提供商未启用"},
            )

        try:
            if provider_name == "deepseek":
                return cls._create_deepseek_model(config)
            elif provider_name == "openai":
                return cls._create_openai_model(config)
            elif provider_name == "anthropic":
                return cls._create_anthropic_model(config)
            elif provider_name == "ollama":
                return cls._create_ollama_model(config)
            else:
                raise ProviderNotFoundError(provider_name)

        except Exception as e:
            logger.error(
                "provider_creation_failed",
                provider=provider_name,
                error=str(e),
            )
            raise

    @classmethod
    def _create_deepseek_model(cls, config: LLMProviderConfig) -> Model:
        """创建 DeepSeek 模型"""
        if not config.api_key:
            raise ProviderNotConfiguredError(
                "deepseek",
                details={"reason": "缺少 API Key"},
            )

        logger.info("creating_deepseek_model", model=config.model)

        return OpenAIChatModel(
            config.model,
            provider=DeepSeekProvider(api_key=config.api_key),
        )

    @classmethod
    def _create_openai_model(cls, config: LLMProviderConfig) -> Model:
        """创建 OpenAI 模型"""
        if not config.api_key:
            raise ProviderNotConfiguredError(
                "openai",
                details={"reason": "缺少 API Key"},
            )

        logger.info("creating_openai_model", model=config.model)

        provider_kwargs: dict[str, Any] = {"api_key": config.api_key}
        if config.base_url:
            provider_kwargs["base_url"] = config.base_url

        return OpenAIChatModel(
            config.model,
            provider=OpenAIProvider(**provider_kwargs),
        )

    @classmethod
    def _create_anthropic_model(cls, config: LLMProviderConfig) -> Model:
        """创建 Anthropic 模型"""
        if not config.api_key:
            raise ProviderNotConfiguredError(
                "anthropic",
                details={"reason": "缺少 API Key"},
            )

        logger.info("creating_anthropic_model", model=config.model)

        # 直接使用 Anthropic 模型字符串，PydanticAI 会自动处理
        from pydantic_ai.models.anthropic import AnthropicModel
        return AnthropicModel(config.model, api_key=config.api_key)

    @classmethod
    def _create_ollama_model(cls, config: LLMProviderConfig) -> Model:
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

    @classmethod
    def list_providers(cls) -> list[str]:
        """列出所有支持的提供商"""
        return ["deepseek", "openai", "anthropic", "ollama"]

    @classmethod
    def get_model_string(cls, config: LLMProviderConfig) -> str:
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
        elif provider_name == "anthropic":
            return f"anthropic:{config.model}"
        elif provider_name == "ollama":
            return f"ollama:{config.model}"
        else:
            return config.model
