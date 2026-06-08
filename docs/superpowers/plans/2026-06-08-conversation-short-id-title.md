# 对话短 ID 与自动标题实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为运行时对话增加稳定短 ID（列表显示 `#1`、`#2` 并支持用短 ID 切换），在首次用户请求后异步生成对话标题，并让 `conversation switch` 创建新对话时明确提示“已创建并切换到新会话”。

**架构：** 在 `MessageBroker` 中新增按 `(connection_id, player_name, conversation_id)` 管理的对话元数据，短 ID 由玩家级顺序分配并保持到连接清理；标题作为同一元数据字段保存。`WebSocketServer` 负责解析短 ID、列表展示和切换提示；`AgentWorker` 在首轮完成后触发后台标题生成，使用 PydanticAI 的无工具一次性 `Agent.run()` 调用，不阻塞正常响应流。

**技术栈：** Python 3.11、asyncio、PydanticAI v1、pytest、现有 `MessageBroker` / `WebSocketServer` / `AgentWorker`。

---

## 当前分支状态

- 已执行：`git checkout master`
- 当前分支：`master`
- 工作区要求：执行本计划前保持 `git status --short` 干净，避免把计划文档以外的改动混入实现提交。

## 文档依据

- PydanticAI 当前推荐的一次性异步调用可使用 `result = await agent.run(prompt, model=model)`。
- 标题生成 Agent 应单独创建为无工具 Agent，避免复用带 Minecraft/MCP 工具的聊天 Agent。

## 文件结构

- 修改：`core/queue.py`
  - 新增 `ConversationMetadata` dataclass，集中维护运行时对话的 `short_id`、`title`、`title_status`。
  - 新增短 ID 分配、短 ID 解析、标题读写、连接/玩家清理时同步清理元数据的方法。
- 修改：`services/websocket/server.py`
  - `conversation list` 展示 `#短ID`、长 ID、当前标记、标题和消息数。
  - `conversation switch` 支持目标为 `#1` 或 `1`，解析到真实 conversation_id。
  - `conversation switch <新ID>` 创建不存在的对话时返回“已创建并切换到新会话: ...”。
  - `conversation new` 创建元数据并在提示中显示短 ID。
- 修改：`services/agent/worker.py`
  - 在首轮对话完成并成功写回历史后，异步调度标题生成任务。
  - 标题任务失败只记录日志，不影响聊天响应。
- 创建：`services/agent/title.py`
  - 提供 `generate_conversation_title(first_user_message, model) -> str`，使用 PydanticAI 无工具 Agent 生成 2-12 字中文标题。
  - 提供标题清洗函数，限制长度、去除引号/换行，空结果回退到用户请求摘要。
- 修改：`core/conversation.py`
  - 保存对话时写入当前运行时标题。
  - 列出已保存对话时读取并展示标题。
- 修改：`tests/test_queue_context.py`
  - 覆盖短 ID 分配、解析、元数据隔离和清理。
  - 覆盖标题状态只生成一次、失败后可重试或保持稳定状态的行为。
- 修改：`tests/test_websocket_conversation_commands.py`
  - 覆盖列表显示 `#1`、`#2`，短 ID 切换，以及新对话提示。
- 修改：`tests/test_queue_context.py`
  - 覆盖 Worker 首轮完成后触发标题生成，非首轮不重复触发。
- 修改：`tests/test_conversation_manager.py`
  - 覆盖保存和已保存列表包含标题。

## 行为约定

- 短 ID 是运行时、按玩家隔离、按创建顺序递增的数字展示 ID：`#1`、`#2`。
- 短 ID 不写入保存文件，不跨连接持久化；保存文件仍使用原 `session_id`。
- `conversation switch #1` 和 `conversation switch 1` 都解析为当前玩家的短 ID `1`。
- 如果 `switch` 参数不是已有短 ID，则按原长 ID 处理。
- 默认对话被首次确保存在时分配短 ID，通常是 `#1`。
- 标题初始为“生成中”或空；列表中空标题显示“未命名”。
- 标题只基于首次用户请求生成；后续消息不改标题。
- 标题任务不会发送额外游戏消息，避免干扰聊天流；用户通过 `conversation list/status/save/saved` 看到标题。
- 标题生成失败不阻塞主流程，日志记录 `conversation_title_generation_failed`，标题保持“未命名”。

---

### 任务 1：运行时对话元数据

**文件：**
- 修改：`core/queue.py:1-391`
- 测试：`tests/test_queue_context.py`

- [ ] **步骤 1：编写失败的短 ID 分配测试**

在 `tests/test_queue_context.py` 追加：

```python
def test_conversation_metadata_assigns_stable_short_ids_per_player() -> None:
    broker = MessageBroker()
    connection_id = uuid4()

    default_meta = broker.ensure_conversation_metadata(connection_id, "alice", "default")
    build_meta = broker.ensure_conversation_metadata(connection_id, "alice", "build")
    default_meta_again = broker.ensure_conversation_metadata(connection_id, "alice", "default")
    bob_meta = broker.ensure_conversation_metadata(connection_id, "bob", "default")

    assert default_meta.short_id == 1
    assert build_meta.short_id == 2
    assert default_meta_again.short_id == 1
    assert bob_meta.short_id == 1
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_queue_context.py::test_conversation_metadata_assigns_stable_short_ids_per_player -v`
预期：FAIL，报错包含 `AttributeError: 'MessageBroker' object has no attribute 'ensure_conversation_metadata'`。

- [ ] **步骤 3：实现元数据 dataclass 和分配方法**

在 `core/queue.py` 的 `SessionLockKey` 下方新增：

```python
@dataclass
class ConversationMetadata:
    conversation_id: str
    short_id: int
    title: str | None = None
    title_status: str = "pending"
```

在 `MessageBroker.__init__` 中 `_conversation_generations` 后新增：

```python
self._conversation_metadata: dict[SessionKey, ConversationMetadata] = {}
self._conversation_short_ids: dict[SessionLockKey, dict[int, str]] = {}
self._next_conversation_short_id: dict[SessionLockKey, int] = {}
```

在 `MessageBroker` 中新增：

```python
def ensure_conversation_metadata(
    self,
    connection_id: UUID,
    player_name: str | None,
    conversation_id: str | None = None,
) -> ConversationMetadata:
    key = make_session_key(connection_id, player_name, conversation_id)
    metadata = self._conversation_metadata.get(key)
    if metadata is not None:
        return metadata

    lock_key = make_session_lock_key(connection_id, player_name)
    short_id = self._next_conversation_short_id.get(lock_key, 1)
    normalized_id = normalize_conversation_id(conversation_id)
    metadata = ConversationMetadata(conversation_id=normalized_id, short_id=short_id)
    self._conversation_metadata[key] = metadata
    self._conversation_short_ids.setdefault(lock_key, {})[short_id] = normalized_id
    self._next_conversation_short_id[lock_key] = short_id + 1
    return metadata
```

- [ ] **步骤 4：让现有确保/写入历史自动创建元数据**

在 `ensure_conversation()` 中 `setdefault` 后调用：

```python
self.ensure_conversation_metadata(connection_id, player_name, conversation_id)
```

在 `set_conversation_history()` 中写入 `_conversation_generations` 后调用：

```python
self.ensure_conversation_metadata(connection_id, player_name, conversation_id)
```

- [ ] **步骤 5：运行短 ID 测试验证通过**

运行：`pytest tests/test_queue_context.py::test_conversation_metadata_assigns_stable_short_ids_per_player -v`
预期：PASS。

- [ ] **步骤 6：编写失败的短 ID 解析测试**

在 `tests/test_queue_context.py` 追加：

```python
def test_resolve_conversation_short_id_accepts_hash_and_plain_number() -> None:
    broker = MessageBroker()
    connection_id = uuid4()
    broker.ensure_conversation_metadata(connection_id, "alice", "default")
    broker.ensure_conversation_metadata(connection_id, "alice", "build")

    assert broker.resolve_conversation_short_id(connection_id, "alice", "#2") == "build"
    assert broker.resolve_conversation_short_id(connection_id, "alice", "2") == "build"
    assert broker.resolve_conversation_short_id(connection_id, "alice", "build") is None
    assert broker.resolve_conversation_short_id(connection_id, "alice", "#9") is None
```

- [ ] **步骤 7：运行解析测试验证失败**

运行：`pytest tests/test_queue_context.py::test_resolve_conversation_short_id_accepts_hash_and_plain_number -v`
预期：FAIL，报错包含 `AttributeError: 'MessageBroker' object has no attribute 'resolve_conversation_short_id'`。

- [ ] **步骤 8：实现短 ID 解析**

在 `MessageBroker` 中新增：

```python
def resolve_conversation_short_id(
    self,
    connection_id: UUID,
    player_name: str | None,
    short_id_text: str,
) -> str | None:
    value = short_id_text.strip()
    if value.startswith("#"):
        value = value[1:]
    if not value.isdigit():
        return None
    short_id = int(value)
    lock_key = make_session_lock_key(connection_id, player_name)
    return self._conversation_short_ids.get(lock_key, {}).get(short_id)
```

- [ ] **步骤 9：运行解析测试验证通过**

运行：`pytest tests/test_queue_context.py::test_resolve_conversation_short_id_accepts_hash_and_plain_number -v`
预期：PASS。

- [ ] **步骤 10：编写失败的列表元数据测试**

在 `tests/test_queue_context.py` 追加：

```python
def test_list_player_conversation_metadata_includes_short_ids_and_titles() -> None:
    broker = MessageBroker()
    connection_id = uuid4()
    broker.set_conversation_history(connection_id, "alice", _build_turn(1), "default")
    broker.set_conversation_history(connection_id, "alice", _build_turn(2), "build")
    broker.set_conversation_title(connection_id, "alice", "build", "建筑计划")

    items = broker.list_player_conversation_metadata(connection_id, "alice")

    assert [(item.conversation_id, item.short_id, item.title) for item in items] == [
        ("default", 1, None),
        ("build", 2, "建筑计划"),
    ]
```

- [ ] **步骤 11：运行列表元数据测试验证失败**

运行：`pytest tests/test_queue_context.py::test_list_player_conversation_metadata_includes_short_ids_and_titles -v`
预期：FAIL，报错包含 `AttributeError`。

- [ ] **步骤 12：实现标题和元数据列表 API**

在 `MessageBroker` 中新增：

```python
def set_conversation_title(
    self,
    connection_id: UUID,
    player_name: str | None,
    conversation_id: str | None,
    title: str,
) -> None:
    metadata = self.ensure_conversation_metadata(connection_id, player_name, conversation_id)
    metadata.title = title
    metadata.title_status = "ready"


def get_conversation_metadata(
    self,
    connection_id: UUID,
    player_name: str | None,
    conversation_id: str | None = None,
) -> ConversationMetadata:
    return self.ensure_conversation_metadata(connection_id, player_name, conversation_id)


def list_player_conversation_metadata(
    self,
    connection_id: UUID,
    player_name: str | None,
) -> list[ConversationMetadata]:
    player = player_name or DEFAULT_PLAYER_KEY
    items: list[ConversationMetadata] = []
    for cid, pname, conversation_id in self._conversation_histories.keys():
        if cid == connection_id and pname == player:
            items.append(self.ensure_conversation_metadata(cid, pname, conversation_id))
    for cid, pname in self._session_locks.keys():
        if cid == connection_id and pname == player:
            items.append(self.ensure_conversation_metadata(cid, pname, DEFAULT_CONVERSATION_ID))
    unique = {item.conversation_id: item for item in items}
    return sorted(unique.values(), key=lambda item: item.short_id)
```

- [ ] **步骤 13：运行列表元数据测试验证通过**

运行：`pytest tests/test_queue_context.py::test_list_player_conversation_metadata_includes_short_ids_and_titles -v`
预期：PASS。

- [ ] **步骤 14：更新清理逻辑并测试**

在 `tests/test_queue_context.py` 追加：

```python
def test_clear_connection_sessions_removes_conversation_metadata() -> None:
    broker = MessageBroker()
    connection_id = uuid4()
    broker.ensure_conversation_metadata(connection_id, "alice", "default")

    broker.clear_connection_sessions(connection_id)

    assert broker.list_player_conversation_metadata(connection_id, "alice") == []
    assert broker.resolve_conversation_short_id(connection_id, "alice", "#1") is None
```

运行：`pytest tests/test_queue_context.py::test_clear_connection_sessions_removes_conversation_metadata -v`
预期：FAIL。

在 `clear_connection_sessions()` 中同步清理：

```python
metadata_keys = [key for key in self._conversation_metadata if key[0] == connection_id]
for key in metadata_keys:
    self._conversation_metadata.pop(key, None)

short_id_keys = [key for key in self._conversation_short_ids if key[0] == connection_id]
for key in short_id_keys:
    self._conversation_short_ids.pop(key, None)
    self._next_conversation_short_id.pop(key, None)
```

运行：`pytest tests/test_queue_context.py::test_clear_connection_sessions_removes_conversation_metadata -v`
预期：PASS。

- [ ] **步骤 15：运行相关队列测试**

运行：`pytest tests/test_queue_context.py -v`
预期：PASS。

- [ ] **步骤 16：Commit**

```bash
git add core/queue.py tests/test_queue_context.py
git commit -m "feat(conversation): add runtime conversation metadata"
```

---

### 任务 2：对话命令显示和短 ID 切换

**文件：**
- 修改：`services/websocket/server.py:527-675`
- 测试：`tests/test_websocket_conversation_commands.py`

- [ ] **步骤 1：编写失败的列表显示测试**

在 `tests/test_websocket_conversation_commands.py` 追加：

```python
def test_conversation_list_displays_short_ids_and_titles(tmp_path, monkeypatch):
    async def _run() -> None:
        server, broker, state = _server(tmp_path, monkeypatch)
        broker.set_conversation_history(state.id, "alice", _build_turn(1), "default")
        broker.set_conversation_history(state.id, "alice", _build_turn(2), "build")
        broker.set_conversation_title(state.id, "alice", "build", "建筑计划")
        state.get_player_session("alice").active_conversation_id = "build"

        msg = await server._handle_conversation_locked(state, "list", "alice")

        assert "#1 default" in msg.text
        assert "#2 build *" in msg.text
        assert "建筑计划" in msg.text
        assert "未命名" in msg.text

    asyncio.run(_run())
```

- [ ] **步骤 2：运行列表显示测试验证失败**

运行：`pytest tests/test_websocket_conversation_commands.py::test_conversation_list_displays_short_ids_and_titles -v`
预期：FAIL，当前输出不包含 `#1` 和标题。

- [ ] **步骤 3：实现列表格式**

将 `services/websocket/server.py` 中 `list` 分支替换为：

```python
elif action in ("list", "列表"):
    live_conversations = dict(self.broker.list_player_conversations(state.id, actor))
    live_conversations.setdefault(conversation_id, len(
        self.broker.get_conversation_history(state.id, actor, conversation_id)
    ))
    for conv_id in live_conversations:
        self.broker.ensure_conversation_metadata(state.id, actor, conv_id)

    lines = ["当前连接内对话:"]
    for metadata in self.broker.list_player_conversation_metadata(state.id, actor):
        message_count = live_conversations.get(metadata.conversation_id, 0)
        marker = " *" if metadata.conversation_id == conversation_id else ""
        title = metadata.title or "未命名"
        lines.append(
            f"• #{metadata.short_id} {metadata.conversation_id}{marker} - "
            f"{title} - {message_count} 条消息"
        )
    return self.protocol_handler.create_info_message("\n".join(lines))
```

- [ ] **步骤 4：运行列表显示测试验证通过**

运行：`pytest tests/test_websocket_conversation_commands.py::test_conversation_list_displays_short_ids_and_titles -v`
预期：PASS。

- [ ] **步骤 5：编写失败的短 ID 切换测试**

在 `tests/test_websocket_conversation_commands.py` 追加：

```python
def test_conversation_switch_accepts_short_id(tmp_path, monkeypatch):
    async def _run() -> None:
        server, broker, state = _server(tmp_path, monkeypatch)
        broker.set_conversation_history(state.id, "alice", _build_turn(1), "default")
        broker.set_conversation_history(state.id, "alice", _build_turn(2), "build")

        msg = await server._handle_conversation_locked(state, "switch #2", "alice")

        assert state.get_player_session("alice").active_conversation_id == "build"
        assert "已切换到对话: #2 build" in msg.text
        assert "1轮" in msg.text

    asyncio.run(_run())
```

- [ ] **步骤 6：运行短 ID 切换测试验证失败**

运行：`pytest tests/test_websocket_conversation_commands.py::test_conversation_switch_accepts_short_id -v`
预期：FAIL，当前会把 `#2` 当作长 ID。

- [ ] **步骤 7：实现 switch 参数解析**

在 `switch` 分支中，把：

```python
target_id = normalize_conversation_id(arg)
```

替换为：

```python
resolved_id = self.broker.resolve_conversation_short_id(state.id, actor, arg)
target_id = normalize_conversation_id(resolved_id or arg)
```

并在设置 `active_conversation_id` 前读取元数据：

```python
was_existing = self.broker.conversation_exists(state.id, actor, target_id)
```

- [ ] **步骤 8：更新 switch 返回文案**

将 `switch` 分支尾部替换为：

```python
metadata = self.broker.ensure_conversation_metadata(state.id, actor, target_id)
display_id = f"#{metadata.short_id} {target_id}"
if not was_existing:
    return self.protocol_handler.create_success_message(
        f"已创建并切换到新会话: {display_id}"
    )
return self.protocol_handler.create_success_message(
    f"已切换到对话: {display_id}（{turns}轮）"
)
```

- [ ] **步骤 9：运行短 ID 切换测试验证通过**

运行：`pytest tests/test_websocket_conversation_commands.py::test_conversation_switch_accepts_short_id -v`
预期：PASS。

- [ ] **步骤 10：编写失败的新会话提示测试**

在 `tests/test_websocket_conversation_commands.py` 追加：

```python
def test_conversation_switch_new_id_reports_created_and_switched(tmp_path, monkeypatch):
    async def _run() -> None:
        server, broker, state = _server(tmp_path, monkeypatch)

        msg = await server._handle_conversation_locked(state, "switch build", "alice")

        assert state.get_player_session("alice").active_conversation_id == "build"
        assert broker.conversation_exists(state.id, "alice", "build") is True
        assert "已创建并切换到新会话" in msg.text
        assert "#1 build" in msg.text

    asyncio.run(_run())
```

- [ ] **步骤 11：运行新会话提示测试验证失败**

运行：`pytest tests/test_websocket_conversation_commands.py::test_conversation_switch_new_id_reports_created_and_switched -v`
预期：FAIL，当前输出为“已切换到对话”。

- [ ] **步骤 12：确保不存在时创建历史后再计算轮次**

在 `switch` 分支中按此顺序组织：

```python
was_existing = self.broker.conversation_exists(state.id, actor, target_id)
if not was_existing:
    self.broker.set_conversation_history(state.id, actor, [], target_id)
history = self.broker.get_conversation_history(state.id, actor, target_id)
session.active_conversation_id = target_id
turns = self._count_conversation_turns(history)
```

- [ ] **步骤 13：运行新会话提示测试验证通过**

运行：`pytest tests/test_websocket_conversation_commands.py::test_conversation_switch_new_id_reports_created_and_switched -v`
预期：PASS。

- [ ] **步骤 14：更新 new 命令提示短 ID**

在 `new` 分支中 `set_conversation_history` 后加入：

```python
metadata = self.broker.ensure_conversation_metadata(state.id, actor, new_id)
```

将返回文案替换为：

```python
return self.protocol_handler.create_success_message(
    f"已新建并切换到对话: #{metadata.short_id} {new_id}"
)
```

- [ ] **步骤 15：运行对话命令测试**

运行：`pytest tests/test_websocket_conversation_commands.py -v`
预期：PASS。

- [ ] **步骤 16：Commit**

```bash
git add services/websocket/server.py tests/test_websocket_conversation_commands.py
git commit -m "feat(conversation): show and switch by short ids"
```

---

### 任务 3：标题生成服务

**文件：**
- 创建：`services/agent/title.py`
- 测试：`tests/test_conversation_title.py`

- [ ] **步骤 1：编写失败的标题清洗测试**

创建 `tests/test_conversation_title.py`：

```python
from services.agent.title import clean_conversation_title


def test_clean_conversation_title_strips_quotes_and_limits_length() -> None:
    assert clean_conversation_title('"建筑计划\n"', "如何建造一个自动农场") == "建筑计划"
    assert clean_conversation_title("", "如何建造一个自动农场和红石收集系统") == "如何建造一个自动农场"
    assert clean_conversation_title("一个非常非常非常非常非常长的标题", "备用") == "一个非常非常非常"
```

- [ ] **步骤 2：运行标题清洗测试验证失败**

运行：`pytest tests/test_conversation_title.py::test_clean_conversation_title_strips_quotes_and_limits_length -v`
预期：FAIL，报错包含 `ModuleNotFoundError: No module named 'services.agent.title'`。

- [ ] **步骤 3：实现标题清洗函数**

创建 `services/agent/title.py`：

```python
from pydantic_ai import Agent
from pydantic_ai.models import Model

_TITLE_AGENT = Agent(
    "deepseek:deepseek-chat",
    output_type=str,
    system_prompt=(
        "你是对话标题生成器。根据用户第一条请求生成 2 到 12 个中文字符的标题。"
        "只输出标题，不要解释，不要标点，不要引号。"
    ),
)


def clean_conversation_title(raw_title: str, fallback_source: str) -> str:
    title = raw_title.strip().strip('"“”\'‘’`').replace("\n", " ").strip()
    title = "".join(title.split())
    if not title:
        title = "".join(fallback_source.strip().split())[:12]
    return title[:12] or "未命名"
```

- [ ] **步骤 4：运行标题清洗测试验证通过**

运行：`pytest tests/test_conversation_title.py::test_clean_conversation_title_strips_quotes_and_limits_length -v`
预期：PASS。

- [ ] **步骤 5：编写失败的异步生成测试**

在 `tests/test_conversation_title.py` 追加：

```python
import asyncio
from types import SimpleNamespace

from services.agent import title as title_module


def test_generate_conversation_title_uses_agent_run(monkeypatch) -> None:
    captured = {}

    class FakeAgent:
        async def run(self, prompt, model=None):
            captured["prompt"] = prompt
            captured["model"] = model
            return SimpleNamespace(output="红石农场")

    monkeypatch.setattr(title_module, "_TITLE_AGENT", FakeAgent())

    result = asyncio.run(title_module.generate_conversation_title("如何建造自动红石农场", object()))

    assert result == "红石农场"
    assert "如何建造自动红石农场" in captured["prompt"]
    assert captured["model"] is not None
```

- [ ] **步骤 6：运行异步生成测试验证失败**

运行：`pytest tests/test_conversation_title.py::test_generate_conversation_title_uses_agent_run -v`
预期：FAIL，报错包含 `AttributeError: module 'services.agent.title' has no attribute 'generate_conversation_title'`。

- [ ] **步骤 7：实现异步标题生成**

在 `services/agent/title.py` 追加：

```python
async def generate_conversation_title(first_user_message: str, model: Model) -> str:
    prompt = (
        "请为下面这条 Minecraft AI 助手对话生成短标题。\n"
        "要求：2 到 12 个中文字符；只输出标题；不要标点、引号或解释。\n"
        f"用户请求：{first_user_message}"
    )
    result = await _TITLE_AGENT.run(prompt, model=model)
    return clean_conversation_title(result.output, first_user_message)
```

- [ ] **步骤 8：运行标题服务测试**

运行：`pytest tests/test_conversation_title.py -v`
预期：PASS。

- [ ] **步骤 9：Commit**

```bash
git add services/agent/title.py tests/test_conversation_title.py
git commit -m "feat(conversation): add title generator"
```

---

### 任务 4：首轮完成后异步生成标题

**文件：**
- 修改：`core/queue.py:253-348`
- 修改：`services/agent/worker.py:311-393`
- 测试：`tests/test_queue_context.py`

- [ ] **步骤 1：编写失败的标题调度测试**

在 `tests/test_queue_context.py` 追加：

```python
def test_worker_generates_title_after_first_completed_turn(monkeypatch) -> None:
    import asyncio
    from types import SimpleNamespace

    from config.settings import Settings
    from models.messages import ChatRequest

    broker = MessageBroker()
    connection_id = uuid4()
    broker.register_connection(connection_id)

    worker = AgentWorker(broker=broker, settings=Settings(max_history_turns=5), worker_id=1)
    completed_messages = _build_turn(1)

    class _FakeManager:
        def get_agent(self):
            return object()

    async def _fake_stream_chat(*args, **kwargs):
        yield SimpleNamespace(
            event_type="content",
            content="完成",
            metadata={"is_complete": True, "all_messages": completed_messages},
        )

    async def _fake_check_and_compress(*args, **kwargs):
        return False, "not compressed"

    async def _fake_generate_title(first_user_message, model):
        assert first_user_message == "user-1"
        return "自动标题"

    monkeypatch.setattr("services.agent.worker.stream_chat", _fake_stream_chat)
    monkeypatch.setattr("services.agent.core.get_agent_manager", lambda: _FakeManager())
    monkeypatch.setattr("services.agent.worker.ProviderRegistry.get_model", lambda *_: object())
    monkeypatch.setattr("services.agent.worker.generate_conversation_title", _fake_generate_title)
    monkeypatch.setattr(
        "core.conversation.ConversationManager.check_and_compress",
        _fake_check_and_compress,
    )

    request = ChatRequest(
        connection_id=connection_id,
        player_name="alice",
        content="user-1",
        conversation_id="default",
    )
    asyncio.run(worker._process_request_locked(request, connection_id))

    metadata = broker.get_conversation_metadata(connection_id, "alice", "default")
    assert metadata.title == "自动标题"
    assert metadata.title_status == "ready"
```

- [ ] **步骤 2：运行标题调度测试验证失败**

运行：`pytest tests/test_queue_context.py::test_worker_generates_title_after_first_completed_turn -v`
预期：FAIL，报错包含 `AttributeError` 或标题仍为 `None`。

- [ ] **步骤 3：扩展标题状态 API**

在 `MessageBroker` 中新增：

```python
def mark_conversation_title_generating(
    self,
    connection_id: UUID,
    player_name: str | None,
    conversation_id: str | None,
) -> bool:
    metadata = self.ensure_conversation_metadata(connection_id, player_name, conversation_id)
    if metadata.title or metadata.title_status == "generating":
        return False
    metadata.title_status = "generating"
    return True


def mark_conversation_title_failed(
    self,
    connection_id: UUID,
    player_name: str | None,
    conversation_id: str | None,
) -> None:
    metadata = self.ensure_conversation_metadata(connection_id, player_name, conversation_id)
    if metadata.title_status == "generating":
        metadata.title_status = "failed"
```

- [ ] **步骤 4：导入标题生成函数**

在 `services/agent/worker.py` 顶部导入：

```python
from services.agent.title import generate_conversation_title
```

- [ ] **步骤 5：实现标题任务方法**

在 `AgentWorker` 类中新增：

```python
async def _generate_title_for_conversation(
    self,
    connection_id: UUID,
    player_name: str | None,
    conversation_id: str,
    first_user_message: str,
    model: object,
) -> None:
    try:
        title = await generate_conversation_title(first_user_message, model)  # type: ignore[arg-type]
        self.broker.set_conversation_title(connection_id, player_name, conversation_id, title)
        logger.info(
            "conversation_title_generated",
            worker_id=self.worker_id,
            connection_id=str(connection_id),
            player=player_name,
            conversation_id=conversation_id,
            title=title,
        )
    except Exception as e:
        self.broker.mark_conversation_title_failed(connection_id, player_name, conversation_id)
        logger.warning(
            "conversation_title_generation_failed",
            worker_id=self.worker_id,
            connection_id=str(connection_id),
            player=player_name,
            conversation_id=conversation_id,
            error=str(e),
        )
```

- [ ] **步骤 6：在首轮完成后调度标题生成**

在 `_process_request_locked()` 中 `if history_updated:` 分支内、自动压缩前加入：

```python
if (
    self._count_user_prompts(trimmed_history) == 1
    and self.broker.mark_conversation_title_generating(
        connection_id,
        request.player_name,
        request.conversation_id,
    )
):
    await self._generate_title_for_conversation(
        connection_id,
        request.player_name,
        request.conversation_id,
        request.content,
        model,
    )
```

在 `AgentWorker` 中新增计数方法：

```python
@staticmethod
def _count_user_prompts(history: list[ModelMessage]) -> int:
    count = 0
    for message in history:
        for part in getattr(message, "parts", []):
            if getattr(part, "part_kind", None) == "user-prompt":
                count += 1
                break
    return count
```

说明：这个步骤先用 `await` 直接调用，保证测试可确定；下一步再改为后台任务并保留可测试性。

- [ ] **步骤 7：运行标题调度测试验证通过**

运行：`pytest tests/test_queue_context.py::test_worker_generates_title_after_first_completed_turn -v`
预期：PASS。

- [ ] **步骤 8：编写失败的非首轮不重复测试**

在 `tests/test_queue_context.py` 追加：

```python
def test_worker_does_not_regenerate_title_after_later_turn(monkeypatch) -> None:
    import asyncio
    from types import SimpleNamespace

    from config.settings import Settings
    from models.messages import ChatRequest

    broker = MessageBroker()
    connection_id = uuid4()
    broker.register_connection(connection_id)
    broker.set_conversation_title(connection_id, "alice", "default", "已有标题")

    worker = AgentWorker(broker=broker, settings=Settings(max_history_turns=5), worker_id=1)
    completed_messages = _build_turn(1) + _build_turn(2)
    called = False

    class _FakeManager:
        def get_agent(self):
            return object()

    async def _fake_stream_chat(*args, **kwargs):
        yield SimpleNamespace(
            event_type="content",
            content="完成",
            metadata={"is_complete": True, "all_messages": completed_messages},
        )

    async def _fake_check_and_compress(*args, **kwargs):
        return False, "not compressed"

    async def _fake_generate_title(*args, **kwargs):
        nonlocal called
        called = True
        return "新标题"

    monkeypatch.setattr("services.agent.worker.stream_chat", _fake_stream_chat)
    monkeypatch.setattr("services.agent.core.get_agent_manager", lambda: _FakeManager())
    monkeypatch.setattr("services.agent.worker.ProviderRegistry.get_model", lambda *_: object())
    monkeypatch.setattr("services.agent.worker.generate_conversation_title", _fake_generate_title)
    monkeypatch.setattr(
        "core.conversation.ConversationManager.check_and_compress",
        _fake_check_and_compress,
    )

    request = ChatRequest(
        connection_id=connection_id,
        player_name="alice",
        content="user-2",
        conversation_id="default",
    )
    asyncio.run(worker._process_request_locked(request, connection_id))

    metadata = broker.get_conversation_metadata(connection_id, "alice", "default")
    assert metadata.title == "已有标题"
    assert called is False
```

- [ ] **步骤 9：运行非首轮测试验证通过**

运行：`pytest tests/test_queue_context.py::test_worker_does_not_regenerate_title_after_later_turn -v`
预期：PASS。

- [ ] **步骤 10：改为真正后台任务**

将步骤 6 中的直接 `await` 替换为：

```python
asyncio.create_task(
    self._generate_title_for_conversation(
        connection_id,
        request.player_name,
        request.conversation_id,
        request.content,
        model,
    )
)
```

为了保持测试确定性，在 `AgentWorker.__init__` 添加可注入开关：

```python
self._title_tasks: set[asyncio.Task] = set()
self.run_title_generation_inline = False
```

新增调度方法：

```python
async def _schedule_title_generation(
    self,
    connection_id: UUID,
    player_name: str | None,
    conversation_id: str,
    first_user_message: str,
    model: object,
) -> None:
    if self.run_title_generation_inline:
        await self._generate_title_for_conversation(
            connection_id, player_name, conversation_id, first_user_message, model
        )
        return
    task = asyncio.create_task(
        self._generate_title_for_conversation(
            connection_id, player_name, conversation_id, first_user_message, model
        )
    )
    self._title_tasks.add(task)
    task.add_done_callback(self._title_tasks.discard)
```

将调度点改成：

```python
await self._schedule_title_generation(
    connection_id,
    request.player_name,
    request.conversation_id,
    request.content,
    model,
)
```

并在两个标题测试中设置：

```python
worker.run_title_generation_inline = True
```

- [ ] **步骤 11：停止 Worker 时取消标题任务**

在 `AgentWorker.stop()` 中 `_http_client.aclose()` 前加入：

```python
for task in list(self._title_tasks):
    task.cancel()
if self._title_tasks:
    await asyncio.gather(*self._title_tasks, return_exceptions=True)
self._title_tasks.clear()
```

- [ ] **步骤 12：运行 Worker 相关测试**

运行：`pytest tests/test_queue_context.py::test_worker_generates_title_after_first_completed_turn tests/test_queue_context.py::test_worker_does_not_regenerate_title_after_later_turn tests/test_queue_context.py::test_worker_persists_completed_history_when_context_disabled -v`
预期：PASS。

- [ ] **步骤 13：Commit**

```bash
git add core/queue.py services/agent/worker.py tests/test_queue_context.py
git commit -m "feat(conversation): generate titles after first turn"
```

---

### 任务 5：持久化保存标题并展示

**文件：**
- 修改：`core/conversation.py:309-593`
- 测试：`tests/test_conversation_manager.py`

- [ ] **步骤 1：更新 MockBroker 支持元数据**

在 `tests/test_conversation_manager.py` 的 `MockBroker.__init__` 中新增：

```python
self._titles = {}
```

在 `MockBroker` 中新增：

```python
def get_conversation_metadata(self, connection_id, player_name=None, conversation_id="default"):
    class Metadata:
        def __init__(self, title):
            self.title = title
            self.short_id = 1
            self.title_status = "ready" if title else "pending"
            self.conversation_id = conversation_id or "default"

    return Metadata(self._titles.get(self._key(connection_id, player_name, conversation_id)))


def set_conversation_title(self, connection_id, player_name, conversation_id, title):
    self._titles[self._key(connection_id, player_name, conversation_id)] = title
```

- [ ] **步骤 2：编写失败的保存标题测试**

在 `tests/test_conversation_manager.py` 追加：

```python
@pytest.mark.asyncio
async def test_save_conversation_persists_runtime_title():
    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)
    connection_id = uuid4()
    player_name = "TestPlayer"

    broker.set_conversation_history(connection_id, player_name, _build_multi_turn(1))
    broker.set_conversation_title(connection_id, player_name, "default", "建筑计划")

    success, session_id = await manager.save_conversation(
        connection_id=connection_id,
        player_name=player_name,
        conversation_id="default",
    )

    assert success is True
    test_file = manager._storage_dir / f"{session_id}.json"
    data = json.loads(test_file.read_text(encoding="utf-8"))
    assert data["title"] == "建筑计划"
    assert data["metadata"]["title"] == "建筑计划"
    test_file.unlink()
```

- [ ] **步骤 3：运行保存标题测试验证失败**

运行：`pytest tests/test_conversation_manager.py::test_save_conversation_persists_runtime_title -v`
预期：FAIL，`title` 字段不存在。

- [ ] **步骤 4：保存对话时写入标题**

在 `save_conversation()` 构建 `saved_data` 前加入：

```python
metadata = self.broker.get_conversation_metadata(connection_id, player_name, conversation_id)
title = metadata.title or "未命名"
```

在 `saved_data` 顶层加入：

```python
"title": title,
```

在 `saved_data["metadata"]` 中加入：

```python
"title": title,
```

- [ ] **步骤 5：运行保存标题测试验证通过**

运行：`pytest tests/test_conversation_manager.py::test_save_conversation_persists_runtime_title -v`
预期：PASS。

- [ ] **步骤 6：编写失败的已保存列表标题测试**

修改 `test_format_conversation_list()` 的 `conversations` 数据，为第一项加入：

```python
"title": "建筑计划",
```

在断言中加入：

```python
assert "建筑计划" in result
```

- [ ] **步骤 7：运行已保存列表标题测试验证失败**

运行：`pytest tests/test_conversation_manager.py::test_format_conversation_list -v`
预期：FAIL，格式化结果没有标题。

- [ ] **步骤 8：列表读取和格式化标题**

在 `list_conversations()` 追加字段：

```python
"title": data.get("title", data.get("metadata", {}).get("title")),
```

在 `format_conversation_list()` 中读取：

```python
title = conv.get("title") or "未命名"
```

将列表行替换为：

```python
lines.append(
    f"{i + 1}. [{session_id}] {title} | {player} | {provider}/{model} | "
    f"对话:{conversation_id} | {count}条消息 | {updated}"
)
```

- [ ] **步骤 9：运行已保存列表标题测试验证通过**

运行：`pytest tests/test_conversation_manager.py::test_format_conversation_list -v`
预期：PASS。

- [ ] **步骤 10：运行对话管理器测试**

运行：`pytest tests/test_conversation_manager.py -v`
预期：PASS。

- [ ] **步骤 11：Commit**

```bash
git add core/conversation.py tests/test_conversation_manager.py
git commit -m "feat(conversation): persist generated titles"
```

---

### 任务 6：整体验证和回归

**文件：**
- 验证：`core/queue.py`
- 验证：`services/websocket/server.py`
- 验证：`services/agent/title.py`
- 验证：`services/agent/worker.py`
- 验证：`core/conversation.py`

- [ ] **步骤 1：运行重点测试**

运行：

```bash
pytest tests/test_queue_context.py tests/test_websocket_conversation_commands.py tests/test_conversation_title.py tests/test_conversation_manager.py -v
```

预期：PASS。

- [ ] **步骤 2：运行完整测试**

运行：`pytest -v`
预期：PASS。若出现与本次改动无关的既有失败，记录失败测试名和失败原因，不修改无关代码。

- [ ] **步骤 3：运行静态检查**

运行：`ruff check .`
预期：PASS。

- [ ] **步骤 4：人工命令验收脚本**

在开发模式启动服务后，于 MCBE 或测试客户端依次执行：

```text
AGENT 对话 list
AGENT 聊天 如何建造自动红石农场
AGENT 对话 list
AGENT 对话 new mining
AGENT 对话 list
AGENT 对话 switch #1
AGENT 对话 switch new-plan
AGENT 对话 list
AGENT 对话 save
AGENT 对话 saved
```

预期：

```text
list 中出现 #1 default
首次聊天后 #1 default 逐步显示生成标题或最终标题
new mining 后出现 #2 mining
switch #1 切回 default
switch new-plan 返回“已创建并切换到新会话: #3 new-plan”
saved 列表显示保存标题
```

- [ ] **步骤 5：检查 git diff**

运行：`git diff --stat` 和 `git diff`
预期：只包含本计划列出的文件；没有密钥、配置明文或无关重构。

- [ ] **步骤 6：最终 Commit**

若前面任务已逐步 commit，跳过此步骤。若未逐步 commit，执行：

```bash
git add core/queue.py services/websocket/server.py services/agent/title.py services/agent/worker.py core/conversation.py tests/test_queue_context.py tests/test_websocket_conversation_commands.py tests/test_conversation_title.py tests/test_conversation_manager.py
git commit -m "feat(conversation): add short ids and generated titles"
```

## 自检结果

- 规格覆盖度：短 ID 显示、短 ID 切换、首次请求异步标题生成、`switch` 新会话提示均有对应任务和测试。
- 占位符扫描：计划没有使用“待定”“后续实现”“添加适当错误处理”等占位表达；每个代码变更步骤给出了具体代码或替换位置。
- 类型一致性：短 ID 和标题都集中在 `ConversationMetadata`；调用点统一使用 `ensure_conversation_metadata()`、`resolve_conversation_short_id()`、`set_conversation_title()`、`get_conversation_metadata()`。
- 范围控制：不改命令配置、不改保存 ID 语义、不引入标题配置项、不把标题生成结果主动推送到游戏聊天流。
