import json
from pathlib import Path

import pytest

from config.settings import CONFIG_FILE, ModelMetadataConfig, Settings, get_settings
from services.agent.model_metadata import ModelMetadataCache
from services.websocket.minecraft import MinecraftProtocolHandler


def write_json_config(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def write_env(tmp_path, content):
    path = tmp_path / ".env"
    path.write_text(content, encoding="utf-8")
    return path


def test_agent_compression_settings_loaded_from_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_json_config(
        tmp_path,
        {
            "agent": {
                "compression_enabled": False,
                "compression_trigger_ratio": 0.6,
                "compression_keep_recent_turns": 3,
                "compression_summary_max_chars": 777,
                "compression_timeout": 12,
            }
        },
    )

    settings = Settings()

    assert settings.compression_enabled is False
    assert settings.compression_trigger_ratio == 0.6
    assert settings.compression_keep_recent_turns == 3
    assert settings.compression_summary_max_chars == 777
    assert settings.compression_timeout == 12


def test_runtime_harness_settings_loaded_from_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_json_config(
        tmp_path,
        {
            "agent": {
                "runtime_harness": {
                    "enabled": True,
                    "prompt_enabled": True,
                    "schema_enabled": True,
                    "audit_enabled": False,
                    "audit_path": "tmp/audit.jsonl",
                    "audit_max_records": 12,
                }
            }
        },
    )

    settings = Settings()

    assert settings.runtime_harness_enabled is True
    assert settings.runtime_harness_prompt_enabled is True
    assert settings.runtime_harness_schema_enabled is True
    assert settings.runtime_harness_audit_enabled is False
    assert settings.runtime_harness_audit_path == "tmp/audit.jsonl"
    assert settings.runtime_harness_audit_max_records == 12


def test_agent_run_budget_settings_loaded_from_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_json_config(
        tmp_path,
        {
            "agent": {
                "request_limit": 3,
                "tool_calls_limit": 4,
                "input_tokens_limit": 1000,
                "output_tokens_limit": 200,
                "total_tokens_limit": 1200,
                "run_timeout": 45.5,
                "max_tool_concurrency": 2,
                "context_output_reserve_tokens": 256,
            }
        },
    )

    settings = Settings()

    assert settings.request_limit == 3
    assert settings.tool_calls_limit == 4
    assert settings.input_tokens_limit == 1000
    assert settings.output_tokens_limit == 200
    assert settings.total_tokens_limit == 1200
    assert settings.run_timeout == 45.5
    assert settings.max_tool_concurrency == 2
    assert settings.context_output_reserve_tokens == 256


def test_agent_run_budget_defaults_when_absent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_json_config(tmp_path, {"agent": {"system_prompt": "x"}})

    settings = Settings()

    assert settings.request_limit == 8
    assert settings.tool_calls_limit == 8
    assert settings.input_tokens_limit is None
    assert settings.output_tokens_limit is None
    assert settings.total_tokens_limit is None
    assert settings.run_timeout == 90.0
    assert settings.max_tool_concurrency == 4
    assert settings.context_output_reserve_tokens == 1024


def test_agent_compression_settings_are_optional_in_runtime_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("WEBSOCKET_PASSWORD", "test-password")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek")
    get_settings.cache_clear()
    data = json.loads(Path(__file__).resolve().parents[1].joinpath("config.example.json").read_text())
    for key in list(data["agent"]):
        if key.startswith("compression_"):
            data["agent"].pop(key)
    write_json_config(tmp_path, data)

    settings = get_settings()

    assert settings.compression_enabled is True
    assert settings.compression_trigger_ratio == 0.8
    assert settings.compression_keep_recent_turns == 8
    assert settings.compression_summary_max_chars == 2000
    assert settings.compression_timeout == 30


def test_agent_compression_settings_validate_ranges(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_json_config(
        tmp_path,
        {
            "agent": {
                "compression_trigger_ratio": 0,
                "compression_keep_recent_turns": -1,
                "compression_summary_max_chars": 0,
                "compression_timeout": 0,
            }
        },
    )

    with pytest.raises(ValueError) as exc_info:
        Settings()

    message = str(exc_info.value)
    assert "compression_trigger_ratio" in message
    assert "compression_summary_max_chars" in message
    assert "compression_timeout" in message


def test_settings_loads_plain_values_from_config_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_json_config(
        tmp_path,
        {
            "server": {"host": "127.0.0.1", "port": 19132},
            "auth": {"jwt_secret": "secret", "default_password": "pass", "jwt_expiration": 60},
            "providers": {
                "default": "ollama",
                "ollama": {"base_url": "http://localhost:11434", "model": "llama3.1"},
            },
            "queue": {"max_size": 50, "llm_worker_count": 1},
            "logging": {"level": "DEBUG", "enable_file_logging": False},
            "flow_control": {"max_chunk_content_length": 123, "chunk_sentence_mode": False},
        },
    )

    settings = Settings()

    assert settings.host == "127.0.0.1"
    assert settings.port == 19132
    assert settings.jwt_secret == "secret"
    assert settings.default_password == "pass"
    assert settings.jwt_expiration == 60
    assert settings.default_provider == "ollama"
    assert settings.ollama_model == "llama3.1"
    assert settings.queue_max_size == 50
    assert settings.llm_worker_count == 1
    assert settings.log_level == "DEBUG"
    assert settings.enable_file_logging is False
    assert settings.max_chunk_content_length == 123
    assert settings.chunk_sentence_mode is False


def test_json_placeholders_resolve_from_dotenv_and_process_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("WEBSOCKET_PASSWORD", raising=False)
    write_env(
        tmp_path,
        "SECRET_KEY=dotenv-secret\n"
        "WEBSOCKET_PASSWORD=dotenv-pass\n"
        "DEEPSEEK_API_KEY=dotenv-deepseek\n",
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "process-deepseek")
    write_json_config(
        tmp_path,
        {
            "auth": {"jwt_secret": "${SECRET_KEY}", "default_password": "${WEBSOCKET_PASSWORD}"},
            "providers": {
                "default": "deepseek",
                "deepseek": {
                    "api_key": "${DEEPSEEK_API_KEY}",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                },
            },
        },
    )

    settings = Settings()

    assert settings.jwt_secret == "dotenv-secret"
    assert settings.default_password == "dotenv-pass"
    assert settings.deepseek_api_key == "process-deepseek"


def test_missing_placeholder_reports_path_and_variable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_json_config(
        tmp_path,
        {
            "providers": {
                "default": "deepseek",
                "deepseek": {
                    "api_key": "${DEEPSEEK_API_KEY}",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                },
            }
        },
    )
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(ValueError) as exc_info:
        Settings()

    message = str(exc_info.value)
    assert "providers.deepseek.api_key" in message
    assert "DEEPSEEK_API_KEY" in message


def test_empty_placeholder_value_reports_path_and_variable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    write_env(tmp_path, "DEEPSEEK_API_KEY=\n")
    write_json_config(
        tmp_path,
        {
            "providers": {
                "default": "deepseek",
                "deepseek": {
                    "api_key": "${DEEPSEEK_API_KEY}",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                },
            }
        },
    )

    with pytest.raises(ValueError) as exc_info:
        Settings()

    message = str(exc_info.value)
    assert "providers.deepseek.api_key" in message
    assert "DEEPSEEK_API_KEY" in message


def test_old_plain_environment_variables_do_not_override_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOST", "10.0.0.10")
    monkeypatch.setenv("PORT", "25565")
    monkeypatch.setenv("DEFAULT_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_WORKER_COUNT", "8")

    settings = Settings()

    assert settings.host == "0.0.0.0"
    assert settings.port == 8080
    assert settings.default_provider == "deepseek"
    assert settings.llm_worker_count == 2


def test_runtime_settings_require_config_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()

    with pytest.raises(FileNotFoundError) as exc_info:
        get_settings()

    assert str(CONFIG_FILE) in str(exc_info.value)


def test_runtime_settings_reject_incomplete_config_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    write_json_config(tmp_path, {"server": {"host": "127.0.0.1"}})

    with pytest.raises(ValueError) as exc_info:
        get_settings()

    message = str(exc_info.value)
    assert "config.json is incomplete" in message
    assert "server.port" in message


def test_minecraft_commands_merge_defaults_with_user_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_json_config(
        tmp_path,
        {
            "minecraft": {
                "commands": {
                    "#登录": "login",
                    "AGENT 聊天": {
                        "type": "chat",
                        "aliases": ["自定义聊天"],
                        "description": "用户自定义聊天",
                        "usage": "<自定义内容>",
                    },
                    "运行命令": {
                        "type": "run_command",
                        "aliases": ["cmd"],
                        "description": "执行游戏命令",
                        "usage": "<命令>",
                    },
                    "帮助": {
                        "type": "help",
                        "aliases": ["help"],
                        "description": "显示帮助",
                        "usage": None,
                    },
                }
            }
        },
    )

    settings = Settings()
    handler = MinecraftProtocolHandler(settings.minecraft)

    assert "AGENT 对话" in settings.minecraft.commands
    assert handler.parse_command("AGENT 对话 list") == ("conversation", "list")
    assert settings.minecraft.commands["AGENT 聊天"] == {
        "type": "chat",
        "aliases": ["自定义聊天"],
        "description": "用户自定义聊天",
        "usage": "<自定义内容>",
    }
    assert handler.parse_command("自定义聊天 hello") == ("chat", "hello")


def test_minecraft_protocol_uses_injected_config():
    settings = Settings(
        minecraft={
            "commands": {
                "#登录": "login",
                "问": {
                    "type": "chat",
                    "aliases": ["ask"],
                    "description": "自定义聊天",
                    "usage": "<内容>",
                },
                "求助": {
                    "type": "help",
                    "aliases": [],
                    "description": "自定义帮助",
                    "usage": None,
                },
            },
            "welcome_message_template": "help={help_command}; ctx={context_status}",
            "context_enabled_text": "开",
            "context_disabled_text": "关",
            "error_prefix": "ERR:",
            "info_prefix": "INFO:",
            "success_prefix": "OK:",
            "error_color": "red",
            "info_color": "blue",
            "success_color": "green",
        }
    )
    handler = MinecraftProtocolHandler(settings.minecraft)

    assert handler.parse_command("ask hello") == ("chat", "hello")
    assert "问 <内容> - 自定义聊天" in handler.get_help_text()
    assert handler.create_welcome_message("abcdef123456", "model", "provider", False) == (
        "help=求助; ctx=关"
    )
    assert handler.create_error_message("bad").text == "ERR:bad"


def test_model_metadata_settings_loaded_from_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_json_config(
        tmp_path,
        {
            "model_metadata": {
                "enabled": False,
                "source_url": "https://example.test/api.json",
                "refresh_on_startup": False,
                "timeout": 42,
                "cache_path": "custom/cache.json",
            }
        },
    )

    settings = Settings()

    assert settings.model_metadata.enabled is False
    assert settings.model_metadata.source_url == "https://example.test/api.json"
    assert settings.model_metadata.refresh_on_startup is False
    assert settings.model_metadata.timeout == 42
    assert str(settings.model_metadata.cache_path) == "custom/cache.json"


# ---------------------------------------------------------------------------
# Task 5 / 7: get_provider_config 上下文窗口优先级与 worker 间接验证
# ---------------------------------------------------------------------------


def test_provider_context_window_prefers_static_table(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(openai_model="gpt-4o")
    cache = ModelMetadataCache.from_models_dev_api(
        {"openai": {"models": {"gpt-4o": {"limit": {"context": 999}}}}}
    )
    settings.attach_model_metadata_cache(cache)

    assert settings.get_provider_config("openai").context_window == 128000


def test_provider_context_window_uses_models_dev_cache_when_static_missing(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    settings = Settings(openai_model="future-model")
    cache = ModelMetadataCache.from_models_dev_api(
        {"openai": {"models": {"future-model": {"limit": {"context": 123456}}}}}
    )
    settings.attach_model_metadata_cache(cache)

    assert settings.get_provider_config("openai").context_window == 123456


def test_provider_context_window_none_when_both_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(openai_model="future-model")
    settings.attach_model_metadata_cache(ModelMetadataCache.from_models_dev_api({}))

    assert settings.get_provider_config("openai").context_window is None


def test_provider_context_window_ignores_cache_when_metadata_disabled(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        openai_model="future-model",
        model_metadata=ModelMetadataConfig(enabled=False),
    )
    cache = ModelMetadataCache.from_models_dev_api(
        {"openai": {"models": {"future-model": {"limit": {"context": 123456}}}}}
    )
    settings.attach_model_metadata_cache(cache)

    assert settings.get_provider_config("openai").context_window is None


def test_provider_context_window_cache_applies_to_all_providers(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        deepseek_model="ds-future",
        anthropic_model="claude-future",
        ollama_model="ollama-future",
    )
    cache = ModelMetadataCache.from_models_dev_api(
        {
            "deepseek": {"models": {"ds-future": {"limit": {"context": 111}}}},
            "anthropic": {"models": {"claude-future": {"limit": {"context": 222}}}},
            "ollama": {"models": {"ollama-future": {"limit": {"context": 333}}}},
        }
    )
    settings.attach_model_metadata_cache(cache)

    assert settings.get_provider_config("deepseek").context_window == 111
    assert settings.get_provider_config("anthropic").context_window == 222
    assert settings.get_provider_config("ollama").context_window == 333
