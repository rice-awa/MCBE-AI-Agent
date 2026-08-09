# 状态、测试与构建

## 玩家 UI 状态

UI 状态必须以玩家为边界。`responseSync.ts` 通过目标玩家 id 找到活跃的 `AgentUiStateV2` 或 `AgentUiState`，同时把必要状态保存到该玩家的 DynamicProperty；不要用单例状态承载所有玩家的历史、设置或响应预览。

DDUI persistence version `2` 是 DynamicProperty 的独立版本轴，不等于 MCBEWS/1、capability
request schema `2`、session schema `1` 或 text response framing `1`。当前面板仍通过
`ActionFormData` / `ModalFormData` 适配层运行；不要把 persistence `2` 写成已接入官方 DDUI API。

### DDUI persistence 2 格式

[`scripts/ui/storage.ts`](../../../MCBE-AI-Agent-addon/scripts/ui/storage.ts) 是 DynamicProperty 的唯一归一化边界。v2 采用 per-conversation 桶格式：

```typescript
type PersistedAgentUiStateV2 = {
  version: 2;
  activeConversationId: string;   // 当前活动会话 id
  conversations: {                 // 会话桶数组（上限 MAX_CONVERSATIONS = 20）
    id: string;
    shortId: number;
    title: string;
    history: HistoryItem[];        // 每桶上限 PERSISTED_HISTORY_LIMIT = 100 条
    lastActiveAt: number;
  }[];
  conversationOrder: string[];     // 会话排序
  settings: Partial<AgentUiSettingsV2>;   // autoSaveHistory / showToolEvents / defaultDelivery
  stats?: Partial<AgentUiStats>;          // 含 token 统计字段
  bridgeStatus?: BridgeStatus;
};
```

核心规则：
- 读取 JSON 失败、版本不匹配或字段类型非法时，回退到默认状态或过滤无效历史。
- `settings`、`stats` 和 `history` 的默认值来自 `state.ts` / `history.ts`，panel 不应自己复制默认值和截断规则。
- v1 → v2 迁移：扁平 `history` 被移入 `default` 会话桶。
- DynamicProperty 读写失败是游戏运行时可能出现的外部边界，应返回 `{ ok: false, error }` 或保持当前 UI，不让一次保存失败杀掉 response handler。

### AgentUiStats 三级 Token 统计

[`scripts/ui/stats.ts`](../../../MCBE-AI-Agent-addon/scripts/ui/stats.ts) 定义了三级 token 统计：

| 级别 | 字段前缀 | 持久化 | 重置时机 |
|------|---------|--------|---------|
| 本轮 (round) | `roundInputTokens` / `roundOutputTokens` / `roundTokensCombined` | 否（仅内存） | 切换会话时通过 `resetRoundToken()` |
| 当前会话 (session) | `sessionInputTokens` / `sessionOutputTokens` / `sessionTokensCombined` | 否（仅内存） | 切换会话时通过 `resetSessionTokens()` |
| 全局累计 (total) | `totalInputTokens` / `totalOutputTokens` / `totalTokensCombined` | 是（随 v2 state 持久化） | `resetGlobalTokens()` |

更新入口：`recordTokenUsage(stats, inputTokens, outputTokens)` 一次性更新三级计数。

参考：[`scripts/ui/state.ts`](../../../MCBE-AI-Agent-addon/scripts/ui/state.ts)、[`scripts/ui/history.ts`](../../../MCBE-AI-Agent-addon/scripts/ui/history.ts)、[`scripts/bridge/responseSync.ts`](../../../MCBE-AI-Agent-addon/scripts/bridge/responseSync.ts)。

## Scenario: text_resp completion-only usage 重组

### 1. Scope / Trigger

- 修改 `textResponseAssembler.ts`、`responseSync.ts` 或 SDK text-response vectors 时适用。
- 该约束防止正常顺序的多帧响应在最终帧首次携带 `u` 时被误判为 metadata conflict。

### 2. Signatures

```typescript
type TokenUsage = { i: number; o: number };
type TextResponseChunk = { id: string; i: number; n: number; p: string; r: string; c: string; u?: TokenUsage };
BoundedTextResponseAssembler.push(chunk: TextResponseChunk): TextResponseMessage | null;
```

### 3. Contracts

- 非完成帧 `i < n` 不得携带 `u`；完成帧 `i == n` 可以首次携带 compact usage `{i,o}`。
- 正常顺序与 final-first 乱序都必须可重组；buffer 在完成帧到达前允许 `state.usage` 为空。
- 已记录 usage 后，重复完成帧必须逐字段一致；冲突时丢弃整个 stream buffer。
- 完成结果把最终 usage 原样交给 `responseSync.ts` 的玩家+conversation 状态更新。

### 4. Validation & Error Matrix

| 条件 | 结果 |
|---|---|
| `i < n` 且存在 `u` | boundary 返回 `null`，不接受该帧 |
| 非完成帧先到、完成帧首次带 `u` | 接受；片段齐全时返回含 usage 的完整消息 |
| 完成帧先到且带 `u` | 缓冲 usage，等待缺失片段 |
| 重复完成帧 usage 相同 | 幂等忽略重复帧 |
| 重复完成帧 usage 不同或格式非法 | 丢弃该 stream buffer |

### 5. Good / Base / Bad Cases

- Good：`1/2` 无 `u`，随后 `2/2` 带 `{i:3,o:5}`，结果文本与 usage 均完整。
- Base：单帧 `1/1` 可带 usage；usage 缺省时仍正常完成。
- Bad：`1/2` 带 usage，或同一 `player+cid+id` 的两个完成帧 usage 冲突。

### 6. Tests Required

- `text-response-assembler.test.ts` 必须分别覆盖 normal-order final usage 与 final-first usage。
- 断言完成消息的 `content`、`usage` 和 `bufferCount === 0`；冲突用例断言 buffer 被清理。
- `response-sync.test.ts` 断言 usage 只更新目标玩家、目标 conversation 的 token stats。

### 7. Wrong vs Correct

```typescript
// Wrong: 正常顺序下 state.usage 尚为空，会拒绝首次到达的完成帧。
chunk.i === chunk.n && !sameUsage(state.usage, chunk.u)

// Correct: 只有 buffer 已记录 usage 时才执行一致性比较。
chunk.i === chunk.n && state.usage !== undefined && !sameUsage(state.usage, chunk.u)
```

## Vitest 测试

`mcbews:text_resp` 的 `usage`（compact `{i,o}`）只接受完成帧；`cid` / `t` 在同一响应的相关
帧必须保持一致。测试至少覆盖 CJK/emoji、完成帧 usage、未知 role、metadata conflict、双玩家
双 conversation 和 session 超限单帧错误。

- `vitest.config.ts` 用 alias 把 `@minecraft/server`、`@minecraft/server-ui` 和 GameTest API 指向 `tests/__mocks__/`；依赖 Minecraft API 的测试必须使用这些 mock，不要在单元测试中连接真实世界。
- 优先测试纯函数和边界：`chunking.test.ts` 验证确定性分片/非法长度，`agent-ui-state.test.ts` 验证默认状态、持久化过滤、历史截断、命令文本和分页。
- router/capability 测试可以用 `vi.mock` 替换 `toolPlayer`，并通过 mock world 设置 block/player 状态；测试异步 handler 必须等待 `handleBridgeScriptEvent()` 完成后再断言。
- 新增协议字段时，至少覆盖合法、未知、空值/无效值、乱序分片或缺失分片，以及目标玩家不存在的行为。

## 构建与提交检查

工程使用 `package.json` 的 pnpm scripts 和 `just.config.ts`：

```bash
cd MCBE-AI-Agent-addon
pnpm protocol:check
pnpm test
pnpm lint
pnpm build
```

`pnpm mcaddon:production` 和 `pnpm local-deploy` 会生成或部署产物，只在需要发布/本地验证时运行；不要把 `dist/`、`lib/`、`.env` 或 Minecraft 本地部署目录的输出当作源代码提交。版本从根目录 `_version.py` 同步，不能只修改 Addon `package.json`。
