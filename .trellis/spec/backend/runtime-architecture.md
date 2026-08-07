# 运行时架构与异步边界

## 主链路

当前服务端的主链路是：

```text
Minecraft /wsserver
  → mcbe-ws-sdk McbeServerFacade
  → services/gateway/hook.py
  → MessageBroker（入站 PriorityQueue）
  → AgentWorker
  → PydanticAI Agent / tools / AddonBridgeService
  → MessageBroker（按 connection_id 的响应队列）
  → BrokerResponseBridge
  → McbeOutboundDelivery / McbewsV1Delivery
  → Minecraft tellraw、scriptevent 或 mcbews:text_resp
```

`services/gateway/server.py` 是组装点：创建 SDK settings、Addon bridge、命令注册表、会话存储、响应桥、Hook 和 Sink，然后启动 Facade 生命周期。新增组件应接入已有组装点，不要在业务模块中另起一套 WebSocket 服务。

## 入站与 Worker

- `HostConnectionHook` 负责连接生命周期、事件识别和命令路由。钩子内不得等待 LLM 请求或 Addon bridge RTT；耗时工作应入 `MessageBroker`，或用 `asyncio.create_task` 启动受控后台任务。
- `MessageBroker.submit_request()` 把请求包装为带优先级和序号的 `QueueItem`；`AgentWorker` 负责消费、按玩家获取锁、调用 Agent 并将响应送回连接队列。
- 队列是 WebSocket 与 Agent 的解耦边界。不要从 Hook 直接调用 Agent，也不要让 Agent 直接持有 WebSocket 连接对象。
- Worker 和 Gateway 都有幂等的 start/stop 逻辑；停止时要取消任务、等待取消完成，并清理 session、pending command、bridge loop、Addon client 和 Broker connection。
- 单次 Agent 执行（`_execute_single_request`）已从 `AgentWorker` 生命周期中独立。`AgentWorker` 只负责队列消费、按玩家取锁、提交执行上下文，并基于 `ExecutionResult` 决定收尾动作（终态追踪、标题生成）。执行核心（模型获取、流式处理、工具调用、审批挂起、异常/取消/超时处理、历史提交）收敛到 `_execute_single_request` 单一观察入口。

### ExecutionResult 契约

`ExecutionResult`（`services/agent/worker.py`）是 `_execute_single_request` 与 `_process_request_locked` 之间的返回值契约：

```python
@dataclass
class ExecutionResult:
    status: Literal[
        "success",           # 正常完成，含空内容（无输出）
        "approval_pending",  # 工具审批挂起，含 pending_tool_call_id
        "partial",           # 异常中断但部分内容已保存
        "timeout",           # 流结束无 is_complete 且无内容
        "cancelled",         # asyncio.CancelledError
        "exception",         # 不可恢复异常，含 error_type（如 "mcp_timeout"）
        "disconnected",      # 连接断开（预留，尚无代码路径产生）
    ]
    content: str = ""
    pending_tool_call_id: str | None = None
    tool_call_id: str | None = None
    error_type: str | None = None
```

`_process_request_locked` 根据 `result.status` 分发收尾：
- `success` → 终态成功追踪
- `approval_pending` → `trace.suspended`（挂起追踪）
- `partial` → 已保存部分结果，`trace.failed`
- `timeout` / `cancelled` / `exception` / `disconnected` → `trace.failed` 或 `trace.cancelled`

`_execute_single_request` 内部不直接调用 `trace` 或审计；所有横切收尾由 `_process_request_locked` 在收到 `ExecutionResult` 后统一执行。

参考实现：[`core/queue.py`](../../../core/queue.py)、[`services/agent/worker.py`](../../../services/agent/worker.py)（`ExecutionResult`、`_execute_single_request`、`_process_request_locked`）、[`services/gateway/hook.py`](../../../services/gateway/hook.py)、[`services/gateway/server.py`](../../../services/gateway/server.py)。

## SDK 适配边界

`mcbe-ws-sdk` 拥有 WebSocket 生命周期、mcbews v1 线协议、下行分片和 delivery。宿主仓库只在 `services/gateway/` 实现业务适配：命令处理、会话映射、Broker 响应转换和配置映射。

- 长文本发送统一经过 `BrokerResponseBridge` 或 SDK 的 `McbeOutboundDelivery` / `McbewsV1Delivery`。
- 不在调用点重新计算 `commandLine` 字节预算，不复制 tellraw、scriptevent 或 `text_resp` 分片逻辑。
- 不直接修改安装的 SDK 源码；若 SDK 契约确实不足，应形成独立 SDK 变更并在本仓库更新依赖/适配测试。

## Harness、审计和 Trace

运行时 Harness 的工具提示、审批/拒绝、工具审计和 Trace 属于 Agent 运行路径的横切能力：工具定义在 `services/agent/tools.py`，执行与审批在 `services/agent/harness/`，事件记录在 `services/agent/trace.py`。它们不能改变工具的成功/失败语义来"顺便记录日志"；写入失败应降级为计数或告警而不阻塞主路径。
