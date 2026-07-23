"""运行时 Harness 工具目录真相源。"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

POLICY_VERSION = "2026-07-21.1"


class ToolIntent(StrEnum):
    CHANGE_WORLD = "改变世界"
    NOTIFY_DISPLAY = "通知展示"
    QUERY_WORLD = "查询世界"
    QUERY_KNOWLEDGE = "查询知识"
    SYSTEM_INFO = "系统信息"


class ToolRisk(StrEnum):
    LOW = "低"
    MEDIUM = "中"
    HIGH = "高"
    DANGEROUS = "危险"


ToolSource = Literal["builtin", "mcp"]


class ParameterPreviewPolicy(BaseModel):
    include: tuple[str, ...] = ()
    sensitive: tuple[str, ...] = ()
    max_length: int = 120


class ToolCatalogEntry(BaseModel):
    name: str
    intent: ToolIntent
    risk: ToolRisk
    when_to_use: str
    when_not_to_use: str
    parameter_constraints: str
    preview: ParameterPreviewPolicy = Field(default_factory=ParameterPreviewPolicy)
    source: ToolSource = "builtin"
    mcp_server: str | None = None
    schema_hash: str | None = None
    policy_version: str = POLICY_VERSION
    may_have_external_side_effects: bool = False


def _entry(
    name: str,
    intent: ToolIntent,
    risk: ToolRisk,
    when_to_use: str,
    when_not_to_use: str,
    parameter_constraints: str,
    *,
    preview: ParameterPreviewPolicy | None = None,
    may_have_external_side_effects: bool = False,
    source: ToolSource = "builtin",
    mcp_server: str | None = None,
    schema_hash: str | None = None,
) -> ToolCatalogEntry:
    return ToolCatalogEntry(
        name=name,
        intent=intent,
        risk=risk,
        when_to_use=when_to_use,
        when_not_to_use=when_not_to_use,
        parameter_constraints=parameter_constraints,
        preview=preview or ParameterPreviewPolicy(),
        source=source,
        mcp_server=mcp_server,
        schema_hash=schema_hash,
        policy_version=POLICY_VERSION,
        may_have_external_side_effects=may_have_external_side_effects,
    )


_TOOL_CATALOG: dict[str, ToolCatalogEntry] = {
    "run_minecraft_command": _entry(
        "run_minecraft_command",
        ToolIntent.CHANGE_WORLD,
        ToolRisk.HIGH,
        "玩家明确要求执行单条 Minecraft 命令或进行一次确定的世界修改时使用。",
        "不要用于闲聊、知识问答、不确定命令或需要多步确认的批量操作。",
        "command 不带前导斜杠；只传单条命令。",
        preview=ParameterPreviewPolicy(include=("command",)),
        may_have_external_side_effects=True,
    ),
    "run_minecraft_commands": _entry(
        "run_minecraft_commands",
        ToolIntent.CHANGE_WORLD,
        ToolRisk.DANGEROUS,
        "玩家明确要求连续执行多条命令，且每条命令都具体可见时使用。",
        "不要用于试探性操作、未知副作用命令或可以用单条命令完成的任务。",
        "commands 中每项不带前导斜杠；保持顺序；避免包含空命令。",
        preview=ParameterPreviewPolicy(include=("commands",)),
        may_have_external_side_effects=True,
    ),
    "send_game_message": _entry(
        "send_game_message",
        ToolIntent.NOTIFY_DISPLAY,
        ToolRisk.LOW,
        "玩家要求向游戏内聊天发送普通文本消息时使用；默认只发送给触发玩家。",
        "不要用于执行命令、查询状态或需要颜色/标题样式的展示；除非玩家明确要求全服广播，否则不要设置 broadcast=true。",
        "message 为要展示的纯文本内容；broadcast 默认 false，仅 true 时发送给全服 @a。",
        preview=ParameterPreviewPolicy(include=("message", "broadcast")),
        may_have_external_side_effects=True,
    ),
    "send_colored_message": _entry(
        "send_colored_message",
        ToolIntent.NOTIFY_DISPLAY,
        ToolRisk.MEDIUM,
        "玩家要求在聊天中发送带 Minecraft 颜色代码的消息时使用；默认只发送给触发玩家。",
        "不要用于普通回复、标题展示或执行任意 tellraw 命令；除非玩家明确要求全服广播，否则不要设置 broadcast=true。",
        "message 为文本；color 使用 Minecraft 颜色代码，例如 §a；broadcast 默认 false，仅 true 时发送给全服 @a。",
        preview=ParameterPreviewPolicy(include=("message", "color", "broadcast")),
        may_have_external_side_effects=True,
    ),
    "send_title_message": _entry(
        "send_title_message",
        ToolIntent.NOTIFY_DISPLAY,
        ToolRisk.MEDIUM,
        "玩家要求在屏幕中央显示标题或副标题时使用；默认只发送给触发玩家。",
        "不要用于普通聊天消息、actionbar 或世界修改；除非玩家明确要求全服广播，否则不要设置 broadcast=true。",
        "title 必填；subtitle 可选；fade_in/stay/fade_out 为 tick 数；broadcast 默认 false，仅 true 时发送给全服 @a。",
        preview=ParameterPreviewPolicy(include=("title", "subtitle", "broadcast")),
        may_have_external_side_effects=True,
    ),
    "send_actionbar_message": _entry(
        "send_actionbar_message",
        ToolIntent.NOTIFY_DISPLAY,
        ToolRisk.LOW,
        "玩家要求在 actionbar 显示短提示时使用；默认只发送给触发玩家。",
        "不要用于长文本、聊天消息、标题或命令执行；除非玩家明确要求全服广播，否则不要设置 broadcast=true。",
        "message 应保持简短，适合 actionbar 展示；broadcast 默认 false，仅 true 时发送给全服 @a。",
        preview=ParameterPreviewPolicy(include=("message", "broadcast")),
        may_have_external_side_effects=True,
    ),
    "send_script_event": _entry(
        "send_script_event",
        ToolIntent.NOTIFY_DISPLAY,
        ToolRisk.MEDIUM,
        "玩家要求向 Addon 或脚本通道发送指定 scriptevent 内容时使用。",
        "不要用于普通聊天展示、未知 message_id 或世界命令执行。",
        "message_id 使用命名空间格式；content 为事件载荷文本。",
        preview=ParameterPreviewPolicy(include=("message_id", "content")),
        may_have_external_side_effects=True,
    ),
    "mcwiki_search": _entry(
        "mcwiki_search",
        ToolIntent.QUERY_KNOWLEDGE,
        ToolRisk.LOW,
        "玩家询问 Minecraft 条目但不确定具体页面名时使用；用 1-3 个游戏名词搜索。",
        "不要用于已知页面正文读取、堆砌大量关键词、或非 Minecraft Wiki 内容。",
        "query 为 1-3 个游戏名词（多词缩小范围）；limit 默认 10、最大 50；namespaces 为命名空间 ID（0=Main 10=Template 14=Category），可用 mcwiki_list_namespaces 查询。",
        preview=ParameterPreviewPolicy(include=("query", "limit", "namespaces")),
        may_have_external_side_effects=False,
    ),
    "mcwiki_get_page": _entry(
        "mcwiki_get_page",
        ToolIntent.QUERY_KNOWLEDGE,
        ToolRisk.LOW,
        "玩家要求获取明确 Minecraft Wiki 页面内容时使用。",
        "不要用于模糊搜索、网页抓取或世界状态查询。",
        "page_name 为明确页面名（中文可）；format 默认 wikitext（优先，无需声明）；html 仅在 wikitext 模板无法理解时使用；max_chars 控制长度。",
        preview=ParameterPreviewPolicy(include=("page_name", "format", "max_chars")),
        may_have_external_side_effects=False,
    ),
    "mcwiki_check_page_exists": _entry(
        "mcwiki_check_page_exists",
        ToolIntent.QUERY_KNOWLEDGE,
        ToolRisk.LOW,
        "不确定页面名是否准确、只需确认存在性时使用。",
        "不要用于读取正文；不存在时应改用 mcwiki_search 搜索正确名称。",
        "page_name 为待检查的页面名，支持中文。",
        preview=ParameterPreviewPolicy(include=("page_name",)),
        may_have_external_side_effects=False,
    ),
    "mcwiki_check_health": _entry(
        "mcwiki_check_health",
        ToolIntent.SYSTEM_INFO,
        ToolRisk.LOW,
        "Wiki 工具连续失败、怀疑上游 API 不可用时检查服务健康。",
        "不要用于回答 Minecraft 知识问题或替代正常搜索/读页。",
        "无参数。",
        may_have_external_side_effects=False,
    ),
    "mcwiki_list_namespaces": _entry(
        "mcwiki_list_namespaces",
        ToolIntent.QUERY_KNOWLEDGE,
        ToolRisk.LOW,
        "需要命名空间数字 ID 以便限定 mcwiki_search 范围时使用。",
        "不要用于搜索条目或读取页面正文。",
        "无参数；返回 ID→名称映射，例如 10=Template。",
        may_have_external_side_effects=False,
    ),
    "list_available_providers": _entry(
        "list_available_providers",
        ToolIntent.SYSTEM_INFO,
        ToolRisk.LOW,
        "玩家询问当前可用 LLM provider 或模型来源时使用。",
        "不要用于切换 provider、测试连接或回答 Minecraft 知识问题。",
        "无参数。",
        may_have_external_side_effects=False,
    ),
    "get_player_snapshot": _entry(
        "get_player_snapshot",
        ToolIntent.QUERY_WORLD,
        ToolRisk.LOW,
        "玩家要求查询玩家位置、状态或在线玩家快照时使用。",
        "不要用于修改玩家状态、背包查询或执行命令。",
        "target 使用 Minecraft 选择器或玩家名；默认 @a。",
        preview=ParameterPreviewPolicy(include=("target",)),
        may_have_external_side_effects=False,
    ),
    "get_look_block": _entry(
        "get_look_block",
        ToolIntent.QUERY_WORLD,
        ToolRisk.LOW,
        "玩家询问自己或他人视线正对着的方块、前方是什么方块时使用。",
        "不要用于放置、破坏、填充方块，也不要替代按坐标查询方块。",
        "target 为玩家名（默认当前对话玩家）；max_distance 为最大检测距离（默认 8）；"
        "include_liquid_blocks / include_passable_blocks 控制是否命中液体与可穿过方块；"
        "include_details 默认 false，仅返回 typeId+坐标；仅当用户明确要求维度/命中面等细节时设为 true。",
        preview=ParameterPreviewPolicy(
            include=(
                "target",
                "max_distance",
                "include_liquid_blocks",
                "include_passable_blocks",
                "include_details",
            )
        ),
        may_have_external_side_effects=False,
    ),
    "get_inventory_snapshot": _entry(
        "get_inventory_snapshot",
        ToolIntent.QUERY_WORLD,
        ToolRisk.MEDIUM,
        "玩家要求查看玩家背包或物品快照时使用。",
        "不要用于发放、移除或修改物品。",
        "target 使用 Minecraft 选择器或玩家名；默认 @a。",
        preview=ParameterPreviewPolicy(include=("target",)),
        may_have_external_side_effects=False,
    ),
    "find_entities": _entry(
        "find_entities",
        ToolIntent.QUERY_WORLD,
        ToolRisk.MEDIUM,
        "玩家要求查询附近实体、指定类型实体或实体数量时使用。",
        "不要用于召唤、杀死、传送或修改实体。",
        "entity_type 必填；radius 为查询半径；target 为查询中心选择器。",
        preview=ParameterPreviewPolicy(include=("entity_type", "radius", "target")),
        may_have_external_side_effects=False,
    ),
    "run_world_command": _entry(
        "run_world_command",
        ToolIntent.CHANGE_WORLD,
        ToolRisk.HIGH,
        "需要通过 Addon 桥接受控执行世界命令，且普通命令工具不可用时使用。",
        "不要作为 run_minecraft_command 的默认替代，也不要用于查询或展示消息。",
        "command 不带前导斜杠；只传单条世界命令。",
        preview=ParameterPreviewPolicy(include=("command",)),
        may_have_external_side_effects=True,
    ),
    "inspect_block": _entry(
        "inspect_block",
        ToolIntent.QUERY_WORLD,
        ToolRisk.LOW,
        "查询单个或多个方块的 type ID、states、含水/空气/液体标记时使用。",
        "不要用于修改方块；不要用命令试探方块状态。",
        "coordinate_mode 为 absolute 或 player_relative；absolute 必须提供 dimension；"
        "提供 position 或 positions（互斥）。",
        preview=ParameterPreviewPolicy(
            include=("coordinate_mode", "dimension", "position", "positions")
        ),
        may_have_external_side_effects=False,
    ),
    "edit_blocks": _entry(
        "edit_blocks",
        ToolIntent.CHANGE_WORLD,
        ToolRisk.HIGH,
        "放置、批量放置或填充方块时使用；平台/地板/墙优先一次 fill 或少量 batch；"
        "默认仅替换空气，需要覆写非空地面时 replace_any=true 并再审批。",
        "不要用于查询；可表达的方块写入不要改用 setblock/fill 命令；"
        "禁止对连续区域 place×N。",
        "mode=place|batch|fill；coordinate_mode=absolute|player_relative；"
        "place 用 position，batch 用 positions，fill 用 from/to；"
        "type_id 必填；states 可选；replace_any 与 expected_previous 互斥；"
        "成功结果仅在 was / previous_type_counts 中报告被替换的非空气方块。",
        preview=ParameterPreviewPolicy(
            include=(
                "mode",
                "coordinate_mode",
                "dimension",
                "type_id",
                "position",
                "positions",
                "from",
                "to",
                "replace_any",
                "expected_previous",
                "states",
            )
        ),
        may_have_external_side_effects=True,
    ),
}


def get_tool_catalog() -> dict[str, ToolCatalogEntry]:
    return dict(_TOOL_CATALOG)


def get_tool_entry(name: str) -> ToolCatalogEntry | None:
    return _TOOL_CATALOG.get(name)


def list_tool_names() -> set[str]:
    return set(_TOOL_CATALOG)


def group_tools_by_intent() -> dict[ToolIntent, list[ToolCatalogEntry]]:
    grouped: dict[ToolIntent, list[ToolCatalogEntry]] = {intent: [] for intent in ToolIntent}
    for entry in _TOOL_CATALOG.values():
        grouped[entry.intent].append(entry)
    return grouped
