"""应用配置 - 使用 Pydantic Settings 管理"""

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MinecraftCommandConfig(BaseModel):
    """Minecraft 命令配置"""

    prefix: str
    description: str
    usage: str | None = None


class MinecraftConfig(BaseModel):
    """Minecraft 协议配置"""

    # 命令前缀定义: {前缀: 命令类型}
    commands: dict[str, str] = {
        "#登录": "login",
        "AGENT 聊天": "chat",
        "AGENT 脚本": "chat_script",
        "AGENT 保存": "save",
        "AGENT 上下文": "context",
        "运行命令": "run_command",
        "切换模型": "switch_model",
        "帮助": "help",
    }

    # 命令帮助信息: {命令类型: (描述, 用法)}
    command_help: dict[str, tuple[str, str | None]] = {
        "chat": ("与 AI 对话", "<内容>"),
        "chat_script": ("使用脚本事件发送", "<内容>"),
        "context": ("管理上下文", "<启用/关闭/状态>"),
        "switch_model": ("切换 LLM", "<provider>"),
        "save": ("保存对话历史", None),
        "run_command": ("执行游戏命令", "<命令>"),
        "help": ("显示此帮助", None),
    }

    # 欢迎消息模板
    welcome_message_template: str = """-----------
成功连接 MCBE AI Agent v2.2.0
连接 ID: {connection_id}...
当前模型: {provider}/{model}
上下文: {context_status}
-----------
使用 "{help_command}" 查看可用命令"""

    # 状态文本
    context_enabled_text: str = "启用"
    context_disabled_text: str = "关闭"

    # 消息前缀
    error_prefix: str = "❌ 错误: "
    info_prefix: str = "ℹ "
    success_prefix: str = "✅ "

    # 颜色代码
    error_color: str = "§c"
    info_color: str = "§b"
    success_color: str = "§a"


class LLMProviderConfig(BaseModel):
    """LLM 提供商配置"""

    name: str
    api_key: str | None = None
    base_url: str | None = None
    model: str
    enabled: bool = True
    timeout: int = 60


class WebSocketConfig(BaseModel):
    """WebSocket 服务器配置"""

    ping_interval: int = 30
    ping_timeout: int = 15
    close_timeout: int = 15
    max_size: int = 10 * 1024 * 1024  # 10MB
    max_queue: int = 32


class Settings(BaseSettings):
    """应用主配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # 服务器配置
    host: str = "0.0.0.0"
    port: int = 8080

    # 认证配置
    jwt_secret: str = Field(default="change-me-in-production", alias="SECRET_KEY")
    jwt_expiration: int = 1800  # 30分钟
    default_password: str = Field(default="123456", alias="WEBSOCKET_PASSWORD")

    # LLM 配置
    default_provider: Literal["deepseek", "openai", "anthropic", "ollama"] = "deepseek"

    # DeepSeek 配置
    deepseek_api_key: str | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com"

    # OpenAI 配置
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = "gpt-4o"
    openai_base_url: str | None = None

    # Anthropic 配置
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = "claude-sonnet-4-20250514"

    # Ollama 配置
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"

    # Agent 配置
    system_prompt: str = "请始终保持积极和专业的态度。回答尽量保持一段话不要太长，适当添加换行符，尽量不要使用markdown"
    enable_reasoning_output: bool = True
    max_history_turns: int = 20
    stream_sentence_mode: bool = Field(
        default=True,
        alias="STREAM_SENTENCE_MODE",
        description="是否开启流式输出（True=开启并按完整句子输出，False=关闭流式并在完成后按句子分批输出）"
    )
    llm_warmup_enabled: bool = Field(
        default=True,
        description="是否在启动时预热 LLM 模型，提高首次响应速度"
    )

    # Minecraft Wiki API 配置
    mcwiki_base_url: str = Field(
        default="https://mcwiki.rice-awa.top",
        alias="MCWIKI_BASE_URL",
    )

    # WebSocket 消息去重配置
    dedup_external_messages: bool = Field(
        default=True,
        alias="DEDUP_EXTERNAL_MESSAGES",
        description="是否排除sender为外部且事件为PlayerMessage的重复消息"
    )

    # 队列配置
    queue_max_size: int = 100
    llm_worker_count: int = 2

    # WebSocket 配置
    websocket: WebSocketConfig = Field(default_factory=WebSocketConfig)

    # Minecraft 配置
    minecraft: MinecraftConfig = Field(default_factory=MinecraftConfig)

    # 日志配置
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    enable_file_logging: bool = True

    # 原始日志开关配置
    enable_ws_raw_log: bool = Field(
        default=True,
        alias="ENABLE_WS_RAW_LOG",
        description="是否启用 WebSocket 原始请求/响应日志"
    )
    enable_llm_raw_log: bool = Field(
        default=True,
        alias="ENABLE_LLM_RAW_LOG",
        description="是否启用 LLM 请求/响应日志"
    )

    def get_provider_config(self, provider_name: str | None = None) -> LLMProviderConfig:
        """获取指定提供商的配置"""
        name = provider_name or self.default_provider

        if name == "deepseek":
            return LLMProviderConfig(
                name="deepseek",
                api_key=self.deepseek_api_key,
                base_url=self.deepseek_base_url,
                model=self.deepseek_model,
                enabled=self.deepseek_api_key is not None,
            )
        elif name == "openai":
            return LLMProviderConfig(
                name="openai",
                api_key=self.openai_api_key,
                base_url=self.openai_base_url,
                model=self.openai_model,
                enabled=self.openai_api_key is not None,
            )
        elif name == "anthropic":
            return LLMProviderConfig(
                name="anthropic",
                api_key=self.anthropic_api_key,
                model=self.anthropic_model,
                enabled=self.anthropic_api_key is not None,
            )
        elif name == "ollama":
            return LLMProviderConfig(
                name="ollama",
                base_url=self.ollama_base_url,
                model=self.ollama_model,
                enabled=True,  # Ollama 不需要 API key
            )
        else:
            raise ValueError(f"未知的提供商: {name}")

    def list_available_providers(self) -> list[str]:
        """列出所有可用的提供商"""
        providers = []
        if self.deepseek_api_key:
            providers.append("deepseek")
        if self.openai_api_key:
            providers.append("openai")
        if self.anthropic_api_key:
            providers.append("anthropic")
        providers.append("ollama")  # Ollama 总是可用
        return providers


@lru_cache
def get_settings() -> Settings:
    """获取配置单例"""
    return Settings()
