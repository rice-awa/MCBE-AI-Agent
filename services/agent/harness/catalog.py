"""运行时 Harness 工具目录真相源。"""

from enum import StrEnum

from pydantic import BaseModel, Field


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


_TOOL_CATALOG: dict[str, ToolCatalogEntry] = {
    "run_minecraft_command": ToolCatalogEntry(
        name="run_minecraft_command",
        intent=ToolIntent.CHANGE_WORLD,
        risk=ToolRisk.HIGH,
        when_to_use="玩家明确要求执行单条 Minecraft 命令或进行一次确定的世界修改时使用。",
        when_not_to_use="不要用于闲聊、知识问答、不确定命令或需要多步确认的批量操作。",
        parameter_constraints="command 不带前导斜杠；只传单条命令。",
        preview=ParameterPreviewPolicy(include=("command",)),
    ),
    "run_minecraft_commands": ToolCatalogEntry(
        name="run_minecraft_commands",
        intent=ToolIntent.CHANGE_WORLD,
        risk=ToolRisk.DANGEROUS,
        when_to_use="玩家明确要求连续执行多条命令，且每条命令都具体可见时使用。",
        when_not_to_use="不要用于试探性操作、未知副作用命令或可以用单条命令完成的任务。",
        parameter_constraints="commands 中每项不带前导斜杠；保持顺序；避免包含空命令。",
        preview=ParameterPreviewPolicy(include=("commands",)),
    ),
    "send_game_message": ToolCatalogEntry(
        name="send_game_message",
        intent=ToolIntent.NOTIFY_DISPLAY,
        risk=ToolRisk.LOW,
        when_to_use="玩家要求向游戏内聊天发送普通文本消息时使用。",
        when_not_to_use="不要用于执行命令、查询状态或需要颜色/标题样式的展示。",
        parameter_constraints="message 为要展示的纯文本内容。",
        preview=ParameterPreviewPolicy(include=("message",)),
    ),
    "send_colored_message": ToolCatalogEntry(
        name="send_colored_message",
        intent=ToolIntent.NOTIFY_DISPLAY,
        risk=ToolRisk.MEDIUM,
        when_to_use="玩家要求在聊天中发送带 Minecraft 颜色代码的消息时使用。",
        when_not_to_use="不要用于普通回复、标题展示或执行任意 tellraw 命令。",
        parameter_constraints="message 为文本；color 使用 Minecraft 颜色代码，例如 §a。",
        preview=ParameterPreviewPolicy(include=("message", "color")),
    ),
    "send_title_message": ToolCatalogEntry(
        name="send_title_message",
        intent=ToolIntent.NOTIFY_DISPLAY,
        risk=ToolRisk.MEDIUM,
        when_to_use="玩家要求在屏幕中央显示标题或副标题时使用。",
        when_not_to_use="不要用于普通聊天消息、actionbar 或世界修改。",
        parameter_constraints="title 必填；subtitle 可选；fade_in/stay/fade_out 为 tick 数。",
        preview=ParameterPreviewPolicy(include=("title", "subtitle")),
    ),
    "send_actionbar_message": ToolCatalogEntry(
        name="send_actionbar_message",
        intent=ToolIntent.NOTIFY_DISPLAY,
        risk=ToolRisk.LOW,
        when_to_use="玩家要求在 actionbar 显示短提示时使用。",
        when_not_to_use="不要用于长文本、聊天消息、标题或命令执行。",
        parameter_constraints="message 应保持简短，适合 actionbar 展示。",
        preview=ParameterPreviewPolicy(include=("message",)),
    ),
    "send_script_event": ToolCatalogEntry(
        name="send_script_event",
        intent=ToolIntent.NOTIFY_DISPLAY,
        risk=ToolRisk.MEDIUM,
        when_to_use="玩家要求向 Addon 或脚本通道发送指定 scriptevent 内容时使用。",
        when_not_to_use="不要用于普通聊天展示、未知 message_id 或世界命令执行。",
        parameter_constraints="message_id 使用命名空间格式；content 为事件载荷文本。",
        preview=ParameterPreviewPolicy(include=("message_id", "content")),
    ),
    "fetch_url_text": ToolCatalogEntry(
        name="fetch_url_text",
        intent=ToolIntent.QUERY_KNOWLEDGE,
        risk=ToolRisk.MEDIUM,
        when_to_use="玩家提供 http/https 链接并要求读取网页文本时使用。",
        when_not_to_use="不要用于非网页链接、敏感内网地址或 Minecraft Wiki 可直接查询的问题。",
        parameter_constraints="url 仅支持 http/https；max_chars 控制返回长度。",
        preview=ParameterPreviewPolicy(include=("url", "max_chars")),
    ),
    "mcwiki_search": ToolCatalogEntry(
        name="mcwiki_search",
        intent=ToolIntent.QUERY_KNOWLEDGE,
        risk=ToolRisk.LOW,
        when_to_use="玩家询问 Minecraft 条目但不确定具体页面名时使用。",
        when_not_to_use="不要用于已知页面正文读取或非 Minecraft Wiki 内容。",
        parameter_constraints="query 为搜索词；limit 建议较小；namespaces 仅在玩家指定范围时使用。",
        preview=ParameterPreviewPolicy(include=("query", "limit", "namespaces")),
    ),
    "mcwiki_get_page": ToolCatalogEntry(
        name="mcwiki_get_page",
        intent=ToolIntent.QUERY_KNOWLEDGE,
        risk=ToolRisk.LOW,
        when_to_use="玩家要求获取明确 Minecraft Wiki 页面内容时使用。",
        when_not_to_use="不要用于模糊搜索、网页抓取或世界状态查询。",
        parameter_constraints="page_name 为明确页面名；format 使用 html/markdown/both/wikitext；max_chars 控制长度。",
        preview=ParameterPreviewPolicy(include=("page_name", "format", "max_chars")),
    ),
    "list_available_providers": ToolCatalogEntry(
        name="list_available_providers",
        intent=ToolIntent.SYSTEM_INFO,
        risk=ToolRisk.LOW,
        when_to_use="玩家询问当前可用 LLM provider 或模型来源时使用。",
        when_not_to_use="不要用于切换 provider、测试连接或回答 Minecraft 知识问题。",
        parameter_constraints="无参数。",
    ),
    "get_player_snapshot": ToolCatalogEntry(
        name="get_player_snapshot",
        intent=ToolIntent.QUERY_WORLD,
        risk=ToolRisk.LOW,
        when_to_use="玩家要求查询玩家位置、状态或在线玩家快照时使用。",
        when_not_to_use="不要用于修改玩家状态、背包查询或执行命令。",
        parameter_constraints="target 使用 Minecraft 选择器或玩家名；默认 @a。",
        preview=ParameterPreviewPolicy(include=("target",)),
    ),
    "get_inventory_snapshot": ToolCatalogEntry(
        name="get_inventory_snapshot",
        intent=ToolIntent.QUERY_WORLD,
        risk=ToolRisk.MEDIUM,
        when_to_use="玩家要求查看玩家背包或物品快照时使用。",
        when_not_to_use="不要用于发放、移除或修改物品。",
        parameter_constraints="target 使用 Minecraft 选择器或玩家名；默认 @a。",
        preview=ParameterPreviewPolicy(include=("target",)),
    ),
    "find_entities": ToolCatalogEntry(
        name="find_entities",
        intent=ToolIntent.QUERY_WORLD,
        risk=ToolRisk.MEDIUM,
        when_to_use="玩家要求查询附近实体、指定类型实体或实体数量时使用。",
        when_not_to_use="不要用于召唤、杀死、传送或修改实体。",
        parameter_constraints="entity_type 必填；radius 为查询半径；target 为查询中心选择器。",
        preview=ParameterPreviewPolicy(include=("entity_type", "radius", "target")),
    ),
    "run_world_command": ToolCatalogEntry(
        name="run_world_command",
        intent=ToolIntent.CHANGE_WORLD,
        risk=ToolRisk.HIGH,
        when_to_use="需要通过 Addon 桥接受控执行世界命令，且普通命令工具不可用时使用。",
        when_not_to_use="不要作为 run_minecraft_command 的默认替代，也不要用于查询或展示消息。",
        parameter_constraints="command 不带前导斜杠；只传单条世界命令。",
        preview=ParameterPreviewPolicy(include=("command",)),
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
