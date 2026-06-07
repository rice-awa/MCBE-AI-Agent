# codex/refactor-agent-dialogue-system-management 代码审查报告

审查分支：`codex/refactor-agent-dialogue-system-management`

对比基线：`origin/beta...HEAD`

审查时间：2026-06-07

## 总体结论

本分支把上下文开关与对话管理拆开，并引入 `conversation_id` 维度，方向是合理的；对话历史按 `(connection_id, player_name, conversation_id)` 隔离也符合多对话目标。

但当前实现存在 5 个合并前需要处理的问题：

1. 同一玩家跨对话请求被允许并发处理。
2. 关闭上下文后，新消息不再写入当前对话。
3. 已有 `config.json` 用户无法使用新增的 `AGENT 对话` 命令。
4. `new <已存在 ID>` 会静默清空原对话。
5. 对话管理命令会与正在运行的请求发生状态覆盖竞态。

## 审查发现

### [必须修复] 同一玩家不同对话被允许并发处理，和现有会话串行约定冲突

位置：

- `core/queue.py:220-228`
- `services/agent/worker.py:127-132`
- `tests/test_queue_context.py:105-113`

问题说明：

`MessageBroker.get_session_lock()` 现在把 `conversation_id` 纳入锁键，`AgentWorker` 也按 `(connection_id, player_name, conversation_id)` 加锁。这意味着同一玩家在不同对话中提交的请求会被多个 Worker 并发处理。

这和仓库说明中的“同玩家串行，不同玩家可并行”不一致，也和 `worker.py:127` 的注释不一致。风险点不只是上下文历史：同一玩家的下行 `tellraw`、Addon UI 同步、工具调用、`run_command` 都仍然共享同一连接/玩家通道。并发执行时响应和工具副作用可能交错，用户在游戏内看到的输出顺序也可能混乱。

建议：

- 对话历史继续按 `(connection_id, player_name, conversation_id)` 分桶。
- 会话锁恢复为 `(connection_id, player_name)` 维度，或者单独新增 player 级锁方法供 Worker 使用。
- 移除或改写 `test_session_lock_per_conversation()`，补充“同一玩家跨 conversation 仍拿同一把锁”的测试。

### [必须修复] 新增 `AGENT 对话` 命令对已有 `config.json` 没有升级兼容

位置：

- `config/settings.py:50-60`
- `config/settings.py:385-386`
- `services/websocket/server.py:506-510`
- `services/websocket/server.py:514-630`

问题说明：

运行时配置要求用户有本地 `config.json`。`_flatten_json_config()` 在检测到 `minecraft` 配置后，会把整个 `minecraft` 对象直接交给 `Settings`，不会把新增默认命令合并进已有配置。

本分支同时把 `AGENT 上下文` 缩小为只处理开关/状态，并提示用户“对话的新建/切换/清除/保存/恢复请使用 `AGENT 对话`”。但老用户的 `config.json` 大概率没有 `AGENT 对话` 这条命令，结果是旧命令不再工作，新命令也无法被解析。

建议：

- 在配置加载时把默认命令表和用户命令表做按 key 合并，至少保证新增内置命令可用。
- 或提供配置迁移逻辑，启动时检测缺失的内置命令并给出明确修复提示。
- 为平滑升级，建议 `AGENT 上下文 清除/压缩/保存/恢复/列表/删除` 保留兼容转发一段时间。
- 补充一个测试：使用旧版 `minecraft.commands` 配置时，`AGENT 对话 list` 仍可解析，或启动时明确报出需要迁移配置。

### [必须修复] 关闭上下文后，新消息不会写入当前对话

位置：

- `services/agent/worker.py:150-155`
- `services/agent/worker.py:302-318`
- `services/websocket/server.py:467-474`

问题说明：

本分支把 `AGENT 上下文` 定义为“只负责是否在请求中携带当前对话历史”，关闭时还明确提示“现有对话历史不会被清除”。但 Worker 使用同一个 `request.use_context` 同时控制历史读取和历史写回：

- `False` 时不读取已有历史，这是预期行为。
- `False` 时也不保存本轮用户消息与 AI 回复，这和“对话管理与上下文携带解耦”的目标不一致。

实际结果是：用户关闭上下文后连续聊若干轮，再重新启用上下文，这些轮次完全不会出现在当前对话中，也无法保存或恢复。

建议：

- `use_context` 只控制传给模型的 `message_history`。
- 无论是否携带旧历史，都应把本轮 `new_messages` 合并或写入当前 `conversation_id`。
- 补充“关闭上下文聊天后，当前对话仍记录本轮消息；重新启用后可以读取”的 Worker 测试。

### [必须修复] 新建已存在的对话 ID 会静默清空历史

位置：

- `services/websocket/server.py:529-533`

问题说明：

`AGENT 对话 new <ID>` 不检查 ID 是否已经存在，直接执行：

```python
self.broker.set_conversation_history(state.id, actor, [], new_id)
```

只要用户误用 `new` 而不是 `switch`，同名对话的历史就会被立即覆盖为空，并且没有二次确认或恢复入口。这属于直接的数据丢失风险。

建议：

- 新建前区分“桶不存在”和“桶存在但为空”。
- 已存在时拒绝覆盖，并提示使用 `switch <ID>`。
- 如确实需要覆盖，提供显式 `reset/clear` 命令。
- 补充同名新建不覆盖历史的回归测试。

### [必须修复] 对话管理命令未与 Worker 串行，成功状态可能被旧请求覆盖

位置：

- `services/websocket/server.py:514-630`
- `services/websocket/server.py:650-661`
- `services/agent/worker.py:127-132`
- `services/agent/worker.py:302-318`

问题说明：

聊天请求提交到队列后，WebSocket Handler 会立即返回。用户可以在 LLM 仍运行时继续执行 `clear`、`restore`、`compress` 或 `切换模型`，但这些管理命令没有获取 Worker 使用的会话锁。

可复现场景：

1. 用户在对话 `default` 发起一次耗时聊天。
2. 请求尚未完成时执行 `AGENT 对话 clear`。
3. 命令提示“历史已清除”。
4. 旧请求完成后，Worker 在 `set_conversation_history()` 中把包含旧请求的历史重新写回 `default`。

`restore`、`compress` 和切换模型后的清理也存在类似覆盖窗口。最终状态取决于异步任务完成顺序，而不是用户最后执行的命令。

建议：

- 所有会修改当前玩家对话状态的命令与 Worker 共用同一把玩家级锁。
- 锁内重新读取活动 `conversation_id` 和目标历史，避免使用等待锁之前取得的过期状态。
- 或为对话桶维护版本号，Worker 写回前校验版本，检测到 `clear/restore` 后放弃过期写入。
- 补充“请求运行中 clear/restore/切换模型，旧请求不得覆盖新状态”的异步测试。

### [建议修改] 自动生成对话 ID 使用秒级时间戳，短时间连续新建可能覆盖同一桶

位置：

- `services/websocket/server.py:529-533`
- `services/websocket/server.py:632-637`

问题说明：

`_generate_conversation_id()` 使用 `chat-%Y%m%d-%H%M%S`。同一玩家在一秒内连续执行 `AGENT 对话 new` 会生成相同 ID，并且 `new` 会立即 `set_conversation_history(..., [])`。如果该 ID 已存在，会静默清空已有历史。

建议：

- 自动 ID 加入短 UUID 或毫秒/纳秒级随机后缀。
- 自动生成后仍应执行存在性检查，避免时钟回拨或并发调用造成冲突。

### [建议修改] 新增可选参数插入原参数之前，破坏位置参数兼容性

位置：

- `core/conversation.py:86-126`

问题说明：

原签名为：

```python
check_and_compress(connection_id, player_name, force=False)
compress_history(connection_id, player_name, force=False)
```

本分支把 `conversation_id` 插入到 `force` 之前。仓库内部大多使用关键字参数，所以当前测试未暴露问题；但任何已有调用：

```python
check_and_compress(connection_id, player_name, True)
```

升级后都会把 `True` 当成 `conversation_id`，随后在 `normalize_conversation_id()` 调用 `.strip()` 时触发异常。

建议：

- 保留旧参数顺序，把 `conversation_id` 放到 `force` 后面。
- 或将新增参数设计为仅限关键字参数，并为旧位置调用补充兼容测试。

### [建议修改] 切换模型只清理当前对话，其他对话会继续沿用旧历史

位置：

- `services/websocket/server.py:650-661`

问题说明：

模型/provider 是玩家级状态，但对话历史已经变为玩家内多桶。当前 `handle_switch_model()` 只清理当前活动对话。用户切换模型后再切回其他旧对话，会把旧 provider 下产生的历史继续带给新 provider。

如果这是预期行为，建议在命令反馈和文档中说明；如果不是，建议切换模型时清理该玩家下所有运行时对话，或者提示用户选择“仅清理当前对话/清理全部对话”。

## 既有问题

以下问题在 `origin/beta` 已存在，不属于本分支新引入，但会影响新对话管理功能的完整可用性。

### [建议修改] 保存会话 ID 使用秒级时间戳，同一秒保存会覆盖文件

位置：

- `core/conversation.py:331-358`

`session_id` 只由 `connection_id + 秒级时间戳` 构成。同一连接在一秒内保存两次，无论是否来自不同 `conversation_id`，都会写入同一个 JSON 文件，后一次保存覆盖前一次。

建议加入 UUID/纳秒后缀，或使用排他创建并在冲突时重试。

### [建议修改] 保存列表展示的短 ID 无法直接用于恢复或删除

位置：

- `core/conversation.py:554-579`
- `services/websocket/server.py:596-623`

列表只展示 `session_id.split("_")[1]`，而 `restore/delete` 要求完整文件名 ID。用户无法根据游戏内列表完成恢复或删除。仓库已有文档 `claude_md/optimization/AGENT命令用户体验优化方案.md` 也记录了这个问题。

建议支持列表序号或稳定短 ID，并让 `restore/delete` 使用同一标识解析。

## 测试覆盖缺口

当前新增测试主要覆盖 `MessageBroker` 的分桶和锁对象，没有覆盖新增命令的端到端行为。建议至少补充：

- `AGENT 对话 new/switch/clear/list/status` 的玩家隔离测试。
- 同一玩家跨对话请求仍按产品约定串行的测试。
- 上下文关闭时不读取旧历史，但仍记录新消息的测试。
- 同名 `new` 不覆盖已有历史的测试。
- 请求运行期间执行 `clear/restore/compress/切换模型` 的竞态测试。
- 旧版 `config.json` 升级后的命令解析测试。
- 不同玩家不能误恢复或删除其他玩家保存会话的权限策略测试。

## 验证结果

### 定向测试

已执行：

```bash
git diff --check origin/beta...HEAD
python3 -m compileall config core models services tests
.venv/bin/pytest -q \
  tests/test_conversation_manager.py \
  tests/test_queue_context.py \
  tests/test_json_settings.py \
  tests/test_connection_manager.py
```

结果：

- `git diff --check` 通过，未发现空白错误。
- `python3 -m compileall ...` 通过，变更文件无语法错误。
- 变更相关测试通过：`37 passed`。

### 完整测试

```bash
.venv/bin/pytest -q
```

结果：`160 passed, 9 failed, 2 skipped`。

9 个失败全部位于 `tests/test_stream_mode.py`，根因是测试替换 `core.chat_agent` 后，`ChatAgentManager` 仍尝试创建泛型 `Agent`，报错：

```text
TypeError: type 'MockAgent' is not subscriptable
```

在 `origin/beta` 基线工作树、同一虚拟环境和同一 `config.json` 下重跑 `tests/test_stream_mode.py`，结果同样为 `9 failed, 1 passed`。因此这 9 个失败属于基线既有问题，不是本分支引入的回归。

### 静态检查

未执行 `ruff` 和 `mypy`：当前 `.venv` 未安装这两个命令。

## 建议合并前检查项

- 修复 Worker 锁粒度和上下文关闭后的历史写回语义。
- 阻止 `new <已存在 ID>` 静默覆盖。
- 补齐旧 `config.json` 的命令迁移或默认合并逻辑。
- 补充聊天框命令和 Addon UI 入口的多玩家、多对话行为测试。
- 修复或隔离基线 `tests/test_stream_mode.py` 的 9 个失败后，再以全量测试通过作为合并门槛。
