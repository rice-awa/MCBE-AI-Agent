# Agent Trace Audit Platform Design

## Goal

为 MCBE Chat Agent 建立本地优先的运行追踪审计平台，按 trace 还原一次玩家请求的完整 LLM 响应链路。首期先落地 Python 运行时采集、只读查询 API、CLI 查询和一个轻量浏览器前端。

## Scope

记录边界：

- 用户完整入站消息；
- 每次实际发送给 Provider 的完整 LLM messages；
- 每次 Provider 返回的完整 assistant message、tool-call parts、finish reason 和 usage；
- 工具原始参数、策略/审批结果、完整执行结果或错误；
- 最终用户可见响应。

明确不记录：WebSocket 原始帧、WebSocket 分片、逐 token delta、API headers/credentials。delivery 仅记录目标、状态、耗时、分片数量和字节数。

## Architecture

业务入口创建 `TraceContext`，通过请求/响应队列显式传递。`trace_id` 表示一次玩家意图，审批恢复或重试使用新 `attempt_id`，并继续复用原 `trace_id`。模型、工具、审批和交付使用 `span_id/parent_span_id` 建立因果树。

`TraceRecorder` 写入独立的 append-only JSONL journal（默认 `logs/agent_traces.jsonl`），采用异步队列和轮转；写盘失败只产生 gap/health 计数，不阻塞 Agent 主流程。现有 `runtime_harness_tools.jsonl` 保持原有摘要语义，不迁移为完整正文。

## Configuration

新增独立配置：

- `agent_trace_enabled`：是否创建 trace 事件，默认 `false`；
- `agent_trace_include_content`：是否持久化完整正文，默认 `false`；仅在 trace 开启时生效；
- `agent_trace_path`：journal 路径；
- `agent_trace_max_records`：轮转上限。

配置状态在 `trace.started` 留证，仅影响新 trace。`enable_ws_raw_log` 与 `enable_llm_raw_log` 不被复用，也不会因开启完整审计而自动开启。

## Event Contract

每条事件包含 `schema_version/event_id/event_name/timestamp/sequence/trace_id/run_id/attempt_id/span_id/parent_span_id/tool_call_id/status/duration_ms/attributes`。完整内容使用有类型的 payload 字段，按事件类型白名单序列化。

最小事件集合：`trace.started`、`request.accepted`、`queue.*`、`agent.attempt.*`、`model.request.*`、`tool.proposed`、`policy.decided`、`tool.execution.*`、`approval.*`、`delivery.*`、`trace.completed/failed/cancelled`。

## Query Surface

标准库 HTTP 服务提供：

- `GET /api/health`；
- `GET /api/config`；
- `GET /api/traces?status=&player=&limit=`；
- `GET /api/traces/{trace_id}`；
- `/` 及 `/assets/*` 提供静态前端。

CLI 提供等价的 `python cli.py trace list/show/health`。

## Frontend

前端是深色运维工作台：左侧 trace 列表和筛选，中间事件时间线，右侧显示完整消息、工具参数/结果、usage、审批和错误。顶部显示审计开关状态，页面不展示 WebSocket raw 视图。

## Acceptance

覆盖无工具、单工具、审批恢复、失败终态、同连接双玩家交错和审计开关开/关。关闭时不落正文；开启时用户消息、LLM messages、工具调用/结果、最终响应可逐字还原；journal 写失败不改变 Agent 业务结果。
