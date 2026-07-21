# Addon Bridge Protocol (mcbews v1)

## 目标

在 Python 宿主与 Minecraft Addon 之间建立稳定的桥接协议。**线协议权威为 `mcbe-ws-sdk` 的 mcbews v1**（破坏性：不再兼容 `mcbeai:*` / `MCBEAI|*`）。

使用 `/scriptevent` 发起请求，并通过聊天分片返回响应；同时支持 Addon UI 向 Python 自动发送聊天消息。

## 运行时拓扑

```text
Minecraft /wsserver
    │
    ▼
McbeServerFacade (mcbe-ws-sdk)
    ├─ ConnectionManager + ConnectionState
    ├─ AddonBridgeService (mcbews v1)
    ├─ HostConnectionHook ──► MessageBroker / auth / 业务命令
    └─ HostResponseSink  ◄── SDK response_queue
BrokerResponseBridge  ◄── MessageBroker per-connection response queue
AgentWorker  ◄── 注入的 AddonBridgeService
```

## 线常量（bit-identical with SDK `MCBEWS_V1`）

| 用途 | 值 |
|------|-----|
| Bridge 请求 scriptevent | `mcbews:bridge_req` |
| AI / 文本响应 scriptevent | `mcbews:text_resp` |
| Bridge 响应聊天前缀 | `MCBEWS\|BRIDGE` |
| UI 聊天前缀 | `MCBEWS\|UI_CHAT` |
| 模拟工具玩家名 | `MCBEWS_BRIDGE` |
| Bridge request body 版本 | `v=2` |

世界存储动态属性键 `mcbeai:ui_state` **故意保留**，以免旧世界丢 UI 状态；它不是线协议的一部分。

## 链路概览

### 链路 A：Python → Addon 能力请求（Bridge）

```text
Python Agent Tool
  -> AddonBridgeService (SDK)
  -> scriptevent mcbews:bridge_req <json>
  -> Addon scriptEventReceive
  -> capability handler
  -> MCBEWS_BRIDGE 模拟玩家聊天分片 (MCBEWS|BRIDGE|id|i/n|payload)
  -> WebSocket PlayerMessage
  -> SDK 分片重组与 future 唤醒
```

### 链路 B：Addon UI → Python 自动聊天（UI Chat）

```text
玩家 UI 面板输入
  -> MCBEWS_BRIDGE 模拟玩家 (MCBEWS|UI_CHAT|...)
  -> WebSocket PlayerMessage
  -> SDK 重组 -> HostConnectionHook.on_ui_chat_reassembled
  -> MessageBroker / AgentWorker
```

### 链路 C：AI 文本同步到 Addon UI

```text
AgentWorker / CommandHandlers
  -> MessageBroker ai_response_sync
  -> BrokerResponseBridge + McbewsV1Delivery
  -> scriptevent mcbews:text_resp {json chunks}
  -> Addon responseSync.ts 重组并刷新 UI
```

## 请求格式（Python → Addon）

- 命令：`scriptevent mcbews:bridge_req <json>`
- JSON 字段（`v=2`）：
  - `request_id`: string
  - `capability`: string（如 `get_player_snapshot`）
  - `payload`: object
  - `v`: 2

## 响应分片格式（Addon → Python）

聊天消息：

```text
MCBEWS|BRIDGE|<request_id>|<index>/<total>|<payload_fragment>
```

发送者必须为 `MCBEWS_BRIDGE`（SDK 过滤）。

## UI 聊天分片

```text
MCBEWS|UI_CHAT|<msg_id>|<index>/<total>|<payload_fragment>
```

## 宿主接入点

| 组件 | 路径 |
|------|------|
| 网关入口 | `services/gateway/server.py` (`HostGatewayServer`) |
| 设置映射 | `services/gateway/settings_map.py` |
| Hook / 命令 | `services/gateway/hook.py`, `command_handlers.py` |
| Broker 出站 | `services/gateway/broker_bridge.py` |
| WS command 关联 | `services/gateway/ws_command_runner.py` |
| 宿主 Addon 常量 | `MCBE-AI-Agent-addon/scripts/bridge/constants.ts` |
| 推荐 SDK Addon 参考 | `mcbe-ws-sdk/addon/scripts/bridge/` |

## 破坏性说明

旧世界若仍装 **mcbeai** 行为包，桥会超时。必须：

1. 更新行为包到 mcbews 常量版本；
2. 开启 **实验 → Beta APIs**；
3. 重新 `/wsserver` 连接。

配置里遗留的 `addon.protocol.*` mcbeai 值会被忽略：运行时强制 `McbewsV1Profile`。
