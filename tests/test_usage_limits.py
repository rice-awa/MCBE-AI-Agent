"""build_usage_limits：provider 能力门控 count_tokens_before_request。"""

from types import SimpleNamespace

from services.agent.core import build_usage_limits, provider_supports_count_tokens


def _settings(
    *,
    count_tokens_before_request: bool = True,
    context_window: int | None = None,
    input_tokens_limit: int | None = None,
    total_tokens_limit: int | None = None,
) -> SimpleNamespace:
    def get_provider_config(_name: str | None = None) -> SimpleNamespace:
        return SimpleNamespace(context_window=context_window)

    return SimpleNamespace(
        request_limit=8,
        tool_calls_limit=8,
        input_tokens_limit=input_tokens_limit,
        output_tokens_limit=None,
        total_tokens_limit=total_tokens_limit,
        context_output_reserve_tokens=1024,
        count_tokens_before_request=count_tokens_before_request,
        get_provider_config=get_provider_config,
    )


def test_provider_supports_count_tokens_only_anthropic() -> None:
    assert provider_supports_count_tokens("anthropic") is True
    assert provider_supports_count_tokens("Anthropic") is True
    assert provider_supports_count_tokens("openai") is False
    assert provider_supports_count_tokens("deepseek") is False
    assert provider_supports_count_tokens("ollama") is False
    assert provider_supports_count_tokens(None) is False
    assert provider_supports_count_tokens("") is False


def test_count_tokens_disabled_for_openai_chat_model_providers() -> None:
    settings = _settings(count_tokens_before_request=True)

    for provider in ("openai", "deepseek", "ollama", None):
        limits = build_usage_limits(settings, provider)
        assert limits.count_tokens_before_request is False, provider


def test_count_tokens_enabled_for_anthropic_when_settings_on() -> None:
    settings = _settings(count_tokens_before_request=True)
    limits = build_usage_limits(settings, "anthropic")
    assert limits.count_tokens_before_request is True


def test_count_tokens_respects_settings_off_even_for_anthropic() -> None:
    settings = _settings(count_tokens_before_request=False)
    limits = build_usage_limits(settings, "anthropic")
    assert limits.count_tokens_before_request is False


def test_token_limits_still_derived_when_count_tokens_gated_off() -> None:
    settings = _settings(
        count_tokens_before_request=True,
        context_window=16_384,
    )
    limits = build_usage_limits(settings, "openai")
    assert limits.count_tokens_before_request is False
    assert limits.input_tokens_limit == 16_384 - 1024
    assert limits.total_tokens_limit == 16_384
    assert limits.request_limit == 8
    assert limits.tool_calls_limit == 8
