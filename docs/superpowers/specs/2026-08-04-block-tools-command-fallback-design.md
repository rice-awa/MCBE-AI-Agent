# 方块工具命令回退放宽与多格方块契约设计（fallback 一元规则）

**日期：** 2026-08-04
**状态：** 已实施（宿主 + Add-on + 文档；游戏内验收待 Bedrock ≥ 1.26.10 环境）
**范围：** 专用方块工具（`place_block` / `fill_block` / `inspect_block`）失败后的命令回退策略放宽为「世界状态已知未变即允许回退」一元规则；Add-on 能力声明与 Script API 参考文档同步更新（`multiblock_placement: "command_fallback"`）
**约束：** 不改 Add-on 运行时拒绝逻辑（Script API `setPermutation` 单格写入多格方块仍拒绝）；命令回退仍需玩家审批；`STATE_UNKNOWN` / `INTERNAL_ERROR` 保持不回退；多人身份仍只使用 `event.sender` / 显式 `player_name`

## 1. 背景与证据

### 1.1 用户可观察现象

2026-08-04 两次「放个门」请求（run `9c905f69-...` / `81ef66b3-...`，`logs/app.log`）：

1. 模型调用 `place_block(block=minecraft:oak_door, ...)` → Add-on 返回 `UNSUPPORTED_BLOCK_PLACEMENT`（`fallback_allowed: false`），`external_state_unknown: false`；
2. 模型改用 `run_minecraft_command: setblock <pos> minecraft:oak_door` → 被运行时 Harness 拒绝：
   > `ToolDenied("专用方块编辑刚刚失败（UNSUPPORTED_BLOCK_PLACEMENT）且不允许命令回退；诊断: 多格方块放置暂不支持，未发送到 Add-on。")`

模型在两个工具之间空转，目标无法达成。

### 1.2 根因

1. **专用工具结构性不支持多格方块**：`place_block` / `fill_block` 映射到 Add-on `edit_blocks` 的单格 `setPermutation` 写入，对门 / 床 / 高草等多格方块只写一个 permutation，会产生残缺结构，因此 Add-on 拒绝（`MCBE-AI-Agent-addon/scripts/bridge/capabilities/blocks/multiblock.ts` 的 `isMultiblockBlock`，spec issue 05 §6/§7）。这是既有设计（single-op spec §2.2 非目标「不实现多格方块放置」）。
2. **fallback 白名单过窄**：宿主 `services/agent/block_ops/bridge.py` 中 `fallback_allowed` 只对 `ADDON_UNAVAILABLE` / `UNSUPPORTED_CAPABILITY` 为 true，其余错误码一律 false；Harness（`services/agent/harness/execution.py` `_block_command_fallback_denial`）据此拒绝同 run 内所有 `setblock` / `fill` / `clone` 回退。模型唯一的绕行出路被策略堵死。

### 1.3 官方文档查证：命令路径可以放置完整门

Microsoft 官方更新说明（`creator/Documents/Update1.26.10.md`）：

> "Fixed an issue where … `/setblock` and `/fill` commands incorrectly placing partial double blocks, such as the upper half of a door."

即：

| 路径 | 1.26.10 之前 | 1.26.10 起 |
| --- | --- | --- |
| 命令 `/setblock` / `/fill` | 只放置半格门（bug） | **完整放置双格结构（已修复）** |
| Script API `setPermutation` / `setBlockPermutation` | 单格写入 | 单格写入（不变，仍只写一个 permutation） |

结论：

- Add-on 拒绝多格方块的逻辑**本身仍然正确**（它只约束 Script API 单格写入路径）；
- 命令回退路径在 1.26.10+ **可以正确放置完整门**，旧注释「fallback_allowed=false so the model does not fall back to a single setblock (which has the same single-cell problem)」已过时。

补充查证（bedrock.dev 官方 BlockStates 表）：Bedrock 门的状态为 `minecraft:cardinal_direction`（north/south/east/west）、`upper_block_bit`（0=下半 / 1=上半）、`open_bit`、`door_hinge_bit`；`setblock` 状态语法为 `["state":value]`（如 `minecraft:oak_door ["minecraft:cardinal_direction":"south"]`）。Java 的 `[facing=south,half=lower]` 语法在 Bedrock 不合法；无状态 `setblock <pos> minecraft:oak_door` 即可。

### 1.4 宿主与 Add-on 的职责边界（本设计的依据）

- 宿主**不读取** Add-on 错误载荷中的 `fallback_allowed` 字段（`bridge.py` 的 `_safe_addon_error_body` 按错误码重新计算）→ 放行逻辑完全由宿主侧控制，Add-on 无需逻辑改动；
- 宿主**不读取**能力声明 `multiblock_placement` 字段（grep 确认）→ 它是纯信息性声明，可安全改值。

## 2. 目标与非目标

### 2.1 目标

1. **fallback 一元规则**：专用方块工具失败时，只要「世界状态已知未变」（`external_state_unknown == false`），即允许命令回退；唯一例外是 `INTERNAL_ERROR`（宿主 bug，不应被命令掩盖）。
2. **保留安全边界**：`STATE_UNKNOWN`（超时 / 调用开始后未知结果，可能已部分写入）继续拒绝回退；回退命令继续走原审批策略（`setblock` / `fill` / `clone` 需玩家审批）。
3. **sapi 契约更新**：Add-on 能力声明 `multiblock_placement: "unsupported"` → `"command_fallback"`；运行时错误载荷自文档化（`command_fallback_viable: true`）；过时注释与 `fill.ts` 载荷对齐。
4. **文档更新**：`docs/sapi/block-operations.md` 补「多格方块行为对照（Script API vs 命令）」。

### 2.2 非目标

- 不实现 Add-on 侧多格方块放置（写上下两格 + 写后校验留作未来选项）。
- 不改变 `STATE_UNKNOWN` 语义与审计契约（「外部状态未知，请勿自动重试或回退命令」）。
- 不改变命令工具自身的审批策略（玩家显式要求原始命令的路径不变）。
- 不改变专用工具对多格方块的拒绝（Script API 单格写入仍返回 `UNSUPPORTED_BLOCK_PLACEMENT`）。

## 3. 契约：fallback 一元规则

### 3.1 规则定义

```text
fallback_allowed = (code != INTERNAL_ERROR) and (external_state_unknown == false)
```

单一事实源：`services/agent/block_ops/schema.py` 新增

```python
def fallback_allowed_for_code(code: BlockErrorCode | str) -> bool:
    """一元规则：世界状态已知未变即允许命令回退。"""
    return code not in {BlockErrorCode.STATE_UNKNOWN, BlockErrorCode.INTERNAL_ERROR}
```

### 3.2 错误码对照

| code | 语义 | 旧值 | 新值 |
| --- | --- | --- | --- |
| `UNSUPPORTED_BLOCK_PLACEMENT` | 多格方块，未发送 | false | **true** |
| `PRECONDITION_FAILED` / `PRECONDITION_CHANGED` | 写前校验不满足，未写入 | false | **true** |
| `PROTECTED_BLOCK` | 受保护数据（容器等），未写入 | false | **true** |
| `BLOCK_UNKNOWN` / `STATE_INVALID` | 类型 / states 非法，未发送 | false | **true** |
| `LIMIT_EXCEEDED` | 超预算 / 超体积，未发送 | false | **true** |
| `INVALID_ARGUMENT` / `INVALID_COORDINATE` | 宿主侧参数错误，未发送 | false | **true** |
| `UNLOADED_CHUNK` / `OUT_OF_BOUNDS` / `CONFLICTING_EDITS` | 目标不可写，未写入 | false | **true** |
| `ADDON_UNAVAILABLE` / `UNSUPPORTED_CAPABILITY` | 桥接不可用 / 能力缺失 | true | true |
| `STATE_UNKNOWN` | 调用开始后未知，可能已写入 | false | **false（保留）** |
| `INTERNAL_ERROR` | 宿主内部错误 | false | **false（保留）** |

### 3.3 运行时回退检查（不变的部分）

`services/agent/harness/execution.py` `_block_command_fallback_denial` 逻辑不变：仅当最近一次专用方块失败记录 `fallback_allowed == false` 时拒绝同 run 内 `setblock` / `fill` / `clone` 回退。一元规则落地后该拒绝只在 `STATE_UNKNOWN` / `INTERNAL_ERROR` 时触发。玩家显式要求原始命令（`_is_explicit_raw_command_prompt`）的放行路径不变。

### 3.4 命令回退的既有安全闸门（保持不变）

- 回退命令仍按 `run_minecraft_command` 自身策略执行：`setblock` / `fill` / `clone` 在审批根列表中，需要玩家审批（或会话级自动同意）；
- `_BLOCK_COMMAND_FALLBACK_ROOTS`（setblock / fill / clone）限定回退命令范围，其它命令根不受影响；
- 回退限制按 `(connection_id, player_name, run_id)` 隔离，TTL 600 秒。

## 4. sapi 契约更新（Add-on）

### 4.1 能力声明（getCapabilities.ts）

`multiblock_placement` 值从 `"unsupported"` 改为 `"command_fallback"`：

```ts
/** "command_fallback": multiblock blocks (doors, beds, tall plants) cannot be
 * written by the Script API single-cell path (setPermutation writes one half),
 * but the command path (/setblock /fill) places the full double-block
 * structure since Bedrock 1.26.10 (Microsoft Update1.26.10). */
multiblock_placement: "command_fallback";
```

宿主不读取该字段，纯声明性，改值无运行时影响。

### 4.2 运行时拒绝（common.ts，逻辑不变，载荷自文档化）

- 错误码、message、`multiblock: true`、`reason: "multiblock_not_supported"` 全部保留（拒绝点本身正确）；
- message 追加 `; command fallback via setblock/fill is supported`；
- 载荷新增 `command_fallback_viable: true`（宿主不读，自文档化契约）。

### 4.3 过时注释（multiblock.ts）

`isMultiblockBlock` 的模块注释删除「fallback_allowed=false so the model does not fall back to a single setblock (which has the same single-cell problem)」段落，改为：

- 判断只约束 **Script API 单格写入路径**（`setPermutation` 只写一个 permutation）；
- 命令路径 `/setblock` / `/fill` 自 Bedrock 1.26.10 起完整放置双格结构（引用 Update1.26.10）；
- `fallback_allowed` 由宿主按一元规则计算，Add-on 不再假定其值。

### 4.4 错误载荷对齐（fill.ts）

`PRECONDITION_FAILED` 载荷中 `fallback_allowed: false` → `true`（宿主虽忽略 Add-on 此字段，契约保持一致，避免误导）。

### 4.5 Add-on 测试

同步断言 message / 载荷字段的测试（`MCBE-AI-Agent-addon/tests/bridge/blocks/repairSafety.test.ts` 等，若有）。

## 5. 宿主实现（Python）

| 位置 | 改动 |
| --- | --- |
| `services/agent/block_ops/schema.py` | 新增 `fallback_allowed_for_code(code)`；`_host_limit_error` 显式写入 `fallback_allowed`（当前缺失，`execution.py:782` 读默认 false 会误拒） |
| `services/agent/block_ops/bridge.py` | `_safe_addon_error_body` 白名单（412-415 行）改为调用新函数；删除 431/458/493/512/531/553 行各分支硬编码 `fallback_allowed=False`；`map_bridge_exception` pre-mutation LIMIT 分支（204 行）改为 true |
| `services/agent/block_ops/tools_impl.py` | `command_line_budget_exceeded_result`（685 行）、`locked_targets_wire_limit_exceeded`（837 行）的 `fallback_allowed` 改为新函数 |
| `services/agent/block_ops/bridge.py` `UNSUPPORTED_BLOCK_PLACEMENT` hint | 改写为指引命令回退（见 §6） |
| `services/agent/harness/execution.py` | `_block_command_fallback_denial` 逻辑不变；拒绝文案「或等待明确允许回退的结果」微调（可选） |
| `services/agent/harness/prompting.py` | 不改（「仅 fallback_allowed=true 时才能考虑命令回退」语义不变） |

## 6. 提示词与 hint

`UNSUPPORTED_BLOCK_PLACEMENT` 的 hint（bridge.py 554 行）改写：

> 多格方块（门、床、高草等）专用工具不支持单格写入；可用 run_minecraft_command 的 setblock 回退（Bedrock 1.26.10+ 自动放置完整结构，无需指定 half 等 Java 状态语法）。

要点：

- 明确「专用工具不支持」与「命令可回退」的边界；
- 引导无状态 `setblock <pos> minecraft:oak_door`（避免模型沿用 Java 语法 `[facing=south,half=lower]`）；
- `_BLOCK_TOOL_PRIORITY`（prompting.py）不改，仍由 `fallback_allowed` 字段驱动决策。

## 7. 文档更新

1. `docs/sapi/block-operations.md`：新增「§5.5 多格方块（门 / 床 / 高草）行为对照」：
   - Script API `setPermutation` / `setBlockPermutation` 为单格写入 → 门只写一个 permutation（残缺结构），Add-on 以 `UNSUPPORTED_BLOCK_PLACEMENT` 拒绝；
   - 命令 `/setblock` / `/fill` 自 Bedrock 1.26.10 起完整放置双格结构（引用 Update1.26.10 原文）；
   - 门的状态清单（`minecraft:cardinal_direction` / `upper_block_bit` / `open_bit` / `door_hinge_bit`）+ setblock 状态语法示例；
   - 附注：Add-on 未来若支持多格放置，路径为写上下两格（`upper_block_bit` 分设）+ 写后校验，或直接命令回退。
2. `docs/superpowers/specs/2026-08-04-block-tools-single-op-design.md`：
   - §2.2 非目标改为「专用工具不实现多格方块放置；命令回退由 setblock / fill 承担（1.26.10+ 完整放置）」；
   - §5.4 失败契约补 fallback 一元规则；
   - §8.2.4 测试项更新（命令回退按一元规则放行）；
   - §12 默认假设补一条（fallback 一元规则 + STATE_UNKNOWN 例外）。
3. 旧 grouped spec（`docs/spec/2026-07-30-block-tools-grouped-edit-design.md`）§10.2 加变更注记（可选）。

## 8. 测试计划

### 8.1 单元（Python）

1. `fallback_allowed_for_code` 全码表参数化：§3.2 表中每个 code 断言期望值。
2. `map_addon_bridge_result`：`UNSUPPORTED_BLOCK_PLACEMENT` / `PRECONDITION_FAILED` / `PROTECTED_BLOCK` 等映射后 `fallback_allowed == true`；`STATE_UNKNOWN` / `INTERNAL_ERROR` 保持 false。
3. `_host_limit_error` / `command_line_budget_exceeded_result`：宿主侧错误显式携带 `fallback_allowed: true`。
4. `tests/test_block_ops.py` 既有断言更新：3136 行（UNSUPPORTED_BLOCK_PLACEMENT）、3163 行参数化（BLOCK_UNKNOWN / STATE_INVALID / UNSUPPORTED_BLOCK_PLACEMENT）、650-804 行各码断言。

### 8.2 单元（Harness）

1. 链路测试：bridge 映射后的 `PRECONDITION_FAILED`（`fallback_allowed: true`）→ `_block_command_fallback_denial` 返回 None（不拒绝 setblock 回退）。
2. 保留：`STATE_UNKNOWN`（`fallback_allowed: false`）→ 拒绝；`_is_explicit_raw_command_prompt` 放行路径。

### 8.3 集成（Add-on）

1. `repairSafety.test.ts` 等：若断言 message / 载荷字段，同步新契约。
2. `getCapabilities` 返回值断言：`multiblock_placement === "command_fallback"`。

### 8.4 手工验收（游戏内，需 Bedrock ≥ 1.26.10）

1. 「放个门」：`place_block` 门 → `UNSUPPORTED_BLOCK_PLACEMENT`（`fallback_allowed: true`）→ 模型 `run_minecraft_command: setblock <pos> minecraft:oak_door` → 出现完整门（上下两半）。
2. 回归：正常 fill / place 不受影响；超时场景（`STATE_UNKNOWN`）回退仍被拒；命令回退仍需玩家审批。

## 9. 分阶段实施

### 阶段一：宿主一元规则

1. `schema.py` 新增 `fallback_allowed_for_code` + `_host_limit_error` 补字段；
2. `bridge.py` `_safe_addon_error_body` / `map_bridge_exception` 改用新函数，删除硬编码；
3. `tools_impl.py` 两个 LIMIT 构造点改用新函数；
4. `UNSUPPORTED_BLOCK_PLACEMENT` hint 改写；
5. 更新 `test_block_ops.py` 与 `test_runtime_harness_execution.py`，新增链路测试。

### 阶段二：Add-on 契约

1. `getCapabilities.ts` 值改 `"command_fallback"` + 注释；
2. `common.ts` message / 载荷自文档化；
3. `multiblock.ts` 注释更新；`fill.ts` 载荷对齐；
4. Add-on 测试同步。

### 阶段三：文档与验收

1. `docs/sapi/block-operations.md` 补 §5.5；
2. single-op spec 修订；
3. 运行 §8.4 手工验收并记录；更新本文档状态。

## 10. 迁移与兼容

- 错误码集合不变，`fallback_allowed` 只放宽不收紧；旧审计记录字段兼容。
- Add-on 无需发布周期：宿主按错误码重算 `fallback_allowed`，`multiblock_placement` 值纯声明。
- `STATE_UNKNOWN` / `INTERNAL_ERROR` 行为完全不变，审计「外部状态未知」语义不变。
- 旧版服务器（< 1.26.10）上 `setblock` 放门可能只放半格：hint 中注明要求 1.26.10+，模型可改为分上下两格放置（`upper_block_bit`）兜底。

## 11. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| `PROTECTED_BLOCK` 放行后，命令可在玩家审批后绕过 Add-on 保护（覆盖容器数据） | 命令回退仍需玩家审批；模型可见 hint（受保护数据提示）；显式原始命令路径本就允许，收紧无意义 |
| 模型沿用 Java 状态语法（`[facing=south,half=lower]`）导致 setblock 失败 | hint 引导无状态 setblock；错误信息不镜像 addon 原始堆栈 |
| 旧版服务器（< 1.26.10）setblock 只放半格门 | hint 注明 1.26.10+；模型可两格放置兜底；验收环境需 ≥ 1.26.10 |
| 放宽后模型过度依赖命令回退而非修正专用工具参数 | `_BLOCK_TOOL_PRIORITY` 保持「优先专用工具」；命令回退仍需审批，成本更高 |

## 12. 默认假设（待用户确认）

1. `multiblock_placement` 能力值改为 `"command_fallback"`（已确认）；
2. fallback 一元规则按 §3.1 实现，`STATE_UNKNOWN` / `INTERNAL_ERROR` 保持不回退（已确认）；
3. `PROTECTED_BLOCK` / `PRECONDITION_FAILED` 等写前拒绝码一并放行（一元规则的直接推论，审批闸门不变）；
4. Add-on 侧不改运行时逻辑，只改声明 / 载荷 / 注释（宿主不读这些字段，无运行时影响）。
