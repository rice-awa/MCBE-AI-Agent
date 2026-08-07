"""运行时 Harness 提示注入测试。"""

from config.settings import Settings
from services.agent.harness import render_runtime_harness_prompt
from services.agent.prompt import PromptManager, PromptTemplate, TOOL_USAGE_GUIDE


def test_runtime_harness_prompt_renders_decision_tree_and_cards() -> None:
    prompt = render_runtime_harness_prompt()

    assert "工具意图决策" in prompt
    assert "工具卡片" in prompt
    assert "run_minecraft_command [改变世界/高]" in prompt
    assert "方块操作编排规则" in prompt
    # spec §6.1 bulleted rules
    assert "连续区域（地板/墙体/屋顶）用 fill_block" in prompt
    assert "单格用 place_block" in prompt
    assert "inspect_block 只在需要确认世界状态时调用" in prompt
    assert "expect 默认 air（仅替换空气）" in prompt
    assert "要覆盖非空方块用 expect=any（需再审批）" in prompt
    assert "失败时只读 code 与 hint" in prompt
    assert "仅 fallback_allowed=true 时才能考虑命令回退" in prompt
    assert "同一轮可以并行发出多个相互独立的 fill/place" in prompt
    assert "多格方块" in prompt
    assert "setblock" in prompt
    assert "NBT" in prompt
    # Cards for the new single-op tools.
    assert "place_block [改变世界/高]" in prompt
    assert "fill_block [改变世界/高]" in prompt
    assert "inspect_block [查询世界/低]" in prompt
    for obsolete in (
        "edit_blocks",
        "grouped edits",
        "合并为一个 edit_blocks",
        "同一施工阶段",
        "一次预检 + 一次审批",
        "小而完整的施工阶段",
        "写前读取和写后确认",
        "通常不超过 4 个 edits",
        "最短 target",
        "mode=place|batch|fill",
        "place×N",
        "replace_any",
        "PRECONDITION_FAILED",
        "previous_type_counts",
    ):
        assert obsolete not in prompt, obsolete


def test_block_tool_cards_omit_recovery_codes() -> None:
    """Recovery (LIMIT/PRECONDITION) is system-only; cards stay slim."""
    from services.agent.harness.catalog import get_tool_entry

    for name in ("place_block", "fill_block"):
        entry = get_tool_entry(name)
        assert entry is not None
        constraints = entry.parameter_constraints
        assert "LIMIT_EXCEEDED" not in constraints
        assert "PRECONDITION_FAILED" not in constraints
        assert "previous_type_counts" not in constraints
        assert "fallback_allowed" not in constraints

    inspect = get_tool_entry("inspect_block")
    assert inspect is not None
    assert "target" in inspect.parameter_constraints
    assert "LIMIT_EXCEEDED" not in inspect.parameter_constraints
    assert get_tool_entry("edit_blocks") is None


def test_runtime_harness_tool_cards_do_not_duplicate_when_not_to_use_prefix() -> None:
    prompt = render_runtime_harness_prompt()

    assert "不要用于不要用于" not in prompt
    assert "不要用于不要作为" not in prompt


def test_system_prompt_uses_configured_base_prompt() -> None:
    manager = PromptManager()
    settings = Settings(system_prompt="请使用配置里的系统提示词")

    prompt = manager.build_system_prompt(
        connection_id="configured-system-prompt",
        player_name="TestPlayer",
        provider="deepseek",
        model="deepseek-chat",
        settings=settings,
    )

    assert "请使用配置里的系统提示词" in prompt
    assert "请始终保持积极和专业的态度" not in prompt


def test_system_prompt_uses_runtime_harness_by_default() -> None:
    manager = PromptManager()

    prompt = manager.build_system_prompt(
        connection_id="runtime-harness-default",
        player_name="TestPlayer",
        provider="deepseek",
        model="deepseek-chat",
        settings=Settings(),
    )

    assert "工具意图决策" in prompt
    assert "run_minecraft_command [改变世界/高]" in prompt


def test_system_prompt_falls_back_to_old_tool_usage_guide() -> None:
    manager = PromptManager()
    settings = Settings(runtime_harness_prompt_enabled=False)

    prompt = manager.build_system_prompt(
        connection_id="runtime-harness-disabled",
        player_name="TestPlayer",
        provider="deepseek",
        model="deepseek-chat",
        settings=settings,
    )

    assert TOOL_USAGE_GUIDE in prompt
    assert "工具意图决策" not in prompt


def test_custom_template_receives_runtime_harness_tool_usage() -> None:
    manager = PromptManager()
    manager.register_template(
        PromptTemplate(
            name="runtime-custom",
            description="自定义运行时 Harness 模板",
            content="自定义前缀\n{tool_usage}\n玩家={player_name}",
        )
    )
    manager.set_session_template("runtime-harness-custom", "TestPlayer", "runtime-custom")

    prompt = manager.build_system_prompt(
        connection_id="runtime-harness-custom",
        player_name="TestPlayer",
        provider="deepseek",
        model="deepseek-chat",
        settings=Settings(),
    )

    assert prompt.startswith("自定义前缀")
    assert "工具意图决策" in prompt
    assert "玩家=TestPlayer" in prompt
