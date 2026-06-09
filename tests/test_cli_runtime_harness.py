"""运行时 Harness CLI 测试。"""

import json
from types import SimpleNamespace

from click.testing import CliRunner

import cli as cli_module
from cli import cli
import services.agent.harness.analyze as analyze_module


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

    async def fake_apply_llm_suggestions(analysis, settings):
        return analysis

    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    monkeypatch.setattr(analyze_module, "apply_llm_suggestions", fake_apply_llm_suggestions)

    result = CliRunner().invoke(cli, ["runtime-harness", "analyze", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["recent"] == 5000
    assert payload["llm_suggestions_used"] is False


def test_runtime_harness_analyze_default_mode_uses_llm_helper(tmp_path, monkeypatch):
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
    async def fake_apply_llm_suggestions(analysis, settings):
        analysis["suggestions"] = ["LLM 聚合建议：收窄高失败工具使用条件。"]
        analysis["llm_suggestions_used"] = True
        analysis["llm_suggestions_fallback"] = None
        return analysis

    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    monkeypatch.setattr(analyze_module, "apply_llm_suggestions", fake_apply_llm_suggestions)

    result = CliRunner().invoke(cli, ["runtime-harness", "analyze", "--recent", "1"])

    assert result.exit_code == 0
    assert "LLM 聚合建议：收窄高失败工具使用条件。" in result.output
    assert "已使用默认 Provider 生成 LLM 建议" in result.output

    json_result = CliRunner().invoke(cli, ["runtime-harness", "analyze", "--recent", "1", "--json"])

    assert json_result.exit_code == 0
    payload = json.loads(json_result.output)
    assert payload["suggestions"] == ["LLM 聚合建议：收窄高失败工具使用条件。"]
    assert payload["llm_suggestions_used"] is True


def test_runtime_harness_analyze_default_mode_falls_back_when_llm_helper_fails(tmp_path, monkeypatch):
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

    async def fake_apply_llm_suggestions(analysis, settings):
        analysis["llm_suggestions_used"] = False
        analysis["llm_suggestions_fallback"] = "model unavailable"
        return analysis

    monkeypatch.setattr(cli_module, "get_settings", lambda: settings)
    monkeypatch.setattr(analyze_module, "apply_llm_suggestions", fake_apply_llm_suggestions)

    result = CliRunner().invoke(cli, ["runtime-harness", "analyze", "--recent", "1"])

    assert result.exit_code == 0
    assert "LLM 建议生成失败，已回退规则模板输出: model unavailable" in result.output

    json_result = CliRunner().invoke(cli, ["runtime-harness", "analyze", "--recent", "1", "--json"])

    assert json_result.exit_code == 0
    payload = json.loads(json_result.output)
    assert payload["llm_suggestions_used"] is False
    assert payload["llm_suggestions_fallback"] == "model unavailable"
