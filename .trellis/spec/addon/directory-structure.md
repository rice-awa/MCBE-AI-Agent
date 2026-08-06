# Addon 目录与模块边界

## 当前布局

```text
MCBE-AI-Agent-addon/
├── scripts/
│   ├── main.ts                  # 入口：早期注册 + 世界加载后初始化
│   ├── bootstrap.ts             # 生命周期分段和幂等初始化
│   ├── bridge/
│   │   ├── router.ts             # mcbews:bridge_req 路由
│   │   ├── chunking.ts           # bridge/UI 聊天分片格式化
│   │   ├── responseSync.ts       # mcbews:text_resp 重组与玩家 UI 更新
│   │   ├── toolPlayer.ts         # 模拟玩家回传 bridge 结果
│   │   └── capabilities/         # 玩家、世界、实体、方块能力
│   └── ui/
│       ├── entry.ts              # UI 注册与玩家生命周期
│       ├── state.ts              # AgentUiState 与默认值
│       ├── storage.ts             # DynamicProperty 读写/归一化
│       ├── history.ts             # 历史分页、截断和展示
│       └── panels/                # 具体 UI panel 和路由
├── tests/
│   ├── __mocks__/                 # Minecraft Server / UI / GameTest mock
│   ├── bridge/                    # bridge、能力和分片测试
│   └── ui/                        # UI state/history/command 测试
├── behavior_packs/                # 行为包清单和资源
├── resource_packs/                # 资源包清单和资源
├── package.json / pnpm-lock.yaml  # pnpm 依赖与命令
└── dist/ / lib/                   # 构建产物，不直接编辑
```

## 新增代码放置规则

- 新的 script event 能力在 `scripts/bridge/capabilities/` 单独建模块，并由 `scripts/bridge/router.ts` 的 capability map 接入；方块相关逻辑继续按 `blocks/` 的 inspect、snapshot、protect、repair、place/fill/batch 等职责拆分。
- 请求/响应的协议常量统一放 `scripts/bridge/constants.ts`；不要在 router、toolPlayer 和 responseSync 中各写一份字符串。
- 纯字符串分片、历史分页、参数归一化等无世界依赖逻辑应保持纯函数，便于在 `tests/bridge` 或 `tests/ui` 直接测试。
- UI panel 通过 `ui/state.ts`、`ui/history.ts` 和 `ui/storage.ts` 访问状态；不要让 panel 直接重复解析 DynamicProperty JSON。
- `scripts/bootstrap.ts` 负责世界加载前后的生命周期分界。早期初始化只注册事件，不读取玩家、维度或实体；需要 world 状态的代码放到 world load / `system.run` 之后。

新增源文件要同步检查 `just.config.ts` 的 entry、bundle、copy 和 clean 路径；不要把 `lib/`、`dist/`、`node_modules/` 当作源代码修改。
