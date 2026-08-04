# 方块工具单一职责契约设计（fill / place / inspect）

**日期：** 2026-08-04
**状态：** 设计已确认，待用户评审
**范围：** `inspect_block` / `edit_blocks` 重构为 `inspect_block` / `place_block` / `fill_block`，宿主桥接、审批恢复、结果投影、运行时 Harness 提示同步调整
**约束：** 本轮不修改 Add-on 二进制协议；`commandLine` 461 字节硬预算不变；多人身份仍只使用 `event.sender` / 显式 `player_name`；高风险写入仍走 preflight → 审批 → execute

## 1. 背景与证据

### 1.1 用户可观察现象

2026-08-04 一次「造个小房子」请求（run `15492746`，`logs/app.log`）：

- 12 次模型请求、11 次工具调用（1 次 inspect + 10 次 `edit_blocks`），总耗时 42.3 秒；
- 累计 input tokens 152,495（缓存命中约 150,016）；
- 3 次 `LIMIT_EXCEEDED` 重试（12 个离散点 777B > 461B；两次门+states 约 510B）；
- 1 次 `UNSUPPORTED_BLOCK_PLACEMENT`（门）；
- 一堵 5×3 的墙被拆成 y=105/106/107/108 四次调用，每次 4–5 个 edits；
- 每个 edit 独立走 2 次桥接往返（preflight + execute），1 个 edit = 2 个请求，5 个 edits = 10 个请求。

其它日期证据：

- 2026-08-02：一次 7 个 edits 的调用产生 malformed JSON（`target.target` 嵌套 + 末尾多 `}`），整轮失败；
- 2026-08-03：审批恢复时 harness 注入隐藏参数 `status`，与工具签名不匹配抛 `TypeError`，连续两个 run 变成 `STATE_UNKNOWN`，随后命令回退被拒绝；
- 跨轮上下文固定开销：连续三句对话 input tokens 9,057 → 18,463 → 29,034；
- inspect 结果偏胖：25 格区域返回 1,624 字符、242 格返回 1,602 字符（8 个全字段样本）。

### 1.2 量化对照（宿主预算估算函数实测）

| 形态 | 单点参数 | 12 点 batch 出站帧 | 4 个柱形 fill |
| --- | --- | --- | --- |
| 现状（对象坐标 + edits 列表） | 107 字符 | 654–777 字节（超限） | 1,484 字节（4 帧） |
| 方案 A（`[x,y,z]` + 单操作工具） | 64 字符 | 不再可表达（无 batch） | 4 × ~350 字节（均合规） |

新工具 schema 估算（含简短描述）：`place_block` ~533 字符、`fill_block` ~505 字符、`inspect_block` ~175 字符；旧 `BlockEdit` 单模型 schema 即 2,271 字符。

## 2. 目标与非目标

### 2.1 目标

1. 模型可见契约收敛为三个单一职责工具：`place_block` / `fill_block` / `inspect_block`。
2. 坐标统一为绝对坐标数组 `[x, y, z]`；不再暴露 `positions` / `box` / `mode` / `coordinate_mode` 等内部概念。
3. 一个工具调用只处理一个任务；模型可在同一轮并行发出多个独立调用（保留现有并行能力）。
4. 消除 `LIMIT_EXCEEDED` 的模型侧重试路径：模型不再能表达超预算的离散点位 batch。
5. 审批恢复不再回灌 Python 隐藏 kwargs（`locked_targets` / `phase` / `status` 等），改用 `plan_id` 内部执行。
6. 结果投影瘦身：place / fill / inspect 只返回决策字段；样本使用 `[x,y,z,type_id]` 紧凑格式。
7. 提示词与 schema 去重：`_BLOCK_TOOL_PRIORITY`、工具卡片、docstring 各写一次职责，不互相复制恢复长文。

### 2.2 非目标

- 不修改 Add-on 桥接能力与 `commandLine` 预算。
- 不做建筑 DSL（墙、屋顶、房间等高级抽象）。
- 专用工具不实现多格方块（门、床等）放置；命令回退由 `setblock` / `fill` 承担（Bedrock 1.26.10+ 完整放置双格结构，见 `docs/sapi/block-operations.md` §5.5）。
- 本轮不支持玩家相对坐标（`forward/right/up`）；如后续需要，以独立 `relative` 参数追加，不混入坐标数组。
- 不自动把 `expect=air` 升级为 `any`，不绕过审批。
- 不保留 `edit_blocks` 的模型可见入口（迁移策略见 §10）。

## 3. 工具契约

### 3.1 `place_block`

```text
place_block(pos: [x, y, z], block: str, expect: "air" | "any" | type_id = "air", states?: dict)
```

- 语义：在单个格子上写入方块。
- `pos` 必须恰好 3 个整数；非法长度由宿主返回 `INVALID_COORDINATE`。
- `expect` 默认 `"air"`（仅替换空气）；`"any"` 允许覆写普通方块（需审批）；type_id 为精确前置条件。
- `states` 可选，仅当需要 block states（如楼梯朝向）时提供。
- 宿主映射为 Add-on `mode=place` 帧，线协议字段不变。

### 3.2 `fill_block`

```text
fill_block(from: [x, y, z], to: [x, y, z], block: str, expect: "air" | "any" | type_id = "air", states?: dict)
```

- 语义：在长方体区域内写入方块；角点自动 min/max 归一化。
- `expect=air` 时过滤语义：非空气格跳过并在结果中报告 `skipped`；匹配数为 0 且非 no-op 时返回 `PRECONDITION_FAILED`。
- 宿主映射为 Add-on `mode=fill` 帧；体积超 `max_fill_volume`（默认 4096）时返回 `LIMIT_EXCEEDED` 并给出建议的缩小方向。
- Add-on 已按 `cells_per_tick` 内部切片，宿主无需按格子分片。

### 3.3 `inspect_block`

```text
inspect_block(target: [x, y, z] | [[x1,y1,z1], [x2,y2,z2]])
```

- 单点：`[x, y, z]`；区域：两个角点数组组成的嵌套数组。
- 区域返回有界摘要（`type_counts` + ≤8 个样本），不返回全量枚举。
- 无 `dimension` 参数：默认当前事件玩家维度，沿用现有 `current_player_dimension_default` 修复。

### 3.4 统一约束

- 所有坐标均为绝对世界坐标，整数 `[x, y, z]`。
- 所有 `block` / `expect` 为字符串 type_id，缺失 `minecraft:` 命名空间时宿主归一化。
- 不再暴露：`edits`、`dimension`、`mode`、`positions`、`coordinate_mode`、`locked_targets`、`locked_targets_by_edit`、`noop_edit_indices`、`repairs_applied`、`phase`、`status`。

## 4. 宿主执行路径

### 4.1 preflight → 审批 → execute（保持不变）

1. `HarnessToolset.call_tool` 对三个新工具执行 preflight（映射为 Add-on preflight 帧）。
2. 审批仍按风险（HIGH）进行，一次工具调用 = 一个审批单元；并行待审批调用继续聚合为同一批（现有 `AGENT 同意` 批量语义不变）。
3. execute 只允许来自审批恢复路径。

### 4.2 恢复路径：`plan_id` 取代隐藏 kwargs

现状问题：审批恢复通过 `super().call_tool(name, execute_args, ...)` 把 `locked_targets` / `phase` / `status` 等内部字段回灌进 Python 函数签名，导致 8/3 的 `TypeError` 与 schema 剥离补丁（`strip_block_internal_tool_schema`）。

新设计：

- preflight 成功后，宿主生成 `plan_id` 并存入 `preflight_cache`（`BlockPreflightPlan` 增加 `plan_id` 字段）。
- 审批记录只保存 `plan_id` + `execution_args_hash`；恢复时调用内部执行函数 `execute_block_plan(plan_id)`，不再调用公开工具函数。
- 公开工具函数签名只包含模型可见参数；`strip_block_internal_tool_schema` 对新工具不再需要（可删除）。
- `project_block_execute_args` / `_python_tool_args` 的 legacy 分支随旧契约删除。

### 4.3 预算防御

- 新契约下模型无法表达离散点 batch，`commandLine` 超预算结构性消失。
- 宿主保留 `check_bridge_command_line_budget` 作为防御：若单帧仍超预算（如超长 states），返回 `LIMIT_EXCEEDED` + 固定 hint，不发送。
- fill 体积上限仍由 `max_fill_volume` 强制，Add-on 内部按 `cells_per_tick` 切片。

## 5. 结果投影

### 5.1 place 成功

```json
{"ok": true, "status": "applied", "at": [-503, 105, -172], "block": "minecraft:torch", "was": "minecraft:air"}
```

`was` 仅当替换了非空气方块时出现。

### 5.2 fill 成功

```json
{"ok": true, "status": "applied", "changed": 15, "skipped": 10, "type_counts": {"minecraft:grass_block": 10}, "bounds": [[-505, 105, -174], [-501, 107, -174]]}
```

`type_counts` 仅含非空气跳过/替换计数；全空气时不出现该字段。

### 5.3 inspect

单点：

```json
{"ok": true, "block": "minecraft:air", "states": {}, "waterlogged": false, "is_air": true, "is_liquid": false}
```

区域：

```json
{"ok": true, "count": 25, "type_counts": {"minecraft:air": 22, "minecraft:oak_log": 3}, "samples": [[-505, 104, -174, "minecraft:air"], "..."]}
```

### 5.4 失败

统一：`{schema_version, ok: false, code, message, retryable, fallback_allowed, hint}`；`LIMIT_EXCEEDED` 额外带 `estimated_bytes/budget`；`PRECONDITION_FAILED` 额外带 `actual_type_id`（仅首格）与坐标。不镜像 Add-on 原始 payload、堆栈或内部路径。

`fallback_allowed` 按一元规则（`fallback_allowed_for_code`）计算：世界状态已知未变（`external_state_unknown == false`）即允许命令回退，唯一例外 `INTERNAL_ERROR`；`STATE_UNKNOWN` 保持拒绝。`UNSUPPORTED_BLOCK_PLACEMENT` 等多格方块写前失败码放行，模型可回退 `setblock`（1.26.10+ 完整放置）。

## 6. 提示词与 schema

### 6.1 `_BLOCK_TOOL_PRIORITY`（唯一长文，条目化）

```text
方块操作编排规则：
- 连续区域（地板/墙体/屋顶）用 fill_block；单格用 place_block。
- inspect_block 只在需要确认世界状态时调用，不要机械地在每次编辑前先查一遍。
- expect 默认 air（仅替换空气）；要覆盖非空方块用 expect=any（需再审批）。
- 失败时只读 code 与 hint；仅 fallback_allowed=true 时才能考虑命令回退。
- 同一轮可以并行发出多个相互独立的 fill/place。
```

### 6.2 工具卡片与 docstring

- catalog 移除 `edit_blocks` 条目，新增 `place_block` / `fill_block`；`inspect_block` 改为单一 target 描述。
- 每个工具 docstring 只写「职责 + 参数 + 结果语义」，不复制恢复策略。
- `render_schema_description_prefix` 前缀保留（意图/风险/参数）。

## 7. 配置

- `block_tools.max_discrete_positions`：新契约不再需要模型侧上限，保留为内部防御（宿主 batch 已移除，可置 0 或删除）。
- `block_tools.max_fill_volume` / `cells_per_tick` / `max_locked_targets_on_wire`：保留。
- `block_tools.inspect_sample_limit` / `inspect_summary_threshold`：保留，样本格式改为紧凑数组。
- `tool_calls_limit`：不调整（100 已足够）；目标是通过更少轮次完成任务，而非提高上限。

## 8. 测试计划

### 8.1 单元

1. `place_block` / `fill_block` schema：`pos` 长度 3、角点归一化、非法坐标拒绝。
2. 参数 → 线协议映射：place → `mode=place`，fill → `mode=fill`；`expect` → `replace_any` / `expected_previous`。
3. 结果投影：place `was` 过滤；fill `type_counts` 仅非空气；inspect 样本 ≤8 且格式为 `[x,y,z,type_id]`。
4. 预算防御：构造超长 states 帧触发 `LIMIT_EXCEEDED`，确认不发送。
5. 公开 schema 断言：三个新工具 schema 不含任何隐藏字段，且总字符数低于旧 `edit_blocks` 单工具 schema。
6. `plan_id` 恢复：preflight → 审批 → `execute_block_plan(plan_id)` 幂等执行一次；无隐藏 kwargs 注入。

### 8.2 集成 / 回归

1. 审批批量：同一轮 4 个并行 `fill_block` 合并为一批审批，一次 `AGENT 同意` 全部执行。
2. 旧 12 离散点场景回归：模型只能表达为 4 个 fill 柱或 12 个 place，不再出现 `LIMIT_EXCEEDED`。
3. 草地建房：`fill_block(expect=any)` 覆盖 grass 成功；`expect=air` 时返回可操作 `PRECONDITION_FAILED`。
4. 命令回退按一元规则放行：写前失败码（`PRECONDITION_FAILED` / `UNSUPPORTED_BLOCK_PLACEMENT` 等）映射后 `fallback_allowed=true`，同 run 内 `setblock` / `fill` / `clone` 不被 `_block_command_fallback_denial` 拒绝；`STATE_UNKNOWN` / `INTERNAL_ERROR` 仍拒绝（回退命令仍需独立审批）。

### 8.3 手工验收（游戏内）

1. Creative 模式铺 5×5 木板 + 四面墙 + 屋顶 + 火把：目标 ≤6 轮、≤8 个工具调用、≤20 秒。
2. 审批后同进程内恢复：`plan_id` 路径执行一次，不重复写入（进程重启后的跨进程恢复不在本轮范围）。
3. 对照 8/4 日志：同一场景累计 input tokens 下降至少 30%。

## 9. 分阶段实施

### 阶段一：新契约落地

1. `tools.py`：新增 `place_block` / `fill_block`，改写 `inspect_block`；删除 `edit_blocks` 公开入口与旧 Pydantic 模型（`BlockEdit` / `BlockTarget` / `BlockTargetBox`）。
2. `tools_impl.py`：新增三个实现函数（映射 legacy 帧、复用 preflight/execute/投影）；`_execute_edits_group` 与 grouped 校验删除。
3. `execution.py`：`_BLOCK_OPS_TOOLS` 更新；preflight 生成 `plan_id`；恢复走 `execute_block_plan`；删除隐藏字段注入。
4. `project.py` / `bridge.py`：新投影格式；保留错误白名单。
5. `prompting.py` / `catalog.py`：提示与卡片更新。

### 阶段二：清理

- 删除 `strip_block_internal_tool_schema`、`project_block_execute_args` 的 legacy 分支、`_EditNormalization` 等 grouped 残留。
- 更新受影响的测试（`test_block_ops.py`、`test_runtime_harness_audit.py`、`test_tool_approval_command.py` 等）。

### 阶段三：验收

- 运行 §8.3 手工验收场景并记录轮次/耗时/token 对照；更新本文档状态。

## 10. 迁移与兼容

- 新工具名直接替换模型可见工具；旧 `edit_blocks` 不再暴露。
- 审批 TTL 短（分钟级），旧 pending 审批自然过期；`execution_args_hash` 旧记录只影响过期前的极端窗口，可接受。
- 审计文件保留历史记录，字段兼容（`tool_name` 变为新名称）。
- Add-on 无需改动；线协议 `capability=edit_blocks` 保留（宿主内部映射到同一 capability），避免加 Add-on 发布周期。

## 11. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 单一职责工具使模型少用面积批量表达 | 提示词明确 fill 优先 + 并行调用；实测 8/4 场景验证 |
| 移除 `dimension` / 相对坐标丢失能力 | 默认当前玩家维度（已有修复）；相对坐标后续以独立参数追加 |
| `plan_id` 恢复引入状态表复杂度 | `preflight_cache` 已有内存缓存；TTL 与审批 TTL 对齐；失败时 `STATE_UNKNOWN` 语义不变 |
| 审批次数增加（单工具单审批） | 并行调用聚合为同一批审批；`AGENT 同意 对话/永远` 可一次性放行 |
| 旧测试与文档残留 | 阶段二专门清理；本文档作为新契约真相源 |

## 12. 默认假设（待用户确认）

1. v1 只保留绝对坐标 `[x, y, z]`，移除玩家相对坐标；
2. `expect` 默认 `air`，`states` 为可选对象参数；
3. 审批采用「单工具单审批」+ 现有批量同意；
4. 不暴露 `dimension`，默认当前玩家维度；
5. 命令回退按一元规则放行（`fallback_allowed_for_code`），`STATE_UNKNOWN` / `INTERNAL_ERROR` 保持拒绝。
