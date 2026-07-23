"""运行时 Harness 提示渲染。"""

from services.agent.harness.catalog import ToolIntent, get_tool_entry, group_tools_by_intent

_INTENT_GUIDANCE: dict[ToolIntent, str] = {
    ToolIntent.CHANGE_WORLD: (
        "玩家明确要求执行命令、修改世界或改变实体状态时使用。"
        "凡 inspect_block/edit_blocks 能表达的方块查询与写入，必须优先使用专用工具，"
        "不要手写 setblock/fill；仅当专用工具不可用或能力不足时，再审批使用命令工具。"
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
    "方块操作优先策略：\n"
    "- 查询用 inspect_block；写入用 edit_blocks（mode=place|batch|fill）。\n"
    "- 平台/地板/墙：优先一次 fill 或少量 batch；禁止对连续区域 place×N。\n"
    "- 默认仅替换空气；覆写非空须 replace_any=true（高风险再审批）。\n"
    "- 删除：放置 minecraft:air 并授权覆写。\n"
    "- 成功结果：was / previous_type_counts 仅含被替换的非空气方块；"
    "无 was / 无 previous_type_counts = 原为空气（正常）。"
    "若出现非预期非空 was，应向玩家说明或改 plan / 对齐 replace_any 意图。\n"
    "- LIMIT_EXCEEDED：减小 positions 数量或 fill 体积，仍用 batch/fill；禁止 place 风暴。\n"
    "- PRECONDITION_FAILED 且 actual 非空气：跳过该格，或 replace_any=true 再审批；"
    "勿无授权对同一格重试。\n"
    "- 专用工具可用时禁止改用 setblock/fill；"
    "仅当返回 ADDON_UNAVAILABLE 才可另行审批 run_minecraft_command。"
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
