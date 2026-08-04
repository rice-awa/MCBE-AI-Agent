"""Agent 工具函数测试"""

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest
from pydantic_ai import Agent

from config.settings import LLMProviderConfig, Settings
from models.agent import AgentDependencies
from models.minecraft import MinecraftCommand
from services.agent.harness.audit import flush_audit_writer, start_audit_writer, stop_audit_writer
from services.agent.harness.prompting import render_schema_description_prefix
from services.agent.tool_results import CommandResult, ToolResult
from services.agent.tools import (
    build_actionbar_command,
    build_tellraw_command,
    build_title_commands,
    escape_command_text,
    get_agent_tools_container,
    iter_registered_tools,
    register_agent_tools,
)


def _flush_audit() -> None:
    """Async audit writer needs a drain before tests read JSONL."""
    try:
        start_audit_writer()
    except Exception:
        pass
    flush_audit_writer(timeout=2.0)


def _unwrap_registered_tool_function(function):
    """剥掉测试/stringify 外壳，拿到带原始签名的工具函数。"""
    current = function
    for _ in range(4):
        if getattr(current, "_tool_result_stringified", False):
            original = (getattr(current, "__kwdefaults__", None) or {}).get("_function")
            if original is not None:
                current = original
                continue
        wrapped = getattr(current, "__wrapped__", None)
        if wrapped is not None:
            current = wrapped
            continue
        break
    return current


def _wrap_tools_for_direct_audit(agent: Agent, settings: Settings) -> None:
    """测试专用：直接 function 调用时挂 audit；先解 stringify 再 wrap，最后再 stringify。

    生产路径只走 HarnessToolset，不调用本 helper。
    """
    from services.agent.harness.audit import wrap_tool_function

    toolset = get_agent_tools_container(agent)
    assert toolset is not None
    for tool_name, tool in getattr(toolset, "tools", {}).items():
        function = getattr(tool, "function", None)
        if function is None or getattr(function, "_runtime_harness_audited", False):
            continue
        original = _unwrap_registered_tool_function(function)
        audited = wrap_tool_function(tool_name, original, settings)
        setattr(audited, "_runtime_harness_audited", True)

        async def stringified(*args, _audited=audited, **kwargs):
            result = await _audited(*args, **kwargs)
            return str(result)

        setattr(stringified, "_tool_result_stringified", True)
        setattr(stringified, "_runtime_harness_audited", True)
        tool.function = stringified
        function_schema = getattr(tool, "function_schema", None)
        if function_schema is not None and hasattr(function_schema, "function"):
            function_schema.function = stringified


def _tool(agent: Agent, name: str):
    tools = iter_registered_tools(agent)
    assert name in tools, f"tool {name} not registered; have {list(tools)}"
    return tools[name]


def test_schema_description_prefix_contains_catalog_fields() -> None:
    prefix = render_schema_description_prefix("run_minecraft_command")

    assert "[运行时 Harness]" in prefix
    assert "意图: 改变世界" in prefix
    assert "风险: 高" in prefix
    assert "适用:" in prefix
    assert "禁用:" in prefix
    assert "参数:" in prefix


def test_registered_tool_description_includes_runtime_harness_prefix() -> None:
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent, settings=Settings())

    description = _tool(agent, "run_minecraft_command").description

    assert description is not None
    assert description.startswith("[运行时 Harness]")
    assert "执行 Minecraft 命令" in description


def test_registered_tool_description_can_skip_runtime_harness_prefix() -> None:
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    settings = Settings(runtime_harness_schema_enabled=False)
    register_agent_tools(agent, settings=settings)

    description = _tool(agent, "run_minecraft_command").description

    assert description is not None
    assert not description.startswith("[运行时 Harness]")
    assert "执行 Minecraft 命令" in description


def test_schema_description_prefix_covers_all_block_tools() -> None:
    place = render_schema_description_prefix("place_block")
    assert "[运行时 Harness]" in place
    assert "意图: 改变世界" in place
    assert "风险: 高" in place

    fill = render_schema_description_prefix("fill_block")
    assert "意图: 改变世界" in fill
    assert "风险: 高" in fill

    inspect = render_schema_description_prefix("inspect_block")
    assert "意图: 查询世界" in inspect
    assert "风险: 低" in inspect


def test_block_tool_descriptions_get_prefix_without_keyerror_guard() -> None:
    """Every registered block tool resolves a catalog entry (guard removed)."""
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent, settings=Settings())

    for name in ("place_block", "fill_block", "inspect_block"):
        description = _tool(agent, name).description
        assert description is not None
        assert description.startswith("[运行时 Harness]"), name


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeHttpClient:
    def __init__(self, payload: dict):
        self.payload = payload
        self.requests: list[tuple[str, dict | None]] = []

    async def get(self, url: str, params: dict | None = None):
        self.requests.append((url, params))
        return _FakeResponse(self.payload)


class _NarrowRuntimeSettings:
    default_provider = "deepseek"
    system_prompt = "test prompt"
    stream_sentence_mode = True
    mcwiki_base_url = "https://wiki.example.test"
    runtime_harness_enabled = True
    runtime_harness_prompt_enabled = True
    runtime_harness_schema_enabled = False
    runtime_harness_audit_enabled = False
    runtime_harness_audit_path = "unused.jsonl"
    runtime_harness_audit_max_records = 1

    def get_provider_config(self, provider_name: str | None = None) -> LLMProviderConfig:
        return LLMProviderConfig(
            name=provider_name or self.default_provider,
            api_key="test-key",
            model="test-model",
            enabled=True,
        )

    def list_available_providers(self) -> list[str]:
        return ["deepseek", "ollama"]


async def _noop_send_to_game(message: str) -> None:
    return None


async def _noop_command(_: str) -> CommandResult:
    return CommandResult.ok("ok")


@pytest.mark.asyncio
async def test_agent_tools_accept_narrow_runtime_settings() -> None:
    settings = _NarrowRuntimeSettings()
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent, settings=settings)
    http_client = _FakeHttpClient(
        {
            "success": True,
            "data": {
                "results": [
                    {
                        "title": "Stone",
                        "url": "https://wiki.example.test/Stone",
                        "snippet": "block",
                    }
                ]
            },
        }
    )
    deps = AgentDependencies(
        connection_id=uuid4(),
        player_name="Alex",
        settings=settings,
        http_client=http_client,
        send_to_game=_noop_send_to_game,
        run_command=_noop_command,
    )
    ctx = SimpleNamespace(deps=deps)

    providers = await _tool(agent, "list_available_providers").function(ctx)
    search = await _tool(agent, "mcwiki_search").function(ctx, "stone")

    assert providers == "可用 Provider: deepseek, ollama"
    assert "Stone" in search
    assert "stone" in search
    assert http_client.requests[0][0] == "https://wiki.example.test/api/search"
    assert http_client.requests[0][1] is not None
    assert http_client.requests[0][1]["limit"] == 10


@pytest.mark.asyncio
async def test_registered_tool_function_writes_audit_jsonl(tmp_path) -> None:
    audit_path = tmp_path / "tools.jsonl"
    settings = Settings(runtime_harness_audit_path=str(audit_path))
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent, settings=settings)
    # 生产 register 不再 wrap；测试直接 function 调用时显式挂 audit
    _wrap_tools_for_direct_audit(agent, settings)

    async def run_command(command: str) -> CommandResult:
        return CommandResult.ok("ok")

    deps = AgentDependencies(
        connection_id=uuid4(),
        player_name="Alex",
        settings=settings,
        http_client=SimpleNamespace(),
        send_to_game=_noop_send_to_game,
        run_command=run_command,
        provider="ollama",
        run_id="run-audit-1",
        conversation_id="conv-1",
    )
    ctx = SimpleNamespace(deps=deps)

    result = await _tool(agent, "run_minecraft_command").function(ctx, "say hi")

    assert result == "ok"
    _flush_audit()
    records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["tool_name"] == "run_minecraft_command"
    assert records[0]["player_name"] == "Alex"
    assert records[0]["parameters"] == {"command": "say hi"}
    assert records[0]["result"]["success"] == "success"


@pytest.mark.asyncio
async def test_registered_tool_audit_uses_structured_failure(tmp_path) -> None:
    audit_path = tmp_path / "tools.jsonl"
    settings = Settings(runtime_harness_audit_path=str(audit_path))
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent, settings=settings)
    _wrap_tools_for_direct_audit(agent, settings)

    async def run_command(command: str) -> CommandResult:
        return CommandResult.failed("bad cmd")

    deps = AgentDependencies(
        connection_id=uuid4(),
        player_name="Alex",
        settings=settings,
        http_client=SimpleNamespace(),
        send_to_game=_noop_send_to_game,
        run_command=run_command,
        provider="ollama",
    )
    ctx = SimpleNamespace(deps=deps)

    result = await _tool(agent, "run_minecraft_command").function(ctx, "say hi")

    assert "bad cmd" in str(result) or "失败" in str(result)
    _flush_audit()
    records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["tool_name"] == "run_minecraft_command"
    assert records[0]["status"] == "failure"
    assert records[0]["result"]["success"] == "failure"
    assert records[0]["result"]["error_kind"] in {"PERMANENT", "INVALID_ARGUMENT", "INTERNAL"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command_result,expected_status,expected_error_kind,expected_unknown",
    [
        (CommandResult.ok("done"), "success", None, False),
        (CommandResult.failed("bad cmd"), "failure", "PERMANENT", False),
        (
            CommandResult.timeout_unknown("timeout"),
            "failure",
            "TRANSIENT",
            True,
        ),
        (
            CommandResult.connection_unavailable("gone"),
            "failure",
            "TRANSIENT",
            False,
        ),
    ],
)
async def test_run_command_tool_structured_status_shared_by_model_and_audit(
    tmp_path,
    command_result: CommandResult,
    expected_status: str,
    expected_error_kind: str | None,
    expected_unknown: bool,
) -> None:
    """参数化覆盖成功/明确失败/超时未知/断线：模型返回与审计状态一致。"""
    audit_path = tmp_path / "tools.jsonl"
    settings = Settings(runtime_harness_audit_path=str(audit_path))
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent, settings=settings)
    _wrap_tools_for_direct_audit(agent, settings)

    async def run_command(command: str) -> CommandResult:
        return command_result

    deps = AgentDependencies(
        connection_id=uuid4(),
        player_name="Alex",
        settings=settings,
        http_client=SimpleNamespace(),
        send_to_game=_noop_send_to_game,
        run_command=run_command,
        provider="ollama",
        run_id="run-param",
    )
    ctx = SimpleNamespace(deps=deps)

    # 直接拿到未 stringify 前的 ToolResult：调用内部逻辑
    from services.agent.tool_results import ToolResult as TR

    mapped = TR.from_command_result(command_result)
    assert mapped.status == expected_status
    assert mapped.error_kind == expected_error_kind
    assert mapped.external_state_unknown is expected_unknown

    model_text = await _tool(agent, "run_minecraft_command").function(ctx, "say hi")
    assert model_text == mapped.output

    _flush_audit()
    records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["result"]["success"] == expected_status
    if expected_status == "failure":
        assert records[0]["status"] == "failure"
        assert records[0]["result"]["error_kind"] == expected_error_kind
        assert records[0]["result"]["external_state_unknown"] == (
            "true" if expected_unknown else "false"
        )
    else:
        assert records[0]["status"] == "success"


def test_register_agent_tools_does_not_mutate_tool_functions() -> None:
    """生产 register 不得修改已注册函数对象（政策入口仅 WrapperToolset）。"""
    settings = Settings(runtime_harness_audit_enabled=True)
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent, settings=settings)
    for name, tool in iter_registered_tools(agent).items():
        fn = tool.function
        assert not getattr(fn, "_runtime_harness_audited", False), name


@pytest.mark.asyncio
async def test_message_tools_default_to_triggering_player() -> None:
    settings = Settings(runtime_harness_audit_enabled=False)
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent, settings=settings)
    commands: list[str] = []

    async def run_command(command: str) -> CommandResult:
        commands.append(command)
        return CommandResult.ok("ok")

    deps = AgentDependencies(
        connection_id=uuid4(),
        player_name="Alex",
        settings=settings,
        http_client=SimpleNamespace(),
        send_to_game=_noop_send_to_game,
        run_command=run_command,
    )
    ctx = SimpleNamespace(deps=deps)

    await _tool(agent, "send_game_message").function(ctx, "plain")
    await _tool(agent, "send_colored_message").function(ctx, "hello")
    await _tool(agent, "send_title_message").function(ctx, "title")
    await _tool(agent, "send_actionbar_message").function(ctx, "hint")

    assert commands[0].startswith("tellraw Alex")
    assert commands[1].startswith("tellraw Alex")
    assert commands[2] == 'title Alex title "title"'
    assert commands[3] == "title Alex times 10 70 20"
    assert commands[4] == 'title Alex actionbar "hint"'


@pytest.mark.asyncio
async def test_message_tools_use_all_players_only_when_broadcast_true() -> None:
    settings = Settings(runtime_harness_audit_enabled=False)
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent, settings=settings)
    commands: list[str] = []

    async def run_command(command: str) -> CommandResult:
        commands.append(command)
        return CommandResult.ok("ok")

    deps = AgentDependencies(
        connection_id=uuid4(),
        player_name="Alex",
        settings=settings,
        http_client=SimpleNamespace(),
        send_to_game=_noop_send_to_game,
        run_command=run_command,
    )
    ctx = SimpleNamespace(deps=deps)

    await _tool(agent, "send_game_message").function(ctx, "plain", broadcast=True)
    await _tool(agent, "send_colored_message").function(ctx, "hello", broadcast=True)
    await _tool(agent, "send_title_message").function(ctx, "title", broadcast=True)
    await _tool(agent, "send_actionbar_message").function(ctx, "hint", broadcast=True)

    assert commands[0].startswith("tellraw @a")
    assert commands[1].startswith("tellraw @a")
    assert commands[2] == 'title @a title "title"'
    assert commands[3] == "title @a times 10 70 20"
    assert commands[4] == 'title @a actionbar "hint"'


@pytest.mark.asyncio
async def test_send_script_event_rejects_invalid_message_id() -> None:
    settings = Settings(runtime_harness_audit_enabled=False)
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent, settings=settings)
    commands: list[str] = []

    async def run_command(command: str) -> CommandResult:
        commands.append(command)
        return CommandResult.ok("ok")

    deps = AgentDependencies(
        connection_id=uuid4(),
        player_name="Alex",
        settings=settings,
        http_client=SimpleNamespace(),
        send_to_game=_noop_send_to_game,
        run_command=run_command,
    )
    ctx = SimpleNamespace(deps=deps)

    result = await _tool(agent, "send_script_event").function(
        ctx,
        "payload",
        "server:data; say hacked",
    )

    assert "脚本事件发送失败" in result
    assert commands == []


def test_create_scriptevent_rejects_uppercase_message_id() -> None:
    for message_id in ("Server:Data", "MCBEWS:TEXT_RESP"):
        with pytest.raises(ValueError):
            MinecraftCommand.create_scriptevent("payload", message_id)


def test_escape_command_text() -> None:
    assert escape_command_text('Hello "MC"') == 'Hello \\"MC\\"'
    assert escape_command_text("Line1\nLine2") == "Line1\\nLine2"


def test_build_title_commands() -> None:
    commands = build_title_commands("主标题", "副标题", 10, 70, 20, "Alex")
    assert commands[0] == 'title Alex title "主标题"'
    assert commands[1] == 'title Alex subtitle "副标题"'
    assert commands[2] == "title Alex times 10 70 20"


def test_build_title_commands_broadcast_target() -> None:
    commands = build_title_commands("主标题", None, 10, 70, 20, "@a")
    assert commands[0] == 'title @a title "主标题"'
    assert commands[1] == "title @a times 10 70 20"


def test_build_actionbar_command() -> None:
    command = build_actionbar_command("提示", "Alex")
    assert command == 'title Alex actionbar "提示"'


def test_build_tellraw_command() -> None:
    command = build_tellraw_command("测试", "§b", "Alex")
    assert command.startswith("tellraw Alex")
    assert "§b" in command


def test_no_private_function_toolset_access_in_tools_module() -> None:
    source = Path(ROOT, "services/agent/tools.py").read_text(encoding="utf-8")
    assert "_function_toolset" not in source
    # 允许公开 helper 名称中的 "tools container"，禁止私有字段访问
    assert "._function_toolset" not in source


def test_tool_result_player_message_hides_diagnostics() -> None:
    result = ToolResult.failure(
        "内部细节 stacktrace",
        error_kind="INTERNAL",
        diagnostic_summary="TypeError: boom",
    )
    assert "stacktrace" not in result.player_message()
    assert "TypeError" not in result.player_message()
    assert result.diagnostic_summary == "TypeError: boom"


def test_block_tool_schemas_only_expose_public_fields() -> None:
    """三个新方块工具 schema 只暴露公开参数，不含隐藏字段。"""
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent, settings=Settings(runtime_harness_schema_enabled=False))

    tools = iter_registered_tools(agent)

    # 旧工具已移除
    assert "edit_blocks" not in tools, "edit_blocks 不应再注册"

    # place_block
    assert "place_block" in tools, "place_block 应已注册"
    place_schema = tools["place_block"].tool_def.parameters_json_schema or {}
    place_props = place_schema.get("properties", {})
    assert set(place_props) == {"pos", "block", "expect", "states"}, (
        f"place_block 参数应为 pos/block/expect/states，实际: {set(place_props)}"
    )
    assert "edits" not in place_props
    assert "dimension" not in place_props
    assert "mode" not in place_props
    assert "positions" not in place_props
    assert "coordinate_mode" not in place_props
    assert "locked_targets" not in place_props
    assert "phase" not in place_props
    assert "status" not in place_props

    # fill_block
    assert "fill_block" in tools, "fill_block 应已注册"
    fill_schema = tools["fill_block"].tool_def.parameters_json_schema or {}
    fill_props = fill_schema.get("properties", {})
    assert set(fill_props) == {"from", "to", "block", "expect", "states"}, (
        f"fill_block 参数应为 from/to/block/expect/states，实际: {set(fill_props)}"
    )
    assert "edits" not in fill_props
    assert "dimension" not in fill_props
    assert "mode" not in fill_props
    assert "positions" not in fill_props
    assert "coordinate_mode" not in fill_props
    assert "locked_targets" not in fill_props
    assert "phase" not in fill_props
    assert "status" not in fill_props

    # inspect_block
    assert "inspect_block" in tools, "inspect_block 应已注册"
    inspect_schema = tools["inspect_block"].tool_def.parameters_json_schema or {}
    inspect_props = inspect_schema.get("properties", {})
    assert set(inspect_props) == {"target"}, (
        f"inspect_block 参数应仅为 target，实际: {set(inspect_props)}"
    )
    assert "edits" not in inspect_props
    assert "dimension" not in inspect_props
    assert "mode" not in inspect_props
    assert "positions" not in inspect_props
    assert "coordinate_mode" not in inspect_props
    assert "locked_targets" not in inspect_props
    assert "phase" not in inspect_props
    assert "status" not in inspect_props


def test_block_tool_pos_expect_and_states_constraints() -> None:
    """pos 长度 3、expect 默认 air、states 可选、fill_block JSON 参数名为 from/to。"""
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent, settings=Settings(runtime_harness_schema_enabled=False))

    tools = iter_registered_tools(agent)

    # place_block pos 约束
    place_schema = tools["place_block"].tool_def.parameters_json_schema or {}
    pos_schema = place_schema["properties"]["pos"]
    assert pos_schema.get("minItems") == 3
    assert pos_schema.get("maxItems") == 3
    assert pos_schema.get("type") == "array"

    # expect 默认值
    expect_schema = place_schema["properties"]["expect"]
    assert expect_schema.get("default") == "air"

    # states 可选
    assert "states" not in (place_schema.get("required") or [])

    # fill_block 参数名
    fill_schema = tools["fill_block"].tool_def.parameters_json_schema or {}
    fill_props = fill_schema["properties"]
    assert "from" in fill_props, "fill_block 应有 from（非 from_）"
    assert "to" in fill_props


def test_inspect_block_target_union_schema() -> None:
    """inspect_block.target 为单点或双角点二选一。"""
    agent = Agent("test", deps_type=AgentDependencies, output_type=str)
    register_agent_tools(agent, settings=Settings(runtime_harness_schema_enabled=False))

    tools = iter_registered_tools(agent)

    inspect_schema = tools["inspect_block"].tool_def.parameters_json_schema or {}
    target_schema = inspect_schema["properties"]["target"]

    # union 类型应产生 anyOf
    assert "anyOf" in target_schema, f"target schema 应有 anyOf，实际: {list(target_schema)}"
    any_of = target_schema["anyOf"]
    assert len(any_of) == 2, f"anyOf 应有 2 个分支，实际: {len(any_of)}"

    # 分支 1: 单点 [x,y,z]
    single = any_of[0]
    assert single.get("type") == "array"
    assert single.get("minItems") == 3
    assert single.get("maxItems") == 3

    # 分支 2: 双角点 [[x1,y1,z1],[x2,y2,z2]]
    dual = any_of[1]
    assert dual.get("type") == "array"
    assert dual.get("minItems") == 2
    assert dual.get("maxItems") == 2
