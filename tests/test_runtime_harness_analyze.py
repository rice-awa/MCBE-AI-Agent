"""运行时 Harness 审计分析测试。"""

import json

from services.agent.harness.analyze import analyze_records, read_recent_records


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
