# 复用与单一契约

## 先搜索再新增

在根目录优先使用 `rg` 搜索已有实现、常量、错误 code 和测试：

```bash
rg -n "player_name|BrokerResponseBridge|chunkPayload|ToolResult|mcbews:" core models services tests MCBE-AI-Agent-addon
```

先回答：职责是否已经属于 `MessageBroker`、`HostSessionStore`、`BrokerResponseBridge`、SDK delivery、`models/messages.py` 或 Addon 的 bridge helper？若是，扩展现有 API 并沿用相邻测试；不要复制出第二份会话键、分片器或响应格式。

## 本项目的高风险重复

- **玩家身份提取**：不要在多个命令中各自从事件猜玩家；入口提取 `sender` 后通过 `player_name` 显式传递。
- **长文本分片**：Python 侧复用 `BrokerResponseBridge` / `McbeOutboundDelivery` / `McbewsV1Delivery`；Addon bridge 回传复用 [`scripts/bridge/chunking.ts`](../../../MCBE-AI-Agent-addon/scripts/bridge/chunking.ts)。
- **消息契约**：`ChatRequest`、`StreamChunk`、`CommandResponse` 和 `ErrorMessage` 已集中在 [`models/messages.py`](../../../models/messages.py)；Addon 的未知 JSON 解析应在 router/response boundary 归一化，而不是让每个 panel 自己 cast。
- **block 操作**：位置解析、快照、保护、repair 和 precondition 已拆在 `services/agent/block_ops/` 与 Addon `scripts/bridge/capabilities/blocks/`；新增能力先复用这些 helper。
- **脱敏**：日志、Trace、工具审计分别使用 `config/redaction.py`、Trace 白名单和 `preview_parameters`，不要手写一套“看起来差不多”的截断。

## 何时抽象

当同一规则被两个以上跨层消费者读取，或重复逻辑会改变安全/并发/协议语义时，应在拥有数据的边界创建共享类型、decoder、normalizer 或 projection。只使用一次的简单转换不必为了形式抽象成公共模块；抽象必须让调用方更难绕过真实契约。

### 已验证的反重复模式：统一收尾链

当两条路径在"收尾"阶段（结果分类→幂等写入→审计→追踪）有相同实现时，提取到共享方法：

- 主路径（`call_tool`）和审批恢复路径（`_resume_approved_block_plan`）在 `execution.py` 中共享 `_finish_tool_execution` 方法，消除约 35 行重复代码。
- **注意**：幂等命中（idempotent hit）分支通常在收尾链之前提前返回，不在共享方法中。两条路径的幂等命中分支仍各自保留 inline audit+trace。如果在重构中删除了某条路径的幂等命中 audit+trace，这是回归，必须恢复。

错误示例——重构后丢失审计/追踪：
```python
# _resume_approved_block_plan 中错误地删除了 audit+trace
if cached is not None:
    logger.info("tool_idempotent_hit", tool=name, ...)
    return materialize_tool_result(cached.result)  # ❌ 丢失了 audit + trace
```

正确示例——保留审计/追踪：
```python
if cached is not None:
    logger.info("tool_idempotent_hit", tool=name, ...)
    self._audit(...)
    self._trace_tool_result(..., attributes={"idempotent_hit": True})
    return materialize_tool_result(cached.result)
```

### 已验证的反重复模式：已验证契约边界 — ExecutionResult

当两条路径在"执行"与"收尾"之间需要稳定的返回值契约时，提取为共享 dataclass，让调用方基于结果而非内部实现来分发后续动作：

- `_execute_single_request` 与 `_process_request_locked` 之间通过 `ExecutionResult`（`services/agent/worker.py`）交换单次 Agent 执行的终态。7 种 status（`success` / `approval_pending` / `partial` / `timeout` / `cancelled` / `exception` / `disconnected`）让 `AgentWorker` 根据结果而非内部实现分支收尾（终态追踪、标题生成、审批挂起标记）。
- 收益：`_execute_single_request` 成为单一观察入口，内部不直接调用 trace 或审计；所有横切收尾收敛到 `_process_request_locked`。测试面向 `ExecutionResult` 稳定接口验证行为，不依赖内部状态。
- 参考：`.trellis/spec/backend/runtime-architecture.md`（`ExecutionResult 契约` 条目）、`services/agent/worker.py`（`ExecutionResult` dataclass 定义、`_execute_single_request` 返回、`_process_request_locked` 收尾分发）。

错误示例——绕过契约直接依赖内部实现：
```python
# 在收尾处直接检查内部变量而非使用 ExecutionResult
if self._last_response_text:  # ❌ 依赖内部状态
    self._emit_final_trace(...)
```

正确示例——通过 ExecutionResult 分发：
```python
# AgentWorker 只查看 ExecutionResult，不读取执行模块内部状态
result = await self._execute_single_request(...)
if result.status == "success":
    # 终态成功追踪（不关心执行内部细节）
    self._record_model_pairs_from_messages(resolved_context, result.new_messages, ...)
elif result.status == "approval_pending":
    self._emit_lifecycle("trace.suspended", resolved_context, status="suspended", ...)
```

## 提交前复查

- 搜索是否存在同名/同义实现和旧协议字符串。
- 搜索新增字段的所有消费者，确认没有遗漏 `player_name`、`conversation_id`、`trace_id` 或错误 code。
- 对相似批量修改再次运行 `rg`，并补上统一 helper 的测试，而不是只确认编译通过。
