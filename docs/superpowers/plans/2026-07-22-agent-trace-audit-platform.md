# Agent Trace Audit Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 MCBE Chat Agent 中建立一个默认关闭、可显式开启完整内容的本地运行追踪审计平台，并提供只读 HTTP API、CLI 查询和轻量浏览器界面。

**Architecture:** 从玩家消息入口创建稳定的 `TraceContext`，沿请求队列、Agent Worker、PydanticAI 模型/工具循环和响应交付边界显式传递。`TraceRecorder` 将版本化事件异步追加到独立 JSONL journal；查询层聚合 journal 为 trace 列表和详情，标准库 HTTP server 同时提供 API 与静态前端。完整用户消息、LLM messages、工具参数/结果和最终响应只在新 trace 内容开关开启时进入 payload，WebSocket raw 始终排除。

**Tech Stack:** Python 3.11、Pydantic v2、Click、`asyncio`、标准库 `ThreadingHTTPServer`、原生 HTML/CSS/JavaScript；不新增 FastAPI、React 或前端构建依赖。

## Global Constraints

- `trace_id` 表示一次玩家意图；审批恢复/重试保持 `trace_id`，生成新的 `attempt_id`。
- `run_id` 在迁移期与 `trace_id` 相同，保留现有日志与幂等兼容。
- 每条事件包含 `schema_version/event_id/event_name/timestamp/sequence/trace_id/run_id/attempt_id/span_id/parent_span_id/tool_call_id/status/duration_ms/attributes`。
- 完整正文使用有类型的 payload 白名单序列化，不把任意 kwargs 直接写入 journal。
- 不记录 WebSocket 原始帧、分片、逐 token delta、headers、credentials 或 API key。
- `runtime_harness_tools.jsonl` 继续保留结构化工具摘要语义，不迁移为完整内容日志。
- `TraceRecorder` 写盘故障不得阻塞或改变 Agent、工具和交付主路径；必须暴露 dropped/write_failed/gap 健康计数。
- 新增配置默认 `agent_trace_enabled=false`、`agent_trace_include_content=false`，配置只影响新 trace。
- 修改现有玩家路径时继续显式使用当前事件的 `player_name/sender`，不得从连接级状态推断玩家。
- 所有新增测试默认离线运行，不访问真实 Provider、真实 Minecraft 世界或 WebSocket。

## File Map

| 文件 | 责任 |
| --- | --- |
| `services/agent/trace.py` | TraceContext、TraceEvent、内容序列化、异步 JSONL writer、Recorder |
| `services/agent/trace_query.py` | journal 容错读取、trace 聚合、列表/详情/健康数据 |
| `services/agent/trace_api.py` | 标准库只读 API 与静态文件 server |
| `models/messages.py` | ChatRequest/StreamChunk 的 trace correlation 字段 |
| `models/agent.py` | AgentDependencies/StreamEvent 的 trace 字段和 metadata 约定 |
| `core/queue.py` | QueueItem 的 TraceContext 和入队时间 |
| `config/settings.py` / `config.example.json` | trace 开关、journal、轮转和 API 配置 |
| `services/gateway/command_handlers.py` | 入站 trace 创建、accepted/rejected、审批恢复 attempt |
| `services/agent/core.py` | 暴露每轮 new messages、模型请求/响应可序列化数据 |
| `services/agent/worker.py` | attempt 生命周期、tool/model/final payload、终态事件 |
| `services/gateway/broker_bridge.py` | delivery 状态事件和 trace correlation |
| `cli.py` | `trace list/show/health/serve` 命令组 |
| `web/trace/index.html` | 审计工作台结构和语义标记 |
| `web/trace/app.js` | API 请求、筛选、时间线、消息/工具详情渲染 |
| `web/trace/styles.css` | 深色运维界面 token、三栏布局、响应式断点 |
| `tests/test_agent_trace.py` | 事件契约、开关、writer、序列化测试 |
| `tests/test_trace_query.py` | journal 聚合、乱序/截断/未知事件容错 |
| `tests/test_trace_api.py` | HTTP endpoint 和静态文件测试 |
| `tests/test_cli_trace.py` | Click trace 命令输出测试 |
| `tests/test_agent_worker.py` | 端到端无工具/工具/失败/审批 trace fixture |
| `README.md` | 启动 API、开关语义、数据边界和浏览器访问说明 |

---

### Task 1: 建立 Trace 数据契约和异步 Journal

**Files:**
- Create: `services/agent/trace.py`
- Modify: `config/settings.py`
- Modify: `config.example.json`
- Test: `tests/test_agent_trace.py`

**Interfaces:**
- Produces `TraceContext`, `TraceEvent`, `TraceRecorder`, `serialize_trace_payload`, `get_trace_recorder`.
- `TraceRecorder.emit(event_name: str, context: TraceContext, *, status: str = "info", span_id: str | None = None, parent_span_id: str | None = None, tool_call_id: str | None = None, duration_ms: int | None = None, attributes: dict[str, Any] | None = None, payload: dict[str, Any] | None = None) -> None` 必须是非阻塞调用。
- `TraceRecorder.start() -> Awaitable[None]`、`stop() -> Awaitable[None]` 管理 writer task；关闭时 flush 队列并记录 health counters。

- [ ] **Step 1: Write failing contract tests**

```python
def test_trace_event_has_stable_fields_and_rejects_unknown_status():
    event = TraceEvent(
        event_name="trace.started",
        trace_id="trace-1",
        run_id="trace-1",
        attempt_id="attempt-1",
        status="started",
        attributes={"include_content": True},
    )
    assert event.schema_version == 1
    assert event.event_id
    assert event.sequence == 0

    with pytest.raises(ValidationError):
        TraceEvent(
            event_name="trace.completed",
            trace_id="trace-1",
            run_id="trace-1",
            attempt_id="attempt-1",
            status="maybe",
        )

@pytest.mark.asyncio
async def test_disabled_recorder_does_not_write_content(tmp_path):
    recorder = TraceRecorder(path=tmp_path / "trace.jsonl", enabled=False, include_content=True)
    await recorder.start()
    recorder.emit("trace.started", context, payload={"content": "secret"})
    await recorder.stop()
    assert not (tmp_path / "trace.jsonl").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agent_trace.py -q`

Expected: FAIL because the trace module and settings fields do not exist.

- [ ] **Step 3: Write the minimal typed contract and writer**

Implement `TraceContext` as a frozen dataclass with `trace_id/run_id/attempt_id/message_id/connection_id/player_name/conversation_id`. Implement `TraceEvent` with `Literal` event statuses, UTC timestamp, monotonic per-trace sequence, and `model_dump(mode="json")`. Gate payload persistence with `include_content`; metadata attributes remain available when only `agent_trace_enabled` is true. Use an `asyncio.Queue`, `put_nowait`, one-line JSON serialization, and counters for `enqueued/written/dropped/write_failed/gap`.

- [ ] **Step 4: Add settings and config template**

Add fields to the existing settings model and `config.example.json`:

```json
"agent_trace_enabled": false,
"agent_trace_include_content": false,
"agent_trace_path": "logs/agent_traces.jsonl",
"agent_trace_max_records": 10000,
"agent_trace_api_host": "127.0.0.1",
"agent_trace_api_port": 8787
```

Validate `agent_trace_max_records >= 1`, port `1..65535`, and make `include_content` ineffective when `enabled` is false.

- [ ] **Step 5: Run focused tests and commit**

Run: `pytest tests/test_agent_trace.py -q`

Expected: all contract, disabled-mode, enabled-mode, queue overflow, rotation, and shutdown flush tests pass.

Commit: `git add services/agent/trace.py config/settings.py config.example.json tests/test_agent_trace.py && git commit -m "feat(trace): add versioned async audit journal"`

### Task 2: Thread Trace Context Through Ingress and Queues

**Files:**
- Modify: `models/messages.py`
- Modify: `models/agent.py`
- Modify: `core/queue.py`
- Modify: `services/gateway/command_handlers.py`
- Test: `tests/test_queue_context.py`
- Test: `tests/test_gateway_hook_auth_chat.py`

**Interfaces:**
- `ChatRequest.trace_id: str | None` and `ChatRequest.attempt_id: str | None` are optional for backward compatibility; normal ingress sets both.
- `StreamChunk.trace_id/attempt_id` are optional and carry correlation to the delivery bridge without exposing raw WebSocket data.
- `QueueItem.trace_context: TraceContext | None` and `QueueItem.enqueued_at_ns: int` are populated by `MessageBroker.submit_request`.

- [ ] **Step 1: Add failing correlation tests**

```python
async def test_chat_ingress_assigns_trace_id_before_enqueue(broker, handlers, state):
    await handlers.handle_chat(state, "hello", "tellraw", player_name="alex")
    item = await broker.get_request()
    request = item.payload
    assert request.trace_id == request.run_id
    assert request.attempt_id
    assert item.trace_context.trace_id == request.trace_id
    assert item.trace_context.player_name == "alex"

async def test_approval_resume_keeps_trace_and_uses_new_attempt(pending_resume):
    resumed = pending_resume()
    assert resumed.trace_id == "trace-original"
    assert resumed.attempt_id != "attempt-original"
```

- [ ] **Step 2: Run the focused tests to verify failure**

Run: `pytest tests/test_queue_context.py tests/test_gateway_hook_auth_chat.py -q`

Expected: FAIL because request and queue items lack correlation fields.

- [ ] **Step 3: Implement explicit propagation**

Generate `trace_id` and first `attempt_id` in `_build_chat_request`; set `run_id=trace_id`. Build `TraceContext` in `QueueItem` using the request's IDs and current `player_name`, `conversation_id`, and `connection_id`. Preserve the original trace when the approval handler rebuilds `ChatRequest`, but generate a fresh attempt ID. Do not use a connection-level player name as a substitute.

- [ ] **Step 4: Emit ingress events and pass trace context to responses**

Call `TraceRecorder.emit("trace.started", ...)` and `request.accepted` after a request is accepted. On queue full or auth rejection emit `request.rejected` with metadata only. Add trace IDs to `StreamChunk` construction paths without including message content in the correlation object.

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/test_queue_context.py tests/test_gateway_hook_auth_chat.py -q`

Expected: existing queue/session tests plus new correlation assertions pass.

Commit: `git add models/messages.py models/agent.py core/queue.py services/gateway/command_handlers.py tests/test_queue_context.py tests/test_gateway_hook_auth_chat.py && git commit -m "feat(trace): propagate request context through queue"`

### Task 3: Capture Agent, Model, Tool, Approval, and Final Response Events

**Files:**
- Modify: `services/agent/core.py`
- Modify: `services/agent/worker.py`
- Modify: `services/agent/harness/execution.py`
- Modify: `services/agent/harness/approvals.py`
- Modify: `services/gateway/broker_bridge.py`
- Test: `tests/test_agent_worker.py`
- Test: `tests/test_runtime_harness_execution.py`
- Test: `tests/test_tool_approval_command.py`

**Interfaces:**
- `AgentDependencies.trace_context: TraceContext` and `AgentDependencies.trace_recorder: TraceRecorder` are present for every accepted request.
- `StreamEvent.metadata["new_messages"]` contains the current attempt's complete PydanticAI message list at the terminal event.
- `TraceRecorder` receives event payloads only through typed helper methods: `record_model_messages`, `record_tool_call`, `record_tool_result`, `record_final_response`.

- [ ] **Step 1: Write failing chain tests**

```python
@pytest.mark.asyncio
async def test_single_tool_trace_contains_model_tool_model_and_final_response(worker_fixture):
    trace_id = await worker_fixture.run_chat("查一下钻石剑")
    events = worker_fixture.read_trace(trace_id)
    names = [event["event_name"] for event in events]
    assert names.index("model.request.completed") < names.index("tool.proposed")
    assert names.index("tool.execution.completed") < names.index("trace.completed")
    assert sum(name == "model.request.completed" for name in names) == 2
    assert events[-1]["payload"]["content"] == worker_fixture.final_text

@pytest.mark.asyncio
async def test_content_disabled_keeps_metadata_but_omits_messages_and_results(worker_fixture):
    trace_id = await worker_fixture.run_chat("secret prompt", include_content=False)
    raw = worker_fixture.read_trace(trace_id)
    assert all("payload" not in event for event in raw)
    assert any(event["event_name"] == "tool.execution.completed" for event in raw)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_agent_worker.py tests/test_runtime_harness_execution.py tests/test_tool_approval_command.py -q`

Expected: FAIL because no trace events are emitted and terminal StreamEvent lacks `new_messages`.

- [ ] **Step 3: Expose message payloads at the Agent boundary**

Add `new_messages` to the terminal and approval `StreamEvent.metadata` in `services/agent/core.py`. Serialize each `ModelRequest` and `ModelResponse` with `model_dump(mode="json")`, preserving role, part order, tool-call IDs, finish reason, provider/model metadata and usage. Do not serialize streaming deltas or HTTP raw bodies.

- [ ] **Step 4: Instrument Worker lifecycle**

At request start emit `agent.attempt.started` and `queue.dequeued`. At terminal metadata emit one `model.request.completed` event per model request/response pair, `trace.completed`, and a typed final response payload containing the complete user-visible answer only when content mode is enabled. On provider setup errors, tool exceptions, cancellations, and unknown external state emit the matching failed/cancelled terminal status exactly once.

- [ ] **Step 5: Instrument tools and approvals**

Emit `tool.proposed`, `policy.decided`, `approval.requested/decided/expired`, and `tool.execution.started/completed/failed/cancelled` from the existing Harness execution and approval boundaries. Use one normalized execution status (`succeeded`, `failed`, `denied`, `cancelled`, `timeout_unknown`, `not_executed`) and include raw args/results only through content-gated payload helpers. Approval resume keeps `trace_id` and writes `agent.attempt.resumed` with the new attempt.

- [ ] **Step 6: Instrument delivery without WebSocket raw data**

In `broker_bridge.py`, use `StreamChunk.trace_id/attempt_id` to emit `delivery.enqueued/started/completed/failed` with target, delivery type, chunk count, byte count and duration. Never log the chunk body or raw protocol frame in trace events.

- [ ] **Step 7: Run chain tests and commit**

Run: `pytest tests/test_agent_worker.py tests/test_runtime_harness_execution.py tests/test_tool_approval_command.py -q`

Expected: no-tool, single-tool, approval resume, denial, error, and cancellation fixtures have one trace terminal event and correct payload gating.

Commit: `git add services/agent/core.py services/agent/worker.py services/agent/harness/execution.py services/agent/harness/approvals.py services/gateway/broker_bridge.py tests/test_agent_worker.py tests/test_runtime_harness_execution.py tests/test_tool_approval_command.py && git commit -m "feat(trace): capture agent and tool execution chain"`

### Task 4: Build Journal Query and Read-Only HTTP API

**Files:**
- Create: `services/agent/trace_query.py`
- Create: `services/agent/trace_api.py`
- Test: `tests/test_trace_query.py`
- Test: `tests/test_trace_api.py`

**Interfaces:**
- `TraceQuery.list_traces(*, status: str | None = None, player: str | None = None, limit: int = 50) -> list[dict[str, Any]]` returns newest-first summaries.
- `TraceQuery.get_trace(trace_id: str) -> dict[str, Any] | None` returns `{summary, events, attempts, models, tools, approvals, delivery}`.
- `TraceQuery.health() -> dict[str, Any]` returns path, file size, parsed lines, malformed lines, last event timestamp, and recorder counters.
- `TraceAPIServer(host: str, port: int, query: TraceQuery, static_dir: Path)` exposes `/api/health`, `/api/config`, `/api/traces`, `/api/traces/<id>`, `/` and `/assets/*`.

- [ ] **Step 1: Write failing query/API tests**

```python
def test_query_groups_out_of_order_events_by_trace(tmp_path):
    write_fixture_events(tmp_path / "agent_traces.jsonl")
    result = TraceQuery(tmp_path / "agent_traces.jsonl").get_trace("trace-1")
    assert result["summary"]["status"] == "completed"
    assert result["events"][0]["sequence"] == 0
    assert result["tools"][0]["tool_name"] == "mcwiki_search"

def test_query_ignores_truncated_last_line_and_reports_gap(tmp_path):
    path = tmp_path / "agent_traces.jsonl"
    path.write_text('{"event_name":"trace.started"}\n{"broken"')
    health = TraceQuery(path).health()
    assert health["malformed_lines"] == 1

def test_api_returns_filtered_trace_list(api_client):
    response = api_client.get("/api/traces?status=failed&limit=10")
    assert response.status == 200
    assert all(item["status"] == "failed" for item in response.json["traces"])
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_trace_query.py tests/test_trace_api.py -q`

Expected: FAIL because the query and server modules do not exist.

- [ ] **Step 3: Implement tolerant JSONL aggregation**

Read line-by-line without loading unrelated files, skip malformed lines while incrementing a counter, sort events by `(sequence, event_id)`, derive summary status from the last terminal event, and expose payloads exactly as recorded. Apply `limit` after filtering. Do not infer or fabricate a completed status for traces without terminal events; report them as `running` or `abandoned` according to journal age.

- [ ] **Step 4: Implement the standard-library server**

Use `ThreadingHTTPServer` and a request handler that only permits `GET`, returns JSON with UTF-8 and `Cache-Control: no-store`, rejects path traversal, and serves `web/trace/index.html` plus assets. Bind the server to the configured loopback host by default. Keep API query work synchronous and read-only.

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/test_trace_query.py tests/test_trace_api.py -q`

Expected: list/detail/health/config endpoints, filter parameters, malformed-line tolerance, static file serving, and traversal rejection pass.

Commit: `git add services/agent/trace_query.py services/agent/trace_api.py tests/test_trace_query.py tests/test_trace_api.py && git commit -m "feat(trace): add journal query and local read api"`

### Task 5: Add CLI Trace Commands and Server Lifecycle

**Files:**
- Modify: `cli.py`
- Modify: `README.md`
- Create: `tests/test_cli_trace.py`

**Interfaces:**
- `python cli.py trace list --recent 20 --status failed --player alex`
- `python cli.py trace show <trace_id> [--json]`
- `python cli.py trace health`
- `python cli.py trace serve --host 127.0.0.1 --port 8787`

- [ ] **Step 1: Write failing Click tests**

```python
def test_trace_list_outputs_summary_rows(cli_runner, trace_fixture):
    result = cli_runner.invoke(cli, ["trace", "list", "--recent", "5"])
    assert result.exit_code == 0
    assert "TRACE" in result.output
    assert "completed" in result.output

def test_trace_show_json_returns_complete_event_tree(cli_runner, trace_fixture):
    result = cli_runner.invoke(cli, ["trace", "show", "trace-1", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["summary"]["trace_id"] == "trace-1"

def test_trace_health_reports_malformed_lines(cli_runner, trace_fixture):
    result = cli_runner.invoke(cli, ["trace", "health"])
    assert result.exit_code == 0
    assert "malformed_lines" in result.output
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_cli_trace.py -q`

Expected: FAIL because the nested Click group is not registered.

- [ ] **Step 3: Register the `trace` group**

Add a Click group with `list`, `show`, `health`, and `serve` commands. Reuse `get_settings()` for the configured journal path and API defaults. Format human output as compact trace summary/timeline rows; `--json` writes only machine-readable JSON to stdout. Return Click exceptions for missing trace IDs and invalid limits.

- [ ] **Step 4: Add server startup documentation**

Document enabling `agent_trace_enabled` and `agent_trace_include_content`, then show:

```bash
python cli.py trace serve
# open http://127.0.0.1:8787
python cli.py trace list --recent 20
python cli.py trace show <trace_id>
```

State explicitly that the API is local/read-only, complete content is opt-in, and WebSocket raw logging remains separate.

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/test_cli_trace.py -q`

Expected: all command output, JSON mode, missing-trace, and health cases pass.

Commit: `git add cli.py README.md tests/test_cli_trace.py && git commit -m "feat(trace): add cli query and local server commands"`

### Task 6: Build the Static Audit Workbench

**Files:**
- Create: `web/trace/index.html`
- Create: `web/trace/app.js`
- Create: `web/trace/styles.css`

**Interfaces:**
- Consumes `GET /api/config`, `/api/traces`, `/api/traces/{trace_id}` without embedding sample data into the UI.
- Renders trace summary objects returned by `TraceQuery` and event payloads only when the server provides them.

- [ ] **Step 1: Define the semantic DOM shell**

Create a three-column workbench with a top status bar, trace search/filter controls, trace list, timeline, and inspector. Use semantic headings and buttons; all icon-only controls have `aria-label` and native `title` tooltips. Include explicit empty/loading/error states.

- [ ] **Step 2: Implement API state and filters**

In `app.js`, use `fetch` with `AbortController` for list/detail requests. Keep selected trace ID in URL hash, preserve filters in query parameters, and refresh the list without reloading the document. Render audit mode as “元数据 / 完整内容” from `/api/config`; do not add a UI toggle that silently changes server configuration.

- [ ] **Step 3: Implement timeline and inspector rendering**

Render event type, relative duration, status, tool name, model/provider and approval transitions in the center timeline. The inspector shows full user/LLM/tool/final content in `<pre>` blocks only when payload is present, with copy buttons and safe text insertion via `textContent`; never use `innerHTML` for journal content.

- [ ] **Step 4: Add responsive styling and accessibility**

Use CSS custom properties, a restrained dark palette with one amber accent for warnings, 8px-or-less radii, CSS grid/flexbox, `text-wrap: pretty`, visible focus states, and `prefers-reduced-motion`. At widths below 1000px collapse the inspector below the timeline; at widths below 720px stack list and timeline while keeping the selected trace readable.

- [ ] **Step 5: Manual static validation**

Run: `python cli.py trace serve --host 127.0.0.1 --port 8787`

Open: `http://127.0.0.1:8787/`

Check: empty journal, metadata-only trace, full-content trace, failed trace, approval trace, narrow viewport, keyboard focus, and malformed-event error state.

### Task 7: Add End-to-End Fixtures, Regression Tests, and Final Verification

**Files:**
- Modify: `tests/test_agent_worker.py`
- Create: `tests/test_trace_integration.py`
- Modify: `README.md`

**Interfaces:**
- Tests use a temporary journal and fake `TraceRecorder`, fake model/provider and fake tool results; no network or WebSocket.
- Integration fixture helper returns `trace_id` and reads the same JSONL shape used by the API.

- [ ] **Step 1: Add the required fixture matrix**

Cover these exact cases:

```text
no-tool: trace.started -> accepted -> model.request.completed -> delivery.completed -> trace.completed
single-tool: model.request.completed -> tool.proposed -> policy.decided -> tool.execution.completed -> model.request.completed
approval: attempt.started -> approval.requested -> trace.suspended -> approval.decided -> attempt.resumed -> attempt=2 terminal
deny: policy.decided(deny) -> tool.execution terminal not_executed -> trace.completed
failure: model/tool failure -> trace.failed
cancel: cancellation -> trace.cancelled
multiplayer: same connection, two player names, separate trace IDs and payloads
privacy: disabled omits payload; enabled preserves user/LLM/tool/final content verbatim
```

- [ ] **Step 2: Run focused integration tests**

Run: `pytest tests/test_trace_integration.py tests/test_agent_worker.py -q`

Expected: every fixture has exactly one trace terminal event, correct attempt grouping, correct player/conversation identity, and content gating.

- [ ] **Step 3: Run the complete offline suite**

Run: `pytest -q`

Expected: all existing and new tests pass with no live-provider marker enabled.

- [ ] **Step 4: Run static checks**

Run: `ruff check services/agent/trace.py services/agent/trace_query.py services/agent/trace_api.py services/agent/worker.py services/agent/core.py services/gateway/command_handlers.py services/gateway/broker_bridge.py tests/test_agent_trace.py tests/test_trace_query.py tests/test_trace_api.py tests/test_cli_trace.py tests/test_trace_integration.py`

Expected: no lint errors. Run `python -m compileall services tests` and expect exit code 0.

- [ ] **Step 5: Verify the browser path**

Start the local server against a fixture journal, request `/api/health`, `/api/traces`, `/api/traces/trace-1`, and `/`. Confirm JSON content type, no path traversal, and that the HTML loads without a build step.

- [ ] **Step 6: Review diff and commit**

Run: `git status --short && git diff --check`

Confirm no `.env`, `config.json`, logs, credentials, generated node modules, or WebSocket raw payloads are staged. Commit: `git add services models core config tests cli.py web README.md && git commit -m "feat: add agent trace audit platform"`.

## Self-Review Checklist

- [x] Spec coverage: configuration, event contract, content gating, queue propagation, model/tool/approval/delivery events, query API, CLI, frontend, and acceptance matrix each have a task.
- [x] Placeholder scan: no unresolved placeholder markers or unspecified error-handling step remains.
- [x] Type consistency: `TraceContext`, `TraceEvent`, `TraceRecorder`, `TraceQuery`, and `TraceAPIServer` signatures are defined before consumers.
- [x] Scope check: implementation stays within the Python runtime repo and adds no SDK or external observability dependency.
- [x] Privacy boundary: complete content is opt-in and WebSocket raw is excluded in every task.
