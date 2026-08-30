可以。结合我们前面从 **Blender MCP → Minecraft Build Harness → Semantic Tool vs Bash → Python Build Script → Spatial IR** 一路讨论下来的结论，我认为现在方案已经比较清晰了：

> **不做一个“Minecraft MCP 工具集合”，而是做一个面向 AI Agent 的 Minecraft Spatial Construction Runtime。**
> 
> Agent 可以自由编写受限 Python 建造程序、自己发明建造算法；Harness 负责把这些程序编译成安全、可验证、可回滚、尽量高效的 Minecraft 世界变更。

下面是我认为目前比较完整的 **V2 方案**。

---

# Minecraft AI Build Harness V2

## 1. 项目目标

这个项目不是为了让 AI 学会调用：

```text
/setblock
/fill
/clone
```

也不是为了预制：

```text
build_house()
build_castle()
build_pagoda()
```

而是希望最终做到：

```text
自然语言需求
    ↓
AI 自主设计
    ↓
AI 自主编写建造算法
    ↓
Harness 理解空间意图
    ↓
安全执行
    ↓
观察世界变化
    ↓
验证
    ↓
AI 自主修复
```

最终 Agent 应该能够面对**事先没有预定义过的建筑类型**。

例如：

```text
“建一个沿悬崖生长的非对称神社，
屋顶随着高度逐渐上翘，
左右栈道根据地形自动调整支撑柱。”
```

Harness 不需要提前存在：

```text
build_cliff_shrine()
```

Agent 应该能够自己写算法解决。

---

# 2. 从 Blender MCP 得出的核心经验

我们调研 Blender MCP 后，最重要的结论并不是：

> MCP 可以控制 Blender。

而是：

> 一个好的 Agent Harness 必须让模型形成闭环。

也就是：

```text
Intent
 ↓
Observe
 ↓
Plan
 ↓
Act
 ↓
Observe Result
 ↓
Measure
 ↓
Verify
 ↓
Correct
```

Blender MCP 的准确性主要来自：

```text
get_scene_info
get_object_info
viewport screenshot
world bounding box
execute code
```

组合形成：

```text
操作
↓
查看实际结果
↓
发现错误
↓
修改
```

而不是要求模型：

> 一次把 Blender Python 写得完全正确。

Minecraft Build Harness 应该继承这一理念。

因此：

**Observation 的重要性甚至高于 Mutation。**

---

# 3. 我们最终不采用“纯 Semantic Build API”

之前考虑过大量类似：

```text
build_wall
build_floor
build_roof
build_arch
build_window
build_column
```

这样的 MCP Tools。

它们有一个明显优势：

```text
参数清晰
容易验证
可靠
容易 Preview
容易做 Diff
```

但是长期问题非常严重。

建筑空间是开放的。

一旦遇到：

```text
曲面屋顶
非规则建筑
参数化塔楼
地形自适应建筑
复杂重复纹样
自定义几何
```

就会开始增加：

```text
build_curved_roof
build_pagoda_roof
build_irregular_wall
build_cliff_foundation
...
```

最后 Harness 自己逐渐变成：

> 一个预制建筑生成器。

这会限制 Agent 的能力上限。

因此 V2 中：

> **Semantic Build API 不再是 Agent 的主要能力边界。**

---

# 4. 也不采用纯 Minecraft Bash

另一个极端是：

```text
execute_command("/fill ...")
execute_command("/setblock ...")
```

或者直接给 Agent：

```python
mc.setblock(...)
mc.fill(...)
```

这种方案表达能力极强。

就像 Coding Agent 使用 Bash。

但是它的问题也很明显。

例如模型写：

```python
for x in range(1000):
    for y in range(1000):
        for z in range(1000):
            mc.setblock(x, y, z, "stone")
```

如果它能够直接写世界：

Harness 很难在执行之前：

```text
检查修改规模
计算 Diff
检查 protected region
Preview
Rollback
优化命令
检查并发
```

因此我们不会给 Agent 一个真正的：

```text
Minecraft root shell
```

而是：

> **Sandboxed Python Construction Environment**

---

# 5. 最终方案：Python + Spatial Compiler

核心架构：

```text
                    User
                      │
                      ▼
                 AI Agent
                      │
               writes Python
                      │
                      ▼
          ┌────────────────────┐
          │ Python Build SDK   │
          │                    │
          │ Geometry           │
          │ Transform          │
          │ Pattern            │
          │ World Query        │
          │ Block Intent       │
          └─────────┬──────────┘
                    │
                    ▼
            Sandboxed Runtime
                    │
                    ▼
             Spatial HIR
                    │
                    ▼
              Geometry IR
                    │
                    ▼
               Voxel IR
                    │
                    ▼
              World Diff
                    │
          ┌─────────┴────────┐
          ▼                  ▼
       Validate            Optimize
          │                  │
          └─────────┬────────┘
                    ▼
          Minecraft Mutation IR
                    │
         ┌──────────┼──────────┐
         ▼          ▼          ▼
       fill       clone    setblock
                    │
                    ▼
             Minecraft World
                    │
                    ▼
                Observe
                    │
                    ▼
                 Verify
                    │
              ┌─────┴─────┐
              ▼           ▼
            PASS        REPAIR
```

这就是整个 V2 最重要的架构。

---

# 6. 最关键的原则：Python 不能直接修改世界

这是整个 Harness 的安全边界。

禁止这种 API：

```python
world.set_block(...)
world.fill(...)
server.run_command(...)
```

如果这些操作会立即写入 Minecraft 世界，就失去了 Harness。

应该改成：

```python
emit(
    box((0, 0, 0), (10, 5, 1)),
    block="minecraft:stone_bricks",
)
```

这里 `emit()` 的含义不是：

```text
现在修改世界
```

而是：

```text
声明 Desired Spatial State
```

Python 程序最终只负责产生：

```text
Spatial IR
```

然后 Harness 再决定是否执行。

---

# 7. Agent 为什么应该使用 Python

你的 Agent Backend 本身就是 Python，这一点其实非常适合这个架构。

LLM 对 Python 的生成能力已经非常强。

比如：

```python
for floor in range(20):
    angle = floor * 7.5

    scale = 1 - floor * 0.025

    footprint = rotate(
        rectangle(
            width=20 * scale,
            depth=20 * scale,
        ),
        angle,
    )

    emit(
        extrude(footprint, height=3),
        STONE,
    )
```

这意味着 Agent 可以自己创造：

```text
螺旋塔
参数化屋顶
复杂拱门
地形适应地基
不规则纹样
```

而 Harness 根本不需要提前知道这些建筑概念。

这就是 Coding Agent 风格最大的优势：

> **提供语言，而不是预定义答案。**

---

# 8. Python SDK 应该尽量底层、通用

核心 SDK 不应该有大量：

```python
build_house()
build_castle()
build_temple()
```

应该主要提供空间算子。

## Geometry

```python
point()
line()
plane()
box()
polygon()
volume()
```

未来可以加入：

```python
curve()
profile()
voxel_shape()
```

---

## Boolean

```python
union()
subtract()
intersect()
```

例如：

```python
wall = box(
    (0, 0, 0),
    (20, 5, 1),
)

door = box(
    (9, 0, 0),
    (11, 3, 1),
)

wall = subtract(wall, door)

emit(wall, STONE_BRICKS)
```

---

# 9. Transform

提供：

```python
translate()
rotate()
mirror()
scale()
```

需要注意：

Minecraft 是离散体素环境。

所以：

```python
scale()
rotate()
```

最终必须经过离散化。

例如：

```python
rotate(shape, degrees=45)
```

不会真的产生连续几何，而会在 Voxelization 阶段映射到 Block Grid。

---

# 10. Pattern

Agent 建筑中大量需求实际上都是 Pattern。

例如：

```python
repeat()
array()
grid()
radial()
```

或者直接允许正常 Python：

```python
for x in range(0, width, 4):
    emit(
        column((x, 0, 0), height=5),
        SPRUCE_LOG,
    )
```

这一点实际上就是为什么 Python 比几十个 MCP Tools 强。

---

# 11. Semantic Components 仍然存在

我们不是彻底删除：

```text
wall
roof
arch
stairs
```

而是重新定义它们的位置。

它们应该属于：

```text
Build Standard Library
```

例如：

```python
from mcbuild.std import (
    wall,
    gable_roof,
    arch,
    column,
)
```

目录可能是：

```text
mcbuild/
│
├── core/
│   ├── geometry.py
│   ├── transform.py
│   ├── boolean.py
│   ├── query.py
│   ├── block.py
│   └── ir.py
│
├── std/
│   ├── wall.py
│   ├── floor.py
│   ├── roof.py
│   ├── arch.py
│   ├── stairs.py
│   └── patterns.py
│
├── compiler/
│   ├── lowering.py
│   ├── voxelizer.py
│   ├── diff.py
│   └── optimizer.py
│
└── runtime/
    ├── validator.py
    ├── transaction.py
    ├── executor.py
    └── verifier.py
```

这样：

```text
Semantic Component = Library

Spatial Runtime = Kernel
```

两者彻底解耦。

---

# 12. Agent 可以自己发明 Semantic Component

比如用户要求一种特殊屋顶。

Agent 可以现场写：

```python
def curved_pagoda_roof(
    width,
    depth,
    base_y,
    material,
):
    center = width / 2

    for x in range(width):
        normalized = abs(x - center) / center

        rise = round(
            4 * normalized ** 1.8
        )

        emit(
            line(
                (x, base_y + rise, 0),
                (x, base_y + rise, depth),
            ),
            material,
        )
```

Harness 不需要存在：

```text
build_pagoda_roof
```

这个 Tool。

这就是我们想要的：

> **Agent 自己发明建造方法。**

---

# 13. 未来甚至可以把成功算法变成 Skill

比如 Agent 发明：

```python
def flying_buttress(...):
    ...
```

经过：

```text
Build
↓
Verify
↓
用户接受
```

以后可以保存为：

```text
Reusable Build Skill
```

形成：

```text
Agent Improvisation

↓

Successful Trajectory

↓

Validated Build Function

↓

Reusable Skill Library
```

但这是后期能力，不应该污染 Runtime Kernel。

---

# 14. Spatial IR 才是整个系统真正的核心

MCP 不是核心。

Python SDK 也不是核心。

真正的核心应该是：

> **Spatial IR**

因为未来输入可能不仅来自 Python。

可能还有：

```text
Python Agent
MCP
Web Editor
Schematic
Structure File
Blueprint
Other AI
NPC Builder
```

所有输入最终都应该转成：

```text
Spatial IR
```

---

# 15. 推荐使用三级 IR

这是目前方案中我最推荐的部分。

## HIR

High-Level Spatial IR。

保留空间语义。

例如：

```text
Box
Plane
Line
Extrude
Transform
Repeat
Boolean
Pattern
FillShape
```

例子：

```json
{
  "type": "FillShape",
  "shape": {
    "type": "Box",
    "min": [0, 0, 0],
    "max": [20, 5, 1]
  },
  "block": "minecraft:stone_bricks"
}
```

---

# 16. MIR

Voxel / Desired State IR。

经过 Voxelization 后：

```text
(x, y, z)
      ↓
desired BlockState
```

概念上：

```python
{
    (0, 0, 0): STONE,
    (1, 0, 0): STONE,
    ...
}
```

但实际实现不能真的全部使用 Python Dict，否则大型结构性能会很差。

需要：

```text
Chunk
Region
Palette
RLE
Sparse voxel
Dense voxel
```

等内部结构。

---

# 17. LIR

Low-Level Minecraft Mutation IR。

例如：

```text
FillCommand
SetBlockCommand
SetBlockBatch
CloneCommand
```

最终：

```text
HIR

↓ lowering

Voxel Desired State

↓ world diff

MIR

↓ optimization

LIR

↓

Minecraft
```

完整编译链：

```text
Python
  ↓
HIR
  ↓
Voxelization
  ↓
Desired State
  ↓
Current World State
  ↓
Diff
  ↓
Mutation Optimization
  ↓
Minecraft LIR
  ↓
fill / clone / setblock
```

---

# 18. 不要过早 Voxelize

这个非常重要。

例如：

```python
emit(
    box(
        (0, 0, 0),
        (100, 100, 100),
    ),
    STONE,
)
```

如果立刻展开：

```text
1,000,000 Block Objects
```

性能会很差。

HIR 应继续保存：

```text
FillBox
```

直到：

```text
Diff
Boolean
Collision
Execution Planning
```

真的需要展开的时候才 Voxelize。

所以应该尽量做到：

```text
Parametric IR
+
Voxel IR
```

共存。

---

# 19. Harness 自动翻译成 fill / setblock

是的，这正应该由 Harness 完成。

例如 Agent 写：

```python
for x in range(20):
    for z in range(20):
        emit(
            point(x, 0, z),
            STONE,
        )
```

Agent 表达的是：

```text
400 个目标方块
```

Harness 分析后发现：

```text
完整连续矩形
```

于是：

```text
400 × setblock
```

自动优化为：

```text
1 × fill
```

Agent 不需要关心 Minecraft 原子命令。

---

# 20. Mutation Optimizer

这一层相当于编译器的：

```text
Instruction Selection
```

例如：

```text
Desired Block State
       ↓
World Diff
       ↓
Detect Rectangles
       ↓
Detect Repeated Region
       ↓
Detect Existing Identical Region
       ↓
Choose Operation
```

最终可以选择：

```text
fill
clone
setblock
batch setblock
NOOP
```

---

# 21. NOOP 很重要

例如 Agent 重复生成同一个结构：

```text
Desired:
stone

Current:
stone
```

就不应该执行任何命令。

所以：

```text
Current State
+
Desired State
=
Diff
```

只有 Diff 才进入 Mutation Planner。

这会天然实现部分：

```text
Idempotency
```

---

# 22. Desired State 是长期核心模型

相比：

```text
执行 `/setblock`
```

我们更希望 Agent 表达：

```text
我希望这里最后是什么状态
```

例如：

```python
emit(
    wall,
    STONE_BRICKS,
)
```

Harness 再比较：

```text
Current World
vs
Desired World
```

从而得出：

```text
Place
Replace
Remove
NOOP
```

这类似：

```text
React
Kubernetes
Terraform
```

中的 Desired State 思想。

---

# 23. Observation System

继承 Blender MCP 最大的经验：

> **Observation First**

建议至少提供以下核心能力。

---

## `get_block`

```text
get_block(position)
```

返回：

```json
{
  "position": [100, 64, 100],

  "block": "minecraft:oak_stairs",

  "state": {
    "facing": "north",
    "half": "bottom",
    "shape": "straight",
    "waterlogged": false
  },

  "blockEntity": null
}
```

---

# 24. `inspect_region`

支持不同精度：

```text
summary
surface
detailed
```

例如 summary：

```json
{
  "bounds": {},

  "size": [32, 20, 32],

  "blockCounts": {
    "minecraft:air": 9200,
    "minecraft:stone": 802
  },

  "occupiedBounds": {},

  "blockEntities": [],

  "entities": []
}
```

不要默认返回：

```text
每一个 Block JSON
```

否则 Context 会爆炸。

---

# 25. Region 数据压缩

详细区域建议使用：

```text
Palette
+
Layer
+
RLE
```

例如：

```text
Legend

0 = air
1 = stone
2 = glass

y=70

000000000
011111110
010000010
010222010
011111110
```

比：

```json
[
  {"x": 1, "y": 70, "z": 1, "block": "..."}
]
```

高效很多。

---

# 26. `get_slice`

这是我仍然认为非常值得作为一级 Tool 的 Observation。

例如：

```text
get_slice(
    axis="y",
    coordinate=72
)
```

返回：

```text
.............
..#########..
..#.......#..
..#..GGG..#..
..#...D...#..
..#########..
```

可以表示：

```text
Top View
Front Elevation
Side Elevation
Cross Section
```

Minecraft 是离散体素世界，这种表示特别适合 LLM。

---

# 27. `get_heightmap`

用于：

```text
地形坡度
建筑选址
地基
支柱
Terraform
```

Agent 可以获取类似：

```text
x,z → surface y
```

---

# 28. World Query 也应该暴露给 Python

例如：

```python
height = world.surface_height(x, z)
```

Agent 可以自己写：

```python
for x in range(width):
    for z in range(depth):

        ground = world.surface_height(x, z)

        if ground < foundation_y:
            emit(
                line(
                    (x, ground, z),
                    (x, foundation_y, z),
                ),
                STONE,
            )
```

这样模型就能够自己发明：

> 地形自适应地基算法。

---

# 29. World Query 必须基于 Snapshot

不建议：

```text
Python
 ↓
read live world
 ↓
modify
 ↓
read modified world
 ↓
modify
```

第一版应该采用：

```text
Create Snapshot at T0

↓

Python Reads Immutable Snapshot

↓

Generate Desired State

↓

Commit
```

也就是：

```python
world.block(...)
```

访问的是：

```text
WorldSnapshot
```

而不是实时世界。

优势：

```text
Deterministic
Repeatable
Previewable
Debuggable
Concurrency-safe
```

---

# 30. 多阶段操作交给 Agent Loop

如果确实需要：

```text
修改
↓
看结果
↓
根据结果继续
```

不应该写在同一个 Python Build Script。

而应该：

```text
Agent
 ↓
Script A
 ↓
Preview
 ↓
Commit
 ↓
Observe
 ↓
Script B
```

这更符合 Blender MCP 那种：

```text
Act → Observe → Correct
```

闭环。

---

# 31. Local Coordinate System

前面的讨论中这个设计仍然应该保留。

不要长期要求 AI 处理：

```text
x=1387
y=72
z=-921
```

创建：

```text
BuildContext
```

例如：

```json
{
  "origin": [1387, 72, -921],

  "facing": "north",

  "size": [32, 20, 32]
}
```

之后 Agent 使用：

```text
local coordinate
```

定义：

```text
u = right
v = up
w = forward
```

Harness 自动做：

```text
Local → World Transform
```

---

# 32. 方向也使用 Local Direction

Agent 写：

```python
facing=FORWARD
```

不要让它到处写：

```text
north
south
east
west
```

Build Context 会自动把：

```text
FORWARD
BACKWARD
LEFT
RIGHT
```

转换成真正的 Minecraft Facing。

这对：

```text
stairs
doors
trapdoors
torches
logs
```

尤其重要。

---

# 33. Anchor System

仍然建议保留。

例如：

```text
entrance
center
roof_center
north_wall
main_hall
```

可以表示为：

```json
{
  "entrance": {
    "position": [7, 1, 0],
    "normal": "forward"
  }
}
```

后续：

```python
pos = anchor("entrance")

emit(
    ...
)
```

比重新计算坐标稳定得多。

---

# 34. BlockState 不应该由 Agent 拼字符串

不要让模型频繁产生：

```text
minecraft:oak_stairs[
    facing=north,
    half=bottom,
    shape=straight,
    waterlogged=false
]
```

应该通过 Typed BlockState。

例如：

```python
stairs(
    material=OAK,
    facing=FORWARD,
    half=BOTTOM,
)
```

Runtime 自动：

```text
Local Facing
↓
World Facing
↓
Minecraft BlockState
```

---

# 35. Neighbor State 尽量交给 Minecraft / Runtime

例如：

```text
fence
wall
pane
stairs
rail
redstone
```

不要让 LLM 手动计算：

```text
north=true
east=false
...
```

尽可能：

```text
Place Desired Blocks
↓
Minecraft Neighbor Update
↓
Read Actual State
↓
Verify
```

---

# 36. Build Script 默认应该只产生 Preview

这是我现在比之前更推荐的一种接口。

Agent 调：

```text
execute_build_script
```

不是立即改世界。

而是：

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

返回：

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

然后：

```text
commit_operation(op_9182)
```

才真正写世界。

---

# 37. 为什么要分成 Execute 和 Commit

这样可以形成明确的：

```text
Planning Boundary
```

也就是：

```text
Script
  ↓
Compilation
  ↓
Preview
```

和：

```text
World Mutation
```

完全隔离。

所以 Harness 可以先做：

```text
Block Budget
Protected Region
Conflict
Invalid Block
Chunk
Dimension
World Border
```

等检查。

---

# 38. Transaction

所有修改都必须 Transactional。

记录：

```text
operationId
beforeRevision
afterRevision
affectedRegion
beforeState
desiredState
actualState
diff
executionPlan
```

支持：

```text
undo_operation
```

复杂建筑建议按 Module Commit：

```text
foundation
frame
walls
openings
second_floor
roof
interior
details
```

而不是一次 commit 整栋建筑。

---

# 39. Agent Build Loop

V2 推荐工作流：

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

   10. Adjust if necessary

   11. Commit

   12. Observe actual result

   13. Verify

   14. Repair if needed

   15. Continue

16. Final Observation

17. Final Verification

18. Finish
```

这个闭环应该成为 Agent Policy 的核心。

---

# 40. Build Spec 仍然非常有价值

虽然我们不给 Agent 大量 Semantic Tool，但对于复杂建筑：

```text
Prompt
↓
立即开始写 Block
```

依然不好。

可以先产生：

```json
{
  "style": "medieval",

  "size": [15, 12, 11],

  "floors": 2,

  "facing": "east",

  "palette": {
    "foundation": "cobblestone",
    "frame": "spruce_log",
    "wall": "white_terracotta",
    "roof": "spruce_stairs"
  },

  "modules": [
    "foundation",
    "frame",
    "walls",
    "openings",
    "second_floor",
    "roof",
    "details"
  ]
}
```

Build Spec 是：

> Agent 的设计层。

Spatial Script 是：

> Agent 的实现层。

---

# 41. Verification System

不要把所有验证都交给视觉模型。

Harness 应提供机器可计算检查。

例如：

```text
verify_region
```

支持：

```text
floating_blocks
unexpected_holes
symmetry
connectivity
expected_bounds
unsupported_blocks
protected_region_violation
```

后期：

```text
room_enclosure
door_connectivity
roof_coverage
interior_accessibility
lighting
spawn_safety
water_leak
redstone_connectivity
```

---

# 42. Observation 与 Verification 分工

Observation：

```text
告诉 Agent 世界是什么样
```

Verification：

```text
帮 Agent 判断某种约束是否满足
```

例如：

```text
get_slice
```

返回结构。

而：

```text
verify_symmetry
```

可以直接返回：

```json
{
  "score": 0.97,
  "mismatches": [
    [10, 70, 21],
    [20, 70, 21]
  ]
}
```

Agent 再决定是否修复。

---

# 43. Visual Observation

后期依然值得做：

```text
render_view
```

例如：

```text
front
back
left
right
top
isometric
```

但是：

> Visual Feedback 负责审美，不负责几何真值。

推荐职责：

```text
Block Data
↓
Correctness

Vision
↓
Appearance
```

视觉判断：

```text
比例
风格
构图
建筑是否难看
```

数据判断：

```text
到底少了哪个 Block
是否穿模
是否悬空
```

---

# 44. Blueprint Observation

仍然值得实现：

```text
render_blueprint
```

例如：

```text
FRONT

      /\
     /  \
    /____\
   | [] [] |
   |   __  |
   |__|__|_|


TOP

#############
#...........#
#...........#
#############
```

对于 LLM，这种：

```text
Floor Plan
Elevation
Section
```

是非常高效的空间表示。

---

# 45. Safety

这一层必须由 Harness 强制执行，而不是写在 Prompt 里。

## Block Budget

例如：

```text
max_changed_blocks = 10000
```

如果超过：

```text
BLOCK_LIMIT_EXCEEDED
```

---

## Protected Region

例如：

```text
world spawn
player claims
other builds
command block area
admin regions
```

默认不能改。

---

## Dangerous Blocks

可以对：

```text
command_block
structure_block
jigsaw
bedrock
TNT
lava
portal blocks
```

做权限限制。

---

# 46. Chunk / World Safety

执行之前检查：

```text
dimension
world border
chunk loaded
chunk writable
```

避免 Agent 因为错误维度或者越界坐标造成问题。

---

# 47. Concurrency

这个点很重要。

Agent：

```text
inspect
↓
计划
```

期间玩家可能已经修改世界。

所以 Snapshot 应有：

```text
regionRevision
```

例如：

```json
{
  "regionRevision": "..."
}
```

Pending Operation 记录：

```text
expectedRevision
```

Commit 前：

```text
Current Revision
!=
Expected Revision
```

则：

```text
CONCURRENT_MODIFICATION
```

让 Agent重新：

```text
inspect
↓
re-plan
```

而不是覆盖玩家修改。

---

# 48. Python Sandbox

既然 Agent 能编写 Python，这会变成非常重要的安全边界。

不能简单：

```python
exec(agent_code)
```

然后觉得限制 `globals` 就安全。

推荐至少：

```text
Agent Server
    ↓
Sandbox Worker
    ↓
Restricted Python Runtime
    ↓
Serialized Spatial IR
```

如果环境允许：

```text
Process Isolation
Container
CPU Limit
Memory Limit
Execution Timeout
```

---

# 49. Python 禁止能力

至少禁止：

```text
os
subprocess
socket
requests
pathlib
shutil
ctypes
threading
multiprocessing
open
eval
exec
__import__
```

不要允许 Agent：

```text
文件系统
网络
任意 JVM
系统 Shell
```

---

# 50. AST Precheck

在执行前：

```python
ast.parse(script)
```

检查：

```text
Import
dunder access
exec
eval
global
nonlocal
dangerous attribute traversal
```

但是：

> AST 检查只能是第一层防御，不应被视为真正 Sandbox。

真正安全仍然依赖隔离进程和资源限制。

---

# 51. Resource Limit

除了安全攻击，更常见的是 Agent 写出：

```python
while True:
    pass
```

或者：

```python
for x in range(10**20):
    ...
```

所以需要：

```text
CPU time
wall time
memory
max IR nodes
max geometry complexity
max voxel expansion
max desired blocks
```

等限制。

---

# 52. Deterministic Random

如果希望 Agent 使用随机建筑：

```python
random(...)
```

建议不要直接给系统随机数。

应该：

```text
operationSeed
```

例如：

```python
rng = build.random
```

确保：

```text
同一 Script
+
同一 Snapshot
+
同一 Seed
=
同一结果
```

这样 Preview 和 Commit 才能保持一致。

---

# 53. Core MCP Tools 应该非常少

既然 Python 是主要表达语言，MCP Tool 不需要几十个。

V2 我会考虑：

```text
inspect_region
get_block
get_slice
get_heightmap

create_build_context

execute_build_script
commit_operation

get_operation
verify_region
undo_operation
```

大约 **10 个核心 Tool**。

甚至后面还可以进一步合并。

---

# 54. 为什么 Observation Tool 不全部放进 Python

这和 Coding Agent 有 Bash 仍然需要：

```text
read_file
search
```

是一样的。

虽然理论上：

```python
world.region(...)
```

也能做到。

但一级 Observation Tool 有优势：

```text
输出格式稳定
Context 可控
可以截断
可以摘要
容易让模型主动调用
```

因此：

```text
MCP 一级 Tool
```

主要负责：

```text
Observe
Execute
Commit
Verify
Undo
```

而具体建筑语言全部进入 Python SDK。

---

# 55. Semantic Tool 的最终定位

早期版本可以保留：

```text
wall
floor
roof
arch
```

帮助 Agent 快速施工。

但长期不要做成 MCP Tool。

而是：

```python
from mcbuild.std import roof
```

这样：

```text
Core Tool Surface
```

不会不断膨胀。

---

# 56. 一个完整示例

用户：

> 建一个两层中世纪房子，15×11，入口朝东。

Agent 先：

```text
inspect_region
```

发现：

```text
地面 Y ≈ 72
南边有树
北侧平坦
```

然后：

```text
create_build_context
```

```text
origin = [100,72,200]
facing = EAST
```

Agent 写：

```python
from mcbuild import *
from mcbuild.std import *

# foundation
emit(
    box(
        (0, 0, 0),
        (14, 0, 10),
    ),
    COBBLESTONE,
)

# structural frame
for x in range(0, 15, 4):
    emit(
        line(
            (x, 1, 0),
            (x, 5, 0),
        ),
        SPRUCE_LOG,
    )

    emit(
        line(
            (x, 1, 10),
            (x, 5, 10),
        ),
        SPRUCE_LOG,
    )

# front wall
front = box(
    (0, 1, 0),
    (14, 5, 0),
)

entrance = box(
    (6, 1, 0),
    (8, 3, 0),
)

front = subtract(
    front,
    entrance,
)

emit(
    front,
    WHITE_TERRACOTTA,
)
```

Harness：

```text
Python
↓
HIR
↓
Desired State
↓
World Diff
```

返回：

```json
{
  "operationId": "op_42",

  "changes": 384,

  "warnings": [],

  "executionPlan": {
    "fill": 9,
    "setblock": 20
  }
}
```

Agent：

```text
commit_operation("op_42")
```

之后：

```text
get_slice(front)
```

发现：

```text
窗口不对称
```

再编写一个很小的 Repair Script。

这就是我们想要的：

```text
Build
↓
Observe
↓
Repair
```

---

# 57. Trajectory

从第一版就建议记录：

```text
Intent
Observation
Script
HIR
Desired State
Diff
Execution Plan
World Before
World After
Verification
Repair
User Feedback
```

这是非常有价值的数据。

未来可以用于：

```text
Agent Benchmark
Tool Evaluation
Prompt Optimization
Failure Analysis
Training Dataset
Build Skill Mining
```

这也是 Blender MCP 新增 trajectory 思路对我们很有启发的一部分。

---

# 58. 开发阶段建议

## Phase 0 — Minecraft Adapter

先解决：

```text
read block
read region
fill
setblock
batch operation
```

但不要直接把这些暴露给 AI。

---

## Phase 1 — Observation

完成：

```text
get_block
inspect_region
get_slice
get_heightmap
```

先确保 Agent 真正“看得见世界”。

---

## Phase 2 — Build Context

实现：

```text
local coordinates
orientation
anchors
world transform
```

---

## Phase 3 — Spatial IR

这是第一个真正的大核心。

先做：

```text
Point
Line
Box
FillShape
Transform
```

不需要一开始实现非常复杂的 Boolean。

---

## Phase 4 — Python SDK

允许：

```python
emit()
box()
line()
translate()
repeat / normal Python loop
world.surface_height()
```

输出 HIR。

---

## Phase 5 — Sandbox

独立 Worker：

```text
AST validate
restricted environment
timeout
memory limit
IR limit
```

---

## Phase 6 — World Diff

实现：

```text
Desired
vs
Current
```

生成：

```text
Place
Replace
Remove
NOOP
```

---

## Phase 7 — Mutation Compiler

先支持：

```text
setblock
fill
```

一开始甚至不需要 clone。

做：

```text
continuous cuboid detection
```

把连续 Block 自动合并成 fill。

---

## Phase 8 — Transactions

加入：

```text
preview
operationId
commit
undo
revision
```

---

## Phase 9 — Verification

实现：

```text
holes
floating blocks
symmetry
bounds
connectivity
```

---

## Phase 10 — Standard Library

再慢慢添加：

```text
wall
floor
arch
roof
stairs
patterns
```

这些都建立在 Core SDK 上。

---

## Phase 11 — Visual Feedback

最后加入：

```text
render_view
render_blueprint
```

---

# 59. 第一版建议控制范围

如果现在真的开项目，我不会直接实现完整三级 Compiler。

第一版可以缩成：

```text
Agent Python
    ↓
Simple Spatial IR
    ↓
Voxel Desired State
    ↓
Diff
    ↓
Cuboid Merge
    ↓
fill / setblock
```

Simple IR 只支持：

```text
Point
Line
Box
Block Assignment
```

Observation：

```text
get_block
inspect_region
get_slice
```

Transaction：

```text
execute_build_script
commit
undo
```

这已经能形成完整 Harness。

---

# 60. 项目的核心边界

最终最好坚持下面的职责划分。

## Agent

负责：

```text
需求理解
建筑设计
结构分解
算法
创造性
循环
数学
参数化建造
Repair 决策
```

---

## Python SDK

负责：

```text
表达 Geometry
表达 Transform
表达 Pattern
World Snapshot Query
产生 Spatial Intent
```

---

## Harness

负责：

```text
Sandbox
Coordinate
BlockState
Validation
Safety
Desired State
Diff
Transaction
Concurrency
Optimization
Verification
```

---

## Minecraft Adapter

只负责：

```text
读取世界
setblock
fill
clone
真实执行
```

---

# 61. 最终架构

```text
                  USER
                    │
                    ▼
               AI AGENT
                    │
         ┌──────────┴──────────┐
         │                     │
         ▼                     ▼
   Observation Tools       Python Script
         │                     │
         │                     ▼
         │              Build Python SDK
         │                     │
         └──────────┬──────────┘
                    ▼
               World Snapshot
                    │
                    ▼
               Spatial HIR
                    │
                    ▼
              Spatial Compiler
                    │
           ┌────────┼────────┐
           │        │        │
           ▼        ▼        ▼
       Validate    Diff    Optimize
           │        │        │
           └────────┼────────┘
                    ▼
             Pending Operation
                    │
             Preview Result
                    │
                    ▼
                  Commit
                    │
                    ▼
             Transaction Layer
                    │
                    ▼
              Minecraft LIR
                    │
          ┌─────────┼─────────┐
          ▼         ▼         ▼
       setblock    fill     clone
                    │
                    ▼
             Minecraft World
                    │
                    ▼
                Observe
                    │
                    ▼
                 Verify
                    │
             ┌──────┴──────┐
             ▼             ▼
           PASS          REPAIR
                            │
                            └──────→ Agent
```

---

# 62. 最终定义

我现在会把这个项目定义成：

> **一个为 AI Agent 设计的 Minecraft Spatial Construction Runtime。**

而不是：

> Minecraft MCP Server。

MCP 只是 Adapter。

Python 只是 Agent-facing Language。

Minecraft Command 只是 Backend Instructions。

真正值得长期维护的核心是：

```text
World Model
+
Spatial IR
+
Desired State
+
Compiler
+
Transaction
+
Verification
```

这几层。

---

# 63. 一句话总结 V2

之前我们的思路是：

```text
AI
↓
Semantic Build Tools
↓
fill / setblock
```

经过这几轮讨论后，我认为更好的长期架构已经变成：

```text
AI
↓
Sandboxed Python
↓
Spatial Intent / IR
↓
Desired World State
↓
Diff
↓
Validation
↓
Optimization
↓
Transaction
↓
fill / clone / setblock
↓
Observe
↓
Verify
↓
Repair
```

其中最重要的转变是：

> **不要教 AI 调 `/fill`，也不要替 AI 预定义所有建筑；给 AI 一门通用的空间编程能力，同时让 Harness 掌握世界修改的最终控制权。**

这样 Agent 才既有类似 Coding Agent 的**开放创造能力**，又不会失去 Blender MCP 那种 **Observe → Act → Verify → Correct** 的可靠 Harness 闭环。

如果按长期潜力来看，我认为现在这个方向明显比最早的“大量 Semantic MCP Tools”方案更值得做。