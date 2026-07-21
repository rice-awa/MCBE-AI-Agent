# mcbe-ws-sdk 完整接入宿主 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 MCBE-AI-Agent 运行时完整切换到 `mcbe-ws-sdk`（`McbeServerFacade` + Hook/Sink），线协议统一为 **mcbews v1**，删除宿主内嵌 `services/websocket/*` 与 `services/addon/*` 传输/桥实现。

**Architecture:** 新增宿主适配层 `services/gateway/`：用 SDK 拥有传输与分片；用 `HostConnectionHook` 承载鉴权/命令业务/入队；用 `HostResponseSink` + `BrokerResponseBridge` 把 `MessageBroker` 响应队列（`StreamChunk` / `SystemNotification` / `run_command` / `ai_response_sync`）映射到 SDK 出站；用宿主侧 `HostSessionStore` 保留 `PlayerSession` / 登录态 / AI 广播；用 `WsCommandRunner` 关联 `commandRequest`↔`commandResponse`。AI / 会话历史 / JWT / CLI 组装逻辑保留。

**Tech Stack:** Python 3.11+, `mcbe-ws-sdk` 0.1.0 (editable path dep), pydantic-settings, websockets (via SDK), pytest-asyncio, TypeScript Bedrock Script API (addon)

## Global Constraints

- **协议权威：SDK mcbews v1**（破坏性，无双读）。禁止再发 `mcbeai:*` / `MCBEAI|*` / `MCBEAI_TOOL`。
- 线常量必须与 `mcbe-ws-sdk/addon/scripts/bridge/constants.ts` / `McbewsV1Profile` bit-identical：
  - `mcbews:bridge_req` / `mcbews:text_resp`
  - `MCBEWS|BRIDGE` / `MCBEWS|UI_CHAT` / `MCBEWS_BRIDGE`
  - request body `v=2`
- SDK **永不** import 宿主；宿主只 import `mcbe_ws_sdk` 公共导出。
- 钩子 `on_player_message` / `on_ui_chat_reassembled` **禁止 await** LLM 或桥 RTT / WS command wait；一律 `asyncio.create_task`。
- 多人身份只用 `PlayerMessageEvent.sender` / 显式 `player_name`；禁止用 `ConnectionState.player_name` 做分支。
- `commandLine` 字节预算 461；下行 text_resp 走 `McbewsV1Delivery`，不要手写分片。
- 普通配置在 `config.json` / `config.example.json`；密钥只在 `.env`。
- Conventional Commits；每个任务结束跑对应 pytest；提交前不破坏 `python cli.py serve` 启动路径（可分阶段用 feature flag，最终删除旧栈）。
- `.gitignore` 当前有 `mcbe-ws-sdk/*`：集成期间把 SDK 当作 path 依赖；宿主代码不修改 SDK 源码（协议 bug 另开 SDK PR）。

---

## File Structure

| 文件 | 变更 | 职责 |
|------|------|------|
| `requirements.txt` | 修改 | 增加 `-e ./mcbe-ws-sdk`（或等价 path 依赖） |
| `services/gateway/__init__.py` | 创建 | 适配层公共导出 |
| `services/gateway/settings_map.py` | 创建 | 宿主 `Settings` → SDK `GatewaySettings` |
| `services/gateway/session_store.py` | 创建 | 每连接宿主会话：auth / PlayerSession / broadcast |
| `services/gateway/ws_command_runner.py` | 创建 | 宿主 `commandRequest` pending map |
| `services/gateway/broker_bridge.py` | 创建 | 消费 `MessageBroker` 响应队列 → SDK 出站 |
| `services/gateway/sink.py` | 创建 | `HostResponseSink`（OutboundText / SystemNotification） |
| `services/gateway/hook.py` | 创建 | `HostConnectionHook`（生命周期 + 命令路由） |
| `services/gateway/command_handlers.py` | 创建 | 从 `WebSocketServer.handle_*` 抽出的业务命令 |
| `services/gateway/server.py` | 创建 | 组装 Facade + Addon + Hook + Sink + Bridge 生命周期 |
| `cli.py` | 修改 | `Application` 改用 `services.gateway.server.HostGatewayServer` |
| `config/settings.py` | 修改 | 协议默认改 mcbews；delay 键 `ai_resp`→`text_resp`；去掉可随意改乱线协议的自由度（或只作文档镜像） |
| `config.example.json` | 修改 | 同步协议与流控键 |
| `services/agent/worker.py` | 修改 | Addon client 注入 SDK `AddonBridgeService`，不再 `get_addon_bridge_service()` |
| `models/agent.py` / `services/agent/tools.py` | 修改 | 如有 `MinecraftCommand` 依赖，改为 SDK 协议模型或保留宿主 tellraw 构造但不走旧 delivery |
| `MCBE-AI-Agent-addon/scripts/bridge/constants.ts` 等 | 修改 | 对齐 mcbews v1（或改为使用/打包 SDK addon） |
| `tools_mcbe_simulator.py` / `tools_mcbe_ws_recorder.py` / `tools_ws_tester.py` | 修改 | 硬编码协议改 mcbews |
| `tests/test_gateway_*.py` | 创建 | 适配层单测 |
| `tests/test_websocket_*.py` / `test_flow_control.py` / `test_addon_bridge_*.py` / `test_connection_manager.py` 等 | 修改或删除 | 迁到 SDK 公共 API / 新 gateway 测试 |
| `services/websocket/*` | 删除 | 旧传输栈 |
| `services/addon/*` | 删除 | 旧桥栈 |
| `CLAUDE.md` / `docs/addon-bridge-protocol.md` | 修改 | 文档改 mcbews + SDK 接入 |
| `docs/superpowers/specs/2026-07-21-mcbe-ws-sdk-host-integration-design.md` | 创建（可选摘要） | 设计决策落盘 |

### 目标运行时拓扑

```text
Minecraft /wsserver
    │
    ▼
McbeServerFacade (SDK)
    ├─ ConnectionManager + SDK ConnectionState
    ├─ AddonBridgeService (mcbews v1)
    ├─ HostConnectionHook ──► MessageBroker.submit_request / auth / 业务命令
    └─ HostResponseSink  ◄── SDK response_queue (OutboundText / SystemNotification)
                ▲
BrokerResponseBridge  ◄── MessageBroker per-connection response queue
    │                      (StreamChunk, host SystemNotification, run_command, ai_response_sync, game_message)
    ├─ format → enqueue_response(OutboundText|SDK SystemNotification)
    ├─ run_command → WsCommandRunner + McbeOutboundDelivery.send_raw_command
    └─ ai_response_sync → McbewsV1Delivery.send_response (mcbews:text_resp)
                ▲
AgentWorker (unchanged queue contract)
```

---

### Task 1: 引入 path 依赖并做烟雾导入

**Files:**
- Modify: `requirements.txt`
- Create: `tests/test_sdk_dependency.py`

**Interfaces:**
- Produces: 环境可 `import mcbe_ws_sdk`，版本可解析

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sdk_dependency.py
def test_mcbe_ws_sdk_importable():
    import mcbe_ws_sdk
    from mcbe_ws_sdk import McbeServerFacade, MCBEWS_V1, AddonBridgeService

    assert McbeServerFacade is not None
    assert MCBEWS_V1.bridge_request_message_id == "mcbews:bridge_req"
    assert MCBEWS_V1.response_message_id == "mcbews:text_resp"
    assert MCBEWS_V1.bridge_response_prefix == "MCBEWS|BRIDGE"
    assert MCBEWS_V1.ui_chat_prefix == "MCBEWS|UI_CHAT"
    assert MCBEWS_V1.bridge_sender == "MCBEWS_BRIDGE"
    assert MCBEWS_V1.request_version == 2
    assert AddonBridgeService is not None
    assert getattr(mcbe_ws_sdk, "__version__", None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sdk_dependency.py -v`  
Expected: FAIL with `ModuleNotFoundError: mcbe_ws_sdk`（若已 editable 安装则会 PASS，跳到 Step 4）

- [ ] **Step 3: Add path dependency**

在 `requirements.txt` 顶部核心依赖区增加：

```text
# Local gateway SDK (monorepo path; publishable independently)
-e ./mcbe-ws-sdk
```

安装：

```bash
pip install -e "./mcbe-ws-sdk"
pip install -r requirements.txt
```

说明：根 `.gitignore` 含 `mcbe-ws-sdk/*`，不要把 SDK 源码当宿主 diff 提交；只提交宿主侧依赖声明与适配代码。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sdk_dependency.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add requirements.txt tests/test_sdk_dependency.py
git commit -m "build: add editable mcbe-ws-sdk path dependency"
```

---

### Task 2: Settings → GatewaySettings 映射 + 协议默认切 mcbews

**Files:**
- Create: `services/gateway/__init__.py`
- Create: `services/gateway/settings_map.py`
- Modify: `config/settings.py`（`AddonProtocolConfig`、`FlowControlDelayConfig` 默认值）
- Modify: `config.example.json`
- Create: `tests/test_gateway_settings_map.py`

**Interfaces:**
- Produces:
  - `build_gateway_settings(settings: Settings) -> GatewaySettings`
  - `build_command_registry(settings: Settings) -> CommandRegistry`
  - 配置默认线协议 = mcbews v1 常量（仅文档/兼容镜像；运行时以 SDK profile 为准）

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gateway_settings_map.py
from config.settings import Settings
from services.gateway.settings_map import build_gateway_settings, build_command_registry


def test_build_gateway_settings_maps_host_and_port():
    s = Settings()
    # 若 Settings 需从 example 构造，改用 get_settings 的测试夹具；字段名以仓库为准
    s.host = "127.0.0.1"
    s.port = 18080
    gw = build_gateway_settings(s)
    assert gw.websocket.host == "127.0.0.1"
    assert gw.websocket.port == 18080
    assert gw.flow.command_line_byte_budget == 461
    assert "text_resp" in gw.flow.chunk_delays
    assert "ai_resp" not in gw.flow.chunk_delays
    assert gw.addon.profile.bridge_request_message_id == "mcbews:bridge_req"
    assert gw.addon.profile.response_message_id == "mcbews:text_resp"


def test_build_command_registry_registers_login_prefix():
    s = Settings()
    reg = build_command_registry(s)
    parsed = reg.parse("#登录 123456")
    assert parsed is not None
    assert parsed.type == "login"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gateway_settings_map.py -v`  
Expected: FAIL import or missing symbols

- [ ] **Step 3: Implement settings_map + config defaults**

`services/gateway/settings_map.py` 核心实现：

```python
from __future__ import annotations

from mcbe_ws_sdk import (
    AddonBridgeSettings,
    CommandRegistry,
    FlowControlSettings,
    GatewaySettings,
    MCBEWS_V1,
    McbewsV1Profile,
    WebsocketTransportConfig,
)
from config.settings import Settings


def build_gateway_settings(settings: Settings) -> GatewaySettings:
    delays = {
        "tellraw": float(settings.flow_control.chunk_delays.tellraw),
        "scriptevent": float(settings.flow_control.chunk_delays.scriptevent),
        "text_resp": float(
            getattr(settings.flow_control.chunk_delays, "text_resp", None)
            or getattr(settings.flow_control.chunk_delays, "ai_resp", 0.15)
        ),
    }
    flow = FlowControlSettings(
        command_line_byte_budget=settings.flow_control.command_line_byte_budget,
        max_chunk_content_length=settings.max_chunk_content_length,
        chunk_sentence_mode=settings.chunk_sentence_mode,
        chunk_delays=delays,
    )
    profile = McbewsV1Profile(
        response_chunk_delay=delays["text_resp"],
        response_prelude_delay=float(
            getattr(settings.flow_control.chunk_delays, "text_resp_prelude", None)
            or getattr(settings.flow_control.chunk_delays, "ai_resp_prelude", 0.5)
        ),
        # 其余字段保持 mcbews 默认；禁止从旧 mcbeai 配置覆盖命名空间
    )
    websocket = WebsocketTransportConfig(
        host=settings.host,
        port=settings.port,
        ping_interval=float(settings.websocket.ping_interval),
        ping_timeout=float(settings.websocket.ping_timeout),
        close_timeout=float(settings.websocket.close_timeout),
        max_size=settings.websocket.max_size,
        max_queue=settings.websocket.max_queue,
    )
    addon = AddonBridgeSettings(profile=profile)
    return GatewaySettings(flow=flow, addon=addon, websocket=websocket)


def build_command_registry(settings: Settings) -> CommandRegistry:
    registry = CommandRegistry()
    # 从 settings.minecraft.commands 注册主前缀与 aliases → type
    # 实现时对照现有 WebSocketServer / CommandRegistry 用法；
    # SDK CommandRegistry API: register(prefix, type=..., aliases=...)
    # 若 API 名不同，以 mcbe_ws_sdk.command.registry 源码为准。
    commands = settings.minecraft.commands  # 结构见 config.example.json
    for prefix, spec in commands.items():
        if isinstance(spec, str):
            registry.register(prefix, type=spec)
        else:
            registry.register(
                prefix,
                type=spec["type"],
                aliases=spec.get("aliases") or [],
            )
    return registry
```

`config/settings.py` 默认：

```python
class FlowControlDelayConfig(BaseModel):
    tellraw: float = 0.05
    scriptevent: float = 0.05
    text_resp: float = 0.15
    text_resp_prelude: float = 0.5
    # 可选：保留 ai_resp / ai_resp_prelude 别名 property 读兼容旧 config.json 一轮

class AddonProtocolConfig(BaseModel):
    """线协议镜像（运行时以 SDK McbewsV1Profile 为准；勿改成 mcbeai）。"""
    bridge_message_id: str = "mcbews:bridge_req"
    bridge_prefix: str = "MCBEWS|BRIDGE"
    ui_chat_prefix: str = "MCBEWS|UI_CHAT"
    bridge_tool_player_name: str = "MCBEWS_BRIDGE"
    ai_resp_message_id: str = "mcbews:text_resp"  # 字段名可后续改 text_resp_message_id
```

同步 `config.example.json` 的 `flow_control.chunk_delays` 与 `addon.protocol`。

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_gateway_settings_map.py tests/test_sdk_dependency.py -v`  
Expected: PASS（若 `Settings()` 构造需要 config.json，使用仓库既有 fixture 或 `EnvInterpolatedJsonConfigSettingsSource` 测试模式）

- [ ] **Step 5: Commit**

```bash
git add services/gateway/ config/settings.py config.example.json tests/test_gateway_settings_map.py
git commit -m "feat(gateway): map host Settings to SDK GatewaySettings (mcbews)"
```

---

### Task 3: HostSessionStore + WsCommandRunner

**Files:**
- Create: `services/gateway/session_store.py`
- Create: `services/gateway/ws_command_runner.py`
- Create: `tests/test_gateway_session_store.py`
- Create: `tests/test_gateway_ws_command_runner.py`

**Interfaces:**
- Produces:
  - `PlayerSession`（从旧 `services.websocket.connection` 迁入，字段不变）
  - `HostConnectionSession`：`authenticated`, `ai_broadcast_all`, `ai_broadcast_players`, `get_player_session`
  - `HostSessionStore`：`get/create/remove(connection_id)`
  - `WsCommandRunner`：与 SDK `examples/addon-server/server.py` 同构；`run(state, command) -> MinecraftCommandResponse`；`resolve(state, response) -> bool`；`close_connection(id)`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_gateway_session_store.py
from uuid import uuid4
from services.gateway.session_store import HostSessionStore


def test_player_session_isolated_per_player():
    store = HostSessionStore()
    cid = uuid4()
    host = store.create(cid, authenticated=True)
    a = host.get_player_session("Alice")
    b = host.get_player_session("Bob")
    a.current_provider = "deepseek"
    assert b.current_provider is None
    assert host.authenticated is True


# tests/test_gateway_ws_command_runner.py
import asyncio
from uuid import uuid4

import pytest

from mcbe_ws_sdk import FlowControlSettings, MinecraftCommandResponse
from mcbe_ws_sdk.gateway.connection import ConnectionState
from services.gateway.ws_command_runner import WsCommandRunner


@pytest.mark.asyncio
async def test_ws_command_runner_resolve_completes_future():
    flow = FlowControlSettings()
    runner = WsCommandRunner(flow, timeout=1.0)
    sent: list[str] = []

    async def send_payload(payload: str) -> None:
        sent.append(payload)

    state = ConnectionState(id=uuid4(), send_payload=send_payload)
    task = asyncio.create_task(runner.run(state, "say hello"))
    await asyncio.sleep(0)  # let run() register + send
    assert sent, "expected raw command payload"
    # 从 payload 解析 requestId 较脆；改为 runner 暴露 last_id 或 resolve 所有 pending：
    # 实现时：run() 在 before_send 登记后，测试通过 runner 内部 bucket 取唯一 key
    bucket = runner._pending[state.id]
    request_id = next(iter(bucket.keys()))
    ok = runner.resolve(
        state,
        MinecraftCommandResponse(request_id=request_id, body={}, status_code=0),  # 字段以 SDK 模型为准
    )
    assert ok is True
    resp = await task
    assert resp.request_id == request_id
```

注意：`MinecraftCommandResponse` 真实字段以 `mcbe_ws_sdk.protocol.minecraft` 为准；测试里按实际构造。

- [ ] **Step 2: Run tests — expect FAIL**

Run: `pytest tests/test_gateway_session_store.py tests/test_gateway_ws_command_runner.py -v`

- [ ] **Step 3: Implement**

`session_store.py`：把旧 `PlayerSession` dataclass 原样迁入（`context_enabled` / `current_provider` / `current_template` / `custom_variables`），外加：

```python
@dataclass
class HostConnectionSession:
    connection_id: UUID
    authenticated: bool = False
    ai_broadcast_all: bool = False
    ai_broadcast_players: set[str] = field(default_factory=set)
    _player_sessions: dict[str, PlayerSession] = field(default_factory=dict)

    def get_player_session(self, player_name: str | None) -> PlayerSession: ...
    def should_broadcast_ai_chat(self, player_name: str | None) -> bool: ...
```

`ws_command_runner.py`：几乎可复制 SDK example 的 `WsCommandRunner`，但 `run()` 给 Agent 用时需要把 `MinecraftCommandResponse` **格式化成 str**（保持 worker 现有 `run_command -> str` 契约）。提供两个方法：

```python
async def run_raw(self, state: ConnectionState, command: str) -> MinecraftCommandResponse: ...
async def run_as_text(self, state: ConnectionState, command: str) -> str:
    resp = await self.run_raw(state, command)
    return format_command_response(resp)  # 与旧 connection._run_command 结果字符串语义对齐
```

`format_command_response` 实现时对照旧路径：成功 body 序列化 / 失败 message。

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add services/gateway/session_store.py services/gateway/ws_command_runner.py tests/test_gateway_session_store.py tests/test_gateway_ws_command_runner.py
git commit -m "feat(gateway): add HostSessionStore and WsCommandRunner"
```

---

### Task 4: HostResponseSink + BrokerResponseBridge

**Files:**
- Create: `services/gateway/sink.py`
- Create: `services/gateway/broker_bridge.py`
- Create: `tests/test_gateway_broker_bridge.py`

**Interfaces:**
- Consumes: SDK `ConnectionState`, `FlowControlSettings`, `McbewsV1Profile`, `McbeOutboundDelivery`, `McbewsV1Delivery`, `enqueue_response`
- Consumes: 宿主 `models.messages.StreamChunk` / `SystemNotification`；dict `run_command` / `ai_response_sync` / `game_message`
- Produces:
  - `HostResponseSink(DefaultResponseSink)`：`on_outbound_text` / `on_system_notification` → delivery
  - `BrokerResponseBridge`：`start(connection_id, state)` / `stop(connection_id)`；后台 drain `broker` 响应队列

**关键映射规则（必须写死在实现与测试中）：**

| Broker 消息 | 出站动作 |
|-------------|----------|
| `StreamChunk` | 按旧 `connection._send_stream_chunk` 着色/前缀 → `enqueue_response(OutboundText(...))` 或 sink delivery |
| 宿主 `SystemNotification` | → SDK `SystemNotification` → enqueue |
| `{"type":"game_message","content":...}` | OutboundText green tellraw |
| `{"type":"run_command","command", "result_future"}` | `WsCommandRunner.run_as_text`；`result_future.set_result(str)`；断连失败文案与旧版一致 |
| `{"type":"ai_response_sync", player_name, role, text}` | `McbewsV1Delivery.send_response(...)` |

- [ ] **Step 1: Write failing unit tests with fake send_payload**

```python
# tests/test_gateway_broker_bridge.py
import asyncio
from uuid import uuid4

import pytest

from core.queue import MessageBroker
from models.messages import StreamChunk
from mcbe_ws_sdk import FlowControlSettings
from mcbe_ws_sdk.gateway.connection import ConnectionState
from services.gateway.broker_bridge import BrokerResponseBridge
from services.gateway.ws_command_runner import WsCommandRunner


@pytest.mark.asyncio
async def test_stream_chunk_becomes_outbound_payload():
    broker = MessageBroker(max_size=10)
    cid = uuid4()
    q = broker.register_connection(cid)
    sent: list[str] = []

    async def send_payload(p: str) -> None:
        sent.append(p)

    state = ConnectionState(id=cid, send_payload=send_payload)
    # SDK ConnectionState 是否自带 response_queue：以 SDK 为准；
    # Bridge 既可 enqueue 到 state.response_queue 再由 Facade sender 发，
    # 也可直接 McbeOutboundDelivery。推荐直接 delivery，避免双队列时序问题。
    flow = FlowControlSettings()
    runner = WsCommandRunner(flow)
    bridge = BrokerResponseBridge(broker, flow, runner, profile=None)
    await bridge.start(state)
    await broker.send_response(
        cid,
        StreamChunk(
            connection_id=cid,
            chunk_type="content",
            content="你好",
            sequence=1,
            player_name="Steve",
        ),
    )
    for _ in range(50):
        if sent:
            break
        await asyncio.sleep(0.02)
    await bridge.stop(cid)
    assert sent, "expected tellraw payloads"
    assert any("你好" in p for p in sent)
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement sink + bridge**

`sink.py`（对齐 SDK example `MinecraftSink`）：

```python
class HostResponseSink(DefaultResponseSink):
    def __init__(self, flow: FlowControlSettings, *, log_raw_payloads: bool = False) -> None:
        self._flow = flow
        self._log_raw_payloads = log_raw_payloads

    def _delivery(self, state: ConnectionState) -> McbeOutboundDelivery | None:
        if state.send_payload is None:
            return None
        return McbeOutboundDelivery(
            connection_id=state.id,
            send_payload=state.send_payload,
            settings=self._flow,
            log_raw_payloads=self._log_raw_payloads,
        )

    async def on_outbound_text(self, state, message: OutboundText) -> None:
        d = self._delivery(state)
        if d:
            await d.send_outbound_text(message)

    async def on_system_notification(self, state, message: SystemNotification) -> None:
        d = self._delivery(state)
        if d:
            await d.send_system_notification(message)
```

`broker_bridge.py` 要点：

```python
class BrokerResponseBridge:
    def __init__(self, broker, flow, ws_commands, *, profile: McbewsV1Profile = MCBEWS_V1, log_raw=False):
        ...

    async def start(self, state: ConnectionState) -> None:
        # 确保 broker.register_connection(state.id) 已在 hook.on_connected 调用
        self._tasks[state.id] = asyncio.create_task(self._loop(state), name=f"broker-bridge:{state.id}")

    async def _loop(self, state: ConnectionState) -> None:
        queue = self._broker.get_response_queue(state.id)  # 若无 getter，扩展 MessageBroker 或保存 register 返回值
        while state.id in self._tasks:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            await self._handle(state, item)

    async def _handle(self, state, item) -> None:
        if isinstance(item, StreamChunk):
            await self._stream_chunk(state, item)
        elif isinstance(item, HostSystemNotification):  # models.messages.SystemNotification
            await self._system(state, item)
        elif isinstance(item, dict) and item.get("type") == "run_command":
            await self._run_command(state, item)
        elif isinstance(item, dict) and item.get("type") == "ai_response_sync":
            await self._ai_sync(state, item)
        elif isinstance(item, dict) and item.get("type") == "game_message":
            await self._game_message(state, item["content"])
```

若 `MessageBroker` 未暴露 get queue：在 `core/queue.py` 增加 `get_response_queue(connection_id) -> Queue | None`（最小改动）。

`run_command` 路径：

```python
async def _run_command(self, state, item):
    future = item.get("result_future")
    try:
        text = await self._ws_commands.run_as_text(state, item["command"])
        if future and not future.done():
            future.set_result(text)
    except Exception as exc:
        if future and not future.done():
            future.set_result(f"命令执行失败: {exc}")
```

- [ ] **Step 4: Run tests — PASS**

- [ ] **Step 5: Commit**

```bash
git add services/gateway/sink.py services/gateway/broker_bridge.py core/queue.py tests/test_gateway_broker_bridge.py
git commit -m "feat(gateway): HostResponseSink and BrokerResponseBridge"
```

---

### Task 5: 迁出命令业务 → command_handlers + HostConnectionHook

**Files:**
- Create: `services/gateway/command_handlers.py`
- Create: `services/gateway/hook.py`
- Create: `tests/test_gateway_hook_auth_chat.py`
- Reference（只读迁移源，勿在终态保留 import）: `services/websocket/server.py`

**Interfaces:**
- Produces: `HostConnectionHook(NoOpHook)` 实现 6 钩子
- Produces: `CommandHandlers` 持有 `broker`, `settings`, `jwt`, `sessions`, `ws_commands`, `addon` 的业务方法（login/chat/context/…）

**钩子行为契约：**

1. `on_connected(state)`  
   - `sessions.create(state.id, authenticated=settings.dev_mode)`  
   - `broker.register_connection(state.id)`  
   - `bridge.start(state)`  
   - 欢迎语：`enqueue_response(SystemNotification|OutboundText)`（文案用 `settings.minecraft.welcome_*`）

2. `on_disconnected(state)`  
   - `bridge.stop` / `ws_commands.close_connection` / `addon.drop_connection`（若 SDK 有）  
   - `broker.unregister_connection` + 清理会话历史（沿用旧 `clear_connection_sessions`）  
   - `sessions.remove`  
   - `prompt_manager.clear_connection`

3. `on_player_message(state, event, parsed)`  
   - 过滤 `sender in {"外部","External"}`  
   - `create_task(self._dispatch(...))` **立即返回**  
   - `_dispatch`：login 免 auth；其它 `check_auth`；按 `parsed.type` 调 handlers；无 parsed 可忽略或当 help

4. `on_ui_chat_reassembled(state, player_name, message)`  
   - auth 检查 → `ai_response_sync` user 角色入 broker 队列 → `handle_chat`（同旧 `_handle_ui_chat_message`）  
   - 同样 `create_task`

5. `on_command_response(state, response)`  
   - `ws_commands.resolve(state, response)`

6. `on_error`  
   - 打日志；可选 system notification

- [ ] **Step 1: Write focused tests for login + chat enqueue**

```python
@pytest.mark.asyncio
async def test_login_success_marks_session_authenticated(monkeypatch):
    # 构造 hook + fake jwt + sessions + broker
    # 调 await hook._dispatch_login(...) 或模拟 PlayerMessageEvent(sender="Steve", message="#登录 密码")
    # assert sessions.get(cid).authenticated is True
    ...


@pytest.mark.asyncio
async def test_chat_submits_chat_request_without_blocking():
    # mock broker.submit_request
    # on_player_message with AGENT 聊天 你好
    # 等待 background task
    # assert submit_request called with ChatRequest(player_name="Steve", ...)
    ...
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: 迁移 `handle_*` 业务体**

原则：从 `server.py` **剪切** 业务方法到 `command_handlers.py`，替换：

| 旧 | 新 |
|----|----|
| `state.authenticated` | `host_session.authenticated` |
| `state.get_player_session` | `host_session.get_player_session` |
| `self._delivery(state).send_*` | `enqueue_response` / bridge 已处理的队列消息 |
| `state.pending_command_futures` | `WsCommandRunner` |
| `get_addon_bridge_service()` | 构造时注入的 `AddonBridgeService` |

`handle_chat` 仍：`ChatRequest(...)` → `broker.submit_request`；并 `broker.send_response({type: ai_response_sync, role: user, ...})`。

- [ ] **Step 4: Run unit tests — PASS**

- [ ] **Step 5: Commit**

```bash
git add services/gateway/hook.py services/gateway/command_handlers.py tests/test_gateway_hook_auth_chat.py
git commit -m "feat(gateway): HostConnectionHook and command handlers"
```

---

### Task 6: HostGatewayServer 生命周期 + cli 切换

**Files:**
- Create: `services/gateway/server.py`
- Modify: `cli.py`
- Create: `tests/test_gateway_server_lifecycle.py`（可用 mock facade）

**Interfaces:**
- Produces: `HostGatewayServer` 与旧 `WebSocketServer` 同形生命周期 API：`async start()` / `async stop()`

```python
class HostGatewayServer:
    def __init__(self, broker: MessageBroker, settings: Settings, jwt_handler: JWTHandler) -> None:
        self._broker = broker
        self._settings = settings
        self._jwt = jwt_handler
        self._gateway_settings = build_gateway_settings(settings)
        self._addon = AddonBridgeService(self._gateway_settings.addon)
        self._ws_commands = WsCommandRunner(self._gateway_settings.flow, timeout=settings.run_command_timeout)
        self._sessions = HostSessionStore()
        self._bridge = BrokerResponseBridge(
            broker, self._gateway_settings.flow, self._ws_commands,
            profile=self._gateway_settings.addon.profile,
            log_raw=settings.enable_ws_raw_log,
        )
        self._handlers = CommandHandlers(...)
        self._hook = HostConnectionHook(...)
        self._sink = HostResponseSink(self._gateway_settings.flow, log_raw_payloads=settings.enable_ws_raw_log)
        self._registry = build_command_registry(settings)
        self._facade = McbeServerFacade(
            settings=self._gateway_settings,
            hook=self._hook,
            sink=self._sink,
            addon=self._addon,
            registry=self._registry,
        )
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        # Facade.run_lifetime 阻塞；放后台 task
        self._task = asyncio.create_task(self._facade.run_lifetime(), name="mcbe-facade")

    async def stop(self) -> None:
        # 按 SDK 文档关闭：若 facade 提供 stop/close 用官方 API；
        # 否则 cancel task + 清理 sessions/bridge
        ...
```

`cli.py`：

```python
# from services.websocket.server import WebSocketServer  # 删除
from services.gateway.server import HostGatewayServer

# Application.__init__:
self.ws_server = HostGatewayServer(self.broker, self.settings, self.jwt_handler)

# Application.start: 删除 FlowControlMiddleware.configure(...)
# AgentWorker 需能拿到同一个 AddonBridgeService —— 见 Task 7
```

- [ ] **Step 1: Write lifecycle test with monkeypatched `McbeServerFacade.run_lifetime`**

```python
@pytest.mark.asyncio
async def test_host_gateway_start_stop(monkeypatch):
    started = asyncio.Event()
    async def fake_lifetime(self):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(McbeServerFacade, "run_lifetime", fake_lifetime)
    ...
    await server.start()
    await asyncio.wait_for(started.wait(), timeout=1)
    await server.stop()
```

- [ ] **Step 2–4: Implement, pass, commit**

```bash
git commit -m "feat(gateway): HostGatewayServer and switch cli serve entry"
```

---

### Task 7: AgentWorker 使用注入的 AddonBridgeService

**Files:**
- Modify: `services/agent/worker.py`
- Modify: `cli.py` / `HostGatewayServer`（暴露 `addon` 给 worker）
- Modify: `tests/test_agent_worker.py` / `test_agent_addon_tools.py` 相关 fixture

**Interfaces:**
- 删除 `from services.addon.service import get_addon_bridge_service`
- `AgentWorker(..., addon: AddonBridgeService | None = None)`
- `_create_addon_bridge_client`：

```python
def _create_addon_bridge_client(self, connection_id: UUID):
    if self._addon is None:
        raise RuntimeError("AddonBridgeService not configured")
    return self._addon.create_client(
        connection_id=connection_id,
        send_command=self._create_command_callback(connection_id),
    )
```

注意：SDK `create_client` 签名以 `mcbe_ws_sdk.addon.service` 为准（可能是 `create_client(connection_id, send_command)`）。

`Application` 创建 workers 时传入 `self.ws_server.addon`。

- [ ] **Step 1: 改 worker 测试 fixture 注入 fake addon**
- [ ] **Step 2: 实现**
- [ ] **Step 3: `pytest tests/test_agent_worker.py tests/test_agent_addon_tools.py -v`**
- [ ] **Step 4: Commit** `fix(agent): inject SDK AddonBridgeService into worker`

---

### Task 8: Addon 与模拟器协议升档（mcbews）

**Files:**
- Modify: `MCBE-AI-Agent-addon/scripts/bridge/constants.ts`（及所有引用旧常量的 TS）
- Modify: `MCBE-AI-Agent-addon/scripts/bridge/responseSync.ts`（`mcbeai:ai_resp` → `mcbews:text_resp`）
- Modify: 其它 `mcbeai:` 动态属性键：评估是否必须改；**线协议相关必须改**；世界存储 key `mcbeai:ui_state` 可保留以免丢档，但在计划中写明
- Modify: `tools_mcbe_simulator.py`, `tools_mcbe_ws_recorder.py`, `tools_ws_tester.py`
- 可选：直接文档声明「推荐使用 `mcbe-ws-sdk/addon` 构建产物」，宿主 addon 仅补 AI UI

**目标 constants（与 SDK 一致）：**

```ts
export const BRIDGE_RESPONSE_PREFIX = "MCBEWS|BRIDGE";
export const BRIDGE_UI_CHAT_PREFIX = "MCBEWS|UI_CHAT";
export const BRIDGE_REQUEST_MESSAGE_ID = "mcbews:bridge_req";
export const BRIDGE_SENDER = "MCBEWS_BRIDGE";
export const TEXT_RESP_MESSAGE_ID = "mcbews:text_resp";
```

删除旧名 `BRIDGE_MESSAGE_ID = mcbeai:bridge_request` / `TOOL_PLAYER_NAME = MCBEAI_TOOL` / `AI_RESP_MESSAGE_ID = mcbeai:ai_resp`。

- [ ] **Step 1: `rg -n 'mcbeai|MCBEAI' MCBE-AI-Agent-addon tools_*.py` 列出全部命中**
- [ ] **Step 2: 逐处替换；addon `npm test` / `npm run build`**
- [ ] **Step 3: 更新 `docs/addon-bridge-protocol.md` 为 mcbews**
- [ ] **Step 4: Commit** `feat(addon): migrate bridge protocol to mcbews v1`

**破坏性说明（写入 CHANGELOG/README）：** 旧世界若仍装 mcbeai addon，桥会超时；必须更新行为包并开 **实验 → Beta APIs**。

---

### Task 9: 删除旧栈并修复/重写测试

**Files:**
- Delete: `services/websocket/` 整包
- Delete: `services/addon/` 整包
- Modify/Delete: `tests/test_flow_control.py`, `test_connection_manager.py`, `test_websocket_*.py`, `test_websocket_delivery.py`, `test_addon_bridge_*.py`, `test_ai_broadcast.py` 等
- Modify: 任何残留 `from services.websocket` / `from services.addon` import

**策略：**

1. `rg -n "services\.websocket|services\.addon" --glob '!mcbe-ws-sdk/**'` 必须为零（测试与生产）。
2. 流控正确性以 **SDK 自带测试** 为准，宿主不再复制 `FlowControlMiddleware` 单测；宿主只测 bridge 着色与 run_command 字符串契约。
3. 业务命令测试改为对 `CommandHandlers` / `HostConnectionHook` 调入。

- [ ] **Step 1: 删除旧包**
- [ ] **Step 2: 修 import 至全绿**

```bash
pytest -q
```

Expected: 全 PASS（允许 skip 标记 live 测试）

- [ ] **Step 3: Commit** `refactor: remove in-tree websocket/addon stacks; host uses mcbe-ws-sdk`

---

### Task 10: 文档与验收清单

**Files:**
- Modify: `CLAUDE.md`（架构图、Key Files、协议、流控路径）
- Modify: `README.md`（若有连接说明）
- Create: `docs/superpowers/specs/2026-07-21-mcbe-ws-sdk-host-integration-design.md`（本计划的决策摘要）
- Modify: `docs/addon-bridge-protocol.md`

**验收（手动）：**

```bash
pip install -e "./mcbe-ws-sdk"
pip install -r requirements.txt
python cli.py init   # 如需要
python cli.py serve
# 游戏内：
# /wsserver <ip>:8080
# #登录 <password>
# AGENT 聊天 你好
# （有 addon）触发 get_player_snapshot 类工具 / UI 聊天
```

检查：
- [ ] 欢迎语出现
- [ ] 登录生效
- [ ] 流式 tellraw 正常
- [ ] `mcbews:text_resp` 同步到 addon UI（若启用）
- [ ] 桥能力不超时
- [ ] 两人同连接会话不串
- [ ] `pytest -q` 绿
- [ ] 仓库无 `mcbeai` 运行时引用（允许 docs 历史/CHANGELOG 提及）

- [ ] **Step: Commit** `docs: document mcbews host integration via mcbe-ws-sdk`

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| Facade 收包循环被 await 阻塞 → 桥/命令永久超时 | 钩子强制 `create_task`；code review 清单项 |
| 双队列（broker vs SDK response_queue）丢消息 | Bridge 优先 **直接 delivery**；SDK queue 仅给 hook 主动 enqueue 的欢迎/help |
| `run_command` 结果字符串格式变化导致 tool 解析失败 | 对照旧实现写兼容格式化 + 单测 |
| 用户本地 `config.json` 仍含 mcbeai | 启动时若检测到旧 key，log **warning** 并忽略命名空间覆盖（强制 MCBEWS_V1） |
| `.gitignore` 忽略 SDK 导致 CI 无包 | CI checkout 子模块/或从 PyPI 装；文档写清 `pip install -e ./mcbe-ws-sdk` |
| 宿主 addon 与 SDK addon 功能差 | Task 8 优先改常量；能力缺口时 cherry-pick SDK `capabilities/` |
| `McbeServerFacade` 单次 `run_lifetime` | stop 后若需重启进程级重建 Application |

## 非目标（YAGNI）

- 不把 MessageBroker / PydanticAI / JWT 搬进 SDK
- 不做 mcbeai/mcbews 双栈兼容
- 不在本计划修改 SDK 公共 API（除非阻塞 bug）
- 不重做 Hermes 适配器

## 建议实施顺序（依赖）

```text
Task1 依赖
  → Task2 配置
    → Task3 session + WsCommandRunner
      → Task4 sink + broker bridge
        → Task5 hook + handlers
          → Task6 server + cli
            → Task7 worker 注入
              → Task8 addon/tools 协议
                → Task9 删旧栈 + 全量测试
                  → Task10 文档验收
```

---

## Self-Review

1. **Spec coverage:** 用户选 A（完整切换）+ 协议以 SDK 为准 → Tasks 1–10 覆盖依赖、映射、会话、出站、钩子、cli、worker、addon、删旧、文档。  
2. **Placeholder scan:** 少量 API 以 SDK 源码字段为准的注释已标明「以 SDK 模型为准」，实现时对照 `mcbe_ws_sdk` 源文件，不保留 TBD 任务。  
3. **Type consistency:** `HostConnectionSession` / `WsCommandRunner` / `BrokerResponseBridge` / `HostGatewayServer` 命名在全任务一致；出站 text 响应统一 `mcbews:text_resp` / `McbewsV1Delivery`。  
4. **Scope:** 单计划可交付；若 addon UI 与 SDK addon 分叉过大，可将 Task 8 拆 PR，但不拆协议权威。

---

## Execution Handoff

计划已写好。执行时建议：

1. **Subagent-Driven（推荐）** — 每任务一个子代理 + 任务间 review  
2. **Inline Execution** — 本会话按 `executing-plans` 批量推进并设检查点
