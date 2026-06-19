"""对话标题生成服务"""

from pydantic_ai import Agent
from pydantic_ai.models import Model

from config.settings import get_settings


def _get_title_agent_model() -> str:
    settings = get_settings()
    provider_config = settings.get_provider_config(settings.default_provider)
    return f"{settings.default_provider}:{provider_config.model}"


_TITLE_AGENT: Agent | None = None


def _get_title_agent() -> Agent:
    """懒加载标题 Agent，避免模块导入时读取配置。"""
    global _TITLE_AGENT
    if _TITLE_AGENT is None:
        _TITLE_AGENT = Agent(
            _get_title_agent_model(),
            output_type=str,
            system_prompt=(
                "你是 Minecraft AI 助手的对话标题生成器。"
                "请根据用户第一条消息生成简短中文标题，只输出标题本身。"
            ),
            defer_model_check=True,
        )
    return _TITLE_AGENT


_SURROUNDING_TITLE_CHARS = " \t\r\n\f\v\"'`“”‘’「」『』《》〈〉【】（）()[]{}"
_UNNAMED_TITLE = "未命名"
_MAX_TITLE_LENGTH = 12


def clean_conversation_title(raw_title: str, fallback_source: str) -> str:
    """清理并限制对话标题。"""
    title = "".join(raw_title.strip().strip(_SURROUNDING_TITLE_CHARS).split())
    title = title.strip(_SURROUNDING_TITLE_CHARS)

    if not title:
        title = "".join(fallback_source.strip().split())[:_MAX_TITLE_LENGTH]

    return title[:_MAX_TITLE_LENGTH] or _UNNAMED_TITLE


async def generate_conversation_title(first_user_message: str, model: Model) -> str:
    """根据首条用户消息生成对话标题。"""
    prompt = (
        "请为 Minecraft AI 助手的一段对话生成标题。\n"
        "要求：标题必须是 2-12 个中文字符；只输出标题；"
        "不要标点符号、引号、解释或多余内容。\n"
        f"用户第一条消息：{first_user_message}"
    )
    result = await _get_title_agent().run(prompt, model=model)
    return clean_conversation_title(result.output, first_user_message)
