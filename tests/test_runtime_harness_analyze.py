"""运行时 Harness 审计分析测试。"""

import json
from types import SimpleNamespace

import pytest

from services.agent.harness.analyze import analyze_records, apply_llm_suggestions, read_recent_records


def write_jsonl(path, records):
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )


def audit_record(tool_name, *, risk="低", status="success", success="success", duration_ms=100, failure_reason=None):
    return {
        "tool_name": tool_name,
        "risk": risk,
        "status": status,
        "duration_ms": duration_ms,
        "result": {
            "success": success,
            "result_preview": None,
            "failure_reason": failure_reason,
        },
    }


def test_read_recent_records_returns_recent_valid_jsonl(tmp_path):
    audit_path = tmp_path / "runtime_harness_tools.jsonl"
    audit_path.write_text(
        "\n".join([
            json.dumps({"index": 1}),
            "not-json",
            json.dumps({"index": 2}),
            json.dumps({"index": 3}),
        ]),
        encoding="utf-8",
    )

    records = read_recent_records(audit_path, recent=2)

    assert records == [{"index": 2}, {"index": 3}]


def test_read_recent_records_missing_file_returns_empty(tmp_path):
    assert read_recent_records(tmp_path / "missing.jsonl", recent=100) == []


def test_analyze_records_aggregates_stats_and_issues():
    records = [
        audit_record("run_minecraft_commands", risk="危险", duration_ms=3000),
        audit_record("run_minecraft_commands", risk="危险", duration_ms=5000),
        audit_record(
            "run_minecraft_commands",
            risk="危险",
            status="failure",
            success="failure",
            duration_ms=4000,
            failure_reason="参数 commands 缺少有效命令",
        ),
        audit_record(
            "run_minecraft_commands",
            risk="危险",
            success="failure",
            duration_ms=6000,
            failure_reason="invalid argument commands",
        ),
    ]

    analysis = analyze_records(records)

    assert analysis["totals"] == {"calls": 4, "failures": 2}
    assert analysis["failure_rate"] == 0.5
    assert analysis["average_duration_ms"] == 4500.0
    assert analysis["risk_distribution"] == {"危险": 4}
    assert analysis["llm_suggestions_used"] is False
    issue = analysis["top_issue_tools"][0]
    assert issue["tool_name"] == "run_minecraft_commands"
    assert issue["reasons"] == [
        "high_failure_rate",
        "dangerous_tool_frequent",
        "parameter_errors_concentrated",
        "high_duration",
    ]
    assert analysis["suggestions"]




def test_analyze_records_top_issue_sorting_ties_by_tool_name():
    records = [
        audit_record("second_tool", status="failure", success="failure", duration_ms=2500),
        audit_record("first_tool", status="failure", success="failure", duration_ms=2500),
    ]

    analysis = analyze_records(records)

    assert [issue["tool_name"] for issue in analysis["top_issue_tools"]] == [
        "first_tool",
        "second_tool",
    ]


def test_analyze_records_empty_has_zero_totals_and_suggestion():
    analysis = analyze_records([])

    assert analysis["totals"] == {"calls": 0, "failures": 0}
    assert analysis["failure_rate"] == 0.0
    assert analysis["average_duration_ms"] == 0.0
    assert analysis["risk_distribution"] == {}
    assert analysis["top_issue_tools"] == []
    assert analysis["suggestions"] == ["暂无审计记录；先保持审计开启并积累工具调用样本。"]


@pytest.mark.asyncio
async def test_apply_llm_suggestions_uses_default_provider_without_tools(monkeypatch):
    analysis = analyze_records([
        audit_record(
            "run_minecraft_commands",
            risk="危险",
            status="failure",
            success="failure",
            duration_ms=3000,
            failure_reason="参数 commands 无效",
        ),
    ])
    analysis["raw_player_message"] = "secret player message"
    analysis["parameters"] = {"command": "secret command"}
    settings = SimpleNamespace(
        default_provider="deepseek",
        get_provider_config=lambda provider: SimpleNamespace(name=provider, model="deepseek-chat"),
    )
    captured = {}

    def fake_get_model(provider_config):
        captured["provider_config"] = provider_config
        return "fake-model"

    class FakeAgent:
        def __init__(self, model, *, output_type, instructions, toolsets):
            captured["model"] = model
            captured["output_type"] = output_type
            captured["instructions"] = instructions
            captured["toolsets"] = toolsets

        async def run(self, prompt):
            captured["prompt"] = prompt
            return SimpleNamespace(output="- 收窄高失败工具的使用条件\n2. 强化危险工具调用确认")

    from services.agent.providers import ProviderRegistry
    import pydantic_ai

    monkeypatch.setattr(ProviderRegistry, "get_model", staticmethod(fake_get_model))
    monkeypatch.setattr(pydantic_ai, "Agent", FakeAgent)

    result = await apply_llm_suggestions(analysis, settings)

    assert result["suggestions"] == [
        "收窄高失败工具的使用条件",
        "强化危险工具调用确认",
    ]
    assert result["llm_suggestions_used"] is True
    assert result["llm_suggestions_fallback"] is None
    assert captured["provider_config"].name == "deepseek"
    assert captured["model"] == "fake-model"
    assert captured["output_type"] is str
    assert captured["toolsets"] is None
    assert "secret" not in captured["prompt"]
    assert "raw_player_message" not in captured["prompt"]
    assert "parameters" not in captured["prompt"]


@pytest.mark.asyncio
async def test_apply_llm_suggestions_falls_back_to_rule_suggestions(monkeypatch):
    analysis = analyze_records([])
    rule_suggestions = list(analysis["suggestions"])
    settings = SimpleNamespace(
        default_provider="deepseek",
        get_provider_config=lambda provider: SimpleNamespace(name=provider),
    )

    class FailingAgent:
        def __init__(self, model, *, output_type, instructions, toolsets):
            pass

        async def run(self, prompt):
            raise RuntimeError("model unavailable")

    from services.agent.providers import ProviderRegistry
    import pydantic_ai

    monkeypatch.setattr(ProviderRegistry, "get_model", staticmethod(lambda provider_config: "fake-model"))
    monkeypatch.setattr(pydantic_ai, "Agent", FailingAgent)

    result = await apply_llm_suggestions(analysis, settings)

    assert result["suggestions"] == rule_suggestions
    assert result["llm_suggestions_used"] is False
    assert result["llm_suggestions_fallback"] == "model unavailable"
