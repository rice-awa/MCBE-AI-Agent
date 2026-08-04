# 方块工具命令回退放宽实施计划（fallback 一元规则 + sapi 契约）

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 `subagent-driven-development`（推荐）或按顺序逐 Task 执行本计划。步骤使用复选框（`- [ ]`）跟踪；每个 Task 完成后勾选并记录证据。源规格：`docs/superpowers/specs/2026-08-04-block-tools-command-fallback-design.md`（下文称「规格」），实现细节冲突时以规格为准。

**Goal:** 专用方块工具失败后，命令回退从「错误码白名单」放宽为「世界状态已知未变即允许回退」一元规则：`fallback_allowed = (code != INTERNAL_ERROR) and (external_state_unknown == false)`。`UNSUPPORTED_BLOCK_PLACEMENT`（门/床等多格方块）等 14 个写前失败码放行，模型可回退 `setblock`（Bedrock 1.26.10+ 放置完整门）；`STATE_UNKNOWN` / `INTERNAL_ERROR` 保持拒绝。同步更新 Add-on 能力声明（`multiblock_placement: "command_fallback"`）与 `docs/sapi/block-operations.md` 多格方块行为对照。

**Architecture:** 放行逻辑完全宿主侧：`schema.py` 新增 `fallback_allowed_for_code()` 作为单一事实源，`bridge.py` / `tools_impl.py` / `schema.py._host_limit_error` 所有错误构造点统一调用；`execution.py` 的 `_block_command_fallback_denial` 机制不变（仅按记录的 `fallback_allowed` 拒绝），落地后只在 STATE_UNKNOWN/INTERNAL 时触发。Add-on 不改运行时逻辑（宿主按 code 重算、不读 addon 字段），只改声明/载荷/注释。

**Tech Stack:** Python 3.11+、pytest + pytest-asyncio；Add-on TypeScript（vitest，仅注释/常量/载荷字段改动，不新增依赖）。

## Global Constraints

- `fallback_allowed_for_code()` 是唯一放行判定；任何错误构造点不得再硬编码 `fallback_allowed`。
- `STATE_UNKNOWN` / `INTERNAL_ERROR` 保持 `fallback_allowed=false`；`execution.py` 拒绝机制、`_BLOCK_COMMAND_FALLBACK_ROOTS`（setblock/fill/clone）、审批根列表、显式原始命令放行路径全部不变。
- Add-on 运行时逻辑零改动：`isMultiblockBlock` 拒绝、错误码、`reason: multiblock_not_supported` 保留；仅 `getCapabilities.ts` 值、`common.ts` 载荷附加字段、`multiblock.ts` 注释、`fill.ts` 载荷字段对齐。
- 不改 `prompting.py`（「仅 fallback_allowed=true 时才能考虑命令回退」语义不变）；仅改 `UNSUPPORTED_BLOCK_PLACEMENT` 的 hint。
- 测试默认离线运行；游戏内验收需 Bedrock ≥ 1.26.10。
- Git：复用当前分支 `docs/block-tools-single-op-design`；提交信息用 Conventional Commits（如 `feat(block-ops): ...` / `docs(block-ops): ...`）；自测通过后 PR 合入 `dev`，不提交 `.env`/`config.json`/日志。

## File Map

| 文件 | 责任 |
| --- | --- |
| `services/agent/block_ops/schema.py` | 新增 `fallback_allowed_for_code()`；`_host_limit_error` 显式补 `fallback_allowed`（当前缺失 → execution.py:782 读默认 false 误拒） |
| `services/agent/block_ops/bridge.py` | `_safe_addon_error_body` 白名单改调用新函数、删各分支硬编码 False；`map_bridge_exception` pre-mutation LIMIT 分支放行；`UNSUPPORTED_BLOCK_PLACEMENT` hint 改写 |
| `services/agent/block_ops/tools_impl.py` | `command_line_budget_exceeded_result` / `locked_targets_wire_limit_exceeded` 的 `fallback_allowed` 改用新函数 |
| `tests/test_block_ops.py` | 各码断言更新 + 全码表参数化 |
| `tests/test_runtime_harness_execution.py` | 新增「写前失败码 → 不拒绝回退」链路测试；保留 STATE_UNKNOWN 拒绝测试 |
| `MCBE-AI-Agent-addon/scripts/bridge/capabilities/getCapabilities.ts` | `multiblock_placement: "unsupported"` → `"command_fallback"` + 注释 |
| `MCBE-AI-Agent-addon/scripts/bridge/capabilities/blocks/common.ts` | multiblock 错误 message 追加命令回退说明；载荷加 `command_fallback_viable: true` |
| `MCBE-AI-Agent-addon/scripts/bridge/capabilities/blocks/multiblock.ts` | 删除过时注释（setblock 单格问题）；改为约束 Script API 路径 + 引用 1.26.10 修复 |
| `MCBE-AI-Agent-addon/scripts/bridge/capabilities/blocks/fill.ts` | `PRECONDITION_FAILED` 载荷 `fallback_allowed: false` → `true` |
| `MCBE-AI-Agent-addon/tests/` | 同步受影响断言（repairSafety.test.ts 等） |
| `docs/sapi/block-operations.md` | 新增 §5.5 多格方块行为对照（Script API vs 命令 / 门状态清单 / 1.26.10 引用） |
| `docs/superpowers/specs/2026-08-04-block-tools-single-op-design.md` | §2.2 / §5.4 / §8.2.4 / §12 修订（非目标、失败契约、测试项、假设） |
| `docs/superpowers/specs/2026-08-04-block-tools-command-fallback-design.md` | 验收后更新状态 |

---

### Task 1: fallback 一元规则核心

**Files:**
- Modify: `services/agent/block_ops/schema.py`
- Test: `tests/test_block_ops.py`

- [x] `schema.py` 新增 `fallback_allowed_for_code(code) -> bool`（`code not in {STATE_UNKNOWN, INTERNAL_ERROR}`）。
- [x] `schema.py::_host_limit_error` 显式写入 `fallback_allowed=true`（INVALID_ARGUMENT / INVALID_COORDINATE / 宿主侧 LIMIT）。
- [x] 全码表参数化测试：规格 §3.2 表 16 个 code 断言期望值（14 true / 2 false）。
- [x] 证据：`pytest tests/test_block_ops.py -k fallback` 通过（26 passed）。

### Task 2: 桥接映射与 hint

**Files:**
- Modify: `services/agent/block_ops/bridge.py`
- Test: `tests/test_block_ops.py`

- [x] `_safe_addon_error_body`：白名单（412-415 行）改为调用 `fallback_allowed_for_code`；删除 431/458/493/512/531/553 行各分支 `fallback_allowed=False`。
- [x] `map_bridge_exception`：pre-mutation `LIMIT_EXCEEDED` 分支（204 行）`fallback_allowed` 改为 true；STATE_UNKNOWN / ADDON_UNAVAILABLE 分支不变。
- [x] `UNSUPPORTED_BLOCK_PLACEMENT` hint 改写为规格 §6 文案（指引 setblock 回退、1.26.10+ 完整放置、勿用 Java 状态语法）。
- [x] 测试更新：3136 行（UNSUPPORTED_BLOCK_PLACEMENT → true）、3163 行参数化（BLOCK_UNKNOWN/STATE_INVALID/UNSUPPORTED_BLOCK_PLACEMENT → true）、650-804 行各码按新规则；STATE_UNKNOWN / INTERNAL 断言保留 false。
- [x] 证据：`pytest tests/test_block_ops.py` 通过。

### Task 3: 宿主侧 LIMIT 构造点 + Harness 链路测试

**Files:**
- Modify: `services/agent/block_ops/tools_impl.py`
- Test: `tests/test_runtime_harness_execution.py`

- [x] `command_line_budget_exceeded_result`（685 行）、`locked_targets_wire_limit_exceeded`（837 行）改用新函数。
- [x] 新增链路测试：bridge 映射后的 `PRECONDITION_FAILED`（fallback_allowed=true）→ `_block_command_fallback_denial` 返回 None。
- [x] 保留并确认：STATE_UNKNOWN → 拒绝；显式原始命令 prompt 放行路径。
- [x] 证据：`pytest tests/test_runtime_harness_execution.py` 通过。

### Task 4: Add-on 契约更新

**Files:**
- Modify: `MCBE-AI-Agent-addon/scripts/bridge/capabilities/getCapabilities.ts`
- Modify: `MCBE-AI-Agent-addon/scripts/bridge/capabilities/blocks/common.ts`
- Modify: `MCBE-AI-Agent-addon/scripts/bridge/capabilities/blocks/multiblock.ts`
- Modify: `MCBE-AI-Agent-addon/scripts/bridge/capabilities/blocks/fill.ts`
- Test: `MCBE-AI-Agent-addon/tests/`

- [x] `getCapabilities.ts`：`multiblock_placement` 改 `"command_fallback"` + 注释（引用 Update1.26.10）。
- [x] `common.ts`：multiblock 错误 message 追加 `; command fallback via setblock/fill is supported`；载荷加 `command_fallback_viable: true`。
- [x] `multiblock.ts`：模块注释更新（只约束 Script API 单格写入；命令路径 1.26.10+ 完整放置；fallback_allowed 由宿主计算）。
- [x] `fill.ts`：PRECONDITION_FAILED 载荷 `fallback_allowed` → `true`。
- [x] Add-on 测试同步（getCapabilities 断言、repairSafety 等受影响的载荷断言）。
- [x] 证据：vitest 通过（92 passed）+ tsc --noEmit 干净。

### Task 5: 文档更新

**Files:**
- Modify: `docs/sapi/block-operations.md`
- Modify: `docs/superpowers/specs/2026-08-04-block-tools-single-op-design.md`

- [x] `block-operations.md` 新增 §5.5「多格方块（门/床/高草）行为对照」：Script API 单格写入 vs 命令完整放置、门状态清单（`minecraft:cardinal_direction` / `upper_block_bit` / `open_bit` / `door_hinge_bit`）、setblock 状态语法示例、1.26.10 引用。
- [x] single-op spec 修订：§2.2 非目标、§5.4 失败契约补一元规则、§8.2.4 测试项、§12 假设。
- [x] 证据：文档段落齐全、引用链接有效。

### Task 6: 验收

- [x] 离线全量回归：`pytest`（宿主 757 passed）+ Add-on vitest（92 passed）全绿。
- [ ] 游戏内（≥ 1.26.10）：「放个门」→ `place_block` 拒绝（`fallback_allowed: true`）→ 模型 `setblock` 回退 → 完整门出现；审批流程正常。（需人工环境）
- [x] 回归：正常 fill/place 不受影响（757 测试含 fill/place 回归）；STATE_UNKNOWN 场景回退仍被拒（`test_state_unknown_response_has_fallback_allowed_false`、`test_new_mutation_tools_map_timeout_to_state_unknown` 等）。
- [x] 更新 spec 文档状态为「已实施」；记录验收证据。
