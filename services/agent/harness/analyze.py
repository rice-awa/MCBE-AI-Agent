"""运行时 Harness 审计分析。"""

from __future__ import annotations

import json
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

from services.agent.harness.catalog import ToolRisk, get_tool_entry

AuditRecord = dict[str, Any]


def read_recent_records(path: str | Path, recent: int) -> list[AuditRecord]:
    """读取最近 N 条有效 JSONL 审计记录。"""
    audit_path = Path(path)
    if not audit_path.exists() or recent <= 0:
        return []

    records: deque[AuditRecord] = deque(maxlen=recent)
    with audit_path.open(encoding="utf-8") as file:
        for line in file:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return list(records)


def analyze_records(records: list[AuditRecord]) -> dict[str, Any]:
    """聚合运行时 Harness 审计统计与规则建议。"""
    total = len(records)
    failures = sum(1 for record in records if _is_failure(record))
    durations = [_duration(record) for record in records if _duration(record) is not None]
    risk_distribution = dict(Counter(_risk(record) for record in records))
    top_issue_tools = _top_issue_tools(records)

    return {
        "totals": {
            "calls": total,
            "failures": failures,
        },
        "failure_rate": round(failures / total, 4) if total else 0.0,
        "average_duration_ms": round(sum(durations) / len(durations), 2) if durations else 0.0,
        "risk_distribution": risk_distribution,
        "top_issue_tools": top_issue_tools,
        "suggestions": _rule_suggestions(total, failures, risk_distribution, top_issue_tools),
        "llm_suggestions_used": False,
    }


def _top_issue_tools(records: list[AuditRecord]) -> list[dict[str, Any]]:
    grouped: dict[str, list[AuditRecord]] = defaultdict(list)
    for record in records:
        tool_name = str(record.get("tool_name") or "unknown")
        grouped[tool_name].append(record)

    issues: list[dict[str, Any]] = []
    for tool_name, tool_records in grouped.items():
        calls = len(tool_records)
        failures = sum(1 for record in tool_records if _is_failure(record))
        durations = [_duration(record) for record in tool_records if _duration(record) is not None]
        average_duration_ms = round(sum(durations) / len(durations), 2) if durations else 0.0
        risk = _risk(tool_records[0])
        reasons = _issue_reasons(tool_records, calls, failures, average_duration_ms, risk)
        if not reasons:
            continue
        failure_rate = round(failures / calls, 4) if calls else 0.0
        issues.append({
            "tool_name": tool_name,
            "calls": calls,
            "failures": failures,
            "failure_rate": failure_rate,
            "average_duration_ms": average_duration_ms,
            "risk": risk,
            "reasons": reasons,
        })

    issues.sort(
        key=lambda issue: (
            -len(issue["reasons"]),
            -issue["failure_rate"],
            -issue["calls"],
            -issue["average_duration_ms"],
            issue["tool_name"],
        ),
    )
    return issues[:5]


def _issue_reasons(
    records: list[AuditRecord],
    calls: int,
    failures: int,
    average_duration_ms: float,
    risk: str,
) -> list[str]:
    reasons: list[str] = []
    failure_rate = failures / calls if calls else 0.0
    parameter_failures = sum(1 for record in records if _has_parameter_failure(record))

    if failures >= 2 and failure_rate >= 0.3:
        reasons.append("high_failure_rate")
    if risk == str(ToolRisk.DANGEROUS) and calls >= 2:
        reasons.append("dangerous_tool_frequent")
    if parameter_failures >= 2 and parameter_failures / max(1, failures) >= 0.5:
        reasons.append("parameter_errors_concentrated")
    if average_duration_ms >= 2000:
        reasons.append("high_duration")
    return reasons


def _rule_suggestions(
    total: int,
    failures: int,
    risk_distribution: dict[str, int],
    top_issue_tools: list[dict[str, Any]],
) -> list[str]:
    if total == 0:
        return ["暂无审计记录；先保持审计开启并积累工具调用样本。"]

    suggestions: list[str] = []
    if failures:
        suggestions.append("优先复查失败工具的参数约束与错误提示，降低重复失败率。")
    dangerous_count = risk_distribution.get(str(ToolRisk.DANGEROUS), 0)
    if dangerous_count:
        suggestions.append("危险工具已被调用；建议强化确认条件，避免批量命令成为默认选择。")
    for issue in top_issue_tools[:3]:
        suggestions.append(_suggest_for_issue(issue))
    if not suggestions:
        suggestions.append("当前样本未发现明显工具风险；继续观察失败率和高耗时调用。")
    return suggestions


def _suggest_for_issue(issue: dict[str, Any]) -> str:
    tool_name = issue["tool_name"]
    reasons = set(issue["reasons"])
    entry = get_tool_entry(tool_name)
    constraint = f"参数约束：{entry.parameter_constraints}" if entry is not None else "请补充明确的参数约束。"
    if "parameter_errors_concentrated" in reasons:
        return f"{tool_name} 参数错误集中；建议在工具卡片中强调{constraint}"
    if "dangerous_tool_frequent" in reasons:
        return f"{tool_name} 属危险工具且调用频繁；建议提示词要求玩家明确列出每条命令后再调用。"
    if "high_duration" in reasons:
        return f"{tool_name} 平均耗时偏高；建议提示词优先使用更窄查询或降低返回长度。"
    return f"{tool_name} 失败率偏高；建议复查何时使用/何时不用规则并增加失败场景提示。"


def _is_failure(record: AuditRecord) -> bool:
    result = record.get("result")
    result_failure = isinstance(result, dict) and result.get("success") == "failure"
    return record.get("status") == "failure" or result_failure


def _duration(record: AuditRecord) -> float | None:
    value = record.get("duration_ms")
    if isinstance(value, int | float):
        return float(value)
    return None


def _risk(record: AuditRecord) -> str:
    return str(record.get("risk") or "unknown")


def _has_parameter_failure(record: AuditRecord) -> bool:
    result = record.get("result")
    if not isinstance(result, dict):
        return False
    reason = str(result.get("failure_reason") or "").lower()
    keywords = ("parameter", "argument", "required", "invalid", "参数", "缺少", "无效", "格式")
    return any(keyword in reason for keyword in keywords)
