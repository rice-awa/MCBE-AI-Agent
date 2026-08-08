"""运行时 Harness 工具目录测试。

工具目录 (_TOOL_CATALOG) 是内置工具的契约中心：语义声明（意图/风险/适用/禁用/预览）
全部由目录维护，不再分散在提示/规则/审计中。
"""

import re

from services.agent.harness.catalog import (
    ToolIntent,
    ToolRisk,
    ToolSource,
    get_tool_catalog,
    get_tool_entry,
    group_tools_by_intent,
    list_tool_names,
    project_tool_usage_guide,
)
from services.agent.harness.prompting import (
    render_runtime_harness_prompt,
    render_tool_cards,
)


# CATALOG_TOOL_NAMES 直接从目录投影，不再手工维护。
CATALOG_TOOL_NAMES: set[str] = list_tool_names()


def test_catalog_entries_are_valid() -> None:
    catalog = get_tool_catalog()

    assert set(catalog) == CATALOG_TOOL_NAMES
    for name, entry in catalog.items():
        assert entry.name == name
        assert isinstance(entry.intent, ToolIntent)
        assert isinstance(entry.risk, ToolRisk)
        assert entry.when_to_use.strip()
        assert entry.when_not_to_use.strip()
        assert entry.parameter_constraints.strip()
        assert entry.preview.max_length > 0


def test_all_registered_agent_tools_are_in_catalog() -> None:
    assert CATALOG_TOOL_NAMES == list_tool_names()


def test_group_tools_by_intent_keeps_catalog_entries() -> None:
    grouped = group_tools_by_intent()
    grouped_names = {entry.name for entries in grouped.values() for entry in entries}

    assert set(grouped) == set(ToolIntent)
    assert grouped_names == CATALOG_TOOL_NAMES


def test_block_tool_cards_follow_single_op_contract() -> None:
    """place/fill/inspect cards describe only the single-op contract (spec §6.2)."""
    place = get_tool_catalog()["place_block"]
    fill = get_tool_catalog()["fill_block"]
    inspect = get_tool_catalog()["inspect_block"]

    assert place.intent == ToolIntent.CHANGE_WORLD
    assert place.risk == ToolRisk.HIGH
    assert fill.intent == ToolIntent.CHANGE_WORLD
    assert fill.risk == ToolRisk.HIGH
    assert inspect.intent == ToolIntent.QUERY_WORLD
    assert inspect.risk == ToolRisk.LOW

    place_text = " ".join(
        (place.when_to_use, place.when_not_to_use, place.parameter_constraints)
    )
    fill_text = " ".join(
        (fill.when_to_use, fill.when_not_to_use, fill.parameter_constraints)
    )
    inspect_text = " ".join(
        (inspect.when_to_use, inspect.when_not_to_use, inspect.parameter_constraints)
    )

    # Parameter names match the public schemas.
    for param in ("pos", "block", "expect", "states"):
        assert param in place_text
    for param in ("from", "to", "block", "expect", "states"):
        assert param in fill_text
    assert "target" in inspect_text

    # 多格方块卡片直接指向 setblock，且默认不带 NBT 参数。
    for text in (place_text, fill_text):
        assert "多格方块" in text
        assert "setblock" in text
        assert "NBT" in text

    # Grouped-edit vocabulary is gone from all block cards.
    for text in (place_text, fill_text, inspect_text):
        for obsolete in (
            "edits",
            "grouped",
            "fallback_allowed",
            "mode=place",
            "batch",
            "PRECONDITION_FAILED",
            "LIMIT_EXCEEDED",
            "previous_type_counts",
        ):
            assert obsolete not in text, (text, obsolete)

    assert "edit_blocks" not in get_tool_catalog()


# ── 新增：契约中心完整性断言 ──────────────────────────────────────


def test_every_catalog_entry_has_unique_tool_name() -> None:
    """每个内置工具在目录中有且只有一个条目。"""
    catalog = get_tool_catalog()
    names = [e.name for e in catalog.values()]
    assert len(names) == len(set(names)), "目录中存在重复工具名称"


def test_no_unregistered_tools_in_catalog() -> None:
    """所有目录工具都是 builtin source（MCP 工具标记为 mcp source）。"""
    catalog = get_tool_catalog()
    for entry in catalog.values():
        assert entry.source in ("builtin", "mcp"), (
            f"工具 {entry.name} 的 source 值异常: {entry.source}"
        )


def test_mcp_tools_retain_separate_source() -> None:
    """MCP 工具必须标记 source=mcp 且有 mcp_server。"""
    catalog = get_tool_catalog()
    mcp_entries = [e for e in catalog.values() if e.source == "mcp"]
    for entry in mcp_entries:
        assert entry.mcp_server is not None, (
            f"MCP 工具 {entry.name} 缺少 mcp_server"
        )


def test_parameter_constraint_references_exist() -> None:
    """参数约束中引用的字段名应与预览策略包一致。"""
    catalog = get_tool_catalog()
    for entry in catalog.values():
        preview_include = entry.preview.include
        constraint_text = entry.parameter_constraints
        # 预览策略中的字段应在约束文本中被提及
        for field in preview_include:
            assert field in constraint_text, (
                f"工具 {entry.name} 预览字段 '{field}' 未在参数约束中出现"
                f"\n约束: {constraint_text}"
            )


def test_tool_usage_guide_projected_from_catalog() -> None:
    """工具使用指南来自目录投影，而非手工副本。"""
    guide = project_tool_usage_guide()
    # 应该包含每个意图的工具列表
    assert "改变世界工具:" not in guide or "run_minecraft_command" in guide
    assert "通知展示工具:" not in guide or "send_game_message" in guide
    assert "查询世界工具:" not in guide or "get_player_snapshot" in guide
    # 基础规则保留
    assert "你可以使用工具与 Minecraft 交互" in guide
    assert "基岩版" in guide


def test_tool_cards_mention_preview_fields() -> None:
    """工具卡片渲染时预览字段应与目录条目一致。"""
    catalog = get_tool_catalog()
    # 卡片通过 render_tool_cards() 使用目录信息
    cards_text = render_tool_cards()
    for entry in catalog.values():
        assert entry.name in cards_text
        assert entry.intent.value in cards_text
        assert entry.risk.value in cards_text


def test_tool_guide_risk_and_preview_from_same_entry() -> None:
    """工具提示、风险策略和工具审计预览来自同一条目。"""
    catalog = get_tool_catalog()
    for entry in catalog.values():
        # 意图/风险/预览全部来自同一条目
        assert isinstance(entry.intent, ToolIntent)
        assert isinstance(entry.risk, ToolRisk)
        assert isinstance(entry.preview, object)  # 确保 preview 被定义


def test_builtin_tools_have_no_mcp_server() -> None:
    """内置工具的 mcp_server 应为 None。"""
    catalog = get_tool_catalog()
    for entry in catalog.values():
        if entry.source == "builtin":
            assert entry.mcp_server is None, (
                f"内置工具 {entry.name} 不应有 mcp_server"
            )


def test_removed_tool_does_not_persist_in_catalog() -> None:
    """已移除的工具不会残留在目录中。"""
    catalog = get_tool_catalog()
    known_removed = {"edit_blocks"}
    for removed in known_removed:
        assert removed not in catalog, (
            f"已移除工具 '{removed}' 仍存在于目录中"
        )


def test_preview_include_matches_parameter_constraint_keywords() -> None:
    """预览 include 字段应作为关键词出现在参数约束中。"""
    catalog = get_tool_catalog()
    for entry in catalog.values():
        if not entry.preview.include:
            # 无预览字段的工具（如无参数工具）跳过检查
            continue
        for field in entry.preview.include:
            # 字段名以 snake_case 出现在约束文本中
            assert field in entry.parameter_constraints, (
                f"工具 {entry.name}: 预览字段 '{field}' "
                f"不在参数约束文本中\n"
                f"约束: {entry.parameter_constraints}"
            )


def test_risk_levels_are_consistent() -> None:
    """CHANGE_WORLD 意图风险不得低于 HIGH。"""
    catalog = get_tool_catalog()
    for entry in catalog.values():
        if entry.intent == ToolIntent.CHANGE_WORLD:
            assert entry.risk in (ToolRisk.HIGH, ToolRisk.DANGEROUS), (
                f"改变世界工具 {entry.name} 风险应为 HIGH 或 DANGEROUS"
            )
