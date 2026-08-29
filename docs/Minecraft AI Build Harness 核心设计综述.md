# Minecraft AI Build Harness 核心设计综述

> 本文仅综合《Minecraft AI Build Harness 设计文档》和《Minecraft AI Build Harness 设计文档-v2.md》的核心内容，不引用《Minecraft AI Build Harness V2 PRD.md》。本文将两版设计整理为一套可独立阅读的统一方案，并单独说明从初版到 v2 的关键演进。

## 1. 执行摘要

Minecraft AI Build Harness 的目标，不是让模型学会拼接 `/fill`、`/setblock`、`/clone` 命令，也不是预置一套覆盖所有建筑类型的高层工具，而是构建一个面向 AI Agent 的 **Minecraft Spatial Construction Runtime**（Minecraft 空间建造运行时）。

它需要同时解决两类问题：

- 让 Agent 拥有足够开放的空间表达能力，能够处理未预定义的建筑、参数化结构、复杂纹样和地形自适应算法；
- 把真实世界修改权留在运行时中，使任何建造行为都可观察、可预览、可验证、可回滚，并受到安全和并发约束。

两版设计共同坚持的核心闭环是：

```text
Observe
  ↓
Plan
  ↓
Preview
  ↓
Commit
  ↓
Diff
  ↓
Verify
  ↓
Repair
```

v2 的关键变化不在闭环本身，而在 Agent 的主要表达方式：

- 初版以 `build_wall`、`build_floor`、`add_opening` 等 Semantic Tool 为主；
- v2 改为由 Agent 编写受限 Python 建造程序，通过通用空间 SDK 产生 Spatial IR；
- Semantic Component 不再无限扩张为 MCP Tool，而是下沉为可复用、可扩展的标准库；
- Minecraft 原子命令不直接暴露给 Agent，而由编译器根据 Desired State 和 World Diff 自动选择。

因此，长期核心不是 MCP、Python 或 Minecraft 命令中的任何一项，而是：

```text
World Model
+ Spatial IR
+ Desired State
+ Compiler
+ Transaction
+ Verification
```

按照项目共享术语，本文所述系统属于 **运行时 Harness** 的空间建造子系统，不是面向 coding agent 的开发维护 Harness。

## 2. 项目定位与目标

### 2.1 最终定位

项目应定义为：

> 一个为 AI Agent 设计的 Minecraft 空间建造运行时。

MCP 只是外部客户端访问运行时的 Adapter；Python 是 Agent-facing Language；`fill`、`clone`、`setblock` 等只是 Minecraft Adapter 最终执行的 Backend Instructions。

同一个 Runtime 将来可以被不同入口复用，例如：

- MCP Client；
- MCBE Chat Agent；
- OpenAI 或其他 Agent SDK；
- Web Editor；
- Minecraft NPC Builder；
- CLI；
- Schematic、Structure File 或 Blueprint 导入器。

所有入口最终都应收敛到统一的 Spatial IR、Desired State、Diff、Transaction 和 Verification 链路。

### 2.2 能力目标

系统应支持 Agent：

- 读取单方块、区域、切片、高度图、实体和 Block Entity；
- 理解已有地形与建筑，而不是在空白世界假设上施工；
- 把自然语言要求整理为 Build Spec 和模块化 Build Plan；
- 使用局部坐标、方向和 Anchor 编写通用建造算法；
- 表达规则或不规则结构、重复纹样、布尔几何和地形自适应结构；
- 在实际写入前查看影响范围、覆盖风险和执行计划；
- 分模块提交并检查真实 World Diff；
- 使用机器可计算的规则验证结构；
- 在失败后生成小范围 Repair Script；
- 撤销或回滚错误操作；
- 在世界被玩家并发修改时拒绝基于旧状态覆盖新状态。

最终衡量系统上限的不是模型能否第一次把建筑完全设计正确，而是以下能力的组合：

```text
Observation Quality
+ Spatial Abstraction
+ Expressive Construction Language
+ Verification Quality
+ Recovery Capability
```

## 3. 初版到 v2 的关键演进

| 维度 | 初版重点 | v2 统一方向 |
| --- | --- | --- |
| Agent 的主要表达方式 | 调用 Typed Semantic Tool | 编写 Sandboxed Python Build Script |
| Semantic 能力 | `build_wall`、`build_roof` 等作为一级 Tool | `wall`、`roof`、`arch` 等作为标准库组件 |
| 原子操作 | `set_blocks`、`fill_region` 等保留为 Escape Hatch | 下沉到 Minecraft Adapter 和 LIR，不直接写世界 |
| 核心抽象 | Semantic API + Spatial Runtime | Spatial IR + Desired State + Compiler |
| MCP Tool 数量 | 约 13～20 个 | 约 10 个核心 Tool |
| 扩展方式 | 为新建筑语义增加 Tool | Agent 编写算法，成功后可沉淀为库或 Skill |
| 执行边界 | Tool Preview 后执行 | Script 只编译为 Pending Operation，显式 Commit 才写世界 |

初版的价值并未被 v2 否定。以下设计全部保留，并在 v2 中成为更底层、更明确的运行时能力：

- Observation First；
- 分层获取世界上下文；
- Build Context、局部坐标和 Anchor；
- BlockState 解析；
- Preview、Diff、Transaction 和 Undo；
- Desired State 与幂等性；
- Block Budget、Protected Region 和危险方块保护；
- Snapshot、Revision 和并发检测；
- Verification、Visual Observation 与 Repair；
- 模块化建造和 Trajectory 记录。

这次演进真正解决的是“表达能力上限”问题。纯 Semantic API 可靠但会随着建筑类型增长而膨胀；纯 Minecraft Bash 足够自由，却绕过 Preview、Diff、安全、优化和回滚。v2 选择的中间路线是：

> 给 Agent 一门通用的空间编程语言，但不给它直接修改世界的能力。

## 4. 核心设计原则

### 4.1 Observation First

Agent 在行动前必须知道世界当前是什么样；行动后必须能够观察真实结果。Observation 的优先级不低于 Mutation，且第一版应优先把世界“看清楚”。

### 4.2 意图与真实写入分离

Python Build Script、Semantic Component 或其他输入都只能声明 Spatial Intent / Desired State。真实 Minecraft 写入必须经过编译、Diff、验证、审批式 Commit 和 Transaction 层。

### 4.3 Desired State 优先于命令序列

Agent 表达“这个区域最终应该是什么状态”，而不是规定每一条底层命令。Runtime 比较 Current State 与 Desired State，只对差异生成修改：

```text
Current State + Desired State → Place / Replace / Remove / NOOP
```

这天然支持幂等性，也让重试和修复更安全。

### 4.4 空间数学属于 Runtime

局部坐标到世界坐标的变换、方向映射、中心点、墙法线、BlockState 解析和邻居状态处理，不应由 LLM 每次临时推算。

### 4.5 大修改必须 Preview，世界修改必须 Commit

编译脚本不等于执行世界变更。Preview 与 Commit 是明确的 Planning Boundary，任何较大操作都应先返回变更规模、影响范围、覆盖风险、警告和底层执行计划。

### 4.6 所有修改都必须 Transactional

每次修改都要有 `operationId`、前后状态、Diff、Revision 和 Undo 能力。复杂建筑应按 foundation、frame、walls、roof 等模块独立提交，不应把整栋建筑变成一个不可分割的操作。

### 4.7 安全规则由空间建造运行时强制执行

Block Budget、Protected Region、危险方块、Chunk、Dimension、World Border、并发 Revision 和 Sandbox 限制都必须是代码级边界，不能只依赖 Prompt 提醒。

### 4.8 数据负责正确性，视觉负责外观

方块数据、Slice、Diff 和 Verification 用来判断结构是否正确；截图和渲染视图用来判断比例、构图、风格和审美。视觉不应被当作几何真值。

### 4.9 MCP 是 Adapter，不是 Runtime

核心能力必须独立于 MCP 协议存在，以便未来支持不同 Agent、UI 和导入入口。

## 5. 总体架构

```text
                         User Intent
                              │
                              ▼
                          AI Agent
                    ┌─────────┴─────────┐
                    ▼                   ▼
             Observation Tools    Python Build Script
                    │                   │
                    │                   ▼
                    │             Python Build SDK
                    │                   │
                    └─────────┬─────────┘
                              ▼
                 Build Context + World Snapshot
                              │
                              ▼
                         Spatial HIR
                              │
                   Lowering / Voxelization
                              │
                              ▼
                  Voxel Desired State (MIR)
                              │
               Current World ─┼─→ World Diff
                              │
                 Validate / Optimize / Plan
                              │
                              ▼
                     Pending Operation
                       Preview Result
                              │
                            Commit
                              │
                              ▼
                     Transaction Layer
                              │
                              ▼
                 Minecraft Mutation IR (LIR)
                    ┌─────────┼─────────┐
                    ▼         ▼         ▼
                 setblock    fill     clone
                    └─────────┼─────────┘
                              ▼
                       Minecraft World
                              │
                              ▼
                     Observe Actual State
                              │
                              ▼
                           Verify
                      ┌───────┴───────┐
                      ▼               ▼
                    PASS            REPAIR
                                      │
                                      └────→ AI Agent
```

### 5.1 分层职责

系统可以按以下边界理解：

1. **Agent 层**：理解需求、设计建筑、拆分模块、编写算法和决定如何修复。
2. **表达层**：Python Build SDK 和标准库负责表达 Geometry、Transform、Pattern、World Query 和 Block Intent。
3. **世界模型层**：Build Context、Anchor、World Snapshot 和 Region Revision 提供稳定的空间与状态基础。
4. **编译层**：把 HIR 降低为 Desired State，计算 World Diff，并选择高效的 Minecraft Mutation IR。
5. **执行治理层**：执行 Sandbox、安全校验、资源限制、Preview、Transaction、Concurrency 和 Undo。
6. **验证层**：读取真实结果，运行结构检查，向 Agent 返回可定位的错误。
7. **Adapter 层**：只负责读取世界并执行 `setblock`、`fill`、`clone` 或批量操作。

## 6. Agent 的空间表达模型

### 6.1 Build Spec 与 Build Plan

复杂建筑不应从 Prompt 直接跳到方块修改。Agent 应先产出 Build Spec，描述：

- 尺寸、朝向、楼层和风格；
- 材料 Palette；
- 关键空间约束；
- foundation、frame、walls、openings、roof、interior 等模块。

Build Spec 是设计层；每个模块的 Python Build Script 是实现层。Build Plan 再规定模块的先后关系以及每个模块的 Preview、Commit、Verify 和 Repair 生命周期。

### 6.2 Build Context

Agent 不应长期直接操作巨大且易错的世界坐标。Runtime 先建立 Build Context：

```json
{
  "origin": [1387, 72, -921],
  "facing": "north",
  "size": [32, 20, 32]
}
```

后续统一使用局部坐标：

```text
u = right
v = up
w = forward
```

Runtime 负责 `Local → World` 变换。Agent 使用 `FORWARD`、`BACKWARD`、`LEFT`、`RIGHT` 等局部方向，而不是在每一处重新推导 north、south、east、west。

### 6.3 Anchor

Build Context 可保存 `entrance`、`center`、`roof_center`、`north_wall`、`main_hall` 等命名 Anchor。Anchor 可以包含点、平面、边界和法线，使后续模块引用稳定的空间语义，减少 Coordinate Drift 和 Direction Drift。

### 6.4 Python Build SDK

核心 SDK 应保持底层和通用，主要提供：

```text
Geometry:  point, line, plane, box, polygon, volume
Boolean:   union, subtract, intersect
Transform: translate, rotate, mirror, scale
Pattern:   repeat, array, grid, radial
Output:    emit
Query:     block, region, surface_height
```

Minecraft 是离散体素环境，因此 rotate、scale、curve 等连续概念最终必须经过离散化，但 Agent 不需要直接处理离散化细节。

普通 Python 循环和函数让 Agent 能够创建螺旋塔、曲面屋顶、参数化塔楼、复杂纹样或地形自适应地基，而不要求 Runtime 预先知道这些建筑类型。

### 6.5 Semantic Component 的位置

`wall`、`floor`、`roof`、`arch`、`stairs`、`column` 等组件仍然有价值，但应属于 `mcbuild.std` 一类的 Build Standard Library，而不是不断扩张的核心 MCP Tool Surface。

这样可以形成清晰边界：

```text
Semantic Component = Library
Spatial Runtime = Kernel
```

Agent 还可以临时编写新的 Semantic Component。经过 Build、Verify 和用户接受的算法，未来可以沉淀为可复用的 Build Skill，但不应反向污染 Runtime Kernel。

标准库还可以逐步提供空间关系组件，例如 `place_on_surface`、`place_against_wall`、`align_to`、`center_on`、`fit_inside` 和 `distribute`。这类组件封装常见的支撑面、法线、对齐和分布规则，但最终仍只产生 Spatial Intent。

### 6.6 Python 不能直接修改世界

以下能力不得提供给 Build Script：

```python
world.set_block(...)
world.fill(...)
server.run_command(...)
```

`emit(shape, block=...)` 的含义只是声明 Desired Spatial State。Script 的唯一有效产物应是可序列化的 Spatial IR；Runtime 保留是否提交、如何提交和能否提交的最终决定权。

### 6.7 World Query 使用不可变 Snapshot

Python 可以读取 `world.surface_height(x, z)`、`world.block(...)` 等查询，但读取对象应是脚本开始时生成的不可变 World Snapshot，而不是实时世界。

这样可以保证：

- 同一输入可重复；
- Preview 与 Commit 可比较；
- 脚本便于调试；
- 不会在单个脚本中形成难以控制的“读—改—再读—再改”；
- 可通过 Region Revision 检测并发修改。

确实需要多阶段适应时，应由 Agent Loop 分成多个脚本：Script A → Preview → Commit → Observe → Script B。

## 7. Spatial IR 与编译链

Spatial IR 是整个系统最值得长期维护的核心。推荐使用三级 IR。

### 7.1 HIR：High-Level Spatial IR

HIR 保留参数化空间语义，例如：

```text
Point
Line
Plane
Box
Extrude
Transform
Repeat
Boolean
Pattern
FillShape
```

一个大 Box 应尽量保留为单个参数化节点，而不是立即展开成成千上万个方块对象。

### 7.2 MIR：Voxel Desired State IR

MIR 表示坐标到目标 BlockState 的映射，是 Current World 与 Desired State 做 Diff 的基础。大型区域不应简单实现为巨型 Python Dict，而应根据数据分布使用：

- Chunk / Region；
- Palette；
- RLE；
- Sparse Voxel；
- Dense Voxel。

### 7.3 LIR：Minecraft Mutation IR

LIR 是最终可执行操作，例如：

```text
FillCommand
SetBlockCommand
SetBlockBatch
CloneCommand
```

完整链路为：

```text
Python
  ↓
Spatial HIR
  ↓
Voxelization / Lowering
  ↓
Voxel Desired State
  ↓
Current World Diff
  ↓
Mutation Optimization
  ↓
Minecraft LIR
  ↓
fill / clone / setblock
```

### 7.4 延迟 Voxelization

系统不应过早把参数化结构展开成方块集合。HIR 应尽可能参与 Boolean、Collision、Bounds、Budget 和执行规划；只有 Diff 或后端执行确实需要时才展开。长期实现应允许 Parametric IR 与 Voxel IR 共存。

### 7.5 World Diff 与 NOOP

Runtime 将 Desired State 与 Current State 比较，得到：

```text
Place
Replace
Remove
NOOP
```

只有差异进入 Mutation Planner。当前世界已经满足目标时返回 NOOP，从而避免重复写入并获得自然幂等性。

### 7.6 Mutation Optimizer

Optimizer 相当于编译器的 Instruction Selection：

- 识别连续长方体并合并为 `fill`；
- 对离散点选择 `setblock` 或批量写入；
- 识别重复区域或现有相同区域，必要时选择 `clone`；
- 丢弃 NOOP；
- 在保证结果一致的前提下减少命令数量和世界写入量。

因此，即使 Agent 用循环表达 400 个连续方块，Runtime 也可以自动编译为一个 `fill`。

## 8. Observation System

Observation 应提供从粗到细的分层入口，避免把整个三维世界一次性塞入模型上下文。

### 8.1 `get_block`

查询单个方块的精确状态，包括：

- 世界坐标；
- Block ID；
- 完整 BlockState；
- Block Entity 信息。

它适合定位单点问题，不适合区域扫描。

### 8.2 `inspect_region`

区域查询至少支持：

```text
summary
surface
detailed
```

Summary 应返回 Bounds、Size、Block Counts、Occupied Bounds、Entities 和 Block Entities，而不是默认返回每个 Block JSON。

详细数据应使用 Palette + Layer + RLE 等紧凑表示，降低 Token 成本并保留空间结构。

### 8.3 `get_slice`

二维 Slice 是体素世界中对 LLM 特别友好的表示，可用于：

- Top View；
- Front / Side Elevation；
- Cross Section；
- 判断门窗、对称、墙体完整性、内部空间和屋顶截面。

它应是一级 Observation Tool，而不是隐藏在 Python 输出中的辅助函数。

### 8.4 `get_heightmap`

返回 `x,z → surface y`，用于选址、坡度分析、Foundation、支柱和 Terraform，也可通过只读 Snapshot 暴露给 Python Build Script。

### 8.5 Visual 与 Blueprint Observation

后期可以加入：

- `render_view`：front、back、left、right、top、isometric；
- `render_blueprint`：Floor Plan、Elevation、Section。

Blueprint 对空间布局的表达通常比大量三维 JSON 更稳定。Visual Observation 用于比例、构图、材料和风格评价；精确缺块、穿模、悬空等问题仍由 Block Data、Slice、Diff 和 Verification 判断。

## 9. BlockState 与世界状态处理

### 9.1 Typed BlockState

Agent 不应手写易错的 BlockState 字符串。SDK 应提供类型化构造，例如用材质、局部朝向和 half 等参数表达楼梯；Runtime 再完成 Local Facing → World Facing → Minecraft BlockState 的解析。

### 9.2 Neighbor State

Fence、Wall、Pane、Stairs、Rail、Redstone、Door、Chest 等方块的最终状态可能依赖邻居。优先流程应是：

```text
Place Desired Blocks
  ↓
Trigger Minecraft / Runtime Neighbor Update
  ↓
Read Actual State
  ↓
Verify
```

不应要求 LLM 手动计算所有连接位。

## 10. Preview、Commit 与 Transaction

### 10.1 `execute_build_script` 只生成 Preview

尽管工具名包含 execute，其语义应是执行受限脚本并编译空间意图，而不是直接修改世界：

```text
Compile Script
  ↓
Generate Desired State
  ↓
Diff
  ↓
Validate
  ↓
Optimize
  ↓
Create Pending Operation
```

Preview 至少返回：

```json
{
  "operationId": "op_9182",
  "status": "preview",
  "affectedBounds": {},
  "desiredBlocks": 1842,
  "changes": {
    "place": 1260,
    "replace": 482,
    "remove": 100
  },
  "executionPlan": {
    "fill": 27,
    "setblock": 81,
    "clone": 0
  },
  "warnings": []
}
```

Agent 可以根据覆盖范围、警告或异常规模修改脚本并重新 Preview。

### 10.2 `commit_operation`

Commit 前重新检查权限、预算、Chunk、Dimension、World Border 和 Region Revision。全部通过后，Transaction Layer 才执行 LIR 并读取真实 After State。

### 10.3 Transaction 记录

每个操作至少应记录：

```text
operationId
beforeRevision / afterRevision
affectedRegion
beforeState
desiredState
actualState
worldDiff
executionPlan
warnings
verification
```

Mutation 结果不能只返回 `success: true`，还应说明实际 Changed Blocks、Bounds、Block Delta、覆盖非空气方块等警告。

### 10.4 模块事务与 Undo

复杂建筑按模块提交：

```text
foundation → frame → walls → openings → floors → roof → interior → details
```

某个模块失败时只回滚该模块，再重新生成或修复，而不是重建整栋建筑。`undo_operation` 使用 Transaction 中保存的 Before State 恢复世界。

## 11. Verification 与 Repair

Observation 告诉 Agent 世界是什么样；Verification 判断特定约束是否满足。第一阶段建议支持：

- unexpected holes；
- floating / unsupported blocks；
- symmetry；
- connectivity；
- expected bounds；
- non-air overwrite；
- protected region violation。

后续可增加：

- room enclosure；
- door connectivity；
- roof coverage；
- interior accessibility；
- lighting；
- spawn safety；
- water leak；
- redstone connectivity。

Verification 结果应包含可定位信息，而不只是 Pass / Fail。例如 Symmetry Check 应给出 Score 和 Mismatch Coordinates。Agent 根据这些结果生成小范围 Repair Script，提交后再次观察并验证。

## 12. 安全、隔离与并发

### 12.1 世界修改安全

Runtime 在 Preview 和 Commit 阶段都应检查：

- **Block Budget**：限制 Desired Blocks 和 Changed Blocks，拒绝异常规模；
- **Protected Region**：World Spawn、Player Claims、Admin Region、重要容器和受保护建筑；
- **Dangerous Blocks**：Command Block、Structure Block、Jigsaw、Bedrock、TNT、Lava、Portal Block 等需要限制或额外权限；
- **World Safety**：Dimension、World Border、Chunk Loaded 和 Chunk Writable；
- **Bounds**：操作必须落在 Build Context 或显式授权范围内。

### 12.2 并发控制

World Snapshot 包含 `regionRevision`，Pending Operation 保存 `expectedRevision`。Commit 时如发现当前 Revision 已变化，应返回 `CONCURRENT_MODIFICATION` 或等价错误，要求 Agent 重新 Inspect 和 Re-plan，而不是覆盖玩家的新修改。

### 12.3 Python Sandbox

Build Script 不能通过普通 `exec()` 加受限 `globals` 直接运行。建议至少采用：

```text
Agent Server
  ↓
Sandbox Worker
  ↓
Restricted Python Runtime
  ↓
Serialized Spatial IR
```

环境允许时进一步使用进程隔离或容器，并限制：

- CPU time；
- wall time；
- memory；
- IR node count；
- geometry complexity；
- voxel expansion；
- desired block count。

至少禁止文件系统、网络、Shell、动态导入和危险反射能力，包括 `os`、`subprocess`、`socket`、`requests`、`pathlib`、`shutil`、`ctypes`、`threading`、`multiprocessing`、`open`、`eval`、`exec` 和 `__import__`。

AST Precheck 可用于拒绝 Import、dunder access、`global`、`nonlocal`、危险 attribute traversal 等语法，但它只是第一层防御，不能代替真正隔离。

### 12.4 确定性随机

随机建筑使用由空间建造运行时提供的 `operationSeed`。同一 Script、Snapshot 和 Seed 应产生同一 IR，确保 Preview 与 Commit 一致，并使失败可复现。

## 13. 推荐的 Agent Build Loop

```text
1. Observe target area
2. Create Build Context
3. Understand user intent
4. Create Build Spec
5. Split structure into modules

FOR EACH MODULE:
    6. Inspect relevant world state
    7. Generate Python Build Script
    8. Execute Script → Preview
    9. Inspect Diff / Warnings
   10. Adjust script if necessary
   11. Commit
   12. Observe actual result
   13. Verify
   14. Repair if needed
   15. Continue

16. Final Observation
17. Final Verification
18. Finish
```

必须避免一次性规划整栋建筑、连续执行大量 Mutation、最后才检查结果。可靠性来自短循环和模块边界，而不是超长的一次性计划。

## 14. 建议的核心 Tool Surface

v2 将 MCP 一级工具控制在约 10 个：

| Tool | 职责 |
| --- | --- |
| `get_block` | 精确读取单方块和 BlockState |
| `inspect_region` | 分层读取区域摘要、表面或详细状态 |
| `get_slice` | 获取平面、立面或截面 |
| `get_heightmap` | 获取地形表面高度 |
| `create_build_context` | 建立 Origin、Facing、Bounds 和 Anchor 环境 |
| `execute_build_script` | 在 Sandbox 中运行脚本并生成 Preview / Pending Operation |
| `commit_operation` | 校验后提交 Pending Operation |
| `get_operation` | 查询 Diff、状态、警告和执行结果 |
| `verify_region` | 执行机器可计算的结构约束检查 |
| `undo_operation` | 使用 Transaction 恢复操作前状态 |

具体建筑语言进入 Python SDK；`setblock`、`fill`、`clone` 等原子能力留在 Adapter / LIR。一级 Observation Tool 继续保留，因为它们的输出格式稳定、Context 可控、可摘要或截断，也更容易促使 Agent 主动观察。

## 15. 组件职责边界

| 组件 | 负责 | 不负责 |
| --- | --- | --- |
| Agent | 需求理解、建筑设计、模块拆分、算法、参数化建造、Repair 决策 | 直接写世界、绕过安全检查 |
| Python SDK | Geometry、Transform、Pattern、Snapshot Query、Spatial Intent | 权限、安全、真实世界 Mutation |
| Standard Library | Wall、Floor、Roof、Arch、Stairs、Patterns 等可复用语义组件 | 定义 Runtime Kernel 或无限扩张 MCP Tool |
| 运行时 Harness / 空间建造运行时 | Sandbox、Coordinate、BlockState、Validation、Desired State、Diff、Transaction、Concurrency、Optimization、Verification | 为每种建筑预制生成器 |
| Minecraft Adapter | 读取真实世界，执行批量操作、`setblock`、`fill`、`clone` | Agent Planning、建筑语义、安全决策 |
| MCP Adapter | 暴露稳定的 Observe、Execute、Commit、Verify、Undo 接口 | 承载核心空间逻辑 |

## 16. 完整示例：两层中世纪房屋

用户要求建造一个 `15×11`、入口朝东的两层中世纪房屋。

1. Agent 用 `inspect_region` 和 `get_heightmap` 发现地面高度、树木和可用区域。
2. 建立 `origin=[100,72,200]`、`facing=EAST` 的 Build Context。
3. 生成 Build Spec：Cobblestone Foundation、Spruce Frame、White Terracotta Wall、Spruce Roof，并拆成多个模块。
4. Foundation Script 用一个 Box 声明地基；Frame Script 用 Python 循环声明柱子；Wall Script 用 `subtract` 从墙面扣除入口。
5. `execute_build_script` 把脚本编译为 HIR、Desired State 和 World Diff，返回 Pending Operation，例如 384 个变更、9 个 `fill` 和 20 个 `setblock`。
6. Agent检查警告后调用 `commit_operation`。
7. Agent 用 Front Slice 观察实际结构，发现窗口不对称。
8. `verify_region` 返回具体不对称坐标。
9. Agent 编写一个只修改窗口的小型 Repair Script，Preview、Commit 并再次 Verify。
10. 当前模块通过后继续屋顶和细节模块，最终进行完整 Observation 和 Verification。

这个例子体现的不是某个 `build_house` Tool，而是通用闭环：

```text
Design → Script → Compile → Preview → Commit → Observe → Verify → Repair
```

## 17. Trajectory 与可观测性

系统从第一版起就应记录完整 Trajectory：

```text
Intent
Observation
Build Spec
Script
HIR
Desired State
Diff
Execution Plan
World Before / After
Verification
Repair
User Feedback
```

这些数据可用于：

- Failure Pattern 分析；
- Tool 和 SDK 设计评估；
- Agent Benchmark；
- Prompt 优化；
- Model Evaluation；
- 训练数据；
- 从成功轨迹中挖掘可复用 Build Skill。

Trajectory 应服务于可追踪、调试和评估，而不是只记录一个缺少上下文的成功布尔值。

## 18. MVP 与演进路线

### 18.1 第一版最小闭环

第一版不需要完整三级编译器或复杂建筑标准库。建议严格控制为：

```text
Agent Python
  ↓
Simple Spatial IR
  ↓
Voxel Desired State
  ↓
World Diff
  ↓
Cuboid Merge
  ↓
fill / setblock
```

最小范围包括：

- IR：Point、Line、Box、Block Assignment；
- Observation：`get_block`、`inspect_region`、`get_slice`；
- Context：局部坐标、Facing 和基础 World Transform；
- Python：`emit`、Box、Line、普通循环、基础 Snapshot Query；
- Sandbox：独立 Worker、AST 检查、超时、内存和 IR / Block 上限；
- Diff：Place、Replace、Remove、NOOP；
- Compiler：连续长方体识别，输出 `fill` 和 `setblock`；
- Transaction：Preview、`operationId`、Commit、Undo、Revision；
- Verification：Holes、Floating Blocks、Symmetry、Bounds、Connectivity。

这已经足以形成完整的 Observe → Build → Preview → Commit → Verify → Repair 闭环。

### 18.2 推荐阶段顺序

| 阶段 | 交付内容 | 目标 |
| --- | --- | --- |
| Phase 0 — Minecraft Adapter | Read Block / Region、Fill、Setblock、Batch | 打通真实世界读写，但不直接暴露给 Agent |
| Phase 1 — Observation | `get_block`、`inspect_region`、`get_slice`、`get_heightmap` | 让 Agent 准确看见世界 |
| Phase 2 — Build Context | Local Coordinate、Orientation、Anchor、World Transform | 消除世界坐标和方向漂移 |
| Phase 3 — Spatial IR | Point、Line、Box、FillShape、Transform | 建立运行时核心表示 |
| Phase 4 — Python SDK | `emit`、Geometry、Loop、Snapshot Query | 提供开放的空间编程能力 |
| Phase 5 — Sandbox | AST、隔离、Timeout、Memory、IR Limit | 建立代码执行安全边界 |
| Phase 6 — World Diff | Desired vs Current、Place / Replace / Remove / NOOP | 获得幂等和最小修改集合 |
| Phase 7 — Mutation Compiler | Cuboid Detection、`fill`、`setblock` | 高效翻译世界差异 |
| Phase 8 — Transactions | Preview、Commit、Undo、Revision | 获得可追踪、可恢复和并发安全的修改 |
| Phase 9 — Verification | Holes、Floating、Symmetry、Bounds、Connectivity | 让 Agent发现并定位施工错误 |
| Phase 10 — Standard Library | Wall、Floor、Arch、Roof、Stairs、Pattern | 提升常见任务效率，不污染 Kernel |
| Phase 11 — Visual Feedback | Render View、Blueprint | 补充审美与构图反馈 |

## 19. 明确的非目标

以下方向不属于该设计的长期核心：

- 把项目做成 `/fill` 和 `/setblock` 的薄 MCP 包装；
- 给 Agent 提供可以直接修改真实世界的 Python Root Shell；
- 为 House、Castle、Temple、Pagoda 等每种建筑持续增加核心 Tool；
- 默认向模型返回完整区域的逐方块 JSON；
- 依赖 Screenshot 定位精确方块错误；
- 在一个脚本中边修改实时世界边继续读取并决策；
- 用 Prompt 代替预算、权限、并发和 Sandbox 的硬约束；
- 把整栋复杂建筑作为一次不可拆分、不可局部回滚的 Mutation。

## 20. 结论

两版设计最终收敛到同一个判断：可靠的 Minecraft AI 建造能力不能只依赖模型本身的空间推理，也不能只靠更多 Prompt 或更多预制工具。系统必须把复杂世界改造成 Agent 能够持续观察、表达意图、预览影响、提交变更、验证结果和修复错误的 Runtime。

初版建立了 Observation、Spatial Abstraction、Semantic Tool、Transaction、Diff、Verification 和 Safety 的完整基础；v2 则进一步解决开放性问题，把 Agent 的主要表达方式升级为 Sandboxed Python，把 Spatial IR 与 Desired State Compiler 提升为核心，并把 Semantic Tool 重定位为标准库。

最终方案可以概括为：

```text
AI Agent
  ↓
Sandboxed Python + Spatial SDK
  ↓
Spatial Intent / IR
  ↓
Desired World State
  ↓
Diff + Validation + Optimization
  ↓
Pending Operation + Transaction
  ↓
Minecraft Mutation
  ↓
Observe + Verify + Repair
```

它既保留 Coding Agent 式的开放创造能力，也保留运行时闭环最关键的可控性：**世界修改的最终控制权始终属于 Runtime，而不是 Agent 生成的代码。**

## 参考范围

- [Minecraft AI Build Harness 设计文档-v2](./Minecraft%20AI%20Build%20Harness%20设计文档-v2.md)
