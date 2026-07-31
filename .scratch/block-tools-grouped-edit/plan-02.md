# Issue 02 实施计划：统一 target 的有界方块查看

**Spec:** `docs/spec/2026-07-30-block-tools-grouped-edit-design.md` §4.1, §7
**Ticket:** `.scratch/block-tools-grouped-edit/issues/02-unified-target-inspection.md`
**分支:** `feature/block-tools-improvement-plan`（已在当前分支）

## 目标

`inspect_block` 模型可见顶层输入收敛为：
```
target      必填，positions 或 box（互斥）
dimension   可选，绝对坐标跨维度时需要
```
按目标规模自动返回完整快照或有界摘要。

## 设计决策

### 1. 模型可见 schema

```python
async def inspect_block(
    ctx: RunContext[AgentDependencies],
    target: dict[str, Any],            # {positions: [...]} 或 {box: {from, to}}
    dimension: str | None = None,
    locked_targets: ... = None,         # harness recovery only
    phase: str | None = None,           # harness recovery only
) -> str:
```

- `target.positions`：非空 list，每个元素 `{x,y,z}` 或 `{forward,right,up}`
- `target.box`：`{from: {x,y,z}, to: {x,y,z}}`，角点顺序自动归一化
- 单点使用 `positions` 长度 1
- 同一 target 内不能混用绝对/相对坐标 → 调用 Add-on 前拒绝

### 2. 内部归一化（host 侧）

新增 `services/agent/block_ops/target.py`：统一目标解析模块。

- `normalize_inspect_target(target, dimension, player_name)` → 返回旧的
  `coordinate_mode / dimension / position / positions`（保持 Add-on 协议不变），
  或 `box` 形态时映射到 inspect 的 box 路径。
- 检测混用坐标模式 → `INVALID_ARGUMENT`
- 玩家相对坐标解析后冻结为绝对坐标（Add-on inspect 已经做了相对解析；
  本 issue 中 inspect 的相对解析继续由 Add-on 完成，host 只做形状校验和归一化）

### 3. Add-on inspect 支持 box

`inspect.ts` 增加 `box` 形态：
- 接受 `payload.box = {from, to}`，归一化角点
- 体积超 `max_fill_volume`（复用 fill 的体积限制）→ `LIMIT_EXCEEDED`
- 遍历 box 内每个 cell，构建 `BlockSnapshot`
- 输出按规模自动分层：
  - count ≤ `summary_threshold`（默认 8）：返回完整 `blocks` 列表
  - count > threshold：返回 `bounds / count / type_counts / unknown_count / samples`
- 未加载/越界 cell 计入 `unknown_count` 并出现在 `samples` 中标记
  `unknown/unloaded`，不伪装成空气

### 4. Add-on 类型扩展

`types.ts` 增加：
- `BoxTarget = { from: PositionInput; to: PositionInput }`
- `InspectSummary` 输出类型

### 5. Host 结果投影

`project.py` 增加 `project_inspect_result`：
- 单点/少量点：保留 `blocks` 完整字段（type_id / states / waterlogged / is_air / is_liquid）
- 多点/box：投影为有界摘要
- 从模型结果中剥离 `targets / player_origin / facing / player_name / repairs_applied / coordinate_mode / bridge diagnostics`
- 完整证据保留给工具审计（harness audit 仍可看到完整 payload）

### 6. 配置

`config.py` 增加 inspect 摘要配置（带硬上限）：
- `DEFAULT_INSPECT_SUMMARY_THRESHOLD = 8`（超过此数转摘要）
- `HARD_MAX_INSPECT_SUMMARY_THRESHOLD = 64`
- `DEFAULT_INSPECT_SAMPLE_LIMIT = 8`
- `HARD_MAX_INSPECT_SAMPLE_LIMIT = 32`

从 `settings.addon.block_tools` 读取 `inspect_summary_threshold` /
`inspect_sample_limit`，经 `_clamp` 保护。

### 7. catalog / prompt / schema 更新

- `catalog.py` inspect_block 条目：preview 改为 `("target", "dimension")`，
  parameter_constraints 描述新形状
- `prompting.py` 提示更新（minimal：仅编排规则，去掉 mode 细节）
- `tools.py` inspect_block 函数签名改为 `target / dimension`
- `execution.py` `_python_tool_args` inspect 分支白名单改为
  `{"target", "dimension", "locked_targets", "phase"}`
- `strip_block_internal_tool_schema` 仍剥离 `locked_targets / phase`

### 8. 旧参数兼容

本 issue 不要求保留旧 inspect schema（spec §12 说旧参数只在内部兼容层
短期接受，但 inspect 不像 edit 那样有大量旧调用）。为减少风险：
- `inspect_block_impl` 内部仍接受 `coordinate_mode / position / positions`，
  但模型可见 schema 只暴露 `target / dimension`。
- `_python_tool_args` 和 preflight 路径中，将 `target` 归一化为旧形状后
  传给 Add-on。

## 实施步骤

1. **Add-on types.ts**：增加 `BoxTarget` 类型
2. **Add-on inspect.ts**：支持 `box` 输入 + 自动分层摘要
3. **Add-on tests**：box 形态、反向角点、摘要分层、unknown/unloaded
4. **Host target.py**：统一目标解析模块 + 测试
5. **Host project.py**：inspect 结果投影
6. **Host config.py**：inspect 摘要配置
7. **Host tools_impl.py**：`inspect_block_impl` 接受 target，归一化后走旧路径
8. **Host tools.py**：inspect_block 函数签名
9. **Host catalog.py / prompting.py**：描述更新
10. **Host execution.py**：`_python_tool_args` 白名单更新
11. **Host tests**：target 归一化、投影、schema 剥离、端到端
12. **类型检查 + 全量测试**

## 不在范围

- `edit_blocks` 的新 schema（Issue 03）
- 多编辑组（Issue 04）
- 命名空间/拼写修复（Issue 05）
- 回退运行时检查（Issue 06）
