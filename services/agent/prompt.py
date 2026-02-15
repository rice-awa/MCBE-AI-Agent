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
会话长度: {context_length} 轮""",
    ),
}


class PromptManager:
    """
    提示词模板管理器

    职责:
    - 管理内置和自定义模板
    - 提供模板切换功能
    - 处理模板变量替换
    """

    def __init__(self):
        self._templates: dict[str, PromptTemplate] = BUILTIN_TEMPLATES.copy()
        self._connection_templates: dict[str, str] = {}  # connection_id -> template_name
        self._connection_variables: dict[str, dict[str, str]] = {}  # connection_id -> {key: value}

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

    def get_connection_template(self, connection_id: str) -> str:
        """获取连接的当前模板名称"""
        return self._connection_templates.get(connection_id, "default")

    def set_connection_template(self, connection_id: str, template_name: str) -> bool:
        """
        设置连接的当前模板

        Args:
            connection_id: 连接 ID
            template_name: 模板名称

        Returns:
            是否设置成功
        """
        if template_name not in self._templates:
            logger.warning("set_connection_template_not_found", template=template_name)
            return False

        self._connection_templates[connection_id] = template_name

        # 初始化该连接的自定义变量（如果不存在）
        if connection_id not in self._connection_variables:
            template = self._templates[template_name]
            self._connection_variables[connection_id] = template.variables.copy()

        logger.info(
            "connection_template_changed",
            connection_id=connection_id,
            template=template_name,
        )
        return True

    def get_connection_variables(self, connection_id: str) -> dict[str, str]:
        """获取连接的自定义变量"""
        if connection_id not in self._connection_variables:
            # 返回默认模板的变量
            template_name = self.get_connection_template(connection_id)
            template = self._templates.get(template_name)
            if template:
                return template.variables.copy()
            return {}
        return self._connection_variables.get(connection_id, {}).copy()

    def set_connection_variable(
        self, connection_id: str, name: str, value: str
    ) -> bool:
        """
        设置连接的自定义变量

        Args:
            connection_id: 连接 ID
            name: 变量名
            value: 变量值

        Returns:
            是否设置成功
        """
        # 自动添加 custom_ 前缀
        if not name.startswith("custom_"):
            name = f"custom_{name}"

        # 确保该连接的变量字典存在
        if connection_id not in self._connection_variables:
            template_name = self.get_connection_template(connection_id)
            template = self._templates.get(template_name)
            if template:
                self._connection_variables[connection_id] = template.variables.copy()
            else:
                self._connection_variables[connection_id] = {}

        self._connection_variables[connection_id][name] = value
        logger.info(
            "connection_variable_set",
            connection_id=connection_id,
            name=name,
            value=value,
        )
        return True

    def remove_connection_variable(
        self, connection_id: str, name: str
    ) -> bool:
        """删除连接的自定义变量"""
        if connection_id not in self._connection_variables:
            return False

        if name in self._connection_variables[connection_id]:
            del self._connection_variables[connection_id][name]
            logger.info(
                "connection_variable_removed",
                connection_id=connection_id,
                name=name,
            )
            return True
        return False

    def clear_connection(self, connection_id: str) -> None:
        """清理连接的模板和变量数据"""
        self._connection_templates.pop(connection_id, None)
        self._connection_variables.pop(connection_id, None)
        logger.info("connection_template_cleared", connection_id=connection_id)

    def build_system_prompt(
        self,
        connection_id: str,
        player_name: str,
        provider: str,
        model: str,
        context_length: int = 0,
    ) -> str:
        """
        构建系统提示词

        Args:
            connection_id: 连接 ID
            player_name: 玩家名称
            provider: LLM 提供商
            model: 模型名称
            context_length: 上下文长度

        Returns:
            完整的系统提示词
        """
        # 获取当前模板
        template_name = self.get_connection_template(connection_id)
        template = self._templates.get(template_name)

        if not template:
            logger.error("template_not_found", template=template_name)
            template = self._templates["default"]

        # 获取变量
        custom_vars = self.get_connection_variables(connection_id)

        # 构建内置变量
        builtin_vars = {
            "player_name": player_name or "未知玩家",
            "connection_id": connection_id[:8] if connection_id else "",
            "provider": provider or "deepseek",
            "model": model or "deepseek-chat",
            "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "context_length": str(context_length),
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

    # 获取上下文长度
    context_length = 0
    if ctx.deps.message_history:
        context_length = len(ctx.deps.message_history) // 2

    return manager.build_system_prompt(
        connection_id=connection_id,
        player_name=player_name,
        provider=provider,
        model=model,
        context_length=context_length,
    )
