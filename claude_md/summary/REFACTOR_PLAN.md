# MCBE WebSocket GPT 项目重构计划

## 概述

将现有的 MCBE WebSocket GPT 服务器重构为现代化的异步架构，采用 PydanticAI 框架实现 AI Agent 能力，支持多种 LLM 提供商，并通过消息队列实现 WebSocket 通信与 LLM 请求的完全解耦。

---

## 一、当前架构问题分析

### 1.1 现有架构

```
┌─────────────────────────────────────────────────────┐
│                  main_server.py                      │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────┐  │
│  │  WebSocket  │───▶│  gpt_main   │───▶│ GPT API │  │
│  │  Handler    │◀───│  (阻塞等待) │◀───│  调用   │  │
│  └─────────────┘    └─────────────┘    └─────────┘  │
└─────────────────────────────────────────────────────┘
```

### 1.2 主要问题

| 问题 | 描述 | 影响 |
|------|------|------|
| **同步阻塞** | LLM 请求期间 WebSocket 消息处理被阻塞 | MC 客户端响应延迟，可能断连 |
| **单一 LLM** | 硬编码 DeepSeek/OpenAI，切换困难 | 无法利用不同模型优势 |
| **耦合严重** | 业务逻辑、通信、AI 调用混杂在一起 | 难以维护和扩展 |
| **无类型安全** | 消息结构使用字典，缺乏验证 | 运行时错误难以排查 |
| **认证简陋** | JWT 实现过于简单，无刷新机制 | 安全性和用户体验不足 |

---

## 二、目标架构设计

### 2.1 整体架构

```
┌────────────────────────────────────────────────────────────────────────┐
│                           Application Layer                             │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────────────┐   │
│  │   Config     │     │   Logging    │     │   Dependency         │   │
│  │   Manager    │     │   System     │     │   Injection          │   │
│  └──────────────┘     └──────────────┘     └──────────────────────┘   │
├────────────────────────────────────────────────────────────────────────┤
│                           Service Layer                                 │
│                                                                         │
│  ┌────────────────────┐              ┌────────────────────────────┐   │
│  │  WebSocket Server  │              │     AI Agent Service       │   │
│  │  ┌──────────────┐  │   asyncio    │  ┌──────────────────────┐  │   │
│  │  │ Connection   │  │    Queue     │  │   PydanticAI Agent   │  │   │
│  │  │ Manager      │◀─┼─────────────▶│  │   - Multi-LLM        │  │   │
│  │  ├──────────────┤  │              │  │   - Tools            │  │   │
│  │  │ Message      │  │              │  │   - Streaming        │  │   │
│  │  │ Router       │  │              │  └──────────────────────┘  │   │
│  │  ├──────────────┤  │              │  ┌──────────────────────┐  │   │
│  │  │ MC Protocol  │  │              │  │   Provider Registry  │  │   │
│  │  │ Handler      │  │              │  │   - OpenAI           │  │   │
│  │  └──────────────┘  │              │  │   - DeepSeek         │  │   │
│  └────────────────────┘              │  │   - Anthropic        │  │   │
│                                       │  │   - Ollama (local)   │  │   │
│                                       │  └──────────────────────┘  │   │
├───────────────────────────────────────┴────────────────────────────────┤
│                            Data Layer                                   │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────────────┐   │
│  │   Session    │     │   Auth       │     │   Conversation       │   │
│  │   Store      │     │   Store      │     │   History            │   │
│  └──────────────┘     └──────────────┘     └──────────────────────┘   │
└────────────────────────────────────────────────────────────────────────┘
```

### 2.2 核心设计原则

1. **异步非阻塞**: 使用 `asyncio.Queue` 实现 WS 和 LLM 的生产者-消费者模式
2. **依赖注入**: 通过 PydanticAI 的 `deps_type` 管理运行时依赖
3. **类型安全**: 所有消息和配置使用 Pydantic 模型定义
4. **可插拔 LLM**: 通过 Provider 抽象层支持多种模型
5. **优雅降级**: LLM 不可用时不影响 WS 基础通信

---

## 三、模块详细设计

### 3.1 项目目录结构

```
mcbe_ai_agent/
├── pyproject.toml              # 项目配置和依赖
├── config/
│   ├── __init__.py
│   ├── settings.py             # Pydantic Settings 配置
│   └── logging.py              # 日志配置
├── models/
│   ├── __init__.py
│   ├── messages.py             # WebSocket 消息模型
│   ├── minecraft.py            # MC 协议消息模型
│   ├── auth.py                 # 认证相关模型
│   └── agent.py                # Agent 输入输出模型
├── services/
│   ├── __init__.py
│   ├── websocket/
│   │   ├── __init__.py
│   │   ├── server.py           # WebSocket 服务器
│   │   ├── connection.py       # 连接管理器
│   │   ├── router.py           # 命令路由器
│   │   └── minecraft.py        # MC 协议处理
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── core.py             # PydanticAI Agent 定义
│   │   ├── providers.py        # LLM Provider 注册
│   │   ├── tools.py            # Agent 工具函数
│   │   └── worker.py           # Agent 工作线程
│   └── auth/
│       ├── __init__.py
│       ├── jwt_handler.py      # JWT 处理
│       └── session.py          # 会话管理
├── core/
│   ├── __init__.py
│   ├── queue.py                # 消息队列管理
│   ├── events.py               # 事件定义
│   └── exceptions.py           # 自定义异常
├── storage/
│   ├── __init__.py
│   ├── conversation.py         # 对话历史存储
│   └── session.py              # 会话持久化
├── main.py                     # 应用入口
└── cli.py                      # 命令行接口
```

### 3.2 核心组件设计

#### 3.2.1 配置管理 (`config/settings.py`)

```python
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Literal

class LLMProviderConfig(BaseSettings):
    """LLM 提供商配置"""
    name: str
    api_key: str | None = None
    base_url: str | None = None
    model: str
    enabled: bool = True

class Settings(BaseSettings):
    """应用配置"""
    # 服务器配置
    host: str = "0.0.0.0"
    port: int = 8080

    # 认证配置
    jwt_secret: str = Field(..., env="JWT_SECRET")
    jwt_expiration: int = 1800
    default_password: str = "123456"

    # LLM 配置
    default_provider: str = "deepseek"
    providers: dict[str, LLMProviderConfig] = {}

    # Agent 配置
    system_prompt: str = "请始终保持积极和专业的态度..."
    enable_reasoning_output: bool = True
    max_history_turns: int = 20

    # 队列配置
    queue_max_size: int = 100
    llm_worker_count: int = 2

    model_config = {"env_file": ".env", "env_nested_delimiter": "__"}
```

#### 3.2.2 消息模型 (`models/messages.py`)

```python
from pydantic import BaseModel, Field
from typing import Literal
from datetime import datetime
from uuid import UUID, uuid4

class BaseMessage(BaseModel):
    """消息基类"""
    id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=datetime.now)
    connection_id: UUID

class ChatRequest(BaseMessage):
    """聊天请求"""
    type: Literal["chat"] = "chat"
    content: str
    use_context: bool = True

class ChatResponse(BaseMessage):
    """聊天响应"""
    type: Literal["response"] = "response"
    content: str
    reasoning: str | None = None
    is_complete: bool = False
    token_usage: dict | None = None

class StreamChunk(BaseMessage):
    """流式响应块"""
    type: Literal["stream_chunk"] = "stream_chunk"
    chunk_type: Literal["reasoning", "content", "error"]
    content: str
    sequence: int

class SystemNotification(BaseMessage):
    """系统通知"""
    type: Literal["notification"] = "notification"
    level: Literal["info", "warning", "error"]
    message: str
```

#### 3.2.3 消息队列 (`core/queue.py`)

```python
import asyncio
from typing import Generic, TypeVar
from dataclasses import dataclass
from uuid import UUID

T = TypeVar('T')

@dataclass
class QueueItem(Generic[T]):
    """队列项包装"""
    connection_id: UUID
    payload: T
    priority: int = 0

class MessageBroker:
    """消息代理 - 管理 WS 和 Agent 之间的通信"""

    def __init__(self, max_size: int = 100):
        # 请求队列: WS -> Agent
        self._request_queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=max_size)
        # 响应队列: Agent -> WS (每个连接一个)
        self._response_queues: dict[UUID, asyncio.Queue] = {}

    async def submit_request(self, item: QueueItem) -> None:
        """提交 LLM 请求（非阻塞）"""
        await self._request_queue.put((item.priority, item))

    async def get_request(self) -> QueueItem:
        """获取待处理请求（Agent Worker 使用）"""
        _, item = await self._request_queue.get()
        return item

    def register_connection(self, connection_id: UUID) -> asyncio.Queue:
        """注册连接的响应队列"""
        queue = asyncio.Queue()
        self._response_queues[connection_id] = queue
        return queue

    def unregister_connection(self, connection_id: UUID) -> None:
        """注销连接"""
        self._response_queues.pop(connection_id, None)

    async def send_response(self, connection_id: UUID, response) -> None:
        """发送响应到指定连接"""
        if queue := self._response_queues.get(connection_id):
            await queue.put(response)
```

#### 3.2.4 PydanticAI Agent (`services/agent/core.py`)

```python
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic import BaseModel
from dataclasses import dataclass
from typing import AsyncIterator
import httpx

from models.agent import AgentResponse
from config.settings import Settings

@dataclass
class AgentDependencies:
    """Agent 运行时依赖"""
    connection_id: str
    player_name: str
    http_client: httpx.AsyncClient
    settings: Settings
    send_to_game: callable  # 发送消息到游戏的回调

# 创建 Agent
chat_agent = Agent(
    'deepseek:deepseek-chat',  # 默认模型，运行时可覆盖
    deps_type=AgentDependencies,
    system_prompt="请始终保持积极和专业的态度。回答尽量简洁，适当添加换行符。",
    retries=2,
)

@chat_agent.system_prompt
async def dynamic_system_prompt(ctx: RunContext[AgentDependencies]) -> str:
    """动态系统提示词"""
    base_prompt = ctx.deps.settings.system_prompt
    return f"{base_prompt}\n\n当前玩家: {ctx.deps.player_name}"

@chat_agent.tool
async def run_minecraft_command(
    ctx: RunContext[AgentDependencies],
    command: str
) -> str:
    """执行 Minecraft 命令"""
    # 通过回调发送命令到游戏
    await ctx.deps.send_to_game({
        "type": "command",
        "command": command
    })
    return f"已执行命令: {command}"

async def stream_chat(
    agent: Agent,
    prompt: str,
    deps: AgentDependencies,
    model_override: str | None = None
) -> AsyncIterator[dict]:
    """流式聊天处理"""
    run_model = model_override or agent.model

    async with agent.run_stream(
        prompt,
        deps=deps,
        model=run_model
    ) as result:
        async for chunk in result.stream_text(delta=True):
            yield {
                "type": "content",
                "content": chunk,
                "is_complete": False
            }

    yield {
        "type": "content",
        "content": "",
        "is_complete": True,
        "usage": result.usage()
    }
```

#### 3.2.5 LLM Provider 注册 (`services/agent/providers.py`)

```python
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.ollama import OllamaModel
from typing import Protocol
from config.settings import LLMProviderConfig

class ModelFactory(Protocol):
    """模型工厂协议"""
    def create(self, config: LLMProviderConfig): ...

class ProviderRegistry:
    """LLM Provider 注册表"""

    _providers: dict[str, type] = {
        "openai": OpenAIProvider,
        "deepseek": DeepSeekProvider,
    }

    _model_classes: dict[str, type] = {
        "openai": OpenAIChatModel,
        "deepseek": OpenAIChatModel,  # DeepSeek 使用 OpenAI 兼容接口
        "anthropic": AnthropicModel,
        "ollama": OllamaModel,
    }

    @classmethod
    def get_model(cls, config: LLMProviderConfig):
        """根据配置获取模型实例"""
        provider_name = config.name.lower()

        if provider_name == "deepseek":
            return OpenAIChatModel(
                config.model,
                provider=DeepSeekProvider(api_key=config.api_key)
            )
        elif provider_name == "openai":
            return OpenAIChatModel(
                config.model,
                provider=OpenAIProvider(api_key=config.api_key)
            )
        elif provider_name == "anthropic":
            return AnthropicModel(config.model, api_key=config.api_key)
        elif provider_name == "ollama":
            return OllamaModel(config.model, base_url=config.base_url)
        else:
            raise ValueError(f"Unknown provider: {provider_name}")

    @classmethod
    def list_providers(cls) -> list[str]:
        return list(cls._providers.keys())
```

#### 3.2.6 Agent Worker (`services/agent/worker.py`)

```python
import asyncio
from uuid import UUID
import httpx

from core.queue import MessageBroker, QueueItem
from services.agent.core import chat_agent, stream_chat, AgentDependencies
from services.agent.providers import ProviderRegistry
from models.messages import ChatRequest, StreamChunk
from config.settings import Settings

class AgentWorker:
    """Agent 工作协程 - 从队列消费请求并处理"""

    def __init__(
        self,
        broker: MessageBroker,
        settings: Settings,
        worker_id: int = 0
    ):
        self.broker = broker
        self.settings = settings
        self.worker_id = worker_id
        self._running = False
        self._http_client: httpx.AsyncClient | None = None

    async def start(self):
        """启动 Worker"""
        self._running = True
        self._http_client = httpx.AsyncClient(timeout=60)

        while self._running:
            try:
                item = await self.broker.get_request()
                await self._process_request(item)
            except asyncio.CancelledError:
                break
            except Exception as e:
                # 记录错误但继续运行
                print(f"Worker {self.worker_id} error: {e}")

    async def stop(self):
        """停止 Worker"""
        self._running = False
        if self._http_client:
            await self._http_client.aclose()

    async def _process_request(self, item: QueueItem[ChatRequest]):
        """处理单个请求"""
        request = item.payload
        connection_id = item.connection_id

        # 构建依赖
        deps = AgentDependencies(
            connection_id=str(connection_id),
            player_name="Player",  # 从 session 获取
            http_client=self._http_client,
            settings=self.settings,
            send_to_game=lambda msg: self.broker.send_response(connection_id, msg)
        )

        # 获取模型
        provider_config = self.settings.providers.get(self.settings.default_provider)
        model = ProviderRegistry.get_model(provider_config) if provider_config else None

        # 流式处理
        sequence = 0
        try:
            async for chunk in stream_chat(chat_agent, request.content, deps, model):
                response = StreamChunk(
                    connection_id=connection_id,
                    chunk_type=chunk["type"],
                    content=chunk["content"],
                    sequence=sequence,
                )
                await self.broker.send_response(connection_id, response)
                sequence += 1
        except Exception as e:
            error_chunk = StreamChunk(
                connection_id=connection_id,
                chunk_type="error",
                content=str(e),
                sequence=sequence,
            )
            await self.broker.send_response(connection_id, error_chunk)
```

#### 3.2.7 WebSocket 连接管理 (`services/websocket/connection.py`)

```python
import asyncio
from uuid import UUID, uuid4
from dataclasses import dataclass, field
from websockets import WebSocketServerProtocol
from datetime import datetime

from core.queue import MessageBroker
from models.messages import StreamChunk

@dataclass
class ConnectionState:
    """连接状态"""
    id: UUID = field(default_factory=uuid4)
    websocket: WebSocketServerProtocol = None
    authenticated: bool = False
    player_name: str | None = None
    connected_at: datetime = field(default_factory=datetime.now)
    context_enabled: bool = True
    response_queue: asyncio.Queue = None

class ConnectionManager:
    """连接管理器"""

    def __init__(self, broker: MessageBroker):
        self.broker = broker
        self._connections: dict[UUID, ConnectionState] = {}

    async def register(self, websocket: WebSocketServerProtocol) -> ConnectionState:
        """注册新连接"""
        state = ConnectionState(websocket=websocket)
        state.response_queue = self.broker.register_connection(state.id)
        self._connections[state.id] = state

        # 启动响应发送任务
        asyncio.create_task(self._response_sender(state))

        return state

    async def unregister(self, connection_id: UUID):
        """注销连接"""
        if state := self._connections.pop(connection_id, None):
            self.broker.unregister_connection(connection_id)

    async def _response_sender(self, state: ConnectionState):
        """响应发送协程 - 独立于 LLM 请求"""
        while state.id in self._connections:
            try:
                response = await asyncio.wait_for(
                    state.response_queue.get(),
                    timeout=1.0
                )
                await self._send_to_minecraft(state, response)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"Response sender error: {e}")
                break

    async def _send_to_minecraft(self, state: ConnectionState, response):
        """发送消息到 Minecraft"""
        if isinstance(response, StreamChunk):
            # 转换为 MC tellraw 格式
            message = self._format_for_minecraft(response)
            await state.websocket.send(message)

    def _format_for_minecraft(self, chunk: StreamChunk) -> str:
        """格式化为 Minecraft 消息"""
        # 实现 MC 协议格式化...
        pass
```

---

## 四、实现路线图

### Phase 1: 基础架构 (核心重构)

**目标**: 建立新的项目结构和核心组件

| 任务 | 描述 | 优先级 |
|------|------|--------|
| 1.1 | 创建项目结构和 `pyproject.toml` | P0 |
| 1.2 | 实现 Pydantic Settings 配置系统 | P0 |
| 1.3 | 定义所有 Pydantic 消息模型 | P0 |
| 1.4 | 实现 `MessageBroker` 消息队列 | P0 |
| 1.5 | 实现基础日志系统 | P1 |

### Phase 2: AI Agent 层

**目标**: 集成 PydanticAI 框架

| 任务 | 描述 | 优先级 |
|------|------|--------|
| 2.1 | 实现 `ProviderRegistry` 多 LLM 支持 | P0 |
| 2.2 | 创建 PydanticAI Agent 定义 | P0 |
| 2.3 | 实现 Agent Worker 消费者 | P0 |
| 2.4 | 添加流式响应处理 | P0 |
| 2.5 | 实现 reasoning content 特殊处理 | P1 |
| 2.6 | 添加 Agent Tools (MC 命令执行等) | P2 |

### Phase 3: WebSocket 层

**目标**: 重构 WebSocket 服务

| 任务 | 描述 | 优先级 |
|------|------|--------|
| 3.1 | 实现 `ConnectionManager` | P0 |
| 3.2 | 重构 MC 协议处理器 | P0 |
| 3.3 | 实现命令路由器 | P0 |
| 3.4 | 添加响应发送协程（非阻塞） | P0 |
| 3.5 | 实现连接心跳和重连逻辑 | P1 |

### Phase 4: 认证与会话

**目标**: 增强安全性和用户体验

| 任务 | 描述 | 优先级 |
|------|------|--------|
| 4.1 | 重构 JWT 认证系统 | P1 |
| 4.2 | 实现 Token 刷新机制 | P1 |
| 4.3 | 添加会话管理 | P1 |
| 4.4 | 实现对话历史持久化 | P2 |

### Phase 5: 用户体验增强

**目标**: 提升交互友好度

| 任务 | 描述 | 优先级 |
|------|------|--------|
| 5.1 | 添加命令自动补全提示 | P2 |
| 5.2 | 实现模型切换命令 | P1 |
| 5.3 | 添加使用统计和 token 计数 | P2 |
| 5.4 | 实现更友好的错误提示 | P1 |
| 5.5 | 添加帮助命令系统 | P2 |

### Phase 6: 部署与监控

**目标**: 生产就绪

| 任务 | 描述 | 优先级 |
|------|------|--------|
| 6.1 | 更新 CLI 入口 | P1 |
| 6.2 | Docker 容器化 | P2 |
| 6.3 | 健康检查端点 | P2 |
| 6.4 | 性能监控集成 | P3 |

---

## 五、依赖清单

### 核心依赖

```toml
[project]
dependencies = [
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "pydantic-ai>=0.0.49",
    "websockets>=12.0",
    "httpx>=0.27",
    "PyJWT>=2.8",
    "structlog>=24.0",  # 结构化日志
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.3",
    "mypy>=1.9",
]
```

### LLM Provider 依赖

```toml
# 按需安装
anthropic = ["anthropic>=0.25"]
ollama = ["ollama>=0.2"]
```

---

## 六、关键技术决策

### 6.1 为什么选择 asyncio.Queue 而非 Redis/RabbitMQ？

| 方案 | 优点 | 缺点 |
|------|------|------|
| asyncio.Queue | 零依赖、低延迟、简单 | 不支持分布式、进程内存限制 |
| Redis Streams | 持久化、分布式、可观测 | 额外依赖、增加复杂度 |
| RabbitMQ | 成熟可靠、支持多种模式 | 过于重量级 |

**决策**: 使用 `asyncio.Queue`，因为：
- 单进程足够处理 MCBE 连接场景
- 最小化依赖和部署复杂度
- 未来可通过接口抽象迁移到 Redis

### 6.2 为什么使用 PydanticAI 而非 LangChain？

| 方案 | 优点 | 缺点 |
|------|------|------|
| PydanticAI | 类型安全、轻量、与 Pydantic 无缝集成 | 生态较新 |
| LangChain | 成熟生态、丰富工具链 | 抽象重、学习曲线陡 |

**决策**: 使用 PydanticAI，因为：
- 原生 Pydantic 集成，类型安全
- 简洁的 Agent 定义语法
- 内置流式响应支持
- 易于理解和调试

### 6.3 消息分发策略

采用 **每连接独立响应队列** 模式：

```
Request Queue (共享)     Response Queues (每连接一个)
     ┌───┐                    ┌───┐ ← Connection A
     │ R │ ──▶ Worker ───────▶│ A │
     │ e │                    └───┘
     │ q │                    ┌───┐ ← Connection B
     │ s │ ──▶ Worker ───────▶│ B │
     └───┘                    └───┘
```

优势：
- 响应不会串流到错误的连接
- 每个连接独立发送速率控制
- Worker 数量可独立扩展

---

## 七、迁移策略

### 7.1 渐进式迁移

1. **并行运行**: 新旧系统同时运行在不同端口
2. **功能对等**: 确保新系统支持所有现有命令
3. **灰度切换**: 通过配置逐步将流量切换到新系统
4. **旧代码保留**: 在 `legacy/` 目录保留旧代码作为参考

### 7.2 兼容性保证

- 保持所有现有命令语法不变
- 保持 MC 消息格式兼容
- 支持现有 `tokens.json` 格式迁移

---

## 八、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| PydanticAI 框架不稳定 | 功能缺失或 API 变更 | 锁定版本，封装抽象层 |
| 队列积压导致内存溢出 | 服务崩溃 | 设置队列上限，实现背压机制 |
| 流式响应中断 | 用户体验差 | 实现重试和断点续传 |
| 多 Worker 竞争条件 | 数据不一致 | 使用无状态设计，状态存储外部化 |

---

## 九、成功指标

- [ ] 所有现有功能正常工作
- [ ] LLM 请求不阻塞 WS ping/pong
- [ ] 支持至少 3 种 LLM Provider
- [ ] 单元测试覆盖率 > 80%
- [ ] 连接 100 个客户端时响应时间 < 100ms

---

## 附录: 快速启动命令

```bash
# 开发环境
uv pip install -e ".[dev]"

# 运行服务
python -m mcbe_ai_agent serve --host 0.0.0.0 --port 8080

# 运行测试
pytest tests/ -v

# 类型检查
mypy src/
```
