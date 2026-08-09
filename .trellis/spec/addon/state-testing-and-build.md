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
