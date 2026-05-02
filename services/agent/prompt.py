"""提示词模板管理器"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from pydantic_ai import RunContext

from config.logging import get_logger

logger = get_logger(__name__)

# 工具使用指南 - 延迟导入避免循环依赖
TOOL_USAGE_GUIDE = """你可以使用工具与 Minecraft 交互。
- 当用户要求"执行命令/给物品/发送消息/发标题/查询 Wiki"等可操作任务时，优先调用对应工具执行，而不是只解释步骤。
- 不要在有对应工具时直接说"我做不到"；若执行失败，要返回失败原因与下一步建议。
- 对于纯问答类问题，可直接回答。
""".strip()


class PromptTemplate(BaseModel):
    """提示词模板"""

    name: str  # 模板名称
    description: str  # 模板描述
    content: str  # 模板内容
    variables: dict[str, str] = {}  # 自定义变量默认值


# 内置模板定义
BUILTIN_TEMPLATES: dict[str, PromptTemplate] = {
    "default": PromptTemplate(
        name="default",
        description="默认模板",
        content="""请始终保持积极和专业的态度。回答尽量保持一段话不要太长，适当添加换行符，尽量不要使用markdown

{tool_usage}

当前玩家: {player_name}
模型: {provider}/{model}""",
    ),
    "concise": PromptTemplate(
        name="concise",
        description="简洁模式 - 更短的回复",
        content="""请用简洁的语言回答问题，保持 1-2 句话。

{tool_usage}

玩家: {player_name}""",
    ),
    "detailed": PromptTemplate(
        name="detailed",
        description="详细模式 - 更全面的回答",
        content="""请详细回答用户的问题，提供完整的解释和背景信息。
如有必要，可以适当使用 Markdown 格式。

{tool_usage}

当前玩家: {player_name}
模型: {provider}/{model}
服务器时间: {server_time}
会话长度: {context_length} 轮
上下文使用: {context_usage}""",
    ),
}


class PromptManager:
    """
    提示词模板管理器

    职责:
    - 管理内置和自定义模板
    - 提供模板切换功能（按 (connection_id, player_name) 分桶）
    - 处理模板变量替换
    """

    DEFAULT_PLAYER_KEY = "__anonymous__"

    def __init__(self):
        self._templates: dict[str, PromptTemplate] = BUILTIN_TEMPLATES.copy()
        # (connection_id, player_name) -> template_name
        self._session_templates: dict[tuple[str, str], str] = {}
        # (connection_id, player_name) -> {key: value}
        self._session_variables: dict[tuple[str, str], dict[str, str]] = {}

    @classmethod
    def _make_key(cls, connection_id: str, player_name: str | None) -> tuple[str, str]:
        return (connection_id, player_name or cls.DEFAULT_PLAYER_KEY)

    def register_template(self, template: PromptTemplate) -> bool:
        """
        注册新模板

        Args:
            template: 模板对象

        Returns:
            是否注册成功
        """
        if not template.name:
            logger.warning("register_template_empty_name")
            return False

        if template.name in self._templates:
            logger.warning("register_template_already_exists", name=template.name)
            return False

        self._templates[template.name] = template
        logger.info("template_registered", name=template.name)
        return True

    def get_template(self, name: str) -> PromptTemplate | None:
        """获取模板"""
        return self._templates.get(name)

    def list_templates(self) -> list[str]:
        """列出所有可用模板"""
        return list(self._templates.keys())

    # ── 按 (连接, 玩家) 维度的模板 / 变量管理 ──

    def get_session_template(
        self, connection_id: str, player_name: str | None
    ) -> str:
        """获取 (连接, 玩家) 当前的模板名称（找不到时回退到匿名桶以兼容旧 API）"""
        key = self._make_key(connection_id, player_name)
        if key in self._session_templates:
            return self._session_templates[key]
        # 回退：按连接级（匿名玩家）查询
        fallback_key = self._make_key(connection_id, None)
        return self._session_templates.get(fallback_key, "default")

    def set_session_template(
        self, connection_id: str, player_name: str | None, template_name: str
    ) -> bool:
        """
        设置 (连接, 玩家) 当前的模板

        Args:
            connection_id: 连接 ID
            player_name: 玩家名
            template_name: 模板名称

        Returns:
            是否设置成功
        """
        if template_name not in self._templates:
            logger.warning("set_session_template_not_found", template=template_name)
            return False

        key = self._make_key(connection_id, player_name)
        self._session_templates[key] = template_name

        # 初始化该会话的自定义变量（如果不存在）
        if key not in self._session_variables:
            template = self._templates[template_name]
            self._session_variables[key] = template.variables.copy()

        logger.info(
            "session_template_changed",
            connection_id=connection_id,
            player=player_name,
            template=template_name,
        )
        return True

    def get_session_variables(
        self, connection_id: str, player_name: str | None
    ) -> dict[str, str]:
        """获取 (连接, 玩家) 的自定义变量（找不到时回退到匿名桶以兼容旧 API）"""
        key = self._make_key(connection_id, player_name)
        if key in self._session_variables:
            return self._session_variables[key].copy()
        # 回退：按连接级（匿名玩家）查询
        fallback_key = self._make_key(connection_id, None)
        if fallback_key in self._session_variables:
            return self._session_variables[fallback_key].copy()
        # 再退一步：用当前模板默认变量
        template_name = self.get_session_template(connection_id, player_name)
        template = self._templates.get(template_name)
        if template:
            return template.variables.copy()
        return {}

    def set_session_variable(
        self,
        connection_id: str,
        player_name: str | None,
        name: str,
        value: str,
    ) -> bool:
        """
        设置 (连接, 玩家) 的自定义变量

        Args:
            connection_id: 连接 ID
            player_name: 玩家名
            name: 变量名
            value: 变量值

        Returns:
            是否设置成功
        """
        # 自动添加 custom_ 前缀
        if not name.startswith("custom_"):
            name = f"custom_{name}"

        key = self._make_key(connection_id, player_name)
        # 确保该会话的变量字典存在
        if key not in self._session_variables:
            template_name = self.get_session_template(connection_id, player_name)
            template = self._templates.get(template_name)
            if template:
                self._session_variables[key] = template.variables.copy()
            else:
                self._session_variables[key] = {}

        self._session_variables[key][name] = value
        logger.info(
            "session_variable_set",
            connection_id=connection_id,
            player=player_name,
            name=name,
            value=value,
        )
        return True

    def remove_session_variable(
        self, connection_id: str, player_name: str | None, name: str
    ) -> bool:
        """删除 (连接, 玩家) 的自定义变量"""
        key = self._make_key(connection_id, player_name)
        if key not in self._session_variables:
            return False

        if name in self._session_variables[key]:
            del self._session_variables[key][name]
            logger.info(
                "session_variable_removed",
                connection_id=connection_id,
                player=player_name,
                name=name,
            )
            return True
        return False

    def clear_session(self, connection_id: str, player_name: str | None) -> None:
        """清理 (连接, 玩家) 的模板与变量数据"""
        key = self._make_key(connection_id, player_name)
        self._session_templates.pop(key, None)
        self._session_variables.pop(key, None)
        logger.info(
            "session_template_cleared",
            connection_id=connection_id,
            player=player_name,
        )

    def clear_connection(self, connection_id: str) -> None:
        """清理某连接下所有玩家的模板与变量数据。"""
        for keys, store in (
            (list(self._session_templates.keys()), self._session_templates),
            (list(self._session_variables.keys()), self._session_variables),
        ):
            for key in keys:
                if key[0] == connection_id:
                    store.pop(key, None)
        logger.info("connection_template_cleared", connection_id=connection_id)

    # ── 兼容旧 API（仅按 connection_id 操作；映射到匿名玩家桶） ──

    def get_connection_template(self, connection_id: str) -> str:
        return self.get_session_template(connection_id, None)

    def set_connection_template(self, connection_id: str, template_name: str) -> bool:
        return self.set_session_template(connection_id, None, template_name)

    def get_connection_variables(self, connection_id: str) -> dict[str, str]:
        return self.get_session_variables(connection_id, None)

    def set_connection_variable(
        self, connection_id: str, name: str, value: str
    ) -> bool:
        return self.set_session_variable(connection_id, None, name, value)

    def remove_connection_variable(self, connection_id: str, name: str) -> bool:
        return self.remove_session_variable(connection_id, None, name)

    def build_system_prompt(
        self,
        connection_id: str,
        player_name: str,
        provider: str,
        model: str,
        context_length: int = 0,
        context_usage: str = "",
    ) -> str:
        """
        构建系统提示词

        Args:
            connection_id: 连接 ID
            player_name: 玩家名称（同时是模板/变量分桶键）
            provider: LLM 提供商
            model: 模型名称
            context_length: 上下文长度
            context_usage: 上下文使用情况（如 "20% 35.1k/200k"）

        Returns:
            完整的系统提示词
        """
        # 获取当前模板（按 (连接, 玩家) 维度）
        template_name = self.get_session_template(connection_id, player_name)
        template = self._templates.get(template_name)

        if not template:
            logger.error("template_not_found", template=template_name)
            template = self._templates["default"]

        # 获取变量
        custom_vars = self.get_session_variables(connection_id, player_name)

        # 构建内置变量
        builtin_vars = {
            "player_name": player_name or "未知玩家",
            "connection_id": connection_id[:8] if connection_id else "",
            "provider": provider or "deepseek",
            "model": model or "deepseek-chat",
            "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "context_length": str(context_length),
            "context_usage": context_usage,
            "tool_usage": TOOL_USAGE_GUIDE,
        }

        # 合并变量（自定义变量优先级更高）
        all_vars = {**builtin_vars, **custom_vars}

        # 替换变量
        content = template.content
        used_custom_vars = []  # 记录已使用的自定义变量
        unused_custom_vars = []  # 记录未使用的自定义变量

        for key, value in all_vars.items():
            placeholder = f"{{{key}}}"
            if placeholder in content:
                content = content.replace(placeholder, str(value))
                if key.startswith("custom_"):
                    used_custom_vars.append((key, value))
            elif key.startswith("custom_"):
                # 自定义变量且模板中没有对应占位符，记录为未使用
                unused_custom_vars.append((key, value))

        # 如果有未使用的自定义变量，追加到提示词末尾
        if unused_custom_vars:
            content += "\n\n--- 自定义变量 ---\n"
            for key, value in unused_custom_vars:
                # 去掉 custom_ 前缀显示
                display_name = key[7:] if key.startswith("custom_") else key
                content += f"{display_name}: {value}\n"

        return content


# 全局单例
_prompt_manager: PromptManager | None = None


def get_prompt_manager() -> PromptManager:
    """获取提示词管理器单例"""
    global _prompt_manager
    if _prompt_manager is None:
        _prompt_manager = PromptManager()
    return _prompt_manager


# 方便在 agent/core.py 中使用的构建函数
async def build_dynamic_prompt(ctx: RunContext) -> str:
    """
    动态构建系统提示词（供 PydanticAI 使用）

    Args:
        ctx: PydanticAI 运行上下文

    Returns:
        完整的系统提示词
    """
    manager = get_prompt_manager()

    # 从 deps 获取必要信息
    connection_id = str(ctx.deps.connection_id)
    player_name = ctx.deps.player_name
    provider = ctx.deps.provider or "deepseek"
    model = ctx.deps.settings.get_provider_config(provider).model if provider else "deepseek-chat"

    # 获取上下文使用信息
    context_info = None
    if ctx.deps.get_context_info:
        context_info = ctx.deps.get_context_info()

    # 计算上下文使用情况字符串
    context_usage = ""
    if context_info and context_info.max_tokens:
        usage_percent = (context_info.estimated_tokens / context_info.max_tokens) * 100
        context_usage = f"{usage_percent:.1f}% {context_info.estimated_tokens}/{context_info.max_tokens}"
    elif context_info:
        context_usage = f"{context_info.estimated_tokens} tokens"

    return manager.build_system_prompt(
        connection_id=connection_id,
        player_name=player_name,
        provider=provider,
        model=model,
        context_length=context_info.message_count if context_info else 0,
        context_usage=context_usage,
    )
