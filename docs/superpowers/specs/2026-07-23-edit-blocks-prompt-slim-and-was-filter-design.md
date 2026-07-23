# `edit_blocks` 提示分层瘦身与成功投影 was 过滤

**日期：** 2026-07-23

**状态：** 设计中（待用户审阅）

**关联功能：** `inspect_block` / `edit_blocks` 模型可见成功投影、运行时 Harness 工具描述与 system 提示

**关联规格：**

- `docs/superpowers/specs/2026-07-22-agent-block-operations-tools.md`
- `docs/superpowers/specs/2026-07-23-edit-blocks-llm-guidance-and-payload-slim-fix.md`（下文称「瘦身规格」）

**约束（不可破坏）：**

- 高风险写入仍走 preflight → 审批 → execute
- 审批幂等键、wire omit、LIMIT 预算逻辑本轮不动
- 失败路径白名单（`PRECONDITION_*` / `LIMIT_EXCEEDED` 的 `hint` / `actual_type_id` / `target`）本轮不动
- 不向模型暴露 Python 堆栈；审计日志可继续保留全量 addon payload
- 多人身份仍只用 `event.sender` / 显式 `player_name`

---

## 1. 背景与问题

### 1.1 用户意图

优化 `edit_blocks` 相关引导与结果语义，目标两点：

1. **工具提示太冗长且重复**，增加 token、淹没关键约束；
2. **让 LLM 能判断放置时是否误替换了原有非空方块**——成功结果应报告「被替换了什么」；若原为空气则默认不报，降低噪声，并配合简短兜底策略减轻模型负担。

### 1.2 现状

信息叠在三层，内容高度重复：

| 层 | 位置 | 现状问题 |
|---|---|---|
| 工具 docstring | `services/agent/tools.py` `edit_blocks` | 用法 + 默认只换空气 + **整段恢复策略** |
| Catalog / schema 前缀 | `services/agent/harness/catalog.py` → `render_schema_description_prefix` | when / when-not / 参数 + **再写一遍 LIMIT / PRECONDITION** |
| System 提示 | `services/agent/harness/prompting.py` `_BLOCK_TOOL_PRIORITY` | 优先策略 + **又写一遍恢复策略** |

成功投影（瘦身规格已落地）已有：

- place → `was`（原 typeId）
- batch / fill → `previous_type_counts`

但 **不区分空气**：空气上放置也会回 `was: "minecraft:air"` 或 `previous_type_counts: {"minecraft:air": N}`，噪声大，无法当「误覆盖告警」。

失败路径已有可操作 `hint`（PRECONDITION 带 `actual_type_id`/`target`；LIMIT 禁止 place 风暴）。本轮不改失败契约。

### 1.3 与瘦身规格的关系

瘦身规格 R3/R4 要求：成功体瘦到决策字段、提示写清恢复策略。落地时三处**各写了一份**恢复句，导致重复；成功示例里 place 仍带 `"was": "minecraft:air"`。

本设计是瘦身规格的 **后续精炼**，不是回滚：

- 恢复策略保留，但 **只留在 system 提示一处**；
- 成功投影增加 **air 过滤**，使 `was` / `previous_type_counts` 真正承担「非空被替换」信号。

---

## 2. 目标与非目标

### 2.1 目标

1. **分层单源提示**
   - System（`_BLOCK_TOOL_PRIORITY`）：**唯一**完整「使用 + 恢复/兜底」长文；并说明成功结果语义（无 was = 原为空气）。
   - Catalog：只写意图 / 风险 / 参数约束 + 一句「成功仅报告被替换的非空气」；**不**再列 LIMIT / PRECONDITION 长清单。
   - Docstring：职责 + 参数 + **成功返回字段语义**；**不**写长恢复清单。
2. **成功投影 air 过滤（严格 typeId）**
   - place：仅非空气时写 `was`；空气则省略整键。
   - batch / fill：`previous_type_counts` 去掉 air 键；空 dict 则省略整键。
3. **可测 + 在线体感**
   - 单测覆盖投影与 harness 提示分层；
   - 用户本地跑一轮「铺平台（空气）/ 误踩泥土」验证模型是否更易读结果、更少被冗长提示淹没。

### 2.2 非目标

- 不改 addon 协议、wire omit、审批恢复、`locked_targets` 策略。
- 不把 liquid / passable 当 air（不扩展「可安全覆盖」类）。
- 不新增配置项控制 was 过滤。
- 不改失败 JSON 白名单与 `hint` 文案（除非为配合测试必须的断言字符串）。
- 不重构 harness 注入架构（方案 2 留作未来）。
- 不在投影中新增自然语言总结字段。

---

## 3. 设计

### 3.1 成功投影规则（`project.py`）

**唯一改动点：** `services/agent/block_ops/project.py`。  
审计 / logger / 全量 bridge payload **不变**；仅 `project_block_result_for_model` 输出给 LLM。

#### 3.1.1 air 判定

```text
normalize(type_id) =
  strip()
  lower()
  if contains ":" then take substring after last ":"
  else whole string

is_air(type_id) ⇔ normalize(type_id) == "air"
```

- 匹配：`minecraft:air`、`Air`、`air`
- **不**匹配：`cave_air`、`void_air`、`minecraft:water` 等（保守；若未来要扩展另开规格）

#### 3.1.2 place

在现有 `_project_place` / `_place_was` 路径上：

1. 解析 `was`（现有：`payload.was` 或 `before.type_id` 等）；
2. 若 `was` 存在且 `is_air(was)` → **不写入** `out["was"]`；
3. 若 `was` 存在且非 air → `out["was"] = was`（保持原字符串形式，不强制改写命名空间）；
4. 其它字段（`ok` / `mode` / `changed` / `at` / `type_id` / 非空 `states`）不变。

示例：

```json
// 原为空气（正常）
{"ok": true, "mode": "place", "changed": true, "at": {"x": 0, "y": 64, "z": 0}, "type_id": "minecraft:stone"}

// 原为泥土（需关注）
{"ok": true, "mode": "place", "changed": true, "at": {"x": 0, "y": 64, "z": 0}, "type_id": "minecraft:stone", "was": "minecraft:dirt"}
```

#### 3.1.3 batch / fill

在 `_project_batch` / `_project_fill` 中，对 `previous_type_counts`：

1. 仅当值为 dict 时处理；
2. 拷贝并 **删除** 所有 key 满足 `is_air(key)` 的项（值原样保留）；
3. 过滤后非空 → 写入 `previous_type_counts`；
4. 过滤后为空 → **省略整键**（不要写 `{}`）。

示例：

```json
// 仅替换空气
{"ok": true, "mode": "fill", "changed_count": 6, "skipped": 19, "volume": 25, "type_id": "minecraft:oak_planks", "from": {...}, "to": {...}}

// 混有非空
{"ok": true, "mode": "fill", "changed_count": 10, "type_id": "minecraft:oak_planks", "previous_type_counts": {"minecraft:dirt": 3, "minecraft:gravel": 1}, ...}
```

#### 3.1.4 模型侧语义（写入 system 提示，一句即可）

- **无 `was` / 无 `previous_type_counts`**：被替换的全是空气（或未换到非空）→ 默认路径，正常。
- **有非空 `was` / counts**：覆盖了非空方块 → 应向玩家说明，或调整 plan / 使用 `replace_any` 的意图对齐。
- **失败**：仍读 `code` + `hint`（本轮契约不变）。

### 3.2 提示分层（单源）

原则：

> 恢复/兜底策略只在 system 提示写长文；catalog 与 docstring 只写「职责 + 参数 + 结果语义」。

#### 3.2.1 System：`_BLOCK_TOOL_PRIORITY`（唯一长文）

保留并略收紧为条目式，至少覆盖：

| 要点 | 必须有 |
|---|---|
| 查 / 写分工 | `inspect_block` / `edit_blocks`（place\|batch\|fill） |
| 形状选择 | 平台/地板/墙优先 fill 或少量 batch；禁止连续区域 place×N |
| 默认策略 | 默认只换空气；覆写非空须 `replace_any=true`（再审批） |
| 删除 | 放置 `minecraft:air` 并授权覆写 |
| **成功结果语义** | `was` / `previous_type_counts` 仅含非空气；无 was = 原为空气；出现非预期 was 应说明或改 plan |
| LIMIT | 缩小 positions 或 fill 体积；仍用 batch/fill；**禁止 place 风暴** |
| PRECONDITION | actual 非空气：跳过或 `replace_any=true` 再审批；勿无授权同格重试 |
| 回退 | 专用工具可用时禁止 setblock/fill；仅 `ADDON_UNAVAILABLE` 可命令回退（仍审批） |

文案可换行条目化，便于扫描；总长度宜 **短于** 当前三处拼接后的总和，但 **不得** 删掉上表要点（测试会断言关键词）。

#### 3.2.2 Catalog：`edit_blocks` / `inspect_block` 条目

`when_to_use` / `when_not_to_use` / `parameter_constraints`：

- **保留**：mode 形状、默认空气、`replace_any` 与 `expected_previous` 互斥、禁止 place×N、优先专用工具。
- **增加（一句）**：成功结果仅在 `was` / `previous_type_counts` 中报告被替换的**非空气**方块。
- **删除**：LIMIT_EXCEEDED / PRECONDITION_FAILED 的多句恢复清单（改由 system 承担）。

`inspect_block` 同步去掉与建造恢复策略纠缠的长句，只保留「查询 / 不用命令试探 / 参数形状」。

#### 3.2.3 Docstring：`edit_blocks`（及精简 `inspect_block`）

`edit_blocks` docstring 结构建议：

1. 一行职责：place / batch / fill。
2. 默认空气、`replace_any` 互斥、删除方式、需审批。
3. **成功投影字段**（决策字段列表 + 非空气才报 was/counts）。
4. Args 列表（参数说明保持清晰；不写恢复 bullet 列表）。
5. 内部参数 `locked_targets` / `phase` 仍仅注释标明 harness 用，不教模型使用。

`inspect_block`：去掉「建造前确认空气 / 无授权勿重试」等与 edit 恢复重复的句子（system 已覆盖）。

#### 3.2.4 明确禁止

- 禁止在 catalog 的 `parameter_constraints` 再贴 LIMIT/PRECONDITION 长段落。
- 禁止在 docstring 再贴与 `_BLOCK_TOOL_PRIORITY` 同构的恢复策略列表。
- 允许 docstring / catalog 用 **一句** 指向结果语义（was 过滤），与 system 不冲突。

### 3.3 测试与验收

#### 3.3.1 单测（必须）

| 用例 | 期望 |
|---|---|
| place 成功、`was`/`before` 为 air | 投影 **无** `was` 键 |
| place 成功、原为 dirt 等 | 投影 `was ==` 原 typeId |
| batch/fill 仅 air counts | **无** `previous_type_counts` |
| batch/fill 混合 counts | 仅非 air 键；air 键不存在 |
| 现有瘦身断言 | place 文本长度上限等仍成立（无 was 时更短，不会变差） |
| `test_runtime_harness_prompt` | 仍含 `LIMIT_EXCEEDED`、place 风暴相关词、`replace_any`、`PRECONDITION_FAILED`、`batch`/`fill` |
| （建议新增） | 渲染后的 **工具卡片** 字符串中，`edit_blocks` 卡片 **不** 再同时出现长恢复句套装（至少不出现与 system 重复的「不要 place 风暴」式完整段落——若难精确断言，则断言 catalog `parameter_constraints` 不含 `LIMIT_EXCEEDED`） |

#### 3.3.2 在线体感（用户，成功标准 B）

1. **铺平台（空气）**：一轮 fill/batch 成功；结果无 `was` / 无 air-only counts；模型不因冗长描述乱 place。
2. **故意/误替换非空**（如 `replace_any` 或条件允许路径下覆盖泥土）：结果出现 `was` 或非空 counts；模型能向玩家说明。

体感不阻塞合入，但 PR 描述应注明是否已手测。

### 3.4 文件清单（预期）

| 文件 | 变更 |
|---|---|
| `services/agent/block_ops/project.py` | `is_air` 辅助；place/batch/fill 过滤 |
| `services/agent/harness/prompting.py` | `_BLOCK_TOOL_PRIORITY` 条目化 + 成功结果语义 |
| `services/agent/harness/catalog.py` | edit/inspect 条目瘦身；去恢复清单；一句结果语义 |
| `services/agent/tools.py` | `edit_blocks` / `inspect_block` docstring 精简 |
| `tests/test_block_ops.py` | was/counts 过滤用例；更新原「was == air」断言 |
| `tests/test_runtime_harness_prompt.py` | 保留恢复关键词；可选 catalog 不重复 LIMIT |
| 本规格 | 设计真相源 |

**不改：** `bridge.py` 失败映射、`tools_impl.py` wire/审批、addon 侧。

### 3.5 风险与缓解

| 风险 | 缓解 |
|---|---|
| 模型误以为「无 was = 失败」 | system 明确写「无 was = 原为空气（正常）」 |
| 旧对话仍含冗长工具描述 | 仅影响新注册 schema / 新 system 渲染；可接受 |
| 测试仍断言 `was == minecraft:air` | 改为断言无 `was` 键 |
| 瘦身规格示例与本规格不一致 | 以本规格为准更新行为；瘦身规格视为历史「已实施 + 本轮精炼」 |

---

## 4. 实施顺序（计划阶段细化）

1. `project.py` air 过滤 + 单测红→绿  
2. `_BLOCK_TOOL_PRIORITY` 改写 + harness prompt 测试  
3. catalog + docstring 去重  
4. 全量相关 pytest  
5. 用户体感（可选，记入 PR）

---

## 5. 成功标准（验收清单）

- [ ] place 空气成功：JSON 无 `was`
- [ ] place 非空成功：有 `was: "<typeId>"`
- [ ] batch/fill：`previous_type_counts` 不含 air；仅 air 时无该字段
- [ ] system 提示含 LIMIT / PRECONDITION / 禁止 place 风暴 / 成功结果语义
- [ ] catalog `edit_blocks.parameter_constraints` **不含** `LIMIT_EXCEEDED`（恢复不在此层）
- [ ] docstring **无** 与 system 同构的恢复 bullet 列表
- [ ] 相关单测通过
- [ ] （期望）用户完成一轮空气平台 + 非空覆盖体感

---

## 6. 开放问题

无。air 判定、分层策略、成功标准已在头脑风暴中锁定（方案 1 / 严格 typeId / 分层单源 / 可测+体感）。
