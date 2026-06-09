"""运行时 Harness CLI 测试。"""

import json
from types import SimpleNamespace

from click.testing import CliRunner

import cli as cli_module
from cli import cli


def test_runtime_harness_analyze_json_handles_missing_audit_file(tmp_path, monkeypatch):
    settings = SimpleNamespace(
        runtime_harness_audit_path=str(tmp_path / "missing.jsonl"),
        runtime_harness_audit_max_records=50,
    )
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)

    result = CliRunner().invoke(cli, ["runtime-harness", "analyze", "--json", "--no-llm"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["recent"] == 50
    assert payload["totals"] == {"calls": 0, "failures": 0}
    assert payload["failure_rate"] == 0.0
    assert payload["llm_suggestions_used"] is False


def test_runtime_harness_analyze_text_outputs_core_stats(tmp_path, monkeypatch):
    audit_path = tmp_path / "runtime_harness_tools.jsonl"
    audit_path.write_text(
        json.dumps({
            "tool_name": "run_minecraft_command",
            "risk": "高",
            "status": "failure",
            "duration_ms": 2500,
            "result": {
                "success": "failure",
                "result_preview": None,
                "failure_reason": "参数 command 无效",
            },
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    settings = SimpleNamespace(
        runtime_harness_audit_path=str(audit_path),
        runtime_harness_audit_max_records=5000,
    )
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)

    result = CliRunner().invoke(cli, ["runtime-harness", "analyze", "--recent", "1", "--no-llm"])

    assert result.exit_code == 0
    assert "总调用: 1" in result.output
    assert "失败率: 100.00%" in result.output
    assert "平均耗时: 2500.00 ms" in result.output
    assert "风险分布: 高=1" in result.output
    assert "已按 --no-llm 跳过 LLM 建议" in result.output


def test_runtime_harness_analyze_rejects_non_positive_recent():
    result = CliRunner().invoke(cli, ["runtime-harness", "analyze", "--recent", "0"])

    assert result.exit_code != 0
    assert "Invalid value for '--recent'" in result.output
    assert "0 is not in the range x>=1" in result.output



def test_runtime_harness_analyze_default_recent_uses_configured_max(tmp_path, monkeypatch):
    settings = SimpleNamespace(
        runtime_harness_audit_path=str(tmp_path / "missing.jsonl"),
        runtime_harness_audit_max_records=5000,
    )
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)

    result = CliRunner().invoke(cli, ["runtime-harness", "analyze", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["recent"] == 5000
    assert payload["llm_suggestions_used"] is False


def test_runtime_harness_analyze_default_mode_uses_rule_fallback(tmp_path, monkeypatch):
    audit_path = tmp_path / "runtime_harness_tools.jsonl"
    audit_path.write_text(
        json.dumps({
            "tool_name": "query_minecraft_state",
            "risk": "低",
            "status": "success",
            "duration_ms": 100,
            "result": {
                "success": "success",
                "result_preview": "ok",
                "failure_reason": None,
            },
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    settings = SimpleNamespace(
        runtime_harness_audit_path=str(audit_path),
        runtime_harness_audit_max_records=5000,
    )
    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)

    result = CliRunner().invoke(cli, ["runtime-harness", "analyze", "--recent", "1"])

    assert result.exit_code == 0
    assert "未启用 LLM 建议，本次使用规则模板输出。" in result.output

    json_result = CliRunner().invoke(cli, ["runtime-harness", "analyze", "--recent", "1", "--json"])

    assert json_result.exit_code == 0
    payload = json.loads(json_result.output)
    assert payload["llm_suggestions_used"] is False
