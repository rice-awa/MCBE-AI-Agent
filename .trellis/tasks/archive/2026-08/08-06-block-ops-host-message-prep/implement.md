# 方案三：集中 block_ops 宿主侧报文准备 — 执行计划

## 前置

- 依赖方案一已验收（已完成并合入 dev）。
- 工作分支：`refactor/block-ops-host-message-prep`。
- 基于最新 `dev` 创建。

## 实施顺序

### Step 1: 读当前代码（已完成）
- 已核对 `tools_impl.py`（62KB）、`target.py`、`__init__.py`、`bridge.py`、`preflight_cache.py`。
- 确认 `target.py:296` 反向导入 `tools_impl.apply_limits_to_payload`。
- 确认 `__init__.py` 重导出 30+ 名称。
- 确认外部调用方分布。

### Step 2: 创建 limits.py
- 从 `tools_impl.py` 提取 `_normalize_limits_input` → `normalize_limits_input`（公开）
- 提取 `apply_limits_to_payload` → 公开
- 提取 `estimate_bridge_command_line_bytes` → 公开
- 提取 `check_bridge_command_line_budget` → 公开
- 提取 `command_line_budget_exceeded_result` → 公开
- 提取 `_suggested_max_discrete_for_budget` → `suggested_max_discrete_for_budget`（模块内）
- 提取 `compact_locked_targets_for_wire` → 公开
- 提取 `locked_targets_wire_limit_exceeded` → 公开
- 提取 `should_omit_locked_targets_on_wire` → 模块内
- 提取 `_aabb_volume` → `_aabb_volume`（模块内）
- 提取 `_request_fill_aabb` → `_request_fill_aabb`（模块内）
- 移入后 `tools_impl.py` 导入 `limits` 替代本地定义
- 验证：`python -c "from services.agent.block_ops.limits import apply_limits_to_payload"`

### Step 3: 创建 message.py
- 从 `tools_impl.py` 提取 `build_inspect_payload` → 公开
- 从 `tools_impl.py` 提取 `build_edit_payload` → 公开
- 从 `target.py` 提取 `build_inspect_payload_from_target` → 公开
- `message.py` 依赖 `limits.py`（`apply_limits_to_payload`、`compact_locked_targets_for_wire`、`should_omit_locked_targets_on_wire`）
- 验证：`python -c "from services.agent.block_ops.message import build_inspect_payload"`

### Step 4: 修改 target.py
- 移除 `build_inspect_payload_from_target` 函数（已移至 `message.py`）
- 移除 `from services.agent.block_ops.tools_impl import apply_limits_to_payload`（消除反向导入）
- 验证：`grep "from services.agent.block_ops.tools_impl import" target.py` 应无结果
- 验证：`grep "import tools_impl" target.py` 应无结果

### Step 5: 创建 preflight.py
- 从 `tools_impl.py` 提取 `BlockPreflightPlan` dataclass → 公开
- 提取 `build_block_preflight_plan` → 公开
- 提取 `run_block_preflight` → 公开
- 提取 `merge_canonical_from_preflight` → 公开
- 提取 `_state_unknown_result` → `state_unknown_result`（公开化）
- `preflight.py` 依赖：`bridge`、`capability`、`config`、`limits`、`message`、`target`、`schema`、`tools_impl`（`_validate_inspect_args`、`_validate_inspect_target` 等校验函数）
- 验证：`python -c "from services.agent.block_ops.preflight import run_block_preflight"`

### Step 6: 瘦身 tools_impl.py
- 删除已提取到 `limits.py`、`message.py`、`preflight.py` 的所有函数引用
- 保留：参数校验 helper、工具实现（`inspect_block_impl`、`place_block_impl`、`fill_block_impl`）、`execute_block_plan`、`BLOCK_TOOL_NAMES`、内部辅助函数
- 更新 `tools_impl.py` 的导入：导入 `limits`、`message`、`preflight` 替换本地定义
- 将 `tools_impl.py` 中调用 `build_inspect_payload`/`build_edit_payload` 的地方改为 `message.build_inspect_payload`/`message.build_edit_payload`
- 将 `tools_impl.py` 中调用限制函数的地方改为 `limits.apply_limits_to_payload` 等
- 验证：`python -c "from services.agent.block_ops.tools_impl import inspect_block_impl, place_block_impl, fill_block_impl, execute_block_plan"`

### Step 7: 收窄 __init__.py
- 删除内部细节的重导出（`build_error_response`、`dumps_*`、`merge_canonical_from_preflight`、`NormalizedTarget` 等）
- 保持外部调用方实际使用的公共接口
- 从新模块 `limits`、`message`、`preflight` 导入必要的公开接口
- 验证：`python -c "from services.agent.block_ops import *"` 仅导出预期名称

### Step 8: 更新外部调用方导入
- `services/agent/harness/execution.py`：
  - Line 1606: `from services.agent.block_ops.tools_impl import BlockPreflightPlan, run_block_preflight` → `from services.agent.block_ops import BlockPreflightPlan, run_block_preflight`
  - Line 1693: `from services.agent.block_ops.tools_impl import _state_unknown_result, execute_block_plan` → `from services.agent.block_ops import execute_block_plan`（`state_unknown_result` 改为从 `preflight` 导入或通过 `block_ops` 导出）
- `services/agent/tools.py`：保留从 `tools_impl` 的导入，或改为 `from services.agent.block_ops import inspect_block_impl`
- `tests/test_block_ops.py`：导入内部校验函数的保持不变（它们本就是单元测试的合理目标），但改为通过子模块导入（如 `from services.agent.block_ops.tools_impl import _normalize_block_input` 不变）

### Step 9: 全量测试验证
```bash
pytest -q tests/test_block_ops.py
pytest -q -k "block"   # 方块相关测试
pytest -q   # 全量测试，确认无新增失败
```

### Step 10: trellis-check 复核
- 确认 `target.py` 不再反向导入 `tools_impl`
- 确认 `__init__.py` 出口已收窄
- 确认三个工具对同类错误结果一致

## 验证命令

```bash
# 模块导入验证
python -c "from services.agent.block_ops import inspect_block_impl, place_block_impl, fill_block_impl, execute_block_plan, BlockPreflightPlan, run_block_preflight"

# 消除反向导入验证
grep -n "from services.agent.block_ops.tools_impl import" services/agent/block_ops/target.py

# 方块测试
pytest -q tests/test_block_ops.py

# 多方块工具生命周期测试
pytest -q -k "block"

# 全量
pytest -q

# 类型检查
python -m pyright services/agent/block_ops/ 2>/dev/null || true
```

## 风险文件

| 文件 | 风险等级 | 原因 |
|------|---------|------|
| `tools_impl.py` | 高 | 最大文件（62KB），移动函数易漏掉跨模块引用或循环导入 |
| `target.py` | 中 | 需移除 `build_inspect_payload_from_target`，其他函数不受影响 |
| `__init__.py` | 中 | 移除重导出可能使外部调用方导入断裂 |
| `preflight.py` | 低 | 纯从 `tools_impl` 提取，不修改逻辑 |

## 回滚点

- 每步完成后验证导入，回退只需恢复对应文件。
- 如果验收门不满足（仍存在反向依赖 / 错误结果不一致），回退到开始前的提交。
