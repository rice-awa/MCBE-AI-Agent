"""自定义异常"""


class MCBEAgentError(Exception):
    """基础异常类"""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class AuthenticationError(MCBEAgentError):
    """认证错误"""

    def __init__(self, message: str = "认证失败", details: dict | None = None):
        super().__init__(message, details)


class TokenExpiredError(AuthenticationError):
    """Token 过期错误"""

    def __init__(self, message: str = "Token 已过期", details: dict | None = None):
        super().__init__(message, details)


class InvalidTokenError(AuthenticationError):
    """无效 Token 错误"""

    def __init__(self, message: str = "无效的 Token", details: dict | None = None):
        super().__init__(message, details)


class LLMProviderError(MCBEAgentError):
    """LLM 提供商错误"""

    def __init__(
        self,
        message: str = "LLM 调用失败",
        provider: str | None = None,
        details: dict | None = None,
    ):
        super().__init__(message, details)
        self.provider = provider


class ProviderNotFoundError(LLMProviderError):
    """提供商未找到"""

    def __init__(self, provider: str, details: dict | None = None):
        super().__init__(f"未知的 LLM 提供商: {provider}", provider, details)


class ProviderNotConfiguredError(LLMProviderError):
    """提供商未配置"""

    def __init__(self, provider: str, details: dict | None = None):
        super().__init__(f"LLM 提供商未配置: {provider}", provider, details)


class ConnectionError(MCBEAgentError):
    """连接错误"""

    def __init__(self, message: str = "连接错误", details: dict | None = None):
        super().__init__(message, details)


class ConnectionClosedError(ConnectionError):
    """连接已关闭"""

    def __init__(self, message: str = "连接已关闭", details: dict | None = None):
        super().__init__(message, details)


class MessageQueueError(MCBEAgentError):
    """消息队列错误"""

    def __init__(self, message: str = "消息队列错误", details: dict | None = None):
        super().__init__(message, details)


class QueueFullError(MessageQueueError):
    """队列已满"""

    def __init__(self, message: str = "消息队列已满，请稍后重试", details: dict | None = None):
        super().__init__(message, details)


class CommandError(MCBEAgentError):
    """命令执行错误"""

    def __init__(self, message: str = "命令执行失败", command: str | None = None, details: dict | None = None):
        super().__init__(message, details)
        self.command = command


class ConfigurationError(MCBEAgentError):
    """配置错误"""

    def __init__(self, message: str = "配置错误", details: dict | None = None):
        super().__init__(message, details)
