# Design: 移植 beta 分支 DDUI UI 前端到 dev（server-ui v2.1.0）

## 1. 目标边界

- **范围**：仅 `MCBE-AI-Agent-addon/` 前端（`scripts/ui/**` + `scripts/bridge/responseSync.ts` + `package.json` + `pnpm-lock.yaml` + `tests/ui/**` + `tests/__mocks__/minecraft-server-ui.ts`）。
- **明确不做**：Python 后端、`mcbe-ws-sdk`、`scripts/bridge/constants.ts`、`router.ts`、`toolPlayer.ts`、`chunking.ts`、`capabilities/**`、`bootstrap.ts`、`main.ts`、manifest、vitest.config。

## 2. 移植策略

采用「检出 beta 文件 + 定向合并/适配」而非整分支 merge（方案 A）：

```
beta 分支                         dev 分支
────────────────────────         ────────────────────────
UI 前端（DDUI 版）        ──检出→  UI 前端（替换旧 ActionForm 版）
responseSync（DDUI 刷新） ──合并→  responseSync（保留 mcbews 线协议）
UI 测试 + DDUI mock      ──检出→  UI 测试 + DDUI mock
package.json（beta 依赖） 参考──→  package.json（升级 v2.1.0 stable）
```

### 2.1 文件清单

#### A. 从 beta 检出（需 v2.1.0 适配）
| 文件 | 说明 |
|---|---|
| `scripts/ui/forms/formAdapter.ts` | DDUI 适配层，**必须重写 API 调用**（见 §3） |
| `scripts/ui/panels/agentConsole.ts` | DDUI 实时聊天主面板 |
| `scripts/ui/panels/morePanel.ts` | 「其他」面板（beta 独有，dev 无） |
| `scripts/ui/panels/settingsPanel.ts` | DDUI 版设置面板 |
| `scripts/ui/panels/statsPanel.ts` | DDUI 版统计面板 |
| `scripts/ui/panels/routes.ts` | 路由（main/more/settings/stats/close） |
| `scripts/ui/entry.ts` | 路由循环（含 morePanel 分支） |
| `scripts/ui/state.ts` | 补回 `refreshConversation` 字段 |
| `tests/ui/agent-console.test.ts` 等 4 个面板测试 | beta 的 UI 面板测试 |
| `tests/__mocks__/minecraft-server-ui.ts` | 补 DDUI mock（适配构造函数） |

#### B. 定向合并
| 文件 | 说明 |
|---|---|
| `scripts/bridge/responseSync.ts` | dev 版 + 补回 beta 的 `refreshConversation` 回调 + `isDuplicateUiUserEcho` 去重 |

#### C. 依赖
| 文件 | 说明 |
|---|---|
| `package.json` | `@minecraft/server-ui` → `2.1.0`，`@minecraft/server` → `2.8.0` |
| `pnpm-lock.yaml` | 重新生成 |
| `behavior_packs/MCBE-AI-Agent/manifest.json` | `dependencies` 模块版本跟随 package.json：`@minecraft/server` → `2.8.0`、`@minecraft/server-ui` → `2.1.0`；manifest 自身 header/module 版本保持 2.5.0（与 package.json 版本体系一致，`abb8c65` 统一过） |
| `resource_packs/MCBE-AI-Agent/manifest.json` | 无模块依赖，保持 2.5.0 不变 |

#### D. 删除（dev 上被取代的旧 UI）
| 文件 | 说明 |
|---|---|
| `scripts/ui/panels/chatInput.ts` | 被 morePanel 取代 |
| `scripts/ui/panels/historyPanel.ts` | 被 morePanel 取代（如 beta 版不包含历史页）——需核对 beta 路由确认 |

## 3. v2.1.0 API 适配（formAdapter.ts 核心）

beta 旧 API → v2.1.0 新 API 映射：

| beta（2.1.0-beta） | v2.1.0 stable |
|---|---|
| `ServerUiModule.CustomForm.create(player, title)` | `new CustomForm(player, title)` |
| `ServerUiModule.Observable.create<T>(init, {clientWritable})` | `new ObservableString(init, opts)` / `new ObservableNumber` / `new ObservableBoolean` / `new ObservableUIRawMessage` |
| 关闭原因 `"UserClose"` / `"ServerClose"` | `DataDrivenScreenClosedReason.ClientClosed` / `ServerClosed`（`UserBusy` 不变） |
| `DropdownItem` | `DropdownItemData` |
| `DduiObservable<T>` 抽象 | 具体 `ObservableString` / `ObservableNumber` / `ObservableBoolean` 类型 |

**适配要点**：
- `createDduiObservable`：把 `Observable.create` 工厂替换为按元素类型分发构造函数（string→`ObservableString`、number→`ObservableNumber`、boolean→`ObservableBoolean`），保持现有调用方签名 `createDduiObservable(initialValue)` 不变，最小化面板代码改动。
- `createCustomForm`：`new CustomForm(player, title)`，保留现有返回类型。
- `showCustomFormSafely`：关闭原因判断适配 `ClientClosed`/`ServerClosed`（现有 `isUserClosedReason`/`isUserBusyReason` 已做递归兼容，补充新枚举字符串即可）。
- 面板代码中凡是用 `DduiObservable` 类型的地方，改用具体的 `ObservableString`/`ObservableBoolean`/`ObservableNumber`（`getData`/`setData`/`subscribe` 接口一致，改动小）。

## 4. responseSync.ts 合并逻辑

dev 版已删除的 beta 逻辑需补回，同时**保留 dev 的线协议**：

```
dev 版（保留）                          beta 版（补回）
────────────────────                  ────────────────────
TEXT_RESP_MESSAGE_ID = "mcbews:text_resp"   （使用 beta 的 UI 实时刷新）
按 playerName 分桶 appendHistoryItem       activeState.refreshConversation?.()
                                          isDuplicateUiUserEcho（去重用户回显）
```

- **保留**：`mcbews:text_resp` 常量、`world/system` 监听、`loadAgentUiState` 持久化。
- **补回**：`onMessageComplete` 中更新 `activeState` 后的 `activeState.refreshConversation?.()`；更新持久化 state 前的 `isDuplicateUiUserEcho` 短路（用户 UI 发送回显去重）。
- **删除**：dev 版中 beta 没有的 `resetResponseSyncForTests`（若 beta 版有则保留）。

## 5. 多人会话隔离

- `responseSync` 的 `activeUiStates` 按 `playerId` key（现有逻辑），`refreshConversation` 回调在 `entry.ts` 中按玩家注册/清理，保持 beta 原逻辑不变。
- 不引入任何新的全局可变状态；禁止用 `ConnectionState.player_name` 做业务分支（CLAUDE.md）。

## 6. 依赖版本

- `@minecraft/server-ui@2.1.0`（stable）与 `@minecraft/server@2.8.0`（stable）随 Minecraft 1.26.30 一起发布，DDUI `CustomForm`/`Observable*` 已转 stable——无需 preview 客户端。
- 生成锁文件：`pnpm install`（addon 目录内）。
- **版本一致性**：dev 已通过 `abb8c65` 把 package.json、behavior/resource manifest 统一到 2.5.0。本次不改版本号体系（保持 2.5.0），只把 `behavior_packs` manifest `dependencies` 里的模块版本与 package.json 同步升级（`@minecraft/server` 2.6.0→2.8.0、`@minecraft/server-ui` 2.0.0→2.1.0）。resource_packs manifest 不动。

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| `isDuplicateUiUserEcho` 依赖回写历史项 `source === "python"`；dev mcbews v1 回写 `source` 取值需核对 | 实现时 grep `docs/addon-bridge-protocol.md` 与 Python `services/gateway/broker_bridge.py` 确认 `text_resp` 回写 `source` 值；若不同则调整去重判断或保留 beta 逻辑并加注释 |
| DDUI API 在 v2.1.0 与 beta mock 的差异导致单测编译失败 | mock 重写为 v2.1.0 构造函数形态，先跑通单测再动面板 |
| `chatInput`/`historyPanel` 是否删除取决于 beta 路由是否包含对应面板 | 以 beta 的 `routes.ts`/`entry.ts` 为准；若 beta 仍引用则保留 |

## 8. 验证策略

- **静态**：`pnpm lint`。
- **单测**：`pnpm test`（迁移的 UI 面板测试 + 既有 bridge/blocks 测试全绿）。
- **构建**：`pnpm build`（TypeScript 编译验证 v2.1.0 API）。
- **运行期（可选）**：MC stable 客户端加载 addon，验证 DDUI 主面板实时刷新、发送、去重。
