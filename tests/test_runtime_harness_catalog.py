"""运行时 Harness 工具目录测试。"""

from services.agent.harness.catalog import (
    ToolIntent,
    ToolRisk,
    get_tool_catalog,
    group_tools_by_intent,
    list_tool_names,
)

REGISTERED_AGENT_TOOL_NAMES = {
    "run_minecraft_command",
    "run_minecraft_commands",
    "send_game_message",
    "send_colored_message",
    "send_title_message",
    "send_actionbar_message",
    "send_script_event",
    "mcwiki_search",
    "mcwiki_get_page",
    "mcwiki_check_page_exists",
    "mcwiki_check_health",
    "mcwiki_list_namespaces",
    "list_available_providers",
    "get_player_snapshot",
    "get_look_block",
    "get_inventory_snapshot",
    "find_entities",
    "run_world_command",
    "place_block",
    "fill_block",
    "inspect_block",
}


def test_catalog_entries_are_valid() -> None:
    catalog = get_tool_catalog()

    assert set(catalog) == REGISTERED_AGENT_TOOL_NAMES
    for name, entry in catalog.items():
        assert entry.name == name
        assert isinstance(entry.intent, ToolIntent)
        assert isinstance(entry.risk, ToolRisk)
        assert entry.when_to_use.strip()
        assert entry.when_not_to_use.strip()
        assert entry.parameter_constraints.strip()
        assert entry.preview.max_length > 0


def test_all_registered_agent_tools_are_in_catalog() -> None:
    assert REGISTERED_AGENT_TOOL_NAMES <= list_tool_names()


def test_group_tools_by_intent_keeps_catalog_entries() -> None:
    grouped = group_tools_by_intent()
    grouped_names = {entry.name for entries in grouped.values() for entry in entries}

    assert set(grouped) == set(ToolIntent)
    assert grouped_names == REGISTERED_AGENT_TOOL_NAMES


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
