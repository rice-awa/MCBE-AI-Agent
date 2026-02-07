# MCBE GPT Agent v2.0 - 现代化重构

## 概述

这是 MCBE WebSocket GPT 项目的完全重构版本，采用现代化异步架构，基于 PydanticAI 框架，支持多种 LLM 提供商，实现了 WebSocket 和 LLM 请求的完全解耦。

## 核心特性

### 🚀 现代化架构
- **异步非阻塞**: WebSocket 通信与 LLM 请求完全分离
- **消息队列**: 使用 `asyncio.Queue` 实现生产者-消费者模式
- **类型安全**: 全面使用 Pydantic 进行数据验证
- **结构化日志**: 基于 structlog 的现代日志系统

### 🤖 AI Agent 能力
- **PydanticAI 框架**: 类型安全的 AI Agent 实现
- **流式响应**: 支持实时流式输出
- **Agent Tools**: 内置 Minecraft 命令执行工具
- **动态系统提示词**: 根据玩家信息动态调整

### 🔌 多 LLM 支持
- **DeepSeek**: deepseek-reasoner (支持思维链)
- **OpenAI**: GPT-4o 等模型
- **Anthropic**: Claude Sonnet 4.5
- **Ollama**: 本地模型支持

### 🎮 用户友好
- **非阻塞通信**: LLM 请求不影响 MC 连接
- **实时切换模型**: 游戏内动态切换 LLM
- **上下文管理**: 灵活的对话历史控制
- **JWT 认证**: 安全的令牌认证机制

## 项目结构

```
mcbe_ai_agent/
├── config/                 # 配置管理
│   ├── settings.py        # Pydantic Settings
│   └── logging.py         # 日志配置
├── models/                # 数据模型
│   ├── messages.py        # WebSocket 消息
│   ├── minecraft.py       # MC 协议模型
│   └── agent.py           # Agent 相关模型
├── core/                  # 核心模块
│   ├── queue.py           # 消息队列 (MessageBroker)
│   ├── events.py          # 事件系统
│   └── exceptions.py      # 自定义异常
├── services/              # 服务层
│   ├── agent/            # AI Agent 服务
│   │   ├── core.py       # PydanticAI Agent
│   │   ├── providers.py  # LLM Provider 注册表
│   │   └── worker.py     # Agent Worker
│   ├── websocket/        # WebSocket 服务
│   │   ├── server.py     # WS 服务器
│   │   ├── connection.py # 连接管理
│   │   └── minecraft.py  # MC 协议处理
│   └── auth/             # 认证服务
│       └── jwt_handler.py
├── storage/               # 存储层 (TODO)
├── main.py               # 应用入口
├── cli.py                # CLI 工具
└── pyproject.toml        # 项目配置
```

## 架构设计

### 消息流转

```
┌─────────────┐          ┌──────────────┐         ┌─────────────┐
│  Minecraft  │          │   Message    │         │   Agent     │
│   Client    │◀────────▶│   Broker     │◀───────▶│   Worker    │
│             │          │              │         │             │
│ WebSocket   │          │  Request Q   │         │ PydanticAI  │
│  Handler    │          │ Response Q   │         │   Stream    │
└─────────────┘          └──────────────┘         └─────────────┘
     │                         │                         │
     │  非阻塞提交请求          │                         │
     ├────────────────────────▶│                         │
     │                         │   Worker 消费请求        │
     │                         ├────────────────────────▶│
     │                         │                         │
     │                         │  ◀───── 流式响应 ───────│
     │  ◀────── 响应队列 ──────│                         │
     │  独立发送协程            │                         │
     └────────────────────────▶MC (tellraw)
```

### 核心优势

1. **非阻塞设计**
   - WebSocket Handler 提交请求后立即返回
   - 独立的响应发送协程处理 LLM 输出
   - MC 客户端 ping/pong 不受 LLM 延迟影响

2. **类型安全**
   ```python
   class ChatRequest(BaseMessage):
       type: Literal["chat"] = "chat"
       content: str
       player_name: str | None = None
       use_context: bool = True
   ```

3. **依赖注入**
   ```python
   @dataclass
   class AgentDependencies:
       connection_id: UUID
       player_name: str
       settings: Settings
       http_client: httpx.AsyncClient
       send_to_game: Callable
       run_command: Callable
   ```

## 快速开始

### 1. 准备环境 (推荐)

强烈建议在 Python 虚拟环境中运行项目，以避免依赖冲突：

**Windows:**
```powershell
python -m venv venv
.\venv\Scripts\activate
```

**Linux/macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### 2. 安装依赖

```bash
cd mcbe_ai_agent
pip install -r requirements.txt
```

### 3. 初始化配置

```bash
python cli.py init
```

这会创建 `.env` 文件，编辑并填入 API 密钥：

```env
DEEPSEEK_API_KEY=your-api-key-here
SECRET_KEY=your-secret-key
WEBSOCKET_PASSWORD=your-password
```

### 4. 查看配置信息

```bash
python cli.py info
```

### 5. 测试 LLM 连接

```bash
python cli.py test-provider deepseek
```

### 6. 启动服务器

```bash
python cli.py serve
```

或使用环境变量：

```bash
python main.py
```

## 游戏内使用

### 1. 连接服务器

在 Minecraft 聊天框输入：
```
/wsserver <服务器IP>:8080
```

### 2. 登录认证

```
#登录 123456
```

### 3. 开始聊天

```
AGENT 聊天 你好，请介绍一下自己
```

### 4. 其他命令

```
AGENT 上下文 启用          # 启用对话上下文
AGENT 上下文 关闭          # 关闭对话上下文
AGENT 上下文 状态          # 查看当前状态
切换模型 openai          # 切换到 OpenAI
切换模型 deepseek        # 切换回 DeepSeek
帮助                     # 显示帮助信息
运行命令 time set day    # 执行游戏命令
```

## 配置说明

### 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `HOST` | 服务器地址 | `0.0.0.0` |
| `PORT` | 服务器端口 | `8080` |
| `SECRET_KEY` | JWT 密钥 | - |
| `WEBSOCKET_PASSWORD` | 登录密码 | `123456` |
| `DEEPSEEK_API_KEY` | DeepSeek API Key | - |
| `OPENAI_API_KEY` | OpenAI API Key | - |
| `ANTHROPIC_API_KEY` | Anthropic API Key | - |
| `DEFAULT_PROVIDER` | 默认 LLM | `deepseek` |
| `LLM_WORKER_COUNT` | Worker 数量 | `2` |
| `LOG_LEVEL` | 日志级别 | `INFO` |

### Settings 配置

在代码中可以通过 `Settings` 类访问所有配置：

```python
from config import get_settings

settings = get_settings()
print(settings.default_provider)
print(settings.list_available_providers())
```

## 架构亮点

### 1. MessageBroker - 消息队列

```python
class MessageBroker:
    """消息代理 - WS 和 Agent 解耦的核心"""

    async def submit_request(self, connection_id, payload, priority=0):
        """非阻塞提交请求"""

    async def send_response(self, connection_id, response):
        """发送响应到指定连接"""
```

**关键特性**:
- 优先级队列支持紧急请求
- 每连接独立响应队列
- 支持多 Worker 并发消费

### 2. ProviderRegistry - LLM 抽象

```python
class ProviderRegistry:
    @classmethod
    def get_model(cls, config: LLMProviderConfig) -> Model:
        """统一的 LLM 创建接口"""
```

**支持的提供商**:
- DeepSeek (OpenAI-compatible)
- OpenAI
- Anthropic (Claude)
- Ollama (本地模型)

### 3. ConnectionManager - 连接管理

```python
class ConnectionManager:
    async def _response_sender(self, state: ConnectionState):
        """独立的响应发送协程 - 不阻塞主循环"""
```

**设计优势**:
- 每个连接独立的发送协程
- 超时机制避免永久阻塞
- 优雅的错误处理

### 4. PydanticAI Agent

```python
@chat_agent.tool
async def run_minecraft_command(ctx: RunContext, command: str) -> str:
    """Agent 可以执行 MC 命令"""
    await ctx.deps.run_command(command)
    return f"已执行命令: /{command}"
```

**Agent 能力**:
- 类型安全的工具定义
- 动态系统提示词
- 流式响应支持
- 依赖注入

## 性能优化

### 非阻塞架构

**旧架构问题**:
```python
# 阻塞式 - LLM 请求阻塞 WS 消息处理
async for chunk in conversation.call_gpt(prompt):
    await websocket.send(chunk)  # WS 被阻塞
```

**新架构解决方案**:
```python
# 非阻塞 - 提交后立即返回
await broker.submit_request(connection_id, chat_req)

# 独立协程处理响应
async def _response_sender():
    while True:
        response = await queue.get()
        await websocket.send(response)
```

### Worker 池

- 多个 Agent Worker 并发处理请求
- 可配置 Worker 数量 (`LLM_WORKER_COUNT`)
- 自动负载均衡

## 与旧版对比

| 特性 | 旧版本 | v2.0 |
|------|--------|------|
| 架构 | 同步阻塞 | 异步非阻塞 |
| LLM 支持 | 单一 (硬编码) | 多提供商 (可扩展) |
| 类型安全 | 字典 | Pydantic 模型 |
| 消息队列 | 无 | MessageBroker |
| Agent 框架 | 自定义 | PydanticAI |
| 配置管理 | 环境变量 | Pydantic Settings |
| 日志系统 | print/基础 logging | structlog |
| 代码组织 | 单文件 | 模块化分层 |

## 开发指南

### 添加新的 LLM Provider

1. 在 `providers.py` 添加创建方法：

```python
@classmethod
def _create_custom_model(cls, config: LLMProviderConfig) -> Model:
    from custom_provider import CustomModel
    return CustomModel(config.model, api_key=config.api_key)
```

2. 在 `get_model` 中注册：

```python
elif provider_name == "custom":
    return cls._create_custom_model(config)
```

### 添加新的 Agent Tool

在 `services/agent/core.py` 中添加：

```python
@chat_agent.tool
async def your_tool(ctx: RunContext[AgentDependencies], param: str) -> str:
    """工具描述"""
    # 实现逻辑
    return "结果"
```

### 自定义命令

在 `services/websocket/minecraft.py` 的 `COMMANDS` 中添加：

```python
COMMANDS = {
    "自定义命令": "custom_cmd",
}
```

然后在 `server.py` 中实现处理器：

```python
async def handle_command(self, state, cmd_type, content):
    if cmd_type == "custom_cmd":
        await self.handle_custom(state, content)
```

## 故障排查

### 1. 连接失败

检查防火墙和端口：
```bash
netstat -an | grep 8080
```

### 2. LLM 请求失败

测试提供商连接：
```bash
python cli.py test-provider deepseek
```

检查日志：
```bash
tail -f logs/mcbe_ai_agent.log
```

### 3. 队列积压

查看统计信息（在代码中）：
```python
stats = broker.get_stats()
print(stats)  # {"pending_requests": N, "active_connections": M}
```

## 扩展性

### 水平扩展

当前架构使用 `asyncio.Queue`，单进程足够。如需分布式：

1. 替换 `MessageBroker` 为 Redis Streams
2. 实现 Pub/Sub 响应分发
3. 使用共享存储（Redis/PostgreSQL）

### 添加持久化

在 `storage/` 目录实现：
- `conversation.py`: 对话历史存储
- `session.py`: 会话管理
- `metrics.py`: 使用统计

## 安全建议

1. **生产环境配置**
   - 更改 `SECRET_KEY` 为强随机值
   - 设置复杂的 `WEBSOCKET_PASSWORD`
   - 使用 HTTPS（通过反向代理）

2. **API 密钥管理**
   - 不要提交 `.env` 到版本控制
   - 使用密钥管理服务（如 AWS Secrets Manager）

3. **速率限制**
   - 在 `MessageBroker` 实现请求速率限制
   - 防止单用户滥用

## 未来计划

- [ ] 对话历史持久化
- [ ] Token 使用统计
- [ ] Web 管理界面
- [ ] 支持更多 Agent Tools
- [ ] 插件系统
- [ ] 多语言支持
- [ ] Docker 容器化
- [ ] Kubernetes 部署示例

## 技术栈

- **Python 3.11+**
- **PydanticAI**: AI Agent 框架
- **Pydantic**: 数据验证
- **WebSockets**: 实时通信
- **httpx**: 异步 HTTP 客户端
- **PyJWT**: JWT 认证
- **structlog**: 结构化日志
- **Click**: CLI 工具

## 许可证

MIT License - 与原项目保持一致

## 来源及参考

- 原项目: [rice-awa/MCBE_WebSocket_gpt](https://github.com/rice-awa/MCBE_WebSocket_gpt)
- PydanticAI: [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai)

---

**版本**: 2.0.0
**重构完成时间**: 2026-02-06
**架构**: 现代化异步 + PydanticAI
