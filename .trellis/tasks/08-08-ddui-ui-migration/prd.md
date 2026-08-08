# 移植 beta 分支 DDUI UI 前端到 dev（server-ui v2.1.0）

## Goal

把 beta 分支（`MCBE-AI-Agent-addon`）上基于 DDUI（Data-Driven UI，`@minecraft/server-ui` v2.1.0）的实时 UI 前端移植到 dev 分支的 addon，替换 dev 当前基于 `ActionFormData`/`ModalFormData` 的旧版一次性表单 UI。dev 的 mcbews v1 线协议与 blocks / getLookBlock / getCapabilities 等 bridge 能力保持不变。

## Background（调研结论）

- 两个分支 addon 同源（均自 `fd3e493` 起步），bridge 层高度一致：
  - `bootstrap.ts`、`main.ts`、`toolPlayer.ts`、`chunking.ts`、`stats.ts`、`storage.ts`、`history.ts`、`commands.ts` 两边完全一致。
  - `state.ts` 仅 beta 多一个 `refreshConversation?: () => void` 字段。
- 真正的差异：
  - **UI 前端**：beta 为 DDUI 实时 UI（`formAdapter.ts` 的 `CustomForm`/`Observable`、实时聊天 `agentConsole`、`morePanel`）；dev 为旧版 `ActionFormData` 一次性表单（`chatInput`/`historyPanel`）。
  - **线协议**：beta 用旧 `MCBEAI|*` / `mcbeai:*`；dev 用 mcbews v1 `MCBEWS|*` / `mcbews:*`（CLAUDE.md 强制，不得回退）。
  - **bridge 能力**：dev 额外有 `blocks/*`、`getLookBlock`、`getCapabilities`（保留）。
  - **依赖**：beta 用 `@minecraft/server-ui` 2.1.0-beta + `@minecraft/server` 2.8.0-beta；dev 用 `@minecraft/server-ui` 2.0.0 + `@minecraft/server` 2.6.0（stable）。
  - **UI 测试**：beta 有 4 个面板测试 + DDUI mock；dev 仅保留 `agent-ui-state.test.ts`。
- **重要（API 不兼容）**：beta 的 `formAdapter.ts` 使用 2.1.0-beta 旧 DDUI API，而 v2.1.0 stable 已改版：
  - `Observable<T>` 拆分为 `ObservableString` / `ObservableNumber` / `ObservableBoolean` / `ObservableUIRawMessage`，且改用构造函数 `new ObservableString(...)`（不再 `Observable.create`）。
  - `DropdownItem` → `DropdownItemData`。
  - `DataDrivenScreenClosedReason`：`UserClose` → `ClientClosed`、`ServerClose` → `ServerClosed`（`UserBusy` 不变）。
  - 因此 formAdapter 不能照搬，需做 API 适配。

## Requirements

- UI 前端（面板层 + formAdapter + 路由 + entry）替换为 beta 的 DDUI 版实现，并按 `@minecraft/server-ui` v2.1.0 stable 新 API 适配：
  - `formAdapter.ts`：DDUI `CustomForm` / `Observable*` 用 v2.1.0 构造函数 API，关闭原因枚举用 `ClientClosed`/`ServerClosed`。
  - `agentConsole.ts`、`morePanel.ts`、`settingsPanel.ts`、`statsPanel.ts`、`routes.ts`、`entry.ts`：采用 beta 的 DDUI 版（`morePanel` 取代 dev 的 `chatInput`/`historyPanel` 路由）。
  - `state.ts`：补回 `refreshConversation?: () => void` 字段。
- bridge 层保留 dev 现状：
  - `constants.ts`、`router.ts`、`toolPlayer.ts`、`chunking.ts`、`capabilities/blocks/*`、`getLookBlock`、`getCapabilities` 均不修改（保留 mcbews v1 线协议与 blocks 能力）。
  - `responseSync.ts`：在 dev 版基础上补回 beta 的 DDUI 实时刷新回调（`activeState.refreshConversation?.()`）与用户回显去重（`isDuplicateUiUserEcho`），**保留 dev 的 `mcbews:text_resp` 线协议 ID**。
- 依赖升级：
  - `package.json`：`@minecraft/server-ui` → `2.1.0`（stable）、`@minecraft/server` → `2.8.0`（stable，随 1.26.30 一起发布）。
  - 重新生成 `pnpm-lock.yaml`。
  - `behavior_packs/MCBE-AI-Agent/manifest.json`：`dependencies` 模块版本跟随 package.json（`@minecraft/server` → `2.8.0`、`@minecraft/server-ui` → `2.1.0`）；manifest 自身版本保持 2.5.0（dev 版本体系，`abb8c65` 统一）。resource_packs manifest 不动。
- UI 测试迁移：
  - 迁移 beta 的 4 个面板测试（`agent-console`、`more-panel`、`settings-panel`、`stats-panel`）+ `agent-ui-state`（dev 已有），并按 v2.1.0 mock 适配（`ObservableString` 等构造函数）。
  - `tests/__mocks__/minecraft-server-ui.ts` 补上 DDUI mock（beta 版适配 v2.1.0 构造函数）。

## Acceptance Criteria

- [ ] `scripts/ui/` 下 UI 面板层、formAdapter、routes、entry、state 全部为 DDUI 版，且 `pnpm build` 编译通过（`@minecraft/server-ui` 2.1.0 + `@minecraft/server` 2.8.0）。
- [ ] `scripts/bridge/constants.ts`、`router.ts` 未被回退：仍为 mcbews v1 线协议（`MCBEWS|*` / `mcbews:*`），blocks / getLookBlock / getCapabilities 能力保留。
- [ ] `responseSync.ts` 同时具备：`mcbews:text_resp` 监听、`refreshConversation` 实时刷新回调、`isDuplicateUiUserEcho` 用户回显去重。
- [ ] `package.json` 依赖为 `@minecraft/server-ui@2.1.0`、`@minecraft/server@2.8.0`，`pnpm-lock.yaml` 同步。
- [ ] `behavior_packs/MCBE-AI-Agent/manifest.json` 的 `dependencies` 模块版本为 `@minecraft/server@2.8.0`、`@minecraft/server-ui@2.1.0`；manifest 自身版本保持 2.5.0；resource_packs manifest 未改动。
- [ ] `pnpm test` 全绿（含迁移过来的 UI 面板测试）。
- [ ] `pnpm lint` 通过。
- [ ] 手动在 MC 客户端验证（可选但建议）：DDUI 主面板实时刷新、发送消息后面板保持打开、历史去重正确。

## Notes

- 本任务仅涉及 `MCBE-AI-Agent-addon/` 目录；不触碰 Python 后端、`mcbe-ws-sdk`、多人会话隔离、流控。
- 风险点：dev 的 mcbews v1 Python 端回写历史时 `source` 字段的值需与 `isDuplicateUiUserEcho` 的判断（`source === "python"`）一致，否则去重失效——需在实现时核对 bridge 协议（`docs/addon-bridge-protocol.md`）中 `text_resp` 回写的历史项 `source` 取值。
- 风险点：DDUI 实时刷新在多人场景下每个玩家一个 `refreshConversation` 回调，需确认 `responseSync` 按 `playerName` 分桶处理（beta 已有该逻辑，移植时保持）。
- `dduiSessionStart` hook：在 server-ui 2.1.0/2.2.0-beta/2.3.0-beta 与 server 2.8.0 的 npm 类型声明中均未找到；本任务不依赖它，不阻塞移植。
- 验证命令需在实施阶段执行；不得把真实密钥或 `config.json` 写进提交。
