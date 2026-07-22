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
    "get_inventory_snapshot",
    "find_entities",
    "run_world_command",
    "inspect_block",
    "edit_blocks",
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
