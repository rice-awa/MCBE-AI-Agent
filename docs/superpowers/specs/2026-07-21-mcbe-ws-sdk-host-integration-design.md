# Design: mcbe-ws-sdk full host integration

**Date:** 2026-07-21  
**Status:** Implemented (worktree)  
**Plan:** `docs/superpowers/plans/2026-07-21-mcbe-ws-sdk-host-integration.md`

## Decision

Switch MCBE-AI-Agent runtime fully to `mcbe-ws-sdk` (`McbeServerFacade` + Hook/Sink). Line protocol is **mcbews v1 only** (breaking, no dual-read). Delete in-tree `services/websocket/*` and `services/addon/*`.

## Architecture

```text
Minecraft Client
      │ /wsserver
      ▼
McbeServerFacade (SDK transport, flow control, addon bridge)
      │ ConnectionHook / ResponseSink
      ▼
Host adapter (services/gateway/)
      │ MessageBroker
      ▼
AgentWorker (PydanticAI) + JWT + PromptManager
```

### Host adapter responsibilities

| Module | Role |
|--------|------|
| `settings_map` | `Settings` → `GatewaySettings` / `CommandRegistry` / `MessageSurfaceConfig` |
| `session_store` | Auth flag + per-player `PlayerSession` (not SDK `ConnectionState.player_name`) |
| `ws_command_runner` | `commandRequest` ↔ `commandResponse` futures |
| `broker_bridge` | Drain broker response queues → SDK delivery |
| `sink` | SDK `OutboundText` / `SystemNotification` → tellraw |
| `hook` | Connect/disconnect, non-blocking command dispatch |
| `command_handlers` | Login/chat/context/model/conversation/… business |
| `server` | Wire facade + lifecycle matching legacy `WebSocketServer` start/stop |

## Protocol authority

Constants must match `mcbe_ws_sdk.profiles.MCBEWS_V1` / SDK addon `constants.ts`:

- `mcbews:bridge_req`, `mcbews:text_resp`
- `MCBEWS|BRIDGE`, `MCBEWS|UI_CHAT`, `MCBEWS_BRIDGE`
- request body `v=2`

Host `addon.protocol` config is documentation/compat only and **must not** override wire IDs.

World key `mcbeai:ui_state` may remain for save compatibility.

## Non-negotiable constraints

1. Hooks never `await` LLM or bridge RTT — use `asyncio.create_task`.
2. Multiplayer identity from `event.sender` / explicit `player_name` only.
3. SDK never imports host; host only public `mcbe_ws_sdk` exports.
4. Prefer direct `McbeOutboundDelivery` in bridge (avoid dual queues for AI text).
5. Flow delays use `tellraw` / `scriptevent` / `text_resp` (not legacy `ai_resp` keys for SDK).

## Out of scope

- Moving MessageBroker / PydanticAI / JWT into SDK
- mcbeai/mcbews dual-stack compatibility
- Hermes adapter rework
