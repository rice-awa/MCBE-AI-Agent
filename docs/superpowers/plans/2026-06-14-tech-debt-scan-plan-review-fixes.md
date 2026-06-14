# tech-debt-scan-plan 审查问题修复计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 修复 `docs/superpowers/reports/2026-06-14-tech-debt-scan-plan-review.md` 中列出的重构回归风险，并为每个风险点补充回归测试。

**架构：** 保持当前重构方向不变，只收紧生命周期、会话版本、流控字节预算和命令路由边界。所有修复都落在现有模块中，不新增框架、不引入兼容性 shim；每个任务先写失败测试，再做最小实现。

**技术栈：** Python 3.11+、pytest、pytest-asyncio、PydanticAI messages、websockets、现有 `MessageBroker` / `AgentWorker` / `FlowControlMiddleware` / `WebSocketServer` 架构。

---

## 文件结构

- 修改：`services/agent/worker.py`
  - 在玩家锁内重新获取对话 generation。
  - 让 worker 不再绕过 `ChatAgentManager` 的 active/fallback 选择。
  - 标题生成改为通过 broker 原子写入，避免连接清理后重建 metadata。

- 修改：`services/agent/core.py`
  - 为 `ChatAgentManager` 增加 `reset()`。
  - 在 stream tool call/result 事件之间维护 `tool_call_id -> tool_name` 映射。

- 修改：`services/agent/runtime.py`
  - shutdown 时重置 agent manager 和 MCP manager 引用，支持同进程重新 initialize。

- 修改：`core/session.py`
  - list 方法不再从 `_session_locks` 合成 default conversation。

- 修改：`core/queue.py`
  - 增加 `set_conversation_title_if_connected()` 代理方法，并在 broker 层检查连接仍注册。

- 修改：`models/minecraft.py`
  - 暴露复用 tellraw message/target sanitization 的小函数，供流控按最终命令字节预算分片。

- 修改：`services/websocket/flow_control.py`
  - tellraw 分片改为按最终 `MinecraftCommand.create_tellraw(...).body.commandLine` 字节长度验证和切分。

- 修改：`services/websocket/command.py`
  - 命令前缀/alias 匹配要求精确匹配或空白边界。

- 修改：`services/websocket/server.py`
  - `_send_ws_payload()` 保留 `TellrawMessage.target` 的原始语义。
  - `handle_run_command()` 捕获 raw command 长度错误并回传玩家。

- 修改：`tests/test_queue_context.py`
  - 补齐同 generation 连续聊天、清理后不出现 phantom default、标题生成 cleanup race 测试。

- 修改：`tests/test_agent_provider_lifecycle.py`
  - 补齐 MCP fallback 选择和 runtime shutdown/reinitialize 测试。

- 修改：`tests/test_websocket_delivery.py`
  - 补齐 tellraw 转义/目标名字节预算测试。

- 修改：`tests/test_websocket_command.py`
  - 补齐命令边界负例测试。

- 修改：`tests/test_websocket_conversation_commands.py`
  - 更新 raw command 过长行为测试：从抛异常改为发送错误消息。
  - 补齐 `@a` 广播目标不被 state.player_name 改写的测试。

- 修改：`tests/test_stream_mode.py`
  - 补齐 tool result metadata 使用真实 tool name 的测试。

- 修改或删除：`PROMPT.md`
  - 推荐删除根目录一次性执行提示；若团队希望保留，移动到历史任务目录并标注 superseded。

- 修改：`docs/superpowers/reports/2026-06-13-architecture-tech-debt-scan.md`
  - 标注该报告是重构前历史输入，不代表当前分支最终状态。

---

### 任务 1：修复同玩家连续聊天 generation 竞态

**文件：**
- 修改：`tests/test_queue_context.py:240-346`
- 修改：`services/agent/worker.py:147-171`, `services/agent/worker.py:331-337`

- [ ] **步骤 1：编写失败的测试**

把 `tests/test_queue_context.py` 中 `test_worker_keeps_serial_chat_history_when_requests_share_initial_generation` 的两个请求都改成同一个入队 generation，模拟真实快速连续入队：

```python
first = ChatRequest(
    connection_id=connection_id,
    content="first",
    player_name="alice",
    conversation_generation=0,
)
second = ChatRequest(
    connection_id=connection_id,
    content="second",
    player_name="alice",
    conversation_generation=0,
)
```

保留断言：

```python
history = broker.get_conversation_history(connection_id, "alice", "default")
assert len(history) == 4
assert history[0].parts[0].content == "user-1"
assert history[2].parts[0].content == "user-2"
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
python3 -m pytest tests/test_queue_context.py::test_worker_keeps_serial_chat_history_when_requests_share_initial_generation -v
```

预期：当前代码 FAIL，`len(history)` 仍为 2，第二次历史写入被 stale generation 拒绝。若环境缺少 pytest，先记录环境阻塞，不跳过实现。

- [ ] **步骤 3：编写最少实现代码**

在 `services/agent/worker.py` 的 `_process_request_locked()` 中，把 generation 快照移到玩家锁内、读取历史之前：

```python
async def _process_request_locked(
    self,
    request: ChatRequest,
    connection_id: UUID,
    conversation_generation: int | None = None,
) -> None:
    """处理单个请求（已持有会话锁）"""
    if conversation_generation is None:
        conversation_generation = self.broker.get_conversation_generation(
            connection_id,
            request.player_name,
            request.conversation_id,
        )
```

保留后续写回：

```python
history_updated = self.broker.set_conversation_history(
    connection_id,
    request.player_name,
    trimmed_history,
    request.conversation_id,
    expected_generation=conversation_generation,
)
```

不要改 `handle_chat()` 的 `chat_req.conversation_generation` 字段赋值；它仍可作为调试信息，但 worker 默认不应把入队时快照当作锁内写回前提。

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
python3 -m pytest tests/test_queue_context.py::test_worker_keeps_serial_chat_history_when_requests_share_initial_generation tests/test_queue_context.py::test_worker_skips_history_write_when_request_generation_is_stale -v
```

预期：两个测试 PASS。第二个测试显式调用 `_process_request_locked(request, connection_id)` 后应按新语义调整，见步骤 5。

- [ ] **步骤 5：修正 stale 管理命令测试**

`test_worker_skips_history_write_when_request_generation_is_stale` 现在需要显式传入旧 generation，才能验证低层方法仍能拒绝 stale 写入：

```python
await worker._process_request_locked(
    request,
    connection_id,
    conversation_generation=request.conversation_generation,
)
```

保留断言：

```python
assert broker.get_conversation_history(connection_id, "alice", "default") == []
```

- [ ] **步骤 6：运行队列上下文测试**

运行：

```bash
python3 -m pytest tests/test_queue_context.py -v
```

预期：PASS。

- [ ] **步骤 7：Commit**

```bash
git add services/agent/worker.py tests/test_queue_context.py
git commit -m "fix: refresh chat history generation under player lock"
```

---

### 任务 2：修复 MCP fallback 被 worker 显式 primary agent 绕过

**文件：**
- 修改：`tests/test_agent_provider_lifecycle.py`
- 修改：`services/agent/worker.py:247-264`
- 可选修改：`services/agent/core.py:201-217`

- [ ] **步骤 1：编写失败的 worker 测试**

在 `tests/test_agent_provider_lifecycle.py` 末尾添加：

```python
def test_worker_stream_chat_does_not_pass_primary_agent(monkeypatch):
    import asyncio
    from uuid import uuid4

    from config.settings import Settings
    from core.queue import MessageBroker
    from models.agent import StreamEvent
    from models.messages import ChatRequest
    from services.agent.worker import AgentWorker

    async def _run() -> None:
        broker = MessageBroker()
        connection_id = uuid4()
        broker.register_connection(connection_id)
        worker = AgentWorker(broker, Settings(default_provider="ollama", dev_mode=True))

        seen_kwargs = {}

        async def fake_stream_chat(*_args, **kwargs):
            seen_kwargs.update(kwargs)
            yield StreamEvent(
                event_type="content",
                content="ok",
                sequence=0,
                metadata={"is_complete": True, "all_messages": []},
            )

        class FakeAgentManager:
            def get_agent(self):
                raise AssertionError("worker must not fetch and pass primary agent")

        monkeypatch.setattr("services.agent.worker.stream_chat", fake_stream_chat)
        monkeypatch.setattr("services.agent.providers.ProviderRegistry.get_model", lambda _config: object())
        monkeypatch.setattr("services.agent.core.get_agent_manager", lambda: FakeAgentManager())
        monkeypatch.setattr("services.agent.mcp.get_mcp_manager", lambda _settings: None)
        monkeypatch.setattr(worker, "_schedule_title_generation", lambda *_args, **_kwargs: asyncio.sleep(0))

        request = ChatRequest(connection_id=connection_id, content="hello", player_name="alice")
        await worker._process_request_locked(request, connection_id)

        assert "agent" not in seen_kwargs

    asyncio.run(_run())
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
python3 -m pytest tests/test_agent_provider_lifecycle.py::test_worker_stream_chat_does_not_pass_primary_agent -v
```

预期：当前代码 FAIL，因为 worker 调用 `get_agent()` 并传入 `agent=agent`。

- [ ] **步骤 3：实现最小修复**

在 `services/agent/worker.py` 删除 primary agent 获取和 `agent=agent` 参数：

```python
from services.agent.core import _is_mcp_timeout_error
from services.agent.mcp import get_mcp_manager

mcp_manager = get_mcp_manager(self.settings)

try:
    async for event in stream_chat(
        request.content,
        deps,
        model,
        message_history=message_history,
    ):
        ...
```

不要删除 `_is_mcp_timeout_error` 后续使用；只删除 `get_agent_manager()` 和 `agent_manager.get_agent()`。

- [ ] **步骤 4：补充 manager 层 fallback 测试**

在 `tests/test_agent_provider_lifecycle.py` 添加：

```python
def test_chat_agent_manager_prefers_fallback_after_mcp_failure(monkeypatch):
    from services.agent.core import ChatAgentManager

    manager = ChatAgentManager()
    created = []

    def fake_create(toolsets=None):
        agent = {"toolsets": toolsets, "index": len(created)}
        created.append(agent)
        return agent

    monkeypatch.setattr(manager, "_create_agent", fake_create)
    primary = manager.get_agent()
    manager.mark_mcp_failed()

    active = manager.get_active_agent(connection_id="conn", mode="stream")

    assert active is not primary
    assert active["toolsets"] is None
```

- [ ] **步骤 5：运行相关测试**

运行：

```bash
python3 -m pytest tests/test_agent_provider_lifecycle.py::test_worker_stream_chat_does_not_pass_primary_agent tests/test_agent_provider_lifecycle.py::test_chat_agent_manager_prefers_fallback_after_mcp_failure -v
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add services/agent/worker.py tests/test_agent_provider_lifecycle.py
git commit -m "fix: let stream chat choose fallback agent"
```

---

### 任务 3：修复 tellraw 分片的真实字节预算

**文件：**
- 修改：`models/minecraft.py:9-17`, `models/minecraft.py:69-83`
- 修改：`services/websocket/flow_control.py:79-109`
- 修改：`tests/test_websocket_delivery.py`

- [ ] **步骤 1：编写失败测试**

在 `tests/test_websocket_delivery.py` 添加：

```python
@pytest.mark.asyncio
async def test_delivery_chunks_tellraw_after_message_escaping(monkeypatch) -> None:
    monkeypatch.setattr("services.websocket.delivery.asyncio.sleep", _no_sleep)
    sender = DummySender()
    delivery = McbeOutboundDelivery(connection_id="conn", send_payload=sender.send)

    count = await delivery.send_tellraw('":"%' * 180, color="§a", source="escaped_tellraw")

    assert count == len(sender.payloads)
    assert count >= 2
    for payload in sender.payloads:
        command_line = json.loads(payload)["body"]["commandLine"]
        assert len(command_line.encode("utf-8")) <= 461


@pytest.mark.asyncio
async def test_delivery_chunks_tellraw_with_quoted_target_budget(monkeypatch) -> None:
    monkeypatch.setattr("services.websocket.delivery.asyncio.sleep", _no_sleep)
    sender = DummySender()
    delivery = McbeOutboundDelivery(connection_id="conn", send_payload=sender.send)

    count = await delivery.send_tellraw(
        "中" * 300,
        color="§a",
        source="quoted_target",
        target='Alice The "Builder"',
    )

    assert count == len(sender.payloads)
    assert count >= 2
    for payload in sender.payloads:
        command_line = json.loads(payload)["body"]["commandLine"]
        assert 'tellraw "Alice The \\"Builder\\"" ' in command_line
        assert len(command_line.encode("utf-8")) <= 461
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
python3 -m pytest tests/test_websocket_delivery.py::test_delivery_chunks_tellraw_after_message_escaping tests/test_websocket_delivery.py::test_delivery_chunks_tellraw_with_quoted_target_budget -v
```

预期：当前代码至少一个测试 FAIL，通常是 `_assert_byte_safe` 抛 `ValueError` 或 payload 超过 461 字节。

- [ ] **步骤 3：暴露 tellraw sanitization 函数**

在 `models/minecraft.py` 中新增函数并复用：

```python
def sanitize_tellraw_text(message: str) -> str:
    return message.replace('"', '\\"').replace(":", "：").replace("%", "\\%")


def sanitize_tellraw_target(target: str) -> str:
    return _sanitize_tellraw_target(target)
```

把 `create_tellraw()` 改为：

```python
safe_message = sanitize_tellraw_text(message)
safe_target = sanitize_tellraw_target(target)
command_line = f'tellraw {safe_target} {{"rawtext":[{{"text":"{color}{safe_message}"}}]}}'
```

保留 `_sanitize_tellraw_target()` 私有函数，避免破坏已有导入。

- [ ] **步骤 4：按最终 commandLine 字节切分 tellraw**

在 `services/websocket/flow_control.py` 引入 sanitization：

```python
from models.minecraft import MinecraftCommand, sanitize_tellraw_target, sanitize_tellraw_text
```

添加 helper：

```python
@classmethod
def _tellraw_command_line_size(cls, text: str, color: str, target: str) -> int:
    command = MinecraftCommand.create_tellraw(text, color=color, target=target)
    return len(command.body.commandLine.encode("utf-8"))

@classmethod
def _split_tellraw_text(cls, message: str, color: str, target: str, max_len: int) -> list[str]:
    parts: list[str] = []
    current = ""
    for char in message:
        candidate = current + char
        if len(candidate) <= max_len and cls._tellraw_command_line_size(candidate, color, target) <= _COMMAND_LINE_BYTE_BUDGET:
            current = candidate
            continue
        if current:
            parts.append(current)
            current = char
        if cls._tellraw_command_line_size(current, color, target) > _COMMAND_LINE_BYTE_BUDGET:
            raise ValueError("single tellraw character exceeds byte budget")
    if current:
        parts.append(current)
    return parts
```

把 `chunk_tellraw()` 中的预算和 `_split_text()` 调用替换为：

```python
text_parts = cls._split_tellraw_text(message, color, target, max_len)
```

不要使用未被使用的 `sanitize_tellraw_target` / `sanitize_tellraw_text` 导入；如果 helper 只通过 `MinecraftCommand.create_tellraw()` 测量，就只导入 `MinecraftCommand`。

- [ ] **步骤 5：运行测试验证通过**

运行：

```bash
python3 -m pytest tests/test_websocket_delivery.py -v
```

预期：PASS，所有 tellraw/raw command delivery 测试通过。

- [ ] **步骤 6：Commit**

```bash
git add models/minecraft.py services/websocket/flow_control.py tests/test_websocket_delivery.py
git commit -m "fix: chunk tellraw by escaped command bytes"
```

---

### 任务 4：重置 AgentRuntime shutdown/reinitialize 状态

**文件：**
- 修改：`tests/test_agent_provider_lifecycle.py`
- 修改：`services/agent/core.py:90-126`
- 修改：`services/agent/runtime.py:52-55`

- [ ] **步骤 1：编写失败测试**

在 `tests/test_agent_provider_lifecycle.py` 添加：

```python
def test_agent_runtime_shutdown_resets_managers_for_reinitialize():
    import asyncio

    calls = []

    class FakeRuntimeAdapters:
        async def warmup_models(self, settings):
            calls.append(("warmup", settings))

        async def shutdown(self):
            calls.append(("adapters_shutdown",))

    class FakeMCPManager:
        def __init__(self, label):
            self.label = label
            self.shutdown_called = False

        async def initialize(self):
            calls.append(("mcp_initialize", self.label))
            return True

        def get_toolsets_for_agent(self):
            return [self.label]

        async def shutdown(self):
            self.shutdown_called = True
            calls.append(("mcp_shutdown", self.label))

    class FakeChatAgentManager:
        def __init__(self):
            self.initialized_with = []
            self.reset_called = False

        async def initialize(self, settings, mcp_toolsets):
            self.initialized_with.append((settings, list(mcp_toolsets)))
            calls.append(("agent_initialize", settings, list(mcp_toolsets)))

        def reset(self):
            self.reset_called = True
            calls.append(("agent_reset",))

    first_mcp = FakeMCPManager("first")
    first_agent = FakeChatAgentManager()
    runtime = AgentRuntime(
        runtime_adapters=FakeRuntimeAdapters(),
        mcp_manager=first_mcp,
        chat_agent_manager=first_agent,
    )

    asyncio.run(runtime.initialize("settings-1"))
    asyncio.run(runtime.shutdown())

    assert first_mcp.shutdown_called
    assert first_agent.reset_called
    assert runtime.mcp_manager is None
    assert runtime.chat_agent_manager is None
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
python3 -m pytest tests/test_agent_provider_lifecycle.py::test_agent_runtime_shutdown_resets_managers_for_reinitialize -v
```

预期：当前代码 FAIL，因为 manager 未 reset，runtime 字段未清空。

- [ ] **步骤 3：实现 `ChatAgentManager.reset()`**

在 `services/agent/core.py` 的 `ChatAgentManager` 类中新增：

```python
def reset(self) -> None:
    self._agent = None
    self._fallback_agent = None
    self._mcp_toolsets = []
    self._settings = None
    self._initialized = False
    self._mcp_available = True
```

- [ ] **步骤 4：实现 runtime shutdown reset**

在 `services/agent/runtime.py` 修改 shutdown：

```python
async def shutdown(self) -> None:
    if self.mcp_manager is not None:
        await self.mcp_manager.shutdown()
        self.mcp_manager = None
    if self.chat_agent_manager is not None:
        reset = getattr(self.chat_agent_manager, "reset", None)
        if reset is not None:
            reset()
        self.chat_agent_manager = None
    await self.runtime_adapters.shutdown()
```

- [ ] **步骤 5：运行 lifecycle 测试**

运行：

```bash
python3 -m pytest tests/test_agent_provider_lifecycle.py -v
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add services/agent/core.py services/agent/runtime.py tests/test_agent_provider_lifecycle.py
git commit -m "fix: reset agent runtime lifecycle state"
```

---

### 任务 5：移除 session lock 合成 phantom default conversation

**文件：**
- 修改：`tests/test_queue_context.py:139-163`, `tests/test_queue_context.py:192-207`
- 修改：`core/session.py:210-235`, `core/session.py:340-356`

- [ ] **步骤 1：编写失败测试**

在 `tests/test_queue_context.py` 添加：

```python
def test_clear_player_conversations_does_not_recreate_default_from_lock() -> None:
    broker = MessageBroker()
    connection_id = uuid4()

    broker.get_session_lock(connection_id, "alice")
    broker.set_conversation_history(connection_id, "alice", _build_turn(1), "default")
    broker.set_conversation_history(connection_id, "alice", _build_turn(2), "other")

    broker.clear_player_conversation_histories(connection_id, "alice")

    assert broker.list_player_conversation_metadata(connection_id, "alice") == []
    assert broker.list_player_conversations(connection_id, "alice") == []
    assert broker.resolve_conversation_short_id(connection_id, "alice", "#1") is None
```

- [ ] **步骤 2：更新现有 metadata 列表测试预期**

`test_list_player_conversation_metadata_includes_short_ids_and_titles` 当前因调用 `get_session_lock()` 期望出现 `default`。改为不期待 lock 合成 default：

```python
assert [(item.conversation_id, item.short_id, item.title) for item in metadata] == [
    ("build-plan", 1, "Build Plan"),
    ("redstone", 2, "Redstone"),
    ("metadata-only", 3, None),
]
assert metadata[0].title_status == "ready"
assert metadata[1].title_status == "ready"
assert metadata[2].title_status == "pending"
```

- [ ] **步骤 3：运行测试验证失败**

运行：

```bash
python3 -m pytest tests/test_queue_context.py::test_clear_player_conversations_does_not_recreate_default_from_lock tests/test_queue_context.py::test_list_player_conversation_metadata_includes_short_ids_and_titles -v
```

预期：当前代码 FAIL，因为 lock 仍会合成 default。

- [ ] **步骤 4：实现最小修复**

在 `core/session.py` 删除 `list_player_conversation_metadata()` 的锁推断块：

```python
for cid, pname in self._session_locks.keys():
    if cid == connection_id and pname == player:
        conversation_ids.add(DEFAULT_CONVERSATION_ID)
```

在 `list_player_conversations()` 删除锁推断块：

```python
for cid, pname in self._session_locks.keys():
    if cid == connection_id and pname == player:
        conversations.setdefault(DEFAULT_CONVERSATION_ID, 0)
```

- [ ] **步骤 5：运行队列上下文测试**

运行：

```bash
python3 -m pytest tests/test_queue_context.py -v
```

预期：PASS。若其他测试依赖 lock 合成 default，应改为显式调用 `ensure_conversation()` 或写入 history/metadata。

- [ ] **步骤 6：Commit**

```bash
git add core/session.py tests/test_queue_context.py
git commit -m "fix: stop listing lock-only conversations"
```

---

### 任务 6：修复命令前缀和 alias 的边界匹配

**文件：**
- 修改：`tests/test_websocket_command.py`
- 修改：`services/websocket/command.py:73-97`

- [ ] **步骤 1：编写失败测试**

在 `tests/test_websocket_command.py` 添加：

```python
def test_resolve_parsed_command_requires_prefix_boundary() -> None:
    registry = CommandRegistry(COMMANDS)

    assert registry.resolve_parsed("AGENT 聊天室 hello") is None
    assert registry.resolve_parsed("#登录密码") is None


def test_resolve_parsed_command_requires_alias_boundary() -> None:
    registry = CommandRegistry(COMMANDS)

    assert registry.resolve_parsed("askew question") is None
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
python3 -m pytest tests/test_websocket_command.py::test_resolve_parsed_command_requires_prefix_boundary tests/test_websocket_command.py::test_resolve_parsed_command_requires_alias_boundary -v
```

预期：当前代码 FAIL，因为 `startswith()` 会误匹配。

- [ ] **步骤 3：实现边界 helper**

在 `services/websocket/command.py` 的 `CommandRegistry` 类中添加私有静态方法：

```python
@staticmethod
def _matches_token(message: str, token: str) -> bool:
    if message == token:
        return True
    if not message.startswith(token):
        return False
    return len(message) > len(token) and message[len(token)].isspace()
```

- [ ] **步骤 4：替换 startswith 调用**

把 `resolve_parsed()` 中两处匹配改为：

```python
if self._matches_token(message, prefix):
    ...

if self._matches_token(message, alias):
    ...
```

内容提取保持原逻辑：

```python
content = message[len(prefix):].strip()
```

- [ ] **步骤 5：运行命令解析测试**

运行：

```bash
python3 -m pytest tests/test_websocket_command.py -v
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add services/websocket/command.py tests/test_websocket_command.py
git commit -m "fix: require command token boundaries"
```

---

### 任务 7：保留 `TellrawMessage(target="@a")` 广播语义

**文件：**
- 修改：`tests/test_websocket_conversation_commands.py`
- 修改：`services/websocket/server.py:1036-1055`

- [ ] **步骤 1：编写失败测试**

在 `tests/test_websocket_conversation_commands.py` 添加：

```python
def test_send_ws_payload_preserves_broadcast_tellraw_target(tmp_path, monkeypatch):
    async def _run() -> None:
        server, _broker, state = _server(tmp_path, monkeypatch)
        state.player_name = "Alice"

        msg = server.protocol_handler.create_info_message("broadcast")
        await server._send_ws_payload(state, msg, source="broadcast_test")

        assert state.websocket.sent
        command_line = __import__("json").loads(state.websocket.sent[-1])["body"]["commandLine"]
        assert command_line.startswith("tellraw @a ")
        assert "tellraw Alice " not in command_line

    asyncio.run(_run())
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
python3 -m pytest tests/test_websocket_conversation_commands.py::test_send_ws_payload_preserves_broadcast_tellraw_target -v
```

预期：当前代码 FAIL，commandLine 以 `tellraw Alice` 开头。

- [ ] **步骤 3：实现最小修复**

在 `services/websocket/server.py` 中把 `_send_ws_payload()` 的 target 改为原样传递：

```python
target=payload.target,
```

完整调用为：

```python
await self._delivery(state).send_tellraw(
    payload.text,
    color=payload.color,
    source=source,
    target=payload.target,
)
```

不要在 `_send_ws_payload()` 中读取 `state.player_name`。

- [ ] **步骤 4：确认单人响应调用点仍显式传 player**

检查 `services/websocket/minecraft.py` 的 `TellrawMessage` 创建路径；需要单人定向的路径应在调用点传入具体 target。如果发现 `_send_ws_payload()` 调用者期望自动定向，则在该调用点传入 `TellrawMessage(target=player_name)`，不要恢复 adapter 层隐式改写。

- [ ] **步骤 5：运行相关测试**

运行：

```bash
python3 -m pytest tests/test_websocket_conversation_commands.py tests/test_websocket_delivery.py -v
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add services/websocket/server.py tests/test_websocket_conversation_commands.py
git commit -m "fix: preserve tellraw broadcast target"
```

---

### 任务 8：过长 raw command 向玩家回传错误

**文件：**
- 修改：`tests/test_websocket_conversation_commands.py:184-197`
- 修改：`services/websocket/server.py:889-897`

- [ ] **步骤 1：把现有测试改为目标行为**

将 `test_handle_run_command_rejects_too_long_raw_command` 改为：

```python
def test_handle_run_command_reports_too_long_raw_command(tmp_path, monkeypatch):
    async def _run() -> None:
        server, _broker, state = _server(tmp_path, monkeypatch)

        await server.handle_run_command(state, "say " + "X" * 1000)

        assert state.websocket.sent
        command_line = __import__("json").loads(state.websocket.sent[-1])["body"]["commandLine"]
        assert "raw command too long" in command_line

    asyncio.run(_run())
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
python3 -m pytest tests/test_websocket_conversation_commands.py::test_handle_run_command_reports_too_long_raw_command -v
```

预期：当前代码 FAIL，因为 `ValueError` 传播且没有发送错误消息。

- [ ] **步骤 3：实现最小修复**

在 `services/websocket/server.py` 中修改 `handle_run_command()`：

```python
try:
    await self._delivery(state).send_raw_command(command, source="run_command")
except ValueError as exc:
    msg = self.protocol_handler.create_error_message(str(exc))
    await self._send_ws_payload(state, msg, source="run_command")
    return
```

保留成功路径的 `logger.info(...)`。

- [ ] **步骤 4：运行相关测试**

运行：

```bash
python3 -m pytest tests/test_websocket_conversation_commands.py::test_handle_run_command_reports_too_long_raw_command tests/test_websocket_delivery.py::test_delivery_rejects_too_long_raw_command tests/test_websocket_delivery.py::test_delivery_rejects_raw_command_over_byte_budget -v
```

预期：PASS。delivery 层仍应抛 `ValueError`，server 层负责转成玩家错误消息。

- [ ] **步骤 5：Commit**

```bash
git add services/websocket/server.py tests/test_websocket_conversation_commands.py
git commit -m "fix: report raw command length errors"
```

---

### 任务 9：工具结果事件使用真实 tool name

**文件：**
- 修改：`tests/test_stream_mode.py`
- 修改：`services/agent/core.py:600-695`

- [ ] **步骤 1：编写失败测试**

在 `tests/test_stream_mode.py` 中添加异步测试。可复用现有 `MockAgent`, `MockCallToolsNode`, `make_function_tool_call_event`, `make_function_tool_result_event`, `AgentDependencies`：

```python
def test_stream_chat_tool_result_uses_tool_name_from_call_event(monkeypatch):
    async def _run() -> None:
        monkeypatch.setattr(core, "Agent", MockAgent)
        monkeypatch.setattr(core, "FunctionToolCallEvent", SimpleNamespace)
        monkeypatch.setattr(core, "FunctionToolResultEvent", SimpleNamespace)

        tool_call = make_function_tool_call_event(
            tool_name="run_minecraft_command",
            args={"command": "say hi"},
        )
        tool_result = make_function_tool_result_event(
            tool_call_id="test_call_id",
            result_content="ok",
        )
        agent = MockAgent(tool_events=[tool_call, tool_result])
        deps = AgentDependencies(
            connection_id=uuid4(),
            settings=SimpleNamespace(),
            http_client=None,
            send_to_game=_noop,
            run_command=None,
            addon_bridge=None,
        )

        events = []
        async for event in core.stream_chat("hello", deps, object(), agent=agent):
            events.append(event)

        result = next(event for event in events if event.event_type == "tool_result")
        assert result.metadata["tool_call_id"] == "test_call_id"
        assert result.metadata["tool_name"] == "run_minecraft_command"

    asyncio.run(_run())
```

若 `monkeypatch.setattr(core, "FunctionToolCallEvent", SimpleNamespace)` 不能让 `isinstance()` 同时区分 call/result，请按现有测试文件模式创建两个独立 fake 类并 patch 这两个符号；测试目标是让 call id `test_call_id` 不被解析成 `test`。

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
python3 -m pytest tests/test_stream_mode.py::test_stream_chat_tool_result_uses_tool_name_from_call_event -v
```

预期：当前代码 FAIL，metadata 中 `tool_name` 为 `test` 或 `None`。

- [ ] **步骤 3：实现 tool_call_id 映射**

在 `services/agent/core.py` 的 `StreamContext` 或 `stream_chat()` 局部状态中增加映射。最小局部实现：在进入 `async with active_agent.iter(...)` 前初始化：

```python
tool_names_by_call_id: dict[str, str] = {}
```

处理 call event 时记录：

```python
tool_names_by_call_id[event.part.tool_call_id] = event.part.tool_name
```

处理 result event 时使用映射：

```python
tool_name = tool_names_by_call_id.get(event.tool_call_id)
yield StreamEvent(
    event_type="tool_result",
    content=result_content,
    sequence=ctx.sequence,
    metadata={
        "tool_call_id": event.tool_call_id,
        "tool_name": tool_name,
    },
)
```

删除 `event.tool_call_id.split("_")[0]` 逻辑。

- [ ] **步骤 4：运行 stream mode 测试**

运行：

```bash
python3 -m pytest tests/test_stream_mode.py -v
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add services/agent/core.py tests/test_stream_mode.py
git commit -m "fix: preserve tool names for stream results"
```

---

### 任务 10：标题生成 cleanup race 不再重建 metadata

**文件：**
- 修改：`tests/test_queue_context.py`
- 修改：`core/queue.py:213-290`
- 修改：`services/agent/worker.py:498-515`

- [ ] **步骤 1：编写失败测试**

在 `tests/test_queue_context.py` 添加：

```python
def test_title_generation_does_not_recreate_metadata_after_unregister(monkeypatch) -> None:
    async def _run() -> None:
        broker = MessageBroker()
        connection_id = uuid4()
        broker.register_connection(connection_id)
        worker = AgentWorker(broker, Settings(default_provider="ollama", dev_mode=True))

        async def fake_generate_title(*_args, **_kwargs):
            return "Late Title"

        original_has_connection = broker.has_connection

        def unregistering_has_connection(conn_id):
            result = original_has_connection(conn_id)
            if result:
                broker.unregister_connection(conn_id)
            return result

        monkeypatch.setattr("services.agent.worker.generate_conversation_title", fake_generate_title)
        monkeypatch.setattr(broker, "has_connection", unregistering_has_connection)

        await worker._generate_title_for_conversation(
            connection_id,
            "alice",
            "default",
            "first message",
            object(),
        )

        assert broker.list_player_conversation_metadata(connection_id, "alice") == []

    asyncio.run(_run())
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
python3 -m pytest tests/test_queue_context.py::test_title_generation_does_not_recreate_metadata_after_unregister -v
```

预期：当前代码 FAIL，`set_conversation_title()` 在 unregister 后重新创建 metadata。

- [ ] **步骤 3：在 broker 添加原子写入方法**

在 `core/queue.py` 添加：

```python
def set_conversation_title_if_connected(
    self,
    connection_id: UUID,
    player_name: str | None,
    conversation_id: str | None,
    title: str,
) -> ConversationMetadata | None:
    if not self.has_connection(connection_id):
        return None
    return self.sessions.set_conversation_title(
        connection_id,
        player_name,
        conversation_id,
        title,
    )
```

该方法仍不是跨线程原子；本项目事件循环内同步执行，关键是把 check/write 封装为一个无 await 的连续操作，避免 worker 两步之间 yield。

- [ ] **步骤 4：worker 使用新方法并删除外部 check/write 间隙**

在 `services/agent/worker.py` 修改 `_generate_title_for_conversation()`：

```python
title = await generate_conversation_title(first_user_message, model)
metadata = self.broker.set_conversation_title_if_connected(
    connection_id,
    player_name,
    conversation_id,
    title,
)
if metadata is None:
    return
```

删除原来的：

```python
if not self.broker.has_connection(connection_id):
    return
self.broker.set_conversation_title(...)
```

- [ ] **步骤 5：调整测试 monkeypatch**

步骤 1 的 monkeypatch 针对 `has_connection` 仍能触发新 broker 方法中的检查，并验证不会在 unregister 后写入 metadata。

- [ ] **步骤 6：运行队列上下文测试**

运行：

```bash
python3 -m pytest tests/test_queue_context.py -v
```

预期：PASS。

- [ ] **步骤 7：Commit**

```bash
git add core/queue.py services/agent/worker.py tests/test_queue_context.py
git commit -m "fix: avoid late title metadata after cleanup"
```

---

### 任务 11：清理或标注历史文档产物

**文件：**
- 删除或移动：`PROMPT.md`
- 修改：`docs/superpowers/reports/2026-06-13-architecture-tech-debt-scan.md`

- [ ] **步骤 1：删除根目录一次性执行提示**

推荐直接删除 `PROMPT.md`。这是根目录任务提示，不是持久项目文档。

运行：

```bash
git rm PROMPT.md
```

若维护者要求保留，则改为移动到历史目录：

```bash
git mv PROMPT.md docs/superpowers/reports/2026-06-13-tech-debt-scan-execution-prompt.md
```

并在文件第一行添加：

```markdown
> 历史任务提示：该文件记录 2026-06-13 重构执行输入，不是当前项目长期开发规范。
```

- [ ] **步骤 2：标注旧扫描报告为历史输入**

在 `docs/superpowers/reports/2026-06-13-architecture-tech-debt-scan.md` 标题下方添加：

```markdown
> 状态说明：这是 2026-06-13 重构前的架构债务扫描输入，保留用于追溯背景；它不代表 `tech-debt-scan-plan` 分支实现后的最终验证状态。最终审查请看 `docs/superpowers/reports/2026-06-14-tech-debt-scan-plan-review.md`，修复执行请看 `docs/superpowers/plans/2026-06-14-tech-debt-scan-plan-review-fixes.md`。
```

- [ ] **步骤 3：运行文档检查**

运行：

```bash
git diff -- PROMPT.md docs/superpowers/reports/2026-06-13-architecture-tech-debt-scan.md
```

预期：`PROMPT.md` 被删除或移动；旧报告顶部明确标注历史状态。

- [ ] **步骤 4：Commit**

若删除：

```bash
git add docs/superpowers/reports/2026-06-13-architecture-tech-debt-scan.md
git rm PROMPT.md
git commit -m "docs: mark tech debt scan artifacts as historical"
```

若移动：

```bash
git add docs/superpowers/reports/2026-06-13-architecture-tech-debt-scan.md docs/superpowers/reports/2026-06-13-tech-debt-scan-execution-prompt.md
git rm PROMPT.md
git commit -m "docs: mark tech debt scan artifacts as historical"
```

---

### 任务 12：全量回归验证和最终审查

**文件：**
- 修改：无，除非验证发现回归。

- [ ] **步骤 1：运行重点测试**

运行：

```bash
python3 -m pytest \
  tests/test_queue_context.py \
  tests/test_agent_provider_lifecycle.py \
  tests/test_websocket_delivery.py \
  tests/test_websocket_command.py \
  tests/test_websocket_conversation_commands.py \
  tests/test_stream_mode.py \
  -v
```

预期：PASS。

- [ ] **步骤 2：运行全量测试**

运行：

```bash
python3 -m pytest -v
```

预期：PASS。

- [ ] **步骤 3：查看 diff 只包含计划内变更**

运行：

```bash
git status --short
git diff --stat master...HEAD
git diff --check
```

预期：

- `git status --short` 只显示计划内已修改文件，或在每个任务 commit 后为空。
- `git diff --check` 无 whitespace error。

- [ ] **步骤 4：请求代码审查**

使用 `requesting-code-review` 技能进行最终审查，重点关注：

- generation 锁内快照是否仍能防止管理命令期间的 stale 写入。
- tellraw 分片是否对中文、多字节、转义字符和 quoted target 都满足 461B。
- runtime shutdown reset 是否破坏现有 lazy initialization。
- `_send_ws_payload()` 去掉 `state.player_name` 后是否存在本应单人发送却广播的调用点。

- [ ] **步骤 5：最终 Commit（如果前面未按任务 commit）**

如果执行者没有逐任务 commit，则一次性提交全部修复：

```bash
git add services/agent/worker.py services/agent/core.py services/agent/runtime.py core/session.py core/queue.py models/minecraft.py services/websocket/flow_control.py services/websocket/command.py services/websocket/server.py tests/test_queue_context.py tests/test_agent_provider_lifecycle.py tests/test_websocket_delivery.py tests/test_websocket_command.py tests/test_websocket_conversation_commands.py tests/test_stream_mode.py docs/superpowers/reports/2026-06-13-architecture-tech-debt-scan.md
git add -u PROMPT.md
git commit -m "fix: address tech debt scan review regressions"
```

---

## 自检结果

- 规格覆盖度：覆盖审查报告 11 项发现；高风险项位于任务 1、2、3；中风险项位于任务 4、5、6、7、8、9；低风险项位于任务 10、11；任务 12 覆盖最终验证。
- 占位符扫描：计划未使用“待定/TODO/后续实现”等占位符；每个代码变更步骤都给出目标文件、代码片段、命令和预期结果。
- 类型一致性：所有新增方法名在计划中保持一致：`ChatAgentManager.reset()`、`MessageBroker.set_conversation_title_if_connected()`、`CommandRegistry._matches_token()`、`sanitize_tellraw_text()`、`sanitize_tellraw_target()`。
