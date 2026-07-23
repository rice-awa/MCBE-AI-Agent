# `edit_blocks` LLM 引导与载荷瘦身修复设计

**日期：** 2026-07-23

**状态：** 已实施

**关联功能：** `inspect_block` / `edit_blocks`、方块桥接线协议、运行时 Harness 工具描述与错误投影

**证据日志：** `logs/tmp.log`（主 run `fe693a3f-...`；对比 place run `e19ba3f8-...`）

**关联规格：**

- `docs/superpowers/specs/2026-07-22-agent-block-operations-tools.md`
- `docs/superpowers/specs/2026-07-22-edit-blocks-approval-resume-fix-design.md`

**约束（不可破坏）：**

- 多人身份仍只用 `event.sender` / 显式 `player_name`
- 高风险写入仍走 preflight → 审批 → execute
- 审批幂等键仍基于规范化后的 authorized 操作语义
- `fallback_allowed=false` 对 LIMIT / PRECONDITION / STATE_UNKNOWN 保持；仅 `ADDON_UNAVAILABLE` 允许命令回退
- 不把 Python 堆栈、函数限定名暴露给模型

---

## 1. 背景与问题陈述

### 1.1 用户可观察现象

玩家请求在脚底附近铺一层木板平台时，Agent 行为如下：

1. 正确选择 `edit_blocks mode=fill`（5×1×5，体积 25）；
2. 审批后 execute 失败：`LIMIT_EXCEEDED`（`FrameTooLargeError: 557 > 461`）；
3. 模型将 fill 拆成更小 AABB，仍有一片失败（`505 > 461`），另一片因 locked 数少而成功；
4. 模型退化成大量并行 `mode=place`；
5. 多数 place 因「默认仅替换空气」撞上非空目标，返回不可决策的 `PRECONDITION_FAILED`；
6. 工具调用数冲到 18，触发 `tool_calls_limit=16`，整轮以 `DENIED` 结束，平台未铺完。

这不是「模型不会用 fill」，而是 **线协议预算 + 回模型的成功/失败 JSON + 工具引导** 三方叠加，把合法建造路径逼进 place 风暴。

### 1.2 日志证据摘要

| 场景 | 关键字段 | 结果 |
|---|---|---|
| place 石头 1 格 | `omitted_locked_targets=True`, `payload_bytes=222` | 成功；模型侧结果 **704 字符** |
| fill 5×1×5（matched=6 空气） | `locked_targets_on_wire=6`, `payload_bytes=428` | **LIMIT** `557 > 461` |
| fill 子片（4 locked） | `locked_targets_on_wire=4`, `payload_bytes=376` | **LIMIT** `505 > 461` |
| fill 子片（2 locked） | `omitted_locked_targets=True`, `payload_bytes=253` | 成功；模型侧结果 **835 字符** |
| place 打到 gravel/stone | addon 有 `actual`/`target`/`message` | 模型只见 `"Addon 返回错误：PRECONDITION_FAILED。"` |
| 工具上限 | `tool_calls=18` | `UsageLimitExceeded` / `DENIED` |

稀疏 fill 粗估（绝对坐标 + 6 个 compact locked cell）：

- payload 本体约 416B；
- 包进 `scriptevent mcbews:bridge_req {...}` 后约 545B；
- **省略 locked 后** 全包约 370B，落在 461B 预算内。

结论：瓶颈是 **稀疏 fill execute 强制上送 `locked_targets`**，不是 `volume=25` 本身。

### 1.3 与既有规格的张力

原方块工具规格（2026-07-22）要求：

- fill 结果应 summarize，避免 oversized responses（User Story 19）；
- 稳定错误码 + 可理解 message，便于模型下一步动作（US 29/44）；
- 审批后使用预检锁定目标，保证语义冻结。

当前实现在 **审计/幂等层** 基本满足锁定目标，但在 **出站线协议** 与 **模型可见投影** 上偏离了「可理解、不过大」：

- 稀疏匹配时把锁定列表重新推上 461B 硬预算；
- 成功路径原样 `json.dumps` addon 审计 JSON；
- 失败路径 `_safe_addon_error_body` 剥掉决策字段。

---

## 2. 根因分析

按影响排序。

### R1. 稀疏 fill 的 wire omit 条件与真实预算不匹配

`should_omit_locked_targets_on_wire` 仅在 `len(locked_targets) >= volume`（密实 fill）或 place/batch 几何已冻结时省略 locked 列表。

默认 `replace_any=false` 时，「铺平台」几乎总是 **matched ≪ volume**，于是 execute **强制带 locked_targets**。6 个带坐标的 cell 即可打爆 MCBE `commandLine` ~461B。

相关代码：`services/agent/block_ops/tools_impl.py`（`should_omit_locked_targets_on_wire` / `build_edit_payload`）。

### R2. 给 LLM 的错误不可操作

| 错误 | 模型看到 | 缺失的下一步信息 |
|---|---|---|
| `LIMIT_EXCEEDED` | 「缩小体积/批次数」 | 真正超预算的是 sparse locked 列表；建议 max discrete；禁止 place×N |
| `PRECONDITION_FAILED` | `"Addon 返回错误：PRECONDITION_FAILED。"` | addon 原文 `target is not air`、`actual.type_id`、`target` 坐标 |

`bridge._safe_addon_error_body` 出于防镜像/安全，对 addon 错误几乎只保留 code 名。对 PRECONDITION 过狠：模型无法改 `replace_any` 或跳过非空格。

### R3. 成功返回零裁剪

`map_addon_bridge_result` 成功路径原样 dump addon payload。place 成功约 700+ 字符，含：

- 重复的 `dimension`（顶层 + targets + before + after）；
- 常空的 `states` / `repairs_applied`；
- 全量 before/after 快照（约 9 字段/格）；
- fill 的 `before_samples`、`verification`、`rollback` 等审计字段。

对比：`get_look_block` ~87 字、`get_player_snapshot` ~168 字。edit 成功体约 8–10×，放大 token、淹没 `ok/changed_count`。

### R4. 失败恢复策略未写入工具描述 / system 提示

`tools.py` docstring、`harness/catalog.py`、`harness/prompting.py` 的 `_BLOCK_TOOL_PRIORITY` 有：

- 优先专用工具；
- 默认只换空气；
- mode 形状说明。

**没有：**

- LIMIT 后继续用更小 **batch/fill**，禁止 place 风暴；
- PRECONDITION + air-only → 跳过或 `replace_any=true` 再审批；
- 平台/地板优先一次 fill 或少量 batch；
- 与 `tool_calls_limit` 对齐的 mutation 预算意识。

### R5. 公开 schema 噪音 + 命名分裂

- 公开签名暴露 `locked_targets`、`phase`（注释写内部，模型 schema 仍可见）；
- catalog / preview 写 fill `from`/`to`，Python 签名是 `from_pos`/`to_pos`；
- fill 成功后 `merge_canonical_from_preflight` 可用 locked 角点重写 AABB，日志中意图 `z=180..184` 成功体变成 `z=180..180 volume=2`，二次规划语义漂移。

### R6. `tool_calls_limit` 与失败放大不匹配

默认 16 次工具调用。一次「铺一层板」在 LIMIT + PRECONDITION 引导失败后轻易 18 次 → DENIED。根因仍是 R1–R4；提高 limit 是缓解，不是主修复。

---

## 3. 目标与非目标

### 3.1 目标

1. **Absolute execute 默认不把 `locked_targets` 推上 461B 线**；稀疏 fill 的常见平台建造不再 LIMIT。
2. **Preflight 阶段即可预估/拒绝超预算 execute**，带可操作 hint，而不是 execute 才炸。
3. **模型可见成功体** 瘦到决策字段（place ~100–200 字级；fill 以 summary 为主）。
4. **模型可见失败体** 保留白名单决策字段（LIMIT 的建议上限；PRECONDITION 的 actual/target/策略句）。
5. **工具描述 + harness 提示** 写清恢复策略：禁止 place 风暴；非空覆写必须 `replace_any`。
6. **对模型隐藏内部参数**（`locked_targets`/`phase`）；对外统一 fill 角点命名。
7. 审计日志、审批摘要、幂等键仍可使用全量/规范化数据；**投影层与审计层分离**。

### 3.2 非目标

- 不修改 MCBE 客户端 461B `commandLine` 硬限制本身。
- 不取消 preflight / 审批 / 幂等。
- 不在 LIMIT/PRECONDITION 时自动回退 `setblock`/`fill` 命令。
- 不扩大「模糊修块 ID」或自动 `replace_any`。
- 本轮不强制改 Add-on 二进制协议版本；优先宿主侧 omit + 投影。若需 `plan_id` 校验，可分阶段。

---

## 4. 设计

### 4.1 分层：三套视图，一套真相

| 视图 | 用途 | 字段范围 |
|---|---|---|
| **Authorized / Audit** | 审批展示、幂等哈希、工具审计 | 规范化操作语义 + locked_targets + repairs 等元数据 |
| **Wire execute** | 出站 bridge / scriptevent | 最小几何 + type/策略 + phase；**默认无 locked 列表** |
| **Model projection** | 回给 LLM 的 tool result 字符串 | 决策字段 only |

原则：

> `locked_targets` 只存在于宿主审批记录与幂等键；默认不重放上线。  
> Addon 审计全量可落盘；模型只看投影。

### 4.2 线协议 / execute（修复 R1）

#### 4.2.1 omit 策略调整

将 `should_omit_locked_targets_on_wire` 调整为：

```text
phase != execute            → 不省略（preflight 仍可带/或不带，按现实现）
coordinate_mode != absolute → 不省略（相对坐标需冻结列表或已解析后改 absolute）
mode in {place, batch, fill} 且 absolute 几何完整 → **默认省略 locked_targets**
```

删除「稀疏 fill 必须带 cell 列表」的特例。稀疏语义由：

- execute 上的 `from`/`to` + 同一 `replace_any` / `expected_previous`；
- 或宿主侧 `plan_id` / `targets_hash`（可选第二阶段）

保证与审批一致。

**幂等与审批一致性：**

- 审批时展示的仍是 preflight 规范化后的 matched/skipped 摘要；
- execute 时 addon 用同一策略再匹配；若世界在审批后变化 → 已有 `PRECONDITION_CHANGED` / 校验路径处理；
- 宿主仍保存 `authorized_args.locked_targets` 用于审计与恢复投影，但 **不写入 wire payload**。

#### 4.2.2 可选第二阶段：plan_id

若后续需要 addon 侧强校验「execute 目标集 = 审批集」：

```json
{
  "mode": "fill",
  "phase": "execute",
  "coordinate_mode": "absolute",
  "dimension": "minecraft:overworld",
  "from": {"x": -797, "y": 93, "z": 180},
  "to": {"x": -793, "y": 93, "z": 184},
  "type_id": "minecraft:oak_planks",
  "replace_any": false,
  "plan_id": "<host-issued>",
  "targets_hash": "<sha of locked cells>"
}
```

第一阶段 **不阻塞**：仅 omit locked + 同策略再匹配即可消除本日志中的 LIMIT。

#### 4.2.3 预算预检

在 preflight 成功、进入审批前（或 execute 组包后、发送前）：

1. 用与 SDK 相同的序列化方式估算 `commandLine` 字节（含 `scriptevent mcbews:bridge_req` 外壳）；
2. 若 `>= command_line_byte_budget`（默认 461）：
   - **不发送**；
   - 返回 `LIMIT_EXCEEDED` + 结构化 hint（见 4.3）；
3. 避免「审批通过后才 FrameTooLarge」的体验（本日志 preflight 过了、execute 才炸）。

若 omit 后仍超预算（极大 `positions` 列表等），在 preflight 直接拒绝并建议拆分。

#### 4.2.4 若必须带 cell 列表时的紧凑编码

仅相对坐标未冻结、或未来特殊模式：

- `[[x,y,z], ...]`，dimension 仅顶层；
- 配置项 `block_tools.max_locked_targets_on_wire`（可选）；
- 超限在 preflight 返回 LIMIT，带 `suggested_max_discrete`。

### 4.3 模型可见错误投影（修复 R2）

修改 `services/agent/block_ops/bridge.py`：

- `_safe_addon_error_body` 对 **白名单 code** 透传裁剪字段；
- 仍禁止：堆栈、内部 path、完整未消毒 payload 镜像。

#### LIMIT_EXCEEDED（含 command_line_budget）

```json
{
  "schema_version": "1",
  "ok": false,
  "code": "LIMIT_EXCEEDED",
  "retryable": true,
  "external_state_unknown": false,
  "fallback_allowed": false,
  "reason": "command_line_budget",
  "message": "出站帧超出 MCBE commandLine 字节预算，请求未发送。",
  "hint": "减小 batch.positions 数量或 fill AABB；继续使用 batch/fill，禁止拆成大量 place；勿用命令绕过审批。",
  "suggested_max_discrete": 3,
  "matched_count": 6,
  "volume": 25
}
```

字段规则：

- `suggested_max_discrete` / `matched_count` / `volume`：有则带，无则省略；
- `hint` 固定包含「禁止 place 风暴」。

#### PRECONDITION_FAILED / PRECONDITION_CHANGED

```json
{
  "schema_version": "1",
  "ok": false,
  "code": "PRECONDITION_FAILED",
  "retryable": false,
  "external_state_unknown": false,
  "fallback_allowed": false,
  "message": "目标非空气（默认仅替换空气）。",
  "target": {"x": -797, "y": 93, "z": 182},
  "actual_type_id": "minecraft:gravel",
  "hint": "跳过该格，或设置 replace_any=true 后重新调用（需再审批）。"
}
```

白名单透传：

- `message`：addon 短句或宿主规范化中文；
- `actual_type_id` ← `actual.type_id`；
- `target` ← `{x,y,z}`（可无 dimension 重复）；
- 不传完整 `states` 除非对条件写入必要。

### 4.4 模型可见成功投影（修复 R3）

在 bridge 成功映射或 `tools_impl` 出口增加 **`project_block_result_for_model(payload) -> dict`**。

审计 / 日志可继续写全量；`ToolResult.ok` 的 `output` 使用投影 JSON。

#### place

```json
{
  "ok": true,
  "mode": "place",
  "changed": true,
  "at": {"x": -793, "y": 94, "z": 185},
  "type_id": "minecraft:stone",
  "was": "minecraft:air"
}
```

可选：`dimension` 仅在与会话默认不一致时带；`states` 仅非空时带。

#### batch

```json
{
  "ok": true,
  "mode": "batch",
  "changed_count": 4,
  "failed_count": 0,
  "type_id": "minecraft:oak_planks",
  "previous_type_counts": {"minecraft:air": 4}
}
```

#### fill

```json
{
  "ok": true,
  "mode": "fill",
  "changed_count": 6,
  "skipped": 19,
  "volume": 25,
  "from": {"x": -797, "y": 93, "z": 180},
  "to": {"x": -793, "y": 93, "z": 184},
  "type_id": "minecraft:oak_planks",
  "previous_type_counts": {
    "minecraft:air": 6,
    "minecraft:grass_path": 12,
    "minecraft:stone": 4,
    "minecraft:gravel": 3
  }
}
```

**从模型投影删除（下沉 audit-only）：**

- `schema_version`（可选保留顶层 ok 契约时仅留 `ok`）；
- `phase`；
- 全量 `targets`；
- 全量 `before` / `after` / `before_samples`；
- 空 `states` / 空 `repairs_applied`；
- `verification` 细节（可用 `verified: true` 一比特）；
- `rollback` 细节（失败且涉及回滚时再暴露摘要）。

**fill 边界语义：**

- 模型投影的 `from`/`to`/`volume` 优先使用 **玩家/模型请求的授权 AABB**（审批时展示的 bounds），不要用 locked 角点收缩后的 AABB；
- `changed_count` / `skipped` / `previous_type_counts` 反映真实匹配。

### 4.5 输入 schema 与命名（修复 R5）

#### 对模型隐藏

- `locked_targets`
- `phase`

仅 harness / `override_args` / 审批恢复注入；不进入 PydanticAI 公开 tool schema（若框架限制「签名即 schema」，改为内部 kwargs 或 wrapper 剥离）。

#### fill 角点对外名

- 对外统一：`from` / `to`（与 catalog、桥协议一致）；
- 内部 Python 可继续 `from_pos`/`to_pos`，但 **模型 schema 与 docstring 只暴露 `from`/`to`**，或双接受但描述只教一个。

#### docstring / catalog 必补（修复 R4）

```text
- 平台/地板/墙：优先一次 fill 或少量 batch，禁止对连续区域 place×N。
- 默认只改空气；要铺满非空地面必须 replace_any=true（高风险再审批）。
- 若返回 LIMIT_EXCEEDED：减小 positions 数量或 fill 体积，仍用 batch/fill，不要 place 风暴。
- 若 PRECONDITION_FAILED 且 actual 非空气：不要对同一格无 replace 重试。
- 单轮建造避免无意义并行 place；优先扩大/收紧 AABB 或 batch。
```

`_BLOCK_TOOL_PRIORITY` 同步增加上述恢复句。

### 4.6 配置（修复 R6，缓解）

| 配置 | 建议 |
|---|---|
| `flow_control.command_line_byte_budget` | 保持 461；预算检查用同一值 |
| `block_tools.max_locked_targets_on_wire` | 新增可选；默认 0（表示 absolute 不传 locked） |
| `agent.tool_calls_limit`（或等价） | **评估完成（非主修复）**：`config.example.json` 已为 40；Settings Field 默认 16 保持；主修 R1–R4（omit/预算/投影/恢复策略），不把提高 limit 当主修复 |
| 可选 | preflight 返回 `wire_budget` 摘要供调试日志 |

### 4.7 安全与幂等（保持）

- 审批仍基于 preflight `authorized_args` + hash；
- execute 用 `execute_args` / harness 注入；
- 多人继续只用 `player_name`；payload 带当前事件玩家；
- LIMIT / PRECONDITION / STATE_UNKNOWN：`fallback_allowed=false`；
- 不向模型暴露堆栈。

---

## 5. 实施计划

### Phase A — Wire omit + 预算预检（P0）

**文件（预期）：**

- `services/agent/block_ops/tools_impl.py` — `should_omit_locked_targets_on_wire`、`build_edit_payload`、preflight 后预算估算钩子
- `services/agent/block_ops/bridge.py` — LIMIT 文案与字段
- `tests/test_block_ops.py` 等

**验收：**

- 复现日志中的 5×1×5 air-only fill（matched=6）：execute **不再** `FrameTooLarge`；
- 4 locked 子片同样成功；
- absolute place 仍 omit locked（回归）；
- 审批恢复路径仍能 execute 一次且语义与批准一致。

### Phase B — 模型错误投影（P0）

**文件：**

- `services/agent/block_ops/bridge.py` — `_safe_addon_error_body` / `map_addon_bridge_result`
- 测试：PRECONDITION 含 `actual_type_id`+`target`；LIMIT 含 `hint`+可选计数

**验收：**

- 模型侧不再只见 `"Addon 返回错误：PRECONDITION_FAILED。"`；
- LIMIT 文案明确禁止 place×N。

### Phase C — 模型成功投影（P0）

**文件：**

- `services/agent/block_ops/bridge.py` 或新建 `services/agent/block_ops/project.py`
- 审计路径确认全量仍可记录（harness audit / logger）

**验收：**

- place 成功模型输出 ≪ 700 字符（目标 ≤ 250）；
- fill 成功含 `changed_count`/`skipped`/`previous_type_counts`，无 `before_samples`；
- fill 投影 bounds 与授权 AABB 一致（不因 sparse locked 收缩）。

### Phase D — 描述与 schema 去噪（P1）

**文件：**

- `services/agent/tools.py` — docstring；隐藏内部参数
- `services/agent/harness/catalog.py` — `from`/`to` 与约束文案
- `services/agent/harness/prompting.py` — `_BLOCK_TOOL_PRIORITY`

**验收：**

- 工具 schema 对模型不可见 `locked_targets`/`phase`（或调用时忽略）；
- 卡片/描述含恢复策略。

### Phase E — 配置与 limit（P1）

- 评估 `tool_calls_limit`；
- 可选 `max_locked_targets_on_wire`；
- 文档更新本文件状态与 CHANGELOG（若项目有）。

---

## 6. 测试计划

### 6.1 单元

1. `should_omit_locked_targets_on_wire`：absolute fill 稀疏 matched < volume → **仍 omit**；
2. `build_edit_payload(phase=execute)`：无 `locked_targets` 键；
3. 预算估算：mock 大 positions → preflight/发送前 LIMIT；
4. `project_block_result_for_model`：place/fill 字段白名单；
5. `_safe_addon_error_body`：PRECONDITION 透传 `actual_type_id`；非白名单 code 仍脱敏；
6. fill 投影 bounds 使用授权 AABB，非 locked 收缩。

### 6.2 集成 / 回归

1. 审批恢复：preflight → approve → execute 一次（对齐 2026-07-22 resume 规格）；
2. air-only 5×1×5 平台：一轮 fill 成功（或明确可操作 LIMIT，不再 place 风暴）；
3. place 打到非空气：错误含 actual + hint；
4. `ADDON_UNAVAILABLE` 仍允许 fallback 提示；
5. STATE_UNKNOWN 仍 `retryable=false` 且无命令回退。

### 6.3 手工（游戏内）

1. Creative 脚下铺 5×5 木板（默认空气）→ 单次或少量 fill，不撞 16 次上限；
2. 对 gravel 铺板且不设 `replace_any` → 清晰失败；
3. `replace_any=true` 再批 → 成功覆写。

---

## 7. 风险与回滚

| 风险 | 缓解 |
|---|---|
| omit locked 后 addon execute 与审批目标集不一致 | 同策略再匹配 + 既有 PRECONDITION_CHANGED；可选 phase2 plan_id |
| 成功投影过瘦导致模型丢上下文 | 保留 `previous_type_counts` 与 bounds；必要时加 `sample_changed: 1–3` 可选 |
| 错误透传被利用做探测 | 仅 type_id + xyz；无堆栈；无内部路径 |
| 隐藏 `phase`/`locked_targets` 破坏恢复 | harness 内部注入，不依赖模型回传 |
| 提高 tool_calls_limit 掩盖问题 | 仅作缓解；主修 R1–R4 |

回滚：按 Phase 独立 revert；wire omit 与投影可分别开关（建议 feature flag 仅当需要灰度时再加，默认修完即开）。

---

## 8. 优先修复清单（Top 5）

1. **Absolute execute 默认省略 `locked_targets`** + preflight/发送前 commandLine 预算估算。  
2. **错误投影**：PRECONDITION 透传 actual/target；LIMIT 带 hint「禁止 place×N」+ 可选计数。  
3. **成功投影**：place/fill 瘦身到决策字段；audit 全量落盘。  
4. **工具描述 + `_BLOCK_TOOL_PRIORITY`** 写清恢复策略。  
5. **隐藏内部参数 + 对齐 from/to**；评估 `tool_calls_limit`。

---

## 9. 一句话结论

当前最大问题不是「模型不会 fill」，而是 **稀疏 fill 的 execute 载荷与 461B 冲突 + 回给模型的成功/失败 JSON 既胖又缺下一步指引**，把模型逼进 place 风暴并撞 `tool_calls_limit`。优先修 wire omit/预算、错误/成功投影和提示中的恢复策略。

---

## 附录 A — 日志关键锚点

| 路径 | 相关点 |
|---|---|
| `logs/tmp.log` | fill LIMIT；拆分 fill；place 风暴；PRECONDITION 洗白；tool_calls_limit |
| `services/agent/block_ops/tools_impl.py` | `should_omit_locked_targets_on_wire`；`merge_canonical_from_preflight`；`build_edit_payload` |
| `services/agent/block_ops/bridge.py` | `_safe_addon_error_body`；LIMIT 文案；成功原样 dump |
| `services/agent/tools.py` | 公开 `locked_targets`/`phase` |
| `services/agent/harness/prompting.py` | `_BLOCK_TOOL_PRIORITY` 无失败恢复 |
| `services/agent/harness/catalog.py` | fill 写 `from/to`，与签名 `from_pos/to_pos` 不一致 |

## 附录 B — 建议模型投影 vs 现状对照

| 场景 | 现状 result_length | 目标 |
|---|---|---|
| place 成功 | ~704–711 | ≤ 250 |
| fill 成功（2 格） | ~835 | ≤ 350（含 counts） |
| LIMIT | ~251（hint 不足） | ~280–350（含可操作字段） |
| PRECONDITION（模型侧） | ~193（过度脱敏） | ~220–300（含 actual/target） |

## 附录 C — 实施勾选

- [x] Phase A wire omit + 预算预检
- [x] Phase B 错误投影
- [x] Phase C 成功投影
- [x] Phase D 描述 / schema 去噪
- [x] Phase E 配置与 limit
- [x] 单元 + 集成测试
- [ ] 游戏内手工验收
- [x] 本文件状态改为「已实施」并记录 commit
