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
