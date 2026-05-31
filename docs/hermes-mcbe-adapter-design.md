# Hermes × MCBE 平台适配器 — 设计方案

> **版本**: v1.0  
> **日期**: 2026-05-30  
> **目标**: 为 Hermes Agent 编写 Minecraft Bedrock Edition 平台适配器 Plugin

---

## 一、概述

### 1.1 项目目标

用户可以通过 Minecraft Bedrock Edition 聊天框与 Hermes Agent 对话，就像使用 Telegram / Discord 一样。Hermes Gateway 通过 MCBE 平台适配器连接 Minecraft 服务器，接收玩家消息并回复。

### 1.2 关键约束

| 约束 | 值 | 来源 |
|------|-----|------|
| MCBE `commandLine` 字节上限 | **461 B**（实测） | `flow_control.py:_COMMAND_LINE_BYTE_BUDGET` |
| tellraw 文本字符预算 | ~400 字符（中文约 400，英文约 350） | 461 - 包装开销(60) |
| 分片间延迟 | tellraw: 0.05s, scriptevent: 0.05s | 需避免命令洪泛触发看门狗 |
| 连接模式 | MC 客户端主动连接 WS 服务器 | `/connect <host>:<port>` |

---

## 二、架构方案对比

### 2.1 方案 A：纯 Hermes 适配器（✅ 推荐）

```
MC 客户端 ──WebSocket──▶  Hermes MCBE 适配器 (WS Server)
                                │
                                ├─ subscribe PlayerMessage
                                ├─ parse message → handle_message(MessageEvent)
                                └─ send() → chunk_tellraw → WS send
                                      │
                                  Hermes Gateway
                                      │
                                  Hermes AIAgent
                                      │ (tools: terminal, web, file, + MC 命令)
```

**优势**：
- 遵循 Hermes 适配器标准模式（与 IRC / LINE 同构）
- 无需额外进程，Hermes Gateway 一体化运行
- 直接复用 `FlowControlMiddleware` 和 `MinecraftCommand` 作为工具函数
- 单一代码仓库，零外部依赖（除 `websockets`）

**代价**：
- 需要从 mcbe-ai-agent 提取协议层代码（`flow_control.py`, `models/minecraft.py`）
- Hermes Agent 的 MC 命令执行能力需要单独注册工具

### 2.2 方案 B：桥接适配器（❌ 不推荐）

```
MC 客户端 ──WS──▶ mcbe-ai-agent ──HTTP──▶ Hermes MCBE Adapter ──▶ Hermes Gateway
```

**问题**：
- 需要同时运行两个服务，增加运维复杂度
- mcbe-ai-agent 的响应流（`StreamChunk`）绑定到进程内 `asyncio.Queue`，跨进程消费需要重构 Worker
- `run_command` 依赖 `asyncio.Future` 同进程传递，跨进程需实现 RPC 层
- 额外增加 HTTP/WS 通信层，引入网络延迟

**结论**：方案 A 更简洁、更符合 Hermes 设计范式。以下详细设计基于方案 A。

---

## 三、详细设计

### 3.1 适配器模式：WebSocket Server + 长连接

MCBE 的协议特性：**MC 客户端主动连接** WS 服务器（`/connect <host>:<port>`）。这与 LINE Webhook / WeCom Callback 同属"回调/服务器"模式。

```python
class MCBEAdapter(BasePlatformAdapter):
    async def connect(self) -> bool:
        # 启动 websockets 服务器监听端口
        self._ws_server = await websockets.serve(
            self._handle_mc_connection,
            host=self._listen_host,
            port=self._listen_port,
            ping_interval=30,
            ping_timeout=15,
        )
        self._mark_connected()
        return True
```

### 3.2 连接生命周期

```
1. MC 客户端执行 /connect <适配器地址>:<端口>
2. 适配器 accept 连接 → 创建 MCConnection 对象
3. 发送 {"Result": "true"}（握手确认）
4. 发送 subscribe PlayerMessage（订阅事件）
5. 发送欢迎消息（tellraw）
6. 进入消息循环：
   - 接收 PlayerMessage → 解析 → handle_message(event)
   - Hermes 回复 → send() → chunk_tellraw → WS send
7. MC 断开 → 清理连接
```

### 3.3 chat_id 映射策略

```python
# chat_id = "{connection_uuid}:{player_name}"
# 单连接多玩家通过 player_name 隔离
chat_id = f"{conn_id.hex[:8]}:{sanitize_player_name(sender)}"

# 构建会话源
source = self.build_source(
    chat_id=chat_id,
    chat_name=f"MC:{player_name}",
    chat_type="dm",
    user_id=player_name,
    user_name=player_name,
)
```

### 3.4 消息流

```
上行（MC → Hermes）:
  PlayerMessage 事件 → 提取 sender + message
  → 构建 MessageEvent(text=message, source=...)
  → await self.handle_message(event)

下行（Hermes → MC）:
  send(chat_id, content)
  → 解析 chat_id 获取 conn_id + player_name
  → FlowControlMiddleware.chunk_tellraw(content)
  → 逐片 await ws.send(payload) [片间 0.05s 延迟]
```

### 3.5 复用 mcbe-ai-agent 的模块

从 mcbe-ai-agent 项目中直接复用的核心模块：

| 模块 | 文件 | 适配器用法 |
|------|------|-----------|
| **Minecraft 命令模型** | `models/minecraft.py` | `MinecraftCommand.create_tellraw()`, `create_raw()` |
| **流控中间件** | `services/websocket/flow_control.py` | `FlowControlMiddleware.chunk_tellraw()` 分片 |
| **PlayerMessage 解析** | `models/minecraft.py` | `PlayerMessageEvent.from_event_body()` |
| **握手/订阅消息** | `models/minecraft.py` | `MinecraftSubscribe.player_message()` |

> **策略**：将这些模块作为 `adapter.py` 的内联工具代码，或作为独立 `_mc_protocol.py` 子模块。避免依赖整个 mcbe-ai-agent 包。

---

## 四、文件结构

```
~/.hermes/plugins/mcbe/
├── __init__.py          # from .adapter import register
├── adapter.py           # MCBEAdapter + register() + 所有钩子
├── _mc_protocol.py      # MinecraftCommand / FlowControlMiddleware / PlayerMessageEvent
├── _mc_connection.py    # MCConnection 管理（WS 连接包装、认证状态）
└── plugin.yaml          # 元数据 + 环境变量声明
```

---

## 五、环境变量设计

```yaml
# plugin.yaml
name: mcbe
label: Minecraft BE
kind: platform
version: 1.0.0
description: Minecraft Bedrock Edition WebSocket 平台适配器
author: Rice-Awa

requires_env:
  - name: MCBE_LISTEN_HOST
    description: "WS 服务器监听地址（默认 0.0.0.0）"
    prompt: "监听地址"
    password: false
  - name: MCBE_LISTEN_PORT
    description: "WS 服务器监听端口（默认 8080）"
    prompt: "监听端口"
    password: false
  - name: MCBE_AUTH_PASSWORD
    description: "MC 玩家认证密码（可选，为空则跳过认证）"
    prompt: "认证密码"
    password: true

optional_env:
  - name: MCBE_HOME_CHANNEL
    description: "Cron 投递默认目标（格式: conn_id:player）"
    prompt: "Home channel"
    password: false
  - name: MCBE_ALLOWED_PLAYERS
    description: "逗号分隔的允许玩家名列表"
    prompt: "Allowed players"
    password: false
  - name: MCBE_WELCOME_MESSAGE
    description: "自定义欢迎消息（可选）"
    prompt: "Welcome message"
    password: false
  - name: MCBE_MAX_CHUNK_LENGTH
    description: "tellraw 单分片最大字符数（默认 400）"
    prompt: "Max chunk length"
    password: false
  - name: MCBE_CHUNK_SENTENCE_MODE
    description: "是否按句子语义分片（默认 true）"
    prompt: "Sentence mode (true/false)"
    password: false
```

---

## 六、核心代码框架

### 6.1 adapter.py 主结构

```python
"""Hermes MCBE Platform Adapter — WebSocket Server 模式"""

import asyncio
import json
import os
from uuid import UUID, uuid4

import websockets
from websockets.server import WebSocketServerProtocol, serve

from gateway.platforms.base import (
    BasePlatformAdapter, SendResult, MessageEvent, MessageType,
)
from gateway.config import Platform, PlatformConfig
from gateway.status import acquire_scoped_lock, release_scoped_lock

# 内联复用 mcbe-ai-agent 协议模块
from ._mc_protocol import (
    MinecraftCommand,
    MinecraftSubscribe,
    PlayerMessageEvent,
    FlowControlMiddleware,
)
from ._mc_connection import MCConnection


class MCBEAdapter(BasePlatformAdapter):
    """Minecraft Bedrock Edition WebSocket 平台适配器。"""

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform("mcbe"))
        extra = config.extra or {}
        self._listen_host = extra.get("host") or os.getenv("MCBE_LISTEN_HOST", "0.0.0.0")
        self._listen_port = int(extra.get("port") or os.getenv("MCBE_LISTEN_PORT", "8080"))
        self._auth_password = extra.get("auth_password") or os.getenv("MCBE_AUTH_PASSWORD", "")
        self._allowed_players = self._parse_allowed_players()
        self._welcome_msg = os.getenv("MCBE_WELCOME_MESSAGE", "§bHermes AI 已连接！")

        # 运行时状态
        self._ws_server = None
        self._connections: dict[UUID, MCConnection] = {}
        self._player_to_conn: dict[str, UUID] = {}  # player_name → conn_id
        self._running = False

        # 配置流控中间件
        max_chunk = int(os.getenv("MCBE_MAX_CHUNK_LENGTH", "400"))
        sentence_mode = os.getenv("MCBE_CHUNK_SENTENCE_MODE", "true").lower() == "true"
        FlowControlMiddleware.configure(
            max_content_length=max_chunk,
            sentence_mode=sentence_mode,
        )

    # ─── 生命周期 ────────────────────────────────────────

    async def connect(self) -> bool:
        if not acquire_scoped_lock("mcbe", f"{self._listen_host}:{self._listen_port}"):
            return False
        self._running = True
        self._ws_server = await serve(
            self._handle_mc_connection,
            self._listen_host,
            self._listen_port,
            ping_interval=30,
            ping_timeout=15,
        )
        self._mark_connected()
        return True

    async def disconnect(self) -> None:
        self._running = False
        if self._ws_server:
            self._ws_server.close()
            await self._ws_server.wait_closed()
        release_scoped_lock("mcbe", f"{self._listen_host}:{self._listen_port}")
        self._mark_disconnected()

    # ─── 消息发送 ────────────────────────────────────────

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        """通过 tellraw 命令发送消息到 Minecraft。"""
        conn = self._resolve_connection(chat_id)
        if not conn or not conn.ws:
            return SendResult(success=False, error="connection not found")

        try:
            payloads = FlowControlMiddleware.chunk_tellraw(content)
            chunk_delay = FlowControlMiddleware.chunk_delay_for("tellraw")
            for i, payload in enumerate(payloads):
                await conn.ws.send(payload)
                if len(payloads) > 1 and i < len(payloads) - 1:
                    await asyncio.sleep(chunk_delay)
            return SendResult(success=True, message_id=str(uuid4()))
        except Exception as e:
            return SendResult(success=False, error=str(e), retryable=True)

    async def get_chat_info(self, chat_id):
        conn = self._resolve_connection(chat_id)
        player_name = chat_id.split(":", 1)[-1] if ":" in chat_id else chat_id
        return {
            "name": player_name,
            "type": "dm",
            "connected": conn is not None and conn.ws is not None,
        }

    # ─── 连接处理 ────────────────────────────────────────

    async def _handle_mc_connection(self, ws: WebSocketServerProtocol):
        """处理单个 MC 客户端连接（一个连接可承载多个玩家）。"""
        conn = MCConnection(ws=ws)
        self._connections[conn.id] = conn

        try:
            # 握手确认
            await ws.send(json.dumps({"Result": "true"}))

            # 订阅 PlayerMessage 事件
            subscribe = MinecraftSubscribe.player_message()
            await ws.send(subscribe.model_dump_json(exclude_none=True))

            # 欢迎消息
            welcome_payloads = FlowControlMiddleware.chunk_tellraw(self._welcome_msg, color="§b")
            for p in welcome_payloads:
                await ws.send(p)

            # 消息循环
            async for raw in ws:
                await self._dispatch_mc_message(conn, raw)

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._connections.pop(conn.id, None)
            # 清理 player → conn 映射
            to_remove = [p for p, cid in self._player_to_conn.items() if cid == conn.id]
            for p in to_remove:
                self._player_to_conn.pop(p, None)

    async def _dispatch_mc_message(self, conn: MCConnection, raw: str):
        """解析并分发 MC WebSocket 消息。"""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        header = data.get("header", {})
        purpose = header.get("messagePurpose")

        if purpose == "commandResponse":
            return  # 适配器不处理 commandResponse

        # 尝试解析 PlayerMessage 事件
        if header.get("eventName") == "PlayerMessage":
            event = PlayerMessageEvent.from_event_body(data.get("body", {}))
            if not event or not event.sender:
                return

            # 玩家白名单检查
            if self._allowed_players and event.sender not in self._allowed_players:
                return

            # 更新 player → conn 映射
            self._player_to_conn[event.sender] = conn.id

            # 构建 Hermes MessageEvent
            chat_id = f"{conn.id.hex[:8]}:{self._sanitize_name(event.sender)}"
            source = self.build_source(
                chat_id=chat_id,
                chat_name=f"MC:{event.sender}",
                chat_type="dm",
                user_id=event.sender,
                user_name=event.sender,
            )
            msg_event = MessageEvent(
                text=event.message,
                message_type=MessageType.TEXT,
                source=source,
                message_id=str(uuid4()),
            )
            await self.handle_message(msg_event)

    # ─── 辅助方法 ────────────────────────────────────────

    def _resolve_connection(self, chat_id: str) -> MCConnection | None:
        """从 chat_id 解析连接对象。"""
        parts = chat_id.split(":", 1)
        if len(parts) == 2:
            conn_id_hex, player_name = parts
            # 尝试从 player_to_conn 查找
            if player_name in self._player_to_conn:
                return self._connections.get(self._player_to_conn[player_name])
            # 尝试从 connections 匹配
            for conn in self._connections.values():
                if conn.id.hex[:8] == conn_id_hex:
                    return conn
        return None

    @staticmethod
    def _sanitize_name(name: str) -> str:
        """清理玩家名中可能导致 chat_id 解析歧义的字符。"""
        return name.replace(":", "_")

    def _parse_allowed_players(self) -> set[str] | None:
        raw = os.getenv("MCBE_ALLOWED_PLAYERS", "").strip()
        if not raw:
            return None
        return {p.strip() for p in raw.split(",") if p.strip()}


# ─── Plugin 入口 ────────────────────────────────────────

def check_requirements() -> bool:
    try:
        import websockets
        return True
    except ImportError:
        return False


def validate_config(config) -> bool:
    extra = getattr(config, "extra", {}) or {}
    host = os.getenv("MCBE_LISTEN_HOST") or extra.get("host")
    port = os.getenv("MCBE_LISTEN_PORT") or extra.get("port")
    return bool(host and port)


def _env_enablement() -> dict | None:
    host = os.getenv("MCBE_LISTEN_HOST", "").strip()
    port = os.getenv("MCBE_LISTEN_PORT", "").strip()
    if not (host and port):
        return None
    seed = {"host": host, "port": int(port)}
    home = os.getenv("MCBE_HOME_CHANNEL", "").strip()
    if home:
        seed["home_channel"] = {"chat_id": home, "name": "Minecraft"}
    return seed


async def _standalone_send(pconfig, chat_id, message, *, thread_id=None,
                           media_files=None, force_document=False):
    """Cron 独立发送：临时建立 WS 连接，发送 tellraw，断开。"""
    import websockets as ws_module
    extra = pconfig.extra or {}
    host = extra.get("host") or os.getenv("MCBE_LISTEN_HOST", "0.0.0.0")
    port = int(extra.get("port") or os.getenv("MCBE_LISTEN_PORT", "8080"))
    uri = f"ws://{host}:{port}"

    try:
        async with ws_module.connect(uri) as wsock:
            # 握手
            await wsock.send(json.dumps({"Result": "true"}))
            # 发送 tellraw
            payloads = FlowControlMiddleware.chunk_tellraw(f"§6[Hermes] {message}")
            for p in payloads:
                await wsock.send(p)
                await asyncio.sleep(0.05)
        return {"success": True, "message_id": str(uuid4())}
    except Exception as e:
        return {"error": str(e)}


def register(ctx):
    ctx.register_platform(
        name="mcbe",
        label="Minecraft BE",
        adapter_factory=lambda cfg: MCBEAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        required_env=["MCBE_LISTEN_HOST", "MCBE_LISTEN_PORT"],
        install_hint="pip install websockets",
        env_enablement_fn=_env_enablement,
        cron_deliver_env_var="MCBE_HOME_CHANNEL",
        standalone_sender_fn=_standalone_send,
        allowed_users_env="MCBE_ALLOWED_PLAYERS",
        allow_all_env="MCBE_ALLOW_ALL_PLAYERS",
        max_message_length=400,
        emoji="⛏️",
        platform_hint=(
            "You are chatting via Minecraft Bedrock Edition chat. "
            "Messages are short tellraw broadcasts visible to all players. "
            "Keep responses under 300 characters where possible. "
            "You have access to the game world — mention what you see."
        ),
    )
```

### 6.2 _mc_connection.py

```python
"""MCBE 连接状态管理"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from websockets.server import WebSocketServerProtocol


@dataclass
class MCConnection:
    """单个 MC 客户端 WebSocket 连接。"""
    ws: WebSocketServerProtocol
    id: UUID = field(default_factory=uuid4)
    authenticated: bool = False
    connected_at: datetime = field(default_factory=datetime.now)
```

### 6.3 _mc_protocol.py

直接从 mcbe-ai-agent 项目复用的协议模块：

- `MinecraftHeader` + `MinecraftCommand` + `MinecraftSubscribe` + `PlayerMessageEvent`（来自 `models/minecraft.py`）
- `FlowControlMiddleware`（来自 `services/websocket/flow_control.py`）

只做最小适配：移除依赖 `config/settings.py` 的全局配置（改用类变量注入），移除 `config/logging` 依赖（改用 `logging` 标准库），移除 `pydantic` 依赖（改为 dataclass + 手动 JSON 构造）。

---

## 七、分片与流控策略

### 7.1 为什么必须分片

MCBE 服务端对 `commandRequest` 的 `commandLine` 字段有严格的字节上限：

| 限制 | 值 | 后果 |
|------|-----|------|
| 安全上限 | 461 B | ≤461: 正常执行 |
| 首次失败 | 462 B | ≥462: 服务端拒绝，WS 无错误返回，消息静默丢弃 |

### 7.2 分片策略（复用 FlowControlMiddleware）

```
输入文本 "很长的回复..."
  ↓
_split_text()
  ├── 句子模式: [。！？.!?\n] 分隔 + 合并短句
  ├── 字符约束: ≤ MAX_CHUNK_LENGTH (默认 400)
  └── 字节约束: ≤ byte_budget (461 - 60 = 401 B for tellraw)
  ↓
[chunk1, chunk2, chunk3, ...]
  ↓
MinecraftCommand.create_tellraw(chunk_n) → JSON 序列化
  ↓
逐片 ws.send(payload) [片间 0.05s 延迟]
```

### 7.3 Hermes 自己的 max_message_length

Hermes 适配器注册的 `max_message_length=400` 表示：如果 Hermes 内核产生超过 400 字符的回复，它会自行按字符拆分。然后适配器的 `send()` 中再经 `FlowControlMiddleware` 按字节预算二次分片。

**双层保护**：
1. Hermes 内核 → 按字符数粗分（`max_message_length=400`）
2. 适配器 send() → 按字节数细分（`FlowControlMiddleware.chunk_tellraw`）

---

## 八、多玩家支持

### 8.1 架构

MCBE 的一个 WS 连接可能承载多个玩家（MC 服务器的 `wsserver` 模式）。适配器需要正确处理多玩家场景：

```
MC Server (1 个 WS 连接)
  ├── Player "Steve"  ──▶  Hermes 会话 A
  ├── Player "Alex"   ──▶  Hermes 会话 B
  └── Player "Notch"  ──▶  Hermes 会话 C
```

### 8.2 chat_id 映射

```python
# chat_id = "{conn_hex}:{player_name}"
# 例："a1b2c3d4:Steve", "a1b2c3d4:Alex"
```

每个 chat_id 在 Hermes 中独立维护会话历史和上下文。

### 8.3 路由

- **上行路由**：从 `PlayerMessage.sender` 提取 `player_name`，构造 chat_id
- **下行路由**：从 `chat_id` 解析 `conn_id_hex` + `player_name`，找到对应 WS 连接
- **连接断开**：清理该连接下所有 player → conn 映射

---

## 九、Cron 独立发送

### 9.1 问题

`hermes cron run` 和 `hermes gateway` 在不同进程中运行。cron 进程没有活跃的 MCBEAdapter 实例。

### 9.2 方案：`standalone_sender_fn`

```python
async def _standalone_send(pconfig, chat_id, message, **kw):
    # 1. 建立临时 WS 连接（作为独立客户端）
    async with websockets.connect(uri) as wsock:
        # 2. 握手确认
        await wsock.send(json.dumps({"Result": "true"}))
        # 3. 发送 tellraw
        payloads = FlowControlMiddleware.chunk_tellraw(f"§6[Hermes] {message}")
        for p in payloads:
            await wsock.send(p)
            await asyncio.sleep(0.05)
    return {"success": True}
```

### 9.3 注意事项

- **WS 连接是短命的**：发完就断，避免占用 MC 服务端资源
- **需要 MC 服务端开放 `/connect` 到 Hermes 的端口**：如果 MC 服务端和 Hermes 不在同一主机/网络，需要额外配置
- **安全性**：`/connect` 无内建认证，建议配合防火墙限制来源 IP

---

## 十、安全性

### 10.1 连接安全

| 层面 | 措施 |
|------|------|
| MC 连接来源 | 可选 `MCBE_ALLOWED_PLAYERS` 白名单 |
| 玩家认证 | 可选 `MCBE_AUTH_PASSWORD`（适配器仅作 pass-through，不实现完整 JWT） |
| 多 Profile 冲突 | `acquire_scoped_lock("mcbe", host:port)` 防止同端口复用 |
| WS 数据大小 | 默认 10MB 上限（`max_size`） |

### 10.2 Hermes 命令安全

MC 聊天框没有 `/approve` 交互机制，因此 Hermes 的危险命令审批在 MC 场景下无法工作。建议：

1. 在 `platform_hint` 中告知 Agent 不要尝试执行危险命令
2. 或将 MCBE 平台标记为 `pii_safe=False`，启用额外安全策略

---

## 十一、实现步骤

### Phase 1：协议模块提取（1 天）

- [ ] 从 mcbe-ai-agent 提取 `models/minecraft.py` → `_mc_protocol.py`
- [ ] 从 mcbe-ai-agent 提取 `services/websocket/flow_control.py` → 合并到 `_mc_protocol.py`
- [ ] 移除对 `pydantic` / `config.settings` / `config.logging` 的依赖
- [ ] 改为 dataclass + 手动 JSON 序列化，或保留最小 pydantic 依赖
- [ ] 单元测试：`MinecraftCommand.create_tellraw`、`FlowControlMiddleware.chunk_tellraw` 字节安全

### Phase 2：适配器骨架（1 天）

- [ ] 创建 `~/.hermes/plugins/mcbe/` 目录结构
- [ ] 实现 `MCBEAdapter` 类（`connect`, `disconnect`, `send`, `get_chat_info`）
- [ ] 实现 WS 连接生命周期（握手 → 订阅 → 消息循环）
- [ ] 实现 PlayerMessage 解析 + `handle_message(event)` 转发
- [ ] 插件注册入口（`register`, `check_requirements`, `validate_config`, `_env_enablement`）

### Phase 3：分片与流控集成（0.5 天）

- [ ] 在 `send()` 中集成 `FlowControlMiddleware.chunk_tellraw`
- [ ] 实现片间延迟（`asyncio.sleep(0.05)`）
- [ ] 测试中英文混合长文本的分片正确性

### Phase 4：多玩家与生命周期（0.5 天）

- [ ] 实现 `chat_id` 映射（`conn_id:player_name`）
- [ ] 玩家白名单过滤
- [ ] 连接断开时清理 player → conn 映射

### Phase 5：Cron 独立发送（0.5 天）

- [ ] 实现 `_standalone_send` 函数
- [ ] 验证 cron job `deliver=mcbe` 端到端可用

### Phase 6：测试与文档（1 天）

- [ ] 端到端测试：MC 客户端连接 → 发消息 → Hermes 回复
- [ ] 多玩家并发测试
- [ ] 长文本分片测试
- [ ] 连接断开重连测试
- [ ] 编写 README + 配置说明

---

## 十二、风险与注意事项

### 12.1 高风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| **pydantic 依赖冲突** | Hermes 环境可能未安装 pydantic | 改为 dataclass + 手动 JSON |
| **461 字节限制遗漏** | 消息静默丢弃，用户无感知 | 复用已充分测试的 FlowControlMiddleware + `_assert_byte_safe` |
| **MC disconnects 不触发重连** | 用户体验差 | 依赖 Hermes Gateway 自动重连 + `_set_fatal_error` |

### 12.2 中风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| **多连接端口冲突** | 多 profile 不能同时运行 | `acquire_scoped_lock` 防止 |
| **命令洪泛触发看门狗** | MC 客户端被踢出 | 0.05s 片间延迟已充分验证 |
| **standalone_send MC 不在线** | cron 消息丢失 | 记录 error，不重试（合理降级） |

### 12.3 低风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| **websockets 未安装** | 适配器不可用 | `check_requirements()` 检测 + `install_hint` |
| **认证密码错误** | 非预期玩家使用 | 白名单 + 可选密码 |

---

## 十三、参考实现

| 参考 | 路径 | 可借鉴的点 |
|------|------|-----------|
| Hermes IRC 适配器 | `plugins/platforms/irc/adapter.py` | TCP 长连接 + 接收循环 + standalone_send |
| Hermes LINE 适配器 | `plugins/platforms/line/adapter.py` | Webhook/回调模式 + slow-LLM UX |
| Hermes WeCom Callback | `plugins/platforms/wecom_callback.py` | HTTP Server + 即时 ACK + 异步回复 |
| mcbe-ai-agent FlowControl | `services/websocket/flow_control.py` | 461B 字节预算 + 分片算法 |
| mcbe-ai-agent MinecraftCommand | `models/minecraft.py` | tellraw/scriptevent 命令构造 |

---

## 十四、总结

**推荐采用纯 Hermes 适配器方案（方案 A）**，以 WebSocket Server 模式运行，MC 客户端通过 `/connect` 直接连接到 Hermes Gateway。

**核心优势**：
- ✅ 零额外进程，Hermes 一体化
- ✅ 遵循 Hermes Plugin 标准，安装即用
- ✅ 复用 mcbe-ai-agent 的成熟协议层代码
- ✅ 预计开发周期 3.5 个工作日

**下一步**：确认方案无异议后，开始 Phase 1（协议模块提取）。
