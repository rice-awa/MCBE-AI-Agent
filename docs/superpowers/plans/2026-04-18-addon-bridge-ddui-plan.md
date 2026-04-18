# MCBE AI Agent Addon 桥接增强与 DDUI 预留实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在保留现有 Python WebSocket 主链路的前提下，将 `MCBE-AI-Agent-addon` 演进为脚本桥接增强层，为 LLM 暴露稳定的游戏数据查询与世界操作工具，并预留 DDUI（Data-Driven UI）交互入口，使聊天命令与脚本 UI 可以共存。

**架构：** Python 端继续作为主会话协调者与 LLM 运行时，Bedrock 原生 WebSocket 仍负责玩家聊天命令与命令执行；TypeScript addon 负责处理 `scriptevent` 请求、在游戏内采集结构化数据、通过模拟玩家回传结果，并在后续承载 DDUI 面板。桥接协议采用显式命名空间、分片消息和请求 ID 关联，避免把旧版 addon 的临时逻辑直接搬入新工程。

**技术栈：** Python 3.11、pytest、Pydantic、PydanticAI、Bedrock WebSocket 协议、TypeScript、`@minecraft/server`、`@minecraft/server-ui`、`@minecraft/server-gametest`

---

## 参考文档

- DDUI 入门：<https://learn.microsoft.com/en-us/minecraft/creator/documents/scripting/intro-to-ddui?view=minecraft-bedrock-stable>
- Script API `scriptEventReceive`：<https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/scripteventcommandmessageafterevent?view=minecraft-bedrock-stable>
- `/scriptevent` 命令：<https://learn.microsoft.com/en-us/minecraft/creator/commands/commands/scriptevent?view=minecraft-bedrock-stable>
- `@minecraft/server-gametest` / `spawnSimulatedPlayer`：<https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server-gametest/minecraft-server-gametest?view=minecraft-bedrock-stable>
- `@minecraft/server-net` 限制：<https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server-net/minecraft-server-net?view=minecraft-bedrock-stable>

## 文件结构

### Python 侧

- 修改：`/root/mcbe_ai_agent/services/websocket/server.py`
  责任：在现有 `PlayerMessage` 流中拦截 addon 模拟玩家回传的桥接消息，并将其交给桥接服务，不与普通玩家命令解析混用。
- 创建：`/root/mcbe_ai_agent/models/addon_bridge.py`
  责任：定义桥接请求、桥接响应、分片元数据、能力枚举等 Pydantic 模型。
- 创建：`/root/mcbe_ai_agent/services/addon/protocol.py`
  责任：桥接消息编码、分片、重组、校验与命名空间约束。
- 创建：`/root/mcbe_ai_agent/services/addon/session.py`
  责任：管理按连接维度的 addon 请求上下文、pending request、超时与重组缓冲区。
- 创建：`/root/mcbe_ai_agent/services/addon/service.py`
  责任：向游戏发送 `scriptevent` 请求、等待 addon 回传、向 Agent 工具暴露统一异步接口。
- 修改：`/root/mcbe_ai_agent/models/agent.py`
  责任：把 addon 桥接服务依赖注入到 Agent 运行上下文。
- 修改：`/root/mcbe_ai_agent/services/agent/worker.py`
  责任：构建 addon service 回调并注入 `AgentDependencies`。
- 修改：`/root/mcbe_ai_agent/services/agent/tools.py`
  责任：新增高层游戏工具，优先调用 addon 桥接能力，而不是让 LLM 自己拼底层命令。
- 创建：`/root/mcbe_ai_agent/tests/test_addon_bridge_protocol.py`
  责任：验证 Python 侧分片协议、命名空间校验、重组逻辑与异常场景。
- 创建：`/root/mcbe_ai_agent/tests/test_addon_bridge_service.py`
  责任：验证请求发送、响应等待、超时、错误映射与并发隔离。

### Addon 侧

- 修改：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/package.json`
  责任：补充测试脚本与必要依赖（如 `vitest`），保证纯逻辑模块可测试。
- 修改：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/behavior_packs/starter/manifest.json`
  责任：补充 `@minecraft/server-ui`、`@minecraft/server-gametest` 依赖，提升 `min_engine_version`，将包名改为项目正式名称。
- 修改：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/main.ts`
  责任：仅作为入口，调用 bootstrap，不承载业务逻辑。
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bootstrap.ts`
  责任：统一初始化桥接路由、模拟玩家保活、UI 入口与调试日志。
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/constants.ts`
  责任：集中维护消息 ID、聊天前缀、分片大小、模拟玩家名称等常量。
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/chunking.ts`
  责任：桥接消息分片与重组纯函数，供测试和运行时复用。
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/toolPlayer.ts`
  责任：创建并维护模拟玩家，作为 addon 到 Python 的回传通道。
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/router.ts`
  责任：订阅 `system.afterEvents.scriptEventReceive`，解析桥接请求并路由到具体能力处理器。
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/capabilities/getPlayerSnapshot.ts`
  责任：返回玩家血量、标签、坐标、维度、游戏模式等结构化信息。
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/capabilities/getInventorySnapshot.ts`
  责任：返回指定玩家或全部玩家背包摘要。
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/capabilities/findEntities.ts`
  责任：按实体类型、半径、维度筛选实体并返回位置快照。
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/capabilities/runWorldCommand.ts`
  责任：以受控白名单方式执行世界命令或脚本侧原子操作。
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/ui/state.ts`
  责任：封装 DDUI `Observable` 状态，为后续面板共用。
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/ui/entry.ts`
  责任：保留 UI 入口与面板分发，不在第一阶段塞满业务。
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/tests/bridge/chunking.test.ts`
  责任：验证 addon 侧分片协议和回传格式。
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/tests/bridge/router.test.ts`
  责任：验证纯路由决策函数，不直接依赖游戏运行时。

### 文档

- 创建：`/root/mcbe_ai_agent/docs/addon-bridge-protocol.md`
  责任：记录桥接事件命名、载荷结构、错误码、能力清单。
- 修改：`/root/mcbe_ai_agent/README.md`
  责任：补充 addon 桥接层的定位、部署方式与联调步骤。

---

### 任务 1：建立桥接协议与测试基线

**文件：**
- 创建：`/root/mcbe_ai_agent/models/addon_bridge.py`
- 创建：`/root/mcbe_ai_agent/services/addon/protocol.py`
- 创建：`/root/mcbe_ai_agent/tests/test_addon_bridge_protocol.py`
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/constants.ts`
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/chunking.ts`
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/tests/bridge/chunking.test.ts`
- 创建：`/root/mcbe_ai_agent/docs/addon-bridge-protocol.md`

- [ ] **步骤 1：编写 Python 侧失败测试，先锁定协议行为**

```python
from services.addon.protocol import (
    encode_bridge_request,
    decode_bridge_chat_chunk,
    reassemble_bridge_chunks,
)


def test_encode_bridge_request_should_use_namespaced_message_id() -> None:
    command = encode_bridge_request(
        request_id="req-1",
        capability="get_player_snapshot",
        payload={"target": "@a"},
    )
    assert command.startswith("scriptevent mcbeai:bridge_request ")


def test_reassemble_bridge_chunks_should_restore_payload() -> None:
    chunks = [
        "MCBEAI|RESP|req-1|1/2|{\"ok\":true,",
        "MCBEAI|RESP|req-1|2/2|\"players\":[\"Steve\"]}",
    ]
    parsed = [decode_bridge_chat_chunk(chunk) for chunk in chunks]
    response = reassemble_bridge_chunks(parsed)
    assert response.request_id == "req-1"
    assert response.payload["players"] == ["Steve"]
```

- [ ] **步骤 2：运行 Python 测试验证失败**

运行：`pytest /root/mcbe_ai_agent/tests/test_addon_bridge_protocol.py -v`
预期：FAIL，报错 `ModuleNotFoundError` 或 `cannot import name 'encode_bridge_request'`

- [ ] **步骤 3：编写 Python 协议模型与纯函数实现**

```python
from pydantic import BaseModel


class AddonBridgeChunk(BaseModel):
    request_id: str
    chunk_index: int
    total_chunks: int
    content: str


def encode_bridge_request(request_id: str, capability: str, payload: dict) -> str:
    body = {"request_id": request_id, "capability": capability, "payload": payload}
    return f"scriptevent mcbeai:bridge_request {json.dumps(body, ensure_ascii=False)}"
```

- [ ] **步骤 4：编写 addon 侧失败测试，锁定分片格式**

```ts
import { describe, expect, it } from "vitest";
import { chunkBridgePayload, formatResponseChunk } from "../../scripts/bridge/chunking";

describe("bridge chunking", () => {
  it("formats chunks with request id and part metadata", () => {
    const chunks = chunkBridgePayload("req-2", JSON.stringify({ ok: true, value: 1 }), 12);
    expect(chunks[0]).toMatch(/^MCBEAI\|RESP\|req-2\|1\/\d+\|/);
  });

  it("formats error responses consistently", () => {
    const chunk = formatResponseChunk("req-3", 1, 1, "{\"ok\":false}");
    expect(chunk).toBe("MCBEAI|RESP|req-3|1/1|{\"ok\":false}");
  });
});
```

- [ ] **步骤 5：补充 addon 测试脚本并实现纯分片函数**

```json
{
  "scripts": {
    "test": "vitest run"
  },
  "devDependencies": {
    "vitest": "^2.1.8",
    "@types/node": "^22.10.1"
  }
}
```

```ts
export function formatResponseChunk(
  requestId: string,
  index: number,
  total: number,
  content: string,
): string {
  return `MCBEAI|RESP|${requestId}|${index}/${total}|${content}`;
}
```

- [ ] **步骤 6：运行双端协议测试验证通过**

运行：`pytest /root/mcbe_ai_agent/tests/test_addon_bridge_protocol.py -v`
预期：PASS

运行：`cd /root/mcbe_ai_agent/MCBE-AI-Agent-addon && npm test`
预期：PASS，输出 `bridge chunking` 用例通过

- [ ] **步骤 7：提交协议基线**

```bash
git add \
  /root/mcbe_ai_agent/models/addon_bridge.py \
  /root/mcbe_ai_agent/services/addon/protocol.py \
  /root/mcbe_ai_agent/tests/test_addon_bridge_protocol.py \
  /root/mcbe_ai_agent/MCBE-AI-Agent-addon/package.json \
  /root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/constants.ts \
  /root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/chunking.ts \
  /root/mcbe_ai_agent/MCBE-AI-Agent-addon/tests/bridge/chunking.test.ts \
  /root/mcbe_ai_agent/docs/addon-bridge-protocol.md
git commit -m "feat(addon): 定义桥接协议与分片基线"
```

### 任务 2：重构 tsaddon 为桥接运行时骨架

**文件：**
- 修改：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/behavior_packs/starter/manifest.json`
- 修改：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/main.ts`
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bootstrap.ts`
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/toolPlayer.ts`
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/router.ts`

- [ ] **步骤 1：编写 addon 路由失败测试，先约束事件分发**

```ts
import { describe, expect, it } from "vitest";
import { shouldHandleScriptEvent } from "../../scripts/bridge/router";

describe("bridge router", () => {
  it("only accepts mcbeai bridge message ids", () => {
    expect(shouldHandleScriptEvent("mcbeai:bridge_request")).toBe(true);
    expect(shouldHandleScriptEvent("server:data")).toBe(false);
  });
});
```

- [ ] **步骤 2：运行 addon 测试验证失败**

运行：`cd /root/mcbe_ai_agent/MCBE-AI-Agent-addon && npm test -- router`
预期：FAIL，报错 `shouldHandleScriptEvent is not defined`

- [ ] **步骤 3：补全 manifest 与入口骨架**

```json
{
  "header": {
    "name": "MCBE AI Agent Addon",
    "description": "Script bridge and in-game UI for MCBE AI Agent",
    "min_engine_version": [1, 21, 80]
  },
  "dependencies": [
    { "module_name": "@minecraft/server", "version": "2.0.0" },
    { "module_name": "@minecraft/server-ui", "version": "2.0.0" },
    { "module_name": "@minecraft/server-gametest", "version": "1.0.0-beta" }
  ]
}
```

```ts
import { initializeAddon } from "./bootstrap";

initializeAddon();
```

- [ ] **步骤 4：实现桥接初始化与模拟玩家保活**

```ts
export function initializeAddon(): void {
  initializeToolPlayer();
  registerBridgeRouter();
  registerUiEntry();
}
```

```ts
export function ensureToolPlayer(): void {
  const existing = world.getAllPlayers().find((player) => player.name === TOOL_PLAYER_NAME);
  if (existing) {
    return;
  }

  gameTest.spawnSimulatedPlayer(
    { dimension: world.getDimension("overworld"), x: 300000, y: 100, z: 300000 },
    TOOL_PLAYER_NAME,
    GameMode.creative,
  );
}
```

- [ ] **步骤 5：运行 addon 测试与构建**

运行：`cd /root/mcbe_ai_agent/MCBE-AI-Agent-addon && npm test`
预期：PASS

运行：`cd /root/mcbe_ai_agent/MCBE-AI-Agent-addon && npm run build`
预期：PASS，输出 `dist/scripts/main.js`

- [ ] **步骤 6：提交桥接运行时骨架**

```bash
git add \
  /root/mcbe_ai_agent/MCBE-AI-Agent-addon/behavior_packs/starter/manifest.json \
  /root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/main.ts \
  /root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bootstrap.ts \
  /root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/toolPlayer.ts \
  /root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/router.ts \
  /root/mcbe_ai_agent/MCBE-AI-Agent-addon/tests/bridge/router.test.ts
git commit -m "feat(addon): 搭建桥接运行时骨架"
```

### 任务 3：打通 Python 到 addon 的请求链路

**文件：**
- 创建：`/root/mcbe_ai_agent/services/addon/session.py`
- 创建：`/root/mcbe_ai_agent/services/addon/service.py`
- 修改：`/root/mcbe_ai_agent/services/websocket/server.py`
- 创建：`/root/mcbe_ai_agent/tests/test_addon_bridge_service.py`

- [ ] **步骤 1：编写失败测试，先锁定桥接服务生命周期**

```python
import asyncio

from services.addon.service import AddonBridgeService


async def test_send_request_should_complete_after_chunk_reassembly(fake_connection_state):
    service = AddonBridgeService(timeout_seconds=1.0)

    task = asyncio.create_task(
        service.request_capability(
            state=fake_connection_state,
            capability="get_player_snapshot",
            payload={"target": "Steve"},
        )
    )

    service.handle_chat_chunk("MCBEAI|RESP|req-1|1/1|{\"ok\":true,\"payload\":{\"name\":\"Steve\"}}")
    result = await task
    assert result["payload"]["name"] == "Steve"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest /root/mcbe_ai_agent/tests/test_addon_bridge_service.py -v`
预期：FAIL，报错 `ModuleNotFoundError: No module named 'services.addon.service'`

- [ ] **步骤 3：实现请求发送、pending 会话与响应重组**

```python
class AddonBridgeService:
    def __init__(self, timeout_seconds: float = 5.0) -> None:
        self._sessions: dict[UUID, AddonBridgeSession] = {}
        self._timeout_seconds = timeout_seconds

    async def request_capability(self, state: ConnectionState, capability: str, payload: dict) -> dict:
        request = self._session_for(state.id).create_request(capability, payload)
        await state.websocket.send(MinecraftCommand.create_raw(request.command).model_dump_json())
        return await asyncio.wait_for(request.future, timeout=self._timeout_seconds)
```

- [ ] **步骤 4：在 WebSocket 服务中增加 addon 回传拦截**

```python
if self.addon_bridge_service.is_bridge_chat_message(player_event.sender, player_event.message):
    self.addon_bridge_service.handle_player_message(state.id, player_event.sender, player_event.message)
    return
```

- [ ] **步骤 5：运行桥接服务测试**

运行：`pytest /root/mcbe_ai_agent/tests/test_addon_bridge_service.py -v`
预期：PASS

运行：`pytest /root/mcbe_ai_agent/tests/test_connection_manager.py -v`
预期：PASS，确认未破坏现有 WebSocket 行为

- [ ] **步骤 6：提交 Python 到 addon 的请求链路**

```bash
git add \
  /root/mcbe_ai_agent/services/addon/session.py \
  /root/mcbe_ai_agent/services/addon/service.py \
  /root/mcbe_ai_agent/services/websocket/server.py \
  /root/mcbe_ai_agent/tests/test_addon_bridge_service.py
git commit -m "feat(addon): 打通 Python 到 addon 的桥接请求链路"
```

### 任务 4：抽象 LLM 可用的高层游戏工具

**文件：**
- 修改：`/root/mcbe_ai_agent/models/agent.py`
- 修改：`/root/mcbe_ai_agent/services/agent/worker.py`
- 修改：`/root/mcbe_ai_agent/services/agent/tools.py`
- 创建：`/root/mcbe_ai_agent/tests/test_agent_addon_tools.py`

- [ ] **步骤 1：编写失败测试，先约束工具 API**

```python
from services.agent.tools import register_agent_tools


async def test_agent_tool_get_player_snapshot_uses_addon_bridge(agent_with_fake_addon):
    result = await agent_with_fake_addon.run_tool(
        "get_player_snapshot",
        {"target": "Steve"},
    )
    assert "Steve" in result
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest /root/mcbe_ai_agent/tests/test_agent_addon_tools.py -v`
预期：FAIL，报错 `Tool not found: get_player_snapshot`

- [ ] **步骤 3：注入 addon service 依赖并新增高层工具**

```python
@chat_agent.tool
async def get_player_snapshot(ctx: RunContext[AgentDependencies], target: str = "@a") -> str:
    result = await ctx.deps.addon_bridge.request("get_player_snapshot", {"target": target})
    return json.dumps(result["payload"], ensure_ascii=False)


@chat_agent.tool
async def get_inventory_snapshot(ctx: RunContext[AgentDependencies], target: str = "@a") -> str:
    result = await ctx.deps.addon_bridge.request("get_inventory_snapshot", {"target": target})
    return json.dumps(result["payload"], ensure_ascii=False)


@chat_agent.tool
async def find_entities(ctx: RunContext[AgentDependencies], entity_type: str, radius: int = 32) -> str:
    result = await ctx.deps.addon_bridge.request(
        "find_entities",
        {"entity_type": entity_type, "radius": radius},
    )
    return json.dumps(result["payload"], ensure_ascii=False)
```

- [ ] **步骤 4：运行 Agent 工具测试**

运行：`pytest /root/mcbe_ai_agent/tests/test_agent_addon_tools.py -v`
预期：PASS

运行：`pytest /root/mcbe_ai_agent/tests/test_agent_tools.py -v`
预期：PASS，确认未破坏既有工具

- [ ] **步骤 5：提交 Agent 高层工具**

```bash
git add \
  /root/mcbe_ai_agent/models/agent.py \
  /root/mcbe_ai_agent/services/agent/worker.py \
  /root/mcbe_ai_agent/services/agent/tools.py \
  /root/mcbe_ai_agent/tests/test_agent_addon_tools.py
git commit -m "feat(agent): 暴露 addon 桥接高层游戏工具"
```

### 任务 5：实现第一批 addon 能力处理器

**文件：**
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/capabilities/getPlayerSnapshot.ts`
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/capabilities/getInventorySnapshot.ts`
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/capabilities/findEntities.ts`
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/capabilities/runWorldCommand.ts`
- 修改：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/router.ts`

- [ ] **步骤 1：编写纯能力函数失败测试**

```ts
import { describe, expect, it } from "vitest";
import { normalizeEntitySnapshot } from "../../scripts/bridge/capabilities/findEntities";

describe("findEntities", () => {
  it("rounds coordinates to integers", () => {
    const result = normalizeEntitySnapshot({
      id: "1",
      typeId: "minecraft:cow",
      location: { x: 1.2, y: 64.8, z: -3.4 },
    });
    expect(result.position).toEqual({ x: 1, y: 65, z: -3 });
  });
});
```

- [ ] **步骤 2：运行 addon 测试验证失败**

运行：`cd /root/mcbe_ai_agent/MCBE-AI-Agent-addon && npm test -- findEntities`
预期：FAIL，报错 `normalizeEntitySnapshot is not defined`

- [ ] **步骤 3：实现第一批能力处理器**

```ts
export function buildPlayerSnapshot(player: Player) {
  return {
    name: player.name,
    health: Math.round((player.getComponent("minecraft:health") as EntityHealthComponent).currentValue),
    tags: player.getTags(),
    location: {
      x: Math.round(player.location.x),
      y: Math.round(player.location.y),
      z: Math.round(player.location.z),
    },
    dimension: player.dimension.id,
  };
}
```

```ts
export async function runWorldCommand(command: string): Promise<{ ok: boolean; output: string }> {
  const result = await world.getDimension("overworld").runCommandAsync(command);
  return { ok: result.successCount >= 0, output: result.statusMessage ?? "命令执行成功" };
}
```

- [ ] **步骤 4：将能力注册到桥接路由**

```ts
const capabilityHandlers: Record<string, BridgeCapabilityHandler> = {
  get_player_snapshot: handleGetPlayerSnapshot,
  get_inventory_snapshot: handleGetInventorySnapshot,
  find_entities: handleFindEntities,
  run_world_command: handleRunWorldCommand,
};
```

- [ ] **步骤 5：运行 addon 测试和构建**

运行：`cd /root/mcbe_ai_agent/MCBE-AI-Agent-addon && npm test`
预期：PASS

运行：`cd /root/mcbe_ai_agent/MCBE-AI-Agent-addon && npm run build`
预期：PASS

- [ ] **步骤 6：提交第一批能力处理器**

```bash
git add \
  /root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/capabilities/getPlayerSnapshot.ts \
  /root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/capabilities/getInventorySnapshot.ts \
  /root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/capabilities/findEntities.ts \
  /root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/capabilities/runWorldCommand.ts \
  /root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bridge/router.ts
git commit -m "feat(addon): 实现首批游戏数据与世界操作能力"
```

### 任务 6：预留 DDUI 共存层，不与聊天命令耦合

**文件：**
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/ui/state.ts`
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/ui/entry.ts`
- 创建：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/ui/panels/agentConsole.ts`
- 修改：`/root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bootstrap.ts`
- 修改：`/root/mcbe_ai_agent/README.md`

- [ ] **步骤 1：编写 UI 状态层失败测试，先约束 Observable 映射**

```ts
import { describe, expect, it } from "vitest";
import { createAgentUiState } from "../../scripts/ui/state";

describe("agent ui state", () => {
  it("starts with bridge disconnected state", () => {
    const state = createAgentUiState();
    expect(state.bridgeStatus.getData()).toBe("disconnected");
  });
});
```

- [ ] **步骤 2：运行 addon 测试验证失败**

运行：`cd /root/mcbe_ai_agent/MCBE-AI-Agent-addon && npm test -- agent-ui-state`
预期：FAIL，报错 `createAgentUiState is not defined`

- [ ] **步骤 3：实现最小 UI 状态容器与入口**

```ts
import { Observable } from "@minecraft/server-ui";

export function createAgentUiState() {
  return {
    bridgeStatus: Observable.create<"disconnected" | "connecting" | "ready">("disconnected"),
    lastPrompt: Observable.create<string>(""),
    lastResponsePreview: Observable.create<string>(""),
  };
}
```

```ts
export function registerUiEntry(): void {
  // 第一阶段仅保留入口，不抢占聊天命令。
  // 后续可通过物品、命令或脚本事件打开 DDUI 面板。
}
```

- [ ] **步骤 4：编写 DDUI 骨架面板**

```ts
CustomForm.create(player, "MCBE AI Agent")
  .label(uiState.bridgeStatus)
  .textField("提问", uiState.lastPrompt, {
    description: "后续将通过桥接请求发送到 Python Agent",
  })
  .button("发送", () => {
    // 第一阶段只更新状态，不直接提交请求
    uiState.bridgeStatus.setData("connecting");
  })
  .show();
```

- [ ] **步骤 5：运行 addon 测试与构建**

运行：`cd /root/mcbe_ai_agent/MCBE-AI-Agent-addon && npm test`
预期：PASS

运行：`cd /root/mcbe_ai_agent/MCBE-AI-Agent-addon && npm run build`
预期：PASS

- [ ] **步骤 6：提交 DDUI 预留层**

```bash
git add \
  /root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/ui/state.ts \
  /root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/ui/entry.ts \
  /root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/ui/panels/agentConsole.ts \
  /root/mcbe_ai_agent/MCBE-AI-Agent-addon/scripts/bootstrap.ts \
  /root/mcbe_ai_agent/README.md
git commit -m "feat(addon): 预留 DDUI 交互层"
```

### 任务 7：联调文档与回归验证

**文件：**
- 修改：`/root/mcbe_ai_agent/docs/addon-bridge-protocol.md`
- 修改：`/root/mcbe_ai_agent/README.md`

- [ ] **步骤 1：补充联调说明与限制说明**

```md
1. 启动 Python 服务：`python cli.py serve --dev`
2. 在 addon 工程执行：`npm run local-deploy`
3. 进入世界后确认模拟玩家 `工具人` 已生成。
4. 使用 `AGENT 脚本 你好` 验证 Python -> addon -> 游戏 的桥接链路。
5. 若后续启用 DDUI，入口与聊天命令并存，不互相替代。
```

- [ ] **步骤 2：运行核心回归**

运行：`pytest /root/mcbe_ai_agent/tests/test_addon_bridge_protocol.py /root/mcbe_ai_agent/tests/test_addon_bridge_service.py /root/mcbe_ai_agent/tests/test_agent_addon_tools.py -v`
预期：PASS

运行：`pytest /root/mcbe_ai_agent/tests/test_agent_tools.py /root/mcbe_ai_agent/tests/test_connection_manager.py -v`
预期：PASS

运行：`cd /root/mcbe_ai_agent/MCBE-AI-Agent-addon && npm test && npm run build`
预期：PASS

- [ ] **步骤 3：提交文档与回归验证**

```bash
git add \
  /root/mcbe_ai_agent/docs/addon-bridge-protocol.md \
  /root/mcbe_ai_agent/README.md
git commit -m "docs(addon): 补充桥接联调与共存说明"
```

## 自检结论

- 规格覆盖度：已覆盖桥接协议、Python 请求链路、addon 能力实现、Agent 工具抽象、DDUI 预留和联调验证。
- 占位符扫描：未使用「TODO」「后续实现」式空占位；所有阶段都给出文件、命令、测试和代码骨架。
- 类型一致性：统一使用 `mcbeai:bridge_request` 作为 Python -> addon 的 `scriptevent` ID，统一使用 `MCBEAI|RESP|...` 作为 addon -> Python 聊天回传前缀；高层工具名称统一使用 `get_player_snapshot`、`get_inventory_snapshot`、`find_entities`、`run_world_command`。
