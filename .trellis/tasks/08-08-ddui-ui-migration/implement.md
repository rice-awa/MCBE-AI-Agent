# Implement: 移植 beta 分支 DDUI UI 前端到 dev（server-ui v2.1.0）

## 前置

- 当前在 dev 分支，工作区干净。
- 任务激活：`task.py start ddui-ui-migration`（规划通过 review gate 后）。

## 执行步骤（有序）

### 1. 建分支

```bash
git fetch origin
git switch -c feature/ddui-ui-migration origin/dev
```

### 2. 检出 beta 的 UI 文件（面板层 + formAdapter + state + 测试）

```bash
cd MCBE-AI-Agent-addon
for f in \
  scripts/ui/forms/formAdapter.ts \
  scripts/ui/panels/agentConsole.ts \
  scripts/ui/panels/morePanel.ts \
  scripts/ui/panels/settingsPanel.ts \
  scripts/ui/panels/statsPanel.ts \
  scripts/ui/panels/routes.ts \
  scripts/ui/entry.ts \
  scripts/ui/state.ts \
  tests/ui/agent-console.test.ts \
  tests/ui/more-panel.test.ts \
  tests/ui/settings-panel.test.ts \
  tests/ui/stats-panel.test.ts \
  tests/__mocks__/minecraft-server-ui.ts \
; do git checkout beta -- "$f"; done
cd ..
```

- 视 beta 的 `entry.ts`/`routes.ts` 是否引用 `chatInput`/`historyPanel` 决定是否删除 dev 的这两个文件：
  - 若 beta 路由不含 `chatInput`/`history` → `git rm scripts/ui/panels/chatInput.ts scripts/ui/panels/historyPanel.ts`。

### 3. 适配 formAdapter.ts 到 @minecraft/server-ui v2.1.0

- `createDduiObservable`：`Observable.create` 工厂 → 按初始值类型分发的构造函数：
  - string → `new ObservableString(value, { clientWritable: true })`
  - number → `new ObservableNumber(...)`
  - boolean → `new ObservableBoolean(...)`
- `createCustomForm`：`ServerUiModule.CustomForm.create(player, title)` → `new CustomForm(player, title)`。
- 关闭原因：`isUserClosedReason`/`isUserBusyReason` 补充 `DataDrivenScreenClosedReason.ClientClosed`/`ServerClosed` 字符串（与 `UserClose`/`ServerClose` 兼容判断）。
- `DduiObservable<T>` 类型别名 → 具体 `ObservableString`/`ObservableNumber`/`ObservableBoolean`；`getData`/`setData`/`subscribe` 契约不变，面板代码只需改类型标注。
- 移除对 `ServerUiModule as unknown as Partial<...>` 的动态探测（v2.1.0 已是 stable，直接导入）。

### 4. 合并 responseSync.ts

- 以 dev 版为基础，补回：
  - `onMessageComplete` 更新 `activeState` 后调用 `activeState.refreshConversation?.()`。
  - 更新内存/持久化历史前的 `isDuplicateUiUserEcho(history, item)` 短路（user 角色 + source==="ui" 与 source==="python" 同内容去重）。
- 保留：`TEXT_RESP_MESSAGE_ID = "mcbews:text_resp"`、监听注册、`loadAgentUiState` 持久化。
- 核对：`docs/addon-bridge-protocol.md` / Python `services/gateway/broker_bridge.py` 中 `text_resp` 回写历史项的 `source` 取值是否为 `"python"`（去重依赖它）。若不一致，调整 `isDuplicateUiUserEcho` 判断。

### 5. 依赖 + manifest

- `package.json`：`@minecraft/server-ui` → `"2.1.0"`，`@minecraft/server` → `"2.8.0"`。
- `behavior_packs/MCBE-AI-Agent/manifest.json`：`dependencies` 里 `@minecraft/server` → `"2.8.0"`、`@minecraft/server-ui` → `"2.1.0"`；header/module/依赖 uuid 版本保持 2.5.0。
- `resource_packs/MCBE-AI-Agent/manifest.json`：不动。
- 生成锁文件：`cd MCBE-AI-Agent-addon && pnpm install`。

### 6. 测试 mock 适配

- `tests/__mocks__/minecraft-server-ui.ts`：确保导出 v2.1.0 构造函数形态的 `CustomForm`/`ObservableString`/`ObservableNumber`/`ObservableBoolean`/`DataDrivenScreenClosedReason`（beta mock 若为旧 `Observable.create` 形态需重写）。
- 迁移的 4 个面板测试若引用旧 API 形态，同步修正。

### 7. 验证

```bash
cd MCBE-AI-Agent-addon
pnpm lint        # ESLint 通过
pnpm test        # 全部单测（含迁移的 UI 面板测试 + 既有 bridge/blocks）全绿
pnpm build       # TypeScript 编译通过（确认 v2.1.0 API）
```

- 检查 `git status`：确认 `constants.ts`/`router.ts`/`toolPlayer.ts`/`chunking.ts`/`capabilities/**` 未被改动。

### 8. Review Gate + 完成

- 对照 prd.md Acceptance Criteria 逐条核对。
- `task.py` 标记完成后按 Trellis 流程提交（Conventional Commits，如 `feat(addon): 移植 beta DDUI UI 前端至 server-ui v2.1.0`）。
- 推送分支 → 目标 `dev` 的 PR（`task.py create-pr` 或手动）。

## 回滚点

- 每步完成一次 `git status`/`git diff` 检查；如编译/单测失败，回到上一可工作状态。
- 关键回滚点：步骤 3（formAdapter 适配）前——若 v2.1.0 API 无法编译，可回退到 dev 原状重新评估。

## 验证命令（实施时执行）

- 见步骤 7。运行期手动验证（MC stable 客户端）为可选增强项，不阻塞提交。
