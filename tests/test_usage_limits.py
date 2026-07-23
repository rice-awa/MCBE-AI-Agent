"""build_usage_limits：显式 token 上限 + provider 能力门控 count_tokens_before_request。

PydanticAI 的 input/total tokens 是整轮累计，不能用 context_window 自动派生
input_tokens_limit，否则多步工具循环会被误杀。
"""

from types import SimpleNamespace

from pydantic_ai.exceptions import UsageLimitExceeded

from services.agent.core import (
    build_usage_limits,
    classify_run_exception,
    format_usage_limit_player_message,
    provider_supports_count_tokens,
)


def _settings(
    *,
    count_tokens_before_request: bool = True,
    context_window: int | None = None,
    input_tokens_limit: int | None = None,
    total_tokens_limit: int | None = None,
    output_tokens_limit: int | None = None,
) -> SimpleNamespace:
    def get_provider_config(_name: str | None = None) -> SimpleNamespace:
        return SimpleNamespace(context_window=context_window)

    return SimpleNamespace(
        request_limit=8,
        tool_calls_limit=8,
        input_tokens_limit=input_tokens_limit,
        output_tokens_limit=output_tokens_limit,
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


def test_context_window_does_not_auto_derive_token_limits() -> None:
    """context_window 不得自动变成 input/total tokens 硬上限（累计语义）。"""
    settings = _settings(context_window=128_000)
    limits = build_usage_limits(settings, "deepseek")
    assert limits.input_tokens_limit is None
    assert limits.total_tokens_limit is None
    assert limits.request_limit == 8
    assert limits.tool_calls_limit == 8


def test_missing_context_window_does_not_force_fallback_token_limits() -> None:
    settings = _settings(context_window=None)
    limits = build_usage_limits(settings, "openai")
    assert limits.input_tokens_limit is None
    assert limits.total_tokens_limit is None
    assert limits.count_tokens_before_request is False


def test_explicit_token_limits_are_passed_through() -> None:
    settings = _settings(
        context_window=128_000,
        input_tokens_limit=50_000,
        total_tokens_limit=60_000,
        output_tokens_limit=2_000,
    )
    limits = build_usage_limits(settings, "deepseek")
    assert limits.input_tokens_limit == 50_000
    assert limits.total_tokens_limit == 60_000
    assert limits.output_tokens_limit == 2_000


def test_format_usage_limit_player_message_input_tokens() -> None:
    exc = UsageLimitExceeded(
        "Exceeded the input_tokens_limit of 126976 (input_tokens=128035)"
    )
    msg = format_usage_limit_player_message(exc)
    assert "累计" in msg or "输入" in msg
    assert "缩短" in msg or "拆" in msg
    # 不再是笼统的一句「预算上限」
    assert msg != "已达到本轮请求预算上限，请缩短问题或稍后再试。"


def test_format_usage_limit_player_message_tool_calls() -> None:
    exc = UsageLimitExceeded(
        "The next tool call(s) would exceed the tool_calls_limit of 16 (tool_calls=17)."
    )
    msg = format_usage_limit_player_message(exc)
    assert "工具" in msg


def test_format_usage_limit_player_message_request_limit() -> None:
    exc = UsageLimitExceeded(
        "The next request would exceed the request_limit of 8"
    )
    msg = format_usage_limit_player_message(exc)
    assert "步数" in msg or "请求" in msg


def test_classify_usage_limit_uses_specific_player_message() -> None:
    exc = UsageLimitExceeded(
        "Exceeded the input_tokens_limit of 126976 (input_tokens=128035)"
    )
    kind, player_msg, diagnostic = classify_run_exception(exc)
    assert kind == "DENIED"
    assert "input_tokens_limit" in diagnostic
    assert "累计" in player_msg or "输入" in player_msg
    assert "UsageLimitExceeded" not in player_msg
