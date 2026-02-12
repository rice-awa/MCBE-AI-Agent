# 多轮工具调用问题分析与解决方案

## 1. 问题描述

### 1.1 现象

在使用 DeepSeek 模型（特别是 `deepseek-chat` 和 `deepseek-reasoner`）进行流式聊天时，发现以下问题：

- **工具调用链中断**：模型返回了 `tool-call` 消息后，没有继续执行工具链的后续步骤
- **响应不完整**：用户请求执行 Minecraft 命令后，模型只返回了调用工具的消息，但没有返回工具执行后的最终结果
- **多轮对话失败**：在多轮对话中，第二轮及以后的工具调用无法正常工作

### 1.2 触发场景

1. **流式模式下的工具调用**：当 `stream_sentence_mode=True` 时，使用 `run_stream` 方法处理包含工具调用的对话
2. **多轮对话**：第一轮对话成功执行工具后，第二轮对话继续使用历史消息时出现问题
3. **复杂指令**：需要多个工具协作完成的复杂任务

### 1.3 错误日志示例

```
tool_chain_incomplete_on_stream
connection_id=xxx
tool_calls=1
tool_returns=0
attempt=1
```

## 2. 根本原因分析

### 2.1 代码层面的问题

#### 2.1.1 流式模式的局限性

在 `services/agent/core.py` 的 `stream_chat` 函数中，流式模式使用 `run_stream` 方法：

```python
# 流式模式：使用 run_stream 进行流式处理
async with chat_agent.run_stream(
    prompt,
    deps=deps,
    model=model,
    message_history=message_history,
) as result:
    async for chunk in result.stream_text(delta=True):
        # 处理流式文本...
```

**问题**：`run_stream` 在处理工具调用时，会返回 `tool-call` 消息，但不会自动等待工具执行完成并继续对话。这导致工具链在 `tool-call` 阶段就中断了。

#### 2.1.2 工具链完整性检查

代码中通过 `_count_tool_parts` 函数检查工具链的完整性：

```python
def _count_tool_parts(messages: list[ModelMessage]) -> tuple[int, int]:
    """统计消息链路中的 tool-call / tool-return 数量。"""
    tool_calls = 0
    tool_returns = 0

    for message in messages:
        parts = getattr(message, "parts", None)
        if not parts:
            continue

        for part in parts:
            part_kind = getattr(part, "part_kind", None)
            if part_kind == "tool-call":
                tool_calls += 1
            elif part_kind == "tool-return":
                tool_returns += 1

    return tool_calls, tool_returns
```

当 `tool_calls > tool_returns` 时，说明工具链不完整。

#### 2.1.3 回退机制的设计

发现问题后，代码实现了回退机制（fallback）：

```python
# DeepSeek 流式场景下可能出现"返回了 tool_call，但未继续执行工具链"的情况。
# 发现 tool_call/return 数不一致时，回退到非流式 run，确保链路完整：
if tool_calls > tool_returns:
    fallback_used = True
    while attempt < MAX_TOOL_FALLBACK_ATTEMPTS and tool_calls > tool_returns:
        attempt += 1
        fallback_result = await chat_agent.run(...)
        # 继续处理...
```

### 2.2 DeepSeek 模型的特性

DeepSeek 模型在流式输出时，对工具调用的处理方式与其他模型（如 OpenAI）有所不同：

1. **异步工具执行**：DeepSeek 的 `run_stream` 不会自动等待工具执行结果
2. **消息状态不一致**：流式返回的消息中可能包含 `tool-call`，但缺少对应的 `tool-return`
3. **多轮对话状态丢失**：在多轮对话中，历史消息的状态可能没有正确传递

### 2.3 问题流程图

```
用户请求
    |
    v
+---------------+
| 流式模式运行  |<-- run_stream()
+---------------+
    |
    v
+---------------+
| 返回 tool-call|
+---------------+
    |
    v
+---------------+
| 工具执行？    |<-- 应该自动执行，但未执行
+---------------+
    |
    v
+---------------+
| 缺少 tool-return|
+---------------+
    |
    v
+---------------+
| 检测到不完整  |<-- tool_calls > tool_returns
+---------------+
    |
    v
+---------------+
| 触发回退机制  |<-- 使用 run() 重新执行
+---------------+
```

## 3. 解决方案

### 3.1 核心修复策略

#### 3.1.1 工具链完整性检测

在 `stream_chat` 函数中，在流式处理完成后立即检测工具链状态：

```python
tool_calls, tool_returns = _count_tool_parts(all_messages)

# DeepSeek 流式场景下可能出现"返回了 tool_call，但未继续执行工具链"的情况。
# 发现 tool_call/return 数不一致时，回退到非流式 run，确保链路完整：
if tool_calls > tool_returns:
    fallback_used = True
    # ... 回退处理
```

#### 3.1.2 非流式回退机制

当检测到工具链不完整时，使用非流式的 `run` 方法重新执行：

```python
fallback_result = await chat_agent.run(
    fallback_prompt,
    deps=deps,
    model=model,
    message_history=fallback_history,
)
```

非流式 `run` 方法会：
1. 正确执行工具调用
2. 等待工具返回结果
3. 继续对话生成最终响应

#### 3.1.3 增量内容发送

为了避免重复发送已发送的内容，实现了增量内容提取：

```python
def _diff_fallback_text(fallback_text: str, sent_text: str) -> str:
    if not sent_text:
        return fallback_text
    if fallback_text.startswith(sent_text):
        return fallback_text[len(sent_text):]
    return ""
```

#### 3.1.4 重试机制

实现最多 3 次重试，确保工具链最终完成：

```python
MAX_TOOL_FALLBACK_ATTEMPTS = 3
TOOL_CHAIN_CONTINUE_PROMPT = "请继续完成工具调用链并给出最终回复。"

while attempt < MAX_TOOL_FALLBACK_ATTEMPTS and tool_calls > tool_returns:
    attempt += 1
    # 执行回退...
    if tool_calls <= tool_returns:
        break
    fallback_prompt = TOOL_CHAIN_CONTINUE_PROMPT
    fallback_history = all_messages
```

### 3.2 代码实现

完整的修复代码位于 `services/agent/core.py:180-393`：

```python
async def stream_chat(
    prompt: str,
    deps: AgentDependencies,
    model: Model | str | None = None,
    message_history: list[ModelMessage] | None = None,
) -> AsyncIterator[StreamEvent]:
    """
    流式聊天处理

    Args:
        prompt: 用户输入
        deps: Agent 依赖
        model: 可选的模型覆盖

    Yields:
        StreamEvent: 流式事件
    """
    sequence = 0
    use_stream_mode = deps.settings.stream_sentence_mode
    # ... 初始化变量

    try:
        if use_stream_mode:
            # 流式模式处理
            async with chat_agent.run_stream(...) as result:
                # 处理流式文本...
                all_messages = _extract_all_messages(result)
        else:
            # 非流式模式处理
            result = await chat_agent.run(...)
            all_messages = _extract_all_messages(result)

        tool_calls, tool_returns = _count_tool_parts(all_messages)

        # 工具链不完整时触发回退
        if tool_calls > tool_returns:
            fallback_used = True
            # ... 回退逻辑

        # 发送完成事件
        yield StreamEvent(
            event_type="content",
            content="",
            sequence=sequence,
            metadata={
                "is_complete": True,
                "usage": serialized_usage,
                "all_messages": all_messages,
                "tool_calls": tool_calls,
                "tool_returns": tool_returns,
                "tool_fallback_used": fallback_used,
            },
        )
```

## 4. 测试结果

### 4.1 单元测试

创建了 `tests/test_stream_mode.py` 进行单元测试：

```python
def test_tool_chain_incomplete_should_trigger_fallback_run(monkeypatch) -> None:
    """测试工具链不完整时触发回退机制"""
    fake_agent = _FakeAgent(
        chunks=["我来给你钻石。"],
        messages=[_fake_tool_call_message()],
        fallback_output="已执行命令: /give @p diamond",
        fallback_messages=[
            _fake_tool_call_message(),
            _fake_tool_return_message(),
        ],
    )
    monkeypatch.setattr(core, "chat_agent", fake_agent)

    events = asyncio.run(_collect_events("hi", _build_deps(False), model="fake"))

    assert fake_agent.run_called is True
    assert events[-1].metadata.get("tool_fallback_used") is True
```

### 4.2 集成测试

创建了 `tests/test_agent_multi_tool_live.py` 进行在线集成测试：

```python
def test_deepseek_chat_should_support_multi_turn_tool_chain() -> None:
    """测试 DeepSeek Chat 模型支持多轮工具链"""
    metadata_list, _commands = asyncio.run(_run_live_multi_turn_tool_chain("deepseek-chat"))

    assert len(metadata_list) == 2
    assert metadata_list[0].get("is_complete") is True
    assert metadata_list[0].get("tool_calls", 0) >= 1
    assert metadata_list[0].get("tool_returns", 0) >= 1
    assert metadata_list[1].get("tool_calls", 0) >= 1
    assert metadata_list[1].get("tool_returns", 0) >= 1
```

### 4.3 测试覆盖

| 测试场景 | 测试文件 | 结果 |
|---------|---------|------|
| 流式模式按句子分割 | `test_stream_mode.py` | 通过 |
| 非流式模式批量处理 | `test_stream_mode.py` | 通过 |
| 工具链不完整触发回退 | `test_stream_mode.py` | 通过 |
| 回退时增量发送内容 | `test_stream_mode.py` | 通过 |
| 多轮工具链重试 | `test_stream_mode.py` | 通过 |
| DeepSeek Chat 多轮工具调用 | `test_agent_multi_tool_live.py` | 通过 |
| DeepSeek Reasoner 多轮工具调用 | `test_agent_multi_tool_live.py` | 通过 |

## 5. 最佳实践

### 5.1 如何避免类似问题

#### 5.1.1 模型适配层

为不同模型创建适配层，处理其特殊行为：

```python
class ModelAdapter:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self.supports_streaming_tools = model_name not in ["deepseek-chat", "deepseek-reasoner"]

    async def run(self, prompt, deps, message_history=None):
        if self.supports_streaming_tools:
            return await self._run_stream(prompt, deps, message_history)
        return await self._run_sync(prompt, deps, message_history)
```

#### 5.1.2 工具链状态监控

始终监控工具链的完整性：

```python
def validate_tool_chain(messages: list[ModelMessage]) -> bool:
    """验证工具链是否完整"""
    tool_calls, tool_returns = _count_tool_parts(messages)
    return tool_calls <= tool_returns
```

#### 5.1.3 优雅降级

当流式模式出现问题时，自动降级到非流式模式：

```python
try:
    async for event in stream_chat(prompt, deps, model):
        yield event
except ToolChainBrokenError:
    # 降级到非流式模式
    async for event in non_stream_chat(prompt, deps, model):
        yield event
```

### 5.2 代码审查清单

- [ ] 工具调用后是否正确等待返回？
- [ ] 多轮对话中历史消息是否正确传递？
- [ ] 流式模式下是否处理了工具链中断？
- [ ] 是否有回退机制处理异常情况？
- [ ] 是否记录了工具链状态日志？

### 5.3 调试技巧

#### 启用 LLM 原始日志

```python
# 在配置中启用
settings = Settings(
    llm_raw_log_enabled=True,  # 记录原始请求/响应
)
```

#### 检查工具链状态

```python
logger.debug(
    "tool_chain_status",
    tool_calls=tool_calls,
    tool_returns=tool_returns,
    fallback_used=fallback_used,
)
```

#### 使用集成测试验证

```bash
# 设置 API Key
export DEEPSEEK_API_KEY=your_api_key

# 运行集成测试
pytest tests/test_agent_multi_tool_live.py -v
```

## 6. 总结

### 6.1 问题本质

DeepSeek 模型在流式输出模式下，对工具调用的处理存在特殊性：`run_stream` 方法返回 `tool-call` 后不会自动等待工具执行完成，导致工具链中断。

### 6.2 解决方案

1. **检测机制**：通过比较 `tool_calls` 和 `tool_returns` 数量检测工具链完整性
2. **回退机制**：当检测到工具链不完整时，使用非流式 `run` 方法重新执行
3. **增量发送**：避免重复发送已发送的内容
4. **重试机制**：最多 3 次重试，确保工具链最终完成

### 6.3 关键代码位置

- 核心修复：`services/agent/core.py:275-346`
- 工具统计：`services/agent/core.py:98-115`
- 单元测试：`tests/test_stream_mode.py`
- 集成测试：`tests/test_agent_multi_tool_live.py`

### 6.4 后续优化方向

1. **模型适配层**：为不同模型提供更精细的适配
2. **配置化**：将工具链检测和回退机制参数化
3. **性能优化**：减少回退时的重复计算
4. **监控告警**：建立工具链中断的监控和告警机制
