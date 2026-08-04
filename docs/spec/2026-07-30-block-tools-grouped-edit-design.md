# MCBE Chat Agent 方块查看与分组编辑工具改进设计

## 文档状态

- 日期：2026-07-30
- 状态：设计方案，尚未实施
- 范围：`inspect_block`、`edit_blocks`、相关运行时 Harness 预检/审批/结果反馈
- 依据：当前代码、`docs/spec/edit_block_tool.md`、`logs/tool_result.txt`、`logs/runtime_harness_tools.jsonl`

## 1. 背景

当前 MCBE Chat Agent 已提供专用的 `inspect_block` 和 `edit_blocks`，并在 Add-on 中实现了方块读取、单点写入、离散批量、区域填充、写前预检和写后确认。

现有设计解决了直接生成 `setblock` / `fill` 命令时缺乏类型校验、状态确认和审批证据的问题，但模型可见接口仍较宽，多个相关编辑仍需要拆成大量调用。专用工具一旦返回语义不清的失败，模型还可能回退到离散命令调用，重新引入语法试错和结果不可验证的问题。

本设计的目标不是让一次调用完成整栋建筑，而是将调用粒度调整为一个自然施工阶段，例如：

- 主体墙体一组；
- 门窗开口一组；
- 内饰或照明一组；
- 有前后依赖的施工阶段继续分成不同调用。

模型仍负责建筑规划和阶段划分；方块工具负责可靠执行一个阶段内的相关、独立编辑。

## 2. 现状证据

### 2.1 模型可见参数较多

当前 `edit_blocks` 同时公开以下概念：

- `mode=place|batch|fill`；
- `position` / `positions` / `from_pos` / `to_pos`；
- `coordinate_mode` / `dimension`；
- `type_id` / `states`；
- `replace_any` / `expected_previous`。

模型必须根据 `mode` 选择互斥参数，并主动避免传入与当前模式无关的字段。这使接口接近其内部实现复杂度，调用者需要学习较多规则。

当前 `inspect_block` 也需要在 `position` 与 `positions` 之间选择，且不支持直接提交区域目标。

### 2.2 日志中的离散调用

样例建房运行共包含：

- 9 次模型请求；
- 28 次工具调用；
- 122,718 个累计输入 tokens；
- 4 次玻璃专用编辑失败；
- 8 次玻璃 `setblock` 回退；
- 3 次门方块命令试错；
- 5 次火把命令调用。

主要成本来自重复模型轮次、错误恢复和命令回退，而不只是单个 JSON 参数的字符数。

### 2.3 零匹配被映射为内部错误

玻璃操作默认只允许替换空气，但目标位置已经是木板。Add-on 的 fill 预检返回成功，同时给出：

```json
{
  "ok": true,
  "ready": true,
  "matched_count": 0,
  "locked_targets": []
}
```

宿主随后要求可执行计划必须包含非空 `locked_targets`，最终返回：

```json
{
  "ok": false,
  "code": "INTERNAL_ERROR",
  "message": "方块工具内部参数处理失败；本次操作未发送到 Add-on"
}
```

这是预期世界状态冲突，不是内部故障。错误分类使模型无法知道应将前置条件改为木板替换，继而回退到命令工具。

### 2.4 查看结果缺少有界投影

写入结果已经投影为较短的模型决策字段，但查看结果基本透传 Add-on 返回内容。大量点位会同时返回 `blocks`、`targets`、维度、玩家原点和其他元数据，结果体积随点位数线性增长。

### 2.5 提示约束不能阻止错误回退

当前工具提示已经规定：专用方块工具可用时，不应改用 `setblock` / `fill`；只有 `ADDON_UNAVAILABLE` 才可回退命令。

样例中模型仍在收到 `INTERNAL_ERROR` 后调用命令。这说明该约束不能只依赖提示词，必须使用工具结果中的 `fallback_allowed` 作为运行时检查依据。

### 2.6 工具审计状态存在失真

两次门命令实际返回失败，但工具审计顶层仍记录为 `status=success`，内部 `result.success` 才是 failure。运行时 Harness 必须在结果被转换为字符串前完成成功/失败分类，否则反馈闭环会错误统计工具质量。

## 3. 设计目标

### 3.1 必须实现

1. 一次 `edit_blocks` 调用可以表达一个施工阶段内的多个独立编辑。
2. 模型不再直接选择 `place` / `batch` / `fill`。
3. 模型使用统一目标结构表达点集或长方体区域。
4. 默认保持“仅替换空气”的安全策略。
5. 前置条件使用一个字段表达，取代两个互斥参数。
6. 整个编辑组只产生一次预检和一次玩家审批。
7. 预期世界状态冲突不得返回 `INTERNAL_ERROR`。
8. 查看大区域时返回有界摘要，而不是完整枚举全部快照。
9. 只有 `fallback_allowed=true` 才能回退方块命令。
10. 所有预检、审批、执行和工具审计路径继续显式传递当前事件的 `player_name`。

### 3.2 不在本设计范围

- 一次调用完成整栋建筑；
- 墙、屋顶、房间等高级建筑 DSL；
- 允许同组后续编辑依赖前一编辑产生的世界状态；
- 任意 Block Entity 原始 NBT 编辑；
- 对所有区域填充承诺完整原子回滚；
- 使用模型生成的原始 Minecraft 命令作为专用工具的内部实现。

## 4. 核心设计：独立编辑组

模型可见的 `edit_blocks` 顶层接口收敛为：

```text
edits       必填，一个或多个独立编辑
dimension   可选，绝对坐标默认使用当前玩家维度
```

每个编辑只包含三个核心概念：

```text
target      目标点集或长方体区域
block       期望写入的方块及可选 states
expect      可选，当前方块必须满足的条件
```

`place` / `batch` / `fill` 保留为模块内部执行适配器，不再作为模型可见接口。

### 4.1 统一目标结构

目标只能是以下两种形状之一。

点集：

```json
{
  "positions": [
    {"x": 354, "y": 98, "z": 266},
    {"x": 354, "y": 99, "z": 266}
  ]
}
```

长方体区域：

```json
{
  "box": {
    "from": {"x": 353, "y": 97, "z": 272},
    "to": {"x": 359, "y": 99, "z": 272}
  }
}
```

约束：

- 单点使用长度为 1 的 `positions`，不再提供单独的 `position`。
- `{x,y,z}` 表示世界坐标。
- `{forward,right,up}` 表示相对当前事件玩家的坐标。
- 同一个 target 内不能混用绝对坐标和玩家相对坐标。
- 相对坐标在预检阶段解析为绝对坐标，审批后不随玩家移动或转向改变。
- 相对坐标始终使用当前事件玩家所在维度；不得显式指定另一个维度。

### 4.2 方块表达

常见情况使用字符串：

```json
{
  "block": "minecraft:oak_planks"
}
```

需要 block states 时使用对象：

```json
{
  "block": {
    "type_id": "minecraft:oak_stairs",
    "states": {
      "minecraft:cardinal_direction": "north",
      "minecraft:vertical_half": "bottom"
    }
  }
}
```

归一化规则：

- 缺失命名空间时补充 `minecraft:`；
- 大小写归一化；
- 仅在候选唯一时修复明显拼写错误；
- 所有修复必须写入审批证据和工具结果；
- 非唯一候选不得静默选择，应返回最多 3 个建议项。

### 4.3 前置条件表达

`expect` 统一替代 `replace_any` 和 `expected_previous`。

| 输入 | 语义 |
|---|---|
| 省略或 `"air"` | 仅修改空气 |
| `"any"` | 允许修改无受保护数据的普通方块 |
| `"minecraft:oak_planks"` | 当前类型必须是木板 |
| `{type_id, states}` | 当前 permutation 必须精确匹配 |

前置条件的执行语义由 target 形状确定：

- `positions` 使用严格语义。除已经处于目标状态而被判定为 no-op 的位置外，任一位置不满足 `expect`，该编辑失败并阻止整组进入审批。
- `box` 使用过滤语义。不满足 `expect` 的位置可以跳过，但必须在预检、审批和最终结果中返回 `skipped` 与类型计数。
- `box` 中匹配数为 0 且目标并非已经处于期望状态时，返回 `PRECONDITION_FAILED`，不得以成功或 partial 进入审批。
- `expect="any"` 仍不能覆盖包含受保护数据的方块；遇到受保护目标时整组在审批前失败。

示例：

```json
{
  "target": {
    "positions": [
      {"x": 354, "y": 98, "z": 266},
      {"x": 354, "y": 99, "z": 266}
    ]
  },
  "block": "minecraft:glass",
  "expect": "minecraft:oak_planks"
}
```

## 5. 编辑组的范围规则

### 5.1 独立性

组内每个编辑必须能基于调用开始时的同一份世界状态独立预检。

允许：

- 四面木板墙在同一组中提交；
- 多个互不重叠的玻璃窗在同一组中提交；
- 多个不同位置的照明方块在同一组中提交；
- 同一施工阶段中使用不同方块类型，但它们之间没有状态依赖。

不允许：

- 同组先建木墙，再要求后一个编辑以该木墙为前置条件替换成玻璃；
- 同组先清空区域，再在清空结果上放置内饰；
- 同组后续编辑依赖前面编辑产生的门、床或其他多格结构。

有依赖时必须拆成下一次 `edit_blocks` 调用。

### 5.2 重叠处理

- 目标重叠且最终 `block`、states、`expect` 完全相同时，预检阶段自动去重。
- 目标重叠但最终状态或前置条件不同时，整组在审批前返回 `CONFLICTING_EDITS`。
- 不允许依靠列表顺序解决冲突重叠。

该规则避免在工具内部引入虚拟世界模拟和复杂顺序依赖。

### 5.3 限制

编辑组仍受现有工作量限制控制：

- 最大编辑项数量；
- 最大离散位置数量；
- 最大 fill 体积；
- 最大总目标方块数；
- 每 tick 扫描和写入预算；
- MCBE bridge commandLine 字节预算。

这些限制由运行时 Harness 和 Add-on 共同执行，不作为模型常规输入参数。

## 6. 分组编辑示例

### 6.1 墙体阶段

```json
{
  "edits": [
    {
      "target": {
        "box": {
          "from": {"x": 353, "y": 97, "z": 272},
          "to": {"x": 359, "y": 99, "z": 272}
        }
      },
      "block": "minecraft:oak_planks"
    },
    {
      "target": {
        "box": {
          "from": {"x": 353, "y": 97, "z": 266},
          "to": {"x": 353, "y": 99, "z": 271}
        }
      },
      "block": "minecraft:oak_planks"
    },
    {
      "target": {
        "box": {
          "from": {"x": 359, "y": 97, "z": 266},
          "to": {"x": 359, "y": 99, "z": 271}
        }
      },
      "block": "minecraft:oak_planks"
    }
  ]
}
```

### 6.2 门窗阶段

该调用在墙体完成并确认后发起：

```json
{
  "edits": [
    {
      "target": {
        "positions": [
          {"x": 354, "y": 98, "z": 266},
          {"x": 354, "y": 99, "z": 266},
          {"x": 358, "y": 98, "z": 266},
          {"x": 358, "y": 99, "z": 266}
        ]
      },
      "block": "minecraft:glass",
      "expect": "minecraft:oak_planks"
    },
    {
      "target": {
        "positions": [
          {"x": 356, "y": 97, "z": 266}
        ]
      },
      "block": {
        "type_id": "minecraft:wooden_door",
        "states": {
          "minecraft:cardinal_direction": "south"
        }
      },
      "expect": "minecraft:oak_planks"
    }
  ]
}
```

门、床和高草等多格方块必须由专门的放置实现确认其完整结构。若当前实现只支持单格 permutation，应返回 `UNSUPPORTED_BLOCK_PLACEMENT`，不能只验证其中一个方块后声称成功。

## 7. 查看工具设计

继续保留 `inspect_block` 名称以降低迁移成本，但将模型可见输入收敛为：

```text
target       必填，与 edit_blocks 使用完全相同的目标结构
dimension    可选，仅绝对坐标跨维度时需要
```

### 7.1 自动结果层级

模型不需要选择 detail 参数，模块根据目标规模自动决定结果：

1. 单点或少量点：返回完整 type ID、states、waterlogged、air、liquid。
2. 多点或 box：返回边界、位置数、方块类型计数、未知数量和有限样本。
3. 未加载位置必须返回 `unknown/unloaded`，不得伪装为空气。
4. 如果模型需要具体异常点，再使用 `positions` 发起下一次精查。

区域摘要示例：

```json
{
  "ok": true,
  "status": "inspected",
  "bounds": {
    "from": {"x": 353, "y": 96, "z": 266},
    "to": {"x": 359, "y": 100, "z": 272}
  },
  "count": 245,
  "type_counts": {
    "minecraft:air": 210,
    "minecraft:red_terracotta": 25,
    "minecraft:hardened_clay": 10
  },
  "unknown_count": 0,
  "samples": []
}
```

### 7.2 避免机械读写循环

`edit_blocks` 自身必须完成写前读取和写后确认。系统提示应明确：

- 只有当模型需要理解现场或制定方案时才主动调用 `inspect_block`；
- 不要为了满足固定流程，在每次编辑前后机械调用查看工具；
- 写后确认属于方块编辑模块内部实现。

## 8. 预检、审批与执行

### 8.1 预检顺序

编辑组在产生审批前完成：

1. 输入格式归一化；
2. 坐标和维度解析；
3. 方块 ID 与 states 校验；
4. 展开目标并检查限制；
5. 重复目标去重；
6. 冲突目标检测；
7. 读取当前世界状态；
8. 判断 no-op、匹配、跳过和失败；
9. 生成锁定的绝对目标与审批摘要。

任一编辑存在无歧义的参数或安全错误时，整组不得进入审批。

### 8.2 审批

一个编辑组只生成一次审批。审批摘要至少包含：

- 最终维度；
- 编辑项数量；
- 总目标数、匹配数、跳过数；
- 每种目标方块的数量；
- 被替换的非空气方块类型计数；
- 自动修复；
- 是否包含 `expect=any`；
- 是否包含无法完整回滚的 fill。

审批恢复必须使用预检生成的 canonical args，继续显式携带当前事件的 `player_name`。

### 8.3 执行

- 组内按 canonical 顺序执行，保证结果稳定且易审计。
- 执行每项前重新检查锁定目标，防止审批期间世界状态变化。
- 单点和离散批量沿用现有可恢复写入策略。
- fill 不承诺完整回滚；开始写入后的不确定失败返回 `unknown`。
- 任一项确定失败后，默认停止尚未开始的后续项。
- 只有能够可靠恢复的已写入项才执行补偿性回滚。
- 工具不得声称整个编辑组具有其实现无法保证的原子性。

## 9. 结果与错误语义

### 9.1 编辑状态

统一使用以下状态：

| 状态 | 含义 |
|---|---|
| `applied` | 所有目标按预期完成 |
| `noop` | 世界已经是目标状态，没有发送写操作 |
| `partial` | 过滤策略允许跳过部分目标，其余目标已完成 |
| `failed` | 执行前或执行中发生确定失败 |
| `unknown` | 外部状态无法确认，禁止自动重试 |

### 9.2 零匹配

零匹配必须分为：

1. 所有目标已经是期望方块：`noop`。
2. 没有目标满足 `expect`，且目标不是期望状态：`PRECONDITION_FAILED`。

第二种情况应返回当前类型计数和明确修复建议，例如：

```json
{
  "ok": false,
  "status": "failed",
  "code": "PRECONDITION_FAILED",
  "matched": 0,
  "actual_type_counts": {
    "minecraft:oak_planks": 2
  },
  "hint": "如确实要替换这些方块，请将 expect 设为 minecraft:oak_planks",
  "fallback_allowed": false
}
```

预期世界状态冲突、零匹配、限制超出和未知方块均不得映射为 `INTERNAL_ERROR`。

### 9.3 聚合结果

模型只接收决策所需字段：

```json
{
  "ok": true,
  "status": "partial",
  "changed_total": 36,
  "edits": [
    {
      "index": 0,
      "status": "applied",
      "changed": 21
    },
    {
      "index": 1,
      "status": "partial",
      "changed": 15,
      "skipped": 6,
      "skipped_type_counts": {
        "minecraft:red_terracotta": 3,
        "minecraft:hardened_clay": 3
      }
    }
  ],
  "warnings": [
    "部分位置因前置条件不满足而跳过"
  ]
}
```

完整 before/after 快照、锁定目标、验证、回滚和 bridge 诊断只进入工具审计与调试记录，不全部发送给模型。

## 10. 提示与命令回退

### 10.1 提示内容分工

系统提示只保留编排规则：

1. 同一施工阶段的独立操作合为一个编辑组。
2. 有顺序依赖的操作拆成下一组。
3. 编辑工具已内置写前读取和写后确认。
4. 仅当 `fallback_allowed=true` 才可回退命令。

工具描述只说明本工具的输入与结果契约。互斥、必填和数据形状由 JSON Schema 表达，不再同时在系统提示、工具卡片和函数 docstring 中重复长段说明。

### 10.2 运行时回退检查

当当前连接已确认支持专用方块能力时：

- 最近的专用方块失败若为 `fallback_allowed=false`，运行时 Harness 拒绝同一任务中的 `setblock`、`fill`、`clone` 回退；
- `ADDON_UNAVAILABLE` 或明确的 `UNSUPPORTED_CAPABILITY` 可以设置 `fallback_allowed=true`；
- 玩家明确要求执行原始命令时，仍走命令工具自身的审批流程；
- 专用工具的参数错误、前置条件失败和内部错误不能自动扩大为命令执行权限。

该检查使用结构化工具结果，不依赖模型从自然语言提示中自行判断。

## 11. 模块与内部 seam

模型可见的方块工具应形成深模块：调用者只学习 `target`、`block` 和 `expect`，复杂行为留在实现内部。

建议内部职责划分：

1. **目标归一化模块**：解析点集、box、绝对和相对坐标。
2. **编辑组规划模块**：展开目标、去重、冲突检查、限制计算。
3. **预检模块**：读取状态、判断 no-op/匹配/跳过/保护错误。
4. **审批投影模块**：生成 canonical args 和有界审批证据。
5. **执行模块**：选择现有 place/batch/fill 适配器并进行写后确认。
6. **结果投影模块**：生成模型结果和完整工具审计结果。

外部 seam 是 `inspect_block` / `edit_blocks` 的模型可见接口。现有 place、batch、fill handler 是内部适配器，不应继续泄露为模型必须理解的模式参数。

## 12. 兼容与迁移

迁移期间保持工具名称不变：

- `inspect_block`
- `edit_blocks`

迁移顺序：

1. 先修复现有零匹配和结果分类，不改变模型接口。
2. Add-on 增加编辑组预检与聚合执行能力，或由宿主在内部组合现有能力。
3. 模型可见 schema 切换为新接口。
4. 旧 `mode`、`position`、`positions`、`from_pos`、`to_pos` 参数只在内部兼容层短期接受。
5. 工具目录、审批摘要和工具审计统一存储新 canonical args。
6. 验证所有 provider 后移除旧模型可见 schema。

不得同时向模型公开旧接口和新接口，否则参数面会进一步扩大。

## 13. 分阶段实施建议

### 阶段一：恢复工具可信度

- 修复 fill 零匹配预检；
- 正确区分 `noop`、`partial`、`PRECONDITION_FAILED` 和 `INTERNAL_ERROR`；
- 修复工具审计成功/失败分类；
- 增加玻璃替换木板的回归测试；
- 保证 `fallback_allowed=false` 在所有预期失败中稳定返回。

### 阶段二：收敛查看工具

- 引入统一 `target`；
- 增加 box 区域查看；
- 根据目标规模自动返回完整快照或摘要；
- 对模型隐藏重复的 targets、玩家和 bridge 元数据。

### 阶段三：引入独立编辑组

- 顶层接口切换为 `edits` 与可选 `dimension`；
- 实现组级目标展开、去重、冲突检查和限制；
- 一组只产生一次预检和一次审批；
- 内部复用现有 place/batch/fill；
- 返回聚合结果。

### 阶段四：缩减提示并限制错误回退

- 删除系统提示、工具卡片和函数描述中的重复规则；
- 通过 `fallback_allowed` 执行运行时回退检查；
- 对无效方块和 states 返回有限候选；
- 增加门、床、高草等多格方块能力声明和验证。

## 14. 测试要求

### 14.1 输入与归一化

- 单点 positions；
- 多点 positions；
- box 角点顺序归一化；
- 绝对和玩家相对坐标；
- 同一 target 混合坐标模式时拒绝；
- dimension 默认和跨维度显式输入；
- 方块 ID 命名空间、大小写和唯一拼写修复；
- `expect` 的 air、any、类型和完整 states。

### 14.2 编辑组

- 相同重叠自动去重；
- 冲突重叠在审批前拒绝；
- 后项依赖前项的同组计划被拒绝或无法通过当前状态预检；
- 总目标数和 bridge 字节预算限制；
- 整组只生成一次审批；
- 审批恢复使用冻结坐标和当前事件 `player_name`。

### 14.3 结果语义

- 目标已经正确时返回 noop；
- 零匹配返回 PRECONDITION_FAILED；
- 部分空气匹配返回 partial；
- 未加载区块返回明确 unknown/unloaded；
- 写入后不一致返回稳定失败；
- fill 状态未知时禁止自动重试；
- 模型结果有界，完整证据仍进入工具审计。

### 14.4 回退与工具审计

- `fallback_allowed=false` 后拒绝方块命令回退；
- `ADDON_UNAVAILABLE` 允许独立审批命令；
- 工具审计顶层状态与 ToolResult 一致；
- 失败结果在字符串化前完成分类；
- 反馈闭环能正确聚合成功、失败、partial 和 unknown。

### 14.5 实际 Minecraft 验收

使用样例建房任务验证：

1. 一次区域查看获得施工区域摘要；
2. 主体或墙体使用 1 至 2 个编辑组；
3. 门窗使用 1 个后续编辑组；
4. 内饰照明使用 1 个编辑组；
5. 专用工具可用时不出现 `setblock` / `fill` 回退；
6. 门等多格方块要么完整验证成功，要么明确返回不支持；
7. 最终模型回复必须基于聚合结果，不得把 partial 或 unknown 描述为全部完成。

## 15. 验收指标

以当前 28 次工具调用、9 次模型请求的样例作为基线：

- 总工具调用降低到约 5 至 7 次；
- 模型请求降低到约 3 至 5 次；
- 模型可见 `edit_blocks` 顶层字段从约 10 个降低到 2 个；
- 专用工具可用时，方块命令回退次数为 0；
- 预期世界状态冲突产生 `INTERNAL_ERROR` 的次数为 0；
- 零匹配、no-op、partial、failed、unknown 均有独立测试；
- 查看结果大小受配置的摘要和样本上限约束；
- 工具审计状态与实际结果一致率为 100%；
- 当前事件的 `player_name` 在预检、审批恢复、执行和工具审计路径中保持一致。

## 16. 最终决策摘要

本设计采用“独立编辑组”，不采用整栋建筑计划或高级建筑 DSL。

- 模型负责阶段规划；
- 工具负责阶段内独立编辑的可靠批量执行；
- 阶段之间的依赖通过多次调用明确表达；
- 模型接口收敛为统一 target、block 和 expect；
- 现有 place/batch/fill 下沉为内部适配器；
- 运行时 Harness 统一处理预检、审批、回退检查、结果投影和工具审计；
- Add-on 继续拥有真实世界读取、写入、验证和受保护方块判断。

该设计优先解决离散调用、错误恢复和接口复杂度，同时避免把建筑规划过度耦合进方块工具。
