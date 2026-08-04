"""运行时 Harness 工具审计测试。"""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from config.settings import Settings
from config.redaction import redact_exception
from services.agent.harness.audit import (
    AuditWriter,
    build_audit_record,
    build_validation_failure_audit_record,
    enqueue_audit_record,
    extract_tool_validation_failures,
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


def test_validation_failure_extractor_matches_call_and_deduplicates_retry_timestamp():
    from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, ToolCallPart

    retry_at = datetime(2026, 8, 2, 15, 0, 0, tzinfo=UTC)
    parameters = {
        "from": [0, 64, 0],
        "to": [4, 64, 4],
        "block": "minecraft:stone",
        "expect": "air",
        "api_key": "do-not-record",
    }
    validation_content = [{
        "type": "json_invalid",
        "loc": ("from",),
        "msg": "Invalid JSON: secret-input",
        "input": "secret-input",
    }]
    retry = RetryPromptPart(
        validation_content,
        tool_name="fill_block",
        tool_call_id="tc-invalid",
        timestamp=retry_at,
    )
    messages = [
        ModelResponse(parts=[
            ToolCallPart(
                tool_name="fill_block",
                args=parameters,
                tool_call_id="tc-invalid",
            )
        ]),
        ModelRequest(parts=[retry, RetryPromptPart(
            validation_content,
            tool_name="fill_block",
            tool_call_id="tc-invalid",
            timestamp=retry_at,
        )]),
    ]

    failures = extract_tool_validation_failures(messages, run_id="run-invalid")

    assert len(failures) == 1
    failure = failures[0]
    assert failure["tool_name"] == "fill_block"
    assert failure["tool_call_id"] == "tc-invalid"
    assert failure["retry_timestamp"] == retry_at.isoformat()
    assert failure["error_type"] == "json_invalid"
    assert failure["error_locations"] == ["from"]
    assert failure["parameters"]["from"] == [0, 64, 0]
    assert failure["parameters"]["block"] == "minecraft:stone"
    assert failure["parameters"]["expect"] == "air"
    assert failure["parameters"].get("api_key") == "[REDACTED]"


def test_validation_failure_audit_record_is_validation_only_and_bounded():
    settings = Settings()
    deps = DummyDeps(settings, run_id="run-invalid")
    ctx = SimpleNamespace(deps=deps, tool_call_id="tc-invalid")
    failure = {
        "tool_name": "edit_blocks",
        "tool_call_id": "tc-invalid",
        "retry_timestamp": "2026-08-02T15:00:00+00:00",
        "error_type": "json_invalid",
        "error_locations": ["edits"],
        "parameters": {"dimension": "minecraft:overworld"},
        "validation_content": [{
            "type": "json_invalid",
            "loc": ["edits"],
            "msg": "secret-input",
            "input": "secret-input",
        }],
    }

    record = build_validation_failure_audit_record(
        failure=failure,
        ctx=ctx,
        run_id="run-invalid",
    )

    assert record["tool_name"] == "edit_blocks"
    assert record["tool_call_id"] == "tc-invalid"
    assert record["status"] == "failure"
    assert record["error_kind"] == "INVALID_ARGUMENT"
    assert record["result"]["failure_reason"] == "json_invalid"
    assert record["result"]["execution_stage"] == "validation"
    assert record["result"]["external_state_unknown"] == "false"
    assert record["validation_error"] == {
        "type": "json_invalid",
        "locations": ["edits"],
    }
    dumped = json.dumps(record, ensure_ascii=False)
    assert "secret-input" not in dumped


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


def test_summarize_result_marks_structured_group_failure_despite_cached_success() -> None:
    """A cached partial edit is operationally failed for audit purposes."""
    result = ToolResult.success(
        '{"ok": false, "status": "partial", "changed_total": 1, "edits": []}'
    )

    summary = summarize_result(result=result)

    assert result.is_success
    assert summary["success"] == "failure"
    assert summary["failure_reason"] == "partial"
    assert summary["error_kind"] == "PERMANENT"


def test_audit_record_keeps_bounded_group_execution_evidence() -> None:
    ctx = SimpleNamespace(deps=DummyDeps(Settings()))
    result = ToolResult(
        output="ok",
        audit_evidence={
            "edits": [{
                "index": 0,
                "before": {"type_id": "minecraft:air"},
                "after": {"type_id": "minecraft:stone"},
                "verification": {"ok": True},
                "player_name": "not-allowed",
                "locked_targets": [{"x": 1, "y": 64, "z": 1}],
            }]
        },
    )

    record = build_audit_record(
        tool_name="edit_blocks",
        parameters={},
        ctx=ctx,
        status="success",
        duration_ms=1,
        result=result,
    )

    assert record["execution_evidence"] == {
        "edits": [{
            "index": 0,
            "before": {"type_id": "minecraft:air"},
            "after": {"type_id": "minecraft:stone"},
            "verification": {"ok": True},
        }]
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


def test_block_approval_audit_keeps_authorized_preview_without_locked_targets() -> None:
    settings = Settings()
    ctx = SimpleNamespace(deps=DummyDeps(settings, run_id="run-block-audit"), tool_call_id="tc-block-audit")
    record = build_audit_record(
        tool_name="fill_block",
        parameters={
            "from": [0, 64, 0],
            "to": [4, 64, 4],
            "block": "minecraft:stone",
            "expect": "air",
        },
        authorized_args={
            "from": [0, 64, 0],
            "to": [4, 64, 4],
            "block": "minecraft:stone",
            "expect": "air",
            "locked_targets": [{"x": 1, "y": 64, "z": 1}],
        },
        approval_evidence={
            "repairs_applied": ["normalized_bounds"],
            "locked_targets": [{"x": 1, "y": 64, "z": 1}],
            "bridge_payload": {"token": "bridge-secret"},
        },
        ctx=ctx,
        status="approval_required",
        duration_ms=4,
        run_id="run-block-audit",
        tool_call_id="tc-block-audit",
    )

    assert record["run_id"] == "run-block-audit"
    assert record["tool_call_id"] == "tc-block-audit"
    assert record["authorized_parameters"]["from"] == [0, 64, 0]
    assert record["authorized_parameters"]["to"] == [4, 64, 4]
    assert record["authorized_parameters"]["block"] == "minecraft:stone"
    assert record["approval_evidence"] == {"repairs_applied": ["normalized_bounds"]}
    dumped = json.dumps(record, ensure_ascii=False)
    assert "locked_targets" not in dumped
    assert "bridge-secret" not in dumped


def test_audit_exception_redacts_sensitive_values_but_keeps_correlation() -> None:
    settings = Settings()
    ctx = SimpleNamespace(deps=DummyDeps(settings, run_id="run-exception"), tool_call_id="tc-exception")
    record = build_audit_record(
        tool_name="edit_blocks",
        parameters={},
        ctx=ctx,
        status="failure",
        duration_ms=5,
        exception=RuntimeError(
            "package.module.edit(token=bridge-secret, password=hunter2, authorization=Bearer abcdef)"
        ),
    )

    dumped = json.dumps(record, ensure_ascii=False)
    assert record["run_id"] == "run-exception"
    assert record["tool_call_id"] == "tc-exception"
    assert "bridge-secret" not in dumped
    assert "hunter2" not in dumped
    assert "Bearer abcdef" not in dumped

    tool_failure = build_audit_record(
        tool_name="edit_blocks",
        parameters={},
        ctx=ctx,
        status="failure",
        duration_ms=5,
        result=ToolResult.failure(
            "failed token=bridge-secret",
            error_kind="INTERNAL",
            diagnostic_summary="package.module.fn(password=hunter2, Bearer abcdef)",
        ),
    )
    tool_dumped = json.dumps(tool_failure, ensure_ascii=False)
    assert "bridge-secret" not in tool_dumped
    assert "hunter2" not in tool_dumped
    assert "Bearer abcdef" not in tool_dumped


@pytest.mark.parametrize(
    "detail",
    [
        '{"nested":{"api_key":"json-secret","authorization":"Bearer json-bearer"}}',
        "{'token': 'repr-secret', 'nested': {'password': 'repr-password'}}",
    ],
)
def test_redact_exception_handles_structured_and_python_repr_secrets(detail: str) -> None:
    redacted = redact_exception(RuntimeError(detail))

    assert redacted is not None
    assert "RuntimeError" in redacted
    for secret in ("json-secret", "json-bearer", "repr-secret", "repr-password"):
        assert secret not in redacted


@pytest.mark.parametrize(
    "detail",
    [
        '{"detail":"Bearer json-bearer","note":"password=json-password","nested":[{"cookie":"json-cookie"}]}',
        '"token=json-scalar-secret"',
        "{'private_key': 'repr-private', 'nested': [{'access_key': 'repr-access'}, {'credential': 'repr-credential'}]}",
    ],
)
def test_redact_exception_scrubs_all_structured_string_leaves(detail: str) -> None:
    redacted = redact_exception(RuntimeError(detail))

    assert redacted is not None
    for secret in (
        "json-bearer", "json-password", "json-cookie", "json-scalar-secret",
        "repr-private", "repr-access", "repr-credential",
    ):
        assert secret not in redacted


def test_redact_exception_handles_hyphen_and_underscore_sensitive_key_aliases() -> None:
    redacted = redact_exception(
        RuntimeError(
            "api-key=api-secret access-key=access-secret private-key=private-secret "
            "x_api_key=x-api-secret set_cookie=cookie-secret"
        )
    )

    assert redacted is not None
    for secret in (
        "api-secret", "access-secret", "private-secret", "x-api-secret", "cookie-secret",
    ):
        assert secret not in redacted


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


@pytest.mark.asyncio
async def test_block_plan_resume_audits_canonical_args_without_hidden_kwargs(
    tmp_path, monkeypatch
) -> None:
    """plan_id 恢复的审计 parameters/authorized_args 从缓存 canonical args 还原。

    隐藏字段（locked_targets/phase/status）不得出现在审计 JSON 中；公共
    工具体体不得被调用（恢复路径直达缓存执行器）。
    """
    from unittest.mock import MagicMock

    from services.agent.block_ops.preflight_cache import get_preflight_cache
    from services.agent.harness import catalog as _catalog_module
    from services.agent.harness.catalog import (
        ParameterPreviewPolicy,
        ToolIntent,
        ToolRisk,
        _entry,
    )
    from services.agent.harness.execution import (
        HarnessToolset,
        PolicyEngine,
        get_block_command_fallback_store,
        get_idempotency_store,
    )

    monkeypatch.setattr(
        _catalog_module,
        "_TOOL_CATALOG",
        {
            **_catalog_module._TOOL_CATALOG,
            "place_block": _entry(
                "place_block",
                ToolIntent.CHANGE_WORLD,
                ToolRisk.HIGH,
                "向世界写入单个方块时使用。",
                "不要用于查询或批量填充。",
                "pos 为 [x, y, z] 绝对坐标；block 为方块 type_id。",
                preview=ParameterPreviewPolicy(include=("pos", "block", "expect")),
                may_have_external_side_effects=True,
            ),
        },
    )

    audit_path = tmp_path / "runtime_harness_tools.jsonl"
    settings = AuditOnlySettings(str(audit_path))
    canonical = {"pos": [1, 64, 1], "block": "stone", "expect": "air"}
    called: dict[str, int] = {"count": 0}

    async def fake_place_impl(
        ctx, *, pos, block, expect="air", states=None
    ):
        called["count"] += 1
        return ToolResult.ok(f"placed:{block}")

    monkeypatch.setattr("services.agent.block_ops.tools_impl.place_block_impl", fake_place_impl)

    get_preflight_cache().put(
        run_id="run-test-1",
        tool_call_id="tc-1",
        original_args_hash="orig-h",
        canonical_args=canonical,
        tool_name="place_block",
        plan_id="pid-audit",
    )

    ts = HarnessToolset(
        wrapped=MagicMock(),
        policy=PolicyEngine.from_settings(settings),
        idempotency=get_idempotency_store(),
        fallback_store=get_block_command_fallback_store(),
    )
    ctx = SimpleNamespace(deps=DummyDeps(settings, run_id="run-test-1"), tool_call_id="tc-1")
    ctx.tool_call_approved = True
    result = await ts.call_tool("place_block", {"plan_id": "pid-audit"}, ctx, MagicMock())
    assert called["count"] == 1
    assert "placed:stone" in str(result)
    _flush()

    records = read_jsonl(audit_path)
    assert len(records) == 1
    record = records[0]
    assert record["tool_name"] == "place_block"
    assert record["status"] == "success"
    assert record["parameters"] == canonical
    assert record["authorized_parameters"] == canonical
    # 隐藏 kwargs 不得出现在审计参数中（record 自身的 status 字段除外）
    for field_name in ("parameters", "authorized_parameters"):
        for hidden in ("locked_targets", "phase", "status"):
            assert hidden not in record[field_name]
    dumped = json.dumps(record, ensure_ascii=False)
    assert "locked_targets" not in dumped
    assert '"phase"' not in dumped
