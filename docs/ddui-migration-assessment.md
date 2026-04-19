# DDUI 迁移评估

## 评估结论

当前 `MCBE-AI-Agent-addon` 适合先做「渐进式迁移准备」，不建议立即整体迁移到真正的 DDUI。

主要原因是 DDUI 目前依赖 `@minecraft/server-ui@2.1.0-beta` 中的 `CustomForm`、`MessageBox` 和 `Observable`，同时要求 `@minecraft/server@2.8.0-beta`。按照 Minecraft Script API 版本规则，使用 `-beta` 模块需要在世界中开启 `Beta APIs` 实验开关。直接迁移会把 addon 从稳定 API 轨道切到 beta API 轨道，影响使用门槛和兼容性。

更稳妥的路线是：

1. 保持当前稳定版本依赖，先补齐传统表单 UI 的可用能力。
2. 通过表单适配层隔离 `ActionFormData`、`ModalFormData` 和未来的 `CustomForm`。
3. 在独立 beta 分支或 beta 构建产物中试点 DDUI，确认运行环境、类型声明和游戏内行为都可用后再扩大迁移范围。

## 官方文档依据

本次评估参考了以下官方文档：

- [DDUI 入门](https://learn.microsoft.com/en-us/minecraft/creator/documents/scripting/intro-to-ddui?view=minecraft-bedrock-stable)
- [`@minecraft/server-ui` 模块文档](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server-ui/minecraft-server-ui?view=minecraft-bedrock-stable)
- [`@minecraft/server-ui` changelog](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server-ui/changelog?view=minecraft-bedrock-stable)
- [`CustomForm` API 文档](https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server-ui/customform?view=minecraft-bedrock-experimental)
- [Script Module Versioning](https://learn.microsoft.com/en-us/minecraft/creator/documents/scripting/versioning?view=minecraft-bedrock-stable)

关键结论：

- `@minecraft/server-ui@2.0.0` 是当前稳定轨道的 manifest 版本。
- `CustomForm`、`MessageBox`、`Observable` 在 `@minecraft/server-ui@2.1.0-beta` 中加入。
- `@minecraft/server-ui@2.1.0-beta` 要求 peer dependency `@minecraft/server@2.8.0-beta`。
- 使用 `-beta` 版本 API 需要开启 `Beta APIs` 实验开关。
- `CustomForm` 文档仍标注为预发布能力，签名或行为后续可能变化。

## 当前 addon 基线

当前 addon 位于 `MCBE-AI-Agent-addon/`，核心结构如下：

```text
MCBE-AI-Agent-addon/
  behavior_packs/MCBE-AI-Agent/manifest.json
  resource_packs/MCBE-AI-Agent/manifest.json
  scripts/main.ts
  scripts/bootstrap.ts
  scripts/bridge/
  scripts/ui/
  tests/
```

入口链路：

```text
scripts/main.ts
  -> initializeAddon()
  -> initializeToolPlayer()
  -> registerBridgeRouter()
  -> registerUiEntry()
```

当前 manifest 依赖：

```json
{
  "module_name": "@minecraft/server",
  "version": "2.0.0"
}
```

```json
{
  "module_name": "@minecraft/server-ui",
  "version": "2.0.0"
}
```

当前 npm 依赖同样固定在稳定 UI 轨道：

```json
{
  "@minecraft/server": "^2.0.0",
  "@minecraft/server-ui": "^2.0.0"
}
```

本地 `node_modules/@minecraft/server-ui/index.d.ts` 只包含传统表单能力：

- `ActionFormData`
- `ModalFormData`
- `MessageFormData`
- 对应的 response 类型

本地类型声明中没有 `CustomForm`、`MessageBox` 和 `Observable`。因此，如果现在直接引入官方 DDUI 示例代码，TypeScript 编译会失败。

## 当前 UI 层状态

当前 UI 层处于骨架阶段：

- `scripts/ui/state.ts` 定义了自定义 `ObservableLike<T>`，用于模拟响应式状态接口。
- `scripts/ui/panels/agentConsole.ts` 使用 `ActionFormData` 和 `ModalFormData` 展示最小面板。
- `scripts/ui/entry.ts` 暂未注册聊天入口命令，只保留了 `openAgentUi(player)`。

这说明项目尚未依赖真正 DDUI，但已经预留了迁移边界。`ObservableLike<T>` 是一个有价值的过渡抽象，后续可以替换为官方 `Observable`，也可以继续作为业务状态接口，避免页面代码直接绑定具体 UI 实现。

## 桥接协议影响

当前 Python 与 addon 的桥接协议和 UI 层基本解耦。

现有协议链路：

```text
Python Agent Tool
  -> AddonBridgeService
  -> scriptevent mcbeai:bridge_request <json>
  -> Addon scriptEventReceive
  -> capability handler
  -> MCBEAI_TOOL 模拟玩家聊天分片
  -> WebSocket PlayerMessage
  -> Python 分片重组与 future 唤醒
```

现有能力：

- `get_player_snapshot`
- `get_inventory_snapshot`
- `find_entities`
- `run_world_command`

DDUI 迁移不会直接破坏这些桥接能力。风险主要集中在 UI 表单 API、manifest 版本、运行环境实验开关，以及未来如果要让 UI 主动触发 Python 聊天请求，需要新增或复用一条明确的 UI 事件通道。

## 适合迁移的部分

以下部分适合优先做迁移准备：

- UI 状态层：保留 `ObservableLike<T>` 或抽象出更明确的 `UiSignal<T>`，未来再映射到官方 `Observable`。
- 表单创建层：新增 `forms/formAdapter.ts`，统一封装主菜单、输入表单、设置表单和提示消息。
- 入口层：在 `registerUiEntry()` 中注册 `AGENT UI`、`AGENT 面板` 等入口命令，并使用 `system.run()` 避免 read-only mode 中直接打开表单。
- 持久化层：使用玩家维度的 dynamic property 保存 UI 设置、历史摘要和统计数据。
- 面板业务层：先用稳定的 `ActionFormData` / `ModalFormData` 做可运行 MVP，保持面板逻辑不依赖具体表单类。

## 不适合立即迁移的部分

以下部分不建议现在直接迁移到 DDUI：

- 不建议直接把所有面板改成 `CustomForm`。
- 不建议在主分支直接切换到 `@minecraft/server-ui@2.1.0-beta`。
- 不建议把 UI 功能补齐、DDUI API 迁移、Python 事件协议扩展放在同一轮完成。
- 不建议在未完成游戏内验证前依赖 `CustomForm` 的客户端写回能力。

这样做会把多个不确定因素叠加在一起：beta API、实验开关、类型声明、游戏运行时兼容性、UI 功能本身的交互设计，以及 Python 与 addon 的新事件协议。

## 迁移方案对比

### 方案 A：立即整体切换到 DDUI

做法：

- manifest 切到 `@minecraft/server@2.8.0-beta` 和 `@minecraft/server-ui@2.1.0-beta`。
- npm 依赖同步切到 beta 版本。
- UI 面板直接改用 `CustomForm` 和 `Observable`。

优点：

- 可以最快使用官方 DDUI 能力。
- 状态绑定和表单交互模型更接近最终形态。

缺点：

- 必须开启 `Beta APIs`。
- 运行环境要求更高，用户导入和联调成本上升。
- 本地当前类型声明不支持，直接实现会先遇到编译问题。
- 预发布 API 后续可能变化，维护风险较高。

评估：不推荐作为当前主线方案。

### 方案 B：稳定表单 MVP 加适配层

做法：

- 保持 `@minecraft/server-ui@2.0.0`。
- 使用 `ActionFormData` 和 `ModalFormData` 完成游戏内聊天面板 MVP。
- 新增表单适配层，让业务面板不直接依赖具体表单类。
- 保留 `ObservableLike<T>`，后续再映射到官方 `Observable`。

优点：

- 不要求用户开启 `Beta APIs`。
- 当前环境可以编译和测试。
- 迁移风险低，能先验证真实 UI 需求和玩家交互路径。
- 后续切 DDUI 时改动集中在适配层。

缺点：

- 不能立即获得 DDUI 的响应式表单能力。
- 传统表单体验有限，复杂设置页和实时状态展示会受限制。

评估：推荐作为当前主线方案。

### 方案 C：双轨构建

做法：

- 主线保留 stable addon。
- 新建 beta/DDUI 分支或 beta manifest 构建产物。
- beta 产物只迁移设置页或独立实验页，用于验证 `CustomForm` 和 `Observable`。

优点：

- 可以并行验证 DDUI，不影响稳定用户。
- 便于收集真实运行时问题。
- 后续如果 DDUI 稳定，可以逐步合并。

缺点：

- 构建、文档和测试矩阵更复杂。
- 需要明确区分 stable 包和 beta 包，避免用户误用。

评估：适合在稳定表单 MVP 完成后启动。

## 推荐迁移路线

推荐采用「方案 B + 后续方案 C」。

第一阶段：稳定表单 MVP。

- 注册 UI 入口命令，例如 `AGENT UI`、`AGENT 面板`。
- 使用 `ActionFormData` 实现主面板。
- 使用 `ModalFormData` 实现消息输入和设置表单。
- 增加聊天历史、统计信息、设置持久化。
- 按玩家隔离 UI 状态，避免多玩家共享 `lastPrompt` 和历史记录。
- 保持旧的 `AGENT 聊天 <消息>` 等命令不受影响。

第二阶段：抽象 UI 适配层。

- 新增 `scripts/ui/forms/formAdapter.ts`。
- 让面板代码调用适配层，而不是直接 new `ActionFormData` 或 `ModalFormData`。
- 统一封装取消、玩家忙碌、表单错误、返回主菜单等行为。
- 为后续 DDUI 实现预留相同接口。

第三阶段：beta/DDUI 试点。

- 新建 beta 分支或 beta 构建配置。
- 将 manifest 和 npm 依赖切到 beta 模块版本。
- 开启 `Beta APIs` 的世界中验证。
- 优先迁移设置页，因为设置页最能体现 `Observable` 的客户端写回价值。
- 主面板和历史页可以继续使用传统表单，除非 DDUI 能明显改善体验。

第四阶段：是否扩大迁移。

满足以下条件后，再考虑扩大 DDUI 覆盖范围：

- 本地类型声明包含 `CustomForm`、`MessageBox` 和 `Observable`。
- `npm run build` 在 beta 分支通过。
- 游戏内确认 `CustomForm.create()` 可用。
- `Observable.create(..., { clientWritable: true })` 的写回行为稳定。
- stable MVP 已经完成并通过游戏内联调。
- 已明确 stable 包和 beta 包的安装说明。

## 需要修改的关键文件

第一阶段预计涉及：

- `MCBE-AI-Agent-addon/scripts/ui/entry.ts`
- `MCBE-AI-Agent-addon/scripts/ui/state.ts`
- `MCBE-AI-Agent-addon/scripts/ui/panels/agentConsole.ts`
- `MCBE-AI-Agent-addon/scripts/ui/commands.ts`
- `MCBE-AI-Agent-addon/scripts/ui/storage.ts`
- `MCBE-AI-Agent-addon/scripts/ui/history.ts`
- `MCBE-AI-Agent-addon/scripts/ui/stats.ts`
- `MCBE-AI-Agent-addon/scripts/ui/forms/formAdapter.ts`
- `MCBE-AI-Agent-addon/scripts/ui/panels/chatInput.ts`
- `MCBE-AI-Agent-addon/scripts/ui/panels/historyPanel.ts`
- `MCBE-AI-Agent-addon/scripts/ui/panels/settingsPanel.ts`
- `MCBE-AI-Agent-addon/scripts/ui/panels/statsPanel.ts`

beta/DDUI 试点预计涉及：

- `MCBE-AI-Agent-addon/behavior_packs/MCBE-AI-Agent/manifest.json`
- `MCBE-AI-Agent-addon/package.json`
- `MCBE-AI-Agent-addon/pnpm-lock.yaml`
- `MCBE-AI-Agent-addon/scripts/ui/forms/formAdapter.ts`
- 试点页面，例如 `settingsPanel.ts`

## 主要风险

### DDUI 类型不可用

当前本地 `@minecraft/server-ui@2.0.0` 类型声明没有 `CustomForm` 和 `Observable`。直接引入 DDUI 会编译失败。

处理方式：主线不直接引用 DDUI 类型。需要试点时放到 beta 分支，并同步升级依赖。

### Beta APIs 门槛

DDUI 所需 beta 模块需要开启 `Beta APIs`。这会提高安装、联调和用户使用门槛。

处理方式：stable 包继续走传统表单。beta 包单独说明前提条件。

### 表单 read-only mode 限制

`ActionFormData.show()`、`ModalFormData.show()` 不能在 read-only mode 调用。聊天入口如果挂在 `world.beforeEvents.chatSend`，必须延迟到 `system.run()` 中打开表单。

处理方式：入口事件只负责匹配命令和 `event.cancel = true`，实际打开 UI 放到 `system.run()`。

### UI 发送消息链路不确定

Script API 不一定能伪造真实玩家聊天消息并触发 Python WebSocket 的 `PlayerMessage` 链路。

处理方式：第一阶段先验证最小路径。如果不可行，新增 UI 专用事件协议，例如 `mcbeai:ui_event`，由 Python 显式识别 UI 请求。

### 多玩家状态串扰

当前 `entry.ts` 使用模块级 `uiState`，多个玩家会共享最近问题和状态。

处理方式：迁移 UI MVP 时改为按玩家维度管理状态，key 可以使用 `player.id` 或稳定的玩家标识，并配合 player dynamic property 持久化。

### GameTest beta 依赖已存在

当前 manifest 已依赖 `@minecraft/server-gametest@1.0.0-beta` 用于模拟玩家桥接回传。这说明项目已经存在部分 beta 依赖，但 UI 主依赖仍是 stable。

处理方式：不要因为已有 GameTest beta 就默认可以整体切到 DDUI beta。DDUI 迁移仍需要单独验证 `server`、`server-ui`、世界实验开关和目标运行时。

## 验证记录

当前环境验证结果：

```bash
cd MCBE-AI-Agent-addon && npm run build
```

结果：通过。

```bash
cd MCBE-AI-Agent-addon && npm test
```

结果：通过，5 个测试文件、14 个测试全部通过。

额外说明：

- 当前机器没有可直接执行的 `pnpm` 命令，因此使用 `npm` 执行等价脚本。
- 曾尝试 `npm test -- --runInBand`，失败原因是 Vitest 不支持该参数，不是项目测试失败。

## 最终建议

短期不要直接把现有 addon 整体迁到 DDUI。当前最优先的工作是完成稳定 API 下的游戏内聊天面板 MVP，并把 UI 表单能力封装到适配层。

中期可以建立 beta/DDUI 试点分支，只迁移设置页或独立实验页，用于验证 `CustomForm`、`Observable` 和 `Beta APIs` 运行条件。

长期等 DDUI API 稳定、本地类型声明可用、游戏内联调通过后，再决定是否把主面板、历史页和设置页逐步迁移到真正 DDUI。
