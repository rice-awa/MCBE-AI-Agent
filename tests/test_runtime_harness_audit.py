"""运行时 Harness 工具审计测试。"""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest

from config.settings import Settings
from services.agent.harness.audit import (
    AuditWriter,
    build_audit_record,
    enqueue_audit_record,
    flush_audit_writer,
    get_audit_writer,
    preview_parameters,
    set_audit_writer,
    start_audit_writer,
    stop_audit_writer,
    summarize_result,
    wrap_tool_function,
    write_audit_record,
)
from services.agent.tool_results import ToolResult


class AuditOnlySettings:
    runtime_harness_enabled = True
    runtime_harness_audit_enabled = True
    runtime_harness_audit_max_records = 5000

    def __init__(self, audit_path: str) -> None:
        self.runtime_harness_audit_path = audit_path


class DummyDeps:
    def __init__(self, settings: object, *, run_id: str | None = "run-test-1") -> None:
        self.connection_id = uuid4()
        self.player_name = "Steve"
        self.provider = "deepseek"
        self.settings = settings
        self.run_id = run_id


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture(autouse=True)
def _isolated_audit_writer():
    """每个测试使用独立 writer，避免全局队列串扰。"""
    writer = AuditWriter()
    set_audit_writer(writer)
    start_audit_writer()
    try:
        yield writer
    finally:
        try:
            stop_audit_writer(timeout=2.0)
        except Exception:
            pass
        set_audit_writer(None)


def _flush() -> None:
    flush_audit_writer(timeout=2.0)


@pytest.mark.asyncio
async def test_successful_tool_call_writes_jsonl(tmp_path):
    audit_path = tmp_path / "runtime_harness_tools.jsonl"
    settings = Settings(runtime_harness_audit_path=str(audit_path))

    async def fake_tool(ctx, command: str):
        return "命令执行成功"

    wrapped = wrap_tool_function("run_minecraft_command", fake_tool, settings)
    ctx = SimpleNamespace(deps=DummyDeps(settings), tool_call_id="tc-1")

    result = await wrapped(ctx, "say hello")
    _flush()

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
    assert record["run_id"] == "run-test-1"
    assert record["tool_call_id"] == "tc-1"


@pytest.mark.asyncio
async def test_successful_tool_call_accepts_audit_only_settings(tmp_path):
    audit_path = tmp_path / "runtime_harness_tools.jsonl"
    settings = AuditOnlySettings(str(audit_path))

    async def fake_tool(ctx, command: str):
        return "命令执行成功"

    wrapped = wrap_tool_function("run_minecraft_command", fake_tool, settings)
    ctx = SimpleNamespace(deps=DummyDeps(settings))

    assert await wrapped(ctx, "say hello") == "命令执行成功"
    _flush()

    records = read_jsonl(audit_path)
    assert records[0]["tool_name"] == "run_minecraft_command"
    assert records[0]["player_name"] == "Steve"


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
    _flush()

    records = read_jsonl(audit_path)
    assert len(records) == 1
    record = records[0]
    assert record["status"] == "failure"
    assert record["parameters"] == {"command": "say fail"}
    assert record["result"]["success"] == "failure"
    assert "boom" in (record["result"]["failure_reason"] or "")
    assert record["error_kind"] == "INTERNAL"


def test_parameter_redaction_and_truncation_work():
    parameters = {
        "command": "x" * 130,
        "raw_player_message": "do not record",
        "api_key": "secret-value",
        "password": "secret-value",
    }

    preview = preview_parameters("run_minecraft_command", parameters)

    # truncate_for_log 把 suffix 计入 max_length（DEFAULT_PARAM_MAX=120）
    assert preview["command"] == "x" * 117 + "..."
    assert len(preview["command"]) == 120
    assert "raw_player_message" not in preview
    assert preview.get("api_key") == "[REDACTED]"
    assert preview.get("password") == "[REDACTED]"


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
    _flush()
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
    _flush()

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
    assert summary["success"] == "failure"
    assert summary["result_preview"] is None
    assert summary["failure_reason"] == "命令执行失败: denied"
    assert summary["error_kind"] == "PERMANENT"
    assert summary["external_state_unknown"] == "false"


def test_summarize_result_uses_structured_tool_result_success() -> None:
    summary = summarize_result(result=ToolResult.success("命令执行失败: literal output"))

    assert summary["success"] == "success"
    assert summary["result_preview"] is None
    assert summary["failure_reason"] is None
    assert summary["error_kind"] is None
    assert summary["external_state_unknown"] == "false"


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
    _flush()
    assert not audit_path.exists()


# --- Task 4a invariants -------------------------------------------------

def test_enqueue_does_not_block_on_disk_io(tmp_path, monkeypatch):
    """enqueue 不得因磁盘 I/O 阻塞调用方；工具结果路径只 put_nowait。"""
    audit_path = tmp_path / "runtime_harness_tools.jsonl"
    writer = get_audit_writer()

    def slow_write(record, path, max_records):  # noqa: ARG001
        time.sleep(0.4)
        raise OSError("disk full")

    monkeypatch.setattr(
        "services.agent.harness.audit.write_audit_record",
        slow_write,
    )

    started = time.perf_counter()
    enqueue_audit_record(
        {"tool_name": "run_minecraft_command", "run_id": "r1", "status": "success"},
        audit_path,
        100,
    )
    elapsed = time.perf_counter() - started
    assert elapsed < 0.15, f"enqueue blocked for {elapsed:.3f}s"

    # flush 会等到队列项被处理（失败仅告警）
    writer.flush(timeout=2.0)
    assert not audit_path.exists()


def test_sensitive_fields_redacted_in_audit_record(tmp_path):
    audit_path = tmp_path / "runtime_harness_tools.jsonl"
    settings = Settings(runtime_harness_audit_path=str(audit_path))
    deps = DummyDeps(settings, run_id="run-redact")
    ctx = SimpleNamespace(deps=deps, tool_call_id="tc-redact")

    record = build_audit_record(
        tool_name="run_minecraft_command",
        parameters={
            "command": "say hi",
            "api_key": "secret-value",
            "authorization": "Bearer secret-value",
            "raw_player_message": "full player content must not appear",
        },
        ctx=ctx,
        status="success",
        duration_ms=12,
        result=ToolResult.success("ok"),
        tool_call_id="tc-redact",
        policy_version="2026-07-21.1",
        run_id="run-redact",
    )

    assert record["run_id"] == "run-redact"
    assert record["tool_call_id"] == "tc-redact"
    assert record["policy_version"] == "2026-07-21.1"
    assert record["parameters"]["command"] == "say hi"
    assert record["parameters"].get("api_key") == "[REDACTED]"
    assert record["parameters"].get("authorization") == "[REDACTED]"
    assert "raw_player_message" not in record["parameters"]
    assert record["result"]["result_preview"] is None
    dumped = json.dumps(record, ensure_ascii=False)
    assert "secret-value" not in dumped
    assert "full player content" not in dumped

    enqueue_audit_record(record, audit_path, 100)
    _flush()
    on_disk = read_jsonl(audit_path)[0]
    assert on_disk["parameters"].get("api_key") == "[REDACTED]"
    assert "secret-value" not in json.dumps(on_disk)


@pytest.mark.asyncio
async def test_shutdown_flush_persists_queued_records(tmp_path):
    audit_path = tmp_path / "runtime_harness_tools.jsonl"
    settings = Settings(runtime_harness_audit_path=str(audit_path))

    async def fake_tool(ctx, command: str):
        return "命令执行成功"

    wrapped = wrap_tool_function("run_minecraft_command", fake_tool, settings)
    ctx = SimpleNamespace(deps=DummyDeps(settings, run_id="run-flush"), tool_call_id="tc-flush")

    assert await wrapped(ctx, "say flush-me") == "命令执行成功"
    # 不手动 flush；通过 stop 触发 shutdown flush
    stop_audit_writer(timeout=3.0)

    assert audit_path.exists()
    records = read_jsonl(audit_path)
    assert len(records) == 1
    assert records[0]["run_id"] == "run-flush"
    assert records[0]["tool_name"] == "run_minecraft_command"
