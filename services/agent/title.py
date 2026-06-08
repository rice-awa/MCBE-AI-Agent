"""对话标题生成服务"""

from pydantic_ai import Agent
from pydantic_ai.models import Model

_TITLE_AGENT = Agent(
    "deepseek:deepseek-chat",
    output_type=str,
    system_prompt=(
        "你是 Minecraft AI 助手的对话标题生成器。"
        "请根据用户第一条消息生成简短中文标题，只输出标题本身。"
    ),
    defer_model_check=True,
)

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
    result = await _TITLE_AGENT.run(prompt, model=model)
    return clean_conversation_title(result.output, first_user_message)
