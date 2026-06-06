# DDUI 开发指南

本文档是 `docs/ddui-migration-assessment.md` 的实施配套，基于现有 `MCBE-AI-Agent-addon/` 代码与 `@minecraft/server-ui` 官方 DDUI 文档，给出可直接动手的 DDUI 改造方案、代码骨架、验证清单与回滚策略。

> 评估文档结论：主线继续走稳定 ActionForm/ModalForm，DDUI 走 beta 分支试点。本文档不推翻这个结论，只在 beta 分支落地时给出**完整实施级**指引。如果你需要的是「为什么要分阶段」「为什么不直接整体迁移」的论证，请回到评估文档。

---

## 1. 目标与范围

### 1.1 目标

- 把现有 `scripts/ui/` 下的稳定表单（ActionForm / ModalForm）平滑切换到 DDUI（CustomForm / MessageBox + Observable），保留全部已实现业务能力。
- 让面板状态层（`bridgeStatus`、`lastPrompt`、`lastResponsePreview`、`history`、`settings`、`stats`）以**响应式**方式驱动 UI，免去手工重开表单刷新数据的步骤。
- 不破坏现有 Python 与 addon 的 ScriptEvent 桥接协议（`mcbeai:bridge_request` / `mcbeai:ai_resp`）。
- 把 manifest、构建、测试矩阵区分为 **stable** 与 **beta** 两条产物线，避免互相污染。

### 1.2 非目标

- 不在主线（master）打开 DDUI；DDUI 改造在 `feature/ddui` 或 `beta/*` 分支执行。
- 不重写桥接能力（`get_player_snapshot` 等仍走原路径）。
- 不修改 Python 侧 `services/websocket/flow_control.py` 的分片协议。
- 不引入除官方 `@minecraft/server` / `@minecraft/server-ui` / `@minecraft/server-gametest` 之外的运行时依赖。

### 1.3 适用读者

- 拿到 DDUI 试点任务的 addon 开发者。
- 需要把 `ObservableLike<T>` 抽象替换为官方 `Observable` 的维护者。
- 关心 stable/beta 分支构建与回滚策略的发布负责人。

---

## 2. 现状基线（速查）

> 详细论证见 `docs/ddui-migration-assessment.md`。这里只列实施需要直接引用的事实。

### 2.1 manifest 与依赖

`behavior_packs/MCBE-AI-Agent/manifest.json`：

| 模块 | 当前版本 | DDUI 目标版本 |
|------|---------|---------------|
| `@minecraft/server` | `2.0.0` | `2.8.0-beta` |
| `@minecraft/server-ui` | `2.0.0` | `2.1.0-beta` |
| `@minecraft/server-gametest` | `1.0.0-beta` | 不变 |
| `min_engine_version` | `[1, 21, 80]` | 跟随 beta API 需求上调 |

`package.json` 同步切换。

### 2.2 入口与状态机

```
itemUse(command_block)
  → openAgentUi(player)
      → loadAgentUiState(player)            // dynamic property
      → setActiveUiState(player.id, state)  // 响应同步注册
      → while (route.panel !== "close")     // 状态机循环
            showAgentConsole / showChatInputPanel / ...
      → clearActiveUiState(player.id)
      → saveAgentUiState(player, state)
```

DDUI 改造后这个 `while` 循环会变成「打开一个 CustomForm，按钮回调内部直接打开下一级 CustomForm/MessageBox」，外层不再需要状态机。

### 2.3 状态层

`scripts/ui/state.ts` 已经定义了过渡抽象：

```typescript
export type ObservableLike<T> = {
  getData(): T;
  setData(value: T): void;
};
```

`bridgeStatus`、`lastPrompt`、`lastResponsePreview` 已经包了一层。`history`、`settings`、`stats` 仍然是普通对象。DDUI 改造时把已经包过的三个直接换成 `Observable`，剩下三个按字段粒度补包。

### 2.4 响应同步链路

`scripts/bridge/responseSync.ts` 的 `activeUiStates: Map<playerId, AgentUiState>`：
- Python 推回 `mcbeai:ai_resp` 分片 → 重组 → `appendHistoryItem` + 更新 `bridgeStatus` / `lastResponsePreview`。
- 这是 DDUI **响应式**最大受益点。stable 版本下，玩家必须重开面板才能看到新数据；DDUI 下用 `Observable.setData()` 可以实时刷新打开中的表单。

### 2.5 表单适配层

`scripts/ui/forms/formAdapter.ts` 当前只封装 stable API：`createActionForm`、`createModalForm`、`showActionFormSafely`、`showModalFormSafely`。DDUI 改造在这里新增 `createCustomForm`、`createMessageBox`、`showCustomFormSafely`，保留 stable 版本作为 fallback。

---

## 3. DDUI API 速查

> 来源：`@minecraft/server-ui@2.1.0-beta` 官方文档（`intro-to-ddui.md`）。本节只列改造会用到的 API。

### 3.1 Observable

```typescript
import { Observable } from "@minecraft/server-ui";

// 服务端只读
const status = Observable.create<string>("Loading...");

// 客户端可写（玩家在 UI 控件里改的值会自动写回）
const username = Observable.create<string>("", { clientWritable: true });

status.getData();       // 取值
status.setData("Ready"); // 设值，会触发所有订阅
const off = status.subscribe((v) => console.log(v));
```

### 3.2 CustomForm

```typescript
import { CustomForm, Observable } from "@minecraft/server-ui";

CustomForm.create(player, "Settings")
  .closeButton()                         // 右上角关闭按钮
  .header("一级标题")                     // 大标题
  .label("说明文字")                       // 普通文本，可传 Observable 实现动态 label
  .divider()                             // 分隔线
  .spacer()                              // 空白间距
  .textField("玩家名", username, { description: "..." })
  .toggle("启用音乐", musicEnabled)
  .slider("音量", volume, 0, 100, { step: 5 })
  .dropdown("难度", difficulty, [
    { label: "Peaceful", value: 0 },
    { label: "Easy", value: 1 },
  ])
  .button("应用", () => applySettings(), {
    visible: showAdvanced,    // Observable<boolean>
    disabled: isLocked,       // Observable<boolean>
  })
  .show()
  .then(() => {
    // 表单关闭后从 Observable 读最新值
    const name = username.getData();
  })
  .catch((e) => console.error(e));
```

要点：
- `.show()` 返回 `Promise<void>`，**不再返回 `formValues` 数组**，所有结果通过 `Observable.getData()` 取。
- `button(label, callback)` 的 callback 在玩家点击时立即触发，可以打开下一级 CustomForm 或 MessageBox。
- `visible` / `disabled` 接收 `Observable<boolean>`，可以做条件显示与级联禁用。

### 3.3 MessageBox

```typescript
import { MessageBox } from "@minecraft/server-ui";

MessageBox.create(player)
  .title("确认清空")
  .body("将删除全部本地聊天记录，无法恢复。")
  .button1("确认清空")
  .button2("取消", "保留记录并关闭对话框")
  .show()
  .then((response) => {
    // response.selection: 0 = button1, 1 = button2, undefined = closed
    if (response.selection === 0) {
      clearHistory();
    }
  });
```

### 3.4 取消 / 关闭原因

```typescript
import { FormCancelationReason } from "@minecraft/server-ui";

// FormCancelationReason.UserBusy   玩家被遮挡（read-only 阶段、聊天框打开等）
// FormCancelationReason.UserClosed 玩家主动关闭
```

CustomForm 的 `.show()` 不会再带 `canceled` 字段——因为它直接 resolve `void`，要捕获取消必须 `.catch(e)` 或检查 Observable 是否为初始值。建议在适配层统一吃掉 `UserBusy` 异常，并主动重试一次 `system.run()` 包装。

---

## 4. 总体架构

### 4.1 改造后目录结构

```
MCBE-AI-Agent-addon/
  scripts/
    bootstrap.ts
    main.ts
    bridge/                       # 不变
    ui/
      entry.ts                    # 简化：删除 while 状态机
      state.ts                    # ObservableLike → Observable
      storage.ts                  # 序列化时调用 Observable.getData()
      history.ts                  # 不变
      stats.ts                    # 不变
      commands.ts                 # 不变
      forms/
        formAdapter.ts            # 新增 createCustomForm / createMessageBox
      panels/
        agentConsole.ts           # CustomForm + 顶部摘要全部改 Observable label
        chatInput.ts              # CustomForm + textField + toggle
        historyPanel.ts           # CustomForm + 分页 button + divider
        settingsPanel.ts          # CustomForm + 五字段 + 提交 button
        statsPanel.ts             # CustomForm + label(Observable) + 重置 button
        confirmDialog.ts          # 新增：MessageBox 二次确认
        routes.ts                 # 可选：保留 panel 字面量做回退路由
```

### 4.2 改造后调用链

```
itemUse(command_block)
  → openAgentUi(player)
      → loadAgentUiState(player)
      → setActiveUiState(player.id, state)   // 不变
      → showAgentConsole(player, state)      // 一次 CustomForm.show()
            ├─ button("发送消息")  → showChatInputPanel(...)
            ├─ button("聊天记录")  → showHistoryPanel(...)
            ├─ button("设置")     → showSettingsPanel(...)
            ├─ button("统计信息") → showStatsPanel(...)
            └─ closeButton()
      → clearActiveUiState(player.id)
      → saveAgentUiState(player, state)
```

外层 `while` 循环消失。「返回主面板」由子面板内部 `await showAgentConsole(...)` 实现尾递归。

---

## 5. 状态层迁移

### 5.1 `state.ts` 改造目标

```typescript
import { Observable } from "@minecraft/server-ui";

export type AgentUiSettings = {
  autoSaveHistory: Observable<boolean>;
  maxHistoryItems: Observable<number>;
  showToolEvents: Observable<boolean>;
  responsePreviewLength: Observable<number>;
  defaultDelivery: Observable<number>;   // 用 dropdown 索引
};

export type AgentUiStats = {
  openCount: Observable<number>;
  sentCount: Observable<number>;
  localHistoryCount: Observable<number>;
  responseChunkCount: Observable<number>;
  lastOpenedAt: Observable<number>;
  lastSentAt: Observable<number>;
};

export type AgentUiState = {
  bridgeStatus: Observable<BridgeStatus>;
  lastPrompt: Observable<string>;
  lastResponsePreview: Observable<string>;
  bridgeStatusLabel: Observable<string>;     // 派生：用于面板顶部 label
  summaryLabel: Observable<string>;          // 派生：拼好的多行摘要
  history: Observable<HistoryItem[]>;
  settings: AgentUiSettings;
  stats: AgentUiStats;
};
```

派生 Observable（`bridgeStatusLabel`、`summaryLabel`）通过 `subscribe` 串联：

```typescript
function wireDerived(state: AgentUiState): void {
  const update = () => {
    state.summaryLabel.setData([
      `桥接状态: ${state.bridgeStatus.getData()}`,
      `最近问题: ${state.lastPrompt.getData() || "无"}`,
      `最近响应: ${state.lastResponsePreview.getData() || "无"}`,
      `历史条数: ${state.history.getData().length}`,
      `发送次数: ${state.stats.sentCount.getData()}`,
    ].join("\n"));
  };
  state.bridgeStatus.subscribe(update);
  state.lastPrompt.subscribe(update);
  state.lastResponsePreview.subscribe(update);
  state.history.subscribe(update);
  state.stats.sentCount.subscribe(update);
  update();
}
```

### 5.2 `storage.ts` 改造目标

序列化时调用 `getData()`，反序列化时构造新的 `Observable`。注意 `clientWritable` 的设置必须在 schema 层面确定（不要每次 load 都翻转），否则玩家修改不会写回。

```typescript
const persisted: PersistedAgentUiState = {
  version: AGENT_UI_STATE_VERSION,
  bridgeStatus: state.bridgeStatus.getData(),
  settings: {
    autoSaveHistory: state.settings.autoSaveHistory.getData(),
    maxHistoryItems: state.settings.maxHistoryItems.getData(),
    showToolEvents: state.settings.showToolEvents.getData(),
    responsePreviewLength: state.settings.responsePreviewLength.getData(),
    defaultDelivery: state.settings.defaultDelivery.getData(),
  },
  history: state.history.getData().slice(-PERSISTED_HISTORY_LIMIT),
  stats: snapshotStats(state.stats),
};
```

`createAgentUiState()` 内部再把这些标量重新包到 `Observable.create(value, { clientWritable })` 里。

### 5.3 `responseSync.ts` 改造目标

只需把现有 `activeState.bridgeStatus.setData(...)`、`activeState.lastResponsePreview.setData(...)` 调用保留。`history` 改为 `Observable<HistoryItem[]>`：

```typescript
const next = appendHistoryItem(state.history.getData(), item, state.settings.maxHistoryItems.getData());
state.history.setData(next);
```

`subscribe` 已经把摘要 label、统计 label 串好了，玩家打开中的 CustomForm 会**自动**刷新——这是 DDUI 改造最大的收益。

---

## 6. 表单适配层升级

### 6.1 `formAdapter.ts` 新签名

```typescript
import { ActionFormData, ModalFormData, CustomForm, MessageBox } from "@minecraft/server-ui";
import type { Player } from "@minecraft/server";

// stable 兼容（仅在 stable 构建保留）
export function createActionForm(...) { ... }
export function createModalForm(...) { ... }
export async function showActionFormSafely(...) { ... }
export async function showModalFormSafely(...) { ... }

// DDUI（仅在 beta 构建保留）
export function createCustomForm(player: Player, title: string): CustomForm {
  return CustomForm.create(player, title).closeButton();
}

export function createMessageBox(player: Player): MessageBox {
  return MessageBox.create(player);
}

export async function showCustomFormSafely(form: CustomForm): Promise<{ ok: boolean; reason?: string }> {
  try {
    await form.show();
    return { ok: true };
  } catch (error) {
    if (isUserBusy(error)) {
      return { ok: false, reason: "user_busy" };
    }
    return { ok: false, reason: error instanceof Error ? error.message : String(error) };
  }
}
```

`isUserBusy()` 解析 `FormCancelationReason.UserBusy`。read-only mode 限制依然存在：所有 `.show()` 必须从 `system.run()` 异步上下文中调用，不要直接挂在 `world.beforeEvents.*` 同步回调里。

### 6.2 `system.run` 包装

`entry.ts` 当前已经在 `void openAgentUi(player).catch(...)` 这一层间接异步化，DDUI 改造后保留这层即可。子面板按钮回调内部如果要打开下一个面板，建议显式 `system.run(() => void showXxx(player, state))`，避免 DDUI 内部状态切换时的边界问题。

---

## 7. 分面板设计

下面给出每个面板的 DDUI 实现骨架。代码风格沿用现有项目（早 return、不写多余注释、错误用 `player.sendMessage` 提示）。

### 7.1 `agentConsole.ts`（主面板）

```typescript
import { system } from "@minecraft/server";
import type { Player } from "@minecraft/server";
import { createCustomForm, showCustomFormSafely } from "../forms/formAdapter";
import type { AgentUiState } from "../state";
import { saveAgentUiState } from "../storage";
import { showChatInputPanel } from "./chatInput";
import { showHistoryPanel } from "./historyPanel";
import { showSettingsPanel } from "./settingsPanel";
import { showStatsPanel } from "./statsPanel";

export async function showAgentConsole(player: Player, state: AgentUiState): Promise<void> {
  const form = createCustomForm(player, "MCBE AI Agent")
    .label(state.summaryLabel)
    .divider()
    .button("发送消息", () => system.run(() => void showChatInputPanel(player, state)))
    .button("聊天记录", () => system.run(() => void showHistoryPanel(player, state, 0)))
    .button("设置", () => system.run(() => void showSettingsPanel(player, state)))
    .button("统计信息", () => system.run(() => void showStatsPanel(player, state)));

  const result = await showCustomFormSafely(form);
  if (!result.ok && result.reason === "user_busy") {
    player.sendMessage("MCBE AI Agent: 当前无法打开面板，请稍后再试。");
  }

  saveAgentUiState(player, state);
}
```

`label(state.summaryLabel)` 是动态 label。`responseSync.ts` 收到 Python 回包时调 `lastResponsePreview.setData(...)`，摘要 label 会自动重算并在面板里实时刷新。

### 7.2 `chatInput.ts`（消息发送）

```typescript
import { Observable } from "@minecraft/server-ui";
import type { Player } from "@minecraft/server";
import { createCustomForm, showCustomFormSafely } from "../forms/formAdapter";
import { sendUiChatMessage } from "../../bridge/toolPlayer";
import { buildAgentChatCommand } from "../commands";
import { appendHistoryItem, createHistoryId } from "../history";
import { recordPromptSent, syncLocalHistoryCount } from "../stats";
import type { AgentUiState } from "../state";
import { saveAgentUiState } from "../storage";
import { showAgentConsole } from "./agentConsole";

export async function showChatInputPanel(player: Player, state: AgentUiState): Promise<void> {
  const draft = Observable.create<string>(state.lastPrompt.getData(), { clientWritable: true });
  const returnToMain = Observable.create<boolean>(true, { clientWritable: true });

  const form = createCustomForm(player, "发送 AI 聊天")
    .textField("消息内容", draft, { description: "输入要发送给 AI 的内容" })
    .toggle("发送后返回主面板", returnToMain)
    .button("发送", () => submit(player, state, draft, returnToMain));

  await showCustomFormSafely(form);
}

async function submit(
  player: Player,
  state: AgentUiState,
  draft: Observable<string>,
  returnToMain: Observable<boolean>,
): Promise<void> {
  const message = draft.getData().trim();
  if (!message) {
    player.sendMessage("MCBE AI Agent: 消息不能为空。");
    return;
  }

  const now = Date.now();
  if (state.settings.autoSaveHistory.getData()) {
    state.history.setData(
      appendHistoryItem(
        state.history.getData(),
        { id: createHistoryId("ui", now), role: "user", content: message, createdAt: now, source: "ui" },
        state.settings.maxHistoryItems.getData(),
      ),
    );
  }
  state.lastPrompt.setData(message);
  state.lastResponsePreview.setData("已记录到本地历史；等待 Python 响应同步...");
  state.bridgeStatus.setData("connecting");
  state.stats.sentCount.setData(state.stats.sentCount.getData() + 1);
  state.stats.lastSentAt.setData(now);
  syncLocalHistoryCount(state.stats, state.history.getData().length);

  try {
    sendUiChatMessage(player.name, message);
    state.bridgeStatus.setData("sent");
    player.sendMessage("MCBE AI Agent: 消息已发送至 AI 服务。");
  } catch {
    state.bridgeStatus.setData("error");
    player.sendMessage(`MCBE AI Agent: 自动发送失败，请手动输入: ${buildAgentChatCommand(message)}`);
  }

  saveAgentUiState(player, state);

  if (returnToMain.getData()) {
    await showAgentConsole(player, state);
  }
}
```

注意：`stats.sentCount` 与 `stats.lastSentAt` 现在是 `Observable<number>`，要 `setData(x + 1)` 而非直接对象赋值。

### 7.3 `historyPanel.ts`（分页历史）

DDUI 没有「重新打开同一个表单」的内置 API，所以分页通过 `Observable<number>` 的页码加 label/button 视觉刷新，或者干脆每次按钮回调里关闭后重开下一页。这里给出后者的做法，简单稳定：

```typescript
import { Observable } from "@minecraft/server-ui";
import type { Player } from "@minecraft/server";
import { createCustomForm, createMessageBox, showCustomFormSafely } from "../forms/formAdapter";
import { clearHistory, formatHistoryItem, getHistoryPage } from "../history";
import { syncLocalHistoryCount } from "../stats";
import type { AgentUiState } from "../state";
import { saveAgentUiState } from "../storage";
import { showAgentConsole } from "./agentConsole";

const PAGE_SIZE = 5;

export async function showHistoryPanel(player: Player, state: AgentUiState, pageIndex: number): Promise<void> {
  const page = getHistoryPage(state.history.getData(), pageIndex, PAGE_SIZE);
  const body = page.items.length === 0
    ? "暂无聊天记录。"
    : [`第 ${page.pageIndex + 1}/${page.totalPages} 页，共 ${page.totalItems} 条`, "", ...page.items.map(formatHistoryItem)].join("\n");

  const bodyLabel = Observable.create<string>(body);

  const form = createCustomForm(player, "聊天记录")
    .label(bodyLabel)
    .divider()
    .button("上一页", () => showHistoryPanel(player, state, Math.max(0, page.pageIndex - 1)), { disabled: pageObservable(page.pageIndex === 0) })
    .button("下一页", () => showHistoryPanel(player, state, Math.min(page.totalPages - 1, page.pageIndex + 1)), { disabled: pageObservable(page.pageIndex >= page.totalPages - 1) })
    .button("清空历史", () => confirmClear(player, state))
    .button("返回主面板", () => showAgentConsole(player, state));

  await showCustomFormSafely(form);
}

function pageObservable(disabled: boolean): Observable<boolean> {
  return Observable.create<boolean>(disabled);
}

async function confirmClear(player: Player, state: AgentUiState): Promise<void> {
  const result = await createMessageBox(player)
    .title("确认清空")
    .body("将删除本地聊天记录，无法恢复。")
    .button1("确认清空")
    .button2("取消")
    .show();

  if (result.selection !== 0) {
    return;
  }

  state.history.setData(clearHistory(state.history.getData()));
  syncLocalHistoryCount(state.stats, 0);
  saveAgentUiState(player, state);
  player.sendMessage("MCBE AI Agent: 本地聊天记录已清空。");
  await showAgentConsole(player, state);
}
```

### 7.4 `settingsPanel.ts`（设置）

```typescript
import type { Player } from "@minecraft/server";
import { createCustomForm, showCustomFormSafely } from "../forms/formAdapter";
import type { AgentUiState } from "../state";
import { saveAgentUiState } from "../storage";
import { showAgentConsole } from "./agentConsole";

const DELIVERY_OPTIONS = [
  { label: "tellraw", value: 0 },
  { label: "scriptevent", value: 1 },
];

export async function showSettingsPanel(player: Player, state: AgentUiState): Promise<void> {
  const form = createCustomForm(player, "MCBE AI Agent 设置")
    .toggle("自动保存历史", state.settings.autoSaveHistory)
    .slider("历史保留条数", state.settings.maxHistoryItems, 10, 50, { step: 5 })
    .toggle("显示工具事件", state.settings.showToolEvents)
    .slider("响应预览长度", state.settings.responsePreviewLength, 60, 240, { step: 20 })
    .dropdown("默认响应方式", state.settings.defaultDelivery, DELIVERY_OPTIONS)
    .divider()
    .button("保存并返回", () => persist(player, state));

  await showCustomFormSafely(form);
}

async function persist(player: Player, state: AgentUiState): Promise<void> {
  state.history.setData(state.history.getData().slice(-state.settings.maxHistoryItems.getData()));
  const result = saveAgentUiState(player, state);
  player.sendMessage(result.ok ? "MCBE AI Agent: 设置已保存。" : "MCBE AI Agent: 设置保存失败，本次仅内存生效。");
  await showAgentConsole(player, state);
}
```

> 关键差异：`clientWritable` 让玩家在 slider/toggle/textField 上的修改**自动写回到 Observable**，所以「保存并返回」按钮回调里**不需要再读取响应数组**——`state.settings.maxHistoryItems.getData()` 就是玩家拨到的值。

### 7.5 `statsPanel.ts`（统计）

```typescript
import { Observable } from "@minecraft/server-ui";
import type { Player } from "@minecraft/server";
import { createCustomForm, createMessageBox, showCustomFormSafely } from "../forms/formAdapter";
import { resetStats, syncLocalHistoryCount } from "../stats";
import type { AgentUiState } from "../state";
import { saveAgentUiState } from "../storage";
import { showAgentConsole } from "./agentConsole";

export async function showStatsPanel(player: Player, state: AgentUiState): Promise<void> {
  const body = Observable.create<string>(buildBody(state));
  state.history.subscribe(() => body.setData(buildBody(state)));
  state.stats.sentCount.subscribe(() => body.setData(buildBody(state)));
  state.stats.responseChunkCount.subscribe(() => body.setData(buildBody(state)));

  const form = createCustomForm(player, "统计信息")
    .label(body)
    .divider()
    .button("重置统计", () => confirmReset(player, state))
    .button("返回主面板", () => showAgentConsole(player, state));

  await showCustomFormSafely(form);
}

function buildBody(state: AgentUiState): string {
  return [
    `打开面板次数: ${state.stats.openCount.getData()}`,
    `发送消息次数: ${state.stats.sentCount.getData()}`,
    `本地历史条数: ${state.history.getData().length}`,
    `响应片段数: ${state.stats.responseChunkCount.getData()}`,
    `最近打开: ${formatTs(state.stats.lastOpenedAt.getData())}`,
    `最近发送: ${formatTs(state.stats.lastSentAt.getData())}`,
  ].join("\n");
}

function formatTs(ts: number): string {
  return ts ? new Date(ts).toLocaleString() : "无";
}

async function confirmReset(player: Player, state: AgentUiState): Promise<void> {
  const result = await createMessageBox(player).title("确认重置").body("将清零统计计数").button1("确认").button2("取消").show();
  if (result.selection !== 0) {
    return;
  }
  const fresh = resetStats();
  state.stats.openCount.setData(fresh.openCount);
  state.stats.sentCount.setData(fresh.sentCount);
  state.stats.responseChunkCount.setData(fresh.responseChunkCount);
  state.stats.lastOpenedAt.setData(fresh.lastOpenedAt);
  state.stats.lastSentAt.setData(fresh.lastSentAt);
  syncLocalHistoryCount(state.stats, state.history.getData().length);
  saveAgentUiState(player, state);
  player.sendMessage("MCBE AI Agent: 统计信息已重置。");
  await showAgentConsole(player, state);
}
```

### 7.6 `confirmDialog.ts`（通用二次确认）

抽象一个通用的 yes/no MessageBox helper，避免每个面板都重写一遍：

```typescript
import type { Player } from "@minecraft/server";
import { createMessageBox } from "../forms/formAdapter";

export async function confirm(player: Player, title: string, body: string, okText = "确认", cancelText = "取消"): Promise<boolean> {
  const result = await createMessageBox(player).title(title).body(body).button1(okText).button2(cancelText).show();
  return result.selection === 0;
}
```

---

## 8. 与桥接协议的互操作

### 8.1 不变项

- `mcbeai:bridge_request` / `mcbeai:ai_resp` ScriptEvent ID 不变。
- `BRIDGE_MAX_CHUNK_CONTENT_LENGTH=256` 上行字符上限不变。
- `MCBEAI_TOOL` 模拟玩家与 `tell @s` 命令链路不变。
- Python 侧 `flow_control.py` 的下行分片协议不变。

### 8.2 受益项

- `responseSync.ts` 的 `activeUiStates` 现在写到的是 `Observable`。Python 推回的每一片都会触发 `subscribe` 回调，进而：
  - 主面板顶部摘要 label 自动重算。
  - 历史面板 label（如果是当前页）自动刷新。
  - 统计面板的 `responseChunkCount` 自动 +1 并更新。
- 玩家不再需要点「刷新」按钮，主面板的「刷新」按钮可以下线。

### 8.3 新增可选事件

如果未来要让 UI 主动触发 Python 处理（例如 UI 端切换模型），考虑新增一条 ScriptEvent：

```
mcbeai:ui_event  payload={"type":"switch_provider","player":"<name>","provider":"deepseek"}
```

由 Python 侧识别并走 `MessageBroker`，避免污染现有 `mcbeai:bridge_request` 命名空间。该协议**不在本次 DDUI 改造范围内**，单独立计划。

---

## 9. Manifest 与依赖切换

### 9.1 双轨产物

- `master` 分支：stable 包，`@minecraft/server@2.0.0` + `@minecraft/server-ui@2.0.0`。
- `feature/ddui` 分支：beta 包，依赖切到 `2.8.0-beta` / `2.1.0-beta`。
- 通过 `just-scripts` 的 `--production` 与 `mcaddon` task 各自构建，产物文件名带 `-beta` 后缀（在 `just.config.ts` 配置）。

### 9.2 manifest 改动

```json
{
  "header": {
    "min_engine_version": [1, 21, 80]
  },
  "dependencies": [
    { "module_name": "@minecraft/server", "version": "2.8.0-beta" },
    { "module_name": "@minecraft/server-ui", "version": "2.1.0-beta" },
    { "module_name": "@minecraft/server-gametest", "version": "1.0.0-beta" }
  ]
}
```

`min_engine_version` 跟随 beta API 实测可用版本调整，必要时拉到 `[1, 21, 90]` 以上。

### 9.3 实验开关

beta 包安装说明里必须强制要求世界开启：
- `Beta APIs`
- 现有 `GameTest Framework`（已开）

### 9.4 npm 依赖

```json
{
  "dependencies": {
    "@minecraft/server": "2.8.0-beta",
    "@minecraft/server-ui": "2.1.0-beta",
    "@minecraft/server-gametest": "1.0.0-beta.1.26.20-preview.28"
  }
}
```

锁定具体 beta 版本号，不要用 `^` 通配——beta 通道随时可能 break。

---

## 10. 开发流程与构建/测试

### 10.1 开发顺序

按下面顺序逐步落地，每完成一步跑一次 `npm run build` + `npm test` 再继续：

1. 切分支 `feature/ddui`，更新 `package.json` 与 `manifest.json`，跑 `npm install`，确认本地 `node_modules/@minecraft/server-ui/index.d.ts` 已包含 `CustomForm` / `MessageBox` / `Observable`。
2. 改 `state.ts`：把所有 `ObservableLike<T>` 替换为 `Observable<T>`，补 `subscribe` 串联派生 label。
3. 改 `storage.ts`：`getData()` / 重新构造 `Observable`。
4. 改 `formAdapter.ts`：新增 `createCustomForm` / `createMessageBox` / `showCustomFormSafely`，保留 stable 函数（方便 grep 与回退）。
5. 改 `agentConsole.ts`，跑游戏内最小验证：能打开主面板。
6. 依次改 `chatInput.ts`、`settingsPanel.ts`、`statsPanel.ts`、`historyPanel.ts`。
7. 新增 `confirmDialog.ts` 抽象，回填到清空历史和重置统计。
8. 删 `entry.ts` 里的 `while` 路由，简化为「调用 `showAgentConsole`」。

### 10.2 构建

```bash
cd MCBE-AI-Agent-addon
npm run build         # 增量构建
npm run build:production
npm run mcaddon       # 产物 .mcaddon
```

### 10.3 测试矩阵

| 测试 | 范围 | 必跑时机 |
|------|------|----------|
| `npm test` | vitest 单元测试 | 每次面板逻辑改动 |
| `npm run lint` | eslint + minecraft-linting | PR 前 |
| 游戏内联调（手动） | 主面板 / 设置 / 历史 / 聊天 / 统计 | 每个面板迁移完成 |
| Python 端联调（cli.py serve） | UI 发消息 → Python 回包 → UI 自动刷新 | 状态层与响应同步链路全部接通后 |

vitest 当前 5 个测试文件、14 个测试。DDUI 改造后建议补：

- `tests/ui/state.test.ts` 验证 `subscribe` 派生 label 的串联逻辑。
- `tests/ui/storage.test.ts` 验证 `Observable` 序列化/反序列化。
- 不要给 `CustomForm.show()` 写真实集成测试——beta API 在测试环境无法 mock 到等价行为，留给游戏内手测。

### 10.4 联调命令

游戏内：

```
/wsserver <服务器IP>:8080
#登录 123456
（手持 command_block 右键打开 UI）
```

Python 侧：

```bash
python cli.py serve
```

观察 `connecting → sent → ready` 三态切换是否在不重开面板的情况下自动反映到主面板。

---

## 11. 验证清单

发布 beta 包前**必须**全部通过：

- [ ] 本地 `node_modules/@minecraft/server-ui/index.d.ts` 含 `CustomForm`、`MessageBox`、`Observable`。
- [ ] `npm run build` 无 type error。
- [ ] `npm test` 全部通过。
- [ ] 游戏内开 `Beta APIs`，加载 addon 不报红字。
- [ ] 手持 `minecraft:command_block` 右键打开主面板，能看到摘要、四个按钮。
- [ ] 「发送消息」流程：textField 输入、toggle 改默认值、点发送、Python 收到、`bridgeStatus` 在打开中的面板自动从 `connecting` → `sent` → `ready`。
- [ ] 「设置」流程：所有控件初始值正确、修改保存后 `getDynamicProperty("mcbeai:ui_state")` 含新值。
- [ ] 「聊天记录」分页：上一页/下一页禁用条件正确（首页禁用上一页、末页禁用下一页）。
- [ ] 「清空历史」二次确认：取消不清空、确认清空。
- [ ] 「统计信息」实时刷新：另一窗口让 Python 回包时，统计页 `responseChunkCount` 不重开就更新。
- [ ] 多玩家同时打开 UI：状态不串扰（不同 `player.id` 走不同的 `activeUiStates` 桶）。
- [ ] `/reload` 后再次打开 UI 不丢历史、不丢设置。

---

## 12. 风险与回滚

### 12.1 主要风险

| 风险 | 触发条件 | 缓解 |
|------|----------|------|
| beta 类型签名变化 | 升级 npm 后 `CustomForm.create` 参数变了 | 锁死具体 beta 版本号；写适配层吸收差异 |
| `clientWritable` 写回延迟 | 玩家拨完 slider 立刻按按钮，回调里 `getData()` 还是旧值 | 在按钮回调里 `await system.runIdle()` 一帧再读，或显式让玩家先按「保存」 |
| `MessageBox.show()` 在 read-only 抛错 | 桥接事件回调里直接弹 MessageBox | 全部通过 `system.run()` 切换到 normal mode |
| `Observable.subscribe` 内存泄漏 | 面板关闭后还订阅 | 主面板生命周期内集中订阅一次；子面板用临时 Observable，不直接订阅全局 state |
| `Beta APIs` 关闭导致玩家加载失败 | 用户拿 beta 包到 stable 世界 | 安装文档强制说明，并在 README 加红字提示 |

### 12.2 回滚策略

- DDUI 改造在独立分支，主线不受影响。
- 如果游戏内验证发现阻断性问题：保留分支但暂停合并。stable 包继续按现有 ActionForm/ModalForm 发版。
- 单文件回滚：`git checkout master -- scripts/ui/<file>` 恢复 stable 实现，因为 `formAdapter.ts` 在 stable 阶段保留双签名，业务面板可以选择性回退。

---

## 13. FAQ

**Q: 为什么不直接在主线开 DDUI？**
A: 见评估文档第 5 章。短答：beta 模块要求世界级实验开关，会改变所有用户的安装条件。

**Q: `Observable<HistoryItem[]>` 修改数组成员，UI 会刷新吗？**
A: 必须 `setData(newArray)` 替换整个引用。不要 `state.history.getData().push(item)`，那不会触发 subscribe。

**Q: 为什么 settings 不直接监听每个 Observable，存到 dynamic property？**
A: 写盘频率会失控（玩家拖一次 slider，DDUI 中间态可能触发多次 setData）。用「保存并返回」按钮显式提交，与现状语义一致，也方便回退。

**Q: 主面板要不要保留「刷新」按钮？**
A: DDUI 改造后建议下线。Observable 已经实时刷新，「刷新」按钮在 stable 阶段是补丁，DDUI 阶段是冗余 UI。

**Q: `MessageBox` 的 `closeReason` 怎么用？**
A: `result.closeReason` 区分玩家点空白关闭还是按按钮。`selection === undefined` + `closeReason === "UserClosed"` 表示玩家直接关掉对话框，按「取消」语义处理即可。

**Q: 旧的 `AGENT 聊天 <消息>` 命令还要吗？**
A: 留着。命令链路与 UI 链路并行，`buildAgentChatCommand()` 已经在 `chatInput.ts` 兜底失败提示里复用。

---

## 14. 参考

- 评估文档：`docs/ddui-migration-assessment.md`
- 桥接协议：`docs/addon-bridge-protocol.md`
- 多人会话隔离修复：`claude_md/fix/MULTIPLAYER_SESSION_FIX.md`
- 官方 DDUI 入门：<https://learn.microsoft.com/en-us/minecraft/creator/documents/scripting/intro-to-ddui>
- `@minecraft/server-ui` API：<https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server-ui/minecraft-server-ui>
- `CustomForm` API（experimental）：<https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server-ui/customform>
- Script Module 版本规则：<https://learn.microsoft.com/en-us/minecraft/creator/documents/scripting/versioning>
