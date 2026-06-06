# DDUI 实时聊天主面板实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。用户明确要求本次不要创建 git commit。

**目标：** 将 DDUI 主面板改造成可保持打开的实时聊天面板，发送输入与最近 10 条对话记录合并在主面板，设置页和统计页保持独立。

**架构：** 复用现有 `CustomForm` + `Observable` 适配层，主面板维护 `conversationBody` 和状态摘要 Observable。发送按钮直接更新内存状态、持久化并通过现有桥接发送；响应同步模块写入活跃状态后调用运行期刷新回调，让打开中的主面板实时更新。

**技术栈：** TypeScript, `@minecraft/server` 2.8 beta, `@minecraft/server-ui` 2.1 beta DDUI `CustomForm`/`Observable`, Vitest, pnpm。

---

## 文件结构

- 修改：`MCBE-AI-Agent-addon/scripts/ui/state.ts`
  - 为 `AgentUiState` 增加非持久化运行期刷新回调字段。
- 修改：`MCBE-AI-Agent-addon/scripts/ui/panels/agentConsole.ts`
  - 将主面板改造成实时聊天面板，合并输入、发送、最近 10 条历史和入口按钮。
- 修改：`MCBE-AI-Agent-addon/scripts/bridge/responseSync.ts`
  - 写入活跃 UI 状态后触发主面板刷新回调。
- 修改：`MCBE-AI-Agent-addon/scripts/ui/panels/settingsPanel.ts`
  - 新增“清空历史”按钮，设置页承担历史清空职责。
- 修改：`MCBE-AI-Agent-addon/scripts/ui/panels/routes.ts`
  - 移除 `chatInput` 和 `history` 路由。
- 修改：`MCBE-AI-Agent-addon/scripts/ui/entry.ts`
  - 移除独立发送页和历史分页页路由分支。
- 删除：`MCBE-AI-Agent-addon/scripts/ui/panels/chatInput.ts`
  - 独立发送页被主面板取代。
- 删除：`MCBE-AI-Agent-addon/scripts/ui/panels/historyPanel.ts`
  - 独立历史分页入口被移除。
- 修改：`MCBE-AI-Agent-addon/tests/__mocks__/minecraft-server-ui.ts`
  - 增强 DDUI mock，记录最后创建的表单与字段值，支持断言发送后输入框清空和 label 更新。
- 修改：`MCBE-AI-Agent-addon/tests/__mocks__/minecraft-server.ts`
  - 增强 scriptEventReceive/world mock，支持响应同步测试注入玩家和触发事件。
- 修改：`MCBE-AI-Agent-addon/tests/ui/agent-console.test.ts`
  - 覆盖主面板发送、路由、关闭和 DDUI 失败。
- 修改：`MCBE-AI-Agent-addon/tests/ui/settings-panel.test.ts`
  - 覆盖清空历史按钮。
- 创建：`MCBE-AI-Agent-addon/tests/bridge/response-sync.test.ts`
  - 覆盖响应同步触发活跃主面板刷新回调。
- 删除：`MCBE-AI-Agent-addon/tests/ui/history-panel.test.ts`
  - 对应已移除历史分页面板。

---

### 任务 1：增强测试 mock

**文件：**
- 修改：`MCBE-AI-Agent-addon/tests/__mocks__/minecraft-server-ui.ts:1-139`
- 修改：`MCBE-AI-Agent-addon/tests/__mocks__/minecraft-server.ts:1-28`

- [ ] **步骤 1：更新 DDUI mock，暴露最后创建的 CustomForm**

将 `MCBE-AI-Agent-addon/tests/__mocks__/minecraft-server-ui.ts` 中 `MockCustomForm` 与导出工具改为支持读取最后表单状态。保留已有 API，增加这些导出：

```ts
export function __getLastCustomForm(): MockCustomForm | undefined;
```

`MockCustomForm` 需要提供：

```ts
getFieldData(label: string): unknown {
  return this.#fields.get(label)?.getData();
}

getLabelTexts(): unknown[] {
  return this.#labels.map((value) => typeof value === "string" ? value : value.getData());
}

clickButton(label: string): void {
  this.#buttons.get(label)?.();
}
```

并让 `.label(value)` 记录 label：

```ts
#labels: Array<string | MockObservable<unknown>> = [];

label(value: string | MockObservable<unknown>) {
  this.#labels.push(value);
  return this;
}
```

`CustomForm.create()` 中设置 `lastCustomForm`：

```ts
let lastCustomForm: MockCustomForm | undefined;

export function __getLastCustomForm(): MockCustomForm | undefined {
  return lastCustomForm;
}

export const CustomForm = {
  create() {
    if (nextInteraction.failOnCustomFormCreate) {
      throw new Error("Mock DDUI CustomForm API is unavailable.");
    }
    lastCustomForm = new MockCustomForm();
    return lastCustomForm;
  },
};
```

`__resetDduiMock()` 里清空 `lastCustomForm`。

- [ ] **步骤 2：更新 server mock，支持注入玩家和触发 script event**

将 `MCBE-AI-Agent-addon/tests/__mocks__/minecraft-server.ts` 扩展为：

```ts
type ScriptEventCallback = (event: { id: string; message: string }) => void;

const scriptEventSubscribers = new Set<ScriptEventCallback>();
let mockPlayers: unknown[] = [];

export function __resetMinecraftServerMock(): void {
  scriptEventSubscribers.clear();
  mockPlayers = [];
}

export function __setMockPlayers(players: unknown[]): void {
  mockPlayers = players;
}

export function __emitScriptEvent(event: { id: string; message: string }): void {
  for (const subscriber of scriptEventSubscribers) {
    subscriber(event);
  }
}
```

并让 `system.afterEvents.scriptEventReceive.subscribe` 记录回调，`world.getAllPlayers()` 返回 `mockPlayers`：

```ts
export const system = {
  afterEvents: {
    scriptEventReceive: {
      subscribe: (callback: ScriptEventCallback) => {
        scriptEventSubscribers.add(callback);
        return callback;
      },
      unsubscribe: (callback: ScriptEventCallback) => {
        scriptEventSubscribers.delete(callback);
      },
    },
  },
  currentTick: 0,
  runInterval: () => {},
  run: () => {},
};

export const world = {
  afterEvents: {
    itemUse: {
      subscribe: () => {},
      unsubscribe: () => {},
    },
  },
  getAllPlayers: () => mockPlayers,
  getPlayers: () => mockPlayers,
  getDimension: () => ({
    getEntities: () => [],
    runCommand: () => ({ successCount: 0 }),
  }),
};
```

- [ ] **步骤 3：运行现有 UI 测试确认 mock 没破坏旧行为**

运行：

```bash
cd MCBE-AI-Agent-addon && pnpm test tests/ui/agent-console.test.ts tests/ui/settings-panel.test.ts
```

预期：现有测试仍 PASS。如果失败，只修 mock 兼容性，不改业务逻辑。

---

### 任务 2：为实时主面板编写失败测试

**文件：**
- 修改：`MCBE-AI-Agent-addon/tests/ui/agent-console.test.ts`

- [ ] **步骤 1：更新 imports**

在 `agent-console.test.ts` 中从 `@minecraft/server-ui` 增加 `__getLastCustomForm`：

```ts
import {
  __getLastCustomForm,
  __resetDduiMock,
  __setNextCustomFormInteraction,
} from "@minecraft/server-ui";
```

- [ ] **步骤 2：移除旧路由测试，添加发送不关闭表单测试**

删除断言“发送消息”路由到 `{ panel: "chatInput" }` 的测试，新增：

```ts
it("sends a message from the main panel without closing it", async () => {
  __setNextCustomFormInteraction({
    clickButtonLabel: "发送",
    fieldValues: {
      "消息内容": "  你好 AI  ",
    },
  });

  const player = createFakePlayer();
  const uiState = createAgentUiState();

  const route = await showAgentConsole(player, uiState);

  expect(route).toEqual({ panel: "main" });
  expect(uiState.history).toHaveLength(1);
  expect(uiState.history[0]).toMatchObject({
    role: "user",
    content: "你好 AI",
    source: "ui",
  });
  expect(uiState.lastPrompt.getData()).toBe("你好 AI");
  expect(uiState.bridgeStatus.getData()).toBe("sent");
  expect(uiState.stats.sentCount).toBe(1);
  expect(__getLastCustomForm()?.getFieldData("消息内容")).toBe("");
  expect(player.messages).toContain("MCBE AI Agent: 消息已发送至 AI 服务。");
});
```

- [ ] **步骤 3：添加空消息测试**

```ts
it("keeps the main panel open when the message is empty", async () => {
  __setNextCustomFormInteraction({
    clickButtonLabel: "发送",
    fieldValues: {
      "消息内容": "   ",
    },
  });

  const player = createFakePlayer();
  const uiState = createAgentUiState();

  const route = await showAgentConsole(player, uiState);

  expect(route).toEqual({ panel: "main" });
  expect(uiState.history).toEqual([]);
  expect(player.messages).toContain("MCBE AI Agent: 消息不能为空。");
});
```

- [ ] **步骤 4：更新入口按钮路由测试**

保留关闭测试，新增设置和统计测试：

```ts
it("routes to settings from the main panel", async () => {
  __setNextCustomFormInteraction({
    clickButtonLabel: "设置",
    autoCloseAfterButtonClick: true,
  });

  const route = await showAgentConsole(createFakePlayer(), createAgentUiState());

  expect(route).toEqual({ panel: "settings" });
});

it("routes to stats from the main panel", async () => {
  __setNextCustomFormInteraction({
    clickButtonLabel: "统计信息",
    autoCloseAfterButtonClick: true,
  });

  const route = await showAgentConsole(createFakePlayer(), createAgentUiState());

  expect(route).toEqual({ panel: "stats" });
});
```

- [ ] **步骤 5：运行测试确认失败**

运行：

```bash
cd MCBE-AI-Agent-addon && pnpm test tests/ui/agent-console.test.ts
```

预期：FAIL，失败点包括仍路由旧 `chatInput`、缺少“发送”按钮行为或 `__getLastCustomForm` 相关断言不满足。

---

### 任务 3：实现实时主面板

**文件：**
- 修改：`MCBE-AI-Agent-addon/scripts/ui/state.ts:21-28`
- 修改：`MCBE-AI-Agent-addon/scripts/ui/panels/agentConsole.ts:1-74`

- [ ] **步骤 1：扩展 AgentUiState 运行期字段**

在 `AgentUiState` 中加入非持久化刷新回调：

```ts
export type AgentUiState = {
  bridgeStatus: ObservableLike<BridgeStatus>;
  lastPrompt: ObservableLike<string>;
  lastResponsePreview: ObservableLike<string>;
  history: HistoryItem[];
  settings: AgentUiSettings;
  stats: AgentUiStats;
  refreshConversation?: () => void;
};
```

`createAgentUiState()` 不需要设置该字段。

- [ ] **步骤 2：重写 agentConsole imports**

`agentConsole.ts` 需要 imports：

```ts
import type { Player } from "@minecraft/server";

import { sendUiChatMessage } from "../../bridge/toolPlayer";
import { buildAgentChatCommand } from "../commands";
import { appendHistoryItem, createHistoryId, summarizeHistoryItem } from "../history";
import {
  createCustomForm,
  createDduiObservable,
  showCustomFormSafely,
} from "../forms/formAdapter";
import type { AgentUiState } from "../state";
import { saveAgentUiState } from "../storage";
import { recordPromptSent, syncLocalHistoryCount } from "../stats";
import type { AgentPanelRoute } from "./routes";
import { CLOSE_ROUTE, MAIN_ROUTE } from "./routes";
```

- [ ] **步骤 3：实现主面板主体**

用以下结构替换 `showAgentConsole()`：

```ts
const CONVERSATION_PREVIEW_LIMIT = 10;

export async function showAgentConsole(
  player: Player,
  uiState: AgentUiState,
): Promise<AgentPanelRoute> {
  try {
    const summary = createDduiObservable(buildSummary(uiState));
    const conversationBody = createDduiObservable(buildConversationBody(uiState));
    const messageValue = createDduiObservable("");
    let nextRoute: AgentPanelRoute = MAIN_ROUTE;

    const refreshConversation = () => {
      summary.setData(buildSummary(uiState));
      conversationBody.setData(buildConversationBody(uiState));
    };
    uiState.refreshConversation = refreshConversation;

    const form = createCustomForm(player, "MCBE AI Agent")
      .closeButton()
      .label(summary)
      .divider()
      .label(conversationBody)
      .divider()
      .textField("消息内容", messageValue, { description: "发送后面板会保持打开" })
      .button("发送", () => {
        const message = messageValue.getData().trim();
        if (!message) {
          player.sendMessage("MCBE AI Agent: 消息不能为空。");
          return;
        }

        const now = Date.now();
        uiState.history = appendHistoryItem(
          uiState.history,
          {
            id: createHistoryId("ui", now),
            role: "user",
            content: message,
            createdAt: now,
            source: "ui",
          },
          uiState.settings.maxHistoryItems,
        );
        uiState.lastPrompt.setData(message);
        uiState.bridgeStatus.setData("connecting");
        uiState.stats = syncLocalHistoryCount(
          recordPromptSent(uiState.stats, now),
          uiState.history.length,
        );
        messageValue.setData("");
        refreshConversation();

        const command = buildAgentChatCommand(message);
        try {
          sendUiChatMessage(player.name, message);
          uiState.bridgeStatus.setData("sent");
          player.sendMessage("MCBE AI Agent: 消息已发送至 AI 服务。");
        } catch {
          uiState.bridgeStatus.setData("error");
          player.sendMessage(`MCBE AI Agent: 自动发送失败，请在聊天框手动发送：${command}`);
        }

        refreshConversation();
        const saveResult = saveAgentUiState(player, uiState);
        if (!saveResult.ok) {
          player.sendMessage("MCBE AI Agent: 保存历史失败，本次仅内存生效。");
        }
      })
      .button("设置", () => {
        nextRoute = { panel: "settings" };
        form.close();
      })
      .button("统计信息", () => {
        nextRoute = { panel: "stats" };
        form.close();
      })
      .button("关闭", () => {
        nextRoute = CLOSE_ROUTE;
        saveAgentUiState(player, uiState);
        form.close();
      });

    const shown = await showCustomFormSafely(player, form);
    if (!shown) {
      saveAgentUiState(player, uiState);
      return CLOSE_ROUTE;
    }

    if (nextRoute.panel !== "main") {
      uiState.refreshConversation = undefined;
    }
    return nextRoute;
  } catch {
    player.sendMessage("MCBE AI Agent: 表单暂时无法打开，请稍后再试。");
    saveAgentUiState(player, uiState);
    uiState.refreshConversation = undefined;
    return CLOSE_ROUTE;
  }
}
```

- [ ] **步骤 4：实现最近 10 条对话文本**

在 `agentConsole.ts` 底部加入：

```ts
function buildConversationBody(uiState: AgentUiState): string {
  const items = uiState.history.slice(-CONVERSATION_PREVIEW_LIMIT);
  if (items.length === 0) {
    return "暂无对话记录。输入消息开始聊天。";
  }

  return items
    .map((item) => summarizeHistoryItem(item, uiState.settings.responsePreviewLength))
    .join("\n");
}
```

保留并更新 `buildSummary()`：

```ts
function buildSummary(uiState: AgentUiState): string {
  return [
    `桥接状态: ${uiState.bridgeStatus.getData()}`,
    `历史条数: ${uiState.history.length}`,
    `发送次数: ${uiState.stats.sentCount}`,
  ].join("\n");
}
```

- [ ] **步骤 5：运行主面板测试确认通过**

运行：

```bash
cd MCBE-AI-Agent-addon && pnpm test tests/ui/agent-console.test.ts
```

预期：PASS。

---

### 任务 4：响应同步刷新主面板

**文件：**
- 创建：`MCBE-AI-Agent-addon/tests/bridge/response-sync.test.ts`
- 修改：`MCBE-AI-Agent-addon/scripts/bridge/responseSync.ts:114-130`

- [ ] **步骤 1：编写响应同步刷新测试**

创建 `MCBE-AI-Agent-addon/tests/bridge/response-sync.test.ts`：

```ts
import { beforeEach, describe, expect, it } from "vitest";
import {
  __emitScriptEvent,
  __resetMinecraftServerMock,
  __setMockPlayers,
} from "@minecraft/server";

import { AI_RESP_MESSAGE_ID } from "../../scripts/bridge/constants";
import {
  clearActiveUiState,
  registerResponseSyncHandler,
  setActiveUiState,
} from "../../scripts/bridge/responseSync";
import { createAgentUiState } from "../../scripts/ui/state";

const PLAYER_ID = "player-1";
const PLAYER_NAME = "TestPlayer";

describe("response sync", () => {
  beforeEach(() => {
    __resetMinecraftServerMock();
  });

  it("refreshes the active conversation when an assistant response completes", () => {
    const player = createFakePlayer();
    const uiState = createAgentUiState();
    let refreshCount = 0;
    uiState.refreshConversation = () => {
      refreshCount += 1;
    };

    __setMockPlayers([player]);
    setActiveUiState(PLAYER_ID, uiState);
    registerResponseSyncHandler();

    __emitScriptEvent({
      id: AI_RESP_MESSAGE_ID,
      message: JSON.stringify({
        id: "resp-1",
        i: 1,
        n: 1,
        p: PLAYER_NAME,
        r: "assistant",
        c: "你好，玩家",
      }),
    });

    expect(uiState.history).toHaveLength(1);
    expect(uiState.history[0]).toMatchObject({
      role: "assistant",
      content: "你好，玩家",
      source: "python",
    });
    expect(uiState.lastResponsePreview.getData()).toBe("你好，玩家");
    expect(refreshCount).toBe(1);

    clearActiveUiState(PLAYER_ID);
  });
});

function createFakePlayer() {
  const properties = new Map<string, unknown>();

  return {
    id: PLAYER_ID,
    name: PLAYER_NAME,
    getDynamicProperty(identifier: string) {
      return properties.get(identifier);
    },
    setDynamicProperty(identifier: string, value: unknown) {
      if (value === undefined) {
        properties.delete(identifier);
        return;
      }
      properties.set(identifier, value);
    },
  };
}
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```bash
cd MCBE-AI-Agent-addon && pnpm test tests/bridge/response-sync.test.ts
```

预期：FAIL，`refreshCount` 为 0。

- [ ] **步骤 3：实现刷新回调调用**

在 `responseSync.ts` 的 active state 分支末尾追加：

```ts
    activeState.refreshConversation?.();
```

位置在 assistant 预览更新之后、持久化之前：

```ts
    if (role === "assistant") {
      const previewLength = activeState.settings.responsePreviewLength;
      activeState.lastResponsePreview.setData(
        text.length > previewLength ? `${text.slice(0, previewLength)}...` : text,
      );
    }

    activeState.refreshConversation?.();
```

- [ ] **步骤 4：运行测试确认通过**

运行：

```bash
cd MCBE-AI-Agent-addon && pnpm test tests/bridge/response-sync.test.ts
```

预期：PASS。

---

### 任务 5：设置页新增清空历史

**文件：**
- 修改：`MCBE-AI-Agent-addon/tests/ui/settings-panel.test.ts`
- 修改：`MCBE-AI-Agent-addon/scripts/ui/panels/settingsPanel.ts:1-83`

- [ ] **步骤 1：编写清空历史失败测试**

在 `settings-panel.test.ts` 增加：

```ts
it("clears local history from settings", async () => {
  __setNextCustomFormInteraction({
    clickButtonLabel: "清空历史",
    autoCloseAfterButtonClick: true,
  });

  const player = createFakePlayer();
  const uiState = createAgentUiState({
    history: [
      {
        id: "history-1",
        role: "user",
        content: "hello",
        createdAt: 1,
        source: "ui",
      },
    ],
    stats: { localHistoryCount: 1 },
    lastPrompt: "hello",
    lastResponsePreview: "world",
  });

  const route = await showSettingsPanel(player, uiState);

  expect(route).toEqual({ panel: "main" });
  expect(uiState.history).toEqual([]);
  expect(uiState.stats.localHistoryCount).toBe(0);
  expect(uiState.lastResponsePreview.getData()).toBe("");
  expect(player.messages).toContain("MCBE AI Agent: 本地聊天记录已清空。");

  const persisted = JSON.parse(String(player.getDynamicProperty(AGENT_UI_STATE_PROPERTY_KEY)));
  expect(persisted.history).toEqual([]);
  expect(persisted.stats.localHistoryCount).toBe(0);
});
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```bash
cd MCBE-AI-Agent-addon && pnpm test tests/ui/settings-panel.test.ts
```

预期：FAIL，找不到“清空历史”按钮行为。

- [ ] **步骤 3：实现清空历史按钮**

在 `settingsPanel.ts` 增加 import：

```ts
import { clearHistory } from "../history";
```

在 `.button("保存", ...)` 后追加按钮：

```ts
      .button("清空历史", () => {
        uiState.history = clearHistory(uiState.history);
        uiState.stats = syncLocalHistoryCount(uiState.stats, 0);
        uiState.lastResponsePreview.setData("");
        saveAgentUiState(player, uiState);
        didSave = true;
        player.sendMessage("MCBE AI Agent: 本地聊天记录已清空。");
        form.close();
      });
```

- [ ] **步骤 4：运行设置页测试确认通过**

运行：

```bash
cd MCBE-AI-Agent-addon && pnpm test tests/ui/settings-panel.test.ts
```

预期：PASS。

---

### 任务 6：移除旧路由和旧面板

**文件：**
- 修改：`MCBE-AI-Agent-addon/scripts/ui/panels/routes.ts`
- 修改：`MCBE-AI-Agent-addon/scripts/ui/entry.ts`
- 删除：`MCBE-AI-Agent-addon/scripts/ui/panels/chatInput.ts`
- 删除：`MCBE-AI-Agent-addon/scripts/ui/panels/historyPanel.ts`
- 删除：`MCBE-AI-Agent-addon/tests/ui/history-panel.test.ts`

- [ ] **步骤 1：简化 route 类型**

将 `routes.ts` 改为：

```ts
export type AgentPanelRoute =
  | { panel: "main" }
  | { panel: "settings" }
  | { panel: "stats" }
  | { panel: "close" };

export const MAIN_ROUTE: AgentPanelRoute = { panel: "main" };
export const CLOSE_ROUTE: AgentPanelRoute = { panel: "close" };
```

- [ ] **步骤 2：移除 entry 旧 imports 和 switch 分支**

从 `entry.ts` 删除：

```ts
import { showChatInputPanel } from "./panels/chatInput";
import { showHistoryPanel } from "./panels/historyPanel";
```

从 switch 删除：

```ts
        case "chatInput":
          route = await showChatInputPanel(player, uiState);
          break;
        case "history":
          route = await showHistoryPanel(player, uiState, route.pageIndex ?? 0);
          break;
```

- [ ] **步骤 3：删除旧面板和旧测试**

删除文件：

```bash
rm "MCBE-AI-Agent-addon/scripts/ui/panels/chatInput.ts" \
   "MCBE-AI-Agent-addon/scripts/ui/panels/historyPanel.ts" \
   "MCBE-AI-Agent-addon/tests/ui/history-panel.test.ts"
```

- [ ] **步骤 4：搜索旧路由引用**

运行：

```bash
grep -R "chatInput\|historyPanel\|showChatInputPanel\|showHistoryPanel\|聊天记录\|发送消息" MCBE-AI-Agent-addon/scripts MCBE-AI-Agent-addon/tests --include='*.ts'
```

预期：没有旧面板/旧路由引用。允许 `history` 作为历史数据模型字段存在。

- [ ] **步骤 5：运行相关测试**

运行：

```bash
cd MCBE-AI-Agent-addon && pnpm test tests/ui/agent-console.test.ts tests/ui/settings-panel.test.ts tests/ui/agent-ui-state.test.ts
```

预期：PASS。

---

### 任务 7：完整验证

**文件：**
- 修改：按前述任务实际变更的文件

- [ ] **步骤 1：运行全部测试**

运行：

```bash
cd MCBE-AI-Agent-addon && pnpm test
```

预期：所有测试 PASS。

- [ ] **步骤 2：运行构建**

运行：

```bash
cd MCBE-AI-Agent-addon && pnpm build
```

预期：构建成功，无 TypeScript 编译错误。

- [ ] **步骤 3：检查 git diff**

运行：

```bash
git diff -- MCBE-AI-Agent-addon docs/superpowers
```

预期：diff 只包含 DDUI 实时主面板、设置清空历史、响应刷新、测试和计划/规格文档相关变更。

---

## 自检结果

- 规格覆盖度：目标、非目标、主面板结构、发送行为、实时响应、最近 10 条、设置页清空历史、统计页独立、路由调整、错误处理、测试计划和验收标准均有对应任务。
- 占位符扫描：计划中不包含 TODO、待定或未定义的占位步骤。
- 类型一致性：计划统一使用 `AgentUiState.refreshConversation?: () => void`、`conversationBody`、`messageValue`、`AgentPanelRoute` 的 `main/settings/stats/close` 四种路由。
- 用户约束：计划不包含 git commit 步骤。