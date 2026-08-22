```markdown
# Minecraft AI Build Harness 设计文档

> 基于 Blender MCP 的 Agent Harness 思想设计一个面向 Minecraft 的 AI 建筑执行、观察、验证与修复系统。

---

## 1. 项目背景

目标是让 AI 能够在 Minecraft 世界中可靠地完成建筑任务，例如：

- 使用 `fill` / `setblock` / 批量方块 API 建造结构
- 查询世界中的方块、BlockState、实体和区域状态
- 理解已有地形和建筑
- 根据用户自然语言生成建筑方案
- 分阶段施工
- 检查施工结果
- 发现错误后自动修复
- 支持 Undo / Rollback
- 防止错误坐标导致大范围世界破坏

项目不应仅仅实现一个：

> “让 AI 调用 `/fill` 和 `/setblock` 的 MCP Server”

而应实现一个完整的：

**Minecraft Spatial Construction Runtime / Build Harness**

MCP 只是 AI 访问这一 Runtime 的协议层。

---

# 2. Blender MCP 调研结论

Blender MCP 的核心价值并不在于“AI 会写 Blender Python”。

真正重要的是它构建了一个 Agent 可以反复操作的环境：

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

其核心思想可以概括为：

> 不要求 AI 第一次操作就完全正确，而是保证 AI 能够观察执行结果，并在下一轮发现和修复错误。

Blender MCP 当前大体由两部分组成：

```text
LLM / MCP Client
        │
        ▼
   MCP Server
        │
        │ Socket / JSON
        ▼
 Blender Addon
        │
        ▼
      bpy
        │
        ▼
 Blender Scene
```

MCP Server 主要负责：

- 定义 AI 可以使用的 Tools
- 参数 Schema
- MCP 协议通信
- 将请求转发给 Blender

Blender Addon 负责：

- 真正操作 Scene
- 查询 Object
- 执行 Python
- 获取 Viewport Screenshot
- 返回执行结果

因此：

> MCP Server 本身并不需要拥有完整的 3D 建模算法。

它更像一个 Agent Harness。

---

# 3. Blender MCP 最值得借鉴的几个设计

## 3.1 Observation 比 Action 更重要

Blender MCP 不只是允许：

```text
execute_blender_code()
```

还允许 AI 获取：

```text
get_scene_info()
get_object_info()
get_viewport_screenshot()
```

因此 AI 可以知道：

```text
我执行了什么
        ↓
世界实际变成了什么
        ↓
是否符合预期
```

对于 Minecraft 应采用同样设计。

不能只有：

```text
set_block()
fill()
```

而必须拥有强大的：

```text
inspect
measure
diff
verify
```

能力。

---

## 3.2 粗粒度观察 + 精细观察

复杂世界不能每次把全部状态塞入 Context。

合理方式是：

```text
World Overview
      ↓
Relevant Region
      ↓
Detailed Blocks
      ↓
Specific Block
```

对应 Minecraft：

```text
inspect_region(summary)

        ↓

inspect_region(detailed)

        ↓

get_slice()

        ↓

get_block()
```

避免一次返回数万个方块造成 Context 爆炸。

---

## 3.3 Typed Tool + Escape Hatch

Blender MCP 保留类似：

```text
execute_blender_code()
```

的通用能力。

但更加可靠的方向是提供：

```text
add_primitive()
modify_object()
set_material()
```

等结构化工具。

类似思想应用到 Minecraft：

不要完全依赖：

```text
run_command("/fill ...")
```

而应该同时存在：

```text
build_wall()
build_floor()
build_roof()
replace_blocks()
```

底层命令只是 Escape Hatch。

---

## 3.4 用结构化约束代替 Prompt 猜测

例如：

```text
“把杯子放在桌子上”
```

不应该完全交给 AI 自己计算空间位置。

更合理的 Tool 是：

```text
place_on(
    object="cup",
    target="table"
)
```

Minecraft 同样应该如此。

例如：

```text
“在北墙中央放一个 3×2 窗户”
```

不应该让 LLM 每次自己计算：

```text
墙起点
墙终点
中心坐标
方向
BlockState
```

而应该：

```text
add_opening(
    target="north_wall",
    width=3,
    height=2,
    horizontal_anchor="center"
)
```

Harness 负责真正的坐标数学。

---

# 4. Minecraft Build Harness 核心理念

整体执行循环：

```text
User Intent
    │
    ▼
Understand
    │
    ▼
Observe World
    │
    ▼
Create Build Spec
    │
    ▼
Create Build Plan
    │
    ▼
Preview
    │
    ▼
Conflict Detection
    │
    ▼
Execute
    │
    ▼
World Diff
    │
    ▼
Verify
    │
 ┌──┴─────┐
 │        │
Pass     Fail
 │        │
 ▼        ▼
Next    Repair
Module    │
          └───────┐
                  ▼
                Verify
```

核心原则：

```text
Observe
Measure
Act
Diff
Verify
Correct
```

---

# 5. 总体架构

建议将系统设计为五层。

```text
┌──────────────────────────────────────┐
│               AI Agent               │
│                                      │
│ Intent / Planning / Reasoning        │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│        Semantic Construction API     │
│                                      │
│ Wall / Floor / Roof / Frame          │
│ Opening / Pattern / Mirror           │
│ Place / Align / Repeat               │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│          Spatial Build Runtime       │
│                                      │
│ Local Coordinate                    │
│ Anchor                              │
│ BlockState Resolution               │
│ Geometry Validation                 │
│ Collision / Support                 │
│ Transaction                         │
│ Diff                                │
└──────────────────┬───────────────────┘
                   │
            ┌──────┴───────┐
            ▼              ▼
┌──────────────────┐ ┌──────────────────┐
│ Primitive Action │ │   Observation    │
│                  │ │                  │
│ fill             │ │ get_block        │
│ set_blocks       │ │ inspect_region   │
│ replace          │ │ get_slice        │
│ clone            │ │ verify_region    │
└────────┬─────────┘ └────────┬─────────┘
         │                    │
         └──────────┬─────────┘
                    ▼
             Minecraft World
```

---

# 6. 一个重要架构原则

不要把 MCP Server 本身设计成整个系统。

推荐结构：

```text
                MCP
                 │
                 ▼
┌──────────────────────────────────┐
│ Minecraft Build Runtime          │
│                                  │
│ Observation                      │
│ Spatial Operations               │
│ Transactions                     │
│ Verification                     │
│ Semantic Building                │
└───────────────┬──────────────────┘
                │
                ▼
         Minecraft Server
```

未来不同客户端都可以复用：

```text
Claude MCP
OpenAI Tool Calling
Agent SDK
Web UI
Minecraft Chat
NPC Builder
CLI
```

因此：

> MCP = Adapter

而不是：

> MCP = Core Runtime

---

# 7. Observation System

这是整个项目优先级最高的模块之一。

---

## 7.1 get_block

最精确的单方块查询。

```typescript
get_block({
    position: [x, y, z]
})
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

# 8. inspect_region

区域观察工具。

推荐至少支持：

```text
summary
blocks
surface
```

例如：

```typescript
inspect_region({
    from: [100, 64, 100],
    to: [132, 96, 132],
    detail: "summary"
})
```

返回：

```json
{
  "size": [33, 33, 33],

  "bounds": {
    "min": [100, 64, 100],
    "max": [132, 96, 132]
  },

  "blockCounts": {
    "minecraft:air": 29800,
    "minecraft:stone_bricks": 1800,
    "minecraft:oak_planks": 760
  },

  "occupiedBounds": {
    "min": [104, 64, 104],
    "max": [128, 82, 128]
  },

  "entities": [],

  "blockEntities": [
    {
      "type": "minecraft:chest",
      "position": [108, 65, 110]
    }
  ]
}
```

第一轮 Observation 不需要把每个 Block 返回给 AI。

---

# 9. Region Data 压缩

如果必须返回详细方块，应避免：

```json
[
  {
    "x": 1,
    "y": 2,
    "z": 3,
    "block": "stone"
  },
  ...
]
```

这种格式 Token 效率非常差。

建议采用：

## Palette

```json
{
  "palette": {
    "0": "minecraft:air",
    "1": "minecraft:stone_bricks",
    "2": "minecraft:oak_planks",
    "3": "minecraft:glass"
  }
}
```

然后采用：

```text
layer
row
RLE
```

进行压缩。

例如：

```text
y=70

00000000000
01111111110
01000000010
01003330010
01111111110
```

这样 AI 很容易理解空间结构。

---

# 10. get_slice

这是建议优先实现的核心 Observation Tool。

例如：

```typescript
get_slice({
    axis: "y",
    coordinate: 72,
    from: [100, 72, 100],
    to: [130, 72, 130]
})
```

返回：

```text
Legend:

. air
# stone_bricks
G glass
D door

...............
....#######....
....#.....#....
....#.GG..#....
....#..D..#....
....#######....
...............
```

支持：

```text
Top View
Front View
Side View
Cross Section
```

Minecraft 是体素世界，因此这种二维 Slice 对 LLM 非常友好。

AI 可以用它判断：

- 对称
- 门窗位置
- 墙体完整性
- 屋顶截面
- 内部空间
- 是否缺块
- Pattern 是否正确

---

# 11. Heightmap

建议增加：

```text
get_heightmap()
```

例如：

```typescript
get_heightmap({
    from: [100, 100],
    to: [132, 132]
})
```

用于：

- 判断地形坡度
- 自动寻找平坦区域
- Foundation 规划
- 判断建筑是否悬空
- Terraform

---

# 12. Local Coordinate System

这是 Build Harness 中非常重要的一层。

不要长期让 AI 操作：

```text
x = 1357
y = 71
z = -928
```

应该首先创建：

```typescript
create_build_context({
    origin: [1357, 71, -928],
    facing: "north",
    size: [32, 20, 32]
})
```

之后 AI 使用：

```text
local coordinate
```

例如：

```text
[0,0,0]
[10,5,8]
```

推荐定义：

```text
u = right
v = up
w = forward
```

Harness 自动负责：

```text
local → world
```

转换。

---

# 13. Coordinate Transform

例如：

```text
Build facing = EAST
```

AI 仍然可以表达：

```text
forward
left
right
back
```

而不用考虑 Minecraft：

```text
+x
-x
+z
-z
```

这能显著减少 Direction Error。

---

# 14. Anchor System

建议在 Build Context 中允许定义 Anchor。

例如：

```json
{
  "anchors": {
    "center": [7, 0, 5],

    "entrance": {
      "position": [0, 1, 5],
      "facing": "forward"
    },

    "north_wall": {
      "type": "plane",
      "bounds": {}
    },

    "roof_center": [7, 8, 5]
  }
}
```

AI 后续可以：

```typescript
place_relative({
    target: "entrance",
    offset: [0, 0, -2]
})
```

而不是重新计算世界坐标。

Anchor 可以极大降低：

```text
Spatial Drift
Coordinate Drift
Direction Drift
```

---

# 15. Primitive Mutation API

最底层提供：

```text
set_blocks
fill_region
replace_blocks
copy_region
```

不建议给 AI 暴露大量过细 API。

例如：

```typescript
set_blocks({
    blocks: [
        {
            position: [1, 2, 3],
            block: "minecraft:stone"
        }
    ]
})
```

---

# 16. 不推荐直接暴露 run_command 作为主要工具

例如：

```text
run_command("/fill ...")
```

可以保留作为 Escape Hatch。

但正常 Building 应该经过结构化 Tool。

原因：

```text
无法提前验证坐标
难以生成 Diff
难以限制 Block 数量
难以检查 protected region
字符串参数容易出错
BlockState 容易写错
难以实现 Desired State
```

---

# 17. Semantic Build API

AI 大多数情况下应该调用 Semantic Tool。

建议第一阶段提供：

```text
build_wall
build_floor
build_box
build_frame
build_column
build_roof

add_opening

replace_blocks

copy_region
transform_region
mirror_region

repeat_pattern
```

例如：

```typescript
build_wall({
    start: [0, 0, 0],
    end: [10, 0, 0],

    height: 5,
    thickness: 1,

    material: "minecraft:stone_bricks"
})
```

Harness 可以将其优化为：

```text
1 × fill
```

而不是：

```text
55 × setblock
```

---

# 18. Semantic Tool 的粒度

不建议一开始实现：

```text
build_castle
build_house
build_chinese_palace
build_modern_house
build_bridge
```

这些工具层级过高。

否则 Runtime 本身就变成建筑生成器。

比较合理的抽象层级是：

```text
Wall
Plane
Frame
Opening
Roof
Pattern
Repeat
Mirror
Transform
Place
Align
```

然后让 Agent 将这些 Primitive Composition 成：

```text
House
Castle
Temple
Station
Village
```

---

# 19. Opening Tool

例如：

```typescript
add_opening({
    target: "north_wall",

    type: "window",

    width: 3,
    height: 2,

    horizontalAnchor: "center",

    verticalOffset: 2
})
```

Harness 自动计算：

```text
墙的位置
墙朝向
墙长度
窗口中心
需要移除的 blocks
```

AI 不需要自己做空间数学。

---

# 20. BlockState Resolution

不要要求模型手动拼：

```text
minecraft:oak_stairs[
    facing=north,
    half=bottom,
    shape=straight,
    waterlogged=false
]
```

建议：

```typescript
place_stair({
    position: [1, 2, 3],

    material: "minecraft:oak",

    facing: "forward",

    half: "bottom"
})
```

Harness 负责：

```text
forward
      ↓
Build Context
      ↓
world north/east/south/west
      ↓
Minecraft BlockState
```

---

# 21. Neighbor State

Minecraft 中很多 BlockState 与附近方块相关：

```text
stairs
fence
wall
pane
rail
redstone
door
chest
```

因此 Harness 应尽可能：

```text
place desired blocks

        ↓

trigger/update neighbors

        ↓

read actual state

        ↓

verify
```

而不是让 LLM 手动计算所有 neighbor state。

---

# 22. Mutation Result 必须包含 Diff

这是非常重要的设计。

不要只返回：

```json
{
  "success": true
}
```

应该：

```json
{
  "success": true,

  "operationId": "op_10241",

  "changedBlocks": 105,

  "bounds": {
    "min": [100, 64, 100],
    "max": [120, 68, 100]
  },

  "blockDelta": {
    "air -> stone_bricks": 99,
    "grass_block -> stone_bricks": 6
  },

  "warnings": []
}
```

如果覆盖现有建筑：

```json
{
  "warnings": [
    {
      "type": "NON_AIR_OVERWRITE",
      "count": 8
    }
  ]
}
```

AI 因此能够理解：

```text
我本来想建一面墙

↓

实际修改了什么

↓

是否出现意外副作用
```

---

# 23. Transaction

建议每次修改均属于 Transaction。

例如：

```text
Operation

↓

Before State

↓

Execute

↓

After State

↓

Diff
```

每个操作返回：

```text
operationId
```

例如：

```json
{
  "operationId": "op_01829"
}
```

随后可以：

```typescript
undo_operation({
    operationId: "op_01829"
})
```

---

# 24. Module Transaction

复杂建筑最好按模块划分：

```text
foundation
frame
walls
openings
floor_2
roof
interior
decoration
landscape
```

例如：

```text
BEGIN roof

build roof...
build roof...
build roof...

COMMIT roof
```

如果屋顶失败：

```text
ROLLBACK roof
```

而不是重建整个房屋。

---

# 25. Preview / Dry Run

对于任何较大操作，都应该允许：

```text
mode = preview
```

例如：

```typescript
build_roof({
    ...,
    mode: "preview"
})
```

返回：

```json
{
  "wouldChange": 428,

  "wouldOverwrite": 19,

  "wouldRemove": 0,

  "affectedBounds": {},

  "warnings": [
    "19 existing non-air blocks will be replaced"
  ]
}
```

AI 根据结果重新调整方案。

确认后：

```text
mode = execute
```

---

# 26. Verify System

不要要求 AI 每次自己分析所有 Block。

应该提供专门的：

```text
verify_region()
```

例如：

```typescript
verify_region({
    region: "house",

    checks: [
        "floating_blocks",
        "unexpected_holes",
        "symmetry",
        "connectivity"
    ]
})
```

返回：

```json
{
  "floatingBlocks": [],

  "holes": [
    [104, 68, 120]
  ],

  "disconnectedComponents": 0,

  "symmetryScore": 0.97
}
```

---

# 27. Verification 类型

第一阶段建议支持：

```text
unexpected_holes

floating_blocks

symmetry

connectivity

expected_bounds

non_air_overwrite

unsupported_blocks

protected_region_violation
```

后续可以加入：

```text
interior_accessibility

room_enclosure

door_connectivity

roof_coverage

lighting

spawn_safety

water_leak

redstone_connectivity
```

---

# 28. Spatial Constraint Tools

后续非常值得实现。

例如：

```text
place_on_surface()
place_against_wall()
align_to()
center_on()
fit_inside()
distribute()
```

例：

```typescript
place_against_wall({
    block: "minecraft:torch",

    wall: "north_wall",

    height: 3,

    interval: 5
})
```

Harness 自动处理：

```text
wall normal

↓

torch facing

↓

support block

↓

position

↓

BlockState
```

---

# 29. Build Spec

复杂建筑不应立即开始施工。

推荐：

```text
User Prompt

↓

Build Spec

↓

Build Plan

↓

Construction
```

例如：

```json
{
  "size": [15, 12, 11],

  "facing": "east",

  "style": "medieval",

  "floors": 2,

  "floorHeight": 5,

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

---

# 30. Build Plan

Agent 根据 Spec 分解：

```text
Foundation

↓

Structural Frame

↓

Walls

↓

Doors / Windows

↓

Second Floor

↓

Roof

↓

Interior

↓

Decoration

↓

Landscape
```

每个 Module 独立：

```text
Preview
Execute
Diff
Verify
Repair
Commit
```

---

# 31. 推荐 Agent Build Loop

建议 Harness Prompt / Agent Policy 明确规定：

```text
1. inspect target region

2. establish build context

3. create build spec

4. create module plan

FOR EACH MODULE:

    5. preview operation

    6. inspect conflicts

    7. execute

    8. inspect diff

    9. verify module

   10. repair if required

   11. commit module

12. inspect final structure

13. verify final structure

14. repair detected issues

15. finalize
```

禁止：

```text
一次性规划整个建筑

↓

连续执行几十个 mutation tool

↓

最后才检查结果
```

---

# 32. Recommended Tool Set

第一版不要设计几十上百个 Tool。

推荐大约 15~20 个。

---

## Observation

```text
get_block

inspect_region

get_slice

get_heightmap

inspect_structure
```

---

## Context

```text
create_build_context

get_build_context

define_anchor
```

---

## Primitive Mutation

```text
set_blocks

fill_region

replace_blocks

copy_region
```

---

## Semantic Build

```text
build_wall

build_floor

build_frame

build_roof

add_opening

repeat_pattern

transform_region
```

---

## Harness

```text
preview_operation

verify_region

undo_operation
```

实际可以根据 MCP Tool 数量进一步合并。

---

# 33. Desired State

长期来看，可以进一步从：

```text
Command Driven
```

升级为：

```text
Desired State Driven
```

传统模式：

```text
AI

↓

setblock A

setblock B

fill C
```

Desired State：

```text
AI

↓

我希望这个区域最终成为这个结构
```

例如：

```typescript
apply_structure({
    region: {...},

    palette: {...},

    blocks: {...}
})
```

Runtime：

```text
Current State
       │
       ▼
Desired State
       │
       ▼
      Diff
       │
 ┌─────┴───────┐
 ▼             ▼
Already OK     Needs Change
                 │
                 ▼
          Optimize Mutation
                 │
        ┌────────┼────────┐
        ▼        ▼        ▼
      fill     clone   setblock
```

---

# 34. Idempotency

Desired State API 应尽量支持幂等。

例如：

```typescript
ensure_wall({
    id: "north_wall",
    ...
})
```

如果当前世界已经满足：

```json
{
  "status": "already_satisfied",
  "changedBlocks": 0
}
```

这样 Agent 即使重复执行也不会产生额外副作用。

---

# 35. Safety

Minecraft AI Builder 必须有非常严格的 Mutation Boundary。

---

## Block Budget

每次操作限制：

```text
maxBlocks
```

例如：

```json
{
  "maxBlocks": 10000
}
```

如果错误坐标导致：

```text
3,000,000 blocks
```

直接返回：

```text
BLOCK_LIMIT_EXCEEDED
```

---

# 36. Protected Region

默认禁止操作：

```text
World Spawn

Other Player Claims

Command Blocks

Important Containers

Admin Regions

Configured Protected Structures
```

Mutation 前进行 Intersection Test。

---

# 37. Dangerous Block Protection

默认限制或要求额外权限：

```text
command_block

structure_block

jigsaw

bedrock

end_portal

nether_portal

lava

TNT
```

具体取决于服务器模式。

---

# 38. Chunk Safety

执行大型施工前检查：

```text
chunk loaded?

chunk writable?

world border?

dimension correct?
```

避免操作未加载区域造成异常。

---

# 39. Concurrency

需要考虑：

```text
玩家正在修改同一区域

↓

Agent 同时施工
```

Transaction 应记录：

```text
Before State Hash
```

Commit 前重新检查。

例如：

```text
EXPECTED_STATE_MISMATCH
```

说明施工期间区域发生变化。

此时 Agent 应：

```text
re-inspect

↓

re-plan
```

而不是覆盖玩家操作。

---

# 40. World State Hash

可以给 Region 生成：

```text
regionRevision
```

例如：

```json
{
  "regionRevision": "sha256:..."
}
```

Mutation Tool 接受：

```text
expectedRevision
```

如果世界已经变化：

```text
CONCURRENT_MODIFICATION
```

避免 Agent 基于旧 Context 继续修改。

---

# 41. Trajectory / Operation Log

建议从第一版就记录：

```text
Intent

↓

Observation

↓

Action

↓

State Before

↓

State After

↓

Diff

↓

Verification

↓

Repair
```

例如：

```json
{
  "operationId": "op_1024",

  "tool": "build_wall",

  "arguments": {},

  "beforeRevision": "...",

  "afterRevision": "...",

  "changedBlocks": 105,

  "warnings": [],

  "verification": {
    "passed": true
  }
}
```

长期可以积累成为：

```text
Minecraft Building Agent Dataset
```

用于：

- Tool Design 分析
- Agent Benchmark
- Failure Pattern 分析
- Prompt 优化
- Model Evaluation
- 未来训练数据

---

# 42. Visual Observation

如果客户端条件允许，建议后期加入：

```text
render_view()
```

例如：

```typescript
render_view({
    target: "house",

    views: [
        "front",
        "back",
        "left",
        "right",
        "top",
        "isometric"
    ]
})
```

视觉负责判断：

```text
比例是否协调

建筑是否好看

屋顶造型是否奇怪

材质搭配是否合理
```

---

# 43. 数据 Observation 与视觉 Observation 的职责

不要依赖 Screenshot 判断：

```text
到底缺了哪个 block
```

精确问题交给：

```text
Block Data
Diff
Slice
Verification
```

视觉 Observation 负责：

```text
Aesthetic Evaluation
Proportion
Composition
Style
```

即：

```text
Data → Correctness

Vision → Appearance
```

---

# 44. Blueprint View

非常推荐后续增加：

```text
render_blueprint()
```

生成：

```text
TOP

#############
#...........#
#...........#
#############

FRONT

      /\
     /  \
    /____\
   | [] [] |
   |   __  |
   |__|__|_|
```

LLM 对：

```text
Floor Plan
Elevation
Cross Section
```

的理解通常比直接理解大量三维 Block JSON 更稳定。

---

# 45. 示例：两层中世纪房屋

用户：

```text
建一个 15×11 的两层中世纪小屋，入口朝东。
```

---

## Step 1

Agent：

```text
inspect_region
```

结果：

```text
北边地形平坦

南边有树

平均地面 Y=72
```

---

## Step 2

```typescript
create_build_context({
    origin: [100, 72, 200],

    facing: "east",

    size: [15, 12, 11]
})
```

---

## Step 3

生成 Build Spec：

```text
15 × 11

2 Floors

Medieval

Foundation = Cobblestone

Frame = Spruce Log

Wall = White Terracotta

Roof = Spruce
```

---

## Step 4

模块：

```text
foundation
frame
walls
windows
floor2
roof
details
```

---

## Step 5

Foundation：

```typescript
build_floor({
    min: [0, 0, 0],

    max: [14, 0, 10],

    material: "minecraft:cobblestone"
})
```

Runtime：

```text
165 changed

0 conflict
```

---

## Step 6

Walls：

```text
build_wall
build_wall
build_wall
build_wall
```

执行后：

```text
get_slice(front)
```

发现：

```text
window symmetry incorrect
```

Agent 修复。

---

## Step 7

Roof：

```typescript
build_roof({
    footprint: {...},

    type: "gable",

    slope: 45,

    material: "minecraft:spruce_stairs"
})
```

Harness 负责：

```text
stair facing

corner handling

roof direction

BlockState
```

---

## Step 8

Verification：

```typescript
verify_region({
    checks: [
        "symmetry",
        "floating_blocks",
        "unexpected_holes",
        "enclosure"
    ]
})
```

返回：

```text
PASS no floating blocks

PASS wall enclosure

WARNING 2 roof blocks missing
```

---

## Step 9

Agent：

```text
repair missing roof blocks
```

再次：

```text
verify_region
```

全部通过。

---

# 46. 为什么 Harness 会比单纯 Prompt 更可靠

普通方案：

```text
User

↓

LLM

↓

/fill
/setblock
/fill
/setblock
...

↓

Minecraft
```

问题是 AI 完全依赖自身：

```text
坐标计算能力

方向理解能力

BlockState 知识

空间记忆

错误预测
```

Harness 方案：

```text
User

↓

LLM Planner

↓

Spatial Runtime

↓

Validated Operation

↓

World Diff

↓

Verification

↓

Repair
```

因此可靠性主要来自：

```text
Runtime

而不是

Model Intelligence
```

---

# 47. 最重要的设计原则

整个项目可以归纳为：

## Principle 1

**Observation First**

AI 操作之前先知道世界是什么样。

---

## Principle 2

**Action 后必须能看到结果**

每次 Mutation 返回 Diff。

---

## Principle 3

**让 Runtime 做空间数学**

不要让 LLM 重复计算：

```text
坐标

Facing

BlockState

中心点

墙法线
```

---

## Principle 4

**Semantic Tool 优先**

优先：

```text
build_wall
```

而不是：

```text
/fill
```

---

## Principle 5

**Primitive Tool 必须保留**

复杂任务需要：

```text
set_blocks
fill
```

作为 Escape Hatch。

---

## Principle 6

**Large Mutation 必须 Preview**

AI 应在破坏世界之前看到可能产生的影响。

---

## Principle 7

**Build 必须 Transactional**

错误应该能够：

```text
undo
rollback
repair
```

---

## Principle 8

**复杂建筑必须模块化**

不要一次建完整个建筑。

---

## Principle 9

**Context 必须分层获取**

避免完整 World Dump。

---

## Principle 10

**最终目标是 Desired State**

从：

```text
执行命令
```

逐渐进化到：

```text
声明我希望世界最终是什么样
```

---

# 48. MVP 开发顺序

第一阶段不建议急着实现：

```text
build_roof
build_arch
build_castle
```

最值得优先实现的是 Runtime 基础能力。

---

## Phase 1 — World Observation

实现：

```text
get_block

inspect_region

get_slice

get_heightmap
```

目标：

> AI 能准确理解目标施工区域。

---

## Phase 2 — Spatial Runtime

实现：

```text
Build Context

Local Coordinates

Direction Mapping

Anchor
```

目标：

> AI 不需要直接处理复杂世界坐标。

---

## Phase 3 — Safe Mutation

实现：

```text
set_blocks

fill_region

replace_blocks

Block Budget

Protected Region
```

目标：

> AI 可以安全修改世界。

---

## Phase 4 — Transaction & Diff

实现：

```text
operationId

before state

after state

diff

undo
```

目标：

> 每次修改都可追踪、可观察、可恢复。

---

## Phase 5 — Semantic Building

实现：

```text
build_wall

build_floor

build_frame

add_opening

repeat_pattern

transform_region
```

目标：

> AI 开始从“方块操作”升级到“建筑操作”。

---

## Phase 6 — Verification

实现：

```text
verify_region

symmetry

holes

floating blocks

connectivity
```

目标：

> Agent 可以自己发现施工错误。

---

## Phase 7 — Advanced Building

实现：

```text
build_roof

place_on_surface

place_against_wall

alignment

constraint
```

---

## Phase 8 — Desired State

实现：

```text
apply_structure

ensure_structure

state diff

idempotency
```

---

## Phase 9 — Visual Feedback

加入：

```text
render_view

render_blueprint
```

让 Agent 同时拥有：

```text
Geometry Correctness

+

Aesthetic Feedback
```

---

# 49. 最推荐的第一版工具

如果需要严格控制第一版规模：

```text
get_block

inspect_region

get_slice

create_build_context

set_blocks

fill_region

replace_blocks

build_wall

build_floor

add_opening

preview_operation

verify_region

undo_operation
```

大约 13 个 Tool 就足以构建第一版完整 Agent Loop。

---

# 50. 项目最终定位

不建议把项目定义为：

> Minecraft MCP

更准确的定位应该是：

> Minecraft AI Spatial Construction Runtime

或者：

> Minecraft Build Harness

核心能力：

```text
World Observation

+

Spatial Abstraction

+

Semantic Construction

+

Transactional Mutation

+

World Diff

+

Verification

+

Repair
```

上层：

```text
MCP
OpenAI Agent
Claude
Web UI
Minecraft NPC
Chat Commands
```

都只是 Runtime 的消费者。

---

# 51. 最终架构目标

```text
                User Intent
                     │
                     ▼
               AI Planner
                     │
          ┌──────────┴───────────┐
          ▼                      ▼
     Build Spec             Observation
          │                      │
          ▼                      │
     Build Plan                  │
          │                      │
          └──────────┬───────────┘
                     ▼
             Semantic Build API
                     │
                     ▼
              Spatial Runtime
        ┌────────────┼────────────┐
        │            │            │
        ▼            ▼            ▼
 Coordinate     Validation    BlockState
  System
        │            │            │
        └────────────┼────────────┘
                     ▼
               Preview / Diff
                     │
                     ▼
              Transaction Layer
                     │
                     ▼
             Primitive Mutation
                     │
                     ▼
              Minecraft World
                     │
                     ▼
               Observation
                     │
                     ▼
                Verification
                     │
               ┌─────┴─────┐
               ▼           ▼
             PASS         FAIL
               │           │
               ▼           ▼
             NEXT        REPAIR
```

---

# 52. 一句话总结

Blender MCP 最值得学习的并不是：

```text
如何让 AI 调用一个复杂软件
```

而是：

> 如何把一个复杂环境改造成 AI 可以观察、操作、验证和自我纠错的 Runtime。

应用到 Minecraft 后，最重要的也不是：

```text
让 GPT 学会写 /fill
```

而是构造：

```text
Observe
   ↓
Plan
   ↓
Preview
   ↓
Execute
   ↓
Diff
   ↓
Verify
   ↓
Repair
```

这个闭环。

当这个闭环足够可靠之后，模型第一次有没有把建筑完全规划正确，反而不再是最关键的问题。

真正决定系统能力上限的是：

```text
Observation Quality

+

Spatial Abstraction

+

Tool Semantics

+

Verification Quality

+

Recovery Capability
```

---

# 参考资料

[1] BlenderMCP — ahujasid/blender-mcp  
官方项目仓库，包含 MCP Server、Blender Addon、Scene Inspection、Viewport Screenshot、资产搜索与 Python 执行能力。

[2] BlenderMCP — src/blender_mcp/server.py  
主要 MCP Tool 定义和 MCP Server → Blender 通信逻辑，可重点研究 Tool Schema、execute_blender_code 和观察类 Tool。

[3] blendmcp — owenpkent/blendmcp  
BlenderMCP fork。增加 add_primitive、modify_object、set_material、duplicate_object、delete_object、batch_edit 等 Typed Tool；其设计可以作为“Typed Tool 比 Raw Code 更可靠”的参考。

[4] Model Context Protocol — Server Overview  
MCP 官方关于 Resources、Prompts、Tools 以及控制模型的定义。

[5] Model Context Protocol — Tools Specification  
MCP Tool Schema、模型调用方式以及安全相关设计规范。

[6] Model Context Protocol — Specification  
MCP 整体协议规范，可用于设计 Minecraft Build MCP Adapter。

> 可点击链接见正文后的参考链接。
```
