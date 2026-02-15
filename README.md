# MCBE GPT Agent v2.0 - 现代化重构

## 概述

这是 [MCBE WebSocket GPT](https://github.com/rice-awa/MCBE_WebSocket_gpt) 项目的完全重构版本，采用现代化异步架构，基于 PydanticAI 框架，支持多种 LLM 提供商，实现了 WebSocket 和 LLM 请求的完全解耦。

## 核心特性

### 🚀 现代化架构
- **异步非阻塞**: WebSocket 通信与 LLM 请求完全分离
- **消息队列**: 使用 `asyncio.Queue` 实现生产者-消费者模式
- **类型安全**: 全面使用 Pydantic 进行数据验证
- **结构化日志**: 基于 structlog 的现代日志系统

### 🤖 AI Agent 能力
- **PydanticAI 框架**: 类型安全的 AI Agent 实现
- **流式响应**: 支持实时流式输出，按完整句子发送
- **Agent Tools**: 内置 Minecraft 命令执行、MCWiki 搜索等工具
- **动态系统提示词**: 根据玩家信息动态调整
- **模型预热**: 启动时自动预热 LLM 模型，提高首次响应速度
- **命令响应回传**: Agent 执行命令后自动回传 commandResponse，工具调用更流畅

### 🔌 多 LLM 支持
- **DeepSeek**: deepseek-reasoner (支持思维链)
- **OpenAI**: GPT-5 等模型
- **Anthropic**: Claude Sonnet 4.5
- **Ollama**: 本地模型支持

### 🎮 用户友好
- **非阻塞通信**: LLM 请求不影响 MC 连接
- **实时切换模型**: 游戏内动态切换 LLM
- **上下文管理**: 灵活的对话历史控制
- **JWT 认证**: 安全的令牌认证机制
- **ScriptEvent 支持**: 支持发送 scriptevent，方便后续对接SAPI

## 项目结构

```
MCBE-AI-Agent/
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
│   │   ├── worker.py     # Agent Worker
│   │   └── tools.py      # Agent 工具定义
│   ├── websocket/        # WebSocket 服务
│   │   ├── server.py     # WS 服务器
│   │   ├── connection.py # 连接管理
│   │   └── minecraft.py  # MC 协议处理
│   ├── auth/             # 认证服务
│   │   └── jwt_handler.py
│   └── mcwiki/           # MCWiki 搜索服务
│       └── search.py     # Wiki 搜索工具
├── storage/               # 存储层 (TODO)
├── cli.py                # 应用入口与 CLI 工具
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

**Termux (Android):**
```bash
pkg install python -y
python -m venv venv
source venv/bin/activate
```

### 2. 安装依赖

```bash
cd MCBE-AI-Agent
pip install -r requirements.txt
```

### 3. 初始化配置

```bash
python cli.py init
```

这会从 `.env.example` 复制创建 `.env` 文件，编辑并填入 API 密钥：

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

## Termux 部署指南

### 1. 准备工作

在 Termux 中安装必要的包：

```bash
# 更换清华源（可选）
termux-change-repo

# 更新包管理器
pkg update && pkg upgrade -y

# 安装基础工具
pkg install python git wget curl -y

```

### 2. 获取项目

```bash
# 克隆项目(如无法使用git克隆可直接下载压缩包到本地，解压使用)
git clone https://github.com/rice-awa/MCBE-AI-Agent
cd MCBE-AI-Agent

# 创建虚拟环境
python -m venv venv
source venv/bin/activate
```

### 3. 安装依赖

```bash
# 安装项目依赖
pip install -r requirements.txt
```

### 4. Termux 特定配置

由于 Termux 的特殊环境，可能需要调整一些配置：

```bash
# 1. 确保主机设置为 0.0.0.0 而不是 localhost
# 编辑 .env 文件
HOST=0.0.0.0
PORT=8080

# 2. 获取 Termux 的 IP 地址
ifconfig | grep inet

# 3. 确保 Termux 可以监听端口
# 可能需要允许 Termux 的网络访问权限
```

### 5. 启动服务

```bash
# 启动服务器
python cli.py serve

# 或使用守护进程方式（使用 tmux 或 screen）
pkg install tmux -y
tmux new -s mcbe_agent
source venv/bin/activate
python cli.py serve
# 按 Ctrl+B 然后按 D 分离会话
```

### 6. Minecraft 连接

在 MCBE 中使用 Termux 的 IP 地址或本地回环地址：

```
/wsserver localhost:8080
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

## Termux 常见问题

### 1. 端口无法访问

**解决方案**:
```bash
# 检查 Termux 是否具有必要权限
termux-setup-storage

# 使用 ngrok 绕过防火墙
ngrok http 8080
```

### 2. Python 包安装失败
**解决方案**:
```bash
# 更新 pip 和 setuptools
pip install --upgrade pip setuptools wheel

# 使用清华源加速
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

### 3. 内存不足

**解决方案**:
```bash
# 使用轻量级模型
DEFAULT_MODEL=deepseek-chat  # 而不是 deepseek-reasoner

# 减少工作线程
LLM_WORKER_COUNT=1

# 优化虚拟内存
pkg install tur-repo -y
pkg install zram -y
```

### 4. 后台运行

**使用 tmux**:
```bash
# 安装 tmux
pkg install tmux -y

# 创建新会话
tmux new -s mcbe_agent

# 在会话中启动
cd ~/MCBE-AI-Agent
source venv/bin/activate
python cli.py serve

# 分离会话: Ctrl+B, 然后按 D
# 重新连接: tmux attach -t mcbe_agent
```

**使用 nohup**:
```bash
nohup python cli.py serve > mcbe.log 2>&1 &
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
| `STREAM_SENTENCE_MODE` | true=流式按句输出，false=关闭流式并在完成后按句子分批输出 | `true` |
| `LOG_LEVEL` | 日志级别 | `INFO` |
| `ENABLE_WS_RAW_LOG` | WebSocket 原始日志开关 | `true` |
| `ENABLE_LLM_RAW_LOG` | LLM 原始日志开关 | `true` |

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
- 类型安全的工具定义 (独立 `tools.py` 模块)
- 动态系统提示词
- 流式响应支持 (按完整句子发送)
- 依赖注入
- MCWiki 搜索工具集成

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

## Termux 优化建议

### 1. 网络配置

```bash
# 使用 zerotier 创建虚拟局域网
pkg install zerotier-one -y
zerotier-one -d
zerotier-cli join <network_id>

# 或使用 tailscale
pkg install tailscale -y
tailscale up
```

### 2. 性能优化

```bash
# 安装性能监控工具
pkg install htop proot-distro -y

# 使用轻量级系统
proot-distro install ubuntu
proot-distro login ubuntu
```

### 3. 存储优化

```bash
# 清理缓存
pkg clean
pip cache purge

# 使用外部存储
termux-setup-storage
ln -s /storage/emulated/0/Download/mcbe_data ./data
```

### 4. 自动化脚本

创建 `termux_start.sh`:
```bash
#!/data/data/com.termux/files/usr/bin/bash

# 激活虚拟环境
source ~/MCBE-AI-Agent/venv/bin/activate

# 启动服务
cd ~/MCBE-AI-Agent
python cli.py serve

# 设置可执行权限
chmod +x termux_start.sh
```

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

在 `services/agent/tools.py` 中添加：

```python
async def your_tool(ctx: RunContext[AgentDependencies], param: str) -> str:
    """工具描述"""
    # 实现逻辑
    return "结果"
```

然后在 `services/agent/core.py` 中注册：

```python
from .tools import your_tool

chat_agent.tool(your_tool)
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

### 1. Termux 连接失败

```bash
# 检查端口监听
netstat -tulpn | grep 8080

# 检查防火墙
iptables -L

# 测试本地连接
curl http://localhost:8080/health
```

### 2. LLM 请求失败

测试提供商连接：
```bash
python cli.py test-provider deepseek
```

检查日志：
```bash
tail -f logs/MCBE-AI-Agent.log
```

### 3. 内存问题

```bash
# 查看内存使用
free -h

# 查看进程内存
ps aux | grep python

# 优化配置
export LLM_WORKER_COUNT=1
export DEFAULT_MODEL="deepseek-chat"
```

### 4. Python 依赖问题

```bash
# 重新安装依赖
pip uninstall -r requirements.txt -y
pip install --no-cache-dir -r requirements.txt

# 使用预编译包
pip install --prefer-binary -r requirements.txt
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

3. **Termux 特定安全**
   - 定期更新 Termux 包
   - 使用强密码保护设备
   - 仅在有需要时开放端口

4. **速率限制**
   - 在 `MessageBroker` 实现请求速率限制
   - 防止单用户滥用

## 更新日志

### v2.2.1 (2026-02-15)
- 🔧 **日志控制优化**: 新增 WebSocket 和 LLM 原始日志开关配置，支持按需启用
- ⚙️ **环境变量支持**: 添加 `ENABLE_WS_RAW_LOG` 和 `ENABLE_LLM_RAW_LOG` 环境变量

### v2.2.0 (2026-02-13)
- ✨ **WebSocket run_command 响应回传**: Agent 执行命令后自动回传 commandResponse，提升工具调用体验
- 🔧 **断线时队列处理优化**: 断线时自动完成队列中的 run_command futures，避免请求卡死
- ⚡ **流式响应处理优化**: 优化增量事件内容缓存和处理逻辑，提升流式输出稳定性
- 🔄 **响应处理逻辑重构**: 重构流式与非流式响应处理流程，移除手动工具链回退逻辑
- 📝 **配置外部化**: Minecraft 命令配置和消息模板迁移至配置文件，便于定制
- 🔧 **CLI 入口统一**: 重构应用入口至 cli.py，统一命令行工具
- 🧪 **测试完善**: 完善基于 agent.iter() 的流式输出模式测试

### v2.1.0 (2026-02-08)
- ✨ 新增 MCWiki 搜索工具，支持查询 Minecraft Wiki
- 🔧 Agent 工具定义重构，独立 `tools.py` 模块
- ⚡ 启动时预热 LLM 模型，提高首次响应速度
- 📝 流式输出优化，按完整句子发送
- 📡 支持通过 ScriptEvent 方式发送聊天消息
- 🔊 优化日志输出与响应记录

### v2.0.0 (2026-02-06)
- 🎉 初始版本发布
- 🚀 现代化异步架构重构
- 🤖 PydanticAI Agent 框架集成
- 🔌 多 LLM 提供商支持
- 🎮 完整的游戏内命令系统

## 未来计划

- [ ] 对话历史持久化
- [ ] Token 使用统计
- [ ] Web 管理界面
- [ ] 支持更多 Agent Tools
- [ ] 插件系统
- [ ] 多语言支持
- [ ] Docker 容器化
- [ ] Kubernetes 部署示例
- [ ] Termux 优化包

## 技术栈

- **Python 3.11+**
- **PydanticAI**: AI Agent 框架
- **Pydantic**: 数据验证
- **WebSockets**: 实时通信
- **httpx**: 异步 HTTP 客户端
- **PyJWT**: JWT 认证
- **structlog**: 结构化日志
- **Click**: CLI 工具
- **Termux**: Android 终端环境

## 许可证

[MIT](./LICENSE)

## 来源及参考

- 原项目: [rice-awa/MCBE_WebSocket_gpt](https://github.com/rice-awa/MCBE_WebSocket_gpt)
- PydanticAI: [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai)
- Termux: [termux/termux-app](https://github.com/termux/termux-app)

---

**版本**: 2.2.1
**最后更新**: 2026-02-15
**架构**: 现代化异步 + PydanticAI
**平台支持**: Windows, Linux, macOS, Termux (Android)