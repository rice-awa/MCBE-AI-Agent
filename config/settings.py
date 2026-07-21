"""应用配置 - 使用 Pydantic Settings 管理"""

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from _version import __version__
from pydantic import BaseModel, Field, PrivateAttr, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from pydantic_settings.sources import JsonConfigSettingsSource

if TYPE_CHECKING:
    from services.agent.model_metadata import ModelMetadataCache


class MinecraftCommandConfig(BaseModel):
    """Minecraft 命令配置"""

    prefix: str                      # 主命令前缀
    type: str                        # 命令类型
    aliases: list[str] = []          # 别名列表
    description: str                 # 命令描述
    usage: str | None = None        # 用法示例


class MinecraftConfig(BaseModel):
    """Minecraft 协议配置"""

    # 命令前缀定义: {前缀: 命令类型} - 兼容旧格式
    # 新格式: {前缀: {"type": 命令类型, "aliases": [别名列表], "description": 描述}}
    commands: dict[str, str | dict] = Field(default_factory=lambda: {
        "#登录": "login",
        "AGENT 聊天": {
            "type": "chat",
            "aliases": ["AGENT chat", "AI 聊天", "AI chat"],
            "description": "与 AI 对话",
            "usage": "<内容>"
        },
        "AGENT 脚本": {
            "type": "chat_script",
            "aliases": ["AGENT script", "AI 脚本", "AI script"],
            "description": "使用脚本事件发送",
            "usage": "<内容>"
        },
        "AGENT 保存": {
            "type": "save",
            "aliases": ["AGENT save", "AI save"],
            "description": "保存当前对话历史",
            "usage": None
        },
        "AGENT 对话": {
            "type": "conversation",
            "aliases": ["AGENT conv", "AGENT conversation", "AI 对话", "AI conv"],
            "description": "管理对话",
            "usage": "<new/switch/clear/status/list/save/restore>"
        },
        "AGENT 上下文": {
            "type": "context",
            "aliases": ["AGENT context", "AI 上下文", "AI context"],
            "description": "管理上下文开关",
            "usage": "<启用/关闭/状态>"
        },
        "AGENT 模板": {
            "type": "template",
            "aliases": ["AGENT template", "AI 模板", "AI template"],
            "description": "切换提示词模板",
            "usage": "<模板名/list>"
        },
        "AGENT 设置": {
            "type": "setting",
            "aliases": ["AGENT setting", "AI 设置", "AI setting"],
            "description": "设置管理",
            "usage": "<变量/别名> <子命令>"
        },
        "AGENT MCP": {
            "type": "mcp",
            "aliases": ["AGENT mcp","AI MCP","AI mcp"],
            "description": "MCP 服务器管理",
            "usage": "<list/status/reload>"
        },
        "AGENT 广播": {
            "type": "ai_broadcast",
            "aliases": ["AGENT broadcast", "AI 广播", "AI broadcast"],
            "description": "控制多人 AI 聊天广播",
            "usage": "<状态/关闭/全服 开启|关闭/玩家 <玩家名> 开启|关闭>"
        },
        "AGENT 同意": {
            "type": "tool_approve",
            "aliases": ["AGENT approve", "AGENT allow", "AI 同意", "AI approve", "AI allow"],
            "description": "同意待审批的高风险工具调用（可省略 id；对话/永远开启自动同意）",
            "usage": "[approval_id|对话|永远]",
        },
        "AGENT 拒绝": {
            "type": "tool_deny",
            "aliases": ["AGENT deny", "AGENT reject", "AI 拒绝", "AI deny", "AI reject"],
            "description": "拒绝待审批的高风险工具调用（可省略 id；对话/永远关闭自动同意）",
            "usage": "[approval_id|对话|永远]",
        },
        "运行命令": {
            "type": "run_command",
            "aliases": ["runcmd", "cmd"],
            "description": "执行游戏命令",
            "usage": "<命令>"
        },
        "切换模型": {
            "type": "switch_model",
            "aliases": ["switch", "模型"],
            "description": "切换 LLM",
            "usage": "<provider>"
        },
        "帮助": {
            "type": "help",
            "aliases": ["help", "?"],
            "description": "显示此帮助",
            "usage": None
        },
    })

    # 命令帮助信息: {命令类型: (描述, 用法)}
    command_help: dict[str, tuple[str, str | None]] = {
        "chat": ("与 AI 对话", "<内容>"),
        "chat_script": ("使用脚本事件发送", "<内容>"),
        "conversation": ("管理对话", "<new/switch/clear/status/list/save/restore>"),
        "context": ("管理上下文开关", "<启用/关闭/状态>"),
        "mcp": ("MCP 服务器管理", "<list/status/reload>"),
        "ai_broadcast": ("控制多人 AI 聊天广播", "<状态/关闭/全服 开启|关闭/玩家 <玩家名> 开启|关闭>"),
        "tool_approve": ("同意高风险工具调用", "[approval_id|对话|永远]"),
        "tool_deny": ("拒绝高风险工具调用", "[approval_id|对话|永远]"),
        "switch_model": ("切换 LLM", "<provider>"),
        "save": ("保存当前对话历史", None),
        "run_command": ("执行游戏命令", "<命令>"),
        "help": ("显示此帮助", None),
    }

    # 欢迎消息模板（{version} 在运行时由 __version__ 填充）
    welcome_message_template: str = """-----------
成功连接 MCBE AI Agent v{version}
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

    def get_command_type(self, prefix: str) -> str | None:
        """从命令前缀获取命令类型"""
        cmd = self.commands.get(prefix)
        if cmd is None:
            return None
        if isinstance(cmd, str):
            return cmd
        if isinstance(cmd, dict):
            return cmd.get("type")
        return None

    def get_command_description(self, cmd_type: str) -> tuple[str, str | None]:
        """获取命令描述和用法"""
        # 从 commands 中查找
        for prefix, cmd in self.commands.items():
            if isinstance(cmd, dict) and cmd.get("type") == cmd_type:
                return cmd.get("description", ""), cmd.get("usage")
            if isinstance(cmd, str) and cmd == cmd_type:
                return self.command_help.get(cmd_type, ("", None))
        return "", None

    def get_all_command_types(self) -> dict[str, str]:
        """获取所有命令类型映射"""
        result = {}
        for prefix, cmd in self.commands.items():
            if isinstance(cmd, str):
                result[prefix] = cmd
            elif isinstance(cmd, dict):
                result[prefix] = cmd.get("type", "")
        return result


class LLMProviderConfig(BaseModel):
    """LLM 提供商配置"""

    name: str
    api_key: str | None = None
    base_url: str | None = None
    model: str
    enabled: bool = True
    timeout: int = 60
    # 模型最大上下文窗口（token 数），用于计算上下文使用率
    context_window: int | None = None


class ModelMetadataConfig(BaseModel):
    """模型元数据配置（来源 models.dev）"""

    enabled: bool = True
    source_url: str = "https://models.dev/api.json"
    refresh_on_startup: bool = True
    timeout: int = 10
    cache_path: Path = Path("data/model_metadata_cache.json")


# 常用模型的上下文窗口大小（单位：tokens）
CONFIG_FILE = Path("config.json")
DOTENV_FILE = Path(".env")
_ENV_REF_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
REQUIRED_CONFIG_PATHS = (
    "server.host",
    "server.port",
    "auth.jwt_secret",
    "auth.jwt_expiration",
    "auth.default_password",
    "providers.default",
    "providers.deepseek.api_key",
    "providers.deepseek.base_url",
    "providers.deepseek.model",
    "providers.openai.api_key",
    "providers.openai.base_url",
    "providers.openai.model",
    "providers.anthropic.api_key",
    "providers.anthropic.model",
    "providers.ollama.base_url",
    "providers.ollama.model",
    "agent.system_prompt",
    "agent.enable_reasoning_output",
    "agent.max_history_turns",
    "agent.agent_retries",
    "agent.stream_sentence_mode",
    "agent.llm_warmup_enabled",
    "agent.mcwiki_base_url",
    "agent.dedup_external_messages",
    "agent.tool_response_verbose",
    "agent.request_limit",
    "agent.tool_calls_limit",
    "agent.input_tokens_limit",
    "agent.output_tokens_limit",
    "agent.total_tokens_limit",
    "agent.run_timeout",
    "agent.max_tool_concurrency",
    "agent.context_output_reserve_tokens",
    "agent.count_tokens_before_request",
    "agent.runtime_harness.enabled",
    "agent.runtime_harness.prompt_enabled",
    "agent.runtime_harness.schema_enabled",
    "agent.runtime_harness.audit_enabled",
    "agent.runtime_harness.audit_path",
    "agent.runtime_harness.audit_max_records",
    "agent.runtime_harness.approval_ttl",
    "agent.runtime_harness.max_batch_commands",
    "agent.runtime_harness.hard_deny_tools",
    "agent.runtime_harness.hard_deny_command_roots",
    "agent.runtime_harness.approval_command_roots",
    "agent.runtime_harness.mcp_tool_allowlist",
    "agent.runtime_harness.tool_policy_version",
    "agent.pydantic_ai_instrumentation",
    "queue.max_size",
    "queue.llm_worker_count",
    "websocket.ping_interval",
    "websocket.ping_timeout",
    "websocket.close_timeout",
    "websocket.max_size",
    "websocket.max_queue",
    "minecraft.commands",
    "minecraft.welcome_message_template",
    "minecraft.context_enabled_text",
    "minecraft.context_disabled_text",
    "minecraft.error_prefix",
    "minecraft.info_prefix",
    "minecraft.success_prefix",
    "minecraft.error_color",
    "minecraft.info_color",
    "minecraft.success_color",
    "mcp.enabled",
    "mcp.servers",
    "logging.level",
    "logging.enable_file_logging",
    "logging.enable_ws_raw_log",
    "logging.enable_llm_raw_log",
    "dev_mode",
    "flow_control.max_chunk_content_length",
    "flow_control.chunk_sentence_mode",
    "model_metadata.enabled",
    "model_metadata.source_url",
    "model_metadata.refresh_on_startup",
    "model_metadata.timeout",
    "model_metadata.cache_path",
)


def _parse_dotenv_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value


def _load_dotenv(path: Path = DOTENV_FILE) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_dotenv_line(line)
        if parsed is not None:
            key, value = parsed
            values[key] = value
    return values


def _secret_environment() -> dict[str, str]:
    values = _load_dotenv()
    values.update(os.environ)
    return values


def _resolve_env_refs(value: Any, env: dict[str, str], path: str = "") -> Any:
    if isinstance(value, dict):
        return {
            key: _resolve_env_refs(child, env, f"{path}.{key}" if path else str(key))
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [
            _resolve_env_refs(child, env, f"{path}[{index}]")
            for index, child in enumerate(value)
        ]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        resolved = env.get(name)
        if resolved is None or resolved == "":
            location = path or "<root>"
            raise ValueError(f"missing environment variable {name!r} for config path {location}")
        return resolved

    return _ENV_REF_PATTERN.sub(replace, value)


def _path_exists(data: dict[str, Any], path: str) -> bool:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


def _validate_runtime_config_data(data: dict[str, Any]) -> None:
    missing = [path for path in REQUIRED_CONFIG_PATHS if not _path_exists(data, path)]
    if missing:
        preview = ", ".join(missing[:5])
        suffix = "" if len(missing) <= 5 else f", ... (+{len(missing) - 5} more)"
        raise ValueError(f"config.json is incomplete; missing required path(s): {preview}{suffix}")


def _load_runtime_config_data() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"{CONFIG_FILE} is required for runtime settings. "
            "Run `python cli.py init` or copy `config.example.json` to `config.json`."
        )
    data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config.json root must be an object")
    data = _merge_minecraft_commands(data)
    _validate_runtime_config_data(data)
    return data


def _merge_minecraft_commands(data: dict[str, Any]) -> dict[str, Any]:
    minecraft = data.get("minecraft")
    if not isinstance(minecraft, dict):
        return data

    user_commands = minecraft.get("commands")
    if not isinstance(user_commands, dict):
        return data

    merged = MinecraftConfig().commands
    merged.update(user_commands)
    return {
        **data,
        "minecraft": {
            **minecraft,
            "commands": merged,
        },
    }


def _flatten_json_config(data: dict[str, Any]) -> dict[str, Any]:
    providers = data.get("providers", {})
    logging_config = data.get("logging", {})
    flow_control = data.get("flow_control", {})
    queue = data.get("queue", {})
    agent = data.get("agent", {})
    auth = data.get("auth", {})
    server = data.get("server", {})

    result: dict[str, Any] = {}

    result.update(server)

    if "jwt_secret" in auth:
        result["jwt_secret"] = auth["jwt_secret"]
    if "jwt_expiration" in auth:
        result["jwt_expiration"] = auth["jwt_expiration"]
    if "jwt_algorithm" in auth:
        result["jwt_algorithm"] = auth["jwt_algorithm"]
    if "default_password" in auth:
        result["default_password"] = auth["default_password"]

    if "default" in providers:
        result["default_provider"] = providers["default"]

    deepseek = providers.get("deepseek", {})
    if "api_key" in deepseek:
        result["deepseek_api_key"] = deepseek["api_key"]
    if "model" in deepseek:
        result["deepseek_model"] = deepseek["model"]
    if "base_url" in deepseek:
        result["deepseek_base_url"] = deepseek["base_url"]

    openai = providers.get("openai", {})
    if "api_key" in openai:
        result["openai_api_key"] = openai["api_key"]
    if "model" in openai:
        result["openai_model"] = openai["model"]
    if "base_url" in openai:
        result["openai_base_url"] = openai["base_url"]

    anthropic = providers.get("anthropic", {})
    if "api_key" in anthropic:
        result["anthropic_api_key"] = anthropic["api_key"]
    if "model" in anthropic:
        result["anthropic_model"] = anthropic["model"]

    ollama = providers.get("ollama", {})
    if "base_url" in ollama:
        result["ollama_base_url"] = ollama["base_url"]
    if "model" in ollama:
        result["ollama_model"] = ollama["model"]

    result.update(agent)

    runtime_harness = agent.get("runtime_harness", {})
    if "enabled" in runtime_harness:
        result["runtime_harness_enabled"] = runtime_harness["enabled"]
    if "prompt_enabled" in runtime_harness:
        result["runtime_harness_prompt_enabled"] = runtime_harness["prompt_enabled"]
    if "schema_enabled" in runtime_harness:
        result["runtime_harness_schema_enabled"] = runtime_harness["schema_enabled"]
    if "audit_enabled" in runtime_harness:
        result["runtime_harness_audit_enabled"] = runtime_harness["audit_enabled"]
    if "audit_path" in runtime_harness:
        result["runtime_harness_audit_path"] = runtime_harness["audit_path"]
    if "audit_max_records" in runtime_harness:
        result["runtime_harness_audit_max_records"] = runtime_harness["audit_max_records"]
    if "approval_ttl" in runtime_harness:
        result["approval_ttl"] = runtime_harness["approval_ttl"]
    if "max_batch_commands" in runtime_harness:
        result["max_batch_commands"] = runtime_harness["max_batch_commands"]
    if "hard_deny_tools" in runtime_harness:
        result["hard_deny_tools"] = runtime_harness["hard_deny_tools"]
    if "hard_deny_command_roots" in runtime_harness:
        result["hard_deny_command_roots"] = runtime_harness["hard_deny_command_roots"]
    if "approval_command_roots" in runtime_harness:
        result["approval_command_roots"] = runtime_harness["approval_command_roots"]
    if "mcp_tool_allowlist" in runtime_harness:
        result["mcp_tool_allowlist"] = runtime_harness["mcp_tool_allowlist"]
    if "tool_policy_version" in runtime_harness:
        result["tool_policy_version"] = runtime_harness["tool_policy_version"]
    if "pydantic_ai_instrumentation" in agent:
        result["pydantic_ai_instrumentation"] = agent["pydantic_ai_instrumentation"]

    if "max_size" in queue:
        result["queue_max_size"] = queue["max_size"]
    if "llm_worker_count" in queue:
        result["llm_worker_count"] = queue["llm_worker_count"]

    if "websocket" in data:
        result["websocket"] = data["websocket"]
    if "minecraft" in data:
        result["minecraft"] = data["minecraft"]
    if "mcp" in data:
        result["mcp"] = data["mcp"]
    if "addon" in data:
        result["addon"] = data["addon"]
    if "model_metadata" in data:
        result["model_metadata"] = data["model_metadata"]
    if "logging" in data:
        result["logging"] = data["logging"]
    if "storage" in data:
        result["storage"] = data["storage"]

    if "level" in logging_config:
        result["log_level"] = logging_config["level"]
    if "enable_file_logging" in logging_config:
        result["enable_file_logging"] = logging_config["enable_file_logging"]
    if "enable_ws_raw_log" in logging_config:
        result["enable_ws_raw_log"] = logging_config["enable_ws_raw_log"]
    if "enable_llm_raw_log" in logging_config:
        result["enable_llm_raw_log"] = logging_config["enable_llm_raw_log"]

    if "dev_mode" in data:
        result["dev_mode"] = data["dev_mode"]

    if "max_chunk_content_length" in flow_control:
        result["max_chunk_content_length"] = flow_control["max_chunk_content_length"]
    if "chunk_sentence_mode" in flow_control:
        result["chunk_sentence_mode"] = flow_control["chunk_sentence_mode"]
    if "flow_control" in data:
        result["flow_control"] = data["flow_control"]

    return result


class EnvInterpolatedJsonConfigSettingsSource(JsonConfigSettingsSource):
    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(
            settings_cls,
            json_file=CONFIG_FILE,
            json_file_encoding="utf-8",
        )

    def __call__(self) -> dict[str, Any]:
        if not CONFIG_FILE.exists():
            return {}
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("config.json root must be an object")
        resolved = _resolve_env_refs(data, _secret_environment())
        return _flatten_json_config(_merge_minecraft_commands(resolved))


MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    # DeepSeek
    "deepseek-chat": 128000,
    "deepseek-coder": 128000,
    # OpenAI
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4": 8192,
    "gpt-3.5-turbo": 16385,
    # Anthropic
    "claude-sonnet-4-20250514": 200000,
    "claude-opus-4-20250514": 200000,
    "claude-3-5-sonnet-20240620": 200000,
    "claude-3-opus-20240229": 200000,
    "claude-3-haiku-20240307": 200000,
    # Ollama (本地模型，默认 4k)
    "llama3": 4096,
    "llama3.1": 128000,
    "mistral": 8192,
    "codellama": 16384,
}


class FlowControlDelayConfig(BaseModel):
    """流控分片间延迟配置（秒）

    Canonical keys: tellraw / scriptevent / text_resp / text_resp_prelude.
    Legacy ai_resp / ai_resp_prelude remain readable (and dumpable) for one
    migration cycle so old config.json and host delivery paths keep working.
    """

    model_config = {"extra": "ignore"}

    tellraw: float = 0.05
    scriptevent: float = 0.05
    text_resp: float = 0.15
    text_resp_prelude: float = 0.5

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_ai_resp_keys(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        migrated = dict(data)
        if "text_resp" not in migrated and "ai_resp" in migrated:
            migrated["text_resp"] = migrated["ai_resp"]
        if "text_resp_prelude" not in migrated and "ai_resp_prelude" in migrated:
            migrated["text_resp_prelude"] = migrated["ai_resp_prelude"]
        return migrated

    @property
    def ai_resp(self) -> float:
        """Legacy alias for text_resp (host delivery still uses ai_resp keys)."""
        return self.text_resp

    @property
    def ai_resp_prelude(self) -> float:
        """Legacy alias for text_resp_prelude."""
        return self.text_resp_prelude

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        data = super().model_dump(*args, **kwargs)
        # Keep legacy keys for FlowControlMiddleware.chunk_delay_for("ai_resp*").
        data["ai_resp"] = data.get("text_resp", self.text_resp)
        data["ai_resp_prelude"] = data.get("text_resp_prelude", self.text_resp_prelude)
        return data


class FlowControlConfig(BaseModel):
    """流控中间件配置"""

    command_line_byte_budget: int = 461
    chunk_delays: FlowControlDelayConfig = Field(default_factory=FlowControlDelayConfig)
    # 非流式模式：按句子分批的最大字符数（避免单次 WebSocket 发送过大）
    non_stream_batch_max_chars: int = 150
    # 非流式模式：每个包之间的发送延迟（秒，避免 MC 崩溃）
    non_stream_send_delay: float = 0.1


class WebSocketConfig(BaseModel):
    """WebSocket 服务器配置"""

    ping_interval: int = 30
    ping_timeout: int = 15
    close_timeout: int = 15
    max_size: int = 10 * 1024 * 1024  # 10MB
    max_queue: int = 32


class MCPServerConfig(BaseModel):
    """单个 MCP 服务器配置 - 兼容 PydanticAI 官方格式"""

    command: str | None = None  # 启动命令 (如 "npx", "python", "uvx")
    args: list[str] = []  # 命令参数
    env: dict[str, str] = {}  # 环境变量
    url: str | None = None  # 远程服务器 URL (用于 HTTP 模式)
    timeout: int = 10  # 初始化超时时间（秒），npx 首次下载需要较长时间


class MCPConfig(BaseModel):
    """MCP 服务器配置 - 使用 mcpServers 字典格式"""

    enabled: bool = False
    servers: dict[str, MCPServerConfig] = {}  # 服务器名称 -> 配置


class AddonProtocolConfig(BaseModel):
    """Addon 桥接协议标识配置（线协议镜像；运行时以 SDK McbewsV1Profile 为准）。"""

    bridge_message_id: str = "mcbews:bridge_req"
    bridge_prefix: str = "MCBEWS|BRIDGE"
    ui_chat_prefix: str = "MCBEWS|UI_CHAT"
    bridge_tool_player_name: str = "MCBEWS_BRIDGE"
    ai_resp_message_id: str = "mcbews:text_resp"


class AddonConfig(BaseModel):
    """Addon 桥接配置"""

    protocol: AddonProtocolConfig = Field(default_factory=AddonProtocolConfig)


class LoggingFilesConfig(BaseModel):
    """日志文件名配置"""

    app: str = "app.log"
    websocket_raw: str = "websocket.log"
    llm_raw: str = "llm.log"


class LoggingConfig(BaseModel):
    """日志路径与轮转配置（与 Settings 顶层扁平字段 log_level/enable_* 共存）"""

    log_dir: Path = Path("logs")
    files: LoggingFilesConfig = Field(default_factory=LoggingFilesConfig)
    rotation_when: str = "midnight"
    rotation_interval: int = 1
    rotation_backup_count: int = 30


class StorageConfig(BaseModel):
    """持久化存储路径配置"""

    conversations_dir: Path = Path("data/conversations")
    tokens_file: Path = Path("data/tokens.json")


class Settings(BaseSettings):
    """应用主配置"""

    model_config = SettingsConfigDict(
        env_file=None,
        env_nested_delimiter="__",
        extra="ignore",
        populate_by_name=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            EnvInterpolatedJsonConfigSettingsSource(settings_cls),
            file_secret_settings,
        )

    # 服务器配置
    host: str = "0.0.0.0"
    port: int = 8080

    # 认证配置
    jwt_secret: str = Field(default="change-me-in-production", alias="SECRET_KEY")
    jwt_expiration: int = 1800  # 30分钟
    jwt_algorithm: str = "HS256"
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
    agent_retries: int = Field(default=2, ge=0)
    worker_http_timeout: int = Field(default=60, ge=1)
    worker_poll_timeout: float = Field(default=1.0, gt=0)
    run_command_timeout: float = Field(default=10.0, gt=0)

    # 对话压缩配置
    compression_enabled: bool = True
    compression_trigger_ratio: float = Field(default=0.8, gt=0, le=1)
    compression_keep_recent_turns: int = Field(default=8, ge=0)
    compression_summary_max_chars: int = Field(default=2000, gt=0)
    compression_timeout: int = Field(default=30, gt=0)

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

    # 每轮 run 预算（UsageLimits + wall-clock）
    request_limit: int = Field(default=8, ge=1)
    tool_calls_limit: int = Field(default=8, ge=1)
    # None = 未显式配置；运行时可由模型 context window 派生
    input_tokens_limit: int | None = Field(default=None, ge=1)
    output_tokens_limit: int | None = Field(default=None, ge=1)
    total_tokens_limit: int | None = Field(default=None, ge=1)
    run_timeout: float = Field(default=90.0, gt=0)
    max_tool_concurrency: int = Field(default=4, ge=1)
    context_output_reserve_tokens: int = Field(default=1024, ge=0)
    # 请求前 token 硬边界（UsageLimits.count_tokens_before_request）。
    # 仅当当前 provider 的 Model 实现了 count_tokens 时才会真正开启
    # （目前 anthropic；OpenAIChatModel / OllamaModel / FunctionModel / TestModel 不支持）。
    # deepseek/openai/ollama 会在 build_usage_limits 中自动关闭，避免 NotImplementedError。
    count_tokens_before_request: bool = True

    # 运行时 Harness 配置
    runtime_harness_enabled: bool = True
    runtime_harness_prompt_enabled: bool = True
    runtime_harness_schema_enabled: bool = True
    runtime_harness_audit_enabled: bool = True
    runtime_harness_audit_path: str = "logs/runtime_harness_tools.jsonl"
    runtime_harness_audit_max_records: int = Field(default=5000, gt=0)
    # 工具策略 / 审批
    approval_ttl: float = Field(default=120.0, gt=0)
    max_batch_commands: int = Field(default=10, ge=1)
    hard_deny_tools: list[str] = Field(default_factory=list)
    hard_deny_command_roots: list[str] = Field(
        default_factory=lambda: ["op", "deop", "stop", "whitelist", "permission", "wsserver"]
    )
    approval_command_roots: list[str] = Field(
        default_factory=lambda: [
            "clear",
            "clone",
            "damage",
            "fill",
            "kill",
            "replaceitem",
            "setblock",
            "structure",
            "summon",
        ]
    )
    mcp_tool_allowlist: list[str] = Field(default_factory=list)
    tool_policy_version: str = "2026-07-21.1"
    # PydanticAI 原生 OpenTelemetry instrumentation 开关；默认关闭 exporter，
    # 关闭时仍依赖 structlog 的 run_id 因果链。开启时 include_content=False，
    # 避免把完整 prompt/completion 写入 trace。
    pydantic_ai_instrumentation: bool = False

    # 队列配置
    queue_max_size: int = 100
    llm_worker_count: int = 2

    # WebSocket 配置
    websocket: WebSocketConfig = Field(default_factory=WebSocketConfig)

    # Minecraft 配置
    minecraft: MinecraftConfig = Field(default_factory=MinecraftConfig)

    # MCP 配置
    mcp: MCPConfig = Field(default_factory=MCPConfig)

    # Addon 桥接配置
    addon: AddonConfig = Field(default_factory=AddonConfig)

    # 模型元数据配置
    model_metadata: ModelMetadataConfig = Field(default_factory=ModelMetadataConfig)

    # 运行时附加的 models.dev 元数据缓存（由 AgentRuntime 启动时注入）
    _model_metadata_cache: "ModelMetadataCache | None" = PrivateAttr(default=None)

    # 持久化存储路径配置
    storage: StorageConfig = Field(default_factory=StorageConfig)

    # 日志配置
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    enable_file_logging: bool = True

    # 开发模式配置
    dev_mode: bool = Field(
        default=False,
        alias="DEV_MODE",
        description="开发模式 - 跳过身份验证，仅用于本地开发调试"
    )

    # 原始日志开关配置（默认关闭，避免缓冲流式 body / 泄露完整内容）
    enable_ws_raw_log: bool = Field(
        default=False,
        alias="ENABLE_WS_RAW_LOG",
        description="是否启用 WebSocket 原始请求/响应日志"
    )
    enable_llm_raw_log: bool = Field(
        default=False,
        alias="ENABLE_LLM_RAW_LOG",
        description="是否启用 LLM 请求/响应日志"
    )

    # 日志路径与轮转配置（嵌套，与上方扁平开关共存）
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    # 工具调用响应显示配置
    tool_response_verbose: bool = Field(
        default=False,
        alias="TOOL_RESPONSE_VERBOSE",
        description="是否在游戏内显示工具调用的返回结果（False=仅显示工具名称和参数，True=显示完整返回内容）"
    )

    # 流控中间件配置
    max_chunk_content_length: int = Field(
        default=400,
        alias="MAX_CHUNK_CONTENT_LENGTH",
        description="MCBE 单条消息内容长度上限（字符数），超过则分片发送",
    )

    chunk_sentence_mode: bool = Field(
        default=True,
        alias="CHUNK_SENTENCE_MODE",
        description="分片时是否优先按句子分割（True=语义分片，False=强制等长截断）",
    )

    # 流控嵌套配置（字节预算与分片延迟）
    flow_control: FlowControlConfig = Field(default_factory=FlowControlConfig)

    def get_provider_config(self, provider_name: str | None = None) -> LLMProviderConfig:
        """获取指定提供商的配置"""
        name = provider_name or self.default_provider

        # 获取模型的上下文窗口大小：静态表优先，回退到 models.dev 元数据缓存
        def get_context_window(provider: str, model: str) -> int | None:
            if model in MODEL_CONTEXT_WINDOWS:
                return MODEL_CONTEXT_WINDOWS[model]
            if self.model_metadata.enabled and self._model_metadata_cache is not None:
                return self._model_metadata_cache.get_context_window(provider, model)
            return None

        if name == "deepseek":
            return LLMProviderConfig(
                name="deepseek",
                api_key=self.deepseek_api_key,
                base_url=self.deepseek_base_url,
                model=self.deepseek_model,
                enabled=self.deepseek_api_key is not None,
                context_window=get_context_window("deepseek", self.deepseek_model),
            )
        elif name == "openai":
            return LLMProviderConfig(
                name="openai",
                api_key=self.openai_api_key,
                base_url=self.openai_base_url,
                model=self.openai_model,
                enabled=self.openai_api_key is not None,
                context_window=get_context_window("openai", self.openai_model),
            )
        elif name == "anthropic":
            return LLMProviderConfig(
                name="anthropic",
                api_key=self.anthropic_api_key,
                model=self.anthropic_model,
                enabled=self.anthropic_api_key is not None,
                context_window=get_context_window("anthropic", self.anthropic_model),
            )
        elif name == "ollama":
            return LLMProviderConfig(
                name="ollama",
                base_url=self.ollama_base_url,
                model=self.ollama_model,
                enabled=True,  # Ollama 不需要 API key
                context_window=get_context_window("ollama", self.ollama_model),
            )
        else:
            raise ValueError(f"未知的提供商: {name}")

    def attach_model_metadata_cache(self, cache: "ModelMetadataCache | None") -> None:
        """运行时注入 models.dev 元数据缓存，供 get_provider_config 回退查询。"""
        self._model_metadata_cache = cache

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
    _load_runtime_config_data()
    return Settings()
