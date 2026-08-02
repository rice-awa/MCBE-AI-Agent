# Final Review Findings 修复报告

## 范围

本次只处理 `.superpowers/sdd/final-review-findings.md` 中的 2 个 Critical 和 2 个 Important；未修改原始计划文件，也未运行全量 Python 或 Add-on 测试。

## Finding → 修复映射

### Critical 1：Approval resume loses deferred tool calls

- `AgentWorker` 从当前 `DeferredToolResults.approvals` / `.calls` 的 key 集合提取明确引用的 `tool_call_id`。
- 审批恢复清洗只向 `ensure_tool_message_pairs()` 传入这组 ID，最多保留每个明确 ID 的一个无响应 pending `ToolCallPart`；其他孤儿仍按普通规则删除。
- 不改变普通聊天历史的孤儿清洗语义，也不把所有孤儿泛化为审批 pending。
- 回归测试：`test_approval_resume_keeps_only_deferred_pending_tool_call`。

### Critical 2：Tool pairing is set-based instead of ordered one-to-one

- `ensure_tool_message_pairs()` 改为按 message/part 顺序维护每个 ID 的未配对 call 队列。
- 响应只能匹配此前唯一未消费的 call；response-before-call、重复 response、重复 call 均按一对一语义清理。
- 保留非工具 part、跨消息合法 pair 和 ModelRequest/ModelResponse 元数据；使用新消息对象，不原地修改输入历史。
- 回归测试：`test_context_pairs_tool_parts_in_order_one_to_one_without_mutating_mixed_messages`，并保留既有 validation retry、metadata、裁剪和自愈覆盖。

### Important 3：Approval-required early return skips validation audit/Trace

- `approval_required` 分支在落审批队列前调用既有 `_record_validation_failures_from_messages()`，与 completed/error 路径共用 extractor、`validation_failures_seen` 去重集合和 audit/Trace 投影。
- validation failure 仍只标记 `INVALID_ARGUMENT` / `execution_stage=validation`，不产生 execution-started 或外部状态未知。
- 回归测试：`test_approval_required_records_validation_failure_once_before_suspension`，验证 audit 一条、Trace 一条、审批仍进入 pending store。

### Important 4：Trace content gate may persist raw sensitive tool input

- validation details 改为 bounded 摘要：只保留受限 type/location；非结构化 input 使用 `[REDACTED]`，结构化 input 走工具目录 parameter preview；不保存自由文本 `msg`。
- `TraceRecorder` 对 model message 中的 tool-call args、工具级 retry content 和 tool-call payload 统一执行目录感知 preview 或通用 bounded 脱敏，`include_content=True` 也不写入 raw secret/full sensitive args。
- 回归测试收紧：`test_validation_failure_trace_is_gated_and_does_not_start_execution`，验证 validation payload、model tool-call args 和 journal 均不含 secret-like 参数。

## 测试

命令：

```bash
.venv/bin/python -m pytest -q tests/test_agent_context.py tests/test_agent_worker.py tests/test_queue_context.py tests/test_runtime_harness_audit.py tests/test_trace_integration.py
```

准确结果：`118 passed, 1 warning in 4.75s`。

该集合运行时 pytest 报告了 1 个已有的 `AsyncMock` `RuntimeWarning`；失败数为 0。本次新增的审批 validation 测试和相关单测单独运行无该警告，未在责任范围外调整既有测试 mock。

## Commit

- message: `fix(agent): address final reliability review findings`
- 本报告与生产/回归测试修复一并包含在上述单次 commit 中；最终 commit hash 在交付结果中返回。

## Concerns

- findings 中记录的 6 项真实 MCBE development-world smoke checks 仍未运行：当前没有可用开发世界或 bridge；不据此声称端到端通过。
- 未运行全量 Python、Add-on 测试，符合本次请求的 focused-test 限制。

---

## Re-review finding 修复（2026-08-03）

### Critical 5：Duplicate deferred IDs can bind approval to stale arguments

- 真实恢复回归通过 `AgentWorker._process_request_locked()` 输入实际 `DeferredToolResults`，并在 worker 的流边界内运行一个真实 PydanticAI `Agent`。修复前测试实际执行了旧参数 `say stale`，结果为 `1 failed, 81 passed, 1 warning in 3.76s`，证实 finding 可复现。
- `ensure_tool_message_pairs()` 的审批例外现在只保留原历史中最近 `ModelResponse` 内、对 deferred ID 唯一的未配对 `ToolCallPart`；更早的同 ID pending calls 全部删除。因此 PydanticAI 的最终 response 恢复与获批参数一致。
- 若最近 `ModelResponse` 内同一 deferred ID 有多个未配对 call，则全部删除，同时保留该空 response 作为恢复边界。PydanticAI 会因最终 response 无未处理 tool call 而拒绝恢复，不会回退并执行旧参数。
- 普通 ordered one-to-one 匹配算法、混合文本/metadata 保留及非原地修改语义未改变；生产代码无需修改 `worker.py`。
- 回归测试：`test_approval_resume_uses_final_duplicate_id_or_fails_closed_if_ambiguous`，覆盖“跨 response 选择最终参数”和“最终 response 内歧义时不执行且返回 error chunk”。

### 本轮测试

命令：

```bash
.venv/bin/python -m pytest -q tests/test_agent_context.py tests/test_agent_worker.py tests/test_queue_context.py
```

准确结果：`82 passed, 1 warning in 4.89s`。

warning 来自既有 `test_request_done_called_once_on_process_exception` 的 `AsyncMock` `RuntimeWarning`；失败数为 0，未在本轮责任范围外调整该 mock。

### 本轮 Commit

- message: `fix(agent): disambiguate deferred approval tool calls`
- 本轮报告、生产修复和回归测试包含在同一 commit 中；最终 commit hash 在交付结果中返回。

### 本轮 Concerns

- findings 中记录的 6 项真实 MCBE development-world smoke checks 仍因没有可用开发世界或 bridge 而未运行。
- 按要求仅运行上述三个 focused 测试文件，未运行全量 Python 或 Add-on 测试。
