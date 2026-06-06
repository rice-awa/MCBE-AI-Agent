import json

import pytest

from config.settings import CONFIG_FILE, Settings, get_settings
from services.websocket.minecraft import MinecraftProtocolHandler


def write_json_config(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def write_env(tmp_path, content):
    path = tmp_path / ".env"
    path.write_text(content, encoding="utf-8")
    return path


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
