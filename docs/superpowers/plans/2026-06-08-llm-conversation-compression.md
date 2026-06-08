# LLM 对话压缩实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为 MCBE AI Agent 实现完整的“LLM 摘要优先、失败回退本地截断”的对话压缩逻辑。

**架构：** 保持当前 `(connection_id, player_name, conversation_id)` 会话隔离和 Worker 完成后自动触发路径不变。新增独立压缩器，先把旧历史转成安全文本，再使用无工具 PydanticAI Agent 生成结构化摘要，最后把摘要消息与最近 N 轮历史合并写回；任何 LLM 压缩失败都回退到本地摘要，聊天主流程不因压缩失败中断。

**技术栈：** Python 3.11、PydanticAI、Pydantic Settings、pytest、asyncio、现有 MessageBroker/AgentWorker/WebSocketServer。

---

## 文件结构

- 修改：`config/settings.py`
  - 增加压缩相关配置字段、默认值、JSON flatten 映射和运行时必填路径。
- 修改：`config.example.json`
  - 在 `agent` 配置块中加入压缩配置示例。
- 修改：`config.json`
  - 同步加入压缩配置，避免当前运行配置无法通过 `_validate_runtime_config_data()`。
- 修改：`core/conversation.py`
  - 将现有本地截断/摘要升级为 `ConversationCompressor`，保留 `ConversationManager` 对外 API。
  - 增加 LLM 摘要、消息序列化、本地回退、摘要消息构造和压缩统计。
- 修改：`services/agent/worker.py`
  - 保持完成事件后自动压缩入口，只调整日志字段和使用新压缩结果文案。
- 修改：`services/websocket/server.py`
  - 保持 `AGENT 对话 compress` 手动入口，返回 LLM 压缩或回退压缩的明确状态。
- 修改：`tests/test_conversation_manager.py`
  - 覆盖压缩器核心行为、LLM 成功、LLM 失败回退、摘要消息格式、玩家/对话隔离。
- 修改：`tests/test_queue_context.py`
  - 覆盖 Worker 完成后仍按当前玩家/对话触发压缩，并不覆盖过期 generation。
- 修改：`tests/test_json_settings.py`
  - 覆盖新增 JSON 配置项可加载。

## 设计约束

- 压缩必须只作用于当前玩家当前对话桶：`(connection_id, player_name, conversation_id)`。
- 压缩必须在现有 session lock 内执行；不要引入独立后台任务，避免与管理命令并发写历史。
- 压缩用独立无工具 Agent；不要使用主聊天 Agent 的 Minecraft 工具和 MCP toolsets。
- 历史写回必须继续使用 `MessageBroker.set_conversation_history()`，保留 generation stale write 保护。
- LLM 压缩失败、超时、provider 未配置时必须回退到本地摘要；不得让聊天回复失败。
- 摘要消息继续使用 `ModelRequest(UserPromptPart(...))`，便于 PydanticAI `message_history` 继续传递。
- 不存储 chain-of-thought 或 `ThinkingPart.content`；继续复用 Worker 现有 reasoning 清理策略。

---

### 任务 1：添加压缩配置

**文件：**
- 修改：`config/settings.py:201-205`
- 修改：`config/settings.py:397-398`
- 修改：`config/settings.py:500-620`
- 修改：`config.example.json:32-41`
- 修改：`config.json:32-41`
- 测试：`tests/test_json_settings.py`

- [ ] **步骤 1：编写失败的配置测试**

在 `tests/test_json_settings.py` 中新增测试：

```python
def test_agent_compression_settings_loaded_from_json():
    settings = Settings()

    assert settings.compression_enabled is True
    assert settings.compression_trigger_ratio == 0.8
    assert settings.compression_keep_recent_turns == 8
    assert settings.compression_summary_max_chars == 2000
    assert settings.compression_timeout == 30
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
pytest tests/test_json_settings.py::test_agent_compression_settings_loaded_from_json -v
```

预期：FAIL，报错包含 `AttributeError` 或 Pydantic 字段不存在。

- [ ] **步骤 3：扩展 Settings 字段**

在 `config/settings.py` 的 `REQUIRED_CONFIG_PATHS` 中 `agent.max_history_turns` 后加入：

```python
    "agent.compression_enabled",
    "agent.compression_trigger_ratio",
    "agent.compression_keep_recent_turns",
    "agent.compression_summary_max_chars",
    "agent.compression_timeout",
```

在 `_flatten_json_config()` 中保留 `result.update(agent)` 不变，因为新增字段位于 `agent` 下，会自动 flatten 到 Settings。

在 `Settings` 类的 agent 配置字段区域加入：

```python
    # 对话压缩配置
    compression_enabled: bool = True
    compression_trigger_ratio: float = 0.8
    compression_keep_recent_turns: int = 8
    compression_summary_max_chars: int = 2000
    compression_timeout: int = 30
```

- [ ] **步骤 4：更新 JSON 配置示例**

在 `config.example.json` 的 `agent` 块中把：

```json
    "max_history_turns": 20,
```

替换为：

```json
    "max_history_turns": 20,
    "compression_enabled": true,
    "compression_trigger_ratio": 0.8,
    "compression_keep_recent_turns": 8,
    "compression_summary_max_chars": 2000,
    "compression_timeout": 30,
```

在 `config.json` 做同样修改，避免运行时配置校验失败。

- [ ] **步骤 5：运行配置测试验证通过**

运行：

```bash
pytest tests/test_json_settings.py::test_agent_compression_settings_loaded_from_json -v
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add config/settings.py config.example.json config.json tests/test_json_settings.py
git commit -m "feat(config): add conversation compression settings"
```

---

### 任务 2：实现本地压缩边界与摘要载体

**文件：**
- 修改：`core/conversation.py:52-307`
- 测试：`tests/test_conversation_manager.py`

- [ ] **步骤 1：编写失败的边界测试**

在 `tests/test_conversation_manager.py` 中新增：

```python
def test_split_history_for_compression_keeps_recent_turns():
    settings = Settings(compression_keep_recent_turns=3)
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    messages = _build_multi_turn(10)
    older, recent = manager.compressor.split_history(messages)

    assert manager._count_turns(older) == 7
    assert manager._count_turns(recent) == 3
    assert recent[0].parts[0].content == "user-8"
    assert recent[-1].parts[0].content == "assistant-10"
```

再新增摘要消息格式测试：

```python
def test_create_summary_message_uses_history_summary_marker():
    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    message = manager.compressor.create_summary_message("事实: 玩家在建房子")

    assert isinstance(message, ModelRequest)
    assert message.parts[0].part_kind == "user-prompt"
    assert "[历史摘要]" in message.parts[0].content
    assert "事实: 玩家在建房子" in message.parts[0].content
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
pytest tests/test_conversation_manager.py::test_split_history_for_compression_keeps_recent_turns tests/test_conversation_manager.py::test_create_summary_message_uses_history_summary_marker -v
```

预期：FAIL，报错 `ConversationManager` 没有 `compressor`。

- [ ] **步骤 3：新增 CompressionResult 和 ConversationCompressor 骨架**

在 `core/conversation.py` imports 下方加入：

```python
class CompressionResult(BaseModel):
    compressed: bool
    used_llm: bool = False
    fallback_used: bool = False
    original_turns: int = 0
    new_turns: int = 0
    summary: str = ""
    message: str = ""
```

在 `ConversationManager` 前加入：

```python
class ConversationCompressor:
    """对话压缩器：分离历史、生成摘要、构造压缩后的 message_history。"""

    def __init__(self, settings: Settings):
        self.settings = settings

    def count_turns(self, history: list[ModelMessage]) -> int:
        turns = 0
        for message in history:
            for part in getattr(message, "parts", []):
                if getattr(part, "part_kind", None) == "user-prompt":
                    turns += 1
                    break
        return turns

    def split_history(
        self,
        history: list[ModelMessage],
    ) -> tuple[list[ModelMessage], list[ModelMessage]]:
        keep_turns = self.settings.compression_keep_recent_turns
        if keep_turns <= 0:
            return list(history), []

        user_turns = 0
        cut_idx = len(history)
        for idx in range(len(history) - 1, -1, -1):
            message = history[idx]
            if any(
                getattr(part, "part_kind", None) == "user-prompt"
                for part in getattr(message, "parts", [])
            ):
                user_turns += 1
                if user_turns == keep_turns:
                    cut_idx = idx
                    break

        if user_turns < keep_turns:
            return [], list(history)
        return list(history[:cut_idx]), list(history[cut_idx:])

    def create_summary_message(self, summary: str) -> ModelMessage | None:
        if not summary.strip():
            return None

        from pydantic_ai.messages import ModelRequest, UserPromptPart

        return ModelRequest(
            parts=[
                UserPromptPart(
                    content=f"[历史摘要]\n{summary.strip()}",
                    part_kind="user-prompt",
                )
            ]
        )
```

在 `ConversationManager.__init__()` 中加入：

```python
        self.compressor = ConversationCompressor(settings)
```

将 `_count_turns()` 改成委托：

```python
    def _count_turns(self, history: list[ModelMessage]) -> int:
        """计算对话轮次（用户提问次数）"""
        return self.compressor.count_turns(history)
```

将 `_create_summary_message()` 改成委托：

```python
    def _create_summary_message(self, summary: str) -> ModelMessage | None:
        """创建摘要消息"""
        try:
            return self.compressor.create_summary_message(summary)
        except Exception as e:
            logger.warning("create_summary_message_failed", error=str(e))
            return None
```

- [ ] **步骤 4：运行边界测试验证通过**

运行：

```bash
pytest tests/test_conversation_manager.py::test_split_history_for_compression_keeps_recent_turns tests/test_conversation_manager.py::test_create_summary_message_uses_history_summary_marker -v
```

预期：PASS。

- [ ] **步骤 5：运行现有压缩测试防回归**

运行：

```bash
pytest tests/test_conversation_manager.py::test_count_turns tests/test_conversation_manager.py::test_truncate_to_turns tests/test_conversation_manager.py::test_compress_history -v
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add core/conversation.py tests/test_conversation_manager.py
git commit -m "refactor(conversation): introduce compression boundary"
```

---

### 任务 3：实现旧历史文本序列化和本地回退摘要

**文件：**
- 修改：`core/conversation.py`
- 测试：`tests/test_conversation_manager.py`

- [ ] **步骤 1：编写失败的序列化测试**

在 `tests/test_conversation_manager.py` 中新增：

```python
def test_serialize_messages_for_summary_skips_thinking_parts():
    from pydantic_ai.messages import ThinkingPart

    settings = Settings()
    broker = MockBroker()
    manager = ConversationManager(broker, settings)
    messages = [
        ModelRequest(parts=[UserPromptPart(content="玩家想建一个城堡")]),
        ModelResponse(parts=[ThinkingPart(content="hidden"), TextPart(content="建议先规划地基")]),
    ]

    text = manager.compressor.serialize_messages_for_summary(messages)

    assert "用户: 玩家想建一个城堡" in text
    assert "AI: 建议先规划地基" in text
    assert "hidden" not in text
```

新增本地回退测试：

```python
def test_local_summary_is_structured_and_limited():
    settings = Settings(compression_summary_max_chars=120)
    broker = MockBroker()
    manager = ConversationManager(broker, settings)
    messages = _build_multi_turn(6)

    summary = manager.compressor.build_local_summary(messages)

    assert summary.startswith("事实:")
    assert "user-1" in summary
    assert len(summary) <= 120
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
pytest tests/test_conversation_manager.py::test_serialize_messages_for_summary_skips_thinking_parts tests/test_conversation_manager.py::test_local_summary_is_structured_and_limited -v
```

预期：FAIL，报错方法不存在。

- [ ] **步骤 3：实现消息文本提取**

在 `ConversationCompressor` 中加入：

```python
    def serialize_messages_for_summary(self, messages: list[ModelMessage]) -> str:
        lines: list[str] = []
        for message in messages:
            role = self._message_role(message)
            text_parts: list[str] = []
            for part in getattr(message, "parts", []):
                part_kind = getattr(part, "part_kind", None)
                if part_kind == "thinking":
                    continue
                if part_kind in {"user-prompt", "text"}:
                    content = str(getattr(part, "content", "") or "").strip()
                    if content:
                        text_parts.append(re.sub(r"\s+", " ", content))
                elif part_kind == "tool-call":
                    tool_name = getattr(part, "tool_name", "tool")
                    text_parts.append(f"调用工具 {tool_name}")
                elif part_kind == "tool-return":
                    content = str(getattr(part, "content", "") or "").strip()
                    if content:
                        text_parts.append(f"工具返回 {re.sub(r'\s+', ' ', content)[:200]}")
            if text_parts:
                lines.append(f"{role}: {' '.join(text_parts)}")
        return "\n".join(lines)

    @staticmethod
    def _message_role(message: ModelMessage) -> str:
        from pydantic_ai.messages import ModelRequest, ModelResponse

        if isinstance(message, ModelRequest):
            return "用户"
        if isinstance(message, ModelResponse):
            return "AI"
        return "消息"
```

- [ ] **步骤 4：实现本地摘要**

在 `ConversationCompressor` 中加入：

```python
    def build_local_summary(self, messages: list[ModelMessage]) -> str:
        text = self.serialize_messages_for_summary(messages)
        if not text:
            return ""

        max_chars = self.settings.compression_summary_max_chars
        compact = re.sub(r"\s+", " ", text).strip()
        summary = f"事实: 以下为较早对话的本地压缩摘要。{compact}"
        if len(summary) <= max_chars:
            return summary
        return summary[: max(0, max_chars - 3)].rstrip() + "..."
```

- [ ] **步骤 5：运行序列化和本地摘要测试验证通过**

运行：

```bash
pytest tests/test_conversation_manager.py::test_serialize_messages_for_summary_skips_thinking_parts tests/test_conversation_manager.py::test_local_summary_is_structured_and_limited -v
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add core/conversation.py tests/test_conversation_manager.py
git commit -m "feat(conversation): add local compression fallback"
```

---

### 任务 4：实现 LLM 摘要器

**文件：**
- 修改：`core/conversation.py`
- 测试：`tests/test_conversation_manager.py`

- [ ] **步骤 1：编写失败的 LLM 成功测试**

在 `tests/test_conversation_manager.py` 中新增：

```python
@pytest.mark.asyncio
async def test_llm_summary_uses_plain_agent_without_tools(monkeypatch):
    settings = Settings(compression_summary_max_chars=2000)
    broker = MockBroker()
    manager = ConversationManager(broker, settings)
    captured = {}

    class FakeResult:
        output = "事实: 玩家正在建城堡\n偏好: 喜欢石砖"

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            captured["agent_kwargs"] = kwargs

        async def run(self, prompt, model=None):
            captured["prompt"] = prompt
            captured["model"] = model
            return FakeResult()

    monkeypatch.setattr("core.conversation.Agent", FakeAgent)
    monkeypatch.setattr("core.conversation.ProviderRegistry.get_model", lambda config: "fake-model")

    summary = await manager.compressor.build_llm_summary(
        messages=_build_multi_turn(2),
        provider_name="deepseek",
    )

    assert summary == "事实: 玩家正在建城堡\n偏好: 喜欢石砖"
    assert captured["model"] == "fake-model"
    assert "toolsets" not in captured["agent_kwargs"] or captured["agent_kwargs"]["toolsets"] is None
    assert "请总结以下较早的 Minecraft AI 对话" in captured["prompt"]
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
pytest tests/test_conversation_manager.py::test_llm_summary_uses_plain_agent_without_tools -v
```

预期：FAIL，报错 `build_llm_summary` 不存在。

- [ ] **步骤 3：添加 imports**

在 `core/conversation.py` 顶部加入：

```python
from pydantic_ai import Agent

from services.agent.providers import ProviderRegistry
from services.agent.core import _extract_result_output
```

如果出现循环导入，将 `_extract_result_output` 的逻辑在 `ConversationCompressor` 内部实现为私有方法，不从 `services.agent.core` 导入。

- [ ] **步骤 4：实现 LLM 摘要 prompt 和调用**

在 `ConversationCompressor` 中加入：

```python
    async def build_llm_summary(
        self,
        messages: list[ModelMessage],
        provider_name: str | None = None,
    ) -> str:
        source_text = self.serialize_messages_for_summary(messages)
        if not source_text:
            return ""

        provider = provider_name or self.settings.default_provider
        provider_config = self.settings.get_provider_config(provider)
        model = ProviderRegistry.get_model(provider_config)
        agent = Agent(
            "openai:gpt-4o-mini",
            instructions=self._summary_instructions(),
            retries=1,
        )
        prompt = self._summary_prompt(source_text)

        result = await asyncio.wait_for(
            agent.run(prompt, model=model),
            timeout=self.settings.compression_timeout,
        )
        output = self._extract_output_text(result).strip()
        return output[: self.settings.compression_summary_max_chars].strip()

    def _summary_instructions(self) -> str:
        return (
            "你是 Minecraft Bedrock AI 助手的对话压缩器。"
            "只总结对后续回答有用的信息，不要编造。"
            "输出中文，使用以下小标题中实际有内容的项：事实、玩家偏好、未完成任务、重要约束。"
            "不要包含推理过程，不要输出 Markdown 表格。"
        )

    def _summary_prompt(self, source_text: str) -> str:
        max_chars = self.settings.compression_summary_max_chars
        return (
            "请总结以下较早的 Minecraft AI 对话，供后续轮次作为历史摘要使用。\n"
            f"摘要上限 {max_chars} 字。\n\n"
            f"较早对话:\n{source_text}"
        )

    @staticmethod
    def _extract_output_text(result: object) -> str:
        for field_name in ("output", "data"):
            if hasattr(result, field_name):
                value = getattr(result, field_name)
                return "" if value is None else str(value)
        return str(result)
```

注意：`Agent("openai:gpt-4o-mini", ...)` 这里只是初始化占位，实际运行传入 `model=model` 覆盖 provider；不要传入 `deps_type`、`toolsets` 或主聊天工具。

- [ ] **步骤 5：运行 LLM 成功测试验证通过**

运行：

```bash
pytest tests/test_conversation_manager.py::test_llm_summary_uses_plain_agent_without_tools -v
```

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add core/conversation.py tests/test_conversation_manager.py
git commit -m "feat(conversation): summarize old history with llm"
```

---

### 任务 5：接入压缩主流程和回退策略

**文件：**
- 修改：`core/conversation.py:86-197`
- 测试：`tests/test_conversation_manager.py`

- [ ] **步骤 1：编写失败的 LLM 压缩集成测试**

在 `tests/test_conversation_manager.py` 中新增：

```python
@pytest.mark.asyncio
async def test_compress_history_writes_llm_summary_and_recent_turns(monkeypatch):
    settings = Settings(
        max_history_turns=20,
        compression_enabled=True,
        compression_keep_recent_turns=4,
    )
    broker = MockBroker()
    manager = ConversationManager(broker, settings)
    connection_id = uuid4()
    broker.set_conversation_history(connection_id, "alice", _build_multi_turn(12), "build")

    async def fake_llm_summary(messages, provider_name=None):
        return "事实: 玩家在建城堡"

    monkeypatch.setattr(manager.compressor, "build_llm_summary", fake_llm_summary)

    success, msg = await manager.compress_history(
        connection_id,
        "alice",
        conversation_id="build",
        provider_name="deepseek",
    )

    history = broker.get_conversation_history(connection_id, "alice", "build")
    assert success is True
    assert "LLM" in msg
    assert manager._count_turns(history) == 5
    assert "[历史摘要]" in history[0].parts[0].content
    assert "玩家在建城堡" in history[0].parts[0].content
    assert history[1].parts[0].content == "user-9"
```

新增 LLM 失败回退测试：

```python
@pytest.mark.asyncio
async def test_compress_history_falls_back_to_local_summary(monkeypatch):
    settings = Settings(
        max_history_turns=20,
        compression_enabled=True,
        compression_keep_recent_turns=2,
    )
    broker = MockBroker()
    manager = ConversationManager(broker, settings)
    connection_id = uuid4()
    broker.set_conversation_history(connection_id, "alice", _build_multi_turn(8), "build")

    async def failing_llm_summary(messages, provider_name=None):
        raise TimeoutError("summary timeout")

    monkeypatch.setattr(manager.compressor, "build_llm_summary", failing_llm_summary)

    success, msg = await manager.compress_history(
        connection_id,
        "alice",
        conversation_id="build",
        provider_name="deepseek",
    )

    history = broker.get_conversation_history(connection_id, "alice", "build")
    assert success is True
    assert "本地回退" in msg
    assert manager._count_turns(history) == 3
    assert "事实:" in history[0].parts[0].content
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
pytest tests/test_conversation_manager.py::test_compress_history_writes_llm_summary_and_recent_turns tests/test_conversation_manager.py::test_compress_history_falls_back_to_local_summary -v
```

预期：FAIL，当前 `compress_history()` 不接受 `provider_name` 或不调用 LLM。

- [ ] **步骤 3：扩展 `check_and_compress()` 签名**

把 `ConversationManager.check_and_compress()` 签名改为：

```python
    async def check_and_compress(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | bool | None = None,
        force: bool = False,
        provider_name: str | None = None,
    ) -> tuple[bool, str]:
```

在调用 `compress_history()` 时传入 `provider_name=provider_name`。

- [ ] **步骤 4：扩展 `compress_history()` 签名**

把 `ConversationManager.compress_history()` 签名改为：

```python
    async def compress_history(
        self,
        connection_id: UUID,
        player_name: str | None,
        conversation_id: str | bool | None = None,
        force: bool = False,
        provider_name: str | None = None,
    ) -> tuple[bool, str]:
```

- [ ] **步骤 5：实现 LLM 优先、失败回退写回**

替换 `compress_history()` 中从 `# 保留最近 threshold 轮对话` 到 `return True` 的压缩主体为：

```python
        older_messages, recent_messages = self.compressor.split_history(history)
        if not older_messages and not force:
            return False, f"当前 {current_turns} 轮，无需压缩"

        summary = ""
        used_llm = False
        fallback_used = False

        if self.settings.compression_enabled and older_messages:
            try:
                summary = await self.compressor.build_llm_summary(
                    older_messages,
                    provider_name=provider_name,
                )
                used_llm = bool(summary)
            except Exception as e:
                fallback_used = True
                logger.warning(
                    "conversation_llm_compression_failed",
                    connection_id=str(connection_id),
                    player=player_name,
                    conversation_id=conversation_id,
                    error=str(e),
                )

        if older_messages and not summary:
            summary = self.compressor.build_local_summary(older_messages)
            fallback_used = True

        compressed_history = list(recent_messages)
        summary_message = self.compressor.create_summary_message(summary)
        if summary_message:
            compressed_history.insert(0, summary_message)

        self.broker.set_conversation_history(
            connection_id,
            player_name,
            compressed_history,
            conversation_id,
        )

        new_turns = self._count_turns(compressed_history)
        logger.info(
            "conversation_compressed",
            connection_id=str(connection_id),
            player=player_name,
            conversation_id=conversation_id,
            original_turns=current_turns,
            new_turns=new_turns,
            summary_length=len(summary),
            used_llm=used_llm,
            fallback_used=fallback_used,
        )

        mode = "LLM摘要" if used_llm else "本地回退摘要"
        return True, f"压缩完成({mode}): {current_turns}轮 -> {new_turns}轮"
```

- [ ] **步骤 6：删除或保留旧 helper 的兼容层**

保留 `_extract_keywords()`、`_summarize_text()`、`_extract_summary()` 直到所有旧测试改完；把内部实现改为委托本地摘要，避免破坏现有测试：

```python
    def _extract_summary(self, history: list[ModelMessage], keep_turns: int) -> str:
        older, _recent = self.compressor.split_history(history)
        return self.compressor.build_local_summary(older)
```

- [ ] **步骤 7：运行压缩集成测试验证通过**

运行：

```bash
pytest tests/test_conversation_manager.py::test_compress_history_writes_llm_summary_and_recent_turns tests/test_conversation_manager.py::test_compress_history_falls_back_to_local_summary -v
```

预期：PASS。

- [ ] **步骤 8：运行全部对话管理测试**

运行：

```bash
pytest tests/test_conversation_manager.py -v
```

预期：PASS。

- [ ] **步骤 9：Commit**

```bash
git add core/conversation.py tests/test_conversation_manager.py
git commit -m "feat(conversation): compress history with llm fallback"
```

---

### 任务 6：把当前 provider 传入自动和手动压缩

**文件：**
- 修改：`services/agent/worker.py:340-349`
- 修改：`services/websocket/server.py:600-603`
- 测试：`tests/test_queue_context.py`
- 测试：`tests/test_conversation_manager.py`

- [ ] **步骤 1：编写 Worker provider 传递测试**

在 `tests/test_queue_context.py` 的 `test_worker_persists_completed_history_when_context_disabled` 后新增：

```python
def test_worker_passes_request_provider_to_auto_compression(monkeypatch) -> None:
    import asyncio
    from types import SimpleNamespace

    from config.settings import Settings
    from models.messages import ChatRequest

    broker = MessageBroker()
    connection_id = uuid4()
    broker.register_connection(connection_id)
    worker = AgentWorker(broker=broker, settings=Settings(max_history_turns=5), worker_id=1)
    completed_messages = []
    for index in range(1, 8):
        completed_messages.extend(_build_turn(index))
    captured = {}

    class _FakeManager:
        def get_agent(self):
            return object()

    async def _fake_stream_chat(*args, **kwargs):
        yield SimpleNamespace(
            event_type="content",
            content="完成",
            metadata={"is_complete": True, "all_messages": completed_messages},
        )

    async def _fake_check_and_compress(self, *args, **kwargs):
        captured.update(kwargs)
        return False, "not compressed"

    monkeypatch.setattr("services.agent.worker.stream_chat", _fake_stream_chat)
    monkeypatch.setattr("services.agent.core.get_agent_manager", lambda: _FakeManager())
    monkeypatch.setattr("services.agent.worker.ProviderRegistry.get_model", lambda *_: object())
    monkeypatch.setattr(
        "core.conversation.ConversationManager.check_and_compress",
        _fake_check_and_compress,
    )

    request = ChatRequest(
        connection_id=connection_id,
        player_name="alice",
        content="test",
        use_context=True,
        provider="anthropic",
        conversation_id="build",
    )
    asyncio.run(worker._process_request_locked(request, connection_id))

    assert captured["provider_name"] == "anthropic"
    assert captured["conversation_id"] == "build"
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
pytest tests/test_queue_context.py::test_worker_passes_request_provider_to_auto_compression -v
```

预期：FAIL，`provider_name` 未传入。

- [ ] **步骤 3：修改 Worker 自动压缩调用**

在 `services/agent/worker.py` 中把：

```python
                                compressed, msg = await conv_manager.check_and_compress(
                                    connection_id,
                                    request.player_name,
                                    force=False,
                                    conversation_id=request.conversation_id,
                                )
```

替换为：

```python
                                compressed, msg = await conv_manager.check_and_compress(
                                    connection_id,
                                    request.player_name,
                                    force=False,
                                    conversation_id=request.conversation_id,
                                    provider_name=provider_name,
                                )
```

- [ ] **步骤 4：修改手动压缩 provider 传递**

在 `services/websocket/server.py` 中把：

```python
            success, result = await conv_manager.check_and_compress(
                state.id, actor, force=True, conversation_id=conversation_id
            )
```

替换为：

```python
            success, result = await conv_manager.check_and_compress(
                state.id,
                actor,
                force=True,
                conversation_id=conversation_id,
                provider_name=session.current_provider or self.settings.default_provider,
            )
```

- [ ] **步骤 5：运行 Worker 测试验证通过**

运行：

```bash
pytest tests/test_queue_context.py::test_worker_passes_request_provider_to_auto_compression -v
```

预期：PASS。

- [ ] **步骤 6：运行相关回归测试**

运行：

```bash
pytest tests/test_queue_context.py::test_worker_persists_completed_history_when_context_disabled tests/test_conversation_manager.py::test_check_and_compress_force -v
```

预期：PASS。

- [ ] **步骤 7：Commit**

```bash
git add services/agent/worker.py services/websocket/server.py tests/test_queue_context.py
git commit -m "fix(conversation): use active provider for compression"
```

---

### 任务 7：校准触发策略和状态文案

**文件：**
- 修改：`core/conversation.py:82-127`
- 修改：`services/websocket/server.py:581-589`
- 测试：`tests/test_conversation_manager.py`

- [ ] **步骤 1：编写触发阈值配置测试**

在 `tests/test_conversation_manager.py` 中修改 `test_compression_threshold()` 为：

```python
def test_compression_threshold():
    settings = Settings(max_history_turns=20, compression_trigger_ratio=0.8)
    broker = MockBroker()
    manager = ConversationManager(broker, settings)

    assert manager._get_compression_threshold() == 16

    settings = Settings(max_history_turns=10, compression_trigger_ratio=0.6)
    manager = ConversationManager(broker, settings)
    assert manager._get_compression_threshold() == 6
```

新增禁用自动压缩测试：

```python
@pytest.mark.asyncio
async def test_check_and_compress_disabled_without_force():
    settings = Settings(compression_enabled=False)
    broker = MockBroker()
    manager = ConversationManager(broker, settings)
    connection_id = uuid4()
    broker.set_conversation_history(connection_id, _build_multi_turn(25))

    success, msg = await manager.check_and_compress(connection_id, None, force=False)

    assert success is False
    assert "已禁用" in msg
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
pytest tests/test_conversation_manager.py::test_compression_threshold tests/test_conversation_manager.py::test_check_and_compress_disabled_without_force -v
```

预期：FAIL，阈值仍使用常量或禁用逻辑不存在。

- [ ] **步骤 3：使用配置化阈值**

在 `core/conversation.py` 中把：

```python
        return int(self.settings.max_history_turns * self.COMPRESSION_THRESHOLD_RATIO)
```

替换为：

```python
        return max(1, int(self.settings.max_history_turns * self.settings.compression_trigger_ratio))
```

- [ ] **步骤 4：实现禁用自动压缩但允许手动压缩**

在 `check_and_compress()` 获取 history 后加入：

```python
        if not self.settings.compression_enabled and not force:
            return False, "对话自动压缩已禁用"
```

保持 `force=True` 仍可手动压缩，便于玩家执行 `AGENT 对话 compress`。

- [ ] **步骤 5：增强状态文案**

在 `services/websocket/server.py` 的 conversation status 返回中，把：

```python
                f"\n对话轮数: {turns}/{self.settings.max_history_turns}"
                f"\n上下文携带: {context_status}"
```

替换为：

```python
                f"\n对话轮数: {turns}/{self.settings.max_history_turns}"
                f"\n压缩触发: {self._conversation_compression_status_text()}"
                f"\n上下文携带: {context_status}"
```

在 `WebSocketServer` 类中新增 helper：

```python
    def _conversation_compression_status_text(self) -> str:
        if not self.settings.compression_enabled:
            return "关闭"
        threshold = max(
            1,
            int(self.settings.max_history_turns * self.settings.compression_trigger_ratio),
        )
        return f"{threshold}轮，保留最近{self.settings.compression_keep_recent_turns}轮"
```

- [ ] **步骤 6：运行触发策略测试验证通过**

运行：

```bash
pytest tests/test_conversation_manager.py::test_compression_threshold tests/test_conversation_manager.py::test_check_and_compress_disabled_without_force -v
```

预期：PASS。

- [ ] **步骤 7：Commit**

```bash
git add core/conversation.py services/websocket/server.py tests/test_conversation_manager.py
git commit -m "feat(conversation): configure compression trigger policy"
```

---

### 任务 8：补齐多人隔离和保存恢复回归

**文件：**
- 修改：`tests/test_conversation_manager.py`
- 修改：`tests/test_queue_context.py`

- [ ] **步骤 1：编写玩家/对话隔离压缩测试**

在 `tests/test_conversation_manager.py` 中新增：

```python
@pytest.mark.asyncio
async def test_compression_only_updates_target_player_conversation(monkeypatch):
    settings = Settings(compression_keep_recent_turns=2)
    broker = MockBroker()
    manager = ConversationManager(broker, settings)
    connection_id = uuid4()
    broker.set_conversation_history(connection_id, "alice", _build_multi_turn(8), "build")
    broker.set_conversation_history(connection_id, "bob", _build_multi_turn(4), "build")
    broker.set_conversation_history(connection_id, "alice", _build_multi_turn(5), "redstone")

    async def fake_llm_summary(messages, provider_name=None):
        return "事实: alice build 被压缩"

    monkeypatch.setattr(manager.compressor, "build_llm_summary", fake_llm_summary)

    success, _msg = await manager.compress_history(
        connection_id,
        "alice",
        conversation_id="build",
        provider_name="deepseek",
    )

    assert success is True
    assert "alice build 被压缩" in broker.get_conversation_history(connection_id, "alice", "build")[0].parts[0].content
    assert broker.get_conversation_history(connection_id, "bob", "build")[0].parts[0].content == "user-1"
    assert broker.get_conversation_history(connection_id, "alice", "redstone")[0].parts[0].content == "user-1"
```

- [ ] **步骤 2：编写保存恢复摘要测试**

在 `tests/test_conversation_manager.py` 中新增：

```python
@pytest.mark.asyncio
async def test_save_and_restore_compressed_summary_message(monkeypatch):
    settings = Settings(compression_keep_recent_turns=2)
    broker = MockBroker()
    manager = ConversationManager(broker, settings)
    connection_id = uuid4()
    player_name = "Alice"
    broker.set_conversation_history(connection_id, player_name, _build_multi_turn(6), "build")

    async def fake_llm_summary(messages, provider_name=None):
        return "事实: 保存前已压缩"

    monkeypatch.setattr(manager.compressor, "build_llm_summary", fake_llm_summary)
    await manager.compress_history(connection_id, player_name, conversation_id="build")

    success, session_id = await manager.save_conversation(
        connection_id=connection_id,
        player_name=player_name,
        conversation_id="build",
    )
    assert success is True

    broker.clear_conversation_history(connection_id, player_name, "build")
    success, msg = await manager.restore_conversation(
        connection_id,
        session_id,
        player_name=player_name,
        conversation_id="build",
    )

    restored = broker.get_conversation_history(connection_id, player_name, "build")
    assert success is True
    assert "已恢复" in msg
    assert "保存前已压缩" in restored[0].parts[0].content

    test_file = manager._storage_dir / f"{session_id}.json"
    if test_file.exists():
        test_file.unlink()
```

- [ ] **步骤 3：运行隔离和保存恢复测试**

运行：

```bash
pytest tests/test_conversation_manager.py::test_compression_only_updates_target_player_conversation tests/test_conversation_manager.py::test_save_and_restore_compressed_summary_message -v
```

预期：PASS。

- [ ] **步骤 4：运行相关会话隔离回归**

运行：

```bash
pytest tests/test_queue_context.py::test_message_broker_history_per_player_isolation tests/test_queue_context.py::test_message_broker_history_per_conversation_isolation tests/test_queue_context.py::test_conversation_generation_blocks_stale_history_write -v
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add tests/test_conversation_manager.py tests/test_queue_context.py
git commit -m "test(conversation): cover compression isolation"
```

---

### 任务 9：清理旧测试和兼容 API

**文件：**
- 修改：`tests/test_conversation_manager.py`
- 修改：`core/conversation.py`

- [ ] **步骤 1：更新旧摘要测试预期**

把 `test_extract_keywords()` 和 `test_summarize_text()` 保留为旧 helper 测试，或删除这两个测试。如果保留，确保 helper 行为仍为简单截断。

把 `test_extract_summary()` 改为验证结构化本地摘要：

```python
def test_extract_summary():
    settings = Settings(compression_keep_recent_turns=2)
    broker = MockBroker()
    manager = ConversationManager(broker, settings)
    messages = _build_multi_turn(3)

    summary = manager._extract_summary(messages, keep_turns=2)

    assert summary.startswith("事实:")
    assert "user-1" in summary or "assistant-1" in summary
```

- [ ] **步骤 2：运行完整 conversation 测试**

运行：

```bash
pytest tests/test_conversation_manager.py -v
```

预期：PASS。

- [ ] **步骤 3：移除未使用 import 和变量**

检查 `core/conversation.py`：

```bash
python -m compileall core/conversation.py
```

预期：输出包含 `Compiling 'core/conversation.py'...` 或无错误。

删除 `max_turns = self.settings.max_history_turns` 这类不再使用的局部变量。

- [ ] **步骤 4：运行静态语法检查**

运行：

```bash
python -m compileall core services tests
```

预期：无 `SyntaxError`。

- [ ] **步骤 5：Commit**

```bash
git add core/conversation.py tests/test_conversation_manager.py
git commit -m "test(conversation): update compression expectations"
```

---

### 任务 10：端到端验证

**文件：**
- 不修改代码

- [ ] **步骤 1：运行压缩相关单元测试**

运行：

```bash
pytest tests/test_conversation_manager.py tests/test_queue_context.py tests/test_json_settings.py -v
```

预期：PASS。

- [ ] **步骤 2：运行全量测试**

运行：

```bash
pytest -v
```

预期：PASS。若在线集成测试因未设置 API Key 被 skip，记录 skip 原因；不要为通过测试而改生产代码。

- [ ] **步骤 3：运行应用语法检查**

运行：

```bash
python -m compileall config core services models tests
```

预期：无 `SyntaxError`。

- [ ] **步骤 4：手动命令验证**

启动服务：

```bash
python cli.py serve
```

在 Minecraft 或 WebSocket 测试客户端中执行：

```text
AGENT 对话 new compression-test
AGENT 聊天 请记住我正在建一个石砖城堡，目标是做成中世纪风格
AGENT 聊天 入口要有吊桥，内部要分成仓库和卧室
AGENT 对话 compress
AGENT 对话 status
```

预期：

```text
压缩完成(LLM摘要): ...
当前对话: compression-test
压缩触发: ...
```

如果没有配置可用 API Key，预期手动压缩返回：

```text
压缩完成(本地回退摘要): ...
```

- [ ] **步骤 5：检查日志**

确认日志中出现以下字段：

```text
conversation_compressed
used_llm=True
fallback_used=False
```

或在 API 不可用时出现：

```text
conversation_llm_compression_failed
conversation_compressed
fallback_used=True
```

- [ ] **步骤 6：最终 Commit**

```bash
git status --short
git add config/settings.py config.example.json config.json core/conversation.py services/agent/worker.py services/websocket/server.py tests/test_conversation_manager.py tests/test_queue_context.py tests/test_json_settings.py
git commit -m "feat(conversation): add llm-backed history compression"
```

---

## 实施注意事项

- 如果 `Agent("openai:gpt-4o-mini", ...)` 初始化在当前 PydanticAI 版本要求可用默认模型，可改为 `Agent(output_type=str, instructions=...)` 支持的最小构造方式；实现前用当前依赖版本验证构造签名。
- 如果 `ToolReturnPart` 的 `part_kind` 在当前版本不是 `tool-return`，以测试中的实际对象为准扩展 `serialize_messages_for_summary()`，但不要把完整大工具返回无限制塞进摘要 prompt。
- 压缩后的轮次会包含摘要消息本身，因为摘要消息是 `user-prompt`；因此保留 4 轮最近历史时 `_count_turns()` 结果是 5。
- `compression_keep_recent_turns` 不应等于 `max_history_turns`，否则压缩收益很小；默认 8 是为了在 `max_history_turns=20` 时显著降低历史长度。
- 不新增 `.env` 配置；普通配置只进入 `config.json`。
- 不新增后台任务、数据库表或持久化摘要索引；当前需求只需要运行时历史压缩。

## 规格自检

- 规格覆盖：已覆盖配置、LLM 摘要、本地回退、Worker 自动触发、手动命令、多人/多对话隔离、保存恢复和验证。
- 占位符扫描：无“待定”、“TODO”、“后续实现”或“添加适当错误处理”等占位描述。
- 类型一致性：统一使用 `ConversationCompressor`、`build_llm_summary()`、`build_local_summary()`、`serialize_messages_for_summary()`、`provider_name`。
- 范围控制：未引入后台队列、数据库、可视化 UI 或复杂 token 计数器；只实现当前完整压缩逻辑。
