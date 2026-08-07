# 架构优化后续改进：补全已验证模式、加深运行时与校验拆分

## Goal

基于架构优化六项候选方案的 review 反馈（[.trellis/tasks/archive/2026-08/08-06-architecture-review-optimization/](../archive/2026-08/08-06-architecture-review-optimization/prd.md)），落实三条后续改进建议。父任务只拥有源需求、子任务映射与最终验收。

## Requirements

### 子任务 1：记录 ExecutionResult 到已验证模式指南（spec-execution-result-pattern）

`docs/spec/guides/code-reuse-thinking-guide.md` 已记录了方案一（统一收尾链）作为已验证反重复模式。方案四提取的 `ExecutionResult` 是类似的"已验证收尾链契约边界"模式，也应记录进去。

- 补充 `ExecutionResult` 的契约说明及其所属位置（`services/agent/worker.py`）
- 说明为什么它是已验证模式：`_execute_single_request` 与 `_process_request_locked` 之间的契约，让测试通过稳定接口验证

### 子任务 2：继续加深 AgentRuntime（runtime-deepening）

方案六折叠了 4 个浅层转发函数（`get_agent_manager`/`get_mcp_manager`/`get_prompt_manager`/`get_conversation_manager`），但 `providers.py` 仍有部分间接依赖需要通过 `runtime_adapters` 或 `get_agent_runtime()` 访问运行时。

- 盘点方案六后仍存在的间接依赖
- 评估哪些可进一步加深到 `AgentRuntime`
- 目标：测试无需同时替换多个模块级获取函数

### 子任务 3：拆分 block_ops/tools_impl 校验函数到独立模块（block-ops-validation-split）

方案三新增了 `limits.py`/`message.py`/`preflight.py`，但 `tools_impl.py` 仍包含大量参数校验 helper（`_normalize_position_array`、`_normalize_block_input`、`_validate_inspect_args`、`_validate_edit_args` 等）。若后续 `tools_impl` 继续增长，可拆出 `validation.py` 模块。

- 评估当前 `tools_impl.py` 中校验函数的独立性和规模
- 若值得拆分，新建 `validation.py` 并迁移

## Acceptance Criteria

- [ ] 子任务 1：`code-reuse-thinking-guide.md` 增加 `ExecutionResult` 模式说明
- [ ] 子任务 2：`runtime.py` 或后续提交减少 `providers.py` 的间接依赖数
- [ ] 子任务 3：`tools_impl.py` 校验函数拆出（或明确留存理由）
- [ ] 各子任务 `pytest -q` 无新增失败
- [ ] 父任务终验确认无回归

## 跨子任务验收标准

- 三项独立改进不互相依赖，可并行推进
- 每项改进变更量小，风险低
