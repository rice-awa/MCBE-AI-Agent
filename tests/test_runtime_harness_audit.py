"""运行时 Harness 工具审计测试。"""

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest

from config.settings import Settings
from services.agent.harness.audit import (
    preview_parameters,
    summarize_result,
    wrap_tool_function,
    write_audit_record,
)
from services.agent.harness.catalog import ToolCatalogEntry
from services.agent.tool_results import ToolResult


class DummyDeps:
    def __init__(self, settings: Settings) -> None:
        self.connection_id = uuid4()
        self.player_name = "Steve"
        self.provider = "deepseek"
        self.settings = settings


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.asyncio
async def test_successful_tool_call_writes_jsonl(tmp_path):
    audit_path = tmp_path / "runtime_harness_tools.jsonl"
    settings = Settings(runtime_harness_audit_path=str(audit_path))

    async def fake_tool(ctx, command: str):
        return "命令执行成功"

    wrapped = wrap_tool_function("run_minecraft_command", fake_tool, settings)
    ctx = SimpleNamespace(deps=DummyDeps(settings))

    result = await wrapped(ctx, "say hello")

    assert result == "命令执行成功"
    records = read_jsonl(audit_path)
    assert len(records) == 1
    record = records[0]
    assert record["tool_name"] == "run_minecraft_command"
    assert record["status"] == "success"
    assert record["player_name"] == "Steve"
    assert record["provider"] == "deepseek"
    assert record["parameters"] == {"command": "say hello"}
    assert record["result"]["success"] == "success"
    assert record["result"]["result_preview"] is None
    assert record["result"]["failure_reason"] is None


@pytest.mark.asyncio
async def test_failed_tool_call_writes_jsonl(tmp_path):
    audit_path = tmp_path / "runtime_harness_tools.jsonl"
    settings = Settings(runtime_harness_audit_path=str(audit_path))

    async def fake_tool(ctx, command: str):
        raise RuntimeError("boom")

    wrapped = wrap_tool_function("run_minecraft_command", fake_tool, settings)
    ctx = SimpleNamespace(deps=DummyDeps(settings))

    with pytest.raises(RuntimeError):
        await wrapped(ctx, command="say fail")

    records = read_jsonl(audit_path)
    assert len(records) == 1
    record = records[0]
    assert record["status"] == "failure"
    assert record["parameters"] == {"command": "say fail"}
    assert record["result"]["success"] == "failure"
    assert record["result"]["failure_reason"] == "boom"


def test_parameter_redaction_and_truncation_work():
    parameters = {
        "command": "x" * 130,
        "raw_player_message": "do not record",
    }

    preview = preview_parameters("run_minecraft_command", parameters)

    assert preview == {"command": "x" * 120 + "..."}
    assert "raw_player_message" not in preview


def test_records_rotate_after_exceeding_max_records(tmp_path):
    audit_path = tmp_path / "runtime_harness_tools.jsonl"

    for index in range(5):
        write_audit_record({"index": index}, audit_path, max_records=3)

    records = read_jsonl(audit_path)
    assert [record["index"] for record in records] == [2, 3, 4]


def test_concurrent_records_do_not_lose_writes(tmp_path):
    audit_path = tmp_path / "runtime_harness_tools.jsonl"
    record_count = 50

    def write_record(index: int) -> None:
        write_audit_record({"index": index}, audit_path, max_records=record_count)

    with ThreadPoolExecutor(max_workers=12) as executor:
        list(executor.map(write_record, range(record_count)))

    records = read_jsonl(audit_path)
    assert len(records) == record_count
    assert sorted(record["index"] for record in records) == list(range(record_count))


@pytest.mark.asyncio
async def test_settings_none_wrapper_does_not_write_jsonl(tmp_path):
    audit_path = tmp_path / "runtime_harness_tools.jsonl"
    deps_settings = Settings(runtime_harness_audit_path=str(audit_path))

    async def fake_tool(ctx, command: str):
        return "private command output"

    wrapped = wrap_tool_function("run_minecraft_command", fake_tool, None)
    ctx = SimpleNamespace(deps=DummyDeps(deps_settings))

    assert await wrapped(ctx, command="say hidden") == "private command output"
    assert not audit_path.exists()


@pytest.mark.asyncio
async def test_returned_failure_string_records_failure_reason(tmp_path):
    audit_path = tmp_path / "runtime_harness_tools.jsonl"
    settings = Settings(runtime_harness_audit_path=str(audit_path))

    async def fake_tool(ctx, command: str):
        return "命令执行失败: denied"

    wrapped = wrap_tool_function("run_minecraft_command", fake_tool, settings)
    ctx = SimpleNamespace(deps=DummyDeps(settings))

    assert await wrapped(ctx, command="say fail") == "命令执行失败: denied"

    records = read_jsonl(audit_path)
    assert len(records) == 1
    record = records[0]
    assert record["status"] == "success"
    assert record["result"]["success"] == "failure"
    assert record["result"]["result_preview"] is None
    assert record["result"]["failure_reason"] == "命令执行失败: denied"


def test_summarize_result_uses_structured_tool_result_status() -> None:
    result = ToolResult.failure("命令执行失败: denied")

    summary = summarize_result(result=result)

    assert str(result) == "命令执行失败: denied"
    assert summary == {
        "success": "failure",
        "result_preview": None,
        "failure_reason": "命令执行失败: denied",
    }


def test_summarize_result_uses_structured_tool_result_success() -> None:
    summary = summarize_result(result=ToolResult.success("命令执行失败: literal output"))

    assert summary == {
        "success": "success",
        "result_preview": None,
        "failure_reason": None,
    }


@pytest.mark.asyncio
async def test_audit_disabled_does_not_write_jsonl(tmp_path):
    audit_path = tmp_path / "runtime_harness_tools.jsonl"
    settings = Settings(
        runtime_harness_audit_enabled=False,
        runtime_harness_audit_path=str(audit_path),
    )

    async def fake_tool(ctx, command: str):
        return "ok"

    wrapped = wrap_tool_function("run_minecraft_command", fake_tool, settings)
    ctx = SimpleNamespace(deps=DummyDeps(settings))

    assert await wrapped(ctx, command="say hidden") == "ok"
    assert not audit_path.exists()
