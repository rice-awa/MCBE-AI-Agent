# 方块工具单一职责契约实施计划（place_block / fill_block / inspect_block）

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 `subagent-driven-development`（推荐）或按顺序逐 Task 执行本计划。步骤使用复选框（`- [ ]`）跟踪；每个 Task 完成后勾选并记录证据。源规格：`docs/superpowers/specs/2026-08-04-block-tools-single-op-design.md`（下文称「规格」），实现细节冲突时以规格为准，发现规格矛盾时先回写规格再继续。

**Goal:** 把 `inspect_block` / `edit_blocks` 重构为三个单一职责工具 `inspect_block` / `place_block` / `fill_block`：模型可见契约只暴露绝对坐标 `[x, y, z]`、`block`/`expect`/`states` 与单点/区域 target；审批恢复改为 `plan_id` 内部执行，不再回灌隐藏 kwargs；结果投影瘦身为决策字段；提示词与 schema 去重；同时保持 Add-on 线协议与 `commandLine` 461 字节预算不变。

**Architecture:** 模型可见层（`services/agent/tools.py` 注册的三个公开工具）→ Harness `HarnessToolset.call_tool`（preflight → 审批 → execute）→ 内部执行器（`place_block_impl` / `fill_block_impl` / `inspect_block_impl`，映射为 Add-on `mode=place` / `mode=fill` / `inspect_block` 帧）→ `bridge.py` 调用 Add-on capability。审批恢复路径由「恢复 execute_args 回灌公开函数」改为「恢复 `plan_id` 查 `preflight_cache` 后调用 `execute_block_plan(plan_id)`」。

**Tech Stack:** Python 3.11+、Pydantic v2、PydanticAI、pytest + pytest-asyncio；不修改 MCBE-AI-Agent-addon TypeScript 代码，不新增依赖。

## Global Constraints

- 不修改 Add-on 二进制协议与 `commandLine` 461 字节硬预算；线协议 `capability=edit_blocks` / `inspect_block` 保留。
- 模型可见参数只允许：`pos` / `from` / `to` / `target` / `block` / `expect` / `states`；删除 `edits`、`dimension`、`mode`、`positions`、`coordinate_mode`、`locked_targets`、`phase`、`status` 等一切内部字段。
- 坐标一律为绝对世界坐标整数 `[x, y, z]`；本轮不支持玩家相对坐标与 `dimension` 参数（默认当前事件玩家维度，沿用现有 Add-on 默认机制）。
- 审批语义保持「一次工具调用 = 一个审批单元，并行待审批调用聚合为同一批」；`AGENT 同意 对话/永远` 行为不变。
- 审批恢复只保存 `plan_id` + `execution_args_hash`；恢复执行 `execute_block_plan(plan_id)`，任何情况下不得把 `locked_targets` / `phase` / `status` 等隐藏 kwargs 注入公开函数。
- 高风险写入仍走 preflight → 审批 → execute；`fallback_allowed` 语义不变，只有结构化结果明确放行时才允许命令回退。
- 修改玩家会话、聊天、上下文、下行消息路径时显式传递当前事件的 `player_name` / `sender`。
- 新增或修改下行长文本发送路径必须走 `BrokerResponseBridge` 或 SDK delivery，不重复实现分片。
- 实施前阅读：`CONTEXT.md`、`CLAUDE.md`、本计划、规格、`docs/addon-bridge-protocol.md`；被替换设计 `docs/spec/2026-07-30-block-tools-grouped-edit-design.md` 仅作背景。
- Git：复用当前分支 `docs/block-tools-single-op-design`（已含规格）继续提交；提交信息用 Conventional Commits（如 `refactor(block-ops): ...`）；自测通过后 PR 合入 `dev`，不直接提交 `master`/`dev`，不提交 `.env`/`config.json`/日志。
- 测试默认离线运行，不访问真实 Provider、真实 Minecraft 世界或 WebSocket。

## File Map

| 文件 | 责任 |
| --- | --- |
| `services/agent/tools.py` | 注册 `place_block` / `fill_block`，重写 `inspect_block`；删除 `edit_blocks` 与旧 Pydantic 模型 |
| `services/agent/block_ops/tools_impl.py` | 三个实现函数、preflight 与 `plan_id` 生成、`execute_block_plan`、Add-on 帧映射、预算防御；删除 grouped/legacy 逻辑 |
| `services/agent/block_ops/target.py` | inspect target 归一化改为新单点/区域结构（或合并入 tools_impl） |
| `services/agent/block_ops/project.py` | 新 place/fill/inspect 结果投影；删除 grouped 聚合投影 |
| `services/agent/block_ops/bridge.py` | 桥接调用与错误映射适配新工具名；保留 Add-on capability 名 |
| `services/agent/block_ops/schema.py` | 稳定错误码与失败 envelope（`INVALID_COORDINATE` 已存在；必要时扩展 LIMIT/PRECONDITION 字段） |
| `services/agent/block_ops/preflight_cache.py` | `plan_id` 字段与按 `plan_id` 查询/清理 |
| `services/agent/block_ops/config.py` | 删除 grouped 上限；保留 fill/inspect/预算配置 |
| `config/settings.py` / `config.example.json` | 同步配置字段清理 |
| `services/agent/harness/execution.py` | `_BLOCK_OPS_TOOLS` 更新；deferred approved 走 `plan_id`；删除 `strip_block_internal_tool_schema` / `_python_tool_args` 的 legacy 分支 |
| `services/agent/harness/approvals.py` | `PendingApproval` 记录 `plan_id` + `execution_args_hash`，不再持久化完整 `execute_args` |
| `services/gateway/command_handlers.py` | 审批恢复 payload 携带 `plan_id`；删除 `project_block_execute_args` 校验 |
| `services/agent/harness/prompting.py` | `_BLOCK_TOOL_PRIORITY` 替换为规格 §6.1 条目化文本 |
| `services/agent/harness/catalog.py` | 移除 `edit_blocks`，新增 `place_block` / `fill_block`，更新 `inspect_block` 卡片 |
| `services/agent/block_ops/__init__.py` | 导出清理（删 `edit_blocks_impl` / `project_block_execute_args` 等） |
| `tests/test_block_ops.py` | 新契约单元测试；删除 grouped 测试 |
| `tests/test_runtime_harness_execution.py` / `tests/test_runtime_harness_audit.py` | `plan_id` 恢复、无隐藏 kwargs、审计字段适配 |
| `tests/test_runtime_harness_catalog.py` / `tests/test_runtime_harness_prompt.py` | 新目录/提示断言 |
| `tests/test_tool_approval_command.py` | 审批批量与恢复按新契约 |
| `tests/test_agent_tools.py` | 注册工具 schema 无隐藏字段、描述前缀 |
| `docs/superpowers/specs/2026-08-04-block-tools-single-op-design.md` | 验收后更新状态 |
| `docs/validation/2026-08-04-block-tools-single-op-acceptance.md` | 新建手工验收记录（轮次/耗时/token 对照） |

---

### Task 1: 新工具公开契约与 schema

**Files:**
- Modify: `services/agent/tools.py`
- Test: `tests/test_agent_tools.py`
- Test: `tests/test_block_ops.py`（schema 断言部分）

**Interfaces:**
- 新增 `place_block(ctx, pos: [int, int, int], block: str, expect: str = "air", states: dict | None = None) -> str`
- 新增 `fill_block(ctx, from_: [int, int, int], to: [int, int, int], block: str, expect: str = "air", states: dict | None = None) -> str`（`from_` 使用 `Field(alias="from")`）
- 重写 `inspect_block(ctx, target: [int, int, int] | [[int, int, int], [int, int, int]]) -> str`
- 删除 `edit_blocks` 与旧模型 `BlockTarget` / `BlockTargetBox` / `BlockEdit` / `AbsoluteBlockCoordinate` / `RelativeBlockCoordinate` / `BlockCoordinate` / `BlockSpec` / `ExpectInput` / `BlockInput`（按引用清理）
- 公开函数签名不含任何隐藏参数；`strip_block_internal_tool_schema` 对新工具应无字段可剥

- [ ] **Step 1: 写失败契约测试**

```python
def test_block_tool_schemas_only_expose_public_fields():
    # 通过 get_agent_tools_container / iter_registered_tools 获取三工具 schema
    # 断言 properties 恰为 {pos|from|to, block, expect, states} 或 {target}
    # 断言不含 edits/dimension/mode/positions/coordinate_mode/locked_targets/phase/status
```

另加：`pos` 长度 3、`expect` 默认 `"air"`、`states` 可选、`inspect_block.target` 单点/双角点二选一、`fill_block` 的 JSON 参数名为 `from`/`to`。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_agent_tools.py -q`

Expected: FAIL（新工具不存在 / 旧工具仍在）。

- [ ] **Step 3: 定义坐标与参数类型**

新增严格类型（可用 `Annotated` 或小型 Pydantic 模型）：`BlockPosition = Annotated[list[int], Field(min_length=3, max_length=3)]`；`InspectTarget = BlockPosition | Annotated[list[BlockPosition], Field(min_length=2, max_length=2)]`。坐标必须为整数，非整数/非列表/长度不符由宿主返回 `INVALID_COORDINATE`（复用现有 `BlockErrorCode.INVALID_COORDINATE`，不抛 Python 异常）。

- [ ] **Step 4: 注册新工具、删除旧入口**

在 `register_agent_tools` 中新增 `place_block` / `fill_block`，重写 `inspect_block` docstring（只写「职责 + 参数 + 结果语义」，不复制恢复策略与长示例）；删除 `edit_blocks` 及其 docstring。旧 Pydantic 模型按引用删除或标记待 Task 6 清理。

- [ ] **Step 5: 验证 schema 并运行测试**

Run: `pytest tests/test_agent_tools.py tests/test_block_ops.py -q`

Expected: PASS（此阶段实现函数可先抛 `NotImplementedError` 之外的占位返回，待 Task 2 接实现）。

---

### Task 2: 实现层与 Add-on 帧映射

**Files:**
- Modify: `services/agent/block_ops/tools_impl.py`
- Modify: `services/agent/block_ops/target.py`
- Modify: `services/agent/block_ops/bridge.py`（仅调用方适配，投影见 Task 4）
- Test: `tests/test_block_ops.py`

**Interfaces:**
- 新增 `place_block_impl(ctx, *, pos, block, expect="air", states=None, phase="execute", locked_targets=None) -> ToolResult`
- 新增 `fill_block_impl(ctx, *, from_, to, block, expect="air", states=None, phase="execute", locked_targets=None) -> ToolResult`
- 精简 `inspect_block_impl(ctx, *, target, phase="execute", locked_targets=None) -> ToolResult`（仅新 target；旧 `position`/`positions`/`coordinate_mode` 路径删除）
- 帧映射：`place_block` → `build_edit_payload(mode="place", position=...)`；`fill_block` → `build_edit_payload(mode="fill", from_pos=min(from,to), to_pos=max(from,to))`；`expect` 映射为 `replace_any` / `expected_previous`
- 保留 `check_bridge_command_line_budget` 作为防御；更新 `_COMMAND_LINE_BUDGET_HINT` 为「缩小 fill AABB 或减少 states；禁止拆成大量 place；勿用命令绕过审批」

- [ ] **Step 1: 写映射/校验失败测试**

```python
def test_place_maps_to_mode_place_frame():
    # place_block_impl 构造 payload：mode=place、position={x,y,z}、type_id 归一化
    # expect=air → replace_any=False 且无 expected_previous；expect=any → replace_any=True
    # expect="minecraft:stone" → expected_previous={type_id:"minecraft:stone"}

def test_fill_normalizes_corners_and_enforces_volume():
    # from=(10,10,10), to=(8,8,8) → from_pos=min、to_pos=max
    # 体积 > max_fill_volume → LIMIT_EXCEEDED，含缩小方向 hint

def test_long_states_frame_hits_budget_defense():
    # 构造超长 states 触发 check_bridge_command_line_budget → LIMIT_EXCEEDED，确认不发送
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_block_ops.py -q`

- [ ] **Step 3: 实现三个 impl**

复用现有 `build_edit_payload` / `build_inspect_payload_from_target` / `_normalize_block_id` / `_normalize_aabb_corners` / `_aabb_volume`；新 impl 只接受新参数形态，内部转换为 legacy 帧。`dimension` 不再作为参数：内部不传 `dimension`，沿用 Add-on 默认当前玩家维度（规格 §3.3）。

- [ ] **Step 4: 接入公开函数**

`tools.py` 的三个函数调用新 impl，删除对 `edit_blocks_impl` 的引用；旧 grouped 实现先保留（Task 6 删除），但公开路径不再可达。

- [ ] **Step 5: 运行测试**

Run: `pytest tests/test_block_ops.py -q`

Expected: PASS；确认任何测试不再通过 `edit_blocks` 公开入口。

---

### Task 3: `plan_id` 审批恢复路径

**Files:**
- Modify: `services/agent/block_ops/preflight_cache.py`
- Modify: `services/agent/block_ops/tools_impl.py`
- Modify: `services/agent/harness/execution.py`
- Modify: `services/agent/harness/approvals.py`
- Modify: `services/gateway/command_handlers.py`
- Test: `tests/test_runtime_harness_execution.py`
- Test: `tests/test_tool_approval_command.py`
- Test: `tests/test_runtime_harness_audit.py`

**Interfaces:**
- `BlockPreflightPlan` 增加 `plan_id: str`；`PreflightCacheEntry` 增加 `plan_id: str`
- `PreflightCache.put(...)` 生成/接收 `plan_id`；新增 `get_by_plan_id(plan_id) -> PreflightCacheEntry | None`
- `run_block_preflight` / `build_block_preflight_plan`：preflight 成功后生成 `plan_id` 并缓存；`execute_args` 不再含隐藏 kwargs（只保留模型可见字段，实际执行由 `execute_block_plan` 从缓存取 frozen canonical args）
- 新增 `async execute_block_plan(plan_id: str, ctx: RunContext) -> ToolResult`：查缓存 → 校验 TTL → 按 frozen canonical args 调用对应 impl（place/fill），幂等执行一次；缓存缺失/过期返回 `STATE_UNKNOWN`（与现有审批过期语义一致）
- `PendingApproval` 增加 `plan_id: str = ""`；`execute_args` 持久化改为 `plan_id` + 既有 `execution_args_hash`（`args_summary` 保留用于玩家展示）
- `command_handlers._resume_from_completed_batch`：对三个块工具校验 `plan_id` 存在且哈希一致，恢复 payload 改为 `{"kind": "tool-approved", "plan_id": item.plan_id}`；删除 `project_block_execute_args` 校验
- `execution.py`：`_BLOCK_OPS_TOOLS = {"inspect_block", "place_block", "fill_block"}`；deferred approved / `tool_args["plan_id"]` 分支直接调用 `execute_block_plan`，不再 `super().call_tool(name, execute_args, ...)`；审计/追踪仍由 `call_tool` 包装，audit 的 `parameters`/`authorized_args` 从缓存 canonical args 还原

- [ ] **Step 1: 写恢复路径失败测试**

```python
def test_approval_resume_uses_plan_id_without_hidden_kwargs():
    # preflight(place_block) → 生成 plan_id → PendingApproval 仅存 plan_id+hash
    # 恢复时执行 execute_block_plan 一次；mock impl 断言收到的 kwargs 只有
    # pos/block/expect/states，不含 locked_targets/phase/status

def test_plan_id_resume_is_idempotent_and_missing_plan_is_state_unknown():
    # 同一 plan_id 第二次恢复不再重复写入（幂等缓存/已执行标记）
    # 缓存过期或缺失 → STATE_UNKNOWN，不抛 TypeError

def test_regression_status_kwarg_type_error_is_impossible():
    # 构造 2026-08-03 场景：恢复 payload 曾注入 status；新路径应因签名不含该字段而根本不可达
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_runtime_harness_execution.py tests/test_tool_approval_command.py -q`

- [ ] **Step 3: 实现 plan_id 缓存**

`preflight_cache.py` 增加 `plan_id`（`secrets.token_hex(16)` 或 uuid4），`PreflightCache.put` 同时按 `(run_id, tool_call_id, original_args_hash)` 与 `plan_id` 索引；TTL 沿用 `DEFAULT_APPROVAL_TTL_SECONDS`；`clear_expired` / `clear_connection` 同步清理两种索引。

- [ ] **Step 4: 实现 execute_block_plan 与恢复分支**

`tools_impl.py` 新增执行器；`execution.py` 的 `_preflight_block_tool` 在 deferred approved 时按 `plan_id` 取缓存；执行阶段对 `_BLOCK_OPS_TOOLS` 且带 `plan_id` 的调用改走执行器。删除 `_python_tool_args` 对块工具的 legacy 投影分支（先保留 inspect 非恢复路径），删除 `project_block_execute_args` 引用。

- [ ] **Step 5: 更新审批存储与网关**

`approvals.py` 持久化 `plan_id`；`command_handlers.py` 恢复 payload 改为 `plan_id`，删除 `project_block_execute_args` / `execution_args_hash` 对比逻辑（改为缓存内一致性校验）。

- [ ] **Step 6: 运行测试**

Run: `pytest tests/test_runtime_harness_execution.py tests/test_tool_approval_command.py tests/test_runtime_harness_audit.py -q`

Expected: PASS；`STATE_UNKNOWN` 语义与现有审批过期一致。

---

### Task 4: 结果投影瘦身

**Files:**
- Modify: `services/agent/block_ops/project.py`
- Modify: `services/agent/block_ops/bridge.py`
- Modify: `services/agent/block_ops/schema.py`（如失败字段需要扩展）
- Test: `tests/test_block_ops.py`

**Interfaces（以规格 §5 为准）：**
- `place_block` 成功：`{"ok": true, "status": "applied", "at": [x, y, z], "block": "minecraft:torch", "was": "minecraft:air"}`；`was` 仅非空气替换时出现
- `fill_block` 成功：`{"ok": true, "status": "applied", "changed": 15, "skipped": 10, "type_counts": {...}, "bounds": [[min], [max]]}`；`type_counts` 仅含非空气跳过/替换计数，全空气不出现
- `inspect_block` 单点：`{"ok": true, "block": ..., "states": {}, "waterlogged": false, "is_air": true, "is_liquid": false}`；区域：`{"ok": true, "count": N, "type_counts": {...}, "samples": [[x, y, z, "minecraft:air"], ...]}`，samples ≤8 且为紧凑数组
- 失败统一 envelope：`{schema_version, ok: false, code, message, retryable, fallback_allowed, hint}`；`LIMIT_EXCEEDED` 带 `estimated_bytes` / `budget`；`PRECONDITION_FAILED` 带 `actual_type_id`（首格）与坐标

- [ ] **Step 1: 写投影失败测试**

对照规格 §5.1–§5.4 的 JSON 样例逐字段断言；另加「不镜像 Add-on 原始 payload / locked_targets / before / after」断言。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_block_ops.py -q`

- [ ] **Step 3: 实现新投影**

`project.py` 新增 `project_place_result_for_model` / `project_fill_result_for_model` / 重写 `_project_inspect`（紧凑 samples）；删除 `_project_batch`、`project_group_edit_result_for_model`、`_per_edit_*` 辅助。`bridge.py`：`map_bridge_exception` 的 `tool_name == "edit_blocks"` 分支改为 `place_block` / `fill_block`（STATE_UNKNOWN 语义不变）；`call_block_capability` 调用方传入新 `mode`。

- [ ] **Step 4: 更新失败字段**

确认 `LIMIT_EXCEEDED` / `PRECONDITION_FAILED` 附加字段在宿主侧（`tools_impl.py` 校验与 `bridge.py` 错误映射）填充；无附加字段时不输出空键。

- [ ] **Step 5: 运行测试**

Run: `pytest tests/test_block_ops.py tests/test_runtime_harness_audit.py -q`

Expected: PASS。

---

### Task 5: 提示词与工具目录

**Files:**
- Modify: `services/agent/harness/prompting.py`
- Modify: `services/agent/harness/catalog.py`
- Test: `tests/test_runtime_harness_prompt.py`
- Test: `tests/test_runtime_harness_catalog.py`
- Test: `tests/test_agent_tools.py`

**Interfaces:**
- `_BLOCK_TOOL_PRIORITY` 整体替换为规格 §6.1 的条目化文本（fill 优先、单格 place、inspect 按需、expect 默认 air、失败只读 code/hint、并行独立调用），不再出现 `edit_blocks` / grouped edits / 最短 target JSON 长示例
- catalog：删除 `edit_blocks` 条目；新增 `place_block` / `fill_block`（`ToolIntent.CHANGE_WORLD` / `ToolRisk.HIGH`）；`inspect_block` 改为「单点或区域 target」描述（`ToolIntent.QUERY_WORLD` / `ToolRisk.LOW`）
- 每个工具 docstring 只写职责 + 参数 + 结果语义；`render_schema_description_prefix` 前缀保留

- [ ] **Step 1: 写断言失败测试**

`test_runtime_harness_catalog.py`：`list_tool_names()` 含 `place_block`/`fill_block`，不含 `edit_blocks`；`test_runtime_harness_prompt.py`：prompt 含「连续区域用 fill_block」「单格用 place_block」，不含「grouped edits」「合并为一个 edit_blocks」。

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_runtime_harness_prompt.py tests/test_runtime_harness_catalog.py -q`

- [ ] **Step 3: 更新提示与目录**

按 Interface 修改；参数约束文案与 schema 一致（`pos` / `from` / `to` / `target` / `block` / `expect` / `states`）。

- [ ] **Step 4: 运行测试**

Run: `pytest tests/test_runtime_harness_prompt.py tests/test_runtime_harness_catalog.py tests/test_agent_tools.py -q`

Expected: PASS。

---

### Task 6: 配置清理与遗留代码删除

**Files:**
- Modify: `services/agent/block_ops/config.py`
- Modify: `config/settings.py`
- Modify: `config.example.json`
- Modify: `services/agent/block_ops/tools_impl.py`
- Modify: `services/agent/block_ops/__init__.py`
- Modify: `services/agent/harness/execution.py`
- Modify: `services/agent/harness/catalog.py` / `services/agent/block_ops/bridge.py`（残留引用清理）
- Test: `tests/test_block_ops.py`

**Interfaces / 清理清单：**
- `config.py`：删除 `max_edits_per_group` / `max_total_targets_per_group` 及其常量（`HARD_MAX_EDITS_PER_GROUP` / `HARD_MAX_TOTAL_TARGETS_PER_GROUP` / `DEFAULT_MAX_*`）；`max_discrete_positions` 保留为内部防御并注释「模型侧 batch 已移除，仅作 Add-on 枚举上限映射」；保留 `max_fill_volume` / `cells_per_tick` / `max_locked_targets_on_wire` / `inspect_summary_threshold` / `inspect_sample_limit`
- `tools_impl.py`：删除 `edit_blocks_impl`、`_execute_edits_group`、`_execute_one_group_edit`、`_legacy_kwargs_from_edit`、`_EditNormalization`、`_GroupEdit`、`_EditPreflight`、`_run_grouped_edit_preflight`、`_normalize_edits_*`、`project_block_execute_args`、`_project_new_edit_execute_args`、`_EDIT_EXECUTE_FIELDS` / `_INSPECT_EXECUTE_FIELDS` 中 legacy 项、`_AUDIT_EDIT_EVIDENCE_FIELDS`（若不再需要）
- `execution.py`：删除 `strip_block_internal_tool_schema`、`_BLOCK_INTERNAL_SCHEMA_KEYS`、`_python_tool_args` 的块工具投影分支；`_BLOCK_OPS_TOOLS` 只含三新工具
- `__init__.py`：删除 `edit_blocks_impl` / `project_block_execute_args` 等导出
- `settings.py` / `config.example.json`：同步删除 grouped 字段；`tool_calls_limit` 不调整

- [ ] **Step 1: 先跑一次全量测试记录基线**

Run: `pytest -q`

Expected: 基线全绿（或记录既有失败清单，避免把旧失败误认为本次引入）。

- [ ] **Step 2: 删除配置与导出**

按清理清单删除；用 `rg -n "max_edits_per_group|max_total_targets_per_group|edit_blocks_impl|project_block_execute_args|strip_block_internal_tool_schema|_EditNormalization|_execute_edits_group" -g '*.py'` 找出残留并逐一清理。

- [ ] **Step 3: 修复受影响的既有测试**

把仍引用 `edit_blocks` / grouped 契约的测试改写为新契约（`tests/test_block_ops.py`、`tests/test_runtime_harness_*`、`tests/test_tool_approval_command.py`、`tests/test_agent_tools.py`），删除不再适用的 grouped 用例。

- [ ] **Step 4: 全量运行**

Run: `pytest -q`，随后按仓库配置运行格式/静态检查（ruff、mypy 等，以 `pyproject.toml` / `ruff.toml` 为准）。

Expected: PASS + 静态检查无新增问题。

---

### Task 7: 新契约回归与集成测试

**Files:**
- Modify: `tests/test_block_ops.py`
- Modify: `tests/test_runtime_harness_execution.py`
- Modify: `tests/test_tool_approval_command.py`
- Modify: `tests/test_agent_worker.py`（如审批/工具调用 fixture 引用旧名）

**场景（对应规格 §8.2）：**

- [ ] **Step 1: 审批批量回归**

同一轮 4 个并行 `fill_block` 合并为一批审批，一次 `AGENT 同意` 全部执行；`PendingApproval.batch_id` 与 `sibling_approval_ids` 语义不变。

- [ ] **Step 2: 旧 12 离散点场景回归**

模型只能表达为 4 个 fill 柱或 12 个 place；断言不再产生 `LIMIT_EXCEEDED`，且 `commandLine` 预算防御对单个新工具帧保持合规（对照规格 §1.2 量化表）。

- [ ] **Step 3: 草地建房回归**

`fill_block(expect="any")` 覆盖 grass 成功；`expect="air"` 时返回可操作 `PRECONDITION_FAILED`（含 `actual_type_id` 与坐标）。

- [ ] **Step 4: 命令回退门控**

`fallback_allowed=false` 时命令回退被拒绝（`_block_command_fallback_denial` 路径对新工具名生效）。

- [ ] **Step 5: 无隐藏字段终审**

新增断言：`iter_registered_tools` 输出的三个新工具 schema 不含隐藏字段，且三个 schema 总字符数低于旧 `edit_blocks` 单工具 schema（2,271 字符基线）；prompt 与 catalog 无 `locked_targets` / `phase` / `status` 字样。

- [ ] **Step 6: 全量运行**

Run: `pytest -q`

Expected: PASS。

---

### Task 8: 手工验收与文档收尾

**Files:**
- Create: `docs/validation/2026-08-04-block-tools-single-op-acceptance.md`
- Modify: `docs/superpowers/specs/2026-08-04-block-tools-single-op-design.md`（状态更新）
- Modify: 本计划（勾选完成）

**验收场景（对应规格 §8.3）：**

- [ ] **Step 1: Creative 建房场景**

5×5 木板地板 + 四面墙 + 屋顶 + 火把；目标 ≤6 轮、≤8 个工具调用、≤20 秒；记录每轮工具名/参数摘要。

- [ ] **Step 2: 审批恢复幂等**

审批后同进程内恢复：`plan_id` 路径执行一次，不重复写入；连续两次恢复同一 plan_id 第二次不重写；进程重启后的跨进程恢复不在本轮范围。

- [ ] **Step 3: Token 对照**

对照 2026-08-04 run `15492746`（12 次模型请求、152,495 input tokens）：同一「造个小房子」场景累计 input tokens 下降 ≥30%；记录轮次/耗时/token 到验收文档。

- [ ] **Step 4: 文档收尾**

验收文档记录证据与差异；规格状态从「设计已确认，待用户评审」更新为「已实施并验收」；本计划所有复选框勾选；git status 检查后提交（提交信息如 `docs(block-tools): 记录单一职责工具验收` / `refactor(block-ops): 单一职责工具落地`），PR 目标 `dev`。

---

## 完成定义（Definition of Done）

- 模型可见工具仅为 `inspect_block` / `place_block` / `fill_block`，全仓无 `edit_blocks` 模型可见残留（内部 capability 名除外）。
- 三个公开 schema 不含任何隐藏字段；`strip_block_internal_tool_schema` / `project_block_execute_args` 已删除。
- 审批恢复仅经 `plan_id` 执行 `execute_block_plan`；`TypeError: unexpected keyword argument 'status'` 回归测试通过。
- 结果投影符合规格 §5 样例；Add-on 线协议与 `commandLine` 预算未改。
- 提示词与目录只描述新契约；`max_edits_per_group` / `max_total_targets_per_group` 已清理。
- `pytest -q` 全绿；手工验收三场景完成并记录；spec 状态已更新。
