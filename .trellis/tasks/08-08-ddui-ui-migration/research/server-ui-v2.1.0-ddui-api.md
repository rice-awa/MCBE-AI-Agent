# @minecraft/server-ui v2.1.0 DDUI API 备忘（来自官方类型声明/文档）

> 来源：npm `@minecraft/server-ui@2.1.0`（stable，随 Minecraft 1.26.30 发布）类型声明；官方 intro-to-ddui 文档；Update1.26.30 发布说明。firecrawl 抓取日期 2026-08-08。

## v2.1.0 相对 2.1.0-beta 的破坏性变更

1. **`Observable<T>` 泛型类被拆分**为具体类，且 `Observable.create` 工厂被移除：
   - `ObservableString`
   - `ObservableNumber`
   - `ObservableBoolean`
   - `ObservableUIRawMessage`
   - 统一构造函数：`new ObservableString(initialValue, options?)`，`options: { clientWritable?: boolean }`。
2. **`DropdownItem` → `DropdownItemData`**。
3. **`DataDrivenScreenClosedReason` 枚举值改名**：
   - `UserClose` → `ClientClosed`
   - `ServerClose` → `ServerClosed`
   - `UserBusy` 不变
4. **`CustomForm` 构造函数**：`new CustomForm(player, title)`；`title` 可为 `string | UIRawMessage | ObservableString | ObservableUIRawMessage`。

## 各 Observable 类型公开方法（契约）

- `getData(): T`
- `setData(value: T): void`（值变化时通知所有订阅者）
- `subscribe(callback: (v: T) => void): (v: T) => void`（返回解绑句柄）
- `unsubscribe(callback): boolean`

> 与 beta 旧 `DduiObservable<T>` 的 `getData`/`setData` 契约一致，面板代码仅需改类型标注。

## CustomForm 链式方法（与 beta 用法兼容）

- `.title(...)`、`.closeButton()`、`.label(...)`、`.spacer()`、`.divider()`、`.header(...)`
- `.textField(label, text: ObservableString, opts?)`
- `.toggle(label, value: ObservableBoolean, opts?)`
- `.slider(label, value: ObservableNumber, min, max, opts?)`
- `.dropdown(label, value: ObservableNumber, options: DropdownItemData[])`
- `.button(label, callback)` —— 回调立即执行，表单保持打开
- `.show(): Promise<unknown>` —— 返回关闭原因（含 `DataDrivenScreenClosedReason` 或 string）
- `uiManager.closeAllForms(player)`：`uiManager` 是模块级单例导出。

## show() 关闭结果处理（官方推荐）

```ts
new CustomForm(player, "Settings").textField(...).show()
  .then(showResult => {
    if (showResult === DataDrivenScreenClosedReason.UserBusy) { ... }
  });
```

DDUI 表单在玩家有其他 UI 打开时返回 `UserBusy`（不会弹出）。beta 的 `showCustomFormSafely` 已处理此场景。

## 依赖版本搭配

- `@minecraft/server-ui@2.1.0` 与 `@minecraft/server@2.8.0` 随 1.26.30 一起发布（stable），无需 preview 客户端。
- manifest `dependencies` 模块版本需与 package.json 一致：`@minecraft/server` → `2.8.0`、`@minecraft/server-ui` → `2.1.0`。

## 关于 dduiSessionStart

- 在 npm `@minecraft/server-ui` 的 2.1.0 / 2.2.0-beta.1.26.43-stable / 2.3.0-beta.1.26.50-preview.24 以及 `@minecraft/server` 2.8.0 类型声明中均未检索到 `dduiSessionStart` 符号。官方 ScriptAPI 索引也未收录。
- 结论：本移植不依赖该 hook；如运行期确有此事件，属 preview 客户端专属，另行单独验证。不阻塞本任务。
