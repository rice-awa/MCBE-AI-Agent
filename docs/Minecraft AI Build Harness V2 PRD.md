# Minecraft AI Build Harness V2 PRD

> 状态：已确认的产品需求草案
>
> 依据：
> - [Minecraft AI Build Harness 设计文档.md](./Minecraft%20AI%20Build%20Harness%20设计文档.md)
> - [Minecraft AI Build Harness 设计文档-v2.md](./Minecraft%20AI%20Build%20Harness%20设计文档-v2.md)
> - 当前 MCBE Chat Agent 的 `place_block` / `fill_block` / `inspect_block`、运行时 Harness、审批、Addon Bridge 和多人会话实现

## 1. 文档目的

本 PRD 定义 Minecraft AI Build Harness V2 的产品目标、用户流程、能力边界、核心契约、首期范围和验收要求。它不是建筑模板库，也不是 Minecraft 命令的简单封装，而是让 MCBE Chat Agent 能够安全地生成、观察、验证和修复空间建造结果的运行时能力。

V2 的核心产品命题是：

```text
自然语言意图
  -> Agent 观察世界
  -> Agent 编写受限 Python 建造程序
  -> Harness 编译 Spatial IR
  -> Preview / Diff / 审批
  -> Commit 到 Minecraft
  -> Verify 实际结果
  -> Agent 根据证据生成修复
```

“能力优先”是首期评估取向：优先证明 Agent 可以表达和完成未预定义的程序化、地形自适应和可修复建筑。但沙箱、审批、预算、保护区、并发版本和外部状态未知仍是不可绕过的硬约束。

## 2. 背景与问题

### 2.1 当前能力

当前项目已经具备可靠的单步方块工具链：

- `inspect_block` 查询单点或区域摘要。
- `place_block` 修改单个绝对坐标方块，支持 `expect` 和 Bedrock BlockState。
- `fill_block` 修改连续 AABB。
- `services/agent/block_ops/` 负责参数归一化、预检、桥接和结构化错误。
- `services/agent/harness/` 负责工具目录、提示约束、审批、幂等、审计和命令回退策略。
- `services/agent/worker.py` 和 MessageBroker 负责异步 Agent 执行、玩家锁和生命周期。
- MCBEWS/1 Addon Bridge 负责 Minecraft 世界读取和受控变更。

### 2.2 当前瓶颈

现有原子工具适合“在这里放一个方块”或“填充一个简单矩形”，但复杂建筑会导致：

- Agent 需要手工生成大量单格调用，坐标错误和重复调用显著增加。
- 工具调用表达的是即时动作，而不是最终空间目标，难以做全局 Diff、NOOP、预览和回滚。
- Agent 无法自然表达循环、变换、重复纹样、地形适应和模块依赖。
- 命令执行成功不等于建筑满足结构约束；当前缺少统一的机器验证和可定位修复证据。
- 大规模操作的审批、并发、预算、超时、外部状态未知和 Undo 需要统一的 Operation 模型。
- 直接把 Python 或命令交给宿主会引入文件系统、网络、Shell、死循环和资源耗尽风险。

## 3. 产品目标

### 3.1 主目标

1. 让 Agent 使用受限 Python 表达未预定义的空间建造算法。
2. 让 Python 只声明空间意图，不直接修改 Minecraft 世界。
3. 让 Harness 在执行前完成 Snapshot、编译、Diff、预算、保护区、并发和风险检查。
4. 让复杂建造具备 Preview、显式 Commit、模块化提交、Verify、Patch 和 Undo 闭环。
5. 保留 `place_block` 和 `fill_block` 的公共工具契约，统一接入新的 Operation Runtime。
6. 保持 MCBEWS/1 兼容线和当前多人玩家身份隔离规则，使用可选 capability 扩展 V2 能力。
7. 记录可脱敏、有限大小的 Build Trajectory，支持故障定位、能力评估和后续 Skill 提炼。

### 3.2 成功定义

在安全硬约束全部满足的前提下，开发者可以给 Agent 一个未预定义的建筑目标，Agent 能够：

- 观察目标区域并创建稳定的本地 Build Context。
- 编写使用基础空间原语的 Python Build Script。
- 生成可理解的模块、Diff 和执行计划。
- 经过审批后提交世界变更。
- 根据实际验证证据生成补丁，而不是依赖 Harness 猜测修复。
- 在需要时安全撤销自己的模块操作。

首期不建立固定 Benchmark 或统一分数榜，采用开发者开放任务 Smoke Test；安全违规仍直接判失败。

## 4. 非目标

V2 首期不做以下事情：

- 不把 Semantic Build API 做成大量预制建筑函数集合。
- 不把 Minecraft root shell、任意命令或原生 `exec` 暴露给 Agent。
- 不允许 Python 直接调用 Bridge、文件系统、网络、进程或系统命令。
- 不在 Harness 内根据建筑意图自动猜测和生成修复。
- 不把视觉模型作为几何正确性的唯一依据。
- 不要求首期实现 Boolean、自由曲面、复杂网格、`clone` 优化或完整标准组件库。
- 不在没有明确用户批准的情况下静默覆盖玩家后续修改。
- 不通过大量 `place_block` 绕过 Python 预算、审批或操作大小限制。

## 5. 用户与权限

### 5.1 用户类型

| 用户 | 能力 |
|---|---|
| 已认证玩家 | 创建 Build Context、观察、运行 Script Preview、提交符合策略的 Operation、验证和撤销自己的操作 |
| 管理员 | 调整服务器策略、收紧/放宽玩家配额、强制撤销、管理保护区和危险方块策略、查看受限 Trajectory |
| 未认证玩家 | 不可运行 Python Build Script；保持现有登录和工具暴露策略 |

所有状态必须按 `connection_id + player_name` 隔离；`connection_id` 不能被当作玩家身份。审批 owner、Build Context、Snapshot、Operation、配额和 Trajectory 都必须携带真实业务 `player_name`。

### 5.2 认证与审批

- Preview、Verify 和只读观察没有世界副作用，不需要世界变更审批。
- Commit、Undo 和原子方块写入仍通过宿主风险策略。
- 审批关联至少包含 `connection_id`、`player_name`、`operation_id`、`conversation_id`、`approval_id` 和有效期。
- 同一 WebSocket 下的不同玩家不能互相批准、读取或恢复对方 Operation。
- “能力优先”不能改变审批、保护区和危险方块硬约束。

## 6. 产品原则

1. **Observation 优先于 Action**：Agent 先获取必要的 Snapshot，再表达空间意图。
2. **Desired State 优先于命令**：Agent 描述最终状态，Harness 决定 `fill`、`setblock` 或 NOOP。
3. **Python 是语言，不是权限**：Python 提供表达能力，所有外部副作用由 Harness 控制。
4. **Preview 与 Commit 分离**：规划结果和世界变更必须有明确边界。
5. **不确定状态不自动重试**：外部写入超时或读回不一致时必须返回 `STATE_UNKNOWN` 或 `VERIFY_MISMATCH`。
6. **模块化控制失败半径**：复杂建筑按模块规划、审批、提交和撤销。
7. **机器验证负责真值，视觉负责审美**：缺口、悬空、边界和方块状态优先由数据规则判断。
8. **错误必须可行动**：返回稳定 code、证据坐标、hint 和 retry/fallback 语义。
9. **公共工具契约保持稳定**：`place_block` / `fill_block` 名称保留，内部统一进入 V2 Runtime。
10. **所有跨层数据显式携带身份和版本**：不依赖连接级当前玩家或隐式全局状态。

## 7. 首期范围与优先级

### 7.1 P0：必须交付

- Sandbox Worker、AST precheck、资源限制和白名单 Python SDK。
- Build Context、本地坐标、Anchor、朝向、维度和授权边界。
- 只读 Snapshot 注入和 `region_revision` 绑定。
- HIR、MIR / Desired State、LIR 三级 Spatial IR 的最小可用契约。
- `execute_build_script` Preview、异步 Operation Job 和状态查询。
- World Diff、NOOP、方块预算、保护区、危险方块、维度和世界边界检查。
- 风险分级审批、模块化 Commit、实际结果摘要和结构化失败。
- `verify_operation` 的基础机器验证规则。
- Agent Patch 流程，不自动猜测修复。
- 版本保护的 `undo_operation`。
- `place_block` / `fill_block` 接入统一 Runtime；`place_block` 默认 read-back。
- MCBEWS/1 可选 capability：Snapshot、Revision、批量 Mutation Plan 和实际结果摘要。
- 结构化 Build Trajectory 和现有工具审计/Trace 的关联字段。

### 7.2 P1：首期后优先

- Blueprint / floor plan / section 的文本观察。
- 更丰富的 Pattern、局部 Boolean 和模块标准库。
- `clone` 和跨区重复结构优化。
- 运行时进度 UI、取消按钮和管理员 Operation 控制面板。
- 更丰富的验证规则：封闭性、可通行性、屋顶覆盖、照明和出生点安全。

### 7.3 P2：远期能力

- 视觉 `render_view` 和等距视图。
- 自定义曲面和更复杂的几何 Kernel。
- 从成功 Trajectory 提炼可复用 Skill。
- 容器级或微虚拟机级 Sandbox 调度。
- 大规模流式 Chunk IR、磁盘 Diff 和跨区域长任务调度。

## 8. 用户主流程

### 8.1 简单单格操作

```text
玩家请求在某坐标放置方块
  -> Agent 选择 place_block
  -> Host 预检 pos/block/expect/states
  -> 风险审批
  -> 统一 Mutation Runtime 执行单步 Operation
  -> Addon ACK
  -> read-back
  -> 返回 verified / noop / unknown / mismatch
```

### 8.2 简单连续区域

```text
玩家请求填充一个单一矩形区域
  -> Agent 选择 fill_block
  -> Host 检查 AABB、预算、expect、保护区和 states
  -> 统一 Mutation Runtime 选择 fill LIR
  -> 审批、执行、结果投影和审计
```

### 8.3 复杂建造

```text
玩家请求复杂建筑
  -> Agent inspect_region / 创建 Build Context
  -> Agent 生成 Build Spec 和模块依赖
  -> Agent 生成 Python Build Script
  -> execute_build_script
  -> Snapshot 固定、Sandbox 执行、HIR/MIR/LIR 编译
  -> Preview Ready
  -> Agent 查看 Diff / warnings / execution plan
  -> commit_operation
  -> 风险审批
  -> 按模块提交并检查 revision
  -> verify_operation
  -> PASS：进入下一个模块
  -> FAIL：Agent 生成 Patch Script 或 place_block 补丁
  -> 需要时 undo_operation
```

### 8.4 并发变更

```text
Preview 绑定 region_revision R1
  -> 玩家修改目标区域
  -> Commit 发现当前 revision != R1
  -> 返回 CONCURRENT_MODIFICATION
  -> 不执行旧 LIR
  -> Agent 重新观察、重新编译和 Preview
```

## 9. 模型可见工具契约

工具名称和字段是产品级建议，最终 Python 类型、Addon payload 和 SDK capability 需要在实现设计中形成单一 Schema。

### 9.1 现有工具

#### `place_block`

```json
{
  "pos": [100, 72, 200],
  "block": "minecraft:stone",
  "expect": "air",
  "states": {}
}
```

产品要求：

- `pos` 保持绝对世界整数坐标。
- `block` 自动归一化 `minecraft:` 前缀，但最终必须通过 Bedrock ID 校验。
- `expect` 支持 `air`、`any` 和具体 type_id；`any` 或覆盖非空气必须提高风险等级。
- `states` 只接受 Bedrock 状态，不接受 Java NBT、`facing`/`half` 等未经映射的 Java 语法。
- 多格方块仍不使用该工具表达完整结构。
- 对外响应至少包含 `ok`、`status`、`at`、`block`，可选 `was`、`verified_state` 和 `operation_id`。
- `status` 必须区分 `verified`、`noop`、`unknown`、`mismatch`。

#### `fill_block`

保留为低表达复杂度的 AABB primitive：两个绝对角点、单一方块 type_id、`expect` 和可选 states。不得把多个 `fill_block` 当作复杂建筑编程接口；复杂结构进入 Python Build Script。

#### `inspect_block`

保留向后兼容，支持单点、AABB 和既有统一 target 形式。新增 Snapshot 读取能力应扩展既有 block_ops helper，不复制位置解析和结果投影。

### 9.2 V2 新工具

#### `create_build_context`

用于固定一次 Build Plan 的空间上下文：

```json
{
  "anchor": [100, 72, 200],
  "orientation": "east",
  "dimension": "overworld",
  "bounds": {
    "min": [100, 72, 200],
    "max": [140, 100, 240]
  }
}
```

Host 必须补充并锁定：

- `context_id`
- `player_name`
- `connection_id`
- `region_revision`
- `protected_regions`
- `limits`
- `created_at` 和过期时间

脚本中的本地 `(0, 0, 0)` 只在该 Context 内有意义。玩家移动不能改变已有 Context 的锚点。

#### `inspect_region`

用于生成脚本 Snapshot 或获取模块验证证据。首期返回压缩的区域信息：边界、revision、palette/type_counts、样本、heightmap 和请求的点查询结果。不得无界返回全量方块 JSON。

#### `execute_build_script`

```json
{
  "context_id": "ctx_xxx",
  "script": "...",
  "seed": 42,
  "modules": ["foundation", "frame"],
  "verification": ["expected_bounds", "unexpected_holes"]
}
```

行为：

- 不直接修改世界。
- 创建异步 Operation Job。
- 绑定脚本 hash、Snapshot hash、Context、seed 和 limits。
- 返回 `operation_id`、初始状态、预计模块和任务追踪信息。

#### `get_operation`

读取 Operation 的状态、进度、模块摘要、Preview、错误、审批和验证结果。返回模型需要的 bounded projection，不返回无界脚本、完整世界快照或密钥。

#### `commit_operation`

```json
{
  "operation_id": "op_xxx",
  "modules": ["foundation", "frame"]
}
```

只允许提交处于 `preview_ready` 且未过期、未发生 revision 冲突的 Operation。审批不是模型自行声明的字段，而是 Host policy 的强制边界。

#### `verify_operation`

对已提交模块或指定区域运行声明式检查，返回 `PASS`、`FAIL` 或 `UNKNOWN`，并包含规则、评分、证据坐标、实际状态、期望状态和修复 hint。

#### `undo_operation`

按 Operation 或 Module 撤销，必须检查当前 revision 和授权 owner。若区域在提交后被改动，返回 `CONCURRENT_MODIFICATION`，不静默覆盖。

## 10. Python Build SDK

### 10.1 允许的 API

首期提供网格友好的空间原语：

```python
emit(shape, block, states=None, module=None)
point(x, y, z)
line(start, end)
box(min, max)
plane(origin, width, depth)
extrude(shape, height)
translate(shape, offset)
rotate(shape, quarter_turns)
repeat(shape, count, step)
```

只读世界查询：

```python
world.get_block(pos)
world.inspect_region(box)
world.surface_height(x, z)
world.get_heightmap(box)
```

脚本可以使用普通 Python 循环、条件、函数和数学运算。所有结果必须能在编译期量化为整数方块坐标和可校验 BlockState。

### 10.2 禁止的能力

至少禁止：

```text
os, subprocess, socket, requests, pathlib, shutil, ctypes
threading, multiprocessing, open, eval, exec, __import__
任意 dunder traversal、动态导入、Bridge client、WebSocket、Minecraft command
```

SDK 不提供 `world.set_block`、`world.fill`、`run_command` 等即时写入 API。`emit` 只产生 HIR。

### 10.3 确定性

脚本结果由以下输入共同决定：

```text
script hash + Snapshot hash + Build Context + operation seed + SDK schema version
```

Preview 和 Commit 不得在 Snapshot 或 seed 不同的情况下复用旧结果。随机数只能来自 Harness 注入的 deterministic RNG。

## 11. Spatial IR 与编译需求

### 11.1 HIR：空间意图

HIR 保留参数化几何和模块语义，首期至少支持 `Point`、`Line`、`Box`、`Plane`、`Extrude`、`Transform`、`Repeat` 和 `FillShape`。

示例：

```json
{
  "schema": "mcbe-spatial-hir/1",
  "module": "foundation",
  "type": "FillShape",
  "shape": {
    "type": "Box",
    "min": [0, 0, 0],
    "max": [14, 0, 10]
  },
  "block": "minecraft:cobblestone"
}
```

### 11.2 MIR：Desired State 与 Diff

MIR 表示在固定 Snapshot 和 Context 下的目标状态，内部实现可使用 Chunk、Palette、Sparse Voxel、RLE 等结构，不要求模型看到内部容器。

Diff 至少区分：

- `place`：当前为空，目标为非空气。
- `replace`：当前和目标均为非空气且状态不同。
- `remove`：目标为空，当前为非空气；首期默认需要额外风险确认。
- `noop`：当前已经满足目标。
- `conflict`：当前状态不再符合 Snapshot 或 expect。

### 11.3 LIR：执行计划

首期支持：

- `FillCommand`
- `SetBlockCommand`
- `SetBlockBatch`
- `Noop`

后续再加入 `CloneCommand`。优化器负责连续矩形合并、重复区域识别、相同状态消除和命令顺序规划；Agent 不负责手工选择 Minecraft 原子命令。

## 12. Operation 模型与状态机

### 12.1 状态

复杂 Build Operation 使用以下状态：

```text
created
  -> snapshotting
  -> sandboxing
  -> compiling
  -> diffing
  -> preview_ready
  -> approval_pending
  -> committing
  -> verifying
  -> succeeded
```

失败或中断状态：

```text
cancelled
failed
expired
concurrent_modification
state_unknown
unsupported_capability
```

`STATE_UNKNOWN` 表示外部世界可能已经发生变更，不能自动重试、自动回退或当作明确失败。

### 12.2 Module

Module 至少包含：

- `module_id` / `name`
- `depends_on`
- HIR 摘要
- Desired Block 统计
- Diff 统计
- LIR 执行计划
- 风险级别
- before/after revision
- 验证规则和结果
- Undo 关系

模块默认按依赖顺序提交。一个模块失败时停止依赖它的后续模块，不自动替换其他已成功模块。

### 12.3 异步 Job

复杂操作必须支持：

- `operation_id` 稳定查询。
- 阶段和有限进度。
- 在世界写入前取消。
- 超时、断线和服务重启后的明确恢复状态。
- 不依赖 Agent Worker 一次请求长期持有 WebSocket 或 Bridge。

## 13. Preview、Commit 与 Undo

### 13.1 Preview 输出

Preview 至少包含：

```json
{
  "operation_id": "op_xxx",
  "status": "preview_ready",
  "context": {"dimension": "overworld", "region_revision": "r1"},
  "bounds": {"min": [0, 0, 0], "max": [14, 8, 10]},
  "desired_blocks": 1842,
  "changes": {"place": 1260, "replace": 482, "remove": 100, "noop": 0},
  "execution_plan": {"fill": 27, "setblock": 81, "clone": 0},
  "modules": [],
  "warnings": [],
  "risk": "high"
}
```

模型看到的是 bounded projection；完整 IR、Diff 和 before state 只在受控内部存储或管理员调试接口使用。

### 13.2 Commit

Commit 前必须重新检查：

- Operation 未过期且 Snapshot 可用。
- 当前区域 revision 与 expected revision 一致。
- 目标维度、世界边界和 Chunk 可写。
- 方块预算、保护区和危险方块规则仍通过。
- 审批 owner 和 operation owner 一致。
- 所有模块依赖已满足。

### 13.3 Undo

Undo 记录和检查：

- `before_revision`
- `after_revision`
- 受影响区域
- before state / desired state / actual state
- 执行计划和实际结果
- 原 Operation、Module 和修复链关系

Undo 必须经过授权和风险策略。区域发生后续变更时返回冲突，不提供无保护的强制覆盖；管理员强制撤销必须单独记录原因和审计事件。

## 14. 验证系统

### 14.1 首期规则

- `expected_bounds`
- `block_count`
- `unexpected_holes`
- `floating_blocks`
- `connectivity`
- `protected_region_violation`
- `unsupported_blocks`
- `world_revision`
- 实际 BlockState 与 Desired State 一致性

### 14.2 结果契约

```json
{
  "status": "FAIL",
  "checks": [
    {
      "name": "unexpected_holes",
      "status": "FAIL",
      "score": 0.97,
      "evidence": [[10, 70, 21], [20, 70, 21]],
      "actual": "minecraft:air",
      "expected": "minecraft:oak_planks",
      "hint": "生成只覆盖缺口坐标的 Patch Script"
    }
  ]
}
```

验证必须返回 `PASS`、`FAIL` 或 `UNKNOWN`。Addon 读取失败、世界状态不可确认或 revision 不可用时不能返回 PASS。

### 14.3 修复策略

Harness 不根据错误自动生成建筑修复。Agent 根据验证证据选择：

- 新的 Python Patch Script。
- 一个或多个 `place_block`。
- 重新观察后重建受影响模块。
- 对未通过模块执行 Undo，再重新规划。

每次修复都必须重新 Preview、检查 revision、审批和 Verify。

## 15. 安全、限额与保护策略

### 15.1 资源限制

默认基线：

- 单 Module 最多 10,000 个 Desired Blocks。
- 单 Build Plan 最多 50,000 个 Desired Blocks。
- NOOP 不消耗变更预算，但仍计入分析规模和时间限制。
- AST 节点数、HIR 节点数、Voxel 展开数、CPU 时间、墙钟时间、内存和脚本输出均有限额。
- 超限返回结构化 `LIMIT_EXCEEDED`，不能通过拆成大量原子调用绕过策略。

实际值进入 `config.json`，可由服务器管理员收紧；模型不能自行提高限制。

### 15.2 世界安全

Commit 和 Undo 必须检查：

- 当前 dimension 与 Build Context 一致。
- 世界边界和有效坐标范围。
- Chunk 已加载且允许写入。
- Spawn、玩家领地、管理员区域和其他保护区。
- `command_block`、`structure_block`、`jigsaw`、`bedrock`、TNT、lava、portal 等危险或高权限方块。
- `expect` / Snapshot 与当前世界状态一致。

### 15.3 风险等级

工具目录继续把世界变更标记为高风险；Operation 另外根据变更规模、替换/删除、危险方块、保护区和跨区范围动态分级：

- `LOW`：只读或 Preview。
- `MEDIUM`：小规模、仅放置、无保护区和危险方块触碰。
- `HIGH`：普通 Commit、替换非空气、较大模块或多模块计划。
- `DANGEROUS`：危险方块、保护区、删除大量状态、管理员强制 Undo 或无法证明安全边界。

所有级别都受 Host policy；`DANGEROUS` 不允许模型自动 Commit。

## 16. Sandbox 需求

### 16.1 隔离模型

Python Build Script 在独立 Sandbox Worker 执行，宿主 Agent Worker 不直接 `exec` 玩家代码。Sandbox 只接受版本化脚本输入和序列化 Snapshot，输出序列化 HIR 或结构化脚本错误。

### 16.2 防护层

1. `ast.parse` 和 AST precheck：拒绝 import、动态执行、dunder 访问、危险属性链、global/nonlocal 等。
2. 白名单 builtins 和 `mcbuild` SDK。
3. 独立进程和取消机制。
4. CPU、墙钟、内存、IR 节点、体素和输出限制。
5. 不提供文件、网络、Socket、系统命令或 Bridge 对象。
6. 通过 deterministic seed 约束随机性。

AST 检查不是唯一的安全边界；真正的隔离依赖 Worker 进程和资源限制。

## 17. Trajectory 与审计

### 17.1 Trajectory 事件

每个 Operation 至少关联：

- 玩家、连接、会话、Trace、Run、Attempt 和 Operation 身份。
- 原始意图的 bounded summary。
- Build Context、Snapshot hash、region revision 和 seed。
- Script hash、SDK schema version 和 Sandbox 结果。
- HIR / MIR / LIR 的统计和受限摘要。
- Diff、执行计划、审批和 Commit 事件。
- 实际结果、Verify 证据、Patch 和 Undo 关系。

### 17.2 存储边界

- 不保存全世界快照。
- before/after 只保留受影响区域的有限、压缩或可恢复表示。
- 脚本正文、玩家消息和完整工具结果遵守现有 Trace / audit 脱敏、白名单和长度策略。
- 配置保留期限、最大记录大小和是否包含详细内容。
- Trace/审计写盘失败不能阻塞 Agent 主路径，只增加 dropped/write_failed/gap 计数或告警。

## 18. MCBEWS/1 与 Addon/SDK 需求

### 18.1 兼容原则

- 运行时继续使用 `mcbews:bridge_req`、`mcbews:text_resp` 和 `MCBEWS|*`。
- 不引入 `mcbeai:*` 或 `MCBEAI|*` 作为旧兼容分支。
- 不混淆 `MCBEWS/1`、capability request schema、session schema、text framing 和 DDUI persistence 版本轴。
- Host、SDK 和 Addon 必须协同发布 V2 capability；这不是单独修改 Host 即可完成的功能。

### 18.2 建议能力

逻辑能力名可以采用以下方向，最终字段由跨仓库 schema 冻结：

```text
world_snapshot
region_revision
apply_mutation_plan
read_mutation_result
```

每项能力必须包含：能力广告、请求 payload、成功响应、失败响应、超时、取消、大小上限、玩家身份和测试向量。

### 18.3 直接替换策略

- 不提供 `build_harness_v2` 功能开关或旧链路灰度开关。
- `place_block` / `fill_block` 的公共工具名保留，但内部切换到 V2 Operation Runtime。
- 不支持 V2 capability 的 Addon 返回 `UNSUPPORTED_CAPABILITY`，不能静默降级为旧执行路径。
- 发布顺序必须先完成 SDK wheel、manifest/capability contract 和 Addon，再切换 Host 默认链路。
- Addon 能力缺失、Bridge 超时和世界写入不确定仍按结构化状态处理，不能自动重复变更。

## 19. 现有代码映射

### 19.1 Host / Agent

| 当前位置 | V2 责任 |
|---|---|
| `services/agent/tools.py` | 保留原子工具；注册 Build Context、Script、Operation、Verify、Undo 工具 |
| `services/agent/block_ops/tools_impl.py` | 复用位置、方块、expect、states、preflight 和结果投影；接入统一 Mutation Runtime |
| `services/agent/block_ops/bridge.py` | 统一 Addon 响应、`STATE_UNKNOWN`、`UNSUPPORTED_CAPABILITY`、read-back 和 mutation result 映射 |
| `services/agent/harness/catalog.py` | 更新工具意图、风险、适用/禁用场景、复杂操作引导和参数预览策略 |
| `services/agent/harness/prompting.py` | 明确“单格用 place、简单 AABB 用 fill、复杂操作用 Python Build Script”的决策树 |
| `services/agent/harness/execution.py` | 扩展 Operation 审批、幂等、owner、状态恢复、审计和 no-fallback 约束 |
| `services/agent/worker.py` | 通过异步 Job 管理，不在 Hook 或 Worker 锁内等待长时间世界操作 |
| `services/gateway/server.py` | 组装 Sandbox、Operation Store、Snapshot Provider 和新 bridge capability client |
| `services/gateway/session_store.py` | 保存玩家级 Build Context、配额和审批偏好，不使用连接级当前玩家 |
| `services/agent/trace.py` / `harness/audit.py` | 关联 bounded Trajectory、工具摘要和失败证据，复用现有脱敏投影 |

### 19.2 Addon / SDK

| 层 | V2 责任 |
|---|---|
| SDK | 维护 MCBEWS/1、capability manifest、请求/响应 framing、出站预算和 delivery |
| Addon router / capability registry | 广告和路由 Snapshot、Revision、Mutation Plan、Result 能力 |
| Addon block capabilities | 在 Minecraft API 边界读取区域、计算 revision、执行受控计划并返回摘要 |
| Addon tests | 校验能力广告、payload、分片、玩家路由、超时和未知 capability |
| Host tests | 校验 SDK wheel contract、Host mapping、玩家隔离、审批、状态未知和旧工具回归 |

## 20. 配置需求

普通配置进入 `config.json` / `config.example.json`，敏感值仍只进入 `.env`。建议新增配置分组：

```json
{
  "build_harness": {
    "enabled": true,
    "sandbox_worker_count": 1,
    "script_timeout_seconds": 10,
    "script_memory_mb": 128,
    "max_ir_nodes": 50000,
    "max_voxel_expansion": 50000,
    "max_changed_blocks_per_module": 10000,
    "max_desired_blocks_per_plan": 50000,
    "operation_ttl_seconds": 900,
    "trajectory_retention_days": 14,
    "detailed_trajectory": false,
    "default_approval_policy": "risk_based"
  }
}
```

说明：`enabled` 表示服务是否具备 V2 能力，不是玩家级灰度开关；V2 发布后不提供旧执行链切换选项。具体字段应由 Settings 模型验证，配置错误在启动边界明确失败。

## 21. 错误与状态契约

至少定义并稳定投影以下 code：

| Code | 含义 | 是否可自动重试 |
|---|---|---|
| `INVALID_ARGUMENT` | 脚本、坐标、方块、Context 或验证规则非法 | 修正参数后可重试 |
| `SCRIPT_REJECTED` | AST / Sandbox 安全检查拒绝 | 不应盲重试 |
| `SCRIPT_TIMEOUT` | 脚本超时 | 修改脚本后重试 |
| `RESOURCE_LIMIT_EXCEEDED` | CPU、内存、IR 或输出超限 | 缩小计划后重试 |
| `LIMIT_EXCEEDED` | 方块预算或 commandLine 预算超限 | 缩小计划后重试 |
| `PROTECTED_REGION` | 触碰保护区 | 不可自动绕过 |
| `DANGEROUS_BLOCK` | 触碰危险方块 | 需要更高权限或拒绝 |
| `CONCURRENT_MODIFICATION` | Snapshot revision 已变化 | 重新观察和编译 |
| `APPROVAL_REQUIRED` | 等待有效玩家审批 | 等待审批 |
| `APPROVAL_DENIED` | 玩家拒绝 | 不应自动重试 |
| `UNSUPPORTED_CAPABILITY` | Addon / SDK 不支持所需 V2 能力 | 发布匹配版本 |
| `VERIFY_MISMATCH` | 实际状态与期望状态不一致 | Agent 生成补丁 |
| `STATE_UNKNOWN` | 外部写入结果未知 | 禁止自动重复写入 |
| `OPERATION_EXPIRED` | Preview 或审批已过期 | 重新编译 |
| `UNDO_CONFLICT` | Undo 目标区域已被后续修改 | 重新观察或管理员强制处理 |
| `INTERNAL_ERROR` | 未分类内部失败 | 记录诊断并人工处理 |

所有失败返回 bounded message、`retryable`、`external_state_unknown`、`fallback_allowed` 和可选 hint。不能把异常 traceback、token、Authorization 或完整内部响应发给玩家或模型。

## 22. 可观察性与运维

### 22.1 日志字段

V2 结构化日志至少关联：

```text
connection_id, player_name, conversation_id, trace_id,
run_id, operation_id, module_id, capability, state,
region_revision, script_hash, error_code
```

不记录完整脚本、完整聊天正文、密钥或无界工具参数。使用现有 `get_logger()`、redaction 和 preview policy。

### 22.2 运维视图

首期不要求完整 UI，但 `get_operation` 和日志应能回答：

- 谁在什么时间创建了哪个 Operation。
- 绑定了哪个 Snapshot / revision / seed。
- 生成了多少 Desired / Changed / NOOP 方块。
- 哪些模块已提交、验证或撤销。
- 当前是否存在审批、并发冲突或状态未知。
- 失败是否发生在 Sandbox、编译、Bridge、Minecraft 写入还是 Verify。

## 23. 验收标准

### 23.1 产品硬门禁

- 未 Commit 的 Script Preview 产生 0 次 Minecraft 世界写入。
- 沙箱无法访问文件、网络、Shell、Bridge 或任意动态执行能力。
- 保护区和危险方块违规写入为 0。
- 预算超限不会被拆成原子调用绕过。
- Snapshot revision 冲突时不执行旧 LIR。
- 外部写入超时、Addon ACK 不完整或读回不一致不会被报告为成功。
- `place_block` 返回 `verified`、`noop`、`unknown` 或 `mismatch` 的语义可区分。
- Undo 在区域未变化时可恢复；区域变化时拒绝静默覆盖。
- 两名玩家共享一个连接时，Build Context、Operation、审批、历史和响应互不串桶。
- 不支持 V2 capability 的 Addon 得到 `UNSUPPORTED_CAPABILITY`，不进入旧链路。

### 23.2 能力 Smoke Test

开发者可自由选择任务，不固定建筑题目；至少覆盖：

- 一个使用循环、变换或重复模式的未预定义结构。
- 一个读取 Snapshot 后适应地形或已有方块的结构。
- 一个经过 Verify 失败、Agent 生成 Patch、再次 Verify 的修复流程。
- 一个包含多个模块、审批、部分提交和 Undo 的流程。

评估重点是 Agent 的表达能力、空间合理性、地形适应、Preview 可理解性、验证证据和修复质量。评估结果是探索性记录，不替代上述硬门禁。

### 23.3 回归范围

实现阶段必须覆盖与现有契约相邻的测试：

- `place_block` / `fill_block` schema、参数校验、审批恢复和失败映射。
- `STATE_UNKNOWN`、`LIMIT_EXCEEDED`、`UNSUPPORTED_CAPABILITY` 和 fallback 规则。
- `player_name` 双玩家隔离、Operation owner 和审批关联。
- Snapshot revision、模块依赖、Commit、Verify、Undo 和取消。
- Sandbox AST、超时、内存、IR 节点和 deterministic seed。
- MCBEWS/1 capability advertisement、payload、分片、响应重组和 SDK wheel contract。

## 24. 交付阶段

虽然产品发布采用 V2 直接替换，但实现应按以下内部阶段推进，每阶段都必须保留可回退的代码变更点；发布前不开放旧运行时切换开关。

### Phase 0：跨层契约

- 冻结 Build Context、Operation、HIR/MIR/LIR、错误 code 和 capability manifest。
- 明确 Host、SDK、Addon 的版本轴和发布顺序。
- 建立单一 Schema 和 fixtures。

### Phase 1：Sandbox 与 SDK

- 独立 Worker、AST precheck、资源限制和 deterministic RNG。
- 实现有限空间原语和 `emit`。
- 输出可序列化 HIR。

### Phase 2：Snapshot、编译和 Preview

- Snapshot / revision provider。
- HIR 到 Desired State / Diff / LIR。
- NOOP、预算、保护区、危险方块、维度和世界边界 preflight。
- 异步 Operation Job 和 Preview projection。

### Phase 3：Commit、Verify、Undo

- 风险审批、模块依赖、批量 Mutation Plan。
- 实际结果、read-back、结构化 Verify 和 Patch 输入。
- revision 保护的 Undo、取消和状态未知处理。

### Phase 4：Agent 默认路径切换

- 更新工具目录和决策提示。
- 保留 `place_block` / `fill_block` 外部契约并接入统一 Runtime。
- Host、SDK、Addon 协同发布。
- 旧 Addon 明确失败，不静默回退。

### Phase 5：能力增强

- 更丰富的观察投影、Blueprint、验证规则、Pattern、Boolean 和视觉能力。

## 25. 主要风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| 直接替换导致旧 Addon 不可用 | 现有世界无法建造 | 先完成 SDK/Addon/Host 协同发布；能力缺失显式报错 |
| Python Sandbox 逃逸或资源耗尽 | 影响宿主服务和服务器 | 独立 Worker、AST、白名单、进程和资源限制 |
| Minecraft 没有原生 revision | 并发覆盖玩家修改 | Addon 计算受影响区域 hash / revision，并在 Commit 前重读 |
| 大型 Snapshot 占用内存 | Worker 延迟或崩溃 | Palette、Chunk、RLE、压缩和严格展开上限 |
| Preview 与 Commit 结果不一致 | 误改世界 | 固定 Snapshot、seed、Context 和 schema version；版本变化拒绝 Commit |
| BlockState 不兼容 | 建筑视觉或结构错误 | 统一 Bedrock ID/State registry，拒绝 Java NBT，验证实际读回 |
| 模块依赖错误 | 建筑部分提交后不可继续 | 显式依赖 DAG、模块状态和失败停止策略 |
| Trajectory 含敏感内容 | 隐私和存储风险 | bounded projection、脱敏、保留期和详细内容开关 |
| 只有人工 Smoke Test | 能力回归不易发现 | 保留关键硬门禁自动测试，并逐步沉淀开放任务样例 |

## 26. 最终产品定义

Minecraft AI Build Harness V2 是一个由 MCBE Chat Agent 驱动的空间构建运行时：

```text
Agent 提供算法
Python 提供表达能力
Spatial IR 保存空间意图
Harness 负责安全、Diff、审批、事务和验证
Addon 负责 Minecraft 世界边界
Trajectory 保存可解释的执行证据
```

在这个边界下，`place_block` 仍然是可靠的单格原子工具，`fill_block` 仍然是简单矩形 primitive；复杂建筑不再依赖 Agent 手工拼接命令，而是通过受限 Python 生成可观察、可预览、可验证、可撤销的世界变更。
