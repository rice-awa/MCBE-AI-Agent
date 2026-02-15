# MCBE AI Agent 功能增强实施计划

## 文档信息

- **项目**: MCBE AI Agent
- **版本**: v2.3.0
- **创建日期**: 2026-02-15
- **目标**: 增强 Agent 功能，优化用户体验
- **文档依赖**:
  - PydanticAI 官方文档 (ai.pydantic.dev)
  - Model Context Protocol Python SDK (modelcontextprotocol/python-sdk)

---

## 目录

1. [功能规格说明](#1-功能规格说明)
2. [技术实现方案](#2-技术实现方案)
3. [优先级排序](#3-优先级排序)
4. [实施时间表](#4-实施时间表)
5. [架构兼容性](#5-架构兼容性)
6. [错误处理方案](#6-错误处理方案)
7. [性能影响评估](#7-性能影响评估)

---

## 1. 功能规格说明

### 1.1 命令别名系统

#### 1.1.1 配置结构

```python
class CommandConfig(BaseModel):
    """命令配置"""
    prefix: str                      # 主命令前缀
    type: str                        # 命令类型
    aliases: list[str] = []          # 别名列表
    description: str                 # 命令描述
    usage: str | None = None        # 用法示例

class MinecraftConfig(BaseModel):
    """Minecraft 协议配置"""
    commands: dict[str, CommandConfig] = {
        "AGENT 聊天": CommandConfig(
            prefix="AGENT 聊天",
            type="chat",
            aliases=["AGENT chat", "AGENT 对话", "AI 聊天"],
            description="与 AI 对话",
            usage="<内容>"
        ),
        # ... 其他命令
    }
```

#### 1.1.2 支持的命令

| 命令 | 别名 | 功能 |
|------|------|------|
| `#登录` | `#login` | 用户认证 |
| `AGENT 聊天` | `AGENT chat`, `AGENT 对话` | 与 AI 对话 |
| `AGENT 脚本` | `AGENT script` | 使用脚本事件发送 |
| `AGENT 上下文` | `AGENT context` | 管理上下文 |
| `AGENT 模板` | `AGENT template` | 切换提示词模板 |
| `AGENT 设置` | `AGENT setting` | 设置管理 |
| `运行命令` | `runcmd`, `cmd` | 执行游戏命令 |
| `切换模型` | `switch`, `模型` | 切换 LLM |
| `帮助` | `help`, `?` | 显示帮助 |

#### 1.1.3 环境变量支持

```bash
# 通过 JSON 字符串配置复杂命令结构
MINECRAFT_COMMANDS='{"AGENT 聊天": {"type": "chat", "aliases": ["AGENT chat", "AI 聊天"]}}'
```

#### 1.1.4 游戏内别名管理

```
AGENT 设置 命令别名 <命令> <添加/删除> <别名>
# 示例:
AGENT 设置 命令别名 AGENT 聊天 添加 AI聊天
AGENT 设置 命令别名 AGENT 聊天 删除 AGENT chat
```

### 1.2 上下文管理功能

#### 1.2.1 现有功能增强

- **清理**: `AGENT 上下文 清除` - 清除当前会话历史
- **状态**: `AGENT 上下文 状态` - 显示上下文状态

#### 1.2.2 新增功能

| 命令 | 功能 | 说明 |
|------|------|------|
| `AGENT 上下文 压缩` | 手动压缩 | 手动触发智能压缩，保留关键信息 |
| `AGENT 上下文 保存` | 保存会话 | 保存当前对话到持久化存储 |
| `AGENT 上下文 恢复 <ID>` | 恢复会话 | 从持久化存储恢复历史对话 |
| `AGENT 上下文 列表` | 列出历史 | 显示已保存的会话列表 |
| `AGENT 上下文 删除 <ID>` | 删除历史 | 删除指定的历史会话 |

#### 1.2.3 自动压缩策略

- **触发条件**: 当对话历史超过 `max_history_turns` (默认20轮) 的 80%
- **压缩方式**:
  1. 保留用户问题的核心关键词
  2. 保留 AI 回答的摘要
  3. 删除推理过程和冗余表达
- **保留**: 最近 N 轮完整对话 + 关键信息摘要

#### 1.2.4 持久化存储

```json
// data/conversations/{connection_id}_{timestamp}.json
{
    "connection_id": "uuid",
    "player_name": "Player",
    "provider": "deepseek",
    "model": "deepseek-chat",
    "created_at": "2026-02-15T10:00:00Z",
    "updated_at": "2026-02-15T10:30:00Z",
    "message_count": 50,
    "messages": [
        {
            "role": "user",
            "content": "用户消息",
            "timestamp": "2026-02-15T10:00:00Z"
        },
        {
            "role": "assistant",
            "content": "AI 回复",
            "timestamp": "2026-02-15T10:00:05Z"
        }
    ],
    "metadata": {
        "template": "default",
        "custom_variables": {}
    }
}
```

### 1.3 提示词模板功能

#### 1.3.1 内置模板

```python
class PromptTemplate(BaseModel):
    """提示词模板"""
    name: str                        # 模板名称
    description: str                 # 模板描述
    content: str                     # 模板内容
    variables: dict[str, str] = {}   # 自定义变量默认值

# 内置模板示例
TEMPLATES = {
    "default": PromptTemplate(
        name="default",
        description="默认模板",
        content="""请始终保持积极和专业的态度。回答尽量保持一段话不要太长，适当添加换行符，尽量不要使用markdown

{tool_usage}

当前玩家: {player_name}
模型: {provider}/{model}"""
    ),
    "concise": PromptTemplate(
        name="concise",
        description="简洁模式 - 更短的回复",
        content="""请用简洁的语言回答问题，保持 1-2 句话。

{tool_usage}

玩家: {player_name}"""
    ),
    "detailed": PromptTemplate(
        name="detailed",
        description="详细模式 - 更全面的回答",
        content="""请详细回答用户的问题，提供完整的解释和背景信息。
如有必要，可以适当使用 Markdown 格式。

{tool_usage}

当前玩家: {player_name}
服务器时间: {server_time}
会话长度: {context_length} 轮"""
    )
}
```

#### 1.3.2 变量系统

| 变量 | 说明 | 示例 |
|------|------|------|
| `{player_name}` | 玩家名称 | Player |
| `{connection_id}` | 连接 ID | abc-123 |
| `{provider}` | LLM 提供商 | deepseek |
| `{model}` | 模型名称 | deepseek-chat |
| `{server_time}` | 服务器时间 | 2026-02-15 10:00:00 |
| `{context_length}` | 上下文长度 | 15 |
| `{custom_*}` | 自定义变量 | {custom_greeting} |

#### 1.3.3 模板切换命令

```
AGENT 模板                    # 显示当前模板
AGENT 模板 default           # 切换到默认模板
AGENT 模板 concise           # 切换到简洁模式
AGENT 模板 detailed          # 切换到详细模式
AGENT 模板 list              # 列出所有可用模板
```

#### 1.3.4 自定义变量

```
AGENT 设置 变量 <名称> <值>
# 示例:
AGENT 设置 变量 greeting "你好，冒险家！"
```

### 1.4 MCP Server 集成

#### 1.4.1 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                     MCBE AI Agent                           │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐   │
│  │  WebSocket  │───▶│   Message   │───▶│    Agent    │   │
│  │   Server    │    │   Broker    │    │   Worker    │   │
│  └─────────────┘    └─────────────┘    └──────┬──────┘   │
│                                                 │           │
│  ┌──────────────────────────────────────────────▼────────┐  │
│  │                    Tool System                     │  │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐   │  │
│  │  │ Built-in   │  │   MCP      │  │  Future    │   │  │
│  │  │ Tools      │  │   Servers  │  │  Plugins   │   │  │
│  │  └────────────┘  └────────────┘  └────────────┘   │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

#### 1.4.2 MCP 配置

```python
class MCPConfig(BaseModel):
    """MCP 服务器配置"""
    enabled: bool = False
    servers: list[MCPServerConfig] = []

class MCPServerConfig(BaseModel):
    """单个 MCP 服务器配置"""
    name: str                        # 服务器名称
    command: str                     # 启动命令
    args: list[str] = []             # 命令参数
    env: dict[str, str] = {}         # 环境变量
    auto_start: bool = True           # 是否自动启动
```

#### 1.4.3 环境变量配置

```bash
# MCP 服务器配置 (JSON 格式)
MCP_ENABLED=true
MCP_SERVERS='[{"name": "filesystem", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"]}]'
```

#### 1.4.4 工具注册流程

1. 启动时扫描配置的 MCP 服务器
2. 通过 MCP 协议获取可用工具列表
3. 将 MCP 工具转换为 PydanticAI 工具格式
4. 注册到 Agent 工具系统

---

## 2. 技术实现方案

### 2.1 组件架构

```
┌─────────────────────────────────────────────────────────────────┐
│                         config/settings.py                       │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐    │
│  │ MinecraftConfig│  │ PromptTemplate │  │   MCPConfig   │    │
│  │ - commands     │  │ - templates    │  │ - servers     │    │
│  │ - aliases      │  │ - variables    │  │ - enabled     │    │
│  └───────┬────────┘  └───────┬────────┘  └───────┬────────┘    │
└──────────┼───────────────────┼───────────────────┼──────────────┘
           │                   │                   │
           ▼                   ▼                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                    services/websocket/                          │
│  ┌─────────────────────────┐  ┌─────────────────────────┐      │
│  │      minecraft.py       │  │       server.py         │      │
│  │ - parse_command()       │  │ - handle_command()     │      │
│  │ - CommandRegistry       │  │ - handle_context()     │      │
│  │ - AliasResolver         │  │ - handle_template()     │      │
│  └─────────────────────────┘  └─────────────────────────┘      │
└─────────────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│                         core/                                   │
│  ┌─────────────────────────┐  ┌─────────────────────────┐      │
│  │        queue.py         │  │    conversation.py      │      │
│  │ - MessageBroker         │  │ - ConversationManager   │      │
│  │ - conversation_history  │  │ - save/load/resume      │      │
│  │ - clear/trim methods    │  │ - compression           │      │
│  └─────────────────────────┘  └─────────────────────────┘      │
└─────────────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│                     services/agent/                             │
│  ┌─────────────────────────┐  ┌─────────────────────────┐      │
│  │        core.py          │  │        worker.py         │      │
│  │ - dynamic_system_prompt │  │ - _trim_history()       │      │
│  │ - PromptManager         │  │ - _compress_history()   │      │
│  │ - TemplateRenderer      │  │ - _process_request()     │      │
│  └─────────────────────────┘  └─────────────────────────┘      │
│                                                                 │
│  ┌─────────────────────────┐  ┌─────────────────────────┐      │
│  │        tools.py         │  │        mcp.py            │      │
│  │ - register_agent_tools │  │ - MCPClient              │      │
│  │ - Built-in tools       │  │ - MCPToolAdapter         │      │
│  └─────────────────────────┘  └─────────────────────────┘      │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 核心模块设计

#### 2.2.1 PydanticAI 最佳实践集成

根据 PydanticAI 官方文档 (ai.pydantic.dev) 的最佳实践:

1. **动态系统提示词**: 使用 `@agent.system_prompt` 装饰器 (现有实现已采用)
2. **工具注册**: 使用 `@agent.tool` 装饰器 (现有实现已采用)
3. **消息序列化**: 使用 `ModelMessagesTypeAdapter` 进行消息的 JSON 序列化/反序列化
4. **流式处理**: 使用 `agent.iter()` 进行细粒度节点控制 (现有实现已采用)

```python
# 消息序列化最佳实践 (来自官方文档)
from pydantic_core import to_json
from pydantic_ai import ModelMessagesTypeAdapter

# 序列化消息历史
as_json_objects = to_json(messages)
# 反序列化
same_messages = ModelMessagesTypeAdapter.validate_json(as_json_objects)
```

#### 2.2.2 MCP Server 最佳实践集成

根据 Model Context Protocol Python SDK 官方文档 (modelcontextprotocol/python-sdk) 的最佳实践:

1. **使用官方 SDK**: 不自行实现 JSON-RPC，使用 `mcp.client.session.ClientSession`
2. **传输方式**: 支持 stdio (本地) 和 streamable_http (远程)
3. **连接方式**: 使用 `StdioServerParameters` 和 `stdio_client`

```python
# 官方推荐的 MCP 客户端连接方式
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

async def main():
    # 通过 stdio 连接 MCP 服务器
    async with stdio_client(
        StdioServerParameters(command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "/data"])
    ) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 列出可用工具
            tools = await session.list_tools()

            # 调用工具
            result = await session.call_tool("tool_name", {"arg": "value"})
```

#### 2.2.3 命令注册表 (CommandRegistry)

```python
# services/websocket/command.py

class CommandRegistry:
    """命令注册表 - 管理命令和别名"""

    def __init__(self, commands_config: dict[str, CommandConfig]):
        self._commands: dict[str, CommandConfig] = {}
        self._alias_map: dict[str, str] = {}  # 别名 -> 主命令前缀
        self._load_commands(commands_config)

    def _load_commands(self, config: dict[str, CommandConfig]) -> None:
        """加载命令配置并构建别名映射"""
        for prefix, cmd_config in config.items():
            self._commands[prefix] = cmd_config
            # 构建别名映射
            for alias in cmd_config.aliases:
                self._alias_map[alias] = prefix

    def resolve(self, message: str) -> tuple[str | None, str]:
        """解析消息，返回 (命令类型, 内容)"""
        # 1. 尝试直接匹配
        for prefix, cmd_config in self._commands.items():
            if message.startswith(prefix):
                content = message[len(prefix):].strip()
                return cmd_config.type, content

        # 2. 尝试别名匹配
        for alias, main_prefix in self._alias_map.items():
            if message.startswith(alias):
                content = message[len(alias):].strip()
                cmd_config = self._commands[main_prefix]
                return cmd_config.type, content

        return None, message

    def add_alias(self, command_prefix: str, alias: str) -> bool:
        """动态添加别名"""
        if command_prefix not in self._commands:
            return False
        self._alias_map[alias] = command_prefix
        self._commands[command_prefix].aliases.append(alias)
        return True

    def remove_alias(self, alias: str) -> bool:
        """动态删除别名"""
        if alias not in self._alias_map:
            return False
        main_prefix = self._alias_map.pop(alias)
        self._commands[main_prefix].aliases.remove(alias)
        return True
```

#### 2.2.2 提示词管理器 (PromptManager)

```python
# services/agent/prompt.py

class PromptManager:
    """提示词管理器 - 模板管理和渲染"""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._templates: dict[str, PromptTemplate] = {}
        self._custom_variables: dict[str, str] = {}
        self._current_template: str = "default"
        self._load_templates()

    def _load_templates(self) -> None:
        """加载内置模板"""
        # 从配置或代码加载内置模板
        self._templates = {
            "default": PromptTemplate(...),
            "concise": PromptTemplate(...),
            "detailed": PromptTemplate(...)
        }

    def render(self, context: dict[str, Any]) -> str:
        """渲染当前模板"""
        template = self._templates.get(self._current_template)
        if not template:
            template = self._templates["default"]

        # 合并内置变量和自定义变量
        all_vars = {
            **self._get_builtin_variables(),
            **self._custom_variables,
            **context
        }

        return template.content.format(**all_vars)

    def _get_builtin_variables(self) -> dict[str, str]:
        """获取内置变量"""
        return {
            "player_name": "",      # 由调用方提供
            "connection_id": "",    # 由调用方提供
            "provider": self._settings.default_provider,
            "model": self._settings.get_provider_config().model,
            "server_time": datetime.now().isoformat(),
            "context_length": "0",
            "tool_usage": TOOL_USAGE_GUIDE
        }

    def set_template(self, name: str) -> bool:
        """切换模板"""
        if name not in self._templates:
            return False
        self._current_template = name
        return True

    def set_variable(self, name: str, value: str) -> None:
        """设置自定义变量"""
        self._custom_variables[name] = value

    def list_templates(self) -> list[str]:
        """列出所有模板"""
        return list(self._templates.keys())
```

#### 2.2.3 对话管理器 (ConversationManager)

```python
# core/conversation.py

import json
import aiofiles
from pathlib import Path
from datetime import datetime
from pydantic_core import to_json
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelMessage

class ConversationManager:
    """对话管理器 - 上下文持久化和压缩

    使用 PydanticAI 官方推荐的序列化方式:
    - to_json() / ModelMessagesTypeAdapter.validate_json() 进行消息序列化
    - 参考: https://ai.pydantic.dev/message-history
    """

    def __init__(self, broker: MessageBroker, storage_path: str = "data/conversations"):
        self._broker = broker
        self._storage_path = Path(storage_path)
        self._storage_path.mkdir(parents=True, exist_ok=True)

    async def save_conversation(self, connection_id: UUID, metadata: dict = None) -> str:
        """保存对话到持久化存储

        使用 PydanticAI 官方序列化方式:
        - to_json() 序列化为 JSON
        """
        history = self._broker.get_conversation_history(connection_id)
        if not history:
            return None

        # 使用官方推荐的序列化方式
        messages_json = to_json(history).decode('utf-8')

        # 创建保存数据
        data = {
            "version": "1.0",  # 版本号用于迁移
            "connection_id": str(connection_id),
            "player_name": metadata.get("player_name"),
            "provider": metadata.get("provider"),
            "model": metadata.get("model"),
            "created_at": metadata.get("created_at", datetime.now().isoformat()),
            "updated_at": datetime.now().isoformat(),
            "message_count": len(history),
            "messages": messages_json,  # 使用官方序列化格式
            "metadata": metadata or {}
        }

        # 生成文件名
        filename = f"{connection_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = self._storage_path / filename

        # 异步写入文件
        async with aiofiles.open(filepath, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(data, ensure_ascii=False, indent=2))

        return filename

    async def load_conversation(self, filename: str) -> list[ModelMessage] | None:
        """加载对话历史

        使用 PydanticAI 官方反序列化方式:
        - ModelMessagesTypeAdapter.validate_json() 反序列化
        """
        filepath = self._storage_path / filename
        if not filepath.exists():
            return None

        async with aiofiles.open(filepath, 'r', encoding='utf-8') as f:
            content = await f.read()
            data = json.loads(content)

        # 兼容旧格式 (直接存储 messages) 和新格式 (使用官方序列化)
        if "messages" in data:
            messages_json = data["messages"]
            # 检查是否为官方序列化格式 (JSON 字符串)
            if isinstance(messages_json, str):
                try:
                    return ModelMessagesTypeAdapter.validate_json(messages_json)
                except Exception:
                    pass
            # 兼容旧格式
            return self._legacy_load_messages(messages_json)

        return None

    async def list_conversations(self, connection_id: UUID = None) -> list[dict]:
        """列出保存的对话"""
        conversations = []
        pattern = f"{connection_id}_*" if connection_id else "*.json"

        for filepath in self._storage_path.glob(pattern):
            async with aiofiles.open(filepath, 'r', encoding='utf-8') as f:
                content = await f.read()
                data = json.loads(content)
                conversations.append({
                    "filename": filepath.name,
                    "connection_id": data.get("connection_id"),
                    "player_name": data.get("player_name"),
                    "message_count": data.get("message_count"),
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at")
                })

        return sorted(conversations, key=lambda x: x.get("updated_at", ""), reverse=True)

    async def delete_conversation(self, filename: str) -> bool:
        """删除对话文件"""
        filepath = self._storage_path / filename
        if filepath.exists():
            filepath.unlink()
            return True
        return False

    def compress_history(
        self,
        messages: list[ModelMessage],
        max_turns: int = 10
    ) -> list[ModelMessage]:
        """压缩对话历史 - 保留关键信息"""
        if len(messages) <= max_turns * 2:
            return messages

        # 使用 Worker 中的 _trim_history 作为基础
        trimmed = AgentWorker._trim_history(messages, max_turns)

        # TODO: 添加摘要压缩逻辑
        # 1. 提取关键实体和信息
        # 2. 生成摘要
        # 3. 替换原始长消息

        return trimmed
```

#### 2.2.4 MCP 客户端 (MCPClient) - 使用官方 SDK

```python
# services/agent/mcp.py

import asyncio
from typing import Any
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
import mcp.types as types

class MCPClient:
    """MCP 协议客户端 - 使用官方 SDK"""

    def __init__(self, config: MCPServerConfig):
        self._config = config
        self._session: ClientSession | None = None
        self._read: Any = None
        self._write: Any = None

    async def start(self) -> None:
        """启动 MCP 服务器连接"""
        if self._config.command == "http":
            # 远程 MCP 服务器 (streamable_http)
            async with streamable_http_client(self._config.url) as (self._read, self._write):
                self._session = ClientSession(self._read, self._write)
                await self._session.initialize()
        else:
            # 本地 MCP 服务器 (stdio)
            server_params = StdioServerParameters(
                command=self._config.command,
                args=self._config.args,
                env={**os.environ, **self._config.env}
            )
            async with stdio_client(server_params) as (self._read, self._write):
                self._session = ClientSession(self._read, self._write)
                await self._session.initialize()

    async def stop(self) -> None:
        """停止 MCP 服务器连接"""
        if self._session:
            await self._session.close()
        self._session = None

    async def list_tools(self) -> list[types.Tool]:
        """获取可用工具列表"""
        if not self._session:
            raise RuntimeError("MCP client not started")

        result = await self._session.list_tools()
        return result.tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        """调用工具"""
        if not self._session:
            raise RuntimeError("MCP client not started")

        result = await self._session.call_tool(name, arguments)
        return result

    async def list_prompts(self) -> list[types.Prompt]:
        """获取可用提示模板"""
        if not self._session:
            raise RuntimeError("MCP client not started")

        result = await self._session.list_prompts()
        return result.prompts

    async def list_resources(self) -> list[types.Resource]:
        """获取可用资源"""
        if not self._session:
            raise RuntimeError("MCP client not started")

        result = await self._session.list_resources()
        return result.resources


class MCPToolAdapter:
    """MCP 工具适配器 - 转换为 PydanticAI 工具"""

    def __init__(self, client: MCPClient):
        self._client = client
        self._tools: list[Any] = []

    async def load_tools(self) -> list[Any]:
        """加载 MCP 工具并转换为 PydanticAI 格式"""
        mcp_tools = await self._client.list_tools()

        for tool in mcp_tools:
            pydantic_tool = self._convert_tool(tool)
            self._tools.append(pydantic_tool)

        return self._tools

    def _convert_tool(self, mcp_tool: types.Tool) -> Any:
        """将 MCP 工具转换为 PydanticAI 工具

        MCP Tool 结构:
        - name: str
        - description: str
        - inputSchema: dict

        转换为 PydanticAI Tool 使用 @agent.tool 装饰器
        """
        tool_name = mcp_tool.name
        tool_description = mcp_tool.description or ""
        input_schema = mcp_tool.inputSchema

        # 使用动态函数创建工具
        async def mcp_tool_wrapper(ctx: RunContext, **kwargs) -> str:
            result = await self._client.call_tool(tool_name, kwargs)
            # 处理结果
            if result.content and isinstance(result.content[0], types.TextContent):
                return result.content[0].text
            return str(result)

        # 设置工具元数据
        mcp_tool_wrapper.__name__ = tool_name
        mcp_tool_wrapper.__doc__ = tool_description

        return mcp_tool_wrapper
```

**依赖安装**:
```bash
pip install mcp
```

### 2.3 文件结构变更

```
mcbe_ai_agent/
├── config/
│   ├── settings.py          # [修改] 添加新配置项
│   └── logging.py
├── core/
│   ├── queue.py             # [修改] 增强上下文管理
│   ├── conversation.py      # [新增] 对话管理器
│   └── events.py
├── services/
│   ├── agent/
│   │   ├── core.py          # [修改] 集成 PromptManager
│   │   ├── worker.py        # [修改] 集成压缩功能
│   │   ├── tools.py         # [修改] 扩展工具系统
│   │   ├── prompt.py        # [新增] 提示词管理器
│   │   └── mcp.py           # [新增] MCP 客户端
│   └── websocket/
│       ├── server.py        # [修改] 集成新命令
│       ├── minecraft.py     # [修改] 使用 CommandRegistry
│       ├── connection.py    # [修改] 增强状态管理
│       └── command.py       # [新增] 命令注册表
├── models/
│   ├── messages.py          # [修改] 添加新消息类型
│   └── agent.py
├── data/
│   └── conversations/       # [新增] 对话持久化存储
│       └── .gitkeep
├── docs/
│   └── plans/
│       └── 2026-02-15-agent-enhancement-plan.md
└── .env.example             # [修改] 添加新环境变量
```

---

## 3. 优先级排序

### 3.1 优先级矩阵

| 优先级 | 功能 | 复杂度 | 业务价值 | 说明 |
|--------|------|--------|---------|------|
| **P0** | 命令别名系统 | 中 | 高 | 用户体验核心功能 |
| **P0** | 提示词模板 | 中 | 高 | 核心定制功能 |
| **P1** | 上下文压缩 | 低 | 中 | 性能和成本优化 |
| **P1** | 上下文持久化 | 中 | 中 | 重要用户体验 |
| **P2** | MCP 集成 | 高 | 中 | 扩展性功能 |

### 3.2 开发顺序

```
阶段 1: 命令别名系统 (P0)
  │
  ├─► 修改 MinecraftConfig 配置结构
  ├─► 实现 CommandRegistry
  ├─► 修改 MinecraftProtocolHandler
  ├─► 添加环境变量支持
  └─► 游戏内别名管理命令

阶段 2: 提示词模板 (P0)
  │
  ├─► 定义 PromptTemplate 数据结构
  ├─► 实现 PromptManager
  ├─► 集成到 dynamic_system_prompt
  ├─► 添加模板切换命令
  └─► 自定义变量支持

阶段 3: 上下文管理增强 (P1)
  │
  ├─► 实现 ConversationManager
  ├─► 添加压缩算法优化
  ├─► 添加保存/恢复命令
  └─► 添加列表/删除命令

阶段 4: MCP 集成 (P2)
  │
  ├─► 实现 MCPClient
  ├─► 实现 MCPToolAdapter
  ├─► 添加配置和工具注册
  └─► 错误处理和重试逻辑
```

---

## 4. 实施时间表

### 4.1 阶段计划

| 阶段 | 功能 | 预估工时 | 主要工作 |
|------|------|----------|----------|
| Phase 1 | 命令别名系统 | 4-6 小时 | 配置修改、CommandRegistry 实现、集成测试 |
| Phase 2 | 提示词模板 | 3-4 小时 | PromptManager 实现、模板定义、命令集成 |
| Phase 3 | 上下文管理 | 4-5 小时 | ConversationManager、压缩算法、命令 |
| Phase 4 | MCP 集成 | 6-8 小时 | MCP 客户端、工具适配、配置系统 |
| **总计** | | **17-23 小时** | |

### 4.2 里程碑

- **M1**: 命令别名系统完成 - Day 1
- **M2**: 提示词模板完成 - Day 1-2
- **M3**: 上下文管理完成 - Day 2-3
- **M4**: MCP 集成完成 - Day 3-4

---

## 5. 架构兼容性

### 5.1 现有组件兼容性

| 组件 | 兼容性 | 适配方案 |
|------|--------|----------|
| MessageBroker | ✅ 完全兼容 | 扩展方法，不修改现有接口 |
| AgentWorker | ✅ 完全兼容 | 集成新方法，不修改核心逻辑 |
| WebSocketServer | ✅ 完全兼容 | 新增命令处理器 |
| ConnectionManager | ✅ 完全兼容 | 扩展状态管理 |
| Settings | ✅ 完全兼容 | 新增配置项，默认值兼容 |

### 5.2 向后兼容性

1. **配置兼容**: 新配置项使用默认值，不影响现有部署
2. **命令兼容**: 现有命令保持不变，新增命令使用新前缀
3. **API 兼容**: 扩展现有类的方法，不修改方法签名
4. **存储兼容**: JSON 格式支持版本字段，便于迁移

### 5.3 异步架构一致性

所有新增功能都遵循现有异步架构:

```python
# 示例: 异步文件操作
async with aiofiles.open(filepath, 'w') as f:
    await f.write(json_data)

# 示例: 异步 MCP 通信
async with client:
    tools = await client.list_tools()
```

---

## 6. 错误处理方案

### 6.1 错误分类

| 错误类型 | 处理策略 | 恢复方式 |
|----------|----------|----------|
| 配置错误 | 回退默认值 | 记录警告，使用默认配置 |
| 命令解析错误 | 返回错误消息 | 提示正确用法 |
| 模板渲染错误 | 使用默认模板 | 记录错误，继续执行 |
| 存储错误 | 内存回退 | 降级为内存存储 |
| MCP 连接错误 | 工具不可用 | 自动重试，优雅降级 |

### 6.2 错误处理实现

```python
# 示例: PromptManager 错误处理
class PromptManager:
    def render(self, context: dict[str, Any]) -> str:
        try:
            template = self._templates.get(self._current_template)
            all_vars = {**self._get_builtin_variables(), **self._custom_variables, **context}
            return template.content.format(**all_vars)
        except KeyError as e:
            logger.warning("template_variable_missing", variable=str(e))
            # 回退: 移除未知变量，使用默认模板
            return self._templates["default"].content
        except Exception as e:
            logger.error("template_render_error", error=str(e))
            # 最终回退: 使用基础提示词
            return self._settings.system_prompt

# 示例: ConversationManager 错误处理
class ConversationManager:
    async def save_conversation(self, connection_id: UUID, metadata: dict) -> str | None:
        try:
            # 执行保存逻辑
            return await self._do_save(connection_id, metadata)
        except FileNotFoundError:
            # 目录不存在，创建后重试
            self._storage_path.mkdir(parents=True, exist_ok=True)
            return await self._do_save(connection_id, metadata)
        except PermissionError:
            logger.error("conversation_save_permission_denied", path=str(self._storage_path))
            return None
        except Exception as e:
            logger.error("conversation_save_failed", error=str(e))
            return None  # 降级: 内存存储
```

### 6.3 用户友好错误消息

```
[配置错误]
❌ 配置格式错误，已使用默认配置

[命令错误]
❌ 未知的命令，请使用 "帮助" 查看可用命令
❌ 别名已存在: AI聊天

[模板错误]
❌ 模板 "invalid" 不存在，已切换到默认模板
❌ 变量 {unknown_var} 不存在，已忽略

[存储错误]
❌ 保存失败: 权限不足，请检查 data/conversations 目录权限
⚠ 存储不可用，已使用内存存储

[MCP 错误]
⚠ MCP 服务器连接失败，将使用内置工具
❌ 工具调用失败: timeout
```

---

## 7. 性能影响评估

### 7.1 性能指标

| 功能 | 内存影响 | CPU 影响 | 延迟影响 |
|------|----------|----------|----------|
| 命令别名解析 | O(n) 别名数 | 忽略不计 | <1ms |
| 提示词渲染 | O(模板长度) | 忽略不计 | <5ms |
| 上下文压缩 | O(消息数) | 中等 | <50ms |
| 对话保存 | O(消息大小) | 磁盘 IO | <100ms |
| MCP 调用 | 取决于工具 | 外部延迟 | 外部决定 |

### 7.2 优化建议

1. **命令别名解析**
   - 使用前缀树 (Trie) 优化多别名匹配
   - 缓存解析结果

2. **提示词渲染**
   - 模板预编译为格式化函数
   - 变量缓存

3. **上下文压缩**
   - 异步执行，不阻塞请求处理
   - 使用后台任务

4. **对话保存**
   - 异步写入，不阻塞主流程
   - 批量写入优化

### 7.3 资源限制

```python
# 配置项限制
MAX_ALIASES_PER_COMMAND = 10       # 每命令最大别名数
MAX_TEMPLATE_LENGTH = 5000          # 模板最大长度
MAX_CUSTOM_VARIABLES = 20          # 最大自定义变量数
MAX_CONVERSATION_MESSAGES = 1000   # 单个对话最大消息数
COMPRESSION_THRESHOLD = 0.8        # 自动压缩阈值
```

---

## 附录

### A. 环境变量清单

```bash
# 命令配置 (JSON 字符串)
MINECRAFT_COMMANDS='{"AGENT 聊天": {"type": "chat", "aliases": ["AGENT chat"]}}'

# 提示词模板配置 (JSON 字符串)
PROMPT_TEMPLATES='{"my_template": {"content": "Hello {player_name}!"}}'

# 自定义变量 (分号分隔)
CUSTOM_VARIABLES='greeting=Hello;sign=Welcome'

# MCP 配置
MCP_ENABLED=false
MCP_SERVERS='[{"name": "filesystem", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"]}]'

# 上下文配置
AUTO_COMPRESS_ENABLED=true
COMPRESS_THRESHOLD=0.8
MAX_HISTORY_TURNS=20

# 存储配置
CONVERSATION_STORAGE_PATH=data/conversations
```

### B. 配置文件示例

```yaml
# config/commands.yaml (未来可能支持 YAML 配置)
commands:
  AGENT 聊天:
    type: chat
    aliases:
      - AGENT chat
      - AI 聊天
      - AI 对话
    description: 与 AI 对话
    usage: "<内容>"

  AGENT 模板:
    type: template
    aliases:
      - AGENT template
    description: 切换提示词模板
    usage: "<模板名>"
```

---

## 文档版本

| 版本 | 日期 | 作者 | 变更 |
|------|------|------|------|
| 1.1 | 2026-02-15 | Claude | 添加 PydanticAI 和 MCP 官方 SDK 最佳实践 |
| 1.0 | 2026-02-15 | Claude | 初始版本 |

---

*本文档为实施计划，具体实现可能根据实际情况调整。*
