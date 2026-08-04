"""运行时 Harness 提示渲染。"""

from services.agent.harness.catalog import ToolIntent, get_tool_entry, group_tools_by_intent

_INTENT_GUIDANCE: dict[ToolIntent, str] = {
    ToolIntent.CHANGE_WORLD: (
        "玩家明确要求执行命令、修改世界或改变实体状态时使用；"
        "优先选择契约最贴近玩家目标的专用工具。"
    ),
    ToolIntent.NOTIFY_DISPLAY: "玩家要求在游戏中展示消息、标题、actionbar 或脚本事件时使用。",
    ToolIntent.QUERY_WORLD: (
        "玩家要求查询当前玩家、背包、实体或世界状态时使用。"
        "方块状态查询优先使用 inspect_block，不要用命令试探。"
    ),
    ToolIntent.QUERY_KNOWLEDGE: "玩家要求查询 Minecraft Wiki 或受控知识资料时使用。",
    ToolIntent.SYSTEM_INFO: "玩家询问可用 provider 等系统状态时使用。",
}


_BLOCK_TOOL_PRIORITY = (
    "方块操作编排规则：\n"
    "- 连续区域（地板/墙体/屋顶）用 fill_block；单格用 place_block。\n"
    "- inspect_block 只在需要确认世界状态时调用，不要机械地在每次编辑前先查一遍。\n"
    "- expect 默认 air（仅替换空气）；要覆盖非空方块用 expect=any（需再审批）。\n"
    "- 失败时只读 code 与 hint；仅 fallback_allowed=true 时才能考虑命令回退。\n"
    "- 同一轮可以并行发出多个相互独立的 fill/place。"
)


def render_tool_decision_tree() -> str:
    lines = ["工具意图决策："]
    for intent in ToolIntent:
        lines.append(f"- {intent.value}：{_INTENT_GUIDANCE[intent]}")
    return "\n".join(lines)


def render_tool_cards() -> str:
    lines = ["工具卡片："]
    for entries in group_tools_by_intent().values():
        for entry in entries:
            lines.append(
                f"- {entry.name} [{entry.intent.value}/{entry.risk.value}]："
                f"用于{entry.when_to_use.rstrip('。')}；"
                f"{entry.when_not_to_use.rstrip('。')}；"
                f"{entry.parameter_constraints.rstrip('。')}。"
            )
    return "\n".join(lines)


def render_runtime_harness_prompt() -> str:
    return "\n\n".join(
        (
            "你可以使用工具与 MCBE 交互。先判断玩家意图，再选择风险最低且能完成目标的工具。",
            _BLOCK_TOOL_PRIORITY,
            render_tool_decision_tree(),
            render_tool_cards(),
        )
    )


def render_schema_description_prefix(tool_name: str) -> str:
    entry = get_tool_entry(tool_name)
    if entry is None:
        raise KeyError(tool_name)
    return (
        f"[运行时 Harness] 意图: {entry.intent.value}; 风险: {entry.risk.value}; "
        f"适用: {entry.when_to_use}; 禁用: {entry.when_not_to_use}; "
        f"参数: {entry.parameter_constraints}\n\n"
    )
