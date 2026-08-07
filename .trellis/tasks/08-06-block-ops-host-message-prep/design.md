# 方案三：集中 block_ops 宿主侧报文准备 — 技术设计

## 问题陈述

`block_ops` 包的当前职责分布存在三个问题：

1. **反向导入隐藏循环依赖** — `target.py:296` 在函数内导入 `tools_impl.apply_limits_to_payload`。这虽不产生导入期错误，但意味着 `target` 模块在概念上依赖 `tools_impl`，而 `tools_impl` 又导入 `target`（`from services.agent.block_ops.target import normalize_aabb_corners as _normalize_aabb_corners`），形成逻辑循环。

2. **包接口过大** — `__init__.py` 重导出 30+ 个名称，包括 `BlockPreflightPlan`、`build_block_preflight_plan`、`estimate_bridge_command_line_bytes` 等内部协调细节，外部调用方容易依赖这些内部细节而非稳定的包接口。

3. **`tools_impl.py` 职责过重（62KB）** — 同时包含参数校验、限制处理、字节预算、报文构建、预检编排、规范参数合并、工具实现和审批恢复执行。

## 设计目标

将上述职责集中到概念清晰的子模块中，消除反向导入，收窄包出口，使每个模块有明确的职责边界。

## 新包结构

```
block_ops/
├── __init__.py         # 收窄出口：只暴露公共工具接口和相关类型
├── bridge.py           # (不变) Addon 适配层
├── capability.py       # (不变) 能力缓存
├── config.py           # (不变) 限制配置
├── limits.py           # ← 新增：限制归一化、字节预算、payload 注入
├── message.py          # ← 新增：Addon 报文构建（从 tools_impl + target 提取）
├── preflight.py        # ← 新增：预检编排、规范参数合并、审批计划构建
├── preflight_cache.py  # (不变) 预检结果缓存
├── project.py          # (不变) 结果投影
├── schema.py           # (不变) 错误码与序列化
├── target.py           # (改) 移除反向导入，报文构建移到 message.py
├── tools_impl.py       # (改) 瘦身：只保留工具实现 + 参数校验 helper
```

## 模块职责与边界

### `limits.py`（新增）— 限制归一化与字节预算

从 `tools_impl` 提取：

| 导出名 | 来源函数 | 可见性 |
|--------|---------|--------|
| `normalize_limits_input(limits)` | `_normalize_limits_input` | 公开 |
| `apply_limits_to_payload(payload, limits)` | `apply_limits_to_payload` | 公开 |
| `estimate_bridge_command_line_bytes(...)` | `estimate_bridge_command_line_bytes` | 公开 |
| `check_bridge_command_line_budget(...)` | `check_bridge_command_line_budget` | 公开 (当前已导出) |
| `command_line_budget_exceeded_result(...)` | `command_line_budget_exceeded_result` | 公开 |
| `suggested_max_discrete_for_budget(...)` | `_suggested_max_discrete_for_budget` | 模块内 |
| `compact_locked_targets_for_wire(...)` | 原位置 | 公开 |
| `locked_targets_wire_limit_exceeded(...)` | 原位置 | 公开 |
| `should_omit_locked_targets_on_wire(...)` | 原位置 | 模块内 |
| `aabb_volume(from_pos, to_pos)` | `_aabb_volume` | 模块内 |
| `request_fill_aabb(args)` | `_request_fill_aabb` | 模块内 |

依赖：`schema.py`（`BlockErrorCode`、`_host_limit_error`）、`config.py`（`DEFAULT_COMMAND_LINE_BYTE_BUDGET`）

### `message.py`（新增）— Addon 报文构建

从 `tools_impl` + `target` 提取：

| 导出名 | 来源 |
|--------|------|
| `build_inspect_payload(...)` | `tools_impl.build_inspect_payload` |
| `build_edit_payload(...)` | `tools_impl.build_edit_payload` |
| `build_inspect_payload_from_target(...)` | `target.build_inspect_payload_from_target` |

依赖：`limits.py`（`apply_limits_to_payload`、`compact_locked_targets_for_wire`、`should_omit_locked_targets_on_wire`）、`target.py`（`NormalizedTarget`）

**关键设计决定**：`build_inspect_payload_from_target` 从 `target.py` 移至 `message.py`，因为它属于"报文构建"职责而非"目标归一化"职责。它强依赖 `apply_limits_to_payload`（之前的反向导入原因），移走后 `target.py` 不再需要导入 `tools_impl` 中的任何内容。

### `target.py`（修改）— 纯目标归一化

- 移除 `build_inspect_payload_from_target` 函数（移至 `message.py`）
- 不再导入 `tools_impl`（消除反向导入）
- 只保留：`NormalizedTarget`、`normalize_inspect_target`、`normalize_array_target`、`normalize_aabb_corners`、坐标判断辅助函数
- 依赖：`schema.py`（`BlockErrorCode`、`_host_limit_error`）

### `preflight.py`（新增）— 预检编排

从 `tools_impl` 提取：

| 导出名 | 来源 |
|--------|------|
| `BlockPreflightPlan` | `tools_impl.BlockPreflightPlan` |
| `build_block_preflight_plan(...)` | `tools_impl.build_block_preflight_plan` |
| `run_block_preflight(...)` | `tools_impl.run_block_preflight` |
| `merge_canonical_from_preflight(...)` | `tools_impl.merge_canonical_from_preflight` |
| `state_unknown_result(...)` | `_state_unknown_result` (公开化) |

依赖：`bridge.py`（`call_block_capability`）、`capability.py`（`ensure_block_capability`）、`config.py`、`limits.py`、`message.py`、`target.py`、`schema.py`、`tools_impl.py`（用于 `_validate_inspect_args`、`_validate_inspect_target` 等）

### `tools_impl.py`（瘦身后）— 工具实现 + 参数校验

移除所有已提取到 `limits.py`、`message.py`、`preflight.py` 的函数后，保留：
- 参数校验 helper：`_normalize_position_array`、`_normalize_single_op_block`、`_normalize_block_id`、`_normalize_block_input`、`_normalize_expect`、`_expect_to_legacy`、`_expect_info_to_legacy`、`_unified_target_to_legacy`、`_locked_targets_aabb`、`_validate_inspect_args`、`_validate_inspect_target`、`_validate_edit_args`
- 工具实现：`inspect_block_impl`、`place_block_impl`、`fill_block_impl`
- 审批恢复：`execute_block_plan`
- 内部辅助：`_connection_id`、`_plain_tool_data`、`_capability_failure_result`、`_require_supported`
- `BLOCK_TOOL_NAMES`

### 数据流（更新后）

```
工具意图 + 参数
    │
    ▼
tools_impl._validate_*
    │
    ├─target.py (目标归一化)                ← 纯函数，不再导入 tools_impl
    │
    ├─limits.py (限制/预算注入)             ← message.py 调用 apply_limits_to_payload
    │
    ▼
message.py (报文构建)                      ← build_inspect_payload / build_edit_payload
    │
    ▼
preflight.py (预检计划/合并/审批恢复)      ← run_block_preflight / merge_canonical...
    │
    ▼
bridge.py → Addon → 执行
    │
    ▼
tools_impl.py (impl 调用桥)               ← 三个 impl 消费预处理结果
```

## 收窄 `__init__.py` 出口

### 保留的导出

只导出外部模块（`tools.py`、`execution.py`、`hook.py`、`tests/`）实际使用的公共接口：

```python
# schema 核心类型/常量
BLOCK_OPS_SCHEMA_VERSION
BlockErrorCode

# 工具标识
BLOCK_TOOL_NAMES

# 能力缓存
BlockCapabilityCache, BlockCapabilityRecord, BlockCapabilityStatus
ensure_block_capability, clear_block_capability
get_block_capability_cache, reset_block_capability_cache

# 配置
BlockToolsLimits, get_block_tools_limits
DEFAULT_COMMAND_LINE_BYTE_BUDGET, get_command_line_byte_budget

# 预检缓存
PreflightCache, get_preflight_cache, reset_preflight_cache, clear_preflight_for_connection

# 桥适配
call_block_capability, map_addon_bridge_result, map_bridge_exception

# 预检编排
BlockPreflightPlan, run_block_preflight, build_block_preflight_plan

# 工具实现
inspect_block_impl, place_block_impl, fill_block_impl, execute_block_plan

# 限制
check_bridge_command_line_budget, estimate_bridge_command_line_bytes

# 结果投影
project_block_result_for_model
```

### 移除的重导出

| 名称 | 原因 | 替代 |
|------|------|------|
| `build_error_response` | 内部细节 | `schema.build_error_response` |
| `build_internal_error_response` | 内部细节 | `schema.build_internal_error_response` |
| `build_state_unknown_response` | 内部细节 | `schema.build_state_unknown_response` |
| `build_success_response` | 内部细节 | `schema.build_success_response` |
| `dumps_error`、`dumps_payload`、`dumps_success` | 序列化内部细节 | `schema.dumps_*` |
| `merge_canonical_from_preflight` | preflight 内部逻辑 | `preflight.merge_canonical_from_preflight` |
| `NormalizedTarget` (target.py) | target 内部概念 | `target.NormalizedTarget` |

## 外部调用方导入路径更新

### 需要更新的文件

| 调用方 | 当前导入 | 新导入 |
|--------|---------|--------|
| `services/agent/harness/execution.py:1606` | `from services.agent.block_ops.tools_impl import BlockPreflightPlan, run_block_preflight` | `from services.agent.block_ops import BlockPreflightPlan, run_block_preflight` |
| `services/agent/harness/execution.py:1693` | `from services.agent.block_ops.tools_impl import _state_unknown_result, execute_block_plan` | `from services.agent.block_ops import execute_block_plan` (state_unknown_result 改公开名) |
| `services/agent/tools.py` | `from services.agent.block_ops.tools_impl import inspect_block_impl` | 不变或 `from services.agent.block_ops import inspect_block_impl` |
| `tests/test_block_ops.py` | 多处直接导入 `tools_impl` 内部函数 | 改为导入 `block_ops` 或对应子模块 |

## 兼容性

- 不改变 `inspect_block`、`place_block`、`fill_block` 的对外参数（PRD 约束）
- 不改变 mcbews v1 报文格式（PRD 约束）
- `bridge.py` 继续作为 Addon 适配位置（PRD 约束）
- 移动的函数保持签名不变
- 被其他工具审批恢复的 `plan_id` 格式不变（`secrets.token_hex(16)`）

## 回滚形态

改动集中在 `block_ops/` 包内。若验收门不满足（仍存在反向依赖 / 错误结果不一致），回退到合入前提交。
