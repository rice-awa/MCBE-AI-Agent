import json
from pathlib import Path

from click.testing import CliRunner

from cli import cli
from config.settings import get_settings


def test_serve_fails_fast_when_config_json_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()

    result = CliRunner().invoke(cli, ["serve"])

    assert result.exit_code != 0
    assert "config.json" in result.output
    assert "python cli.py init" in result.output
    assert "受限模式" not in result.output


def test_info_command_shows_context_window_for_providers(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("WEBSOCKET_PASSWORD", "test-password")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek")
    get_settings.cache_clear()
    data = json.loads(
        Path(__file__).resolve().parents[1].joinpath("config.example.json").read_text(
            encoding="utf-8"
        )
    )
    (tmp_path / "config.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )

    result = CliRunner().invoke(cli, ["info"])

    assert result.exit_code == 0
    assert "context:" in result.output
    assert "context: 128000" in result.output


def test_info_command_shows_unknown_context_for_unrecognized_model(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("WEBSOCKET_PASSWORD", "test-password")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek")
    get_settings.cache_clear()
    data = json.loads(
        Path(__file__).resolve().parents[1].joinpath("config.example.json").read_text(
            encoding="utf-8"
        )
    )
    data["providers"]["ollama"]["model"] = "unrecognized-future-model"
    (tmp_path / "config.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )

    result = CliRunner().invoke(cli, ["info"])

    assert result.exit_code == 0
    assert "context: unknown" in result.output
