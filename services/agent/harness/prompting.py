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
    "- 同一施工阶段中互相独立的编辑合并为一个 edit_blocks 调用。\n"
    "- 有顺序依赖的编辑拆到下一组，不依赖列表顺序模拟世界状态。\n"
    "- edit_blocks 已内置写前读取和写后确认，不要机械地前后调用 inspect_block。\n"
    "- 只有专用方块工具结构化结果明确给出 fallback_allowed=true，"
    "才可另行调用命令工具并遵循其审批策略。"
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
