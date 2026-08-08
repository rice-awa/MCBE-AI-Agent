# 记录 ExecutionResult 到已验证模式指南

## Goal

在 `.trellis/spec/guides/code-reuse-thinking-guide.md` 中补充 `ExecutionResult` 作为已验证的反重复/契约边界模式，与已有"统一收尾链"模式并列。

## 背景

方案四提取了 `ExecutionResult` dataclass（`services/agent/worker.py`）作为 `_execute_single_request` 与 `_process_request_locked` 之间的返回值契约。7 种终态（success/approval_pending/partial/timeout/cancelled/exception/disconnected）让 `AgentWorker` 基于结果而非内部实现分发收尾。测试通过 `ExecutionResult` 验证行为，而非了解内部状态。`runtime-architecture.md` 已记录 `ExecutionResult` 完整契约，但 `code-reuse-thinking-guide.md` 作为复用模式手册尚未收录。

## Acceptance Criteria

- [ ] `code-reuse-thinking-guide.md` 增加"已验证契约边界：ExecutionResult"或类似条目
- [ ] 说明模式位置（`services/agent/worker.py`）
- [ ] 说明收益：AgentWorker 通过结果而非内部实现收尾，测试面向稳定接口
- [ ] 给出正确/错误示例（类似统一收尾链条目）
- [ ] `pytest -q` 无新增失败